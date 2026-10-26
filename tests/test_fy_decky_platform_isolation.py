import asyncio
import json
import pathlib
import sys
import tempfile
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import fy_decky_catalog
import jharness as H


class DelayedCorePage(H.WorldPage):
    def __init__(self):
        super().__init__()
        self.delayed_core = None
        self.hold_core = True

    async def send(self, obj):
        params = obj["params"]
        if obj["method"] == "Runtime.evaluate" and params.get("expression", "").startswith("(function(__fyt){"):
            self.sent.append(obj)
            self.calls.append((obj["method"], params))
            result = self.m_Runtime_evaluate(params)
            result["result"]["objectId"] = "core-" + str(params.get("contextId"))
            reply = {"id": obj["id"], "result": result}
            if self.hold_core:
                self.hold_core = False
                self.delayed_core = reply
            else:
                self.q.put_nowait(json.dumps(reply))
            return
        await super().send(obj)


class StoreSessionOwnership(unittest.IsolatedAsyncioTestCase):
    async def boot(self, page=None):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.trust(isolation=True)
        page = page or H.WorldPage()
        _, task = await H.attach(loader, page=page)
        self.addCleanup(task.cancel)
        self.assertTrue(await H.settle(lambda: page.has("Framey"), limit=3))
        return loader, page, task

    def emit(self, page, fy, request_id, method, context, **changes):
        request = {"id": request_id, "gen": "store-page", "plugin": "_core", "method": method,
                   "arg": {"source": "decky_stable", "id": "decky-1"}, "t": fy.CORE_TOK}
        request.update(changes.pop("request", {}))
        params = {"name": "fyCall", "executionContextId": context, "payload": json.dumps(request)}
        params.update(changes)
        page.emit("Runtime.bindingCalled", **params)

    async def test_store_methods_require_binding_context_and_core_capability(self):
        loader, page, task = await self.boot()
        fy, calls = loader.fy, []
        async def core(method, arg):
            calls.append((method, arg))
            return {"rows": []}
        fy.core = core
        fy.TOKS["plugin-capability"] = "a"
        owned = page.iso()
        foreign = page.context("framey")
        page.created(foreign)
        for index, method in enumerate(("catalog_list", "catalog_details", "catalog_assess")):
            self.emit(page, fy, 10 + index, method, page.main.id)
            self.emit(page, fy, 20 + index, method, foreign.id)
            self.emit(page, fy, 30 + index, method, owned.id, name="otherBinding")
            self.emit(page, fy, 40 + index, method, str(owned.id))
            self.emit(page, fy, 50 + index, method, owned.id, request={"t": "plugin-capability"})
        await asyncio.sleep(0.1)
        self.assertFalse(task.done())
        self.assertEqual(calls, [])
        self.assertTrue(all(reply[2] == fy.NOAUTH for reply in page.replies()))
        for index, method in enumerate(("catalog_list", "catalog_details", "catalog_assess")):
            self.emit(page, fy, 60 + index, method, owned.id)
        self.assertTrue(await H.settle(lambda: len(calls) == 3))
        self.assertEqual([method for method, _ in calls], ["catalog_list", "catalog_details", "catalog_assess"])

    async def test_metadata_is_a_reply_value_without_main_world_evaluation(self):
        loader, page, _ = await self.boot()
        fy = loader.fy
        marker = '"});window.STORE_METADATA_EXECUTED=true;//'
        metadata = {"entry": {"id": "decky-1", "name": marker, "description": "<img src=x onerror=bad()>",
                              "format": "decky", "read_only": True, "architecture": "unknown"}}
        async def core(method, arg):
            return metadata
        fy.core = core
        before = len(page.evaluates())
        self.emit(page, fy, 70, "catalog_details", page.iso().id)
        self.assertTrue(await H.settle(lambda: any(reply[1] == 70 for reply in page.replies())))
        reply = next(reply for reply in page.replies() if reply[1] == 70)
        self.assertEqual(reply[2], metadata)
        self.assertFalse(any(marker in params["expression"] for params in page.evaluates()[before:]))
        self.assertFalse(any("contextId" not in params for params in page.evaluates()[before:]))

    async def test_old_core_reply_cannot_replace_new_store_reply_destination(self):
        loader, page, _ = await self.boot(DelayedCorePage())
        fy = loader.fy
        self.assertIsNotNone(page.delayed_core)
        old_context = page.iso().id
        page.navigate()
        await asyncio.sleep(0.05)
        await fy.LIVE["main"].ping()
        self.assertTrue(await H.settle(lambda: page.iso() and page.iso().id != old_context and page.has("Framey")))
        expected = "core-" + str(page.iso().id)
        self.assertTrue(await H.settle(lambda: fy.LIVE["main"].core == expected))
        page.q.put_nowait(json.dumps(page.delayed_core))
        await asyncio.sleep(0.05)
        async def core(method, arg):
            return {"rows": [], "format": "decky", "read_only": True}
        fy.core = core
        self.emit(page, fy, 80, "catalog_list", page.iso().id)
        self.assertTrue(await H.settle(lambda: any(reply[1] == 80 for reply in page.replies())))
        response = next(obj for obj in reversed(page.sent) if obj["method"] == "Runtime.callFunctionOn" and
                        obj["params"].get("functionDeclaration") == fy.REPLY)
        self.assertEqual(response["params"]["objectId"], expected)


class StoreMetadataProjection(unittest.IsolatedAsyncioTestCase):
    async def test_inspection_route_rejects_unconfirmed_requests_before_network(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        result = await loader.core("catalog_assess", {"source": "decky_stable", "id": "decky-1", "confirm": False})
        self.assertEqual(result.get("code"), "confirmation_required", result)
        self.assertEqual(loader.net.calls, [])

    async def test_unknown_upstream_claims_never_establish_runtime_or_arm64_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = pathlib.Path(temporary)
            host = types.SimpleNamespace(HOME=home, CONF=home / "config" / "settings.json", PLATFORM="linux-aarch64",
                                         settings=lambda: {}, now=lambda: 1800000000)
            feed = [{"id": 1, "name": "Metadata only", "author": "Example", "description": "Unknown runtime",
                     "tags": ["utility"], "visible": True, "versions": [{"name": "1.0.0", "hash": "a" * 64}],
                     "compatible": True, "architecture": "aarch64", "platform": ["linux-aarch64"],
                     "runtime_verified": True, "eligible": True, "read_only": False,
                     "frontend": "window.EXECUTE_UPSTREAM=1", "backend": "raise AssertionError('execute')"}]
            async def fetch(url, max_bytes):
                return json.dumps(feed).encode()
            async def no_archive(*args):
                self.fail("Metadata browsing must not fetch an archive")
            catalog = fy_decky_catalog.DeckyCatalog(host, fetcher=fetch, archive_fetcher=no_archive)
            listing = await catalog.list({"source": "decky_stable"})
            self.assertNotIn("code", listing, listing)
            details = await catalog.details({"source": "decky_stable", "id": "decky-1"})
            for row in (listing["rows"][0], details["entry"]):
                self.assertTrue(row["read_only"])
                self.assertFalse(row["eligible"])
                self.assertFalse(row["platform_declared"])
                self.assertEqual(row["architecture"], "unknown")
                self.assertEqual(row["compatibility"]["status"], "incompatible")
                self.assertFalse(row["compatibility"]["runtime_verified"])
                self.assertNotIn("frontend", row)
                self.assertNotIn("backend", row)


if __name__ == "__main__":
    unittest.main()
