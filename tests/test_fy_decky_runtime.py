import asyncio
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fy_decky_runtime as runtime

ENTRY = """from .helper import SOURCE\nABI=1\nasync def startup(host): host.calls.append("entry-start")\nasync def shutdown(host): host.calls.append("entry-stop")\ndef frontend_bundle(): return SOURCE\n"""


def payload(*, version="0.1.0", extra=None, entry=ENTRY, platform=None, api=1):
    files = {
        "__init__.py": b"",
        "runtime.py": entry.encode(),
        "helper.py": b'SOURCE="export const ABI=1;"\n',
    }
    files.update(extra or {})
    manifest = {
        "schema": 1,
        "version": version,
        "manager_api": 1,
        "core_api": api,
        "compat_profile": 1,
        "platform": platform or ["linux-x86_64", "linux-aarch64"],
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    raw_manifest = json.dumps(manifest).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("decky_runtime/manifest.json", raw_manifest)
        for k, v in files.items():
            z.writestr("decky_runtime/" + k, v)
    archive = buf.getvalue()
    release = {
        "schema": 1,
        "version": version,
        "archive": {
            "url": f"https://github.com/chaosfox26/framey/releases/download/v{version}/decky-runtime-{version}.zip",
            "sha256": hashlib.sha256(archive).hexdigest(),
            "size": len(archive),
        },
        "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
    }
    return archive, release


class Host:
    def __init__(self, root):
        self.HOME = root / "home"
        self.HOME.mkdir()
        self.ROOT = root / "loader"
        self.ROOT.mkdir()
        self.CONF = self.HOME / ".config/framey/settings.json"
        self.PLATFORM = "linux-x86_64"
        self.API_GENERATION = 1
        self.calls = []
        self.downloads = []
        self.archive, self.release = payload()
        self.before_fetch = None

    def fetch(self, url, *args, **kwargs):
        self.downloads.append(url)
        if self.before_fetch:
            self.before_fetch(url)
        return json.dumps(self.release).encode() if url == runtime.METADATA_URL else self.archive

    async def decky_runtime_stop(self):
        self.calls.append("core-stop")

    async def decky_runtime_start(self):
        self.calls.append("core-start")


class Manager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = Host(Path(self.temp.name))

    async def asyncTearDown(self):
        await runtime.shutdown(self.host)
        self.temp.cleanup()

    async def test_absent_status_allowed_are_side_effect_free(self):
        s = runtime.status(self.host)
        self.assertFalse(s["enabled"])
        self.assertFalse(s["installed"])
        self.assertEqual(s["phase"], "absent")
        self.assertTrue(runtime.allowed(self.host, {"id": "native"}))
        self.assertFalse(runtime.allowed(self.host, {"decky_compat": {"profile": 1}}))
        self.assertFalse(self.host.CONF.parent.exists())
        self.assertEqual(self.host.downloads, [])
        self.assertEqual(self.host.calls, [])

    async def test_verified_install_relative_import_frontend_and_descriptor(self):
        before = list(sys.path)
        s = await runtime.set_enabled(self.host, True)
        self.assertTrue(s["enabled"], s)
        self.assertEqual(sys.path, before)
        h = runtime.acquire(self.host)
        self.assertTrue(runtime.current(self.host, h))
        self.assertEqual(
            runtime.frontend(self.host),
            {
                "abi": 1,
                "generation": s["generation"],
                "version": "0.1.0",
                "source": "export const ABI=1;",
            },
        )
        d = runtime.worker_descriptor(self.host)
        self.assertEqual(d["root"], str(h.root))
        self.assertEqual(d["state_path"], str(self.host.CONF.parent / "decky-runtime.json"))
        self.assertEqual(runtime.verify_revision(h.root, d["manifest_sha256"])["version"], "0.1.0")
        self.assertEqual(self.host.calls, ["entry-start", "core-start"])

    async def test_disable_remove_preserve_data_and_reenable_fresh_generation(self):
        keep = self.host.ROOT / "plugins/owned"
        keep.mkdir(parents=True)
        (keep / "main.js").write_text("retained")
        config = self.host.HOME / ".config/framey/plugins/owned"
        config.mkdir(parents=True)
        (config / "settings.json").write_text("retained")
        await runtime.set_enabled(self.host, True)
        h = runtime.acquire(self.host)
        await runtime.set_enabled(self.host, False)
        self.assertFalse(runtime.current(self.host, h))
        downloads = len(self.host.downloads)
        await runtime.set_enabled(self.host, False)
        await runtime.set_enabled(self.host, True)
        new = runtime.acquire(self.host)
        self.assertNotEqual(new.generation, h.generation)
        self.assertIsNot(new.entry, h.entry)
        self.assertEqual(len(self.host.downloads), downloads)
        s = await runtime.remove(self.host)
        self.assertFalse(s["installed"])
        self.assertFalse(s["enabled"])
        self.assertEqual((keep / "main.js").read_text(), "retained")
        self.assertEqual((config / "settings.json").read_text(), "retained")
        await runtime.remove(self.host)

    async def test_archive_hash_failure_stays_off_before_entry_exec(self):
        self.host.release["archive"]["sha256"] = "0" * 64
        s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["phase"], "error")
        self.assertEqual(self.host.calls, [])
        self.assertFalse(runtime.status(self.host)["installed"])

    async def test_untrusted_url_and_abi_are_denied(self):
        self.host.release["archive"]["url"] = "https://evil.invalid/runtime.zip"
        s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertEqual(len(self.host.downloads), 1)
        self.host.archive, self.host.release = payload(api=2)
        s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertNotIn("entry-start", self.host.calls)

    async def test_unknown_zip_members_and_symlinks_rejected(self):
        for link in [False, True]:
            raw, release = payload()
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as z:
                for info in source.infolist():
                    z.writestr(info, source.read(info))
                if link:
                    info = zipfile.ZipInfo("decky_runtime/link")
                    info.external_attr = 0o120777 << 16
                    z.writestr(info, "/tmp/forbidden")
                else:
                    z.writestr("decky_runtime/extra.py", 'raise RuntimeError("must not execute")')
            self.host.archive = out.getvalue()
            release["archive"].update(
                sha256=hashlib.sha256(self.host.archive).hexdigest(), size=len(self.host.archive)
            )
            self.host.release = release
            s = await runtime.set_enabled(self.host, True)
            self.assertFalse(s["enabled"], s)

    async def test_corrupt_state_denies_decky_only_and_keeps_bytes(self):
        self.host.CONF.parent.mkdir(parents=True)
        p = self.host.CONF.parent / "decky-runtime.json"
        p.write_bytes(b"{BAD")
        s = runtime.status(self.host)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["phase"], "error")
        self.assertTrue(runtime.allowed(self.host, {"id": "native"}))
        s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertEqual(p.read_bytes(), b"{BAD")
        self.assertEqual(self.host.downloads, [])

    async def test_concurrent_enable_downloads_only_once(self):
        results = await asyncio.gather(
            runtime.set_enabled(self.host, True), runtime.set_enabled(self.host, True)
        )
        self.assertTrue(all(r["enabled"] for r in results))
        self.assertEqual(self.host.downloads.count(runtime.METADATA_URL), 1)
        self.assertEqual(self.host.calls.count("entry-start"), 1)

    async def test_disable_during_download_fences_late_activation(self):
        started = threading.Event()
        release = threading.Event()

        def block(url):
            if url != runtime.METADATA_URL:
                started.set()
                release.wait(2)

        self.host.before_fetch = block
        enable = asyncio.create_task(runtime.set_enabled(self.host, True))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        disable = asyncio.create_task(runtime.set_enabled(self.host, False))
        await asyncio.sleep(0)
        self.assertFalse(runtime.status(self.host)["enabled"])
        release.set()
        await asyncio.gather(enable, disable)
        self.assertFalse(runtime.status(self.host)["enabled"])
        self.assertNotIn("entry-start", self.host.calls)

    async def test_shutdown_preserves_intent_and_resume_does_not_download(self):
        await runtime.set_enabled(self.host, True)
        h = runtime.acquire(self.host)
        await runtime.shutdown(self.host)
        self.assertTrue(runtime.status(self.host)["requested_enabled"])
        self.assertFalse(runtime.current(self.host, h))
        n = len(self.host.downloads)
        s = await runtime.resume(self.host)
        self.assertTrue(s["enabled"], s)
        self.assertEqual(len(self.host.downloads), n)

    async def test_modified_revision_denied_before_frontend_delivery(self):
        await runtime.set_enabled(self.host, True)
        h = runtime.acquire(self.host)
        p = h.root / "helper.py"
        p.chmod(0o600)
        p.write_text('SOURCE="MUTATED"')
        with self.assertRaises(runtime.RuntimeError):
            runtime.frontend(self.host)

    async def test_teardown_failure_is_visible_and_remove_does_not_claim_success(self):
        async def bad_stop():
            raise ValueError("owned teardown failure")

        await runtime.set_enabled(self.host, True)
        self.host.decky_runtime_stop = bad_stop
        s = await runtime.remove(self.host)
        self.assertFalse(s["enabled"])
        self.assertTrue(s["installed"])
        self.assertEqual(s["phase"], "error")
        self.assertIn("owned teardown failure", s["error"]["message"])

    async def test_rpc_closed_boolean_and_native_scope(self):
        for arg in [{"enabled": 1}, {"enabled": True, "url": "bad"}, {}]:
            r = await runtime.METHODS["decky_runtime_enable"](self.host, arg)
            self.assertEqual(r["code"], "bad_request")
        self.assertEqual(self.host.downloads, [])


