import errno
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_decky_catalog as decky
import fy_pkg
import fy_store


def feed(rows=1, versions=1, start=1):
    return json.dumps([
        {"id": index, "name": "Entry %d" % index,
         "versions": [{"name": str(version), "hash": hashlib.sha256(str(version).encode()).hexdigest()}
                      for version in range(versions)]}
        for index in range(start, start + rows)
    ], separators=(",", ":")).encode()


class DeckyPlatformCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="decky-cache-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = 1_000_000
        self.state = {"prefs": {"dev": {"enabled": True}}}
        self.host = SimpleNamespace(HOME=self.root, CONF=self.root / "framey" / "settings.json",
                                    PLATFORM="linux-aarch64", now=lambda: self.clock, settings=lambda: self.state)
        self.body = feed()
        self.failure = None
        self.calls = []

        async def fetcher(url, maximum):
            self.calls.append((url, maximum))
            if self.failure:
                raise self.failure
            return self.body

        self.fetcher = fetcher
        self.catalog = decky.DeckyCatalog(self.host, fetcher=fetcher)
        network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network access"))
        network.start()
        self.addCleanup(network.stop)

    @unittest.skipUnless(os.name == "posix", "requires POSIX file modes")
    async def test_new_cache_and_directory_remain_private_with_permissive_umask(self):
        old_umask = os.umask(0)
        try:
            result = await self.catalog.list()
        finally:
            os.umask(old_umask)
        self.assertEqual(result["total"], 1)
        self.assertEqual(stat.S_IMODE(self.catalog.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.catalog.path.parent.stat().st_mode), 0o700)
        self.assertLessEqual(self.catalog.path.stat().st_size, decky.CACHE_MAX)

    async def test_failed_replace_keeps_saved_catalog_and_removes_partial_file(self):
        await self.catalog.list()
        before = self.catalog.path.read_bytes()
        self.body = feed(start=2)
        with mock.patch.object(fy_store.os, "replace", side_effect=OSError(errno.EIO, "replace failed")):
            result = await self.catalog.list({"refresh": True})
        self.assertEqual([row["id"] for row in result["rows"]], ["decky-2"])
        self.assertTrue(result["warning"])
        self.assertEqual(self.catalog.path.read_bytes(), before)
        self.assertEqual([path.name for path in self.catalog.path.parent.iterdir()], [self.catalog.path.name])
        self.failure = OSError("offline")
        restarted = decky.DeckyCatalog(self.host, fetcher=self.fetcher)
        result = await restarted.list()
        self.assertEqual([row["id"] for row in result["rows"]], ["decky-1"])
        self.assertEqual(result["origin"], "cache")

    async def test_failed_file_sync_keeps_the_previous_cache(self):
        await self.catalog.list()
        before = self.catalog.path.read_bytes()
        self.body = feed(start=2)
        with mock.patch.object(fy_store.os, "fsync", side_effect=OSError(errno.ENOSPC, "sync failed")):
            result = await self.catalog.list({"refresh": True})
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["warning"])
        self.assertEqual(self.catalog.path.read_bytes(), before)
        self.assertEqual([path.name for path in self.catalog.path.parent.iterdir()], [self.catalog.path.name])

    async def test_corrupt_cache_is_preserved_when_refresh_fails(self):
        fy_store.ensure(self.catalog.path.parent)
        original = b"{interrupted cache"
        self.catalog.path.write_bytes(original)
        self.failure = OSError("offline")
        result = await self.catalog.list()
        self.assertEqual((result["total"], result["origin"]), (0, "unavailable"))
        self.assertTrue(result["fetch_error"])
        self.assertEqual(self.catalog.path.read_bytes(), original)

    async def test_oversized_cache_is_preserved_when_refresh_fails(self):
        fy_store.ensure(self.catalog.path.parent)
        original = b" " * (decky.CACHE_MAX + 1)
        self.catalog.path.write_bytes(original)
        self.failure = OSError("offline")
        result = await self.catalog.list()
        self.assertEqual((result["total"], result["origin"]), (0, "unavailable"))
        self.assertTrue(result["fetch_error"])
        self.assertEqual(self.catalog.path.read_bytes(), original)

    async def test_valid_cache_with_bad_schema_is_preserved_when_refresh_fails(self):
        fy_store.ensure(self.catalog.path.parent)
        original = b'{"v":2,"cache":{"future":"keep"}}'
        self.catalog.path.write_bytes(original)
        self.failure = OSError("offline")
        result = await self.catalog.list()
        self.assertEqual((result["total"], result["origin"]), (0, "unavailable"))
        self.assertEqual(self.catalog.path.read_bytes(), original)

    async def test_valid_large_feed_survives_cache_revalidation_after_restart(self):
        self.body = feed(rows=20, versions=100)
        self.assertLess(len(self.body), decky.FEED_MAX)
        downloaded = await self.catalog.list()
        self.assertEqual(downloaded["total"], 20)
        self.assertLess(self.catalog.path.stat().st_size, decky.CACHE_MAX)
        self.failure = OSError("offline")
        restarted = decky.DeckyCatalog(self.host, fetcher=self.fetcher)
        result = await restarted.list()
        self.assertEqual(result["total"], 20)
        self.assertEqual(result["origin"], "cache")
        self.assertEqual(len(self.calls), 1)

    async def test_oversized_download_keeps_the_previous_stale_catalog(self):
        await self.catalog.list()
        saved = json.loads(self.catalog.path.read_bytes())["cache"]["decky_stable"]["raw"]
        self.clock += decky.TTL + 1
        self.body = b" " * (decky.FEED_MAX + 1)
        result = await self.catalog.list()
        self.assertEqual([row["id"] for row in result["rows"]], ["decky-1"])
        self.assertEqual(result["origin"], "cache")
        self.assertTrue(result["stale"])
        self.assertTrue(result["fetch_error"])
        persisted = json.loads(self.catalog.path.read_bytes())
        self.assertEqual(persisted["cache"]["decky_stable"]["raw"], saved)
        self.assertLessEqual(self.catalog.path.stat().st_size, decky.CACHE_MAX)

    async def test_two_large_source_caches_remain_bounded_and_survive_restart(self):
        self.body = feed(rows=20, versions=100)
        await self.catalog.list()
        self.body = feed(rows=20, versions=100, start=21)
        await self.catalog.list({"source": "decky_testing"})
        self.assertLessEqual(self.catalog.path.stat().st_size, decky.CACHE_MAX)
        persisted = json.loads(self.catalog.path.read_bytes())
        self.assertEqual(set(persisted["cache"]), {"decky_stable", "decky_testing"})
        self.failure = OSError("offline")
        restarted = decky.DeckyCatalog(self.host, fetcher=self.fetcher)
        stable = await restarted.list()
        testing = await restarted.list({"source": "decky_testing"})
        self.assertEqual((stable["total"], testing["total"]), (20, 20))
        self.assertEqual((stable["origin"], testing["origin"]), ("cache", "cache"))
        self.assertEqual(len(self.calls), 2)

    async def test_large_second_source_cannot_destroy_the_saved_stable_catalog(self):
        self.body = feed(rows=20, versions=100)
        stable = await self.catalog.list()
        self.assertEqual(stable["total"], 20)
        self.body = feed(rows=50, versions=100, start=21)
        self.assertLess(len(self.body), decky.FEED_MAX)
        testing = await self.catalog.list({"source": "decky_testing"})
        self.assertEqual(testing["total"], 50)
        self.assertLessEqual(self.catalog.path.stat().st_size, decky.CACHE_MAX)
        self.failure = OSError("offline")
        restarted = decky.DeckyCatalog(self.host, fetcher=self.fetcher)
        stable = await restarted.list()
        self.assertEqual(stable["total"], 20)
        self.assertEqual(stable["origin"], "cache")

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    async def test_replacing_a_cache_link_never_modifies_its_target(self):
        fy_store.ensure(self.catalog.path.parent)
        target = self.root / "other-file"
        target.write_bytes(b"keep")
        self.catalog.path.symlink_to(target)
        result = await self.catalog.list()
        self.assertEqual(result["total"], 1)
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertFalse(self.catalog.path.is_symlink())
        self.assertEqual(stat.S_IMODE(self.catalog.path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    async def test_linked_cache_directory_is_not_written_through(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / self.catalog.path.name
        target.write_bytes(b"keep")
        self.catalog.path.parent.symlink_to(outside, target_is_directory=True)
        result = await self.catalog.list()
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["warning"])
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertEqual([path.name for path in outside.iterdir()], [target.name])


class DeckyPlatformArchiveBoundsTests(unittest.TestCase):
    def archive(self, count=0):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("plugin.json", b'{"name":"Inspection fixture","flags":[]}')
            archive.writestr("main.py", b"raise RuntimeError('must never run')\n")
            for index in range(count):
                archive.writestr("extra-%d" % index, b"")
        data = output.getvalue()
        return data, hashlib.sha256(data).hexdigest()

    def test_entry_budget_is_checked_before_constructing_zip_reader(self):
        data, digest = self.archive(fy_pkg.ENTRIES)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("entry budget checked too late")):
            with self.assertRaises(decky.CatalogError) as caught:
                decky.inspect_archive(data, digest)
        self.assertEqual(caught.exception.code, "bad_archive")

    def test_read_only_assessment_needs_no_disk_writes_or_plugin_imports(self):
        data, digest = self.archive()
        with mock.patch("builtins.open", side_effect=AssertionError("inspection touched disk")), \
                mock.patch.object(os, "open", side_effect=AssertionError("inspection touched disk")):
            result = decky.inspect_archive(data, digest)
        self.assertTrue(result["not_executed"])
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["architecture_status"], "unverified")
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
