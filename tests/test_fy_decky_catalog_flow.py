import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_catalog
import fy_decky_catalog


STABLE_URL = "https://plugins.deckbrew.xyz/plugins"
TESTING_URL = "https://testing.deckbrew.xyz/plugins"


def version(name, digest, created):
    return {"name": name, "hash": digest * 64, "created": created, "downloads": 120, "updates": 7}


def upstream_plugin(pid, name, description, tags, updated, **extra):
    return {
        "id": pid,
        "name": name,
        "author": "Example Author",
        "description": description,
        "tags": tags,
        "visible": True,
        "updated": updated,
        "versions": [version("2.0.0", "a", "2026-04-01T00:00:00Z"), version("1.0.0", "b", "2026-03-01T00:00:00Z")],
        "image_url": "https://untrusted.example/preview.png",
        "screenshots": ["https://untrusted.example/screenshot.png"],
        "unknown_upstream_field": {"ignored": True},
        **extra,
    }


def official_feed():
    return [
        upstream_plugin(1, "Alpha Audio Tools", "Spatial listening tools", ["audio", "sound"], "2026-04-02T00:00:00Z"),
        upstream_plugin(2, "Bravo Capture", "Picture capture", ["utilities", "screen"], "2026-06-01T00:00:00Z"),
        upstream_plugin(3, "Charlie UI", "Scenic overlays", ["appearance", "sound"], "2026-05-01T00:00:00Z"),
        upstream_plugin(4, "Hidden Entry", "Not publicly visible", ["hidden"], "2026-07-01T00:00:00Z", visible=False),
    ]


def native_feed():
    return {
        "schema": 1,
        "name": "Framey catalog",
        "plugins": [{
            "id": "native-module", "name": "Native Module", "version": "1.0.0",
            "description": "A Framey package", "category": "tools", "tags": ["native"],
            "api": 1, "platform": ["linux-aarch64"],
            "source": {"repository": "https://github.com/example/framey-module", "commit": "c" * 40, "path": ""},
        }],
    }


class FakeHost:
    def __init__(self, root):
        self.ROOT = root / "loader"
        self.HOME = root / "home"
        self.PLUGINS = self.ROOT / "plugins"
        self.CONF = self.HOME / ".config" / "framey" / "state.json"
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.0.2", "linux-aarch64", 1
        self.clock = 1_790_000_000.0
        self.state = {"prefs": {"dev": {"enabled": False}}}
        self.logs = []
        for path in (self.ROOT, self.HOME, self.PLUGINS, self.CONF.parent):
            path.mkdir(parents=True, exist_ok=True)

    def now(self):
        return self.clock

    def settings(self):
        return copy.deepcopy(self.state)

    def manifests(self):
        return []

    def log(self, *args):
        self.logs.append(args)


class ControlledFetcher:
    def __init__(self):
        self.routes = {STABLE_URL: official_feed(), TESTING_URL: official_feed(), fy_catalog.DEFAULT_URL: native_feed()}
        self.calls = []
        self.failure = None

    async def __call__(self, url, max_bytes):
        self.calls.append((url, max_bytes))
        if self.failure:
            raise self.failure
        if url not in self.routes:
            raise AssertionError("Unexpected metadata URL: " + url)
        return json.dumps(self.routes[url]).encode("utf-8")


class NoArchiveFetcher:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Browsing metadata must not download a package archive")


class DeckyCatalogFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-flow-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = FakeHost(self.root)
        self.fetcher = ControlledFetcher()
        self.archives = NoArchiveFetcher()
        self.catalog = fy_decky_catalog.DeckyCatalog(self.host, fetcher=self.fetcher, archive_fetcher=self.archives)
        network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("unit test attempted real network access"))
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def ids(page):
        return [row["id"] for row in page["rows"]]

    async def test_browse_uses_official_stable_feed_and_marks_every_package_read_only(self):
        page = await self.catalog.list()
        self.assertEqual(self.ids(page), ["decky-1", "decky-2", "decky-3"])
        self.assertEqual((page["total"], page["offset"], page["limit"]), (3, 0, 50))
        self.assertEqual(page["source"]["id"], "decky_stable")
        self.assertEqual(page["source"]["url"], STABLE_URL)
        self.assertEqual(page["origin"], "downloaded")
        self.assertIsNone(page["error"])
        self.assertFalse(page["stale"])
        self.assertEqual([call[0] for call in self.fetcher.calls], [STABLE_URL])
        for row in page["rows"]:
            self.assertEqual(row["format"], "decky")
            self.assertTrue(row["read_only"])
            self.assertFalse(row["eligible"])
            self.assertEqual(row["architecture"], "unknown")
            self.assertFalse(row["platform_declared"])
            self.assertEqual((row["compat"]["state"], row["compat"]["badge"]), ("incompatible", "port"))
            self.assertTrue(row["reason"])
            self.assertRegex(row["identity"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.archives.calls, [])

    async def test_search_matches_public_names_descriptions_and_tags(self):
        for query, expected in (("ALPHA", ["decky-1"]), ("picture capture", ["decky-2"]), ("sound", ["decky-1", "decky-3"])):
            with self.subTest(query=query):
                page = await self.catalog.list({"query": query, "sort": "name"})
                self.assertEqual(self.ids(page), expected)
                self.assertEqual(page["total"], len(expected))
        self.assertEqual(len(self.fetcher.calls), 1)
        self.assertEqual(self.archives.calls, [])

    async def test_category_and_search_filters_combine(self):
        page = await self.catalog.list({"category": "utilities"})
        self.assertEqual(self.ids(page), ["decky-2"])
        self.assertIn("audio", page["categories"])
        self.assertIn("appearance", page["categories"])
        absent = await self.catalog.list({"category": "audio", "query": "screen"})
        self.assertEqual((absent["rows"], absent["total"]), ([], 0))

    async def test_updated_sort_and_pages_keep_the_filtered_total(self):
        full = await self.catalog.list({"sort": "updated"})
        self.assertEqual(self.ids(full), ["decky-2", "decky-3", "decky-1"])
        pages = [await self.catalog.list({"sort": "updated", "offset": offset, "limit": 1}) for offset in range(3)]
        self.assertEqual([self.ids(page)[0] for page in pages], self.ids(full))
        self.assertEqual([page["total"] for page in pages], [3, 3, 3])
        beyond = await self.catalog.list({"offset": 10, "limit": 1})
        self.assertEqual((beyond["rows"], beyond["total"]), ([], 3))

    async def test_relevance_places_a_name_match_before_a_description_match(self):
        self.fetcher.routes[STABLE_URL] = [
            upstream_plugin(1, "Alpha Audio", "Screen controls", ["audio"], "2026-04-01T00:00:00Z"),
            upstream_plugin(2, "Screen Tools", "Display controls", ["utilities"], "2026-04-01T00:00:00Z"),
        ]
        alphabetical = await self.catalog.list({"query": "screen", "sort": "name"})
        self.assertEqual(self.ids(alphabetical), ["decky-1", "decky-2"])
        relevant = await self.catalog.list({"query": "screen", "sort": "relevance"})
        self.assertEqual(self.ids(relevant), ["decky-2", "decky-1"])

    async def test_details_offer_version_metadata_without_fetching_an_archive(self):
        page = await self.catalog.list()
        listed = page["rows"][0]
        self.assertNotIn("versions", listed)
        details = await self.catalog.details({"id": "decky-1"})
        entry = details["entry"]
        self.assertEqual(entry["identity"], listed["identity"])
        self.assertEqual(entry["version"], "2.0.0")
        self.assertEqual(entry["version_count"], 2)
        self.assertEqual([v["name"] for v in entry["versions"]], ["2.0.0", "1.0.0"])
        self.assertEqual([v["hash"] for v in entry["versions"]], ["a" * 64, "b" * 64])
        for item in entry["versions"]:
            self.assertEqual((item["downloads"], item["updates"]), (120, 7))
            self.assertRegex(item["identity"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(entry["versions"][0]["identity"], entry["versions"][1]["identity"])
        self.assertEqual(entry["author"], "Example Author")
        self.assertEqual(self.archives.calls, [])
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_upstream_images_and_unknown_fields_are_not_projected(self):
        page = await self.catalog.list()
        details = await self.catalog.details({"id": "decky-1"})
        projected = json.dumps({"page": page["rows"], "entry": details["entry"]})
        self.assertNotIn("untrusted.example", projected)
        self.assertNotIn("unknown_upstream_field", projected)
        self.assertNotIn("screenshots", projected)
        self.assertEqual(self.archives.calls, [])

    async def test_package_inspection_without_confirmation_does_not_fetch_an_archive(self):
        details = await self.catalog.details({"id": "decky-1"})
        entry = details["entry"]
        result = await self.catalog.assess({
            "id": entry["id"], "source": "decky_stable", "identity": entry["identity"],
            "version_identity": entry["versions"][0]["identity"], "confirm": False,
        })
        self.assertTrue(result["error"])
        self.assertTrue(result["code"])
        self.assertEqual(self.archives.calls, [])

    async def test_version_details_are_bounded_and_keep_upstream_selection_order(self):
        versions = [
            {"name": "1.0.%d" % index, "hash": hashlib.sha256(str(index).encode()).hexdigest(),
             "created": "2026-04-01T00:00:00Z", "downloads": index, "updates": 0}
            for index in range(60)
        ]
        self.fetcher.routes[STABLE_URL] = [upstream_plugin(1, "Versioned Module", "Many releases", ["tools"], "2026-04-01T00:00:00Z", versions=versions)]
        page = await self.catalog.list()
        self.assertEqual((page["rows"][0]["version"], page["rows"][0]["version_count"]), ("1.0.0", 60))
        details = await self.catalog.details({"id": "decky-1"})
        entry = details["entry"]
        self.assertEqual(entry["version_count"], 60)
        self.assertEqual([item["name"] for item in entry["versions"]], [item["name"] for item in versions[:50]])
        self.assertEqual(self.archives.calls, [])

    async def test_fresh_metadata_cache_survives_adapter_restart(self):
        downloaded = await self.catalog.list()
        self.fetcher.failure = OSError("offline fixture")
        self.host.clock += 60
        restarted = fy_decky_catalog.DeckyCatalog(self.host, fetcher=self.fetcher, archive_fetcher=self.archives)
        cached = await restarted.list()
        self.assertEqual(self.ids(cached), self.ids(downloaded))
        self.assertEqual(cached["origin"], "cache")
        self.assertFalse(cached["stale"])
        self.assertIsNone(cached["error"])
        self.assertEqual(cached["downloaded_at"], downloaded["downloaded_at"])
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_expired_metadata_cache_remains_browsable_when_offline(self):
        downloaded = await self.catalog.list()
        self.host.clock += 24 * 60 * 60 + 1
        self.fetcher.failure = OSError("offline fixture")
        stale = await self.catalog.list()
        self.assertEqual(self.ids(stale), self.ids(downloaded))
        self.assertEqual(stale["origin"], "cache")
        self.assertTrue(stale["stale"])
        self.assertIsNone(stale["error"])
        self.assertTrue(stale["fetch_error"])
        self.assertTrue(stale["warning"])
        self.assertEqual(stale["downloaded_at"], downloaded["downloaded_at"])
        self.assertEqual(len(self.fetcher.calls), 2)
        self.assertEqual(self.archives.calls, [])

    async def test_offline_first_use_is_unavailable_without_native_bundled_rows(self):
        bundled = self.host.ROOT / "catalog" / "index.json"
        bundled.parent.mkdir()
        bundled.write_text(json.dumps(native_feed()))
        self.fetcher.failure = OSError("offline fixture")
        page = await self.catalog.list()
        self.assertEqual((page["rows"], page["total"]), ([], 0))
        self.assertEqual(page["origin"], "unavailable")
        self.assertIsNone(page["error"])
        self.assertTrue(page["fetch_error"])
        self.assertTrue(page["warning"])
        self.assertEqual(self.archives.calls, [])

    async def test_refresh_fetches_new_metadata_without_fetching_a_package(self):
        await self.catalog.list()
        self.fetcher.routes[STABLE_URL] = [upstream_plugin(8, "Replacement", "New public module", ["tools"], "2026-09-01T00:00:00Z")]
        self.host.clock += 10
        page = await self.catalog.list({"refresh": True})
        self.assertEqual(self.ids(page), ["decky-8"])
        self.assertEqual(page["origin"], "downloaded")
        self.assertEqual(page["downloaded_at"], self.host.clock)
        self.assertEqual(len(self.fetcher.calls), 2)
        self.assertEqual(self.archives.calls, [])

    async def test_testing_source_requires_developer_mode_and_uses_a_separate_cache(self):
        stable = await self.catalog.list()
        denied = await self.catalog.list({"source": "decky_testing"})
        self.assertEqual(denied.get("code"), "dev_off")
        self.assertEqual(len(self.fetcher.calls), 1)
        self.host.state["prefs"]["dev"]["enabled"] = True
        self.fetcher.routes[TESTING_URL] = [upstream_plugin(9, "Testing Module", "Preview catalog", ["preview"], "2026-09-01T00:00:00Z")]
        testing = await self.catalog.list({"source": "decky_testing"})
        self.assertEqual(self.ids(testing), ["decky-9"])
        self.assertEqual(testing["source"]["url"], TESTING_URL)
        restored = await self.catalog.list({"source": "decky_stable"})
        self.assertEqual(self.ids(restored), self.ids(stable))
        self.assertEqual(restored["origin"], "cache")
        self.assertEqual([call[0] for call in self.fetcher.calls], [STABLE_URL, TESTING_URL])

    async def test_native_catalog_keeps_its_entries_and_cache_when_switching_to_decky(self):
        service = fy_catalog.Catalog(self.host, fetcher=self.fetcher, archive_fetcher=self.archives)
        native = await service.list({"source": "stable"})
        self.assertEqual(self.ids(native), ["native-module"])
        decky = await service.list({"source": "decky_stable"})
        self.assertEqual(self.ids(decky), ["decky-1", "decky-2", "decky-3"])
        details = await service.details({"source": "decky_stable", "id": "decky-1"})
        self.assertEqual(details["entry"]["format"], "decky")
        self.assertTrue(details["entry"]["read_only"])
        restored = await service.list()
        self.assertEqual(self.ids(restored), ["native-module"])
        self.assertEqual(restored["source"]["id"], "stable")
        self.assertEqual(restored["origin"], "cache")
        self.assertEqual([call[0] for call in self.fetcher.calls], [fy_catalog.DEFAULT_URL, STABLE_URL])
        self.assertEqual(self.archives.calls, [])

    async def test_decky_sources_are_advertised_separately_without_changing_native_selection(self):
        service = fy_catalog.Catalog(self.host, fetcher=self.fetcher)
        choices = await service.sources()
        self.assertEqual({source["id"] for source in choices["sources"]}, {"stable", "testing", "custom"})
        self.assertEqual({source["id"] for source in choices["decky_sources"]}, {"decky_stable", "decky_testing"})
        denied = await service.sources({"source": "decky_stable"})
        self.assertEqual(denied.get("code"), "bad_source")
        self.assertEqual((await service.sources())["source"]["id"], "stable")
        restarted = fy_catalog.Catalog(self.host, fetcher=self.fetcher)
        page = await restarted.list()
        self.assertEqual(page["source"]["id"], "stable")
        self.assertEqual(self.ids(page), ["native-module"])
        self.assertEqual(len(self.fetcher.calls), 1)


if __name__ == "__main__":
    unittest.main()
