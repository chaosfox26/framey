import asyncio
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_files


def link(src, dst):
    try:
        os.symlink(src, dst, target_is_directory=os.path.isdir(src))
    except (OSError, NotImplementedError):
        if os.name == "nt" and os.path.isdir(src) and subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True).returncode == 0:
            return
        raise unittest.SkipTest("symlinks not permitted here")


class Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "home"
        self.out = Path(self.tmp.name).resolve() / "outside"
        for d in (self.home / "Downloads" / "deep", self.home / "zeta", self.home / "Alpha", self.out):
            d.mkdir(parents=True)
        (self.home / "b.TXT").write_text("bb")
        (self.home / "a.png").write_bytes(b"12345")
        (self.home / ".hidden").write_text("h")
        (self.out / "secret.txt").write_text("s")
        self.roots = {"home": self.home}


class SafeJoin(Tree):
    def code(self, rel):
        with self.assertRaises(fy_files.FileError) as c:
            fy_files.safe_join(self.home, rel)
        return c.exception.code

    def test_ok(self):
        self.assertEqual(fy_files.safe_join(self.home, ""), self.home)
        self.assertEqual(fy_files.safe_join(self.home, "Downloads/deep"), self.home / "Downloads" / "deep")
        self.assertEqual(fy_files.safe_join(self.home, "missing/x"), self.home / "missing" / "x")

    def test_rejects_syntax(self):
        for rel in ("..", "a/../b", "../outside", "/etc/passwd", "a\\b", "a\0b", "a//b", "a/", "./a", "a/./b", "C:foo", 5, None):
            with self.subTest(rel=rel):
                self.assertEqual(self.code(rel), "bad_path")

    def test_long_and_deep(self):
        self.assertEqual(self.code("a" * 1025), "bad_path")
        self.assertEqual(self.code("/".join(["d"] * 33)), "bad_path")
        fy_files.safe_join(self.home, "/".join(["d"] * 32))
        fy_files.safe_join(self.home, "a" * 200 + "/" + "b" * 200)

    def test_symlink_out(self):
        link(self.out, self.home / "esc")
        self.assertEqual(self.code("esc"), "outside_root")
        self.assertEqual(self.code("esc/secret.txt"), "outside_root")

    def test_symlink_inside(self):
        link(self.home / "Downloads", self.home / "dl")
        self.assertEqual(fy_files.safe_join(self.home, "dl/deep"), self.home / "Downloads" / "deep")


