"""Optional backend capability through owned payloads and the actual Worker."""

import asyncio
import copy
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import fy_sdk
import fy_store
import fy_worker
from optional_backend_fixture import install_runtime

ROOT = Path(__file__).resolve().parents[1]


def own_entry(marker):
    return (
        "from pathlib import Path\n"
        f"TRACE=Path({str(marker)!r})\n"
        "TRACE.write_text('entry\\n')\nABI=1\n"
        "class Adapter:\n"
        " def __init__(self,root,fy): self.root=root\n"
        " async def start(self): TRACE.write_text(TRACE.read_text()+'start\\n')\n"
        " async def call(self,method,arg):\n"
        "  TRACE.write_text(TRACE.read_text()+'call\\n')\n"
        "  return {'method':method,'arg':arg,'root':str(self.root)}\n"
        " async def stop(self): TRACE.write_text(TRACE.read_text()+'stop\\n')\n"
        "def backend_adapter(original_root,fy,*,profile,rpc_result_version):\n"
        " if type(profile) is not int or profile!=1 or type(rpc_result_version) is not int or rpc_result_version!=1: raise ValueError('ABI')\n"
        " return Adapter(original_root,fy)\n"
    ).encode()


class OptionalBackend(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.marker = self.base / "trace"
        self.descriptor = install_runtime(
            self.home, {"__init__.py": b"", "runtime.py": own_entry(self.marker)}
        )
        self.plugin = self.base / "plugin"
        (self.plugin / "vendor/decky").mkdir(parents=True)
        self.vendor = self.plugin / "vendor/decky"
        self.dirs = fy_store.dirs("owned", home=self.home, runtime_base=self.base / "run")

    def sdk(self, descriptor=True):
        runtime = {"plugin_dir": self.plugin}
        if descriptor is True:
            runtime["decky_runtime"] = self.descriptor
        elif descriptor is not None:
            runtime["decky_runtime"] = descriptor
        return fy_sdk.build("owned", {}, self.dirs, runtime, object())

    def retire(self):
        path = Path(self.descriptor["state_path"])
        state = json.loads(path.read_text())
        state["enabled"] = False
        state["generation"] += 1
        path.write_text(json.dumps(state))

    async def test_native_build_has_no_payload_import_and_denies_capability(self):
        api = self.sdk(None)
        self.assertFalse(self.marker.exists())
        with self.assertRaises(fy_sdk.FrameyApiError):
            api.compat.backend(original_root=self.vendor, profile=1, rpc_result_version=1)
        self.assertFalse(self.marker.exists())

    async def test_native_settings_work_with_optional_payload_physically_absent(self):
        shutil.rmtree(self.descriptor["root"])
        api = self.sdk(None)
        api.settings.set("native", {"value": 3})
        self.assertEqual(api.settings.get("native"), {"value": 3})
        self.assertFalse(self.marker.exists())
        self.assertFalse(Path(self.descriptor["root"]).exists())

    async def test_private_relative_modules_ignore_vendor_name_collisions(self):
        payload = (
            own_entry(self.marker)
            + b"\nfrom .helper import VALUE\nassert VALUE == 'owned runtime'\n"
        )
        self.descriptor = install_runtime(
            self.home,
            {"__init__.py": b"", "runtime.py": payload, "helper.py": b"VALUE = 'owned runtime'\n"},
        )
        vendor_marker = self.base / "vendor-helper-ran"
        (self.vendor / "helper.py").write_text(
            f"from pathlib import Path\nPath({str(vendor_marker)!r}).write_text('bad')\nVALUE='vendor'\n"
        )
        before = list(sys.path)
        adapter = self.sdk().compat.backend(
            original_root=self.vendor, profile=1, rpc_result_version=1
        )
        await adapter.start()
        await adapter.stop()
        self.assertFalse(vendor_marker.exists())
        self.assertEqual(sys.path, before)
        self.assertEqual(self.marker.read_text(), "entry\nstart\nstop\n")

    async def test_adapter_runs_owned_payload_then_revokes_calls_but_allows_stop(self):
        api = self.sdk()
        adapter = api.compat.backend(original_root=self.vendor, profile=1, rpc_result_version=1)
        await adapter.start()
        self.assertEqual(
            await adapter.call("echo", {"args": [4], "legacy": False}),
            {"method": "echo", "arg": {"args": [4], "legacy": False}, "root": str(self.vendor)},
        )
        self.retire()
        with self.assertRaises(fy_sdk.FrameyApiError):
            await adapter.call("echo", {})
        await adapter.stop()
        self.assertEqual(self.marker.read_text(), "entry\nstart\ncall\nstop\n")

    async def test_vendor_escape_and_bool_profile_fail_before_payload_import(self):
        api = self.sdk()
        for root, profile in [(self.base, 1), (self.vendor, True)]:
            with self.subTest(root=root, profile=profile), self.assertRaises(fy_sdk.FrameyApiError):
                api.compat.backend(original_root=root, profile=profile, rpc_result_version=1)
        self.assertFalse(self.marker.exists())

    async def test_vendor_alias_created_after_acquisition_cannot_start(self):
        adapter = self.sdk().compat.backend(
            original_root=self.vendor, profile=1, rpc_result_version=1
        )
        self.vendor.rename(self.plugin / "vendor/retained")
        foreign = self.base / "foreign"
        foreign.mkdir()
        self.vendor.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(fy_sdk.FrameyApiError):
            await adapter.start()
        await adapter.stop()
        self.assertEqual(self.marker.read_text(), "entry\nstop\n")

    async def test_retired_descriptor_fails_before_backend_module_import(self):
        marker = self.base / "backend-imported"
        backend = self.plugin / "backend.py"
        backend.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
        self.retire()
        worker = fy_worker.Worker(
            asyncio.get_running_loop(), -1, sdk_module=fy_sdk, store_module=fy_store
        )
        worker.limits_configured = True
        with self.assertRaises(fy_worker.Fail):
            worker.init(
                {
                    "plugin": "owned",
                    "file": str(backend),
                    "dirs": self.dirs._asdict(),
                    "meta": {"decky_compat": {"profile": 1}},
                    "runtime": {"decky_runtime": self.descriptor},
                }
            )
        self.assertFalse(marker.exists())

    async def test_managed_backend_without_descriptor_fails_before_module_import(self):
        marker = self.base / "backend-imported"
        backend = self.plugin / "backend.py"
        backend.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
        worker = fy_worker.Worker(
            asyncio.get_running_loop(), -1, sdk_module=fy_sdk, store_module=fy_store
        )
        worker.limits_configured = True
        with self.assertRaises(fy_worker.Fail):
            worker.init(
                {
                    "plugin": "owned",
                    "file": str(backend),
                    "dirs": self.dirs._asdict(),
                    "meta": {"decky_compat": {"profile": 1}},
                }
            )
        self.assertFalse(marker.exists())

    async def test_descriptor_copy_cannot_be_mutated_through_build_input(self):
        descriptor = copy.deepcopy(self.descriptor)
        api = self.sdk(descriptor)
        descriptor["root"] = str(self.base / "foreign")
        adapter = api.compat.backend(original_root=self.vendor, profile=1, rpc_result_version=1)
        await adapter.start()
        await adapter.stop()
        self.assertEqual(self.marker.read_text(), "entry\nstart\nstop\n")

    async def test_isolated_python_resolves_fixed_core_manager_without_checkout_search_path(self):
        script = f"""import importlib.util,json,asyncio,sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("owned_worker",{str(ROOT / "fy_worker.py")!r})\nw=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)\nsdk=w.optional("fy_sdk");store=w.optional("fy_store")\nassert {str(ROOT)!r} not in sys.path\nd=json.loads({json.dumps(self.descriptor)!r})\na=sdk.build("owned",{{}},store.dirs("owned",home=Path({str(self.home)!r}),runtime_base=Path({str(self.base / "run")!r})),{{"plugin_dir":Path({str(self.plugin)!r}),"decky_runtime":d}},object())\nb=a.compat.backend(original_root=Path({str(self.vendor)!r}),profile=1,rpc_result_version=1)\nasyncio.run(b.start());asyncio.run(b.stop())\n"""
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            cwd=self.base,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.marker.read_text(), "entry\nstart\nstop\n")
