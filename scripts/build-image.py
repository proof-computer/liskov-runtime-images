#!/usr/bin/env python3
"""Build one canonical Liskov Cargo/PRoot rootfs from digest-pinned inputs."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = REPOSITORY_ROOT / "sources.lock.json"
GETIFADDRS_OVERRIDE_SOURCE = REPOSITORY_ROOT / "assets/getifaddrs_override.c"
HELPER_PATH = "usr/local/bin/liskov-runtime-contact"
HELPER_LICENSE_PATH = "usr/share/doc/liskov-runtime-contact/LICENSE"
GETIFADDRS_OVERRIDE_LIBRARY_PATH = "usr/local/lib/libgetifaddrs_override.so"
GETIFADDRS_OVERRIDE_SOURCE_PATH = (
    "usr/share/liskov-runtime-images/getifaddrs_override.c"
)
PROVENANCE_PATH = "usr/share/liskov-runtime-images/provenance.json"
OVERLAY_PATHS = (
    HELPER_PATH,
    HELPER_LICENSE_PATH,
    GETIFADDRS_OVERRIDE_LIBRARY_PATH,
    GETIFADDRS_OVERRIDE_SOURCE_PATH,
    PROVENANCE_PATH,
)
OCI_MANIFEST_MEDIA_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
OCI_LAYER_MEDIA_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.docker.image.rootfs.diff.tar",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}


class BuildError(RuntimeError):
    """A fail-closed input, extraction, or reproducibility error."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise BuildError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def verify_size(path: Path, expected: int, label: str) -> None:
    actual = path.stat().st_size
    if actual != expected:
        raise BuildError(f"{label} size mismatch: expected {expected}, got {actual}")


def verify_aarch64_shared_object(path: Path) -> None:
    bytes_ = path.read_bytes()
    if (
        len(bytes_) < 64
        or bytes_[:4] != b"\x7fELF"
        or bytes_[4] != 2
        or bytes_[5] != 1
        or int.from_bytes(bytes_[16:18], "little") != 3
        or int.from_bytes(bytes_[18:20], "little") != 183
    ):
        raise BuildError(
            "getifaddrs override must be an ELF64 little-endian AArch64 shared object"
        )


def build_getifaddrs_override(destination: Path) -> None:
    if not GETIFADDRS_OVERRIDE_SOURCE.is_file():
        raise BuildError("getifaddrs override source is missing")
    configured = os.environ.get("LISKOV_AARCH64_CC", "").strip()
    compiler = configured or (
        "cc" if platform.machine().lower() in {"aarch64", "arm64"} else "aarch64-linux-gnu-gcc"
    )
    if any(character.isspace() for character in compiler):
        raise BuildError("LISKOV_AARCH64_CC must name one compiler executable")
    if shutil.which(compiler) is None:
        raise BuildError(
            f"AArch64 C compiler is missing: {compiler}; set LISKOV_AARCH64_CC"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="liskov-getifaddrs-build-") as temporary:
        compiler_source = Path(temporary) / "getifaddrs_override.c"
        shutil.copyfile(GETIFADDRS_OVERRIDE_SOURCE, compiler_source)
        command = [
            compiler,
            "-std=c11",
            "-D_GNU_SOURCE",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-fPIC",
            "-fno-ident",
            "-shared",
            "-Wl,--build-id=none",
            "-Wl,--as-needed",
            "-Wl,-z,relro,-z,now",
            "-Wl,-s",
            "-o",
            str(destination),
            str(compiler_source),
        ]
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "LC_ALL": "C",
                "SOURCE_DATE_EPOCH": "0",
                "TZ": "UTC",
            },
        )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")[-4096:]
        raise BuildError(f"getifaddrs override compilation failed: {error}")
    verify_aarch64_shared_object(destination)
    os.chmod(destination, 0o755)


def cache_path(cache_dir: Path, digest: str, suffix: str) -> Path:
    return cache_dir / f"{digest}{suffix}"


