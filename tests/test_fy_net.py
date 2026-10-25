import asyncio
import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_net

PUBLIC = "93.184.216.34"
BIG = os.urandom(900000)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code, body=b"", ctype="text/plain", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else b""
        path = self.path
        seen = {k.lower(): v for k, v in self.headers.items()}
        self.server.log.append((self.command, path, seen, data))
        if path.startswith("/ok"):
            self.reply(200, b"hello \xc3\xa9", "text/plain; charset=utf-8", [("ETag", '"x1"'), ("Set-Cookie", "sid=1")])
        elif path == "/json":
            self.reply(200, b'{"a":1}', "application/json")
        elif path == "/bin":
            self.reply(200, b"\xff\xfe\x00\x01", "application/octet-stream")
        elif path == "/badutf":
            self.reply(200, b"\xff\xfe", "text/plain")
        elif path == "/empty":
            self.reply(200, b"", "application/octet-stream")
        elif path == "/big":
            self.reply(200, BIG, "application/octet-stream")
        elif path == "/slow":
            time.sleep(3)
            self.reply(200, b"late")
        elif path == "/drip":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.3)
            except OSError:
                pass
        elif path == "/short":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"abc")
            self.wfile.flush()
            self.close_connection = True
        elif path == "/echo":
            self.reply(200, json.dumps({"headers": seen, "method": self.command, "body": data.decode("latin-1")}).encode(), "application/json")
        elif path.startswith("/to/"):
            code, _, target = path[4:].partition("/")
            self.reply(int(code), b"", extra=[("Location", target.replace("~", "/"))])
        elif path == "/self":
            self.reply(302, b"", extra=[("Location", "/self")])
        elif path == "/gz":
            self.reply(200, b"zz", "text/plain", [("Content-Encoding", "gzip")])
        else:
            self.reply(404, b"nope")

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_any


