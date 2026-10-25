import builtins
import contextlib
import hashlib
import importlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fy_decky_catalog
import fy_pkg
import fy_sdk
import fy_store


def bundle(manifest=None, native=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps({"name": "Sample"} if manifest is None else manifest))
        archive.writestr("package.json", json.dumps({"name": "sample", "version": "1.0.0"}))
        for name in ("main.py", "backend.py", "setup.py", "__init__.py", "sitecustomize.py"):
            archive.writestr(name, "raise AssertionError('selected archive code was executed')\n")
        archive.writestr("dist/index.js", "throw new Error('selected archive frontend was executed');")
        if native is not None:
            archive.writestr("bin/helper", native)
    return data.getvalue()


def elf(machine):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[16:18] = (2).to_bytes(2, "little")
    header[18:20] = machine.to_bytes(2, "little")
    header[20:24] = (1).to_bytes(4, "little")
    header[52:54] = (64).to_bytes(2, "little")
    return bytes(header)


class SdkCompatibilityDeclarationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-sdk-compat-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        dirs = fy_store.dirs("sample", home=root / "home", runtime_base=root / "runtime")
        self.api = fy_sdk.build("sample", {}, dirs, {"platform": "linux-aarch64", "framey": "1.1.0"}, object())

    def test_provided_sdk_defaults_do_not_make_undeclared_package_compatible(self):
        self.assertEqual(self.api.API, 1)
        for manifest in ({}, {"api": None, "platform": None, "framey": None}, {"framey": {}, "platform": []}):
            with self.subTest(manifest=manifest):
                result = fy_pkg.decide(manifest, self.api.runtime.framey, self.api.runtime.platform, self.api.API)
                self.assertEqual(result["state"], "unknown")
                self.assertEqual(result["badge"], "unknown")

    def test_explicit_unsupported_api_is_incompatible_on_the_actual_sdk(self):
        for generation in (self.api.API + 1, 1000):
            with self.subTest(generation=generation):
                result = fy_pkg.decide({"api": generation}, self.api.runtime.framey, self.api.runtime.platform, self.api.API)
                self.assertEqual(result["state"], "incompatible")
                self.assertEqual(result["badge"], "api")

    def test_declared_intel_only_port_remains_incompatible_on_arm64(self):
        result = fy_pkg.decide({"api": self.api.API, "platform": ["linux-x86_64"]},
                               self.api.runtime.framey, self.api.runtime.platform, self.api.API)
        self.assertEqual(result["state"], "incompatible")
        self.assertEqual(result["badge"], "platform")


class SelectedDeckyArchiveSdkTests(unittest.TestCase):
    def inspect(self, data):
        return fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest(), "linux-aarch64")

    def test_missing_declarations_and_no_native_code_never_mean_works(self):
        result = self.inspect(bundle())
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["architecture_status"], "unverified")
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertTrue(result["not_executed"])

    def test_native_arm64_evidence_does_not_establish_sdk_or_vr_compatibility(self):
        result = self.inspect(bundle(native=elf(183)))
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertFalse(result["runtime_verified"])
        self.assertEqual(result["basis"], "static_package_inspection")
        self.assertIn(fy_decky_catalog.PORT_REASON, result["reasons"])

    def test_framey_looking_declarations_cannot_admit_a_foreign_bundle(self):
        for declared_api in (1, 2):
            with self.subTest(declared_api=declared_api):
                data = bundle({"name": "Sample", "api": declared_api,
                               "platform": ["linux-aarch64"], "framey": {"min": "1.0.0"}})
                result = self.inspect(data)
                self.assertEqual(result["state"], "port_required")
                self.assertEqual(result["status"], "incompatible")

    def test_inspection_cannot_import_execute_launch_or_extract_selected_code(self):
        data = bundle()
        with contextlib.ExitStack() as stack:
            for owner, attribute in ((builtins, "exec"), (builtins, "eval"),
                                     (importlib, "import_module"), (subprocess, "Popen"),
                                     (zipfile.ZipFile, "extract"), (zipfile.ZipFile, "extractall"),
                                     (fy_sdk, "build")):
                stack.enter_context(mock.patch.object(owner, attribute, side_effect=AssertionError(attribute + " was called")))
            result = self.inspect(data)
        self.assertTrue(result["not_executed"])
        self.assertTrue(result["has_backend"])
        self.assertTrue(result["has_frontend"])


if __name__ == "__main__":
    unittest.main()
