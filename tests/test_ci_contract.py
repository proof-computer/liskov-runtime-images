from __future__ import annotations

import importlib.util
import json
import shutil
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
                "v4-control": {"outputStem": "v4"},
            }
        }
        (self.root / "sources.lock.json").write_text(
            json.dumps(lock),
            encoding="utf-8",
        )
        for names in build_manifest.expected_payloads(self.root).values():
            for name in names:
                (self.bundle / name).write_bytes(f"payload:{name}".encode())
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

    def test_release_only_promotes_and_never_constructs_or_smokes(self) -> None:
        release = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("build-image.py", release)
        self.assertNotIn("smoke-rootfs.sh", release)
        self.assertNotRegex(release, r"(?m)^  (build|proot):$")
        self.assertIn("gh attestation verify", release)
        self.assertIn("scripts/build-manifest.py", release)


if __name__ == "__main__":
    unittest.main()