class AdmissionAndFailure(unittest.IsolatedAsyncioTestCase):
    setUp = Manager.setUp
    asyncTearDown = Manager.asyncTearDown

    async def test_legacy_marker_denied_even_when_runtime_enabled(self):
        await runtime.set_enabled(self.host, True)
        self.assertFalse(runtime.allowed(self.host, {"decky_compat": {"profile": 1}}))
        self.assertTrue(
            runtime.allowed(
                self.host, {"decky_compat": {"profile": 1, "runtime_abi": 1, "wrapper_version": 2}}
            )
        )
        self.assertTrue(runtime.allowed(self.host, {"id": "native"}))

    async def test_symlinked_state_parent_does_not_read_or_write_outside(self):
        elsewhere = Path(self.temp.name) / "other"
        elsewhere.mkdir()
        (self.host.HOME / ".config").symlink_to(elsewhere, target_is_directory=True)
        s = runtime.status(self.host)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["phase"], "error")
        s = await runtime.set_enabled(self.host, True)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(self.host.downloads, [])

    async def test_entry_start_failure_remains_off_and_drains(self):
        self.host.archive, self.host.release = payload(
            entry=ENTRY.replace(
                'host.calls.append("entry-start")',
                'host.calls.append("entry-start"); raise ValueError("OWNED activation failure")',
            )
        )
        s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertFalse(s["requested_enabled"])
        self.assertEqual(s["phase"], "error")
        self.assertIn("entry-stop", self.host.calls)
        self.assertNotIn("core-start", self.host.calls)

    async def test_missing_installed_bytes_on_resume_never_downloads(self):
        await runtime.set_enabled(self.host, True)
        await runtime.shutdown(self.host)
        h = runtime._get(self.host)
        h.erase(h.revision(h.data["installed"]))
        n = len(self.host.downloads)
        s = await runtime.resume(self.host)
        self.assertFalse(s["enabled"])
        self.assertEqual(len(self.host.downloads), n)
        self.assertEqual(s["phase"], "error")

    async def test_bad_manifest_hash_fails_before_load(self):
        self.host.release["manifest_sha256"] = "0" * 64
        s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertEqual(self.host.calls, [])


