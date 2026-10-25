import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent / "fixtures" / "j"))
import jharness as H


class LoaderReleaseSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loader = H.Loader()
        self.addAsyncCleanup(self.loader.close)

    async def test_reviewed_release_movement_is_refused_before_archive_download(self):
        loader = self.loader
        loader.net.json("chaosfox26/framey/releases/latest", {"tag_name": "v2.0.0", "body": ""})
        loader.net.commit("chaosfox26", "framey", "v2.0.0", "c" * 40)
        reply = await loader.core("loader_update", {"candidate": {"tag": "v1.1.0", "commit": "b" * 40}, "confirm": True})
        self.assertIn("changed", reply["error"])
        self.assertFalse(any("/archive/" in url for url in loader.net.calls))
        self.assertFalse(loader.root.with_name("framey.bak").exists())

    async def test_malformed_reviewed_selection_is_refused_without_network(self):
        reply = await self.loader.core("loader_update", {"candidate": {"tag": "v2.0.0", "commit": "not-a-commit"}})
        self.assertIn("Invalid", reply["error"])
        self.assertEqual(self.loader.net.calls, [])

    async def test_update_preserves_unowned_sibling_directory_before_network(self):
        stage = self.loader.root.with_name("framey.new")
        stage.mkdir()
        note = stage / "unrelated.txt"
        note.write_text("keep these files")
        reply = await self.loader.fy.install_loader("v2.0.0", "b" * 40)
        self.assertIn("occupied", reply["error"])
        self.assertEqual(note.read_text(), "keep these files")
        self.assertEqual(self.loader.net.calls, [])

    async def test_startup_recovery_preserves_unowned_sibling_directory(self):
        stage = self.loader.root.with_name("framey.new")
        stage.mkdir()
        (stage / "unrelated.txt").write_text("keep")
        self.loader.fy.heal_loader()
        self.assertEqual((stage / "unrelated.txt").read_text(), "keep")

    async def test_startup_recovery_removes_owned_abandoned_stage(self):
        stage = self.loader.root.with_name("framey.new")
        stage.mkdir()
        (stage / ".framey-stage").write_bytes(b"framey-update-stage-v1\n")
        (stage / "partial.txt").write_text("incomplete")
        self.loader.fy.heal_loader()
        self.assertFalse(stage.exists())
