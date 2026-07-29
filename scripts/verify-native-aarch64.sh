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
root_name=$(python3 -c '
import json, pathlib, sys
lock = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
print(lock["images"][sys.argv[2]]["archiveRoot"])
' "${repository_root}/sources.lock.json" "${target}")
expected_helper_version=$(python3 -c '
import json, pathlib, sys
lock = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
print(lock["helper"]["version"])
' "${repository_root}/sources.lock.json")
tar -xJf "${archive}" -C "${verification_root}" \
  "${root_name}/usr/local/bin/liskov-runtime-contact" \
  "${root_name}/usr/local/lib/libgetifaddrs_override.so"
helper="${verification_root}/${root_name}/usr/local/bin/liskov-runtime-contact"
shim="${verification_root}/${root_name}/usr/local/lib/libgetifaddrs_override.so"

file "${helper}" | grep -Eq 'ARM aarch64|ARM64'
file "${helper}" | grep -q 'statically linked'
readelf -h "${helper}" | grep -q 'Machine:.*AArch64'
if readelf -l "${helper}" | grep -q 'interpreter'; then
  echo "embedded helper unexpectedly requests a dynamic interpreter" >&2
  exit 1
fi
file "${shim}" | grep -Eq 'ARM aarch64|ARM64'
file "${shim}" | grep -q 'shared object'
readelf -h "${shim}" | grep -q 'Type:.*DYN'
readelf -h "${shim}" | grep -q 'Machine:.*AArch64'
readelf --wide --dyn-syms "${shim}" | grep -Eq 'GLOBAL +DEFAULT +[0-9]+ +getifaddrs$'
readelf --wide --dyn-syms "${shim}" | grep -Eq 'GLOBAL +DEFAULT +[0-9]+ +freeifaddrs$'

LD_PRELOAD="${shim}" python3 - <<'PY'
import ctypes
import socket

class Sockaddr(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("data", ctypes.c_ubyte * 14)]

class SockaddrIn(ctypes.Structure):
    _fields_ = [
        ("family", ctypes.c_ushort),
        ("port", ctypes.c_ushort),
        ("address", ctypes.c_uint32),
        ("padding", ctypes.c_ubyte * 8),
    ]

class Ifaddrs(ctypes.Structure):
    pass

IfaddrsPointer = ctypes.POINTER(Ifaddrs)
Ifaddrs._fields_ = [
    ("next", IfaddrsPointer),
    ("name", ctypes.c_char_p),
    ("flags", ctypes.c_uint),
    ("address", ctypes.POINTER(Sockaddr)),
    ("netmask", ctypes.POINTER(Sockaddr)),
    ("union", ctypes.POINTER(Sockaddr)),
    ("data", ctypes.c_void_p),
]

process = ctypes.CDLL(None, use_errno=True)
head = IfaddrsPointer()
if process.getifaddrs(ctypes.byref(head)) != 0:
    raise SystemExit(f"getifaddrs failed: {ctypes.get_errno()}")
try:
    if not head or bool(head.contents.next) or head.contents.name != b"lo":
        raise SystemExit("getifaddrs override did not return exactly one loopback")
    address = ctypes.cast(head.contents.address, ctypes.POINTER(SockaddrIn)).contents
    netmask = ctypes.cast(head.contents.netmask, ctypes.POINTER(SockaddrIn)).contents
    if address.family != socket.AF_INET:
        raise SystemExit("getifaddrs override returned a non-IPv4 address")
    if socket.ntohl(address.address) != 0x7F000001:
        raise SystemExit("getifaddrs override returned the wrong loopback address")
    if socket.ntohl(netmask.address) != 0xFF000000:
        raise SystemExit("getifaddrs override returned the wrong loopback netmask")
finally:
    process.freeifaddrs(head)
PY

version=$("${helper}" --version)
if [[ "${version}" != "liskov-runtime-contact ${expected_helper_version}" ]]; then
  echo "unexpected embedded helper version: ${version}" >&2
  exit 1
fi

echo "native AArch64 verification passed: ${target}"
