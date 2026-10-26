import sys
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fy_catalog
import fy_decky_catalog
import fy_net
from test_fy_catalog_flow import FakeHost


class RequestIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_each_native_and_decky_request_uses_current_runtime_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = FakeHost(Path(temporary))
            host.LOADER = "https://github.com/chaosfox26/framey"
            native = fy_catalog.Catalog(host)
            decky = fy_decky_catalog.DeckyCatalog(host)
            for version in ("1.0.2", "2.3.4-beta.1"):
                host.VERSION = version
                expected = "Framey/%s (+https://github.com/chaosfox26/framey)" % version
                with mock.patch.object(fy_net, "fetch_plugin", new=mock.AsyncMock(return_value={"status": 200, "text": "[]"})) as fetch:
                    await native._download(fy_catalog.DEFAULT_URL)
                    for source in fy_decky_catalog.SOURCES.values():
                        await decky._download(source["url"])
                    self.assertEqual(fetch.await_count, 3)
                    for call in fetch.await_args_list:
                        self.assertEqual(call.kwargs["headers"], {"Accept": "application/json", "User-Agent": expected})
                with mock.patch.object(fy_net, "fetch_bytes", new=mock.AsyncMock(return_value=b"archive")) as archive:
                    for _ in range(2):
                        self.assertEqual(await decky._archive(fy_decky_catalog.CDN + "a" * 64 + ".zip"), b"archive")
                    self.assertEqual(archive.await_count, 2)
                    for call in archive.await_args_list:
                        self.assertEqual(call.kwargs["headers"], {"User-Agent": expected})
                        self.assertEqual(call.kwargs["max_bytes"], fy_decky_catalog.ARCHIVE_MAX)

    async def test_invalid_version_or_project_cannot_inject_headers_or_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = FakeHost(Path(temporary))
            for value in (None, 123, "", "1.0.2\r\nAuthorization: secret", "1.0.2 user", "1.0.2\t", "é", "\ud800", "1" * 81):
                host.VERSION = value
                host.LOADER = "https://private.example/path?token=secret"
                with self.subTest(value=repr(value)):
                    actual = fy_catalog._user_agent(host)
                    self.assertEqual(actual, "Framey/unknown (+https://github.com/chaosfox26/framey)")
                    self.assertEqual(fy_net._headers({"User-Agent": actual}), {"User-Agent": actual})
            host.VERSION = "1.0.2"
            for project in (None, 1, "https://github.com/user/repo?token=secret", "https://github.com/user/repo\r\nsecret", "http://github.com/user/repo"):
                host.LOADER = project
                self.assertEqual(fy_catalog._user_agent(host), "Framey/1.0.2 (+https://github.com/chaosfox26/framey)")