class Quiet(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


class Peer(socket.socket):
    pass


def local_opener(port, claim=PUBLIC):
    def opener(scheme, host, ip, p, timeout):
        s = socket.create_connection(("127.0.0.1", port), timeout)
        if claim is None:
            return s
        peer = Peer(s.family, s.type, s.proto, fileno=s.detach())
        peer.getpeername = lambda: (claim, p)
        return peer

    return opener


class Base(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Quiet(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server.log = []
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.log.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dirs = types.SimpleNamespace(runtime=Path(self.tmp.name) / "rt", data=Path(self.tmp.name) / "data", config=None, logs=None)
        self.names = {"good.test": PUBLIC, "other.test": "93.184.216.35", "loop.test": "127.0.0.1", "priv.test": "10.0.0.5", "mapped.test": "::ffff:127.0.0.1", "ll.test": "169.254.169.254", "v6loop.test": "::1", "mixed.test": None}

    def resolver(self, host, port):
        if host == "mixed.test":
            return [PUBLIC, "192.168.1.5"]
        return [self.names[host]]

    async def get(self, path, pid="fan", host="good.test", scheme="https", **kw):
        kw.setdefault("resolver", self.resolver)
        kw.setdefault("dirs", self.dirs)
        kw.setdefault("opener_factory", local_opener(self.port))
        return await fy_net.fetch_plugin(pid, "%s://%s%s" % (scheme, host, path), **kw)

    async def code(self, path, **kw):
        with self.assertRaises(fy_net.NetError) as c:
            await self.get(path, **kw)
        return c.exception.code


class IsPublic(unittest.TestCase):
    def test_table(self):
        yes = ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946", "::ffff:8.8.8.8", "64:ff9b::808:808"]
        no = [
            "127.0.0.1", "127.255.0.9", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.0.1", "169.254.169.254", "100.64.0.1", "100.127.255.255", "0.0.0.0",
            "224.0.0.1", "255.255.255.255", "240.0.0.1", "198.18.0.1", "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd12:3456::1", "ff02::1",
            "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:192.168.1.1", "::ffff:169.254.1.1", "::ffff:100.64.0.1", "::ffff:0.0.0.0", "::7f00:1", "2002:7f00:1::", "2002:0a00:1::",
            "64:ff9b::7f00:1", "64:ff9b::a00:1", "2001:db8::1", "fec0::1", "feff::1", "garbage", "", None, 5, "1.2.3",
        ]
        for ip in yes:
            with self.subTest(ip=ip):
                self.assertTrue(fy_net.is_public(ip))
        for ip in no:
            with self.subTest(ip=ip):
                self.assertFalse(fy_net.is_public(ip))


class Basics(Base):
    async def test_text_response_shape(self):
        r = await self.get("/ok")
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["text"], "hello \u00e9")
        self.assertIsNone(r["b64"])
        self.assertIsNone(r["path"])
        self.assertFalse(r["truncated"])
        self.assertEqual(r["bytes"], 8)
        self.assertEqual(r["headers"], {"content-type": "text/plain; charset=utf-8", "content-length": "8", "etag": '"x1"'})

    async def test_json_binary_badutf_empty_404(self):
        self.assertEqual((await self.get("/json"))["text"], '{"a":1}')
        r = await self.get("/bin")
        self.assertIsNone(r["text"])
        self.assertEqual(base64.b64decode(r["b64"]), b"\xff\xfe\x00\x01")
        r = await self.get("/badutf")
        self.assertIsNone(r["text"])
        self.assertEqual(base64.b64decode(r["b64"]), b"\xff\xfe")
        r = await self.get("/empty")
        self.assertEqual((r["text"], r["bytes"]), ("", 0))
        r = await self.get("/missing")
        self.assertEqual((r["status"], r["text"]), (404, "nope"))

    async def test_head_has_no_body(self):
        r = await self.get("/ok", method="head")
        self.assertEqual((r["status"], r["bytes"], r["text"]), (200, 0, ""))
        self.assertEqual(r["headers"]["content-length"], "8")

    async def test_methods_and_body(self):
        r = await self.get("/echo", method="POST", body="payload", headers={"X-A": "1"})
        j = json.loads(r["text"])
        self.assertEqual((j["method"], j["body"], j["headers"]["x-a"]), ("POST", "payload", "1"))
        for m in ("PUT", "DELETE"):
            self.assertEqual(json.loads((await self.get("/echo", method=m))["text"])["method"], m)
        self.assertEqual(await self.code("/echo", method="PATCH"), "bad_request")
        self.assertEqual(await self.code("/echo", method=5), "bad_request")
        self.assertEqual(await self.code("/echo", method="POST", body="x" * 262145), "too_large")
        self.assertEqual(await self.code("/echo", method="POST", body={"a": 1}), "bad_request")
        r = await self.get("/echo", method="POST", body=b"x" * 262144)
        self.assertEqual(len(json.loads(r["text"])["body"]), 262144)

    async def test_host_header_and_defaults(self):
        j = json.loads((await self.get("/echo", headers={"Host": "evil", "Connection": "upgrade", "Content-Length": "5"}))["text"])
        self.assertEqual(j["headers"]["host"], "good.test")
        self.assertNotEqual(j["headers"].get("content-length"), "5")
        j = json.loads((await self.get("/echo", scheme="http", allow_http=True))["text"])
        self.assertEqual(j["headers"]["host"], "good.test")
        self.assertEqual(j["headers"]["accept-encoding"], "identity")

    async def test_non_default_port_in_host_header(self):
        r = await fy_net.fetch_plugin("fan", "https://good.test:8443/echo", resolver=self.resolver, opener_factory=local_opener(self.port))
        self.assertEqual(json.loads(r["text"])["headers"]["host"], "good.test:8443")

    async def test_header_validation(self):
        bad = [{"Bad Name": "x"}, {"X": "a\r\nInjected: 1"}, {"X": "a\nb"}, {"X": "\u00e9"}, {"X": "v" * 1025}, {"X-" + "a" * 40: "v"}, {"": "v"}, {"X": 5}, {str(i): "v" for i in range(17)}, ["list"]]
        for h in bad:
            with self.subTest(h=h):
                self.assertEqual(await self.code("/echo", headers=h), "bad_request")
        ok = {"X" + str(i): "v" for i in range(16)}
        await self.get("/echo", headers=ok)
        await self.get("/echo", headers={"X": "tab\there"})

    async def test_bad_urls(self):
        for url in ("https://user:pw@good.test/", "https://user@good.test/", "https:///x", "https://good.test/a b", "https://good.test/\u00e9", "https://good.test/a\\b", "x" * 10, "https://" + "a" * 2100, "https://good.test:99999/", None, 5, "https://bad host/"):
            with self.subTest(url=url):
                with self.assertRaises(fy_net.NetError) as c:
                    await fy_net.fetch_plugin("fan", url, resolver=self.resolver)
                self.assertIn(c.exception.code, ("bad_url", "scheme"))

    async def test_scheme(self):
        for url in ("http://good.test/ok", "ftp://good.test/", "file:///etc/passwd", "gopher://good.test/", "javascript:alert(1)"):
            with self.subTest(url=url):
                with self.assertRaises(fy_net.NetError) as c:
                    await fy_net.fetch_plugin("fan", url, resolver=self.resolver)
                self.assertEqual(c.exception.code, "scheme")
        r = await self.get("/ok", scheme="http", allow_http=True)
        self.assertEqual(r["status"], 200)

    async def test_bad_pid(self):
        for pid in ("", "A", "has space", "x" * 33, None, "a/b"):
            with self.assertRaises(fy_net.NetError) as c:
                await self.get("/ok", pid=pid)
            self.assertEqual(c.exception.code, "bad_request")

    async def test_no_cookie_jar_and_no_credentials_added(self):
        await self.get("/ok")
        await self.get("/ok")
        self.assertTrue(all("cookie" not in seen and "authorization" not in seen for _, _, seen, _ in self.server.log))

    async def test_gzip_not_requested_and_not_decoded(self):
        r = await self.get("/gz", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r["text"], "zz")
        self.assertEqual(self.server.log[-1][2]["accept-encoding"], "identity")


class Limits(Base):
    async def test_inline_cap_truncates(self):
        r = await self.get("/big")
        self.assertEqual((r["bytes"], r["truncated"]), (524288, True))
        self.assertEqual(base64.b64decode(r["b64"]), BIG[:524288])
        r = await self.get("/big", max_bytes=1000)
        self.assertEqual((r["bytes"], r["truncated"]), (1000, True))
        self.assertEqual(base64.b64decode(r["b64"]), BIG[:1000])
        r = await self.get("/big", max_bytes=10**9)
        self.assertEqual(r["bytes"], 524288)
        r = await self.get("/big", max_bytes=-5)
        self.assertEqual(r["bytes"], 524288)

    async def test_exact_size_is_not_truncated(self):
        r = await self.get("/ok", max_bytes=8)
        self.assertEqual((r["bytes"], r["truncated"]), (8, False))
        r = await self.get("/ok", max_bytes=7)
        self.assertEqual((r["bytes"], r["truncated"]), (7, True))

    async def test_truncated_text_cut_inside_a_character(self):
        r = await self.get("/ok", max_bytes=7)
        self.assertEqual(r["text"], "hello ")

    async def test_response_fits_a_protocol_line(self):
        r = await self.get("/big")
        self.assertLess(len(json.dumps(r)), 786432)

    async def test_timeout_when_server_stalls(self):
        t = time.monotonic()
        self.assertEqual(await self.code("/slow", timeout=1), "timeout")
        self.assertLess(time.monotonic() - t, 2.5)

    async def test_timeout_is_total_not_per_read(self):
        t = time.monotonic()
        self.assertEqual(await self.code("/drip", timeout=1), "timeout")
        self.assertLess(time.monotonic() - t, 2.5)

    async def test_timeout_clamped(self):
        t = time.monotonic()
        self.assertEqual(await self.code("/slow", timeout=0.01), "timeout")
        self.assertGreater(time.monotonic() - t, 0.9)
        r = await self.get("/ok", timeout="junk")
        self.assertEqual(r["status"], 200)

    async def test_connection_closed_early_is_an_error(self):
        self.assertEqual(await self.code("/short"), "network")

    async def test_connection_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "https://good.test/", resolver=self.resolver, opener_factory=local_opener(port))
        self.assertEqual(c.exception.code, "network")

    async def test_concurrency_per_plugin_and_total(self):
        slow = [asyncio.create_task(self.get("/slow", timeout=2, pid="fan")) for _ in range(2)]
        await asyncio.sleep(0.3)
        self.assertEqual(await self.code("/ok", pid="fan"), "busy")
        other = [asyncio.create_task(self.get("/slow", timeout=2, pid=p)) for p in ("a", "b")]
        await asyncio.sleep(0.3)
        self.assertEqual(await self.code("/ok", pid="c"), "busy")
        await asyncio.gather(*slow, *other, return_exceptions=True)
        self.assertEqual((await self.get("/ok", pid="fan"))["status"], 200)
        self.assertEqual(fy_net._live, {})

    async def test_slot_released_after_error(self):
        for _ in range(5):
            await self.code("/nothing-here", host="priv.test")
        self.assertEqual(fy_net._live, {})


class Addresses(Base):
    async def test_resolver_answers_are_blocked(self):
        for host in ("loop.test", "priv.test", "mapped.test", "ll.test", "v6loop.test", "mixed.test"):
            with self.subTest(host=host):
                self.assertEqual(await self.code("/ok", host=host), "blocked_address")
        self.assertEqual(self.server.log, [])

    async def test_literal_ips_blocked_without_asking_the_resolver(self):
        asked = []

        def resolver(h, p):
            asked.append(h)
            return [PUBLIC]

        for host in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "[::1]", "[::ffff:127.0.0.1]", "[fe80::1]", "100.64.0.1", "0.0.0.0"):
            with self.subTest(host=host):
                with self.assertRaises(fy_net.NetError) as c:
                    await fy_net.fetch_plugin("fan", "https://%s:8080/json" % host, resolver=resolver, opener_factory=local_opener(self.port))
                self.assertEqual(c.exception.code, "blocked_address")
        self.assertEqual(asked, [])
        self.assertEqual(self.server.log, [])

    async def test_resolver_failure_is_a_network_error(self):
        def boom(h, p):
            raise OSError("dns")

        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "https://good.test/", resolver=boom)
        self.assertEqual(c.exception.code, "network")
        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "https://good.test/", resolver=lambda h, p: [])
        self.assertEqual(c.exception.code, "blocked_address")

    async def test_connected_peer_checked_against_rebinding(self):
        answers = iter([[PUBLIC], ["127.0.0.1"]])
        r = await fy_net.fetch_plugin("fan", "https://good.test/ok", resolver=lambda h, p: next(answers), opener_factory=local_opener(self.port))
        self.assertEqual(r["status"], 200)
        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "https://good.test/ok", resolver=lambda h, p: [PUBLIC], opener_factory=local_opener(self.port, claim=None))
        self.assertEqual(c.exception.code, "blocked_address")
        self.assertEqual(len(self.server.log), 1)

    async def test_peer_check_runs_after_the_connection_is_made(self):
        closed = []

        class Spy(Peer):
            def close(self):
                closed.append(1)
                super().close()

        def opener(scheme, host, ip, port, timeout):
            s = socket.create_connection(("127.0.0.1", self.port), timeout)
            return Spy(s.family, s.type, s.proto, fileno=s.detach())

        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "https://good.test/ok", resolver=lambda h, p: [PUBLIC], opener_factory=opener)
        self.assertEqual(c.exception.code, "blocked_address")
        self.assertTrue(closed)
        self.assertEqual(self.server.log, [])

    async def test_default_resolver_blocks_localhost_name(self):
        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "https://localhost:%d/json" % self.port)
        self.assertEqual(c.exception.code, "blocked_address")
        self.assertEqual(self.server.log, [])

    async def test_default_connect_path_blocks_loopback_peer(self):
        with self.assertRaises(fy_net.NetError) as c:
            await fy_net.fetch_plugin("fan", "http://127.0.0.1:%d/json" % self.port, allow_http=True)
        self.assertEqual(c.exception.code, "blocked_address")
        with mock.patch.object(fy_net, "is_public", lambda ip: True):
            with self.assertRaises(fy_net.NetError) as c:
                await fy_net.fetch_plugin("fan", "https://127.0.0.1:%d/json" % self.port, timeout=3)
            self.assertEqual(c.exception.code, "network")
        self.assertEqual(self.server.log, [])
        with mock.patch.object(fy_net, "is_public", lambda ip: ip == "127.0.0.1"):
            r = await fy_net.fetch_plugin("fan", "http://127.0.0.1:%d/json" % self.port, allow_http=True)
        self.assertEqual(r["text"], '{"a":1}')

    async def test_tries_next_address_when_one_refuses(self):
        calls = []

        def opener(scheme, host, ip, port, timeout):
            calls.append(ip)
            if ip == PUBLIC:
                raise ConnectionRefusedError()
            return local_opener(self.port)(scheme, host, ip, port, timeout)

        r = await fy_net.fetch_plugin("fan", "https://good.test/ok", resolver=lambda h, p: [PUBLIC, "93.184.216.35"], opener_factory=opener)
        self.assertEqual((r["status"], calls), (200, [PUBLIC, "93.184.216.35"]))


