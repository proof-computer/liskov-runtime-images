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
from unittest.mock import patch

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

    def test_spdx_declares_fsl_for_the_shim_and_noassertion_for_os_packages(
        self,
    ) -> None:
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
        self.assertEqual(document["dataLicense"], "CC0-1.0")
        by_id = {package["SPDXID"]: package for package in document["packages"]}
        shim = by_id["SPDXRef-Package-Liskov-Getifaddrs-Override"]
        self.assertEqual(shim["licenseConcluded"], "FSL-1.1-Apache-2.0")
        self.assertEqual(shim["licenseDeclared"], "FSL-1.1-Apache-2.0")
        rootfs = by_id["SPDXRef-Package-Rootfs"]
        self.assertEqual(rootfs["licenseConcluded"], "NOASSERTION")
        self.assertEqual(rootfs["licenseDeclared"], "NOASSERTION")
        distro = [
            package
            for package in document["packages"]
            if str(package["SPDXID"]).startswith("SPDXRef-Package-Distro-")
        ]
        self.assertGreater(len(distro), 0)
        for package in distro:
            self.assertEqual(package["licenseConcluded"], "NOASSERTION")
            self.assertEqual(package["licenseDeclared"], "NOASSERTION")

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


class AptSnapshotPocketTests(unittest.TestCase):
    _gpg_dir: Path
    _gpg_env: dict[str, str]
    _keyring: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._gpg_dir = Path(tempfile.mkdtemp(prefix="liskov-pocket-gpg-"))
        os.chmod(cls._gpg_dir, 0o700)
        cls._gpg_env = {**os.environ, "GNUPGHOME": str(cls._gpg_dir)}
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-gen-key",
                "Liskov Pocket Test <pocket@example.invalid>",
                "ed25519",
                "sign",
                "never",
            ],
            check=True,
            env=cls._gpg_env,
            capture_output=True,
        )
        cls._keyring = cls._gpg_dir / "archive.gpg"
        subprocess.run(
            ["gpg", "--batch", "--export", "--output", str(cls._keyring)],
            check=True,
            env=cls._gpg_env,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls._gpg_dir)

    def _sign(self, body: str) -> bytes:
        completed = subprocess.run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--clearsign",
            ],
            input=body.encode(),
            check=True,
            env=self._gpg_env,
            capture_output=True,
        )
        return completed.stdout

    def _ar(self, members: list[tuple[str, bytes]]) -> bytes:
        parts = [b"!<arch>\n"]
        for name, payload in members:
            header = (
                f"{name}/".ljust(16).encode("ascii")
                + b"0           "
                + b"0     "
                + b"0     "
                + b"100644  "
                + str(len(payload)).encode("ascii").rjust(10)
                + b"`\n"
            )
            self.assertEqual(len(header), 60)
            parts.append(header)
            parts.append(payload)
            if len(payload) % 2:
                parts.append(b"\n")
        return b"".join(parts)

    def _deb(self, member: str, payload: bytes, compression: str = "gz") -> bytes:
        buffer = io.BytesIO()
        mode = "w:gz" if compression == "gz" else "w"
        with tarfile.open(fileobj=buffer, mode=mode) as archive:
            info = tarfile.TarInfo(name=f"./{member}")
            info.size = len(payload)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
        data_name = "data.tar.gz" if compression == "gz" else "data.tar"
        return self._ar(
            [
                ("debian-binary", b"2.0\n"),
                (data_name, buffer.getvalue()),
            ]
        )

    def _release(self, suite: str, index: bytes, *, codename: str = "resolute") -> str:
        digest = build_image.sha256_bytes(index)
        return (
            "Origin: Ubuntu\n"
            f"Suite: {suite}\n"
            f"Codename: {codename}\n"
            "Components: main\n"
            "Architectures: arm64\n"
            "SHA256:\n"
            f" {digest} {len(index)} main/binary-arm64/Packages.xz\n"
        )

    def _pocket(self, suite: str, timestamp: str = "20261001T000000Z") -> dict[str, str]:
        archive = f"https://snapshot.ubuntu.com/ubuntu/{timestamp}/"
        return {
            "suite": suite,
            "archiveUrl": archive,
            "inReleaseUrl": f"{archive}dists/{suite}/InRelease",
            "inReleaseSha256": "0" * 64,
        }

    def _image(self, pockets: list[dict[str, object]]) -> dict[str, object]:
        return {
            "distribution": "ubuntu",
            "suite": "resolute",
            "architecture": "arm64",
            "components": ["main"],
            "variant": "apt",
            "include": ["ca-certificates", "curl"],
            "builder": {
                "repository": "library/debian",
                "imageDigest": "sha256:" + ("ab" * 32),
                "mmdebstrapPackage": "mmdebstrap",
                "mmdebstrapVersion": "1.5.7-1+deb13u1",
            },
            "foreignKeyring": {
                "packageUrl": (
                    "https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/"
                    "pool/main/u/ubuntu-keyring/ubuntu-keyring_2023.11.28.1build1_all.deb"
                ),
                "packageSha256": "0" * 64,
                "member": "usr/share/keyrings/ubuntu-archive-keyring.gpg",
                "sha256": "0" * 64,
            },
            "pockets": pockets,
            "fixups": {},
            "removals": [],
        }

    def _fixture(self) -> tuple[dict[str, object], dict[str, bytes]]:
        suites = ("resolute", "resolute-updates", "resolute-security")
        indexes = {
            suite: f"Package: {suite}\n".encode() for suite in suites
        }
        pockets = []
        files: dict[str, bytes] = {}
        for suite in suites:
            pocket = self._pocket(suite)
            signed = self._sign(self._release(suite, indexes[suite]))
            pocket["inReleaseSha256"] = build_image.sha256_bytes(signed)
            pockets.append(pocket)
            files[pocket["inReleaseUrl"]] = signed
            index_name = "main/binary-arm64/Packages.xz"
            files[f"{pocket['archiveUrl']}dists/{suite}/{index_name}"] = indexes[suite]
        keyring = self._keyring.read_bytes()
        deb = self._deb("usr/share/keyrings/ubuntu-archive-keyring.gpg", keyring)
        image = self._image(pockets)
        foreign = image["foreignKeyring"]
        self.assertIsInstance(foreign, dict)
        foreign["packageSha256"] = build_image.sha256_bytes(deb)
        foreign["sha256"] = build_image.sha256_bytes(keyring)
        files[str(foreign["packageUrl"])] = deb
        return image, files

    def _fetch(self, files: dict[str, bytes]):
        def fetch(url, destination, expected, label, headers=None):
            payload = files.get(url)
            if payload is None:
                raise build_image.urllib.error.URLError(url)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            build_image.verify_digest(destination, expected, label)
            return destination

        return fetch

    def test_debian_lock_stays_one_pocket(self) -> None:
        lock = json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes())
        image = lock["images"]["debian-trixie-snapshot"]
        self.assertNotIn("pockets", image)
        self.assertNotIn("foreignKeyring", image)
        self.assertIsNone(build_image.parse_apt_pockets(image))
        self.assertEqual(
            image["snapshot"]["inReleaseSha256"],
            "0584fba32e13e0ab8285fb16c27adea1ec03a73669c18702821094fd6ca86675",
        )

    def test_refuses_empty_duplicate_mixed_timestamp_and_mismatched_distribution(self) -> None:
        empty = self._image([])
        with self.assertRaisesRegex(build_image.BuildError, "non-empty"):
            build_image.parse_apt_pockets(empty)

        duplicate = self._image([self._pocket("resolute"), self._pocket("resolute")])
        with self.assertRaisesRegex(build_image.BuildError, "duplicate"):
            build_image.parse_apt_pockets(duplicate)

        mixed = self._image(
            [
                self._pocket("resolute"),
                self._pocket("resolute-updates", "20261002T000000Z"),
                self._pocket("resolute-security"),
            ]
        )
        with self.assertRaisesRegex(build_image.BuildError, "mixed-timestamp"):
            build_image.parse_apt_pockets(mixed)

        foreign = self._image(
            [
                self._pocket("resolute"),
                self._pocket("resolute-updates"),
                {
                    "suite": "resolute-security",
                    "archiveUrl": "https://snapshot.debian.org/archive/debian/20261001T000000Z/",
                    "inReleaseUrl": (
                        "https://snapshot.debian.org/archive/debian/20261001T000000Z/"
                        "dists/resolute-security/InRelease"
                    ),
                    "inReleaseSha256": "ab" * 32,
                },
            ]
        )
        with self.assertRaisesRegex(build_image.BuildError, "mismatched-distribution"):
            build_image.parse_apt_pockets(foreign)

        wrong_suite = self._image(
            [
                {
                    **self._pocket("resolute"),
                    "inReleaseUrl": (
                        "https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/dists/noble/InRelease"
                    ),
                }
            ]
        )
        with self.assertRaisesRegex(build_image.BuildError, "mismatched-distribution"):
            build_image.parse_apt_pockets(wrong_suite)

    def test_three_pocket_fixture_verifies_metadata_before_bootstrap(self) -> None:
        image, files = self._fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root_dir = Path(temporary)
            work = root_dir / "work"
            work.mkdir()
            cache = root_dir / "cache"
            cache.mkdir()
            empty = root_dir / "empty.tar"
            with tarfile.open(empty, "w"):
                pass
            lines_path = root_dir / "lines"
            flag_path = root_dir / "flag"
            keyring_flag = root_dir / "keyring-package"
            key_url = root_dir / "key-url"
            runner = root_dir / "runner.sh"
            runner.write_text(
                "#!/bin/sh\n"
                f"printf '%s' \"$LISKOV_APT_POCKET_LINES\" > {lines_path}\n"
                f"printf '%s' \"$LISKOV_APT_POCKETS\" > {flag_path}\n"
                f"printf '%s' \"$LISKOV_APT_FOREIGN_KEYRING_URL\" > {key_url}\n"
                "if [ -n \"${LISKOV_APT_KEYRING_PACKAGE:-}\" ]; then\n"
                f"  printf '%s' \"$LISKOV_APT_KEYRING_PACKAGE\" > {keyring_flag}\n"
                "fi\n"
                f"cat {empty}\n",
                encoding="utf-8",
            )
            runner.chmod(0o755)
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                patch.dict(os.environ, {"LISKOV_APT_SNAPSHOT_RUNNER": str(runner)}),
            ):
                materials, _omitted, recipe = build_image.materialize_apt_snapshot(
                    image, work / "rootfs", cache, work
                )
            pocket_lines = lines_path.read_text(encoding="utf-8")
            self.assertEqual(flag_path.read_text(encoding="utf-8"), "1")
            self.assertFalse(keyring_flag.exists())
            self.assertTrue(key_url.read_text(encoding="utf-8").startswith("https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/"))
        self.assertEqual(
            [line.split("|", 1)[0] for line in pocket_lines.splitlines() if line],
            ["resolute", "resolute-updates", "resolute-security"],
        )
        self.assertEqual(
            [pocket["suite"] for pocket in recipe["pockets"]],
            ["resolute", "resolute-updates", "resolute-security"],
        )
        self.assertTrue(
            all(
                pocket["archiveUrl"] == "https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/"
                for pocket in recipe["pockets"]
            )
        )
        roles = [item["role"] for item in materials]
        self.assertEqual(roles.count("archive-snapshot-release"), 3)
        self.assertEqual(roles.count("archive-package-index"), 3)
        self.assertEqual(roles.count("archive-keyring"), 1)
        self.assertIn("archive-keyring-package", roles)
        self.assertNotIn("pockets", json.loads((REPOSITORY_ROOT / "sources.lock.json").read_bytes()))

    def test_bad_digest_bad_signature_and_missing_index_fail_closed(self) -> None:
        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        first = pockets[0]
        self.assertIsInstance(first, dict)
        first["inReleaseSha256"] = "ab" * 32
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "SHA-256 mismatch"),
            ):
                build_image.stage_multi_pocket_inputs(image, cache)

        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        signed = bytearray(files[pockets[0]["inReleaseUrl"]])
        signed[signed.index(b"Origin")] = ord("X")
        files[pockets[0]["inReleaseUrl"]] = bytes(signed)
        pockets[0]["inReleaseSha256"] = build_image.sha256_bytes(bytes(signed))
        with tempfile.TemporaryDirectory() as runner_dir:
            marker = Path(runner_dir) / "ran"
            runner = Path(runner_dir) / "runner.sh"
            runner.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
            runner.chmod(0o755)
            with tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                with (
                    patch.object(build_image, "download", side_effect=self._fetch(files)),
                    patch.dict(os.environ, {"LISKOV_APT_SNAPSHOT_RUNNER": str(runner)}),
                    self.assertRaisesRegex(build_image.BuildError, "signature verification failed"),
                ):
                    build_image.materialize_apt_snapshot(
                        image, work / "rootfs", work / "cache", work
                    )
            self.assertFalse(marker.exists())

        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        index = files[
            pockets[0]["archiveUrl"] + "dists/resolute/main/binary-arm64/Packages.xz"
        ]
        body = self._release("resolute", index).replace(
            " main/binary-arm64/Packages.xz\n",
            " main/binary-amd64/Packages.xz\n",
        )
        signed = self._sign(body)
        files[pockets[0]["inReleaseUrl"]] = signed
        pockets[0]["inReleaseSha256"] = build_image.sha256_bytes(signed)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "missing main/binary-arm64/Packages.xz"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

    def test_rejects_index_mismatch_missing_pocket_and_bad_keyring(self) -> None:
        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        index_url = pockets[0]["archiveUrl"] + "dists/resolute/main/binary-arm64/Packages.xz"
        index = files[index_url]
        body = self._release("resolute", index).replace(
            f" {len(index)} ",
            f" {len(index) + 1} ",
            1,
        )
        signed = self._sign(body)
        files[pockets[0]["inReleaseUrl"]] = signed
        pockets[0]["inReleaseSha256"] = build_image.sha256_bytes(signed)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "size mismatch"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        digest = build_image.sha256_bytes(b"other-index")
        body = (
            "Origin: Ubuntu\n"
            "Suite: resolute\n"
            "Codename: resolute\n"
            "Components: main\n"
            "Architectures: arm64\n"
            "SHA256:\n"
            f" {digest} 1 main/binary-arm64/Packages.xz\n"
        )
        signed = self._sign(body)
        files[pockets[0]["inReleaseUrl"]] = signed
        pockets[0]["inReleaseSha256"] = build_image.sha256_bytes(signed)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "SHA-256 mismatch"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        del files[pockets[2]["inReleaseUrl"]]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "missing apt-snapshot pocket"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

        image, files = self._fixture()
        foreign = image["foreignKeyring"]
        self.assertIsInstance(foreign, dict)
        foreign["packageSha256"] = "cd" * 32
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "SHA-256 mismatch"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

        image, files = self._fixture()
        foreign = image["foreignKeyring"]
        self.assertIsInstance(foreign, dict)
        foreign["sha256"] = "ef" * 32
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "foreign archive keyring SHA-256 mismatch"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

        image, _files = self._fixture()
        foreign = image["foreignKeyring"]
        self.assertIsInstance(foreign, dict)
        foreign["packageUrl"] = "https://example.invalid/ubuntu-keyring.deb"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(build_image.BuildError, "not under the pocket snapshot"):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

    def test_rejects_signed_suite_and_codename_mismatch(self) -> None:
        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        index_url = pockets[1]["archiveUrl"] + "dists/resolute-updates/main/binary-arm64/Packages.xz"
        body = self._release("noble", files[index_url])
        signed = self._sign(body)
        files[pockets[1]["inReleaseUrl"]] = signed
        pockets[1]["inReleaseSha256"] = build_image.sha256_bytes(signed)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "mismatched-distribution"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

        image, files = self._fixture()
        pockets = image["pockets"]
        self.assertIsInstance(pockets, list)
        index_url = pockets[2]["archiveUrl"] + "dists/resolute-security/main/binary-arm64/Packages.xz"
        body = self._release("resolute-security", files[index_url], codename="noble")
        signed = self._sign(body)
        files[pockets[2]["inReleaseUrl"]] = signed
        pockets[2]["inReleaseSha256"] = build_image.sha256_bytes(signed)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(build_image, "download", side_effect=self._fetch(files)),
                self.assertRaisesRegex(build_image.BuildError, "mismatched-distribution"),
            ):
                build_image.stage_multi_pocket_inputs(image, Path(temporary))

    def test_extracts_zstd_keyring_member(self) -> None:
        payload = b"zstd-keyring"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = root / "usr/share/keyrings/ubuntu-archive-keyring.gpg"
            member.parent.mkdir(parents=True)
            member.write_bytes(payload)
            archive = root / "data.tar.zst"
            subprocess.run(
                [
                    "tar",
                    "--zstd",
                    "-cf",
                    str(archive),
                    "-C",
                    str(root),
                    "usr/share/keyrings/ubuntu-archive-keyring.gpg",
                ],
                check=True,
            )
            deb = root / "keyring.deb"
            deb.write_bytes(
                self._ar(
                    [
                        ("debian-binary", b"2.0\n"),
                        ("data.tar.zst", archive.read_bytes()),
                    ]
                )
            )
            destination = root / "out.gpg"
            build_image.extract_deb_data_file(
                deb,
                "usr/share/keyrings/ubuntu-archive-keyring.gpg",
                destination,
            )
            self.assertEqual(destination.read_bytes(), payload)

    def test_runner_keeps_the_debian_path_and_pins_the_foreign_keyring(self) -> None:
        script = (REPOSITORY_ROOT / "scripts/mmdebstrap-docker.sh").read_text(encoding="utf-8")
        multi, one_pocket = script.split(
            "for variable in \\\n  LISKOV_APT_BUILDER_IMAGE \\\n",
            1,
        )
        self.assertNotIn("LISKOV_APT_KEYRING_PACKAGE", multi)
        self.assertIn(
            '"${LISKOV_APT_MMDEBSTRAP_PACKAGE}=${LISKOV_APT_MMDEBSTRAP_VERSION}" \\\n'
            "        curl \\\n"
            "        ca-certificates >&2",
            multi,
        )
        self.assertLess(multi.index("curl -fLSs"), multi.index("sha256sum -c"))
        self.assertLess(multi.index("sha256sum -c"), multi.index("exec mmdebstrap"))
        self.assertIn("signed-by=${signed_by}", multi)
        self.assertIn(
            '"${LISKOV_APT_KEYRING_PACKAGE}=${LISKOV_APT_KEYRING_VERSION}"',
            one_pocket,
        )
        self.assertIn('"${LISKOV_APT_SUITE}" - "${LISKOV_APT_ARCHIVE_URL}"', one_pocket)
        self.assertIn("--components=", one_pocket)


if __name__ == "__main__":
    unittest.main()
