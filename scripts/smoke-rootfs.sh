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
tar -xJf "${archive}" -C "${smoke_root}"
root_name=$(python3 -c '
import json, pathlib, sys
lock = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
print(lock["images"][sys.argv[2]]["archiveRoot"])
' "${repository_root}/sources.lock.json" "${target}")
rootfs="${smoke_root}/${root_name}"
helper="${rootfs}/usr/local/bin/liskov-runtime-contact"

file "${helper}" | grep -Eq 'ARM aarch64|ARM64'
file "${helper}" | grep -q 'statically linked'
readelf -h "${helper}" | grep -q 'Machine:.*AArch64'
if readelf -l "${helper}" | grep -q 'interpreter'; then
  echo "embedded helper unexpectedly requests a dynamic interpreter" >&2
  exit 1
fi

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
  proot "${qemu_args[@]}" -0 \
    -r "${rootfs}" \
    -b /dev \
    -b /proc \
    -b /sys \
    -b /etc/resolv.conf \
    -w / \
    /bin/sh -c '
      set -eu
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install --no-install-recommends --yes ca-certificates curl
      curl --fail --location --silent --show-error \
        --output /dev/null https://liskov.proof.computer/
    '
fi

echo "smoke passed: ${target}"
