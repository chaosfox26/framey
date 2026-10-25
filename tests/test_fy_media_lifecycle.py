import asyncio
import io
import os
import threading
import unittest
from unittest import mock

import fy_media
import test_fy_media as fixtures
from test_fy_media import Base, VIDEO, http, refused


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.transport = self
        self.aborted = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def abort(self):
        self.aborted = True


class Lifecycle(Base):
    async def test_concurrent_issuance_preserves_plugin_and_global_caps(self):
        original = self.media._start

        async def delayed_start():
            await asyncio.sleep(0.01)
            await original()

        for i in range(12):
            (self.dir / ("n%d.mp4" % i)).write_bytes(VIDEO)
        with mock.patch.object(self.media, "_start", delayed_start):
            await asyncio.gather(*(
                self.media.url_for("p", self.dir / ("n%d.mp4" % i))
                for i in range(12)
            ))
        self.assertEqual(len(self.media._tokens), fy_media.PER_PLUGIN)
        small = fy_media.Media(max_tokens=2, data_max=1)
        self.addAsyncCleanup(small.close)
        await asyncio.gather(*(
            small.url_for("p%d" % i, self.dir / "v.mp4") for i in range(12)
        ))
        self.assertEqual(len(small._tokens), 2)

    async def test_concurrent_same_file_reuses_one_token(self):
        results = await asyncio.gather(*(
            self.media.url_for("p", self.dir / "v.mp4") for _ in range(12)
        ))
        self.assertEqual(len({r["token"] for r in results}), 1)
        self.assertEqual(len(self.media._tokens), 1)

    async def test_pending_inline_and_stream_requests_cannot_outlive_revocation(self):
        for name in ("t.txt", "v.mp4"):
            with self.subTest(name=name):
                started = asyncio.Event()
                release = threading.Event()
                loop = asyncio.get_running_loop()
                inspect = fy_media._inspect

                def delayed_inspect(*args):
                    loop.call_soon_threadsafe(started.set)
                    if not release.wait(3):
                        raise RuntimeError("Test worker timed out")
                    return inspect(*args)

                with mock.patch.object(fy_media, "_inspect", delayed_inspect):
                    task = asyncio.create_task(self.media.url_for("p", self.dir / name))
                    try:
                        await asyncio.wait_for(started.wait(), 2)
                        self.assertEqual(self.media.revoke_plugin("p"), 0)
                    finally:
                        release.set()
                    with self.assertRaises(fy_media.MediaError) as caught:
                        await task
                    self.assertEqual(caught.exception.code, "cancelled")
                self.assertEqual(self.media._tokens, {})
                self.assertIsNone(self.media._server)

    async def test_close_invalidates_issuance_waiting_for_server_start(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.media._start

        async def delayed_start():
            started.set()
            await release.wait()
            await original()

        with mock.patch.object(self.media, "_start", delayed_start):
            task = asyncio.create_task(self.media.url_for("p", self.dir / "v.mp4"))
            await asyncio.wait_for(started.wait(), 2)
            await self.media.close()
            release.set()
            with self.assertRaises(fy_media.MediaError) as caught:
                await task
            self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(self.media._tokens, {})
        self.assertIsNone(self.media._server)
        self.assertIsNone(self.media._handle)

    async def test_cancelled_issuance_closes_a_started_empty_server(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.media._start
        port = None

        async def delayed_start():
            nonlocal port
            await original()
            port = self.media._port
            started.set()
            await release.wait()

        with mock.patch.object(self.media, "_start", delayed_start):
            task = asyncio.create_task(self.media.url_for("p", self.dir / "v.mp4"))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.media._tokens, {})
        self.assertIsNone(self.media._server)
        self.assertTrue(await refused(port))

    async def test_invalid_default_ttl_cannot_break_resource_issuance(self):
        for default in (float("nan"), float("inf"), None, False, -1):
            with self.subTest(default=default):
                media = fy_media.Media(default_ttl=default, data_max=1)
                self.addAsyncCleanup(media.close)
                result = await media.url_for("p", self.dir / "v.mp4")
                self.assertIsInstance(result["expires"], int)
                self.assertEqual((await http(result["url"]))[0], 200)

    async def test_cancelled_open_closes_the_file_when_worker_finishes(self):
        started, finished = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        class File(io.BytesIO):
            def close(self):
                super().close()
                loop.call_soon_threadsafe(finished.set)

        opened = File(b"test")

        def delayed_open(path):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise RuntimeError("Test worker timed out")
            return opened

        with mock.patch.object(fy_media, "_open_file", delayed_open):
            task = asyncio.create_task(fy_media._open("unused"))
            try:
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(opened.closed)
            finally:
                release.set()
            await asyncio.wait_for(finished.wait(), 2)
            self.assertTrue(opened.closed)

    async def test_revocation_during_open_never_sends_success_headers(self):
        result = await self.media.url_for("p", self.dir / "v.mp4")
        other = await self.media.url_for("q", self.dir / "w.webm")
        started, release = asyncio.Event(), asyncio.Event()
        original = fy_media._open
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(("GET /m/%s HTTP/1.1\r\n\r\n" % result["token"]).encode())

        async def delayed_open(path):
            started.set()
            await release.wait()
            return await original(path)

        with mock.patch.object(fy_media, "_open", delayed_open):
            task = asyncio.create_task(self.media._handle_request(reader, writer))
            await asyncio.wait_for(started.wait(), 2)
            self.assertTrue(self.media.revoke(result["token"], "p"))
            self.assertTrue(writer.aborted)
            release.set()
            await task
        self.assertTrue(writer.data.startswith(b"HTTP/1.1 404"))
        self.assertEqual((await http(other["url"]))[0], 200)

    async def test_revocation_during_read_never_writes_the_pending_chunk(self):
        result = await self.media.url_for("p", self.dir / "v.mp4")
        other = await self.media.url_for("q", self.dir / "w.webm")
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(("GET /m/%s HTTP/1.1\r\n\r\n" % result["token"]).encode())
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def delayed_chunk(f, n):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise RuntimeError("Test worker timed out")
            return f.read(n)

        with mock.patch.object(fy_media, "_chunk", delayed_chunk):
            task = asyncio.create_task(self.media._handle_request(reader, writer))
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertTrue(self.media.revoke(result["token"], "p"))
            finally:
                release.set()
            await task
        self.assertTrue(writer.aborted)
        head, _, body = writer.data.partition(b"\r\n\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.1 200"))
        self.assertEqual(body, b"")
        self.assertEqual((await http(other["url"]))[0], 200)

    async def test_cancelled_read_defers_file_close_until_worker_finishes(self):
        result = await self.media.url_for("p", self.dir / "v.mp4")
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(("GET /m/%s HTTP/1.1\r\n\r\n" % result["token"]).encode())
        opened = fy_media._open_file(str(self.dir / "v.mp4"))
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        async def open_file(path):
            return opened

        def delayed_chunk(f, n):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise RuntimeError("Test worker timed out")
            self.assertFalse(f.closed)
            return f.read(n)

        with mock.patch.object(fy_media, "_open", open_file), mock.patch.object(fy_media, "_chunk", delayed_chunk):
            task = asyncio.create_task(self.media._handle_request(reader, writer))
            try:
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(opened.closed)
            finally:
                release.set()
            for _ in range(100):
                if opened.closed:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(opened.closed)


