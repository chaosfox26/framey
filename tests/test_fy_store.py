import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_store

POSIX = os.name == "posix"
POSIX_ONLY = "file modes and ownership checks are POSIX only; run on Linux: python3 -I -m unittest discover -s tests -p test_fy_store.py"


def link_dir(target, link):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def can(fn):
    d = tempfile.mkdtemp()
    try:
        fn(d)
        return True
    except (OSError, NotImplementedError, subprocess.CalledProcessError):
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def probe_dir_link(d):
    os.mkdir(os.path.join(d, "t"))
    link_dir(os.path.join(d, "t"), os.path.join(d, "l"))


SYMLINKS = can(lambda d: os.symlink(d, os.path.join(d, "l")))
DIRLINKS = can(probe_dir_link)
SYMLINK_ONLY = "file symlinks need privilege on Windows (developer mode); run on Linux: python3 -I -m unittest discover -s tests -p test_fy_store.py"
DIRLINK_ONLY = "no directory link could be created here; run on Linux: python3 -I -m unittest discover -s tests -p test_fy_store.py"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fystore-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.run_base = self.tmp / "run"
        self.dirs = fy_store.dirs("demo", home=self.home, runtime_base=self.run_base)

    def names(self, d):
        return sorted(p.name for p in Path(d).iterdir())


