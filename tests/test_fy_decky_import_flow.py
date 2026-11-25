import asyncio
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fy_catalog
from decky_runtime import catalog as decky
from decky_runtime import runtime as provider
from types import SimpleNamespace
SPEC = importlib.util.spec_from_file_location("framey_decky_import_flow", ROOT / "framey.py")
FRAMEY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FRAMEY)
RUNTIME_FILES = (
    "decky_runtime/catalog.py", "decky_runtime/importer.py", "decky_runtime/code.py",
    "decky_runtime/http.py", "decky_runtime/runtime.py", "decky_runtime/compat/decky-host.js",
)



def original_package(version="1.0.0", flags=None, extra=None):
    files = {
        "plugin.json": json.dumps({"name": "Original fixture", "flags": flags or [], "api_version": 2}),
        "package.json": json.dumps({"version": version, "type": "module"}),
        "dist/index.js": 'export default () => ({content: "Original panel"});',
        "main.py": 'raise AssertionError("Import inspection must not execute the backend")\n',
        "LICENSE": "Fixture license\n",
    }
    files.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr("original/" + name, value)
    return output.getvalue()


class ImportHost:
    def __init__(self, root):
        self.ROOT, self.HOME = root / "loader", root / "home"
        self.PLUGINS = self.ROOT / "plugins"
        self.CONF = self.HOME / ".config/framey/settings.json"
        self.PLUGINS.mkdir(parents=True)
        self.CONF.parent.mkdir(parents=True)
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.0.2", "linux-aarch64", 1
        self.clock = 1_790_000_000
        self.state = {"prefs": {"dev": {"enabled": True}}}
        self.waiting, self.reloads, self.swaps = set(), [], []
        self.health, self.on_reload, self.on_swap = "", None, None

    def now(self):
        return self.clock

    def settings(self):
        return copy.deepcopy(self.state)

    def pending(self, pid):
        return pid in self.waiting

    def stage_swap(self, pid, fill, **options):
        result = FRAMEY.stage_swap(pid, fill, **options)
        self.swaps.append(pid)
        if self.on_swap:
            self.on_swap(pid)
        return result

    async def reload(self, pid):
        self.reloads.append(pid)
        if self.on_reload:
            self.on_reload(pid)

    async def backend_check(self, pid):
        return self.health


class DeckyImportFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fy-decky-import-flow-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.host = ImportHost(self.root)
        self.patches = mock.patch.multiple(
            FRAMEY, ROOT=self.host.ROOT, HOME=self.host.HOME, CONF=self.host.CONF,
            PLUGINS=self.host.PLUGINS, pending=self.host.pending,
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.payloads = {name: original_package(name) for name in ("2.0.0", "1.0.0")}
        self.hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.payloads.items()}
        self.feed = [{"id": 31, "name": "Original fixture", "versions": [
            {"name": name, "hash": self.hashes[name]} for name in ("2.0.0", "1.0.0")
        ]}]
        self.archives = {decky.CDN + self.hashes[name] + ".zip": data
                         for name, data in self.payloads.items()}
        self.downloads, self.before_download = [], None

        async def metadata(url, limit):
            self.assertIn(url, {value["url"] for value in decky.SOURCES.values()})
            return json.dumps(self.feed).encode()

        async def archive(url, limit):
            self.assertEqual(limit, 5_000_000)
            self.downloads.append(url)
            if self.before_download:
                self.before_download()
            return self.archives[url]

        # These are provider transaction tests, not manager verification tests.
        # The core manager's verified-package/state boundary has its own suite.
        self.runtime_enabled = True
        self.lease = SimpleNamespace(entry=provider)
        manager = SimpleNamespace(
            acquire=lambda host: self.lease if self.runtime_enabled else (_ for _ in ()).throw(RuntimeError("disabled")),
            current=lambda host, lease: self.runtime_enabled and lease is self.lease,
            status=lambda host: {"enabled": self.runtime_enabled},
        )
        self.manager_patch = mock.patch.dict(sys.modules, {"fy_decky_runtime": manager})
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.addAsyncCleanup(provider.shutdown, self.host)
        self.catalog = fy_catalog.Catalog(self.host, metadata, archive)
        fy_catalog._instances[self.host] = self.catalog

    async def selection(self, version="1.0.0", source="decky_stable"):
        reply = await self.catalog.details({"source": source, "id": "decky-31"})
        entry = reply["entry"]
        selected = next(value for value in entry["versions"] if value["name"] == version)
        return {"source": source, "id": "decky-31", "identity": entry["identity"],
                "version_identity": selected["identity"], "profile": 1, "confirm": True}

    async def invoke(self, request):
        self.assertIn("catalog_import", fy_catalog.METHODS,
                      "The loader must expose a separate confirmed experimental import")
        return await fy_catalog.METHODS["catalog_import"](self.host, request)

    def destination(self):
        return self.host.PLUGINS / "decky-31"

    async def test_selected_nondefault_original_version_is_wrapped_with_bound_receipt(self):
        selected = await self.selection()
        result = await self.invoke(selected)
        self.assertEqual(result.get("ok"), "decky-31", result)
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["hash"], self.hashes["1.0.0"])
        self.assertEqual(result["profile"], 1)
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["updated"])
        self.assertTrue(result["experimental"])
        self.assertEqual(self.host.reloads, ["decky-31"])
        self.assertEqual(self.downloads, [decky.CDN + self.hashes["1.0.0"] + ".zip"])
        destination = self.destination()
        self.assertTrue((destination / "main.js").is_file())
        self.assertTrue((destination / "backend.py").is_file())
        self.assertEqual((destination / "vendor/decky/main.py").read_text(),
                         'raise AssertionError("Import inspection must not execute the backend")\n')
        self.assertEqual((destination / "vendor/decky/LICENSE").read_text(), "Fixture license\n")
        receipt = json.loads((destination / ".catalog.json").read_text())
        for field in ("identity", "version_identity", "profile"):
            self.assertEqual(receipt[field], selected[field])
        self.assertEqual(receipt["source"], decky.SOURCES["decky_stable"])
        self.assertEqual(receipt["hash"], self.hashes["1.0.0"])
        self.assertEqual(receipt["version"], "1.0.0")
        self.assertFalse(receipt["runtime_verified"])
        self.assertIsNone(FRAMEY.source({"id": "decky-31"})[0])

    async def test_unconfirmed_or_wrong_profile_never_downloads_or_installs(self):
        selected = await self.selection()
        for change in ({"confirm": False}, {"profile": 2}, {"profile": True},
                       {"identity": "bad"}, {"version_identity": "bad"}):
            result = await self.invoke(selected | change)
            self.assertIn("code", result)
            self.assertNotIn("ok", result)
        self.assertEqual(self.downloads, [])
        self.assertFalse(self.destination().exists())

    async def test_official_source_and_testing_developer_guard_are_enforced(self):
        selected = await self.selection()
        for source in ("custom", "stable", "unknown"):
            self.assertEqual((await self.invoke(selected | {"source": source}))["code"], "bad_source")
        self.host.state["prefs"]["dev"]["enabled"] = False
        self.assertEqual((await self.invoke(selected | {"source": "decky_testing"}))["code"], "dev_off")
        self.assertEqual(self.downloads, [])

    async def test_cross_source_and_stale_listing_or_version_never_fetch_package(self):
        selected = await self.selection()
        self.assertEqual((await self.invoke(selected | {"source": "decky_testing"}))["code"], "stale_catalog")
        self.assertEqual((await self.invoke(selected | {"version_identity": "0" * 64}))["code"], "stale_catalog")
        self.feed[0]["versions"].pop()
        await self.catalog.list({"source": "decky_stable", "refresh": True})
        self.assertEqual((await self.invoke(selected))["code"], "stale_catalog")
        self.assertEqual(self.downloads, [])

    async def test_stale_catalog_cannot_import_when_refresh_fails(self):
        selected = await self.selection()
        self.host.clock += 90000

        async def failed_metadata(url, limit):
            raise OSError("offline")

        provider._SERVICES[self.host].fetcher = failed_metadata
        self.assertEqual((await self.invoke(selected))["code"], "stale_catalog")
        self.assertEqual(self.downloads, [])

    async def test_digest_is_rechecked_after_successful_read_only_inspection(self):
        selected = await self.selection()
        inspected = await self.catalog.assess(selected)
        self.assertTrue(inspected["assessment"]["not_executed"])
        self.archives[decky.CDN + self.hashes["1.0.0"] + ".zip"] = self.payloads["2.0.0"]
        self.assertEqual((await self.invoke(selected))["code"], "digest_mismatch")
        self.assertEqual(len(self.downloads), 2)
        self.assertFalse(self.destination().exists())

    async def test_profile_flags_are_rejected_without_executing_original_backend(self):
        self.payloads["1.0.0"] = original_package(flags=["root"])
        self.hashes["1.0.0"] = hashlib.sha256(self.payloads["1.0.0"]).hexdigest()
        self.feed[0]["versions"][1]["hash"] = self.hashes["1.0.0"]
        self.archives[decky.CDN + self.hashes["1.0.0"] + ".zip"] = self.payloads["1.0.0"]
        result = await self.invoke(await self.selection())
        self.assertEqual(result["code"], "unsupported_profile")
        self.assertFalse(self.destination().exists())

    async def test_catalog_version_must_match_original_package_version(self):
        self.feed[0]["versions"][1]["name"] = "9.0.0"
        result = await self.invoke(await self.selection("9.0.0"))
        self.assertEqual(result["code"], "identity_mismatch")
        self.assertFalse(self.destination().exists())

    async def test_existing_destination_backup_stage_and_pending_are_preserved_before_download(self):
        selected = await self.selection()
        for name in ("decky-31", ".old-decky-31", ".stage-decky-31"):
            path = self.host.PLUGINS / name
            path.mkdir()
            (path / "user.txt").write_text("keep me")
            result = await self.invoke(selected)
            self.assertNotIn("ok", result)
            self.assertEqual((path / "user.txt").read_text(), "keep me")
            shutil.rmtree(path)
        self.host.waiting.add("decky-31")
        self.assertEqual((await self.invoke(selected))["code"], "pending")
        self.assertEqual(self.downloads, [])

    async def test_broken_destination_symlink_is_never_replaced(self):
        self.destination().symlink_to(self.root / "not-there")
        result = await self.invoke(await self.selection())
        self.assertNotIn("ok", result)
        self.assertTrue(self.destination().is_symlink())
        self.assertEqual(self.downloads, [])

    async def test_destination_introduced_during_download_is_preserved(self):
        def occupy():
            self.destination().mkdir()
            (self.destination() / "user.txt").write_text("appeared during download")

        self.before_download = occupy
        result = await self.invoke(await self.selection())
        self.assertNotIn("ok", result)
        self.assertEqual((self.destination() / "user.txt").read_text(), "appeared during download")
        self.assertFalse(self.host.swaps)

    async def test_stage_backup_and_pending_introduced_during_download_are_preserved(self):
        selected = await self.selection()
        for name in (".stage-decky-31", ".old-decky-31"):
            path = self.host.PLUGINS / name

            def occupy():
                path.mkdir()
                (path / "user.txt").write_text("keep")

            self.before_download = occupy
            self.assertNotIn("ok", await self.invoke(selected))
            self.assertEqual((path / "user.txt").read_text(), "keep")
            shutil.rmtree(path)
        self.before_download = lambda: self.host.waiting.add("decky-31")
        self.assertEqual((await self.invoke(selected))["code"], "pending")
        self.assertFalse(self.destination().exists())

    async def test_oversize_original_is_rejected_before_staging(self):
        self.archives[decky.CDN + self.hashes["1.0.0"] + ".zip"] = b"x" * 5_000_001
        self.assertEqual((await self.invoke(await self.selection()))["code"], "bad_archive")
        self.assertFalse(self.destination().exists())

    async def test_failed_backend_removes_only_new_owned_installation(self):
        self.host.health = "private traceback text should not reach the catalog UI"
        result = await self.invoke(await self.selection())
        self.assertEqual(result["code"], "load_failed")
        self.assertNotIn("traceback", result["error"])
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.host.reloads, ["decky-31", "decky-31"])

    async def test_replacement_before_cleanup_worker_runs_is_not_deleted(self):
        self.host.health = "failed"
        to_thread = asyncio.to_thread
        replaced = []
        saved = self.host.PLUGINS / "saved-import"

        async def replace_before_thread(function, *args, **kwargs):
            if self.host.reloads and self.destination().exists() and not replaced:
                self.destination().rename(saved)
                self.destination().mkdir()
                (self.destination() / "user.txt").write_text("replacement before worker")
                replaced.append(True)
            return await to_thread(function, *args, **kwargs)

        with mock.patch.object(fy_catalog.asyncio, "to_thread", replace_before_thread):
            result = await self.invoke(await self.selection())
        self.assertEqual(result.get("code"), "load_failed", result)
        self.assertEqual(replaced, [True])
        self.assertTrue((self.destination() / "user.txt").exists(), "Rollback deleted replacement")
        self.assertEqual((self.destination() / "user.txt").read_text(), "replacement before worker")
        self.assertTrue((saved / "plugin.json").is_file())

    async def test_replacement_during_atomic_capture_is_kept_for_recovery(self):
        self.host.health = "failed"
        rename = Path.rename
        replaced = []
        saved = self.host.PLUGINS / "saved-import"
        captured_paths = []

        def replace_during_capture(path, target):
            target = Path(target)
            if path == self.destination() and target.parent.name.startswith(".framey-decky-rollback-"):
                rename(path, saved)
                path.mkdir()
                (path / "user.txt").write_text("captured foreign files")
                captured_paths.append(target)
                result = rename(path, target)
                path.mkdir()
                (path / "newer.txt").write_text("newer destination")
                replaced.append(True)
                return result
            return rename(path, target)

        with mock.patch.object(Path, "rename", replace_during_capture):
            result = await self.invoke(await self.selection())
        self.assertEqual(result.get("code"), "load_failed", result)
        self.assertEqual(replaced, [True], "Rollback did not atomically capture its deletion candidate")
        self.assertEqual((self.destination() / "newer.txt").read_text(), "newer destination")
        self.assertEqual((captured_paths[0] / "user.txt").read_text(), "captured foreign files")
        self.assertEqual(captured_paths[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(captured_paths[0].parent.name, result["error"])
        self.assertTrue((saved / "plugin.json").is_file())

    async def test_cleanup_failure_retains_owned_capture_and_preserves_new_destination(self):
        self.host.health = "failed"
        rmtree = shutil.rmtree
        retained = []

        def fail_owned_cleanup(path, *args, **kwargs):
            path = Path(path)
            if path == self.destination() or path.parent.name.startswith(".framey-decky-rollback-"):
                retained.append(path)
                if not self.destination().exists():
                    self.destination().mkdir()
                    (self.destination() / "user.txt").write_text("appeared during cleanup")
                raise OSError("controlled cleanup failure")
            return rmtree(path, *args, **kwargs)

        with mock.patch.object(fy_catalog.shutil, "rmtree", fail_owned_cleanup):
            result = await self.invoke(await self.selection())
        self.assertEqual(result.get("code"), "load_failed", result)
        self.assertTrue(retained)
        self.assertEqual((self.destination() / "user.txt").read_text(), "appeared during cleanup")
        self.assertTrue((retained[0] / "plugin.json").is_file())
        self.assertIn(retained[0].parent.name, result["error"])

    async def test_failure_does_not_delete_an_installation_replaced_during_reload(self):
        saved = self.host.PLUGINS / "saved-import"

        def replace(pid):
            if not saved.exists():
                self.destination().rename(saved)
                self.destination().mkdir()
                (self.destination() / "user.txt").write_text("external replacement")

        self.host.on_reload = replace
        self.host.health = "failed"
        self.assertEqual((await self.invoke(await self.selection()))["code"], "load_failed")
        self.assertEqual((self.destination() / "user.txt").read_text(), "external replacement")

    async def test_replacement_during_healthy_startup_is_not_reported_as_import_success(self):
        def replace(pid):
            if len(self.host.reloads) == 1:
                self.destination().rename(self.host.PLUGINS / "saved-import")
                self.destination().mkdir()
                (self.destination() / "user.txt").write_text("external replacement")

        self.host.on_reload = replace
        result = await self.invoke(await self.selection())
        self.assertEqual(result.get("code"), "load_failed", result)
        self.assertEqual((self.destination() / "user.txt").read_text(), "external replacement")

    async def test_replacement_after_swap_is_neither_loaded_nor_removed(self):
        def replace(pid):
            self.destination().rename(self.host.PLUGINS / "saved-import")
            self.destination().mkdir()
            (self.destination() / "user.txt").write_text("replacement after swap")

        self.host.on_swap = replace
        self.host.health = "failed"
        self.assertEqual((await self.invoke(await self.selection()))["code"], "load_failed")
        self.assertTrue((self.destination() / "user.txt").exists(), "External replacement was removed")
        self.assertEqual((self.destination() / "user.txt").read_text(), "replacement after swap")
        self.assertEqual(self.host.reloads, [])

    async def test_disabled_runtime_capability_refuses_direct_import(self):
        selected = await self.selection()
        self.runtime_enabled = False
        result = await self.invoke(selected)
        self.assertEqual(result["code"], "runtime_unavailable")
        self.assertEqual(self.downloads, [])

    async def test_legacy_install_still_refuses_decky(self):
        selected = await self.selection()
        result = await self.catalog.install(selected)
        self.assertEqual(result["code"], "decky_port_required")
        self.assertFalse(self.destination().exists())

    async def test_disable_during_download_never_stages_or_reloads(self):
        selected = await self.selection()
        self.before_download = lambda: setattr(self, "runtime_enabled", False)
        result = await self.invoke(selected)
        self.assertEqual(result["code"], "runtime_unavailable")
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.host.swaps, [])
        self.assertEqual(self.host.reloads, [])

    async def test_disable_after_swap_rolls_back_new_owned_install_without_reloading(self):
        selected = await self.selection()
        self.host.on_swap = lambda _pid: setattr(self, "runtime_enabled", False)
        result = await self.invoke(selected)
        self.assertIn("code", result)
        self.assertNotIn("ok", result)
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.host.reloads, [])


