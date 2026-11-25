import asyncio
import importlib
import importlib.util
import hashlib
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decky_runtime import http as m
class ImportContract(unittest.TestCase):
    def test_import_and_construction_do_not_start_a_listener(self):
        self.assertIsNotNone(
            importlib.util.find_spec("decky_runtime.http"),
            "The immutable code HTTP service must be available as a core module",
        )
        with mock.patch.object(asyncio, "start_server") as start:
            module = importlib.reload(m)
            server = module.ModuleServer("https://steam.framey.test")
            self.assertIsNone(server.server)
            self.assertEqual(server.snapshot()["packages"], 0)
            start.assert_not_called()


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.origin = "https://steam.framey.test:9443"
        self.server = m.ModuleServer(self.origin)
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.close()

    def issue(self, owner="one", files=None, **kw):
        files = files or {"dist/index.js": b"export const answer=42;"}
        hashes = {p: hashlib.sha256(value).hexdigest() for p, value in files.items()}
        return self.server.issue_bytes(owner, files, hashes, **kw)

    async def request(self, target, method="GET", origin=None, host=None, extra=""):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        raw = f"{method} {target} HTTP/1.1\r\nHost: {host or '127.0.0.1:' + str(self.server.port)}\r\nOrigin: {self.origin if origin is None else origin}\r\n{extra}\r\n"
        writer.write(raw.encode("ascii"))
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, _, body = response.partition(b"\r\n\r\n")
        lines = head.decode("latin1").split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
        return int(lines[0].split()[1]), headers, body

    async def test_exact_immutable_js_bytes_and_headers(self):
        path = self.issue()["prefix"] + "dist/index.js"
        status, headers, body = await self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"export const answer=42;")
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Access-Control-Allow-Origin"], self.origin)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Credentials", headers)
        status, headers, body = await self.request(path, "HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), 23)

    async def test_expired_and_revoked_resources_fail(self):
        a = self.issue(ttl=1)
        await asyncio.sleep(1.05)
        self.assertEqual((await self.request(a["prefix"] + "dist/index.js"))[0], 404)
        b = self.issue()
        self.assertFalse(self.server.revoke(b["token"], "different"))
        self.assertTrue(self.server.revoke(b["token"], "one"))
        self.assertEqual((await self.request(b["prefix"] + "dist/index.js"))[0], 404)

    async def test_cross_package_owner_isolation_and_fixed_prefix(self):
        a = self.issue("a", {"dist/a.js": b"export default 1;"})
        b = self.issue("b", {"dist/b.js": b"export default 2;"})
        self.assertEqual(len(a["token"]), 64)
        self.assertNotEqual(a["token"], b["token"])
        self.assertEqual((await self.request(a["prefix"] + "dist/b.js"))[0], 404)
        self.assertEqual((await self.request(b["prefix"] + "dist/b.js"))[2], b"export default 2;")
        self.assertEqual(self.server.revoke_owner("a"), 1)
        self.assertEqual((await self.request(b["prefix"] + "dist/b.js"))[0], 200)

    async def test_raw_traversal_percent_queries_and_absolute_targets_fail(self):
        a = self.issue()
        bad = [
            "dist/../index.js",
            "../dist/index.js",
            "dist//index.js",
            "dist/./index.js",
            "dist/%69ndex.js",
            "dist/%2e%2e/index.js",
            "dist/%252e/index.js",
            "dist/x%2findex.js",
            "dist/x%5cindex.js",
            "dist/index.js?x=1",
            "dist/index.js#x",
            "dist/INDEX.js",
            "dist/index.js/",
            "dist/x\\index.js",
        ]
        for suffix in bad:
            self.assertEqual(
                (await self.request(a["prefix"] + suffix))[0],
                404,
                "Noncanonical request target accepted",
            )
        self.assertEqual(
            (
                await self.request(
                    "http://127.0.0.1:" + str(self.server.port) + a["prefix"] + "dist/index.js"
                )
            )[0],
            404,
        )

    async def test_origin_host_and_methods_are_bounded(self):
        a = self.issue()
        target = a["prefix"] + "dist/index.js"
        self.assertEqual((await self.request(target, origin="https://other.invalid"))[0], 403)
        self.assertEqual((await self.request(target, host="attacker.invalid"))[0], 400)
        self.assertEqual((await self.request(target, "POST"))[0], 405)

    async def test_hash_and_bytesupply_reject_before_registration(self):
        for files, hashes, code in [
            ({"dist/index.js": b"export default 1;"}, {"dist/index.js": "0" * 64}, "hash"),
            ({"dist/index.js": Path("unopened")}, {"dist/index.js": "0" * 64}, "bytes"),
            ({"dist/index.js": bytearray(b"x")}, {"dist/index.js": "0" * 64}, "bytes"),
            ({"../escape.js": b"x"}, {"../escape.js": hashlib.sha256(b"x").hexdigest()}, "path"),
        ]:
            with self.assertRaises(m.ModuleError) as cm:
                self.server.issue_bytes("one", files, hashes)
            self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.server.snapshot()["packages"], 0)

    async def test_code_only_manifest_and_limits(self):
        for files, hashes, code in [
            (
                {"dist/data.json": b"{}"},
                {"dist/data.json": hashlib.sha256(b"{}").hexdigest()},
                "path",
            ),
            (
                {f"dist/x{i}.js": b"x" for i in range(65)},
                {f"dist/x{i}.js": hashlib.sha256(b"x").hexdigest() for i in range(65)},
                "bounds",
            ),
            (
                {"dist/x.js": b"x" * (2 * 1024 * 1024 + 1)},
                {"dist/x.js": hashlib.sha256(b"x" * (2 * 1024 * 1024 + 1)).hexdigest()},
                "bounds",
            ),
        ]:
            with self.assertRaises(m.ModuleError) as cm:
                self.server.issue_bytes("one", files, hashes)
            self.assertEqual(cm.exception.code, code)

    async def test_mutable_caller_maps_and_closed_server_cannot_replace_code(self):
        files = {"dist/index.js": b"export default 1;"}
        manifest = {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}
        a = self.server.issue_bytes("one", files, manifest)
        files["dist/index.js"] = b"replaced"
        manifest.clear()
        self.assertEqual(
            (await self.request(a["prefix"] + "dist/index.js"))[2], b"export default 1;"
        )
        await self.server.close()
        with self.assertRaises(m.ModuleError) as cm:
            self.issue("later")
        self.assertEqual(cm.exception.code, "closed")
        self.assertEqual(self.server.snapshot()["packages"], 0)

    async def test_safe_ingest_rejects_symlinks_and_serves_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "dist").mkdir()
            file = root / "dist/index.js"
            file.write_bytes(b"export default 1;")
            manifest = {"dist/index.js": hashlib.sha256(file.read_bytes()).hexdigest()}
            files = m.ingest_verified(root, manifest)
            file.write_bytes(b"changed after verified ingestion")
            a = self.server.issue_bytes("one", files, manifest)
            file.unlink()
            self.assertEqual(
                (await self.request(a["prefix"] + "dist/index.js"))[2], b"export default 1;"
            )
            outside = root / "outside.js"
            outside.write_bytes(b"export default 1;")
            file.symlink_to(outside)
            with self.assertRaises(m.ModuleError):
                m.ingest_verified(root, manifest)
            file.unlink()
            (root / "dist").rmdir()
            (root / "dist").symlink_to(root, target_is_directory=True)
            with self.assertRaises(m.ModuleError):
                m.ingest_verified(root, manifest)
            real = root / "real"
            (real / "dist").mkdir(parents=True)
            (real / "dist/index.js").write_bytes(b"export default 1;")
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(m.ModuleError):
                m.ingest_verified(alias, manifest)

    async def test_connection_count_and_slow_header_deadline(self):
        conns = [await asyncio.open_connection("127.0.0.1", self.server.port) for _ in range(8)]
        await asyncio.sleep(0.03)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        try:
            data = await asyncio.wait_for(reader.read(1), 0.05)
        except ConnectionResetError:
            data = b""
        self.assertEqual(data, b"")
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
        await asyncio.sleep(self.server.read_timeout + 0.1)
        for reader, writer in conns:
            self.assertEqual(await reader.read(), b"")
            writer.close()
            await writer.wait_closed()
        self.assertLessEqual(self.server.snapshot()["connections"], 8)

    async def test_renew_keeps_same_url_and_bytes_but_never_revives_expiry(self):
        await self.server.close()
        clock = {"now": 1000.0}
        self.server = m.ModuleServer(self.origin, clock=lambda: clock["now"])
        await self.server.start()
        resource = self.issue(ttl=2)
        target = resource["prefix"] + "dist/index.js"
        clock["now"] = 1001.0
        self.assertTrue(self.server.renew("one", resource["token"], ttl=600))
        clock["now"] = 1003.0
        self.assertEqual((await self.request(target))[2], b"export const answer=42;")
        self.assertEqual(self.server.snapshot()["packages"], 1)
        clock["now"] = 1601.0
        self.assertFalse(self.server.renew("one", resource["token"], ttl=600))
        self.assertEqual((await self.request(target))[0], 404)

    async def test_renew_rejects_wrong_owner_retired_token_and_closed_service(self):
        await self.server.close()
        clock = {"now": 1000.0}
        self.server = m.ModuleServer(self.origin, clock=lambda: clock["now"])
        await self.server.start()
        resource = self.issue(ttl=2)
        clock["now"] = 1001.0
        self.assertFalse(self.server.renew("other", resource["token"]))
        self.assertFalse(self.server.renew("one", "0" * 64))
        clock["now"] = 1002.0
        self.assertFalse(self.server.renew("one", resource["token"]))
        current = self.issue()
        replacement = self.issue()
        self.assertFalse(self.server.renew("one", current["token"]))
        self.assertTrue(self.server.renew("one", replacement["token"]))
        self.assertTrue(self.server.revoke(replacement["token"], "one"))
        self.assertFalse(self.server.renew("one", replacement["token"]))
        await self.server.close()
        self.assertFalse(self.server.renew("one", replacement["token"]))

    async def test_issuance_and_renewal_reject_invalid_ttl_without_replacing_snapshot(self):
        resource = self.issue()
        for ttl in (0, 601, True, None, float("nan"), float("inf"), 10**400):
            with self.subTest(ttl_type=type(ttl).__name__):
                with self.assertRaises(m.ModuleError) as error:
                    self.server.renew("one", resource["token"], ttl=ttl)
                self.assertEqual(error.exception.code, "ttl")
                with self.assertRaises(m.ModuleError) as error:
                    self.issue(ttl=ttl)
                self.assertEqual(error.exception.code, "ttl")
        self.assertEqual(
            (await self.request(resource["prefix"] + "dist/index.js"))[2],
            b"export const answer=42;",
        )

    async def test_global_resident_budget_is_atomic_and_owner_replacement_releases_it(self):
        value = b"x" * 2097152
        files = {f"dist/{index}.js": value for index in range(4)}
        self.issue("one", files)
        self.issue("two", files)
        with self.assertRaises(m.ModuleError) as error:
            self.issue("three", {"dist/index.js": b"x"})
        self.assertEqual(error.exception.code, "bounds")
        self.assertEqual(self.server.snapshot()["packages"], 2)
        self.assertEqual(self.server.snapshot()["residentBytes"], 16777216)
        self.issue("one", {"dist/index.js": b"x"})
        self.issue("three", {"dist/index.js": b"x"})
        self.assertEqual(self.server.snapshot()["residentBytes"], 8388610)

    async def test_close_waits_for_admitted_handler_cleanup(self):
        resource = self.issue()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_reply(*args, **kwargs):
            entered.set()
            await release.wait()

        _reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        try:
            with mock.patch.object(self.server, "_reply", new=blocked_reply):
                request = (
                    "GET " + resource["prefix"] + "dist/index.js HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.server.port}\r\n"
                    f"Origin: {self.origin}\r\n\r\n"
                )
                writer.write(request.encode("ascii"))
                await writer.drain()
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.wait_for(self.server.close(), 1)
                handlers = [
                    task
                    for task in asyncio.all_tasks()
                    if not task.done()
                    and getattr(task.get_coro(), "__qualname__", "") == "ModuleServer._serve"
                ]
                self.assertEqual(handlers, [], "Close returned before request cleanup")
                self.assertEqual(self.server.snapshot()["connections"], 0)
        finally:
            release.set()
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode):
        server = m.ModuleServer("https://steam.framey.test:9443")
        actual = asyncio.start_server
        entered = asyncio.Event()
        release = asyncio.Event()
        created = []
        calls = []
        tasks = []

        async def delayed(*args, **kwargs):
            calls.append(None)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if mode != "resistant_close":
                    raise
                await release.wait()
            candidate = await actual(*args, **kwargs)
            created.append(candidate)
            return candidate

        try:
            with mock.patch.object(m.asyncio, "start_server", new=delayed):
                tasks = [asyncio.create_task(server.start())]
                await entered.wait()
                if mode == "concurrent":
                    tasks.append(asyncio.create_task(server.start()))
                    await asyncio.sleep(0)
                    release.set()
                    await asyncio.gather(*tasks)
                    self.assertEqual(
                        len(calls), 1, "Concurrent start must share exactly one listener creation"
                    )
                    await server.close()
                    self.assertFalse(
                        any(candidate.is_serving() for candidate in created),
                        "Close left a listener serving",
                    )
                elif mode in ("close", "resistant_close"):
                    closing = asyncio.create_task(server.close())
                    tasks.append(closing)
                    await asyncio.sleep(0)
                    self.assertTrue(server.closed)
                    release.set()
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    self.assertIsInstance(
                        results[0],
                        m.ModuleError,
                        "Startup completed after close without a closed fence",
                    )
                    self.assertEqual(results[0].code, "closed")
                    self.assertIsNone(server.server)
                    self.assertFalse(
                        any(candidate.is_serving() for candidate in created),
                        "Startup published a listener after close",
                    )
                    if mode == "resistant_close":
                        self.assertEqual(
                            len(created),
                            1,
                            "Post-await fence case did not actually create a candidate listener",
                        )
                else:
                    tasks.append(asyncio.create_task(server.start()))
                    await asyncio.sleep(0)
                    tasks[0].cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await tasks[0]
                    release.set()
                    await tasks[1]
                    self.assertEqual(
                        len(calls), 1, "One cancelled waiter duplicated or cancelled shared startup"
                    )
                    await server.close()
                    self.assertFalse(any(candidate.is_serving() for candidate in created))
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await server.close()
            for candidate in created:
                candidate.close()
                await candidate.wait_closed()

    async def test_concurrent_start_has_one_listener_and_close_closes_it(self):
        await self.exercise("concurrent")

    async def test_close_during_start_cannot_publish_listener(self):
        await self.exercise("close")

    async def test_cancelled_waiter_does_not_cancel_shared_start(self):
        await self.exercise("cancel")

    async def test_cancel_resistant_start_still_hits_closed_fence(self):
        await self.exercise("resistant_close")


class OverloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_eight_stalled_plus_forty_overflow_has_only_eight_live_handlers(self):
        server = m.ModuleServer("https://steam.framey.test:9443")
        await server.start()
        clients = []
        try:
            clients.extend(
                [await asyncio.open_connection("127.0.0.1", server.port) for _ in range(8)]
            )
            await asyncio.sleep(0.01)
            clients.extend(
                await asyncio.gather(
                    *[asyncio.open_connection("127.0.0.1", server.port) for _ in range(40)]
                )
            )
            await asyncio.sleep(0.01)
            live = [
                task
                for task in asyncio.all_tasks()
                if not task.done()
                and getattr(task.get_coro(), "__qualname__", "") == "ModuleServer._serve"
            ]
            self.assertLessEqual(len(live), 8, "Overflow kept untracked live request handlers")
            self.assertEqual(server.snapshot()["connections"], 8)
            for reader, _ in clients[8:]:
                try:
                    data = await asyncio.wait_for(reader.read(1), 0.05)
                except ConnectionResetError:
                    data = b""
                self.assertEqual(data, b"", "Overflow must abort without a response/read await")
        finally:
            for _, writer in clients:
                writer.close()
            for _, writer in clients:
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
            await server.close()
