import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "framey_plugin.py"
SPEC = importlib.util.spec_from_file_location("plugin_tools_test", SCRIPT)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class PluginToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.manifest = {"id": "sample", "name": "Sample", "version": "1.2.3", "api": 1,
                         "platform": ["linux-arm64", "linux-x86_64"], "framey": {"min": "1.0"}}
        self.save_manifest()
        (self.source / "main.js").write_text("return { name: 'Sample' };\n", encoding="utf-8")
        (self.source / "backend.py").write_text("def call(method, arg):\n    return arg\n", encoding="utf-8")

    def save_manifest(self):
        (self.source / "plugin.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def write(self, name, data=b"fixture"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def refused(self, code=None, **kwargs):
        with self.assertRaises((tool.ToolError, tool.pk.PkgError)) as caught:
            tool.validate(self.source, **kwargs)
        if code:
            self.assertEqual(caught.exception.code, code)

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)], cwd=cwd or self.root,
                              capture_output=True, text=True, timeout=30)

    def test_deterministic_package_metadata_and_safe_extractor_roundtrip(self):
        self.write("assets/logo.svg", b"<svg/>")
        self.write("locales/en.json", b'{"hello":"Hello"}')
        self.write("LICENSE", b"Fixture license")
        self.write("vendor/NOTICE.txt", b"Fixture notice")
        first, second = self.root / "first.zip", self.root / "second.zip"
        report = tool.pack(self.source, first)
        for path in self.source.rglob("*"):
            os.chmod(path, 0o755 if path.is_dir() else 0o700)
            os.utime(path, (1700000000, 1700000000))
        tool.pack(self.source, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(report["sha256"], hashlib.sha256(first.read_bytes()).hexdigest())
        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            metadata = json.loads(archive.read(tool.METADATA))
            self.assertEqual(metadata["id"], "sample")
            self.assertEqual(metadata["platform"], ["linux-aarch64", "linux-x86_64"])
            for entry in metadata["files"]:
                data = archive.read(entry["path"])
                self.assertEqual(entry["size"], len(data))
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
            for info in archive.infolist():
                self.assertEqual(info.date_time, tool.STAMP)
                self.assertEqual(info.external_attr >> 16, stat.S_IFREG | 0o644)
        dest = self.root / "unpacked"
        extracted = tool.pk.safe_extract_zip(first.read_bytes(), dest)
        self.assertEqual(extracted, report["package_files"])
        for name in report["files"]:
            self.assertEqual((dest / name).read_bytes(), (self.source / name).read_bytes())
        self.assertEqual(tool.validate(dest)["id"], "sample")

    def test_allowlist_omits_local_state_and_preserves_notices(self):
        omitted = [".git/config", ".env", ".identity.json", ".source", "settings.json", "data/state.json",
                   "cache/session.json", "assets/.session", "assets/__pycache__/module.pyc",
                   "vendor/node_modules/unused.js", "assets/private.key", "assets/cache/token.json"]
        for name in omitted:
            self.write(name, b"unpublished fixture")
        self.write("COPYING.md", b"Fixture terms")
        output = self.root / "package.zip"
        report = tool.pack(self.source, output)
        with zipfile.ZipFile(output) as archive:
            self.assertFalse(set(omitted) & set(archive.namelist()))
            self.assertIn("COPYING.md", archive.namelist())
            self.assertNotIn(b"unpublished fixture", output.read_bytes())
        self.assertIn("settings.json", report["omitted"])

    def test_manifest_validation_refuses_bad_ids_versions_types_and_constraints(self):
        cases = [{"id": "../escape"}, {"id": "_core"}, {"version": "unknown"}, {"api": True},
                 {"api": 0}, {"platform": "linux-aarch64"}, {"platform": ["windows-x86_64"]},
                 {"framey": {"min": "2.0", "max": "1.0"}}, {"backend": {"memory_mb": 65}},
                 {"companions": [{"name": "incomplete"}]}]
        original = self.manifest.copy()
        for patch in cases:
            with self.subTest(patch=patch):
                self.manifest = dict(original, **patch)
                self.save_manifest()
                self.refused()

    def test_manifest_json_missing_duplicate_and_nonfinite(self):
        path = self.source / "plugin.json"
        for data in (b"[]", b"{", b"\xff", b'{"id":"sample","id":"other"}', b'{"id":"sample","timeout":NaN}'):
            path.write_bytes(data)
            self.refused()
        path.unlink()
        self.refused()

    def test_entrypoint_validation_and_backend_only_plugin(self):
        (self.source / "main.js").unlink()
        self.assertEqual(tool.validate(self.source)["id"], "sample")
        for content in (b"", b"\xff", b"x\x00", b"def broken(:\n", b"return 1\n"):
            (self.source / "backend.py").write_bytes(content)
            self.refused()
        (self.source / "backend.py").unlink()
        self.refused()

    def test_plugin_code_is_never_executed(self):
        marker = self.root / "executed"
        self.write("backend.py", b"raise RuntimeError('must not execute')\n")
        self.assertEqual(tool.validate(self.source)["id"], "sample")
        self.assertFalse(marker.exists())

    def test_complete_decky_layout_is_refused_with_a_plain_format_reason(self):
        (self.source / "main.js").unlink()
        (self.source / "backend.py").unlink()
        self.manifest = {"name": "Sample Decky Plugin", "api_version": 1, "flags": []}
        self.save_manifest()
        self.write("package.json", b'{"name":"sample-decky-plugin","version":"1.0.0"}')
        self.write("main.py", b"class Plugin:\n    async def _main(self):\n        pass\n")
        self.write("dist/index.js", b"export default function sample() { return {}; }\n")
        self.write("LICENSE", b"Synthetic fixture license")
        output = self.root / "decky.zip"
        with self.assertRaises(tool.ToolError) as caught:
            tool.pack(self.source, output)
        self.assertEqual(caught.exception.code, "unsupported_format")
        self.assertIn("api_version", str(caught.exception))
        self.assertIn("dist/index.js", str(caught.exception))
        self.assertIn("main.py", str(caught.exception))
        self.assertIn("Port the source", str(caught.exception))
        self.assertFalse(output.exists())
        result = self.cli("validate", self.source)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Decky format is unsupported", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_decky_markers_require_actual_layout_evidence_and_no_framey_entrypoint(self):
        self.manifest["api_version"] = 1
        self.save_manifest()
        self.write("dist/index.js", b"fixture compiled frontend")
        self.write("main.py", b"raise RuntimeError('not Framey backend')\n")
        report = tool.validate(self.source)
        self.assertEqual(report["id"], "sample")
        self.assertIn("main.py", report["omitted"])
        self.assertIn("dist/", report["omitted"])
        (self.source / "main.js").unlink()
        (self.source / "backend.py").unlink()
        (self.source / "dist" / "index.js").unlink()
        self.refused("unsupported_format")
        (self.source / "main.py").unlink()
        self.write("dist/index.js", b"fixture compiled frontend")
        self.refused("unsupported_format")
        (self.source / "dist" / "index.js").unlink()
        self.refused("invalid_plugin")
        self.manifest.pop("api_version")
        self.save_manifest()
        self.write("main.py", b"ordinary unrelated Python module")
        self.refused("invalid_plugin")

    def test_decky_detection_does_not_follow_omitted_links_or_read_omitted_code(self):
        self.manifest["api_version"] = 1
        self.save_manifest()
        (self.source / "main.js").unlink()
        (self.source / "backend.py").unlink()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "index.js").write_bytes(b"fixture")
        (self.source / "dist").symlink_to(outside, target_is_directory=True)
        (self.source / "main.py").symlink_to(outside / "index.js")
        self.refused("invalid_plugin")
        (self.source / "main.py").unlink()
        self.write("main.py", b"fixture")
        original = tool._read
        with mock.patch.object(tool, "_read", side_effect=lambda path: original(path) if path.name == "plugin.json" else self.fail("omitted code read")):
            self.refused("unsupported_format")

    def test_explicit_source_helper_port_is_valid_framey(self):
        report = tool.validate(ROOT / "examples" / "decky-style", framey_version="1.0.2", platform="linux-aarch64")
        self.assertEqual(report["compatibility"]["state"], "ok")
        self.assertIn("main.js", report["files"])
        self.assertIn("backend.py", report["files"])
        self.assertNotIn("dist/index.js", report["files"])

    def test_compatibility_and_missing_declarations(self):
        report = tool.validate(self.source, framey_version="1.0.2", platform="linux-aarch64")
        self.assertEqual(report["compatibility"]["state"], "ok")
        self.refused("incompatible", framey_version="0.1", platform="linux-aarch64")
        self.refused("incompatible", framey_version="1.2", platform="linux-riscv64")
        self.refused("bad_target", framey_version="1.2")
        self.refused("bad_target", framey_version="bad", platform="linux-aarch64")
        self.refused("bad_target", api=True)
        self.manifest.pop("api")
        self.manifest.pop("platform")
        self.save_manifest()
        self.assertEqual(len(tool.validate(self.source)["warnings"]), 2)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_source_and_included_symlinks_refused(self):
        link = self.root / "linked-source"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(tool.ToolError):
            tool.validate(link)
        outside = self.root / "outside.js"
        outside.write_text("fixture", encoding="utf-8")
        self.source.joinpath("main.js").unlink()
        self.source.joinpath("main.js").symlink_to(outside)
        self.refused("unsafe_type")
        self.source.joinpath("main.js").unlink()
        self.source.joinpath("assets").symlink_to(self.root, target_is_directory=True)
        self.refused("unsafe_type")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_special_file_refused_without_blocking(self):
        os.mkfifo(self.source / "main.js.fifo")
        self.source.joinpath("backend.py").unlink()
        os.mkfifo(self.source / "backend.py")
        self.refused("unsafe_type")

    def test_unsafe_paths_case_and_unicode_collisions_refused(self):
        for names, code in ((["assets/a:b.js"], "unsafe_path"),
                            (["assets/Case.js", "assets/case.js"], "case_collision"),
                            (["assets/é.js", "assets/e\u0301.js"], "case_collision"),
                            (["assets/" + "nested/" * 8 + "deep.js"], "unsafe_path")):
            with self.subTest(names=names):
                paths = [self.write(name) for name in names]
                self.refused(code)
                for path in paths:
                    path.unlink()
                import shutil
                shutil.rmtree(self.source / "assets")

    def test_size_count_and_generated_metadata_budgets(self):
        with mock.patch.object(tool.pk, "ENTRY_MAX", 10):
            self.refused("too_large")
        with mock.patch.object(tool.pk, "TOTAL_MAX", 10):
            self.refused("too_large")
        with mock.patch.object(tool.pk, "ENTRIES", 2):
            self.refused("too_many")
        with mock.patch.object(tool.pk, "ENTRIES", 3):
            with self.assertRaises(tool.pk.PkgError):
                tool.pack(self.source, self.root / "too-many.zip")
        with mock.patch.object(tool, "PACKAGE_MAX", 10):
            with self.assertRaises(tool.ToolError):
                tool.pack(self.source, self.root / "too-big.zip")
        self.assertFalse(list(self.root.glob("*.zip")))

    def test_output_is_explicit_new_external_zip(self):
        existing = self.root / "existing.zip"
        existing.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            tool.pack(self.source, existing)
        self.assertEqual(existing.read_bytes(), b"preserve")
        for output in (self.source / "package.zip", self.source / "main.js", self.root / "package.txt"):
            with self.assertRaises(tool.ToolError):
                tool.pack(self.source, output)
        with self.assertRaises(FileNotFoundError):
            tool.pack(self.source, self.root / "absent" / "package.zip")
        self.assertFalse((self.root / "absent").exists())
        directory = self.root / "directory.zip"
        directory.mkdir()
        with self.assertRaises(FileExistsError):
            tool.pack(self.source, directory)
        self.assertTrue(directory.is_dir())
        link = self.root / "linked.zip"
        link.symlink_to(existing)
        with self.assertRaises(FileExistsError):
            tool.pack(self.source, link)
        dangling = self.root / "dangling.zip"
        dangling.symlink_to(self.root / "absent.zip")
        with self.assertRaises(FileExistsError):
            tool.pack(self.source, dangling)
        self.assertFalse((self.root / "absent.zip").exists())

    def test_partial_write_is_removed(self):
        output = self.root / "broken.zip"
        with mock.patch.object(tool.os, "fsync", side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                tool.pack(self.source, output)
        self.assertFalse(output.exists())

    def test_output_parent_link_into_source_is_refused(self):
        parent = self.root / "linked-output"
        parent.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(tool.ToolError):
            tool.pack(self.source, parent / "package.zip")
        self.assertFalse((self.source / "package.zip").exists())

    def test_source_module_loading_does_not_create_bytecode_cache(self):
        checkout = self.root / "checkout"
        (checkout / "tools").mkdir(parents=True)
        (checkout / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
        (checkout / "fy_pkg.py").write_bytes((ROOT / "fy_pkg.py").read_bytes())
        result = subprocess.run([sys.executable, "-I", str(checkout / "tools" / SCRIPT.name), "validate", str(self.source)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((checkout / "__pycache__").exists())

    def test_cli_help_errors_reports_and_safe_import_location(self):
        self.write("fy_pkg.py", b"raise RuntimeError('untrusted import')")
        (self.root / "fy_pkg.py").write_text("raise RuntimeError('untrusted import')", encoding="utf-8")
        self.assertEqual(self.cli("--help").returncode, 0)
        self.assertEqual(self.cli("pack", self.source).returncode, 2)
        self.assertEqual(self.cli("validate", self.source, "--api", "0").returncode, 2)
        result = self.cli("validate", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], "sample")
        output = self.root / "cli.zip"
        result = self.cli("pack", self.source, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        result = self.cli("pack", self.source, "--output", output)
        self.assertEqual(result.returncode, 3)
        self.assertIn("io_error", result.stderr)
        (self.source / "plugin.json").write_text("{}", encoding="utf-8")
        result = self.cli("validate", self.source)
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid_plugin", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
