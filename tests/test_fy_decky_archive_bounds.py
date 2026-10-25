import hashlib
import io
import json
import struct
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog as decky
import fy_pkg


def manifest_bytes(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def files():
    return [
        ("plugin.json", manifest_bytes({"name": "Boundary Sample", "author": "Example", "flags": []})),
        ("package.json", manifest_bytes({"name": "boundary-sample", "version": "1.0.0"})),
        ("dist/index.js", b"throw new Error('package inspection must not execute frontend code');"),
        ("main.py", b"raise AssertionError('package inspection must not import backend code')\n"),
    ]


def archive(entries, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as bundle:
        for name, value in entries:
            bundle.writestr(name, value)
    return output.getvalue()


class DeckyArchiveBounds(unittest.TestCase):
    def inspect(self, data):
        return decky.inspect_archive(data, hashlib.sha256(data).hexdigest())

    def reject(self, data):
        with self.assertRaises(decky.CatalogError):
            self.inspect(data)

    def test_compressed_byte_budget_is_checked_before_zip_parsing(self):
        data = b"x" * (decky.ARCHIVE_MAX + 1)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("oversize archive reached ZIP parser")):
            self.reject(data)

    def test_compressed_byte_budget_is_inclusive(self):
        rows = files() + [("assets/payload.bin", b"")]
        overhead = len(archive(rows, compression=zipfile.ZIP_STORED))
        rows[-1] = ("assets/payload.bin", b"x" * (decky.ARCHIVE_MAX - overhead))
        data = archive(rows, compression=zipfile.ZIP_STORED)
        self.assertEqual(len(data), decky.ARCHIVE_MAX)
        self.assertTrue(self.inspect(data)["not_executed"])
        self.reject(data + b"x")

    def test_directories_count_toward_the_entry_budget(self):
        rows = files()
        rows += [("folders/%03d/" % number, b"") for number in range(fy_pkg.ENTRIES - len(rows))]
        result = self.inspect(archive(rows))
        self.assertTrue(result["not_executed"])
        self.reject(archive(rows + [("folders/overflow/", b"")]))

    def test_forged_eocd_count_cannot_hide_extra_entries(self):
        rows = files() + [("extra/%03d" % number, b"") for number in range(fy_pkg.ENTRIES)]
        data = bytearray(archive(rows))
        end = data.rfind(b"PK\x05\x06")
        self.assertGreaterEqual(end, 0)
        struct.pack_into("<HH", data, end + 8, 1, 1)
        self.reject(bytes(data))

    def test_excessive_central_directory_is_refused(self):
        data = bytearray(archive(files()))
        end = data.rfind(b"PK\x05\x06")
        struct.pack_into("<I", data, end + 12, 2_000_000)
        self.reject(bytes(data))

    def test_per_file_expansion_limit_is_inclusive(self):
        rows = files() + [("assets/payload.bin", b"x" * fy_pkg.ENTRY_MAX)]
        result = self.inspect(archive(rows))
        self.assertTrue(result["not_executed"])
        rows[-1] = ("assets/payload.bin", b"x" * (fy_pkg.ENTRY_MAX + 1))
        self.reject(archive(rows))

    def test_total_expansion_limit_includes_metadata_and_sources(self):
        rows = files()
        remaining = fy_pkg.TOTAL_MAX - sum(len(value) for _, value in rows)
        number = 0
        while remaining:
            size = min(fy_pkg.ENTRY_MAX, remaining)
            rows.append(("assets/part%d.bin" % number, b"x" * size))
            remaining -= size
            number += 1
        result = self.inspect(archive(rows))
        self.assertTrue(result["not_executed"])
        self.reject(archive(rows + [("one-extra-byte", b"x")]))

    def test_depth_limit_cannot_be_hidden_by_a_package_root(self):
        name = "/".join(["folder"] * fy_pkg.DEPTH + ["payload"])
        self.reject(archive(files() + [(name, b"x")]))

    def test_unicode_normalized_directory_aliases_are_refused(self):
        rows = files() + [("caf\u00e9/first", b"x"), ("cafe\u0301/second", b"y")]
        self.reject(archive(rows))

    def test_casefolded_directory_aliases_are_refused(self):
        rows = files() + [("assets/first", b"x"), ("ASSETS/second", b"y")]
        self.reject(archive(rows))

    def test_scalar_or_array_metadata_cannot_be_a_package_manifest(self):
        for path in ("plugin.json", "package.json"):
            for value in (None, True, 1, "name", [], ["name"]):
                with self.subTest(path=path, value=value):
                    rows = [(name, manifest_bytes(value) if name == path else data) for name, data in files()]
                    self.reject(archive(rows))

    def test_metadata_recursion_fails_as_a_bounded_catalog_error(self):
        deeply_nested = b'{"extra":' * 1100 + b"0" + b"}" * 1100
        for path in ("plugin.json", "package.json"):
            with self.subTest(path=path):
                rows = [(name, deeply_nested if name == path else data) for name, data in files()]
                self.reject(archive(rows))

    def test_multiple_package_roots_do_not_select_an_arbitrary_manifest(self):
        rows = [("first/" + name, data) for name, data in files()]
        rows += [("second/" + name, data) for name, data in files()]
        self.reject(archive(rows))


if __name__ == "__main__":
    unittest.main()
