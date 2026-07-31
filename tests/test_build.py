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
INSPECT_SPEC = importlib.util.spec_from_file_location(
    "inspect_artifact", REPOSITORY_ROOT / "scripts/inspect-artifact.py"
)
assert INSPECT_SPEC is not None and INSPECT_SPEC.loader is not None
inspect_artifact = importlib.util.module_from_spec(INSPECT_SPEC)
INSPECT_SPEC.loader.exec_module(inspect_artifact)


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
        self.assertNotIn("helper", lock)
        self.assertRegex(
            lock["images"]["v4-control"]["sourceSha256"],
            r"^[0-9a-f]{64}$",
        )
        build_image.digest_hex(
            lock["images"]["debian-trixie"]["manifestDigest"],
            "manifestDigest",
        )

    def test_overlay_includes_owned_getifaddrs_source_and_library(self) -> None:
        self.assertEqual(
            build_image.OVERLAY_PATHS,
            (
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
        builder = (
            REPOSITORY_ROOT / "scripts/build-image.py"
        ).read_text(encoding="utf-8")
        for removed in (
            "SPDXRef-Package-Liskov-Runtime-Contact",
            "helperVersion",
            "helperReleaseCommit",
            "helperArchiveSha256",
            "helperBinarySha256",
        ):
            self.assertNotIn(removed, builder)

    def test_runtime_helper_is_test_only_and_pinned_to_an_exact_release(self) -> None:
        contract = json.loads(
            (REPOSITORY_ROOT / "tests/runtime-contact-release.json").read_bytes()
        )
        self.assertEqual(
            contract["schema"],
            "proof.liskov.runtime-image.test-helper-release",
        )
        self.assertEqual(contract["schemaVersion"], 1)
        self.assertEqual(contract["tag"], "v0.2.10")
        self.assertEqual(contract["version"], "0.2.10")
        self.assertRegex(contract["sourceCommit"], r"^[0-9a-f]{40}$")
        self.assertEqual(contract["target"], "aarch64-unknown-linux-musl")
        self.assertRegex(contract["binary"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(contract["binary"]["byteSize"], 0)
        self.assertIn("/releases/download/v0.2.10/", contract["binary"]["url"])

    def test_release_notes_describe_helperless_images(self) -> None:
        release_workflow = (
            REPOSITORY_ROOT / ".github/workflows/release.yml"
        ).read_text()
        self.assertNotIn("helper_version=", release_workflow)
        self.assertIn("do not embed the runtime-contact helper", release_workflow)

    def test_native_verifier_checks_only_the_owned_shim(self) -> None:
        verifier = (
            REPOSITORY_ROOT / "scripts/verify-native-aarch64.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn("liskov-runtime-contact", verifier)
        self.assertIn("libgetifaddrs_override.so", verifier)

    def test_inspector_rejects_removed_helper_paths_and_link_aliases(self) -> None:
        root = "rootfs"
        cases: list[tarfile.TarInfo] = []

        helper = tarfile.TarInfo(f"{root}/usr/local/bin/liskov-runtime-contact")
        helper.type = tarfile.REGTYPE
        cases.append(helper)

        symlink = tarfile.TarInfo(f"{root}/usr/local/bin/helper-alias")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "liskov-runtime-contact"
        cases.append(symlink)

        hardlink = tarfile.TarInfo(f"{root}/license-alias")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = (
            f"{root}/usr/share/doc/liskov-runtime-contact/LICENSE"
        )
        cases.append(hardlink)

        for member in cases:
            with self.subTest(name=member.name, linkname=member.linkname):
                with self.assertRaises(SystemExit):
                    inspect_artifact.assert_no_removed_helper_aliases(
                        [member], [member.name], root
                    )

        safe = tarfile.TarInfo(f"{root}/usr/local/bin/safe-link")
        safe.type = tarfile.SYMTYPE
        safe.linkname = "../../bin/sh"
        inspect_artifact.assert_no_removed_helper_aliases(
            [safe], [safe.name], root
        )

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

    def test_size_verifier_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact"
            path.write_bytes(b"fixed-size")
            build_image.verify_size(path, 10, "test artifact")
            with self.assertRaises(build_image.BuildError):
                build_image.verify_size(path, 9, "test artifact")

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
        debian = self.load_manifest("liskov-runtime-images-v5-canary.policy.json")

        self.assertEqual(
            probe["runtime"]["command"],
            "/bin/true",
        )
        self.assertEqual(
            probe["deployment"]["placement"]["processorSelection"],
            {
                "mode": "manager",
                "managerId": "9470",
                "requireScheduleClear": True,
                "requireConsumerAccess": True,
                "candidateLimit": 16,
            },
        )
        for manifest in (probe, v4, debian):
            self.assertEqual(manifest["runtime"]["maxGenerations"], 1)
            self.assertEqual(
                manifest["runtime"]["resources"]["networkRequestQuota"],
                0,
            )
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
        probe_workflow = (
            REPOSITORY_ROOT
            / ".github/workflows/canary-v4-bridge-probe.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("bootstrap-mode: bridge-probe", probe_workflow)
        for manifest in (v4, debian):
            self.assertFalse(manifest["observability"]["logs"]["enabled"])

    def test_canary_workflows_pin_the_verified_rc12_archives(self) -> None:
        expected = {
            "canary-v4-bridge-probe.yml": (
                "liskov-runtime-image-v4-control-ubuntu-questing-aarch64.tar.xz",
                "ae230e74fb871e33f9eabc19eee9370bb2b36fc3622cc97bf08ed1c5e87e59ca",
            ),
            "canary-v4-control.yml": (
                "liskov-runtime-image-v4-control-ubuntu-questing-aarch64.tar.xz",
                "ae230e74fb871e33f9eabc19eee9370bb2b36fc3622cc97bf08ed1c5e87e59ca",
            ),
            "canary-debian-trixie.yml": (
                "liskov-runtime-image-debian-trixie-aarch64.tar.xz",
                "0639e88db6b46cef6091acafe35dfb1b59c5e354463d969f1a9509451d118377",
            ),
        }
        for workflow_name, (archive, digest) in expected.items():
            workflow = (
                REPOSITORY_ROOT / ".github" / "workflows" / workflow_name
            ).read_text(encoding="utf-8")
            self.assertIn(f"/v0.1.0-rc.12/{archive}", workflow)
            self.assertIn(f"expected-sha256: {digest}", workflow)
            self.assertIn("attestations: read", workflow)
            self.assertIn(
                "attestation-repository: proof-computer/liskov-runtime-images",
                workflow,
            )
            self.assertIn(
                "attestation-source-digest: "
                "d3a7b61e7e2acb2e2f686c6fc1379d2ee74ea582",
                workflow,
            )
            self.assertIn(
                "attestation-signer-workflow: "
                "proof-computer/liskov-runtime-images/.github/workflows/ci.yml",
                workflow,
            )

    def test_debian_workflow_uses_the_canonical_repository_policy_path(self) -> None:
        manifest_name = "liskov-runtime-images-v5-canary.policy.json"
        manifest = self.load_manifest(manifest_name)

        self.assertEqual(manifest["applicationId"], "liskov-runtime-images-v5-canary")
        release = manifest["release"]
        if release["mode"] == "build":
            builder = release["builder"]
            self.assertEqual(
                builder["repository"], "proof-computer/liskov-runtime-images"
            )
            self.assertEqual(builder["allowedRefs"], ["refs/heads/main"])
            self.assertEqual(builder["manifestPath"], f".liskov/{manifest_name}")
        else:
            self.assertEqual(release["mode"], "pinned")
            self.assertEqual(
                release["artifact"]["imageDigest"],
                "sha256:0639e88db6b46cef6091acafe35dfb1b59c5e354463d969f1a9509451d118377",
            )
        workflow = (
            REPOSITORY_ROOT / ".github/workflows/canary-debian-trixie.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(f"manifest-path: .liskov/{manifest_name}", workflow)

    def test_fresh_debian_canary_is_pinned_to_the_exact_rc11_candidate(self) -> None:
        seed = self.load_manifest("liskov-runtime-images-v6-canary.policy.json")

        self.assertEqual(seed["applicationId"], "liskov-runtime-images-v6-canary")
        self.assertEqual(
            seed["release"],
            {
                "mode": "pinned",
                "artifact": {
                    "kind": "runtime_image",
                    "imageDigest": (
                        "sha256:"
                        "97523a10978903fe63bb0df55fc0be411a137a101d2697a3d866c2abb1a0ddf4"
                    ),
                    "bootstrapCid": (
                        "ipfs://Qmet3Lch34ZHrHeRZyKgRv2ghdgsL6f12gvaGowsTDG4We"
                    ),
                    "bootstrapDigest": (
                        "sha256:"
                        "51f888cb07a1ff5e4dc8797bf277ade9c7017ade2b33f12bdb930940fe065fd4"
                    ),
                },
            },
        )
        self.assertEqual(seed["runtime"]["maxGenerations"], 1)
        self.assertEqual(
            seed["deployment"]["lifecycle"]["recovery"]["launch"]["maxRetries"],
            0,
        )
        self.assertEqual(seed["runtime"]["resources"]["networkRequestQuota"], 0)

    def test_targeted_debian_canaries_use_a_schedule_clear_contacted_processor(
        self,
    ) -> None:
        for version in ("v8", "v9"):
            with self.subTest(version=version):
                application_id = f"liskov-runtime-images-{version}-canary"
                seed = self.load_manifest(f"{application_id}.policy.json")

                self.assertEqual(seed["applicationId"], application_id)
                self.assertEqual(
                    seed["release"]["artifact"],
                    {
                        "kind": "runtime_image",
                        "imageDigest": (
                            "sha256:"
                            "97523a10978903fe63bb0df55fc0be411a137a101d2697a3d866c2abb1a0ddf4"
                        ),
                        "bootstrapCid": (
                            "ipfs://Qmet3Lch34ZHrHeRZyKgRv2ghdgsL6f12gvaGowsTDG4We"
                        ),
                        "bootstrapDigest": (
                            "sha256:"
                            "51f888cb07a1ff5e4dc8797bf277ade9c7017ade2b33f12bdb930940fe065fd4"
                        ),
                    },
                )
                self.assertEqual(
                    seed["deployment"]["placement"]["processorSelection"],
                    {
                        "mode": "static",
                        "managerId": "9470",
                        "processorIds": [
                            "5DH3ipjftEhSSihRyXJEndcMtRBmxyVbphdH85rXw8BUJFkv"
                        ],
                        "requireScheduleClear": True,
                        "requireConsumerAccess": True,
                    },
                )
                self.assertEqual(seed["runtime"]["maxGenerations"], 1)
                self.assertEqual(
                    seed["deployment"]["lifecycle"]["recovery"]["launch"][
                        "maxRetries"
                    ],
                    0,
                )
                self.assertEqual(
                    seed["runtime"]["resources"]["networkRequestQuota"],
                    0,
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