class Redirects(Base):
    def target(self, code, url):
        return "/to/%d/%s" % (code, url.replace("/", "~"))

    async def test_same_origin_keeps_authorization(self):
        r = await self.get(self.target(302, "https://good.test/echo"), headers={"Authorization": "Bearer s3", "Cookie": "a=b"})
        h = json.loads(r["text"])["headers"]
        self.assertEqual((h["authorization"], h["cookie"]), ("Bearer s3", "a=b"))

    async def test_relative_redirect_keeps_authorization(self):
        r = await self.get(self.target(301, "/echo"), headers={"Authorization": "Bearer s3"})
        self.assertEqual(json.loads(r["text"])["headers"]["authorization"], "Bearer s3")

    async def test_cross_host_drops_authorization_and_cookie(self):
        r = await self.get(self.target(302, "https://other.test/echo"), headers={"Authorization": "Bearer s3", "Cookie": "a=b", "Proxy-Authorization": "x", "X-Api-Key": "k", "X-Auth-Token": "t", "X-Keep": "yes", "Accept": "text/plain", "User-Agent": "fy"})
        h = json.loads(r["text"])["headers"]
        for name in ("authorization", "cookie", "proxy-authorization", "x-api-key", "x-auth-token", "x-keep"):
            self.assertNotIn(name, h)
        self.assertEqual((h["accept"], h["user-agent"], h["host"]), ("text/plain", "fy", "other.test"))
        first = self.server.log[0][2]
        self.assertEqual(first["authorization"], "Bearer s3")

    async def test_cross_port_drops_authorization(self):
        r = await self.get(self.target(302, "https://good.test:444/echo"), headers={"Authorization": "Bearer s3"})
        self.assertNotIn("authorization", json.loads(r["text"])["headers"])

    async def test_scheme_change_drops_authorization_when_http_allowed(self):
        r = await self.get(self.target(302, "http://good.test/echo"), headers={"Authorization": "Bearer s3"}, allow_http=True)
        self.assertNotIn("authorization", json.loads(r["text"])["headers"])

    async def test_dropped_credentials_stay_dropped_on_return(self):
        mid = self.target(302, "https://good.test/echo").replace("/", "~")
        r = await self.get(self.target(302, "https://other.test/to/302/" + mid), headers={"Authorization": "Bearer s3"})
        self.assertNotIn("authorization", json.loads(r["text"])["headers"])

    async def test_redirect_to_loopback_blocked(self):
        for host in ("loop.test", "priv.test", "ll.test", "127.0.0.1", "[::1]", "[::ffff:127.0.0.1]"):
            with self.subTest(host=host):
                self.assertEqual(await self.code(self.target(302, "https://%s/echo" % host)), "blocked_address")
        self.assertTrue(all(p.startswith("/to/") for _, p, _, _ in self.server.log))

    async def test_redirect_to_http_blocked_by_default(self):
        self.assertEqual(await self.code(self.target(302, "http://good.test/echo")), "redirect_blocked")
        self.assertEqual(await self.code(self.target(302, "ftp://good.test/echo")), "redirect_blocked")
        self.assertEqual(await self.code(self.target(302, "file:///etc/passwd")), "redirect_blocked")
        self.assertEqual(await self.code(self.target(302, "https://u:p@good.test/echo")), "redirect_blocked")

    async def test_redirect_to_http_allowed_with_flag(self):
        r = await self.get(self.target(302, "http://good.test/ok"), allow_http=True)
        self.assertEqual(r["status"], 200)

    async def test_redirect_limit(self):
        self.assertEqual(await self.code("/self"), "redirect_blocked")
        self.assertEqual(sum(1 for _, p, _, _ in self.server.log if p == "/self"), 6)

    async def test_five_hops_ok(self):
        url = "/echo"
        for _ in range(5):
            url = self.target(302, "https://good.test" + url)
        self.assertEqual((await self.get(url))["status"], 200)

    async def test_post_becomes_get_on_302_and_keeps_on_307(self):
        r = await self.get(self.target(302, "https://good.test/echo"), method="POST", body="data", headers={"Content-Type": "text/plain"})
        j = json.loads(r["text"])
        self.assertEqual((j["method"], j["body"], "content-type" in j["headers"]), ("GET", "", False))
        r = await self.get(self.target(307, "https://good.test/echo"), method="POST", body="data")
        j = json.loads(r["text"])
        self.assertEqual((j["method"], j["body"]), ("POST", "data"))

    async def test_body_not_replayed_to_another_host(self):
        self.assertEqual(await self.code(self.target(307, "https://other.test/echo"), method="POST", body="secret"), "redirect_blocked")
        self.assertEqual([p for _, p, _, _ in self.server.log if p == "/echo"], [])

    async def test_redirect_without_location_is_returned(self):
        r = await self.get("/to/302/", )
        self.assertEqual(r["status"], 302)