class DirsTest(Base):
    def test_layout(self):
        d = self.dirs
        self.assertEqual(d.config, self.home / ".config" / "framey" / "plugins" / "demo")
        self.assertEqual(d.data, self.home / ".local" / "share" / "framey" / "demo")
        self.assertEqual(d.runtime, self.run_base / "framey" / "demo")
        self.assertEqual(d.logs, self.home / ".local" / "state" / "framey" / "demo")

    def test_no_runtime_without_xdg_and_no_tmp_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(fy_store, "runtime_root", return_value=None):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            d = fy_store.dirs("demo", home=self.home)
        self.assertIsNone(d.runtime)
        self.assertEqual(d.data, self.home / ".local" / "share" / "framey" / "demo")
        with self.assertRaisesRegex(RuntimeError, "XDG_RUNTIME_DIR"):
            fy_store.ensure(d.runtime)
        self.assertEqual(fy_store.clean_tmp(d.runtime), 0)
        self.assertEqual(fy_store.purge(d, ("runtime",)), {"removed": [], "failed": [{"name": "runtime", "reason": "unavailable"}]})

    def test_bad_ids(self):
        for bad in ("", "A", "a b", "../x", "a/b", "x" * 33, "é", "a\n", None, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                fy_store.dirs(bad, home=self.home, runtime_base=self.run_base)

    def test_nothing_created_by_dirs(self):
        self.assertFalse(self.home.exists())
        self.assertFalse(self.run_base.exists())

    def test_lazy_creation_on_first_write(self):
        s = fy_store.Settings(self.dirs.config / "settings.json")
        self.assertFalse(self.dirs.config.exists())
        self.assertEqual(s.get("a", 7), 7)
        self.assertFalse(self.dirs.config.exists())
        s.set("a", 1)
        self.assertTrue(self.dirs.config.is_dir())
        log = fy_store.Log("demo", self.dirs.logs)
        self.assertFalse(self.dirs.logs.exists())
        log.info("x")
        self.assertTrue((self.dirs.logs / "plugin.log").is_file())

    @unittest.skipUnless(POSIX, POSIX_ONLY)
    def test_lazy_creation_modes(self):
        fy_store.Settings(self.dirs.config / "settings.json").set("a", 1)
        fy_store.Log("demo", self.dirs.logs).info("x")
        fy_store.ensure(self.dirs.data)
        fy_store.ensure(self.dirs.runtime)
        for d in (self.dirs.config, self.dirs.data, self.dirs.runtime, self.dirs.logs, self.dirs.config.parent, self.dirs.data.parent):
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700, str(d))
        self.assertEqual(stat.S_IMODE((self.dirs.config / "settings.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.dirs.logs / "plugin.log").stat().st_mode), 0o600)

    def test_ensure_idempotent_and_file_conflict(self):
        p = fy_store.ensure(self.dirs.data)
        self.assertEqual(fy_store.ensure(p), p)
        f = self.tmp / "afile"
        f.write_text("x")
        with self.assertRaises(OSError):
            fy_store.ensure(f)

    @unittest.skipUnless(POSIX and DIRLINKS, POSIX_ONLY)
    def test_ensure_refuses_symlinked_anchor(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        base = self.tmp / "tmpbase"
        base.mkdir()
        link_dir(outside, base / "framey-1000")
        with self.assertRaises(PermissionError):
            fy_store.ensure(base / "framey-1000" / "demo")
        self.assertEqual(list(outside.iterdir()), [])


class JsonTest(Base):
    def test_read_defaults(self):
        p = self.tmp / "x.json"
        self.assertEqual(fy_store.read_json(p, "d"), "d")
        p.write_bytes(b"{bad")
        self.assertEqual(fy_store.read_json(p, "d"), "d")
        p.write_bytes(b"\xff\xfe")
        self.assertEqual(fy_store.read_json(p, "d"), "d")
        p.write_bytes(b"[" * 200000)
        self.assertEqual(fy_store.read_json(p, "d"), "d")
        p.write_text(json.dumps({"a": "x" * 100}))
        self.assertEqual(fy_store.read_json(p, "d", max_bytes=50), "d")
        self.assertEqual(fy_store.read_json(self.tmp, "d"), "d")

    def test_roundtrip_unicode(self):
        p = self.tmp / "sub" / "x.json"
        fy_store.write_json_atomic(p, {"k": "héllo ✓", "n": [1, 2.5, None, True]})
        self.assertEqual(fy_store.read_json(p), {"k": "héllo ✓", "n": [1, 2.5, None, True]})

    def test_rejects_unserialisable_and_oversize(self):
        p = self.tmp / "x.json"
        fy_store.write_json_atomic(p, {"ok": 1})
        for bad in ({"a": object()}, {"a": float("nan")}, {"a": "\ud800"}, {1, 2}):
            with self.assertRaises(ValueError):
                fy_store.write_json_atomic(p, bad)
        with self.assertRaises(ValueError):
            fy_store.write_json_atomic(p, {"a": "x" * 300000})
        self.assertEqual(fy_store.read_json(p), {"ok": 1})
        self.assertEqual(self.names(self.tmp), ["x.json"])

    def test_size_cap_boundary(self):
        p = self.tmp / "x.json"
        overhead = len(json.dumps({"a": ""}, separators=(",", ":")))
        fy_store.write_json_atomic(p, {"a": "x" * (50 - overhead)}, max_bytes=50)
        self.assertEqual(p.stat().st_size, 50)
        with self.assertRaises(ValueError):
            fy_store.write_json_atomic(p, {"a": "x" * (51 - overhead)}, max_bytes=50)
        self.assertEqual(p.stat().st_size, 50)

    def test_interrupted_replace_keeps_old_file(self):
        p = self.tmp / "x.json"
        fy_store.write_json_atomic(p, {"v": "old"})
        for exc in (OSError(errno.EIO, "boom"), KeyboardInterrupt()):
            with mock.patch.object(fy_store.os, "replace", side_effect=exc):
                with self.assertRaises(type(exc)):
                    fy_store.write_json_atomic(p, {"v": "new"})
            self.assertEqual(fy_store.read_json(p), {"v": "old"})
            self.assertEqual(self.names(self.tmp), ["x.json"])

    def test_interrupted_fsync_keeps_old_file(self):
        p = self.tmp / "x.json"
        fy_store.write_json_atomic(p, {"v": "old"})
        with mock.patch.object(fy_store.os, "fsync", side_effect=OSError(errno.EIO, "boom")):
            with self.assertRaises(OSError):
                fy_store.write_json_atomic(p, {"v": "new"})
        self.assertEqual(fy_store.read_json(p), {"v": "old"})
        self.assertEqual(self.names(self.tmp), ["x.json"])

    def test_interrupted_write_keeps_old_file(self):
        p = self.tmp / "x.json"
        fy_store.write_json_atomic(p, {"v": "old"})
        real = os.fdopen

        class Half:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()
                return False

            def write(self, data):
                self.f.write(data[: len(data) // 2])
                raise OSError(errno.ENOSPC, "disk full")

        def broken(fd, mode):
            return Half(real(fd, mode))

        with mock.patch.object(fy_store.os, "fdopen", broken):
            with self.assertRaises(OSError):
                fy_store.write_json_atomic(p, {"v": "new" * 100})
        self.assertEqual(fy_store.read_json(p), {"v": "old"})
        self.assertEqual(self.names(self.tmp), ["x.json"])


class SettingsTest(Base):
    def setUp(self):
        super().setUp()
        self.path = self.dirs.config / "settings.json"
        self.s = fy_store.Settings(self.path)

    def test_basic_ops(self):
        self.assertEqual(self.s.all(), {})
        self.s.set("a", 1)
        self.s.update({"b": [1, 2], "c": {"d": None}})
        self.assertEqual(self.s.get("b"), [1, 2])
        self.assertEqual(self.s.get("zz", "dflt"), "dflt")
        self.s.delete("a")
        self.s.delete("missing")
        self.assertEqual(self.s.all(), {"b": [1, 2], "c": {"d": None}})
        self.s.reset()
        self.assertEqual(self.s.all(), {})
        self.assertEqual(json.loads(self.path.read_text()), {})

    def test_all_returns_copy(self):
        self.s.set("a", [1])
        got = self.s.all()
        got["a"].append(2)
        self.assertEqual(self.s.get("a"), [1])

    def test_true_to_one_is_written(self):
        self.s.set("a", True)
        self.s.set("a", 1)
        self.assertIs(type(self.s.get("a")), int)

    def test_delete_missing_does_not_create_file(self):
        self.s.delete("nope")
        self.assertFalse(self.path.exists())

    def test_key_rules(self):
        for bad in ("", "k" * 65, 5, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.s.set(bad, 1)
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.s.update({bad: 1})
        self.s.set("k" * 64, 1)
        self.assertEqual(self.s.get("k" * 64), 1)

    def test_key_count_cap(self):
        self.s.update({"k%d" % i: i for i in range(200)})
        with self.assertRaises(ValueError):
            self.s.set("extra", 1)
        self.s.set("k5", "changed")
        self.assertEqual(len(self.s.all()), 200)
        with self.assertRaises(ValueError):
            self.s.update({"x1": 1, "x2": 2})
        self.assertNotIn("x1", self.s.all())

    def test_size_cap_keeps_old_data(self):
        self.s.set("a", "keep")
        with self.assertRaises(ValueError):
            self.s.set("big", "x" * 262144)
        self.assertEqual(self.s.all(), {"a": "keep"})
        small = fy_store.Settings(self.path, max_bytes=40)
        with self.assertRaises(ValueError):
            small.set("b", "y" * 40)
        self.assertEqual(self.s.all(), {"a": "keep"})

    def test_update_is_all_or_nothing(self):
        self.s.set("a", 1)
        with self.assertRaises(ValueError):
            self.s.update({"b": 2, "": 3})
        with self.assertRaises(ValueError):
            self.s.update({"b": 2, "c": object()})
        self.assertEqual(self.s.all(), {"a": 1})

    def test_corrupt_or_non_object_file_reads_empty(self):
        fy_store.ensure(self.dirs.config)
        self.path.write_text("[1, 2]")
        self.assertEqual(self.s.all(), {})
        self.path.write_text("{oops")
        self.assertEqual(self.s.get("a", "d"), "d")
        self.s.set("a", 1)
        self.assertEqual(self.s.all(), {"a": 1})

    def test_failed_write_changes_nothing(self):
        self.s.set("a", 1)
        with mock.patch.object(fy_store.os, "replace", side_effect=OSError(errno.EIO, "x")):
            with self.assertRaises(OSError):
                self.s.set("a", 2)
        self.assertEqual(self.s.get("a"), 1)

    def test_concurrent_set_from_two_threads(self):
        other = fy_store.Settings(self.path)
        errors = []

        def work(store, tag):
            try:
                for i in range(50):
                    store.set("%s%d" % (tag, i), i)
                    store.get("%s%d" % (tag, i))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=work, args=(self.s, "a")), threading.Thread(target=work, args=(other, "b"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        data = fy_store.read_json(self.path)
        self.assertEqual(len(data), 100)
        self.assertEqual(data["a49"], 49)
        self.assertEqual(data["b49"], 49)
        self.assertEqual(self.names(self.dirs.config), ["settings.json"])

    @unittest.skipUnless(POSIX, "readers outside the lock can block os.replace on Windows; run on Linux: python3 -I -m unittest discover -s tests -p test_fy_store.py")
    def test_concurrent_update_and_reset_never_corrupt(self):
        stop = threading.Event()
        bad = []

        def reader():
            while not stop.is_set():
                if fy_store.read_json(self.path, "missing") == "missing" and self.path.exists():
                    bad.append(1)

        self.s.set("seed", 1)
        r = threading.Thread(target=reader)
        r.start()
        try:
            for i in range(40):
                self.s.update({"k": i})
                if i % 10 == 0:
                    self.s.reset()
        finally:
            stop.set()
            r.join(10)
        self.assertEqual(bad, [])


class LogTest(Base):
    def setUp(self):
        super().setUp()
        self.clock = [100.0]

    def make(self, **kw):
        log = fy_store.Log("demo", self.dirs.logs, **kw)
        log._clock = lambda: self.clock[0]
        return log

    def files(self):
        if not self.dirs.logs.exists():
            return []
        return self.names(self.dirs.logs)

    def lines(self, name="plugin.log"):
        return (self.dirs.logs / name).read_text(encoding="utf-8").splitlines()

    def test_line_format_and_levels(self):
        log = self.make()
        log.debug("d")
        log.info("hello %s %d", "w", 3)
        log.warning("w")
        log.error("e")
        got = self.lines()
        self.assertEqual(len(got), 4)
        pat = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (DEBUG|INFO|WARNING|ERROR) ")
        for line in got:
            self.assertRegex(line, pat)
        self.assertTrue(got[1].endswith(" INFO hello w 3"))
        self.assertIn(" DEBUG d", got[0])
        self.assertIn(" ERROR e", got[3])

    def test_stream_gets_only_warning_and_error(self):
        import io

        stream = io.StringIO()
        log = self.make(stream=stream)
        log.debug("a")
        log.info("b")
        log.warning("c %s", 1)
        log.error("d")
        self.assertEqual(stream.getvalue(), "[demo] c 1\n[demo] d\n")

    def test_bad_format_and_odd_values_never_raise(self):
        log = self.make()
        log.info("%d", "notanumber")
        log.info("100% done")
        log.info("%s %s", 1)
        log.info(None)
        log.info(b"bytes")
        log.info("x", object())
        self.assertEqual(len(self.lines()), 6)

    def test_newlines_cannot_forge_lines(self):
        log = self.make()
        log.info("a\nERROR forged\r\nmore")
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("a\\nERROR forged\\r\\nmore", self.lines()[0])

    def test_line_cap(self):
        log = self.make()
        log.info("x" * 5000)
        log.info("é" * 5000)
        for line in self.lines():
            self.assertLessEqual(len(line), 2048)
        self.assertEqual(len(self.lines()), 2)

    def test_rotation_caps(self):
        log = self.make(max_bytes=1000, keep=3, per_second=10**9)
        for i in range(200):
            log.info("line %03d %s", i, "p" * 40)
        self.assertEqual(self.files(), ["plugin.log", "plugin.log.1", "plugin.log.2"])
        for name in self.files():
            self.assertLessEqual((self.dirs.logs / name).stat().st_size, 1000)
        self.assertIn("line 199", self.lines()[-1])
        first_old = int(self.lines("plugin.log.1")[0].split("line ")[1][:3])
        first_new = int(self.lines()[0].split("line ")[1][:3])
        self.assertLess(first_old, first_new)
        total = sum((self.dirs.logs / n).stat().st_size for n in self.files())
        self.assertLessEqual(total, 3000)

    def test_rotation_keep_one_and_two(self):
        for keep, expect in ((1, ["plugin.log"]), (2, ["plugin.log", "plugin.log.1"])):
            shutil.rmtree(self.dirs.logs, ignore_errors=True)
            log = self.make(max_bytes=300, keep=keep, per_second=10**9)
            for i in range(60):
                log.info("line %d %s", i, "p" * 40)
            self.assertEqual(self.files(), expect)

    def test_single_huge_line_still_bounded(self):
        log = self.make(max_bytes=100, per_second=10**9)
        log.info("x" * 500)
        self.assertLessEqual((self.dirs.logs / "plugin.log").stat().st_size, 100)

    def test_rate_limit(self):
        log = self.make()
        for i in range(50):
            log.info("n%d", i)
        self.assertEqual(len(self.lines()), 20)
        self.assertEqual(log.dropped, 30)
        self.clock[0] += 0.5
        log.info("still same window")
        self.assertEqual(len(self.lines()), 20)
        self.clock[0] += 1.0
        log.info("new window")
        self.assertEqual(len(self.lines()), 21)
        self.assertTrue(self.lines()[-1].endswith("new window"))

    def test_rate_limit_also_gates_stream(self):
        import io

        stream = io.StringIO()
        log = self.make(stream=stream, per_second=3)
        for i in range(10):
            log.error("e%d", i)
        self.assertEqual(stream.getvalue().count("\n"), 3)

    def test_clock_going_backwards_resets_window(self):
        log = self.make(per_second=2)
        log.info("a")
        log.info("b")
        log.info("c")
        self.clock[0] -= 50
        log.info("d")
        self.assertEqual(len(self.lines()), 3)

    def test_concurrent_writers_produce_whole_lines(self):
        log = fy_store.Log("demo", self.dirs.logs, per_second=10**9)

        def work(tag):
            for i in range(60):
                log.info("%s %d %s", tag, i, "z" * 30)

        threads = [threading.Thread(target=work, args=(t,)) for t in "abcd"]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        got = self.lines()
        self.assertEqual(len(got), 240)
        for line in got:
            self.assertRegex(line, r"INFO [abcd] \d+ z{30}$")

    def test_concurrent_writers_with_rotation_stay_bounded(self):
        log = fy_store.Log("demo", self.dirs.logs, max_bytes=2000, keep=3, per_second=10**9)

        def work():
            for i in range(100):
                log.info("%d %s", i, "z" * 40)

        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertLessEqual(len(self.files()), 3)
        for n in self.files():
            self.assertLessEqual((self.dirs.logs / n).stat().st_size, 2000)

    def test_close_stops_writing(self):
        log = self.make()
        log.info("a")
        log.close()
        log.info("b")
        log.close()
        self.assertEqual(len(self.lines()), 1)

    def test_unwritable_location_never_raises(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("x")
        log = fy_store.Log("demo", blocker / "sub")
        log.error("still fine")
        self.assertTrue(blocker.is_file())


class SnapshotTest(Base):
    def test_snapshot_restore_drop(self):
        cfg = self.dirs.config
        self.assertIsNone(fy_store.snapshot_settings(cfg))
        self.assertFalse(fy_store.restore_snapshot(cfg))
        s = fy_store.Settings(cfg / "settings.json")
        s.set("a", 1)
        snap = fy_store.snapshot_settings(cfg)
        self.assertEqual(snap, cfg / "settings.json.premigrate")
        s.set("a", 2)
        s.set("b", 3)
        self.assertEqual(fy_store.read_json(snap), {"a": 1})
        self.assertTrue(fy_store.restore_snapshot(cfg))
        self.assertEqual(s.all(), {"a": 1})
        self.assertTrue(snap.exists())
        s.set("a", 9)
        self.assertEqual(fy_store.snapshot_settings(cfg), snap)
        self.assertEqual(fy_store.read_json(snap), {"a": 1})
        fy_store.drop_snapshot(cfg)
        fy_store.drop_snapshot(cfg)
        self.assertFalse(snap.exists())
        fy_store.snapshot_settings(cfg)
        self.assertEqual(fy_store.read_json(snap), {"a": 9})
        fy_store.drop_snapshot(cfg)
        self.assertFalse(fy_store.restore_snapshot(cfg))
        self.assertEqual(self.names(cfg), ["settings.json"])


class RetrySnapshotTest(Base):
    def test_a_retry_keeps_the_original_snapshot_and_restore_brings_it_back(self):
        cfg = self.dirs.config
        s = fy_store.Settings(cfg / "settings.json")
        s.update({"old_key": 1, "keep": 2})
        snap = fy_store.snapshot_settings(cfg)
        s.delete("old_key")
        s.set("half_migrated", True)
        self.assertEqual(fy_store.snapshot_settings(cfg), snap)
        self.assertEqual(fy_store.read_json(snap), {"old_key": 1, "keep": 2})
        self.assertTrue(fy_store.restore_snapshot(cfg))
        self.assertEqual(s.all(), {"old_key": 1, "keep": 2})
        self.assertEqual(fy_store.read_json(snap), {"old_key": 1, "keep": 2})
        self.assertEqual(self.names(cfg), ["settings.json", "settings.json.premigrate"])


class NonFiniteJsonTest(Base):
    def test_nan_and_infinity_in_a_file_make_it_unreadable(self):
        p = self.dirs.config / "settings.json"
        p.parent.mkdir(parents=True)
        for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '[NaN]', '{"a": {"b": [1, NaN]}}'):
            with self.subTest(text=text):
                p.write_text(text)
                self.assertEqual(fy_store.read_json(p, "d"), "d")
                self.assertEqual(fy_store.Settings(p).all(), {})
        p.write_text('{"a": 1.5}')
        self.assertEqual(fy_store.read_json(p), {"a": 1.5})


class PurgeTest(Base):
    def fill(self):
        for name in fy_store.Dirs._fields:
            d = Path(getattr(self.dirs, name))
            fy_store.ensure(d)
            (d / "f.txt").write_text(name)
            (d / "sub").mkdir()
            (d / "sub" / "g.txt").write_text(name)

    def test_purge_all(self):
        self.fill()
        r = fy_store.purge(self.dirs)
        self.assertEqual(sorted(r["removed"]), ["config", "data", "logs", "runtime"])
        self.assertEqual(r["failed"], [])
        for name in fy_store.Dirs._fields:
            self.assertFalse(Path(getattr(self.dirs, name)).exists())
        self.assertTrue(self.dirs.config.parent.is_dir())
        self.assertEqual(fy_store.purge(self.dirs), {"removed": [], "failed": []})

    def test_purge_subset_and_unknown(self):
        self.fill()
        r = fy_store.purge(self.dirs, ("logs", "bogus"))
        self.assertEqual(r["removed"], ["logs"])
        self.assertEqual(r["failed"], [{"name": "bogus", "reason": "unknown"}])
        self.assertTrue(self.dirs.data.exists())

    def test_purge_leaves_siblings(self):
        self.fill()
        other = fy_store.dirs("other", home=self.home, runtime_base=self.run_base)
        for name in fy_store.Dirs._fields:
            fy_store.ensure(getattr(other, name))
        fy_store.purge(self.dirs)
        for name in fy_store.Dirs._fields:
            self.assertTrue(Path(getattr(other, name)).is_dir())

    def test_purge_refuses_foreign_layouts(self):
        self.home.mkdir()
        victim = self.tmp / "documents"
        victim.mkdir()
        (victim / "keep.txt").write_text("keep")
        ok = fy_store.Dirs(victim, victim, victim, victim)
        r = fy_store.purge(ok)
        self.assertEqual(r["removed"], [])
        self.assertEqual(len(r["failed"]), 4)
        self.assertTrue((victim / "keep.txt").exists())
        for forged in (self.home, self.tmp, Path(self.tmp.anchor or "/")):
            r = fy_store.purge(fy_store.Dirs(forged, forged, forged, forged))
            self.assertEqual(r["removed"], [])
            self.assertTrue(forged.exists())

    def test_purge_refuses_wrong_depth_for_config(self):
        d = self.home / "framey" / "demo"
        d.mkdir(parents=True)
        r = fy_store.purge(fy_store.Dirs(d, d, d, d), ("config",))
        self.assertEqual(r["failed"], [{"name": "config", "reason": "bad_path"}])
        self.assertTrue(d.exists())

    def test_purge_refuses_regular_file(self):
        fy_store.ensure(self.dirs.data.parent)
        self.dirs.data.write_text("not a dir")
        r = fy_store.purge(self.dirs, ("data",))
        self.assertEqual(r["failed"], [{"name": "data", "reason": "not_directory"}])
        self.assertTrue(self.dirs.data.exists())

    @unittest.skipUnless(DIRLINKS, DIRLINK_ONLY)
    def test_purge_refuses_symlinked_plugin_dir(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("x")
        fy_store.ensure(self.dirs.data.parent)
        link_dir(outside, self.dirs.data)
        r = fy_store.purge(self.dirs, ("data",))
        self.assertEqual(r["removed"], [])
        self.assertEqual(r["failed"], [{"name": "data", "reason": "symlink"}])
        self.assertTrue((outside / "precious.txt").exists())

    @unittest.skipUnless(DIRLINKS, DIRLINK_ONLY)
    def test_purge_refuses_symlinked_parent(self):
        outside = self.tmp / "outside"
        (outside / "demo").mkdir(parents=True)
        (outside / "demo" / "precious.txt").write_text("x")
        self.dirs.data.parent.parent.mkdir(parents=True)
        link_dir(outside, self.dirs.data.parent)
        r = fy_store.purge(self.dirs, ("data",))
        self.assertEqual(r["failed"], [{"name": "data", "reason": "symlink"}])
        self.assertTrue((outside / "demo" / "precious.txt").exists())

    @unittest.skipUnless(DIRLINKS, DIRLINK_ONLY)
    def test_purge_refuses_symlinked_grandparent_for_config(self):
        outside = self.tmp / "outside"
        (outside / "plugins" / "demo").mkdir(parents=True)
        (outside / "plugins" / "demo" / "precious.txt").write_text("x")
        self.dirs.config.parent.parent.parent.mkdir(parents=True)
        link_dir(outside, self.dirs.config.parent.parent)
        r = fy_store.purge(self.dirs, ("config",))
        self.assertEqual(r["failed"], [{"name": "config", "reason": "symlink"}])
        self.assertTrue((outside / "plugins" / "demo" / "precious.txt").exists())

    @unittest.skipUnless(DIRLINKS, DIRLINK_ONLY)
    def test_purge_does_not_follow_links_inside(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("x")
        fy_store.ensure(self.dirs.data)
        link_dir(outside, self.dirs.data / "link")
        if SYMLINKS:
            os.symlink(outside / "precious.txt", self.dirs.data / "flink")
        r = fy_store.purge(self.dirs, ("data",))
        self.assertEqual(r["removed"], ["data"])
        self.assertTrue((outside / "precious.txt").exists())

    @unittest.skipUnless(DIRLINKS, DIRLINK_ONLY)
    def test_clean_tmp_ignores_symlinked_runtime(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "tmp-keep").write_text("x")
        link = self.tmp / "rtlink"
        link_dir(outside, link)
        self.assertEqual(fy_store.clean_tmp(link), 0)
        self.assertTrue((outside / "tmp-keep").exists())

    def test_clean_tmp(self):
        rt = fy_store.ensure(self.dirs.runtime)
        (rt / "tmp-a").write_text("x")
        (rt / "tmp-dir").mkdir()
        (rt / "tmp-dir" / "f").write_text("x")
        (rt / "keep.sock").write_text("x")
        (rt / "tmpnot").write_text("x")
        self.assertEqual(fy_store.clean_tmp(rt), 2)
        self.assertEqual(self.names(rt), ["keep.sock", "tmpnot"])
        self.assertEqual(fy_store.clean_tmp(rt), 0)
        self.assertEqual(fy_store.clean_tmp(self.tmp / "absent"), 0)


class MigrateMoveTest(Base):
    def test_move_file_idempotent(self):
        src = self.tmp / "old" / "a.json"
        src.parent.mkdir()
        src.write_text("data")
        dst = self.tmp / "new" / "deep" / "a.json"
        self.assertEqual(fy_store.migrate_move(src, dst), {"moved": True, "reason": "ok"})
        self.assertEqual(dst.read_text(), "data")
        self.assertFalse(src.exists())
        again = fy_store.migrate_move(src, dst)
        self.assertEqual(again, {"moved": False, "reason": "missing_source"})
        self.assertEqual(dst.read_text(), "data")

    def test_move_never_overwrites_by_default(self):
        src = self.tmp / "a"
        dst = self.tmp / "b"
        src.write_text("new")
        dst.write_text("mine")
        self.assertEqual(fy_store.migrate_move(src, dst), {"moved": False, "reason": "exists"})
        self.assertEqual((src.read_text(), dst.read_text()), ("new", "mine"))
        self.assertEqual(fy_store.migrate_move(src, dst, overwrite=True)["moved"], True)
        self.assertEqual(dst.read_text(), "new")
        self.assertFalse(src.exists())

    def test_move_directory_and_refuse_directory_overwrite(self):
        src = self.tmp / "d1"
        (src / "x").mkdir(parents=True)
        (src / "x" / "f").write_text("1")
        dst = self.tmp / "d2"
        self.assertTrue(fy_store.migrate_move(src, dst)["moved"])
        self.assertEqual((dst / "x" / "f").read_text(), "1")
        other = self.tmp / "d3"
        other.mkdir()
        (other / "g").write_text("2")
        self.assertEqual(fy_store.migrate_move(other, dst, overwrite=True), {"moved": False, "reason": "exists"})
        self.assertEqual((dst / "x" / "f").read_text(), "1")

    def test_move_same_file(self):
        f = self.tmp / "f"
        f.write_text("x")
        self.assertEqual(fy_store.migrate_move(f, f), {"moved": False, "reason": "same"})
        self.assertEqual(f.read_text(), "x")

    @unittest.skipUnless(SYMLINKS, SYMLINK_ONLY)
    def test_move_refuses_symlink_source(self):
        t = self.tmp / "t"
        t.write_text("x")
        link = self.tmp / "l"
        os.symlink(t, link)
        self.assertEqual(fy_store.migrate_move(link, self.tmp / "z"), {"moved": False, "reason": "link"})
        self.assertTrue(link.is_symlink())

    def test_cross_device_fallback_copies_verifies_and_removes(self):
        src = self.tmp / "a.bin"
        src.write_bytes(b"0123456789")
        dst = self.tmp / "dest" / "a.bin"
        real = os.replace
        calls = []

        def fake(a, b):
            calls.append((str(a), str(b)))
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b)

        with mock.patch.object(fy_store.os, "replace", fake):
            r = fy_store.migrate_move(src, dst)
        self.assertEqual(r, {"moved": True, "reason": "ok"})
        self.assertEqual(dst.read_bytes(), b"0123456789")
        self.assertFalse(src.exists())
        self.assertEqual(self.names(dst.parent), ["a.bin"])

    def test_cross_device_directory(self):
        src = self.tmp / "tree"
        (src / "a" / "b").mkdir(parents=True)
        (src / "a" / "b" / "f").write_text("deep")
        (src / "top").write_text("t")
        dst = self.tmp / "moved"
        real = os.replace

        def fake(a, b):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b)

        with mock.patch.object(fy_store.os, "replace", fake):
            self.assertTrue(fy_store.migrate_move(src, dst)["moved"])
        self.assertEqual((dst / "a" / "b" / "f").read_text(), "deep")
        self.assertFalse(src.exists())

    def test_crash_midway_leaves_source_and_no_partial_destination(self):
        src = self.tmp / "a.bin"
        src.write_bytes(b"x" * 5000)
        dst = self.tmp / "dest" / "a.bin"
        real = os.replace

        def fake(a, b):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b)

        def dying(fsrc, fdst, length=0):
            fdst.write(fsrc.read(1000))
            raise OSError(errno.EIO, "power cut")

        with mock.patch.object(fy_store.os, "replace", fake), mock.patch.object(fy_store.shutil, "copyfileobj", dying):
            r = fy_store.migrate_move(src, dst)
        self.assertFalse(r["moved"])
        self.assertTrue(r["reason"].startswith("error:"))
        self.assertEqual(src.read_bytes(), b"x" * 5000)
        self.assertFalse(dst.exists())
        self.assertEqual(self.names(dst.parent), [])
        with mock.patch.object(fy_store.os, "replace", fake):
            self.assertTrue(fy_store.migrate_move(src, dst)["moved"])
        self.assertEqual(dst.read_bytes(), b"x" * 5000)

    def test_stale_part_file_from_a_hard_crash_is_replaced(self):
        src = self.tmp / "a.bin"
        src.write_bytes(b"good")
        dst = self.tmp / "dest" / "a.bin"
        dst.parent.mkdir()
        (dst.parent / "a.bin.fy-part").write_bytes(b"half")
        real = os.replace

        def fake(a, b):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b)

        with mock.patch.object(fy_store.os, "replace", fake):
            self.assertTrue(fy_store.migrate_move(src, dst)["moved"])
        self.assertEqual(dst.read_bytes(), b"good")
        self.assertEqual(self.names(dst.parent), ["a.bin"])

    def test_copy_verification_failure_is_reported(self):
        src = self.tmp / "a.bin"
        src.write_bytes(b"abcd")
        dst = self.tmp / "dest" / "a.bin"
        real = os.replace

        def fake(a, b):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b)

        def short(fsrc, fdst, length=0):
            fdst.write(b"ab")

        with mock.patch.object(fy_store.os, "replace", fake), mock.patch.object(fy_store.shutil, "copyfileobj", short):
            r = fy_store.migrate_move(src, dst)
        self.assertEqual(r, {"moved": False, "reason": "error:EIO"})
        self.assertEqual(src.read_bytes(), b"abcd")
        self.assertFalse(dst.exists())


class MigrateKeysTest(Base):
    def test_rename_skip_and_idempotent(self):
        s = {"old": 1, "keep": 2, "a": 3, "b": 4}
        r = fy_store.migrate_keys(s, {"old": "new", "missing": "x", "a": "b", "keep": "keep"})
        self.assertEqual(r, {"renamed": [["old", "new"]]})
        self.assertEqual(s, {"new": 1, "keep": 2, "a": 3, "b": 4})
        self.assertEqual(fy_store.migrate_keys(s, {"old": "new", "a": "b"}), {"renamed": []})
        self.assertEqual(s, {"new": 1, "keep": 2, "a": 3, "b": 4})

    def test_bad_mapping(self):
        for bad in ([("a", "b")], {"a": 1}, {1: "b"}, {"": "b"}, {"a": "k" * 65}, {"k%d" % i: "v%d" % i for i in range(201)}):
            with self.assertRaises(ValueError, msg=repr(bad)[:40]):
                fy_store.migrate_keys({}, bad)


class CopyOnceTest(Base):
    def test_copies_once(self):
        src = self.tmp / "legacy.json"
        src.write_text("one")
        dst = self.tmp / "new" / "settings.json"
        self.assertTrue(fy_store.copy_once(src, dst, ".migrated-legacy"))
        self.assertEqual(dst.read_text(), "one")
        self.assertTrue((dst.parent / ".migrated-legacy").exists())
        dst.write_text("edited")
        src.write_text("two")
        self.assertFalse(fy_store.copy_once(src, dst, ".migrated-legacy"))
        self.assertEqual(dst.read_text(), "edited")
        dst.unlink()
        self.assertFalse(fy_store.copy_once(src, dst, ".migrated-legacy"))
        self.assertFalse(dst.exists())
        self.assertTrue(src.exists())

    def test_missing_source_writes_no_marker(self):
        dst = self.tmp / "new" / "x"
        self.assertFalse(fy_store.copy_once(self.tmp / "nope", dst, "m"))
        self.assertFalse((dst.parent / "m").exists())

    def test_existing_destination_is_never_overwritten_and_marker_completes(self):
        src = self.tmp / "s"
        src.write_text("src")
        dst = self.tmp / "d"
        dst.write_text("mine")
        self.assertFalse(fy_store.copy_once(src, dst, "m"))
        self.assertEqual(dst.read_text(), "mine")
        self.assertTrue((self.tmp / "m").exists())

    def test_crash_before_marker_is_completed_on_rerun(self):
        src = self.tmp / "s"
        src.write_text("src")
        dst = self.tmp / "out" / "d"
        with mock.patch.object(fy_store.os, "open", side_effect=OSError(errno.EIO, "x")):
            with self.assertRaises(OSError):
                fy_store.copy_once(src, dst, "m")
        self.assertEqual(dst.read_text(), "src")
        self.assertFalse((dst.parent / "m").exists())
        self.assertFalse(fy_store.copy_once(src, dst, "m"))
        self.assertTrue((dst.parent / "m").exists())
        self.assertEqual(dst.read_text(), "src")

    def test_failed_copy_leaves_no_marker_or_partial(self):
        src = self.tmp / "s"
        src.write_text("src")
        dst = self.tmp / "out" / "d"

        def dying(fsrc, fdst, length=0):
            fdst.write(b"s")
            raise OSError(errno.EIO, "x")

        with mock.patch.object(fy_store.shutil, "copyfileobj", dying):
            with self.assertRaises(OSError):
                fy_store.copy_once(src, dst, "m")
        self.assertEqual(self.names(dst.parent), [])
        self.assertTrue(fy_store.copy_once(src, dst, "m"))

    def test_copies_directories(self):
        src = self.tmp / "sd"
        (src / "n").mkdir(parents=True)
        (src / "n" / "f").write_text("1")
        dst = self.tmp / "out" / "dd"
        self.assertTrue(fy_store.copy_once(src, dst, "m"))
        self.assertEqual((dst / "n" / "f").read_text(), "1")
        self.assertTrue(src.exists())

    def test_marker_name_rules(self):
        src = self.tmp / "s"
        src.write_text("x")
        for bad in ("", ".", "..", "a/b", "a\\b", "x" * 65, None, "a b", "..x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                fy_store.copy_once(src, self.tmp / "d", bad)
        self.assertFalse((self.tmp / "d").exists())


class LocaleTest(Base):
    def put(self, name, content):
        d = self.tmp / "plugin" / "locales"
        d.mkdir(parents=True, exist_ok=True)
        data = content if isinstance(content, bytes) else json.dumps(content).encode("utf-8")
        (d / name).write_bytes(data)

    def read(self, lang):
        return fy_store.read_locale(self.tmp / "plugin", lang)

    def test_reads_valid(self):
        self.put("en.json", {"hello": "Hello", "bye": "Goodbye ✓"})
        self.put("pt-BR.json", {"hello": "Olá"})
        self.assertEqual(self.read("en"), {"hello": "Hello", "bye": "Goodbye ✓"})
        self.assertEqual(self.read("pt-BR"), {"hello": "Olá"})

    def test_missing_and_bad_lang(self):
        self.put("en.json", {"a": "b"})
        self.assertEqual(self.read("de"), {})
        for bad in ("", "e", "english", "en_US", "../en", "en/../en", "en.json", "en-", "en-x", "en-" + "a" * 9, None, 5, "en\n"):
            self.assertEqual(self.read(bad), {}, repr(bad))
        self.assertEqual(fy_store.read_locale(self.tmp / "nowhere", "en"), {})

    def test_size_cap(self):
        ok = json.dumps({"k": "x" * (65536 - 10)}).encode()
        self.assertLessEqual(len(ok), 65536)
        self.put("en.json", ok)
        self.assertEqual(len(self.read("en")["k"]), 65536 - 10)
        self.put("en.json", json.dumps({"k": "x" * 65536}).encode())
        self.assertEqual(self.read("en"), {})

    def test_key_cap(self):
        self.put("en.json", {"k%d" % i: "v" for i in range(2000)})
        self.assertEqual(len(self.read("en")), 2000)
        self.put("en.json", {"k%d" % i: "v" for i in range(2001)})
        self.assertEqual(self.read("en"), {})

    def test_shape_rules(self):
        for bad in ({"a": 1}, {"a": {"b": "c"}}, {"a": None}, {"a": ["x"]}, ["a"], "str", 3, None):
            self.put("en.json", bad)
            self.assertEqual(self.read("en"), {}, repr(bad))
        self.put("en.json", b"{broken")
        self.assertEqual(self.read("en"), {})
        self.put("en.json", b"\xff\xfe\x00")
        self.assertEqual(self.read("en"), {})

    @unittest.skipUnless(SYMLINKS, SYMLINK_ONLY)
    def test_symlinked_locale_file_is_ignored(self):
        target = self.tmp / "secret.json"
        target.write_text(json.dumps({"s": "secret"}))
        d = self.tmp / "plugin" / "locales"
        d.mkdir(parents=True)
        os.symlink(target, d / "en.json")
        self.assertEqual(self.read("en"), {})


class SettingsWriteGuardTests(unittest.TestCase):
    def test_an_unreadable_settings_file_refuses_writes_and_a_corrupt_one_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text('{"a": 1}')
            s = fy_store.Settings(p)
            with mock.patch("builtins.open", side_effect=OSError(errno.EMFILE, "too many open files")):
                with self.assertRaises(OSError):
                    s.set("b", 2)
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            p.write_bytes(b"{broken")
            s.set("b", 2)
            self.assertEqual((Path(d) / "settings.json.bad").read_bytes(), b"{broken")
            self.assertEqual(json.loads(p.read_text()), {"b": 2})

    def test_discard_held_removes_downgrade_and_stage_copies_before_uninstall(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".old-fan").mkdir()
            (root / ".old-fan" / "plugin.json").write_text("{}")
            (root / ".stage-fan").mkdir()
            (root / "fan").mkdir()
            fy_store.discard_held(root, "fan")
            self.assertEqual(sorted(x.name for x in root.iterdir()), ["fan"])
            with self.assertRaises(ValueError):
                fy_store.discard_held(root, "../x")

    def test_sync_runs_in_a_thread_and_never_blocks_the_loop(self):
        import asyncio
        import fy_net
        with mock.patch.object(fy_net.os, "sync", create=True) as sync:
            asyncio.run(fy_net.sync_disk())
        sync.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
