import ast
import asyncio
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile
import tokenize
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "a"))

import fy_pkg
import fy_sdk
import fy_store
import fy_worker
import harness

STARTER = ROOT / "examples" / "starter"
MANIFEST = json.loads((STARTER / "plugin.json").read_text())
REAL_SLEEP = asyncio.sleep


class HostError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Rpc:
    def __init__(self):
        self.emitted = []
        self.hosted = []
        self.reply = {"status": 200, "bytes": 1256, "text": "<html>", "headers": {}}

    def emit(self, name, payload):
        self.emitted.append((name, payload))
        return True

    async def host(self, op, arg):
        self.hosted.append((op, arg))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def fast_sleep(_):
    await REAL_SLEEP(0)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_valid_and_complete(self):
        r = fy_pkg.normalize_manifest(MANIFEST)
        self.assertTrue(r["ok"], r["errors"])
        v = r["value"]
        self.assertEqual((v["id"], v["api"], v["data_version"], v["i18n"]["default"]), ("starter", 1, 1, "en"))
        self.assertEqual(v["backend"], {"timeout": 15, "idle": 120, "keepalive": False, "concurrency": 1, "memory_mb": 64, "startup_timeout": 10})
        self.assertEqual(v["companions"], [])
        self.assertTrue(MANIFEST["source"].startswith("https://github.com/"))

    def test_compatibility(self):
        self.assertEqual(fy_pkg.decide(MANIFEST, "1.1.0", "linux-aarch64")["state"], "ok")
        self.assertEqual(fy_pkg.decide(MANIFEST, "1.2.4", "linux-x86_64")["state"], "ok")
        old = fy_pkg.decide(MANIFEST, "1.0.1", "linux-aarch64")
        self.assertEqual((old["state"], old["badge"]), ("incompatible", "newer"))
        self.assertEqual(fy_pkg.decide(MANIFEST, "1.1.0", "linux-armv7l")["badge"], "platform")

    def test_files_present(self):
        for name in ("plugin.json", "main.js", "backend.py", "README.md", "locales/en.json"):
            self.assertTrue((STARTER / name).is_file(), name)

    def test_only_english_ships(self):
        self.assertEqual(sorted(p.name for p in (STARTER / "locales").iterdir()), ["en.json"])

    def test_locale_file_loads_through_the_store_reader(self):
        strings = fy_store.read_locale(STARTER, "en")
        self.assertGreater(len(strings), 20)
        self.assertEqual(strings["title"], "Starter")
        self.assertTrue(all(isinstance(v, str) for v in strings.values()))
        self.assertEqual(fy_store.read_locale(STARTER, "de"), {})

    def test_readme_has_ten_numbered_steps(self):
        text = (STARTER / "README.md").read_text()
        self.assertEqual(len(re.findall(r"^\d+\. ", text, re.M)), 10)
        self.assertIn("not a security sandbox", text)


class BackendBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.rpc = Rpc()
        dirs = {k: str(self.tmp / k) for k in ("config", "data", "runtime", "logs")}
        runtime = {"framey": "1.1.0", "api": 1, "platform": "linux-aarch64", "python": "3.11.2", "plugin_dir": str(STARTER)}
        self.sdk = fy_sdk.build("starter", {"id": "starter", "version": "1.0.0"}, dirs, runtime, self.rpc)
        self.dirs = dirs
        patcher = mock.patch.dict(sys.modules, {"framey_api": self.sdk})
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("fy_starter_backend", STARTER / "backend.py")
        self.backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.backend)
        self.ctx = types.SimpleNamespace(plugin="starter", log=self.sdk.log, settings=self.sdk.settings, runtime=self.sdk.runtime, dirs=self.sdk.dirs, data_from=None, data_to=None, from_version=None, to_version=None, purge=False, reason=None)

    def log_text(self):
        try:
            return (Path(self.dirs["logs"]) / "plugin.log").read_text()
        except OSError:
            return ""


class SettingsTests(BackendBase):
    async def test_defaults_then_saved_values(self):
        b = self.backend
        self.assertEqual(await b.call("get", None), {"enabled": True, "level": 50, "mode": "normal"})
        self.assertEqual(await b.call("set", {"level": 70, "mode": "eco", "enabled": False}), {"ok": True})
        self.assertEqual(await b.call("get", None), {"enabled": False, "level": 70, "mode": "eco"})
        saved = json.loads((Path(self.dirs["config"]) / "settings.json").read_text())
        self.assertEqual(saved, {"level": 70, "mode": "eco", "enabled": False})
        self.assertIn("saved level=70 mode=eco enabled=False", self.log_text())

    async def test_every_bad_argument_is_an_error_reply_and_changes_nothing(self):
        b = self.backend
        good = {"level": 10, "mode": "boost", "enabled": True}
        bad = [None, [], "x", 5, {}, dict(good, level=-1), dict(good, level=101), dict(good, level="5"), dict(good, level=True), dict(good, level=5.5), dict(good, level=None),
               dict(good, mode="turbo"), dict(good, mode=None), dict(good, mode=["eco"]), dict(good, enabled="yes"), dict(good, enabled=1), dict(good, enabled=None)]
        for arg in bad:
            r = await b.call("set", arg)
            self.assertEqual(list(r), ["error"], arg)
            self.assertIsInstance(r["error"], str)
        self.assertFalse((Path(self.dirs["config"]) / "settings.json").exists())

    async def test_unknown_methods(self):
        for m in ("nope", "", None, 5, "x" * 500):
            r = await self.backend.call(m, {})
            self.assertEqual(list(r), ["error"])
            self.assertLess(len(r["error"]), 100)