class Dest(Base):
    async def test_streams_to_runtime_file(self):
        r = await self.get("/big", dest="runtime:big.bin")
        p = Path(r["path"])
        self.assertEqual(p, self.dirs.runtime / "big.bin")
        self.assertEqual(p.read_bytes(), BIG)
        self.assertEqual((r["bytes"], r["text"], r["b64"], r["truncated"]), (len(BIG), None, None, False))
        self.assertEqual(os.listdir(self.dirs.runtime), ["big.bin"])

    async def test_data_dest_and_replace(self):
        await self.get("/ok", dest="data:x.txt")
        r = await self.get("/json", dest="data:x.txt")
        self.assertEqual(Path(r["path"]).read_text(), '{"a":1}')
        self.assertEqual(os.listdir(self.dirs.data), ["x.txt"])

    async def test_quota_counts_the_other_files_and_cuts_a_download_that_would_exceed_it(self):
        self.dirs.data.mkdir(parents=True)
        (self.dirs.data / "old.bin").write_bytes(b"x" * 1000)
        with mock.patch.object(fy_net, "QUOTA", 1000 + 500000):
            self.assertEqual(await self.code("/big", dest="data:big.bin"), "quota")
            self.assertEqual(sorted(os.listdir(self.dirs.data)), ["old.bin"])
            r = await self.get("/ok", dest="data:small.txt")
            self.assertEqual(r["bytes"], 8)
        self.assertEqual((self.dirs.data / "old.bin").read_bytes(), b"x" * 1000)

    async def test_a_download_may_replace_its_own_earlier_copy_inside_the_quota(self):
        self.dirs.data.mkdir(parents=True)
        (self.dirs.data / "x.bin").write_bytes(b"y" * 400000)
        with mock.patch.object(fy_net, "QUOTA", 950000):
            r = await self.get("/big", dest="data:x.bin")
        self.assertEqual(r["bytes"], len(BIG))
        self.assertEqual((self.dirs.data / "x.bin").read_bytes(), BIG)

    async def test_a_full_folder_and_a_crowded_folder_refuse_before_any_download(self):
        self.dirs.data.mkdir(parents=True)
        (self.dirs.data / "a.bin").write_bytes(b"x" * 1000)
        with mock.patch.object(fy_net, "QUOTA", 1000):
            self.assertEqual(await self.code("/ok", dest="data:b.txt"), "quota")
        sub = self.dirs.data / "deep"
        sub.mkdir()
        for i in range(4):
            (sub / ("f%d" % i)).write_bytes(b"")
        with mock.patch.object(fy_net, "QUOTA_FILES", 3):
            self.assertEqual(await self.code("/ok", dest="data:b.txt"), "quota")
        self.assertEqual(sorted(os.listdir(self.dirs.data)), ["a.bin", "deep"])

    async def test_the_default_quota_is_64_mb_and_the_per_file_cap_stays_20_mb(self):
        self.assertEqual((fy_net.QUOTA, fy_net.DEST_MAX), (64 * 1024 * 1024, 20 * 1024 * 1024))

    async def test_cap_cancels_and_removes_part(self):
        with mock.patch.object(fy_net, "DEST_MAX", 100000):
            with self.assertRaises(fy_net.NetError) as c:
                await self.get("/big", dest="runtime:big.bin")
        self.assertEqual(c.exception.code, "too_large")
        self.assertEqual(os.listdir(self.dirs.runtime), [])

    async def test_cap_keeps_existing_file(self):
        await self.get("/ok", dest="runtime:keep.bin")
        with mock.patch.object(fy_net, "DEST_MAX", 3):
            with self.assertRaises(fy_net.NetError):
                await self.get("/ok", dest="runtime:keep.bin")
        self.assertEqual((self.dirs.runtime / "keep.bin").read_bytes(), b"hello \xc3\xa9")
        self.assertEqual(os.listdir(self.dirs.runtime), ["keep.bin"])

    async def test_interrupted_download_leaves_nothing(self):
        self.assertEqual(await self.code("/short", dest="runtime:cut.bin"), "network")
        self.assertEqual(os.listdir(self.dirs.runtime), [])

    async def test_timeout_leaves_nothing(self):
        self.assertEqual(await self.code("/drip", dest="runtime:cut.bin", timeout=1), "timeout")
        self.assertEqual(os.listdir(self.dirs.runtime), [])

    async def test_redirect_creates_nothing_extra(self):
        r = await self.get("/to/302/https:~~good.test~ok", dest="runtime:r.txt")
        self.assertEqual(Path(r["path"]).read_bytes(), b"hello \xc3\xa9")
        self.assertEqual(os.listdir(self.dirs.runtime), ["r.txt"])

    async def test_bad_dest_names(self):
        for dest in ("runtime:../x", "runtime:..", "runtime:.", "runtime:a/b", "runtime:a\\b", "runtime:", "other:x", "x", "runtime:" + "a" * 65, "/etc/passwd", 5, "runtime:a b", "config:x", "logs:x"):
            with self.subTest(dest=dest):
                self.assertEqual(await self.code("/ok", dest=dest), "bad_dest")
        self.assertEqual(self.server.log, [])
        self.assertEqual(await self.code("/ok", dest="runtime:x", dirs=None), "bad_dest")
        await self.get("/ok", dest="runtime:" + "a" * 64)

    async def test_dest_over_existing_directory_refused(self):
        (self.dirs.runtime / "d").mkdir(parents=True)
        self.assertEqual(await self.code("/ok", dest="runtime:d"), "bad_dest")

    async def test_stale_part_is_replaced(self):
        self.dirs.runtime.mkdir(parents=True)
        (self.dirs.runtime / "s.bin.part").write_bytes(b"old")
        await self.get("/ok", dest="runtime:s.bin")
        self.assertEqual(os.listdir(self.dirs.runtime), ["s.bin"])

    async def test_part_symlink_not_followed(self):
        self.dirs.runtime.mkdir(parents=True)
        target = Path(self.tmp.name) / "victim"
        target.write_text("keep")
        try:
            os.symlink(target, self.dirs.runtime / "v.bin.part")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted here")
        await self.get("/ok", dest="runtime:v.bin")
        self.assertEqual(target.read_text(), "keep")

    async def test_dest_file_mode(self):
        r = await self.get("/ok", dest="runtime:m.bin")
        if os.name == "posix":
            self.assertEqual(os.stat(r["path"]).st_mode & 0o777, 0o600)

    async def test_dest_limit_ignores_inline_cap(self):
        r = await self.get("/big", dest="runtime:b.bin", max_bytes=10)
        self.assertEqual(r["bytes"], len(BIG))


