import asyncio
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_state as fs


class StatePersistenceParity(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.json"

    def test_cold_save_preserves_corrupt_settings_until_explicit_repair(self):
        original = b'{"disabled":["demo"],broken'
        self.path.write_bytes(original)
        fs._UNREADABLE.pop(os.path.abspath(str(self.path)), None)
        with self.assertRaises(fs.StateError) as caught:
            fs.save(self.path, fs.defaults())
        self.assertEqual(caught.exception.code, "unreadable")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.with_suffix(".json.bad").read_bytes(), original)
        self.assertEqual(fs.repair(self.path), {"ok": True})
        fs.save(self.path, fs.defaults())
        self.assertFalse(fs.load(self.path)["repaired"])

    def test_corruption_after_load_does_not_get_overwritten_by_stale_state(self):
        fs.save(self.path, fs.defaults())
        pending = fs.load(self.path)
        fs.set_hidden(pending, "demo", True)
        self.path.write_bytes(b"damaged after read")
        with self.assertRaises(fs.StateError) as caught:
            fs.save(self.path, pending)
        self.assertEqual(caught.exception.code, "unreadable")
        self.assertEqual(self.path.read_bytes(), b"damaged after read")

    def test_foreign_data_that_cannot_roundtrip_never_replaces_settings(self):
        fs.save(self.path, fs.defaults())
        before = self.path.read_bytes()
        recursive = []
        recursive.append(recursive)
        for value in (float("nan"), float("inf"), float("-inf"), "\ud800", object(), recursive):
            with self.subTest(kind=type(value).__name__):
                pending = fs.defaults()
                pending["future"] = value
                with self.assertRaises(fs.StateError) as caught:
                    fs.save(self.path, pending)
                self.assertEqual(caught.exception.code, "bad_state")
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertFalse(fs.load(self.path)["repaired"])

    def test_encoder_depth_limit_is_a_state_error(self):
        fs.save(self.path, fs.defaults())
        before = self.path.read_bytes()
        with mock.patch.object(fs.json, "dumps", side_effect=RecursionError):
            with self.assertRaises(fs.StateError) as caught:
                fs.save(self.path, fs.defaults())
        self.assertEqual(caught.exception.code, "bad_state")
        self.assertEqual(self.path.read_bytes(), before)

    def test_bad_copy_is_private_and_the_first_copy_is_preserved(self):
        self.path.write_bytes(b"first damaged settings")
        fs.load(self.path)
        bad = self.path.with_suffix(".json.bad")
        self.assertEqual(stat.S_IMODE(bad.stat().st_mode), 0o600)
        self.path.write_bytes(b"second damaged settings")
        fs.load(self.path)
        self.assertEqual(bad.read_bytes(), b"first damaged settings")

    def test_bad_copy_never_follows_an_existing_symlink(self):
        target = self.path.parent / "other"
        target.write_bytes(b"keep")
        self.path.with_suffix(".json.bad").symlink_to(target)
        self.path.write_bytes(b"damaged settings")
        fs.load(self.path)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_successful_repair_survives_backup_cleanup_failure(self):
        self.path.write_bytes(b"damaged settings")
        with mock.patch.object(Path, "iterdir", side_effect=OSError("unavailable")):
            self.assertEqual(fs.repair(self.path), {"ok": True})
        self.assertFalse(self.path.exists())
        self.assertEqual(fs.state(self.path), "ok")
        backups = list(self.path.parent.glob("settings.json.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"damaged settings")

    def test_unrelated_symlinks_do_not_consume_recovery_backup_slots(self):
        for stamp in (1000, 1001, 1002):
            self.path.with_name("settings.json.corrupt-%d" % stamp).write_bytes(b"previous")
        unrelated = self.path.parent / "unrelated"
        unrelated.write_bytes(b"keep")
        link = self.path.with_name("settings.json.corrupt-9999999999999")
        link.symlink_to(unrelated)
        self.path.write_bytes(b"damaged settings")
        self.assertEqual(fs.repair(self.path), {"ok": True})
        self.assertTrue(self.path.with_name("settings.json.corrupt-1001").is_file())
        self.assertTrue(self.path.with_name("settings.json.corrupt-1002").is_file())
        self.assertTrue(link.is_symlink())
        self.assertEqual(unrelated.read_bytes(), b"keep")


class ModuleOrderingParity(unittest.TestCase):
    def test_reserved_navigation_ids_are_removed_from_plugin_state(self):
        ids = ["demo", "_core", "core", "modules", "settings", "store", "discover", "developer", "dev", "decky-store"]
        state, repaired = fs.validate({
            "disabled": ids,
            "modules": {
                "order": ids,
                "hidden": ids,
                "frozen": {pid: {"at": 1, "version": "1"} for pid in ids},
                "state": {pid: {"ver": "1"} for pid in ids},
            },
            "prefs": {"dev": {"hot": ids, "targets": {"store": "loopback"}}},
            "updates": {
                "plugins": {pid: {"latest": "2"} for pid in ids},
                "seen": {"plugins": {pid: "2" for pid in ids}},
            },
        })
        self.assertTrue(repaired)
        self.assertEqual(state["disabled"], ["demo"])
        self.assertEqual(fs.effective_order([(pid, pid) for pid in ids], state), ["demo"])
        for key in ("order", "hidden"):
            self.assertEqual(state["modules"][key], ["demo"])
        for key in ("frozen", "state"):
            self.assertEqual(list(state["modules"][key]), ["demo"])
        self.assertEqual(state["prefs"]["dev"]["hot"], ["demo"])
        self.assertEqual(state["prefs"]["dev"]["targets"], {"store": "loopback"})
        self.assertEqual(list(state["updates"]["plugins"]), ["demo"])
        self.assertEqual(state["updates"]["seen"]["plugins"], {"demo": "2"})

    def test_reserved_plugin_setters_reject_without_mutating_state(self):
        state = fs.defaults()
        for pid in ("_core", "core", "modules", "settings", "store", "discover", "developer", "dev", "decky-store"):
            for setter in (
                lambda: fs.set_order(state, [pid]),
                lambda: fs.set_hidden(state, pid, True),
                lambda: fs.set_frozen(state, pid, True, "1", at=1),
                lambda: fs.set_module_state(state, pid, {"ver": "1"}),
                lambda: fs.set_pref(state, "dev.hot", [pid]),
            ):
                with self.subTest(pid=pid), self.assertRaises(fs.StateError):
                    setter()
        self.assertEqual(state, fs.defaults())

    def test_internal_loader_sentinel_and_non_plugin_target_names_remain_valid(self):
        state = fs.defaults()
        fs.set_module_state(state, "_loader", {"ver": "1"})
        fs.set_pref(state, "dev.targets", {"core": "loopback", "store": "loopback", "decky-store": "loopback"})
        validated, repaired = fs.validate(state)
        self.assertFalse(repaired)
        self.assertEqual(validated["modules"]["state"]["_loader"]["ver"], "1")
        self.assertEqual(validated["prefs"]["dev"]["targets"], {"core": "loopback", "store": "loopback", "decky-store": "loopback"})

    def test_similar_decky_plugin_ids_keep_their_persisted_state(self):
        state = fs.defaults()
        ids = ["decky-style", "my-decky-store", "decky-store-extra"]
        fs.set_order(state, ids)
        fs.set_pref(state, "dev.hot", ids)
        for pid in ids:
            fs.set_hidden(state, pid, True)
            fs.set_frozen(state, pid, True, "1", at=1)
            fs.set_module_state(state, pid, {"ver": "1"})
        validated, repaired = fs.validate(state)
        self.assertFalse(repaired)
        self.assertEqual(validated, state)

    def test_bad_move_arguments_return_errors_without_changing_saved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            fs.save(path, fs.defaults())
            host = types.SimpleNamespace(CONF=path, manifests=lambda: [{"id": "demo", "name": "Demo"}])
            before = path.read_bytes()
            for pid, delta in (([], 1), ({}, -1), (True, 1), ("demo", 1.0), ("demo", -1.0)):
                with self.subTest(pid=pid, delta=delta):
                    reply = asyncio.run(fs.METHODS["mod_order"](host, {"id": pid, "delta": delta}))
                    self.assertIn("error", reply)
                    self.assertIn(reply["code"], ("bad_id", "bad_value"))
                    self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
