import ast
import asyncio
import inspect
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import tokenize
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fy_dev

LINUX = sys.platform.startswith("linux")
BASE = 1_000_000


class Handle:
    def __init__(self, at, fn, args):
        self.at, self.fn, self.args, self.dead = at, fn, args, False

    def cancel(self):
        self.dead = True


class Task:
    def __init__(self, coro):
        self.coro = coro


class FakeLoop:
    def __init__(self):
        self.now = 0.0
        self.timers = []
        self.readers = {}
        self.tasks = []

    def call_later(self, delay, fn, *args):
        h = Handle(self.now + delay, fn, args)
        self.timers.append(h)
        return h

    def live(self):
        return [h for h in self.timers if not h.dead]

    def advance(self, dt):
        end = self.now + dt
        while True:
            due = sorted((h for h in self.timers if not h.dead and h.at <= end), key=lambda h: h.at)
            if not due:
                break
            h = due[0]
            self.timers.remove(h)
            self.now = max(self.now, h.at)
            h.fn(*h.args)
        self.now = end

    def add_reader(self, fd, cb):
        self.readers[fd] = cb

    def remove_reader(self, fd):
        return self.readers.pop(fd, None) is not None

    def create_task(self, coro):
        t = Task(coro)
        self.tasks.append(t)
        return t

    def drain(self):
        while self.tasks:
            asyncio.run(self.tasks.pop(0).coro)

    def close(self):
        for t in self.tasks:
            t.coro.close()
        self.tasks = []


class FakeLibc:
    def __init__(self, init_fails=False, watch_limit=None):
        self.init_fails = init_fails
        self.watch_limit = watch_limit
        self.r = self.w = None
        self.flags = None
        self.inits = 0
        self.watches = {}
        self.removed = []
        self.next = 1

    def inotify_init1(self, flags):
        self.inits += 1
        self.flags = flags
        if self.init_fails:
            return -1
        self.r, self.w = os.pipe()
        return self.r

    def inotify_add_watch(self, fd, path, mask):
        if self.watch_limit is not None and len(self.watches) >= self.watch_limit:
            return -1
        wd = self.next
        self.next += 1
        self.watches[wd] = (os.fsdecode(path), mask)
        return wd

    def inotify_rm_watch(self, fd, wd):
        self.removed.append(wd)
        self.watches.pop(wd, None)
        return 0

    def wd_for(self, path):
        return next(wd for wd, (p, _) in self.watches.items() if p == str(path))

    def emit(self, wd, mask, name=b""):
        raw = name + b"\0" if name else b""
        raw += b"\0" * (-len(raw) % 16)
        os.write(self.w, fy_dev.HEADER.pack(wd, mask, 0, len(raw)) + raw)

    def close(self):
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except (OSError, TypeError):
                pass


class FakeHost:
    def __init__(self, loop, enabled=True):
        self.loop_ = loop
        self.state = {"prefs": {"dev": {"enabled": enabled}}}
        self.roots = {}
        self.saves = 0
        self.logs = []
        self.reloaded = []

    def loop(self):
        return self.loop_

    def mono(self):
        return self.loop_.now

    def now(self):
        return BASE + self.loop_.now

    def settings(self):
        return json.loads(json.dumps(self.state))

    def save(self, s):
        self.saves += 1
        self.state = json.loads(json.dumps(s))

    def real(self, pid):
        return self.roots.get(pid)

    def log(self, *a):
        self.logs.append(a)

    async def reload_one(self, pid):
        self.reloaded.append(pid)

    @property
    def hot_state(self):
        return self.state["prefs"]["dev"]


def tree(base, name, files=("main.js", "sub/a.py", ".git/x", "__pycache__/y.pyc", ".stage-1/z")):
    root = os.path.join(base, name)
    for rel in files:
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(rel)
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.loop = FakeLoop()
        self.addCleanup(self.loop.close)
        self.host = FakeHost(self.loop)
        self.host.roots["p"] = tree(self.tmp, "p")
        self.host.roots["q"] = tree(self.tmp, "q", ("q.js",))
        self.libc = FakeLibc()
        self.addCleanup(self.libc.close)

    def hot(self, **kw):
        kw.setdefault("libc", self.libc)
        h = fy_dev.HotReload(self.host, self.host.reload_one, **kw)
        self.addCleanup(h.disable_all)
        return h

    def fire(self, hot, wd, mask, name=b""):
        self.libc.emit(wd, mask, name)
        self.loop.readers[hot._fd]()

    def settle(self, dt=0.5):
        self.loop.advance(dt)
        self.loop.drain()


