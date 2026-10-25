import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import jharness as H
import fy_state
import test_fy_fixes_a as F


class PickerFlow(F.Base):
    async def start(self, file_roots=None):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a", {"api": 1}, main="window.RAN_a=1", backend=F.BACKEND % False)
        loader.fy.load_backends()
        if file_roots is not None:
            base = pathlib.Path(tempfile.mkdtemp(prefix="fyroot"))
            self.addCleanup(shutil.rmtree, base, True)
            extra = base / "share"
            (extra / "sub").mkdir(parents=True)
            (extra / "sub" / "pic.png").write_bytes(b"x")
            patch = mock.patch.object(fy_state, "ROOT_BASES", fy_state.ROOT_BASES + (base.as_posix() + "/",))
            patch.start()
            self.addCleanup(patch.stop)
            (loader.fy.CONF.parent / "loader.json").write_text(json.dumps({"file_roots": [extra.as_posix()]}))
            self.extra = extra
        loader.fy.plugin_args(loader.fy.manifest(loader.fy.PLUGINS / "a"))
        return loader

    async def test_the_picker_lists_roots_first_then_browses_and_the_pick_resolves(self):
        loader = await self.start(file_roots=True)
        extra = self.extra
        fy = loader.fy
        data = fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        (data / "own.txt").write_text("own")
        page, _ = await self.attach(loader)
        tok = F.token_of(fy, "a")
        roots = await self.call(page, id=1, gen="g", plugin="_core", method="files_list", arg={"path": "", "dirs_only": False, "pid": "a"}, t=tok)
        self.assertNotIn("error", roots)
        self.assertEqual(roots["entries"], [])
        ids = [r["id"] for r in roots["roots"]]
        self.assertIn("data", ids)
        self.assertIn(extra.name, ids)
        self.assertNotIn("home", ids)
        self.assertNotIn("media", ids)
        self.assertTrue(all(r["label"] for r in roots["roots"]))
        root = next(r["id"] for r in roots["roots"] if r["id"] == extra.name)
        listed = await self.call(page, id=2, gen="g", plugin="_core", method="files_list", arg={"root": root, "path": "", "dirs_only": False, "pid": "a"}, t=tok)
        self.assertEqual([(e["name"], e["type"]) for e in listed["entries"]], [("sub", "dir")])
        inside = await self.call(page, id=3, gen="g", plugin="_core", method="files_list", arg={"root": root, "path": "sub", "dirs_only": False, "pid": "a"}, t=tok)
        self.assertEqual([e["name"] for e in inside["entries"]], ["pic.png"])
        picked = await self.call(page, id=4, gen="g", plugin="_core", method="resource", arg={"pid": "a", "root": "data", "path": "own.txt"}, t=tok)
        self.assertEqual(picked.get("kind"), "data")

    async def test_the_plugin_token_is_refused_outside_its_roots(self):
        loader = await self.start()
        fy = loader.fy
        fy.dirs("a").data.mkdir(parents=True, exist_ok=True)
        page, _ = await self.attach(loader)
        tok = F.token_of(fy, "a")
        win = [{"pid": "a", "root": "C:/Windows"}, {"pid": "a", "root": r"C:\Windows"}, {"pid": "a", "root": "home"}, {"pid": "a", "root": "media"}, {"pid": "a", "root": "etc"}, {"pid": "a", "root": "../"}, {"pid": "a", "root": 5}]
        for i, arg in enumerate(win):
            r = await self.call(page, id=i + 1, gen="g", plugin="_core", method="files_list", arg=arg, t=tok)
            self.assertEqual(r, {"error": "not authorized"}, arg)

    async def test_paths_that_leave_the_root_are_refused(self):
        loader = await self.start()
        fy = loader.fy
        data = fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        outside = pathlib.Path(tempfile.mkdtemp(prefix="fyout"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "secret.txt").write_text("SECRET")
        try:
            os.symlink(outside, data / "esc", target_is_directory=True)
        except (OSError, NotImplementedError):
            if subprocess.run(["cmd", "/c", "mklink", "/J", str(data / "esc"), str(outside)], capture_output=True).returncode:
                self.skipTest("symlinks not permitted here")
        page, _ = await self.attach(loader)
        tok = F.token_of(fy, "a")
        bad = ["../", "..", "sub/../..", "/etc", "C:/Windows", r"C:\Windows", r"a\..\..", "esc", "esc/secret.txt"]
        for i, path in enumerate(bad):
            r = await self.call(page, id=i + 1, gen="g", plugin="_core", method="files_list", arg={"root": "data", "path": path, "pid": "a"}, t=tok)
            self.assertIn(r.get("code"), ("bad_path", "outside_root", "not_found"), path)
            self.assertNotIn("SECRET", json.dumps(r))
        r = await self.call(page, id=50, gen="g", plugin="_core", method="resource", arg={"pid": "a", "root": "data", "path": "esc/secret.txt"}, t=tok)
        self.assertNotIn("SECRET", json.dumps(r))
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
