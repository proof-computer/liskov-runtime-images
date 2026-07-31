#!/usr/bin/env python3
"""Fail-closed structural inspection for a built rootfs archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REMOVED_HELPER_PATHS = (
    PurePosixPath("usr/local/bin/liskov-runtime-contact"),
    PurePosixPath("usr/share/doc/liskov-runtime-contact/LICENSE"),
)


def normalized_posix_path(path: PurePosixPath) -> PurePosixPath | None:
    parts: list[str] = []
    for part in path.parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts)


def assert_no_removed_helper_aliases(
    members: list[tarfile.TarInfo],
    names: list[str],
    expected_root: str,
) -> None:
    forbidden = {
        PurePosixPath(expected_root) / relative
        for relative in REMOVED_HELPER_PATHS
    }
    for member, name in zip(members, names, strict=True):
        member_path = normalized_posix_path(PurePosixPath(name))
        if member_path in forbidden:
            raise SystemExit(f"removed helper path is present: {name}")
        if not (member.issym() or member.islnk()):
            continue
        link = PurePosixPath(member.linkname)
        candidates: list[PurePosixPath | None]
        if link.is_absolute():
            candidates = [
                normalized_posix_path(
                    PurePosixPath(expected_root, *link.parts[1:])
                )
            ]
        elif member.issym():
            candidates = [
                normalized_posix_path(PurePosixPath(name).parent / link)
            ]
        else:
            candidates = [
                normalized_posix_path(link),
                normalized_posix_path(PurePosixPath(expected_root) / link),
                normalized_posix_path(PurePosixPath(name).parent / link),
            ]
        if any(candidate in forbidden for candidate in candidates):
            raise SystemExit(
                f"archive link aliases a removed helper path: {name} -> {member.linkname}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()

    lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
    image = lock["images"][args.target]
    expected_root = image["archiveRoot"]
    expected_epoch = lock["sourceDateEpoch"]
    shim_library_member = (
        f"{expected_root}/usr/local/lib/libgetifaddrs_override.so"
    )
    shim_source_member = (
        f"{expected_root}/usr/share/liskov-runtime-images/getifaddrs_override.c"
    )
    provenance_member = (
        f"{expected_root}/usr/share/liskov-runtime-images/provenance.json"
    )

    with tarfile.open(args.archive, "r:xz") as archive:
        members = archive.getmembers()
        if not members:
            raise SystemExit("archive is empty")
        names = [member.name.removeprefix("./") for member in members]
        if len(names) != len(set(names)):
            raise SystemExit("archive contains duplicate member names")
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
        assert_no_removed_helper_aliases(members, names, expected_root)

        by_name = dict(zip(names, members, strict=True))
        for required in (
            shim_library_member,
            shim_source_member,
            provenance_member,
        ):
            if required not in by_name:
                raise SystemExit(f"missing required member {required}")
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
        overlay = provenance.get("overlay", {})
        expected_overlay_paths = [
            "usr/local/lib/libgetifaddrs_override.so",
            "usr/share/liskov-runtime-images/getifaddrs_override.c",
            "usr/share/liskov-runtime-images/provenance.json",
        ]
        if set(overlay) != {"networkInterfaceOverride", "paths"}:
            raise SystemExit("embedded overlay provenance has unexpected fields")
        if overlay.get("paths") != expected_overlay_paths:
            raise SystemExit("embedded overlay provenance path set is invalid")
        shim_provenance = overlay.get("networkInterfaceOverride", {})
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
                "getifaddrsOverrideSha256": shim_library_digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
