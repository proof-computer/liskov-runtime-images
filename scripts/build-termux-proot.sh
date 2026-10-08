#!/usr/bin/env bash
set -euo pipefail

# Builds the public Termux PRoot base that the Acurast processor bundles
# (it reports v5.1.107.72-dirty), so the QEMU/PRoot smoke translates the same
# syscalls production does. Distribution PRoot 5.1.0 predates statx, which
# every Rust coreutils file lookup uses; under it Ubuntu Resolute cannot even
# chmod a file that exists.

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/build-termux-proot.sh <output-binary>" >&2
  exit 2
fi

output=$1
proot_version=v5.1.107.72
proot_commit=58aad2cb1c36ea6af7b32d76ccd5bf8d0a967939
proot_archive_sha256=0e132e306214adba900479d3262058f179577856f375d786d3e062498ca957fd

for tool in clang curl ld.lld make sha256sum tar; do
  command -v "${tool}" >/dev/null || {
    echo "required PRoot build tool is missing: ${tool}" >&2
    exit 2
  }
done

build_root=$(mktemp -d "${TMPDIR:-/tmp}/liskov-termux-proot.XXXXXX")
trap 'rm -rf -- "${build_root}"' EXIT

curl --fail --location --show-error --silent \
  --retry 3 --retry-all-errors \
  --output "${build_root}/proot.tar.gz" \
  "https://github.com/termux/proot/archive/${proot_commit}.tar.gz"
printf '%s  %s\n' "${proot_archive_sha256}" "${build_root}/proot.tar.gz" \
  | sha256sum --check --strict --quiet
tar -xzf "${build_root}/proot.tar.gz" -C "${build_root}"
source_root="${build_root}/proot-${proot_commit}"

# Only the Android ashmem extensions include <linux/ashmem.h>, which glibc
# hosts do not ship. These are the four ioctl definitions they use, from the
# Android UAPI header; the smoke never reaches /dev/ashmem.
mkdir -p "${source_root}/src/linux"
cat > "${source_root}/src/linux/ashmem.h" <<'EOF'
#ifndef LISKOV_SMOKE_ASHMEM_H
#define LISKOV_SMOKE_ASHMEM_H
#include <linux/ioctl.h>
#include <linux/types.h>
#define ASHMEM_NAME_LEN 256
#define __ASHMEMIOC 0x77
#define ASHMEM_SET_NAME _IOW(__ASHMEMIOC, 1, char[ASHMEM_NAME_LEN])
#define ASHMEM_SET_SIZE _IOW(__ASHMEMIOC, 3, size_t)
#define ASHMEM_GET_SIZE _IO(__ASHMEMIOC, 4)
#endif
EOF

# The makefile stamps the version from `git describe`; the archive has no
# history, so answer with the pinned tag.
printf '#!/bin/sh\nprintf "%s\\n"\n' "${proot_version}" > "${build_root}/pinned-version"
chmod 0755 "${build_root}/pinned-version"
# Upstream ashmem_memfd.c calls strcmp and memset without <string.h>; clang
# 16 and later reject that by default, which the pinned source predates.
make -C "${source_root}/src" \
  CC='clang -fuse-ld=lld -Wno-error=implicit-function-declaration' \
  GIT="${build_root}/pinned-version" \
  >"${build_root}/make.log" 2>&1 || {
  tail -n 40 "${build_root}/make.log" >&2
  exit 1
}
built_version=$("${source_root}/src/proot" --version)
[[ "${built_version}" == *"${proot_version}"* ]] || {
  echo "built PRoot does not report ${proot_version}" >&2
  exit 1
}

mkdir -p "$(dirname "${output}")"
install -m 0755 "${source_root}/src/proot" "${output}"
echo "built Termux PRoot ${proot_version} (${proot_commit}) at ${output}"
