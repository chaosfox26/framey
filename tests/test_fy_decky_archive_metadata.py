import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_pkg
import fy_decky_catalog


def package(entries):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as output:
        for name, data in entries:
            output.writestr(name, data)
    return result.getvalue()


class ArchiveMetadataBoundaries(unittest.TestCase):
    def inspect(self, entries):
        data = package(entries)
        return fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest())

    def manifest(self, **extra):
        return json.dumps({"name": "Example", "flags": [], "api_version": 1, **extra}).encode()

    def elf(self, machine):
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[6] = 1
        struct.pack_into("<H", header, 16, 3)
        struct.pack_into("<H", header, 18, machine)
        struct.pack_into("<I", header, 20, 1)
        struct.pack_into("<H", header, 52, 64)
        return bytes(header)

    def test_python_only_decky_package_does_not_imply_arm_compatibility(self):
        result = self.inspect([("plugin.json", self.manifest()), ("main.py", "raise AssertionError('never run')")])
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["state"], "port_required")
        self.assertTrue(result["has_backend"])
        self.assertTrue(result["not_executed"])

    def test_arm_directory_label_cannot_override_an_x86_binary_header(self):
        result = self.inspect([("plugin.json", self.manifest()), ("bin/linux-arm64/helper", self.elf(62))])
        self.assertEqual(result["architecture"], "x86_64")
        self.assertEqual(result["architecture_status"], "incompatible")
        self.assertEqual(result["state"], "port_required")

    def test_arm_binary_evidence_still_requires_a_framey_port(self):
        result = self.inspect([("plugin.json", self.manifest(platform=["linux-aarch64"])),
                               ("dist/index.js", "// inspection fixture"), ("bin/helper", self.elf(183))])
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertEqual(result["state"], "port_required")
        self.assertTrue(result["not_executed"])

    def test_decky_layout_is_not_made_compatible_by_framey_style_metadata(self):
        result = self.inspect([("plugin.json", self.manifest(id="example", api=1, platform=["linux-aarch64"])),
                               ("dist/index.js", "// inspection fixture"), ("main.py", "raise AssertionError('never run')")])
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(result["architecture"], "unknown")

    def test_nul_truncated_zip_names_are_rejected_before_writing(self):
        original = b"safeXsuffix.txt"
        ambiguous = b"safe\x00suffix.txt"
        data = package([(original.decode(), "fixture")])
        self.assertEqual(data.count(original), 2)
        data = data.replace(original, ambiguous)
        for preserve in (False, True):
            with self.subTest(preserve_exec=preserve), tempfile.TemporaryDirectory() as root:
                with self.assertRaises(fy_pkg.PkgError) as caught:
                    fy_pkg.safe_extract_zip(data, root, preserve_exec=preserve)
                self.assertEqual(caught.exception.code, "unsafe_path")
                self.assertEqual(list(Path(root).iterdir()), [])


class DeckyStoreReservedIdTests(unittest.TestCase):
    def test_decky_store_is_reserved_for_the_loader_page(self):
        self.assertIn("decky-store", fy_pkg.RESERVED_PLUGIN_IDS)
        result = fy_pkg.normalize_manifest({"id": "decky-store", "name": "Package"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["value"]["id"], "")
        self.assertIn("id is reserved for Framey", result["errors"])

    def test_similar_plugin_identifiers_still_normalize(self):
        for plugin_id in ("decky-style", "my-decky-store", "decky-store-extra"):
            with self.subTest(plugin_id=plugin_id):
                result = fy_pkg.normalize_manifest({"id": plugin_id})
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["value"]["id"], plugin_id)

    def test_display_metadata_may_use_the_reserved_page_name(self):
        result = fy_pkg.normalize_manifest({"id": "example", "name": "Decky Store", "short": "decky-store"})
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
