from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
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
TRUST_SPEC = importlib.util.spec_from_file_location(
    "snapshot_tls_trust", REPOSITORY_ROOT / "scripts/snapshot-tls-trust.py"
)
assert TRUST_SPEC is not None and TRUST_SPEC.loader is not None
snapshot_tls_trust = importlib.util.module_from_spec(TRUST_SPEC)
TRUST_SPEC.loader.exec_module(snapshot_tls_trust)


class SourceLockTests(unittest.TestCase):
    def test_lock_is_digest_pinned_and_has_two_distinct_roles(self) -> None:
        lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
        self.assertEqual(lock["schemaVersion"], 1)
        self.assertEqual(
            set(lock["images"]),
            {"v4-control", "debian-trixie", "debian-trixie-snapshot"},
        )
        snapshot = lock["images"]["debian-trixie-snapshot"]
        self.assertEqual(snapshot["kind"], "apt-snapshot")
        self.assertEqual(snapshot["supportStatus"], "release-candidate")
        self.assertRegex(snapshot["snapshot"]["inReleaseSha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(snapshot["keyring"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            snapshot["snapshot"]["archiveUrl"],
            r"^http://snapshot\.debian\.org/archive/debian/[0-9]{8}T[0-9]{6}Z/$",
        )
        build_image.digest_hex(snapshot["builder"]["imageDigest"], "builder.imageDigest")
        self.assertEqual(
            set(snapshot["fixups"]), {"etc/hostname", "etc/hosts", "etc/resolv.conf"}
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

    def test_rc13_ab_canaries_are_bounded_logged_and_pinned_to_released_archives(self) -> None:
        cases = {
            "liskov-runtime-images-rc13-snapshot-canary": (
                "canary-debian-trixie-snapshot-rc13.yml",
                "liskov-runtime-image-debian-trixie-snapshot-aarch64.tar.xz",
                "ac69afc5c8737db519f653f42f150c814cfd2c1173bececa15fc32258a8e2f9a",
            ),
            "liskov-runtime-images-rc13-snapshot-canary-b": (
                "canary-debian-trixie-snapshot-rc13-b.yml",
                "liskov-runtime-image-debian-trixie-snapshot-aarch64.tar.xz",
                "ac69afc5c8737db519f653f42f150c814cfd2c1173bececa15fc32258a8e2f9a",
            ),
            "liskov-runtime-images-rc13-oci-canary": (
                "canary-debian-trixie-oci-rc13.yml",
                "liskov-runtime-image-debian-trixie-aarch64.tar.xz",
                "0639e88db6b46cef6091acafe35dfb1b59c5e354463d969f1a9509451d118377",
            ),
        }
        placements = []
        for name, (workflow_name, archive, digest) in cases.items():
            manifest = self.load_manifest(f"{name}.policy.json")
            self.assertEqual(manifest["applicationId"], name)
            self.assertEqual(manifest["runtime"]["maxGenerations"], 1)
            self.assertEqual(manifest["runtime"]["resources"]["networkRequestQuota"], 0)
            self.assertNotIn("liskov-runtime-contact", manifest["runtime"]["command"])
            self.assertEqual(
                manifest["deployment"]["lifecycle"]["recovery"]["launch"]["maxRetries"], 0
            )
            self.assertEqual(
                manifest["deployment"]["spend"],
                {
                    "maxRewardPlanckPerJob": "40000000000",
                    "maxNativeFeePlanckPerJob": "10000000000",
                },
            )
            self.assertTrue(manifest["observability"]["logs"]["enabled"])
            builder = manifest["release"]["builder"]
            self.assertEqual(builder["allowedRefs"], ["refs/heads/main"])
            self.assertEqual(builder["manifestPath"], f".liskov/{name}.policy.json")
            self.assertTrue(builder["workflowRef"].endswith(f"/{workflow_name}@refs/heads/main"))
            placements.append(manifest["deployment"]["placement"])
            workflow = (
                REPOSITORY_ROOT / ".github" / "workflows" / workflow_name
            ).read_text(encoding="utf-8")
            self.assertIn(f"/v0.1.0-rc.13/{archive}", workflow)
            self.assertIn(f"expected-sha256: {digest}", workflow)
            self.assertIn(
                "attestation-source-digest: e061dfd03f430657b4e937d91cfc804b1670d0f5",
                workflow,
            )
            self.assertIn(f"application-id: {name}", workflow)
        # The A/B pair must compete for the same processors (ADR-0041).
        for placement in placements[1:]:
            self.assertEqual(placement, placements[0])

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


class SnapshotTlsTrustTests(unittest.TestCase):
    def snapshot_image(self) -> dict[str, object]:
        lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
        image = lock["images"]["debian-trixie-snapshot"]
        self.assertIsInstance(image, dict)
        return image

    def test_snapshot_include_lists_ca_certificates_without_an_overlay(self) -> None:
        image = self.snapshot_image()
        include = image["include"]
        self.assertIsInstance(include, list)
        self.assertIn("ca-certificates", include)
        for path in build_image.OVERLAY_PATHS:
            self.assertNotIn("ssl/certs", path)
            self.assertNotIn("ca-certificates", path)
        url = snapshot_tls_trust.debian_archive_url(
            {"images": {"debian-trixie-snapshot": image}}
        )
        self.assertEqual(url, image["snapshot"]["inReleaseUrl"])
        self.assertTrue(url.startswith("https://"))

    def test_spdx_records_installed_ca_certificates(self) -> None:
        image = self.snapshot_image()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status = root / "var/lib/dpkg/status"
            status.parent.mkdir(parents=True)
            status.write_text(
                "Package: ca-certificates\n"
                "Status: install ok installed\n"
                "Version: 20250419\n"
                "Architecture: all\n",
                encoding="utf-8",
            )
            document = build_image.spdx_document(
                "debian-trixie-snapshot",
                image,
                "a" * 64,
                root,
                "b" * 64,
                "2026-09-20T00:00:00Z",
            )
            packages = [
                package
                for package in document["packages"]
                if package["name"] == "ca-certificates"
            ]
            self.assertEqual(len(packages), 1)
            self.assertEqual(
                packages[0]["externalRefs"][0]["referenceLocator"],
                "pkg:deb/debian/ca-certificates@20250419?arch=all",
            )

    def trusted_root(self, root: Path) -> dict[str, object]:
        status = root / "var/lib/dpkg/status"
        status.parent.mkdir(parents=True)
        status.write_text(
            "Package: ca-certificates\n"
            "Status: install ok installed\n"
            "Version: 20250419\n"
            "Architecture: all\n",
            encoding="utf-8",
        )
        certs = root / "etc/ssl/certs"
        certs.mkdir(parents=True)
        (certs / "ca-certificates.crt").write_text("bundle\n", encoding="utf-8")
        os.symlink(
            "/usr/share/ca-certificates/example.crt",
            certs / "3513523f.0",
        )
        return {
            "packages": [
                {
                    "name": "ca-certificates",
                    "externalRefs": [
                        {
                            "referenceLocator": (
                                "pkg:deb/debian/ca-certificates@20250419?arch=all"
                            )
                        }
                    ],
                }
            ]
        }

    def test_support_check_is_snapshot_only(self) -> None:
        lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sbom = self.trusted_root(root)
            snapshot_tls_trust.assert_snapshot_support(
                lock, sbom, root, "debian-trixie-snapshot"
            )
            for target in ("debian-trixie", "v4-control"):
                with self.subTest(target=target):
                    with self.assertRaises(snapshot_tls_trust.TrustProofError):
                        snapshot_tls_trust.assert_snapshot_support(
                            lock, sbom, root, target
                        )
            (root / "etc/ssl/certs/ca-certificates.crt").unlink()
            with self.assertRaises(snapshot_tls_trust.TrustProofError):
                snapshot_tls_trust.assert_snapshot_support(
                    lock, sbom, root, "debian-trixie-snapshot"
                )

    def test_curl_status_distinguishes_network_from_missing_trust(self) -> None:
        self.assertEqual(snapshot_tls_trust.classify_curl_status(0), "ok")
        self.assertEqual(snapshot_tls_trust.classify_curl_status(60), "trust")
        for status in (6, 7, 28):
            with self.subTest(status=status):
                self.assertEqual(
                    snapshot_tls_trust.classify_curl_status(status),
                    "network",
                )
        self.assertEqual(snapshot_tls_trust.classify_curl_status(35), "unexpected")

    def test_fixtures_pin_a_public_chain_and_a_disposable_ca(self) -> None:
        fixture_dir = REPOSITORY_ROOT / "tests/fixtures/tls"
        manifest = json.loads((fixture_dir / "manifest.json").read_bytes())
        self.assertEqual(
            manifest["schema"],
            "proof.liskov.runtime-image.tls-trust-fixtures",
        )
        self.assertEqual(manifest["capturedFrom"], "snapshot.debian.org:443")
        self.assertIsInstance(manifest["verificationTime"], int)
        self.assertFalse((fixture_dir / "leaf.key").exists())
        self.assertFalse((fixture_dir / "ca.key").exists())
        leaf = fixture_dir / manifest["leaf"]
        dates = subprocess.check_output(
            ["openssl", "x509", "-in", leaf, "-noout", "-dates"],
            text=True,
        )
        parsed: dict[str, int] = {}
        for line in dates.splitlines():
            name, value = line.split("=", 1)
            moment = dt.datetime.strptime(
                value, "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=dt.timezone.utc)
            parsed[name] = int(moment.timestamp())
        self.assertLess(parsed["notBefore"], manifest["verificationTime"])
        self.assertLess(manifest["verificationTime"], parsed["notAfter"])
        leaf_issuer = subprocess.check_output(
            ["openssl", "x509", "-in", leaf, "-noout", "-issuer"],
            text=True,
        )
        self.assertNotIn("fixture", leaf_issuer)
        server = fixture_dir / manifest["untrustedCertificate"]
        issuer = subprocess.check_output(
            ["openssl", "x509", "-in", server, "-noout", "-issuer"],
            text=True,
        )
        self.assertIn("Liskov runtime-images fixture CA", issuer)
        verified = subprocess.run(
            [
                "openssl",
                "verify",
                "-CAfile",
                str(fixture_dir / manifest["fixtureCa"]),
                str(server),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(
            snapshot_tls_trust.verification_time(fixture_dir),
            manifest["verificationTime"],
        )

    def test_fixture_server_is_rejected_by_the_default_trust_store(self) -> None:
        fixture_dir = REPOSITORY_ROOT / "tests/fixtures/tls"
        manifest = json.loads((fixture_dir / "manifest.json").read_bytes())
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "port"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts/untrusted-tls-server.py"),
                    "--cert",
                    str(fixture_dir / manifest["untrustedCertificate"]),
                    "--key",
                    str(fixture_dir / manifest["untrustedKey"]),
                    "--ready-file",
                    str(ready),
                ]
            )
            try:
                for _ in range(100):
                    if ready.is_file() or process.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue(ready.is_file(), "untrusted TLS server did not start")
                result = subprocess.run(
                    [
                        "curl",
                        "--http1.1",
                        "--fail",
                        "--silent",
                        "--show-error",
                        "--max-time",
                        "10",
                        "--output",
                        "/dev/null",
                        "--proto",
                        "=https",
                        f"https://127.0.0.1:{ready.read_text().strip()}/",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 60, result.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_smoke_limits_the_proof_to_the_snapshot_target(self) -> None:
        smoke = (REPOSITORY_ROOT / "scripts/smoke-rootfs.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'if [[ "${target}" == "debian-trixie-snapshot" ]]; then\n'
            "  prove_snapshot_tls_trust\n"
            "fi",
            smoke,
        )
        self.assertNotIn("--insecure", smoke)
        self.assertNotIn("curl -k", smoke)
        self.assertNotIn("-b /etc/ssl", smoke)
        self.assertIn("-attime", smoke)
        self.assertIn("this is not a missing-trust failure", smoke)


if __name__ == "__main__":
    unittest.main()
