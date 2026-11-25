"""Owned registry/bootstrap contracts; vendor code objects are compiled, never called here."""

import hashlib
import importlib.util
import io
import json
import logging
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from optional_backend_fixture import install_runtime
import fy_sdk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
importer = importlib.import_module("decky_runtime.importer")

SOURCE_SHA = "6fd4d012568edae22ba421495455b3c02899155e4c7484f5aa5be6ce9b14dd5f"
ENTRY_ID = "framey.storage-cleaner.delete-cache-await.v1"
REGISTRY_VERSION = "framey-owned-decky-patches-v1"
HELPER = "_framey_decky_patches.py"


def original_source():
    return (ROOT / "tests/fixtures/storage-cleaner-original/main.py").read_bytes()


class OwnedPatchContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framey-owned-patch-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        helper = ROOT / "decky_runtime/compat/decky_patches.py"
        self.assertTrue(helper.is_file(), "Framey-owned production registry is missing")
        from decky_runtime.compat import decky_patches

        self.subject = decky_patches

    def static_module(self, label="arbitrary-wrapper-id"):
        source = original_source()
        directory = self.root / label
        directory.mkdir()
        file = directory / "main.py"
        file.write_bytes(source)
        module = types.ModuleType("owned_static_" + label.replace("-", "_"))
        module.__file__ = str(file)
        module.homeDir = str(self.home)
        compiled = compile(source, str(file), "exec", dont_inherit=True)
        plugin = next(
            c for c in compiled.co_consts if isinstance(c, types.CodeType) and c.co_name == "Plugin"
        )
        methods = {
            c.co_name: classmethod(types.FunctionType(c, module.__dict__))
            for c in plugin.co_consts
            if isinstance(c, types.CodeType)
        }
        methods["__module__"] = module.__name__
        module.Plugin = type("Plugin", (), methods)
        return module, source

    def test_unknown_source_does_not_inspect_or_change_runtime(self):
        module = types.SimpleNamespace(Plugin=object())
        before = vars(module).copy()
        self.assertEqual(self.subject.apply_known_patches(module, b"unknown bytes", self.home), [])
        self.assertEqual(vars(module), before)

    async def test_known_source_matches_without_wrapper_id_and_replaces_only_delete(self):
        for label in ["chosen-custom-id", "not-catalog-50"]:
            module, source = self.static_module(label)
            before = dict(module.Plugin.__dict__)
            receipts = self.subject.apply_known_patches(module, source, self.home)
            self.assertEqual([r["entry_id"] for r in receipts], [ENTRY_ID])
            self.assertEqual(receipts[0]["path_profile"], "primary-Steam-cache-paths-v2")
            self.assertIsNot(module.Plugin.__dict__["delete_cache"], before["delete_cache"])
            for name in ["get_size", "list_games_with_temp_data", "_listdirs", "_convert_size"]:
                self.assertIs(module.Plugin.__dict__[name], before[name])
            self.assertEqual(Path(module.__file__).read_bytes(), source)
            self.assertEqual(hashlib.sha256(source).hexdigest(), SOURCE_SHA)

    def test_known_bytes_wrong_home_class_signature_or_loaded_file_fail_closed(self):
        for variant in ["home", "class", "signature", "file", "globals"]:
            with self.subTest(variant=variant):
                module, source = self.static_module("variant-" + variant)
                if variant == "home":
                    module.homeDir = str(self.root)
                elif variant == "class":
                    module.Plugin = object()
                elif variant == "signature":

                    async def wrong(cls, other):
                        pass

                    module.Plugin.delete_cache = classmethod(wrong)
                elif variant == "globals":
                    method = module.Plugin.__dict__["delete_cache"].__func__
                    module.Plugin.delete_cache = classmethod(
                        types.FunctionType(method.__code__, {})
                    )
                else:
                    Path(module.__file__).write_bytes(source + b"\n")
                with self.assertRaises(self.subject.PatchContractError):
                    self.subject.apply_known_patches(module, source, self.home)


