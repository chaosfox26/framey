import errno
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_store


class StoragePreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store-preserve-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def cross_device(self, src):
        real_replace = os.replace

        def replace(a, b):
            if Path(a) == src:
                raise OSError(errno.EXDEV, "cross-device")
            return real_replace(a, b)

        return mock.patch.object(fy_store.os, "replace", replace)

    def test_same_size_copy_corruption_keeps_source_and_old_destination(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        src.write_bytes(b"good")
        dst.write_bytes(b"previous")

        def corrupt(a, b, length=0):
            b.write(b"bad!")

        with self.cross_device(src), mock.patch.object(fy_store.shutil, "copyfileobj", corrupt):
            result = fy_store.migrate_move(src, dst, overwrite=True)
        self.assertEqual(result, {"moved": False, "reason": "error:EIO"})
        self.assertEqual(src.read_bytes(), b"good")
        self.assertEqual(dst.read_bytes(), b"previous")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["destination", "source"])

    def test_same_size_directory_copy_corruption_keeps_source(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        src.mkdir()
        (src / "file").write_bytes(b"good")

        def corrupt(a, b, length=0):
            b.write(b"bad!")

        with self.cross_device(src), mock.patch.object(fy_store.shutil, "copyfileobj", corrupt):
            result = fy_store.migrate_move(src, dst)
        self.assertEqual(result, {"moved": False, "reason": "error:EIO"})
        self.assertEqual((src / "file").read_bytes(), b"good")
        self.assertFalse(dst.exists())

    def test_source_change_during_copy_is_not_committed(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        src.write_bytes(b"good")

        def change(a, b, length=0):
            b.write(a.read())
            src.write_bytes(b"late")

        with self.cross_device(src), mock.patch.object(fy_store.shutil, "copyfileobj", change):
            result = fy_store.migrate_move(src, dst)
        self.assertEqual(result, {"moved": False, "reason": "error:EIO"})
        self.assertEqual(src.read_bytes(), b"late")
        self.assertFalse(dst.exists())

    def test_copy_source_may_have_the_old_predictable_temporary_name(self):
        src, dst = self.tmp / "destination.fy-part", self.tmp / "destination"
        src.write_bytes(b"keep")
        with self.cross_device(src):
            result = fy_store.migrate_move(src, dst)
        self.assertEqual(result, {"moved": True, "reason": "ok"})
        self.assertEqual(dst.read_bytes(), b"keep")
        self.assertFalse(src.exists())

    def test_copy_once_does_not_delete_an_unrelated_partial_directory(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        src.write_bytes(b"copy")
        partial = self.tmp / "destination.fy-part"
        partial.mkdir()
        (partial / "keep").write_bytes(b"unrelated")
        self.assertTrue(fy_store.copy_once(src, dst, "done"))
        self.assertEqual((partial / "keep").read_bytes(), b"unrelated")

    def test_failed_nested_move_does_not_create_directories_in_source(self):
        src = self.tmp / "source"
        src.mkdir()
        (src / "keep").write_bytes(b"keep")
        dst = src / "new" / "destination"
        result = fy_store.migrate_move(src, dst)
        self.assertEqual(result, {"moved": False, "reason": "error:EINVAL"})
        self.assertEqual([p.name for p in src.iterdir()], ["keep"])

    def test_copy_once_refuses_nested_destination_before_creating_it(self):
        src = self.tmp / "source"
        src.mkdir()
        (src / "keep").write_bytes(b"keep")
        with self.assertRaises(OSError) as caught:
            fy_store.copy_once(src, src / "new" / "destination", "done")
        self.assertEqual(caught.exception.errno, errno.EINVAL)
        self.assertEqual([p.name for p in src.iterdir()], ["keep"])

    def test_directory_copy_syncs_each_file_before_source_removal(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        (src / "nested").mkdir(parents=True)
        (src / "nested" / "file").write_bytes(b"keep")
        with self.cross_device(src), mock.patch.object(fy_store.os, "fsync", side_effect=OSError(errno.EIO, "sync failed")):
            result = fy_store.migrate_move(src, dst)
        self.assertFalse(result["moved"])
        self.assertTrue(result["reason"].startswith("error:"))
        self.assertEqual((src / "nested" / "file").read_bytes(), b"keep")
        self.assertFalse(dst.exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory sync")
    def test_failed_destination_directory_sync_keeps_source(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        src.write_bytes(b"keep")
        real_fsync = os.fsync

        def fail_directory(fd):
            import stat

            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "directory sync failed")
            return real_fsync(fd)

        with self.cross_device(src), mock.patch.object(fy_store.os, "fsync", fail_directory):
            result = fy_store.migrate_move(src, dst)
        self.assertEqual(result, {"moved": False, "reason": "error:EIO"})
        self.assertEqual(src.read_bytes(), b"keep")
        self.assertEqual(dst.read_bytes(), b"keep")

    def test_invalid_later_key_does_not_partially_rename_settings(self):
        settings = {"old": 1}
        with self.assertRaises(ValueError):
            fy_store.migrate_keys(settings, {"old": "new", "later": ""})
        self.assertEqual(settings, {"old": 1})

    def test_oversized_existing_settings_are_not_truncated_into_a_recovery_copy(self):
        path = self.tmp / "settings.json"
        original = json.dumps({"large": "x" * 100}).encode()
        path.write_bytes(original)
        with self.assertRaises(ValueError):
            fy_store.Settings(path, max_bytes=32).set("new", 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(Path(str(path) + ".bad").exists())

    def test_failed_recovery_copy_preserves_corrupt_settings(self):
        path = self.tmp / "settings.json"
        path.write_bytes(b"{broken")
        with mock.patch.object(fy_store.os, "fsync", side_effect=OSError(errno.ENOSPC, "full")):
            with self.assertRaises(OSError):
                fy_store.Settings(path).set("new", 1)
        self.assertEqual(path.read_bytes(), b"{broken")

    def test_non_file_recovery_destination_preserves_corrupt_settings(self):
        path = self.tmp / "settings.json"
        path.write_bytes(b"{broken")
        Path(str(path) + ".bad").mkdir()
        with self.assertRaises(OSError):
            fy_store.Settings(path).set("new", 1)
        self.assertEqual(path.read_bytes(), b"{broken")

    @unittest.skipUnless(os.name == "posix", "requires POSIX file modes")
    def test_recovery_copy_is_private_even_with_permissive_umask(self):
        import stat

        path = self.tmp / "settings.json"
        path.write_bytes(b"{broken")
        old_umask = os.umask(0)
        try:
            fy_store.Settings(path).set("new", 1)
        finally:
            os.umask(old_umask)
        self.assertEqual(stat.S_IMODE(Path(str(path) + ".bad").stat().st_mode), 0o600)

    def test_different_existing_recovery_copy_does_not_allow_losing_new_corruption(self):
        path = self.tmp / "settings.json"
        path.write_bytes(b"{new broken")
        recovery = Path(str(path) + ".bad")
        recovery.write_bytes(b"{old broken")
        with self.assertRaises(OSError):
            fy_store.Settings(path).set("new", 1)
        self.assertEqual(path.read_bytes(), b"{new broken")
        self.assertEqual(recovery.read_bytes(), b"{old broken")

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_dangling_recovery_link_is_not_followed(self):
        path = self.tmp / "settings.json"
        path.write_bytes(b"{broken")
        victim = self.tmp / "untouched"
        Path(str(path) + ".bad").symlink_to(victim)
        with self.assertRaises(OSError):
            fy_store.Settings(path).set("new", 1)
        self.assertEqual(path.read_bytes(), b"{broken")
        self.assertFalse(victim.exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_linked_snapshot_is_not_accepted_as_a_rollback_copy(self):
        path = self.tmp / "settings.json"
        path.write_bytes(b'{"old":1}')
        victim = self.tmp / "unrelated"
        victim.write_bytes(b'{"other":2}')
        (self.tmp / "settings.json.premigrate").symlink_to(victim)
        with self.assertRaises(OSError):
            fy_store.snapshot_settings(self.tmp)
        with self.assertRaises(OSError):
            fy_store.restore_snapshot(self.tmp)
        self.assertEqual(path.read_bytes(), b'{"old":1}')
        self.assertEqual(victim.read_bytes(), b'{"other":2}')

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_snapshot_operations_refuse_a_linked_config_directory(self):
        config = fy_store.dirs("demo", home=self.tmp).config
        fy_store.ensure(config.parent)
        outside = self.tmp / "outside"
        outside.mkdir()
        settings = outside / "settings.json"
        settings.write_bytes(b'{"old":1}')
        snapshot = outside / "settings.json.premigrate"
        snapshot.write_bytes(b'{"other":2}')
        config.symlink_to(outside, target_is_directory=True)
        for operation in (fy_store.snapshot_settings, fy_store.restore_snapshot, fy_store.drop_snapshot):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(PermissionError):
                    operation(config)
        self.assertEqual(settings.read_bytes(), b'{"old":1}')
        self.assertEqual(snapshot.read_bytes(), b'{"other":2}')

    def test_concurrent_copy_once_commits_one_copy_and_one_marker(self):
        src, dst = self.tmp / "source", self.tmp / "destination"
        src.write_bytes(b"copy")
        results, errors = [], []
        barrier = threading.Barrier(6)

        def copy():
            try:
                barrier.wait(5)
                results.append(fy_store.copy_once(src, dst, "done"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=copy) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False] * 5 + [True])
        self.assertEqual(dst.read_bytes(), b"copy")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["destination", "done", "source"])


class StorageLogAndDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store-log-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_small_log_caps_preserve_valid_utf8(self):
        log = fy_store.Log("demo", self.tmp, max_bytes=44)
        log.info("é" * 100)
        data = (self.tmp / "plugin.log").read_bytes()
        self.assertLessEqual(len(data), 44)
        self.assertTrue(data.endswith(b"\n"))
        self.assertIn("INFO", data.decode("utf-8"))

    def test_invalid_log_caps_fail_before_any_file_is_written(self):
        for size in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                fy_store.Log("demo", self.tmp, max_bytes=size)
        self.assertEqual(list(self.tmp.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_log_file_link_never_appends_to_target(self):
        victim = self.tmp / "keep"
        victim.write_bytes(b"keep")
        (self.tmp / "plugin.log").symlink_to(victim)
        fy_store.Log("demo", self.tmp).info("must not append")
        self.assertEqual(victim.read_bytes(), b"keep")

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_log_link_injected_between_check_and_open_is_not_followed(self):
        victim = self.tmp / "keep"
        victim.write_bytes(b"keep")
        log_path = self.tmp / "plugin.log"
        real_open = os.open

        def inject(path, flags, *args, **kwargs):
            if Path(path) == log_path:
                log_path.symlink_to(victim)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(fy_store.os, "open", inject):
            fy_store.Log("demo", self.tmp).info("must not append")
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_distinct_log_instances_serialize_rotation(self):
        logs = [fy_store.Log("demo", self.tmp, max_bytes=500, keep=3, per_second=10**9) for _ in range(4)]
        errors = []

        def write(log):
            try:
                for n in range(100):
                    log.info("%s %s", n, "x" * 40)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(log,)) for log in logs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["plugin.log", "plugin.log.1", "plugin.log.2"])
        for path in self.tmp.iterdir():
            self.assertLessEqual(path.stat().st_size, 500)
            for line in path.read_text().splitlines():
                self.assertRegex(line, r"^\S+ INFO \d+ x{40}$")

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_ensure_rejects_link_injected_when_directory_creation_loses_a_race(self):
        root = self.tmp / "framey"
        root.mkdir()
        outside = self.tmp / "outside"
        outside.mkdir()
        path = root / "demo"
        real_mkdir = Path.mkdir

        def inject(p, *args, **kwargs):
            if p == path:
                p.symlink_to(outside, target_is_directory=True)
                raise FileExistsError(p)
            return real_mkdir(p, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", inject):
            with self.assertRaises(PermissionError):
                fy_store.ensure(path)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "requires POSIX ownership")
    def test_purge_refuses_foreign_owned_plugin_directory(self):
        dirs = fy_store.dirs("demo", home=self.tmp)
        fy_store.ensure(dirs.data)
        (dirs.data / "keep").write_bytes(b"keep")
        real_lstat = os.lstat

        def foreign(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if Path(path) == dirs.data:
                fields = list(result)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return result

        with mock.patch.object(fy_store.os, "lstat", foreign):
            result = fy_store.purge(dirs, ("data",))
        self.assertEqual(result, {"removed": [], "failed": [{"name": "data", "reason": "unsafe_directory"}]})
        self.assertEqual((dirs.data / "keep").read_bytes(), b"keep")

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_clean_tmp_refuses_a_linked_framey_parent(self):
        outside = self.tmp / "outside"
        runtime = outside / "demo"
        runtime.mkdir(parents=True)
        (runtime / "tmp-keep").write_bytes(b"keep")
        (self.tmp / "framey").symlink_to(outside, target_is_directory=True)
        self.assertEqual(fy_store.clean_tmp(self.tmp / "framey" / "demo"), 0)
        self.assertEqual((runtime / "tmp-keep").read_bytes(), b"keep")

    def test_clean_tmp_tolerates_runtime_disappearing_before_scan(self):
        with mock.patch.object(fy_store.os, "scandir", side_effect=FileNotFoundError()):
            self.assertEqual(fy_store.clean_tmp(self.tmp), 0)

    @unittest.skipUnless(os.name == "posix", "requires POSIX links")
    def test_discard_held_refuses_a_linked_plugin_root(self):
        outside = self.tmp / "outside"
        held = outside / ".old-demo"
        held.mkdir(parents=True)
        (held / "keep").write_bytes(b"keep")
        root = self.tmp / "plugins"
        root.symlink_to(outside, target_is_directory=True)
        fy_store.discard_held(root, "demo")
        self.assertEqual((held / "keep").read_bytes(), b"keep")

    @unittest.skipUnless(os.name == "posix", "requires POSIX FIFO")
    def test_log_fifo_does_not_block_the_calling_thread(self):
        os.mkfifo(self.tmp / "plugin.log")
        thread = threading.Thread(target=fy_store.Log("demo", self.tmp).info, args=("entry",), daemon=True)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
