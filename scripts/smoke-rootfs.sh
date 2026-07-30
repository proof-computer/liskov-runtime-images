#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: scripts/smoke-rootfs.sh <target> <archive>" >&2
  exit 2
fi

target=$1
archive=$2
repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

for tool in proot python3 tar file readelf; do
  command -v "${tool}" >/dev/null || {
    echo "required smoke tool is missing: ${tool}" >&2
    exit 2
  }
done

qemu_args=()
case "$(uname -m)" in
  aarch64 | arm64) ;;
  *)
    qemu_path=$(command -v qemu-aarch64-static || true)
    if [[ -z "${qemu_path}" ]]; then
      echo "smoke-rootfs requires native AArch64 or qemu-aarch64-static" >&2
      exit 2
    fi
    qemu_args=(-q "${qemu_path}")
    ;;
esac

smoke_root=$(mktemp -d "${TMPDIR:-/tmp}/liskov-runtime-images-smoke.XXXXXX")
bridge_pid=

cleanup() {
  if [[ -n "${bridge_pid:-}" ]]; then
    kill "${bridge_pid}" 2>/dev/null || true
    wait "${bridge_pid}" 2>/dev/null || true
  fi
  if [[ -n "${smoke_root:-}" && -d "${smoke_root}" ]]; then
    rm -rf -- "${smoke_root}"
  fi
}
trap cleanup EXIT