class InotifyTests(Base):
    def test_enable_watches_only_listed_directories(self):
        hot = self.hot()
        self.assertEqual(hot.enable("p"), {"mode": "inotify", "truncated": False})
        self.assertEqual(self.libc.flags, fy_dev.IN_NONBLOCK | fy_dev.IN_CLOEXEC)
        paths = {p for p, _ in self.libc.watches.values()}
        root = self.host.roots["p"]
        self.assertEqual(paths, {root, os.path.join(root, "sub")})
        self.assertTrue(all(m == fy_dev.WATCH_MASK for _, m in self.libc.watches.values()))
        self.assertEqual(fy_dev.WATCH_MASK, 0x8 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800)
        self.assertIn(self.libc.r, self.loop.readers)

    def test_idle_costs_only_the_ttl_timer(self):
        hot = self.hot()
        hot.enable("p")
        self.assertEqual(len(self.loop.live()), 1)
        self.loop.advance(1800)
        self.assertEqual(len(self.loop.live()), 1)
        self.assertEqual(self.host.reloaded, [])

    def test_state_is_saved_and_listed(self):
        hot = self.hot()
        hot.enable("p")
        self.assertEqual(self.host.hot_state["hot"], ["p"])
        self.assertEqual(self.host.hot_state["hot_until"], BASE + 3600)
        self.assertEqual(self.host.hot_state["enabled"], True)
        self.assertEqual(hot.active(), ["p"])
        self.assertEqual(hot.status(), [{"id": "p", "mode": "inotify", "truncated": False, "remaining_s": 3600}])
        self.loop.advance(60)
        self.assertEqual(hot.status()[0]["remaining_s"], 3540)

    def test_burst_is_collapsed_by_the_debounce(self):
        hot = self.hot(debounce=0.4)
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        for _ in range(10):
            self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
            self.loop.advance(0.1)
        self.settle(0.25)
        self.assertEqual(self.host.reloaded, [])
        self.settle(0.1)
        self.assertEqual(self.host.reloaded, ["p"])
        self.settle(5)
        self.assertEqual(self.host.reloaded, ["p"])
        self.assertEqual(len(self.loop.live()), 1)

    def test_two_events_in_one_read_make_one_reload(self):
        hot = self.hot()
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.libc.emit(wd, fy_dev.IN_CLOSE_WRITE, b"a.js")
        self.libc.emit(wd, fy_dev.IN_MOVED_TO, b"b.js")
        self.loop.readers[hot._fd]()
        self.settle()
        self.assertEqual(self.host.reloaded, ["p"])

    def test_ignored_names_do_not_schedule_anything(self):
        hot = self.hot()
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        before = hot.status()
        for name in (b".stage-abc", b".old-x", b".git", b".gitignore", b"__pycache__", b"x.swp", b"file~"):
            self.fire(hot, wd, fy_dev.IN_CREATE, name)
        self.loop.advance(1)
        self.loop.drain()
        self.assertEqual(self.host.reloaded, [])
        self.assertEqual(len(self.loop.live()), 1)
        self.assertEqual(hot.status()[0]["remaining_s"], before[0]["remaining_s"] - 1)
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.assertEqual(self.host.reloaded, ["p"])

    def test_ignore_rules(self):
        for name in (".stage-1", ".old-1", ".git", ".gitattributes", "__pycache__", "a.swp", "a.py~"):
            self.assertTrue(fy_dev.ignored(name), name)
        for name in ("main.js", "stage", "old", "git", "a.py", "__init__.py", "swp"):
            self.assertFalse(fy_dev.ignored(name), name)

    def test_ttl_stops_the_watcher_after_idle(self):
        hot = self.hot()
        hot.enable("p")
        fd = hot._fd
        self.loop.advance(3599)
        self.assertEqual(hot.active(), ["p"])
        self.loop.advance(2)
        self.assertEqual(hot.active(), [])
        self.assertEqual(self.loop.live(), [])
        self.assertEqual(self.loop.readers, {})
        self.assertEqual(self.libc.watches, {})
        self.assertIsNone(hot._fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual(self.host.hot_state["hot"], [])
        self.assertEqual(self.host.hot_state["hot_until"], 0)

    def test_a_change_rearms_the_ttl(self):
        hot = self.hot()
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.loop.advance(3000)
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.loop.advance(3000)
        self.assertEqual(hot.active(), ["p"])
        self.loop.advance(601)
        self.assertEqual(hot.active(), [])

    def test_disable_all_stops_everything_but_keeps_the_saved_choice(self):
        hot = self.hot()
        hot.enable("p")
        hot.enable("q")
        fd = hot._fd
        hot.disable_all()
        self.assertEqual(hot.active(), [])
        self.assertEqual(self.loop.live(), [])
        self.assertEqual(self.loop.readers, {})
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual(self.host.hot_state["hot"], ["p", "q"])

    def test_one_descriptor_is_shared_and_closed_with_the_last_module(self):
        hot = self.hot()
        hot.enable("p")
        hot.enable("q")
        self.assertEqual(self.libc.inits, 1)
        hot.disable("p")
        self.assertIn(self.libc.r, self.loop.readers)
        self.assertEqual(self.host.hot_state["hot"], ["q"])
        hot.disable("q")
        self.assertEqual(self.loop.readers, {})
        self.assertEqual(self.host.hot_state["hot"], [])
        hot.disable("q")
        hot.disable("zzz")

    def test_events_are_attributed_to_the_right_module(self):
        hot = self.hot()
        hot.enable("p")
        hot.enable("q")
        self.fire(hot, self.libc.wd_for(self.host.roots["q"]), fy_dev.IN_CLOSE_WRITE, b"q.js")
        self.settle()
        self.assertEqual(self.host.reloaded, ["q"])

    def test_new_directory_gets_a_watch_up_to_the_cap(self):
        hot = self.hot(max_dirs=3)
        hot.enable("p")
        root = self.host.roots["p"]
        wd = self.libc.wd_for(root)
        self.fire(hot, wd, fy_dev.IN_CREATE | fy_dev.IN_ISDIR, b"fresh")
        self.assertIn(os.path.join(root, "fresh"), {p for p, _ in self.libc.watches.values()})
        self.fire(hot, wd, fy_dev.IN_CREATE | fy_dev.IN_ISDIR, b"more")
        self.assertNotIn(os.path.join(root, "more"), {p for p, _ in self.libc.watches.values()})
        self.fire(hot, wd, fy_dev.IN_CREATE | fy_dev.IN_ISDIR, b".stage-9")
        self.assertNotIn(os.path.join(root, ".stage-9"), {p for p, _ in self.libc.watches.values()})
        self.settle()
        self.assertEqual(self.host.reloaded, ["p"])

    def test_directories_over_the_cap_are_reported_truncated(self):
        for i in range(10):
            os.makedirs(os.path.join(self.host.roots["p"], "d%d" % i))
        hot = self.hot(max_dirs=4)
        self.assertEqual(hot.enable("p"), {"mode": "inotify", "truncated": True})
        self.assertEqual(len(self.libc.watches), 4)

    def test_queue_overflow_reloads_every_module(self):
        hot = self.hot()
        hot.enable("p")
        hot.enable("q")
        self.fire(hot, -1, fy_dev.IN_Q_OVERFLOW)
        self.settle()
        self.assertEqual(sorted(self.host.reloaded), ["p", "q"])

    def test_unknown_watch_and_removed_watch_are_ignored(self):
        hot = self.hot()
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.libc.emit(999, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.libc.emit(wd, fy_dev.IN_IGNORED)
        self.libc.emit(wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.loop.readers[hot._fd]()
        self.settle()
        self.assertEqual(self.host.reloaded, [])

    def test_removed_watch_falls_back_to_polling_and_detects_later_edits(self):
        hot = self.hot(interval=1)
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.fire(hot, wd, fy_dev.IN_IGNORED)
        self.assertEqual(hot.status()[0]["mode"], "mtime")
        self.assertEqual(self.loop.readers, {})
        with open(os.path.join(self.host.roots["p"], "main.js"), "a") as f:
            f.write("changed")
        self.settle(1.5)
        self.assertEqual(self.host.reloaded, ["p"])

    def test_moved_root_falls_back_and_reloads_replacement(self):
        hot = self.hot()
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.fire(hot, wd, fy_dev.IN_MOVE_SELF)
        self.assertEqual(hot.status()[0]["mode"], "mtime")
        self.settle()
        self.assertEqual(self.host.reloaded, ["p"])

    def test_new_directory_tree_gets_descendant_watches(self):
        hot = self.hot(max_dirs=8)
        hot.enable("p")
        root = self.host.roots["p"]
        fresh = os.path.join(root, "fresh", "nested")
        os.makedirs(fresh)
        self.fire(hot, self.libc.wd_for(root), fy_dev.IN_MOVED_TO | fy_dev.IN_ISDIR, b"fresh")
        self.assertIn(fresh, {p for p, _ in self.libc.watches.values()})

    def test_new_directories_over_watch_cap_mark_status_truncated(self):
        hot = self.hot(max_dirs=2)
        hot.enable("p")
        self.assertFalse(hot.status()[0]["truncated"])
        self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CREATE | fy_dev.IN_ISDIR, b"fresh")
        self.assertTrue(hot.status()[0]["truncated"])

    def test_disable_and_reenable_does_not_run_stale_queued_reload(self):
        hot = self.hot()
        hot.enable("p")
        self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.loop.advance(0.5)
        hot.disable("p")
        hot.enable("p")
        self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.loop.advance(0.5)
        self.assertEqual(len(self.loop.tasks), 1)
        self.loop.drain()
        self.assertEqual(self.host.reloaded, [])
        self.settle()
        self.assertEqual(self.host.reloaded, ["p"])

    def test_reenable_waits_for_in_progress_reload(self):
        hot = self.hot()
        hot.enable("p")

        async def scenario():
            started, finish = asyncio.Event(), asyncio.Event()
            running = []

            async def reload(pid):
                running.append(pid)
                started.set()
                await finish.wait()

            hot.reload_one = reload
            self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CLOSE_WRITE, b"main.js")
            self.loop.advance(0.5)
            task = asyncio.create_task(self.loop.tasks.pop(0).coro)
            await started.wait()
            hot.disable("p")
            hot.enable("p")
            self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CLOSE_WRITE, b"main.js")
            self.loop.advance(1)
            self.assertEqual(self.loop.tasks, [])
            self.assertEqual(running, ["p"])
            finish.set()
            await task
            self.loop.advance(0.5)
            self.assertEqual(len(self.loop.tasks), 1)
            await self.loop.tasks.pop(0).coro
            self.assertEqual(running, ["p", "p"])

        asyncio.run(scenario())

    def test_changed_activity_persists_extended_expiry_after_debounce(self):
        hot = self.hot()
        hot.enable("p")
        self.loop.advance(3000)
        self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.assertEqual(self.host.hot_state["hot_until"], BASE + 6600)

    def test_removed_developer_mode_stops_changes_before_reload(self):
        hot = self.hot()
        hot.enable("p")
        self.host.hot_state["enabled"] = False
        self.fire(hot, self.libc.wd_for(self.host.roots["p"]), fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.assertEqual(self.host.reloaded, [])
        self.assertEqual(hot.active(), [])

    def test_reload_in_progress_is_never_run_twice_at_once(self):
        hot = self.hot()
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.loop.advance(0.5)
        self.assertEqual(len(self.loop.tasks), 1)
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.loop.advance(0.5)
        self.assertEqual(len(self.loop.tasks), 1)
        self.loop.advance(0.5)
        self.assertEqual(len(self.loop.tasks), 1)
        self.loop.drain()
        self.assertEqual(self.host.reloaded, ["p"])
        self.loop.advance(0.5)
        self.loop.drain()
        self.assertEqual(self.host.reloaded, ["p", "p"])

    def test_failing_reload_is_logged_and_does_not_block_the_next(self):
        calls = []

        async def bad(pid):
            calls.append(pid)
            if len(calls) == 1:
                raise RuntimeError("boom")

        hot = fy_dev.HotReload(self.host, bad, libc=self.libc)
        self.addCleanup(hot.disable_all)
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"a")
        self.settle()
        self.assertTrue(any("boom" in str(x) for a in self.host.logs for x in a))
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"a")
        self.settle()
        self.assertEqual(calls, ["p", "p"])

    def test_structured_reload_failure_is_visible_until_a_successful_reload(self):
        async def failed(pid):
            return {"error": "unknown plugin"}

        hot = self.hot()
        self.host.hot = hot
        hot.reload_one = failed
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.assertIn("could not be reloaded", hot.status()[0]["reload_error"])
        self.assertIn("Hot reload failed for p", fy_dev.status(self.host)["warning"])
        self.assertTrue(any("unknown plugin" in str(line) for line in self.host.logs))
        hot.reload_one = self.host.reload_one
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.assertNotIn("reload_error", hot.status()[0])
        self.assertIsNone(fy_dev.status(self.host)["warning"])

    def test_reload_timeout_cancels_the_call_and_releases_the_plugin_slot(self):
        cancelled = []

        async def hung(pid):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(pid)

        hot = self.hot()
        self.host.hot = hot
        hot.reload_one = hung
        hot.enable("p")
        wd = self.libc.wd_for(self.host.roots["p"])
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        with mock.patch.object(fy_dev, "RELOAD_TIMEOUT", 0.02):
            self.settle()
        self.assertEqual(cancelled, ["p"])
        self.assertEqual(hot._busy, set())
        self.assertIsNone(hot._w["p"]["task"])
        self.assertIn("took too long", fy_dev.status(self.host)["warning"])
        hot.reload_one = self.host.reload_one
        self.fire(hot, wd, fy_dev.IN_CLOSE_WRITE, b"main.js")
        self.settle()
        self.assertEqual(self.host.reloaded, ["p"])

    def test_read_error_stops_all_watching(self):
        hot = self.hot()
        hot.enable("p")
        fd = hot._fd
        os.close(self.libc.w)
        self.libc.w = None
        os.close(fd)
        self.loop.readers[fd]()
        self.assertEqual(hot.active(), [])
        self.assertEqual(self.loop.readers, {})
        self.libc.r = None


