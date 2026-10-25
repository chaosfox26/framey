import hashlib
import io
import json
import os
import stat
import struct
import subprocess
import sys
import unittest
import warnings
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_decky_catalog as decky
import fy_pkg


def package(prefix=""):
    return [
        (prefix + "plugin.json", json.dumps({"name": "Example", "flags": []}).encode()),
        (prefix + "package.json", json.dumps({"name": "example", "version": "1.0.0"}).encode()),
        (prefix + "dist/index.js", b"throw new Error('must not execute');"),
        (prefix + "main.py", b"raise AssertionError('must not import')\n"),
    ]


def zipped(entries):
    target = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    return target.getvalue()


class DeckyArchivePaths(unittest.TestCase):
    def inspect(self, data):
        return decky.inspect_archive(data, hashlib.sha256(data).hexdigest(), "linux-aarch64")

    def reject(self, data):
        with self.assertRaises(decky.CatalogError) as caught:
            self.inspect(data)
        return caught.exception

    def test_explicit_package_and_dist_directories_are_stripped_once(self):
        data = zipped([("example-v1/", b""), ("example-v1/dist/", b"")] + package("example-v1/"))
        result = self.inspect(data)
        self.assertTrue(result["has_frontend"])
        self.assertTrue(result["has_backend"])
        self.assertEqual(result["files"], 4)
        self.assertTrue(result["not_executed"])

    def test_an_extra_root_level_cannot_hide_nested_package_metadata(self):
        self.reject(zipped(package("outer/inner/")))

    def test_inspection_delegates_guard_failures_before_opening_any_entry(self):
        data = zipped(package("example/"))
        for guard in ("_zip_end_ok", "_Budget", "_parts", "_layout"):
            with self.subTest(guard=guard):
                with mock.patch.object(fy_pkg, guard, side_effect=fy_pkg.PkgError("shared guard rejected", "unsafe_path")) as shared:
                    with mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("entry read before shared validation")):
                        result = self.reject(data)
                self.assertEqual(result.code, "bad_archive")
                shared.assert_called()

    def test_unicode_equivalent_files_and_parent_directories_are_rejected(self):
        for extra in (
            [("assets/caf\u00e9.txt", b"one"), ("assets/cafe\u0301.txt", b"two")],
            [("caf\u00e9/one.txt", b"one"), ("cafe\u0301/two.txt", b"two")],
            [("Assets/one.txt", b"one"), ("assets/two.txt", b"two")],
        ):
            with self.subTest(paths=[name for name, _ in extra]):
                self.reject(zipped(package("example/") + [("example/" + name, value) for name, value in extra]))

    def test_file_and_implicit_parent_conflicts_are_rejected_in_either_order(self):
        for extra in (
            [("assets", b"file"), ("assets/icon.svg", b"image")],
            [("assets/icon.svg", b"image"), ("assets", b"file")],
            [("assets/", b""), ("assets", b"file")],
        ):
            with self.subTest(paths=[name for name, _ in extra]):
                self.reject(zipped(package("example/") + [("example/" + name, value) for name, value in extra]))

    def test_directory_entries_count_toward_the_shared_entry_limit(self):
        entries = [("example/", b""), ("example/dist/", b"")] + package("example/")
        with mock.patch.object(fy_pkg, "ENTRIES", 6):
            self.assertEqual(self.inspect(zipped(entries))["files"], 4)
            self.assertEqual(self.reject(zipped(entries + [("example/assets/", b"")])).code, "bad_archive")

    def test_a_package_prefix_does_not_erase_path_depth_or_component_limits(self):
        for name in ("/".join(["example"] + ["nested"] * fy_pkg.DEPTH + ["file"]), "example/" + "x" * 256):
            with self.subTest(path=name):
                self.reject(zipped(package("example/") + [(name, b"payload")]))

    def test_prefixed_links_special_types_and_directory_type_mismatches_are_rejected(self):
        for kind, name in (
            (stat.S_IFLNK, "example/link"),
            (stat.S_IFIFO, "example/pipe"),
            (stat.S_IFCHR, "example/device"),
            (stat.S_IFBLK, "example/block"),
            (stat.S_IFSOCK, "example/socket"),
            (stat.S_IFDIR, "example/not-a-directory"),
            (stat.S_IFREG, "example/not-a-file/"),
        ):
            with self.subTest(kind=kind, path=name):
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (kind | 0o755) << 16
                self.reject(zipped(package("example/") + [(entry, b"../../outside")]))

    def test_unix_link_extra_never_triggers_filesystem_or_extraction_actions(self):
        entry = zipfile.ZipInfo("example/bin/helper")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFREG | 0o755) << 16
        extra = struct.pack("<IIHH", 0, 0, 0, 0) + b"../../outside"
        entry.extra = struct.pack("<HH", 0x000D, len(extra)) + extra
        data = zipped(package("example/") + [(entry, b"ordinary file bytes")])
        with ExitStack() as stack:
            for owner, name in (
                (Path, "mkdir"), (Path, "open"), (Path, "write_bytes"), (Path, "write_text"),
                (os, "symlink"), (os, "link"), (zipfile.ZipFile, "extract"), (zipfile.ZipFile, "extractall"),
                (subprocess, "Popen"), (subprocess, "run"),
            ):
                stack.enter_context(mock.patch.object(owner, name, side_effect=AssertionError("inspection performed a filesystem or execution action")))
            result = self.inspect(data)
        self.assertTrue(result["not_executed"])
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["files"], 5)


if __name__ == "__main__":
    unittest.main()
