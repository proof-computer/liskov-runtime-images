from __future__ import annotations

import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "build_image", REPOSITORY_ROOT / "scripts/build-image.py"
)
assert SPEC is not None and SPEC.loader is not None
build_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_image)


class SourceLockTests(unittest.TestCase):
    def test_lock_is_digest_pinned_and_has_two_distinct_roles(self) -> None:
        lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
        self.assertEqual(lock["schemaVersion"], 1)
        self.assertEqual(
            set(lock["images"]),
            {"v4-control", "debian-trixie"},
        )
        self.assertEqual(
            lock["images"]["v4-control"]["supportStatus"],
            "compatibility-control",
        )
        self.assertEqual(
            lock["images"]["debian-trixie"]["supportStatus"],
            "release-candidate",
        )
        for digest in (
            lock["helper"]["archiveSha256"],
            lock["helper"]["binarySha256"],
            lock["images"]["v4-control"]["sourceSha256"],
        ):
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        build_image.digest_hex(
            lock["images"]["debian-trixie"]["manifestDigest"],
            "manifestDigest",
        )


class ExtractionTests(unittest.TestCase):
    def write_tar(self, path: Path, entries: list[tuple[str, bytes]]) -> None:
        with tarfile.open(path, "w") as archive:
            for name, value in entries:
                member = tarfile.TarInfo(name)
                member.size = len(value)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(value))

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.tar"
            self.write_tar(archive, [("../escape", b"no")])
            with self.assertRaises(build_image.BuildError):
                build_image.extract_archive(archive, root / "out")

    def test_oci_whiteout_removes_only_lower_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            (output / "old").write_text("lower", encoding="utf-8")
            archive = root / "layer.tar"
            self.write_tar(
                archive,
                [
                    (".wh.old", b""),
                    ("new", b"upper"),
                ],
            )
            build_image.extract_archive(archive, output, apply_whiteouts=True)
            self.assertFalse((output / "old").exists())
            self.assertEqual((output / "new").read_bytes(), b"upper")

    def test_rejects_write_through_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            os.symlink("/tmp", output / "escape")
            archive = root / "layer.tar"
            self.write_tar(archive, [("escape/file", b"no")])
            with self.assertRaises(build_image.BuildError):
                build_image.extract_archive(archive, output)


class ReproducibilityTests(unittest.TestCase):
    def test_canonical_archive_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "source"
            image = parent / "rootfs"
            image.mkdir(parents=True)
            (image / "file").write_text("content", encoding="utf-8")
            os.symlink("file", image / "link")
            first = root / "first.tar.xz"
            second = root / "second.tar.xz"
            build_image.canonical_archive(parent, "rootfs", first, 1783900800)
            os.utime(image / "file", (1, 1))
            build_image.canonical_archive(parent, "rootfs", second, 1783900800)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_inventory_binds_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.symlink("target", root / "link")
            records = build_image.inventory(root)
            self.assertEqual(records[0]["type"], "symlink")
            self.assertEqual(records[0]["target"], "target")
            self.assertEqual(
                records[0]["sha256"],
                build_image.sha256_bytes(b"symlink:target"),
            )


if __name__ == "__main__":
    unittest.main()