class RefusalTests(Base):
    def test_developer_mode_must_be_on(self):
        self.host.state["prefs"]["dev"]["enabled"] = False
        with self.assertRaises(fy_dev.DevError) as c:
            self.hot().enable("p")
        self.assertEqual(c.exception.code, "dev_off")
        self.assertEqual(self.loop.live(), [])
        self.host.state["prefs"] = {}
        with self.assertRaises(fy_dev.DevError):
            self.hot().enable("p")

    def test_bad_ids_and_missing_folders(self):
        hot = self.hot()
        for pid in ("../x", "", "A", None, 5, "x" * 33):
            with self.assertRaises(fy_dev.DevError) as c:
                hot.enable(pid)
            self.assertEqual(c.exception.code, "bad_plugin")
        with self.assertRaises(fy_dev.DevError) as c:
            hot.enable("nope")
        self.assertEqual(c.exception.code, "not_found")
        self.host.roots["gone"] = os.path.join(self.tmp, "missing")
        with self.assertRaises(fy_dev.DevError):
            hot.enable("gone")
        self.assertEqual(hot.active(), [])

    def test_at_most_eight_modules(self):
        hot = self.hot()
        for i in range(8):
            self.host.roots["m%d" % i] = tree(self.tmp, "m%d" % i, ("f.js",))
            hot.enable("m%d" % i)
        self.host.roots["m8"] = tree(self.tmp, "m8", ("f.js",))
        with self.assertRaises(fy_dev.DevError) as c:
            hot.enable("m8")
        self.assertEqual(c.exception.code, "too_many")
        hot.enable("m3")
        self.assertEqual(len(hot.active()), 8)

    def test_enabling_twice_replaces_the_watch(self):
        hot = self.hot()
        hot.enable("p")
        hot.enable("p")
        self.assertEqual(len(self.libc.watches), 2)
        self.assertEqual(len(self.loop.live()), 1)
        self.assertEqual(hot.active(), ["p"])

    def test_save_failure_does_not_break_enable(self):
        def broken(s):
            raise OSError("disk full")

        self.host.save = broken
        hot = self.hot()
        self.assertEqual(hot.enable("p")["mode"], "inotify")
        self.assertTrue(any("could not save" in str(a) for a in self.host.logs))
        self.assertIn("could not be saved", hot.warning)