class StoreAccessBackoff(unittest.IsolatedAsyncioTestCase):
    async def test_503_with_retry_after_blocks_manual_and_restart_feed_and_archive_retries(self):
        for adapter in (fy_catalog.Catalog, fy_decky_catalog.DeckyCatalog):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as temporary:
                host = FakeHost(Path(temporary))
                fetch = mock.AsyncMock(return_value={"status": 503, "headers": {"retry-after": "3600"}})
                with mock.patch.object(fy_net, "fetch_plugin", fetch):
                    service = adapter(host)
                    first = await service.list()
                    self.assertEqual(first["retry_after"], host.clock + 3600)
                    self.assertIn("temporarily unavailable", first["warning"])
                    await service.list({"refresh": True})
                    await adapter(host).list({"refresh": True})
                    self.assertEqual(fetch.await_count, 1)
        with tempfile.TemporaryDirectory() as temporary:
            host = FakeHost(Path(temporary))
            async def metadata(*args):
                return json.dumps([{"id": 1, "name": "Example", "versions": [{"name": "1.0.0", "hash": "a" * 64}]}]).encode()
            service = fy_decky_catalog.DeckyCatalog(host, fetcher=metadata)
            entry = (await service.details({"id": "decky-1"}))["entry"]
            request = {"id": entry["id"], "identity": entry["identity"], "version_identity": entry["versions"][0]["identity"], "confirm": True}
            fetch = mock.AsyncMock(side_effect=fy_net.NetError("network", "private response", status=503, retry_after=3600))
            with mock.patch.object(fy_net, "fetch_bytes", fetch):
                self.assertEqual((await service.assess(request))["code"], "server_retry")
                await service.list({"refresh": True})
                self.assertEqual((await service.assess(request))["code"], "server_retry")
                restarted = fy_decky_catalog.DeckyCatalog(host, fetcher=metadata)
                self.assertEqual((await restarted.assess(request))["code"], "server_retry")
                self.assertEqual(fetch.await_count, 1)
        self.assertIsNone(fy_catalog._response_access({"status": 503, "headers": {}}, 0))

    async def test_malformed_optional_catalog_cannot_erase_valid_denial_deadline(self):
        for adapter, source in ((fy_catalog.Catalog, "stable"), (fy_decky_catalog.DeckyCatalog, "decky_stable")):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as temporary:
                host = FakeHost(Path(temporary))
                fetch = mock.AsyncMock(return_value={"status": 429, "headers": {"retry-after": "7200"}})
                with mock.patch.object(fy_net, "fetch_plugin", fetch):
                    service = adapter(host)
                    await service.list({"source": source})
                    saved = json.loads(service.path.read_text())
                    record = next(iter(saved["cache"].values()))
                    if adapter is fy_catalog.Catalog:
                        record["catalog"] = {"schema": 1, "name": "Example", "plugins": "malformed"}
                    else:
                        record["raw"] = [{"id": "malformed"}]
                    service.path.write_text(json.dumps(saved))
                    restarted = adapter(host)
                    denied = await restarted.list({"source": source, "refresh": True})
                    self.assertEqual(denied["retry_after"], host.clock + 7200)
                    self.assertIn("slow down", denied["warning"])
                    self.assertEqual(fetch.await_count, 1)

    async def test_denied_feed_cooldown_survives_manual_refresh_and_cold_restart(self):
        for adapter, source in ((fy_catalog.Catalog, "stable"), (fy_decky_catalog.DeckyCatalog, "decky_stable")):
            for status in (401, 403, 429):
                with self.subTest(adapter=adapter.__name__, status=status), tempfile.TemporaryDirectory() as temporary:
                    host = FakeHost(Path(temporary))
                    reply = {"status": status, "headers": {"retry-after": "7200"}, "text": "private server response"}
                    fetch = mock.AsyncMock(return_value=reply)
                    with mock.patch.object(fy_net, "fetch_plugin", fetch):
                        service = adapter(host)
                        request = {"source": source, "refresh": True}
                        first = await service.list(request)
                        expected = "rate_limited" if status == 429 else "access_denied"
                        self.assertIsNone(first["error"])
                        self.assertTrue(first["stale"])
                        self.assertEqual(first["retry_after"], host.clock + 7200)
                        self.assertNotIn("private", first["warning"])
                        self.assertIn("slow down" if status == 429 else "denied Framey access", first["warning"])
                        await service.list(request)
                        restarted = adapter(host)
                        same = await restarted.list(request)
                        self.assertEqual(same["warning"], first["warning"])
                        self.assertEqual(fetch.await_count, 1)
                        self.assertEqual(next(iter((restarted.state["cache"] if adapter is fy_catalog.Catalog else restarted.cache).values()))["access_code"], expected)
                        host.clock += 7201
                        await restarted.list(request)
                        self.assertEqual(fetch.await_count, 2)

    async def test_warm_catalog_survives_access_denial_and_no_private_exception_is_saved(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = FakeHost(Path(temporary))
            feed = [{"id": 1, "name": "Example", "versions": []}]
            fetch = mock.AsyncMock(side_effect=[{"status": 200, "text": json.dumps(feed)}, {"status": 403, "headers": {}, "text": "secret response"}])
            with mock.patch.object(fy_net, "fetch_plugin", fetch):
                service = fy_decky_catalog.DeckyCatalog(host)
                self.assertEqual((await service.list())["total"], 1)
                denied = await service.list({"refresh": True})
                self.assertEqual(denied["total"], 1)
                self.assertTrue(denied["stale"])
                self.assertIn("denied Framey access", denied["warning"])
                self.assertNotIn("secret response", service.path.read_text())
            async def failure(*args):
                error = RuntimeError("private response")
                error.message = "secret message /home/private"
                raise error
            host.clock += fy_decky_catalog.RETRY + 1
            service.fetcher = failure
            await service.list({"refresh": True})
            self.assertNotIn("secret message", service.path.read_text())

    async def test_archive_denial_cooldown_survives_restart_without_fetch_or_execution(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("plugin.json", '{"name":"Example","flags":[]}')
        payload = data.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        feed = [{"id": 1, "name": "Example", "versions": [{"name": "1.0.0", "hash": digest}]}]
        async def metadata(*args):
            return json.dumps(feed).encode()
        with tempfile.TemporaryDirectory() as temporary:
            host = FakeHost(Path(temporary))
            service = fy_decky_catalog.DeckyCatalog(host, fetcher=metadata)
            entry = (await service.details({"id": "decky-1"}))["entry"]
            request = {"id": entry["id"], "identity": entry["identity"], "version_identity": entry["versions"][0]["identity"], "confirm": True}
            error = fy_net.NetError("rate_limited", "private response", status=429, retry_after=3600)
            fetch = mock.AsyncMock(side_effect=[error, payload])
            with mock.patch.object(fy_net, "fetch_bytes", fetch):
                denied = await service.assess(request)
                self.assertEqual(denied["code"], "rate_limited")
                self.assertNotIn("private", denied["error"])
                self.assertEqual((await service.assess(request))["code"], "rate_limited")
                restarted = fy_decky_catalog.DeckyCatalog(host, fetcher=metadata)
                self.assertEqual((await restarted.assess(request))["code"], "rate_limited")
                self.assertEqual(fetch.await_count, 1)
                host.clock += 3601
                successful = await restarted.assess(request)
                self.assertTrue(successful["assessment"]["not_executed"])
                self.assertEqual(fetch.await_count, 2)


if __name__ == "__main__":
    unittest.main()
