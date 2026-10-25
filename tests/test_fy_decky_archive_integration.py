import contextlib
import hashlib
import importlib.util
import json
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import fy_decky_catalog
import jharness as H


class DeckyArchiveBoundaryIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.loader = H.Loader()
        self.addAsyncCleanup(self.loader.close)
        self.fy = self.loader.fy
        self.loader.plugin("target", {"name": "Existing native plugin"}, main="existing frontend")
        state = self.fy.settings()
        state["private_fixture"] = {"keep": "settings unchanged"}
        self.fy.save(state)
        self.before_plugins = self.plugins_snapshot()
        self.before_settings = self.fy.CONF.read_bytes()
        self.payload_marker = self.loader.base / "payload-was-executed"
        self.archive_calls = []
        self.feed_calls = []

    def plugins_snapshot(self):
        return {str(p.relative_to(self.loader.plugins)): p.read_bytes()
                for p in self.loader.plugins.rglob("*") if p.is_file()}

    def assert_installed_state_unchanged(self):
        self.assertEqual(self.plugins_snapshot(), self.before_plugins)
        self.assertEqual(self.fy.CONF.read_bytes(), self.before_settings)
        self.assertFalse(self.payload_marker.exists())

    def decky_archive(self, *, native_names=False, manifest_id=True):
        manifest = {"name": "Decky package", "author": "Example upstream", "api_version": 1,
                    "flags": ["root"], "version": "1.0.0"}
        if manifest_id:
            manifest["id"] = "target"
        payload = "from pathlib import Path\nPath(%r).write_text('unexpected execution')\n" % str(self.payload_marker)
        files = {"plugin.json": json.dumps(manifest), "main.py": payload,
                 "dist/index.js": "throw new Error('frontend must not be evaluated');",
                 "package.json": json.dumps({"name": "decky-package", "version": "1.0.0",
                                              "scripts": {"install": "must-not-run"},
                                              "dependencies": {"@decky/api": "1.0.0"}}),
                 "setup.py": payload, "__init__.py": payload,
                 "install-root.sh": "#!/bin/sh\nexit 97\n"}
        if native_names:
            files.update({"backend.py": payload, "main.js": "throw new Error('native frontend must not run');"})
        return H.mkzip(files, top="decky-package")

    def runtime_guards(self):
        stack = contextlib.ExitStack()
        guards = {}
        for name in ("stage_swap", "helper", "load_backends"):
            guards[name] = stack.enter_context(mock.patch.object(
                self.fy, name, side_effect=AssertionError("read-only boundary invoked " + name)))
        for name in ("reload", "page_reload"):
            guards[name] = stack.enter_context(mock.patch.object(
                self.fy, name, new=mock.AsyncMock(side_effect=AssertionError("read-only boundary invoked " + name))))
        guards["spec_from_file_location"] = stack.enter_context(mock.patch.object(
            importlib.util, "spec_from_file_location", side_effect=AssertionError("plugin import attempted")))
        guards["subprocess"] = stack.enter_context(mock.patch(
            "subprocess.Popen", side_effect=AssertionError("process execution attempted")))
        return stack, guards

    def assert_guards_unused(self, guards):
        for guard in guards.values():
            guard.assert_not_called()

    def catalog(self, archive, advertised_hash=None):
        digest = advertised_hash or hashlib.sha256(archive).hexdigest()
        feed = [{"id": 1, "name": "Decky package", "author": "Example upstream",
                 "description": "A package inspected without executing it", "tags": ["tools"],
                 "versions": [{"name": "1.0.0", "hash": digest}]}]

        async def fetcher(url, max_bytes):
            self.feed_calls.append(url)
            return json.dumps(feed).encode()

        async def archive_fetcher(url, max_bytes):
            self.archive_calls.append(url)
            return archive

        return fy_decky_catalog.DeckyCatalog(self.fy.host, fetcher=fetcher, archive_fetcher=archive_fetcher)

    async def reviewed_request(self, catalog):
        result = await catalog.details({"id": "decky-1", "refresh": True})
        self.assertFalse(result.get("error"), result)
        entry = result["entry"]
        return {"id": entry["id"], "identity": entry["identity"],
                "version_identity": entry["versions"][0]["identity"], "confirm": True}

    async def test_native_installer_rejects_decky_package_before_staging(self):
        for manifest_id in (False, True):
            with self.subTest(framey_style_id=manifest_id):
                stack, guards = self.runtime_guards()
                with stack:
                    result = await self.fy.install_zip(self.decky_archive(manifest_id=manifest_id))
                self.assertIn("error", result)
                self.assert_guards_unused(guards)
                self.assert_installed_state_unchanged()

    async def test_decky_metadata_is_not_made_native_by_adding_entrypoint_names(self):
        stack, guards = self.runtime_guards()
        with stack:
            result = await self.fy.install_zip(self.decky_archive(native_names=True))
        self.assertIn("error", result)
        self.assert_guards_unused(guards)
        self.assert_installed_state_unchanged()

    async def test_static_assessment_never_enters_native_install_import_or_execution_paths(self):
        for native_names in (False, True):
            with self.subTest(native_entrypoint_names=native_names):
                catalog = self.catalog(self.decky_archive(native_names=native_names))
                request = await self.reviewed_request(catalog)
                stack, guards = self.runtime_guards()
                with stack:
                    result = await catalog.assess(request)
                self.assertNotIn("error", result)
                self.assertTrue(result["read_only"])
                self.assertEqual(result["assessment"]["state"], "port_required")
                self.assertTrue(result["assessment"]["not_executed"])
                self.assertTrue(result["assessment"]["root_required"])
                self.assert_guards_unused(guards)
                self.assert_installed_state_unchanged()

    async def test_failed_hash_assessment_never_opens_archive_or_mutates_installation(self):
        catalog = self.catalog(self.decky_archive(), advertised_hash="0" * 64)
        request = await self.reviewed_request(catalog)
        stack, guards = self.runtime_guards()
        with stack, mock.patch("fy_decky_catalog.zipfile.ZipFile", side_effect=AssertionError("unverified archive opened")) as opener:
            result = await catalog.assess(request)
        self.assertEqual(result["code"], "digest_mismatch")
        opener.assert_not_called()
        self.assert_guards_unused(guards)
        self.assert_installed_state_unchanged()

    async def test_unconfirmed_assessment_never_fetches_package_or_mutates_installation(self):
        catalog = self.catalog(self.decky_archive())
        request = await self.reviewed_request(catalog)
        request["confirm"] = False
        stack, guards = self.runtime_guards()
        with stack:
            result = await catalog.assess(request)
        self.assertEqual(result["code"], "confirmation_required")
        self.assertEqual(self.archive_calls, [])
        self.assert_guards_unused(guards)
        self.assert_installed_state_unchanged()


if __name__ == "__main__":
    unittest.main()
