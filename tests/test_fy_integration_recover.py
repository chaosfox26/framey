import asyncio
import http.server
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import tracemalloc
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H

BACKEND = "async def call(method, arg):\n    return None\n"
SMOKE_OK = subprocess.CompletedProcess([], 0, b"framey-smoke-ok\n", b"")


class Power(BaseException):
    pass


class Cut:
    def __init__(self, at):
        self.at, self.n, self.dead = at, 0, False

    def go(self):
        if self.dead:
            return False
        self.n += 1
        if self.n == self.at:
            self.dead = True
            raise Power()
        return True


def cut_at(k):
    cut = Cut(k)
    real_rename, real_rmtree = pathlib.Path.rename, shutil.rmtree

    def rename(self, target):
        if cut.go():
            return real_rename(self, target)

    def rmtree(path, *a, **kw):
        if cut.go():
            return real_rmtree(path, *a, **kw)

    return cut, mock.patch.object(pathlib.Path, "rename", rename), mock.patch.object(shutil, "rmtree", rmtree)


def fy_names():
    return [n for n in sys.modules if n.startswith("fy_")]


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def boot(self, broken=None, text="", prep=None, check=None):
        saved = {n: sys.modules.pop(n) for n in fy_names()}
        path = list(sys.path)
        loader = H.Loader()
        try:
            loader.plugin("a", {"api": 1}, backend=BACKEND)
            (loader.home / ".config" / "framey" / "settings.json").write_text(json.dumps({"prefs": {"autocheck": True}}))
            if broken:
                (loader.root / broken).write_text(text)
            if prep:
                prep(loader)
            fy = loader.fy

            async def tabs():
                return []

            fy.tabs = tabs
            with mock.patch.object(asyncio.get_running_loop(), "add_signal_handler"):
                try:
                    await asyncio.wait_for(fy.main(), 0.2)
                except asyncio.TimeoutError:
                    if check:
                        check(fy)
                    return
            self.fail("main() returned instead of running")
        finally:
            await loader.close()
            for n in fy_names():
                del sys.modules[n]
            sys.modules.update(saved)
            sys.path[:] = path

    def variants(self, src):
        cut = src.find("\ndef ")
        if cut < 0:
            cut = src.find("\nclass ")
        return {"empty": "", "truncated": src[: cut + 1] if cut > 0 else src[: len(src) // 2], "syntax": "def broken(:\n"}

    async def test_the_loader_starts_with_any_single_fy_module_empty_truncated_or_broken(self):
        for f in sorted(H.repo_files()):
            if not (f.name.startswith("fy_") and f.suffix == ".py"):
                continue
            for kind, text in self.variants(f.read_text()).items():
                with self.subTest(module=f.name, kind=kind):
                    await self.boot(f.name, text, check=lambda fy: self.assertTrue(fy.pref("autocheck") is True))

    async def test_the_loader_starts_when_plugins_is_a_file(self):
        def make_file(loader):
            shutil.rmtree(loader.plugins)
            loader.plugins.write_text("not a folder")

        await self.boot(prep=make_file, check=lambda fy: self.assertEqual((fy.manifests(), fy.catalog()), ([], [])))

    async def test_the_loader_starts_when_the_store_is_missing(self):
        await self.boot(prep=lambda loader: shutil.rmtree(loader.root / "store"), check=lambda fy: self.assertEqual(fy.catalog(), []))


class PayloadAndFrameTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_oversized_page_call_is_refused_with_too_large_and_never_parsed(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        page, task = await H.attach(loader)
        self.addCleanup(task.cancel)
        big = json.dumps({"gen": "g", "id": 9, "plugin": "_core", "method": "list", "arg": "x" * (300 << 10), "t": page.tok})
        page.q.put_nowait(json.dumps({"method": "Runtime.bindingCalled", "params": {"payload": big}}))
        self.assertTrue(await H.settle(lambda: any(r[2] == {"error": "too large"} for r in page.replies())))
        self.assertEqual(page.replies()[-1], ["g", 9, {"error": "too large"}])

    async def test_an_eight_megabyte_payload_does_not_expand_in_memory(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        big = "[" + "0," * (4 << 20) + "0]"
        tracemalloc.start()
        try:
            self.assertEqual(fy.request(big), {})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1 << 20)
        self.assertEqual(fy.request('{"a": 1}'), {"a": 1})

    async def test_a_websocket_frame_over_four_megabytes_is_refused_before_it_is_read(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        r = asyncio.StreamReader()
        r.feed_data(bytes([0x81, 127]) + (5 << 20).to_bytes(8, "big"))
        self.assertIsNone(await fy.WS(r, None).recv())
        body = json.dumps({"ok": 1}).encode()
        r = asyncio.StreamReader()
        r.feed_data(bytes([0x81, len(body)]) + body)
        self.assertEqual(await fy.WS(r, None).recv(), '{"ok": 1}')


class Trickle(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Length", str(2_000_000))
            self.end_headers()
            self.wfile.write(b"x" * 2_000_000)
            return
        self.send_response(200)
        self.send_header("Content-Length", "5000000")
        self.end_headers()
        try:
            while True:
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.5)
        except OSError:
            pass


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        spec = importlib.util.spec_from_file_location("framey_real_fetch", loader.root / "framey.py")
        self.fy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fy)

    async def test_a_trickling_server_is_cut_at_the_deadline(self):
        with mock.patch.object(self.fy, "FETCH_S", 2):
            start = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "too slow"):
                await asyncio.to_thread(self.fy.fetch, self.base + "/slow")
            self.assertLess(time.monotonic() - start, 3.5)

    async def test_a_body_over_the_byte_cap_is_refused(self):
        with self.assertRaisesRegex(ValueError, "too large"):
            await asyncio.to_thread(self.fy.fetch, self.base + "/big", None, None, 1_000_000)


class RecoverTests(unittest.IsolatedAsyncioTestCase):
    async def fresh(self):
        loader = H.Loader()
        loader.plugin("a", {"api": 1})
        (loader.root / "store" / "keep.txt").write_text("k")
        net = loader.net
        full = {f.name: f.read_text() for f in H.repo_files()}
        net.archive("chaosfox26", "framey", "b" * 40, H.mkzip(full, top="framey-" + "b" * 40))
        return loader

    def healthy(self, loader, msg):
        fy, root = loader.fy, loader.root
        self.assertTrue(fy.loader_ok(root), msg)
        self.assertTrue((root / "plugins" / "a" / "plugin.json").is_file(), msg)
        self.assertEqual((root / "store" / "keep.txt").read_text(), "k", msg)

    async def cut_run(self, k, prep, action):
        loader = await self.fresh()
        try:
            fy = loader.fy
            with mock.patch("subprocess.run", return_value=SMOKE_OK):
                if prep:
                    await prep(fy)
                cut, p1, p2 = cut_at(k)
                with p1, p2:
                    try:
                        await action(fy)
                        crashed = False
                    except Power:
                        crashed = True
                fy.recover()
                fy.recover()
            self.healthy(loader, "cut at %d" % k)
            return crashed
        finally:
            await loader.close()

    async def test_a_power_cut_at_every_step_of_a_loader_update_leaves_a_recoverable_tree(self):
        crashes = 0
        for k in range(1, 200):
            if not await self.cut_run(k, None, lambda fy: fy.install_loader("v9.0.0", "b" * 40)):
                break
            crashes += 1
        self.assertGreater(crashes, 5)

    async def test_a_power_cut_at_every_step_of_a_loader_rollback_leaves_a_recoverable_tree(self):
        async def prep(fy):
            self.assertEqual((await fy.install_loader("v9.0.0", "b" * 40))["ok"], "framey")

        crashes = 0
        for k in range(1, 200):
            if not await self.cut_run(k, prep, lambda fy: fy.loader_roll(None)):
                break
            crashes += 1
        self.assertGreater(crashes, 2)

    async def test_recovery_restores_the_old_loader_when_the_live_tree_is_gone(self):
        loader = await self.fresh()
        fy, root, bak = loader.fy, loader.root, loader.home / "framey.bak"
        shutil.move(str(root), str(bak))
        fy.recover()
        self.healthy(loader, "live tree missing")
        self.assertFalse(bak.exists())

    async def test_an_unfinished_plugin_folder_is_not_resurrected_by_recovery(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        (loader.plugins / ".old-zz").mkdir()
        (loader.plugins / ".old-zz" / "main.js").write_text("1")
        fy.recover()
        self.assertFalse((loader.plugins / "zz").exists())

    async def test_uninstall_clears_held_copies_before_removing_the_plugin(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a", {"api": 1})
        (loader.plugins / ".old-a").mkdir()
        (loader.plugins / ".old-a" / "plugin.json").write_text(json.dumps({"id": "a", "name": "a", "version": "0.9"}))
        self.assertEqual(await loader.core("uninstall", {"id": "a", "purge": False}), {"ok": True})
        self.assertFalse((loader.plugins / ".old-a").exists())
        self.assertFalse((loader.plugins / "a").exists())


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_unreadable_settings_file_is_never_overwritten(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a", {"api": 1})
        conf = loader.fy.CONF
        conf.mkdir(parents=True)
        self.addCleanup(lambda: conf.is_dir() and conf.rmdir())
        with self.assertRaises(Exception) as cm:
            await loader.core("disable", "a")
        self.assertEqual(getattr(cm.exception, "code", ""), "unreadable")
        self.assertTrue(conf.is_dir())


if __name__ == "__main__":
    unittest.main()