def download(
    url: str,
    destination: Path,
    expected_sha256: str,
    label: str,
    headers: dict[str, str] | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        verify_digest(destination, expected_sha256, label)
        return destination

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "liskov-runtime-images/1", **(headers or {})},
    )
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise BuildError(f"{label} download returned HTTP {response.status}")
            with temporary.open("wb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    digest.update(chunk)
                    output.write(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise BuildError(
                f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def digest_hex(value: str, field: str) -> str:
    if not value.startswith("sha256:") or len(value) != 71:
        raise BuildError(f"{field} must be a sha256:<64 lowercase hex> digest")
    result = value.removeprefix("sha256:")
    if any(character not in "0123456789abcdef" for character in result):
        raise BuildError(f"{field} must be a lowercase SHA-256 digest")
    return result


def registry_token(image: dict[str, Any]) -> str:
    query = urllib.parse.urlencode(
        {
            "service": "registry.docker.io",
            "scope": f"repository:{image['repository']}:pull",
        }
    )
    request = urllib.request.Request(
        f"{image['authUrl']}?{query}",
        headers={"User-Agent": "liskov-runtime-images/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise BuildError(f"could not obtain OCI registry token: {error}") from error
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise BuildError("OCI registry token response did not contain a token")
    return token


def registry_blob(
    image: dict[str, Any],
    digest: str,
    destination: Path,
    token: str,
    label: str,
    *,
    manifest: bool = False,
) -> Path:
    expected = digest_hex(digest, label)
    endpoint = "manifests" if manifest else "blobs"
    url = (
        f"{image['registry'].rstrip('/')}/v2/{image['repository']}/"
        f"{endpoint}/{digest}"
    )
    headers = {"Authorization": f"Bearer {token}"}
    if manifest:
        headers["Accept"] = ", ".join(OCI_MANIFEST_MEDIA_TYPES)
    return download(url, destination, expected, label, headers)


def normalized_member_name(name: str, strip_prefix: str | None = None) -> str:
    while name.startswith("./"):
        name = name[2:]
    if strip_prefix is not None:
        if name == strip_prefix:
            return ""
        prefix = f"{strip_prefix}/"
        if not name.startswith(prefix):
            raise BuildError(f"archive member is outside expected root {strip_prefix}: {name}")
        name = name[len(prefix) :]
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\x00" in name:
        raise BuildError(f"unsafe archive member path: {name!r}")
    return str(path) if name else ""


def safe_destination(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise BuildError(f"archive member traverses symlink parent: {name}")
        if current.exists() and not current.is_dir():
            raise BuildError(f"archive member traverses non-directory parent: {name}")
        current.mkdir(mode=0o755, exist_ok=True)
    return root.joinpath(*parts)


def remove_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def open_tar(path: Path) -> tarfile.TarFile:
    return tarfile.open(path, mode="r:*")


def extract_archive(
    archive: Path,
    root: Path,
    *,
    strip_prefix: str | None = None,
    apply_whiteouts: bool = False,
    ignore_xattrs: bool = False,
) -> list[str]:
    """Safely materialize a trusted-and-verified tar without following parents."""

    root.mkdir(parents=True, exist_ok=True)
    omitted: list[str] = []
    directories: list[tuple[Path, tarfile.TarInfo]] = []
    pending_hardlinks: list[tuple[Path, str, tarfile.TarInfo]] = []

    with open_tar(archive) as source:
        members = list(source.getmembers())

        if apply_whiteouts:
            for member in members:
                name = normalized_member_name(member.name, strip_prefix)
                if not name:
                    continue
                path = PurePosixPath(name)
                if not path.name.startswith(".wh."):
                    continue
                parent = safe_destination(root, str(path.parent / "placeholder")).parent
                if path.name == ".wh..wh..opq":
                    if parent.is_dir():
                        for child in parent.iterdir():
                            remove_path(child)
                else:
                    remove_path(parent / path.name.removeprefix(".wh."))

        for member in members:
            name = normalized_member_name(member.name, strip_prefix)
            if not name:
                continue
            path = PurePosixPath(name)
            if apply_whiteouts and path.name.startswith(".wh."):
                continue
            destination = safe_destination(root, name)

            xattr_headers = [
                key
                for key in member.pax_headers
                if key.startswith("SCHILY.xattr.") or key.startswith("LIBARCHIVE.xattr.")
            ]
            if xattr_headers and not ignore_xattrs:
                raise BuildError(
                    f"archive member carries unsupported extended attributes: {name}"
                )

            if member.isdir():
                if destination.exists() and not destination.is_dir():
                    remove_path(destination)
                destination.mkdir(mode=member.mode & 0o7777, exist_ok=True)
                directories.append((destination, member))
            elif member.issym():
                remove_path(destination)
                os.symlink(member.linkname, destination)
                try:
                    os.utime(
                        destination,
                        (member.mtime, member.mtime),
                        follow_symlinks=False,
                    )
                except (NotImplementedError, PermissionError):
                    pass
            elif member.islnk():
                remove_path(destination)
                pending_hardlinks.append((destination, member.linkname, member))
            elif member.isfile():
                remove_path(destination)
                extracted = source.extractfile(member)
                if extracted is None:
                    raise BuildError(f"could not read archive member: {name}")
                with destination.open("wb") as output:
                    shutil.copyfileobj(extracted, output, 1024 * 1024)
                os.chmod(destination, member.mode & 0o7777)
                os.utime(destination, (member.mtime, member.mtime))
            elif member.isfifo():
                remove_path(destination)
                os.mkfifo(destination, member.mode & 0o7777)
            elif member.ischr() or member.isblk():
                # PRoot binds host devices; unprivileged materialization cannot
                # reproduce device nodes and PRoot-Distro v5 skips them too.
                omitted.append(name)
            else:
                raise BuildError(f"unsupported archive member type: {name}")

        remaining = pending_hardlinks
        for _ in range(len(remaining) + 1):
            if not remaining:
                break
            next_remaining: list[tuple[Path, str, tarfile.TarInfo]] = []
            for destination, linkname, member in remaining:
                target_name = normalized_member_name(linkname, strip_prefix)
                target = safe_destination(root, target_name)
                if not target.exists() or target.is_symlink():
                    next_remaining.append((destination, linkname, member))
                    continue
                os.link(target, destination, follow_symlinks=False)
                os.chmod(destination, member.mode & 0o7777)
            if len(next_remaining) == len(remaining):
                unresolved = ", ".join(linkname for _, linkname, _ in next_remaining)
                raise BuildError(f"unresolved or unsafe hardlink target(s): {unresolved}")
            remaining = next_remaining

    for directory, member in sorted(
        directories, key=lambda item: len(item[0].parts), reverse=True
    ):
        os.chmod(directory, member.mode & 0o7777)
        os.utime(directory, (member.mtime, member.mtime))
    return omitted


def materialize_oci(
    image: dict[str, Any], root: Path, cache_dir: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    token = registry_token(image)
    manifest_digest = image["manifestDigest"]
    manifest_hex = digest_hex(manifest_digest, "manifestDigest")
    manifest_path = registry_blob(
        image,
        manifest_digest,
        cache_path(cache_dir, manifest_hex, ".manifest.json"),
        token,
        "OCI manifest",
        manifest=True,
    )
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schemaVersion") != 2:
        raise BuildError("OCI manifest schemaVersion is not 2")
    if manifest.get("mediaType") not in OCI_MANIFEST_MEDIA_TYPES:
        raise BuildError(f"unsupported OCI manifest media type: {manifest.get('mediaType')}")

    config_descriptor = manifest.get("config")
    if not isinstance(config_descriptor, dict):
        raise BuildError("OCI manifest config descriptor is missing")
    if config_descriptor.get("digest") != image["configDigest"]:
        raise BuildError("OCI manifest config digest differs from sources.lock.json")

    actual_layers = manifest.get("layers")
    if not isinstance(actual_layers, list) or actual_layers != image["layers"]:
        raise BuildError("OCI manifest layers differ from sources.lock.json")

    config_hex = digest_hex(image["configDigest"], "configDigest")
    config_path = registry_blob(
        image,
        image["configDigest"],
        cache_path(cache_dir, config_hex, ".config.json"),
        token,
        "OCI config",
    )
    config = json.loads(config_path.read_bytes())
    if config.get("os") != "linux" or config.get("architecture") != image["architecture"]:
        raise BuildError("OCI config platform does not match the locked Linux architecture")
    if config.get("variant") != image.get("variant"):
        raise BuildError("OCI config architecture variant differs from sources.lock.json")

    materials = [
        {
            "uri": f"oci://{image['sourceReference']}@{manifest_digest}",
            "digest": {"sha256": manifest_hex},
            "mediaType": manifest["mediaType"],
        },
        {
            "uri": f"oci-config://{image['repository']}@{image['configDigest']}",
            "digest": {"sha256": config_hex},
            "mediaType": config_descriptor.get("mediaType"),
        },
    ]
    omitted: list[str] = []
    for index, descriptor in enumerate(actual_layers):
        media_type = descriptor.get("mediaType")
        if media_type not in OCI_LAYER_MEDIA_TYPES:
            raise BuildError(f"unsupported OCI layer media type: {media_type}")
        layer_digest = descriptor["digest"]
        layer_hex = digest_hex(layer_digest, f"layers[{index}].digest")
        suffix = ".layer.tar.gz" if media_type.endswith("gzip") else ".layer.tar"
        layer_path = registry_blob(
            image,
            layer_digest,
            cache_path(cache_dir, layer_hex, suffix),
            token,
            f"OCI layer {index}",
        )
        if layer_path.stat().st_size != descriptor["size"]:
            raise BuildError(f"OCI layer {index} size differs from sources.lock.json")
        omitted.extend(extract_archive(layer_path, root, apply_whiteouts=True))
        materials.append(
            {
                "uri": f"oci-layer://{image['repository']}@{layer_digest}",
                "digest": {"sha256": layer_hex},
                "mediaType": media_type,
                "size": descriptor["size"],
            }
        )
    return materials, omitted


def file_record(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    metadata = path.lstat()
    record: dict[str, Any] = {
        "path": relative,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }
    if path.is_symlink():
        target = os.readlink(path)
        record.update(
            {
                "type": "symlink",
                "target": target,
                "sha256": sha256_bytes(f"symlink:{target}".encode()),
            }
        )
    elif path.is_file():
        record.update(
            {
                "type": "file",
                "size": metadata.st_size,
                "sha256": sha256_file(path),
            }
        )
    elif path.is_dir():
        record["type"] = "directory"
    elif stat.S_ISFIFO(metadata.st_mode):
        record["type"] = "fifo"
    else:
        record["type"] = "special"
    return record


def inventory(root: Path) -> list[dict[str, Any]]:
    return [
        file_record(root, path)
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    ]


def inventory_digest(records: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json(records))


def archive_inventory(archive_path: Path, archive_root: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open_tar(archive_path) as archive:
        for member in archive:
            relative = normalized_member_name(member.name, archive_root)
            if not relative:
                continue
            record: dict[str, Any] = {
                "path": relative,
                "mode": f"{member.mode & 0o7777:04o}",
            }
            if member.isfile():
                file_object = archive.extractfile(member)
                if file_object is None:
                    raise BuildError(f"could not inventory archive member: {relative}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: file_object.read(1024 * 1024), b""):
                    digest.update(chunk)
                record.update(
                    {
                        "type": "file",
                        "size": member.size,
                        "sha256": digest.hexdigest(),
                    }
                )
            elif member.isdir():
                record["type"] = "directory"
            elif member.issym():
                record.update(
                    {
                        "type": "symlink",
                        "target": member.linkname,
                        "sha256": sha256_bytes(
                            f"symlink:{member.linkname}".encode()
                        ),
                    }
                )
            elif member.islnk():
                target = normalized_member_name(member.linkname, archive_root)
                record.update({"type": "hardlink", "target": target})
            elif member.isfifo():
                record["type"] = "fifo"
            elif member.ischr():
                record.update(
                    {
                        "type": "character-device",
                        "deviceMajor": member.devmajor,
                        "deviceMinor": member.devminor,
                    }
                )
            elif member.isblk():
                record.update(
                    {
                        "type": "block-device",
                        "deviceMajor": member.devmajor,
                        "deviceMinor": member.devminor,
                    }
                )
            else:
                record["type"] = "special"
            extended_attributes = {
                key: sha256_bytes(value.encode("utf-8", "surrogateescape"))
                for key, value in sorted(member.pax_headers.items())
                if key.startswith("SCHILY.xattr.") or key.startswith("LIBARCHIVE.xattr.")
            }
            if extended_attributes:
                record["extendedAttributes"] = extended_attributes
            records.append(record)
    return records


def parse_dpkg_status(root: Path) -> list[dict[str, str]]:
    status_path = root / "var/lib/dpkg/status"
    if not status_path.is_file():
        return []
    packages: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines() + [""]:
        if not line:
            if fields.get("Package") and fields.get("Version"):
                packages.append(
                    {
                        "name": fields["Package"],
                        "version": fields["Version"],
                        "architecture": fields.get("Architecture", "unknown"),
                    }
                )
            fields = {}
        elif not line.startswith((" ", "\t")) and ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return sorted(packages, key=lambda package: (package["name"], package["architecture"]))


def spdx_document(
    target: str,
    image: dict[str, Any],
    helper: dict[str, Any],
    getifaddrs_override_sha256: str,
    root: Path,
    final_inventory_digest: str,
    created: str,
) -> dict[str, Any]:
    image_spdx_id = "SPDXRef-Package-Rootfs"
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": image_spdx_id,
            "name": image["archiveRoot"],
            "versionInfo": "revision-1",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": (
                        f"pkg:generic/proof-computer/{image['archiveRoot']}?"
                        f"arch=aarch64&inventory_sha256={final_inventory_digest}"
                    ),
                }
            ],
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": image_spdx_id,
        }
    ]
    for index, package in enumerate(parse_dpkg_status(root), start=1):
        package_id = f"SPDXRef-Package-Distro-{index}"
        packages.append(
            {
                "SPDXID": package_id,
                "name": package["name"],
                "versionInfo": package["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            f"pkg:deb/{image['distribution']}/{package['name']}"
                            f"@{urllib.parse.quote(package['version'], safe='')}?"
                            f"arch={package['architecture']}"
                        ),
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": image_spdx_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": package_id,
            }
        )
    helper_id = "SPDXRef-Package-Liskov-Runtime-Contact"
    packages.append(
        {
            "SPDXID": helper_id,
            "name": "liskov-runtime-contact",
            "versionInfo": helper["version"],
            "downloadLocation": helper["archiveUrl"],
            "filesAnalyzed": False,
            "checksums": [
                {"algorithm": "SHA256", "checksumValue": helper["binarySha256"]}
            ],
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "NOASSERTION",
            "supplier": "Organization: PROOF Computer",
        }
    )
    relationships.append(
        {
            "spdxElementId": image_spdx_id,
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": helper_id,
        }
    )
    shim_id = "SPDXRef-Package-Liskov-Getifaddrs-Override"
    packages.append(
        {
            "SPDXID": shim_id,
            "name": "liskov-getifaddrs-override",
            "versionInfo": "1",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "checksums": [
                {
                    "algorithm": "SHA256",
                    "checksumValue": getifaddrs_override_sha256,
                }
            ],
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "Copyright 2026 PROOF Computer",
            "supplier": "Organization: PROOF Computer",
        }
    )
    relationships.append(
        {
            "spdxElementId": image_spdx_id,
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": shim_id,
        }
    )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"liskov-runtime-image-{target}",
        "documentNamespace": (
            "https://github.com/proof-computer/liskov-runtime-images/"
            f"sbom/{target}/{final_inventory_digest}"
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: liskov-runtime-images/scripts/build-image.py"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def canonical_archive(source_parent: Path, archive_root: str, output: Path, epoch: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tar_command = [
        "tar",
        "--sort=name",
        f"--mtime=@{epoch}",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "--format=gnu",
        "--hard-dereference",
        "--no-acls",
        "--no-selinux",
        "--no-xattrs",
        "-cf",
        "-",
        archive_root,
    ]
    xz_command = ["xz", "--threads=1", "--check=crc64", "-9e", "-c"]
    environment = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as archive_handle:
        tar_process = subprocess.Popen(
            tar_command,
            cwd=source_parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        assert tar_process.stdout is not None
        xz_process = subprocess.Popen(
            xz_command,
            stdin=tar_process.stdout,
            stdout=archive_handle,
            stderr=subprocess.PIPE,
            env=environment,
        )
        tar_process.stdout.close()
        _, xz_stderr = xz_process.communicate()
        tar_stderr = tar_process.stderr.read() if tar_process.stderr else b""
        if tar_process.stderr:
            tar_process.stderr.close()
        tar_status = tar_process.wait()
        if tar_status != 0 or xz_process.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise BuildError(
                "canonical archive failed: "
                f"tar={tar_status} {tar_stderr.decode(errors='replace').strip()} "
                f"xz={xz_process.returncode} {xz_stderr.decode(errors='replace').strip()}"
            )
    temporary.replace(output)


def compress_tar(uncompressed: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    environment = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    with uncompressed.open("rb") as source, temporary.open("wb") as destination:
        result = subprocess.run(
            ["xz", "--threads=1", "--check=crc64", "-9e", "-c"],
            stdin=source,
            stdout=destination,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise BuildError(
            f"xz compression failed: {result.stderr.decode(errors='replace').strip()}"
        )
    temporary.replace(output)


def normalized_tar_info(
    member: tarfile.TarInfo,
    name: str,
    epoch: int,
    *,
    linkname: str | None = None,
) -> tarfile.TarInfo:
    info = copy.copy(member)
    info.name = name
    if linkname is not None:
        info.linkname = linkname
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = epoch
    info.pax_headers = {
        key: value
        for key, value in member.pax_headers.items()
        if key not in {"path", "linkpath", "mtime", "atime", "ctime", "uname", "gname"}
    }
    return info


def canonical_overlay_archive(
    base_archive: Path,
    base_root: str,
    overlay_root: Path,
    archive_root: str,
    output: Path,
    epoch: int,
) -> None:
    """Copy a v4 release tar losslessly, normalize metadata, and append the overlay."""

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="liskov-runtime-images-v4-tar-") as temporary:
        uncompressed = Path(temporary) / "rootfs.tar"
        existing: set[str] = set()
        with open_tar(base_archive) as source, tarfile.open(
            uncompressed, mode="w", format=tarfile.PAX_FORMAT
        ) as destination:
            for member in source:
                relative = normalized_member_name(member.name, base_root)
                name = archive_root if not relative else f"{archive_root}/{relative}"
                if name in existing:
                    raise BuildError(f"duplicate base archive member: {name}")
                existing.add(name)
                linkname = None
                if member.islnk():
                    link_relative = normalized_member_name(member.linkname, base_root)
                    linkname = (
                        archive_root
                        if not link_relative
                        else f"{archive_root}/{link_relative}"
                    )
                info = normalized_tar_info(
                    member, name, epoch, linkname=linkname
                )
                file_object = source.extractfile(member) if member.isfile() else None
                destination.addfile(info, file_object)

            required_directories: set[str] = set()
            for relative in OVERLAY_PATHS:
                path = PurePosixPath(archive_root) / relative
                for parent in path.parents:
                    parent_name = str(parent)
                    if parent_name == "." or parent_name == archive_root:
                        break
                    required_directories.add(parent_name)
            for directory_name in sorted(required_directories):
                if directory_name in existing:
                    continue
                info = tarfile.TarInfo(directory_name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.uid = 0
                info.gid = 0
                info.mtime = epoch
                destination.addfile(info)
                existing.add(directory_name)

            for relative in sorted(OVERLAY_PATHS):
                name = f"{archive_root}/{relative}"
                if name in existing:
                    raise BuildError(f"base archive already contains overlay member: {name}")
                source_path = overlay_root / relative
                if not source_path.is_file():
                    raise BuildError(f"overlay source file is missing: {relative}")
                info = tarfile.TarInfo(name)
                info.size = source_path.stat().st_size
                info.mode = stat.S_IMODE(source_path.stat().st_mode)
                info.uid = 0
                info.gid = 0
                info.mtime = epoch
                with source_path.open("rb") as file_object:
                    destination.addfile(info, file_object)
                existing.add(name)
        compress_tar(uncompressed, output)


def created_at(epoch: int) -> str:
    return (
        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build(target: str, output_dir: Path, cache_dir: Path) -> list[Path]:
    lock = json.loads(LOCK_PATH.read_bytes())
    if lock.get("schemaVersion") != 1:
        raise BuildError("unsupported sources.lock.json schemaVersion")
    images = lock.get("images")
    if not isinstance(images, dict) or target not in images:
        raise BuildError(f"unknown target {target!r}; expected one of {sorted(images or {})}")
    image = images[target]
    helper = lock["helper"]
    epoch = lock["sourceDateEpoch"]
    fixed_created_at = created_at(epoch)

    helper_archive = download(
        helper["archiveUrl"],
        cache_path(cache_dir, helper["archiveSha256"], ".helper.tar.gz"),
        helper["archiveSha256"],
        "liskov-runtime-contact release archive",
    )
    verify_size(
        helper_archive,
        helper["archiveSize"],
        "liskov-runtime-contact release archive",
    )

    with tempfile.TemporaryDirectory(prefix=f"liskov-runtime-images-{target}-") as temporary:
        work = Path(temporary)
        root = work / "rootfs"
        materials: list[dict[str, Any]]
        omitted: list[str]
        v4_source_archive: Path | None = None

        if image["kind"] == "termux-rootfs":
            source_archive = download(
                image["sourceUrl"],
                cache_path(cache_dir, image["sourceSha256"], ".rootfs.tar.xz"),
                image["sourceSha256"],
                f"{target} rootfs",
            )
            root.mkdir()
            omitted = extract_archive(
                source_archive,
                root,
                strip_prefix=image["sourceRoot"],
                ignore_xattrs=True,
            )
            v4_source_archive = source_archive
            materials = [
                {
                    "uri": image["sourceUrl"],
                    "digest": {"sha256": image["sourceSha256"]},
                    "mediaType": "application/x-xz",
                }
            ]
        elif image["kind"] == "oci":
            root.mkdir()
            materials, omitted = materialize_oci(image, root, cache_dir)
        else:
            raise BuildError(f"unsupported image kind: {image['kind']}")

        base_records = inventory(root)
        base_inventory_digest = inventory_digest(base_records)

        helper_root = work / "helper"
        helper_root.mkdir()
        extract_archive(helper_archive, helper_root)
        helper_binary = helper_root / helper["binaryPath"]
        helper_license = helper_root / helper["licensePath"]
        if not helper_binary.is_file() or not helper_license.is_file():
            raise BuildError("helper release archive is missing its binary or license")
        verify_digest(helper_binary, helper["binarySha256"], "liskov-runtime-contact binary")
        verify_size(helper_binary, helper["binarySize"], "liskov-runtime-contact binary")

        binary_destination = root / HELPER_PATH
        license_destination = root / HELPER_LICENSE_PATH
        shim_library_destination = root / GETIFADDRS_OVERRIDE_LIBRARY_PATH
        shim_source_destination = root / GETIFADDRS_OVERRIDE_SOURCE_PATH
        provenance_destination = root / PROVENANCE_PATH
        for destination in (
            binary_destination,
            license_destination,
            shim_library_destination,
            shim_source_destination,
            provenance_destination,
        ):
            if destination.exists() or destination.is_symlink():
                raise BuildError(f"base image already contains overlay path {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(helper_binary, binary_destination)
        os.chmod(binary_destination, 0o755)
        shutil.copyfile(helper_license, license_destination)
        os.chmod(license_destination, 0o644)
        build_getifaddrs_override(shim_library_destination)
        shutil.copyfile(GETIFADDRS_OVERRIDE_SOURCE, shim_source_destination)
        os.chmod(shim_source_destination, 0o644)
        shim_source_digest = sha256_file(shim_source_destination)
        shim_library_digest = sha256_file(shim_library_destination)

        in_image_provenance = {
            "domain": "proof.liskov.runtime-image.provenance.v1",
            "target": target,
            "supportStatus": image["supportStatus"],
            "archiveRoot": image["archiveRoot"],
            "sourceDateEpoch": epoch,
            "baseInventorySha256": base_inventory_digest,
            "materials": materials,
            "prootDistroCompatibility": lock["prootDistroCompatibility"],
            "overlay": {
                "helperVersion": helper["version"],
                "helperReleaseCommit": helper["releaseCommit"],
                "helperArchiveSha256": helper["archiveSha256"],
                "helperArchiveSize": helper["archiveSize"],
                "helperBinarySha256": helper["binarySha256"],
                "helperBinarySize": helper["binarySize"],
                "networkInterfaceOverride": {
                    "contract": "loopback-only-getifaddrs-v1",
                    "sourcePath": GETIFADDRS_OVERRIDE_SOURCE_PATH,
                    "sourceSha256": shim_source_digest,
                    "libraryPath": GETIFADDRS_OVERRIDE_LIBRARY_PATH,
                    "librarySha256": shim_library_digest,
                    "preload": "conditional-bootstrap-export",
                    "references": [
                        "https://docs.acurast.com/developers/build/cargo-runtime-environment/#network-interfaces-getifaddrs",
                        "https://github.com/Acurast/acurast-example-apps/tree/main/apps/app-cargo-openclaw/app",
                    ],
                },
                "paths": list(OVERLAY_PATHS),
            },
            "materialization": {
                "builder": "scripts/build-image.py",
                "numericOwner": "0:0",
                "normalizedMtime": epoch,
                "xattrsPreservedInReleaseArchive": image["kind"] == "termux-rootfs",
                "analysisStagingOmittedSpecialFiles": sorted(omitted),
            },
        }
        provenance_destination.write_bytes(canonical_json(in_image_provenance))
        os.chmod(provenance_destination, 0o644)

        archive_parent = work / "archive"
        archive_parent.mkdir()
        archive_root = archive_parent / image["archiveRoot"]
        root.rename(archive_root)

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = image["outputStem"]
        archive_output = output_dir / f"{stem}.tar.xz"
        if v4_source_archive is not None:
            canonical_overlay_archive(
                v4_source_archive,
                image["sourceRoot"],
                archive_root,
                image["archiveRoot"],
                archive_output,
                epoch,
            )
        else:
            canonical_archive(
                archive_parent, image["archiveRoot"], archive_output, epoch
            )
        archive_digest = sha256_file(archive_output)
        final_records = archive_inventory(archive_output, image["archiveRoot"])
        final_inventory_digest = inventory_digest(final_records)
        overlay_records = [
            record for record in final_records if record["path"] in OVERLAY_PATHS
        ]
        if [record["path"] for record in overlay_records] != sorted(OVERLAY_PATHS):
            raise BuildError("final overlay path set is incomplete")

        inventory_output = output_dir / f"{stem}.files.json"
        inventory_output.write_bytes(canonical_json(final_records))
        overlay_output = output_dir / f"{stem}.overlay.json"
        overlay_output.write_bytes(
            canonical_json(
                {
                    "domain": "proof.liskov.runtime-image.overlay.v1",
                    "target": target,
                    "baseInventorySha256": base_inventory_digest,
                    "finalInventorySha256": final_inventory_digest,
                    "paths": overlay_records,
                }
            )
        )
        provenance_output = output_dir / f"{stem}.provenance.json"
        provenance_output.write_bytes(
            canonical_json(
                {
                    **in_image_provenance,
                    "artifact": {
                        "file": archive_output.name,
                        "sha256": archive_digest,
                        "byteSize": archive_output.stat().st_size,
                        "finalInventorySha256": final_inventory_digest,
                    },
                }
            )
        )
        sbom_output = output_dir / f"{stem}.spdx.json"
        sbom_output.write_bytes(
            canonical_json(
                spdx_document(
                    target,
                    image,
                    helper,
                    shim_library_digest,
                    archive_root,
                    final_inventory_digest,
                    fixed_created_at,
                )
            )
        )

    outputs = [
        archive_output,
        inventory_output,
        overlay_output,
        provenance_output,
        sbom_output,
    ]
    for output in outputs:
        print(f"{sha256_file(output)}  {output}")
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="Image key from sources.lock.json")
    parser.add_argument(
        "--output-dir", type=Path, default=REPOSITORY_ROOT / "out", help="Output directory"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=REPOSITORY_ROOT / ".cache/downloads",
        help="Verified download cache",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        build(args.target, args.output_dir.resolve(), args.cache_dir.resolve())
    except (BuildError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"build-image: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
