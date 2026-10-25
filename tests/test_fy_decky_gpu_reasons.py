import hashlib
import io
import json
import struct
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog


def inspect(source="", name="Example", binary=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps({"name": name, "flags": []}))
        archive.writestr("dist/index.js", "")
        archive.writestr("main.py", source)
        if binary is not None:
            archive.writestr("bin/tool", binary)
    payload = data.getvalue()
    return fy_decky_catalog.inspect_archive(payload, hashlib.sha256(payload).hexdigest())


def x86_64_header():
    data = bytearray(64)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", data, 16, 2, 62, 1)
    struct.pack_into("<H", data, 52, 64)
    return bytes(data)


class HardwareReasonTests(unittest.TestCase):
    def test_gpu_driver_hint_is_unverified_without_claiming_steam_deck_paths(self):
        result = inspect("driver = 'amdgpu'\n")
        self.assertEqual(result["hardware_assumptions"], ["amdgpu"])
        self.assertIn("References the AMD graphics driver; support for Steam Frame’s graphics hardware has not been verified.", result["reasons"])
        self.assertFalse(any("Steam Deck hardware or paths" in x for x in result["reasons"]))
        self.assertEqual(result["architecture_status"], "unverified")
        self.assertFalse(any("AMD-only" in x for x in result["reasons"]))

    def test_plugin_name_does_not_establish_a_gpu_requirement(self):
        result = inspect(name="AMD Frame Generation FSR")
        self.assertEqual(result["hardware_assumptions"], [])
        self.assertFalse(any("AMD graphics driver" in x for x in result["reasons"]))
        self.assertEqual(result["architecture"], "unknown")

    def test_processor_reason_does_not_imply_amd_graphics(self):
        result = inspect(binary=x86_64_header())
        self.assertEqual(result["architecture"], "x86_64")
        self.assertTrue(any("Intel/AMD processors" in x for x in result["reasons"]))
        self.assertFalse(any("AMD graphics" in x for x in result["reasons"]))

    def test_gpu_and_steam_deck_path_hints_have_separate_reasons(self):
        result = inspect("driver = 'amdgpu'\npath = '/sys/devices/platform/steamdeck'\n")
        self.assertTrue(any("AMD graphics driver" in x for x in result["reasons"]))
        self.assertTrue(any("Steam Deck hardware or paths" in x for x in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