class Listing(Tree):
    def test_sort_dirs_first_casefold(self):
        r = fy_files.list_dir(self.roots, "home")
        self.assertEqual([e["name"] for e in r["entries"]], ["Alpha", "Downloads", "zeta", "a.png", "b.TXT"])
        self.assertEqual([e["type"] for e in r["entries"]], ["dir", "dir", "dir", "file", "file"])
        self.assertEqual(r["entries"][3]["size"], 5)
        self.assertIsNone(r["entries"][0]["size"])
        self.assertIsInstance(r["entries"][3]["mtime"], int)
        self.assertEqual((r["root"], r["path"], r["parent"], r["truncated"], r["roots"]), ("home", "", None, False, [{"id": "home", "label": "home"}]))

    def test_hidden(self):
        self.assertNotIn(".hidden", [e["name"] for e in fy_files.list_dir(self.roots, "home")["entries"]])
        names = [e["name"] for e in fy_files.list_dir(self.roots, "home", hidden=True)["entries"]]
        self.assertIn(".hidden", names)

    def test_dirs_only_and_exts(self):
        self.assertEqual([e["name"] for e in fy_files.list_dir(self.roots, "home", dirs_only=True)["entries"]], ["Alpha", "Downloads", "zeta"])
        r = fy_files.list_dir(self.roots, "home", exts=["txt", ".PNG"])
        self.assertEqual([e["name"] for e in r["entries"]], ["Alpha", "Downloads", "zeta", "a.png", "b.TXT"])
        r = fy_files.list_dir(self.roots, "home", exts=["png"])
        self.assertEqual([e["name"] for e in r["entries"]], ["Alpha", "Downloads", "zeta", "a.png"])
        with self.assertRaises(fy_files.FileError):
            fy_files.list_dir(self.roots, "home", exts=["x"] * 17)
        with self.assertRaises(fy_files.FileError):
            fy_files.list_dir(self.roots, "home", exts=["a/b"])

    def test_limit_truncates_after_sort(self):
        r = fy_files.list_dir(self.roots, "home", limit=2)
        self.assertEqual([e["name"] for e in r["entries"]], ["Alpha", "Downloads"])
        self.assertTrue(r["truncated"])
        self.assertEqual(len(fy_files.list_dir(self.roots, "home", limit=9999)["entries"]), 5)

    def test_big_folder_caps(self):
        big = self.home / "big"
        big.mkdir()
        for i in range(520):
            (big / ("f%04d" % i)).touch()
        r = fy_files.list_dir(self.roots, "home", "big")
        self.assertEqual(len(r["entries"]), 500)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["entries"][0]["name"], "f0000")
        with mock.patch.object(fy_files, "SCAN_MAX", 100):
            with self.assertRaises(fy_files.FileError) as c:
                fy_files.list_dir(self.roots, "home", "big")
        self.assertEqual(c.exception.code, "too_many")

    def test_nested_parent_and_no_roots_field(self):
        r = fy_files.list_dir(self.roots, "home", "Downloads/deep")
        self.assertEqual(r["parent"], "Downloads")
        self.assertNotIn("roots", r)
        self.assertEqual(fy_files.list_dir(self.roots, "home", "Downloads")["parent"], "")

    def test_errors(self):
        cases = [(("nope",), "bad_root"), (("home", "missing"), "not_found"), (("home", "a.png"), "not_dir"), (("home", ".."), "bad_path"), ((None,), "bad_root")]
        for args, code in cases:
            with self.subTest(args=args):
                with self.assertRaises(fy_files.FileError) as c:
                    fy_files.list_dir(self.roots, *args)
                self.assertEqual(c.exception.code, code)

    def test_link_entries(self):
        link(self.out, self.home / "esc")
        link(self.home / "a.png", self.home / "lnk.png")
        r = fy_files.list_dir(self.roots, "home")
        kinds = {e["name"]: e["type"] for e in r["entries"]}
        self.assertEqual(kinds["esc"], "link")
        self.assertEqual(kinds["lnk.png"], "link")
        with self.assertRaises(fy_files.FileError) as c:
            fy_files.list_dir(self.roots, "home", "esc")
        self.assertEqual(c.exception.code, "outside_root")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0, "needs posix non-root")
    def test_denied(self):
        d = self.home / "locked"
        d.mkdir()
        d.chmod(0)
        self.addCleanup(d.chmod, 0o700)
        with self.assertRaises(fy_files.FileError) as c:
            fy_files.list_dir(self.roots, "home", "locked")
        self.assertEqual(c.exception.code, "denied")

    def test_stat_file(self):
        s = fy_files.stat_file(self.roots, "home", "a.png")
        self.assertEqual((s["type"], s["size"], s["name"]), ("file", 5, "a.png"))
        self.assertEqual(fy_files.stat_file(self.roots, "home", "Downloads/deep")["type"], "dir")
        for rel, code in (("missing", "not_found"), ("../x", "bad_path")):
            with self.assertRaises(fy_files.FileError) as c:
                fy_files.stat_file(self.roots, "home", rel)
            self.assertEqual(c.exception.code, code)