class FallbackTests(Base):
    def test_no_libc_uses_mtime(self):
        with mock.patch.object(fy_dev, "load_libc", return_value=None):
            hot = fy_dev.HotReload(self.host, self.host.reload_one)
            self.addCleanup(hot.disable_all)
            self.assertEqual(hot.enable("p"), {"mode": "mtime", "truncated": False})
        self.assertEqual(self.loop.readers, {})

    def test_inotify_flag_off_uses_mtime(self):
        hot = self.hot(inotify=False)
        self.assertEqual(hot.enable("p")["mode"], "mtime")
        self.assertEqual(self.libc.inits, 0)

    def test_init_failure_uses_mtime(self):
        hot = self.hot(libc=FakeLibc(init_fails=True))
        self.assertEqual(hot.enable("p")["mode"], "mtime")
        self.assertIsNone(hot._fd)

    def test_watch_limit_undoes_partial_watches(self):
        hot = self.hot(libc=self.libc)
        self.libc.watch_limit = 1
        self.assertEqual(hot.enable("p")["mode"], "mtime")
        self.assertEqual(self.libc.watches, {})
        self.assertIsNone(hot._fd)
        self.assertEqual(self.loop.readers, {})

    def test_reader_registration_failure_uses_mtime(self):
        def refuse(fd, cb):
            raise NotImplementedError

        self.loop.add_reader = refuse
        hot = self.hot()
        self.assertEqual(hot.enable("p")["mode"], "mtime")
        self.assertIsNone(hot._fd)

    def test_other_modules_keep_inotify_when_one_falls_back(self):
        hot = self.hot()
        hot.enable("q")
        self.libc.watch_limit = len(self.libc.watches)
        self.assertEqual(hot.enable("p")["mode"], "mtime")
        self.assertEqual(hot.status()[0]["mode"], "mtime")
        self.assertIn(self.libc.r, self.loop.readers)
        self.assertEqual(len(self.libc.watches), 1)


