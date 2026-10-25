"""Static package inspection must keep hostile files bounded and inert."""

import hashlib
import io
import json
import stat
import struct
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog as decky
import fy_pkg


def bundle(rows=(), compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.writestr("plugin.json", json.dumps({"name": "Sample", "flags": []}))
        for name, value in rows:
            archive.writestr(name, value)
    return output.getvalue()


def inspect(data):
    return decky.inspect_archive(data, hashlib.sha256(data).hexdigest())


def elf_header(machine=183, bits=2, endian=1):
    value = bytearray(64 if bits == 2 else 52)
    value[:7] = b"\x7fELF" + bytes((bits, endian, 1))
    order = "<" if endian == 1 else ">"
    struct.pack_into(order + "HHI", value, 16, 2, machine, 1)
    struct.pack_into(order + "H", value, 52 if bits == 2 else 40, len(value))
    return value


class HostileArchiveFiles(unittest.TestCase):
    def test_unicode_normalization_and_parent_case_collisions_are_rejected(self):
        cases = (
            [("assets/caf\u00e9.txt", b"first"), ("assets/cafe\u0301.txt", b"second")],
            [("Resources/a.txt", b"first"), ("resources/b.txt", b"second")],
        )
        for rows in cases:
            with self.subTest(names=[name for name, _ in rows]):
                with self.assertRaises(decky.CatalogError):
                    inspect(bundle(rows))

    def test_symlink_metadata_is_rejected_before_any_member_is_opened(self):
        link = zipfile.ZipInfo("assets/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        data = bundle([(link, b"../../outside")])
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("unsafe member was opened")):
            with self.assertRaises(decky.CatalogError) as caught:
                inspect(data)
        self.assertEqual(caught.exception.code, "bad_archive")

    def test_corrupt_deflate_returns_a_bounded_archive_error(self):
        data = bytearray(bundle(compression=zipfile.ZIP_DEFLATED))
        offset = 30 + int.from_bytes(data[26:28], "little") + int.from_bytes(data[28:30], "little")
        data[offset:offset + 2] = b"\x07\xff"
        with self.assertRaises(decky.CatalogError) as caught:
            inspect(bytes(data))
        self.assertEqual(caught.exception.code, "bad_archive")

    def test_inspection_never_extracts_writes_or_imports_plugin_code(self):
        payload = b"raise AssertionError('plugin code was executed')\n"
        data = bundle([("main.py", payload), ("setup.py", payload), ("__init__.py", payload)])
        denied = AssertionError("read-only inspection tried to write or extract")
        with mock.patch.object(zipfile.ZipFile, "extract", side_effect=denied), \
                mock.patch.object(zipfile.ZipFile, "extractall", side_effect=denied), \
                mock.patch.object(fy_pkg, "safe_extract_zip", side_effect=denied), \
                mock.patch.object(fy_pkg, "safe_extract_tar", side_effect=denied), \
                mock.patch.object(fy_pkg, "_save", side_effect=denied), \
                mock.patch("builtins.open", side_effect=denied), \
                mock.patch.object(Path, "mkdir", side_effect=denied):
            result = inspect(data)
        self.assertTrue(result["not_executed"])
        self.assertEqual(result["state"], "port_required")

    def test_large_native_member_reads_only_a_bounded_prefix(self):
        data = bundle([("bin/helper", bytes(elf_header()) + b"x" * 1_000_000)])
        original_read = zipfile.ZipExtFile.read
        reads = []

        def record_read(stream, size=-1):
            reads.append((stream.name, size))
            return original_read(stream, size)

        with mock.patch.object(zipfile.ZipExtFile, "read", record_read):
            result = inspect(data)
        self.assertEqual([size for name, size in reads if name == "bin/helper"], [64])
        self.assertTrue(all(0 <= size <= 262145 for _, size in reads))
        self.assertTrue(any("CRCs were not checked" in warning for warning in result["warnings"]))


class NativeHeaderEvidence(unittest.TestCase):
    def test_malformed_elf_headers_do_not_establish_arm64(self):
        bad_class = elf_header(bits=1)
        bad_version = elf_header()
        bad_version[6] = 0
        bad_object_version = elf_header()
        bad_object_version[20:24] = b"\0" * 4
        bad_header_size = elf_header()
        bad_header_size[52:54] = b"\0\0"
        for header in (elf_header()[:20], bad_class, bad_version, bad_object_version, bad_header_size):
            with self.subTest(header=bytes(header[:24])):
                result = inspect(bundle([("bin/helper", bytes(header))]))
                self.assertEqual(result["architecture"], "unknown")
                self.assertEqual(result["architecture_status"], "unverified")
                self.assertEqual(result["native_binaries"][0]["architecture"], "unknown")

    def test_truncated_macho_header_does_not_establish_arm64(self):
        header = b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x100000C)
        result = inspect(bundle([("bin/helper", header)]))
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["architecture_status"], "unverified")

    def test_valid_arm64_header_remains_evidence_without_runtime_claim(self):
        result = inspect(bundle([("bin/helper", bytes(elf_header()))]))
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertTrue(result["not_executed"])
