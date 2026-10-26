import asyncio
import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "fixtures" / "j"), str(HERE.parent)]

import jharness as H


BACKEND = """import framey_api
async def call(method, arg):
    framey_api.events.emit("live", arg)
    return {"native": arg}
"""


class Sender:
    def __init__(self, core):
        self.core = core
        self.calls = []
        self.entered, self.resume = asyncio.Event(), asyncio.Event()
        self.block = False

    async def __call__(self, method, **params):
        self.calls.append((method, params))
        if self.block and len(self.calls) == 1:
            self.entered.set()
            await self.resume.wait()


class DeckyRuntimeEvents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loader = H.Loader()
        self.addAsyncCleanup(self.loader.close)
        self.fy = self.loader.fy
        self.network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("real network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.addAsyncCleanup(self.cleanup_requests)

    async def cleanup_requests(self):
        tasks = list(self.fy.TASKS)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def attach(self):
        page, task = await H.attach(self.loader)
        self.addAsyncCleanup(self.finish_session, task)
        return page, task

    async def finish_session(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def setup_inspection(self):
        self.loader.plugin("live", {"api": 1}, backend=BACKEND)
        self.fy.load_backends()
        marker = self.loader.base / "inspected-code-ran"
        payload = "from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("plugin.json", json.dumps({"name": "Inspected", "author": "Example", "flags": []}))
            archive.writestr("package.json", json.dumps({"name": "inspected", "version": "1.0.0"}))
            archive.writestr("main.py", payload)
            archive.writestr("backend.py", payload)
            archive.writestr("dist/index.js", "throw new Error('INSPECTED_FRONTEND_EXECUTED')")
        data = buffer.getvalue()
        feed = [{"id": 1, "name": "Inspected", "author": "Example", "versions": [{"name": "1.0.0", "hash": hashlib.sha256(data).hexdigest()}]}]
        self.archive_entered, self.archive_resume, self.archive_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.feed_calls, self.archive_calls = [], []

        async def fetcher(url, limit):
            self.feed_calls.append(url)
            return json.dumps(feed).encode()

        async def archive_fetcher(url, limit):
            self.archive_calls.append(url)
            self.archive_entered.set()
            try:
                await self.archive_resume.wait()
            except asyncio.CancelledError:
                self.archive_cancelled.set()
                raise
            return data

        module = self.fy.use("fy_catalog")
        service = module.Catalog(self.fy.host, fetcher=fetcher, archive_fetcher=archive_fetcher)
        module._instances[self.fy.host] = service
        details = await self.fy.core("catalog_details", {"source": "decky_stable", "id": "decky-1"})
        entry = details["entry"]
        request = {"source": "decky_stable", "id": entry["id"], "identity": entry["identity"], "version_identity": entry["versions"][0]["identity"], "confirm": True}
        return request, marker

    async def assert_settled(self, predicate):
        self.assertTrue(await H.settle(predicate, limit=3))

    async def test_browsing_metadata_does_not_download_execute_or_emit_plugin_events(self):
        _, marker = await self.setup_inspection()
        page, _ = await self.attach()
        page.bind(id=1, gen="browse", plugin="_core", method="catalog_list", arg={"source": "decky_stable"})
        page.bind(id=2, gen="browse", plugin="_core", method="catalog_details", arg={"source": "decky_stable", "id": "decky-1"})
        await self.assert_settled(lambda: len(page.replies()) == 2)
        replies = {reply[1]: reply for reply in page.replies()}
        self.assertEqual(replies[1][2]["rows"][0]["id"], "decky-1")
        self.assertEqual(replies[2][2]["entry"]["id"], "decky-1")
        self.assertEqual(self.archive_calls, [])
        self.assertFalse(marker.exists())
        self.assertEqual(page.events(), [])
        status = self.fy.supervisor.status()
        self.assertEqual(set(status), {"live"})
        self.assertEqual((status["live"]["state"], status["live"]["calls"], status["live"]["os_pid"]), ("stopped", 0, None))
        self.assertEqual(sorted(path.name for path in self.loader.plugins.iterdir()), ["live"])

    async def test_pending_inspection_does_not_block_native_events_or_reuse_reply_generation(self):
        request, marker = await self.setup_inspection()
        page, _ = await self.attach()
        page.bind(id=7, gen="store-old", plugin="_core", method="catalog_assess", arg=request)
        await asyncio.wait_for(self.archive_entered.wait(), 2)
        page.bind(id=7, gen="plugin-new", plugin="live", method="emit", arg="native-payload")
        await self.assert_settled(lambda: page.replies() and page.events())
        self.assertEqual(page.replies(), [["plugin-new", 7, {"native": "native-payload"}]])
        self.assertEqual(page.events(), [["live", "live", "native-payload", 0]])
        self.archive_resume.set()
        await self.assert_settled(lambda: len(page.replies()) == 2)
        assessment = page.replies()[1]
        self.assertEqual(assessment[:2], ["store-old", 7])
        self.assertTrue(assessment[2]["assessment"]["not_executed"])
        self.assertTrue(assessment[2]["assessment"]["verified_sha256"])
        self.assertFalse(marker.exists())
        self.assertEqual(set(self.fy.supervisor.status()), {"live"})
        self.assertEqual(sorted(path.name for path in self.loader.plugins.iterdir()), ["live"])
        self.assertEqual(page.events(), [["live", "live", "native-payload", 0]])
        self.assertFalse(any("INSPECTED_FRONTEND_EXECUTED" in source for source in page.exprs()))

    async def test_late_inspection_reply_is_not_redirected_to_a_replacement_session(self):
        request, marker = await self.setup_inspection()
        old, session = await self.attach()
        old.bind(id=4, gen="old-store", plugin="_core", method="catalog_assess", arg=request)
        await asyncio.wait_for(self.archive_entered.wait(), 2)
        await self.finish_session(session)
        new, _ = await self.attach()
        new.bind(id=4, gen="new-plugin", plugin="live", method="emit", arg="new")
        await self.assert_settled(lambda: new.replies() and new.events())
        self.archive_resume.set()
        await self.assert_settled(lambda: not self.fy.TASKS)
        self.assertEqual(new.replies(), [["new-plugin", 4, {"native": "new"}]])
        self.assertEqual(new.events(), [["live", "live", "new", 0]])
        self.assertFalse(marker.exists())

    async def test_cancelled_inspection_cleans_up_without_cancelling_native_work(self):
        request, marker = await self.setup_inspection()
        page, _ = await self.attach()
        page.bind(id=1, gen="store", plugin="_core", method="catalog_assess", arg=request)
        await asyncio.wait_for(self.archive_entered.wait(), 2)
        self.assertEqual(len(self.fy.TASKS), 1)
        task = next(iter(self.fy.TASKS))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(self.archive_cancelled.wait(), 2)
        await self.assert_settled(lambda: not self.fy.TASKS)
        page.bind(id=2, gen="native", plugin="live", method="emit", arg="still-live")
        await self.assert_settled(lambda: page.replies() and page.events())
        self.assertEqual(page.replies(), [["native", 2, {"native": "still-live"}]])
        self.assertEqual(page.events(), [["live", "live", "still-live", 0]])
        self.assertFalse(marker.exists())
        self.assertEqual(set(self.fy.supervisor.status()), {"live"})

    async def test_plugin_token_cannot_invoke_inspection_or_register_a_backend(self):
        request, marker = await self.setup_inspection()
        page, _ = await self.attach()
        token = next(token for token, pid in self.fy.TOKS.items() if pid == "live")
        page.bind(id=1, gen="plugin", plugin="_core", method="catalog_assess", arg=request, t=token)
        await self.assert_settled(lambda: page.replies())
        self.assertEqual(page.replies(), [["plugin", 1, {"error": "not authorized"}]])
        self.assertEqual(self.archive_calls, [])
        self.assertFalse(marker.exists())
        self.assertEqual(set(self.fy.supervisor.status()), {"live"})

    async def test_old_session_cleanup_does_not_unregister_its_replacement(self):
        await self.setup_inspection()
        _, old_task = await self.attach()
        new, _ = await self.attach()
        replacement = self.fy.LIVE["main"]
        await self.finish_session(old_task)
        self.assertIs(self.fy.LIVE.get("main"), replacement)
        new.bind(id=1, gen="replacement", plugin="live", method="emit", arg="survives")
        await self.assert_settled(lambda: new.replies() and new.events())
        self.assertEqual(new.replies(), [["replacement", 1, {"native": "survives"}]])
        self.assertEqual(new.events(), [["live", "live", "survives", 0]])

    async def test_drained_events_cannot_cross_into_a_replacement_main_session(self):
        old, new = Sender("old-core"), Sender("new-core")
        old.block = True
        self.fy.LIVE["main"] = old
        events = [{"plugin": "live", "name": "tick", "payload": n, "dropped": 0} for n in (1, 2)]
        delivering = asyncio.create_task(self.fy.emit_all(events))
        self.addAsyncCleanup(self.finish_session, delivering)
        await asyncio.wait_for(old.entered.wait(), 2)
        self.fy.LIVE["main"] = new
        old.resume.set()
        await asyncio.wait_for(delivering, 2)
        self.assertEqual(new.calls, [], "events drained for an old page must not enter its replacement")

    async def test_drained_events_cannot_cross_a_core_generation_on_the_same_sender(self):
        sender = Sender("old-core")
        sender.block = True
        self.fy.LIVE["main"] = sender
        events = [{"plugin": "live", "name": "tick", "payload": n, "dropped": 0} for n in (1, 2)]
        delivering = asyncio.create_task(self.fy.emit_all(events))
        self.addAsyncCleanup(self.finish_session, delivering)
        await asyncio.wait_for(sender.entered.wait(), 2)
        sender.core = "new-core"
        sender.resume.set()
        await asyncio.wait_for(delivering, 2)
        self.assertFalse(any(params["objectId"] == "new-core" for _, params in sender.calls))

    async def test_drained_events_do_not_enter_a_reloaded_plugin_generation(self):
        directory = self.loader.plugin("live", {"api": 1}, backend=BACKEND)
        self.fy.load_backends()
        supervisor = self.fy.supervisor
        sender = Sender("same-core")
        sender.block = True
        self.fy.LIVE["main"] = sender
        events = [{"plugin": "live", "name": "tick", "payload": n, "dropped": 0} for n in (1, 2)]
        delivering = asyncio.create_task(self.fy.emit_all(events))
        self.addAsyncCleanup(self.finish_session, delivering)
        await asyncio.wait_for(sender.entered.wait(), 2)
        supervisor.forget("live")
        supervisor.register("live", directory, self.fy.manifest(directory))
        sender.resume.set()
        await asyncio.wait_for(delivering, 2)
        self.assertEqual(len(sender.calls), 1, "the remaining old-plugin batch must not enter its replacement")

    async def test_flush_does_not_redirect_a_scheduled_old_batch_after_reattach(self):
        old, new = Sender("old-core"), Sender("new-core")
        self.fy.LIVE["main"] = old
        supervisor = self.fy.sup()
        supervisor.events.put("live", "old", "queued")
        if self.fy.FLUSH:
            self.fy.FLUSH.cancel()
            self.fy.FLUSH = None
        scheduled = []
        self.fy.spawn = scheduled.append
        self.fy.flush()
        self.assertEqual(len(scheduled), 1)
        self.fy.LIVE["main"] = new
        await scheduled[0]
        self.assertEqual(new.calls, [], "a drained old batch must not acquire a new destination when it starts")


if __name__ == "__main__":
    unittest.main()
