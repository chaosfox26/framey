import asyncio
import hashlib
import io
import json
import threading
import types
import zipfile
from pathlib import Path
from unittest import mock

import fy_decky_catalog as decky
import fy_net
from test_fy_net import Base, PUBLIC, local_opener, read_head, serve_script


def feed():
    return json.dumps([{"id": 1, "name": "Sample", "author": "Example", "tags": [],
                        "versions": [{"name": "1.0.0", "hash": "a" * 64}]}]).encode()


def reply(connection, body=b"", status="200 OK", headers=()):
    head = ["HTTP/1.1 " + status, "Content-Length: " + str(len(body)), "Connection: close"]
    head += [name + ": " + value for name, value in headers]
    connection.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + body)


class DeckyNetworkPlatform(Base):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        self.host = types.SimpleNamespace(HOME=root, CONF=root / "settings.json", PLATFORM="linux-aarch64",
                                          now=lambda: 1_790_000_000.0,
                                          settings=lambda: {"prefs": {"dev": {"enabled": False}}})
        self.catalog = decky.DeckyCatalog(self.host)
        self.requests, self.connections, self.lookups = [], [], []

    def transport(self, handler, addresses=None):
        addresses = addresses or {}

        def script(connection):
            try:
                request = read_head(connection)
                self.requests.append(request)
                handler(connection, request)
            except OSError:
                pass
            finally:
                connection.close()

        port = serve_script(self, script)

        def resolve(host, target_port):
            self.lookups.append((host, target_port))
            return addresses.get(host, [PUBLIC])

        def connect(scheme, host, ip, target_port, timeout, track=None):
            self.connections.append((scheme, host, ip, target_port))
            connection = local_opener(port)(scheme, host, ip, target_port, timeout)
            if track:
                track(connection)
            return connection

        resolver = mock.patch.object(fy_net, "_gai", resolve)
        opener = mock.patch.object(fy_net, "_open", connect)
        resolver.start()
        opener.start()
        self.addCleanup(resolver.stop)
        self.addCleanup(opener.stop)

    async def test_official_feed_uses_public_https_and_reuses_the_validated_cache(self):
        self.transport(lambda connection, request: reply(connection, feed(), headers=[("Content-Type", "application/json")]))
        first = await self.catalog.list()
        second = await self.catalog.details({"id": "decky-1"})
        self.assertEqual(first["origin"], "downloaded")
        self.assertEqual(second["origin"], "cache")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.connections, [("https", "plugins.deckbrew.xyz", PUBLIC, 443)])
        self.assertIn(b"GET /plugins HTTP/1.1\r\n", self.requests[0])
        self.assertIn(b"Host: plugins.deckbrew.xyz\r\n", self.requests[0])
        self.assertFalse(first["rows"][0]["eligible"])
        self.assertFalse(first["rows"][0]["compatibility"]["runtime_verified"])

    async def test_catalog_redirects_recheck_all_dns_answers(self):
        self.transport(lambda connection, request: reply(connection, status="302 Found", headers=[("Location", "https://other.example/feed")]),
                       {"other.example": [PUBLIC, "127.0.0.1"]})
        result = await self.catalog.list()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["origin"], "unavailable")
        self.assertTrue(result["fetch_error"])
        self.assertEqual([host for _, host, _, _ in self.connections], ["plugins.deckbrew.xyz"])
        self.assertIn(("other.example", 443), self.lookups)

    async def test_https_feed_cannot_redirect_to_http_in_developer_mode(self):
        self.host.settings = lambda: {"prefs": {"dev": {"enabled": True}}}
        self.transport(lambda connection, request: reply(connection, status="302 Found", headers=[("Location", "http://other.example/feed")]))
        result = await self.catalog.list()
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["fetch_error"])
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.lookups, [("plugins.deckbrew.xyz", 443)])

    async def test_oversized_feed_never_becomes_a_partial_catalog(self):
        self.transport(lambda connection, request: reply(connection, feed() + b" " * decky.FEED_MAX, headers=[("Content-Type", "application/json")]))
        result = await self.catalog.list()
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["origin"], "unavailable")
        self.assertTrue(result["fetch_error"])
        self.assertNotIn("catalog", self.catalog.cache["decky_stable"])
        self.assertEqual(fy_net._live, {})

    async def test_cancelled_reader_does_not_cancel_the_shared_bounded_feed_refresh(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def handler(connection, request):
            entered.set()
            release.wait(3)
            reply(connection, feed(), headers=[("Content-Type", "application/json")])

        self.transport(handler)
        first = asyncio.create_task(self.catalog.list())
        second = asyncio.create_task(self.catalog.list())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(len(self.catalog.refreshes), 1)
            self.assertEqual(fy_net._live.get("_core"), 1)
        finally:
            release.set()
        result = await asyncio.wait_for(second, 3)
        self.assertEqual(result["rows"][0]["id"], "decky-1")
        self.assertEqual(len(self.requests), 1)
        await asyncio.sleep(0)
        self.assertEqual(self.catalog.refreshes, {})
        self.assertEqual(fy_net._live, {})

    async def test_complete_artifact_bytes_can_exceed_the_page_inline_limit(self):
        body = bytes(range(256)) * 4096
        self.transport(lambda connection, request: reply(connection, body, headers=[("Content-Type", "application/zip")]))
        with mock.patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:1"}), mock.patch("urllib.request.build_opener", side_effect=AssertionError("artifact used ambient proxy transport")):
            result = await fy_net.fetch_bytes("_core", decky.CDN + "a" * 64 + ".zip")
        self.assertEqual(result, body)
        self.assertGreater(len(result), fy_net.INLINE_MAX)
        self.assertEqual(self.connections[0][:2], ("https", "cdn.tzatzikiweeb.moe"))
        self.assertIn(b"Accept-Encoding: identity\r\n", self.requests[0])

    async def test_selected_archive_uses_the_same_transport_and_remains_read_only(self):
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("plugin.json", '{"name":"Sample","flags":[]}')
            archive.writestr("package.json", '{"name":"sample","version":"1.0.0"}')
            archive.writestr("main.py", "raise AssertionError('inspection executed a backend')\n")
            archive.writestr("dist/index.js", "throw new Error('inspection executed a frontend');")
            archive.writestr("data.bin", b"x" * (fy_net.INLINE_MAX + 1))
        payload = bundle.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        metadata = feed().replace(b"a" * 64, digest.encode())

        def handler(connection, request):
            reply(connection, metadata if request.startswith(b"GET /plugins ") else payload)

        self.host.fetch = mock.Mock(side_effect=AssertionError("archive bypassed bounded async transport"))
        self.transport(handler)
        entry = (await self.catalog.details({"id": "decky-1"}))["entry"]
        self.assertEqual(len(self.requests), 1)
        result = await self.catalog.assess({"id": "decky-1", "identity": entry["identity"],
                                            "version_identity": entry["versions"][0]["identity"], "confirm": True})
        self.assertNotIn("error", result, result)
        self.assertTrue(result["read_only"])
        self.assertTrue(result["assessment"]["not_executed"])
        self.assertEqual(result["assessment"]["bytes"], len(payload))
        self.assertEqual(result["assessment"]["architecture_status"], "unverified")
        self.assertEqual(len(self.requests), 2)
        self.assertIn((digest + ".zip").encode(), self.requests[-1])
        self.assertEqual([host for _, host, _, _ in self.connections], ["plugins.deckbrew.xyz", "cdn.tzatzikiweeb.moe"])
        self.host.fetch.assert_not_called()
        self.assertEqual(fy_net._live, {})

    async def test_artifact_limit_fails_instead_of_returning_a_partial_archive(self):
        self.transport(lambda connection, request: reply(connection, b"12345678"))
        self.assertEqual(await fy_net.fetch_bytes("_core", "https://download.example/archive.zip", max_bytes=8), b"12345678")
        with self.assertRaises(fy_net.NetError) as error:
            await fy_net.fetch_bytes("_core", "https://download.example/archive.zip", max_bytes=7)
        self.assertEqual(error.exception.code, "too_large")
        self.assertEqual(fy_net._live, {})

    async def test_artifact_limits_and_plain_http_fail_before_any_lookup(self):
        with mock.patch.object(fy_net, "_gai", side_effect=AssertionError("invalid artifact submitted DNS")):
            for maximum in (0, -1, True, 5.0, None, fy_net.DEST_MAX + 1):
                with self.subTest(maximum=maximum):
                    with self.assertRaises(fy_net.NetError) as error:
                        await fy_net.fetch_bytes("_core", "https://download.example/archive.zip", max_bytes=maximum)
                    self.assertEqual(error.exception.code, "bad_request")
            with self.assertRaises(fy_net.NetError) as error:
                await fy_net.fetch_bytes("_core", "http://download.example/archive.zip")
            self.assertEqual(error.exception.code, "scheme")

    async def test_artifact_redirects_allow_signed_public_https_urls(self):
        def handler(connection, request):
            if b"Host: download.example\r\n" in request:
                reply(connection, status="302 Found", headers=[("Location", "https://cdn.example/archive.zip?expires=100&signature=example")])
            else:
                reply(connection, b"zip bytes")

        self.transport(handler)
        result = await fy_net.fetch_bytes("_core", "https://download.example/archive.zip")
        self.assertEqual(result, b"zip bytes")
        self.assertEqual([host for _, host, _, _ in self.connections], ["download.example", "cdn.example"])
        self.assertIn(b"GET /archive.zip?expires=100&signature=example HTTP/1.1\r\n", self.requests[-1])

    async def test_artifact_status_and_early_disconnect_never_return_bytes(self):
        def handler(connection, request):
            if request.startswith(b"GET /short "):
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\nConnection: close\r\n\r\nabc")
            else:
                reply(connection, b"incomplete", status="206 Partial Content" if b"/partial " in request else "404 Not Found")

        self.transport(handler)
        for path in ("/short", "/partial", "/missing"):
            with self.subTest(path=path):
                with self.assertRaises(fy_net.NetError) as error:
                    await fy_net.fetch_bytes("_core", "https://download.example" + path)
                self.assertEqual(error.exception.code, "network")

    async def test_artifact_cancellation_releases_the_actual_network_worker(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def handler(connection, request):
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\nabc")
            entered.set()
            release.wait(3)

        self.transport(handler)
        task = asyncio.create_task(fy_net.fetch_bytes("_core", "https://download.example/archive.zip"))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(fy_net._live, {})
        finally:
            release.set()
