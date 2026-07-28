#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/verify-reproducible.sh <target>" >&2
  exit 2
fi

target=$1
repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
repro_root=$(mktemp -d "${TMPDIR:-/tmp}/liskov-runtime-images-repro.XXXXXX")

cleanup() {
  if [[ -n "${repro_root:-}" && -d "${repro_root}" ]]; then
    rm -rf -- "${repro_root}"
  fi
}
trap cleanup EXIT

python3 "${repository_root}/scripts/build-image.py" \
  "${target}" \
  --output-dir "${repro_root}/a" \
  --cache-dir "${repository_root}/.cache/downloads"
python3 "${repository_root}/scripts/build-image.py" \
  "${target}" \
  --output-dir "${repro_root}/b" \
  --cache-dir "${repository_root}/.cache/downloads"

mapfile -t first_outputs < <(find "${repro_root}/a" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort)
mapfile -t second_outputs < <(find "${repro_root}/b" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort)

if [[ "${first_outputs[*]}" != "${second_outputs[*]}" ]]; then
  echo "reproducibility output sets differ for ${target}" >&2
  exit 1
fi

for output in "${first_outputs[@]}"; do
  cmp --silent "${repro_root}/a/${output}" "${repro_root}/b/${output}" || {
    echo "reproducibility mismatch for ${target}: ${output}" >&2
    exit 1
  }
done

archive=$(find "${repro_root}/a" -maxdepth 1 -type f -name '*.tar.xz' -print -quit)
python3 "${repository_root}/scripts/inspect-artifact.py" "${archive}" --target "${target}"
sha256sum "${repro_root}/a/"*
echo "reproducible: ${target}"
