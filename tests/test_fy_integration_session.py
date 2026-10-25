import asyncio
import json
import pathlib
import re
import sys
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H

BACKEND = """
import asyncio
import time
import framey_api


async def call(method, arg):
    if method == "emit":
        framey_api.events.emit("ping", {"n": arg})
        return True
    if method == "burst":
        for i in range(arg):
            framey_api.events.emit("tick", {"i": i})
        return arg
    if method == "sleep":
        await asyncio.sleep(arg)
        return "slept"
    if method == "block":
        time.sleep(arg)
        return "late"
    return {"m": method, "a": arg}
"""


class Base(unittest.IsolatedAsyncioTestCase):
    async def start(self, prefs=None, plugins=("a",), manifest=None):
        loader = H.Loader(prefs=prefs)
        self.addAsyncCleanup(loader.close)
        for pid in plugins:
            loader.plugin(pid, dict({"api": 1}, **(manifest or {})), main="window.RAN_%s=1" % pid, backend=BACKEND)
        loader.fy.load_backends()
        return loader

    async def attach(self, loader, name="main"):
        page, task = await H.attach(loader, name)
        self.addCleanup(task.cancel)
        return page, task


class EventTests(Base):
    async def test_a_backend_event_reaches_the_page_in_one_batch(self):
        loader = await self.start()
        page, _ = await self.attach(loader)
        page.bind(id=1, gen="g", plugin="a", method="emit", arg=7)
        self.assertTrue(await H.settle(lambda: page.events() and page.replies()))
        self.assertEqual(page.events(), [["a", "ping", {"n": 7}, 0]])
        self.assertEqual(page.replies(), [["g", 1, True]])

    async def test_a_burst_is_flushed_in_batches_of_ten_in_order(self):
        loader = await self.start()
        page, _ = await self.attach(loader)
        page.bind(id=2, gen="g", plugin="a", method="burst", arg=25)
        self.assertTrue(await H.settle(lambda: len(page.events()) == 25))
        self.assertEqual([e[2]["i"] for e in page.events()], list(range(25)))

    async def test_exactly_two_full_batches_leave_no_third_flush_armed(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        page.bind(id=2, gen="g", plugin="a", method="burst", arg=20)
        self.assertTrue(await H.settle(lambda: len(page.events()) == 20))
        await asyncio.sleep(0.4)
        self.assertEqual(len(page.events()), 20)
        self.assertIsNone(fy.FLUSH)

    async def test_events_without_a_page_stay_queued_and_are_cleared_at_attach(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual(await fy.backend("a", "burst", 5), 5)
        self.assertTrue(await H.settle(lambda: fy.supervisor.events._size == 5))
        await asyncio.sleep(0.4)
        self.assertEqual(fy.supervisor.events._size, 5)
        self.assertIsNone(fy.FLUSH)
        page, _ = await self.attach(loader)
        self.assertEqual(fy.supervisor.events._size, 0)
        await asyncio.sleep(0.3)
        self.assertEqual(page.events(), [])
        page.bind(id=3, gen="g", plugin="a", method="emit", arg=1)
        self.assertTrue(await H.settle(lambda: page.events()))

    async def test_only_one_flush_timer_exists_while_events_are_pending(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        loop = asyncio.get_running_loop()
        fy.events_ready()
        first = fy.FLUSH
        fy.events_ready()
        fy.events_ready()
        self.assertIs(fy.FLUSH, first)
        await asyncio.sleep(0.3)
        self.assertIsNone(fy.FLUSH)
        self.assertTrue(first.cancelled() or first.when() <= loop.time())

    async def test_plugin_events_stop_at_the_per_plugin_rate_cap(self):
        loader = await self.start()
        page, _ = await self.attach(loader)
        page.bind(id=4, gen="g", plugin="a", method="burst", arg=200)
        self.assertTrue(await H.settle(lambda: page.replies()))
        await asyncio.sleep(0.6)
        delivered = len(page.events())
        self.assertLessEqual(delivered, 64)
        self.assertGreaterEqual(delivered, 10)


class PagePushTests(Base):
    async def test_panel_state_goes_to_the_bar_and_to_the_main_page(self):
        loader = await self.start()
        main, _ = await self.attach(loader, "main")
        bar, _ = await self.attach(loader, "bar")
        main.bind(id=0, plugin="_core", method="panel", arg=True)
        self.assertTrue(await H.settle(lambda: bar.exprs()[-1] == "window.FYBar&&FYBar.set(true)"))
        self.assertTrue(await H.settle(lambda: main.exprs()[-1] == "window.Framey&&Framey._panel(true)"))
        self.assertEqual(main.replies(), [])
        main.bind(id=0, plugin="_core", method="panel", arg="yes")
        self.assertTrue(await H.settle(lambda: bar.exprs()[-1] == "window.FYBar&&FYBar.set(false)"))
        self.assertTrue(await H.settle(lambda: main.exprs()[-1] == "window.Framey&&Framey._panel(false)"))
        self.assertFalse(loader.fy.PANEL)

    async def test_progress_and_notice_events_use_the_core_channel(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        ev = {"job": 1, "id": "a", "stage": "download", "index": 2, "total": 6, "label": "Downloading", "state": "running", "measured": None, "error": None}
        await fy.progress(1, ev)
        await fy.notify_page("_core", "notice", {"title": "T", "body": "B", "actions": [{"id": "open:modules", "label": "Open Modules"}]})
        got = page.events()
        self.assertEqual(got[0], ["_core", "progress", ev, 0])
        self.assertEqual(got[1][:2], ["_core", "notice"])
        self.assertEqual(got[1][2]["title"], "T")

    async def test_nothing_is_sent_to_a_page_that_is_not_attached(self):
        loader = await self.start()
        fy = loader.fy
        self.assertFalse(fy.attached("main"))
        await fy.progress(1, {})
        await fy.notify_page("_core", "notice", {})
        await fy.push_badge({"n": 3})
        await fy.panel(True)
        await fy.panel(False)
        self.assertEqual(fy.LIVE, {})

    async def test_the_badge_goes_to_the_bar_on_attach_and_after_a_check(self):
        loader = await self.start()
        fy = loader.fy
        bar, _ = await self.attach(loader, "bar")
        self.assertIn("window.FYBar&&FYBar.badge(0)", bar.exprs())
        self.assertIn((fy.ROOT / "fy-badge.js").read_text(), bar.exprs())
        self.assertLess(bar.exprs().index((fy.ROOT / "fy-badge.js").read_text()), bar.exprs().index("window.FYBar&&FYBar.badge(0)"))
        await fy.upd().record_checked({"a": {"state": "update", "latest": "abc1234"}})
        self.assertEqual(bar.exprs()[-1], "window.FYBar&&FYBar.badge(1)")
        self.assertEqual(await loader.core("badge"), {"n": 1, "loader": False})

    async def test_a_bar_attached_later_starts_with_the_stored_badge(self):
        loader = await self.start()
        fy = loader.fy
        await fy.upd().record_checked({"a": {"state": "update", "latest": "abc1234"}})
        bar, _ = await self.attach(loader, "bar")
        self.assertIn("window.FYBar&&FYBar.badge(1)", bar.exprs())

    async def test_check_job_records_the_result_and_refreshes_the_badge(self):
        loader = await self.start()
        fy = loader.fy
        d = loader.plugins / "a"
        (d / "plugin.json").write_text(json.dumps({"id": "a", "name": "a", "version": "1.0.0", "api": 1, "source": "https://github.com/o/a"}))
        (d / ".version").write_text("1" * 40)
        loader.net.commit("o", "a", "HEAD", "2" * 40)
        bar, _ = await self.attach(loader, "bar")
        r = await loader.core("check")
        self.assertEqual(r["checked"]["a"]["state"], "update")
        self.assertEqual(bar.exprs()[-1], "window.FYBar&&FYBar.badge(1)")
        self.assertEqual(fy.settings()["updates"]["plugins"]["a"], {"latest": "2222222", "state": "update"})

    async def test_target_replay_follows_the_plugin_code_on_every_attach(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual(await loader.core("target_add", {"pid": "a", "target": "main", "kind": "css", "key": "k", "code": "body{color:red}"}), {"ok": True})
        self.assertEqual(await loader.core("target_add", {"pid": "a", "target": "bar", "kind": "js", "key": "j", "code": "window.X=1", "teardown": "window.X=0"}), {"ok": True})
        self.assertEqual(await loader.core("target_add", {"pid": "a", "target": "store", "kind": "css", "key": "k", "code": "x"}), {"error": "Unknown target: store", "code": "unknown_target"})
        main, _ = await self.attach(loader, "main")
        bar, _ = await self.attach(loader, "bar")
        exprs = main.exprs()
        css = [i for i, e in enumerate(exprs) if "body{color:red}" in e]
        plugin = [i for i, e in enumerate(exprs) if "window.RAN_a=1" in e]
        self.assertEqual(len(css), 1)
        self.assertLess(plugin[0], css[0])
        restored = exprs.index("Framey.restore()")
        self.assertLess(plugin[0], restored)
        self.assertLess(restored, css[0])
        expected = fy.host.targets.reconcile_expr("main") + ";" + fy.host.targets.replay("main")[0][0]
        self.assertEqual(exprs[-1], expected)
        self.assertTrue(any("window.X=1" in e for e in bar.exprs()))
        self.assertFalse(any("window.X=1" in e for e in exprs))

    async def test_reload_of_a_plugin_removes_its_injected_items_from_the_pages(self):
        loader = await self.start(plugins=("a", "b"))
        fy = loader.fy
        main, _ = await self.attach(loader, "main")
        await loader.core("target_add", {"pid": "a", "target": "main", "kind": "js", "key": "j", "code": "window.A=1", "teardown": "window.A=0"})
        await loader.core("target_add", {"pid": "b", "target": "main", "kind": "js", "key": "j", "code": "window.B=1"})
        n = len(main.exprs())
        self.assertEqual(await loader.core("reload_one", "a"), {"ok": True})
        removal = main.exprs()[n]
        self.assertIn('"a"', removal)
        self.assertNotIn('"b"', removal)
        self.assertEqual({k[0] for k in fy.host.targets.items}, {"b"})
        await loader.core("reload")
        self.assertEqual(fy.host.targets.items, {})

    async def test_extra_target_sessions_exist_only_while_a_registration_exists(self):
        loader = await self.start(prefs={"dev.enabled": True, "dev.targets": {"store": "steamstore"}})
        fy = loader.fy
        extra = H.Page()
        opened = []

        async def tabs():
            return [{"url": "https://x/steamstore/page", "webSocketDebuggerUrl": "ws://x/store"}]

        async def fake_open(cls, url):
            opened.append(url)
            return extra

        fy.tabs = tabs
        fy.WS.open = classmethod(fake_open)
        self.assertEqual(fy.WATCH, {})
        r = await loader.core("target_add", {"pid": "a", "target": "store", "kind": "css", "key": "k", "code": "p{}"})
        self.assertEqual(r, {"ok": True})
        self.assertEqual(list(fy.WATCH), ["store"])
        self.assertTrue(await H.settle(lambda: "store" in fy.LIVE and any("p{}" in e for e in extra.exprs())))
        self.assertFalse(any("window.Framey" in e and "dispose" in e for e in extra.exprs()))
        await loader.core("target_add", {"pid": "a", "target": "store", "kind": "css", "key": "k2", "code": "q{}"})
        self.assertEqual(list(fy.WATCH), ["store"])
        await loader.core("target_remove", {"pid": "a", "target": "store"})
        self.assertEqual(fy.WATCH, {})
        self.assertTrue(await H.settle(lambda: "store" not in fy.LIVE))
        self.assertTrue(extra.closed)

    async def test_without_developer_mode_an_extra_target_is_refused_and_no_session_starts(self):
        loader = await self.start()
        fy = loader.fy
        r = await loader.core("target_add", {"pid": "a", "target": "store", "kind": "css", "key": "k", "code": "p{}"})
        self.assertEqual(r["code"], "unknown_target")
        self.assertEqual(fy.WATCH, {})

    async def test_switching_developer_mode_off_stops_the_extra_sessions(self):
        loader = await self.start(prefs={"dev.enabled": True, "dev.targets": {"store": "steamstore"}})
        fy = loader.fy

        async def tabs():
            return []

        fy.tabs = tabs
        await loader.core("target_add", {"pid": "a", "target": "store", "kind": "css", "key": "k", "code": "p{}"})
        self.assertEqual(list(fy.WATCH), ["store"])
        loader.trust(**{"dev.enabled": False})
        await loader.core("reload")
        self.assertEqual(fy.WATCH, {})


class HotPathTests(Base):
    async def test_an_idle_loader_with_no_worker_adds_no_task_and_no_timer(self):
        loader = await self.start(plugins=())
        fy = loader.fy
        main, _ = await self.attach(loader, "main")
        bar, _ = await self.attach(loader, "bar")
        await asyncio.sleep(0.2)
        loop = asyncio.get_running_loop()
        tasks = set(asyncio.all_tasks())
        timers = sorted(round(h.when(), 1) for h in loop._scheduled if not h.cancelled())
        for i in range(3):
            main.bind(id=0, plugin="_core", method="panel", arg=True)
            main.bind(id=0, plugin="_core", method="panel", arg=False)
            main.bind(id=10 + i, gen="g", plugin="_core", method="list")
            main.bind(id=20 + i, gen="g", plugin="_core", method="state_get")
            main.bind(id=30 + i, gen="g", plugin="_core", method="loader")
            main.bind(id=40 + i, gen="g", plugin="_core", method="store")
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 12))
        await asyncio.sleep(0.5)
        self.assertEqual(set(asyncio.all_tasks()), tasks)
        self.assertEqual(sorted(round(h.when(), 1) for h in loop._scheduled if not h.cancelled()), timers)
        self.assertIsNone(fy.updates)
        self.assertIsNone(fy.supervisor and fy.supervisor.status() or None)
        self.assertEqual(fy.BG, set())
        self.assertEqual(fy.TASKS, set())
        self.assertNotIn("fy_updates", fy.MODS)
        self.assertNotIn("fy_dev", fy.MODS)
        self.assertNotIn("fy_diag", fy.MODS)
        self.assertNotIn("fy_targets", fy.MODS)
        self.assertNotIn("fy_media", fy.MODS)

    async def test_an_idle_worker_costs_one_timer_and_nothing_else(self):
        loader = await self.start(manifest={"backend": {"idle": 30}})
        fy = loader.fy
        main, _ = await self.attach(loader, "main")
        await asyncio.sleep(0.2)
        loop = asyncio.get_running_loop()
        before_timers = len([h for h in loop._scheduled if not h.cancelled()])
        main.bind(id=1, gen="g", plugin="a", method="hi", arg=1)
        self.assertTrue(await H.settle(lambda: main.replies()))
        await asyncio.sleep(0.3)
        self.assertEqual(len([h for h in loop._scheduled if not h.cancelled()]), before_timers + 1)
        self.assertEqual(fy.TASKS, set())
        tasks = set(asyncio.all_tasks())
        await asyncio.sleep(0.6)
        self.assertEqual(set(asyncio.all_tasks()), tasks)

    async def test_the_idle_timer_stops_the_worker_and_leaves_nothing_behind(self):
        loader = await self.start(manifest={"backend": {"idle": 10}})
        fy = loader.fy
        sup = fy.sup()
        await fy.backend("a", "hi", 1)
        self.assertEqual(sup.status()["a"]["state"], "ready")
        handle = sup._ws["a"].timer
        self.assertIsNotNone(handle)
        handle.cancel()
        sup._ws["a"].timer = None
        sup._idle(sup._ws["a"])
        self.assertTrue(await H.settle(lambda: sup.status()["a"]["state"] == "stopped", 10))
        self.assertIsNone(sup.status()["a"]["os_pid"])
        await asyncio.sleep(0.1)
        self.assertEqual(fy.TASKS, set())


class ContainmentTests(Base):
    async def test_a_blocked_backend_gets_a_clear_error_and_the_loader_and_others_keep_responding(self):
        loader = await self.start(plugins=("blk", "other"), manifest={"timeout": 1})
        fy = loader.fy
        main, _ = await self.attach(loader)
        await fy.backend("other", "hi", 0)
        gaps = []

        async def ticker():
            last = time.monotonic()
            while True:
                await asyncio.sleep(0.02)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        tick = asyncio.create_task(ticker())
        self.addCleanup(tick.cancel)
        main.bind(id=1, gen="g.0", plugin="blk", method="block", arg=4)
        await asyncio.sleep(0.3)
        t0 = time.monotonic()
        main.bind(id=2, gen="g.0", plugin="other", method="hi", arg=5)
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 1, 3))
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(main.replies()[0], ["g.0", 2, {"m": "hi", "a": 5}])
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 2, 8))
        err = main.replies()[1][2]
        self.assertEqual(err, {"error": "backend call timed out after 1 s; module not responding"})
        self.assertLess(max(gaps), 0.4)
        self.assertEqual(fy.FAILS["blk"]["what"], "backend")
        rows = {m["id"]: m for m in await loader.core("list")}
        self.assertEqual(rows["blk"]["fail"]["what"], "backend")
        self.assertIsNone(rows["other"]["fail"])

    async def test_failures_are_recorded_cleared_and_stamped_with_the_request_generation(self):
        loader = await self.start(plugins=("a",))
        fy = loader.fy
        (loader.plugins / "a" / "backend.py").write_text("async def call(m, a):\n    if m == 'good':\n        return 'fine'\n    raise RuntimeError('kaput ' + str(a))\n")
        fy.load_backends()
        main, _ = await self.attach(loader)
        main.bind(id=1, gen="g.0", plugin="a", method="bad", arg=9)
        self.assertTrue(await H.settle(lambda: main.replies()))
        self.assertEqual(main.replies()[0], ["g.0", 1, {"error": "kaput 9"}])
        self.assertEqual(fy.FAILS["a"]["msg"], "kaput 9")
        self.assertLess(abs(fy.FAILS["a"]["at"] - time.time()), 30)
        main.bind(id=2, gen="g.1", plugin="a", method="good", arg=0)
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 2))
        self.assertEqual(main.replies()[1], ["g.1", 2, "fine"])
        self.assertNotIn("a", fy.FAILS)
        main.bind(id=3, gen="g.1", plugin="ghost", method="x", t=fy.CORE_TOK)
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 3))
        self.assertEqual(main.replies()[2], ["g.1", 3, {"error": "backend not loaded"}])
        main.bind(id=0, plugin="a", method="good")
        main.bind(plugin="a", method="good", gen="g", arg=1)
        await asyncio.sleep(0.3)
        self.assertEqual(len(main.replies()), 3)

    async def test_render_and_load_failures_reported_by_the_page_are_recorded_and_a_fresh_injection_clears_them(self):
        loader = await self.start(plugins=("a",))
        fy = loader.fy
        main, _ = await self.attach(loader)
        main.bind(id=0, plugin="_core", method="fail", arg={"id": "a", "what": "render", "msg": "oops"})
        main.bind(id=0, plugin="_core", method="fail", arg={"id": "../x", "what": "render", "msg": "no"})
        main.bind(id=0, plugin="_core", method="fail", arg={"id": "a", "what": "other", "msg": "no"})
        main.bind(id=0, plugin="_core", method="fail", arg="junk")
        self.assertTrue(await H.settle(lambda: "a" in fy.FAILS))
        await asyncio.sleep(0.2)
        self.assertEqual(list(fy.FAILS), ["a"])
        self.assertEqual(fy.FAILS["a"]["what"], "render")
        fy.main_code()
        self.assertEqual(fy.FAILS, {})

    async def test_a_plugin_whose_javascript_throws_is_recorded_and_the_next_one_still_loads(self):
        loader = await self.start(plugins=("a", "b"))
        fy = loader.fy

        class Throwing(H.Page):
            async def send(self, obj):
                self.sent.append(obj)
                e = obj["params"].get("expression", "") if obj["method"] == "Runtime.evaluate" else ""
                if obj["method"] == "Runtime.evaluate" and obj["params"].get("returnByValue"):
                    self.q.put_nowait(json.dumps({"id": obj["id"], "result": {"result": {"value": "undefined"}}}))
                if e == "BADJS":
                    self.q.put_nowait(json.dumps({"id": obj["id"], "result": {"exceptionDetails": {"text": "Uncaught", "exception": {"description": "SyntaxError: Unexpected token\n    at x"}}}}))

        page = Throwing()

        async def fake_open(cls, url):
            return page

        fy.WS.open = classmethod(fake_open)
        task = asyncio.create_task(fy.session("main", {"webSocketDebuggerUrl": "ws://x/m"}, lambda: [("GOODJS", "a"), ("BADJS", "b"), ("MORE", None)], "typeof window.Framey"))
        self.addCleanup(task.cancel)
        self.assertTrue(await H.settle(lambda: "MORE" in page.exprs()))
        self.assertEqual(list(fy.FAILS), ["b"])
        self.assertEqual(fy.FAILS["b"]["what"], "load")
        self.assertIn("SyntaxError", fy.FAILS["b"]["msg"])
        self.assertTrue(any(e.startswith('Framey.fail("b",') for e in page.exprs()))

    async def test_one_call_at_a_time_per_module_and_different_modules_in_parallel(self):
        loader = await self.start(plugins=("a", "b"))
        fy = loader.fy
        await asyncio.gather(fy.backend("a", "hi", 0), fy.backend("b", "hi", 0))
        t0 = time.monotonic()
        await asyncio.gather(fy.backend("a", "sleep", 0.4), fy.backend("a", "sleep", 0.4))
        serial = time.monotonic() - t0
        t0 = time.monotonic()
        await asyncio.gather(fy.backend("a", "sleep", 0.4), fy.backend("b", "sleep", 0.4))
        parallel = time.monotonic() - t0
        self.assertGreaterEqual(serial, 0.75)
        self.assertLess(parallel, serial - 0.2)

    async def test_more_than_32_calls_in_flight_are_answered_busy(self):
        loader = await self.start(plugins=("a",))
        fy = loader.fy
        main, _ = await self.attach(loader)
        for i in range(1, 41):
            main.bind(id=i, gen="g", plugin="a", method="sleep", arg=0.5)
        self.assertTrue(await H.settle(lambda: len(main.replies()) >= 8, 3))
        busy = [r for r in main.replies() if isinstance(r[2], dict) and r[2].get("error") == "busy"]
        self.assertGreaterEqual(len(busy), 8)

    async def test_reload_one_disposes_the_old_instance_and_evaluates_only_that_plugin(self):
        loader = await self.start(plugins=("a", "b"))
        fy = loader.fy
        main, _ = await self.attach(loader)
        fy.mark("a", "backend", "kaput")
        n = len(main.sent)
        self.assertEqual(await loader.core("reload_one", "a"), {"ok": True})
        sent = main.trace(n)
        self.assertEqual(sent[0], ("dispose", ["a"]))
        self.assertEqual(sent[1], "(function(Framey){window.RAN_a=1\n})")
        self.assertNotIn("RAN_b", sent[1])
        self.assertNotIn(fy.CORE_TOK, repr(sent))
        self.assertNotIn("fyCall", sent[1])
        self.assertEqual(sent[2], "window.Framey&&Framey.redraw()")
        self.assertNotIn("a", fy.FAILS)
        self.assertEqual(len(sent), 3)

    async def test_the_loader_reply_flow_refreshes_the_page_after_a_change(self):
        loader = await self.start(plugins=("a",))
        main, _ = await self.attach(loader)
        n = len(main.sent)
        main.bind(id=3, gen="G", plugin="_core", method="reload")
        self.assertTrue(await H.settle(lambda: len(main.trace(n)) > 4))
        seq = [x if isinstance(x, str) else x for x in main.trace(n)]
        self.assertEqual(seq[0][0], "reply")
        self.assertEqual(seq[0][1][:2], ["G", 3])
        self.assertIn("ok", seq[0][1][2])
        self.assertEqual(seq[1], ("dispose", [None]))
        self.assertTrue(seq[2].endswith("delete window.Framey"))
        self.assertEqual(seq[3], "typeof window.Framey")
        self.assertTrue(seq[4].startswith("(function(__fyt){window.FY_ICON_URL="))
        n1 = len(main.sent)
        main.bind(id=4, gen="G", plugin="_core", method="disable", arg="nope")
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 2))
        await asyncio.sleep(0.2)
        self.assertEqual([x for x in main.trace(n1) if isinstance(x, tuple)], [("reply", ["G", 4, {"error": "unknown plugin"}])])

    async def test_hostile_page_input_never_stops_the_session(self):
        loader = await self.start(plugins=("a",))
        main, task = await self.attach(loader)
        main.q.put_nowait("{ not json")
        main.bind(id=1)
        main.q.put_nowait(json.dumps({"method": "Runtime.bindingCalled", "params": {"payload": "not json"}}))
        main.q.put_nowait(json.dumps({"method": "Runtime.bindingCalled", "params": {"payload": json.dumps([1, 2])}}))
        main.bind(id=7, gen="g", plugin=["x"], method="m")
        main.bind(id=8, gen="g", plugin="_core", method={"a": 1})
        main.bind(id=9, gen="g", plugin="_core", method="uninstall", arg={"id": {"x": 1}})
        self.assertTrue(await H.settle(lambda: len(main.replies()) == 3))
        self.assertFalse(task.done())
        got = {r[1]: r for r in main.replies()}
        self.assertIn("error", got[7][2])
        self.assertIn("error", got[8][2])
        self.assertIn("unknown plugin", got[9][2]["error"])


if __name__ == "__main__":
    unittest.main()
