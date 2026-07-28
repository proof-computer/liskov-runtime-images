#!/usr/bin/env python3
"""Compare our locked OCI materialization with exact PRoot-Distro v5 output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "build_image", REPOSITORY_ROOT / "scripts/build-image.py"
)
assert SPEC is not None and SPEC.loader is not None
build_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_image)


def comparable_inventory(root: Path) -> list[dict[str, object]]:
    records = build_image.inventory(root)
    return [
        {
            key: value
            for key, value in record.items()
            if key in {"path", "mode", "type", "size", "sha256", "target"}
        }
        for record in records
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proot-distro-source", type=Path, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=REPOSITORY_ROOT / ".cache/downloads",
    )
    args = parser.parse_args()

    lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
    expected_commit = lock["prootDistroCompatibility"]["commit"]
    source = args.proot_distro_source.resolve()
    actual_commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_commit != expected_commit:
        raise SystemExit(
            f"PRoot-Distro checkout is {actual_commit}, expected {expected_commit}"
        )
    sys.path.insert(0, str(source))
    from proot_distro.helpers.tar_extract import extract_tar_to_rootfs

    image = lock["images"]["debian-trixie"]
    token = build_image.registry_token(image)
    layer = image["layers"][0]
    layer_hex = build_image.digest_hex(layer["digest"], "layer.digest")
    layer_path = build_image.registry_blob(
        image,
        layer["digest"],
        build_image.cache_path(args.cache_dir, layer_hex, ".layer.tar.gz"),
        token,
        "OCI layer",
    )

    with tempfile.TemporaryDirectory(
        prefix="liskov-runtime-images-proot-v5-"
    ) as temporary:
        comparison = Path(temporary)
        ours = comparison / "ours"
        upstream = comparison / "upstream"
        ours.mkdir()
        upstream.mkdir()
        build_image.extract_archive(layer_path, ours, apply_whiteouts=True)
        extract_tar_to_rootfs(
            str(layer_path),
            str(upstream),
            handle_whiteouts=True,
        )
        ours_inventory = comparable_inventory(ours)
        upstream_inventory = comparable_inventory(upstream)
        if ours_inventory != upstream_inventory:
            ours_by_path = {record["path"]: record for record in ours_inventory}
            upstream_by_path = {
                record["path"]: record for record in upstream_inventory
            }
            paths = sorted(set(ours_by_path) | set(upstream_by_path))
            differences = [
                {
                    "path": path,
                    "ours": ours_by_path.get(path),
                    "prootDistro": upstream_by_path.get(path),
                }
                for path in paths
                if ours_by_path.get(path) != upstream_by_path.get(path)
            ]
            print(
                json.dumps(
                    {"ok": False, "differences": differences[:20]},
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1

    print(
        json.dumps(
            {
                "ok": True,
                "prootDistroCommit": expected_commit,
                "layerDigest": layer["digest"],
                "entryCount": len(ours_inventory),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