class Adapter(Base):
    def host(self, dev=False):
        return types.SimpleNamespace(HOME=Path(self.tmp.name), settings=lambda: {"prefs": {"dev": {"enabled": dev}}}, dirs=lambda pid: self.dirs)

    async def test_core_method_returns_error_dicts_and_ignores_dest(self):
        r = await fy_net.METHODS["fetch"](self.host(), {"pid": "fan", "url": "http://good.test/x"})
        self.assertEqual(r["code"], "scheme")
        self.assertIn("error", r)
        r = await fy_net.METHODS["fetch"](self.host(), {"pid": "fan", "url": "https://good.test/x", "dest": "runtime:evil"})
        self.assertNotEqual(r.get("code"), "bad_dest")
        self.assertFalse((self.dirs.runtime / "evil").exists())
        r = await fy_net.METHODS["fetch"](self.host(), "junk")
        self.assertEqual(r["code"], "bad_request")
        r = await fy_net.METHODS["fetch"](self.host(), {"url": "https://good.test/x"})
        self.assertEqual(r["code"], "bad_request")

    async def test_hostcall_dest_and_dev_flag(self):
        with mock.patch.object(fy_net, "fetch_plugin", mock.AsyncMock(return_value={"ok": 1})) as m:
            await fy_net.hostcall(self.host(dev=True), "fan", {"url": "u", "dest": "data:x", "timeout": 5, "max_bytes": 9, "method": "PUT", "body": "b", "headers": {"A": "b"}})
            kw = m.call_args.kwargs
            self.assertEqual((kw["allow_http"], kw["dest"], kw["dirs"], kw["timeout"], kw["max_bytes"], kw["method"]), (True, "data:x", self.dirs, 5, 9, "PUT"))
            await fy_net.hostcall(self.host(dev=False), "fan", {})
            self.assertFalse(m.call_args.kwargs["allow_http"])
        broken = self.host()
        broken.settings = mock.Mock(side_effect=RuntimeError)
        with mock.patch.object(fy_net, "fetch_plugin", mock.AsyncMock(return_value={})) as m:
            await fy_net.hostcall(broken, "fan", {})
            self.assertFalse(m.call_args.kwargs["allow_http"])


