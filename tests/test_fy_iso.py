import asyncio
import json
import pathlib
import signal
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import fy_iso
import jharness as H

PROBES = ("typeof window.fyCall", "typeof window.Framey", "typeof window.FYBar")


def is_probe(p):
    return p.get("returnByValue") is True


class ModeTests(unittest.TestCase):
    def test_json_values_true_false_and_auto_are_accepted(self):
        for v in (True, False, "auto"):
            self.assertEqual(fy_iso.mode({"isolation": v}, None), v)

    def test_anything_else_in_loader_json_means_off(self):
        for v in ("yes", "true", 1, 0, None, [], {}, "AUTO", 1.0):
            self.assertIs(fy_iso.mode({"isolation": v}, None), False)
        self.assertIs(fy_iso.mode({}, None), False)

    def test_the_file_is_read_only_when_loader_json_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "isolation"
            for text, want in (("true", True), ("false", False), ('"auto"', "auto"), ("yes", False), ("", False), ("1", False), ("true" + " " * 40, False)):
                f.write_text(text)
                self.assertEqual(fy_iso.mode(None, f), want, text)
                self.assertIs(fy_iso.mode({}, f), False)
            self.assertIs(fy_iso.mode(None, pathlib.Path(d) / "missing"), False)

    def test_the_event_filter_drops_console_traffic_without_parsing(self):
        self.assertFalse(fy_iso.wanted('{"method":"Runtime.consoleAPICalled","params":{}}'))
        self.assertFalse(fy_iso.wanted('{"method":"Page.frameResized","params":{}}'))
        for name in fy_iso.EVENTS:
            self.assertTrue(fy_iso.wanted('{"method":"%s","params":{}}' % name))
        self.assertTrue(fy_iso.wanted('{"id":4,"result":{}}'))
        self.assertTrue(fy_iso.wanted("garbage"))


class Base(unittest.IsolatedAsyncioTestCase):
    async def boot(self, isolation=True, name="main", plugin=True, js="window.RAN_a=1", **opt):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        if isolation is not None:
            loader.trust(isolation=isolation)
        if plugin:
            loader.plugin("a", {"api": 1}, main=js)
        fy_iso.STEP = opt.pop("step", 5)
        self.addCleanup(setattr, fy_iso, "STEP", 5)
        page = H.WorldPage(**opt)
        _, task = await H.attach(loader, name, page=page)
        self.addCleanup(task.cancel)
        want = "Framey" if name == "main" else "FYBar"
        await H.settle(lambda: page.has(want), limit=3)
        return loader, page

    def panel_expr(self, c):
        return [e for e in c.evals if e.startswith("(function(__fyt){window.FY_ICON_URL")]

    def main_world(self, page):
        return [p["expression"] for p in page.evaluates() if "contextId" not in p]

    def lines(self, loader):
        return [x for x in loader.errors.getvalue().splitlines() if "isolation" in x]