class MtimeTests(Base):
    def touch(self, rel, text=None, bump=10**9):
        p = os.path.join(self.host.roots["p"], *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if text is not None:
            with open(p, "w") as f:
                f.write(text)
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + bump))

    def make(self, **kw):
        hot = self.hot(inotify=False, **kw)
        hot.enable("p")
        return hot

    def test_two_timers_while_enabled_and_none_after(self):
        hot = self.make()
        self.assertEqual(len(self.loop.live()), 2)
        for _ in range(100):
            self.loop.advance(2.0)
            self.assertLessEqual(len(self.loop.live()), 2)
        hot.disable("p")
        self.assertEqual(self.loop.live(), [])

    def test_no_change_no_reload(self):
        self.make()
        self.settle(60)
        self.assertEqual(self.host.reloaded, [])

    def test_modified_file_is_detected_once(self):
        self.make()
        self.touch("main.js")
        self.loop.advance(2.0)
        self.loop.advance(0.4)
        self.loop.drain()
        self.assertEqual(self.host.reloaded, ["p"])
        self.settle(30)
        self.assertEqual(self.host.reloaded, ["p"])

    def test_size_change_alone_is_detected(self):
        self.make()
        with open(os.path.join(self.host.roots["p"], "main.js"), "a") as f:
            f.write("more")
        st = os.stat(os.path.join(self.host.roots["p"], "main.js"))
        self.loop.advance(2.5)
        self.loop.drain()
        self.assertEqual(self.host.reloaded, ["p"], st)

    def test_new_and_deleted_files_are_detected(self):
        self.make()
        self.touch("sub/new.py", "x")
        self.settle(2.5)
        self.assertEqual(self.host.reloaded, ["p"])
        os.remove(os.path.join(self.host.roots["p"], "sub", "new.py"))
        self.settle(2.5)
        self.assertEqual(self.host.reloaded, ["p", "p"])

    def test_ignored_paths_are_not_scanned(self):
        self.make()
        self.touch("__pycache__/y.pyc")
        self.touch(".git/x")
        self.touch(".stage-1/z")
        self.touch("a.swp", "x")
        self.touch("b.py~", "x")
        self.settle(10)
        self.assertEqual(self.host.reloaded, [])

    def test_file_count_is_bounded(self):
        for i in range(250):
            self.touch("many/f%03d.txt" % i, "x", 0)
        hot = self.hot(inotify=False, max_files=200)
        self.assertEqual(hot.enable("p"), {"mode": "mtime", "truncated": True})
        self.assertEqual(len(hot._w["p"]["snap"]), 200)

    def test_directory_count_is_bounded(self):
        for i in range(40):
            os.makedirs(os.path.join(self.host.roots["p"], "d%02d" % i))
        dirs, files, cut = fy_dev.walk(self.host.roots["p"], 5, 200)
        self.assertEqual(len(dirs), 5)
        self.assertTrue(cut)

    def test_entries_per_directory_are_bounded(self):
        for i in range(30):
            self.touch("wide/f%02d" % i, "x", 0)
        with mock.patch.object(fy_dev, "ENTRIES_MAX", 10):
            dirs, files, cut = fy_dev.walk(os.path.join(self.host.roots["p"], "wide"), 32, 200)
        self.assertTrue(cut)
        self.assertLessEqual(len(files), 10)

    def test_ttl_ends_the_polling_chain(self):
        hot = self.hot(inotify=False, ttl=100)
        hot.enable("p")
        self.loop.advance(99)
        self.assertEqual(len(self.loop.live()), 2)
        self.loop.advance(2)
        self.assertEqual(self.loop.live(), [])
        self.assertEqual(hot.active(), [])

    def test_a_detected_change_extends_the_ttl(self):
        hot = self.hot(inotify=False, ttl=100)
        hot.enable("p")
        self.loop.advance(90)
        self.touch("main.js")
        self.settle(3)
        self.loop.advance(95)
        self.assertEqual(hot.active(), ["p"])
        self.loop.advance(10)
        self.assertEqual(hot.active(), [])

    def test_tick_after_disable_is_harmless(self):
        hot = self.make()
        h = hot._w["p"]["tick"]
        hot.disable("p")
        hot._tick("p")
        self.assertEqual(self.loop.live(), [])
        self.assertTrue(h.dead)


class ResumeTests(Base):
    def test_resume_re_enables_saved_modules_with_the_remaining_time(self):
        self.host.hot_state.update({"hot": ["p", "gone"], "hot_until": BASE + 600})
        hot = self.hot()
        self.assertEqual(hot.resume(), ["p"])
        self.assertEqual(hot.status()[0]["remaining_s"], 600)
        self.assertEqual(self.host.hot_state["hot"], ["p"])

    def test_resume_ignores_expired_or_missing_state(self):
        for bad in ({}, {"hot": ["p"]}, {"hot": ["p"], "hot_until": BASE - 1}, {"hot": "p", "hot_until": BASE + 5}, {"hot": ["p"], "hot_until": True}, {"hot": ["p"], "hot_until": float("nan")}, {"hot": ["p"], "hot_until": float("inf")}):
            self.host.state["prefs"]["dev"] = dict(bad, enabled=True)
            hot = self.hot()
            self.assertEqual(hot.resume(), [])
            self.assertEqual(hot.active(), [])

    def test_resume_needs_developer_mode(self):
        self.host.hot_state.update({"hot": ["p"], "hot_until": BASE + 600, "enabled": False})
        hot = self.hot()
        self.assertEqual(hot.resume(), [])

    def test_get_creates_one_instance_and_shutdown_stops_it(self):
        with mock.patch.object(fy_dev, "load_libc", return_value=None):
            a = fy_dev.get(self.host)
            self.assertIs(a, fy_dev.get(self.host))
            a.enable("p")
            fy_dev.shutdown(self.host)
        self.assertEqual(a.active(), [])
        fy_dev.shutdown(types.SimpleNamespace())


PROC_HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def row(n, local, st="0A"):
    return "   %d: %s 00000000:0000 %s 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n" % (n, local, st)