class RealTls(unittest.IsolatedAsyncioTestCase):
    async def test_default_connect_path_with_tls(self):
        import shutil
        import ssl
        import subprocess

        if not shutil.which("openssl"):
            self.skipTest("openssl not available")
        with tempfile.TemporaryDirectory() as d:
            key, crt = os.path.join(d, "k.pem"), os.path.join(d, "c.pem")
            done = subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt, "-days", "2", "-subj", "/CN=good.test", "-addext", "subjectAltName=DNS:good.test"],
                capture_output=True,
            )
            if done.returncode:
                self.skipTest("openssl could not make a certificate")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt, key)
            srv = Quiet(("127.0.0.1", 0), Handler)
            srv.log = []
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
            port = srv.server_address[1]
            trust = ssl.create_default_context(cafile=crt)
            real = socket.create_connection

            def create(addr, timeout=None, *a, **k):
                return real(("127.0.0.1", port), timeout)

            with mock.patch.object(fy_net.socket, "create_connection", create), mock.patch.object(fy_net.ssl, "create_default_context", lambda: trust), mock.patch.object(fy_net, "is_public", lambda ip: True):
                r = await fy_net.fetch_plugin("fan", "https://good.test:%d/ok" % port, resolver=lambda h, p: [PUBLIC])
                self.assertEqual((r["status"], r["text"]), (200, "hello \u00e9"))
                self.assertEqual(srv.log[0][2]["host"], "good.test:%d" % port)

            untrusting = ssl.create_default_context()
            with mock.patch.object(fy_net.socket, "create_connection", create), mock.patch.object(fy_net.ssl, "create_default_context", lambda: untrusting), mock.patch.object(fy_net, "is_public", lambda ip: True):
                with self.assertRaises(fy_net.NetError) as c:
                    await fy_net.fetch_plugin("fan", "https://good.test:%d/ok" % port, resolver=lambda h, p: [PUBLIC])
                self.assertEqual(c.exception.code, "network")


