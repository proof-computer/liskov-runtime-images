#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: scripts/build-qualified-target.sh <material|release> <target> <output-root>" >&2
  exit 2
fi

mode=$1
target=$2
output_root=$3
repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [[ "${mode}" != "material" && "${mode}" != "release" ]]; then
  echo "build mode must be material or release" >&2
  exit 2
fi
if [[ -e "${output_root}/a" || -e "${output_root}/b" ]]; then
  echo "qualified build output trees must be fresh" >&2
  exit 1
fi

python3 "${repository_root}/scripts/build-image.py" \
  "${target}" \
  --output-dir "${output_root}/a" \
  --cache-dir "${repository_root}/.cache/downloads"

if [[ "${mode}" == "release" ]]; then
  python3 "${repository_root}/scripts/build-image.py" \
    "${target}" \
    --output-dir "${output_root}/b" \
    --cache-dir "${repository_root}/.cache/downloads"
  diff --no-dereference --recursive --brief "${output_root}/a" "${output_root}/b"
fi

archive=$(find "${output_root}/a" -maxdepth 1 -type f -name '*.tar.xz' -print -quit)
if [[ -z "${archive}" ]]; then
  echo "qualified build did not emit a rootfs archive" >&2
  exit 1
fi
"${repository_root}/scripts/verify-native-aarch64.sh" "${target}" "${archive}"
