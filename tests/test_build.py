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
        self.assertEqual(lock["helper"]["version"], "0.2.3")
        self.assertEqual(
            lock["helper"]["releaseCommit"],
            "dd092b782cffa2f199bf24bcc1b34a6ec8c2d7fd",
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

    def test_overlay_includes_owned_getifaddrs_source_and_library(self) -> None:
        self.assertEqual(
            build_image.OVERLAY_PATHS,
            (
                "usr/local/bin/liskov-runtime-contact",
                "usr/share/doc/liskov-runtime-contact/LICENSE",
                "usr/local/lib/libgetifaddrs_override.so",
                "usr/share/liskov-runtime-images/getifaddrs_override.c",
                "usr/share/liskov-runtime-images/provenance.json",
            ),
        )
        source = build_image.GETIFADDRS_OVERRIDE_SOURCE.read_text(encoding="utf-8")
        self.assertIn("int getifaddrs(struct ifaddrs **interfaces)", source)
        self.assertIn("void freeifaddrs(struct ifaddrs *interfaces)", source)
        self.assertIn('strdup("lo")', source)
        self.assertNotIn("Acurast", source)

    def test_release_notes_track_the_locked_helper_version(self) -> None:
        lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_text())
        release_workflow = (
            REPOSITORY_ROOT / ".github/workflows/release.yml"
        ).read_text()
        self.assertIn(
            f"`liskov-runtime-contact\\` v{lock['helper']['version']} binary",
            release_workflow,
        )

    def test_native_verifier_reads_the_locked_helper_version(self) -> None:
        verifier = (
            REPOSITORY_ROOT / "scripts/verify-native-aarch64.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('lock["helper"]["version"]', verifier)
        self.assertIn(
            'liskov-runtime-contact ${expected_helper_version}',
            verifier,
        )
        self.assertNotRegex(verifier, r"liskov-runtime-contact 0\.\d+\.\d+")

    def test_shared_object_verifier_rejects_wrong_machine_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "shim.so"
            elf = bytearray(64)
            elf[:4] = b"\x7fELF"
            elf[4] = 2
            elf[5] = 1
            elf[16:18] = (3).to_bytes(2, "little")
            elf[18:20] = (183).to_bytes(2, "little")
            path.write_bytes(elf)
            build_image.verify_aarch64_shared_object(path)
            elf[18:20] = (62).to_bytes(2, "little")
            path.write_bytes(elf)
            with self.assertRaises(build_image.BuildError):
                build_image.verify_aarch64_shared_object(path)

    def test_compiler_staging_path_is_not_part_of_the_rootfs(self) -> None:
        source = (
            REPOSITORY_ROOT / "scripts/build-image.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'TemporaryDirectory(prefix="liskov-getifaddrs-build-")',
            source,
        )
        self.assertNotIn(
            'destination.with_name("getifaddrs_override.c")',
            source,
        )


class CanaryContractTests(unittest.TestCase):
    def load_manifest(self, name: str) -> dict[str, object]:
        return json.loads(
            (REPOSITORY_ROOT / ".liskov" / name).read_text(encoding="utf-8")
        )

    def test_probe_is_exactly_bounded_and_final_canaries_do_not_nest_helper(self) -> None:
        probe = self.load_manifest("canary-v4-bridge-probe.json")
        v4 = self.load_manifest("canary-v4-control.json")
        debian = self.load_manifest("canary-debian-trixie.json")

        self.assertEqual(
            probe["runtime"]["command"],
            "/usr/local/bin/liskov-runtime-contact --bridge-probe -- /bin/true",
        )
        for manifest in (probe, v4, debian):
            self.assertEqual(
                manifest["deployment"]["schedule"],
                {
                    "durationMs": 900000,
                    "startDelayMs": 300000,
                    "maxStartDelayMs": 300000,
                },
            )
            self.assertEqual(
                manifest["deployment"]["lifecycle"]["recovery"]["launch"]["maxRetries"],
                0,
            )
            self.assertEqual(
                manifest["deployment"]["spend"],
                {
                    "maxRewardPlanckPerJob": "40000000000",
                    "maxNativeFeePlanckPerJob": "10000000000",
                },
            )
        for manifest in (v4, debian):
            self.assertNotIn(
                "liskov-runtime-contact",
                manifest["runtime"]["command"],
            )
        self.assertTrue(probe["observability"]["logs"]["enabled"])
        for manifest in (v4, debian):
            self.assertFalse(manifest["observability"]["logs"]["enabled"])

    def test_canary_workflows_pin_the_verified_rc5_archives(self) -> None:
        expected = {
            "canary-v4-bridge-probe.yml": (
                "liskov-runtime-image-v4-control-ubuntu-questing-aarch64.tar.xz",
                "bfc0738df2829da1ed436be1500997bdb37c662c576f5d3ae872dc59894851c2",
            ),
            "canary-v4-control.yml": (
                "liskov-runtime-image-v4-control-ubuntu-questing-aarch64.tar.xz",
                "bfc0738df2829da1ed436be1500997bdb37c662c576f5d3ae872dc59894851c2",
            ),
            "canary-debian-trixie.yml": (
                "liskov-runtime-image-debian-trixie-aarch64.tar.xz",
                "a8832aff7799c715448fd6c03971ec9e32affcbc81f4116a1010273853dc8bb6",
            ),
        }
        for workflow_name, (archive, digest) in expected.items():
            workflow = (
                REPOSITORY_ROOT / ".github" / "workflows" / workflow_name
            ).read_text(encoding="utf-8")
            self.assertIn(f"/v0.1.0-rc.5/{archive}", workflow)
            self.assertIn(f"expected-sha256: {digest}", workflow)


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
