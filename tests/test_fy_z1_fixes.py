import asyncio
import importlib.util
import io
import json
import pathlib
import sys
import tracemalloc
import unittest
import zipfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H


class Base(unittest.TestCase):
    def setUp(self):
        self.loader = H.Loader()
        self.addCleanup(asyncio.run, self.loader.close())
        self.fy = self.loader.fy


class Vet(Base):
    def test_plugin_json_over_64_kb_is_refused(self):
        data = H.mkzip({"repo/plugin.json": "{" + " " * 70000 + "}"})
        self.assertEqual(self.fy.vet(data), {"error": "plugin.json over 64 KB"})

    def test_bomb_plugin_json_is_refused_without_expanding(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("repo/plugin.json", b"{" + b" " * (20 << 20) + b"}")
        data = bytearray(b.getvalue())
        at = data.rfind(b"PK\x01\x02")
        data[at + 24:at + 28] = (4096).to_bytes(4, "little")
        tracemalloc.start()
        try:
            r = self.fy.vet(bytes(data))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertIn("error", r)
        self.assertLess(peak, 4 << 20)


def fresh_framey(loader):
    spec = importlib.util.spec_from_file_location("framey_z1_real", loader.root / "framey.py")
    fy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fy)
    fy.log = lambda *a: None
    return fy


