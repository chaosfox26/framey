"""Owned fixtures for the independently loaded optional runtime package."""

import hashlib
import asyncio
import importlib.util
import io
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "decky_runtime"


def owned_archive():
    files = {
        "plugin.json": json.dumps({"name": "Owned fixture", "flags": [], "api_version": 1}),
        "package.json": json.dumps({"version": "1.0.0", "type": "module"}),
        "dist/index.js": "export default () => ({content: 'owned'});\n",
        "main.py": "raise AssertionError('Importing must preserve, never execute vendor bytes')\n",
        "LICENSE": "Owned fixture license\n",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr("fixture/" + name, value)
    return output.getvalue(), files


class OptionalPackage(unittest.TestCase):
    def test_fixed_frontend_bundle_evaluates_to_the_owned_abi_factory(self):
        from decky_runtime import runtime

        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run(
            [node, "-e", "const fs=require('node:fs'),vm=require('node:vm');"
             "const factory=vm.runInNewContext(fs.readFileSync(0,'utf8'),{});"
             "const provider=factory();console.log(JSON.stringify({factory:typeof factory,"
             "abi:provider.abi,attach:typeof provider.attach,store:typeof provider.store}));"],
            input=runtime.frontend_bundle(), capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"factory": "function", "abi": 1,
                                                   "attach": "function", "store": "function"})

    def test_payload_is_a_package_and_old_optional_modules_are_absent(self):
        for name in (
            "__init__.py",
            "runtime.py",
            "catalog.py",
            "importer.py",
            "code.py",
            "http.py",
        ):
            self.assertTrue((PACKAGE / name).is_file(), name)
        for name in (
            "fy_decky_catalog.py",
            "fy_decky_import.py",
            "fy_decky_code.py",
            "fy_code_http.py",
        ):
            self.assertFalse((ROOT / name).exists(), name)

    def test_entry_loads_under_fresh_package_namespace_without_sys_path_changes(self):
        self.assertTrue((PACKAGE / "__init__.py").is_file())
        before = list(sys.path)
        name = "_framey_test_optional_package"
        spec = importlib.util.spec_from_file_location(
            name, PACKAGE / "__init__.py", submodule_search_locations=[str(PACKAGE)]
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        self.addCleanup(
            lambda: [
                sys.modules.pop(key, None)
                for key in list(sys.modules)
                if key == name or key.startswith(name + ".")
            ]
        )
        spec.loader.exec_module(package)
        entry = __import__(name + ".runtime", fromlist=["runtime"])
        self.assertEqual(entry.ABI, 1)
        self.assertTrue(callable(entry.frontend_bundle))
        self.assertTrue(callable(entry.backend_adapter))
        self.assertNotIn(name + ".code", sys.modules)
        self.assertEqual(sys.path, before)

    def test_import_preserves_originals_but_generates_only_thin_capability_stubs(self):
        self.assertTrue((PACKAGE / "importer.py").is_file())
        from decky_runtime import importer

        raw, originals = owned_archive()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "owned"
            importer.import_plugin(raw, hashlib.sha256(raw).hexdigest(), destination, "owned")
            for name, value in originals.items():
                self.assertEqual((destination / "vendor/decky" / name).read_text(), value)
            frontend = (destination / "main.js").read_text()
            backend = (destination / "backend.py").read_text()
            self.assertIn(".compat.mount(", frontend)
            self.assertNotIn("function createFrameyViewRuntime", frontend)
            self.assertIn("fy.compat.backend(", backend)
            self.assertNotIn("class DeckyBackend", backend)
            self.assertNotIn("sys.path", backend)
            self.assertFalse((destination / "_framey_decky_patches.py").exists())
            metadata = json.loads((destination / "plugin.json").read_text())
            self.assertEqual(metadata["decky_compat"]["runtime_abi"], 1)
            self.assertEqual(metadata["decky_compat"]["wrapper_version"], 2)
            provenance = json.loads((destination / "decky-provenance.json").read_text())
            self.assertEqual(provenance["framey_features"]["rpc_envelope"], "framey-rpc-value-v1")


class BackendAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_and_awaits_an_admitted_catalog_job(self):
        from decky_runtime import runtime
        from decky_runtime.catalog import CatalogService

        entered, release = asyncio.Event(), asyncio.Event()

        class Host:
            pass

        async def owned_import(_service, _arg):
            entered.set()
            await release.wait()
            return {"ok": "owned"}

        with tempfile.TemporaryDirectory() as temporary:
            host = Host()
            host.HOME = Path(temporary)
            host.CONF = Path(temporary) / "settings.json"
            with (
                mock.patch.dict(
                    sys.modules, {"fy_decky_runtime": SimpleNamespace(current=lambda *_: True)}
                ),
                mock.patch.object(CatalogService, "import_decky", owned_import),
            ):
                task = asyncio.create_task(runtime.catalog_dispatch(host, "import", {}, object()))
                await entered.wait()
                await runtime.shutdown(host)
                try:
                    self.assertTrue(
                        task.done(), "Shutdown must not leave an admitted import job waiting"
                    )
                finally:
                    release.set()
                    try:
                        await task
                    except (asyncio.CancelledError, ValueError):
                        pass

    async def test_cancelled_catalog_save_drains_the_owned_write_before_returning(self):
        from decky_runtime.catalog import CatalogService, DeckyCatalog

        entered, release = asyncio.Event(), asyncio.Event()

        async def owned_write(_):
            entered.set()
            await release.wait()
            return ""

        with tempfile.TemporaryDirectory() as temporary:
            host = SimpleNamespace(HOME=Path(temporary), CONF=Path(temporary) / "settings.json")
            service = CatalogService(host, current=lambda: None)
            with mock.patch.object(DeckyCatalog, "_save", owned_write):
                task = asyncio.create_task(service._save())
                await entered.wait()
                task.cancel()
                await asyncio.sleep(0)
                try:
                    self.assertFalse(
                        task.done(),
                        "The admitted write must be drained before cancellation returns",
                    )
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

    async def test_inactive_catalog_lease_never_constructs_optional_provider(self):
        from decky_runtime import runtime
        from fy_catalog import CatalogError

        self.assertTrue(hasattr(runtime, "catalog_dispatch"))
        host = SimpleNamespace()
        with mock.patch.dict(
            sys.modules, {"fy_decky_runtime": SimpleNamespace(current=lambda *_: False)}
        ):
            with self.assertRaises(CatalogError) as caught:
                await runtime.catalog_dispatch(host, "list", {"source": "decky_stable"}, object())
        self.assertEqual(caught.exception.code, "runtime_unavailable")

    async def test_owned_backend_uses_original_root_and_versioned_value_result(self):
        self.assertTrue((PACKAGE / "runtime.py").is_file())
        from decky_runtime import runtime

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vendor = root / "vendor"
            vendor.mkdir()
            (vendor / "plugin.json").write_text(
                json.dumps({"name": "Owned fixture", "flags": [], "api_version": 1})
            )
            (vendor / "main.py").write_text(
                "class Plugin:\n    async def value(self):\n        return {'error': 'domain value'}\n"
            )
            api = SimpleNamespace(
                dirs=SimpleNamespace(
                    config=root / "config", data=root / "data", logs=root / "logs"
                ),
                log=logging.getLogger("owned.optional.package"),
                events=SimpleNamespace(emit=None),
            )
            adapter = runtime.backend_adapter(vendor, api, profile=1, rpc_result_version=1)
            await adapter.start()
            try:
                result = await adapter.call("value", {"args": [], "legacy": False})
                self.assertEqual(
                    result, {"__framey_decky_rpc_result__": 1, "value": {"error": "domain value"}}
                )
            finally:
                await adapter.stop()
