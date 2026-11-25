import asyncio
import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_panel as panel


class FakeNative:
    def __init__(self, trace, available=True):
        self.trace = trace
        self.available = available
        self.last_error = None
        self.own_visible_value = False
        self.dashboard_visible_value = True
        self.interactive_value = False
        self.closed = False
        self.fail = None
        self.ignore_show = False
        self.ignore_hide = False
        self.visibility_sequence = []

    def result(self, name, *args):
        self.trace.append(("native", name, *args))
        if self.fail == name:
            self.last_error = "/home/private-user/token=secret"
            return {"ok": False, "error": self.last_error}
        return {"ok": True, "error": None}

    def probe(self):
        self.trace.append(("native", "probe"))
        return {"available": self.available, "error": None if self.available else "/home/private-user/token=secret"}

    def find_own(self):
        self.trace.append(("native", "find_own"))
        return 101 if self.available else None

    def find_steam(self):
        self.trace.append(("native", "find_steam"))
        return 202 if self.available else None

    def position_right(self, key=None):
        return self.result("position_right", *(() if key is None else (key,)))

    def position_above_bar(self, anchor_x=0.5, width=382):
        return self.result("position_above_bar", anchor_x, *(() if width == 382 else (width,)))

    def steam_visible(self):
        return True

    def show_own(self):
        result = self.result("show_own")
        if result["ok"] and not self.ignore_show:
            self.own_visible_value = True
        return result

    def hide_own(self):
        result = self.result("hide_own")
        if result["ok"] and not self.ignore_hide:
            self.own_visible_value = False
        return result

    def interactive(self, enabled):
        result = self.result("interactive", enabled)
        if result["ok"]:
            self.interactive_value = enabled
        return result

    def own_visible(self):
        self.trace.append(("native", "own_visible"))
        if self.visibility_sequence:
            self.own_visible_value = self.visibility_sequence.pop(0)
        return self.own_visible_value

    def dashboard_visible(self):
        self.trace.append(("native", "dashboard_visible"))
        return self.dashboard_visible_value

    def close(self):
        self.trace.append(("native", "close"))
        self.closed = True
        self.available = False


class FakeBrowser:
    def __init__(self, trace):
        self.trace = trace
        self.failure = None
        self.closed = False
        self.block_name = None
        self.entered, self.resume = asyncio.Event(), asyncio.Event()
        self.target = {"ok": False, "code": "app_target_unavailable"}

    async def active_target(self):
        return self.target

    async def operation(self, name):
        self.trace.append(("browser", name))
        if self.block_name == name:
            self.entered.set()
            await self.resume.wait()
        if self.failure == name:
            return {"ok": False, "error": "/home/private-user/token=secret", "code": "private-message"}
        return {"ok": True, "available": True}

    async def probe(self):
        return await self.operation("probe")

    async def ensure(self, layout="window", width=382):
        self.layout = layout
        self.width = width
        return await self.operation("ensure")

    async def show(self):
        return await self.operation("show")

    async def hide(self):
        return await self.operation("hide")

    async def close(self):
        self.closed = True
        return await self.operation("close")


class FakeHost:
    def __init__(self):
        self.PANEL = True
        self.trace = []
        self.config = {"prefs": {"panel_mode": "embedded"}}
        self.saved = []
        self.events = []
        self.surface = "main"
        self.switch_entered = asyncio.Event()
        self.switch_resume = None
        self.switch_failure = False
        self.menu_width = 382

    def settings(self):
        return copy.deepcopy(self.config)

    def save(self, value):
        self.trace.append(("host", "save", value["prefs"]["panel_mode"]))
        self.saved.append(copy.deepcopy(value))
        self.config = copy.deepcopy(value)

    async def capture_panel_state(self):
        self.trace.append(("host", "capture"))
        return {"open": self.PANEL, "tab": "modules"}

    async def switch_panel_surface(self, name, state):
        self.trace.append(("host", "switch", name, copy.deepcopy(state)))
        self.switch_entered.set()
        if self.switch_resume is not None:
            await self.switch_resume.wait()
        if self.switch_failure and name == "panel":
            raise OSError("/home/private-user/token=secret")
        self.surface = name
        return {"ok": True}

    async def ui_panel_visibility(self, on):
        self.trace.append(("host", "visibility", on))
        self.ui_visible = on

    async def notify_page(self, plugin, name, payload):
        self.events.append((plugin, name, copy.deepcopy(payload)))

    def panel_route_allowed(self, route):
        return route in ("#discover", "#decky-store", "#settings", "fan")

    async def panel(self, opened):
        self.trace.append(("host", "panel", opened))
        self.PANEL = opened
        await self.panel_controller.panel_changed(opened)

    async def ui_panel_route(self, route):
        self.trace.append(("host", "route", route))

    async def launcher_width(self):
        return self.menu_width


class PanelControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.host = FakeHost()
        self.natives, self.browsers = [], []
        self.next_native_available = True
        self.next_target = {"ok": False, "code": "app_target_unavailable"}

        def native_factory(enabled=False):
            self.assertTrue(enabled)
            adapter = FakeNative(self.host.trace, self.next_native_available)
            self.natives.append(adapter)
            return adapter

        def browser_factory(host):
            self.assertIs(host, self.host)
            browser = FakeBrowser(self.host.trace)
            browser.target = self.next_target
            self.browsers.append(browser)
            return browser

        async def native_runner(fn, *args):
            return fn(*args)

        self.native_factory = native_factory
        self.browser_factory = browser_factory
        self.native_runner = native_runner
        self.controller = panel.Controller(self.host, native_factory=native_factory, browser_factory=browser_factory, native_runner=native_runner)
        self.host.panel_controller = self.controller
        self.addAsyncCleanup(self.controller.close)

    async def select(self, mode):
        result = await self.controller.request(mode)
        self.assertFalse(result.get("error"), result)
        return await self.controller.apply_pending()

    async def until(self, condition, timeout=1):
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.005)

    def calls(self, actor, operation):
        return [entry for entry in self.host.trace if entry[:2] == (actor, operation)]

    async def test_embedded_status_and_default_start_are_lazy(self):
        for _ in range(3):
            status = self.controller.status()
            self.assertEqual(status["mode"], "embedded")
            self.assertIs(status["active"], self.host.PANEL)
            self.assertIs(status["visible"], self.host.PANEL)
            self.assertIs(status["available"], True)
            self.assertIs(status["supported"], True)
        await self.controller.start()
        await asyncio.sleep(0)
        self.assertEqual(self.natives, [])
        self.assertEqual(self.browsers, [])
        self.assertEqual(self.host.trace, [])
        self.assertEqual(self.host.saved, [])

    async def test_compact_creates_a_small_surface_and_positions_it_above_the_bar(self):
        result = await self.select("compact")
        self.assertEqual(result["mode"], "compact")
        self.assertEqual(result["layout"], "compact")
        self.assertEqual(self.browsers[-1].layout, "compact")
        self.assertTrue(self.calls("native", "position_above_bar"))
        self.assertFalse(self.calls("native", "position_right"))
        self.assertEqual(self.calls("host", "switch")[-1][3]["layout"], "compact")
        self.assertEqual(self.host.config["prefs"]["panel_mode"], "compact")

    async def test_compact_realigns_to_the_bar_and_hides_when_the_dashboard_closes(self):
        await self.select("compact")
        native = self.natives[-1]
        self.host.trace.clear()
        self.controller._last_align = 0
        await self.controller._sync_native()
        self.assertTrue(self.calls("native", "position_above_bar"))
        native.dashboard_visible_value = False
        await self.controller._sync_native()
        self.assertFalse(self.controller.visible)
        self.assertFalse(native.interactive_value)

    async def test_reopening_compact_positions_before_showing_even_inside_alignment_interval(self):
        await self.select("compact")
        self.host.PANEL = False
        await self.controller.panel_changed(False)
        self.host.trace.clear()
        self.host.PANEL = True
        await self.controller.panel_changed(True)
        positions = self.calls("native", "position_above_bar")
        self.assertTrue(positions, "A newly visible panel must use the current bar position")
        self.assertLess(self.host.trace.index(positions[0]), self.host.trace.index(self.calls("browser", "show")[0]))

    async def test_presentation_change_recreates_owned_popup_and_keeps_selected_route(self):
        await self.select("dashboard")
        old_native, old_browser = self.natives[-1], self.browsers[-1]
        result = await self.select("compact")
        self.assertEqual(result["mode"], "compact")
        self.assertTrue(old_native.closed)
        self.assertTrue(old_browser.closed)
        self.assertEqual(len(self.browsers), 2)
        self.assertEqual(self.browsers[-1].layout, "compact")
        self.assertEqual(self.calls("host", "switch")[-1][3]["tab"], "modules")
        self.assertEqual(self.host.surface, "panel")

    async def test_close_during_recreation_never_creates_another_native_adapter(self):
        await self.select("dashboard")
        await self.controller.request("compact")
        self.host.switch_entered.clear()
        self.host.switch_resume = asyncio.Event()
        applying = asyncio.create_task(self.controller.apply_pending())
        try:
            await asyncio.wait_for(self.host.switch_entered.wait(), 1)
            await self.controller.close()
        finally:
            self.host.switch_resume.set()
        await asyncio.wait_for(applying, 1)
        self.assertEqual(len(self.natives), 1)
        self.assertEqual(len(self.browsers), 1)

    async def test_failed_saved_compact_restoration_preserves_requested_preference(self):
        self.host.config["prefs"]["panel_mode"] = "compact"
        self.host.switch_failure = True
        result = await self.controller.start()
        self.assertEqual(result["mode"], "embedded")
        self.assertEqual(self.host.config["prefs"]["panel_mode"], "compact")
        self.assertTrue(result.get("error"))

    async def test_window_mode_needs_a_verified_app_target_without_native_side_effects(self):
        result = await self.controller.request("window")
        self.assertEqual(result.get("code"), "target_unavailable")
        self.assertFalse(result["pending"])
        self.assertEqual(result["mode"], "embedded")
        self.assertFalse(self.calls("browser", "ensure"))
        self.assertFalse(self.calls("host", "switch"))

    async def test_window_mode_positions_beside_visible_steam_when_no_application_is_focused(self):
        self.next_target = {"ok": True, "target": "steam"}
        result = await self.select("window")
        self.assertEqual(result["mode"], "window")
        self.assertIn(("native", "position_right", b"valve.steam.gamepadui.main"), self.host.trace)
        self.assertEqual(self.browsers[0].layout, "window")

    async def test_launcher_toggle_uses_the_icon_anchor_and_never_opens_inside_steam(self):
        self.host.PANEL = False
        result = await self.controller.toggle_launcher(0.75)
        self.assertEqual(result["mode"], "compact")
        self.assertIn(("native", "position_above_bar", 0.75), self.host.trace)
        self.assertEqual(self.calls("host", "switch")[-1][3]["tab"], "#launcher")
        self.assertEqual(self.calls("host", "route")[-1][2], "#launcher")
        self.assertEqual(self.host.surface, "panel")

    async def test_launcher_rejects_invalid_anchor_before_any_action(self):
        for value in (True, -1, 2, "0.5", float("nan"), None):
            result = await self.controller.toggle_launcher(value)
            self.assertEqual(result.get("code"), "bad_anchor")
        self.assertEqual(self.natives, [])
        self.assertEqual(self.host.trace, [])

    async def test_launcher_size_follows_the_frontend_plugin_shortcut_count(self):
        self.host.menu_width = 662
        self.host.PANEL = False
        await self.controller.toggle_launcher(0.75)
        self.assertEqual(self.browsers[-1].width, 662)
        self.assertIn(("native", "position_above_bar", 0.75, 662), self.host.trace)

    async def test_launcher_click_during_pending_handoff_does_not_change_its_open_state(self):
        await self.controller.request("dashboard")
        before = copy.deepcopy(self.controller._pending)
        trace = self.host.trace[:]
        result = await self.controller.toggle_launcher(0.75)
        self.assertEqual(result.get("code"), "busy")
        self.assertEqual(self.controller._pending, before)
        self.assertEqual(self.host.trace, trace)

    async def test_launcher_reserves_transition_while_reading_its_width(self):
        entered, resume = asyncio.Event(), asyncio.Event()

        async def width():
            entered.set()
            await resume.wait()
            return 382

        self.host.launcher_width = width
        opening = asyncio.create_task(self.controller.toggle_launcher(0.75))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(self.controller.status()["requested_mode"], "compact")
            blocked = await self.controller.request("dashboard")
            self.assertEqual(blocked.get("code"), "busy")
        finally:
            resume.set()
        result = await asyncio.wait_for(opening, 1)
        self.assertEqual(result["mode"], "compact")
        self.assertTrue(result["open"])

    async def test_shutdown_during_launcher_width_read_never_opens_a_surface(self):
        entered, resume = asyncio.Event(), asyncio.Event()

        async def width():
            entered.set()
            await resume.wait()
            return 382

        self.host.launcher_width = width
        opening = asyncio.create_task(self.controller.toggle_launcher(0.75))
        await asyncio.wait_for(entered.wait(), 1)
        await self.controller.close()
        trace = self.host.trace[:]
        resume.set()
        result = await asyncio.wait_for(opening, 1)
        self.assertEqual(result.get("code"), "closed")
        self.assertEqual(self.host.trace, trace)
        self.assertEqual(self.natives, [])

    async def test_closed_launcher_is_recreated_before_changing_its_native_width(self):
        await self.controller.toggle_launcher(0.75)
        old_browser = self.browsers[0]
        await self.controller.toggle_launcher(0.75)
        self.host.menu_width = 662
        result = await self.controller.toggle_launcher(0.75)
        self.assertEqual(result["mode"], "compact")
        self.assertEqual(len(self.browsers), 2)
        self.assertTrue(old_browser.closed)
        self.assertEqual(self.browsers[-1].width, 662)
        self.assertIn(("native", "position_above_bar", 0.75, 662), self.host.trace)

    async def test_launch_prepares_selected_route_and_acknowledges_before_handoff(self):
        self.next_target = {"ok": True, "target": "steam"}
        await self.controller.toggle_launcher(0.5)
        previous = self.calls("host", "switch")[:]
        result = await self.controller.launch("fan")
        self.assertIs(result["ok"], True)
        self.assertIs(result["pending"], True)
        self.assertEqual(self.calls("host", "switch"), previous)
        await self.controller.apply_pending()
        self.assertEqual(self.controller.mode, "window")
        self.assertEqual(self.calls("host", "switch")[-1][3]["tab"], "fan")
        self.assertEqual(self.host.surface, "panel")
        self.assertFalse(any(entry[2] == "main" for entry in self.calls("host", "switch")))

    async def test_launcher_handoff_keeps_verified_steam_when_its_visibility_flag_changes(self):
        self.next_target = {"ok": True, "target": "steam"}
        await self.controller.toggle_launcher(0.5)
        with mock.patch.object(FakeNative, "steam_visible", return_value=False):
            result = await self.controller.launch("fan")
            self.assertIs(result.get("ok"), True, result)
            result = await self.controller.apply_pending()
        self.assertEqual(result["mode"], "window")
        self.assertEqual(self.host.surface, "panel")
        self.assertEqual(self.calls("host", "switch")[-1][3]["tab"], "fan")
        self.assertIn(("native", "position_right", b"valve.steam.gamepadui.main"), self.host.trace)

    async def test_launcher_handoff_failure_keeps_the_main_steam_document_closed(self):
        self.next_target = {"ok": True, "target": "steam"}
        await self.controller.toggle_launcher(0.5)
        await self.controller.launch("#discover")
        self.host.switch_failure = True
        result = await self.controller.apply_pending()
        self.assertEqual(result["mode"], "embedded")
        self.assertFalse(result["open"])
        self.assertFalse(self.calls("host", "switch")[-1][3]["open"])

    async def test_launch_rejects_nonexistent_routes_without_surface_changes(self):
        for route in ("#not-real", "other", "');bad()//", None, {"route": "fan"}):
            result = await self.controller.launch(route)
            self.assertEqual(result.get("code"), "bad_route")
        self.assertEqual(self.host.trace, [])

    async def test_saved_unavailable_window_mode_reports_why_it_could_not_restore(self):
        self.host.config["prefs"]["panel_mode"] = "window"
        result = await self.controller.start()
        self.assertEqual(result["mode"], "embedded")
        self.assertTrue(result.get("error"), "Failed placement restoration must remain visible")
        self.assertEqual(self.host.config["prefs"]["panel_mode"], "window")
        self.assertFalse(self.calls("browser", "ensure"))

    async def test_invalid_modes_do_not_initialize_and_pending_change_is_busy(self):
        for mode in (None, True, "native", {"mode": []}, {"mode": "unknown"}):
            with self.subTest(mode=mode):
                result = await self.controller.request(mode)
                self.assertTrue(result.get("error"))
                self.assertFalse(self.controller.status()["pending"])
        self.assertEqual(self.natives, [])
        self.assertEqual(self.browsers, [])
        pending = await self.controller.request("dashboard")
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["mode"], "embedded")
        busy = await self.controller.request("floating")
        self.assertTrue(busy.get("error"))
        self.assertEqual(self.controller.status()["requested_mode"], "dashboard")
        self.assertEqual(self.host.saved, [])
        self.assertEqual(self.calls("host", "switch"), [])

    async def test_preflight_reply_precedes_explicit_handoff_and_persistence(self):
        result = await self.controller.request({"mode": "dashboard"})
        self.assertEqual(result["mode"], "embedded")
        self.assertTrue(result["pending"])
        await asyncio.sleep(0)
        self.assertEqual(self.controller.status()["mode"], "embedded")
        self.assertEqual(self.calls("host", "switch"), [])
        self.assertEqual(self.host.saved, [])
        applied = await self.controller.apply_pending()
        self.assertEqual(applied["mode"], "dashboard")
        self.assertFalse(applied["pending"])
        self.assertEqual(self.host.saved[-1]["prefs"]["panel_mode"], "dashboard")
        switched = self.calls("host", "switch")
        self.assertEqual(len(switched), 1)
        self.assertEqual(switched[0][2:], ("panel", {"open": True, "tab": "modules"}))
        self.assertLess(self.host.trace.index(switched[0]), self.host.trace.index(self.calls("host", "save")[0]))

    async def test_selected_mode_is_saved_only_after_surface_is_ready(self):
        self.host.switch_resume = asyncio.Event()
        await self.controller.request("floating")
        applying = asyncio.create_task(self.controller.apply_pending())
        try:
            await asyncio.wait_for(self.host.switch_entered.wait(), 1)
            self.assertEqual(self.host.saved, [])
            self.assertEqual(self.host.config["prefs"]["panel_mode"], "embedded")
        finally:
            self.host.switch_resume.set()
        result = await asyncio.wait_for(applying, 1)
        self.assertEqual(result["mode"], "floating")
        self.assertEqual(self.host.saved[-1]["prefs"]["panel_mode"], "floating")

    async def test_dashboard_and_floating_share_existing_surface_and_adapters(self):
        await self.select("dashboard")
        native, browser = self.natives[0], self.browsers[0]
        switches = self.calls("host", "switch")[:]
        ensures = self.calls("browser", "ensure")[:]
        result = await self.select("floating")
        self.assertEqual(result["mode"], "floating")
        self.assertEqual(self.natives, [native])
        self.assertEqual(self.browsers, [browser])
        self.assertEqual(self.calls("host", "switch"), switches)
        self.assertEqual(self.calls("browser", "ensure"), ensures)

    async def test_handoff_failure_clears_pending_and_restores_embedded_with_fixed_error(self):
        self.host.switch_failure = True
        await self.controller.request("dashboard")
        result = await self.controller.apply_pending()
        self.assertEqual(result["mode"], "embedded")
        self.assertFalse(result["pending"])
        self.assertTrue(result.get("error"))
        self.assertNotIn("private-user", str(result))
        self.assertNotIn("token=secret", str(result))
        self.assertEqual(self.host.surface, "main")
        self.assertFalse(any(value["prefs"]["panel_mode"] == "dashboard" for value in self.host.saved))

    async def test_start_applies_previously_saved_native_mode(self):
        self.host.config["prefs"]["panel_mode"] = "floating"
        await self.controller.start()
        self.assertEqual(self.controller.status()["mode"], "floating")
        self.assertEqual(self.host.surface, "panel")
        self.assertEqual(len(self.natives), 1)
        self.assertEqual(len(self.browsers), 1)

    async def test_failed_native_probe_retry_uses_a_fresh_adapter(self):
        self.next_native_available = False
        failed = await self.controller.request("dashboard")
        self.assertTrue(failed.get("error"))
        self.assertFalse(failed["pending"])
        self.assertEqual(failed["mode"], "embedded")
        self.assertFalse(failed["available"])
        self.assertNotIn("private-user", str(failed))
        first = self.natives[0]
        await self.until(lambda: first.closed)
        self.next_native_available = True
        recovered = await self.select("dashboard")
        self.assertEqual(recovered["mode"], "dashboard")
        self.assertEqual(len(self.natives), 2)
        self.assertIsNot(self.natives[-1], first)
        self.assertTrue(first.closed)

    async def test_close_cleans_resources_and_prevents_further_native_calls(self):
        await self.select("floating")
        native, browser = self.natives[0], self.browsers[0]
        await self.controller.close()
        self.assertTrue(native.closed)
        self.assertTrue(browser.closed)
        self.assertFalse(self.controller.status()["active"])
        native_calls = [entry for entry in self.host.trace if entry[0] == "native"]
        browser_calls = [entry for entry in self.host.trace if entry[0] == "browser"]
        await self.controller.panel_changed(True)
        await self.controller.request("dashboard")
        await self.controller.close()
        self.assertEqual([entry for entry in self.host.trace if entry[0] == "native"], native_calls)
        self.assertEqual([entry for entry in self.host.trace if entry[0] == "browser"], browser_calls)

    async def test_visibility_callback_does_not_shadow_dynamic_host_panel_state(self):
        delegate = self.host

        class ProxyHost:
            def __getattr__(self, name):
                return getattr(delegate, name)

        proxy = ProxyHost()
        controller = panel.Controller(proxy, native_factory=self.native_factory, browser_factory=self.browser_factory, native_runner=self.native_runner)
        self.addAsyncCleanup(controller.close)
        delegate.PANEL = False
        await controller.panel_changed(False)
        self.assertNotIn("PANEL", proxy.__dict__)
        delegate.PANEL = True
        await controller.panel_changed(True)
        self.assertTrue(controller.status()["open"])
        self.assertNotIn("PANEL", proxy.__dict__)

    async def test_logical_close_wins_over_an_inflight_native_show(self):
        self.host.PANEL = False
        await self.select("floating")
        native, browser = self.natives[0], self.browsers[0]
        native.dashboard_visible_value = False
        browser.block_name = "show"
        self.host.PANEL = True
        opening = asyncio.create_task(self.controller.panel_changed(True))
        await asyncio.wait_for(browser.entered.wait(), 1)
        self.host.PANEL = False
        trace_after_close = len(self.host.trace)
        closing = asyncio.create_task(self.controller.panel_changed(False))
        await asyncio.sleep(0)
        browser.resume.set()
        await asyncio.wait_for(asyncio.gather(opening, closing), 1)
        self.assertFalse(self.controller.status()["open"])
        self.assertFalse(self.controller.status()["active"])
        self.assertFalse(native.own_visible_value)
        self.assertFalse(native.interactive_value)
        self.assertNotIn(("native", "interactive", True), self.host.trace[trace_after_close:])

    async def test_logical_close_during_initial_apply_hides_before_apply_returns(self):
        self.assertTrue(self.host.PANEL)
        await self.controller.request("floating")
        native, browser = self.natives[0], self.browsers[0]
        browser.block_name = "show"
        applying = asyncio.create_task(self.controller.apply_pending())
        try:
            await asyncio.wait_for(browser.entered.wait(), 1)
            self.host.PANEL = False
            await self.controller.panel_changed(False)
        finally:
            browser.resume.set()
        result = await asyncio.wait_for(applying, 1)
        self.assertFalse(result["open"])
        self.assertFalse(result["active"])
        self.assertFalse(result["visible"])
        self.assertFalse(native.own_visible_value)
        self.assertFalse(native.interactive_value)
        self.assertIn(("browser", "hide"), self.host.trace)
        self.assertNotIn(("native", "interactive", True), self.host.trace)

    async def test_close_during_handoff_readiness_has_no_late_persist_publish_or_rehost(self):
        self.host.switch_resume = asyncio.Event()
        await self.controller.request("floating")
        applying = asyncio.create_task(self.controller.apply_pending())
        try:
            await asyncio.wait_for(self.host.switch_entered.wait(), 1)
            await asyncio.wait_for(self.controller.close(), 1)
            trace_at_close = self.host.trace[:]
            events_at_close = self.host.events[:]
        finally:
            self.host.switch_resume.set()
        result = await asyncio.wait_for(applying, 1)
        self.assertEqual(self.host.saved, [])
        self.assertEqual(self.host.events, events_at_close)
        self.assertEqual(self.host.trace, trace_at_close)
        self.assertFalse(result["pending"])
        self.assertFalse(result["active"])
        self.assertTrue(self.natives[0].closed)
        self.assertTrue(self.browsers[0].closed)

    async def test_dashboard_visibility_and_floating_interaction_follow_actual_state(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 0.01):
            await self.select("dashboard")
            native = self.natives[0]
            self.assertTrue(self.controller.status()["active"])
            native.dashboard_visible_value = False
            await self.until(lambda: self.controller.status()["active"] is False)
            self.assertFalse(native.interactive_value)
            result = await self.select("floating")
            self.assertTrue(result["active"])
            self.assertTrue(result["visible"])
            self.assertTrue(native.interactive_value)
            self.assertTrue(result.get("input_notice"))
            native.dashboard_visible_value = True
            await self.until(lambda: native.interactive_value is False)
            self.assertTrue(self.controller.status()["visible"])

    async def test_logically_closed_native_panel_has_no_monitor_polling(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 0.01):
            await self.select("floating")
            self.host.PANEL = False
            await self.controller.panel_changed(False)
            calls = self.host.trace[:]
            await asyncio.sleep(0.035)
            self.assertEqual(self.host.trace, calls)
            self.assertFalse(self.controller.status()["active"])

    async def test_timed_out_native_call_prevents_overlapping_retry_or_cleanup(self):
        await self.controller.close()
        entered, resume = asyncio.Event(), asyncio.Event()
        running = 0
        maximum = 0

        async def gated_runner(fn, *args):
            nonlocal running, maximum
            running += 1
            maximum = max(maximum, running)
            try:
                if fn.__name__ == "probe":
                    entered.set()
                    await resume.wait()
                return fn(*args)
            finally:
                running -= 1

        controller = panel.Controller(self.host, native_factory=self.native_factory, browser_factory=self.browser_factory, native_runner=gated_runner)
        self.addAsyncCleanup(controller.close)
        with mock.patch.object(panel, "NATIVE_TIMEOUT", 0.01):
            request = asyncio.create_task(controller.request("dashboard"))
            await asyncio.wait_for(entered.wait(), 1)
            failed = await asyncio.wait_for(request, 1)
            self.assertTrue(failed.get("error"))
            retry = await controller.request("dashboard")
            self.assertTrue(retry.get("error"))
            self.assertEqual(len(self.natives), 1)
            self.assertEqual(maximum, 1)
            self.assertEqual(self.host.trace, [("host", "capture")])
            resume.set()
            await self.until(lambda: self.natives[0].closed)
            self.assertEqual(maximum, 1)
        self.assertEqual(running, 0)

    async def test_monitor_checks_visibility_without_repositioning_at_poll_rate(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 0.01), mock.patch.object(panel, "ALIGN_INTERVAL", 30):
            await self.select("floating")
            positions = len(self.calls("native", "position_right"))
            checks = len(self.calls("native", "dashboard_visible"))
            await self.until(lambda: len(self.calls("native", "dashboard_visible")) >= checks + 2)
            self.assertEqual(len(self.calls("native", "position_right")), positions)

    async def test_methods_reuse_host_controller_and_only_prepare_mode_change(self):
        self.assertIs(panel.get(self.host), self.controller)
        status = await panel.METHODS["panel_status"](self.host, None)
        self.assertEqual(status, self.controller.status())
        pending = await panel.METHODS["panel_mode"](self.host, {"mode": "floating"})
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["mode"], "embedded")
        self.assertEqual(self.host.saved, [])
        self.assertEqual(self.calls("host", "switch"), [])

    async def test_busy_preflight_status_retains_requested_mode(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        capture = self.host.capture_panel_state

        async def gated_capture():
            entered.set()
            await resume.wait()
            return await capture()

        self.host.capture_panel_state = gated_capture
        requesting = asyncio.create_task(self.controller.request("dashboard"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            status = await panel.METHODS["panel_status"](self.host, None)
            self.assertTrue(status["pending"])
            self.assertEqual(status["requested_mode"], "dashboard")
            self.assertIn(status["requested_mode"], ("embedded", "dashboard", "floating"))
            busy = await self.controller.request("floating")
            self.assertEqual(busy["requested_mode"], "dashboard")
        finally:
            resume.set()
            await asyncio.wait_for(requesting, 1)

    async def test_public_status_reoffers_retry_after_failed_preflight_without_probing(self):
        self.next_native_available = False
        failed = await self.controller.request("dashboard")
        self.assertTrue(failed.get("error"))
        await self.until(lambda: self.natives[0].closed)
        await asyncio.sleep(0)
        trace = self.host.trace[:]
        adapters = self.natives[:]
        browsers = self.browsers[:]
        retry_status = await panel.METHODS["panel_status"](self.host, None)
        self.assertTrue(retry_status["supported"])
        self.assertTrue(retry_status["available"])
        self.assertFalse(retry_status["pending"])
        self.assertEqual(self.host.trace, trace)
        self.assertEqual(self.natives, adapters)
        self.assertEqual(self.browsers, browsers)

    async def test_fallback_preserves_saved_placement_after_recovery_surface_is_ready(self):
        await self.select("floating")
        self.host.saved.clear()
        self.host.switch_entered = asyncio.Event()
        self.host.switch_resume = asyncio.Event()
        self.browsers[0].failure = "hide"
        self.host.PANEL = False
        falling_back = asyncio.create_task(self.controller.panel_changed(False))
        try:
            await asyncio.wait_for(self.host.switch_entered.wait(), 1)
            self.assertEqual(self.host.saved, [])
            self.assertEqual(self.host.config["prefs"]["panel_mode"], "floating")
        finally:
            self.host.switch_resume.set()
        result = await asyncio.wait_for(falling_back, 1)
        self.assertEqual(result["mode"], "embedded")
        self.assertEqual(self.host.surface, "main")
        self.assertEqual(self.host.saved, [])
        self.assertEqual(self.host.config["prefs"]["panel_mode"], "floating")

    async def test_closed_fallback_does_not_save_after_late_surface_readiness(self):
        await self.select("floating")
        self.host.saved.clear()
        self.host.switch_entered = asyncio.Event()
        self.host.switch_resume = asyncio.Event()
        self.browsers[0].failure = "hide"
        self.host.PANEL = False
        falling_back = asyncio.create_task(self.controller.panel_changed(False))
        try:
            await asyncio.wait_for(self.host.switch_entered.wait(), 1)
            await asyncio.wait_for(self.controller.close(), 1)
        finally:
            self.host.switch_resume.set()
        await asyncio.wait_for(falling_back, 1)
        self.assertEqual(self.host.saved, [])
        self.assertEqual(self.host.config["prefs"]["panel_mode"], "floating")
        self.assertFalse(self.controller.status()["active"])

    async def test_floating_panel_keeps_world_pose_while_dashboard_is_closed(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 0.01), mock.patch.object(panel, "ALIGN_INTERVAL", 0):
            await self.select("floating")
            native = self.natives[0]
            native.dashboard_visible_value = False
            positions = len(self.calls("native", "position_right"))
            checks = len(self.calls("native", "dashboard_visible"))
            await self.controller.panel_changed(True)
            await self.until(lambda: len(self.calls("native", "dashboard_visible")) >= checks + 2)
            self.assertEqual(len(self.calls("native", "position_right")), positions)
            self.assertTrue(self.controller.status()["visible"])

    async def test_floating_warning_and_handoff_deadline_match_root_contract(self):
        result = await self.select("floating")
        self.assertEqual(result["input_notice"], "Controller pointers may interrupt game controls while the floating panel is visible.")
        self.assertEqual(panel.SWITCH_TIMEOUT, 15)

    async def test_close_during_capture_prevents_late_native_preflight(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        capture = self.host.capture_panel_state

        async def gated_capture():
            entered.set()
            await resume.wait()
            return await capture()

        self.host.capture_panel_state = gated_capture
        requesting = asyncio.create_task(self.controller.request("floating"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(self.controller.close(), 1)
        finally:
            resume.set()
        result = await asyncio.wait_for(requesting, 1)
        self.assertFalse(result["pending"])
        self.assertFalse(result["active"])
        self.assertEqual(self.natives, [])
        self.assertEqual(self.browsers, [])
        self.assertEqual(self.calls("host", "switch"), [])

    async def test_failed_fallback_save_does_not_falsify_ready_embedded_visibility(self):
        self.host.switch_failure = True
        await self.controller.request("dashboard")
        with mock.patch.object(self.host, "save", side_effect=OSError("/home/private-user/token=secret")):
            result = await self.controller.apply_pending()
        self.assertEqual(result["mode"], "embedded")
        self.assertEqual(self.host.surface, "main")
        self.assertTrue(self.host.ui_visible)
        self.assertTrue(result["active"])
        self.assertTrue(result["visible"])
        self.assertTrue(result.get("error"))
        self.assertNotIn("private-user", str(result))
        self.assertNotIn("token=secret", str(result))

    async def test_acknowledged_but_ignored_show_falls_back_in_a_bounded_visibility_check(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 60), mock.patch.object(panel, "VISIBILITY_INTERVAL", 0.001):
            await self.controller.request("floating")
            native, browser = self.natives[0], self.browsers[0]
            native.ignore_show = True
            result = await asyncio.wait_for(self.controller.apply_pending(), 1)
            self.assertEqual(result["mode"], "embedded")
            self.assertTrue(result.get("error"))
            self.assertEqual(self.host.surface, "main")
            self.assertTrue(browser.closed)
            await self.until(lambda: native.closed)
            checks = len(self.calls("native", "own_visible"))
            self.assertGreaterEqual(checks, 2)
            self.assertLessEqual(checks, 6)
            self.assertNotIn("private-user", str(result))
            self.assertNotIn("token=secret", str(result))

    async def test_acknowledged_but_ignored_hide_does_not_leave_false_native_active_state(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 60), mock.patch.object(panel, "VISIBILITY_INTERVAL", 0.001):
            await self.select("floating")
            native, browser = self.natives[0], self.browsers[0]
            native.ignore_hide = True
            before = len(self.calls("native", "own_visible"))
            self.host.PANEL = False
            result = await asyncio.wait_for(self.controller.panel_changed(False), 1)
            self.assertEqual(result["mode"], "embedded")
            self.assertFalse(result["active"])
            self.assertFalse(result["visible"])
            self.assertTrue(result.get("error"))
            self.assertEqual(self.host.surface, "main")
            self.assertTrue(browser.closed)
            await self.until(lambda: native.closed)
            checks = len(self.calls("native", "own_visible")) - before
            self.assertGreaterEqual(checks, 2)
            self.assertLessEqual(checks, 6)

    async def test_delayed_visibility_match_keeps_native_panel_without_fallback(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 60), mock.patch.object(panel, "VISIBILITY_INTERVAL", 0.001):
            await self.controller.request("floating")
            native, browser = self.natives[0], self.browsers[0]
            native.ignore_show = True
            native.visibility_sequence = [False, False, True]
            result = await asyncio.wait_for(self.controller.apply_pending(), 1)
            self.assertEqual(result["mode"], "floating")
            self.assertTrue(result["visible"])
            self.assertTrue(result["active"])
            self.assertFalse(result.get("error"))
            self.assertFalse(browser.closed)
            self.assertFalse(native.closed)
            self.assertEqual(len(self.calls("native", "own_visible")), 3)
            self.assertEqual(self.host.surface, "panel")

    async def test_immediate_visibility_match_adds_no_confirmation_or_idle_polling(self):
        with mock.patch.object(panel, "POLL_INTERVAL", 60), mock.patch.object(panel, "VISIBILITY_INTERVAL", 0.001):
            result = await self.select("floating")
            self.assertTrue(result["visible"])
            self.assertEqual(len(self.calls("native", "own_visible")), 1)
            trace = self.host.trace[:]
            await asyncio.sleep(0.01)
            self.assertEqual(self.host.trace, trace)


if __name__ == "__main__":
    unittest.main()