class RoutesAndPersistence(unittest.IsolatedAsyncioTestCase):
    setUp = Manager.setUp
    asyncTearDown = Manager.asyncTearDown

    async def test_state_write_failure_still_drains_active_owner(self):
        from unittest.mock import patch

        await runtime.set_enabled(self.host, True)
        with patch.object(
            runtime._Manager, "save", side_effect=OSError("OWNED state write failed")
        ):
            s = await runtime.set_enabled(self.host, False)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["phase"], "error")
        self.assertIn("entry-stop", self.host.calls)
        self.assertIn("core-stop", self.host.calls)

    async def test_frontend_route_closed_public_source_and_code_forwarding(self):
        extra = """\nasync def code_dispatch(host, operation, arg):\n host.calls.append((operation,arg))\n return {"operation":operation,"actual":arg}\n"""
        self.host.archive, self.host.release = payload(entry=ENTRY + extra)
        await runtime.set_enabled(self.host, True)
        result = await runtime.METHODS["decky_runtime_frontend"](
            self.host, {"pid": "owned", "id": "owned"}
        )
        self.assertEqual(result["abi"], 1)
        self.assertEqual(result["source"], "export const ABI=1;")
        bad = await runtime.METHODS["decky_runtime_frontend"](self.host, {"source": "untrusted"})
        self.assertEqual(bad["code"], "bad_request")
        for op in ["issue", "touch", "revoke"]:
            result = await runtime.METHODS["decky_code_" + op](self.host, {"pid": "owned"})
            self.assertEqual(result, {"operation": op, "actual": {"pid": "owned"}})
        await runtime.set_enabled(self.host, False)
        self.assertEqual(
            await runtime.METHODS["decky_code_revoke"](self.host, {"pid": "owned"}),
            {"revoked": True},
        )
        self.assertEqual(
            (await runtime.METHODS["decky_code_issue"](self.host, {"pid": "owned"}))["code"],
            "runtime_disabled",
        )

    async def test_code_reply_after_disable_is_not_published(self):
        extra = """\nasync def code_dispatch(host, operation, arg):\n host.code_started.set()\n await host.code_finish.wait()\n return {"token":"OWNED late token"}\n"""
        self.host.archive, self.host.release = payload(entry=ENTRY + extra)
        await runtime.set_enabled(self.host, True)
        self.host.code_started = asyncio.Event()
        self.host.code_finish = asyncio.Event()
        request = asyncio.create_task(
            runtime.METHODS["decky_code_issue"](self.host, {"pid": "owned"})
        )
        await self.host.code_started.wait()
        await runtime.set_enabled(self.host, False)
        self.host.code_finish.set()
        result = await request
        self.assertEqual(result["code"], "stale_generation")
        self.assertNotIn("token", result)


