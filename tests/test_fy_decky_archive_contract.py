import copy
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


STABLE = "https://plugins.deckbrew.xyz/plugins"
TESTING = "https://testing.deckbrew.xyz/plugins"
NEW_URL = "https://downloads.example.org/new.zip"
OLD_URL = "https://downloads.example.org/old.zip"


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def package(version, native=False, decky_marker=True, framey_api=None):
    manifest = {"id": "sample", "name": "Sample", "version": version}
    if native or framey_api is not None:
        manifest["api"] = 1 if native else framey_api
    if decky_marker:
        manifest.update(api_version=1, author="Example", flags=[])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample/plugin.json", encoded(manifest))
        archive.writestr("sample/package.json", encoded({"name": "sample", "version": version}))
        archive.writestr("sample/dist/index.js", "throw new Error('inspection must not execute this bundle');")
        archive.writestr("sample/main.py", "raise AssertionError('inspection must not import this backend')\n")
        if native:
            archive.writestr("sample/main.js", "Framey.register({render(){}});")
            archive.writestr("sample/backend.py", "def call(method, arg):\n    return {}\n")
    return buffer.getvalue()


def store_entry(versions, **extra):
    return {"id": 4, "name": "Sample", "author": "Example", "description": "Schema contract fixture",
            "tags": ["utility"], "versions": versions, **extra}


class Host:
    def __init__(self, root):
        self.ROOT, self.HOME, self.PLUGINS = root / "loader", root / "home", root / "plugins"
        for path in (self.ROOT, self.HOME, self.PLUGINS):
            path.mkdir()
        self.CONF = self.HOME / "settings.json"
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.0.2", "linux-aarch64", 1
        self.state = {"prefs": {"dev": {"enabled": False}}}
        self.archive = mock.Mock(side_effect=AssertionError("inspection reached native installer"))
        self.stage_swap = mock.Mock(side_effect=AssertionError("inspection reached package staging"))
        self.reload = mock.AsyncMock(side_effect=AssertionError("inspection reached runtime reload"))

    def settings(self):
        return copy.deepcopy(self.state)

    def now(self):
        return 1_000_000.0

    def log(self, *args):
        pass


class DeckySelectedArtifactContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-selection-")
        self.addCleanup(temporary.cleanup)
        self.host = Host(Path(temporary.name))
        self.old, self.new = package("1.0.0"), package("2.0.0")
        self.old_hash, self.new_hash = hashlib.sha256(self.old).hexdigest(), hashlib.sha256(self.new).hexdigest()
        self.feed = [store_entry([
            {"name": "2.0.0", "hash": self.new_hash, "artifact": NEW_URL},
            {"name": "1.0.0", "hash": self.old_hash, "artifact": OLD_URL},
        ])]
        self.feed_calls, self.downloads = [], []

        async def fetcher(url, limit):
            self.feed_calls.append((url, limit))
            return encoded(self.feed)

        async def archive_fetcher(url, limit):
            self.downloads.append((url, limit))
            if url in (OLD_URL, decky.CDN + self.old_hash + ".zip"):
                return self.old
            if url == NEW_URL:
                return self.new
            raise AssertionError("unreviewed package URL")

        self.service = decky.DeckyCatalog(self.host, fetcher=fetcher, archive_fetcher=archive_fetcher)
        no_network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("fixture attempted real network access"))
        no_network.start()
        self.addCleanup(no_network.stop)

    async def review(self, source="decky_stable", refresh=False):
        result = await self.service.details({"id": "decky-4", "source": source, "refresh": refresh})
        self.assertIn("entry", result, result)
        return result["entry"]

    async def inspect(self, row, source="decky_stable", index=1, **extra):
        return await self.service.assess({"id": row["id"], "source": source, "identity": row["identity"],
                                         "version_identity": row["versions"][index]["identity"], "confirm": True, **extra})

    def assert_no_installation(self):
        self.host.archive.assert_not_called()
        self.host.stage_swap.assert_not_called()
        self.host.reload.assert_not_called()
        self.assertEqual(list(self.host.PLUGINS.iterdir()), [])

    async def test_selected_older_version_uses_its_own_artifact_and_checksum(self):
        result = await self.inspect(await self.review())
        self.assertEqual((result["version"], result["hash"]), ("1.0.0", self.old_hash))
        self.assertEqual(self.downloads, [(OLD_URL, decky.ARCHIVE_MAX)])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["assessment"]["state"], "port_required")
        self.assert_no_installation()

    async def test_absent_artifact_uses_selected_hash_in_the_official_cdn_path(self):
        self.feed[0]["versions"][1].pop("artifact")
        result = await self.inspect(await self.review())
        self.assertEqual(result["hash"], self.old_hash)
        self.assertEqual(self.downloads, [(decky.CDN + self.old_hash + ".zip", decky.ARCHIVE_MAX)])
        self.assert_no_installation()

    async def test_request_fields_cannot_override_the_reviewed_artifact_or_hash(self):
        result = await self.inspect(await self.review(), artifact="https://unreviewed.example.org/package.zip", hash="0" * 64)
        self.assertEqual((result["version"], result["hash"]), ("1.0.0", self.old_hash))
        self.assertEqual(self.downloads, [(OLD_URL, decky.ARCHIVE_MAX)])
        self.assert_no_installation()

    async def test_changed_artifact_url_invalidates_review_even_when_hash_is_unchanged(self):
        previous = await self.review()
        self.feed[0]["versions"][1]["artifact"] = "https://downloads.example.org/replaced.zip"
        current = await self.review(refresh=True)
        self.assertNotEqual(previous["versions"][1]["identity"], current["versions"][1]["identity"])
        result = await self.inspect(previous)
        self.assertEqual(result["code"], "stale_catalog")
        self.assertEqual(self.downloads, [])
        self.assert_no_installation()

    async def test_explicit_unsupported_artifact_does_not_fall_back_to_the_cdn(self):
        self.feed[0]["versions"][1]["artifact"] = "http://downloads.example.org/old.zip"
        row = await self.review()
        self.assertFalse(row["versions"][1]["inspectable"])
        result = await self.inspect(row)
        self.assertEqual(result["code"], "unverified")
        self.assertEqual(self.downloads, [])
        self.assert_no_installation()

    async def test_stable_review_identity_cannot_authorize_testing_source_download(self):
        row = await self.review()
        self.host.state["prefs"]["dev"]["enabled"] = True
        result = await self.inspect(row, source="decky_testing")
        self.assertEqual(result["code"], "stale_catalog")
        self.assertEqual(self.downloads, [])
        self.assertEqual([url for url, _ in self.feed_calls], [STABLE, TESTING])
        self.assert_no_installation()

    async def test_custom_source_key_is_not_reinterpreted_as_an_official_decky_feed(self):
        result = await self.service.list({"source": "custom", "url": "https://catalog.example.org/decky.json"})
        self.assertEqual(result["code"], "bad_source")
        self.assertEqual((self.feed_calls, self.downloads), ([], []))

    async def test_extra_url_does_not_replace_the_official_stable_endpoint(self):
        result = await self.service.list({"source": "decky_stable", "url": "https://catalog.example.org/decky.json"})
        self.assertEqual(result["source"]["url"], STABLE)
        self.assertEqual([url for url, _ in self.feed_calls], [STABLE])
        self.assertEqual(self.downloads, [])

    async def test_upstream_compatibility_claims_do_not_make_a_decky_bundle_installable(self):
        self.feed[0].update(format="framey", compatible=True, platform=["linux-aarch64"], api=1)
        row = await self.review()
        self.assertEqual(row["format"], "decky")
        self.assertFalse(row["eligible"])
        self.assertFalse(row["compatibility"]["runtime_verified"])
        self.assertEqual(row["architecture"], "unknown")
        self.assert_no_installation()

    async def test_native_catalog_install_rejects_decky_selection_before_any_download(self):
        service = fy_catalog.Catalog(self.host)
        for request in ({"source": "decky_stable", "id": "decky-4"}, {"source": "decky_testing", "id": "decky-4"}, {"format": "decky", "id": "decky-4"}):
            with self.subTest(request=request):
                result = await service.install({**request, "identity": "a" * 64, "confirm": True})
                self.assertEqual(result["code"], "decky_port_required")
        self.assertEqual((self.feed_calls, self.downloads), ([], []))
        self.assert_no_installation()


class DeckyNativeFormatBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("framey_decky_archive_contract", ROOT / "framey.py")
        cls.loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.loader)

    def test_decky_manifest_and_entrypoints_are_not_a_native_framey_package(self):
        result = self.loader.vet(package("1.0.0"))
        self.assertIsInstance(result, dict)
        self.assertIn("decky", result["error"].lower())

    def test_decky_entrypoint_layout_is_not_native_from_an_api_label_alone(self):
        result = self.loader.vet(package("1.0.0", decky_marker=False, framey_api=1))
        self.assertIsInstance(result, dict)
        self.assertIn("decky", result["error"].lower())

    def test_explicit_native_api_and_entrypoints_can_keep_legacy_markers_and_resources(self):
        result = self.loader.vet(package("1.0.0", native=True, decky_marker=True))
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[3], "sample")
        result[0].close()

    def test_catalog_schemas_do_not_silently_guess_each_others_formats(self):
        with self.assertRaises(fy_catalog.CatalogError):
            fy_catalog.parse_catalog(encoded([store_entry([])]))
        with self.assertRaises(decky.CatalogError):
            decky.parse_decky(encoded({"schema": 1, "name": "Native catalog", "plugins": []}))


if __name__ == "__main__":
    unittest.main()
