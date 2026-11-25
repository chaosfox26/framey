"""Managed code issuance tests; original code is data, never executed."""

import asyncio
import gc
import hashlib
import importlib
import json
import sys
import tempfile
import threading
import unittest
import weakref
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    service = importlib.import_module("decky_runtime.code")
except ModuleNotFoundError:
    service = None


def sha(value):
    return hashlib.sha256(value).hexdigest()


class Host:
    def __init__(self, root):
        self.PLUGINS = root
        self.generations = {}
        self.disabled = set()

    def manifest(self, path):
        try:
            return json.loads((path / "plugin.json").read_text())
        except (OSError, ValueError):
            return None

    def caller_identity(self, pid):
        root = self.PLUGINS / pid
        if pid in self.disabled or not root.is_dir():
            return None
        try:
            stamps = tuple(
                (p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_size)
                for p in (root, root / "plugin.json", root / "main.js")
            )
        except OSError:
            return None
        return self.generations.get(pid, 0), stamps


async def until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("bounded operation did not settle")


class CodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(service, "native managed code issuer is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host = Host(Path(self.temp.name))
        self.addAsyncCleanup(service.shutdown, self.host)
        self.make("alpha")
        self.make("beta")

    def make(self, pid, files=None, format="esm"):
        root = self.host.PLUGINS / pid
        root.mkdir(exist_ok=True)
        (root / "main.js").write_text("// Generated wrapper identity fixture, never executed.\n")
        manifest = {
            "id": pid,
            "name": "Managed fixture",
            "version": "1.0.0",
            "decky_compat": {"profile": 1, "experimental": True, "sha256": "a" * 64},
        }
        (root / "plugin.json").write_text(json.dumps(manifest))
        originals = {
            "dist/index.js": b'import {n} from "./chunk.js";export const result=n+1;\n',
            "dist/chunk.js": b"export const n=41;\n",
            "package.json": json.dumps(
                {"type": "module" if format == "esm" else "commonjs", "version": "1.0.0"}
            ).encode(),
            "LICENSE files/Read me é.txt": b"Legal asset names are not code.\n",
            "main.py": b'raise AssertionError("original Python must never execute")\n',
        }
        if files is not None:
            originals.update(files)
        for path, data in originals.items():
            output = root / "vendor/decky" / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
        provenance = {
            "name": "Managed fixture",
            "version": "1.0.0",
            "sha256": "a" * 64,
            "frontend_format": format,
            "eligible": True,
            "state": "experimental",
            "original_files": {p: sha(v) for p, v in originals.items()},
        }
        (root / "decky-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False))
        return root, originals

    def provenance(self, pid="alpha"):
        return json.loads((self.host.PLUGINS / pid / "decky-provenance.json").read_text())

    def write_provenance(self, value, pid="alpha"):
        (self.host.PLUGINS / pid / "decky-provenance.json").write_text(
            json.dumps(value, ensure_ascii=False)
        )

    async def issue(self, pid="alpha", **extra):
        return await service.decky_code_issue(self.host, {"pid": pid, **extra})

    async def touch(self, lease, pid="alpha"):
        return await service.decky_code_touch(self.host, {"pid": pid, "token": lease["token"]})

    async def revoke(self, lease, pid="alpha"):
        return await service.decky_code_revoke(self.host, {"pid": pid, "token": lease["token"]})

    async def get(self, url):
        parsed = urlsplit(url)
        reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
        try:
            writer.write(
                f"GET {parsed.path} HTTP/1.1\r\nHost: 127.0.0.1:{parsed.port}\r\nOrigin: https://steamloopback.host\r\n\r\n".encode()
            )
            await writer.drain()
            response = await reader.read()
            head, body = response.split(b"\r\n\r\n", 1)
            return int(head.split()[1]), body, head
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_verified_managed_graph_serves_only_original_immutable_js(self):
        lease = await self.issue(id="alpha")
        self.assertEqual(set(lease), {"root", "entry", "token", "ttl"})
        self.assertEqual(lease["ttl"], 600)
        self.assertRegex(lease["token"], r"^[0-9a-f]{64}$")
        self.assertEqual(lease["entry"], lease["root"] + "dist/index.js")
        code, body, head = await self.get(lease["entry"])
        self.assertEqual(code, 200)
        self.assertEqual(
            body, (self.host.PLUGINS / "alpha/vendor/decky/dist/index.js").read_bytes()
        )
        self.assertIn(b"Access-Control-Allow-Origin: https://steamloopback.host", head)
        self.assertEqual((await self.get(lease["root"] + "main.py"))[0], 404)
        self.assertEqual((await self.get(lease["root"] + "package.json"))[0], 404)
        self.assertEqual(
            (await self.get(lease["root"] + "LICENSE%20files/Read%20me%20%C3%A9.txt"))[0], 404
        )

    async def test_each_issue_has_fresh_token_and_old_cleanup_cannot_revoke_new(self):
        old = await self.issue()
        new = await self.issue()
        self.assertNotEqual(old["root"], new["root"])
        self.assertEqual((await self.get(old["entry"]))[0], 404)
        self.assertEqual(await self.revoke(old), {"revoked": True})
        self.assertEqual((await self.get(new["entry"]))[0], 200)
        self.assertEqual(await self.touch(new), {"renewed": True, "ttl": 600})

    async def test_wrong_owner_token_never_renews_or_revokes_another_plugin(self):
        lease = await self.issue()
        self.assertEqual((await self.touch(lease, "beta"))["code"], "not_allowed")
        self.assertEqual((await self.revoke(lease, "beta"))["code"], "not_allowed")
        self.assertEqual((await self.get(lease["entry"]))[0], 200)

    async def test_strict_payload_identity_and_forbidden_inputs_fail_before_io(self):
        for payload in [
            None,
            [],
            {"pid": "alpha", "id": "beta"},
            {"pid": "alpha", "ttl": 600},
            {"pid": "alpha", "path": "dist/index.js"},
            {"pid": "alpha", "origin": "https://other.invalid"},
            {"pid": "alpha", "js": "alert(1)"},
            {"pid": "../alpha"},
            {"pid": True},
        ]:
            with self.subTest(payload=payload):
                self.assertIn(
                    (await service.decky_code_issue(self.host, payload))["code"],
                    ("invalid_arguments", "not_allowed"),
                )
        for payload in [
            {"pid": "alpha", "token": "x"},
            {"pid": "alpha", "token": "a" * 64, "ttl": 600},
            {"pid": "alpha"},
            {"pid": "alpha", "id": "beta", "token": "a" * 64},
        ]:
            self.assertEqual(
                (await service.decky_code_touch(self.host, payload))["code"], "invalid_arguments"
            )
        self.assertNotIn(self.host, service._STATES)

    async def test_unknown_disabled_missing_callback_fail_closed_without_listener(self):
        self.host.disabled.add("alpha")
        self.assertEqual((await self.issue())["code"], "not_allowed")
        self.assertEqual((await self.issue("missing"))["code"], "not_allowed")
        self.host.caller_identity = None
        self.assertEqual((await self.issue("beta"))["code"], "not_allowed")
        self.assertNotIn(self.host, service._STATES)

    async def test_forged_provenance_name_version_sha_and_profile_are_rejected(self):
        original = self.provenance()
        for field, value in [
            ("name", "Other"),
            ("version", "2.0.0"),
            ("sha256", "b" * 64),
            ("eligible", False),
            ("state", "supported"),
        ]:
            forged = {**original, field: value}
            self.write_provenance(forged)
            self.assertEqual((await self.issue())["code"], "invalid_provenance")
        self.write_provenance(original)
        path = self.host.PLUGINS / "alpha/plugin.json"
        manifest = json.loads(path.read_text())
        for field, value in [
            ("profile", True),
            ("profile", 2),
            ("experimental", False),
            ("sha256", "x"),
        ]:
            changed = {**manifest, "decky_compat": {**manifest["decky_compat"], field: value}}
            path.write_text(json.dumps(changed))
            self.assertEqual((await self.issue())["code"], "invalid_provenance")

    async def test_iife_and_forged_esm_package_type_are_not_issued(self):
        self.make("iife", format="iife")
        self.assertEqual((await self.issue("iife"))["code"], "unsupported_format")
        value = self.provenance("iife")
        value["frontend_format"] = "esm"
        self.write_provenance(value, "iife")
        self.assertEqual((await self.issue("iife"))["code"], "unsupported_format")

    async def test_bad_original_hashmap_and_noncanonical_selected_js_fail_closed(self):
        original = self.provenance()
        for path, digest in [
            ("../escape.js", "a" * 64),
            ("/absolute.js", "a" * 64),
            ("dist/a\\b.js", "a" * 64),
            ("dist/a//b.js", "a" * 64),
            ("dist/has space.js", "a" * 64),
            ("dist/é.js", "a" * 64),
            ("dist/a.js", "INVALID"),
        ]:
            changed = {**original, "original_files": {**original["original_files"], path: digest}}
            self.write_provenance(changed)
            self.assertEqual((await self.issue())["code"], "invalid_provenance")
        self.write_provenance(
            {**original, "original_files": {str(i): "a" * 64 for i in range(501)}}
        )
        self.assertEqual((await self.issue())["code"], "code_limit")

    async def test_absent_entry_and_oversized_metadata_or_graph_are_bounded(self):
        value = self.provenance()
        del value["original_files"]["dist/index.js"]
        self.write_provenance(value)
        self.assertEqual((await self.issue())["code"], "invalid_provenance")
        (self.host.PLUGINS / "alpha/decky-provenance.json").write_bytes(b" " * (512 * 1024 + 1))
        self.assertEqual((await self.issue())["code"], "code_limit")
        self.make("large", {"dist/index.js": b"x" * (2 * 1024 * 1024 + 1)})
        self.assertEqual((await self.issue("large"))["code"], "code_limit")
        self.make("many", {f"dist/chunk{i}.js": b"export{};" for i in range(64)})
        self.assertEqual((await self.issue("many"))["code"], "code_limit")

    async def test_symlinked_graph_parent_provenance_package_or_entry_is_rejected(self):
        for target in [
            "decky-provenance.json",
            "vendor/decky/package.json",
            "vendor/decky/dist/index.js",
            "vendor/decky/dist",
        ]:
            self.make("linked")
            path = self.host.PLUGINS / "linked" / target
            backup = path.with_name(path.name + ".saved")
            path.rename(backup)
            path.symlink_to(backup)
            self.assertIn(
                (await self.issue("linked"))["code"], ("invalid_provenance", "code_changed")
            )
            path.unlink()
            backup.rename(path)

    async def test_changed_source_revokes_immutable_old_bytes_on_touch(self):
        lease = await self.issue()
        (self.host.PLUGINS / "alpha/vendor/decky/dist/chunk.js").write_bytes(
            b"export const n=99;\n"
        )
        self.assertEqual((await self.touch(lease))["code"], "code_changed")
        self.assertEqual((await self.get(lease["entry"]))[0], 404)
        self.assertEqual(await self.revoke(lease), {"revoked": True})

    async def test_provenance_change_retires_old_token_and_new_issue_is_fresh(self):
        lease = await self.issue()
        value = self.provenance()
        value["runtime_verified"] = False
        self.write_provenance(value)
        self.assertEqual((await self.touch(lease))["code"], "code_changed")
        new = await self.issue()
        self.assertNotEqual(new["token"], lease["token"])

    async def test_expiration_never_renews_old_token_and_reopen_gets_fresh_token(self):
        lease = await self.issue()
        state = service._STATES[self.host]
        now = state.server.clock()
        state.server.clock = lambda: now + 601
        self.assertEqual((await self.touch(lease))["code"], "code_expired")
        self.assertEqual(await self.revoke(lease), {"revoked": True})
        new = await self.issue()
        self.assertNotEqual(new["token"], lease["token"])

    async def test_invalidation_is_synchronous_and_does_not_affect_another_pid(self):
        a = await self.issue()
        b = await self.issue("beta")
        service.invalidate(self.host, "alpha")
        self.assertEqual((await self.get(a["entry"]))[0], 404)
        self.assertEqual((await self.get(b["entry"]))[0], 200)
        self.assertEqual((await self.issue())["code"], "not_allowed")
        self.host.generations["alpha"] = 1
        self.assertNotIn("error", await self.issue())

    async def test_advanced_generation_is_not_blacklisted_by_global_or_pid_invalidation(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                self.host.generations["alpha"] = self.host.generations.get("alpha", 0) + 1
                self.host.generations["beta"] = self.host.generations.get("beta", 0) + 1
                old = await self.issue()
                beta = await self.issue("beta")
                self.host.generations["alpha"] = self.host.generations.get("alpha", 0) + 1
                if not scoped:
                    self.host.generations["beta"] = self.host.generations.get("beta", 0) + 1
                service.invalidate(self.host, "alpha" if scoped else None)
                self.assertEqual((await self.get(old["entry"]))[0], 404)
                fresh = await self.issue()
                self.assertNotIn("error", fresh)
                self.assertNotEqual(fresh["token"], old["token"])
                # Cleanup authenticated in the new epoch cannot remove its new token.
                self.assertEqual((await self.revoke(old))["code"], "not_allowed")
                self.assertEqual((await self.get(fresh["entry"]))[0], 200)
                self.assertEqual((await self.get(beta["entry"]))[0], 200 if scoped else 404)

    async def test_generation_then_invalidate_retires_active_and_queued_old_issues_only(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                self.host.generations["alpha"] = self.host.generations.get("alpha", 0) + 1
                old = await self.issue()
                started, release = threading.Event(), threading.Event()
                original = service._read_graph

                def blocked(*args, **kwargs):
                    started.set()
                    release.wait(2)
                    return original(*args, **kwargs)

                with mock.patch.object(service, "_read_graph", side_effect=blocked):
                    active = asyncio.create_task(self.issue())
                    await until(started.is_set)
                    queued = asyncio.create_task(self.issue())
                    await until(lambda: len(service._STATES[self.host].calls) == 2)
                    self.host.generations["alpha"] = self.host.generations.get("alpha", 0) + 1
                    service.invalidate(self.host, "alpha" if scoped else None)
                    await until(lambda: len(service._STATES[self.host].calls) == 1)
                    replacement = asyncio.create_task(self.issue())
                    await asyncio.sleep(0)
                    release.set()
                    first, second, fresh = await asyncio.gather(active, queued, replacement)
                self.assertEqual(first["code"], "stale_caller")
                self.assertEqual(second["code"], "stale_caller")
                self.assertNotIn("error", fresh)
                self.assertEqual((await self.get(fresh["entry"]))[0], 200)
                self.assertEqual((await self.revoke(old))["code"], "not_allowed")
                self.assertEqual((await self.get(fresh["entry"]))[0], 200)
                state = service._STATES[self.host]
                self.assertEqual(state.server.snapshot()["packages"], 1)
                self.assertFalse(state.calls or state.retired)

    async def test_identity_change_detected_without_hook_retires_old_graph(self):
        lease = await self.issue()
        self.host.generations["alpha"] = 1
        self.assertEqual((await self.touch(lease))["code"], "stale_caller")
        self.assertEqual((await self.get(lease["entry"]))[0], 404)
        self.assertNotIn("error", await self.issue())

    async def test_late_io_after_invalidation_cannot_publish_and_is_drained(self):
        started, release = threading.Event(), threading.Event()
        original = service._read_graph

        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)

        with mock.patch.object(service, "_read_graph", side_effect=blocked):
            task = asyncio.create_task(self.issue())
            await until(started.is_set)
            service.invalidate(self.host, "alpha")
            release.set()
            self.assertEqual((await task)["code"], "stale_caller")
        state = service._STATES[self.host]
        self.assertEqual(state.server.snapshot()["packages"], 0)
        self.assertFalse(state.calls or state.retired)

    async def test_shutdown_retains_blocked_io_owner_despite_repeated_cancellation(self):
        started, release = threading.Event(), threading.Event()
        original = service._read_graph

        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)

        with mock.patch.object(service, "_read_graph", side_effect=blocked):
            issue = asyncio.create_task(self.issue())
            await until(started.is_set)
            closing = asyncio.create_task(service.shutdown(self.host))
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            await asyncio.gather(issue, return_exceptions=True)
        state = service._STATES[self.host]
        self.assertTrue(state.closed)
        self.assertFalse(state.calls or state.retired)
        self.assertEqual(state.server.snapshot()["packages"], 0)
        self.assertEqual((await self.issue())["code"], "service_closed")

    async def test_shutdown_awaits_real_descriptor_close_from_retired_read_thread(self):
        opened, release = threading.Event(), threading.Event()
        original = service._open_file
        tracked = []

        class HeldFile:
            def __init__(self, file):
                self.file = file
                tracked.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.file.close()

            def seek(self, *args):
                return self.file.seek(*args)

            def tell(self):
                return self.file.tell()

            def read(self, *args):
                opened.set()
                release.wait(2)
                return self.file.read(*args)

        def tracked_open(path):
            file = original(path)
            return HeldFile(file) if path.endswith("/dist/index.js") else file

        with mock.patch.object(service, "_open_file", side_effect=tracked_open):
            issue = asyncio.create_task(self.issue())
            await until(opened.is_set)
            self.assertTrue(any(not item.file.closed for item in tracked))
            closing = asyncio.create_task(service.shutdown(self.host))
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            await closing
            await asyncio.gather(issue, return_exceptions=True)
        self.assertTrue(all(item.file.closed for item in tracked))
        self.assertFalse(service._STATES[self.host].calls)

    async def test_pending_and_rate_limits_are_bounded_and_cleanup_revoke_is_available(self):
        lease = await self.issue()
        started, release = threading.Event(), threading.Event()
        original = service._read_graph

        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)

        with mock.patch.object(service, "_read_graph", side_effect=blocked):
            task = asyncio.create_task(self.issue())
            await until(started.is_set)
            queued = asyncio.create_task(self.issue())
            await until(lambda: len(service._STATES[self.host].calls) == 2)
            self.assertEqual((await self.issue())["code"], "busy")
            self.assertEqual(await self.revoke(lease), {"revoked": True})
            release.set()
            first = await task
            second = await queued
            self.assertNotEqual(first["token"], second["token"])
            self.assertEqual(await self.revoke(first), {"revoked": True})
            self.assertEqual((await self.get(second["entry"]))[0], 200)
        with mock.patch.object(service, "PER_PID_RATE", 1):
            self.assertEqual((await self.issue())["code"], "rate_limited")

    async def test_cancelled_queued_issue_does_not_revoke_active_touch_lease(self):
        lease = await self.issue()
        started, release = threading.Event(), threading.Event()
        original = service._read_graph

        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)

        with mock.patch.object(service, "_read_graph", side_effect=blocked):
            touch = asyncio.create_task(self.touch(lease))
            await until(started.is_set)
            queued = asyncio.create_task(self.issue())
            await until(lambda: len(service._STATES[self.host].calls) == 2)
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            status = (await self.get(lease["entry"]))[0]
            release.set()
            await touch
        self.assertEqual(status, 200)

    async def test_optional_old_report_fields_are_not_new_mandatory_requirements(self):
        value = self.provenance()
        del value["eligible"]
        del value["state"]
        self.write_provenance(value)
        self.assertNotIn("error", await self.issue())

    async def test_total_pending_and_global_resident_candidate_byte_limits(self):
        for pid in ("gamma", "delta", "epsilon"):
            self.make(pid)
        started, release = threading.Event(), threading.Event()
        original = service._read_graph

        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)

        with mock.patch.object(service, "_read_graph", side_effect=blocked):
            tasks = [
                asyncio.create_task(self.issue(pid)) for pid in ("alpha", "beta", "gamma", "delta")
            ]
            await until(
                lambda: (
                    service._STATES.get(self.host) is not None
                    and len(service._STATES[self.host].calls) == 4
                )
            )
            self.assertEqual((await self.issue("epsilon"))["code"], "busy")
            release.set()
            await asyncio.gather(*tasks)
        with mock.patch.object(service, "GLOBAL_BYTES", 100):
            self.assertEqual((await self.issue("epsilon"))["code"], "code_limit")
        state = service._STATES[self.host]
        self.assertEqual(state.reserved, 0)
        self.assertFalse(state.calls)

    async def test_hosts_have_independent_state_and_host_gc_closes_owned_listener(self):
        other = Host(self.host.PLUGINS)
        lease = await service.decky_code_issue(other, {"pid": "alpha"})
        self.assertNotIn("error", lease)
        state = service._STATES[other]
        reference = weakref.ref(other)
        del other
        gc.collect()
        self.assertIsNone(reference())
        await until(lambda: state.closing is not None and state.closing.done())
        self.assertTrue(state.closed)
        self.assertEqual(state.server.snapshot()["packages"], 0)
        self.assertIsNone(state.server.server)
        self.assertNotIn("error", await self.issue())


if __name__ == "__main__":
    unittest.main()
