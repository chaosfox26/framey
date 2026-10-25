import asyncio
import json
import shutil
import threading
import unittest
from pathlib import Path
from unittest import mock

from test_fy_updates import InstallBase, SHA_A, plugin_zip
import fy_updates


class CancellationTransactionTests(InstallBase):
    def delayed_swap(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.host.stage_swap

        def swap(*args, **kwargs):
            entered.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("test swap was not released")
                return original(*args, **kwargs)
            finally:
                finished.set()

        self.host.stage_swap = swap
        return entered, release, finished

    async def cancel_swap(self, operation, cancellations=1):
        entered, release, finished = self.delayed_swap()
        task = asyncio.create_task(operation)
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0.02)
        returned_before_swap_finished = task.done()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self.assertTrue(await asyncio.to_thread(finished.wait, 3))
        return returned_before_swap_finished

    async def test_cancellation_keeps_transaction_open_until_swap_thread_finishes(self):
        early = await self.cancel_swap(self.up.install_version("demo", "v1.3.0"))
        self.assertFalse(early, "a cancelled caller released an active filesystem transaction")
        if self.plugin_version() == "1.3.0":
            self.assertIn("demo", self.host.reloads)
        else:
            self.assertEqual(self.plugin_version(), "1.2.0")

    async def test_cancellation_preserves_older_copy_during_same_version_repair(self):
        old = self.host.PLUGINS / ".old-demo"
        old.mkdir()
        (old / "plugin.json").write_text(json.dumps({"id": "demo", "version": "1.1.0"}))
        (old / "main.js").write_text("previous working version")
        await self.cancel_swap(self.up.repair("demo"))
        self.assertTrue(old.is_dir(), "cancellation lost the retained rollback copy")
        self.assertEqual(json.loads((old / "plugin.json").read_text())["version"], "1.1.0")
        self.assertFalse((self.host.PLUGINS / ".hold-demo").exists())
        self.assertEqual((self.host.PLUGINS / "demo" / ".version").read_text(), SHA_A)

    async def test_repeated_cancellation_cannot_abandon_a_running_swap(self):
        early = await self.cancel_swap(self.up.install_version("demo", "v1.3.0"), cancellations=2)
        self.assertFalse(early, "a second cancellation abandoned an active filesystem transaction")

    async def test_cancellation_during_reload_leaves_running_and_disk_versions_aligned(self):
        entered, release = asyncio.Event(), asyncio.Event()
        healthy = ["1.2.0"]

        async def reload(pid=None):
            self.host.reloads.append(pid)
            version = self.plugin_version()
            entered.set()
            await release.wait()
            healthy[0] = version

        self.host.reload = reload
        task = asyncio.create_task(self.up.install_version("demo", "v1.3.0"))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.sleep(0.02)
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertEqual(healthy[0], self.plugin_version(), "cancellation left unverified files active")


class ResetFailureTests(InstallBase):
    async def test_failed_settings_delete_reports_failed_reset_and_restores_code(self):
        config = self.host.HOME / ".config/framey/plugins/demo"
        settings = config / "settings.json"
        settings.write_text('{"theme":"dark"}')
        current_script = self.host.PLUGINS / "demo/main.js"
        current_script.write_text("original local code")
        remove = shutil.rmtree

        def deny_settings_delete(path, *args, **kwargs):
            if Path(path) == config:
                return None
            return remove(path, *args, **kwargs)

        with mock.patch.object(fy_updates.shutil, "rmtree", side_effect=deny_settings_delete):
            result = await self.up.repair("demo", reset=True, confirm=True)
        self.assertEqual(result.get("code"), "reset_failed")
        self.assertEqual(result.get("stage"), "reload")
        self.assertNotIn("ok", result)
        self.assertEqual(settings.read_text(), '{"theme":"dark"}')
        self.assertEqual(current_script.read_text(), "original local code")


class TransactionHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_cancellation_propagates_without_spinning(self):
        async def cancelled_transaction():
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(fy_updates._finish_transaction(cancelled_transaction()), 1)


class PrereleaseRepairTests(InstallBase):
    def prerelease(self):
        version = "1.2.0-rc.1"
        directory = self.host.add("demo", version, sha=SHA_A)
        identity = {"kind": "release", "source": "o/r", "commit": SHA_A, "tag": "v" + version}
        (directory / ".identity.json").write_text(json.dumps(identity))
        self.host.archives[SHA_A] = plugin_zip("demo", version)
        self.host.prefs(channel="stable")
        return directory, identity

    async def test_stable_channel_repairs_already_installed_prerelease_identity(self):
        directory, identity = self.prerelease()
        (directory / "main.js").write_text("damaged local copy")
        result = await self.up.repair("demo")
        self.assertEqual(result, {"ok": "demo", "version": "1.2.0-rc.1"})
        self.assertEqual((directory / "main.js").read_text(), "// 1.2.0-rc.1")
        self.assertEqual((directory / ".version").read_text(), SHA_A)
        self.assertEqual(json.loads((directory / ".identity.json").read_text()), identity)
        self.assertEqual(self.host.latest_calls, [])

    async def test_repair_does_not_install_different_prerelease_on_stable(self):
        directory, identity = self.prerelease()
        self.host.archives[SHA_A] = plugin_zip("demo", "1.2.0-rc.2")
        original_script = (directory / "main.js").read_text()
        result = await self.up.repair("demo")
        self.assertEqual(result.get("code"), "prerelease")
        self.assertEqual(result.get("stage"), "verify")
        self.assertEqual(self.host.swaps, [])
        self.assertEqual(self.host.reloads, [])
        self.assertEqual(self.plugin_version(), "1.2.0-rc.1")
        self.assertEqual((directory / "main.js").read_text(), original_script)
        self.assertEqual(json.loads((directory / ".identity.json").read_text()), identity)


if __name__ == "__main__":
    unittest.main()
