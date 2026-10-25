import asyncio
import threading
import time
import types
from pathlib import Path
from unittest import mock

import fy_net
from test_fy_net import Base, PUBLIC, local_opener, read_head, serve_script


class NetworkBounds(Base):
    async def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("The controlled worker did not finish")
            await asyncio.sleep(0.01)

    async def test_cancelled_openers_keep_slots_and_close_late_sockets(self):
        release = threading.Event()
        entered = threading.Event()
        self.addCleanup(release.set)
        calls = []
        sockets = []

        def opener(*args):
            calls.append(1)
            if len(calls) == 2:
                entered.set()
            release.wait(5)
            sock = local_opener(self.port)(*args)
            sockets.append(sock)
            return sock

        tasks = [asyncio.create_task(self.get("/ok", opener_factory=opener, dest="data:file%d" % i)) for i in range(2)]
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.assertTrue(all(isinstance(r, asyncio.CancelledError) for r in results))
            self.assertEqual(await self.code("/ok"), "busy")
            self.assertEqual(fy_net._live.get("fan"), 2)
        finally:
            release.set()
        await self.wait_until(lambda: not fy_net._live)
        self.assertTrue(all(sock.fileno() == -1 for sock in sockets))
        self.assertEqual(self.server.log, [])
        self.assertEqual(list(self.dirs.data.iterdir()), [])

    async def test_cancelled_resolution_never_opens_a_connection(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        opened = []

        def resolver(host, port):
            entered.set()
            release.wait(5)
            return [PUBLIC]

        def opener(*args):
            opened.append(1)
            return local_opener(self.port)(*args)

        task = asyncio.create_task(self.get("/ok", resolver=resolver, opener_factory=opener))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(fy_net._live.get("fan"), 1)
        finally:
            release.set()
        await self.wait_until(lambda: not fy_net._live)
        self.assertEqual(opened, [])

    async def test_cancelled_download_removes_its_temporary_file(self):
        task = asyncio.create_task(self.get("/drip", dest="runtime:cancel.bin"))
        await self.wait_until(lambda: self.dirs.runtime.exists() and any(self.dirs.runtime.iterdir()))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(list(self.dirs.runtime.iterdir()), [])
        self.assertEqual(fy_net._live, {})

    async def test_cancelled_complete_body_cannot_replace_the_existing_file(self):
        release = threading.Event()
        entered = threading.Event()
        self.addCleanup(release.set)
        self.dirs.data.mkdir(parents=True)
        target = self.dirs.data / "keep"
        target.write_bytes(b"keep")
        original = fy_net._read

        def delayed(*args):
            result = original(*args)
            entered.set()
            release.wait(5)
            return result

        try:
            with mock.patch.object(fy_net, "_read", delayed):
                task = asyncio.create_task(self.get("/ok", dest="data:keep"))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(target.read_bytes(), b"keep")
        finally:
            release.set()
        await self.wait_until(lambda: not fy_net._live)
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertEqual(list(self.dirs.data.iterdir()), [target])

    async def test_concurrent_folder_writes_observe_the_combined_quota(self):
        with mock.patch.object(fy_net, "QUOTA", 12):
            result = await asyncio.gather(self.get("/ok", dest="data:first"), self.get("/ok", dest="data:second"), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, dict) for r in result), 1)
        self.assertEqual([r.code for r in result if isinstance(r, fy_net.NetError)], ["quota"])
        self.assertEqual(sum(p.stat().st_size for p in self.dirs.data.iterdir()), 8)
        self.assertEqual(fy_net._saving, set())

    async def test_part_names_are_independent_download_destinations(self):
        result = await asyncio.gather(self.get("/ok", dest="data:item"), self.get("/json", dest="data:item.part"))
        self.assertEqual(Path(result[0]["path"]).read_bytes(), b"hello \xc3\xa9")
        self.assertEqual(Path(result[1]["path"]).read_bytes(), b'{"a":1}')
        self.assertEqual(sorted(p.name for p in self.dirs.data.iterdir()), ["item", "item.part"])

    async def test_invalid_authorities_fail_before_resolution(self):
        resolver = mock.Mock(return_value=[PUBLIC])
        for authority in ("good.test:0", "good.test:", "good..test", "good.test..", "-bad.test", "bad-.test", "a" * 64 + ".test", "[v1.good.test]"):
            with self.subTest(authority=authority):
                with self.assertRaises(fy_net.NetError) as error:
                    await fy_net.fetch_plugin("fan", "https://%s/ok" % authority, resolver=resolver)
                self.assertEqual(error.exception.code, "bad_url")
        resolver.assert_not_called()

    async def test_invalid_redirect_authorities_and_controls_are_blocked(self):
        for location in ("https://[broken/", "https://good.test:0/ok", "https://good.test/a\tb", "https://good.test/" + "x" * fy_net.URL_MAX):
            def script(connection, location=location):
                try:
                    read_head(connection)
                    connection.sendall(("HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: %s\r\n\r\n" % location).encode())
                finally:
                    connection.close()

            port = serve_script(self, script)
            with self.subTest(location=location):
                self.assertEqual(await self.code("/ok", opener_factory=local_opener(port)), "redirect_blocked")

    async def test_redirect_header_padding_is_accepted(self):
        def script(connection):
            try:
                read_head(connection)
                connection.sendall(b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation:  https://other.test/ok \t\r\n\r\n")
            finally:
                connection.close()

        port = serve_script(self, script)

        def opener(scheme, host, ip, target_port, timeout):
            return local_opener(self.port if host == "other.test" else port)(scheme, host, ip, target_port, timeout)

        result = await self.get("/ok", opener_factory=opener)
        self.assertEqual(result["status"], 200)

    def test_developer_http_exception_requires_an_explicit_boolean(self):
        for enabled in ("false", "true", 1, 0, [], {}, None):
            host = types.SimpleNamespace(settings=lambda: {"prefs": {"dev": {"enabled": enabled}}})
            self.assertFalse(fy_net._dev(host))
        host = types.SimpleNamespace(settings=lambda: {"prefs": {"dev": {"enabled": True}}})
        self.assertTrue(fy_net._dev(host))

    async def test_unwritable_destination_is_a_typed_error(self):
        self.dirs.data.parent.mkdir(parents=True, exist_ok=True)
        self.dirs.data.write_bytes(b"file")
        self.assertEqual(await self.code("/ok", dest="data:item"), "bad_dest")
        self.assertEqual(self.server.log, [])

    async def test_invalid_text_bodies_are_typed_errors(self):
        self.assertEqual(await self.code("/echo", method="POST", body="\ud800"), "bad_request")
        self.assertEqual(self.server.log, [])

    async def test_empty_directory_trees_are_bounded(self):
        self.dirs.data.mkdir(parents=True)
        for index in range(4):
            (self.dirs.data / str(index)).mkdir()
        with mock.patch.object(fy_net, "QUOTA_FILES", 3):
            self.assertEqual(await self.code("/ok", dest="data:item"), "quota")

    async def test_directory_entry_limit_reserves_space_for_new_downloads(self):
        self.dirs.data.mkdir(parents=True)
        (self.dirs.data / "first").write_bytes(b"old")
        with mock.patch.object(fy_net, "QUOTA_FILES", 1):
            self.assertEqual(await self.code("/ok", dest="data:second"), "quota")
            self.assertEqual((await self.get("/ok", dest="data:first"))["bytes"], 8)

    async def test_dns_answers_and_pending_submissions_are_bounded(self):
        self.assertEqual(await self.code("/ok", resolver=lambda h, p: [PUBLIC] * (fy_net.ADDRESSES_MAX + 1)), "network")
        self.assertEqual(await self.code("/ok", resolver=lambda h, p: ["2606:4700:4700::1111%eth0"]), "blocked_address")
        release = threading.Event()
        self.addCleanup(release.set)
        started = []

        def stuck(host, port):
            started.append(1)
            release.wait(5)
            return [PUBLIC]

        try:
            with mock.patch.object(fy_net, "DNS_MAX", 0.05):
                for _ in range(fy_net.TOTAL + 3):
                    self.assertEqual(await self.code("/ok", resolver=stuck), "timeout")
            self.assertEqual(len(started), fy_net.TOTAL)
            self.assertEqual(fy_net._resolver()._work_queue.qsize(), 0)
        finally:
            release.set()
        await self.wait_until(lambda: not fy_net._live)
        self.assertEqual((await self.get("/ok"))["status"], 200)

    async def test_dns_answer_iteration_is_inside_the_resolution_deadline(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def answers(host, port):
            yield PUBLIC
            release.wait(5)
            yield PUBLIC

        try:
            with mock.patch.object(fy_net, "DNS_MAX", 0.05):
                start = time.monotonic()
                self.assertEqual(await self.code("/ok", resolver=answers), "timeout")
                self.assertLess(time.monotonic() - start, 1)
        finally:
            release.set()
