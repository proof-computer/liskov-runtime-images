#!/usr/bin/env python3
"""Create and validate a commit-bound runtime-image release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "BUILD-MANIFEST.json"
CHECKSUMS_NAME = "SHA256SUMS"
TARGETS = ("debian-trixie", "v4-control")
SUFFIXES = (
    ".tar.xz",
    ".files.json",
    ".overlay.json",
    ".provenance.json",
    ".spdx.json",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ManifestError(RuntimeError):
    """A malformed, incomplete, or mismatched bundle."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_stems(root: Path = REPOSITORY_ROOT) -> dict[str, str]:
    try:
        lock = json.loads((root / "sources.lock.json").read_bytes())
        images = lock["images"]
        stems = {target: images[target]["outputStem"] for target in TARGETS}
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ManifestError("sources.lock.json does not declare every release target") from error
    if not all(isinstance(stem, str) and stem for stem in stems.values()):
        raise ManifestError("every release target must have a non-empty outputStem")
    return stems


def expected_payloads(root: Path = REPOSITORY_ROOT) -> dict[str, tuple[str, ...]]:
    stems = target_stems(root)
    return {
        target: tuple(f"{stems[target]}{suffix}" for suffix in SUFFIXES)
        for target in TARGETS
    }


def regular_files(bundle_dir: Path) -> set[str]:
    if not bundle_dir.is_dir():
        raise ManifestError(f"bundle directory does not exist: {bundle_dir}")
    names: set[str] = set()
    for path in bundle_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"bundle contains a non-regular entry: {path.name}")
        names.add(path.name)
    return names


def positive_integer(value: str, field: str) -> int:
    if not value.isdigit() or int(value) <= 0:
        raise ManifestError(f"{field} must be a positive integer")
    return int(value)


def validate_metadata(
    *,
    repository: str,
    source_commit: str,
    material_fingerprint: str,
    version: str,
    workflow_path: str,
    workflow_ref: str,
    run_id: str,
    run_attempt: str,
) -> None:
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ManifestError("repository must be owner/repo")
    if COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ManifestError("source commit must be a full lowercase SHA-1")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", material_fingerprint) is None:
        raise ManifestError("material fingerprint must be sha256:<lowercase hex>")
    if not version.startswith("v") or any(character.isspace() for character in version):
        raise ManifestError("release version must be a whitespace-free v-prefixed value")
    if not workflow_path.startswith(".github/workflows/"):
        raise ManifestError("workflow path must be repository-relative")
    expected_prefix = f"{repository}/{workflow_path}@"
    if not workflow_ref.startswith(expected_prefix):
        raise ManifestError("workflow ref does not bind the declared repository and path")
    positive_integer(run_id, "workflow run id")
    positive_integer(run_attempt, "workflow run attempt")


