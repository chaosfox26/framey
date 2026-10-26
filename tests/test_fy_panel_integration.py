import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "fixtures" / "j"), str(HERE.parent)]

import jharness as H


class SurfacePage(H.PluginPage):
    def __init__(self):
        super().__init__()
        self.bindings_received = 0
        self.tab = "modules"

    async def recv(self):
        raw = await super().recv()
        if isinstance(raw, str) and json.loads(raw).get("method") == "Runtime.bindingCalled":
            self.bindings_received += 1
        return raw

    async def send(self, obj):
        await super().send(obj)
        params = obj["params"]
        if obj["method"] == "Runtime.callFunctionOn":
            declaration = params.get("functionDeclaration", "")
            if declaration == "function(){return {tab:this.tab()}}":
                self.reply(obj, {"value": {"tab": self.tab}})
            elif declaration == "function(){this.dispose(null);return true}":
                self.reply(obj, {"value": True})


class HeldSourcePage(SurfacePage):
    def __init__(self):
        super().__init__()
        self.sources = []

    async def send(self, obj):
        if obj["method"] == "Runtime.evaluate" and obj["params"].get("expression", "").startswith("(function(Framey){"):
            self.sent.append(obj)
            self.sources.append(obj)
            return
        await super().send(obj)


class PanelIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loader = H.Loader()
        self.addAsyncCleanup(self.loader.close)
        self.fy = self.loader.fy
        self.directory = self.loader.plugin("sample", {"api": 1}, main="window.PANEL_PLUGIN=1")
        self.pages = {}
        self.sessions = []
        self.inventory = []

        async def open_page(cls, endpoint):
            return self.pages[endpoint.rsplit("/", 1)[-1]]

        async def tabs():
            return self.inventory[:]

        self.fy.WS.open = classmethod(open_page)
        self.fy.tabs = tabs
        process = mock.patch("asyncio.create_subprocess_exec", new_callable=mock.AsyncMock)
        self.process = process.start()
        self.process.side_effect = AssertionError("unexpected native/process launch")
        self.addCleanup(process.stop)
        network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("unexpected external network"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.addAsyncCleanup(self.close_sessions)

    async def close_sessions(self):
        self.fy.stopping = True
        for task in self.sessions + list(self.fy.TASKS):
            task.cancel()
        await asyncio.gather(*self.sessions, *list(self.fy.TASKS), return_exceptions=True)
        self.process.assert_not_awaited()
        self.network.assert_not_called()
        self.assertEqual(self.loader.net.calls, [])

    async def settled(self, condition):
        self.assertTrue(await H.settle(condition, limit=2))

    async def attach(self, name, page=None, ready=True):
        page = page or SurfacePage()
        self.pages[name] = page
        key = "framey.panel" if name == "panel" else "valve.steam.gamepadui.main"
        url = "about:blank?vrOverlayKey=framey.panel" if name == "panel" else "https://steamloopback.host/?vrOverlayKey=" + key
        tab = {"id": name, "url": url, "webSocketDebuggerUrl": "ws://fixture/" + name}
        self.inventory.append(tab)
        page.fy = self.fy
        probe = "typeof window.Framey" if name == self.fy.ui_name() else "typeof window.__framey_targets"
        marker = self.fy.panel_overlay_url if name == "panel" else lambda url: self.fy.overlay_key(url, key)
        task = asyncio.create_task(self.fy.session(name, tab, lambda: self.fy.main_code(name), probe, marker))
        self.sessions.append(task)
        await self.settled(lambda: name in self.fy.LIVE and bool(page.exprs()))
        if ready and name == self.fy.ui_name():
            await self.settled(lambda: self.fy.LIVE[name].ready)
        return page

    async def test_non_ui_main_code_keeps_core_token_and_plugin_frontend_untouched(self):
        self.fy.UI_SURFACE = "panel"
        token = self.fy.CORE_TOK
        plugin_token = self.fy.new_tok("sample")
        code = self.fy.main_code("main")
        self.assertEqual(self.fy.CORE_TOK, token)
        self.assertEqual(self.fy.TOKS, {plugin_token: "sample"})
        self.assertFalse(any(pid == "#core" or isinstance(expression, tuple) for expression, pid in code))
        self.assertTrue(any("__framey_targets" in expression for expression, _ in code))
        self.assertFalse(any("PANEL_PLUGIN" in expression for expression, _ in code))

    async def test_native_panel_registers_one_frontend_before_deferred_restore(self):
        self.fy.UI_SURFACE = "panel"
        page = SurfacePage()
        page.hold_runs = True
        await self.attach("panel", page, ready=False)
        await self.settled(lambda: len(page.runs) == 1)
        self.assertEqual(sum(expression.startswith("(function(__fyt){") for expression in page.exprs()), 1)
        self.assertEqual(sum(expression.startswith("(function(Framey){") for expression in page.exprs()), 1)
        self.assertEqual(sum(entry[0] == "bind" for entry in page.trace() if isinstance(entry, tuple)), 1)
        self.assertNotIn("Framey.restore()", page.exprs())
        self.assertFalse(self.fy.LIVE["panel"].ready)
        page.reply(page.runs[0])
        await self.settled(lambda: self.fy.LIVE["panel"].ready)
        self.assertEqual(page.exprs().count("Framey.restore()"), 1)
        run_index = page.sent.index(page.runs[0])
        restore_index = next(i for i, obj in enumerate(page.sent) if obj["params"].get("expression") == "Framey.restore()")
        self.assertLess(run_index, restore_index)

    async def test_ui_events_and_frontend_reload_use_panel_while_target_main_stays_main(self):
        self.fy.UI_SURFACE = "panel"
        ui = await self.attach("panel")
        steam = await self.attach("main")
        self.assertIs(self.fy.ui_sender(), self.fy.LIVE["panel"])
        ui_before, steam_before = len(ui.sent), len(steam.sent)
        await self.fy.notify_page("_core", "notice", {"body": "panel"})
        await self.fy.emit_all([{"plugin": "sample", "name": "live", "payload": 1, "dropped": 0}])
        await self.fy.page_reload("sample", redraw=False)
        await self.settled(lambda: len(ui.runs) == 2)
        await self.fy.send_main("main", "window.CANONICAL_TARGET=1")
        self.assertEqual([event[:2] for event in ui.events()], [["_core", "notice"], ["sample", "live"]])
        self.assertEqual(steam.events(), [])
        self.assertTrue(any(entry[0] == "dispose" and entry[1] == ["sample"] for entry in ui.trace(ui_before) if isinstance(entry, tuple)))
        self.assertTrue(any("PANEL_PLUGIN" in expression for expression in ui.exprs()))
        self.assertFalse(any("PANEL_PLUGIN" in expression for expression in steam.exprs()))
        self.assertEqual(steam.exprs()[-1], "window.CANONICAL_TARGET=1")
        self.assertFalse(any(obj["params"].get("expression") == "window.CANONICAL_TARGET=1" for obj in ui.sent[ui_before:]))
        self.assertGreater(len(steam.sent), steam_before)

    async def test_non_ui_main_denies_core_and_backend_but_keeps_scoped_target_authority(self):
        self.fy.UI_SURFACE = "panel"
        await self.attach("panel")
        steam = await self.attach("main")
        token = next(token for token, pid in self.fy.TOKS.items() if pid == "sample")
        core = mock.AsyncMock(return_value={"ok": True})
        backend = mock.AsyncMock(return_value="must not run")
        with mock.patch.object(self.fy, "core", core), mock.patch.object(self.fy, "backend", backend):
            steam.bind(id=1, gen="non-ui", plugin="_core", method="list", t=self.fy.CORE_TOK)
            steam.bind(id=2, gen="non-ui", plugin="sample", method="work", t=token)
            steam.bind(id=3, gen="target", plugin="_core", method="target_add", arg={"pid": "other", "id": "other", "target": "main", "kind": "css", "key": "accent", "code": "body{}"}, t=token)
            await self.settled(lambda: steam.bindings_received == 3 and not self.fy.TASKS)
        backend.assert_not_awaited()
        state = self.fy.use("fy_state")
        self.assertNotIn("panel_mode", state.PREF_F)
        persisted, _ = state.validate({"prefs": {"panel_mode": "floating"}})
        self.assertEqual(persisted["prefs"]["panel_mode"], "floating")
        refused = await self.fy.core("prefs_set", {"panel_mode": "floating"})
        self.assertEqual(refused.get("code"), "bad_key")
        self.assertEqual(self.fy.settings()["prefs"]["panel_mode"], "embedded")
        core.assert_awaited_once_with("target_add", {"pid": "sample", "id": "sample", "target": "main", "kind": "css", "key": "accent", "code": "body{}"})

    async def test_retired_pending_source_chain_cannot_bind_run_or_restore(self):
        self.fy.UI_SURFACE = "panel"
        page = HeldSourcePage()
        await self.attach("panel", page, ready=False)
        await self.settled(lambda: len(page.sources) == 1)
        self.fy.LIVE["panel"].accepting = False
        page.reply(page.sources[0], {"objectId": "retired-source"})
        await self.settled(lambda: any(obj["method"] == "Runtime.releaseObject" for obj in page.sent))
        self.assertEqual(page.runs, [])
        self.assertFalse(any(entry[0] == "bind" for entry in page.trace() if isinstance(entry, tuple)))
        self.assertNotIn("Framey.restore()", page.exprs())
        self.assertFalse(self.fy.LIVE["panel"].ready)

    async def test_panel_is_reserved_from_developer_and_public_target_names(self):
        self.loader.trust(**{"dev.enabled": True, "dev.targets": {"other": "allowed-overlay"}})
        target_module = self.fy.use("fy_targets")
        targets = target_module.get(self.fy.host)
        self.assertIn("other", targets.names())
        self.loader.trust(**{"dev.targets": {"panel": "reserved-overlay", "other": "allowed-overlay"}})
        self.assertNotIn("panel", targets.names())
        self.assertNotIn("panel", targets.dev_table())
        result = await self.fy.core("target_add", {"pid": "sample", "target": "panel", "kind": "css", "key": "x", "code": "body{}"})
        self.assertEqual(result.get("code"), "unknown_target")
        self.assertEqual(self.fy.WATCH, {})

    async def test_panel_state_transfer_uses_validated_inert_json(self):
        self.fy.UI_SURFACE = "panel"
        self.fy.PANEL = True
        sender = types.SimpleNamespace(core="core", query=mock.AsyncMock(return_value={"tab": "#modules:sample-1"}))
        self.fy.LIVE["panel"] = sender
        state = await self.fy.capture_panel_state()
        self.assertEqual(state, {"open": True, "tab": "#modules:sample-1"})
        self.fy.UI_RESTORE = state
        expression = self.fy.main_code("panel")[0][0]
        self.assertIn('sessionStorage.fyOpen="1";', expression)
        self.assertIn('sessionStorage.fyTab="#modules:sample-1";', expression)
        malicious = "');window.TRANSFER_EXECUTED=1;//"
        sender.query.return_value = {"tab": malicious}
        self.assertEqual(await self.fy.capture_panel_state(), {"open": True, "tab": None})
        self.fy.UI_RESTORE = {"open": "true", "tab": malicious}
        expression = self.fy.main_code("panel")[0][0]
        self.assertNotIn("TRANSFER_EXECUTED", expression)
        self.assertNotIn("sessionStorage.fyTab=", expression)
        self.assertNotIn("sessionStorage.fyOpen=", expression)
        self.fy.LIVE.clear()

    async def test_panel_mode_rpc_reply_is_sent_before_apply_pending(self):
        page = await self.attach("main")
        applied = asyncio.Event()
        replies_at_apply = []

        async def apply_pending():
            replies_at_apply.extend(page.replies())
            applied.set()

        controller = types.SimpleNamespace(status=mock.Mock(return_value={"pending": False}), request=mock.AsyncMock(return_value={"mode": "embedded", "pending": True, "requested_mode": "floating"}), apply_pending=apply_pending, close=mock.AsyncMock(), panel_changed=mock.AsyncMock())
        self.fy.host.panel_controller = controller
        page.bind(id=12, gen="switch", plugin="_core", method="panel_mode", arg={"mode": "floating"})
        await self.settled(lambda: bool(page.replies()))
        self.assertEqual(page.replies()[0][2], {"mode": "embedded", "pending": True, "requested_mode": "floating"})
        await asyncio.wait_for(applied.wait(), 2)
        self.assertEqual(replies_at_apply, [["switch", 12, {"mode": "embedded", "pending": True, "requested_mode": "floating"}]])
        controller.request.assert_awaited_once_with({"mode": "floating"})

    async def test_panel_marker_accepts_only_bounded_blank_owned_overlay_urls(self):
        valid = "about:blank?vrOverlayKey=framey.panel"
        for url in (valid, "about:blank#?vrOverlayKey=framey.panel", valid + "&padding=" + "x" * (4096 - len(valid + "&padding="))):
            with self.subTest(url=url[:100]):
                self.assertTrue(self.fy.panel_overlay_url(url))
        for url in (None, 7, "", "about:blank", "about:blank?vrOverlayKey=other", "about:notblank?vrOverlayKey=framey.panel", "about://attacker/blank?vrOverlayKey=framey.panel", "https://steamloopback.host/?vrOverlayKey=framey.panel", "https://attacker.invalid/?vrOverlayKey=framey.panel", "file:///blank?vrOverlayKey=framey.panel", valid + "-other", valid + "&vrOverlayKey=framey.panel", valid + "&padding=" + "x" * 4096):
            with self.subTest(url=str(url)[:100]):
                self.assertFalse(self.fy.panel_overlay_url(url))

    async def test_ambiguous_panel_watch_does_not_attach_either_candidate(self):
        class WatchIterationFinished(Exception):
            pass

        tabs = mock.AsyncMock(return_value=[
            {"id": "first", "url": "about:blank?vrOverlayKey=framey.panel", "webSocketDebuggerUrl": "ws://fixture/first"},
            {"id": "second", "url": "about:blank?vrOverlayKey=framey.panel", "webSocketDebuggerUrl": "ws://fixture/second"},
        ])
        session = mock.AsyncMock()
        sleep = mock.AsyncMock(side_effect=WatchIterationFinished)
        with mock.patch.object(self.fy, "tabs", tabs), mock.patch.object(self.fy, "session", session), mock.patch.object(self.fy.asyncio, "sleep", sleep):
            with self.assertRaises(WatchIterationFinished):
                await self.fy.watch("panel", self.fy.panel_overlay_url, lambda: [], "typeof window.Framey")
        tabs.assert_awaited_once_with()
        session.assert_not_awaited()
        sleep.assert_awaited_once_with(5)

    async def test_native_session_eof_finishes_before_controller_close_and_ignores_replacement(self):
        self.fy.UI_SURFACE = "panel"
        self.fy.PANEL = True
        entered, resume = asyncio.Event(), asyncio.Event()
        callbacks = []

        async def panel_changed(opened):
            callbacks.append((opened, asyncio.current_task()))
            entered.set()
            await resume.wait()

        self.fy.host.panel_controller = types.SimpleNamespace(mode="floating", panel_changed=panel_changed, close=mock.AsyncMock())
        page = await self.attach("panel")
        reader = self.sessions[-1]
        page.q.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(reader), 1)
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(reader.done())
            self.assertFalse(reader.cancelled())
            self.assertEqual(callbacks[0][0], False)
            self.assertIsNot(callbacks[0][1], reader)
            self.assertFalse(resume.is_set())
        finally:
            resume.set()
        await asyncio.wait_for(callbacks[0][1], 1)
        self.assertFalse(self.fy.PANEL)

        self.fy.PANEL = True
        replacement = types.SimpleNamespace(ui=True, accepting=True, core="replacement")
        self.fy.LIVE["panel"] = replacement
        await self.fy.panel_session_lost("panel")
        self.assertTrue(self.fy.PANEL)
        self.assertIs(self.fy.LIVE["panel"], replacement)
        self.assertEqual(len(callbacks), 1)
        self.fy.LIVE.clear()


if __name__ == "__main__":
    unittest.main()
