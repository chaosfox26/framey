import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_catalog


COMMIT = "a" * 40
CUSTOM_URL = "https://raw.githubusercontent.com/example/framey-catalog/main/index.json"


def plugin(pid, name, description, category, tags, updated, **extra):
    return {
        "id": pid,
        "name": name,
        "version": "1.0.0",
        "description": description,
        "category": category,
        "tags": tags,
        "updated": updated,
        "channel": "stable",
        "kind": "plugin",
        "api": 1,
        "platform": ["linux-aarch64"],
        "source": {
            "repository": "https://github.com/example/framey-plugins",
            "commit": COMMIT,
            "path": pid,
        },
        **extra,
    }


def catalog_data(rows=None):
    return {
        "schema": 1,
        "name": "Framey catalog",
        "plugins": rows if rows is not None else [
            plugin("alpha", "Alpha Audio", "Spatial listening tools", "audio", ["sound", "utility"], "2026-04-02"),
            plugin("bravo", "Bravo Tools", "Picture capture", "utilities", ["screen"], "2026-06-01"),
            plugin("charlie", "Charlie UI", "Scenic overlays", "appearance", ["sound", "visual"], "2026-05-01"),
            plugin("delta", "Delta Desktop", "Shortcut manager", "utilities", ["desktop"], "2026-03-01"),
            plugin("future", "Future API", "Requires a newer API", "utilities", ["future"], "2026-01-01", api=2),
            plugin("testflight", "Test Flight", "Preview feature", "utilities", ["preview"], "2026-07-01", channel="testing"),
            plugin("preview", "Preview Release", "Prerelease build", "utilities", ["preview"], "2026-08-01", version="2.0.0-rc.1"),
        ],
    }


class FakeHost:
    def __init__(self, root):
        self.ROOT = root / "loader"
        self.HOME = root / "home"
        self.PLUGINS = self.ROOT / "plugins"
        self.CONF = self.HOME / ".config" / "framey" / "state.json"
        self.VERSION = "1.0.2"
        self.PLATFORM = "linux-aarch64"
        self.API_GENERATION = 1
        self.clock = 1_750_000_000.0
        self.state = {"prefs": {"dev": {"enabled": False}}}
        self.pending_updates = {}
        self.logs = []
        for path in (self.ROOT, self.HOME, self.PLUGINS, self.CONF.parent):
            path.mkdir(parents=True, exist_ok=True)

    def now(self):
        return self.clock

    def settings(self):
        return copy.deepcopy(self.state)

    def pending(self, pid):
        return self.pending_updates.get(pid)

    def log(self, *args):
        self.logs.append(args)

    def manifests(self):
        result = []
        for path in sorted(self.PLUGINS.iterdir()):
            try:
                manifest = json.loads((path / "plugin.json").read_text())
            except (OSError, ValueError):
                continue
            result.append({**manifest, "id": path.name})
        return result

    def add_installed(self, pid, version="0.9.0"):
        path = self.PLUGINS / pid
        path.mkdir()
        (path / "plugin.json").write_text(json.dumps({"id": pid, "name": pid.title(), "version": version}))
        return path

    def add_managed(self, pid, version="0.9.0", commit="b" * 40):
        path = self.add_installed(pid, version)
        source = {"repository": "https://github.com/example/framey-plugins", "commit": commit, "path": pid}
        (path / ".catalog.json").write_text(json.dumps({"v": 1, "id": pid, "source": source}))
        return path


class ControlledFetcher:
    def __init__(self, data):
        self.data = data
        self.routes = {}
        self.failure = None
        self.calls = []

    async def __call__(self, url, max_bytes):
        self.calls.append((url, max_bytes))
        if self.failure:
            raise self.failure
        result = self.routes.get(url, self.data)
        return json.dumps(result).encode("utf-8")


class CatalogFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-catalog-flow-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = FakeHost(self.root)
        self.fetcher = ControlledFetcher(catalog_data())
        self.catalog = fy_catalog.Catalog(self.host, fetcher=self.fetcher)
        network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("unit test attempted real network access"))
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def ids(page):
        return [row["id"] for row in page["rows"]]

    async def test_browse_downloads_stable_entries_with_source_and_status(self):
        page = await self.catalog.list()
        self.assertEqual(self.ids(page), ["alpha", "bravo", "charlie", "delta", "future"])
        self.assertEqual((page["total"], page["offset"], page["limit"]), (5, 0, 50))
        self.assertEqual(page["origin"], "downloaded")
        self.assertEqual(page["source"]["id"], "stable")
        self.assertTrue(page["source"]["url"].startswith("https://"))
        self.assertFalse(page["stale"])
        self.assertIsNone(page["error"])
        self.assertEqual(page["downloaded_at"], self.host.clock)
        self.assertEqual(len(self.fetcher.calls), 1)
        self.assertGreater(self.fetcher.calls[0][1], 0)
        for row in page["rows"]:
            self.assertRegex(row["identity"], r"^[0-9a-f]{64}$")
        self.assertIn("audio", page["categories"])
        self.assertIn("utilities", page["categories"])

    async def test_search_finds_name_description_and_tags_without_case_sensitivity(self):
        for query, expected in (("ALPHA", ["alpha"]), ("picture capture", ["bravo"]), ("sound", ["alpha", "charlie"])):
            with self.subTest(query=query):
                page = await self.catalog.list({"query": query, "sort": "name"})
                self.assertEqual(self.ids(page), expected)
                self.assertEqual(page["total"], len(expected))
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_category_and_search_are_combined(self):
        utilities = await self.catalog.list({"category": "utilities"})
        self.assertEqual(self.ids(utilities), ["bravo", "delta", "future"])
        filtered = await self.catalog.list({"category": "utilities", "query": "desktop"})
        self.assertEqual(self.ids(filtered), ["delta"])
        absent = await self.catalog.list({"category": "audio", "query": "desktop"})
        self.assertEqual((absent["rows"], absent["total"]), ([], 0))

    async def test_relevance_ranks_a_name_match_ahead_of_a_description_match(self):
        alphabetical = await self.catalog.list({"query": "tools", "sort": "name"})
        self.assertEqual(self.ids(alphabetical), ["alpha", "bravo"])
        relevant = await self.catalog.list({"query": "tools", "sort": "relevance"})
        self.assertEqual(self.ids(relevant), ["bravo", "alpha"])

    async def test_updated_sort_and_pagination_describe_the_same_filtered_result(self):
        full = await self.catalog.list({"sort": "updated"})
        self.assertEqual(self.ids(full), ["bravo", "charlie", "alpha", "delta", "future"])
        first = await self.catalog.list({"sort": "updated", "limit": 2})
        second = await self.catalog.list({"sort": "updated", "limit": 2, "offset": 2})
        last = await self.catalog.list({"sort": "updated", "limit": 2, "offset": 4})
        self.assertEqual(self.ids(first) + self.ids(second) + self.ids(last), self.ids(full))
        self.assertEqual([page["total"] for page in (first, second, last)], [5, 5, 5])
        self.assertEqual((second["offset"], second["limit"]), (2, 2))
        beyond = await self.catalog.list({"offset": 99, "limit": 2})
        self.assertEqual((beyond["rows"], beyond["total"]), ([], 5))

    async def test_pagination_accepts_maximum_and_rejects_an_oversized_page(self):
        page = await self.catalog.list({"limit": 100})
        self.assertEqual(page["limit"], 100)
        self.assertEqual(page["total"], 5)
        invalid = await self.catalog.list({"limit": 100_000})
        self.assertEqual(invalid.get("code"), "bad_arg")
        self.assertNotIn("rows", invalid)

    async def test_details_preserve_entry_identity_and_install_source(self):
        page = await self.catalog.list()
        listed = next(row for row in page["rows"] if row["id"] == "alpha")
        details = await self.catalog.details({"id": "alpha"})
        entry = details["entry"]
        self.assertEqual(entry["identity"], listed["identity"])
        self.assertEqual(entry["description"], "Spatial listening tools")
        self.assertEqual(entry["tags"], ["sound", "utility"])
        self.assertEqual(entry["source"], catalog_data()["plugins"][0]["source"])
        self.assertEqual(details["source"]["id"], "stable")
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_details_find_entries_past_the_first_hundred_with_the_same_status(self):
        self.fetcher.data = catalog_data([
            plugin("module-%03d" % i, "Module %03d" % i, "Catalog module", "tools", [], "2026-04-02")
            for i in range(120)
        ])
        page = await self.catalog.list({"offset": 100, "limit": 100})
        details = await self.catalog.details({"id": "module-119", "query": "absent", "offset": -1})
        self.assertEqual(details["entry"], page["rows"][-1])
        self.assertEqual(set(details), {"entry", "source", "origin", "stale", "checked_at", "downloaded_at", "error", "fetch_error", "warning"})
        self.assertEqual(details["source"], page["source"])
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_details_apply_native_visibility_and_developer_gating(self):
        for pid in ("testflight", "preview"):
            with self.subTest(pid=pid):
                for source in ("stable", "custom"):
                    arg = {"id": pid, "source": source}
                    if source == "custom":
                        arg["url"] = CUSTOM_URL
                    hidden = await self.catalog.details(arg)
                    self.assertEqual(hidden, {"error": "Module is not in this catalog", "code": "not_found"})
                denied = await self.catalog.details({"id": pid, "source": "testing"})
                self.assertEqual(denied["code"], "dev_off")
        self.host.state["prefs"]["dev"]["enabled"] = True
        for pid in ("testflight", "preview"):
            details = await self.catalog.details({"id": pid, "source": "testing"})
            self.assertEqual(details["entry"]["id"], pid)
            self.assertEqual(details["source"]["id"], "testing")

    async def test_details_preserve_refresh_failure_status_and_error_shapes(self):
        await self.catalog.list()
        self.fetcher.failure = OSError("offline")
        self.host.clock += fy_catalog.TTL + 1
        details = await self.catalog.details({"id": "alpha", "refresh": True})
        self.assertTrue(details["stale"])
        self.assertEqual(details["origin"], "cache")
        self.assertIsNone(details["error"])
        self.assertTrue(details["fetch_error"])
        self.assertEqual(details["warning"], "Saved catalog; refresh failed")
        invalid = await self.catalog.details({"id": "not valid"})
        self.assertEqual(invalid, {"error": "Choose a catalog module", "code": "bad_arg"})
        missing = await self.catalog.details({"id": "absent"})
        self.assertEqual(missing, {"error": "Module is not in this catalog", "code": "not_found"})

    async def test_fresh_cache_survives_a_new_catalog_instance_without_fetching(self):
        downloaded = await self.catalog.list()
        self.fetcher.failure = OSError("offline")
        self.host.clock += 60
        restarted = fy_catalog.Catalog(self.host, fetcher=self.fetcher)
        cached = await restarted.list()
        self.assertEqual(self.ids(cached), self.ids(downloaded))
        self.assertEqual(cached["origin"], "cache")
        self.assertFalse(cached["stale"])
        self.assertEqual(cached["downloaded_at"], downloaded["downloaded_at"])
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_expired_cache_reports_offline_and_keeps_the_last_good_rows(self):
        downloaded = await self.catalog.list()
        self.host.clock += 24 * 60 * 60 + 1
        self.fetcher.failure = OSError("offline fixture")
        stale = await self.catalog.list()
        self.assertEqual(self.ids(stale), self.ids(downloaded))
        self.assertEqual(stale["origin"], "cache")
        self.assertTrue(stale["stale"])
        self.assertIsNone(stale["error"])
        self.assertTrue(stale["fetch_error"])
        self.assertEqual(stale["downloaded_at"], downloaded["downloaded_at"])
        self.assertEqual(len(self.fetcher.calls), 2)

    async def test_refresh_bypasses_fresh_cache_and_replaces_its_contents(self):
        await self.catalog.list()
        self.fetcher.data = catalog_data([plugin("replacement", "Replacement", "New catalog entry", "tools", ["new"], "2026-09-01")])
        self.host.clock += 10
        refreshed = await self.catalog.list({"refresh": True})
        self.assertEqual(self.ids(refreshed), ["replacement"])
        self.assertEqual(refreshed["origin"], "downloaded")
        self.assertEqual(refreshed["downloaded_at"], self.host.clock)
        self.assertEqual(len(self.fetcher.calls), 2)

    async def test_offline_first_use_reports_bundled_fallback(self):
        self.fetcher.failure = OSError("offline fixture")
        page = await self.catalog.list()
        self.assertEqual(page["origin"], "bundled")
        self.assertIsNone(page["error"])
        self.assertTrue(page["fetch_error"])
        self.assertIsInstance(page["rows"], list)
        self.assertEqual(page["source"]["id"], "stable")
        self.assertEqual(len(self.fetcher.calls), 1)

    async def test_custom_source_selection_persists_and_keeps_its_own_cache(self):
        stable = await self.catalog.list()
        self.fetcher.routes[CUSTOM_URL] = catalog_data([plugin("custom", "Custom Module", "Private catalog", "tools", ["custom"], "2026-10-01")])
        await self.catalog.sources({"source": "custom", "url": CUSTOM_URL})
        custom = await self.catalog.list()
        self.assertEqual(self.ids(custom), ["custom"])
        self.assertEqual((custom["source"]["id"], custom["source"]["url"]), ("custom", CUSTOM_URL))
        restarted = fy_catalog.Catalog(self.host, fetcher=self.fetcher)
        self.assertEqual(self.ids(await restarted.list()), ["custom"])
        await restarted.sources({"source": "stable"})
        restored = await restarted.list()
        self.assertEqual(self.ids(restored), self.ids(stable))
        self.assertEqual(restored["source"]["id"], "stable")
        self.assertEqual(restored["origin"], "cache")
        self.assertEqual(len(self.fetcher.calls), 2)

    async def test_testing_source_requires_developer_mode_and_includes_previews(self):
        denied = await self.catalog.list({"source": "testing"})
        self.assertEqual(denied.get("code"), "dev_off")
        self.assertEqual(self.fetcher.calls, [])
        self.host.state["prefs"]["dev"]["enabled"] = True
        testing = await self.catalog.list({"source": "testing"})
        self.assertIn("testflight", self.ids(testing))
        self.assertIn("preview", self.ids(testing))
        self.assertEqual(testing["source"]["id"], "testing")

    async def test_installed_and_compatibility_state_control_install_eligibility(self):
        self.host.add_installed("alpha")
        page = await self.catalog.list()
        rows = {row["id"]: row for row in page["rows"]}
        self.assertTrue(rows["alpha"]["installed"])
        self.assertFalse(rows["alpha"]["linked"])
        self.assertFalse(rows["alpha"]["eligible"])
        self.assertTrue(rows["alpha"]["reason"])
        self.assertFalse(rows["bravo"]["installed"])
        self.assertEqual(rows["bravo"]["compat"]["state"], "ok")
        self.assertTrue(rows["bravo"]["eligible"])
        self.assertEqual(rows["future"]["compat"]["state"], "incompatible")
        self.assertFalse(rows["future"]["eligible"])
        self.assertTrue(rows["future"]["reason"])

    async def test_undeclared_compatibility_is_visible_without_claiming_support(self):
        undeclared = plugin("unknown", "Unknown Support", "No compatibility declaration", "tools", [], "2026-01-01")
        undeclared.pop("api")
        undeclared.pop("platform")
        self.fetcher.data = catalog_data([undeclared])
        page = await self.catalog.list()
        row = page["rows"][0]
        self.assertEqual(row["compat"]["state"], "unknown")
        self.assertFalse(row["platform_declared"])
        self.assertTrue(row["eligible"])

    async def test_catalog_managed_module_can_offer_a_new_pinned_commit(self):
        self.host.add_managed("alpha")
        page = await self.catalog.list()
        row = next(row for row in page["rows"] if row["id"] == "alpha")
        self.assertTrue(row["installed"])
        self.assertTrue(row["catalog_managed"])
        self.assertTrue(row["update_available"])
        self.assertTrue(row["eligible"])
        self.assertFalse(row["reason"])

    async def test_catalog_managed_module_at_the_same_commit_has_no_install_action(self):
        self.host.add_managed("alpha", version="1.0.0", commit=COMMIT)
        page = await self.catalog.list()
        row = next(row for row in page["rows"] if row["id"] == "alpha")
        self.assertTrue(row["catalog_managed"])
        self.assertFalse(row["update_available"])
        self.assertFalse(row["eligible"])
        self.assertTrue(row["reason"])

    async def test_catalog_managed_frozen_module_cannot_be_updated(self):
        self.host.add_managed("alpha")
        self.host.state["modules"] = {"frozen": {"alpha": {"version": "0.9.0", "at": 1}}}
        page = await self.catalog.list()
        row = next(row for row in page["rows"] if row["id"] == "alpha")
        self.assertTrue(row["catalog_managed"])
        self.assertTrue(row["update_available"])
        self.assertFalse(row["eligible"])
        self.assertTrue(row["reason"])

    async def test_catalog_managed_module_cannot_be_downgraded(self):
        self.host.add_managed("alpha", version="2.0.0")
        page = await self.catalog.list()
        row = next(row for row in page["rows"] if row["id"] == "alpha")
        self.assertTrue(row["catalog_managed"])
        self.assertFalse(row["eligible"])
        self.assertTrue(row["reason"])

    async def test_pending_recovery_prevents_another_catalog_update(self):
        self.host.add_managed("alpha")
        backup = self.host.PLUGINS / ".old-alpha"
        for pending_type in ("backup", "record"):
            with self.subTest(pending_type=pending_type):
                if pending_type == "backup":
                    backup.mkdir()
                else:
                    self.host.pending_updates["alpha"] = {"version": "0.9.0"}
                page = await self.catalog.list()
                row = next(row for row in page["rows"] if row["id"] == "alpha")
                self.assertTrue(row["catalog_managed"])
                self.assertFalse(row["eligible"])
                self.assertTrue(row["reason"])
                if backup.exists():
                    backup.rmdir()

    @unittest.skipUnless(os.name == "posix", "directory symlink fixture needs POSIX")
    async def test_linked_installed_module_is_visible_but_not_installable(self):
        external = self.root / "external"
        external.mkdir()
        (external / "plugin.json").write_text(json.dumps({"id": "bravo", "name": "Bravo Tools", "version": "0.8.0"}))
        (self.host.PLUGINS / "bravo").symlink_to(external, target_is_directory=True)
        page = await self.catalog.list()
        row = next(row for row in page["rows"] if row["id"] == "bravo")
        self.assertTrue(row["installed"])
        self.assertTrue(row["linked"])
        self.assertFalse(row["eligible"])

    async def test_setup_entry_has_no_plugin_install_action(self):
        setup = plugin("setup-guide", "Setup Guide", "Headset setup instructions", "setup", ["setup"], "2026-01-01", kind="setup")
        setup.pop("version")
        setup["source"] = {"repository": "https://github.com/example/framey-setup"}
        self.fetcher.data = catalog_data([setup])
        page = await self.catalog.list()
        self.assertEqual(self.ids(page), ["setup-guide"])
        row = page["rows"][0]
        self.assertEqual(row["kind"], "setup")
        self.assertFalse(row["eligible"])
        self.assertTrue(row["reason"])


if __name__ == "__main__":
    unittest.main()
