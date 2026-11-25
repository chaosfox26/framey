"""Owned module resources follow authenticated caller and frontend lifetimes."""

import asyncio
import importlib.util
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 64


def load_framey():
    spec = importlib.util.spec_from_file_location("framey_code_host", ROOT / "framey.py")
    fy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fy)
    return fy


class CodeHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fy = load_framey()

    def test_native_code_operations_bind_the_plugin_token_and_never_core_identity(self):
        # Isolate token binding/dispatch here; the verified enabled runtime path
        # is exercised below, and disabled admission has separate core tests.
        admission = mock.patch.object(self.fy, "enabled", return_value=True)
        admission.start()
        self.addCleanup(admission.stop)
        token = self.fy.new_tok("owner")
        for method in ("decky_code_issue", "decky_code_touch", "decky_code_revoke"):
            allowed, arg = self.fy.authorize(
                {
                    "method": method,
                    "t": token,
                    "arg": {"pid": "other", "id": "other", "token": TOKEN},
                }
            )
            self.assertTrue(allowed, method)
            self.assertEqual(arg, {"pid": "owner", "id": "owner", "token": TOKEN})
            self.assertFalse(self.fy.authorize({"method": method, "t": self.fy.CORE_TOK})[0])
            self.assertFalse(self.fy.authorize({"method": method, "t": "unknown"})[0])
        self.fy.drop_tok("owner")
        self.assertFalse(self.fy.authorize({"method": "decky_code_issue", "t": token})[0])

    async def test_core_routes_bound_arguments_to_the_owned_code_service(self):
        # Isolate token binding/dispatch here; the verified enabled runtime path
        # is exercised below, and disabled admission has separate core tests.
        admission = mock.patch.object(self.fy, "enabled", return_value=True)
        admission.start()
        self.addCleanup(admission.stop)
        async def issue(host, arg):
            return {"owner": arg["pid"]}

        service = SimpleNamespace(METHODS={"decky_code_issue": issue})
        allowed, arg = self.fy.authorize(
            {"method": "decky_code_issue", "t": self.fy.new_tok("owner"), "arg": {"pid": "other"}}
        )
        self.assertTrue(allowed)
        with mock.patch.object(
            self.fy, "use", side_effect=lambda name: service if name == "fy_decky_runtime" else None
        ):
            self.assertEqual(await self.fy.core("decky_code_issue", arg), {"owner": "owner"})

    def test_closing_session_accepts_only_narrow_authenticated_revoke_shape(self):
        valid = {
            "plugin": "_core",
            "id": 0,
            "method": "decky_code_revoke",
            "arg": {"pid": "owner", "token": TOKEN},
        }
        self.assertTrue(self.fy.cleanup_request(valid))
        for change in (
            {"method": "decky_code_issue"},
            {"method": "decky_code_touch"},
            {"id": 1},
            {"plugin": "owner"},
            {"arg": {"pid": "owner", "token": TOKEN, "path": "dist/index.js"}},
            {"arg": {"pid": "_core", "token": TOKEN}},
            {"arg": {"pid": "owner", "token": "bad"}},
        ):
            self.assertFalse(self.fy.cleanup_request({**valid, **change}), change)

    def test_token_and_frontend_retirement_notify_loaded_services(self):
        revoked = []
        service = SimpleNamespace(invalidate=lambda host, pid=None: revoked.append(pid))
        with mock.patch.dict(self.fy.MODS, {"fy_decky_runtime": service}, clear=True):
            self.fy.new_tok("owner")
            self.assertEqual(revoked, ["owner"])
            revoked.clear()
            with mock.patch.object(self.fy, "prelude", return_value=""):
                self.fy.main_code()
            self.assertEqual(revoked, [None])

    async def test_surface_replacement_retires_before_old_client_cleanup_await(self):
        revoked = []

        async def query(expression):
            self.assertEqual(revoked, [None], "Native grants outlived frontend retirement")

        old = SimpleNamespace(ui=True, accepting=True, core="core", query=query)
        self.fy.LIVE["main"] = old
        service = SimpleNamespace(invalidate=lambda host, pid=None: revoked.append(pid))
        with (
            mock.patch.dict(self.fy.MODS, {"fy_decky_runtime": service}, clear=True),
            mock.patch.object(self.fy, "start_surface_watches"),
        ):
            await self.fy.switch_panel_surface("none", {})
        self.assertEqual(revoked, [None])

    def test_retiring_an_old_session_cannot_revoke_replacement_grants(self):
        retire = getattr(self.fy, "retire_surface", None)
        self.assertTrue(callable(retire))
        revoked = []
        service = SimpleNamespace(invalidate=lambda host, pid=None: revoked.append(pid))
        old, current = SimpleNamespace(ui=True), SimpleNamespace(ui=True)
        self.fy.LIVE["main"] = current
        with mock.patch.dict(self.fy.MODS, {"fy_decky_runtime": service}, clear=True):
            self.assertFalse(retire("main", old))
            self.assertEqual(revoked, [])
            self.assertTrue(retire("main", current))
            self.assertEqual(revoked, [None])
        self.assertNotIn("main", self.fy.LIVE)

    async def test_down_waits_for_the_native_code_service(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def shutdown(host):
            started.set()
            await release.wait()

        with mock.patch.dict(
            self.fy.MODS, {"fy_decky_runtime": SimpleNamespace(shutdown=shutdown)}, clear=True
        ):
            work = asyncio.create_task(self.fy.down())
            try:
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertTrue(started.is_set())
                self.assertFalse(work.done())
                release.set()
                await work
            finally:
                release.set()
                await asyncio.gather(work, return_exceptions=True)

    async def test_real_import_and_native_issuer_accept_a_fresh_root_generation(self):
        import fy_decky_runtime
        from optional_backend_fixture import install_runtime
        from decky_runtime import importer as fy_decky_import
        archive = io.BytesIO()
        files = {
            "plugin.json": json.dumps({"name": "Owned graph", "api_version": 2, "flags": []}),
            "package.json": json.dumps({"type": "module", "version": "1.0.0"}),
            "dist/index.js": 'import {n} from "./chunk.js";export default ()=>({title:String(n)});',
            "dist/chunk.js": "export const n=42;",
        }
        with zipfile.ZipFile(archive, "w") as writer:
            for name, value in files.items():
                writer.writestr(name, value)
        with tempfile.TemporaryDirectory() as folder:
            self.fy.PLUGINS = Path(folder) / "plugins"
            self.fy.PLUGINS.mkdir()
            self.fy.HOME = Path(folder) / "home"
            self.fy.CONF = self.fy.HOME / ".config/framey/settings.json"
            package = ROOT / "decky_runtime"
            payload = {path.relative_to(package).as_posix(): path.read_bytes()
                       for path in package.rglob("*") if path.is_file()
                       and "__pycache__" not in path.parts and path.name != "runtime-version.json"}
            install_runtime(self.fy.HOME, payload)
            raw = archive.getvalue()
            fy_decky_import.import_plugin(raw, hashlib.sha256(raw).hexdigest(), self.fy.PLUGINS / "owner", "owner")
            with (
                mock.patch.dict(self.fy.MODS, {"fy_decky_runtime": fy_decky_runtime}, clear=True),
                mock.patch.object(self.fy, "settings", return_value={"disabled": [], "prefs": {}}),
            ):
                try:
                    enabled = await fy_decky_runtime.resume(self.fy.host)
                    self.assertTrue(enabled["enabled"], enabled)
                    token = self.fy.new_tok("owner")
                    allowed, args = self.fy.authorize({"method": "decky_code_issue", "t": token, "arg": {}})
                    self.assertTrue(allowed)
                    first = await self.fy.core("decky_code_issue", args)
                    self.assertIn("root", first, first)
                    self.fy.invalidate_callers()
                    fresh = await self.fy.core("decky_code_issue", args)
                    self.assertIn("root", fresh, fresh)
                    self.assertNotEqual(first["token"], fresh["token"])
                finally:
                    await fy_decky_runtime.shutdown(self.fy.host)


if __name__ == "__main__":
    unittest.main()
