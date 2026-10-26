import asyncio
import unittest
from unittest import mock

from test_fy_supervisor import BackendError, EventHub, SupCase, fy_supervisor


class SupervisorParityTests(SupCase):
    async def test_stop_fails_already_queued_calls_without_restarting(self):
        self.reg("a", "slow", timeout=5)
        await self.sup.call("a", "x", 0)
        w = self.worker("a")
        active = asyncio.create_task(self.sup.call("a", "x", 3))
        self.addAsyncCleanup(self.finish_tasks, active)
        await self.until(lambda: w.inflight == 1 and bool(w.pending))
        queued = asyncio.create_task(self.sup.call("a", "x", 0))
        self.addAsyncCleanup(self.finish_tasks, queued)
        await self.until(lambda: len(w.waiters) == 1)

        await asyncio.wait_for(self.sup.stop("a", "disable"), 4)
        with self.assertRaises(BackendError) as cm:
            await asyncio.wait_for(queued, 2)
        self.assertEqual(cm.exception.code, "stopped")
        await self.finish_tasks(active)
        await asyncio.sleep(0)
        self.assertEqual((w.state, w.proc, w.inflight, len(w.waiters)), ("stopped", None, 0, 0))

    async def test_cancelling_an_active_call_cancels_the_backend_work(self):
        d, _ = self.reg("a", "ok", timeout=5)
        (d / "backend.py").write_text("""import asyncio
from pathlib import Path

async def call(method, arg):
    if method == "work":
        Path("started").write_text("yes")
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            Path("cancelled").write_text("yes")
            raise
        Path("completed").write_text("yes")
    return "ok"
""")
        active = asyncio.create_task(self.sup.call("a", "work", None))
        self.addAsyncCleanup(self.finish_tasks, active)
        await self.until(lambda: (self.data_dir("a") / "started").exists())
        worker_pid = self.worker("a").os_pid
        active.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await active

        await self.until(lambda: (self.data_dir("a") / "cancelled").exists(), 1)
        self.assertFalse((self.data_dir("a") / "completed").exists())
        self.assertEqual(await asyncio.wait_for(self.sup.call("a", "quick", None), 2), "ok")
        self.assertEqual(self.worker("a").os_pid, worker_pid)
        self.assertEqual(self.worker("a").inflight, 0)
        self.assertEqual(self.worker("a").pending, {})

    async def test_stop_during_async_post_startup_hook_cannot_publish_ready(self):
        self.reg("a", "ok", timeout=5)
        self.host.states["a"] = {"ver": "0.9.0", "data_v": None}
        dropping, resume = asyncio.Event(), asyncio.Event()

        async def drop_snapshot(pid):
            dropping.set()
            await resume.wait()

        self.host.drop_snapshot = drop_snapshot
        active = asyncio.create_task(self.sup.call("a", "echo", "old"))
        self.addAsyncCleanup(self.finish_tasks, active)
        await asyncio.wait_for(dropping.wait(), 2)
        await asyncio.wait_for(self.sup.stop("a", "disable"), 4)
        resume.set()
        with self.assertRaises(BackendError) as cm:
            await asyncio.wait_for(active, 2)
        self.assertEqual(cm.exception.code, "stopped")
        await self.until(lambda: not self.worker("a").starting)
        self.assertEqual((self.state("a"), self.worker("a").proc), ("stopped", None))
        self.assertEqual(await self.sup.call("a", "echo", "new"), {"echo": "new"})

    async def test_uninstall_does_not_admit_calls_queued_behind_the_hook(self):
        d, _ = self.reg("a", "ok", timeout=5)
        (d / "backend.py").write_text("""import asyncio
from pathlib import Path

async def uninstall(purge):
    Path("uninstalling").write_text("yes")
    while not Path("finish-uninstall").exists():
        await asyncio.sleep(0.02)

async def call(method, arg):
    Path("called").write_text("yes")
    return "ran"
""")
        removing = asyncio.create_task(self.sup.uninstall("a"))
        self.addAsyncCleanup(self.finish_tasks, removing)
        await self.until(lambda: (self.data_dir("a") / "uninstalling").exists())
        queued = asyncio.create_task(self.sup.call("a", "work", None))
        self.addAsyncCleanup(self.finish_tasks, queued)
        try:
            await self.until(lambda: queued.done() or len(self.worker("a").waiters) == 1)
        finally:
            (self.data_dir("a") / "finish-uninstall").write_text("yes")
        result = await asyncio.wait_for(removing, 4)
        self.assertEqual(result["hook"], "ran")
        with self.assertRaises(BackendError) as cm:
            await asyncio.wait_for(queued, 2)
        self.assertEqual(cm.exception.code, "stopped")
        self.assertFalse((self.data_dir("a") / "called").exists())
        self.assertEqual((self.state("a"), self.worker("a").proc), ("stopped", None))

    async def test_cancelled_launch_releases_reservation_and_fails_its_caller(self):
        self.reg("a", "ok", timeout=5)
        launch = self.sup._launch
        entered, resume = asyncio.Event(), asyncio.Event()

        async def gated_launch(w, dirs):
            entered.set()
            await resume.wait()
            return await launch(w, dirs)

        self.sup._launch = gated_launch
        active = asyncio.create_task(self.sup.call("a", "echo", "old"))
        self.addAsyncCleanup(self.finish_tasks, active)
        await asyncio.wait_for(entered.wait(), 2)
        boot = self.worker("a").boot
        boot.cancel()
        await asyncio.gather(boot, return_exceptions=True)
        with self.assertRaises(BackendError) as cm:
            await asyncio.wait_for(active, 1)
        self.assertEqual(cm.exception.code, "stopped")
        self.assertEqual((self.state("a"), self.worker("a").proc, self.sup._live()), ("stopped", None, 0))
        resume.set()
        self.assertEqual(await self.sup.call("a", "echo", "new"), {"echo": "new"})

    async def test_caller_cancelled_as_startup_finishes_still_arms_one_idle_timer(self):
        self.reg("a", "ok", idle=0.2)
        entered, resume = asyncio.Event(), asyncio.Event()
        start_hooks = self.sup._start_hooks
        host_loop, idle_timers = self.host.loop, []
        supervisor = self.sup

        class RecordingLoop:
            def __getattr__(self, name):
                return getattr(host_loop(), name)

            def call_later(self, delay, callback, *args):
                timer = host_loop().call_later(delay, callback, *args)
                if callback == supervisor._idle:
                    idle_timers.append(timer)
                return timer

        async def gated_hooks(w, dirs):
            await start_hooks(w, dirs)
            entered.set()
            await resume.wait()

        self.host.loop = RecordingLoop
        self.sup._start_hooks = gated_hooks
        caller = asyncio.create_task(self.sup.call("a", "echo", "unused"))
        self.addAsyncCleanup(self.finish_tasks, caller)
        await asyncio.wait_for(entered.wait(), 2)
        resume.set()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        await self.until(lambda: not self.worker("a").starting)
        w = self.worker("a")
        self.assertEqual((w.state, w.inflight, len(w.waiters)), ("ready", 0, 0))
        self.assertIsNotNone(w.proc)
        self.assertIsNotNone(w.timer)
        self.assertEqual(idle_timers, [w.timer])
        await self.until(lambda: w.state == "stopped" and w.proc is None, 3)
        self.assertIsNone(w.timer)
        self.assertIn("unload idle", self.hooks("a"))

    async def test_failed_startup_still_reserves_worker_slot_during_termination(self):
        self.host.prefs["workers_max"] = 1
        self.reg("a", "startup_fail")
        self.reg("b", "ok")
        kill = self.sup._kill
        terminating, resume = asyncio.Event(), asyncio.Event()

        async def gated_kill(w, proc):
            if w.pid == "a":
                terminating.set()
                await resume.wait()
            await kill(w, proc)

        self.sup._kill = gated_kill
        active = asyncio.create_task(self.sup.call("a", "echo", 1))
        self.addAsyncCleanup(self.finish_tasks, active)
        await asyncio.wait_for(terminating.wait(), 2)
        try:
            with self.assertRaises(BackendError) as cm:
                await asyncio.wait_for(self.sup.call("b", "echo", 2), 2)
            self.assertEqual(cm.exception.code, "busy_workers")
            self.assertIsNone(self.worker("b").proc)
        finally:
            resume.set()
        await self.finish_tasks(active)
        await self.until(lambda: self.worker("a").proc is None)
        self.assertEqual(await self.sup.call("b", "echo", 3), {"echo": 3})

    async def test_forgotten_worker_cannot_repopulate_events_during_unload(self):
        d, _ = self.reg("a", "ok")
        (d / "backend.py").write_text("""import framey_api

def call(method, arg):
    framey_api.events.emit("before", None)
    return "ok"

def unload(reason):
    framey_api.events.emit("after", reason)
""")
        await self.sup.call("a", "work", None)
        self.assertEqual([event["name"] for event in self.sup.events.take()], ["before"])
        old = self.worker("a")
        self.sup.forget("a")
        await self.until(lambda: old.proc is None)
        self.assertEqual(self.sup.events.take(), [])
        self.assertNotIn("a", self.sup.status())

    async def test_startup_failure_after_migration_restores_settings_snapshot(self):
        self.reg("a", "startup_fail")
        self.host.states["a"] = {"ver": "0.9.0", "data_v": 1}
        with self.assertRaises(BackendError) as cm:
            await self.sup.call("a", "work", None)
        self.assertEqual(cm.exception.stage, "startup")
        self.assertEqual(self.host.snapshots, ["a"])
        self.assertEqual(self.host.restored, ["a"])
        self.assertEqual(self.host.dropped, [])
        self.assertEqual(self.host.states["a"], {"ver": "0.9.0", "data_v": 1})

    async def test_cancelling_uninstall_still_stops_the_worker(self):
        d, _ = self.reg("a", "ok")
        (d / "backend.py").write_text("""import asyncio
from pathlib import Path

async def uninstall(purge):
    Path("uninstalling").write_text("yes")
    await asyncio.sleep(30)

async def call(method, arg):
    return "ok"
""")
        removing = asyncio.create_task(self.sup.uninstall("a"))
        self.addAsyncCleanup(self.finish_tasks, removing)
        await self.until(lambda: (self.data_dir("a") / "uninstalling").exists())
        removing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(removing, 4)
        await self.until(lambda: self.worker("a").proc is None, 2)
        self.assertEqual((self.state("a"), self.worker("a").inflight), ("stopped", 0))
        self.assertEqual(await self.sup.call("a", "work", None), "ok")

    async def test_purge_keeps_backend_calls_blocked_until_removal_finishes(self):
        self.reg("a", "ok")
        purging, resume = asyncio.Event(), asyncio.Event()

        async def purge(pid):
            purging.set()
            await resume.wait()
            return {"removed": ["data"], "failed": []}

        self.host.purge = purge
        removing = asyncio.create_task(self.sup.uninstall("a", purge=True))
        self.addAsyncCleanup(self.finish_tasks, removing)
        await asyncio.wait_for(purging.wait(), 2)
        try:
            with self.assertRaises(BackendError) as cm:
                await asyncio.wait_for(self.sup.call("a", "echo", "blocked"), 2)
            self.assertEqual(cm.exception.code, "stopped")
            self.assertIsNone(self.worker("a").proc)
        finally:
            resume.set()
        self.assertTrue((await asyncio.wait_for(removing, 2))["purged"])

    async def test_uninstall_waits_for_active_call_when_concurrency_is_two(self):
        d, _ = self.reg("a", "ok", concurrency=2)
        (d / "backend.py").write_text("""import asyncio
from pathlib import Path

async def call(method, arg):
    Path("calling").write_text("yes")
    while not Path("finish-call").exists():
        await asyncio.sleep(0.02)
    return "ok"

def uninstall(purge):
    Path("uninstalling").write_text("yes")
""")
        active = asyncio.create_task(self.sup.call("a", "work", None))
        self.addAsyncCleanup(self.finish_tasks, active)
        await self.until(lambda: (self.data_dir("a") / "calling").exists())
        removing = asyncio.create_task(self.sup.uninstall("a"))
        self.addAsyncCleanup(self.finish_tasks, removing)
        try:
            await self.until(lambda: removing.done() or bool(self.worker("a").waiters) or (self.data_dir("a") / "uninstalling").exists())
            self.assertFalse((self.data_dir("a") / "uninstalling").exists())
        finally:
            (self.data_dir("a") / "finish-call").write_text("yes")
        self.assertEqual(await asyncio.wait_for(active, 2), "ok")
        self.assertEqual((await asyncio.wait_for(removing, 4))["hook"], "ran")
        self.assertTrue((self.data_dir("a") / "uninstalling").exists())

    async def finish_tasks(self, *tasks):
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class EventLossParityTests(unittest.TestCase):
    def test_legacy_events_and_remote_losses_are_delivered_once(self):
        hub = EventHub(lambda: None)
        self.assertTrue(hub.put("a", "legacy", None))
        self.assertTrue(hub.put("a", "new", None, dropped=4))
        events = hub.take()
        self.assertEqual([event["name"] for event in events], ["legacy", "new"])
        self.assertEqual([event["dropped"] for event in events], [4, 0])

    def test_invalid_remote_loss_counts_reject_the_event_and_count_one_loss(self):
        hub = EventHub(lambda: None)
        for value in (True, False, -1, None, 1.5, "3", {}):
            with self.subTest(dropped=value):
                self.assertFalse(hub.put("a", "invalid", None, dropped=value))
        self.assertEqual(hub.take(), [])
        self.assertTrue(hub.put("a", "good", None))
        self.assertEqual(hub.take()[0]["dropped"], 7)

    def test_remote_losses_survive_a_rejected_event_until_delivery(self):
        hub = EventHub(lambda: None)
        self.assertFalse(hub.put("a", "Bad Name", None, dropped=4))
        self.assertEqual(hub.take(), [])
        self.assertTrue(hub.put("a", "good", None))
        self.assertEqual(hub.take()[0]["dropped"], 5)
        self.assertTrue(hub.put("a", "later", None))
        self.assertEqual(hub.take()[0]["dropped"], 0)

    def test_remote_and_rate_losses_are_combined_once(self):
        clock = [0.0]
        hub = EventHub(lambda: None, clock=lambda: clock[0])
        with mock.patch.object(fy_supervisor, "EVENT_RATE", 1):
            self.assertTrue(hub.put("a", "first", None))
            self.assertFalse(hub.put("a", "rate", None, dropped=7))
            self.assertEqual(hub.take()[0]["dropped"], 8)
            clock[0] = 1.0
            self.assertTrue(hub.put("a", "next", None))
            self.assertEqual(hub.take()[0]["dropped"], 0)

    def test_remote_and_queue_losses_are_combined_once(self):
        hub = EventHub(lambda: None)
        with mock.patch.object(fy_supervisor, "EVENT_QUEUE", 2):
            self.assertTrue(hub.put("a", "oldest", None))
            self.assertTrue(hub.put("a", "second", None))
            self.assertTrue(hub.put("a", "third", None, dropped=4))
        events = hub.take()
        self.assertEqual([event["name"] for event in events], ["second", "third"])
        self.assertEqual([event["dropped"] for event in events], [5, 0])


if __name__ == "__main__":
    unittest.main()
