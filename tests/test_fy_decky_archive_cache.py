import asyncio
import copy
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog as decky


def package(version):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("plugin.json", json.dumps({"name": "Archive Fixture", "flags": []}))
        output.writestr("package.json", json.dumps({"name": "archive-fixture", "version": version}))
        output.writestr("dist/index.js", "// read-only inspection fixture " + version)
        output.writestr("main.py", "raise AssertionError('archive inspection executed a backend')\n")
    return stream.getvalue()


class Host:
    VERSION, PLATFORM, API_GENERATION = "1.0.2", "linux-aarch64", 1

    def __init__(self, root):
        self.ROOT, self.HOME = root / "loader", root / "home"
        self.PLUGINS = self.ROOT / "plugins"
        self.CONF = self.HOME / ".config/framey/settings.json"
        self.state = {"prefs": {"dev": {"enabled": True}}}
        self.clock = 1_790_000_000
        for path in (self.ROOT, self.HOME, self.PLUGINS, self.CONF.parent):
            path.mkdir(parents=True, exist_ok=True)

    def settings(self):
        return copy.deepcopy(self.state)

    def now(self):
        return self.clock

    def log(self, *args):
        pass


class ArchiveIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-archive-cache-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = Host(self.root)
        self.data = {name: package(name) for name in ("1.0.0", "2.0.0")}
        self.hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.data.items()}
        self.artifacts = {decky.CDN + digest + ".zip": self.data[name] for name, digest in self.hashes.items()}
        self.feed = [{"id": 1, "name": "Archive Fixture", "author": "Example", "description": "Fixture",
                      "tags": ["tools"], "visible": True, "versions": [
                          {"name": name, "hash": self.hashes[name]} for name in ("2.0.0", "1.0.0")]}]
        self.feeds = {source["url"]: copy.deepcopy(self.feed) for source in decky.SOURCES.values()}
        self.archive_calls = []

        async def metadata(url, limit):
            return json.dumps(self.feeds[url]).encode()

        async def archive(url, limit):
            self.archive_calls.append((url, limit))
            return self.artifacts[url]

        self.catalog = decky.DeckyCatalog(self.host, fetcher=metadata, archive_fetcher=archive)

    async def selection(self, source="decky_stable", version="2.0.0"):
        result = await self.catalog.details({"source": source, "id": "decky-1"})
        entry = result["entry"]
        chosen = next(value for value in entry["versions"] if value["name"] == version)
        return {"source": source, "id": entry["id"], "identity": entry["identity"],
                "version_identity": chosen["identity"], "confirm": True}

    def disk_snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_receipt(self, result, selected, version):
        self.assertEqual((result["version"], result["hash"]), (version, self.hashes[version]))
        self.assertEqual(result["source"], decky.SOURCES[selected["source"]])
        self.assertEqual(result["identity"], selected["identity"])
        self.assertEqual(result["version_identity"], selected["version_identity"])
        self.assertEqual(result["artifact"], decky.CDN + self.hashes[version] + ".zip")
        self.assertEqual((result["checked_at"], result["downloaded_at"]), (self.host.clock, self.host.clock))
        self.assertTrue(result["read_only"])
        self.assertTrue(result["assessment"]["not_executed"])

    async def test_identity_from_another_source_cannot_fetch_an_archive(self):
        selected = await self.selection()
        result = await self.catalog.assess(selected | {"source": "decky_testing"})
        self.assertEqual(result.get("code"), "stale_catalog")
        self.assertEqual(self.archive_calls, [])

    async def test_unknown_or_custom_source_cannot_reuse_an_official_selection(self):
        selected = await self.selection()
        result = await self.catalog.assess(selected | {"source": "custom", "url": "https://example.test/catalog.json"})
        self.assertEqual(result.get("code"), "bad_source")
        self.assertEqual(self.archive_calls, [])

    async def test_refreshed_listing_rejects_previous_identity_before_download(self):
        selected = await self.selection()
        source = decky.SOURCES["decky_stable"]["url"]
        self.feeds[source][0]["versions"] = self.feeds[source][0]["versions"][1:]
        await self.catalog.list({"source": "decky_stable", "refresh": True})
        result = await self.catalog.assess(selected)
        self.assertEqual(result.get("code"), "stale_catalog")
        self.assertEqual(self.archive_calls, [])

    async def test_concurrent_selected_version_is_busy_then_gets_its_own_receipt(self):
        first = await self.selection(version="1.0.0")
        second = await self.selection(version="2.0.0")
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(url, limit):
            self.archive_calls.append((url, limit))
            entered.set()
            await release.wait()
            return self.artifacts[url]

        self.catalog.archive_fetcher = delayed
        task = asyncio.create_task(self.catalog.assess(first))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            blocked = await self.catalog.assess(second)
            self.assertEqual(blocked.get("code"), "busy")
            self.assertEqual(len(self.archive_calls), 1)
            self.assertFalse(task.done())
            release.set()
            previous = await asyncio.wait_for(asyncio.shield(task), 1)
            self.assert_receipt(previous, first, "1.0.0")
            newest = await self.catalog.assess(second)
            self.assert_receipt(newest, second, "2.0.0")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(self.archive_calls), 2)
        self.assertTrue(all(limit == decky.ARCHIVE_MAX for _, limit in self.archive_calls))

    async def test_metadata_refresh_during_inspection_does_not_rebind_its_receipt(self):
        selected = await self.selection(version="1.0.0")
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(url, limit):
            self.archive_calls.append((url, limit))
            entered.set()
            await release.wait()
            return self.artifacts[url]

        self.catalog.archive_fetcher = delayed
        task = asyncio.create_task(self.catalog.assess(selected))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            url = decky.SOURCES["decky_stable"]["url"]
            self.feeds[url][0]["versions"] = self.feeds[url][0]["versions"][1:]
            await self.catalog.list({"source": "decky_stable", "refresh": True})
            replacement = await self.selection(version="1.0.0")
            self.assertNotEqual(replacement["identity"], selected["identity"])
            self.assertEqual((await self.catalog.assess(replacement)).get("code"), "busy")
            release.set()
            result = await asyncio.wait_for(asyncio.shield(task), 1)
            self.assert_receipt(result, selected, "1.0.0")
            self.assertEqual((await self.catalog.assess(selected)).get("code"), "stale_catalog")
            self.assert_receipt(await self.catalog.assess(replacement), replacement, "1.0.0")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancelled_download_leaves_no_archive_or_verdict_on_disk(self):
        selected = await self.selection()
        before = self.disk_snapshot()
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def delayed(url, limit):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        self.catalog.archive_fetcher = delayed
        task = asyncio.create_task(self.catalog.assess(selected))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(cleaned.wait(), 1)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(self.disk_snapshot(), before)

    async def test_timed_out_download_is_unverified_and_cleans_up(self):
        selected = await self.selection()
        before = self.disk_snapshot()
        cleaned = asyncio.Event()

        async def delayed(url, limit):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        self.catalog.archive_fetcher = delayed
        with mock.patch.object(decky, "ASSESS_TIMEOUT", 0.02):
            result = await asyncio.wait_for(self.catalog.assess(selected), 1)
        self.assertEqual(result.get("code"), "timeout")
        self.assertNotIn("assessment", result)
        await asyncio.wait_for(cleaned.wait(), 1)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(self.disk_snapshot(), before)

    async def stalled_inspection(self, timed_out):
        selected = await self.selection()
        before = self.disk_snapshot()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        inspect_archive = decky.inspect_archive

        def delayed(*args, **kwargs):
            entered.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("test inspection was not released")
                return inspect_archive(*args, **kwargs)
            finally:
                finished.set()

        with mock.patch.object(decky, "inspect_archive", side_effect=delayed):
            try:
                with mock.patch.object(decky, "ASSESS_TIMEOUT", 0.05 if timed_out else 50):
                    task = asyncio.create_task(self.catalog.assess(selected))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                    if timed_out:
                        result = await asyncio.wait_for(asyncio.shield(task), 1)
                        self.assertEqual(result.get("code"), "timeout")
                        self.assertNotIn("assessment", result)
                    else:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 1)
                    self.assertFalse(finished.is_set())
                    self.assertEqual((await self.catalog.assess(selected)).get("code"), "busy")
                    self.assertEqual(len(self.archive_calls), 1)
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 1))
                await asyncio.gather(task, return_exceptions=True)
        deadline = asyncio.get_running_loop().time() + 1
        while True:
            retry = await self.catalog.assess(selected)
            if retry.get("code") != "busy":
                break
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("completed inspection did not release its admission slot")
            await asyncio.sleep(0.001)
        self.assert_receipt(retry, selected, "2.0.0")
        self.assertEqual(len(self.archive_calls), 2)
        self.assertEqual(self.disk_snapshot(), before)

    async def test_cancelled_header_thread_keeps_the_slot_until_it_finishes(self):
        await self.stalled_inspection(False)

    async def test_timed_out_header_thread_keeps_the_slot_until_it_finishes(self):
        await self.stalled_inspection(True)

    async def test_default_archive_download_uses_bounded_cancellable_bytes_fetch(self):
        selected = await self.selection()
        self.catalog.archive_fetcher = None
        data = self.data["2.0.0"]
        fetch = mock.AsyncMock(return_value=data)
        with mock.patch.object(decky.fy_net, "fetch_bytes", fetch):
            result = await self.catalog.assess(selected)
        self.assert_receipt(result, selected, "2.0.0")
        fetch.assert_awaited_once_with("_core", decky.CDN + self.hashes["2.0.0"] + ".zip",
                                      headers={"User-Agent": "Framey/1.0.2 (+https://github.com/chaosfox26/framey)"},
                                      timeout=30, max_bytes=decky.ARCHIVE_MAX)

    async def test_successful_inspection_is_read_only_and_does_not_reuse_a_stale_verdict(self):
        selected = await self.selection()
        before = self.disk_snapshot()
        first = await self.catalog.assess(selected)
        url = decky.CDN + self.hashes["2.0.0"] + ".zip"
        self.artifacts[url] = self.data["1.0.0"]
        second = await self.catalog.assess(selected)
        self.assertTrue(first["assessment"]["verified_sha256"])
        self.assertEqual(second.get("code"), "digest_mismatch")
        self.assertNotIn("assessment", second)
        self.assertEqual(len(self.archive_calls), 2)
        self.assertEqual(self.disk_snapshot(), before)

    async def test_oversized_download_cannot_create_a_verdict_or_quarantine_file(self):
        selected = await self.selection()
        before = self.disk_snapshot()
        url = decky.CDN + self.hashes["2.0.0"] + ".zip"
        self.artifacts[url] = b"x" * (decky.ARCHIVE_MAX + 1)
        result = await self.catalog.assess(selected)
        self.assertEqual(result.get("code"), "bad_archive")
        self.assertNotIn("assessment", result)
        self.assertEqual(self.archive_calls, [(url, decky.ARCHIVE_MAX)])
        self.assertEqual(self.disk_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
