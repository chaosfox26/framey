"""The root must authorize Steam data without granting raw debugger access."""

import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_framey():
    spec = importlib.util.spec_from_file_location("framey_steam_host", ROOT / "framey.py")
    fy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fy)
    return fy


class SteamHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fy = load_framey()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fy.PLUGINS = Path(temp.name)
        installed = self.fy.PLUGINS / "owner"
        installed.mkdir()
        (installed / "plugin.json").write_text('{"version":"1"}')
        (installed / "main.js").write_text("registerPlugin({})")
        self.manifests = [{"id": "owner", "version": "1"}]
        self.settings = {"disabled": [], "prefs": {}}
        self.contexts = (
            mock.patch.object(self.fy, "manifests", side_effect=lambda: self.manifests),
            mock.patch.object(self.fy, "settings", side_effect=lambda: self.settings),
            mock.patch.object(self.fy, "cdp_owner_ok", return_value=True),
        )
        for context in self.contexts:
            context.start()
            self.addCleanup(context.stop)

    def test_plugin_token_binds_steam_read_and_core_cannot_impersonate_it(self):
        token = self.fy.new_tok("owner")
        forged = {"pid": "foreign", "id": "foreign", "operation": "focus"}
        allowed, args = self.fy.authorize({"method": "steam_read", "t": token, "arg": forged})
        self.assertTrue(allowed)
        self.assertEqual(args, {"pid": "owner", "id": "owner", "operation": "focus"})
        self.assertEqual(forged["pid"], "foreign")
        for bad in (self.fy.CORE_TOK, "unknown", None):
            self.assertFalse(self.fy.authorize({"method": "steam_read", "t": bad})[0])
        self.fy.drop_tok("owner")
        self.assertFalse(self.fy.authorize({"method": "steam_read", "t": token})[0])

    def test_caller_identity_tracks_install_enable_manifest_and_token_generation(self):
        self.fy.new_tok("owner")
        self.assertTrue(callable(getattr(self.fy.host, "steam_caller", None)))
        first = self.fy.steam_caller("owner")
        self.assertIsNotNone(first)
        self.assertEqual(first, self.fy.steam_caller("owner"))
        self.fy.new_tok("owner")
        self.assertNotEqual(first, self.fy.steam_caller("owner"))
        second = self.fy.steam_caller("owner")
        self.manifests[0]["version"] = "2"
        self.assertNotEqual(second, self.fy.steam_caller("owner"))
        third = self.fy.steam_caller("owner")
        replacement = self.fy.PLUGINS / "replacement"
        replacement.mkdir()
        (replacement / "plugin.json").write_text('{"version":"1"}')
        (replacement / "main.js").write_text("registerPlugin({})")
        installed = self.fy.PLUGINS / "owner"
        installed.rename(self.fy.PLUGINS / "old")
        replacement.rename(installed)
        self.assertNotEqual(third, self.fy.steam_caller("owner"))
        self.settings["disabled"] = ["owner"]
        self.assertIsNone(self.fy.steam_caller("owner"))
        self.settings["disabled"] = []
        self.manifests.clear()
        self.assertIsNone(self.fy.steam_caller("owner"))

    def test_target_policy_requires_owned_steam_origin_and_excludes_injection_surfaces(self):
        allow = getattr(self.fy, "steam_target_allowed", None)
        self.assertTrue(callable(allow))
        tab = {"id": "steam-page", "type": "page", "url": "https://steamloopback.host/library"}
        self.assertIs(allow(tab), True)
        for url in (
            "http://steamloopback.host/library",
            "https://steamloopback.host:443/library",
            "https://steamloopback.host.evil/library",
            "https://user@steamloopback.host/library",
            "https://steamloopback.host/library\n",
            "https://example.org/library",
            "https://steamloopback.host/?vrOverlayKey=valve.steam.gamepadui.main",
            "https://steamloopback.host/?vrOverlayKey=valve.steam.gamepadui.bar",
            "https://steamloopback.host/?vrOverlayKey=framey.panel",
        ):
            with self.subTest(url=url):
                self.assertIs(allow({**tab, "url": url}), False)
        self.assertIs(allow({**tab, "type": "iframe"}), False)
        self.assertIs(allow({**tab, "id": None}), False)
        with mock.patch.object(self.fy, "cdp_owner_ok", return_value=False):
            self.assertIs(allow(tab), False)
        self.fy.LIVE["custom"] = SimpleNamespace(target_id="steam-page")
        self.assertIs(allow(tab), False)
        self.fy.LIVE.clear()
        self.settings["prefs"] = {"dev": {"enabled": True, "targets": {"custom": "/library"}}}
        self.assertIs(allow(tab), False)

    async def test_core_dispatches_only_bound_identity_to_service(self):
        async def read(host, args):
            return {"owner": args["pid"], "operation": args["operation"]}

        service = SimpleNamespace(METHODS={"steam_read": read})
        token = self.fy.new_tok("owner")
        allowed, args = self.fy.authorize(
            {"method": "steam_read", "t": token, "arg": {"operation": "focus", "pid": "other"}}
        )
        self.assertTrue(allowed)
        with mock.patch.object(
            self.fy, "use", side_effect=lambda n: service if n == "fy_steam" else None
        ):
            self.assertEqual(
                await self.fy.core("steam_read", args), {"owner": "owner", "operation": "focus"}
            )

    async def test_reload_and_all_frontend_reset_revoke_reads_synchronously(self):
        invalidated = []
        service = SimpleNamespace(invalidate=lambda host, pid=None: invalidated.append(pid))
        with mock.patch.dict(self.fy.MODS, {"fy_steam": service}, clear=True):
            self.fy.new_tok("owner")
            invalidated.clear()
            await self.fy.reload("owner")
            self.assertEqual(invalidated, ["owner"])
            invalidated.clear()
            with mock.patch.object(self.fy, "prelude", return_value=""):
                self.fy.main_code()
            self.assertIn(None, invalidated)

    async def test_down_drains_service_before_returning(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def shutdown(host):
            started.set()
            await finish.wait()

        with mock.patch.dict(
            self.fy.MODS, {"fy_steam": SimpleNamespace(shutdown=shutdown)}, clear=True
        ):
            down = asyncio.create_task(self.fy.down())
            try:
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertTrue(started.is_set(), "Steam service shutdown was not started")
                self.assertFalse(down.done(), "Framey returned while Steam reads were draining")
                finish.set()
                await down
            finally:
                finish.set()
                await asyncio.gather(down, return_exceptions=True)

    async def test_failed_surface_attach_stays_excluded_before_and_after_await(self):
        tab = {"id": "attaching", "type": "page", "url": "https://steamloopback.host/library"}
        self.assertTrue(self.fy.steam_target_allowed(tab))

        async def surface(*args):
            self.assertFalse(self.fy.steam_target_allowed(tab))
            await asyncio.sleep(0)
            raise OSError("synthetic attach failed")

        with mock.patch.object(self.fy, "_surface_session", surface):
            with self.assertRaises(OSError):
                await self.fy.session("custom", tab, lambda: [], "")
        self.assertFalse(self.fy.steam_target_allowed(tab))

    async def test_cancelled_root_connections_await_writer_release(self):
        for operation in (
            lambda: self.fy.WS.open("ws://127.0.0.1/devtools/page/test"),
            self.fy.tabs,
        ):
            with self.subTest(operation=operation):
                entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

                class Reader:
                    async def readuntil(self, delimiter):
                        entered.set()
                        await asyncio.Event().wait()

                class Writer:
                    reaped = False

                    def write(self, data):
                        pass

                    def close(self):
                        closing.set()

                    async def wait_closed(self):
                        await release.wait()
                        self.reaped = True

                writer = Writer()

                async def connect(*args):
                    return Reader(), writer

                with mock.patch.object(self.fy.asyncio, "open_connection", connect):
                    task = asyncio.create_task(operation())
                    try:
                        await asyncio.wait_for(entered.wait(), 1)
                        task.cancel()
                        await asyncio.wait_for(closing.wait(), 1)
                        await asyncio.sleep(0)
                        self.assertFalse(
                            task.done(), "Cancelled connection lost its writer before release"
                        )
                        task.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(
                            task.done(), "Repeated cancellation interrupted writer cleanup"
                        )
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                        self.assertTrue(writer.reaped)
                    finally:
                        release.set()
                        await asyncio.gather(task, return_exceptions=True)

    def test_port_owner_check_matches_the_actual_local_connection(self):
        # A listener on another port must neither authorize nor deny this one.
        import os

        rows = (
            "header\n"
            f"0: 0100007F:1F90 00000000:0000 0A 0 0 0 {os.getuid() + 1}\n"
            f"1: 0100007F:A431 00000000:0000 0A 0 0 0 {os.getuid()}\n"
        )
        with (
            mock.patch.object(self.fy, "CDP", ("127.0.0.1", 42033)),
            mock.patch("builtins.open", mock.mock_open(read_data=rows)),
        ):
            # The setup patches the public callback for all other host tests;
            # execute the real implementation here.
            real = load_framey()
            real.CDP = self.fy.CDP
            self.assertTrue(real.cdp_owner_ok())
            real.CDP = ("127.0.0.1", 42034)
            self.assertFalse(real.cdp_owner_ok())
            real.CDP = ("192.0.2.1", 42033)
            self.assertFalse(real.cdp_owner_ok())


if __name__ == "__main__":
    unittest.main()
