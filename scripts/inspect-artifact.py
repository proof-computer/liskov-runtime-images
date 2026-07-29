#!/usr/bin/env python3
"""Fail-closed structural inspection for a built rootfs archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import tarfile
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()

    lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
    image = lock["images"][args.target]
    helper = lock["helper"]
    expected_root = image["archiveRoot"]
    expected_epoch = lock["sourceDateEpoch"]
    helper_member = f"{expected_root}/usr/local/bin/liskov-runtime-contact"
    shim_library_member = (
        f"{expected_root}/usr/local/lib/libgetifaddrs_override.so"
    )
    shim_source_member = (
        f"{expected_root}/usr/share/liskov-runtime-images/getifaddrs_override.c"
    )
    provenance_member = (
        f"{expected_root}/usr/share/liskov-runtime-images/provenance.json"
    )
    license_member = (
        f"{expected_root}/usr/share/doc/liskov-runtime-contact/LICENSE"
    )

    with tarfile.open(args.archive, "r:xz") as archive:
        members = archive.getmembers()
        if not members:
            raise SystemExit("archive is empty")
        names = [member.name.removeprefix("./") for member in members]
        roots = {PurePosixPath(name).parts[0] for name in names if name}
        if roots != {expected_root}:
            raise SystemExit(f"unexpected archive roots: {sorted(roots)}")
        for member, name in zip(members, names, strict=True):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"unsafe archive member: {name}")
            if member.uid != 0 or member.gid != 0:
                raise SystemExit(f"non-root numeric owner on {name}")
            if member.mtime != expected_epoch:
                raise SystemExit(f"non-canonical mtime on {name}")

        by_name = dict(zip(names, members, strict=True))
        for required in (
            helper_member,
            shim_library_member,
            shim_source_member,
            provenance_member,
            license_member,
        ):
            if required not in by_name:
                raise SystemExit(f"missing required member {required}")
        helper_info = by_name[helper_member]
        if not helper_info.isfile() or stat.S_IMODE(helper_info.mode) != 0o755:
            raise SystemExit("embedded helper is not an executable regular file")
        helper_file = archive.extractfile(helper_info)
        if helper_file is None:
            raise SystemExit("could not read embedded helper")
        helper_digest = hashlib.sha256(helper_file.read()).hexdigest()
        if helper_digest != helper["binarySha256"]:
            raise SystemExit("embedded helper digest differs from sources.lock.json")
        shim_library_file = archive.extractfile(by_name[shim_library_member])
        if shim_library_file is None:
            raise SystemExit("could not read embedded getifaddrs override")
        shim_library = shim_library_file.read()
        if (
            len(shim_library) < 64
            or shim_library[:4] != b"\x7fELF"
            or shim_library[4] != 2
            or shim_library[5] != 1
            or int.from_bytes(shim_library[16:18], "little") != 3
            or int.from_bytes(shim_library[18:20], "little") != 183
        ):
            raise SystemExit("embedded getifaddrs override is not an AArch64 shared object")
        shim_library_digest = hashlib.sha256(shim_library).hexdigest()
        shim_source_file = archive.extractfile(by_name[shim_source_member])
        if shim_source_file is None:
            raise SystemExit("could not read embedded getifaddrs override source")
        shim_source_digest = hashlib.sha256(shim_source_file.read()).hexdigest()
        repository_source_digest = hashlib.sha256(
            (REPOSITORY_ROOT / "assets/getifaddrs_override.c").read_bytes()
        ).hexdigest()
        if shim_source_digest != repository_source_digest:
            raise SystemExit("embedded getifaddrs source differs from the repository source")

        provenance_file = archive.extractfile(by_name[provenance_member])
        if provenance_file is None:
            raise SystemExit("could not read embedded provenance")
        provenance = json.load(provenance_file)
        if provenance.get("domain") != "proof.liskov.runtime-image.provenance.v1":
            raise SystemExit("embedded provenance domain is invalid")
        if provenance.get("target") != args.target:
            raise SystemExit("embedded provenance target is invalid")
        shim_provenance = provenance.get("overlay", {}).get(
            "networkInterfaceOverride", {}
        )
        if (
            shim_provenance.get("sourceSha256") != shim_source_digest
            or shim_provenance.get("librarySha256") != shim_library_digest
        ):
            raise SystemExit("embedded getifaddrs provenance digest is invalid")

    print(
        json.dumps(
            {
                "ok": True,
                "target": args.target,
                "archive": str(args.archive),
                "archiveRoot": expected_root,
                "memberCount": len(members),
                "helperSha256": helper_digest,
                "getifaddrsOverrideSha256": shim_library_digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
