import hashlib
import copy
import io
import json
import stat
import struct
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_decky_catalog as decky


SHA = "a" * 64


def raw_plugin(**changes):
    row = {
        "id": 1, "name": "Sample", "author": "Example", "description": "Sample Decky plugin",
        "tags": ["utility"], "visible": True,
        "versions": [{"name": "1.0.0", "hash": SHA, "created": "2026-01-01T00:00:00Z", "downloads": 1, "updates": 0}],
    }
    row.update(changes)
    return row


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def archive(files, compression=zipfile.ZIP_DEFLATED):
    result = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(result, "w", compression=compression) as output:
            for name, data in files:
                output.writestr(name, data)
    return result.getvalue()


def package_files(**manifest):
    return [
        ("plugin.json", encoded({"name": "Sample", "author": "Example", "flags": [], **manifest})),
        ("package.json", encoded({"name": "sample", "version": "1.0.0", "dependencies": {"react": "1.0.0"}})),
        ("dist/index.js", b"// inspection fixture"),
        ("main.py", b"raise AssertionError('inspection must never import a backend')\n"),
    ]


def elf(machine):
    value = bytearray(64)
    bits = 1 if machine in (3, 40) else 2
    value[:7] = b"\x7fELF" + bytes((bits, 1, 1))
    struct.pack_into("<H", value, 16, 2)
    struct.pack_into("<H", value, 18, machine)
    struct.pack_into("<I", value, 20, 1)
    struct.pack_into("<H", value, 40 if bits == 1 else 52, 52 if bits == 1 else 64)
    return bytes(value)


class DeckyFeedValidation(unittest.TestCase):
    def reject(self, data):
        with self.assertRaises(decky.CatalogError):
            decky.parse_decky(data)

    def test_valid_feed_whitelists_fields_and_preserves_hash(self):
        row = raw_plugin(upstream_extra={"command": "must not propagate"})
        for data in (encoded([row]), encoded([row]).decode("utf-8")):
            with self.subTest(kind=type(data).__name__):
                parsed = decky.parse_decky(data)
                self.assertEqual(parsed["plugins"][0]["id"], "decky-1")
                self.assertEqual(parsed["plugins"][0]["versions"][0]["hash"], SHA)
                self.assertNotIn("upstream_extra", parsed["plugins"][0])

    def test_invalid_json_types_and_nonfinite_numbers(self):
        for data in (b"", b"[", b"\xff", b"{}", b"null", b"1", b"[] trailing", {}, None):
            with self.subTest(data=repr(data)):
                self.reject(data)
        raw = encoded([raw_plugin()]).decode()
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                self.reject(raw.replace('"downloads":1', '"downloads":' + token))

    def test_duplicate_ids_and_duplicate_keys_are_rejected(self):
        self.reject(encoded([raw_plugin(), raw_plugin()]))
        raw = encoded([raw_plugin()]).decode()
        self.reject(raw.replace('"id":1', '"id":1,"id":2', 1))
        self.reject(raw.replace('"hash":"' + SHA + '"', '"hash":"' + SHA + '","hash":"' + "b" * 64 + '"'))

    def test_valid_json_exceeding_feed_limit_and_deep_nesting_are_rejected(self):
        raw = encoded([raw_plugin()])
        self.reject(raw + b" " * (524289 - len(raw)))
        self.reject("[" * 1100 + "0" + "]" * 1100)
        nested = 0
        for _ in range(40):
            nested = {"extra": nested}
        self.reject(encoded([raw_plugin(upstream_extra=nested)]))

    def test_feed_count_and_numeric_identifier_limits(self):
        self.reject(encoded([raw_plugin(id=i + 1) for i in range(513)]))
        for pid in (0, -1, True, "1", None):
            with self.subTest(pid=pid):
                self.reject(encoded([raw_plugin(id=pid)]))

    def test_hash_and_version_shapes_cannot_be_reinterpreted_as_urls(self):
        for digest in ("a" * 63, "a" * 65, "g" * 64, "https://example.org/archive.zip", "../archive.zip", None):
            with self.subTest(digest=digest):
                row = raw_plugin()
                row["versions"][0]["hash"] = digest
                self.reject(encoded([row]))
        for versions in (None, {}, [None], ["1.0.0"]):
            with self.subTest(versions=versions):
                self.reject(encoded([raw_plugin(versions=versions)]))

    def test_controls_in_display_metadata_are_rejected(self):
        for field in ("name", "author", "description"):
            for value in ("Text\x00", "Text\x1b[2J"):
                with self.subTest(field=field, value=repr(value)):
                    self.reject(encoded([raw_plugin(**{field: value})]))


