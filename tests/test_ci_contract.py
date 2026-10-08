from __future__ import annotations

import importlib.util
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"),
        REPOSITORY_ROOT / "scripts" / name,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classifier = load_script("change-classifier.py")
build_manifest = load_script("build-manifest.py")


class ChangeClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "assets").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "sources.lock.json").write_text("{}\n", encoding="utf-8")
        (self.root / "scripts" / "build-image.py").write_text(
            "print('builder')\n",
            encoding="utf-8",
        )
        (self.root / "assets" / "overlay.c").write_text(
            "/* overlay */\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "bridge-smoke-server.py").write_text(
            "print('bridge')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_every_governed_path_classifies_conservatively(self) -> None:
        fast_paths = [
            ".gitattributes",
            ".gitignore",
            "AGENTS.md",
            "CHANGELOG.md",
            "LICENSE",
            "Makefile",
            "README.md",
            "docs/proot-fixup-ledger.md",
            "SECURITY.md",
            "THIRD_PARTY_NOTICES.md",
            ".github/workflows/ci.yml",
            ".liskov/canary.json",
            "tests/test_build.py",
        ]
        for path in fast_paths:
            with self.subTest(path=path):
                self.assertEqual(
                    classifier.classify_paths([path], self.root)["mode"],
                    "fast",
                )

        material_paths = [
            "sources.lock.json",
            "assets/getifaddrs_override.c",
            "scripts/build-image.py",
            "scripts/verify-native-aarch64.sh",
            "tests/acurast.sh.template",
            "tests/bridge-smoke-server.py",
            "tests/runtime-contact-release.json",
            "unexpected/new-input.txt",
            "docs/build.py",
            "docs/release-policy.json",
        ]
        for path in material_paths:
            with self.subTest(path=path):
                result = classifier.classify_paths([path], self.root)
                self.assertEqual(result["mode"], "material")
                self.assertEqual(result["targets"], list(classifier.TARGETS))

    def test_mixed_commit_and_unknown_path_fail_safe_to_material(self) -> None:
        result = classifier.classify_paths(
            ["README.md", ".github/workflows/release.yml", "new.recipe"],
            self.root,
        )
        self.assertEqual(result["mode"], "material")

    def test_valid_release_intent_promotes_mixed_material_commit_directly(self) -> None:
        fingerprint = classifier.material_fingerprint(self.root)
        intent = {
            "schemaVersion": 1,
            "version": "v0.1.0-rc.8",
            "materialInputFingerprint": fingerprint,
            "targets": list(classifier.TARGETS),
        }
        (self.root / classifier.RELEASE_INTENT_PATH).write_text(
            json.dumps(intent),
            encoding="utf-8",
        )
        result = classifier.classify_paths(
            ["release-intent.json", "scripts/build-image.py"],
            self.root,
        )
        self.assertEqual(result["mode"], "release")
        self.assertEqual(result["releaseVersion"], "v0.1.0-rc.8")

    def test_release_intent_rejects_stale_fingerprint_and_wrong_tag(self) -> None:
        intent = {
            "schemaVersion": 1,
            "version": "v0.1.0-rc.8",
            "materialInputFingerprint": classifier.material_fingerprint(self.root),
            "targets": list(classifier.TARGETS),
        }
        path = self.root / classifier.RELEASE_INTENT_PATH
        path.write_text(json.dumps(intent), encoding="utf-8")
        classifier.validate_release_intent(
            self.root,
            expected_tag="v0.1.0-rc.8",
        )
        with self.assertRaises(classifier.ClassificationError):
            classifier.validate_release_intent(
                self.root,
                expected_tag="v0.1.0-rc.9",
            )
        (self.root / "scripts" / "build-image.py").write_text(
            "print('changed')\n",
            encoding="utf-8",
        )
        with self.assertRaises(classifier.ClassificationError):
            classifier.validate_release_intent(self.root)

    def test_schema_two_release_intent_validates_explicit_catalogue(self):
        catalogue = [{"target": target, "name": name, "version": "0.1.0",
                      "archiveSha256": "c" * 64, "previousReleases": [], "versionDecision": None}
                     for target, name in build_manifest.MAINTAINED_NAMES.items()]
        intent = {"schemaVersion": 2, "version": "v0.2.0", "catalogue": catalogue,
                  "materialInputFingerprint": classifier.material_fingerprint(self.root),
                  "targets": list(classifier.TARGETS)}
        path = self.root / classifier.RELEASE_INTENT_PATH
        path.write_text(json.dumps(intent), encoding="utf-8")
        self.assertEqual(classifier.classify_paths([classifier.RELEASE_INTENT_PATH], self.root)["mode"], "release")
        intent["catalogue"] = catalogue[:-1]
        path.write_text(json.dumps(intent), encoding="utf-8")
        with self.assertRaises(classifier.ClassificationError):
            classifier.validate_release_intent(self.root)


class BuildManifestTests(unittest.TestCase):
    repository = "proof-computer/liskov-runtime-images"
    source_commit = "a" * 40
    fingerprint = f"sha256:{'b' * 64}"
    version = "v0.1.0-rc.8"
    workflow_path = ".github/workflows/ci.yml"
    workflow_ref = (
        "proof-computer/liskov-runtime-images/"
        ".github/workflows/ci.yml@refs/heads/main"
    )
    run_id = "12345"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repository"
        self.bundle = Path(self.temporary.name) / "bundle"
        self.root.mkdir()
        self.bundle.mkdir()
        lock = {
            "images": {
                "debian-trixie": {"outputStem": "debian"},
                "debian-trixie-snapshot": {"outputStem": "debian-snapshot"},
                "v4-control": {"outputStem": "v4"},
                "ubuntu-resolute-snapshot": {"outputStem": "ubuntu-resolute-snapshot"},
            }
        }
        (self.root / "sources.lock.json").write_text(
            json.dumps(lock),
            encoding="utf-8",
        )
        for names in build_manifest.expected_payloads(self.root).values():
            for name in names:
                (self.bundle / name).write_bytes(f"payload:{name}".encode())
        self.catalogue = [
            {
                "target": target, "name": name, "version": "0.1.0",
                "archiveSha256": build_manifest.sha256_file(self.bundle / build_manifest.expected_payloads(self.root)[target][0]),
                "previousReleases": [], "versionDecision": None,
            }
            for target, name in build_manifest.MAINTAINED_NAMES.items()
        ]
        build_manifest.create_manifest(
            self.bundle,
            repository=self.repository,
            source_commit=self.source_commit,
            material_fingerprint=self.fingerprint,
            version=self.version,
            workflow_path=self.workflow_path,
            workflow_ref=self.workflow_ref,
            run_id=self.run_id,
            run_attempt="2",
            root=self.root,
            catalogue=self.catalogue,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self, bundle: Path | None = None, **overrides):
        arguments = {
            "repository": self.repository,
            "source_commit": self.source_commit,
            "material_fingerprint": self.fingerprint,
            "version": self.version,
            "workflow_path": self.workflow_path,
            "run_id": self.run_id,
            "root": self.root,
            "catalogue": self.catalogue,
        }
        arguments.update(overrides)
        return build_manifest.validate_manifest(bundle or self.bundle, **arguments)

    def clone_bundle(self, name: str) -> Path:
        destination = Path(self.temporary.name) / name
        shutil.copytree(self.bundle, destination)
        return destination

    def test_validates_complete_commit_bound_bundle(self) -> None:
        manifest = self.validate()
        self.assertEqual(manifest["source"]["commit"], self.source_commit)
        self.assertEqual(
            [target["name"] for target in manifest["targets"]],
            list(build_manifest.TARGETS),
        )

    def test_rejects_wrong_commit_run_or_workflow(self) -> None:
        for overrides in (
            {"source_commit": "c" * 40},
            {"run_id": "54321"},
            {"workflow_path": ".github/workflows/other.yml"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(build_manifest.ManifestError):
                    self.validate(**overrides)

    def test_rejects_checksum_mismatch(self) -> None:
        bundle = self.clone_bundle("checksum-mismatch")
        payload = next(
            name
            for name in build_manifest.regular_files(bundle)
            if name.endswith(".tar.xz")
        )
        (bundle / payload).write_bytes(b"tampered")
        with self.assertRaises(build_manifest.ManifestError):
            self.validate(bundle)

    def test_rejects_missing_or_additional_files(self) -> None:
        missing = self.clone_bundle("missing")
        next(path for path in missing.iterdir() if path.name.endswith(".spdx.json")).unlink()
        with self.assertRaises(build_manifest.ManifestError):
            self.validate(missing)

        additional = self.clone_bundle("additional")
        (additional / "unexpected.txt").write_text("extra", encoding="utf-8")
        with self.assertRaises(build_manifest.ManifestError):
            self.validate(additional)

    def test_rejects_incomplete_targets_and_malformed_schema(self) -> None:
        incomplete = self.clone_bundle("incomplete")
        manifest_path = incomplete / build_manifest.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["targets"] = manifest["targets"][:-1]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(build_manifest.ManifestError):
            self.validate(incomplete)

        malformed = self.clone_bundle("malformed")
        manifest_path = malformed / build_manifest.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schemaVersion"] = 99
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(build_manifest.ManifestError):
            self.validate(malformed)

    def rewrite_manifest(self, bundle, manifest):
        (bundle / build_manifest.MANIFEST_NAME).write_bytes(build_manifest.canonical_json(manifest))
        self.refresh_checksums(bundle)

    def refresh_checksums(self, bundle):
        names = sorted(build_manifest.regular_files(bundle) - {build_manifest.CHECKSUMS_NAME})
        (bundle / build_manifest.CHECKSUMS_NAME).write_text(
            "".join(f"{build_manifest.sha256_file(bundle / name)}  {name}\n" for name in names),
            encoding="utf-8",
        )

    def test_schema_two_binds_independent_named_versions_and_controls(self):
        manifest = self.validate()
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual([entry["name"] for entry in manifest["catalogue"]], ["debian-trixie", "ubuntu-resolute"])
        self.assertEqual([entry["version"] for entry in manifest["catalogue"]], ["0.1.0", "0.1.0"])
        for entry in manifest["catalogue"]:
            self.assertEqual(entry["files"], list(build_manifest.expected_payloads(self.root)[entry["target"]]))
        self.assertNotIn("catalogue", manifest["targets"][0])
        self.assertEqual(manifest["workflow"]["ref"], self.workflow_ref)
        self.assertEqual(manifest["workflow"]["runAttempt"], 2)
        self.assertEqual(build_manifest.sha256_file(self.bundle / build_manifest.MANIFEST_NAME), "507e1cab97d65afb705c9dbec008b0f619b7e5cd952051602b4fd41f4a9d2fc5")

    def test_rejects_modified_catalogue_even_with_refreshed_checksums(self):
        for field, value in (("version", "0.1.1"), ("archiveSha256", "0" * 64), ("name", "ubuntu-resolute")):
            with self.subTest(field=field):
                bundle = self.clone_bundle(f"changed-{field}")
                manifest = json.loads((bundle / build_manifest.MANIFEST_NAME).read_bytes())
                manifest["catalogue"][0][field] = value
                self.rewrite_manifest(bundle, manifest)
                with self.assertRaises(build_manifest.ManifestError):
                    self.validate(bundle)
        bundle = self.clone_bundle("changed-byte")
        manifest = json.loads((bundle / build_manifest.MANIFEST_NAME).read_bytes())
        archive = manifest["catalogue"][0]["files"][0]
        (bundle / archive).write_bytes((bundle / archive).read_bytes() + b"!")
        for file in manifest["files"]:
            if file["name"] == archive:
                file["byteSize"] += 1
                file["sha256"] = build_manifest.sha256_file(bundle / archive)
        self.rewrite_manifest(bundle, manifest)
        with self.assertRaisesRegex(build_manifest.ManifestError, "digest mismatch"):
            self.validate(bundle)

    def test_map_rejects_controls_duplicates_missing_and_non_numeric_versions(self):
        cases = [[], self.catalogue[:-1], [self.catalogue[0], self.catalogue[0]]]
        for target in ("v4-control", "debian-trixie", "other", []):
            bad = copy.deepcopy(self.catalogue)
            bad[0]["target"] = target
            cases.append(bad)
        for version in ("v0.1.0", "0.1", "01.1.0", "0.1.0-rc.1", "0.1.0+build", 0, None):
            bad = copy.deepcopy(self.catalogue)
            bad[0]["version"] = version
            cases.append(bad)
        for bad in cases:
            with self.subTest(catalogue=bad):
                with self.assertRaises(build_manifest.ManifestError):
                    build_manifest.validate_catalogue(bad)

    def test_immutable_identity_and_independent_patch_progression(self):
        candidate = copy.deepcopy(self.catalogue)
        old_digest = candidate[0]["archiveSha256"]
        candidate[0]["previousReleases"] = [{"version": "0.1.0", "archiveSha256": old_digest}]
        build_manifest.validate_catalogue(candidate)  # unchanged identity/bytes can be carried forward
        candidate[0]["archiveSha256"] = "c" * 64
        with self.assertRaisesRegex(build_manifest.ManifestError, "immutable"):
            build_manifest.validate_catalogue(candidate)
        candidate[0]["version"] = "0.1.1"
        validated = build_manifest.validate_catalogue(candidate)
        self.assertEqual(validated[1]["version"], "0.1.0")
        candidate[0]["version"] = "0.1.2"
        with self.assertRaisesRegex(build_manifest.ManifestError, "patch by one"):
            build_manifest.validate_catalogue(candidate)
        candidate[0]["version"] = "0.2.0"
        with self.assertRaisesRegex(build_manifest.ManifestError, "explicit version decision"):
            build_manifest.validate_catalogue(candidate)
        candidate[0]["versionDecision"] = "ADR-0200"
        build_manifest.validate_catalogue(candidate)
        candidate[0]["version"] = "0.0.9"
        with self.assertRaisesRegex(build_manifest.ManifestError, "backwards"):
            build_manifest.validate_catalogue(candidate)

    def test_schema_one_historical_bundles_keep_exact_keys_and_target_sets(self):
        for targets in (build_manifest.HISTORICAL_TARGETS, build_manifest.TARGETS):
            with self.subTest(targets=targets):
                bundle = self.clone_bundle(f"historical-{len(targets)}")
                manifest = json.loads((bundle / build_manifest.MANIFEST_NAME).read_bytes())
                manifest["schemaVersion"] = 1
                del manifest["catalogue"]
                manifest["targets"] = [record for record in manifest["targets"] if record["name"] in targets]
                manifest["files"] = [record for record in manifest["files"] if record["target"] in targets]
                allowed = {record["name"] for record in manifest["files"]}
                for path in bundle.iterdir():
                    if path.name not in allowed | {build_manifest.MANIFEST_NAME, build_manifest.CHECKSUMS_NAME}:
                        path.unlink()
                self.rewrite_manifest(bundle, manifest)
                result = self.validate(bundle, catalogue=None)
                self.assertEqual(result["schemaVersion"], 1)
                manifest["catalogue"] = []
                self.rewrite_manifest(bundle, manifest)
                with self.assertRaises(build_manifest.ManifestError):
                    self.validate(bundle)

    def test_cli_creates_and_validates_fixture_without_release(self):
        import subprocess
        bundle = Path(self.temporary.name) / "cli-bundle"
        bundle.mkdir()
        for names in build_manifest.expected_payloads(self.root).values():
            for name in names:
                shutil.copyfile(self.bundle / name, bundle / name)
        (self.root / "release-intent.json").write_text(json.dumps({
            "schemaVersion": 2, "catalogue": self.catalogue,
        }), encoding="utf-8")
        common = ["--bundle-dir", str(bundle), "--root", str(self.root),
                  "--repository", self.repository, "--source-commit", self.source_commit,
                  "--material-fingerprint", self.fingerprint, "--version", self.version,
                  "--workflow-path", self.workflow_path, "--workflow-run-id", self.run_id]
        script = str(REPOSITORY_ROOT / "scripts/build-manifest.py")
        for command in (["create", *common, "--workflow-ref", self.workflow_ref, "--workflow-run-attempt", "2"], ["validate", *common]):
            result = subprocess.run([sys.executable, script, *command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["schemaVersion"], 2)
        (self.root / "release-intent.json").write_text('{"schemaVersion":1}', encoding="utf-8")
        result = subprocess.run([sys.executable, script, "validate", *common], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


class WorkflowContractTests(unittest.TestCase):
    def test_ci_material_builds_once_and_release_builds_twice(self) -> None:
        builder = (
            REPOSITORY_ROOT / "scripts" / "build-qualified-target.sh"
        ).read_text(encoding="utf-8")
        self.assertEqual(builder.count("scripts/build-image.py"), 2)
        self.assertIn('if [[ "${mode}" == "release" ]]', builder)
        ci = (
            REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("scripts/build-qualified-target.sh", ci)
        self.assertIn("retention-days: 90", ci)
        self.assertGreaterEqual(ci.count("github.event_name == 'push'"), 3)
        self.assertEqual(classifier.TARGETS, build_manifest.TARGETS)
        self.assertIn("ubuntu-resolute-snapshot", classifier.TARGETS)
        gate = (REPOSITORY_ROOT / "scripts" / "validate-change.sh").read_text(
            encoding="utf-8"
        )
        for target in classifier.TARGETS:
            self.assertIn(target, gate)
        self.assertIn("matrix.target == 'ubuntu-resolute-snapshot'", ci)
        self.assertIn("matrix.target == 'debian-trixie-snapshot'", ci)

    def test_release_only_promotes_and_never_constructs_or_smokes(self) -> None:
        release = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("build-image.py", release)
        self.assertNotIn("smoke-rootfs.sh", release)
        self.assertIn("ubuntu-resolute-snapshot", release)
        self.assertIn("not the catalogue name", release)
        self.assertNotRegex(release, r"(?m)^  (build|proot):$")
        self.assertIn("gh attestation verify", release)
        self.assertIn("scripts/build-manifest.py", release)


if __name__ == "__main__":
    unittest.main()
