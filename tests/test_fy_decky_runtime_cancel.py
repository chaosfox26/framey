"""Selected archive inspection stays outside the plugin worker lifecycle."""

import asyncio
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fy_catalog
import fy_decky_catalog as decky
from fy_supervisor import Supervisor


SPEC = importlib.util.spec_from_file_location("framey_decky_runtime_contract", ROOT / "framey.py")
FY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FY)


class Host:
    VERSION, PLATFORM, API_GENERATION = "1.0.2", "linux-aarch64", 1

    def __init__(self, root):
        self.ROOT = root / "loader"
        self.HOME = root / "home"
        self.CONF = self.HOME / ".config/framey/settings.json"
        self.PLUGINS = self.ROOT / "plugins"

    def now(self):
        return 1_790_000_000.0

    def settings(self):
        return {"prefs": {"workers_max": 1}}

    def manifests(self):
        return []

    def log(self, *args):
        pass

    def loop(self):
        return asyncio.get_running_loop()


class InspectionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-runtime-")
        self.addCleanup(temporary.cleanup)
        self.host = Host(Path(temporary.name))
        self.spawn = mock.Mock(side_effect=AssertionError("inspection must not launch a worker"))
        self.supervisor = Supervisor(self.host, spawn=self.spawn)
        self.supervisor.register("untouched", self.host.PLUGINS / "untouched", {})
        self.addAsyncCleanup(self.supervisor.shutdown, 0.1)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("plugin.json", json.dumps({"name": "Inspection fixture", "flags": []}))
            archive.writestr("dist/index.js", "throw Error('inspection must not execute frontend code');")
            archive.writestr("main.py", "raise AssertionError('inspection must not execute backend code')")
        self.archive = output.getvalue()
        self.digest = hashlib.sha256(self.archive).hexdigest()
        self.feed = json.dumps([{
            "id": 1, "name": "Inspection fixture", "author": "Example", "description": "Read-only fixture",
            "tags": [], "visible": True,
            "versions": [{"name": "1.0.0", "hash": self.digest}],
        }]).encode()
        self.fetches = []

        async def metadata(url, limit):
            self.fetches.append((url, limit))
            return self.feed

        async def archive(url, limit):
            return self.archive

        self.catalog = decky.DeckyCatalog(self.host, fetcher=metadata, archive_fetcher=archive)
        details = await self.catalog.details({"id": "decky-1"})
        entry = details["entry"]
        self.arg = {"source": "decky_stable", "id": entry["id"], "identity": entry["identity"],
                    "version_identity": entry["versions"][0]["identity"], "confirm": True}

    def assert_runtime_untouched(self):
        self.spawn.assert_not_called()
        self.assertEqual(self.supervisor._live(), 0)
        self.assertEqual(self.supervisor._memory(), 0)
        self.assertEqual(self.supervisor._tasks, set())
        worker = self.supervisor._ws["untouched"]
        self.assertEqual((worker.state, worker.inflight, len(worker.waiters), len(worker.pending)),
                         ("stopped", 0, 0, 0))

    async def test_cancel_during_archive_download_cleans_awaited_work_and_allows_retry(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def pending(url, limit):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.catalog.archive_fetcher = pending
        task = asyncio.create_task(self.catalog.assess(self.arg))
        await asyncio.wait_for(entered.wait(), 0.5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        self.assertTrue(cancelled.is_set())
        self.assert_runtime_untouched()

        async def completed(url, limit):
            return self.archive

        self.catalog.archive_fetcher = completed
        result = await asyncio.wait_for(self.catalog.assess(self.arg), 0.5)
        self.assertTrue(result["assessment"]["not_executed"])
        self.assertTrue(result["assessment"]["verified_sha256"])
        self.assert_runtime_untouched()

    async def test_timeout_cancels_archive_wait_without_using_plugin_slots(self):
        cancelled = asyncio.Event()

        async def pending(url, limit):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.catalog.archive_fetcher = pending
        with mock.patch.object(decky, "ASSESS_TIMEOUT", 0.02):
            result = await asyncio.wait_for(self.catalog.assess(self.arg), 0.5)
        self.assertEqual(result["code"], "timeout")
        await asyncio.wait_for(cancelled.wait(), 0.5)
        self.assertTrue(cancelled.is_set())
        self.assert_runtime_untouched()

    async def test_missing_confirmation_does_not_start_archive_or_worker_work(self):
        archive = mock.AsyncMock(side_effect=AssertionError("confirmation required before download"))
        self.catalog.archive_fetcher = archive
        result = await self.catalog.assess(dict(self.arg, confirm=False))
        self.assertEqual(result["code"], "confirmation_required")
        archive.assert_not_called()
        self.assert_runtime_untouched()

    async def test_cancelled_catalog_waiter_does_not_cancel_shared_metadata_refresh(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def metadata(url, limit):
            entered.set()
            await release.wait()
            return self.feed

        self.catalog.fetcher = metadata
        self.catalog.cache.clear()
        first = asyncio.create_task(self.catalog.assess(self.arg))
        await asyncio.wait_for(entered.wait(), 0.5)
        busy = await self.catalog.assess(self.arg)
        self.assertEqual(busy["code"], "busy")
        second = asyncio.create_task(self.catalog.details({"id": "decky-1"}))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(first, 0.5)
        release.set()
        result = await asyncio.wait_for(second, 0.5)
        self.assertEqual(result["entry"]["id"], "decky-1")
        await asyncio.sleep(0)
        self.assertEqual(self.catalog.refreshes, {})
        retry = await asyncio.wait_for(self.catalog.assess(self.arg), 0.5)
        self.assertTrue(retry["assessment"]["not_executed"])
        self.assert_runtime_untouched()

    async def test_actual_loader_routes_assessment_to_catalog_without_backend_admission(self):
        self.assertEqual(FY.OWNER.get("catalog_assess"), "fy_catalog")
        invoke = fy_catalog.METHODS.get("catalog_assess")
        self.assertTrue(callable(invoke))
        backend = mock.AsyncMock(side_effect=AssertionError("core inspection must not use backend routing"))
        service = fy_catalog.Catalog(self.host)
        with mock.patch.object(FY, "host", self.host), mock.patch.object(FY, "backend", backend), \
                mock.patch.object(FY, "use", return_value=fy_catalog), \
                mock.patch.object(fy_catalog, "_service", return_value=service), \
                mock.patch.object(service, "_decky", return_value=self.catalog):
            result = await asyncio.wait_for(FY.core("catalog_assess", self.arg), 0.5)
        self.assertTrue(result["assessment"]["not_executed"])
        backend.assert_not_called()
        self.assert_runtime_untouched()

    async def test_loader_cancellation_propagates_without_settling_or_resurrecting_workers(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def pending(url, limit):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.catalog.archive_fetcher = pending
        service = fy_catalog.Catalog(self.host)
        settled = mock.AsyncMock()
        backend = mock.AsyncMock(side_effect=AssertionError("inspection must not invoke a backend"))
        with mock.patch.object(FY, "host", self.host), mock.patch.object(FY, "backend", backend), \
                mock.patch.object(FY, "settled", settled), mock.patch.object(FY, "use", return_value=fy_catalog), \
                mock.patch.object(fy_catalog, "_service", return_value=service), \
                mock.patch.object(service, "_decky", return_value=self.catalog):
            task = asyncio.create_task(FY.core("catalog_assess", self.arg))
            await asyncio.wait_for(entered.wait(), 0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.5)
        self.assertTrue(cancelled.is_set())
        settled.assert_not_called()
        backend.assert_not_called()
        self.assert_runtime_untouched()

    async def test_plugin_token_cannot_start_core_archive_inspection(self):
        with mock.patch.object(FY, "TOKS", {"fixture-token": "untouched"}):
            allowed, _ = FY.authorize({"t": "fixture-token", "method": "catalog_assess", "arg": self.arg})
        self.assertFalse(allowed)
        allowed, arg = FY.authorize({"t": FY.CORE_TOK, "method": "catalog_assess", "arg": self.arg})
        self.assertTrue(allowed)
        self.assertEqual(arg, self.arg)
        self.assert_runtime_untouched()


if __name__ == "__main__":
    unittest.main()