class ExposureTests(unittest.TestCase):
    def test_closed(self):
        self.assertEqual(fy_dev.remote_exposure("", ""), {"state": "closed", "addresses": []})
        self.assertEqual(fy_dev.remote_exposure(PROC_HEAD, PROC_HEAD)["state"], "closed")
        other = PROC_HEAD + row(0, "0100007F:0050") + row(1, "00000000:1F91")
        self.assertEqual(fy_dev.remote_exposure(other, "")["state"], "closed")

    def test_loopback_only(self):
        tcp = PROC_HEAD + row(0, "0100007F:1F90")
        self.assertEqual(fy_dev.remote_exposure(tcp, ""), {"state": "loopback", "addresses": ["127.0.0.1:8080"]})
        tcp = PROC_HEAD + row(0, "0200007F:1F90")
        self.assertEqual(fy_dev.remote_exposure(tcp, "")["state"], "loopback")

    def test_all_interfaces_is_exposed(self):
        tcp = PROC_HEAD + row(0, "00000000:1F90")
        self.assertEqual(fy_dev.remote_exposure(tcp, ""), {"state": "exposed", "addresses": ["0.0.0.0:8080"]})

    def test_loopback_and_wildcard_together_is_exposed(self):
        tcp = PROC_HEAD + row(0, "0100007F:1F90") + row(1, "00000000:1F90")
        r = fy_dev.remote_exposure(tcp, "")
        self.assertEqual(r["state"], "exposed")
        self.assertEqual(r["addresses"], ["0.0.0.0:8080", "127.0.0.1:8080"])

    def test_lan_address_is_exposed(self):
        tcp = PROC_HEAD + row(0, "0A00A8C0:1F90")
        self.assertEqual(fy_dev.remote_exposure(tcp, ""), {"state": "exposed", "addresses": ["192.168.0.10:8080"]})

    def test_ipv6(self):
        loop6 = PROC_HEAD + row(0, "00000000000000000000000001000000:1F90")
        self.assertEqual(fy_dev.remote_exposure("", loop6), {"state": "loopback", "addresses": ["[::1]:8080"]})
        mapped = PROC_HEAD + row(0, "0000000000000000FFFF00000100007F:1F90")
        self.assertEqual(fy_dev.remote_exposure("", mapped)["state"], "loopback")
        wild6 = PROC_HEAD + row(0, "00000000000000000000000000000000:1F90")
        self.assertEqual(fy_dev.remote_exposure("", wild6), {"state": "exposed", "addresses": ["[::]:8080"]})
        mapped_any = PROC_HEAD + row(0, "0000000000000000FFFF000000000000:1F90")
        self.assertEqual(fy_dev.remote_exposure("", mapped_any)["state"], "exposed")
        self.assertEqual(fy_dev.remote_exposure(PROC_HEAD + row(0, "0100007F:1F90"), wild6)["state"], "exposed")

    def test_only_listening_rows_count(self):
        tcp = PROC_HEAD + row(0, "00000000:1F90", "01") + row(1, "00000000:1F90", "06")
        self.assertEqual(fy_dev.remote_exposure(tcp, "")["state"], "closed")

    def test_garbage_is_ignored(self):
        tcp = PROC_HEAD + "garbage\n\n   9: ZZZZZZZZ:1F90 00000000:0000 0A\n   8: 0100007F 0 0A\n   7: 0100007F:XX 0 0A\n   6: 0102:1F90 0 0A\n" + row(0, "0100007F:1F90")
        self.assertEqual(fy_dev.remote_exposure(tcp, "")["state"], "loopback")
        self.assertEqual(fy_dev.remote_exposure(None, None)["state"], "closed")
        self.assertEqual(fy_dev.remote_exposure(b"x", 5)["state"], "closed")

    def test_port_argument(self):
        tcp = PROC_HEAD + row(0, "00000000:0050")
        self.assertEqual(fy_dev.remote_exposure(tcp, "", port=80)["state"], "exposed")
        self.assertEqual(fy_dev.remote_exposure(tcp, "")["state"], "closed")

    def test_address_list_is_bounded(self):
        tcp = PROC_HEAD + "".join(row(i, "%02X00A8C0:1F90" % i) for i in range(40))
        r = fy_dev.remote_exposure(tcp, "")
        self.assertEqual(len(r["addresses"]), 16)
        self.assertEqual(r["state"], "exposed")

    def test_exposure_now_reads_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fy_dev.exposure_now(d), {"state": "unknown", "addresses": []})
            with open(os.path.join(d, "tcp"), "w") as f:
                f.write(PROC_HEAD + row(0, "0100007F:1F90"))
            self.assertEqual(fy_dev.exposure_now(d)["state"], "unknown")
            with open(os.path.join(d, "tcp6"), "w") as f:
                f.write(PROC_HEAD)
            self.assertEqual(fy_dev.exposure_now(d)["state"], "loopback")
            with open(os.path.join(d, "tcp6"), "w") as f:
                f.write(PROC_HEAD + row(0, "00000000000000000000000000000000:1F90"))
            self.assertEqual(fy_dev.exposure_now(d)["state"], "exposed")

    def test_truncated_proc_table_cannot_claim_loopback_only(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("tcp", "tcp6"):
                with open(os.path.join(d, name), "w") as f:
                    f.write(PROC_HEAD + row(0, "0100007F:1F90") + " " * 300)
            with mock.patch.object(fy_dev, "PROC_MAX", len(PROC_HEAD) + 200):
                self.assertEqual(fy_dev.exposure_now(d)["state"], "unknown")

    @unittest.skipUnless(LINUX, "needs Linux /proc/net (pending Linux run)")
    def test_exposure_now_on_this_machine(self):
        self.assertIn(fy_dev.exposure_now()["state"], ("closed", "loopback", "exposed"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DevtoolsTests(unittest.IsolatedAsyncioTestCase):
    async def serve(self, body, status=b"200 OK", delay=0):
        async def handle(r, w):
            try:
                await r.readuntil(b"\r\n\r\n")
                if delay:
                    await asyncio.sleep(delay)
                w.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\nContent-Type: application/json\r\n\r\n" + body)
                await w.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                w.close()

        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.addAsyncCleanup(srv.wait_closed)
        self.addCleanup(srv.close)
        return srv.sockets[0].getsockname()[1]

    def host(self, targets=None):
        return types.SimpleNamespace(settings=lambda: {"prefs": {"dev": {"enabled": True, "targets": targets or {}}}})

    async def test_lists_targets_with_inspector_urls(self):
        body = json.dumps([
            {"id": "AAA1", "title": "Steam", "url": "https://steamloopback.host/routes/?vrOverlayKey=valve.steam.gamepadui.main&x=1", "type": "page"},
            {"id": "BBB2", "title": "Bar", "url": "https://steamloopback.host/?vrOverlayKey=valve.steam.gamepadui.bar", "type": "page"},
            {"id": "CCC3", "title": "Other " + "t" * 300, "url": "https://x.test/" + "u" * 400},
            {"id": "DDD4", "title": "Mine", "url": "https://steamloopback.host/mine/zone"},
            {"id": "bad id!", "title": "skipped", "url": "u"},
            {"title": "no id"},
            "junk",
        ]).encode()
        port = await self.serve(body)
        r = await fy_dev.devtools_info(self.host({"mine": "/mine/"}), port=port)
        self.assertTrue(r["available"])
        self.assertIsNone(r["error"])
        self.assertEqual(r["port"], port)
        self.assertEqual([t["name"] for t in r["targets"]], ["main", "bar", "", "mine"])
        self.assertEqual(r["targets"][0]["inspector"], "http://127.0.0.1:%d/devtools/inspector.html?ws=127.0.0.1:%d/devtools/page/AAA1" % (port, port))
        self.assertEqual(len(r["targets"][2]["title"]), 120)
        self.assertEqual(len(r["targets"][2]["url"]), 300)
        self.assertIn("ssh -L %d:127.0.0.1:%d steamos@<headset>" % (port, port), r["hint"])
        self.assertIn("not verified", r["hint"])

    async def test_target_list_is_bounded(self):
        port = await self.serve(json.dumps([{"id": "t%d" % i, "title": "x", "url": "u"} for i in range(100)]).encode())
        r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertEqual(len(r["targets"]), 32)

    async def test_refused_port_gives_a_readable_error(self):
        port = free_port()
        r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertFalse(r["available"])
        self.assertEqual(r["targets"], [])
        self.assertIn("Nothing is listening on port %d" % port, r["error"])
        self.assertIn("ssh -L", r["hint"])

    async def test_non_json_and_non_list_replies(self):
        for body in (b"<html>no</html>", b"{\"a\": 1}", b"", b"\xff\xfe"):
            port = await self.serve(body)
            r = await fy_dev.devtools_info(self.host(), port=port)
            self.assertFalse(r["available"], body)
            self.assertIn("not with a list", r["error"])

    async def test_non_finite_constants_in_the_reply_are_refused(self):
        for body in (b'[{"id": "a", "title": NaN, "url": "u"}]', b'[Infinity]', b'[{"id": "a", "n": -Infinity}]'):
            port = await self.serve(body)
            r = await fy_dev.devtools_info(self.host(), port=port)
            self.assertFalse(r["available"], body)
            self.assertIn("not with a list", r["error"])

    async def test_error_status_is_not_trusted(self):
        port = await self.serve(b"[]", status=b"500 Internal Server Error")
        r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertFalse(r["available"])
        self.assertIn("not with a list", r["error"])

    async def test_status_code_must_be_exactly_200(self):
        port = await self.serve(b"[]", status=b"2000 Invalid")
        r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertFalse(r["available"])

    async def test_json_decoder_recursion_error_is_a_readable_error(self):
        port = await self.serve(b"[]")
        with mock.patch.object(fy_dev.json, "loads", side_effect=RecursionError):
            r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertFalse(r["available"])
        self.assertIn("not with a list", r["error"])

    async def test_non_string_target_text_is_not_stringified(self):
        port = await self.serve(b'[{"id":"a","title":{"x":1},"url":["x"]}]')
        r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertTrue(r["available"])
        self.assertEqual(r["targets"][0]["title"], "")
        self.assertEqual(r["targets"][0]["url"], "")

    async def test_slow_port_times_out(self):
        port = await self.serve(b"[]", delay=1)
        with mock.patch.object(fy_dev, "TIMEOUT", 0.2):
            r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertFalse(r["available"])
        self.assertIn("did not answer within 0.2 s", r["error"])

    async def test_oversize_reply_is_refused(self):
        port = await self.serve(json.dumps([{"id": "a", "title": "x" * 5000, "url": "u"}]).encode())
        with mock.patch.object(fy_dev, "JSON_MAX", 1000):
            r = await fy_dev.devtools_info(self.host(), port=port)
        self.assertFalse(r["available"])

    async def test_host_without_dev_table_is_fine(self):
        port = await self.serve(b"[]")
        r = await fy_dev.devtools_info(types.SimpleNamespace(settings=lambda: {}), port=port)
        self.assertTrue(r["available"])
        r = await fy_dev.devtools_info(types.SimpleNamespace(settings=lambda: 1 / 0), port=port)
        self.assertTrue(r["available"])


class MethodTests(Base):
    def run_method(self, name, arg=None):
        return asyncio.run(fy_dev.METHODS[name](self.host, arg))

    def test_method_names(self):
        self.assertEqual(set(fy_dev.METHODS), {"dev_set", "dev_status", "devtools_info"})
        for fn in fy_dev.METHODS.values():
            self.assertTrue(inspect.iscoroutinefunction(fn))

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(fy_dev, "load_libc", return_value=self.libc)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: fy_dev.shutdown(self.host))

    def test_status_without_hot_reload(self):
        s = self.run_method("dev_status")
        self.assertTrue(s["enabled"])
        self.assertEqual(s["hot"], [])
        self.assertIn(s["exposure"]["state"], ("unknown", "closed", "loopback", "exposed"))
        self.assertFalse(hasattr(self.host, "hot"))

    def test_switch_on_and_off(self):
        s = self.run_method("dev_set", {"id": "p", "hot": True})
        self.assertEqual([h["id"] for h in s["hot"]], ["p"])
        self.assertEqual(s["hot"][0]["mode"], "inotify")
        s = self.run_method("dev_set", {"id": "p", "hot": False})
        self.assertEqual(s["hot"], [])

    def test_save_failure_is_visible_in_status(self):
        self.host.save = lambda s: (_ for _ in ()).throw(OSError("disk full"))
        r = self.run_method("dev_set", {"id": "p", "hot": True})
        self.assertEqual(r["hot"][0]["id"], "p")
        self.assertIn("could not be saved", r["warning"])

    def test_switch_all_off(self):
        self.run_method("dev_set", {"id": "p", "hot": True})
        self.run_method("dev_set", {"id": "q", "hot": True})
        s = self.run_method("dev_set", {"hot": False})
        self.assertEqual(s["hot"], [])
        self.assertEqual(self.host.hot_state["hot"], [])

    def test_errors_are_replies_not_exceptions(self):
        for arg in (None, {}, {"id": "p"}, {"id": "p", "hot": 1}, {"hot": True}, {"id": 5, "hot": False}, [], "x"):
            r = self.run_method("dev_set", arg)
            self.assertEqual(r["code"], "bad_arg", arg)
        r = self.run_method("dev_set", {"id": "nope", "hot": True})
        self.assertEqual(r["code"], "not_found")
        self.host.state["prefs"]["dev"]["enabled"] = False
        r = self.run_method("dev_set", {"id": "p", "hot": True})
        self.assertEqual(r["code"], "dev_off")

    def test_switching_off_works_when_developer_mode_was_turned_off(self):
        self.run_method("dev_set", {"id": "p", "hot": True})
        self.host.state["prefs"]["dev"]["enabled"] = False
        s = self.run_method("dev_set", {"id": "p", "hot": False})
        self.assertEqual(s["hot"], [])
        self.assertFalse(s["enabled"])

    def test_devtools_info_method(self):
        r = asyncio.run(fy_dev.METHODS["devtools_info"](self.host, None))
        self.assertEqual(r["port"], 8080)
        self.assertIn("available", r)


@unittest.skipUnless(LINUX, "needs real inotify on Linux (pending Linux run)")
class RealInotifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacing_watched_root_reloads_and_keeps_detecting_edits(self):
        with tempfile.TemporaryDirectory() as d:
            root = tree(d, "p", ("main.js",))
            seen, done = [], asyncio.Event()
            loop = asyncio.get_running_loop()

            async def reload_one(pid):
                with open(os.path.join(root, "main.js")) as f:
                    seen.append(f.read())
                done.set()

            host = types.SimpleNamespace(
                settings=lambda: {"prefs": {"dev": {"enabled": True}}},
                save=lambda s: None, real=lambda pid: root,
                mono=loop.time, now=loop.time, loop=lambda: loop, log=lambda *a: None,
            )
            hot = fy_dev.HotReload(host, reload_one, debounce=0.02, interval=0.05, ttl=30)
            self.addCleanup(hot.disable_all)
            self.assertEqual(hot.enable("p")["mode"], "inotify")
            os.rename(root, root + "-previous")
            os.mkdir(root)
            with open(os.path.join(root, "main.js"), "w") as f:
                f.write("replacement")
            await asyncio.wait_for(done.wait(), 2)
            self.assertEqual(seen, ["replacement"])
            self.assertEqual(hot.status()[0]["mode"], "mtime")
            done.clear()
            with open(os.path.join(root, "main.js"), "w") as f:
                f.write("later edit")
            await asyncio.wait_for(done.wait(), 2)
            self.assertEqual(seen, ["replacement", "later edit"])

    async def test_writes_collapse_into_one_reload(self):
        with tempfile.TemporaryDirectory() as d:
            root = tree(d, "p", ("main.js",))
            seen = []
            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            async def reload_one(pid):
                seen.append(pid)
                done.set()

            host = types.SimpleNamespace(
                settings=lambda: {"prefs": {"dev": {"enabled": True}}},
                save=lambda s: None,
                real=lambda pid: root,
                mono=loop.time,
                now=loop.time,
                loop=lambda: loop,
                log=lambda *a: None,
            )
            hot = fy_dev.HotReload(host, reload_one, debounce=0.05, ttl=30)
            self.addCleanup(hot.disable_all)
            self.assertEqual(hot.enable("p")["mode"], "inotify")
            os.mkdir(os.path.join(root, ".stage-x"))
            with open(os.path.join(root, ".stage-x", "f"), "w") as f:
                f.write("x")
            await asyncio.sleep(0.2)
            self.assertEqual(seen, [])
            for i in range(5):
                with open(os.path.join(root, "main.js"), "w") as f:
                    f.write(str(i))
            await asyncio.wait_for(done.wait(), 2)
            await asyncio.sleep(0.3)
            self.assertEqual(seen, ["p"])
            hot.disable_all()
            self.assertIsNone(hot._fd)


class HygieneTests(unittest.TestCase):
    path = os.path.join(ROOT, "fy_dev.py")

    def source(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_no_comments_or_docstrings(self):
        src = self.source()
        kinds = {t.type for t in tokenize.generate_tokens(io.StringIO(src).readline)}
        self.assertNotIn(tokenize.COMMENT, kinds)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(ast.get_docstring(node))

    def test_no_sleep_loops_and_stdlib_only(self):
        tree_ = ast.parse(self.source())
        names = set()
        for node in ast.walk(tree_):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module.split(".")[0])
            elif isinstance(node, ast.Attribute) and node.attr == "sleep":
                self.fail("sleep used")
        self.assertLessEqual(names, {"asyncio", "ipaddress", "json", "os", "re", "struct", "sys", "ctypes"})
        self.assertNotIn("framey", names)

    def test_import_has_no_side_effects(self):
        code = "import sys; sys.path.insert(0, %r); import fy_dev; print(sorted(m for m in ('ctypes', 'framey') if m in sys.modules))" % ROOT
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)

    def test_python_39_syntax(self):
        ast.parse(self.source(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
