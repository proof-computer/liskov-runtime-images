#!/usr/bin/env bash
set -euo pipefail

image=${LISKOV_AARCH64_CC_DOCKER_IMAGE:-liskov-runtime-images-crosscc:local}
mounts=()
mounted_roots=()
for argument in "$@"; do
  if [[ "${argument}" != /tmp/* ]]; then
    continue
  fi
  relative=${argument#/tmp/}
  temporary_root="/tmp/${relative%%/*}"
  already_mounted=0
  for mounted_root in "${mounted_roots[@]}"; do
    if [[ "${mounted_root}" == "${temporary_root}" ]]; then
      already_mounted=1
      break
    fi
  done
  if [[ "${already_mounted}" -eq 0 ]]; then
    mounted_roots+=("${temporary_root}")
    mounts+=(--volume "${temporary_root}:${temporary_root}:z")
  fi
done
if [[ "${#mounts[@]}" -eq 0 ]]; then
  echo "Docker cross-compiler accepts only compiler inputs and outputs under /tmp" >&2
  exit 2
fi

docker run --rm \
  --user "$(id -u):$(id -g)" \
  "${mounts[@]}" \
  "${image}" \
  aarch64-linux-gnu-gcc "$@"
