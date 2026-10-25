"""Offline media/privacy boundaries for Decky catalog browsing and assessment."""
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog
import fy_media
import test_fy_decky_catalog_flow as fixtures


ARTWORK = "https://artwork.example.invalid/tracker.svg"
MARKUP = '<svg onload="window.artworkExecuted=true"><script>artworkExecuted=true</script></svg>'


def package():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps({"name": MARKUP, "flags": [], "icon": "artwork.svg"}))
        archive.writestr("package.json", json.dumps({"dependencies": {MARKUP: "*"}, "image": ARTWORK}))
        archive.writestr("main.py", 'raise RuntimeError("Downloaded plugin code must never execute")\n')
        archive.writestr("dist/index.js", 'throw Error("Downloaded plugin code must never execute");')
        archive.writestr("artwork.svg", MARKUP)
    return buffer.getvalue()


class DeckyMediaPrivacy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = fixtures.FakeHost(self.root)
        self.previous = fy_media._media
        self.media = fy_media.Media(data_max=1)
        fy_media._media = self.media
        self.addAsyncCleanup(self.restore_media)
        self.dirs = types.SimpleNamespace(data=self.root / "data", runtime=self.root / "runtime", config=self.root / "config")
        for folder in vars(self.dirs).values():
            folder.mkdir()
            (folder / "clip.mp4").write_bytes(b"private plugin media")
        self.host.dirs = lambda pid: self.dirs
        self.resource = await self.media.url_for("owner", self.dirs.data / "clip.mp4")
        self.downloads, self.archives = [], []
        self.archive = package()
        digest = hashlib.sha256(self.archive).hexdigest()
        self.feed = [fixtures.upstream_plugin(1, MARKUP, MARKUP, ["media"], "2026-10-01", versions=[{
            "name": "1.0.0", "hash": digest, "created": "2026-10-01", "downloads": 0, "updates": 0,
        }], icon=ARTWORK, image_url=ARTWORK, screenshots=[ARTWORK], artwork=MARKUP,
            resource={"pid": "owner", "revoke": self.resource["token"]}, root="config", path="clip.mp4")]
        self.feed[0]["pid"] = "owner"
        network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("Test attempted real network access"))
        network.start()
        self.addCleanup(network.stop)
        self.catalog = fy_decky_catalog.DeckyCatalog(self.host, self.fetch, self.fetch_archive)

    async def restore_media(self):
        await self.media.close()
        fy_media._media = self.previous

    async def fetch(self, url, max_bytes):
        self.downloads.append((url, max_bytes))
        self.assertEqual(url, fixtures.STABLE_URL)
        return json.dumps(self.feed).encode()

    async def fetch_archive(self, url, max_bytes):
        self.archives.append((url, max_bytes))
        self.assertEqual(url, fy_decky_catalog.CDN + hashlib.sha256(self.archive).hexdigest() + ".zip")
        self.assertEqual(max_bytes, fy_decky_catalog.ARCHIVE_MAX)
        return self.archive

    def assert_resource_unchanged(self):
        self.assertEqual(list(self.media._tokens), [self.resource["token"]])
        self.assertEqual(self.media._tokens[self.resource["token"]][0], "owner")

    async def test_browse_and_details_drop_artwork_and_ignore_resource_shaped_metadata(self):
        with mock.patch.object(fy_media, "get", side_effect=AssertionError("Catalog touched the media service")):
            listing = await self.catalog.list()
            details = await self.catalog.details({"id": "decky-1"})
        for value in (listing, details, self.catalog.cache):
            encoded = json.dumps(value)
            self.assertNotIn(ARTWORK, encoded)
            self.assertNotIn(self.resource["token"], encoded)
            for field in ('"image_url":', '"screenshots":', '"artwork":', '"resource":', '"revoke":'):
                self.assertNotIn(field, encoded)
        self.assertEqual(listing["rows"][0]["name"], MARKUP)
        self.assertEqual(details["entry"]["description"], MARKUP)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.archives, [])
        self.assert_resource_unchanged()

    async def test_assessment_fetches_only_selected_archive_and_never_calls_media(self):
        with mock.patch.object(fy_media, "get", side_effect=AssertionError("Assessment touched the media service")):
            details = await self.catalog.details({"id": "decky-1"})
            item = details["entry"]
            result = await self.catalog.assess({"id": item["id"], "identity": item["identity"],
                                                "version_identity": item["versions"][0]["identity"], "confirm": True})
        self.assertNotIn("error", result)
        assessment = result["assessment"]
        self.assertTrue(assessment["not_executed"])
        self.assertTrue(assessment["verified_sha256"])
        self.assertEqual(assessment["architecture"], "unknown")
        self.assertNotEqual(assessment["status"], "compatible")
        self.assertEqual(assessment["dependencies"], [MARKUP])
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(len(self.archives), 1)
        self.assertNotIn(ARTWORK, json.dumps(result))
        self.assertEqual(list(self.host.PLUGINS.iterdir()), [])
        self.assert_resource_unchanged()

    async def test_unconfirmed_assessment_does_not_fetch_any_resource(self):
        with mock.patch.object(fy_media, "get", side_effect=AssertionError("Invalid assessment touched media")):
            result = await self.catalog.assess({"id": "decky-1", "image_url": ARTWORK, "revoke": self.resource["token"]})
        self.assertEqual(result["code"], "confirmation_required")
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.archives, [])
        self.assert_resource_unchanged()

    async def test_missing_or_invalid_owner_cannot_use_adapter_privileged_revoke(self):
        for pid in (None, "", "BAD PID", [], {}, 0, False):
            with self.subTest(pid=pid):
                args = {"revoke": self.resource["url"]}
                if pid is not None:
                    args["pid"] = pid
                self.assertEqual(await fy_media.resource(self.host, args), {"ok": False})
                self.assert_resource_unchanged()
        self.assertEqual(await fy_media.resource(self.host, {"pid": "other", "revoke": self.resource["token"]}), {"ok": False})
        self.assertEqual(await fy_media.resource(self.host, {"pid": "owner", "revoke": self.resource["token"]}), {"ok": True})

    async def test_catalog_fields_cannot_widen_page_resource_roots(self):
        for root in ("config", "home", "media", "logs"):
            result = await fy_media.resource(self.host, {"pid": "decky-1", "root": root, "path": "clip.mp4",
                                                         "format": "decky", "read_only": True, "source": ARTWORK})
            self.assertEqual(result["code"], "bad_root")
        result = await fy_media.resource(self.host, {"pid": "decky-1", "root": "data", "path": "../config/clip.mp4"})
        self.assertEqual(result["code"], "bad_path")
        result = await fy_media.resource(self.host, {"pid": "decky-1", "root": "data", "path": ARTWORK})
        self.assertIn(result["code"], ("bad_path", "not_found", "bad_type"))
        self.assert_resource_unchanged()

    async def test_cached_catalog_reload_does_not_serialize_artwork_or_media_tokens(self):
        await self.catalog.list()
        raw = self.catalog.path.read_text()
        self.assertNotIn(ARTWORK, raw)
        self.assertNotIn(self.resource["token"], raw)
        restarted = fy_decky_catalog.DeckyCatalog(self.host, self.fetch, self.fetch_archive)
        with mock.patch.object(fy_media, "get", side_effect=AssertionError("Cache reload touched media")):
            result = await restarted.details({"id": "decky-1"})
        self.assertEqual(result["origin"], "cache")
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.archives, [])
        self.assert_resource_unchanged()


if __name__ == "__main__":
    unittest.main()