class DefaultTests(Base):
    async def test_the_default_sends_exactly_the_old_calls(self):
        loader, page = await self.boot(isolation=None)
        self.assertEqual(page.methods()[:3], ["Runtime.addBinding", "Runtime.evaluate", "Runtime.evaluate"])
        self.assertEqual(page.calls[0][1], {"name": "fyCall"})
        self.assertEqual(page.calls[1][1], {"expression": loader.fy.WIPE})
        self.assertEqual(page.calls[2][1], {"expression": "typeof window.Framey", "returnByValue": True})
        banned = {"Page.enable", "Page.getFrameTree", "Page.createIsolatedWorld", "Runtime.enable", "Runtime.removeBinding", "Runtime.disable", "Page.disable"}
        self.assertEqual(banned & set(page.methods()), set())
        for p in page.evaluates():
            self.assertEqual(set(p), {"expression", "returnByValue"} if is_probe(p) else {"expression"})
        self.assertEqual(page.worlds(), [])
        self.assertTrue(page.sees(page.main, "fyCall"))
        self.assertIn("Framey", page.main.globals)
        self.assertEqual(self.lines(loader), [])

    async def test_explicit_false_and_garbage_are_the_same_as_the_default(self):
        for v in (False, "maybe", 7):
            loader, page = await self.boot(isolation=v)
            self.assertEqual(page.methods()[0], "Runtime.addBinding", v)
            self.assertEqual(page.worlds(), [])
            self.assertTrue(all("contextId" not in p for p in page.evaluates()))

    async def test_the_file_override_turns_it_on_only_without_loader_json(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        marker = loader.home / ".config" / "framey" / "isolation"
        marker.write_text("true")
        self.assertIs(loader.fy.isolation(), True)
        loader.trust(channel="stable")
        self.assertIs(loader.fy.isolation(), False)
        loader.trust(isolation="auto")
        self.assertEqual(loader.fy.isolation(), "auto")

    async def test_bar_and_cross_target_pushes_keep_the_old_wire_format(self):
        loader, page = await self.boot(isolation=None)
        await loader.fy.send_main("main", "X")
        await loader.fy.send("main", "Y")
        self.assertEqual(page.calls[-2:], [("Runtime.evaluate", {"expression": "X"}), ("Runtime.evaluate", {"expression": "Y"})])


class IsolatedTests(Base):
    async def test_setup_follows_the_documented_protocol(self):
        loader, page = await self.boot(isolation=True)
        c = {m: p for m, p in page.calls if m in ("Page.createIsolatedWorld", "Runtime.addBinding")}
        self.assertEqual(c["Page.createIsolatedWorld"], {"frameId": "F1", "worldName": "framey"})
        self.assertEqual(c["Runtime.addBinding"], {"name": "fyCall", "executionContextName": "framey"})
        self.assertEqual(page.methods()[:6], ["Runtime.enable", "Page.enable", "Runtime.evaluate", "Page.getFrameTree", "Runtime.addBinding", "Runtime.evaluate"])
        self.assertEqual(page.methods().count("Runtime.addBinding"), 1)
        self.assertEqual(self.lines(loader), [])

    async def test_the_page_context_sees_neither_the_binding_nor_framey_nor_the_tokens(self):
        loader, page = await self.boot(isolation=True)
        iso = page.iso()
        self.assertFalse(page.sees(page.main, "fyCall"))
        self.assertNotIn("Framey", page.main.globals)
        self.assertEqual(page.main.tokens, [])
        self.assertEqual(page.main.evals, [loader.fy.WIPE])
        self.assertTrue(page.sees(iso, "fyCall"))
        self.assertIn("Framey", iso.globals)
        self.assertIn(loader.fy.CORE_TOK, iso.tokens)
        self.assertTrue(any("RAN_a" in e for e in iso.evals))
        for tok in [loader.fy.CORE_TOK, *loader.fy.TOKS]:
            self.assertFalse(any(tok in e for e in page.main.evals))

    async def test_a_page_script_cannot_invoke_the_binding_and_a_foreign_context_is_ignored(self):
        loader, page = await self.boot(isolation=True)
        self.assertFalse(page.call(page.main, id=5, gen="g", plugin="_core", method="list"))
        page.emit("Runtime.bindingCalled", name="fyCall", payload=json.dumps({"id": 6, "gen": "g", "plugin": "_core", "method": "list", "t": loader.fy.CORE_TOK}), executionContextId=page.main.id)
        page.bind(id=7, gen="g", plugin="_core", method="list")
        await asyncio.sleep(0.3)
        self.assertEqual(page.replies(), [])
        self.assertTrue(page.call(page.iso(), id=8, gen="g", plugin="_core", method="list"))
        self.assertTrue(await H.settle(lambda: page.replies()))
        self.assertEqual(len(page.replies()), 1)
        self.assertEqual(page.replies()[0][:2], ["g", 8])

    async def test_every_framey_evaluation_carries_the_isolated_context_id(self):
        loader, page = await self.boot(isolation=True)
        fy = loader.fy
        await fy.panel(True)
        await fy.notify_page("_core", "progress", {"n": 1})
        r = await fy.core("asset", {"name": "picker"})
        self.assertEqual(r, {"ok": True})
        await fy.core("reload_one", "a")
        await fy.refresh()
        page.call(page.iso(), id=9, gen="g", plugin="_core", method="list")
        await H.settle(lambda: page.replies())
        await asyncio.sleep(0.1)
        iso = page.iso().id
        for p in page.evaluates():
            if "contextId" in p:
                self.assertEqual(p["contextId"], iso, p["expression"][:60])
        main = self.main_world(page)
        self.assertEqual(main, ["typeof window.fyCall", "typeof window.fyCall", fy.WIPE])
        self.assertTrue(any(e.startswith("window.Framey&&Framey._panel(true)") for e in page.iso().evals))
        self.assertTrue(any("FY_ICON_URL" in e for e in page.iso().evals))
        self.assertTrue(any(e[1] == "progress" for e in page.events()))

    async def test_a_plugin_that_throws_is_reported_inside_the_isolated_world(self):
        loader, page = await self.boot(isolation=True, js="THROW_ME")
        fail = 'Framey.fail("a",{message:"Error: boom"})'
        self.assertTrue(await H.settle(lambda: fail in page.iso().evals, limit=3))
        self.assertNotIn(fail, page.main.evals)
        self.assertIn("plugin a failed: Error: boom", loader.errors.getvalue())
        self.assertEqual(loader.fy.FAILS["a"]["what"], "load")

    async def test_auto_behaves_like_true(self):
        loader, page = await self.boot(isolation="auto")
        self.assertIn("Framey", page.iso().globals)
        self.assertNotIn("Framey", page.main.globals)

    async def test_bar_runs_in_its_own_isolated_world(self):
        loader, page = await self.boot(isolation=True, name="bar")
        iso = page.iso()
        self.assertIn("FYBar", iso.globals)
        self.assertNotIn("FYBar", page.main.globals)
        self.assertFalse(page.sees(page.main, "fyCall"))
        await loader.fy.send("bar", "window.FYBar&&FYBar.badge(2)")
        self.assertIn("window.FYBar&&FYBar.badge(2)", iso.evals)
        self.assertIn("window.FYBar&&FYBar.set(false)", iso.evals)
        self.assertNotIn("FYBar.badge(2)", "".join(page.main.evals))

    async def test_console_traffic_is_dropped_and_does_not_disturb_the_session(self):
        loader, page = await self.boot(isolation=True)
        for i in range(50):
            page.emit("Runtime.consoleAPICalled", type="log", args=[{"type": "string", "value": "x" * 1000}])
        page.emit("Page.frameResized")
        self.assertTrue(page.call(page.iso(), id=3, gen="g", plugin="_core", method="list"))
        self.assertTrue(await H.settle(lambda: page.replies()))

    async def test_wipe_runs_in_the_isolated_world_on_refresh_and_clears_the_handle(self):
        loader, page = await self.boot(isolation=True)
        iso = page.iso()
        n = len(self.panel_expr(iso))
        await loader.fy.refresh()
        self.assertTrue(await H.settle(lambda: len(self.panel_expr(iso)) == n + 1))
        self.assertEqual(iso.evals.count(loader.fy.WIPE), 2)
        self.assertEqual(page.main.evals.count(loader.fy.WIPE), 1)


class NavigationTests(Base):
    async def test_navigation_recreates_the_world_and_reinjects_once(self):
        loader, page = await self.boot(isolation=True)
        first = page.iso()
        self.assertEqual(len(self.panel_expr(first)), 1)
        page.navigate()
        await asyncio.sleep(0.2)
        self.assertEqual(page.methods().count("Page.createIsolatedWorld"), 1)
        self.assertFalse(page.has("Framey"))
        await loader.fy.LIVE["main"].ping()
        self.assertTrue(await H.settle(lambda: page.iso() and page.iso().id != first.id and page.has("Framey"), limit=3))
        new = page.iso()
        await asyncio.sleep(0.3)
        self.assertEqual(page.methods().count("Page.createIsolatedWorld"), 2)
        self.assertEqual(len(self.panel_expr(new)), 1)
        self.assertTrue(page.sees(new, "fyCall"))
        self.assertFalse(page.sees(page.main, "fyCall"))
        self.assertEqual(page.main.tokens, [])
        self.assertEqual(page.main.evals, [])
        self.assertTrue(page.call(new, id=1, gen="g", plugin="_core", method="list"))
        self.assertTrue(await H.settle(lambda: page.replies()))
        self.assertTrue(page.call(first, id=2, gen="g", plugin="_core", method="list"))
        await asyncio.sleep(0.3)
        self.assertEqual(len(page.replies()), 1)

    async def test_calls_made_between_navigation_and_renewal_are_dropped_not_sent_to_the_page(self):
        loader, page = await self.boot(isolation=True)
        page.navigate(announce=False)
        await asyncio.sleep(0.1)
        before = len(page.evaluates())
        await loader.fy.send("main", "window.Framey&&Framey._panel(true)")
        self.assertEqual(len(page.evaluates()), before)
        self.assertEqual(page.main.evals, [])

    async def test_a_lost_context_is_renewed_at_the_next_probe_without_a_navigation_event(self):
        loader, page = await self.boot(isolation=True)
        first = page.iso()
        page.destroy(first)
        await asyncio.sleep(0.1)
        await loader.fy.LIVE["main"].ping()
        self.assertTrue(await H.settle(lambda: page.iso() and page.has("Framey"), limit=3))
        self.assertNotEqual(page.iso().id, first.id)
        self.assertEqual(len(self.panel_expr(page.iso())), 1)

    async def test_a_changed_frame_id_is_resolved_again(self):
        loader, page = await self.boot(isolation=True)
        page.navigate(frame="F2", announce=False)
        await asyncio.sleep(0.1)
        await loader.fy.LIVE["main"].ping()
        self.assertTrue(await H.settle(lambda: page.iso() and page.has("Framey"), limit=3))
        self.assertEqual(page.methods().count("Page.getFrameTree"), 2)

    async def test_probes_in_flight_during_an_injection_do_not_inject_again(self):
        loader, page = await self.boot(isolation=True)
        send = loader.fy.LIVE["main"]
        await loader.fy.send("main", loader.fy.WIPE)
        await asyncio.gather(send.ping(), send.ping(), send.ping())
        await asyncio.sleep(0.4)
        self.assertEqual(len(self.panel_expr(page.iso())), 2)
        self.assertEqual(self.lines(loader), [])
        self.assertIn("Framey", page.iso().globals)

    async def test_a_stale_context_error_on_the_probe_triggers_a_renewal(self):
        loader, page = await self.boot(isolation=True)
        old = page.iso()
        del page.contexts[old.id]
        await loader.fy.LIVE["main"].ping()
        await asyncio.sleep(0.2)
        await loader.fy.LIVE["main"].ping()
        self.assertTrue(await H.settle(lambda: page.iso() and page.has("Framey"), limit=3))
        self.assertNotEqual(page.iso().id, old.id)


class FallbackTests(Base):
    async def check(self, loader, page, line="isolation unavailable"):
        self.assertTrue(await H.settle(lambda: "Framey" in page.main.globals, limit=3))
        self.assertEqual(len(self.lines(loader)), 1, loader.errors.getvalue())
        self.assertIn(line, self.lines(loader)[0])
        self.assertIn("fyCall", page.plain)
        self.assertTrue(page.sees(page.main, "fyCall"))
        idx = max(i for i, (m, p) in enumerate(page.calls) if m == "Runtime.addBinding")
        self.assertEqual(page.calls[idx][1], {"name": "fyCall"})
        for m, p in page.calls[idx:]:
            self.assertNotIn("contextId", p)
        await loader.fy.send("main", "window.Framey&&Framey._panel(true)")
        self.assertIn("window.Framey&&Framey._panel(true)", page.main.evals)
        self.assertTrue(page.call(page.main, id=1, gen="g", plugin="_core", method="list"))
        self.assertTrue(await H.settle(lambda: page.replies()))

    async def test_create_world_failure_falls_back(self):
        loader, page = await self.boot(isolation="auto", world_error=True)
        await self.check(loader, page)
        self.assertIn("Runtime.removeBinding", page.methods())
        self.assertIn("Runtime.disable", page.methods())

    async def test_a_browser_that_rejects_the_context_name_falls_back(self):
        loader, page = await self.boot(isolation="auto", reject_name=True)
        await self.check(loader, page)
        self.assertEqual(page.worlds(), [])

    async def test_a_browser_that_ignores_the_context_name_is_detected_and_the_leak_removed(self):
        loader, page = await self.boot(isolation="auto", ignore_name=True)
        await self.check(loader, page)
        self.assertIn("visible to the page", self.lines(loader)[0])
        self.assertLess(page.methods().index("Runtime.removeBinding"), len(page.methods()) - 1)
        self.assertEqual(page.worlds(), [])

    async def test_a_missing_frame_id_falls_back(self):
        loader, page = await self.boot(isolation="auto", no_frame=True)
        await self.check(loader, page)

    async def test_a_silent_browser_falls_back_after_the_step_timeout(self):
        loader, page = await self.boot(isolation="auto", silent=("Page.getFrameTree",), step=0.2)
        await self.check(loader, page, "TimeoutError")

    async def test_a_binding_missing_in_the_world_falls_back(self):
        loader, page = await self.boot(isolation="auto", hide=True)
        await self.check(loader, page)
        self.assertIn("missing in the isolated world", self.lines(loader)[0])

    async def test_auto_never_injects_into_a_page_that_already_defines_fyCall(self):
        loader, page = await self.boot(isolation="auto", page_defines=True)
        await asyncio.sleep(0.3)
        self.assertNotIn("Framey", page.main.globals)
        self.assertEqual(page.main.evals, [])
        self.assertEqual(len(self.lines(loader)), 1, loader.errors.getvalue())
        self.assertIn("already defines fyCall", self.lines(loader)[0])

    async def test_the_handle_not_appearing_in_two_probe_intervals_falls_back(self):
        loader, page = await self.boot(isolation="auto", blind=True)
        send = loader.fy.LIVE["main"]
        self.assertEqual(len(self.panel_expr(page.iso())), 1)
        await send.ping()
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.panel_expr(page.iso())), 2)
        self.assertEqual(self.lines(loader), [])
        await send.ping()
        self.assertTrue(await H.settle(lambda: self.lines(loader), limit=3))
        self.assertIn("handle was not found", self.lines(loader)[0])
        await asyncio.sleep(0.2)
        self.assertEqual(page.calls[[i for i, (m, p) in enumerate(page.calls) if m == "Runtime.addBinding"][-1]][1], {"name": "fyCall"})
        self.assertTrue(await H.settle(lambda: "Framey" in page.main.globals, limit=3))
        self.assertIn("Runtime.removeBinding", page.methods())

    async def test_repeated_failures_to_renew_fall_back(self):
        loader, page = await self.boot(isolation="auto")
        send = loader.fy.LIVE["main"]
        page.destroy(page.iso())
        await asyncio.sleep(0.1)
        page.world_failures = 99
        for _ in range(3):
            await send.ping()
            await asyncio.sleep(0.1)
        self.assertTrue(await H.settle(lambda: "Framey" in page.main.globals, limit=3))
        self.assertTrue(any("could not be renewed" in x for x in self.lines(loader)))
        self.assertTrue(page.sees(page.main, "fyCall"))


