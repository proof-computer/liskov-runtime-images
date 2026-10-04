#!/usr/bin/env python3
"""Snapshot-image trust-store checks for the QEMU/PRoot smoke."""

from __future__ import annotations

import argparse
import importlib.util
import json
import stat
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_TARGET = "debian-trixie-snapshot"
BUNDLE_PATH = Path("etc/ssl/certs/ca-certificates.crt")
HASHED_DIRECTORY = Path("etc/ssl/certs")
TRUST_CURL_STATUS = 60
NETWORK_CURL_STATUSES = frozenset({6, 7, 28})
SPEC = importlib.util.spec_from_file_location(
    "build_image", REPOSITORY_ROOT / "scripts/build-image.py"
)
assert SPEC is not None and SPEC.loader is not None
build_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_image)


class TrustProofError(RuntimeError):
    """The snapshot image did not prove its shipped trust store."""


def classify_curl_status(status: int) -> str:
    """Separate a successful request, a trust rejection, and a network failure."""

    if status == 0:
        return "ok"
    if status == TRUST_CURL_STATUS:
        return "trust"
    if status in NETWORK_CURL_STATUSES:
        return "network"
    return "unexpected"


def debian_archive_url(lock: dict[str, Any]) -> str:
    url = lock["images"][SNAPSHOT_TARGET]["snapshot"]["inReleaseUrl"]
    if not isinstance(url, str) or not url.startswith("https://"):
        raise TrustProofError("snapshot archive URL is not https")
    return url


def verification_time(fixtures: Path) -> int:
    manifest = json.loads((fixtures / "manifest.json").read_bytes())
    value = manifest["verificationTime"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TrustProofError("fixture verification time is not a positive integer")
    return value


def _installed_packages(rootfs: Path) -> set[str]:
    status_path = rootfs / "var/lib/dpkg/status"
    if not status_path.is_file():
        return set()
    installed: set[str] = set()
    fields: dict[str, str] = {}

    def finish() -> None:
        if (
            fields.get("Package")
            and fields.get("Status") == "install ok installed"
        ):
            installed.add(fields["Package"])

    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            finish()
            fields = {}
        elif not line.startswith((" ", "\t")) and ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    finish()
    return installed


def _hashed_certificate_links(rootfs: Path) -> list[Path]:
    directory = rootfs / HASHED_DIRECTORY
    if not directory.is_dir():
        return []
    links: list[Path] = []
    for entry in directory.iterdir():
        if not entry.is_symlink() or entry.suffix != ".0":
            continue
        if entry.stem and all(char in "0123456789abcdef" for char in entry.stem):
            links.append(entry)
    return links


def assert_snapshot_support(
    lock: dict[str, Any],
    sbom: dict[str, Any],
    rootfs: Path,
    target: str,
) -> None:
    """Require the snapshot image to carry Debian trust material without an overlay."""

    if target != SNAPSHOT_TARGET:
        raise TrustProofError(
            f"snapshot TLS support assertion does not apply to {target}"
        )
    include = lock["images"][target]["include"]
    if "ca-certificates" not in include:
        raise TrustProofError("snapshot include list lacks ca-certificates")
    for overlay in build_image.OVERLAY_PATHS:
        if "ssl/certs" in overlay or "ca-certificates" in overlay:
            raise TrustProofError(f"trust material is an image overlay: {overlay}")
    parsed_names = {
        package["name"] for package in build_image.parse_dpkg_status(rootfs)
    }
    if "ca-certificates" not in parsed_names:
        raise TrustProofError(
            "dpkg status would not produce a ca-certificates SBOM package"
        )
    if "ca-certificates" not in _installed_packages(rootfs):
        raise TrustProofError("ca-certificates is not installed in the rootfs")
    packages = [
        package
        for package in sbom.get("packages", [])
        if package.get("name") == "ca-certificates"
    ]
    if not packages:
        raise TrustProofError("SPDX SBOM lacks ca-certificates")
    locators = [
        ref.get("referenceLocator", "")
        for package in packages
        for ref in package.get("externalRefs", [])
    ]
    if not any(
        locator.startswith("pkg:deb/debian/ca-certificates@") for locator in locators
    ):
        raise TrustProofError("SPDX SBOM lacks a Debian ca-certificates package URL")
    bundle = rootfs / BUNDLE_PATH
    try:
        info = bundle.lstat()
    except FileNotFoundError as error:
        raise TrustProofError("CA bundle is missing") from error
    if stat.S_ISREG(info.st_mode):
        if info.st_size == 0:
            raise TrustProofError("CA bundle is empty")
    elif not stat.S_ISLNK(info.st_mode):
        raise TrustProofError("CA bundle is not a file")
    if not _hashed_certificate_links(rootfs):
        raise TrustProofError(
            "hashed certificate directory has no subject-hash links"
        )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TrustProofError(f"{path} is not a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    support = commands.add_parser("support")
    support.add_argument("--lock", required=True, type=Path)
    support.add_argument("--sbom", required=True, type=Path)
    support.add_argument("--rootfs", required=True, type=Path)
    support.add_argument("--target", required=True)

    archive_url = commands.add_parser("archive-url")
    archive_url.add_argument("--lock", required=True, type=Path)

    timestamp = commands.add_parser("verification-time")
    timestamp.add_argument("--fixtures", required=True, type=Path)

    classify = commands.add_parser("classify-curl")
    classify.add_argument("--status", required=True, type=int)

    args = parser.parse_args(argv)
    try:
        if args.command == "support":
            assert_snapshot_support(
                _load_json(args.lock),
                _load_json(args.sbom),
                args.rootfs,
                args.target,
            )
        elif args.command == "archive-url":
            print(debian_archive_url(_load_json(args.lock)))
        elif args.command == "verification-time":
            print(verification_time(args.fixtures))
        elif args.command == "classify-curl":
            print(classify_curl_status(args.status))
        else:
            raise TrustProofError(f"unknown command: {args.command}")
    except TrustProofError as error:
        print(f"snapshot TLS trust: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