class FileChanges(Base):
    @unittest.skipUnless(hasattr(os, "mkfifo"), "Requires POSIX FIFOs")
    async def test_fifo_issuance_and_replacement_are_refused_without_blocking(self):
        fifo = self.dir / "pipe.mp4"
        os.mkfifo(fifo)
        with self.assertRaises(fy_media.MediaError):
            await asyncio.wait_for(self.media.url_for("p", fifo), 1)
        path = self.dir / "v.mp4"
        result = await self.media.url_for("p", path)
        path.unlink()
        os.mkfifo(path)
        self.assertEqual((await asyncio.wait_for(http(result["url"]), 1))[0], 404)

    async def test_parent_directory_replaced_by_symlink_is_refused(self):
        folder, outside = self.dir / "inside", self.dir / "outside"
        folder.mkdir()
        outside.mkdir()
        (folder / "clip.mp4").write_bytes(VIDEO)
        (outside / "clip.mp4").write_bytes(b"outside")
        result = await self.media.url_for("p", folder / "clip.mp4")
        folder.rename(self.dir / "old")
        folder.symlink_to(outside, target_is_directory=True)
        self.assertEqual((await http(result["url"]))[::2], (404, b""))

    async def test_adapter_rechecks_root_if_path_changes_after_safe_join(self):
        import fy_files
        host = fixtures.Adapter.host(self)
        self.addAsyncCleanup(fy_media.close)
        target = self.dir / "d" / "pic.png"
        outside = self.dir / "outside.png"
        outside.write_bytes(b"outside")
        original = fy_files.safe_join

        def swap_after_join(root, rel):
            path = original(root, rel)
            target.unlink()
            target.symlink_to(outside)
            return path

        with mock.patch.object(fy_files, "safe_join", swap_after_join):
            result = await fy_media.resource(host, {"pid": "p", "root": "data", "path": "pic.png"})
        self.assertEqual(result["code"], "outside_root")

    async def test_adapter_refuses_root_replacement_after_validation(self):
        import fy_files
        host = fixtures.Adapter.host(self)
        self.addAsyncCleanup(fy_media.close)
        outside = self.dir / "outside"
        outside.mkdir()
        (outside / "pic.png").write_bytes(b"outside")
        original = fy_files.safe_join

        def swap_after_join(root, rel):
            path = original(root, rel)
            (self.dir / "d").rename(self.dir / "old")
            (self.dir / "d").symlink_to(outside, target_is_directory=True)
            return path

        with mock.patch.object(fy_files, "safe_join", swap_after_join):
            result = await fy_media.resource(host, {"pid": "p", "root": "data", "path": "pic.png"})
        self.assertEqual(result["code"], "outside_root")