class StrictTests(Base):
    async def test_isolation_true_never_injects_the_main_world_when_the_world_fails(self):
        for opt in ({"world_error": True}, {"reject_name": True}, {"no_frame": True}, {"hide": True}, {"page_defines": True}):
            loader, page = await self.boot(isolation=True, **opt)
            await asyncio.sleep(0.3)
            self.assertNotIn("Framey", page.main.globals, opt)
            self.assertEqual(page.main.evals, [], opt)
            self.assertIn("isolation true: page not injected", "\n".join(self.lines(loader)), opt)
            await loader.close()


class CrossTargetTests(Base):
    async def test_target_pushes_stay_in_the_page_context(self):
        loader, page = await self.boot(isolation=True)
        fy = loader.fy
        r = await fy.core("target_add", {"pid": "a", "target": "main", "kind": "css", "key": "k", "code": "x{}"})
        self.assertEqual(r, {"ok": True})
        r = await fy.core("target_add", {"pid": "a", "target": "main", "kind": "js", "key": "j", "code": "1", "teardown": "2"})
        self.assertEqual(r, {"ok": True})
        self.assertEqual(len(page.main.evals) , 3)
        self.assertTrue(any("data-fy-target" in e for e in page.main.evals))
        self.assertFalse(any("data-fy-target" in e for e in page.iso().evals))
        await fy.core("target_remove", {"pid": "a", "target": "main", "key": "k"})
        self.assertEqual(len(page.main.evals), 4)
        self.assertEqual(self.main_world(page)[2:3], [fy.WIPE])

    async def test_replayed_targets_run_in_the_page_context_after_every_attach_and_navigation(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.trust(isolation=True)
        fy = loader.fy
        await fy.core("target_add", {"pid": "a", "target": "main", "kind": "css", "key": "k", "code": "x{}"})
        page = H.WorldPage()
        _, task = await H.attach(loader, "main", page=page)
        self.addCleanup(task.cancel)
        self.assertTrue(await H.settle(lambda: page.has("Framey") and any("data-fy-target" in e for e in page.main.evals), limit=3))
        self.assertFalse(any("data-fy-target" in e for e in page.iso().evals))
        page.navigate()
        await asyncio.sleep(0.1)
        await fy.LIVE["main"].ping()
        self.assertTrue(await H.settle(lambda: len(page.main.evals) == 1 and page.iso() and page.has("Framey"), limit=3))
        self.assertTrue(any("data-fy-target" in e for e in page.main.evals))
        self.assertEqual(len(self.panel_expr(page.iso())), 1)

    async def test_dev_target_sessions_are_never_isolated(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.trust(isolation=True)
        page = H.WorldPage()
        _, task = await H.attach(loader, "other", page=page)
        self.addCleanup(task.cancel)
        await asyncio.sleep(0.2)
        self.assertEqual(page.methods()[0], "Runtime.addBinding")
        self.assertEqual(page.calls[0][1], {"name": "fyCall"})
        self.assertEqual(set(page.methods()) - {"Runtime.addBinding", "Runtime.evaluate"}, set())


class ShutdownTests(Base):
    async def test_sigterm_wipes_both_isolated_worlds_and_the_targets_in_the_page_context(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.trust(isolation=True)
        loader.plugin("a", {"api": 1}, main="1")
        fy = loader.fy
        pages = {"main": H.WorldPage(), "bar": H.WorldPage()}
        for p in pages.values():
            p.tok = fy.CORE_TOK

        async def tabs():
            return [{"url": "https://steam/?vrOverlayKey=valve.steam.gamepadui.%s" % n, "webSocketDebuggerUrl": "ws://x/" + n} for n in pages]

        async def fake_open(cls, url):
            return pages[url.rsplit("/", 1)[1]]

        stops = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda l, c: None)
        loop.add_signal_handler = lambda sig, cb, *a: stops.append((sig, cb))
        fy.tabs = tabs
        fy.WS.open = classmethod(fake_open)
        await fy.core("target_add", {"pid": "a", "target": "main", "kind": "css", "key": "k", "code": "x{}"})
        task = asyncio.create_task(fy.main())
        self.addCleanup(task.cancel)
        self.assertTrue(await H.settle(lambda: pages["main"].has("Framey") and pages["bar"].has("FYBar"), limit=5))
        self.assertEqual(stops[0][0], signal.SIGTERM)
        stops[0][1]()
        await asyncio.wait_for(task, 10)
        main, bar = pages["main"], pages["bar"]
        self.assertFalse(main.has("Framey"))
        self.assertFalse(bar.has("FYBar"))
        self.assertEqual(main.iso().evals[-1], fy.WIPE)
        self.assertEqual(bar.iso().evals[-1], fy.WIPE_BAR)
        wipes = [p for p in main.evaluates() if p["expression"] == fy.WIPE and p.get("contextId") == main.iso().id]
        self.assertEqual(len(wipes), 2)
        self.assertTrue(any("[data-fy-target]" in e and "remove()" in e for e in main.main.evals))


if __name__ == "__main__":
    unittest.main()
