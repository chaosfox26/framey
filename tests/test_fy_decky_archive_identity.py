import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog as decky


def bundle(version):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps({"name": "Sample Module", "flags": []}))
        archive.writestr("package.json", json.dumps({"name": "sample-module", "version": version}))
        archive.writestr("dist/index.js", "throw new Error('inspection must not execute this file');")
        archive.writestr("main.py", "raise RuntimeError('inspection must not import this file')\n")
    return out.getvalue()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class FakeHost:
    def __init__(self, root):
        self.HOME, self.ROOT = root / "home", root / "loader"
        self.PLUGINS, self.CONF = self.ROOT / "plugins", self.HOME / "config" / "settings.json"
        for directory in (self.HOME, self.ROOT, self.PLUGINS, self.CONF.parent):
            directory.mkdir(parents=True, exist_ok=True)
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.0.2", "linux-aarch64", 1
        self.state = {"prefs": {"dev": {"enabled": False}}}
        self.clock = 1_790_000_000.0
        self.stage_swap = mock.Mock(side_effect=AssertionError("inspection attempted installation"))
        self.reload = mock.AsyncMock(side_effect=AssertionError("inspection attempted runtime reload"))

    def settings(self):
        return copy.deepcopy(self.state)

    def now(self):
        return self.clock

    def log(self, *args):
        pass


class SelectedDeckyArchiveIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-identity-")
        self.addCleanup(temporary.cleanup)
        self.host = FakeHost(Path(temporary.name))
        self.old_bytes, self.new_bytes = bundle("1.0.0"), bundle("2.0.0")
        self.old_hash, self.new_hash = digest(self.old_bytes), digest(self.new_bytes)
        self.feed = [{
            "id": 7,
            "name": "Sample Module",
            "author": "Example Maintainer",
            "description": "A static inspection fixture",
            "tags": ["tools"],
            "versions": [
                {"name": "2.0.0", "hash": self.new_hash},
                {"name": "1.0.0", "hash": self.old_hash},
            ],
        }]
        self.feed_calls, self.archive_calls = [], []
        self.payloads = {
            decky.CDN + self.new_hash + ".zip": self.new_bytes,
            decky.CDN + self.old_hash + ".zip": self.old_bytes,
        }

        async def feed_fetcher(url, max_bytes):
            self.feed_calls.append(url)
            return json.dumps(self.feed).encode()

        async def archive_fetcher(url, max_bytes):
            self.archive_calls.append(url)
            return self.payloads[url]

        self.feed_fetcher, self.archive_fetcher = feed_fetcher, archive_fetcher
        self.service = decky.DeckyCatalog(self.host, fetcher=feed_fetcher, archive_fetcher=archive_fetcher)
        network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("test attempted a real download"))
        network.start()
        self.addCleanup(network.stop)

    async def details(self, **changes):
        response = await self.service.details({"id": "decky-7", "source": "decky_stable", **changes})
        self.assertNotIn("code", response, response)
        return response["entry"]

    def request(self, entry, position=1, **changes):
        return {
            "id": "decky-7", "source": "decky_stable", "identity": entry["identity"],
            "version_identity": entry["versions"][position]["identity"], "confirm": True,
            **changes,
        }

    def assert_read_only(self):
        self.host.stage_swap.assert_not_called()
        self.host.reload.assert_not_awaited()
        self.assertEqual(list(self.host.PLUGINS.iterdir()), [])

    def assert_receipt(self, result, entry, position=1, checked_at=None):
        version = entry["versions"][position]
        self.assertEqual(result["source"], decky.SOURCES["decky_stable"])
        self.assertEqual(result["artifact"], decky.CDN + version["hash"] + ".zip")
        self.assertEqual(result["identity"], entry["identity"])
        self.assertEqual(result["version_identity"], version["identity"])
        self.assertEqual((result["version"], result["hash"]), (version["name"], version["hash"]))
        timestamp = self.host.clock if checked_at is None else checked_at
        self.assertEqual(result["checked_at"], timestamp)
        self.assertEqual(result["downloaded_at"], timestamp)

    async def test_nonfirst_selected_version_controls_download_and_result(self):
        entry = await self.details()
        result = await self.service.assess(self.request(entry))
        self.assert_receipt(result, entry)
        self.assertEqual((result["version"], result["hash"]), ("1.0.0", self.old_hash))
        self.assertEqual(self.archive_calls, [decky.CDN + self.old_hash + ".zip"])
        self.assertTrue(result["assessment"]["verified_sha256"])
        self.assertTrue(result["assessment"]["not_executed"])
        self.assertTrue(result["assessment"]["reasons"])
        self.assert_read_only()

    async def test_equal_version_labels_are_disambiguated_by_identity_and_digest(self):
        self.feed[0]["versions"][0]["name"] = "1.0.0"
        entry = await self.details()
        self.assertNotEqual(entry["versions"][0]["identity"], entry["versions"][1]["identity"])
        result = await self.service.assess(self.request(entry))
        self.assert_receipt(result, entry)
        self.assertEqual(result["hash"], self.old_hash)
        self.assertEqual(self.archive_calls, [decky.CDN + self.old_hash + ".zip"])
        self.assert_read_only()

    async def test_artifact_url_change_invalidates_the_previous_confirmation(self):
        entry = await self.details()
        self.feed[0]["versions"][1]["artifact"] = "https://downloads.example.org/replaced.zip"
        current = await self.details(refresh=True)
        self.assertNotEqual(current["versions"][1]["identity"], entry["versions"][1]["identity"])
        result = await self.service.assess(self.request(entry))
        self.assertEqual(result["code"], "stale_catalog")
        self.assertEqual(self.archive_calls, [])
        self.assert_read_only()

    async def test_hash_change_invalidates_the_previous_confirmation(self):
        entry = await self.details()
        self.feed[0]["versions"][1]["hash"] = self.new_hash
        await self.details(refresh=True)
        result = await self.service.assess(self.request(entry))
        self.assertEqual(result["code"], "stale_catalog")
        self.assertEqual(self.archive_calls, [])

    async def test_version_label_change_invalidates_the_previous_confirmation(self):
        entry = await self.details()
        self.feed[0]["versions"][1]["name"] = "renamed-release"
        await self.details(refresh=True)
        result = await self.service.assess(self.request(entry))
        self.assertEqual(result["code"], "stale_catalog")
        self.assertEqual(self.archive_calls, [])

    async def test_caller_supplied_hash_and_artifact_do_not_override_reviewed_source(self):
        entry = await self.details()
        request = self.request(entry, hash=self.new_hash, artifact="https://downloads.example.org/unreviewed.zip")
        result = await self.service.assess(request)
        self.assert_receipt(result, entry)
        self.assertEqual(result["hash"], self.old_hash)
        self.assertEqual(self.archive_calls, [decky.CDN + self.old_hash + ".zip"])
        self.assert_read_only()

    async def test_hash_mismatch_cannot_produce_a_successful_assessment(self):
        entry = await self.details()
        self.payloads[decky.CDN + self.old_hash + ".zip"] = self.new_bytes
        result = await self.service.assess(self.request(entry))
        self.assertEqual(result["code"], "digest_mismatch")
        self.assertNotIn("assessment", result)
        self.assert_read_only()

    async def test_failed_download_identity_does_not_poison_a_later_correct_assessment(self):
        entry = await self.details()
        url = decky.CDN + self.old_hash + ".zip"
        self.payloads[url] = self.new_bytes
        self.assertEqual((await self.service.assess(self.request(entry)))["code"], "digest_mismatch")
        self.payloads[url] = self.old_bytes
        result = await self.service.assess(self.request(entry))
        self.assert_receipt(result, entry)
        self.assertEqual(result["hash"], self.old_hash)
        self.assertTrue(result["assessment"]["verified_sha256"])
        self.assertEqual(self.archive_calls, [url, url])

    async def test_feed_refresh_during_download_does_not_relabel_selected_bytes(self):
        entry = await self.details()
        reviewed_at = self.host.clock

        async def refresh_during_download(url, max_bytes):
            self.archive_calls.append(url)
            self.feed[0]["versions"][1] = {"name": "1.1.0", "hash": self.new_hash}
            self.host.clock += 60
            await self.details(refresh=True)
            return self.old_bytes

        self.service.archive_fetcher = refresh_during_download
        result = await self.service.assess(self.request(entry))
        self.assert_receipt(result, entry, checked_at=reviewed_at)
        self.assertEqual((result["version"], result["hash"]), ("1.0.0", self.old_hash))
        self.assertEqual(self.archive_calls, [decky.CDN + self.old_hash + ".zip"])
        self.assertEqual(len(self.feed_calls), 2)
        self.assert_read_only()

    async def test_testing_catalog_cannot_reuse_a_stable_confirmation(self):
        entry = await self.details()
        self.host.state["prefs"]["dev"]["enabled"] = True
        result = await self.service.assess(self.request(entry, source="decky_testing"))
        self.assertEqual(result["code"], "stale_catalog")
        self.assertEqual(self.archive_calls, [])
        self.assertEqual(len(self.feed_calls), 2)

    async def test_restarted_metadata_cache_preserves_exact_selected_version_identity(self):
        entry = await self.details()
        reviewed_at = self.host.clock
        self.host.clock += 60
        self.service = decky.DeckyCatalog(self.host, fetcher=self.feed_fetcher, archive_fetcher=self.archive_fetcher)
        cached = await self.details()
        self.assertEqual(cached["identity"], entry["identity"])
        self.assertEqual(cached["versions"][1]["identity"], entry["versions"][1]["identity"])
        result = await self.service.assess(self.request(cached))
        self.assert_receipt(result, cached, checked_at=reviewed_at)
        self.assertEqual((result["version"], result["hash"]), ("1.0.0", self.old_hash))
        self.assertEqual(len(self.feed_calls), 1)
        self.assert_read_only()


if __name__ == "__main__":
    unittest.main()