python3 "${repository_root}/scripts/inspect-artifact.py" "${archive}" --target "${target}"
root_name=$(python3 -c '
import json, pathlib, sys
lock = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
print(lock["images"][sys.argv[2]]["archiveRoot"])
' "${repository_root}/sources.lock.json" "${target}")
# The v4 source deliberately preserves its device nodes. Unprivileged CI
# cannot recreate them, and PRoot replaces this directory with the bound host
# /dev for the smoke, so skip only those archive members during extraction.
tar --exclude="${root_name}/dev/*" -xJf "${archive}" -C "${smoke_root}"
rootfs="${smoke_root}/${root_name}"
helper="${rootfs}/usr/local/bin/liskov-runtime-contact"
shim="${rootfs}/usr/local/lib/libgetifaddrs_override.so"
shim_source="${rootfs}/usr/share/liskov-runtime-images/getifaddrs_override.c"

file "${helper}" | grep -Eq 'ARM aarch64|ARM64'
file "${helper}" | grep -q 'statically linked'
readelf -h "${helper}" | grep -q 'Machine:.*AArch64'
if readelf -l "${helper}" | grep -q 'interpreter'; then
  echo "embedded helper unexpectedly requests a dynamic interpreter" >&2
  exit 1
fi
test -r "${shim_source}"
file "${shim}" | grep -Eq 'ARM aarch64|ARM64'
file "${shim}" | grep -q 'shared object'
readelf -h "${shim}" | grep -q 'Type:.*DYN'
readelf -h "${shim}" | grep -q 'Machine:.*AArch64'
readelf --wide --dyn-syms "${shim}" | grep -Eq 'GLOBAL +DEFAULT +[0-9]+ +getifaddrs$'
readelf --wide --dyn-syms "${shim}" | grep -Eq 'GLOBAL +DEFAULT +[0-9]+ +freeifaddrs$'

proot "${qemu_args[@]}" -0 \
  -r "${rootfs}" \
  -b /dev \
  -b /proc \
  -b /sys \
  -b /etc/resolv.conf \
  -w / \
  /bin/sh -c '
    set -eu
    test "$(id -u)" = 0
    test -x /bin/sh
    /usr/local/bin/liskov-runtime-contact --version
    LD_PRELOAD=/usr/local/lib/libgetifaddrs_override.so /bin/sh -c :
    getent hosts liskov.proof.computer >/dev/null
  '

socket_name="liskov-runtime-images-smoke-${GITHUB_RUN_ID:-local}-$$"
ready_file="${smoke_root}/bridge.ready"
method_file="${smoke_root}/bridge.method"
python3 "${repository_root}/tests/bridge-smoke-server.py" \
  --socket-name "${socket_name}" \
  --ready-file "${ready_file}" \
  --method-file "${method_file}" &
bridge_pid=$!

for _ in {1..100}; do
  [[ -f "${ready_file}" ]] && break
  sleep 0.05
done
[[ -f "${ready_file}" ]] || {
  echo "bridge smoke server did not become ready" >&2
  exit 1
}

set +e
BRIDGE_SOCKET="${socket_name}" proot "${qemu_args[@]}" -0 \
  -r "${rootfs}" \
  -b /dev \
  -b /proc \
  -b /sys \
  -b /etc/resolv.conf \
  -w / \
  /usr/local/bin/liskov-runtime-contact -- /bin/true
helper_status=$?
set -e

wait "${bridge_pid}"
bridge_pid=
if [[ "${helper_status}" -ne 70 ]]; then
  echo "bridge smoke expected fail-closed status 70, got ${helper_status}" >&2
  exit 1
fi
grep -qx 'deployment_id' "${method_file}"

if [[ "${LISKOV_SMOKE_HTTPS:-0}" == 1 ]]; then
  https_socket_name="${socket_name}-https"
  https_ready_file="${smoke_root}/bridge-https.ready"
  https_method_file="${smoke_root}/bridge-https.method"
  python3 "${repository_root}/tests/bridge-smoke-server.py" \
    --socket-name "${https_socket_name}" \
    --ready-file "${https_ready_file}" \
    --method-file "${https_method_file}" \
    --valid-identity &
  bridge_pid=$!

  for _ in {1..100}; do
    [[ -f "${https_ready_file}" ]] && break
    sleep 0.05
  done
  [[ -f "${https_ready_file}" ]] || {
    echo "HTTPS bridge smoke server did not become ready" >&2
    exit 1
  }

  set +e
  BRIDGE_SOCKET="${https_socket_name}" proot "${qemu_args[@]}" -0 \
    -r "${rootfs}" \
    -b /dev \
    -b /proc \
    -b /sys \
    -b /etc/resolv.conf \
    -w / \
    /usr/local/bin/liskov-runtime-contact \
      --core-url https://example.com \
      -- /bin/true
  https_status=$?
  set -e

  wait "${bridge_pid}"
  bridge_pid=
  if [[ "${https_status}" -ne 70 ]]; then
    echo "HTTPS smoke expected permanent HTTP rejection status 70, got ${https_status}" >&2
    exit 1
  fi
  expected_methods=$'deployment_id\ndeployment_publicKeys\ndeployment_assignedProcessors\nsigner_sign'
  if [[ "$(cat "${https_method_file}")" != "${expected_methods}" ]]; then
    echo "HTTPS smoke did not complete identity discovery and signing" >&2
    exit 1
  fi

  probe_socket_name="${socket_name}-probe"
  probe_ready_file="${smoke_root}/bridge-probe.ready"
  probe_method_file="${smoke_root}/bridge-probe.method"
  python3 "${repository_root}/tests/bridge-smoke-server.py" \
    --socket-name "${probe_socket_name}" \
    --ready-file "${probe_ready_file}" \
    --method-file "${probe_method_file}" \
    --probe &
  bridge_pid=$!

  for _ in {1..100}; do
    [[ -f "${probe_ready_file}" ]] && break
    sleep 0.05
  done
  [[ -f "${probe_ready_file}" ]] || {
    echo "bridge probe smoke server did not become ready" >&2
    exit 1
  }

  set +e
  BRIDGE_SOCKET="${probe_socket_name}" proot "${qemu_args[@]}" -0 \
    -r "${rootfs}" \
    -b /dev \
    -b /proc \
    -b /sys \
    -b /etc/resolv.conf \
    -w / \
    /usr/local/bin/liskov-runtime-contact \
      --bridge-probe \
      --core-url https://example.com \
      -- /bin/true
  probe_status=$?
  set -e

  wait "${bridge_pid}"
  bridge_pid=
  if [[ "${probe_status}" -ne 70 ]]; then
    echo "bridge probe smoke expected permanent HTTP rejection status 70, got ${probe_status}" >&2
    exit 1
  fi
  expected_probe_methods=$'processor_version\ndeployment_id\ndeployment_ipfsHash\ndeployment_publicKeys\ndeployment_assignedProcessors\nsigner_sign\nprocessor_version\ndeployment_id\ndeployment_publicKeys\ndeployment_assignedProcessors\nsigner_sign'
  if [[ "$(cat "${probe_method_file}")" != "${expected_probe_methods}" ]]; then
    echo "bridge probe smoke did not cover the expected bounded methods" >&2
    exit 1
  fi
fi

echo "smoke passed: ${target}"