class PackagedPatchBootstrap(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framey-owned-patch-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        package = ROOT / "decky_runtime"
        files = {path.relative_to(package).as_posix(): path.read_bytes()
                 for path in package.rglob("*") if path.is_file()
                 and "__pycache__" not in path.parts and path.name != "runtime-version.json"}
        self.descriptor = install_runtime(self.home, files)
        self.home_patch = patch.object(Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)


    def archive(self, source=None, extra=None):
        files = {
            "plugin.json": b'{"name":"Owned fixture","api_version":2,"flags":[]}',
            "package.json": b'{"version":"1.0.0","type":"module"}',
            "dist/index.js": b'export default () => ({content:"owned"});',
            "main.py": source
            or b"class Plugin:\n    async def _migration(self):\n        self.value = 7\n    async def get_value(self):\n        return self.value\n",
            "LICENSE": b"ORIGINAL LICENSE UNCHANGED\n",
            **(extra or {}),
        }
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            for name, data in files.items():
                archive.writestr("owned/" + name, data)
        data = raw.getvalue()
        return data, hashlib.sha256(data).hexdigest(), files

    def package(self, source=None, extra=None):
        raw, digest, files = self.archive(source, extra)
        dest = self.root / "custom-user-chosen-wrapper-id"
        importer.import_plugin(raw, digest, dest, "custom-user-chosen-wrapper-id")
        return dest, files

    def load_wrapper(self, dest):
        dirs = {name: self.root / name for name in ["config", "data", "runtime", "logs"]}
        sdk = fy_sdk.build(
            "owned_patch", {"plugin_dir": str(dest)}, dirs,
            {"plugin_dir": str(dest), "decky_runtime": self.descriptor},
            types.SimpleNamespace(emit=lambda *_: True),
        )
        self.addCleanup(sdk.log.close)
        spec = importlib.util.spec_from_file_location(
            "owned_generated_wrapper", dest / "backend.py"
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"framey_api": sdk}):
            spec.loader.exec_module(module)
        return module

    def test_package_keeps_exact_vendor_digest_map_and_separate_owned_provenance(self):
        dest, files = self.package(original_source())
        helper = dest / HELPER
        self.assertFalse(helper.exists(), "Thin wrappers must not contain a copied runtime helper")
        record = json.loads((dest / "decky-provenance.json").read_text())
        self.assertEqual(
            record["original_files"],
            {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        )
        for name, data in files.items():
            self.assertEqual((dest / "vendor/decky" / name).read_bytes(), data)
        self.assertEqual(record["runtime"], {"abi": 1, "wrapper_version": 2, "profile": 1})
        self.assertEqual(record["framey_features"]["rpc_envelope"], "framey-rpc-value-v1")


    async def test_verified_runtime_registry_ignores_vendor_and_module_cache_shadow(self):
        poison = b'raise RuntimeError("VENDOR HELPER SHADOW EXECUTED")\n'
        dest, _ = self.package(extra={HELPER: poison, "decky_patches.py": poison})
        self.assertFalse((dest / HELPER).exists(), "A thin stub must not copy the runtime registry")
        shadow = types.ModuleType("_framey_owned_decky_patches")
        shadow.apply_known_patches = lambda *args: (_ for _ in ()).throw(
            AssertionError("cached shadow used")
        )
        with (
            patch.dict(sys.modules, {"_framey_owned_decky_patches": shadow}),
            patch.object(sys, "path", [str(dest / "vendor/decky"), *sys.path]),
        ):
            module = self.load_wrapper(dest)
            metadata = await module._framey_bridge.start()
            try:
                self.assertEqual(
                    metadata["framey_patches"],
                    {"registry_version": REGISTRY_VERSION, "applied": []},
                )
                self.assertEqual(await module._framey_bridge.call("get_value", {"args": [], "legacy": False}), {"__framey_decky_rpc_result__": 1, "value": 7})
            finally:
                await module._framey_bridge.stop()

    def test_missing_or_tampered_selected_runtime_helper_refuses_before_original_import(self):
        dest, _ = self.package(b'raise AssertionError("VENDOR BODY MUST NOT RUN")\n')
        helper = Path(self.descriptor["root"]) / "compat/decky_patches.py"
        for state in ["missing", "tampered"]:
            with self.subTest(state=state):
                if state == "missing":
                    helper.unlink(missing_ok=True)
                else:
                    helper.write_bytes(b'raise AssertionError("TAMPERED HELPER EXECUTED")\n')
                with self.assertRaises(fy_sdk.FrameyApiError):
                    self.load_wrapper(dest)


if __name__ == "__main__":
    unittest.main()