class JobTests(BackendBase):
    async def test_job_emits_progress_then_done(self):
        b = self.backend
        with mock.patch.object(b.asyncio, "sleep", fast_sleep):
            self.assertEqual(await b.call("start", None), {"ok": True})
            await b.state["job"]
        steps = [("progress", {"step": i, "total": 5}) for i in range(1, 6)]
        self.assertEqual(self.rpc.emitted, steps + [("done", {"steps": 5})])
        self.assertIsNone(b.state["job"])
        self.assertIn("job finished", self.log_text())

    async def test_second_start_is_refused_while_running(self):
        b = self.backend
        gate = asyncio.Event()

        async def hold(_):
            await gate.wait()

        with mock.patch.object(b.asyncio, "sleep", hold):
            self.assertEqual(await b.call("start", None), {"ok": True})
            self.assertEqual(await b.call("start", None), {"error": "a job is already running"})
            gate.set()
            await b.state["job"]
        self.assertEqual(await b.call("start", None), {"ok": True})
        await b.unload("reload")

    async def test_unload_cancels_the_job_and_logs_the_reason(self):
        b = self.backend
        gate = asyncio.Event()

        async def hold(_):
            await gate.wait()

        with mock.patch.object(b.asyncio, "sleep", hold):
            await b.call("start", None)
            await REAL_SLEEP(0)
            await b.unload("reload")
        self.assertIsNone(b.state["job"])
        self.assertEqual(self.rpc.emitted, [])
        self.assertIn("unloaded (reload)", self.log_text())
        await b.unload("idle")

    async def test_events_pass_the_sdk_caps(self):
        names = {n for n, _ in [("progress", 0), ("done", 0)]}
        for n in names:
            self.assertTrue(fy_sdk.EVENT_NAME.fullmatch(n))
        self.assertTrue(self.sdk.events.emit("progress", {"step": 1, "total": 5}))


class FetchTests(BackendBase):
    async def test_fetch_uses_the_sdk_with_an_https_url(self):
        r = await self.backend.call("fetch_demo", None)
        self.assertEqual(r, {"status": 200, "bytes": 1256})
        op, arg = self.rpc.hosted[0]
        self.assertEqual(op, "fetch")
        self.assertTrue(arg["url"].startswith("https://"))
        self.assertEqual(arg["max_bytes"], 65536)
        self.assertEqual(arg["method"], "GET")

    async def test_fetch_errors_become_error_replies(self):
        self.rpc.reply = HostError("blocked_address", "That address is not allowed")
        r = await self.backend.call("fetch_demo", None)
        self.assertEqual(r, {"error": "That address is not allowed", "code": "blocked_address"})
        self.rpc.reply = {"error": {"code": "timeout", "message": "too slow"}}
        r = await self.backend.call("fetch_demo", None)
        self.assertEqual(r, {"error": "too slow", "code": "timeout"})


class HookTests(BackendBase):
    async def test_hook_arities_match_what_the_worker_calls(self):
        b = self.backend
        for name in ("startup", "migrate", "uninstall", "unload"):
            self.assertTrue(fy_worker.takes_arg(getattr(b, name)), name)
        self.assertTrue(inspect.iscoroutinefunction(b.call))

    async def test_startup_and_uninstall_log(self):
        await fy_worker.invoke(self.backend.startup, self.ctx)
        self.ctx.purge = True
        await fy_worker.invoke(self.backend.uninstall, self.ctx)
        text = self.log_text()
        self.assertIn("started starter 1.0.0 on api 1", text)
        self.assertIn("uninstall, purge=True", text)

    async def test_migrate_renames_the_old_key_once(self):
        self.sdk.settings.set("lvl", 30)
        self.ctx.data_from, self.ctx.data_to = 0, 1
        await fy_worker.invoke(self.backend.migrate, self.ctx)
        self.assertEqual(self.sdk.settings.all(), {"level": 30})
        await fy_worker.invoke(self.backend.migrate, self.ctx)
        self.assertEqual(self.sdk.settings.all(), {"level": 30})
        self.assertEqual(await self.backend.call("get", None), {"enabled": True, "level": 30, "mode": "normal"})
        self.assertIn("migrated data 0 -> 1", self.log_text())

    async def test_migrate_keeps_a_newer_key(self):
        self.sdk.settings.update({"lvl": 30, "level": 80})
        await fy_worker.invoke(self.backend.migrate, self.ctx)
        self.assertEqual(self.sdk.settings.get("level"), 80)


class RealWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fyh-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        for name in ("fy_worker.py", "fy_proto.py", "fy_sdk.py", "fy_store.py"):
            shutil.copy(ROOT / name, self.root / name)
        self.wire = harness.Wire(self.root)
        self.addCleanup(self.wire.close)
        self.next = 0

    def req(self, op, **extra):
        self.next += 1
        return self.wire.request(dict(op=op, id=self.next, **extra))

    def startup(self, op="startup", **extra):
        self.next += 1
        m = harness.startup_message(self.next, "starter", STARTER / "backend.py", self.root)
        m["op"] = op
        m["meta"] = {"id": "starter", "version": "1.0.0", "plugin_dir": str(STARTER)}
        m.update(extra)
        return self.wire.request(m)

    def test_runs_end_to_end_in_the_real_worker(self):
        r = self.startup()
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(set(r["result"]["hooks"]), {"call", "startup", "unload", "migrate", "uninstall"})
        self.assertEqual(self.req("call", method="get", arg=None)["result"], {"enabled": True, "level": 50, "mode": "normal"})
        self.assertEqual(self.req("call", method="set", arg={"level": 85, "mode": "boost", "enabled": True})["result"], {"ok": True})
        self.assertEqual(self.req("call", method="get", arg=None)["result"]["level"], 85)
        self.assertIn("error", self.req("call", method="set", arg={"level": 500, "mode": "boost", "enabled": True})["result"])
        self.next += 1
        self.wire.send({"op": "call", "id": self.next, "method": "start", "arg": None})
        seen = []
        while True:
            m = self.wire.recv()
            self.assertIsNotNone(m)
            if m.get("ev") == "emit":
                seen.append((m["name"], m["payload"]))
                if m["name"] == "done":
                    break
        self.assertEqual(seen[-1], ("done", {"steps": 5}))
        self.assertEqual([p["step"] for n, p in seen if n == "progress"], [1, 2, 3, 4, 5])
        r = self.req("unload", reason="reload")
        self.assertTrue(r["ok"], r)
        log = (self.root / "logs" / "plugin.log").read_text()
        self.assertIn("started starter 1.0.0", log)
        self.assertIn("unloaded (reload)", log)

    def test_migration_before_startup_in_the_real_worker(self):
        config = self.root / "config"
        config.mkdir()
        (config / "settings.json").write_text(json.dumps({"lvl": 40}))
        r = self.startup("migrate", from_version="0.9.0", to_version="1.0.0", data_from=0, data_to=1)
        self.assertTrue(r["ok"], r)
        r = self.startup()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["result"]["migrated"])
        self.assertEqual(self.req("call", method="get", arg=None)["result"]["level"], 40)

    def test_uninstall_hook_runs(self):
        self.assertTrue(self.startup()["ok"])
        r = self.req("uninstall", purge=True)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["result"]["ran"])
        self.assertIn("uninstall, purge=True", (self.root / "logs" / "plugin.log").read_text())


class HygieneTests(unittest.TestCase):
    def test_backend_has_no_comments_docstrings_or_prints(self):
        src = (STARTER / "backend.py").read_text()
        self.assertNotIn(tokenize.COMMENT, {t.type for t in tokenize.generate_tokens(io.StringIO(src).readline)})
        tree = ast.parse(src, feature_version=(3, 9))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(ast.get_docstring(node))
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            if isinstance(node, ast.Name) and node.id in ("print", "open", "eval", "exec"):
                self.fail(node.id)
        self.assertEqual(names, {"asyncio", "framey_api"})

    def test_backend_uses_only_declared_sdk_members(self):
        declared = {n.id if isinstance(n, ast.Name) else n.name for n in ast.parse((ROOT / "types" / "framey_api.pyi").read_text()).body if isinstance(n, (ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in [getattr(n, "target", n)]}
        used = set(re.findall(r"\bfy\.(\w+)", (STARTER / "backend.py").read_text()))
        self.assertLessEqual(used, declared)
        self.assertGreaterEqual(used, {"settings", "log", "events", "fetch", "migrate", "runtime", "FrameyApiError", "PLUGIN", "API"})

    def test_no_comments_in_main_js(self):
        text = (STARTER / "main.js").read_text()
        self.assertIsNone(re.search(r"^\s*(//|/\*)", text, re.M))
        self.assertNotIn("innerHTML", text)


if __name__ == "__main__":
    unittest.main()