class DeckyArchiveValidation(unittest.TestCase):
    def inspect(self, data, **changes):
        return decky.inspect_archive(data, hashlib.sha256(data).hexdigest(), **changes)

    def reject(self, data):
        with self.assertRaises(decky.CatalogError):
            self.inspect(data)

    def test_matching_hash_allows_read_only_port_assessment(self):
        data = archive(package_files())
        result = self.inspect(data)
        self.assertEqual(result["state"], "port_required")
        self.assertTrue(result["not_executed"])
        self.assertTrue(result["verified_sha256"])
        self.assertEqual(result["bytes"], len(data))
        self.assertTrue(result["has_frontend"])
        self.assertTrue(result["has_backend"])
        self.assertIn("react", result["dependencies"])

    def test_hash_mismatch_is_rejected_before_zip_is_opened(self):
        data = archive(package_files())
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("ZIP was read before hash verification")):
            with self.assertRaises(decky.CatalogError) as caught:
                decky.inspect_archive(data, "0" * 64)
        self.assertEqual(caught.exception.code, "digest_mismatch")

    def test_invalid_hash_and_non_zip_content_are_bounded_errors(self):
        for digest in (None, "", "a" * 63, "g" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(decky.CatalogError):
                    decky.inspect_archive(b"not zip", digest)
        for data in (b"", b"not zip", b"PK\x03\x04", archive(package_files())[:-10]):
            with self.subTest(data=data[:20]):
                self.reject(data)

    def test_archive_entry_and_expansion_budgets(self):
        self.reject(archive([("large.bin", b"x" * 6_000_000)], compression=zipfile.ZIP_STORED))
        self.reject(archive([("large.bin", b"x" * 6_000_000)]))
        self.reject(archive([("part%d.bin" % i, b"x" * 4_200_000) for i in range(5)]))
        self.reject(archive([("part%d.txt" % i, b"x") for i in range(501)]))

    def test_traversal_absolute_backslash_and_control_paths_are_rejected(self):
        for name in ("../escape", "/escape", "a/../../escape", "a/./escape", "a//escape", "a\\escape", "a\x1bescape"):
            with self.subTest(name=repr(name)):
                self.reject(archive(package_files() + [(name, b"x")]))

    def test_duplicate_case_collisions_and_file_directory_conflicts(self):
        for rows in (
            [("main.py", b"first"), ("main.py", b"second")],
            [("dist/index.js", b"first"), ("DIST/INDEX.js", b"second")],
            [("dist", b"file"), ("dist/index.js", b"child")],
        ):
            with self.subTest(names=[name for name, _ in rows]):
                self.reject(archive(rows))

    def test_links_devices_and_special_entries_are_rejected(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            with self.subTest(kind=kind):
                info = zipfile.ZipInfo("special")
                info.create_system = 3
                info.external_attr = (kind | 0o777) << 16
                self.reject(archive([(info, b"target")]))

    def test_encryption_and_unsupported_compression_are_rejected(self):
        original = archive([("file.txt", b"x")], compression=zipfile.ZIP_STORED)
        for field in ("encrypted", "compression"):
            with self.subTest(field=field):
                data = bytearray(original)
                local, central = data.index(b"PK\x03\x04"), data.index(b"PK\x01\x02")
                if field == "encrypted":
                    struct.pack_into("<H", data, local + 6, 1)
                    struct.pack_into("<H", data, central + 8, 1)
                else:
                    struct.pack_into("<H", data, local + 8, 99)
                    struct.pack_into("<H", data, central + 10, 99)
                self.reject(bytes(data))

    def test_plugin_and_package_metadata_have_separate_limits(self):
        for name, length in (("plugin.json", 70_000), ("package.json", 270_000)):
            with self.subTest(name=name):
                data = encoded({"name": "sample"})
                files = [(path, data + b" " * (length - len(data)) if path == name else value) for path, value in package_files()]
                self.reject(archive(files))

    def test_missing_malformed_and_duplicate_metadata_are_rejected(self):
        self.reject(archive([("dist/index.js", b"// sample")]))
        for name, bad in (("plugin.json", b"{"), ("package.json", b"{"), ("plugin.json", b'{"name":"first","name":"second"}')):
            with self.subTest(name=name, bad=bad):
                files = [(path, bad if path == name else value) for path, value in package_files()]
                self.reject(archive(files))

    def test_binary_absence_stays_unknown_and_never_implies_arm_support(self):
        result = self.inspect(archive(package_files()))
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["native_binaries"], [])
        self.assertEqual(result["state"], "port_required")

    def test_declared_root_requirement_is_reported_without_running_setup(self):
        result = self.inspect(archive(package_files(flags=["root"])))
        self.assertIs(result["root_required"], True)
        self.assertIn("root", result["flags"])
        self.assertTrue(result["not_executed"])

    def test_elf_headers_report_known_architectures_without_executing(self):
        for machine, architecture in ((183, "aarch64"), (62, "x86_64"), (3, "x86"), (40, "arm")):
            with self.subTest(machine=machine):
                result = self.inspect(archive(package_files() + [("bin/helper", elf(machine))]))
                binary = next(row for row in result["native_binaries"] if row["path"].endswith("bin/helper"))
                self.assertEqual(binary["architecture"], architecture)
                self.assertEqual(binary["format"], "ELF")
                self.assertEqual(result["state"], "port_required")
                self.assertTrue(result["not_executed"])

    def test_mixed_binary_architectures_remain_a_port_requirement(self):
        data = archive(package_files() + [("bin/arm-helper", elf(183)), ("bin/x64-helper", elf(62))])
        result = self.inspect(data)
        self.assertEqual(result["architecture"], "mixed")
        self.assertEqual(result["state"], "port_required")

    def test_backend_and_import_hooks_are_never_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "executed"
            payload = "from pathlib import Path\nPath(%r).write_text('bad')\n" % str(marker)
            data = archive(package_files() + [("backend.py", payload), ("__init__.py", payload), ("setup.py", payload)])
            result = self.inspect(data)
            self.assertTrue(result["not_executed"])
            self.assertFalse(marker.exists())


class FakeHost:
    def __init__(self, root):
        self.ROOT, self.HOME, self.PLUGINS = root / "loader", root / "home", root / "plugins"
        for folder in (self.ROOT, self.HOME, self.PLUGINS):
            folder.mkdir()
        self.CONF = self.HOME / "settings.json"
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.1.0", "linux-aarch64", 1
        self.state = {"prefs": {"dev": {"enabled": False}}}
        self.archive = mock.Mock(side_effect=AssertionError("Decky inspection reached native archive installation"))
        self.stage_swap = mock.Mock(side_effect=AssertionError("Decky inspection reached staging"))
        self.reload = mock.AsyncMock(side_effect=AssertionError("Decky inspection reached runtime reload"))

    def settings(self):
        return copy.deepcopy(self.state)

    def now(self):
        return 1_000_000.0

    def log(self, *args):
        pass


class DeckyAssessmentConfirmation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.host = FakeHost(Path(self.temporary.name))
        self.zip_bytes = archive(package_files())
        self.row = raw_plugin()
        self.row["versions"][0]["hash"] = hashlib.sha256(self.zip_bytes).hexdigest()
        self.body = encoded([self.row])
        self.feed_calls = []

        async def fetcher(url, max_bytes):
            self.feed_calls.append((url, max_bytes))
            return self.body

        self.archive_fetcher = mock.AsyncMock(return_value=self.zip_bytes)
        self.service = decky.DeckyCatalog(self.host, fetcher=fetcher, archive_fetcher=self.archive_fetcher)

    async def review(self, refresh=False):
        result = await self.service.details({"id": "decky-1", "source": "decky_stable", "refresh": refresh})
        return result["entry"]

    async def assess(self, row, **changes):
        return await self.service.assess({"id": row["id"], "source": "decky_stable", "identity": row["identity"], "version_identity": row["versions"][0]["identity"], "confirm": True, **changes})

    def assert_no_runtime_mutation(self):
        self.host.archive.assert_not_called()
        self.host.stage_swap.assert_not_called()
        self.host.reload.assert_not_called()
        self.assertEqual(list(self.host.PLUGINS.iterdir()), [])

    async def test_missing_confirmation_never_downloads_a_bundle(self):
        row = await self.review()
        for request in ({"id": "decky-1"}, {"id": "decky-1", "confirm": True}, {"id": "decky-1", "source": "decky_stable", "identity": row["identity"], "version_identity": row["versions"][0]["identity"], "confirm": False}):
            with self.subTest(request=request):
                result = await self.service.assess(request)
                self.assertEqual(result["code"], "confirmation_required")
        self.archive_fetcher.assert_not_awaited()
        self.assert_no_runtime_mutation()

    async def test_mismatched_entry_and_version_identities_never_download(self):
        row = await self.review()
        for changes in ({"identity": "0" * 64}, {"version_identity": "0" * 64}):
            with self.subTest(changes=changes):
                result = await self.assess(row, **changes)
                self.assertEqual(result["code"], "stale_catalog")
        self.archive_fetcher.assert_not_awaited()
        self.assert_no_runtime_mutation()

    async def test_changed_feed_hash_requires_a_fresh_review(self):
        old = await self.review()
        changed = copy.deepcopy(self.row)
        changed["versions"][0]["hash"] = "b" * 64
        self.body = encoded([changed])
        new = await self.review(refresh=True)
        self.assertNotEqual(new["versions"][0]["identity"], old["versions"][0]["identity"])
        result = await self.assess(old)
        self.assertEqual(result["code"], "stale_catalog")
        self.archive_fetcher.assert_not_awaited()
        self.assert_no_runtime_mutation()

    async def test_successful_assessment_only_reads_the_verified_bundle(self):
        row = await self.review()
        result = await self.assess(row)
        self.assertEqual(result["format"], "decky")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["hash"], self.row["versions"][0]["hash"])
        self.assertEqual(result["assessment"]["state"], "port_required")
        self.assertTrue(result["assessment"]["not_executed"])
        self.archive_fetcher.assert_awaited_once()
        self.assert_no_runtime_mutation()


if __name__ == "__main__":
    unittest.main()
