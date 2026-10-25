import asyncio
import json
import pathlib
import sys
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H

SHA = "b" * 40
OLD = "1" * 40
SOURCE = "https://github.com/o/%s"


def iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))


def route(loader, repo, days_ago=10, archived=False):
    loader.net.json("o/%s" % repo, {"full_name": "o/" + repo, "archived": archived, "fork": False, "default_branch": "main"})
    loader.net.json("o/%s/commits?sha=main&per_page=1" % repo, [{"sha": SHA, "commit": {"committer": {"date": iso(days_ago)}, "author": {"date": iso(days_ago)}}}])
    loader.net.commit("o", repo, "HEAD", SHA)


def maint_calls(loader):
    return [u for u in loader.net.calls if "/repos/o/" in u and ("/commits?sha=" in u or u.endswith("/releases/latest"))]


class Base(unittest.IsolatedAsyncioTestCase):
    async def start(self, plugins=("a",), zip_plugins=()):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        for pid in plugins:
            loader.plugin(pid, {"api": 1, "version": "1.0.0", "source": SOURCE % pid}, extra={".version": OLD, ".source": SOURCE % pid})
            route(loader, pid)
        for pid in zip_plugins:
            loader.plugin(pid, {"api": 1, "version": "1.0.0"})
        return loader

    async def attach(self, loader, name="main"):
        page, task = await H.attach(loader, name)
        self.addCleanup(task.cancel)
        return page

    async def call(self, page, **req):
        n = len(page.replies())
        page.bind(**req)
        self.assertTrue(await H.settle(lambda: len(page.replies()) > n))
        return page.replies()[-1][2]

    async def idle(self, loader):
        self.assertTrue(await H.settle(lambda: not loader.fy.BG))

    def maint_events(self, page):
        return [e for e in page.events() if e[1] == "maint"]


class AuthorizationTests(Base):
    async def test_maint_methods_need_the_core_token(self):
        loader = await self.start()
        fy = loader.fy
        page = await self.attach(loader)
        plugin_tok = next(t for t, p in fy.TOKS.items() if p == "a")
        for method, arg in (("maint_get", {"id": "a"}), ("maint_refresh", {"id": "a"}), ("maint_prefs", {"maint.enabled_background": True})):
            for i, t in enumerate((None, plugin_tok, "x" * 32, 5)):
                r = await self.call(page, id=i + 1, gen="g", plugin="_core", method=method, arg=arg, t=t)
                self.assertEqual(r, {"error": "not authorized"}, (method, t))
        self.assertEqual(loader.net.calls, [])
        self.assertEqual(fy.use("fy_maint").load(fy.host)["prefs"]["enabled_background"], False)
        r = await self.call(page, id=20, gen="g", plugin="_core", method="maint_get", arg={"id": "a"}, t=fy.CORE_TOK)
        self.assertEqual((r["id"], r["label"]), ("a", "unknown"))
        r = await self.call(page, id=21, gen="g", plugin="_core", method="maint_prefs", arg=None, t=fy.CORE_TOK)
        self.assertEqual(r, {"maint.enabled_background": False, "maint.window_days": 365})

    async def test_a_plugin_backend_call_named_like_a_maint_method_never_reaches_core(self):
        loader = await self.start()
        page = await self.attach(loader)
        r = await self.call(page, id=1, gen="g", plugin="a", method="maint_refresh", arg={"id": "all"}, t=None)
        self.assertIn("error", r)
        self.assertEqual(loader.net.calls, [])


