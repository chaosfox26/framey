import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("framey_package_source_contract", ROOT / "framey.py")
FY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FY)
COMMIT = "a" * 40


class CatalogSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fy-source-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.plugins = Path(self.tmp.name) / "plugins"
        self.plugin = self.plugins / "demo"
        self.plugin.mkdir(parents=True)
        self.source = "https://github.com/example/framey-demos/tree/" + COMMIT
        (self.plugin / ".source").write_text(self.source)
        self.manifest = {"id": "demo", "source": "https://github.com/example/fallback"}
        patcher = mock.patch.object(FY, "PLUGINS", self.plugins)
        patcher.start()
        self.addCleanup(patcher.stop)

    def receipt(self, **changes):
        value = {
            "v": 1,
            "id": "demo",
            "source": {
                "repository": "https://github.com/example/framey-demos",
                "commit": COMMIT,
                "path": "examples/demo",
            },
            "identity": "b" * 64,
        }
        value.update(changes)
        (self.plugin / ".catalog.json").write_text(json.dumps(value))

    def assert_blocked(self):
        source, message = FY.source(self.manifest)
        self.assertIsNone(source)
        self.assertIsInstance(message, str)
        self.assertTrue(message.strip())

    def test_legacy_source_without_receipt_still_resolves_the_pinned_commit(self):
        source, message = FY.source(self.manifest)
        self.assertEqual(source, ("example", "framey-demos", COMMIT))
        self.assertEqual(message, "")

    def test_catalog_subdirectory_never_falls_back_to_repository_root_update(self):
        self.receipt()
        self.assert_blocked()

    def test_catalog_root_package_stays_on_the_reviewed_catalog_update_path(self):
        self.receipt(source={"repository": "https://github.com/example/framey-demos", "commit": COMMIT, "path": ""})
        self.assert_blocked()

    def test_malformed_receipt_never_falls_back_to_source_or_manifest(self):
        (self.plugin / ".catalog.json").write_text("{broken")
        self.assert_blocked()

    def test_nonobject_receipt_never_falls_back_to_source_or_manifest(self):
        for value in ([], "demo", 1, None, True):
            with self.subTest(value=value):
                (self.plugin / ".catalog.json").write_text(json.dumps(value))
                self.assert_blocked()

    def test_incomplete_receipt_never_falls_back_to_source_or_manifest(self):
        for value in ({}, {"v": 1}, {"v": 1, "id": "demo", "source": {}}):
            with self.subTest(value=value):
                (self.plugin / ".catalog.json").write_text(json.dumps(value))
                self.assert_blocked()

    def test_receipt_with_different_plugin_identity_is_not_a_generic_source(self):
        self.receipt(id="another-plugin")
        self.assert_blocked()

    def test_unsafe_receipt_path_cannot_enable_generic_repository_updates(self):
        for path in ("../demo", "/demo", "examples/../demo", "examples\\demo"):
            with self.subTest(path=path):
                self.receipt(source={"repository": "https://github.com/example/framey-demos", "commit": COMMIT, "path": path})
                self.assert_blocked()

    def test_oversize_receipt_cannot_enable_generic_repository_updates(self):
        self.receipt(source={"repository": "https://github.com/example/framey-demos", "commit": COMMIT, "path": ""})
        path = self.plugin / ".catalog.json"
        with path.open("a") as handle:
            handle.write(" " * 8193)
        self.assert_blocked()

    def test_directory_receipt_is_refused(self):
        (self.plugin / ".catalog.json").mkdir()
        self.assert_blocked()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_receipt_is_refused_without_following_its_target(self):
        outside = Path(self.tmp.name) / "receipt.json"
        outside.write_text("{}")
        try:
            (self.plugin / ".catalog.json").symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(str(error))
        self.assert_blocked()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_broken_receipt_symlink_does_not_look_like_an_unmanaged_plugin(self):
        outside = Path(self.tmp.name) / "missing-receipt.json"
        try:
            (self.plugin / ".catalog.json").symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(str(error))
        self.assert_blocked()


if __name__ == "__main__":
    unittest.main()