class Cancellation(unittest.IsolatedAsyncioTestCase):
    setUp = Manager.setUp
    asyncTearDown = Manager.asyncTearDown

    async def test_cancelled_install_drains_fetch_and_stays_off(self):
        started = threading.Event()
        finish = threading.Event()
        settled = threading.Event()

        def block(url):
            if url != runtime.METADATA_URL:
                started.set()
                finish.wait(2)
                settled.set()

        self.host.before_fetch = block
        enable = asyncio.create_task(runtime.set_enabled(self.host, True))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        enable.cancel()
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await enable
        self.assertTrue(settled.is_set())
        self.assertFalse(runtime.status(self.host)["enabled"])
        self.assertFalse(runtime.status(self.host)["requested_enabled"])
        self.assertNotIn("entry-start", self.host.calls)

    async def test_cancelled_remove_finishes_owned_teardown_before_returning(self):
        replacement = """async def shutdown(host):\n host.stop_started.set()\n await host.stop_finish.wait()\n host.calls.append("entry-stop")"""
        self.host.archive, self.host.release = payload(
            entry=ENTRY.replace(
                'async def shutdown(host): host.calls.append("entry-stop")', replacement
            )
        )
        self.host.stop_started = asyncio.Event()
        self.host.stop_finish = asyncio.Event()
        await runtime.set_enabled(self.host, True)
        remove = asyncio.create_task(runtime.remove(self.host))
        await self.host.stop_started.wait()
        remove.cancel()
        await asyncio.sleep(0)
        self.host.stop_finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await remove
        self.assertIn("entry-stop", self.host.calls)
        self.assertFalse(runtime.status(self.host)["enabled"])
        self.assertFalse(runtime.status(self.host)["installed"])
        self.assertIsNone(runtime._get(self.host).handle)


