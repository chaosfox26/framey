"""Offline release-builder tests; fixture files are inert owned source bytes."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools" / "pack_decky_runtime.py"
PAYLOAD = (
    "__init__.py",
    "runtime.py",
    "catalog.py",
    "importer.py",
    "code.py",
    "http.py",
    "compat/framey-view.js",
    "compat/decky-assets.js",
    "compat/decky-navigation.js",
    "compat/decky-menus.js",
    "compat/decky-sidebar.js",
    "compat/decky-modules.js",
    "compat/decky-host.js",
    "compat/decky_backend.py",
    "compat/decky_patches.py",
    "client.js",
    "frontend/store.js",
)


class RuntimeBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source" / "decky_runtime"
        self.source.mkdir(parents=True)
        for name in PAYLOAD:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("owned fixture: " + name + "\r\n").encode())
        (self.source.parent / "LICENSE").write_bytes(b"owned fixture license\n")
        self.version = {
            "schema": 1,
            "version": "0.1.0",
            "manager_api": 1,
            "core_api": 1,
            "compat_profile": 1,
            "platform": ["linux-x86_64", "linux-aarch64"],
        }
        self.write_version()

    def write_version(self):
        (self.source / "runtime-version.json").write_text(
            json.dumps(self.version), encoding="utf-8"
        )

    def builder(self):
        self.assertTrue(BUILDER.is_file(), "The deterministic runtime builder is missing")
        spec = importlib.util.spec_from_file_location("owned_pack_decky_runtime", BUILDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_zip_and_metadata_are_reproducible_and_match_exact_payload(self):
        # Catches timestamps/modes/order, source-byte transformation, hash drift,
        # omitted helper files, and accidental private/source metadata disclosure.
        builder = self.builder()
        out1, out2 = self.root / "out1", self.root / "out2"
        builder.build_runtime(self.source, out1, tag="decky-runtime-v0.1.0")
        for name in PAYLOAD:
            os.utime(self.source / name, (1700000000, 1700000000))
            os.chmod(self.source / name, 0o700)
        builder.build_runtime(self.source, out2, tag="decky-runtime-v0.1.0")
        expected = {"decky-runtime-0.1.0.zip", "decky-runtime.json", "manifest.json"}
        self.assertEqual({p.name for p in out1.iterdir()}, expected)
        for name in expected:
            self.assertEqual((out1 / name).read_bytes(), (out2 / name).read_bytes())
        raw = (out1 / "decky-runtime-0.1.0.zip").read_bytes()
        manifest_raw = (out1 / "manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        self.assertEqual(
            set(manifest),
            {"schema", "version", "manager_api", "core_api", "compat_profile", "platform", "files"},
        )
        self.assertEqual(manifest["platform"], ["linux-aarch64", "linux-x86_64"])
        self.assertEqual(set(manifest["files"]), set(PAYLOAD) | {"LICENSE"})
        with zipfile.ZipFile(out1 / "decky-runtime-0.1.0.zip") as archive:
            names = archive.namelist()
            self.assertEqual(
                names,
                sorted("decky_runtime/" + n for n in set(PAYLOAD) | {"LICENSE", "manifest.json"}),
            )
            self.assertEqual(archive.read("decky_runtime/manifest.json"), manifest_raw)
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(stat.S_IMODE(info.external_attr >> 16), 0o644)
                self.assertTrue(stat.S_ISREG(info.external_attr >> 16))
                self.assertFalse(info.extra or info.comment or info.flag_bits & 1)
            for name, digest in manifest["files"].items():
                original = (
                    (self.source.parent / "LICENSE") if name == "LICENSE" else self.source / name
                )
                self.assertEqual(archive.read("decky_runtime/" + name), original.read_bytes())
                self.assertEqual(
                    hashlib.sha256(archive.read("decky_runtime/" + name)).hexdigest(), digest
                )
        release = json.loads((out1 / "decky-runtime.json").read_bytes())
        self.assertEqual(
            release,
            {
                "schema": 1,
                "version": "0.1.0",
                "archive": {
                    "url": "https://github.com/chaosfox26/framey/releases/download/decky-runtime-v0.1.0/decky-runtime-0.1.0.zip",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                },
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            },
        )
        self.assertNotIn(str(self.root).encode(), (out1 / "decky-runtime.json").read_bytes())
        self.assertNotIn(str(self.root).encode(), manifest_raw)

    def assert_refused_without_output(self):
        output = self.root / "rejected"
        try:
            self.builder().build_runtime(self.source, output, tag="decky-runtime-v0.1.0")
        except Exception as error:
            self.assertIsInstance(error, ValueError)
            self.assertFalse(output.exists(), "Rejected input must not leave release outputs")
            return
        self.fail("The builder accepted invalid release inputs")

    def test_missing_required_file_is_rejected_before_any_output(self):
        (self.source / "runtime.py").unlink()
        self.assert_refused_without_output()

    def test_unlisted_source_file_is_rejected_instead_of_silently_omitted(self):
        (self.source / "hidden-runtime.py").write_text("owned inert file")
        self.assert_refused_without_output()

    def test_source_file_symlink_is_rejected(self):
        target = self.source / "runtime.py"
        outside = self.root / "outside.py"
        outside.write_text("owned inert outside fixture")
        target.unlink()
        target.symlink_to(outside)
        self.assert_refused_without_output()

    def test_source_directory_symlink_is_rejected(self):
        original = self.source / "compat"
        outside = self.root / "outside-compat"
        original.rename(outside)
        original.symlink_to(outside, target_is_directory=True)
        self.assert_refused_without_output()

    def test_missing_license_is_rejected_before_any_output(self):
        (self.source.parent / "LICENSE").unlink()
        self.assert_refused_without_output()

    def test_unrecognized_version_metadata_is_rejected(self):
        self.version["download_url"] = "https://invalid.example/ignored"
        self.write_version()
        self.assert_refused_without_output()

    def test_boolean_api_version_is_not_an_integer_abi(self):
        self.version["manager_api"] = True
        self.write_version()
        self.assert_refused_without_output()

    def test_duplicate_platform_is_rejected(self):
        self.version["platform"] = ["linux-aarch64", "linux-aarch64"]
        self.write_version()
        self.assert_refused_without_output()

    def test_unsupported_platform_is_rejected(self):
        self.version["platform"] = ["windows-x86_64"]
        self.write_version()
        self.assert_refused_without_output()

    def test_version_cannot_create_release_paths(self):
        self.version["version"] = "0.1.0/../../escape"
        self.write_version()
        self.assert_refused_without_output()

    def test_duplicate_json_field_is_rejected(self):
        raw = json.dumps(self.version)
        (self.source / "runtime-version.json").write_text(raw[:-1] + ',"version":"9.9.9"}')
        self.assert_refused_without_output()

    def test_oversize_file_is_rejected_before_output(self):
        (self.source / "runtime.py").write_bytes(b"x" * 5_000_001)
        self.assert_refused_without_output()

    def test_unsafe_tag_is_rejected_before_output(self):
        output = self.root / "rejected"
        with self.assertRaises(ValueError):
            self.builder().build_runtime(self.source, output, tag="../../another/repo")
        self.assertFalse(output.exists())

    def test_existing_release_output_is_not_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        previous = output / "decky-runtime.json"
        previous.write_bytes(b"retained previous output")
        with self.assertRaises(ValueError):
            self.builder().build_runtime(self.source, output, tag="decky-runtime-v0.1.0")
        self.assertEqual(previous.read_bytes(), b"retained previous output")
        self.assertEqual([p.name for p in output.iterdir()], ["decky-runtime.json"])

    def test_output_inside_source_cannot_become_future_payload(self):
        with self.assertRaises(ValueError):
            self.builder().build_runtime(
                self.source, self.source / "dist", tag="decky-runtime-v0.1.0"
            )
        self.assertFalse((self.source / "dist").exists())

    def test_cli_builds_real_fixture_archive(self):
        output = self.root / "cli"
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--source",
                str(self.source),
                "--output",
                str(output),
                "--tag",
                "decky-runtime-v0.1.0",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((output / "decky-runtime-0.1.0.zip").is_file())
        self.assertEqual(json.loads(result.stdout)["version"], "0.1.0")

    def test_declared_source_files_cannot_be_hardlinks(self):
        target = self.source / "runtime.py"
        duplicate = self.root / "hardlinked.py"
        os.link(target, duplicate)
        self.assert_refused_without_output()

    def test_expanded_input_budget_is_enforced_before_output(self):
        builder = self.builder()
        with mock.patch.object(builder, "EXPANDED_MAX", 32):
            with self.assertRaises(ValueError):
                builder.build_runtime(
                    self.source, self.root / "rejected", tag="decky-runtime-v0.1.0"
                )
        self.assertFalse((self.root / "rejected").exists())

    def test_zip_byte_budget_is_enforced_before_output(self):
        builder = self.builder()
        with mock.patch.object(builder, "ARCHIVE_MAX", 32):
            with self.assertRaises(ValueError):
                builder.build_runtime(
                    self.source, self.root / "rejected", tag="decky-runtime-v0.1.0"
                )
        self.assertFalse((self.root / "rejected").exists())

    def test_oversize_version_metadata_is_rejected(self):
        (self.source / "runtime-version.json").write_bytes(b" " * 16_385)
        self.assert_refused_without_output()

    def test_generated_bytecode_is_never_part_of_the_runtime_archive(self):
        cache = self.source / "__pycache__"
        cache.mkdir()
        (cache / "not-runtime.pyc").write_bytes(b"owned inert bytecode fixture")
        output = self.root / "clean"
        self.builder().build_runtime(self.source, output, tag="decky-runtime-v0.1.0")
        with zipfile.ZipFile(output / "decky-runtime-0.1.0.zip") as archive:
            self.assertFalse(any("__pycache__" in name for name in archive.namelist()))

    def test_failed_cli_does_not_claim_a_release(self):
        (self.source / "client.js").unlink()
        output = self.root / "failed-cli"
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--source",
                str(self.source),
                "--output",
                str(output),
                "--tag",
                "decky-runtime-v0.1.0",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.stdout)
        self.assertFalse(output.exists())

    def test_fifo_input_is_refused_without_blocking_the_cli(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO fixture requires POSIX")
        target = self.source / "runtime.py"
        target.unlink()
        os.mkfifo(target)
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--source",
                str(self.source),
                "--output",
                str(self.root / "fifo"),
                "--tag",
                "decky-runtime-v0.1.0",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.stdout)
        self.assertFalse((self.root / "fifo").exists())

    def test_source_change_during_snapshot_is_rejected(self):
        builder = self.builder()
        original = builder._read_regular
        changed = False

        def read_and_change(path, maximum):
            nonlocal changed
            raw = original(path, maximum)
            if path.name == "catalog.py" and not changed:
                changed = True
                (self.source / "runtime.py").write_bytes(b"changed own fixture")
            return raw

        output = self.root / "changed"
        with mock.patch.object(builder, "_read_regular", side_effect=read_and_change):
            with self.assertRaises(ValueError):
                builder.build_runtime(self.source, output, tag="decky-runtime-v0.1.0")
        self.assertFalse(output.exists())

    def test_generated_release_and_files_pass_the_real_manager_verifier(self):
        # Producer/consumer proof using inert bytes, never package imports.
        output = self.root / "manager-contract"
        self.builder().build_runtime(self.source, output, tag="decky-runtime-v0.1.0")
        spec = importlib.util.spec_from_file_location(
            "owned_runtime_package_verifier", ROOT / "fy_decky_runtime.py"
        )
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        release, record = manager._release((output / "decky-runtime.json").read_bytes())
        self.assertEqual(release["version"], "0.1.0")
        installed = self.root / "verified-only"
        installed.mkdir()
        with zipfile.ZipFile(output / "decky-runtime-0.1.0.zip") as archive:
            for name in archive.namelist():
                target = installed / name.removeprefix("decky_runtime/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        for platform in ["linux-aarch64", "linux-x86_64"]:
            verified_archive = manager.verify_archive(
                (output / "decky-runtime-0.1.0.zip").read_bytes(),
                (output / "decky-runtime.json").read_bytes(),
                platform=platform,
                core_api=1,
            )
            self.assertEqual(set(verified_archive["files"]), set(PAYLOAD) | {"LICENSE"})
            manifest = manager.verify_revision(
                installed, record["manifest_sha256"], platform=platform, core_api=1
            )
            self.assertEqual(manifest["version"], "0.1.0")
            self.assertEqual(set(manifest["files"]), set(PAYLOAD) | {"LICENSE"})
        (installed / "runtime.py").write_bytes(b"changed own inert fixture")
        with self.assertRaises(ValueError):
            manager.verify_revision(
                installed, record["manifest_sha256"], platform="linux-aarch64", core_api=1
            )


if __name__ == "__main__":
    unittest.main()
