#!/usr/bin/env python3
"""Classify changes and bind release intent to the governed runtime-image inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
RELEASE_INTENT_PATH = "release-intent.json"
TARGETS = ("debian-trixie", "v4-control")
FINGERPRINT_DOMAIN = "proof.liskov.runtime-image.material-inputs.v1"
MATERIAL_EXACT = {
    "sources.lock.json",
    "tests/acurast.sh.template",
    "tests/bridge-smoke-server.py",
    "tests/runtime-contact-release.json",
}
MATERIAL_PREFIXES = (
    "assets/",
    "scripts/",
)
FAST_EXACT = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
}
FAST_PREFIXES = (
    ".github/",
    ".liskov/",
    "tests/",
)
VERSION_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)


class ClassificationError(RuntimeError):
    """A fail-closed classification or release-intent error."""


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_material_path(path: str) -> bool:
    return path in MATERIAL_EXACT or path.startswith(MATERIAL_PREFIXES)


def is_fast_path(path: str) -> bool:
    return path in FAST_EXACT or path.startswith(FAST_PREFIXES)


def material_paths(root: Path) -> list[str]:
    candidates: list[Path]
    if (root / ".git").exists():
        listed = git(
            root,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        )
        candidates = [root / relative for relative in decode_paths(listed)]
    else:
        candidates = list(root.rglob("*"))
    paths: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if (
            is_material_path(relative)
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        ):
            paths.append(relative)
    return sorted(paths)


def material_fingerprint(root: Path = REPOSITORY_ROOT) -> str:
    records = [
        {"path": relative, "sha256": sha256_file(root / relative)}
        for relative in material_paths(root)
    ]
    if not records:
        raise ClassificationError("no governed material inputs were found")
    payload = {"domain": FINGERPRINT_DOMAIN, "materials": records}
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def validate_release_intent(
    root: Path = REPOSITORY_ROOT,
    *,
    expected_tag: str | None = None,
) -> dict[str, object]:
    path = root / RELEASE_INTENT_PATH
    try:
        intent = json.loads(path.read_bytes())
    except FileNotFoundError as error:
        raise ClassificationError(f"{RELEASE_INTENT_PATH} is missing") from error
    except json.JSONDecodeError as error:
        raise ClassificationError(f"{RELEASE_INTENT_PATH} is not valid JSON") from error
    if not isinstance(intent, dict):
        raise ClassificationError(f"{RELEASE_INTENT_PATH} must be a JSON object")
    expected_keys = {
        "schemaVersion",
        "version",
        "materialInputFingerprint",
        "targets",
    }
    if set(intent) != expected_keys:
        raise ClassificationError(
            f"{RELEASE_INTENT_PATH} keys must be exactly {sorted(expected_keys)}"
        )
    if intent["schemaVersion"] != 1:
        raise ClassificationError("release-intent schemaVersion must be 1")
    version = intent["version"]
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ClassificationError("release-intent version must be a v-prefixed SemVer")
    if expected_tag is not None and version != expected_tag:
        raise ClassificationError(
            f"release tag {expected_tag!r} does not match declared version {version!r}"
        )
    targets = intent["targets"]
    if targets != list(TARGETS):
        raise ClassificationError(
            f"release-intent targets must be exactly {list(TARGETS)}"
        )
    fingerprint = material_fingerprint(root)
    if intent["materialInputFingerprint"] != fingerprint:
        raise ClassificationError(
            "release-intent materialInputFingerprint does not match the current governed inputs: "
            f"expected {fingerprint}"
        )
    return intent


def classify_paths(paths: Iterable[str], root: Path = REPOSITORY_ROOT) -> dict[str, object]:
    changed = sorted({path.strip("/") for path in paths if path.strip("/")})
    if RELEASE_INTENT_PATH in changed:
        intent = validate_release_intent(root)
        return {
            "mode": "release",
            "targets": list(TARGETS),
            "materialInputFingerprint": intent["materialInputFingerprint"],
            "releaseVersion": intent["version"],
            "changedPaths": changed,
        }
    material = [
        path for path in changed if is_material_path(path) or not is_fast_path(path)
    ]
    return {
        "mode": "material" if material else "fast",
        "targets": list(TARGETS) if material else [],
        "materialInputFingerprint": material_fingerprint(root),
        "releaseVersion": "",
        "changedPaths": changed,
    }


def git_changed_paths(root: Path, base: str, head: str) -> list[str]:
    if head == "WORKTREE":
        tracked = git(root, "diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", base)
        untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
        return decode_paths(tracked + untracked)
    if not base or set(base) == {"0"}:
        output = git(root, "ls-tree", "-r", "--name-only", "-z", head)
    else:
        output = git(
            root,
            "diff",
            "--name-only",
            "-z",
            "--diff-filter=ACDMRTUXB",
            base,
            head,
        )
    return decode_paths(output)


def git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ClassificationError(
            f"git {' '.join(arguments)} failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def decode_paths(value: bytes) -> list[str]:
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in value.split(b"\0")
        if path
    ]


def write_github_output(path: Path, result: dict[str, object]) -> None:
    values = {
        "mode": result["mode"],
        "targets-json": json.dumps(result["targets"], separators=(",", ":")),
        "material-fingerprint": result["materialInputFingerprint"],
        "release-version": result["releaseVersion"],
    }
    with path.open("a", encoding="utf-8") as handle:
        for name, value in values.items():
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--base", required=True)
    classify_parser.add_argument("--head", required=True)
    classify_parser.add_argument("--github-output", type=Path)
    classify_parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)

    fingerprint_parser = subparsers.add_parser("fingerprint")
    fingerprint_parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)

    intent_parser = subparsers.add_parser("validate-release-intent")
    intent_parser.add_argument("--release-tag")
    intent_parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)

    args = parser.parse_args()
    try:
        if args.command == "fingerprint":
            print(material_fingerprint(args.root.resolve()))
            return 0
        if args.command == "validate-release-intent":
            intent = validate_release_intent(
                args.root.resolve(),
                expected_tag=args.release_tag,
            )
            print(json.dumps(intent, sort_keys=True))
            return 0
        root = args.root.resolve()
        result = classify_paths(
            git_changed_paths(root, args.base, args.head),
            root,
        )
        print(json.dumps(result, sort_keys=True))
        if args.github_output is not None:
            write_github_output(args.github_output, result)
        return 0
    except ClassificationError as error:
        print(f"change classification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
