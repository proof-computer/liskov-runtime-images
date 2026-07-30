#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
base=${1:-HEAD}

python3 -m unittest discover -s "${repository_root}/tests" -v
python3 -m compileall -q "${repository_root}/scripts" "${repository_root}/tests"
for script in "${repository_root}"/scripts/*.sh; do
  bash -n "${script}"
done

classification=$(python3 "${repository_root}/scripts/change-classifier.py" classify \
  --root "${repository_root}" \
  --base "${base}" \
  --head WORKTREE)
mode=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["mode"])' <<<"${classification}")
if [[ "${mode}" == "fast" ]]; then
  echo "fast change: rootfs construction is not required"
  exit 0
fi

validation_root=$(mktemp -d "${TMPDIR:-/tmp}/liskov-runtime-images-local.XXXXXX")
cleanup() {
  if [[ -n "${validation_root:-}" && -d "${validation_root}" ]]; then
    rm -rf -- "${validation_root}"
  fi
}
trap cleanup EXIT

compiler_environment=()
if [[ -n "${LISKOV_AARCH64_CC:-}" ]]; then
  if ! command -v "${LISKOV_AARCH64_CC}" >/dev/null; then
    echo "LISKOV_AARCH64_CC does not resolve to an executable compiler" >&2
    exit 2
  fi
  compiler_environment=(env "LISKOV_AARCH64_CC=${LISKOV_AARCH64_CC}")
elif command -v aarch64-linux-gnu-gcc >/dev/null; then
  compiler_environment=(env "LISKOV_AARCH64_CC=aarch64-linux-gnu-gcc")
elif command -v aarch64-suse-linux-gcc >/dev/null; then
  compiler_environment=(env "LISKOV_AARCH64_CC=aarch64-suse-linux-gcc")
else
  if command -v docker >/dev/null &&
    docker image inspect liskov-runtime-images-crosscc:local >/dev/null 2>&1; then
    compiler_environment=(
      env
      "LISKOV_AARCH64_CC=${repository_root}/scripts/aarch64-cc-docker.sh"
    )
  else
    echo "material validation requires an AArch64 compiler or the liskov-runtime-images-crosscc:local container" >&2
    exit 2
  fi
fi

for target in debian-trixie v4-control; do
  "${compiler_environment[@]}" python3 "${repository_root}/scripts/build-image.py" \
    "${target}" \
    --output-dir "${validation_root}/${target}" \
    --cache-dir "${repository_root}/.cache/downloads"
done
echo "material change: one local construction passed for every target"