class Roots(Tree):
    def test_roots(self):
        dirs = types.SimpleNamespace(config=self.out / "c", data=self.out / "d", runtime=self.out / "r", logs=self.out / "l")
        extra = [str(self.out), str(self.out), "relative", str(self.out / "nope"), 5, str(self.home / "a.png")]
        r = fy_files.roots(self.home, dirs, extra)
        self.assertNotIn("home", r)
        self.assertEqual(r["data"], self.out / "d")
        self.assertEqual(r["config"], self.out / "c")
        self.assertNotIn("runtime", r)
        self.assertEqual(r["outside"], self.out)
        self.assertEqual(r["outside-2"], self.out)
        self.assertEqual(len(r), 4)
        self.assertEqual(list(fy_files.roots(self.home, None, None)), [])

    def test_allowlist_refuses_filesystem_root_home_tree_and_system_dirs(self):
        bad = ["/", str(self.home), str(self.home.parent)]
        if os.name != "nt":
            bad.append("/etc")
        for item in bad:
            self.assertFalse(fy_files.allowed(item, self.home), item)
        self.assertTrue(fy_files.allowed(str(self.out), self.home))
        self.assertEqual(list(fy_files.roots(self.home, None, bad)), [])

    def test_symlink_root_is_refused(self):
        link(self.out, self.home / "lnk")
        r = fy_files.roots(self.home, None, [str(self.home / "lnk"), str(self.out)])
        self.assertEqual(list(r), ["outside"])
        self.assertEqual(r["outside"], self.out)

    def test_extra_capped(self):
        extra = []
        for i in range(12):
            (self.out / ("x%d" % i)).mkdir()
            extra.append(str(self.out / ("x%d" % i)))
        self.assertEqual(len(fy_files.roots(self.home, None, extra)), 8)


class Adapter(Tree):
    def host(self, roots_pref):
        return types.SimpleNamespace(
            HOME=self.home,
            settings=lambda: {"prefs": {"file_roots": roots_pref}},
            dirs=lambda pid: types.SimpleNamespace(config=self.out / "c", data=self.out, runtime=None, logs=None),
        )

    def test_files_list_method(self):
        h = self.host([])
        r = asyncio.run(fy_files.METHODS["files_list"](h, {"pid": "fan", "root": "data"}))
        self.assertEqual([e["name"] for e in r["entries"]], ["secret.txt"])
        r = asyncio.run(fy_files.METHODS["files_list"](h, {"pid": "fan", "root": "data", "path": "../x"}))
        self.assertEqual(r["code"], "bad_path")
        self.assertIn("error", r)
        r = asyncio.run(fy_files.METHODS["files_list"](h, {"pid": "BAD ID", "root": "data"}))
        self.assertEqual(r["code"], "bad_root")

    def test_page_calls_get_no_config_root_but_backends_do(self):
        (self.out / "c").mkdir()
        (self.out / "c" / "settings.json").write_text("{}")
        h = self.host([])
        r = asyncio.run(fy_files.METHODS["files_list"](h, {"pid": "fan", "root": "config"}))
        self.assertEqual(r["code"], "bad_root")
        r = asyncio.run(fy_files.hostcall(h, "fan", {"root": "config"}))
        self.assertEqual([e["name"] for e in r["entries"]], ["settings.json"])
        r = asyncio.run(fy_files.METHODS["files_list"](h, {"pid": "fan"}))
        self.assertNotIn("config", [x["id"] for x in r["roots"]])

    def test_directories_follow_the_passed_pid_never_the_argument(self):
        asked = []
        h = self.host([])
        h.dirs = lambda pid: asked.append(pid) or types.SimpleNamespace(config=None, data=self.out, runtime=None, logs=None)
        asyncio.run(fy_files.hostcall(h, "fan", {"pid": "victim", "root": "data"}))
        asyncio.run(fy_files.hostcall(h, "fan", {"root": "data", "pid": "other"}))
        self.assertEqual(asked, ["fan", "fan"])
        asyncio.run(fy_files.METHODS["files_list"](h, {"pid": "authenticated", "root": "data"}))
        self.assertEqual(asked[-1], "authenticated")

    def test_extra_root_from_prefs(self):
        r = asyncio.run(fy_files.hostcall(self.host([str(self.out)]), "fan", {"root": "outside"}))
        self.assertEqual([e["name"] for e in r["entries"]], ["secret.txt"])

    def test_settings_failure_is_harmless(self):
        h = self.host([])
        h.settings = mock.Mock(side_effect=RuntimeError)
        r = asyncio.run(fy_files.hostcall(h, "fan", {}))
        self.assertEqual(r["root"], "data")
        r = asyncio.run(fy_files.hostcall(h, "fan", "junk"))
        self.assertEqual(r["root"], "data")


if __name__ == "__main__":
    unittest.main()
