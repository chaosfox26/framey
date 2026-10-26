import http.client
import io
import tempfile
import types
import unittest
from email.utils import formatdate
from pathlib import Path
from unittest import mock

import fy_catalog
import fy_decky_catalog
import fy_net
from test_fy_net import PUBLIC, local_opener, read_head, serve_script


class UserAgentWireTransport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.host = types.SimpleNamespace(HOME=root, CONF=root / "settings.json", VERSION="1.2.3",
                                          LOADER="https://github.com/chaosfox26/framey")
        self.requests = []

    def transport(self, handler=None, failing_first_address=False):
        def script(connection):
            try:
                raw = read_head(connection)
                first, rest = raw.split(b"\r\n", 1)
                headers = http.client.parse_headers(io.BytesIO(rest))
                self.requests.append((first.decode("ascii"), headers))
                if handler:
                    status, body, extra = handler(first.decode("ascii"), headers)
                elif first.startswith(b"GET /start "):
                    status, body, extra = 302, b"", [("Location", "/same")]
                elif first.startswith(b"GET /same "):
                    status, body, extra = 307, b"", [("Location", "https://other.example/final")]
                else:
                    status, body, extra = 200, b"ok", []
                lines = ["HTTP/1.1 " + str(status) + " Result", "Connection: close", "Content-Type: text/plain", "Content-Length: " + str(len(body))]
                lines.extend(name + ": " + value for name, value in extra)
                connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
            except OSError:
                pass
            finally:
                connection.close()

        port = serve_script(self, script)

        def connect(scheme, host, ip, target_port, timeout, track=None):
            self.assertEqual(scheme, "https")
            if failing_first_address and ip == PUBLIC:
                raise ConnectionRefusedError()
            connection = local_opener(port)(scheme, host, ip, target_port, timeout)
            if track:
                track(connection)
            return connection

        addresses = [PUBLIC, "93.184.216.35"] if failing_first_address else [PUBLIC]
        resolver = mock.patch.object(fy_net, "_gai", return_value=addresses)
        opener = mock.patch.object(fy_net, "_open", connect)
        resolver.start()
        opener.start()
        self.addCleanup(resolver.stop)
        self.addCleanup(opener.stop)

    def assert_agent(self, requests, expected):
        for _, headers in requests:
            self.assertEqual(headers.get_all("User-Agent"), [expected])

    async def test_real_catalog_feed_header_survives_same_and_cross_host_redirects(self):
        self.transport()
        for service in (fy_catalog.Catalog(self.host), fy_decky_catalog.DeckyCatalog(self.host)):
            start = len(self.requests)
            self.assertEqual(await service._download("https://good.example/start"), "ok")
            sent = self.requests[start:]
            self.assertEqual([headers["Host"] for _, headers in sent], ["good.example", "good.example", "other.example"])
            self.assert_agent(sent, "Framey/1.2.3 (+https://github.com/chaosfox26/framey)")
            self.assertTrue(all(headers.get("Authorization") is None and headers.get("Cookie") is None for _, headers in sent))

    async def test_archive_header_survives_redirects_and_is_refreshed_for_later_requests(self):
        self.transport()
        service = fy_decky_catalog.DeckyCatalog(self.host)
        self.assertEqual(await service._archive("https://good.example/start"), b"ok")
        self.assert_agent(self.requests, "Framey/1.2.3 (+https://github.com/chaosfox26/framey)")
        before = len(self.requests)
        self.host.VERSION = "1.2.4"
        self.assertEqual(await service._archive("https://good.example/start"), b"ok")
        self.assert_agent(self.requests[before:], "Framey/1.2.4 (+https://github.com/chaosfox26/framey)")

    async def test_later_feed_attempt_uses_current_application_version(self):
        self.transport()
        service = fy_decky_catalog.DeckyCatalog(self.host)
        await service._download("https://good.example/final")
        self.host.VERSION = "1.2.4"
        await service._download("https://good.example/final")
        self.assert_agent(self.requests[:1], "Framey/1.2.3 (+https://github.com/chaosfox26/framey)")
        self.assert_agent(self.requests[1:], "Framey/1.2.4 (+https://github.com/chaosfox26/framey)")

    async def test_address_failover_keeps_the_single_application_header(self):
        self.transport(failing_first_address=True)
        await fy_decky_catalog.DeckyCatalog(self.host)._download("https://good.example/final")
        self.assertEqual(len(self.requests), 1)
        self.assert_agent(self.requests, "Framey/1.2.3 (+https://github.com/chaosfox26/framey)")

    async def test_plugin_agent_is_preserved_while_cross_origin_sensitive_headers_are_removed(self):
        self.transport()
        agent = "SamplePlugin/2.3 (+https://example.org/project)"
        await fy_net.fetch_plugin("sample", "https://good.example/start", headers={"User-Agent": agent,
                                   "Authorization": "sample", "Cookie": "sample=value", "Proxy-Authorization": "sample",
                                   "X-Api-Key": "sample", "X-Auth-Token": "sample"})
        self.assert_agent(self.requests, agent)
        for _, headers in self.requests[:2]:
            self.assertEqual(headers["Authorization"], "sample")
            self.assertEqual(headers["Cookie"], "sample=value")
        for name in ("Authorization", "Cookie", "Proxy-Authorization", "X-Api-Key", "X-Auth-Token"):
            self.assertIsNone(self.requests[-1][1].get(name))

    async def test_missing_plugin_agent_does_not_inject_the_application_identity(self):
        self.transport()
        await fy_net.fetch_plugin("sample", "https://good.example/final")
        self.assertIsNone(self.requests[0][1].get("User-Agent"))

    async def test_feed_reply_exposes_only_bounded_cooldown_header_without_retrying(self):
        self.transport(lambda first, headers: (429, b"wait", [("Retry-After", "7200"), ("Set-Cookie", "sample=value")]))
        reply = await fy_net.fetch_plugin("_core", "https://good.example/limited", headers={"User-Agent": "Framey/1.2.3"})
        self.assertEqual(reply["status"], 429)
        self.assertEqual(reply["headers"]["retry-after"], "7200")
        self.assertNotIn("set-cookie", reply["headers"])
        self.assertEqual(len(self.requests), 1)

    async def test_binary_denial_and_rate_errors_preserve_only_safe_typed_metadata(self):
        statuses = {"/denied": 403, "/authenticate": 401, "/limited": 429, "/failure": 503}
        self.transport(lambda first, headers: (statuses[first.split()[1]], b"response body must stay private", [("Retry-After", "3600")]))
        for path, code in (("/denied", "access_denied"), ("/authenticate", "access_denied"), ("/limited", "rate_limited"), ("/failure", "network")):
            with self.subTest(path=path):
                with self.assertRaises(fy_net.NetError) as caught:
                    await fy_net.fetch_bytes("_core", "https://good.example" + path)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status, statuses[path])
                self.assertEqual(caught.exception.retry_after, 3600)
                self.assertNotIn("response body", caught.exception.message)
                self.assertNotIn("good.example", caught.exception.message)
        self.assertEqual(len(self.requests), len(statuses))

    async def test_binary_http_date_cooldown_is_parsed_and_capped(self):
        now = 1_700_000_000
        self.transport(lambda first, headers: (429, b"", [("Retry-After", formatdate(now + 8 * 86400, usegmt=True))]))
        with mock.patch.object(fy_net.time, "time", return_value=now):
            with self.assertRaises(fy_net.NetError) as caught:
                await fy_net.fetch_bytes("_core", "https://good.example/limited")
        self.assertEqual(caught.exception.retry_after, 7 * 86400)