def create_manifest(
    bundle_dir: Path,
    *,
    repository: str,
    source_commit: str,
    material_fingerprint: str,
    version: str,
    workflow_path: str,
    workflow_ref: str,
    run_id: str,
    run_attempt: str,
    root: Path = REPOSITORY_ROOT,
) -> dict[str, object]:
    validate_metadata(
        repository=repository,
        source_commit=source_commit,
        material_fingerprint=material_fingerprint,
        version=version,
        workflow_path=workflow_path,
        workflow_ref=workflow_ref,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    expected = expected_payloads(root)
    payload_names = {name for names in expected.values() for name in names}
    actual = regular_files(bundle_dir)
    if actual != payload_names:
        raise ManifestError(
            f"release payload set mismatch: expected {sorted(payload_names)}, got {sorted(actual)}"
        )
    files: list[dict[str, object]] = []
    target_records: list[dict[str, object]] = []
    for target in TARGETS:
        names = expected[target]
        target_records.append({"name": target, "files": list(names)})
        for name in names:
            path = bundle_dir / name
            files.append(
                {
                    "name": name,
                    "target": target,
                    "byteSize": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "release": {"version": version},
        "source": {
            "repository": repository,
            "commit": source_commit,
            "materialInputFingerprint": material_fingerprint,
        },
        "workflow": {
            "name": "CI",
            "path": workflow_path,
            "ref": workflow_ref,
            "runId": positive_integer(run_id, "workflow run id"),
            "runAttempt": positive_integer(run_attempt, "workflow run attempt"),
        },
        "targets": target_records,
        "files": files,
    }
    (bundle_dir / MANIFEST_NAME).write_bytes(canonical_json(manifest))
    checksum_names = sorted(payload_names | {MANIFEST_NAME})
    checksum_lines = [
        f"{sha256_file(bundle_dir / name)}  {name}\n" for name in checksum_names
    ]
    (bundle_dir / CHECKSUMS_NAME).write_text("".join(checksum_lines), encoding="utf-8")
    return manifest


def require_object(value: object, field: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError(f"{field} must be an object with exactly {sorted(keys)}")
    return value


def validate_manifest(
    bundle_dir: Path,
    *,
    repository: str,
    source_commit: str,
    material_fingerprint: str,
    version: str,
    workflow_path: str,
    run_id: str,
    root: Path = REPOSITORY_ROOT,
) -> dict[str, object]:
    expected = expected_payloads(root)
    payload_names = {name for names in expected.values() for name in names}
    final_names = payload_names | {MANIFEST_NAME, CHECKSUMS_NAME}
    actual_names = regular_files(bundle_dir)
    if actual_names != final_names:
        raise ManifestError(
            f"bundle file set mismatch: expected {sorted(final_names)}, got {sorted(actual_names)}"
        )
    try:
        manifest_value = json.loads((bundle_dir / MANIFEST_NAME).read_bytes())
    except json.JSONDecodeError as error:
        raise ManifestError("BUILD-MANIFEST.json is malformed") from error
    manifest = require_object(
        manifest_value,
        "manifest",
        {"schemaVersion", "release", "source", "workflow", "targets", "files"},
    )
    if manifest["schemaVersion"] != 1:
        raise ManifestError("unsupported BUILD-MANIFEST schemaVersion")
    release = require_object(manifest["release"], "release", {"version"})
    source = require_object(
        manifest["source"],
        "source",
        {"repository", "commit", "materialInputFingerprint"},
    )
    workflow = require_object(
        manifest["workflow"],
        "workflow",
        {"name", "path", "ref", "runId", "runAttempt"},
    )
    if release["version"] != version:
        raise ManifestError("release version mismatch")
    if source["repository"] != repository:
        raise ManifestError("source repository mismatch")
    if source["commit"] != source_commit:
        raise ManifestError("source commit mismatch")
    if source["materialInputFingerprint"] != material_fingerprint:
        raise ManifestError("material fingerprint mismatch")
    if workflow["name"] != "CI" or workflow["path"] != workflow_path:
        raise ManifestError("signer workflow mismatch")
    if workflow["runId"] != positive_integer(run_id, "expected workflow run id"):
        raise ManifestError("workflow run mismatch")
    if not isinstance(workflow["runAttempt"], int) or workflow["runAttempt"] <= 0:
        raise ManifestError("workflow runAttempt must be a positive integer")
    expected_ref_prefix = f"{repository}/{workflow_path}@"
    if not isinstance(workflow["ref"], str) or not workflow["ref"].startswith(
        expected_ref_prefix
    ):
        raise ManifestError("workflow ref mismatch")

    expected_targets = [
        {"name": target, "files": list(expected[target])} for target in TARGETS
    ]
    if manifest["targets"] != expected_targets:
        raise ManifestError("release target set is missing, additional, or incomplete")
    records = manifest["files"]
    if not isinstance(records, list) or len(records) != len(payload_names):
        raise ManifestError("manifest files must contain every payload exactly once")
    seen: set[str] = set()
    for record_value in records:
        record = require_object(
            record_value,
            "file record",
            {"name", "target", "byteSize", "sha256"},
        )
        name = record["name"]
        target = record["target"]
        if not isinstance(name, str) or name in seen:
            raise ManifestError("manifest file names must be unique strings")
        if target not in TARGETS or name not in expected[target]:
            raise ManifestError(f"manifest file {name!r} is not valid for target {target!r}")
        path = bundle_dir / name
        if record["byteSize"] != path.stat().st_size:
            raise ManifestError(f"byte size mismatch for {name}")
        digest = record["sha256"]
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            raise ManifestError(f"malformed SHA-256 for {name}")
        if digest != sha256_file(path):
            raise ManifestError(f"SHA-256 mismatch for {name}")
        seen.add(name)
    if seen != payload_names:
        raise ManifestError("manifest payload set is incomplete")

    checksum_records: dict[str, str] = {}
    for line in (bundle_dir / CHECKSUMS_NAME).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if match is None or match.group(2) in checksum_records:
            raise ManifestError("SHA256SUMS is malformed or contains duplicates")
        checksum_records[match.group(2)] = match.group(1)
    expected_checksum_names = payload_names | {MANIFEST_NAME}
    if set(checksum_records) != expected_checksum_names:
        raise ManifestError("SHA256SUMS file set is incomplete or contains extras")
    for name, digest in checksum_records.items():
        if digest != sha256_file(bundle_dir / name):
            raise ManifestError(f"SHA256SUMS mismatch for {name}")
    return manifest


def add_common_metadata(parser: argparse.ArgumentParser, *, include_ref: bool) -> None:
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--material-fingerprint", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    if include_ref:
        parser.add_argument("--workflow-ref", required=True)
        parser.add_argument("--workflow-run-attempt", required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    add_common_metadata(create_parser, include_ref=True)
    validate_parser = subparsers.add_parser("validate")
    add_common_metadata(validate_parser, include_ref=False)
    args = parser.parse_args()
    common = {
        "bundle_dir": args.bundle_dir.resolve(),
        "repository": args.repository,
        "source_commit": args.source_commit,
        "material_fingerprint": args.material_fingerprint,
        "version": args.version,
        "workflow_path": args.workflow_path,
        "run_id": args.workflow_run_id,
        "root": args.root.resolve(),
    }
    try:
        if args.command == "create":
            manifest = create_manifest(
                **common,
                workflow_ref=args.workflow_ref,
                run_attempt=args.workflow_run_attempt,
            )
        else:
            manifest = validate_manifest(**common)
        print(json.dumps(manifest, sort_keys=True))
        return 0
    except (ManifestError, OSError) as error:
        print(f"build manifest validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