class HttpBounds(Base):
    async def test_header_bound_includes_the_terminator(self):
        result = await self.media.url_for("p", self.dir / "v.mp4")
        prefix = "GET /m/%s HTTP/1.1\r\nX: " % result["token"]
        for length, status in ((fy_media.HEAD_MAX, 200), (fy_media.HEAD_MAX + 1, 431)):
            raw = prefix + "a" * (length - len(prefix) - 4) + "\r\n\r\n"
            self.assertEqual((await http(result["url"], raw=raw))[0], status)

    async def test_invalid_http_versions_are_rejected(self):
        result = await self.media.url_for("p", self.dir / "v.mp4")
        for version in ("garbage", "HTTP/2.0", "HTTP/1.2", ""):
            raw = "GET /m/%s %s\r\n\r\n" % (result["token"], version)
            self.assertEqual((await http(result["url"], raw=raw))[0], 400)

    async def test_adapter_revoke_requires_an_exact_issued_url_or_token(self):
        host = fixtures.Adapter.host(self)
        self.addAsyncCleanup(fy_media.close)
        result = await fy_media.hostcall(host, "p", {"root": "data", "path": "big.mp4"})
        for raw in (result["url"] + "0", result["url"] + "?x=1", "https://example.invalid/m/" + result["token"], "prefix " + result["url"]):
            self.assertEqual(await fy_media.hostcall(host, "p", {"revoke": raw}), {"ok": False})
        self.assertEqual(await fy_media.hostcall(host, "q", {"revoke": result["url"]}), {"ok": False})
        self.assertEqual(await fy_media.hostcall(host, "p", {"revoke": result["url"]}), {"ok": True})
        self.assertTrue(await refused(self.port(result)))