class RetryAfterMetadata(unittest.TestCase):
    def test_delta_seconds_and_http_dates_have_a_seven_day_bound(self):
        now = 1_700_000_000
        for value, expected in (("0", 0), (" 3600\t", 3600), ("9" * 200, 7 * 86400),
                                (formatdate(now + 7200, usegmt=True), 7200),
                                (formatdate(now - 1, usegmt=True), 0)):
            with self.subTest(value=value):
                self.assertEqual(fy_net.retry_after_seconds(value, now=now), expected)
        self.assertEqual(fy_net.retry_after_seconds(formatdate(now + 1, usegmt=True), now=now + 0.25), 1)

    def test_missing_malformed_and_unbounded_values_are_not_server_cooldowns(self):
        for value in (None, 5, True, "", "-1", "+1", "1.5", "NaN", "Infinity", "x" * 257,
                      "60\r\nInjected: value", "\u00e9", "Tue, 14 Nov 2023 22:13:20"):
            with self.subTest(value=value):
                self.assertEqual(fy_net.retry_after_seconds(value, now=1_700_000_000), 0)
        for now in (float("nan"), float("inf"), True, "bad"):
            self.assertEqual(fy_net.retry_after_seconds("Tue, 14 Nov 2023 22:13:20 GMT", now=now), 0)

    def test_legacy_errors_keep_their_contract_and_optional_metadata_is_bounded(self):
        error = fy_net.NetError("network", "The connection failed")
        self.assertEqual((error.code, error.message, str(error)), ("network", "The connection failed", "The connection failed"))
        self.assertIsNone(error.status)
        self.assertIsNone(error.retry_after)
        for status in (True, "429", 99, 600):
            self.assertIsNone(fy_net.NetError("network", "failed", status=status).status)
        self.assertEqual(fy_net.NetError("rate_limited", "wait", retry_after=10**30).retry_after, 7 * 86400)
