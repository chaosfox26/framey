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


def elf(machine, bits=64, byte_order="little"):
    size = 64 if bits == 64 else 52
    data = bytearray(size)
    data[:7] = b"\x7fELF" + bytes((2 if bits == 64 else 1, 1 if byte_order == "little" else 2, 1))
    data[16:18] = (3).to_bytes(2, byte_order)
    data[18:20] = machine.to_bytes(2, byte_order)
    data[20:24] = (1).to_bytes(4, byte_order)
    offset = 52 if bits == 64 else 40
    data[offset:offset + 2] = size.to_bytes(2, byte_order)
    return bytes(data)


def pe(machine):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)
    data[0x40:0x44] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x44, machine)
    return bytes(data)


def package(files, flags=(), dependencies=None, remote_binary=None):
    manifest = {"name": "Example Plugin", "author": "Example Author", "flags": list(flags), "api_version": 1}
    metadata = {"name": "example-plugin", "version": "1.0.0", "dependencies": dependencies or {}}
    if remote_binary is not None:
        metadata["remote_binary"] = remote_binary
    entries = {"plugin.json": json.dumps(manifest).encode(), "package.json": json.dumps(metadata).encode(), **files}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr("example-plugin/" + name, data)
    data = output.getvalue()
    return data, hashlib.sha256(data).hexdigest()