class NewOnlyTransaction(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fy-decky-new-transaction-")
        self.addCleanup(self.temporary.cleanup)
        self.plugins = Path(self.temporary.name)
        patcher = mock.patch.multiple(FRAMEY, PLUGINS=self.plugins, pending=lambda pid: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_only_transaction_preserves_paths_occupied_before_fill(self):
        for name in ("demo", ".stage-demo", ".old-demo"):
            with self.subTest(path=name):
                path = self.plugins / name
                path.mkdir()
                (path / "user.txt").write_text("keep")
                called = []
                with self.assertRaises(OSError):
                    FRAMEY.stage_swap("demo", lambda stage: called.append(stage), new_only=True)
                self.assertEqual(called, [])
                self.assertEqual((path / "user.txt").read_text(), "keep")
                shutil.rmtree(path)

    def test_new_only_transaction_preserves_destination_introduced_during_fill(self):
        destination = self.plugins / "demo"

        def fill(stage):
            stage.mkdir(exist_ok=True)
            (stage / "main.js").write_text("new")
            destination.mkdir()
            (destination / "user.txt").write_text("keep")

        with self.assertRaises(OSError):
            FRAMEY.stage_swap("demo", fill, new_only=True)
        self.assertEqual((destination / "user.txt").read_text(), "keep")
        self.assertFalse((self.plugins / ".stage-demo").exists())

    def test_new_only_transaction_rechecks_pending_state_after_fill(self):
        waiting = set()

        def fill(stage):
            stage.mkdir(exist_ok=True)
            (stage / "main.js").write_text("new")
            waiting.add("demo")

        with mock.patch.object(FRAMEY, "pending", lambda pid: pid in waiting):
            with self.assertRaises(OSError):
                FRAMEY.stage_swap("demo", fill, new_only=True)
        self.assertFalse((self.plugins / "demo").exists())


class ImportLoaderCapability(unittest.TestCase):
    def test_loader_keeps_optional_payload_out_of_the_mandatory_manifest(self):
        for name in RUNTIME_FILES:
            self.assertNotIn(name, FRAMEY.MANIFEST)
        self.assertEqual(FRAMEY.OWNER.get("catalog_import"), "fy_catalog")
        self.assertIn("catalog_import", FRAMEY.LOCKED)
        self.assertIn("catalog_import", FRAMEY.RELOAD)
        self.assertIn("fy_decky_runtime.py", FRAMEY.MANIFEST)



if __name__ == "__main__":
    unittest.main()
