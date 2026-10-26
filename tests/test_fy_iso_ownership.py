import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fy_iso


class OwnershipTests(unittest.IsolatedAsyncioTestCase):
    def iso(self, rpc=None):
        async def default(method, **params):
            return {}
        logs = []
        iso = fy_iso.Iso(rpc or default, lambda *parts: logs.append(parts))
        iso.on, iso.frame, iso.ctx = True, "main", 10
        return iso, logs

    def created(self, ctx, frame="main"):
        return {"method": "Runtime.executionContextCreated", "params": {
            "context": {"name": fy_iso.WORLD, "id": ctx, "auxData": {"frameId": frame}}}}

    async def test_matching_world_events_cannot_replace_the_owned_context(self):
        iso, _ = self.iso()
        iso.injected, iso.stale = True, 1
        for frame in ("main", "child"):
            iso.event(self.created(20, frame))
        self.assertEqual(iso.ctx, 10)
        self.assertTrue(iso.injected)
        self.assertEqual(iso.stale, 1)
        iso.event({"method": "Runtime.executionContextsCleared"})
        iso.event(self.created(20))
        self.assertIsNone(iso.ctx)

    async def test_malformed_events_and_child_navigation_preserve_ownership(self):
        iso, _ = self.iso()
        for message in (None, [], {"params": []},
                        {"method": "Page.frameNavigated", "params": {"frame": []}},
                        {"method": "Page.frameNavigated", "params": {"frame": {"id": 0}}},
                        {"method": "Page.frameNavigated", "params": {"frame": {"id": "child", "parentId": "main"}}},
                        {"method": "Runtime.executionContextDestroyed", "params": {"executionContextId": "10"}}):
            iso.event(message)
            self.assertEqual((iso.frame, iso.ctx), ("main", 10), message)
        iso.ctx = 1
        iso.event({"method": "Runtime.executionContextDestroyed", "params": {"executionContextId": True}})
        self.assertEqual(iso.ctx, 1)

    async def test_destroying_owned_context_advances_generation_for_reused_ids(self):
        iso, _ = self.iso()
        epoch = iso.epoch
        iso.event({"method": "Runtime.executionContextDestroyed", "params": {"executionContextId": 20}})
        self.assertEqual(iso.epoch, epoch)
        iso.event({"method": "Runtime.executionContextDestroyed", "params": {"executionContextId": 10}})
        self.assertGreater(iso.epoch, epoch)
        self.assertIsNone(iso.ctx)

    async def test_late_creation_cannot_restore_a_navigated_or_stopped_context(self):
        for transition in ("clear", "navigate", "destroy", "stop"):
            entered, release = asyncio.Event(), asyncio.Event()
            async def rpc(method, **params):
                if method == "Page.createIsolatedWorld":
                    entered.set()
                    await release.wait()
                    return {"executionContextId": 20}
                return {}
            iso, _ = self.iso(rpc)
            task = asyncio.create_task(iso.make())
            await entered.wait()
            if transition == "clear":
                iso.event({"method": "Runtime.executionContextsCleared"})
            elif transition == "navigate":
                iso.event({"method": "Page.frameNavigated", "params": {"frame": {"id": "next"}}})
            elif transition == "destroy":
                iso.event({"method": "Runtime.executionContextDestroyed", "params": {"executionContextId": 20}})
            else:
                await iso.stop()
            release.set()
            with self.assertRaisesRegex(ValueError, "changed while being created"):
                await task
            self.assertNotEqual(iso.ctx, 20, transition)
            self.assertIsNone(iso.creating)

    async def test_valid_create_response_owns_the_context_even_without_created_event(self):
        async def rpc(method, **params):
            return {"executionContextId": 20}
        iso, _ = self.iso(rpc)
        await iso.make()
        self.assertEqual(iso.ctx, 20)
        self.assertFalse(iso.injected)

    async def test_invalid_context_and_frame_ids_are_rejected(self):
        for value in (True, 0, -1, "20", None):
            async def rpc(method, **params):
                return {"executionContextId": value, "frameTree": {"frame": {"id": value}}}
            iso, _ = self.iso(rpc)
            with self.assertRaises(ValueError):
                await iso.make()
            if value != "20":
                with self.assertRaises(ValueError):
                    await iso.locate()

    async def test_verification_without_owned_context_never_evaluates_main_world(self):
        calls = []
        async def rpc(method, **params):
            calls.append((method, params))
            return {"result": {"value": "function"}}
        iso, _ = self.iso(rpc)
        iso.ctx = None
        with self.assertRaisesRegex(ValueError, "no execution context id"):
            await iso.verify()
        self.assertEqual(calls, [])

    async def test_late_frame_tree_cannot_replace_navigation_frame(self):
        async def rpc(method, **params):
            iso.event({"method": "Page.frameNavigated", "params": {"frame": {"id": "next"}}})
            return {"frameTree": {"frame": {"id": "main"}}}
        iso, _ = self.iso(rpc)
        with self.assertRaisesRegex(ValueError, "changed while being located"):
            await iso.locate()
        self.assertEqual(iso.frame, "next")

    async def test_renew_checks_binding_and_counts_missing_binding_towards_failure(self):
        async def rpc(method, **params):
            if method == "Page.createIsolatedWorld":
                return {"executionContextId": 20}
            return {"result": {"value": "undefined"}}
        iso, logs = self.iso(rpc)
        for _ in range(fy_iso.LOST):
            self.assertFalse(await iso.renew())
            self.assertIsNone(iso.ctx)
        self.assertTrue(iso.dead)
        self.assertIn("binding is missing", logs[-1][-1])

    async def test_navigation_during_binding_verification_is_not_success(self):
        async def rpc(method, **params):
            if method == "Page.createIsolatedWorld":
                return {"executionContextId": 20}
            iso.event({"method": "Runtime.executionContextsCleared"})
            return {"result": {"value": "function"}}
        iso, logs = self.iso(rpc)
        self.assertFalse(await iso.renew())
        self.assertIsNone(iso.ctx)
        self.assertIn("changed while being verified", logs[-1][-1])

    async def test_stopping_during_renewal_does_not_retry_or_report_loss(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def rpc(method, **params):
            calls.append(method)
            if method == "Page.createIsolatedWorld":
                entered.set()
                await release.wait()
                return {"executionContextId": 20}
            return {}
        iso, logs = self.iso(rpc)
        task = asyncio.create_task(iso.renew())
        await entered.wait()
        await iso.stop()
        release.set()
        self.assertFalse(await task)
        self.assertIsNone(iso.ctx)
        self.assertFalse(iso.on)
        self.assertFalse(iso.busy)
        self.assertEqual(iso.lost, 0)
        self.assertEqual(logs, [])
        self.assertEqual(calls.count("Page.createIsolatedWorld"), 1)
        self.assertNotIn("Page.getFrameTree", calls)
        iso.event(self.created(20))
        self.assertIsNone(iso.ctx)


if __name__ == "__main__":
    unittest.main()
