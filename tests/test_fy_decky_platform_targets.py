import asyncio
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import fy_targets as ft
import jharness as H
from test_fy_targets import FakeHost


class CatalogTargetBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_routes_and_transport_objects_are_not_injection_targets(self):
        host = FakeHost()
        for selected in ("store", "discover", "decky-store", "decky_store", "#discover", "#decky-store",
                         "https://catalog.invalid", {"target": "main", "frameId": "child"}, ["main"]):
            with self.subTest(selected=selected):
                result = await ft.target_add(host, {"pid": "p", "target": selected, "kind": "css", "key": "k", "code": "x"})
                self.assertEqual(result["code"], "unknown_target")
        self.assertEqual(host.sent, [])
        self.assertEqual(ft.get(host).items, {})
        self.assertEqual(ft.get(host).wanted(), [])

    async def test_transport_metadata_cannot_redirect_a_known_target(self):
        host = FakeHost()
        args = {"pid": "p", "target": "main", "kind": "css", "key": "k", "code": "x",
                "frameId": "foreign-frame", "contextId": 999, "executionContextId": 999,
                "webSocketDebuggerUrl": "ws://catalog.invalid/arbitrary", "url": "https://catalog.invalid"}
        self.assertEqual(await ft.target_add(host, args), {"ok": True})
        self.assertEqual(len(host.sent), 1)
        self.assertEqual(host.sent[0][0], "main")
        for value in ("foreign-frame", "catalog.invalid", "999"):
            self.assertNotIn(value, host.sent[0][1])

    async def test_global_cleanup_does_not_interleave_plugin_replacements(self):
        host = FakeHost()
        targets = ft.Targets(host)
        await targets.add_css("a", "main", "k", "old-a")
        await targets.add_css("b", "main", "k", "old-b")
        host.sent.clear()
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(target, expression):
            if not entered.is_set():
                entered.set()
                await release.wait()
            host.sent.append((target, expression))
        host.send = send
        cleanup = asyncio.create_task(targets.remove_all())
        await asyncio.wait_for(entered.wait(), 1)
        replace_a = asyncio.create_task(targets.add_css("a", "main", "k", "new-a"))
        await asyncio.sleep(0)
        replace_b = asyncio.create_task(targets.add_css("b", "main", "k", "new-b"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(cleanup, replace_a, replace_b), 1)
        self.assertEqual({slot[0] for slot in targets.items}, {"a", "b"})
        self.assertEqual(len(host.sent), 3)
        self.assertIn(ft.remove_expr("css", "a", "k", "main"), host.sent[0][1])
        self.assertIn(ft.remove_expr("css", "b", "k", "main"), host.sent[0][1])
        self.assertIn('"new-a"', host.sent[1][1])
        self.assertIn('"new-b"', host.sent[2][1])

    async def test_source_tab_churn_keeps_bounded_items_and_cleanup_is_final(self):
        host = FakeHost()
        targets = ft.Targets(host)
        async def source_visit(source):
            await targets.add_js("p", "main", "catalog-view", "globalThis.view=" + json.dumps(source), "globalThis.view=null")
            await targets.reconcile("main")
        tasks = [source_visit(source) for source in ("stable", "decky_stable", "testing", "decky_testing") * 4]
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        self.assertEqual(len(targets.items), 1)
        self.assertLessEqual(sum(value[2] for value in targets.items.values()), ft.MAX_ITEM)
        await asyncio.wait_for(targets.remove_plugin("p"), 1)
        await asyncio.wait_for(targets.reconcile("main"), 1)
        self.assertEqual(targets.items, {})
        self.assertEqual(host.sent[-1], ("main", targets.reconcile_expr("main")))
        self.assertNotIn("globalThis.view=", host.sent[-1][1])


class CatalogTargetSessionBoundary(unittest.IsolatedAsyncioTestCase):
    async def loader(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        return loader

    async def session(self, loader, name, page, code, probe, marker=None, tab=None):
        fy = loader.fy
        async def open_page(cls, url):
            return page
        fy.WS.open = classmethod(open_page)
        task = asyncio.create_task(fy.session(name, tab or {"webSocketDebuggerUrl": "ws://fixture/" + name}, code, probe, marker))
        async def stop():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(stop)
        return task

    async def test_catalog_metadata_cannot_override_authenticated_plugin_owner(self):
        loader = await self.loader()
        fy = loader.fy
        token = fy.new_tok("owner")
        forged = {"pid": "other", "id": "other", "target": "main", "kind": "css", "key": "k", "code": "x",
                  "source": "decky_stable", "frameId": "foreign", "webSocketDebuggerUrl": "ws://catalog.invalid"}
        allowed, args = fy.authorize({"method": "target_add", "t": token, "arg": forged})
        self.assertTrue(allowed)
        self.assertEqual((args["pid"], args["id"]), ("owner", "owner"))
        self.assertEqual(await fy.use("fy_targets").target_add(fy.host, args), {"ok": True})
        self.assertEqual({slot[0] for slot in fy.host.targets.items}, {"owner"})
        self.assertFalse(fy.authorize({"method": "target_add", "t": fy.CORE_TOK, "arg": forged})[0])
        self.assertFalse(fy.authorize({"method": "target_add", "arg": forged})[0])

    async def test_custom_tab_url_is_validated_before_initial_reconciliation(self):
        loader = await self.loader()
        loader.trust(**{"dev.enabled": True, "dev.targets": {"catalog_view": "trusted-match"}})
        fy = loader.fy
        targets = fy.use("fy_targets").get(fy.host)
        await targets.add_css("p", "catalog_view", "k", "sensitive-marker")
        selected = {"id": "selected-tab", "url": "https://fixture/trusted-match", "webSocketDebuggerUrl": "ws://fixture/catalog_view"}
        async def tabs():
            return [{**selected, "url": "https://fixture/unmatched"}]
        fy.tabs = tabs
        page = H.Page(probe="object")
        await self.session(loader, "catalog_view", page, lambda: targets.replay("catalog_view"),
                           "typeof window.__framey_targets", lambda url: "trusted-match" in url, selected)
        self.assertTrue(await H.settle(lambda: page.closed, limit=1))
        self.assertEqual(page.exprs(), [])
        self.assertFalse(any(sent["method"] == "Runtime.addBinding" for sent in page.sent))

    async def test_custom_target_push_revalidates_url_before_next_probe(self):
        loader = await self.loader()
        loader.trust(**{"dev.enabled": True, "dev.targets": {"catalog_view": "trusted-match"}})
        fy = loader.fy
        targets = fy.use("fy_targets").get(fy.host)
        selected = {"id": "selected-tab", "url": "https://fixture/trusted-match", "webSocketDebuggerUrl": "ws://fixture/catalog_view"}
        current = dict(selected)
        async def tabs():
            return [dict(current)]
        fy.tabs = tabs
        page = H.Page(probe="object")
        await self.session(loader, "catalog_view", page, lambda: targets.replay("catalog_view"),
                           "typeof window.__framey_targets", lambda url: "trusted-match" in url, selected)
        self.assertTrue(await H.settle(lambda: any(expression == "typeof window.__framey_targets" for expression in page.exprs()), limit=1))
        current["url"] = "https://fixture/unmatched"
        after_navigation = len(page.exprs())
        await targets.add_css("p", "catalog_view", "k", "late-sensitive-marker")
        self.assertEqual(page.exprs()[after_navigation:], [])
        self.assertTrue(page.closed)

    async def test_removed_main_target_does_not_reappear_after_delayed_core_reply(self):
        loader = await self.loader()
        fy = loader.fy
        targets = fy.use("fy_targets").get(fy.host)
        await targets.add_js("p", "main", "k", "globalThis.stale_replay_marker=1", "globalThis.stale_replay_marker=0")
        class DelayedCore(H.Page):
            def __init__(self):
                super().__init__()
                self.core_request = None
            async def send(self, message):
                params = message["params"]
                if message["method"] == "Runtime.evaluate" and params["expression"].startswith("(function(__fyt){"):
                    self.sent.append(message)
                    self.core_request = message["id"]
                else:
                    await super().send(message)
        page = DelayedCore()
        await self.session(loader, "main", page, fy.main_code, "typeof window.Framey")
        self.assertTrue(await H.settle(lambda: page.core_request is not None, limit=1))
        await targets.remove("p")
        after_removal = len(page.exprs())
        page.q.put_nowait(json.dumps({"id": page.core_request, "result": {"result": {"objectId": "core"}}}))
        self.assertTrue(await H.settle(lambda: any("Framey.restore()" in expression for expression in page.exprs()[after_removal:]), limit=1))
        self.assertFalse(any("stale_replay_marker" in expression for expression in page.exprs()[after_removal:]))
        self.assertEqual(targets.items, {})


if __name__ == "__main__":
    unittest.main()