def serve_script(test, script):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    test.addCleanup(srv.close)

    def run():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=script, args=(c,), daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def read_head(c):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = c.recv(65536)
        if not chunk:
            break
        data += chunk
    return data


def trickle(gone=None):
    def script(c):
        read_head(c)
        try:
            c.sendall(b"HTTP/1.1 200 OK\r\nX-A: ")
            while True:
                time.sleep(0.2)
                c.sendall(b"a")
        except OSError:
            if gone is not None:
                gone.set()
        finally:
            c.close()

    return script


class Hardening(Base):
    async def test_non_finite_timeouts_use_the_default(self):
        seen = []
        original = asyncio.wait_for

        async def spy(aw, timeout):
            seen.append(timeout)
            return await original(aw, timeout)

        with mock.patch.object(asyncio, "wait_for", spy):
            for t in (float("nan"), float("inf"), float("-inf"), 10**400, "x", None):
                await self.get("/ok", timeout=t)
            await self.get("/ok", timeout=5)
        self.assertEqual(seen, [17.0] * 6 + [7.0])

    async def test_closing_responses_are_read_to_the_end(self):
        bodies = {
            "http10": b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\nhello",
            "http10_len": b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nhello",
            "close_len": b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello",
            "close_eof": b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nhello",
            "close_chunked": b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
        }
        for name, payload in bodies.items():
            def script(c, payload=payload):
                read_head(c)
                c.sendall(payload)
                c.close()

            port = serve_script(self, script)
            with self.subTest(name=name):
                r = await self.get("/x", opener_factory=local_opener(port))
                self.assertEqual((r["status"], r["text"], r["bytes"]), (200, "hello", 5))
                r = await self.get("/x", opener_factory=local_opener(port), dest="data:%s.bin" % name)
                self.assertEqual(Path(r["path"]).read_bytes(), b"hello")

    async def test_closing_response_cut_short_is_still_an_error(self):
        def script(c):
            read_head(c)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\nConnection: close\r\n\r\nabc")
            c.close()

        port = serve_script(self, script)
        with self.assertRaises(fy_net.NetError) as c:
            await self.get("/x", opener_factory=local_opener(port))
        self.assertEqual(c.exception.code, "network")

    async def test_header_trickle_is_cut_at_the_deadline_and_frees_the_thread(self):
        gone = threading.Event()
        port = serve_script(self, trickle(gone))
        t = time.monotonic()
        self.assertEqual(await self.code("/x", opener_factory=local_opener(port), timeout=1), "timeout")
        self.assertLess(time.monotonic() - t, 4.5)
        self.assertTrue(await asyncio.to_thread(gone.wait, 3))
        self.assertEqual(fy_net._executor()._max_workers, 4)

    async def test_stuck_fetches_do_not_starve_the_next_one_or_the_default_executor(self):
        port = serve_script(self, trickle())
        stuck = [asyncio.create_task(self.get("/x", pid=p, opener_factory=local_opener(port), timeout=1)) for p in ("a", "b", "c", "d")]
        done = await asyncio.gather(*stuck, return_exceptions=True)
        self.assertTrue(all(isinstance(x, fy_net.NetError) and x.code == "timeout" for x in done))
        self.assertEqual(await asyncio.wait_for(asyncio.to_thread(lambda: 7), 1), 7)
        r = await self.get("/ok", timeout=5)
        self.assertEqual(r["status"], 200)
        self.assertEqual(fy_net._live, {})

    async def test_expired_deadline_stops_before_resolving(self):
        asked = []

        def resolver(h, p):
            asked.append(h)
            return [PUBLIC]

        with self.assertRaises(fy_net.NetError) as c:
            fy_net._run("https://good.test/ok", "GET", {}, None, time.monotonic() - 1, 1000, None, False, resolver, None, [])
        self.assertEqual(c.exception.code, "timeout")
        self.assertEqual(asked, [])

    async def test_a_stuck_lookup_is_cut_at_the_deadline_and_frees_the_fetch_thread(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck(h, p):
            release.wait(10)
            return [PUBLIC]

        t = time.monotonic()
        self.assertEqual(await self.code("/ok", resolver=stuck, timeout=1), "timeout")
        self.assertLess(time.monotonic() - t, 4)
        self.assertEqual(fy_net._live, {})
        self.assertEqual((await self.get("/ok", pid="other", timeout=5))["status"], 200)
        self.assertEqual(await asyncio.wait_for(asyncio.to_thread(lambda: 5), 1), 5)

    async def test_every_resolver_thread_stuck_still_ends_in_timeouts_and_recovers(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck(h, p):
            release.wait(10)
            return [PUBLIC]

        done = await asyncio.gather(*(self.get("/ok", pid=p, resolver=stuck, timeout=1) for p in ("a", "b", "c", "d")), return_exceptions=True)
        self.assertTrue(all(isinstance(x, fy_net.NetError) and x.code == "timeout" for x in done))
        self.assertEqual(await self.code("/ok", timeout=1), "timeout")
        self.assertEqual(fy_net._live, {})
        release.set()
        await asyncio.sleep(0.3)
        self.assertEqual((await self.get("/ok", timeout=5))["status"], 200)

    def test_a_lookup_without_a_deadline_is_still_bounded(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(fy_net, "DNS_MAX", 0.3):
            with self.assertRaises(fy_net.NetError) as c:
                fy_net._resolve("slow.test", 443, lambda h, p: release.wait(10))
        self.assertEqual(c.exception.code, "timeout")
        self.assertEqual(fy_net._resolve("fast.test", 443, lambda h, p: [PUBLIC]), [PUBLIC])
        self.assertEqual(fy_net._resolve("93.184.216.34", 443, None), [PUBLIC])

    def test_abort_wakes_a_blocked_reader(self):
        a, b = socket.socketpair()
        self.addCleanup(b.close)
        got = []

        def block():
            try:
                got.append(a.recv(10))
            except OSError:
                got.append(None)

        t = threading.Thread(target=block)
        t.start()
        time.sleep(0.2)
        fy_net._abort([a])
        t.join(3)
        self.assertFalse(t.is_alive())


if __name__ == "__main__":
    unittest.main()
