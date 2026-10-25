"""Adversarial catalog boundaries, using only in-memory catalog downloads."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_catalog as catalog


COMMIT = "a" * 40
CUSTOM_URL = "https://catalog.example.org/framey.json"


def plugin(**changes):
    row = {
        "id": "sample", "name": "Sample", "version": "1.0.0",
        "description": "A sample plugin", "category": "utilities", "tags": ["tools"],
        "channel": "stable", "kind": "plugin", "platform": ["linux-aarch64"],
        "api": 1, "framey": {"min": "1.0.0", "max": "2.0.0"},
        "source": {"repository": "https://github.com/example/sample", "commit": COMMIT, "path": "plugins/sample"},
    }
    row.update(changes)
    return row


def document(*rows):
    return {"schema": 1, "name": "Framey catalog", "plugins": list(rows or (plugin(),))}


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class CatalogParsing(unittest.TestCase):
    def reject(self, value):
        with self.assertRaises(catalog.CatalogError) as caught:
            catalog.parse_catalog(value)
        self.assertTrue(caught.exception.code)

    def test_valid_bytes_and_text_preserve_pinned_identity(self):
        for value in (encoded(document()), encoded(document()).decode("utf-8")):
            with self.subTest(kind=type(value).__name__):
                parsed = catalog.parse_catalog(value)
                self.assertEqual(parsed["schema"], 1)
                self.assertEqual(parsed["plugins"][0]["id"], "sample")
                self.assertEqual(parsed["plugins"][0]["source"]["commit"], COMMIT)

    def test_malformed_json_and_non_document_inputs(self):
        for value in (b"", b"{", b"{} trailing", b"\xff", b"null", b"[]", b"true", b"1", None, {}, 1):
            with self.subTest(value=repr(value)):
                self.reject(value)

    def test_duplicate_json_keys_at_every_authority_boundary(self):
        original = encoded(document()).decode("utf-8")
        variants = (
            original.replace('"schema":1', '"schema":1,"schema":1', 1),
            original.replace('"id":"sample"', '"id":"sample","id":"other"', 1),
            original.replace('"commit":"' + COMMIT + '"', '"commit":"' + COMMIT + '","commit":"' + "b" * 40 + '"', 1),
        )
        for value in variants:
            with self.subTest(value=value):
                self.reject(value)

    def test_nonfinite_numbers_are_never_accepted(self):
        original = encoded(document()).decode("utf-8")
        for number in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(number=number):
                self.reject(original.replace('"api":1', '"api":' + number, 1))

    def test_oversize_and_deep_json_raise_catalog_errors(self):
        valid = encoded(document())
        self.reject(valid + b" " * (524289 - len(valid)))
        self.reject("[" * 1100 + "0" + "]" * 1100)
        value = document()
        nested = 0
        for _ in range(40):
            nested = {"nested": nested}
        value["plugins"][0]["description"] = nested
        self.reject(encoded(value))

    def test_plugin_count_and_duplicate_identifiers_are_bounded(self):
        self.reject(encoded(document(plugin(), plugin())))
        self.reject(encoded(document(*(plugin(id="sample%d" % i) for i in range(201)))))

    def test_unknown_fields_cannot_smuggle_install_instructions(self):
        variants = []
        value = document()
        value["command"] = "run"
        variants.append(value)
        value = document()
        value["plugins"][0]["install"] = "run"
        variants.append(value)
        value = document()
        value["plugins"][0]["source"]["branch"] = "main"
        variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                self.reject(encoded(value))

    def test_identifier_and_display_controls_are_rejected(self):
        for field, values in {
            "id": ("../sample", "/sample", "sample/other", "sample\\other", "_core", "sample\x00"),
            "name": ("Sample\x00", "Sample\x1b[2J", "Sample\nHidden"),
            "description": ("Text\x00", "Text\x1b[2J"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=repr(value)):
                    self.reject(encoded(document(plugin(**{field: value}))))

    def test_repository_must_be_a_canonical_https_github_repository(self):
        for url in (
            "http://github.com/example/sample", "https://user:pass@github.com/example/sample",
            "https://github.com.evil.example/example/sample", "https://127.0.0.1/example/sample",
            "https://github.com/example/sample/tree/main", "https://github.com/example/sample?token=x",
            "https://github.com/example/sample#main", "https://github.com:444/example/sample",
            "https://github.com/example/../sample", "https://github.com/example\\sample",
        ):
            with self.subTest(url=url):
                row = plugin()
                row["source"]["repository"] = url
                self.reject(encoded(document(row)))

    def test_source_paths_never_escape_or_exceed_depth(self):
        for path in ("../sample", "/sample", "a/../../sample", "a/./sample", "a//sample", "a\\sample", "a\x00sample", "a/b/c/d/e/f/g"):
            with self.subTest(path=repr(path)):
                row = plugin()
                row["source"]["path"] = path
                self.reject(encoded(document(row)))

    def test_mutable_or_ambiguous_commit_refs_are_rejected(self):
        for commit in ("main", "v1.0.0", "a" * 7, "A" * 40, "a" * 39, "a" * 41, "g" * 40, None):
            with self.subTest(commit=commit):
                row = plugin()
                row["source"]["commit"] = commit
                self.reject(encoded(document(row)))

    def test_invalid_compatibility_values_do_not_become_unconstrained(self):
        for field, value in (("api", True), ("api", 0), ("api", "1"), ("platform", "linux-aarch64"), ("platform", ["../arm64"]), ("framey", {"min": "bad"}), ("framey", {"min": "2.0.0", "max": "1.0.0"})):
            with self.subTest(field=field, value=value):
                self.reject(encoded(document(plugin(**{field: value}))))


class SourceUrlValidation(unittest.TestCase):
    def test_safe_public_https_url_is_retained(self):
        self.assertEqual(catalog.validate_source_url(CUSTOM_URL), CUSTOM_URL)

    def test_unsafe_schemes_credentials_ports_and_controls(self):
        for url in (
            "http://catalog.example.org/index.json", "file:///tmp/index.json", "javascript:alert(1)",
            "https://user@catalog.example.org/index.json", "https://user:password@catalog.example.org/index.json",
            "https://catalog.example.org:444/index.json", "https://catalog.example.org/index.json?token=x",
            "https://catalog.example.org/index.json#fragment", "https://catalog.example.org\\evil/index.json",
            "https://catalog.example.org/\x00index.json", "https://catalog.example.org/\nindex.json",
            " https://catalog.example.org/index.json", "https://catalog.example.org/a b.json", None, 1,
        ):
            with self.subTest(url=repr(url)):
                with self.assertRaises(catalog.CatalogError):
                    catalog.validate_source_url(url)

    def test_private_and_special_ip_literals_are_rejected_without_dns(self):
        for host in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "[::1]", "[fc00::1]", "[fe80::1]", "[::ffff:127.0.0.1]"):
            with self.subTest(host=host):
                with self.assertRaises(catalog.CatalogError):
                    catalog.validate_source_url("https://" + host + "/index.json")


class FakeHost:
    def __init__(self, root):
        self.ROOT = root / "loader"
        self.HOME = root / "home"
        self.PLUGINS = root / "plugins"
        for folder in (self.ROOT, self.HOME, self.PLUGINS):
            folder.mkdir()
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.1.0", "linux-aarch64", 1
        self.state = {"prefs": {"dev": {"enabled": False}}, "disabled": [], "modules": {}}
        self.archive = mock.Mock(side_effect=AssertionError("an invalid request reached archive download"))
        self.stage_swap = mock.Mock(side_effect=AssertionError("an invalid request reached installation"))
        self.reload = mock.AsyncMock(side_effect=AssertionError("an invalid request reached reload"))

    def settings(self):
        return copy.deepcopy(self.state)

    def save(self, state):
        self.state = copy.deepcopy(state)

    def manifests(self):
        return []

    def real(self, pid):
        return None

    def plugin_dir(self, pid):
        return self.PLUGINS / pid

    def log(self, *args):
        pass


class CatalogConfirmation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host = FakeHost(Path(self.temp.name))
        self.body = encoded(document())
        self.calls = []

        async def fetcher(url, max_bytes):
            self.calls.append((url, max_bytes))
            return self.body

        self.catalog = catalog.Catalog(self.host, fetcher=fetcher)

    async def review(self, **extra):
        return await self.catalog.list({"source": "custom", "url": CUSTOM_URL, "refresh": True, **extra})

    async def install(self, row, **changes):
        return await self.catalog.install({"id": row["id"], "source": "custom", "url": CUSTOM_URL, "identity": row["identity"], "confirm": True, **changes})

    def assert_no_install(self):
        self.host.archive.assert_not_called()
        self.host.stage_swap.assert_not_called()
        self.host.reload.assert_not_called()

    async def test_id_only_and_unconfirmed_requests_never_install(self):
        row = (await self.review())["rows"][0]
        for request in ({"id": row["id"]}, {"id": row["id"], "confirm": True}, {"id": row["id"], "source": "custom", "url": CUSTOM_URL, "identity": row["identity"], "confirm": False}):
            with self.subTest(request=request):
                result = await self.catalog.install(request)
                self.assertIn("error", result)
                self.assertEqual(result["code"], "confirmation_required")
        self.assert_no_install()

    async def test_identity_mismatch_never_downloads_or_stages(self):
        row = (await self.review())["rows"][0]
        result = await self.install(row, identity="0" * 64)
        self.assertEqual(result["code"], "stale_catalog")
        self.assert_no_install()

    async def test_changed_reviewed_commit_requires_a_new_confirmation(self):
        old = (await self.review())["rows"][0]
        changed = plugin()
        changed["source"]["commit"] = "b" * 40
        self.body = encoded(document(changed))
        new = (await self.review())["rows"][0]
        self.assertNotEqual(old["identity"], new["identity"])
        result = await self.install(old)
        self.assertEqual(result["code"], "stale_catalog")
        self.assert_no_install()

    async def test_review_identity_cannot_authorize_another_custom_source(self):
        row = (await self.review())["rows"][0]
        result = await self.install(row, url="https://other.example.org/framey.json")
        self.assertEqual(result["code"], "stale_catalog")
        self.assert_no_install()

    async def test_invalid_refresh_retains_reviewed_identity_and_marks_cache_stale(self):
        old = (await self.review())["rows"][0]
        self.body = b'{"schema":1,"plugins":['
        result = await self.review()
        self.assertTrue(result["stale"])
        self.assertIsNone(result["error"])
        self.assertTrue(result["fetch_error"])
        self.assertEqual(result["origin"], "cache")
        self.assertEqual(result["rows"][0]["identity"], old["identity"])
        self.assertEqual(result["rows"][0]["source"], old["source"])
        denied = await self.install(old)
        self.assertEqual(denied["code"], "stale_catalog")
        self.assert_no_install()

    async def test_incompatible_architecture_api_and_version_stay_ineligible(self):
        variants = ({"platform": ["linux-x86_64"]}, {"api": 2}, {"framey": {"min": "2.0.0"}}, {"framey": {"max": "1.0.0"}})
        for changes in variants:
            with self.subTest(changes=changes):
                self.body = encoded(document(plugin(**changes)))
                row = (await self.review())["rows"][0]
                self.assertFalse(row["eligible"])
                result = await self.install(row)
                self.assertIn("error", result)
        self.assert_no_install()

    async def test_testing_source_requires_developer_mode(self):
        result = await self.catalog.list({"source": "testing", "refresh": True})
        self.assertEqual(result["code"], "dev_off")
        self.assertEqual(self.calls, [])
        self.assert_no_install()


if __name__ == "__main__":
    unittest.main()