class Pin(Base):
    def setUp(self):
        super().setUp()
        self.fy = fresh_framey(self.loader)
        self.fy.use("fy_net")
        self.looked, self.opened = [], []
        answers = [["93.184.216.34"], ["127.0.0.1"]]

        def gai(host, port):
            self.looked.append(host)
            return answers[min(len(self.looked), len(answers)) - 1]

        def refuse(scheme, host, ip, port, timeout, track=None):
            self.opened.append(ip)
            raise OSError("refused")

        for patch in (mock.patch("fy_net._gai", gai), mock.patch("fy_net._open", refuse)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_fetch_resolves_once_and_connects_only_to_that_address(self):
        with self.assertRaises(Exception) as ctx:
            self.fy.fetch("https://example.com/x.zip")
        self.assertEqual(getattr(ctx.exception, "code", ""), "network")
        self.assertEqual(self.looked, ["example.com"])
        self.assertEqual(self.opened, ["93.184.216.34"])

    def test_install_resolves_once_and_connects_only_to_that_address(self):
        self.assertEqual(asyncio.run(self.fy.install("https://example.com/x.zip")), {"error": "Could not connect to that server"})
        self.assertEqual(self.looked, ["example.com"])
        self.assertEqual(self.opened, ["93.184.216.34"])


HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def proc_row(sl, local, state, uid):
    return "%4s: %s 00000000:0000 %s 00000000:00000000 00:00000000 00000000 %5d        0 %d 1 0\n" % (sl, local, state, uid, 1000 + int(sl))


class Owner(Base):
    def table(self, *rows):
        path = pathlib.Path(self.loader.base) / "tcp"
        path.write_text(HEAD + "".join(rows))
        return path

    def test_listener_on_8080_must_belong_to_our_uid(self):
        path = self.table(proc_row(0, "0100007F:1F90", "0A", 1000), proc_row(1, "0100007F:0050", "0A", 0))
        with mock.patch("os.getuid", return_value=1000, create=True), mock.patch.object(sys, "platform", "linux"):
            self.assertTrue(self.fy.cdp_owner_ok(str(path)))
        with mock.patch("os.getuid", return_value=1001, create=True), mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(self.fy.cdp_owner_ok(str(path)))

    def test_a_foreign_listener_on_8080_is_refused_and_no_listener_is_refused(self):
        foreign = self.table(proc_row(0, "0100007F:1F90", "0A", 0))
        none = self.table(proc_row(0, "0100007F:1F90", "01", 1000))
        with mock.patch("os.getuid", return_value=1000, create=True), mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(self.fy.cdp_owner_ok(str(foreign)))
            self.assertFalse(self.fy.cdp_owner_ok(str(none)))
            self.assertFalse(self.fy.cdp_owner_ok(str(pathlib.Path(self.loader.base) / "missing")))

    def test_other_platforms_skip_the_check(self):
        with mock.patch.object(sys, "platform", "win32"):
            self.assertTrue(self.fy.cdp_owner_ok("/nonexistent"))


class Sweep(Base):
    def setUp(self):
        super().setUp()
        self.base = pathlib.Path(self.loader.base) / "dev"
        self.dest = self.base / "tool"
        self.dest.mkdir(parents=True)
        (self.dest / "plugin.json").write_text('{"id": "old"}')
        self.stage = self.base / ".stage-tool"
        self.stage.mkdir()
        (self.stage / "keep.txt").write_text("mine")

    def fill(self, stage):
        stage.mkdir(exist_ok=True)
        (stage / "plugin.json").write_text('{"id": "tool"}')

    def test_an_unmarked_stage_folder_next_to_a_linked_target_is_not_removed(self):
        with self.assertRaises(OSError):
            self.fy.stage_swap("tool", self.fill, dest=self.dest)
        self.assertEqual((self.stage / "keep.txt").read_text(), "mine")
        self.assertEqual((self.dest / "plugin.json").read_text(), '{"id": "old"}')

    def test_a_marked_stage_folder_is_cleared_and_the_swap_goes_through(self):
        (self.stage / ".version").write_text("1.0.0")
        self.fy.stage_swap("tool", self.fill, dest=self.dest)
        self.assertEqual((self.dest / "plugin.json").read_text(), '{"id": "tool"}')
        self.assertFalse(self.stage.exists())


class Back(Base):
    def setUp(self):
        super().setUp()
        self.L = self.fy.ROOT.resolve()
        self.bak = self.fy.sib(".bak")
        (self.L / "plugins" / "mine").mkdir(parents=True, exist_ok=True)
        (self.L / "plugins" / "mine" / "data.txt").write_text("keep")

    def make_backup(self, with_plugins):
        self.bak.mkdir()
        (self.bak / "framey.py").write_text("VERSION = '1'\n")
        if with_plugins:
            (self.bak / "plugins" / "theirs").mkdir(parents=True)

    def test_back_refuses_when_both_sides_hold_plugins(self):
        self.make_backup(with_plugins=True)
        with self.assertRaises(OSError):
            self.fy.back(self.L, self.bak)
        self.assertEqual((self.L / "plugins" / "mine" / "data.txt").read_text(), "keep")
        self.assertTrue((self.bak / "plugins" / "theirs").is_dir())

    def test_back_moves_loader_plugins_into_an_empty_backup(self):
        self.make_backup(with_plugins=False)
        self.fy.back(self.L, self.bak)
        self.assertEqual((self.L / "plugins" / "mine" / "data.txt").read_text(), "keep")
        self.assertFalse(self.bak.exists())


class Corrupt(Base):
    def test_corrupt_settings_block_injection_and_are_never_overwritten(self):
        conf = self.fy.CONF
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_bytes(b'{"disabled": ["a"')
        self.assertTrue(self.fy.unreadable())
        self.assertTrue(self.fy.unreadable())
        self.assertEqual(sum("could not be read or parsed" in x for x in self.loader.errors.getvalue().splitlines()), 1)
        with self.assertRaises(Exception):
            self.fy.save(self.fy.settings())
        self.assertEqual(conf.read_bytes(), b'{"disabled": ["a"')
        conf.write_bytes(b'{"disabled": ["a"]}')
        self.assertFalse(self.fy.unreadable())


class Exit(Base):
    def test_run_exits_without_waiting_for_a_running_executor_job(self):
        import time

        async def main():
            asyncio.get_running_loop().run_in_executor(None, time.sleep, 2)

        with mock.patch.object(self.fy, "main", main), mock.patch("os._exit") as hard:
            started = time.monotonic()
            self.fy.main_entry()
            elapsed = time.monotonic() - started
        hard.assert_called_once_with(0)
        self.assertLess(elapsed, 1.0)


class Crash(BaseException):
    pass


class LoaderSwap(Base):
    def package(self):
        return {f.name: f.read_text(encoding="utf-8") for f in H.repo_files()}

    def install_and_crash_after(self, n):
        sha = "c" * 40
        self.loader.net.archive("chaosfox26", "framey", sha, H.mkzip(self.package(), top="framey-" + sha))
        real = pathlib.Path.rename
        calls = []

        def crashing(path, target):
            calls.append(path.name)
            real(path, target)
            if len(calls) == n:
                raise Crash()

        with mock.patch.object(pathlib.Path, "rename", crashing):
            with self.assertRaises(Crash):
                asyncio.run(self.fy.install_loader("v2.0.0", sha))

    def test_crash_in_the_gap_leaves_a_backup_that_heal_restores_to_the_unit_path(self):
        root = self.fy.ROOT.resolve()
        (root / "plugins" / "mine").mkdir(parents=True, exist_ok=True)
        (root / "plugins" / "mine" / "x.txt").write_text("keep")
        self.install_and_crash_after(1)
        self.assertFalse((root / "framey.py").exists())
        self.assertTrue((self.fy.sib(".bak") / "framey.py").is_file())
        self.fy.heal_loader()
        self.assertTrue((root / "framey.py").is_file())
        self.assertEqual((root / "plugins" / "mine" / "x.txt").read_text(), "keep")

    def test_crash_after_the_new_loader_is_in_place_gets_its_plugins_back(self):
        root = self.fy.ROOT.resolve()
        (root / "plugins" / "mine").mkdir(parents=True, exist_ok=True)
        (root / "plugins" / "mine" / "x.txt").write_text("keep")
        self.install_and_crash_after(2)
        self.assertTrue((root / "framey.py").is_file())
        self.fy.heal_loader()
        self.assertEqual((root / "plugins" / "mine" / "x.txt").read_text(), "keep")


class TrustedInput(Base):
    def mode(self, cfg=None):
        path = self.fy.CONF.parent / "loader.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if cfg is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(cfg))
        return self.fy.trusted_input()

    def test_default_is_auto_and_the_three_modes_pass_through(self):
        self.assertEqual(self.mode(), "auto")
        self.assertEqual(self.mode({}), "auto")
        for m in ("off", "auto", "require"):
            self.assertEqual(self.mode({"trusted_input": m}), m)

    def test_unknown_values_fall_back_to_auto(self):
        for v in ("on", "REQUIRE", "", None, 1, ["require"]):
            self.assertEqual(self.mode({"trusted_input": v}), "auto", v)


class CoreDispatch(Base):
    def test_the_real_core_dispatcher_answers_a_core_method(self):
        r = asyncio.run(self.loader.core("loader"))
        self.assertIn("commit", r)
        self.assertIsInstance(r["kept"], bool)


if __name__ == "__main__":
    unittest.main()
