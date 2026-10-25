"""Exercise the source-port example with real SDK storage and worker dispatch."""

import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "a"))

import fy_pkg
import fy_sdk
import harness

EXAMPLE = ROOT / "examples" / "decky-style"
MANIFEST = json.loads((EXAMPLE / "plugin.json").read_text())


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME_FILES = ("plugin.json", "main.js", "backend.py")


def runtime_package():
    """Build a fixture from owned runtime files, never an external Decky release."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in RUNTIME_FILES:
            archive.writestr("decky-style/" + name, (EXAMPLE / name).read_bytes())
    return data.getvalue()


class Rpc:
    def __init__(self):
        self.events = []

    def emit(self, name, payload):
        self.events.append((name, payload))
        return True


class PackageTests(unittest.TestCase):
    def test_manifest_explicitly_supports_arm64_without_native_tools(self):
        normalized = fy_pkg.normalize_manifest(MANIFEST)
        self.assertTrue(normalized["ok"], normalized["errors"])
        self.assertEqual(MANIFEST["platform"], ["linux-aarch64", "linux-x86_64"])
        self.assertEqual(fy_pkg.decide(MANIFEST, "1.1.0", "linux-aarch64")["state"], "ok")
        self.assertEqual(fy_pkg.decide(MANIFEST, "1.1.0", "linux-armv7l")["state"], "incompatible")
        self.assertEqual(normalized["value"]["companions"], [])
        self.assertNotIn("privileged", MANIFEST)

    def test_runtime_package_extracts_as_native_framey_layout(self):
        with tempfile.TemporaryDirectory(prefix="fy-decky-package-") as temporary:
            extracted = Path(temporary) / "decky-style"
            self.assertEqual(fy_pkg.safe_extract_zip(runtime_package(), extracted), sorted(RUNTIME_FILES))
            self.assertEqual(sorted(item.name for item in extracted.iterdir()), sorted(RUNTIME_FILES))
            for name in RUNTIME_FILES:
                self.assertEqual((extracted / name).read_bytes(), (EXAMPLE / name).read_bytes())
            manifest = json.loads((extracted / "plugin.json").read_text())
            self.assertTrue(fy_pkg.normalize_manifest(manifest)["ok"])
            self.assertEqual(fy_pkg.decide(manifest, "1.1.0", "linux-aarch64")["state"], "ok")

    def test_committed_main_is_exact_shared_helper_plus_source(self):
        builder = load_module("fy_decky_example_builder", EXAMPLE / "build.py")
        self.assertEqual((EXAMPLE / "main.js").read_text(), builder.bundle())
        for name in ("backend.py", "source.js", "README.md"):
            self.assertTrue((EXAMPLE / name).is_file())


class BackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-example-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dirs = {key: str(self.root / key) for key in ("config", "data", "runtime", "logs")}
        self.rpc = Rpc()
        runtime = {"framey": "1.1.0", "api": 1, "platform": "linux-aarch64", "python": "3.11.2"}
        self.sdk = fy_sdk.build("decky-style", MANIFEST, self.dirs, runtime, self.rpc)
        patcher = mock.patch.dict(sys.modules, {"framey_api": self.sdk})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = load_module("fy_decky_example_backend", EXAMPLE / "backend.py")

    async def test_persists_goal_and_count_across_backend_reimports(self):
        self.assertEqual(await self.backend.call("read_settings", []), {"goal": "Practice", "count": 0})
        self.assertEqual(await self.backend.call("save_goal", ["Read"]), {"goal": "Read", "count": 0})
        self.assertEqual(await self.backend.call("check_in", []), {"goal": "Read", "count": 1})
        again = load_module("fy_decky_example_backend_again", EXAMPLE / "backend.py")
        self.assertEqual(await again.call("read_settings", []), {"goal": "Read", "count": 1})
        saved = json.loads((self.root / "config" / "settings.json").read_text())
        self.assertEqual(saved, {"goal": "Read", "count": 1})
        self.assertEqual(await again.call("reset_count", []), {"goal": "Read", "count": 0})

    async def test_event_contains_the_already_saved_snapshot_in_positional_array(self):
        seen = []
        original = self.rpc.emit

        def observe(name, payload):
            seen.append(json.loads((self.root / "config" / "settings.json").read_text())["count"])
            return original(name, payload)

        self.rpc.emit = observe
        result = await self.backend.call("check_in", [])
        self.assertEqual(self.rpc.events, [("check_in", [result])])
        self.assertEqual(seen, [1])
        self.assertTrue(fy_sdk.EVENT_NAME.fullmatch("check_in"))

    async def test_bad_arguments_never_change_settings(self):
        cases = [("save_goal", [None]), ("save_goal", [[]]), ("save_goal", ["Hardware"]),
                 ("save_goal", []), ("save_goal", ["Read", "Plan"]), ("save_goal", {"goal": "Read"}),
                 ("check_in", None), ("check_in", [True]), ("read_settings", [1]),
                 ("reset_count", [0]), ("missing", []), (None, [])]
        for method, args in cases:
            result = await self.backend.call(method, args)
            self.assertEqual(list(result), ["error"], (method, args))
        self.assertEqual(self.sdk.settings.all(), {})
        self.assertEqual(self.rpc.events, [])

    async def test_corrupt_values_have_safe_defaults_and_counter_is_bounded(self):
        self.sdk.settings.update({"goal": ["Read"], "count": True})
        self.assertEqual(await self.backend.call("read_settings", []), {"goal": "Practice", "count": 0})
        self.sdk.settings.update({"goal": "Read", "count": self.backend.MAX_COUNT})
        self.assertIn("error", await self.backend.call("check_in", []))
        self.assertEqual(self.rpc.events, [])
        self.assertEqual(self.sdk.settings.get("count"), self.backend.MAX_COUNT)

    async def test_unload_is_repeatable_and_keeps_saved_settings(self):
        await self.backend.call("save_goal", ["Plan"])
        self.backend.unload("reload")
        self.backend.unload("disable")
        self.assertEqual(self.sdk.settings.get("goal"), "Plan")
        self.assertIn("unloaded (disable)", (self.root / "logs" / "plugin.log").read_text())


class WorkerTests(unittest.TestCase):
    def test_real_worker_dispatches_calls_and_emits_native_event(self):
        with tempfile.TemporaryDirectory(prefix="fy-decky-wire-") as temporary:
            root = Path(temporary)
            for name in ("fy_worker.py", "fy_proto.py", "fy_sdk.py", "fy_store.py"):
                shutil.copy(ROOT / name, root / name)
            extracted = root / "plugins" / "decky-style"
            fy_pkg.safe_extract_zip(runtime_package(), extracted)
            wire = harness.Wire(root)
            try:
                start = harness.startup_message(1, "decky-style", extracted / "backend.py", root)
                self.assertTrue(wire.request(start)["ok"])
                wire.send({"op": "call", "id": 2, "method": "check_in", "arg": []})
                emitted = []
                while True:
                    message = wire.recv()
                    self.assertIsNotNone(message)
                    if message.get("ev") == "emit":
                        emitted.append((message["name"], message["payload"]))
                    if message.get("re") == 2:
                        self.assertEqual(message["result"], {"goal": "Practice", "count": 1})
                        break
                self.assertEqual(emitted, [("check_in", [{"goal": "Practice", "count": 1}])])
                self.assertTrue(wire.request({"op": "unload", "id": 3, "reason": "reload"})["ok"])
            finally:
                wire.close()


if __name__ == "__main__":
    unittest.main()