class Descriptor(unittest.IsolatedAsyncioTestCase):
    setUp = Manager.setUp
    asyncTearDown = Manager.asyncTearDown

    async def test_descriptor_current_state_and_fixed_revision_binding(self):
        await runtime.set_enabled(self.host, True)
        d = runtime.worker_descriptor(self.host)
        self.assertEqual(
            runtime.validate_worker_descriptor(d, home=self.host.HOME)["version"], "0.1.0"
        )
        for field, value in [
            ("generation", True),
            ("root", str(self.host.ROOT)),
            ("state_path", str(self.host.CONF)),
            ("manifest_sha256", "0" * 64),
            ("version", "9.9.9"),
        ]:
            with self.subTest(field=field), self.assertRaises(runtime.RuntimeError):
                runtime.validate_worker_descriptor({**d, field: value}, home=self.host.HOME)
        await runtime.set_enabled(self.host, False)
        with self.assertRaises(runtime.RuntimeError):
            runtime.validate_worker_descriptor(d, home=self.host.HOME)

    async def test_normal_invalidation_never_imports_when_off(self):
        runtime.invalidate(self.host, "owned")
        self.assertEqual(self.host.calls, [])
        self.assertFalse(self.host.CONF.parent.exists())
        self.host.archive, self.host.release = payload(
            entry=ENTRY + '\ndef invalidate(host,pid=None): host.calls.append(("invalidate",pid))\n'
        )
        await runtime.set_enabled(self.host, True)
        runtime.invalidate(self.host, "owned")
        self.assertIn(("invalidate", "owned"), self.host.calls)

    async def test_enable_state_write_failure_is_reported_and_never_activates(self):
        from unittest.mock import patch

        with patch.object(
            runtime._Manager, "save", side_effect=OSError("OWNED enable state failure")
        ):
            s = await runtime.set_enabled(self.host, True)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["phase"], "error")
        self.assertIn("OWNED enable state failure", s["error"]["message"])
        self.assertEqual(self.host.downloads, [])


class RetiredCleanup(unittest.IsolatedAsyncioTestCase):
    setUp = Manager.setUp
    asyncTearDown = Manager.asyncTearDown

    async def test_revoke_reaches_retained_owner_after_failed_shutdown(self):
        entry = (
            ENTRY.replace(
                'async def shutdown(host): host.calls.append("entry-stop")',
                'async def shutdown(host): raise ValueError("OWNED shutdown incomplete")',
            )
            + """\nasync def code_dispatch(host, operation, arg):\n host.calls.append((operation, arg))\n return {"actual_cleanup":operation}\n"""
        )
        self.host.archive, self.host.release = payload(entry=entry)
        await runtime.set_enabled(self.host, True)
        state = await runtime.set_enabled(self.host, False)
        self.assertEqual(state["phase"], "error")
        actual = await runtime.METHODS["decky_code_revoke"](self.host, {"pid": "owned"})
        self.assertEqual(actual, {"actual_cleanup": "revoke"})
        self.assertIn(("revoke", {"pid": "owned"}), self.host.calls)
