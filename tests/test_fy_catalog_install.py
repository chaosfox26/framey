import asyncio
import copy
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_catalog


SHA = "b" * 40
OLD = "a" * 40
REPOSITORY = "https://github.com/example/framey-plugins"


def entry(**extra):
    return {"id": "sample", "name": "Sample", "version": "1.1.0", "description": "Example",
            "category": "examples", "tags": ["example"], "platform": ["linux-aarch64"],
            "source": {"repository": REPOSITORY, "commit": SHA, "path": "examples/sample"}, **extra}


def archive(manifest=None, path="examples/sample"):
    manifest = manifest or {"id": "sample", "name": "Sample", "version": "1.1.0", "platform": ["linux-aarch64"]}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("project/README.md", "Not part of the selected plugin")
        package.writestr("project/LICENSE", "Required source license notice")
        package.writestr("project/" + path + "/plugin.json", json.dumps(manifest))
        package.writestr("project/" + path + "/main.js", "Framey.register({render(){}});")
        package.writestr("project/" + path + "/.source", "https://example.org/not-authoritative")
    return out.getvalue()


class Host:
    def __init__(self, root):
        self.ROOT, self.HOME = root / "loader", root / "home"
        self.PLUGINS = self.ROOT / "plugins"
        self.CONF = self.HOME / ".config/framey/settings.json"
        self.PLUGINS.mkdir(parents=True)
        self.HOME.mkdir()
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.0.2", "linux-aarch64", 1
        self.state = {"prefs": {}, "modules": {"frozen": {}}}
        self.downloads, self.swaps, self.reloads = [], [], []
        self.payload = archive()
        self.health = ""
        self.on_archive = None

    def now(self):
        return 1_750_000_000

    def settings(self):
        return copy.deepcopy(self.state)

    def archive(self, owner, repo, commit):
        self.downloads.append((owner, repo, commit))
        if self.on_archive:
            self.on_archive()
        return self.payload

    def pending(self, _):
        return None

    def stage_swap(self, pid, fill, keep=False):
        stage, dest, old = (self.PLUGINS / n for n in (".stage-" + pid, pid, ".old-" + pid))
        fill(stage)
        if dest.exists():
            dest.rename(old)
        stage.rename(dest)
        self.swaps.append((pid, keep))

    async def reload(self, pid):
        self.reloads.append(pid)

    async def backend_check(self, _):
        return self.health

    def restore(self, dest):
        shutil.rmtree(dest)
        dest.with_name(".old-" + dest.name).rename(dest)