class ArchitectureAssessmentTests(unittest.TestCase):
    def assess(self, files, **metadata):
        data, digest = package(files, **metadata)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("inspection must not execute a program")), mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("inspection must not extract a file")):
            result = decky.inspect_archive(data, digest, "linux-aarch64")
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(result["reasons"][0], decky.PORT_REASON)
        self.assertIn("Framey", result["reasons"][0])
        self.assertTrue(result["not_executed"])
        self.assertFalse(result.get("runtime_verified", False))
        self.assertTrue(result["verified_sha256"])
        return result

    def test_arm64_elf_is_supplemental_evidence_not_a_compatibility_verdict(self):
        result = self.assess({"dist/index.js": b"throw new Error('not executed');", "bin/tool": elf(183)})
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertEqual([{key: item[key] for key in ("path", "format", "architecture")} for item in result["native_binaries"]], [{"path": "bin/tool", "format": "ELF", "architecture": "aarch64"}])
        self.assertTrue(any("ARM64" in reason and ("not verified" in reason or "does not establish" in reason) for reason in result["reasons"]))

    def test_x86_elf_variants_require_an_arm_build(self):
        for machine, bits, architecture in ((62, 64, "x86_64"), (3, 32, "x86")):
            with self.subTest(architecture=architecture):
                result = self.assess({"dist/index.js": b"export default {};", "bin/tool": elf(machine, bits)})
                self.assertEqual(result["native_binaries"][0]["architecture"], architecture)
                self.assertEqual(result["architecture_status"], "incompatible")
                self.assertTrue(any("ARM64" in reason for reason in result["reasons"][1:]))

    def test_mixed_arm_and_x86_bundle_does_not_prove_dispatch_or_runtime_support(self):
        result = self.assess({"dist/index.js": b"export default {};", "bin/tool-arm64": elf(183), "bin/tool-x86_64": elf(62)})
        self.assertEqual(result["architecture"], "mixed")
        self.assertEqual({item["architecture"] for item in result["native_binaries"]}, {"aarch64", "x86_64"})
        self.assertNotEqual(result["architecture_status"], "compatible")

    def test_arm32_does_not_count_as_arm64(self):
        result = self.assess({"bin/tool-arm": elf(40, 32)})
        self.assertEqual(result["native_binaries"][0]["architecture"], "arm")
        self.assertNotEqual(result["architecture_status"], "evidence_only")

    def test_pe_arm64_and_x86_remain_decky_packages_requiring_a_port(self):
        for machine in (0xAA64, 0x8664, 0x14C):
            with self.subTest(machine=machine):
                result = self.assess({"bin/helper.exe": pe(machine)})
                self.assertEqual(result["native_binaries"][0]["format"], "PE")
                self.assertNotEqual(result["architecture_status"], "compatible")
                self.assertFalse(any("Linux program" in reason for reason in result["reasons"]))

    def test_truncated_and_unknown_native_headers_stay_unverified(self):
        for header in (b"\x7fELF", b"\x7fELF\x02\x01\x01", elf(183)[:19], elf(0xFFFF), b"MZ"):
            with self.subTest(header=header):
                result = self.assess({"bin/unknown": header})
                self.assertEqual(result["architecture"], "unknown")
                self.assertEqual(result["architecture_status"], "unverified")

    def test_empty_native_extension_does_not_inherit_architecture_from_its_name(self):
        result = self.assess({"module.cpython-311-aarch64-linux-gnu.so": b"not an ELF binary"})
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["native_binaries"], [])

    def test_native_extension_headers_are_inspected_in_nested_python_modules(self):
        result = self.assess({"py_modules/example/native.cpython-311-x86_64-linux-gnu.so": elf(62)})
        self.assertEqual(result["architecture_status"], "incompatible")
        self.assertTrue(result["native_binaries"][0]["path"].endswith(".so"))

    def test_script_launchers_do_not_establish_native_dependency_support(self):
        result = self.assess({"bin/launch.sh": b"#!/bin/sh\nexec unknown-native-helper\n", "main.py": b"raise RuntimeError('must not execute')\n"})
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["architecture_status"], "unverified")
        self.assertTrue(result["has_backend"])
        self.assertEqual(result["native_binaries"], [])

    def test_python_only_backend_does_not_prove_steam_frame_compatibility(self):
        result = self.assess({"main.py": b"import missing_dependency\nraise RuntimeError('must not execute')\n"})
        self.assertTrue(result["has_backend"])
        self.assertFalse(result["has_frontend"])
        self.assertEqual(result["architecture_status"], "unverified")

    def test_root_flag_is_independent_of_native_architecture_evidence(self):
        for files in ({"main.py": b"pass\n"}, {"bin/helper": elf(183)}):
            with self.subTest(files=tuple(files)):
                result = self.assess(files, flags=["root"])
                self.assertTrue(result["root_required"])
                self.assertTrue(any("administrator" in reason and "helper" in reason for reason in result["reasons"]))

    def test_remote_programs_are_unverified_even_when_bundled_elf_is_arm64(self):
        result = self.assess({"bin/bundled": elf(183)}, remote_binary=[{"name": "extra", "url": "https://example.invalid/tool"}])
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertTrue(any("additional programs" in reason and "not been checked" in reason for reason in result["reasons"]))


    def test_declared_elf_class_and_header_fields_must_be_consistent(self):
        cases = [elf(183)[:20], elf(183)[:52]]
        for offset, replacement in ((6, b"\x00"), (20, b"\x02\x00\x00\x00"), (52, b"\x00\x00"), (16, b"\x00\x00")):
            data = bytearray(elf(183))
            data[offset:offset + len(replacement)] = replacement
            cases.append(bytes(data))
        cases.extend((elf(183, 32), elf(62, 32), elf(3, 64), elf(40, 64)))
        for header in cases:
            with self.subTest(header=header):
                result = self.assess({"bin/invalid": header})
                self.assertEqual(result["architecture"], "unknown")
                self.assertEqual(result["architecture_status"], "unverified")

    def test_truncated_macho_and_unparsed_fat_headers_do_not_establish_an_architecture(self):
        for header in (b"\xcf\xfa\xed\xfe" + (0x100000C).to_bytes(4, "little"), b"\xca\xfe\xba\xbe" + b"\x00" * 60):
            with self.subTest(header=header):
                result = self.assess({"bin/unknown-format": header})
                self.assertEqual(result["native_binaries"][0]["format"], "Mach-O")
                self.assertEqual(result["architecture"], "unknown")
                self.assertEqual(result["architecture_status"], "unverified")

    def test_macho_x86_is_not_described_as_a_linux_program(self):
        header = bytearray(32)
        header[:4] = b"\xcf\xfa\xed\xfe"
        header[4:8] = (0x1000007).to_bytes(4, "little")
        result = self.assess({"bin/mac-helper": bytes(header)})
        self.assertEqual(result["native_binaries"][0]["format"], "Mach-O")
        self.assertEqual(result["architecture_status"], "incompatible")
        self.assertFalse(any("Linux program" in reason for reason in result["reasons"]))

    def test_architecture_evidence_includes_binaries_beyond_display_row_limit(self):
        files = {"bin/arm-%02d" % number: elf(183) for number in range(64)}
        files["bin/last-x86"] = elf(62)
        result = self.assess(files)
        self.assertEqual(result["native_binary_count"], 65)
        self.assertEqual(len(result["native_binaries"]), 64)
        self.assertTrue(result["native_binaries_truncated"])
        self.assertEqual(result["architecture"], "mixed")
        self.assertTrue(any("different processor builds" in reason for reason in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
