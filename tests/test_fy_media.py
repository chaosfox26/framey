import asyncio
import base64
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_media
import fy_proto

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
VIDEO = bytes(range(256)) * 40


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def http(url, method="GET", headers=None, raw=None):
    m = re.match(r"http://127\.0\.0\.1:(\d+)(/.*)", url)
    r, w = await asyncio.open_connection("127.0.0.1", int(m[1]))
    head = raw if raw is not None else "%s %s HTTP/1.1\r\nHost: x\r\n%s\r\n" % (method, m[2], "".join("%s: %s\r\n" % kv for kv in (headers or {}).items()))
    w.write(head.encode("latin-1"))
    await w.drain()
    data = await asyncio.wait_for(r.read(), 5)
    w.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    hdrs = {k.lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
    return int(lines[0].split(" ")[1]), hdrs, body


async def refused(port):
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
    except (ConnectionError, OSError):
        return True
    w.close()
    return False


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        (self.dir / "a.png").write_bytes(PNG)
        (self.dir / "v.mp4").write_bytes(VIDEO)
        (self.dir / "w.webm").write_bytes(VIDEO)
        (self.dir / "t.txt").write_text("hi é")
        (self.dir / "empty.mp4").write_bytes(b"")
        self.clock = Clock()
        self.media = fy_media.Media(clock=self.clock, data_max=1000)
        self.addAsyncCleanup(self.media.close)

    def port(self, r):
        return int(re.search(r":(\d+)/", r["url"])[1])


class DataTier(Base):
    async def test_small_file_is_a_data_url(self):
        m = fy_media.Media(clock=self.clock)
        r = await m.url_for("fan", self.dir / "a.png")
        self.assertEqual((r["kind"], r["token"]), ("data", None))
        head, _, b64 = r["url"].partition(",")
        self.assertEqual(head, "data:image/png;base64")
        self.assertEqual(base64.b64decode(b64), PNG)
        self.assertAlmostEqual(r["expires"], time.time() + 600, delta=5)
        self.assertIsNone(m._server)
        self.assertIsNone(m._handle)
        self.assertEqual(m._tokens, {})

    async def test_mime_allowlist(self):
        m = fy_media.Media()
        for name, mime in (("a.PNG", "image/png"), ("a.jpg", "image/jpeg"), ("a.jpeg", "image/jpeg"), ("a.webp", "image/webp"), ("a.gif", "image/gif"), ("a.svg", "image/svg+xml"), ("a.mp3", "audio/mpeg"), ("a.ogg", "audio/ogg"), ("a.wav", "audio/wav"), ("a.mp4", "video/mp4"), ("a.webm", "video/webm"), ("a.json", "application/json"), ("a.txt", "text/plain")):
            (self.dir / name).write_bytes(b"x")
            self.assertTrue((await m.url_for("p", self.dir / name))["url"].startswith("data:%s;base64," % mime), name)
        for name in ("a.html", "a.js", "a.exe", "a.py", "a", "a.png.html", "a.htm", "a.xml"):
            (self.dir / name).write_bytes(b"x")
            with self.assertRaises(fy_media.MediaError) as c:
                await m.url_for("p", self.dir / name)
            self.assertEqual(c.exception.code, "bad_type", name)

    async def test_missing_dir_and_device(self):
        for p, code in ((self.dir / "none.png", "not_found"), (self.dir, "bad_type")):
            with self.assertRaises(fy_media.MediaError) as c:
                await self.media.url_for("p", p)
            self.assertEqual(c.exception.code, code)
        (self.dir / "folder.png").mkdir()
        with self.assertRaises(fy_media.MediaError) as c:
            await self.media.url_for("p", self.dir / "folder.png")
        self.assertEqual(c.exception.code, "not_found")
        self.assertIsNone(self.media._server)

    async def test_boundary_at_data_max(self):
        (self.dir / "e.png").write_bytes(b"x" * 1000)
        (self.dir / "f.png").write_bytes(b"x" * 1001)
        self.assertEqual((await self.media.url_for("p", self.dir / "e.png"))["kind"], "data")
        self.assertEqual((await self.media.url_for("p", self.dir / "f.png"))["kind"], "http")

    async def test_default_data_max_is_one_mib(self):
        m = fy_media.Media()
        (self.dir / "e.png").write_bytes(b"x" * 1048576)
        (self.dir / "f.png").write_bytes(b"x" * 1048577)
        self.assertEqual((await m.url_for("p", self.dir / "e.png"))["kind"], "data")
        r = await m.url_for("p", self.dir / "f.png")
        self.assertEqual(r["kind"], "http")
        await m.close()


class NonFinite(Base):
    async def test_nan_and_infinite_ttl_fall_back_without_raising(self):
        for ttl in (float("nan"), float("inf"), float("-inf"), 10**400):
            with self.subTest(ttl=ttl):
                r = await self.media.url_for("fan", self.dir / "empty.mp4", ttl=ttl)
                self.assertEqual(r["kind"], "data")
                r = await self.media.url_for("fan", self.dir / "v.mp4", ttl=ttl)
                self.assertEqual(r["kind"], "http")
                self.assertTrue(math.isfinite(r["expires"]))


class Http(Base):
    async def test_serves_whole_file_with_safe_headers(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        self.assertEqual(r["kind"], "http")
        self.assertRegex(r["url"], r"^http://127\.0\.0\.1:\d+/m/[0-9a-f]{32}$")
        self.assertEqual(r["url"].rsplit("/", 1)[1], r["token"])
        status, h, body = await http(r["url"])
        self.assertEqual((status, body), (200, VIDEO))
        self.assertEqual(h["content-type"], "video/mp4")
        self.assertEqual(h["content-length"], str(len(VIDEO)))
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(h["access-control-allow-origin"], "*")
        self.assertEqual(h["accept-ranges"], "bytes")
        self.assertEqual(h["content-security-policy"], "sandbox")

    async def test_server_binds_loopback_only_and_starts_lazily(self):
        self.assertIsNone(self.media._server)
        await self.media.url_for("fan", self.dir / "v.mp4")
        self.assertEqual({s.getsockname()[0] for s in self.media._server.sockets}, {"127.0.0.1"})

    async def test_text_charset(self):
        m = fy_media.Media(clock=self.clock, data_max=1)
        r = await m.url_for("p", self.dir / "t.txt")
        self.assertEqual((await http(r["url"]))[1]["content-type"], "text/plain; charset=utf-8")
        await m.close()

    async def test_ranges(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        n = len(VIDEO)
        cases = [
            ("bytes=0-9", 206, VIDEO[0:10], "bytes 0-9/%d" % n),
            ("bytes=100-", 206, VIDEO[100:], "bytes 100-%d/%d" % (n - 1, n)),
            ("bytes=-50", 206, VIDEO[-50:], "bytes %d-%d/%d" % (n - 50, n - 1, n)),
            ("bytes=%d-%d" % (n - 5, n + 500), 206, VIDEO[-5:], "bytes %d-%d/%d" % (n - 5, n - 1, n)),
            ("bytes=0-0", 206, VIDEO[:1], "bytes 0-0/%d" % n),
            ("bytes=-%d" % (n + 50), 206, VIDEO, "bytes 0-%d/%d" % (n - 1, n)),
        ]
        for rng, code, body, crange in cases:
            with self.subTest(rng=rng):
                status, h, got = await http(r["url"], headers={"Range": rng})
                self.assertEqual((status, got, h["content-range"]), (code, body, crange))
                self.assertEqual(h["content-length"], str(len(body)))
        for rng in ("bytes=%d-" % n, "bytes=9-3", "bytes=-0", "bytes=%d-%d" % (n + 1, n + 9)):
            with self.subTest(rng=rng):
                status, h, got = await http(r["url"], headers={"Range": rng})
                self.assertEqual((status, got, h["content-range"]), (416, b"", "bytes */%d" % n))
        for rng in ("bytes=0-1,5-6", "items=0-5", "bytes=-", "garbage", ""):
            with self.subTest(rng=rng):
                status, _, got = await http(r["url"], headers={"Range": rng})
                self.assertEqual((status, got), (200, VIDEO))

    async def test_empty_file(self):
        r = await self.media.url_for("fan", self.dir / "empty.mp4")
        data = await self.media.url_for("fan", self.dir / "empty.mp4")
        self.assertEqual(data["kind"], "data")
        m = fy_media.Media(clock=self.clock, data_max=-1)
        r = await m.url_for("fan", self.dir / "empty.mp4")
        self.assertEqual((await http(r["url"]))[::2], (200, b""))
        self.assertEqual((await http(r["url"], headers={"Range": "bytes=0-5"}))[0], 416)
        await m.close()

    async def test_token_guessing_and_bad_requests(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        base = r["url"].rsplit("/", 1)[0]
        good = r["token"]
        flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
        for path in ("/m/" + flipped, "/m/" + "0" * 32, "/m/" + good.upper(), "/m/" + good[:-1], "/m/" + good + "0", "/m/", "/m", "/", "/m/" + good + "/", "/m/" + good + "?x=1", "/x/" + good, "/m/../m/" + good, "//m/" + good, "/m/%s" % good.replace(good[0], "%" + "%02x" % ord(good[0]), 1)):
            with self.subTest(path=path):
                port = self.port(r)
                status, _, body = await http("http://127.0.0.1:%d%s" % (port, path))
                self.assertEqual((status, body), (404, b""))
        self.assertEqual((await http(base + "/" + good))[0], 200)

    async def test_methods_and_malformed(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        for method in ("POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
            self.assertEqual((await http(r["url"], method=method))[0], 405, method)
        self.assertEqual((await http(r["url"], raw="GARBAGE\r\n\r\n"))[0], 400)
        self.assertEqual((await http(r["url"], raw="GET /m/x\r\nHost: x\r\n\r\n"))[0], 400)
        status, _, _ = await http(r["url"], raw="GET /m/%s HTTP/1.1\r\nX: %s\r\n\r\n" % (r["token"], "a" * 3000))
        self.assertEqual(status, 431)
        self.assertEqual((await http(r["url"]))[0], 200)

    async def test_ttl_expiry_and_server_close(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4", ttl=30)
        port = self.port(r)
        self.assertAlmostEqual(r["expires"], time.time() + 30, delta=5)
        self.assertEqual((await http(r["url"]))[0], 200)
        self.clock.t += 29
        self.assertEqual((await http(r["url"]))[0], 200)
        self.clock.t += 2
        self.assertEqual((await http(r["url"]))[0], 404)
        self.media._tick()
        self.assertEqual(self.media._tokens, {})
        self.assertIsNone(self.media._server)
        self.assertIsNone(self.media._handle)
        self.assertTrue(await refused(port))

    async def test_expiry_timer_is_a_single_one_shot(self):
        m = fy_media.Media(clock=time.monotonic, data_max=1)
        r = await m.url_for("fan", self.dir / "v.mp4", ttl=1)
        r2 = await m.url_for("fan", self.dir / "w.webm", ttl=3)
        self.assertIsNotNone(m._handle)
        port = self.port(r)
        await asyncio.sleep(1.4)
        self.assertEqual(list(m._tokens), [r2["token"]])
        self.assertIsNotNone(m._handle)
        self.assertEqual((await http(r2["url"]))[0], 200)
        await asyncio.sleep(2.0)
        self.assertEqual(m._tokens, {})
        self.assertIsNone(m._server)
        self.assertIsNone(m._handle)
        self.assertTrue(await refused(port))
        await m.close()

    async def test_ttl_clamp(self):
        for ttl, want in ((None, 600), (0, 600), (-5, 600), ("x", 600), (True, 600), (99999, 3600), (1.5, 1.5)):
            r = await self.media.url_for("fan", self.dir / "v.mp4", ttl=ttl)
            self.assertAlmostEqual(r["expires"], time.time() + want, delta=5, msg=ttl)

    async def test_revoke(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        port = self.port(r)
        self.assertFalse(self.media.revoke("0" * 32))
        self.assertFalse(self.media.revoke(None))
        self.assertFalse(self.media.revoke(5))
        self.assertTrue(self.media.revoke(r["token"]))
        self.assertFalse(self.media.revoke(r["token"]))
        self.assertTrue(await refused(port))
        self.assertIsNone(self.media._handle)

    async def test_revoke_keeps_server_for_other_tokens(self):
        a = await self.media.url_for("fan", self.dir / "v.mp4")
        b = await self.media.url_for("fan", self.dir / "w.webm")
        self.assertEqual(self.port(a), self.port(b))
        self.assertTrue(self.media.revoke(a["token"]))
        self.assertEqual((await http(a["url"]))[0], 404)
        self.assertEqual((await http(b["url"]))[0], 200)

    async def test_revoke_plugin(self):
        a = await self.media.url_for("fan", self.dir / "v.mp4")
        b = await self.media.url_for("fan", self.dir / "w.webm")
        c = await self.media.url_for("other", self.dir / "v.mp4")
        self.assertNotEqual(a["token"], c["token"])
        self.assertEqual(self.media.revoke_plugin("fan"), 2)
        self.assertEqual(self.media.revoke_plugin("fan"), 0)
        self.assertEqual((await http(a["url"]))[0], 404)
        self.assertEqual((await http(c["url"]))[0], 200)
        self.assertEqual(self.media.revoke_plugin("other"), 1)
        self.assertIsNone(self.media._server)
        self.assertEqual(self.media.revoke_plugin("nobody"), 0)

    async def test_same_file_reuses_token_and_refreshes_ttl(self):
        a = await self.media.url_for("fan", self.dir / "v.mp4", ttl=10)
        self.clock.t += 8
        b = await self.media.url_for("fan", self.dir / "v.mp4", ttl=10)
        self.assertEqual(a["token"], b["token"])
        self.clock.t += 8
        self.assertEqual((await http(b["url"]))[0], 200)
        c = await self.media.url_for("other", self.dir / "v.mp4")
        self.assertNotEqual(a["token"], c["token"])

    async def test_token_cap_evicts_own_oldest(self):
        urls = []
        for i in range(5):
            (self.dir / ("n%d.mp4" % i)).write_bytes(VIDEO)
            urls.append(await self.media.url_for("fan", self.dir / ("n%d.mp4" % i)))
        self.assertEqual(len(self.media._tokens), 4)
        self.assertNotIn(urls[0]["token"], self.media._tokens)
        self.assertIn(urls[4]["token"], self.media._tokens)

    async def test_one_plugin_cannot_lock_out_another(self):
        for pid in ("a", "b", "c", "d"):
            for i in range(4):
                (self.dir / ("%s%d.mp4" % (pid, i))).write_bytes(VIDEO)
                await self.media.url_for(pid, self.dir / ("%s%d.mp4" % (pid, i)))
        self.assertEqual(len(self.media._tokens), 16)
        b = await self.media.url_for("e", self.dir / "v.mp4")
        self.assertEqual(b["kind"], "http")
        self.assertEqual(len(self.media._tokens), 16)
        self.assertEqual(sum(1 for e in self.media._tokens.values() if e[0] == "e"), 1)

    async def test_revoke_checks_owner(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        self.assertFalse(self.media.revoke(r["token"], "other"))
        self.assertTrue(self.media.revoke(r["token"], "fan"))

    async def test_expired_tokens_free_the_cap(self):
        for i in range(16):
            (self.dir / ("n%d.mp4" % i)).write_bytes(VIDEO)
            await self.media.url_for("fan", self.dir / ("n%d.mp4" % i), ttl=5)
        self.clock.t += 6
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        self.assertEqual(len(self.media._tokens), 1)
        self.assertEqual((await http(r["url"]))[0], 200)

    async def test_server_restarts_after_idle_close(self):
        a = await self.media.url_for("fan", self.dir / "v.mp4")
        self.media.revoke(a["token"])
        self.assertIsNone(self.media._server)
        b = await self.media.url_for("fan", self.dir / "v.mp4")
        self.assertEqual((await http(b["url"]))[::2], (200, VIDEO))

    async def test_close_drops_everything(self):
        a = await self.media.url_for("fan", self.dir / "v.mp4")
        port = self.port(a)
        await self.media.close()
        self.assertEqual(self.media._tokens, {})
        self.assertIsNone(self.media._server)
        self.assertIsNone(self.media._handle)
        self.assertTrue(await refused(port))
        await self.media.close()

    async def test_concurrent_first_use_starts_one_server(self):
        for i in range(5):
            (self.dir / ("c%d.mp4" % i)).write_bytes(VIDEO)
        res = await asyncio.gather(*[self.media.url_for("fan", self.dir / ("c%d.mp4" % i)) for i in range(5)])
        self.assertEqual(len({self.port(r) for r in res}), 1)

    async def test_connection_cap(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        port = self.port(r)
        held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(8)]
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.media._conns), 8)
        status, _, _ = await http(r["url"])
        self.assertEqual(status, 503)
        for _, w in held:
            w.close()
        await asyncio.sleep(0.2)
        self.assertEqual((await http(r["url"]))[0], 200)

    async def test_slow_head_times_out(self):
        r = await self.media.url_for("fan", self.dir / "v.mp4")
        with mock.patch.object(fy_media, "READ_TIMEOUT", 0.3):
            rd, w = await asyncio.open_connection("127.0.0.1", self.port(r))
            w.write(b"GET /m/")
            await w.drain()
            data = await asyncio.wait_for(rd.read(), 3)
            self.assertEqual(data, b"")
            w.close()

    async def test_revoke_cuts_off_a_running_download(self):
        big = self.dir / "big.mp4"
        big.write_bytes(os.urandom(6_000_000))
        r = await self.media.url_for("fan", big)
        rd, w = await asyncio.open_connection("127.0.0.1", self.port(r))
        w.write(("GET /m/%s HTTP/1.1\r\nHost: x\r\n\r\n" % r["token"]).encode())
        await w.drain()
        first = await rd.read(1000)
        self.assertTrue(first.startswith(b"HTTP/1.1 200"))
        self.media.revoke(r["token"])
        rest = await asyncio.wait_for(rd.read(), 5)
        self.assertLess(len(first) + len(rest), 6_000_000)
        w.close()

    async def test_file_replaced_by_symlink_after_token_is_refused(self):
        f = self.dir / "swap.mp4"
        f.write_bytes(VIDEO)
        r = await self.media.url_for("fan", f)
        self.assertEqual((await http(r["url"]))[::2], (200, VIDEO))
        secret = self.dir / "secret.bin"
        secret.write_bytes(b"secret" * 500)
        f.unlink()
        try:
            os.symlink(secret, f)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted here")
        status, _, body = await http(r["url"])
        self.assertEqual((status, body), (404, b""))

    async def test_file_removed_after_token(self):
        f = self.dir / "gone.mp4"
        f.write_bytes(VIDEO)
        r = await self.media.url_for("fan", f)
        f.unlink()
        self.assertEqual((await http(r["url"]))[0], 404)


class Adapter(Base):
    def host(self):
        dirs = types.SimpleNamespace(data=self.dir / "d", config=self.dir / "c", runtime=self.dir / "r", logs=self.dir / "l")
        for p in (dirs.data, dirs.config, dirs.runtime, dirs.logs):
            p.mkdir()
        (dirs.data / "pic.png").write_bytes(PNG)
        (dirs.data / "big.mp4").write_bytes(b"x" * 2_000_000)
        (dirs.logs / "plugin.png").write_bytes(PNG)
        return types.SimpleNamespace(HOME=self.dir, settings=lambda: {}, dirs=lambda pid: dirs)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        fy_media._media = None
        self.addAsyncCleanup(fy_media.close)

    async def test_backend_data_tier_fits_the_host_reply_cap_at_the_boundary(self):
        h = self.host()
        edge = fy_media.HOST_DATA_MAX
        (self.dir / "d" / "edge.png").write_bytes(b"x" * edge)
        (self.dir / "d" / "over.png").write_bytes(b"x" * (edge + 1))
        under = await fy_media.hostcall(h, "fan", {"root": "data", "path": "edge.png"})
        self.assertEqual(under["kind"], "data")
        self.assertLessEqual(len(fy_proto.dumps(under)), fy_proto.PAYLOAD_MAX)
        over = await fy_media.hostcall(h, "fan", {"root": "data", "path": "over.png"})
        self.assertEqual(over["kind"], "http")
        self.assertLessEqual(len(fy_proto.dumps(over)), fy_proto.PAYLOAD_MAX)
        await fy_media.close()

    async def test_a_page_call_keeps_the_one_mib_data_tier(self):
        h = self.host()
        (self.dir / "d" / "mid.png").write_bytes(b"x" * 600000)
        r = await fy_media.METHODS["resource"](h, {"pid": "fan", "root": "data", "path": "mid.png"})
        self.assertEqual(r["kind"], "data")
        await fy_media.close()

    async def test_resource_method_small_and_large(self):
        h = self.host()
        r = await fy_media.METHODS["resource"](h, {"pid": "fan", "root": "data", "path": "pic.png"})
        self.assertEqual(r["kind"], "data")
        r = await fy_media.METHODS["resource"](h, {"pid": "fan", "root": "data", "path": "big.mp4", "ttl": 60})
        self.assertEqual(r["kind"], "http")
        self.assertEqual((await http(r["url"]))[0], 200)
        self.assertEqual(self.dir.joinpath("d", "big.mp4").stat().st_size, len((await http(r["url"]))[2]))
        out = await fy_media.METHODS["resource"](h, {"pid": "fan", "revoke": r["url"]})
        self.assertEqual(out, {"ok": True})
        self.assertTrue(await refused(self.port(r)))
        self.assertEqual(await fy_media.METHODS["resource"](h, {"pid": "fan", "revoke": r["token"]}), {"ok": False})
        self.assertEqual(await fy_media.METHODS["resource"](h, {"pid": "fan", "revoke": 5}), {"ok": False})

    async def test_roots_limited_to_plugin_dirs(self):
        h = self.host()
        for arg, code in (
            ({"root": "home", "path": "d/pic.png"}, "bad_root"),
            ({"root": "logs", "path": "plugin.png"}, "bad_root"),
            ({"root": "data", "path": "../c/x.png"}, "bad_path"),
            ({"root": "data", "path": "/etc/hosts"}, "bad_path"),
            ({"root": "data", "path": "nope.png"}, "not_found"),
            ({"root": "data", "path": "pic.html"}, "bad_type"),
            ({"root": 5, "path": "pic.png"}, "bad_root"),
            ({"path": "pic.png"}, "bad_root"),
        ):
            with self.subTest(arg=arg):
                r = await fy_media.METHODS["resource"](h, dict(arg, pid="fan"))
                self.assertEqual(r["code"], code)
                self.assertIn("error", r)
        r = await fy_media.METHODS["resource"](h, {"pid": "BAD PID", "root": "data", "path": "pic.png"})
        self.assertEqual(r["code"], "bad_root")
        r = await fy_media.METHODS["resource"](h, "junk")
        self.assertEqual(r["code"], "bad_root")

    async def test_page_calls_get_no_config_root_but_backends_do(self):
        h = self.host()
        (self.dir / "c" / "cfg.png").write_bytes(PNG)
        arg = {"pid": "fan", "root": "config", "path": "cfg.png"}
        r = await fy_media.METHODS["resource"](h, arg)
        self.assertEqual(r["code"], "bad_root")
        r = await fy_media.hostcall(h, "fan", arg)
        self.assertEqual(r["kind"], "data")
        r = await fy_media.METHODS["resource"](h, {"pid": "fan", "root": "runtime", "path": "x.png"})
        self.assertEqual(r["code"], "not_found")

    async def test_directories_follow_the_passed_pid_never_the_argument(self):
        asked = []
        h = self.host()
        inner = h.dirs
        h.dirs = lambda pid: asked.append(pid) or inner(pid)
        await fy_media.hostcall(h, "fan", {"pid": "victim", "root": "data", "path": "pic.png"})
        await fy_media.METHODS["resource"](h, {"pid": "authenticated", "root": "data", "path": "pic.png"})
        self.assertEqual(asked, ["fan", "authenticated"])

    async def test_symlink_out_of_plugin_dir_refused(self):
        h = self.host()
        outside = self.dir / "outdir"
        outside.mkdir()
        (outside / "secret.png").write_bytes(PNG)
        try:
            os.symlink(outside, self.dir / "d" / "esc", target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt" or subprocess.run(["cmd", "/c", "mklink", "/J", str(self.dir / "d" / "esc"), str(outside)], capture_output=True).returncode:
                self.skipTest("links not permitted here")
        r = await fy_media.METHODS["resource"](h, {"pid": "fan", "root": "data", "path": "esc/secret.png"})
        self.assertEqual(r["code"], "outside_root")

    async def test_singleton_lifecycle(self):
        a = fy_media.get()
        self.assertIs(a, fy_media.get())
        await fy_media.close()
        self.assertIsNone(fy_media._media)
        await fy_media.close()
        self.assertIsNot(a, fy_media.get())


if __name__ == "__main__":
    unittest.main()
