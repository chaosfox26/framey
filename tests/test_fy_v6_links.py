import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import framey


class KeptCopyLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plugins = self.tmp / "plugins"
        self.plugins.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "plugin.json").write_text("{}")
        self.dest = self.plugins / "demo"
        self.dest.mkdir()
        (self.dest / "plugin.json").write_text('{"version": "2"}')
        for p in (mock.patch.object(framey, "PLUGINS", self.plugins), mock.patch.object(framey, "log"), mock.patch.object(framey, "heal_loader")):
            p.start()
            self.addCleanup(p.stop)

    def link(self, name, target):
        try:
            os.symlink(target, self.plugins / name, target_is_directory=True)
        except (OSError, NotImplementedError):
            raise unittest.SkipTest("symlinks unavailable")

    def test_restore_refuses_symlinked_kept_copy(self):
        self.link(".old-demo", self.outside)
        with self.assertRaises(OSError):
            framey.restore(self.dest)
        self.assertFalse(self.dest.is_symlink())
        self.assertEqual((self.dest / "plugin.json").read_text(), '{"version": "2"}')
        self.assertTrue((self.plugins / ".old-demo").is_symlink())
        self.assertTrue((self.outside / "plugin.json").is_file())

    def test_restore_refuses_junction_like_kept_copy(self):
        old = self.plugins / ".old-demo"
        old.mkdir()
        (old / "plugin.json").write_text("{}")
        with mock.patch.object(type(old), "is_junction", lambda p: p.name == ".old-demo", create=True):
            with self.assertRaises(OSError):
                framey.restore(self.dest)
        self.assertTrue(old.is_dir())
        self.assertEqual((self.dest / "plugin.json").read_text(), '{"version": "2"}')

    def test_recover_leaves_symlinked_kept_copy_alone(self):
        self.dest.rename(self.tmp / "gone")
        self.link(".old-demo", self.outside)
        framey.recover()
        self.assertFalse((self.plugins / "demo").exists())
        self.assertTrue((self.plugins / ".old-demo").is_symlink())
        self.assertTrue((self.outside / "plugin.json").is_file())

    def test_recover_restores_orphan_hold_copy(self):
        self.dest.rename(self.plugins / ".hold-demo")
        framey.recover()
        self.assertTrue((self.plugins / ".old-demo" / "plugin.json").is_file())
        self.assertFalse((self.plugins / ".hold-demo").exists())


if __name__ == "__main__":
    unittest.main()