class CatalogInstall(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fy-catalog-install-")
        self.addCleanup(self.tmp.cleanup)
        self.host = Host(Path(self.tmp.name))
        self.selected = entry()

        async def fetcher(url, max_bytes):
            return json.dumps({"schema": 1, "name": "Examples", "plugins": [self.selected]}).encode()

        self.catalog = fy_catalog.Catalog(self.host, fetcher)

    async def install(self):
        row = (await self.catalog.list())["rows"][0]
        return await self.catalog.install({"id": row["id"], "identity": row["identity"], "confirm": True})

    def existing(self):
        dest = self.host.PLUGINS / "sample"
        dest.mkdir()
        (dest / "plugin.json").write_text(json.dumps({"id": "sample", "name": "Sample", "version": "1.0.0"}))
        (dest / "old.txt").write_text("previous working version")
        (dest / ".catalog.json").write_text(json.dumps({"v": 1, "id": "sample", "source": {"repository": REPOSITORY, "commit": OLD, "path": "examples/sample"}}))
        return dest

    async def test_selected_subdirectory_installs_with_authoritative_provenance(self):
        result = await self.install()
        self.assertEqual(result["ok"], "sample")
        self.assertFalse(result["updated"])
        dest = self.host.PLUGINS / "sample"
        self.assertTrue((dest / "main.js").is_file())
        self.assertFalse((dest / "README.md").exists())
        self.assertEqual((dest / "LICENSE").read_text(), "Required source license notice")
        self.assertEqual((dest / ".source").read_text().strip(), REPOSITORY + "/tree/" + SHA)
        self.assertEqual((dest / ".version").read_text().strip(), SHA)
        manifest = json.loads((dest / "plugin.json").read_text())
        self.assertEqual(manifest["source_path"], "examples/sample")
        receipt = json.loads((dest / ".catalog.json").read_text())
        self.assertEqual(receipt["source"], self.selected["source"])
        self.assertEqual(self.host.swaps, [("sample", True)])
        self.assertEqual(self.host.reloads, ["sample"])

    async def test_managed_update_keeps_previous_copy(self):
        self.existing()
        result = await self.install()
        self.assertTrue(result["updated"])
        self.assertEqual((self.host.PLUGINS / ".old-sample/old.txt").read_text(), "previous working version")

    async def test_failed_new_backend_removes_new_copy(self):
        self.host.health = "Backend failed to start"
        result = await self.install()
        self.assertEqual(result["code"], "load_failed")
        self.assertFalse((self.host.PLUGINS / "sample").exists())
        self.assertEqual(self.host.reloads, ["sample", "sample"])

    async def test_failed_updated_backend_restores_previous_copy(self):
        self.existing()
        self.host.health = "Backend failed to start"
        result = await self.install()
        self.assertEqual(result["code"], "load_failed")
        self.assertEqual((self.host.PLUGINS / "sample/old.txt").read_text(), "previous working version")
        self.assertFalse((self.host.PLUGINS / ".old-sample").exists())

    async def test_package_identity_mismatch_never_swaps(self):
        for changes in ({"id": "other"}, {"version": "9.0.0"}):
            manifest = {"id": "sample", "name": "Sample", "version": "1.1.0", **changes}
            self.host.payload = archive(manifest)
            self.assertEqual((await self.install())["code"], "identity_mismatch")
        self.assertFalse(self.host.swaps)

    async def test_actual_manifest_compatibility_and_privilege_are_rechecked(self):
        for changes, code in (({"platform": ["linux-x86_64"]}, "incompatible"), ({"privileged": None}, "protected")):
            manifest = {"id": "sample", "name": "Sample", "version": "1.1.0", **changes}
            self.host.payload = archive(manifest)
            self.assertEqual((await self.install())["code"], code)
        self.assertFalse(self.host.swaps)

    async def test_missing_selected_directory_never_installs_root_package(self):
        self.host.payload = archive(path="other")
        self.assertEqual((await self.install())["code"], "bad_package")
        self.assertFalse(self.host.swaps)

    async def test_frozen_state_is_rechecked_after_download(self):
        self.existing()
        self.host.on_archive = lambda: self.host.state["modules"]["frozen"].update(sample={"at": 1})
        result = await self.install()
        self.assertEqual(result["code"], "not_eligible")
        self.assertFalse(self.host.swaps)

    async def test_catalog_artifact_hash_is_checked_before_extraction(self):
        self.selected["artifact"] = {"sha256": "0" * 64, "size": len(self.host.payload)}
        self.assertEqual((await self.install())["code"], "digest_mismatch")
        self.assertFalse(self.host.swaps)

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs POSIX executable modes")
    async def test_executable_helpers_keep_only_safe_execute_permissions(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.host.payload)) as package, zipfile.ZipFile(payload, "w") as output:
            for info in package.infolist():
                output.writestr(info, package.read(info))
            helper = zipfile.ZipInfo("project/examples/sample/bin/helper")
            helper.create_system = 3
            helper.external_attr = 0o104777 << 16
            output.writestr(helper, "#!/bin/sh\nexit 0\n")
        self.host.payload = payload.getvalue()
        self.assertEqual((await self.install())["ok"], "sample")
        mode = (self.host.PLUGINS / "sample/bin/helper").stat().st_mode & 0o7777
        self.assertEqual(mode, 0o755)

    async def test_failed_refresh_remains_stale_after_restart(self):
        page = await self.catalog.list()

        async def offline(url, max_bytes):
            raise OSError("offline")

        self.catalog.fetcher = offline
        failed = await self.catalog.list({"refresh": True})
        self.assertTrue(failed["stale"])
        restarted = fy_catalog.Catalog(self.host, offline)
        restored = await restarted.list()
        self.assertTrue(restored["stale"])
        self.assertTrue(restored["fetch_error"])
        row = page["rows"][0]
        result = await restarted.install({"id": row["id"], "identity": row["identity"], "confirm": True})
        self.assertEqual(result["code"], "stale_catalog")
        self.assertFalse(self.host.downloads)

    async def test_cancellation_drains_the_swap_before_releasing_install_lock(self):
        original = self.host.stage_swap
        entered, release = threading.Event(), threading.Event()

        def pause(pid, fill, keep=False):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test did not release the transaction")
            return original(pid, fill, keep)

        self.host.stage_swap = pause
        task = asyncio.create_task(self.install())
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        self.assertFalse(task.done())
        self.assertTrue(self.catalog.install_lock.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.catalog.install_lock.locked())
        self.assertTrue((self.host.PLUGINS / "sample/main.js").is_file())
        self.assertEqual(self.host.reloads, ["sample"])


if __name__ == "__main__":
    unittest.main()
