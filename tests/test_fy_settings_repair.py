import asyncio
import importlib.util
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fy_store


def fresh_framey(home):
    spec = importlib.util.spec_from_file_location("framey_w2_" + os.path.basename(home), ROOT / "framey.py")
    fy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fy)
    fy.log = lambda *a: None
    fy.CONF = pathlib.Path(home) / ".config" / "framey" / "settings.json"
    fy.CONF.parent.mkdir(parents=True, exist_ok=True)
    fy.PLUGINS = pathlib.Path(home) / "plugins"
    plugin = fy.PLUGINS / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps({"name": "demo", "version": "1", "short": "x"}))
    (plugin / "main.js").write_text("1")
    return fy


def injected(fy):
    return [pid for _, pid in fy.main_code() if pid and pid != "#core"]


class SettingsRepairTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="w2-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.fy = fresh_framey(self.home)
        self.conf = self.fy.CONF

    def core(self, method):
        return asyncio.run(self.fy.core(method, None))

    def test_corrupt_file_is_visible_and_injects_no_plugins(self):
        self.conf.write_bytes(b"{broken")
        self.assertEqual(self.fy.settings_state(), "corrupt")
        self.assertEqual(self.core("status"), {"settings_state": "corrupt"})
        self.assertEqual(injected(self.fy), [])

    def test_valid_file_reports_ok_and_injects(self):
        self.conf.write_text(json.dumps({"v": 1}))
        self.assertEqual(self.core("status"), {"settings_state": "ok"})
        self.assertEqual(injected(self.fy), ["demo"])

    def test_repair_renames_clears_state_and_reinjects(self):
        self.conf.write_bytes(b"{broken")
        self.assertEqual(self.core("settings_repair"), {"ok": True})
        self.assertFalse(self.conf.exists())
        kept = [p.name for p in self.conf.parent.iterdir() if re.fullmatch(r"settings\.json\.corrupt-\d+", p.name)]
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.core("status"), {"settings_state": "ok"})
        self.assertEqual(injected(self.fy), ["demo"])

    def test_repair_keeps_only_the_three_newest_corrupt_copies(self):
        for stamp in (1000, 1001, 1002, 1003):
            (self.conf.parent / ("settings.json.corrupt-%d" % stamp)).write_text("old")
        (self.conf.parent / "settings.json.corrupt-x").write_text("not ours")
        self.conf.write_bytes(b"{broken")
        self.assertEqual(self.core("settings_repair"), {"ok": True})
        names = sorted(p.name for p in self.conf.parent.iterdir() if p.name.startswith("settings.json.corrupt-"))
        self.assertEqual(len(names), 4)
        self.assertNotIn("settings.json.corrupt-1000", names)
        self.assertNotIn("settings.json.corrupt-1001", names)
        self.assertIn("settings.json.corrupt-x", names)

    def test_a_directory_named_like_a_copy_takes_no_slot(self):
        for stamp in (1000, 1001, 1002):
            (self.conf.parent / ("settings.json.corrupt-%d" % stamp)).write_text("old")
        (self.conf.parent / "settings.json.corrupt-1003").mkdir()
        self.conf.write_bytes(b"{broken")
        self.assertEqual(self.core("settings_repair"), {"ok": True})
        self.assertTrue((self.conf.parent / "settings.json.corrupt-1001").is_file())
        self.assertTrue((self.conf.parent / "settings.json.corrupt-1003").is_dir())

    def test_unreadable_file_is_reported_and_never_touched(self):
        self.conf.mkdir()
        self.assertEqual(self.fy.settings_state(), "unreadable")
        self.assertEqual(self.core("status"), {"settings_state": "unreadable"})
        r = self.core("settings_repair")
        self.assertIs(r["ok"], False)
        self.assertIn("error", r)
        self.assertTrue(self.conf.is_dir())
        self.assertEqual(list(self.conf.parent.glob("settings.json.corrupt-*")), [])

    def test_plugin_token_cannot_call_settings_methods(self):
        self.fy.TOKS["ptok"] = "demo"
        for method in ("settings_repair", "status"):
            allowed, _ = self.fy.authorize({"plugin": "_core", "method": method, "t": "ptok"})
            self.assertFalse(allowed, method)
        allowed, _ = self.fy.authorize({"plugin": "_core", "method": "settings_repair", "t": self.fy.CORE_TOK})
        self.assertTrue(allowed)
        self.assertIn("settings_repair", self.fy.EDIT)


class RuntimeRootTests(unittest.TestCase):
    RUN = "/fake/run/user"
    UID = 1000

    def fake_lstat(self, entries):
        def lstat(p):
            key = os.path.normpath(str(p))
            if key not in entries:
                raise FileNotFoundError(p)
            return entries[key]
        return lstat

    def st(self, mode, uid=UID):
        return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))

    def root(self, entries):
        env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
        p = os.path.join(self.RUN, str(self.UID))
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(fy_store.os, "getuid", create=True, return_value=self.UID), \
                mock.patch.object(fy_store.os, "lstat", side_effect=self.fake_lstat({os.path.normpath(p): entries} if entries else {})):
            return fy_store.runtime_root(run=self.RUN, uid=self.UID)

    def test_xdg_wins(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/x/y"}):
            self.assertEqual(fy_store.runtime_root(run=self.RUN, uid=self.UID), "/x/y")

    def test_private_uid_dir_is_used(self):
        self.assertEqual(self.root(self.st(stat.S_IFDIR | 0o700)), os.path.join(self.RUN, str(self.UID)))

    def test_unsafe_uid_dirs_fail_closed(self):
        self.assertIsNone(self.root(None))
        self.assertIsNone(self.root(self.st(stat.S_IFDIR | 0o755)))
        self.assertIsNone(self.root(self.st(stat.S_IFDIR | 0o770)))
        self.assertIsNone(self.root(self.st(stat.S_IFDIR | 0o700, uid=0)))
        self.assertIsNone(self.root(self.st(stat.S_IFLNK | 0o700)))
        self.assertIsNone(self.root(self.st(stat.S_IFREG | 0o700)))

    def test_dirs_uses_the_fallback_and_never_tmp(self):
        home = tempfile.mkdtemp(prefix="w2h-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(fy_store, "runtime_root", return_value=None):
            self.assertIsNone(fy_store.dirs("demo", home=home).runtime)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(fy_store, "runtime_root", return_value=os.path.join(self.RUN, "1000")):
            self.assertEqual(fy_store.dirs("demo", home=home).runtime, pathlib.Path(self.RUN, "1000", "framey", "demo"))


if __name__ == "__main__":
    unittest.main()