class MethodTests(Base):
    async def test_the_three_methods_are_merged_and_work_through_core(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual({k for k, v in fy.OWNER.items() if v == "fy_maint"}, {"maint_get", "maint_refresh", "maint_prefs"})
        self.assertEqual(await loader.core("maint_prefs", {"maint.enabled_background": True, "maint.window_days": 90}), {"maint.enabled_background": True, "maint.window_days": 90})
        self.assertEqual((await loader.core("maint_prefs", {"x": 1}))["code"], "bad_key")
        self.assertEqual((await loader.core("maint_get", {"id": "zzz"}))["code"], "unknown_plugin")
        r = await loader.core("maint_refresh", {"id": "a"})
        self.assertEqual((r["label"], r["scope"], r["repo"], r["window_days"]), ("recent", "whole repository", "o/a", 90))
        self.assertFalse(r["stale"])
        again = await loader.core("maint_get", {"id": "a"})
        self.assertEqual(again, r)
        self.assertEqual(len(maint_calls(loader)), 2)
        both = await loader.core("maint_refresh", {"id": "all"})
        self.assertEqual(sorted(both["records"]), ["a"])

    async def test_a_missing_module_degrades_the_methods_and_nothing_else(self):
        with mock.patch.dict(sys.modules, {"fy_maint": None}):
            loader = await self.start()
            fy = loader.fy
            for method in ("maint_get", "maint_refresh", "maint_prefs"):
                self.assertEqual(await loader.core(method, {"id": "a"}), {"error": "fy_maint unavailable"})
            page = await self.attach(loader)
            checked = await loader.core("check")
            self.assertEqual(checked["checked"]["a"]["state"], "update")
            await self.idle(loader)
            self.assertEqual(maint_calls(loader), [])
            self.assertEqual(self.maint_events(page), [])
            self.assertEqual([m["id"] for m in await loader.core("list")], ["a"])
            self.assertIn("fy_maint unavailable", loader.errors.getvalue())
            await fy.upd()._check_all(fy.now())
            await self.idle(loader)


class PiggybackTests(Base):
    async def test_a_manual_check_also_refreshes_maintenance_for_plugins_with_a_github_source(self):
        loader = await self.start(("a", "b"), zip_plugins=("z",))
        page = await self.attach(loader)
        r = await loader.core("check")
        self.assertEqual(sorted(r["checked"]), ["a", "b", "z"])
        self.assertTrue(await H.settle(lambda: self.maint_events(page)))
        self.assertEqual(self.maint_events(page)[-1], ["_core", "maint", {"ids": ["a", "b"]}, 0])
        self.assertTrue(all("/o/a" in u or "/o/b" in u for u in maint_calls(loader)))
        self.assertEqual(len(maint_calls(loader)), 4)
        for pid in ("a", "b"):
            rec = await loader.core("maint_get", {"id": pid})
            self.assertEqual((rec["label"], rec["stale"]), ("recent", False))
        z = await loader.core("maint_get", {"id": "z"})
        self.assertEqual((z["label"], z["checked_at"]), ("unknown", 0))
        await loader.core("check")
        await self.idle(loader)
        self.assertEqual(len(maint_calls(loader)), 4)

    async def test_the_check_reply_does_not_wait_for_the_maintenance_run(self):
        loader = await self.start()
        fy = loader.fy
        gate = asyncio.Event()
        real = fy.use("fy_maint").check_all

        async def slow(host, plugins, now=None, force=False):
            await gate.wait()
            return await real(host, plugins, now, force)

        with mock.patch.object(fy.use("fy_maint"), "check_all", slow):
            r = await asyncio.wait_for(loader.core("check"), 5)
            self.assertIn("a", r["checked"])
            self.assertTrue(fy.BG)
            gate.set()
            await self.idle(loader)
        self.assertEqual((await loader.core("maint_get", {"id": "a"}))["label"], "recent")

    async def test_a_failing_maintenance_run_never_breaks_the_check(self):
        loader = await self.start()
        fy = loader.fy

        async def boom(host, plugins, now=None, force=False):
            raise RuntimeError("maint exploded")

        with mock.patch.object(fy.use("fy_maint"), "check_all", boom):
            r = await loader.core("check")
            await self.idle(loader)
        self.assertEqual(r["checked"]["a"]["state"], "update")
        self.assertIn("maintenance check failed: maint exploded", loader.errors.getvalue())

    async def test_a_rate_limited_github_leaves_the_update_check_alone_and_is_shown_as_unknown(self):
        loader = await self.start()
        loader.net.routes["https://api.github.com/repos/o/a"] = H.http_error("u", 403, X_RateLimit_Remaining="0")
        r = await loader.core("check")
        await self.idle(loader)
        self.assertEqual(r["checked"]["a"]["state"], "update")
        rec = await loader.core("maint_get", {"id": "a"})
        self.assertEqual(rec["label"], "unknown")
        self.assertIn("rate limit", rec["failure"]["reason"])

    async def test_maintenance_status_is_informational_and_never_gates_an_update(self):
        loader = await self.start()
        fy = loader.fy
        route(loader, "a", days_ago=2000, archived=True)
        manifest = json.dumps({"id": "a", "name": "A", "version": "1.1.0", "short": "s", "api": 1, "source": SOURCE % "a"})
        loader.net.archive("o", "a", SHA, H.mkzip({"plugin.json": manifest, "main.js": "NEW"}, top="a-" + SHA))
        loader.net.json("o/a/contents/plugin.json?ref=" + SHA, {"content": __import__("base64").b64encode(manifest.encode()).decode()})
        rec = await loader.core("maint_refresh", {"id": "a"})
        self.assertEqual(rec["label"], "archived")
        plan = await loader.core("batch_plan", {"ids": ["a"]})
        self.assertTrue(plan["items"][0]["eligible"])
        self.assertNotIn("archived", json.dumps(plan))
        self.assertNotIn("maint", json.dumps((await loader.core("list"))[0]))
        self.assertEqual(await loader.core("update", "a"), {"ok": "a", "version": "1.1.0"})
        self.assertEqual((await loader.core("maint_get", {"id": "a"}))["label"], "archived")
        self.assertTrue((loader.plugins / "a").is_dir())
        self.assertEqual(fy.settings()["disabled"], [])


class BackgroundTests(Base):
    async def cycle(self, loader):
        fy = loader.fy
        await fy.upd()._check_all(fy.now())
        await self.idle(loader)

    async def test_the_existing_auto_check_consults_the_background_flag_and_no_timer_is_added(self):
        loader = await self.start()
        fy = loader.fy
        up = fy.upd()
        self.assertIsNotNone(up.cycled)
        await self.cycle(loader)
        self.assertEqual(maint_calls(loader), [])
        self.assertEqual(fy.use("fy_maint").load(fy.host)["bg_next"], 0)
        await loader.core("maint_prefs", {"maint.enabled_background": True})
        await self.cycle(loader)
        self.assertEqual(len(maint_calls(loader)), 2)
        nxt = fy.use("fy_maint").load(fy.host)["bg_next"]
        week = 7 * 86400
        self.assertTrue(fy.now() + week - 5 <= nxt <= fy.now() + week + 86400 + 5)
        await self.cycle(loader)
        self.assertEqual(len(maint_calls(loader)), 2)
        self.assertIsNone(up._timer)
        source = (HERE.parent / "fy_maint.py").read_text()
        self.assertNotIn("call_later", source)
        self.assertNotIn("call_at", source)

    async def test_turning_the_flag_off_again_stops_background_runs(self):
        loader = await self.start()
        await loader.core("maint_prefs", {"maint.enabled_background": True})
        await loader.core("maint_prefs", {"maint.enabled_background": False})
        await self.cycle(loader)
        self.assertEqual(maint_calls(loader), [])

    async def test_the_background_run_tells_the_page(self):
        loader = await self.start()
        page = await self.attach(loader)
        await loader.core("maint_prefs", {"maint.enabled_background": True})
        await self.cycle(loader)
        self.assertEqual(len(self.maint_events(page)), 1)

    async def test_a_disabled_background_run_sends_no_event_and_no_request(self):
        loader = await self.start()
        page = await self.attach(loader)
        await self.cycle(loader)
        self.assertEqual((self.maint_events(page), maint_calls(loader)), ([], []))


if __name__ == "__main__":
    unittest.main()
