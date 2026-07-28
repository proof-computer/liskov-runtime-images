#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: scripts/verify-native-aarch64.sh <target> <archive>" >&2
  exit 2
fi

target=$1
archive=$2
repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

case "$(uname -m)" in
  aarch64 | arm64) ;;
  *)
    echo "native verification requires an AArch64 host" >&2
    exit 2
    ;;
esac

for tool in python3 tar file readelf; do
  command -v "${tool}" >/dev/null || {
    echo "required native verification tool is missing: ${tool}" >&2
    exit 2
  }
done

verification_root=$(mktemp -d "${TMPDIR:-/tmp}/liskov-runtime-images-native.XXXXXX")
cleanup() {
  if [[ -n "${verification_root:-}" && -d "${verification_root}" ]]; then
    rm -rf -- "${verification_root}"
  fi
}
trap cleanup EXIT

python3 "${repository_root}/scripts/inspect-artifact.py" "${archive}" --target "${target}"
tar -xJf "${archive}" -C "${verification_root}"
root_name=$(python3 -c '
import json, pathlib, sys
lock = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
print(lock["images"][sys.argv[2]]["archiveRoot"])
' "${repository_root}/sources.lock.json" "${target}")
helper="${verification_root}/${root_name}/usr/local/bin/liskov-runtime-contact"

file "${helper}" | grep -Eq 'ARM aarch64|ARM64'
file "${helper}" | grep -q 'statically linked'
readelf -h "${helper}" | grep -q 'Machine:.*AArch64'
if readelf -l "${helper}" | grep -q 'interpreter'; then
  echo "embedded helper unexpectedly requests a dynamic interpreter" >&2
  exit 1
fi

version=$("${helper}" --version)
if [[ "${version}" != "liskov-runtime-contact 0.1.0" ]]; then
  echo "unexpected embedded helper version: ${version}" >&2
  exit 1
fi

echo "native AArch64 verification passed: ${target}"
