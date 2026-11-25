"""Native-core distribution selection uses literal source data, never imports Framey."""

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/pack_core.py"
SOURCE = b'NATIVE_CORE_LAYOUT = 1\nMANIFEST = ("framey.py", "core-files.txt", "fy_core_layout.py", "fy_decky_runtime.py", "fy-optional.js", "native.py")\n'
WIRE = b"framey-native-core-v1\nLICENSE\ncore-files.txt\nframey.py\nfy-optional.js\nfy_core_layout.py\nfy_decky_runtime.py\nnative.py\n"


class CoreArchive(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HELPER.is_file(), "Missing native-core selection implementation")
        spec = importlib.util.spec_from_file_location("owned_pack_core", HELPER)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)

    def test_layout_verifier_is_shipped_without_importing_build_tools(self):
        helper = ROOT / "fy_core_layout.py"
        self.assertTrue(helper.is_file(), "Core runtime lacks its layout verifier")
        spec = importlib.util.spec_from_file_location("owned_runtime_layout", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module.core_files(SOURCE, WIRE),
            (
                "LICENSE",
                "core-files.txt",
                "framey.py",
                "fy-optional.js",
                "fy_core_layout.py",
                "fy_decky_runtime.py",
                "native.py",
            ),
        )

    def test_selection_rejects_stale_wire_instead_of_silently_adding_new_files(self):
        with self.assertRaises(ValueError):
            self.helper.core_files(SOURCE, WIRE.replace(b"native.py\n", b"other.py\n"))

    def test_core_file_list_is_generated_without_importing_source(self):
        hostile = SOURCE + b'raise AssertionError("SOURCE MUST NOT EXECUTE")\n'
        self.assertEqual(self.helper.generate_manifest(hostile), WIRE)
        self.assertEqual(
            self.helper.core_files(hostile, WIRE),
            (
                "LICENSE",
                "core-files.txt",
                "framey.py",
                "fy-optional.js",
                "fy_core_layout.py",
                "fy_decky_runtime.py",
                "native.py",
            ),
        )

    def test_legacy_and_bool_layout_markers_are_rejected(self):
        for source in [
            SOURCE.replace(b"NATIVE_CORE_LAYOUT = 1\n", b""),
            SOURCE.replace(b"= 1\n", b"= True\n"),
        ]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.helper.generate_manifest(source)

    def test_optional_payload_cannot_be_reintroduced_by_manifest(self):
        for name in [
            "decky_runtime/runtime.py",
            "compat/decky-host.js",
            "compat/framey-view.js",
            "fy_decky_import.py",
            "fy_code_http.py",
            "tools/framey_decky.py",
        ]:
            source = SOURCE.replace(b'"native.py"', ('"' + name + '"').encode())
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.helper.generate_manifest(source)

    def test_generator_refuses_metadata_too_large_for_native_app(self):
        names = ["a" * 395 + str(index) + ".py" for index in range(200)]
        source = SOURCE.replace(b'"native.py"', ",".join(repr(name) for name in names).encode())
        with self.assertRaises(ValueError):
            self.helper.generate_manifest(source)

    def test_unsafe_duplicate_or_dynamic_selection_is_rejected(self):
        for value in [
            '"../native.py"',
            '"/native.py"',
            '"a\\\\b.py"',
            '"native.py", "NATIVE.py"',
            "*EXTRA",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper.generate_manifest(SOURCE.replace(b'"native.py"', value.encode()))

    def test_optional_case_aliases_and_manifest_reassignment_are_rejected(self):
        for source in [
            SOURCE.replace(b'"native.py"', b'"DECKY_RUNTIME/runtime.py"'),
            SOURCE + b'MANIFEST += ("decky_runtime/runtime.py",)\n',
            SOURCE.replace(b'"native.py"', b'"license"'),
        ]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.helper.generate_manifest(source)

    def test_core_archive_is_exact_and_deterministic_with_optional_source_present(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            root.mkdir()
            files = {
                "framey.py": SOURCE,
                "core-files.txt": WIRE,
                "LICENSE": b"OWN LICENSE\n",
                "fy_core_layout.py": b"CORE VERIFIER\n",
                "fy_decky_runtime.py": b"CORE MANAGER\n",
                "fy-optional.js": b"CORE FACADE\n",
                "native.py": b"CORE NATIVE\n",
                "decky_runtime/runtime.py": b"OPTIONAL\n",
                "compat/decky-host.js": b"OPTIONAL\n",
            }
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            first = Path(folder) / "one.tar.gz"
            second = Path(folder) / "two.tar.gz"
            self.helper.write_core_archive(root, first, "a" * 40)
            self.helper.write_core_archive(root, second, "a" * 40)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(fileobj=io.BytesIO(first.read_bytes()), mode="r:gz") as archive:
                names = archive.getnames()
                self.assertEqual(
                    names,
                    [
                        "framey/" + name
                        for name in [
                            ".version",
                            "LICENSE",
                            "core-files.txt",
                            "framey.py",
                            "fy-optional.js",
                            "fy_core_layout.py",
                            "fy_decky_runtime.py",
                            "native.py",
                        ]
                    ],
                )
                self.assertEqual(archive.extractfile("framey/.version").read(), b"a" * 40)

    def test_selected_symlink_never_enters_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            root.mkdir()
            files = {
                "framey.py": SOURCE,
                "core-files.txt": WIRE,
                "LICENSE": b"OWN",
                "fy_core_layout.py": b"OWN",
                "fy_decky_runtime.py": b"OWN",
                "fy-optional.js": b"OWN",
            }
            for name, data in files.items():
                (root / name).write_bytes(data)
            outside = Path(folder) / "outside.py"
            outside.write_bytes(b"KEEP")
            try:
                (root / "native.py").symlink_to(outside)
            except OSError:
                self.skipTest("Platform does not permit own fixture symlink")
            output = Path(folder) / "core.tar.gz"
            with self.assertRaises(ValueError):
                self.helper.write_core_archive(root, output, "a" * 40)
            self.assertFalse(output.exists())
            self.assertEqual(outside.read_bytes(), b"KEEP")

    def test_missing_selected_file_never_produces_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            root.mkdir()
            (root / "framey.py").write_bytes(SOURCE)
            (root / "core-files.txt").write_bytes(WIRE)
            output = Path(folder) / "core.tar.gz"
            with self.assertRaises(ValueError):
                self.helper.write_core_archive(root, output, "a" * 40)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
