import asyncio
import gc
import shutil
import subprocess
import sys
import types
import unittest
import warnings
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "a"))

import harness
import fy_proto
import fy_worker


class WorkerRequestOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = []
        self.worker = fy_worker.Worker(asyncio.get_running_loop(), -1)
        self.worker.write = self.messages.append
        self.worker.inited = True
        self.worker.mod = types.SimpleNamespace()
        self.worker.init = mock.Mock()
        self.created = []

    async def asyncTearDown(self):
        pending = set(self.created) | set(self.worker.tasks)
        pending |= {task for task in asyncio.all_tasks()
                    if task is not asyncio.current_task()}
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def send(self, **message):
        self.worker.on_line(fy_proto.encode(message))

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    def reply(self, rid):
        return [message for message in self.messages if message.get("re") == rid]

    async def start_waiting_call(self, rid=1, host=False):
        entered = asyncio.Event()

        async def call(method, arg):
            entered.set()
            if host:
                return await self.worker.host("fetch", {"url": "https://example.test"})
            await asyncio.Event().wait()

        self.worker.ready = True
        self.worker.mod.call = call
        self.send(op="call", id=rid, method="wait")
        await asyncio.wait_for(entered.wait(), 1)
        await self.settle()

    async def test_boolean_cancel_target_does_not_alias_request_one(self):
        await self.start_waiting_call()
        self.send(op="cancel", id=2, target=True)
        await self.settle()
        self.assertEqual(self.reply(2)[0]["result"], {"cancelled": False})
        self.assertIn(1, self.worker.calls)
        self.send(op="cancel", id=3, target=1)
        await self.settle()
        self.assertEqual(self.reply(3)[0]["result"], {"cancelled": True})
        self.assertEqual(self.reply(1), [])

    async def test_cancel_before_call_starts_does_not_leak_operation_coroutine(self):
        self.worker.ready = True
        call = mock.Mock(return_value="unused")
        self.worker.mod.call = call
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            self.send(op="call", id=40, method="unused")
            self.send(op="cancel", id=41, target=40)
            await self.settle()
            gc.collect()
        call.assert_not_called()
        self.assertEqual(self.reply(41)[0]["result"], {"cancelled": True})
        self.assertEqual(self.reply(40), [])
        self.assertFalse([warning for warning in caught
                          if "was never awaited" in str(warning.message)])

    async def test_boolean_host_reply_id_cannot_complete_host_request_one(self):
        task = asyncio.create_task(self.worker.host("fetch", {}))
        self.created.append(task)
        await self.settle()
        self.worker.host_reply({"id": True, "ok": True, "result": "wrong"})
        await self.settle()
        self.assertFalse(task.done())
        self.worker.host_reply({"id": 1, "ok": True, "result": "correct"})
        self.assertEqual(await asyncio.wait_for(task, 1), "correct")
        self.assertEqual(self.worker.waiting, {})

    async def test_duplicate_call_keeps_original_request_cancellable(self):
        await self.start_waiting_call()
        original = self.worker.calls[1]
        self.send(op="call", id=1, method="duplicate")
        await self.settle()
        self.assertIs(self.worker.calls.get(1), original)
        replies = self.reply(1)
        self.assertEqual(len(replies), 1)
        self.assertFalse(replies[0]["ok"])
        self.assertEqual(replies[0]["error"]["code"], "busy")
        self.send(op="cancel", id=2, target=1)
        await self.settle()
        self.assertEqual(self.reply(2)[0]["result"], {"cancelled": True})
        self.assertEqual(self.worker.calls, {})
        self.assertEqual(self.reply(1), replies)

    async def test_cancelled_call_releases_its_host_waiter_and_ignores_late_reply(self):
        await self.start_waiting_call(rid=7, host=True)
        ask = next(message for message in self.messages if "host" in message)
        self.assertEqual(len(self.worker.waiting), 1)
        self.send(op="cancel", id=8, target=7)
        await self.settle()
        self.assertEqual(self.reply(8)[0]["result"], {"cancelled": True})
        self.assertEqual(self.worker.waiting, {})
        self.worker.host_reply({"id": ask["id"], "ok": True, "result": "late"})
        self.send(op="ping", id=9)
        self.assertEqual(self.reply(9)[0]["result"], {"pong": True})
        self.assertEqual(self.reply(7), [])

    async def test_burst_admits_capacity_and_rejects_only_excess_call(self):
        entered = []

        async def call(method, arg):
            entered.append(method)
            await asyncio.Event().wait()

        self.worker.ready = True
        self.worker.mod.call = call
        with mock.patch.object(fy_worker, "CALLS_MAX", 2):
            for rid in (30, 31, 32):
                self.send(op="call", id=rid, method=str(rid))
            await self.settle()
        self.assertEqual(entered, ["30", "31"])
        self.assertEqual(self.reply(30), [])
        self.assertEqual(self.reply(31), [])
        self.assertEqual(self.reply(32)[0]["error"]["code"], "busy")

    async def test_call_that_suppresses_cancellation_cannot_send_late_reply(self):
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def call(method, arg):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return "late result"

        self.worker.ready = True
        self.worker.mod.call = call
        self.send(op="call", id=33)
        await asyncio.wait_for(entered.wait(), 1)
        self.send(op="cancel", id=34, target=33)
        await asyncio.wait_for(cancelled.wait(), 1)
        self.assertEqual(self.reply(34)[0]["result"], {"cancelled": True})
        release.set()
        await self.settle()
        self.assertEqual(self.reply(33), [])
        self.assertEqual(self.worker.calls, {})

    async def test_unload_cancels_call_and_host_waiter_before_hook(self):
        await self.start_waiting_call(rid=35, host=True)
        observed = []

        def unload(reason):
            observed.append((reason, dict(self.worker.waiting)))

        self.worker.mod.unload = unload
        with mock.patch.object(fy_worker, "leave", side_effect=asyncio.CancelledError) as leave:
            self.send(op="unload", id=36, reason="reload")
            await self.settle()
            self.assertEqual(observed, [("reload", {})])
            self.assertEqual(self.reply(35), [])
            self.assertTrue(self.reply(36)[0]["ok"])
            leave.assert_called_once_with(0)

    async def test_overlapping_startup_runs_hook_once(self):
        entered, release = asyncio.Event(), asyncio.Event()
        starts = []

        async def startup(ctx):
            starts.append(ctx.plugin)
            entered.set()
            await release.wait()

        self.worker.mod.startup = startup
        self.send(op="startup", id=10)
        await asyncio.wait_for(entered.wait(), 1)
        self.send(op="startup", id=11)
        await self.settle()
        self.assertEqual(len(starts), 1)
        self.assertEqual(self.reply(11)[0]["error"]["code"], "busy")
        release.set()
        await self.settle()
        self.assertTrue(self.reply(10)[0]["ok"])
        self.assertTrue(self.worker.ready)

    async def test_startup_waits_for_migration_to_finish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def migrate(ctx):
            order.append("migrate")
            entered.set()
            await release.wait()

        def startup(ctx):
            order.append("startup")

        self.worker.mod.migrate = migrate
        self.worker.mod.startup = startup
        self.send(op="migrate", id=12)
        await asyncio.wait_for(entered.wait(), 1)
        self.send(op="startup", id=13)
        await self.settle()
        self.assertEqual(order, ["migrate"])
        self.assertEqual(self.reply(13)[0]["error"]["code"], "busy")
        release.set()
        await self.settle()
        self.send(op="startup", id=14)
        await self.settle()
        self.assertEqual(order, ["migrate", "startup"])
        self.assertTrue(self.reply(14)[0]["result"]["migrated"])

    async def test_migrate_after_startup_does_not_modify_live_backend(self):
        self.worker.ready = True
        migrate = mock.Mock()
        self.worker.mod.migrate = migrate
        self.send(op="migrate", id=15)
        await self.settle()
        migrate.assert_not_called()
        self.assertEqual(self.reply(15)[0]["error"]["stage"], "migrate")

    async def test_repeated_term_runs_unload_once(self):
        entered, release = asyncio.Event(), asyncio.Event()
        reasons = []

        async def unload(reason):
            reasons.append(reason)
            entered.set()
            await release.wait()

        self.worker.mod.unload = unload
        with mock.patch.object(fy_worker, "leave", side_effect=asyncio.CancelledError) as leave:
            self.worker.term()
            await asyncio.wait_for(entered.wait(), 1)
            self.worker.term()
            await self.settle()
            self.assertEqual(reasons, ["shutdown"])
            release.set()
            await self.settle()
            leave.assert_called_once_with(0)
        self.assertEqual(len(self.reply(0)), 1)

    async def test_async_hook_timeout_does_not_wait_forever_for_cancellation(self):
        entered = asyncio.Event()

        async def stubborn(reason):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.Event().wait()

        self.worker.mod.unload = stubborn
        task = asyncio.create_task(self.worker.hook("unload", "shutdown", .02, "unload"))
        self.created.append(task)
        await asyncio.wait_for(entered.wait(), 1)
        done, _ = await asyncio.wait({task}, timeout=.3)
        self.assertIn(task, done, "the timeout must bound even a hook that suppresses cancellation")
        with self.assertRaises(fy_worker.Fail) as raised:
            task.result()
        self.assertEqual(raised.exception.err["code"], "timeout")
        self.assertEqual(raised.exception.err["stage"], "unload")


class WorkerProcessParityTests(unittest.TestCase):
    def setUp(self):
        self.root = harness.make_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        plugin, _ = harness.make_plugin(self.root, "p", "ok")
        self.wire = harness.Wire(self.root)
        self.addCleanup(self.wire.close)
        self.startup = self.wire.request(harness.startup_message(1, "p", plugin / "backend.py", self.root))
        self.assertTrue(self.startup["ok"], self.startup)

    def test_eof_runs_shutdown_hook_before_exit(self):
        self.wire.proc.stdin.close()
        self.assertEqual(self.wire.proc.wait(timeout=5), 0)
        self.assertIn("unload shutdown", (self.root / "hooks.log").read_text().splitlines())

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux resource limits")
    def test_reported_data_limit_matches_finite_native_limit(self):
        self.assertTrue(self.startup["result"]["limits_applied"], self.startup)
        self.assertIsNone(self.startup["result"]["limits_reason"])
        rep = self.wire.request({"op": "call", "id": 2, "method": "limits"})
        soft, hard = rep["result"]["RLIMIT_DATA"]
        self.assertGreater(soft, 64 << 20)
        self.assertEqual(soft, hard)
        self.assertLess(soft, 256 << 20)


class WorkerInitializationParityTests(unittest.TestCase):
    def setUp(self):
        self.worker = fy_worker.Worker(None, -1)
        self.message = harness.startup_message(1, "p", Path("/tmp/p/backend.py"), Path("/tmp/framey-test"))

    def test_import_retry_does_not_increase_memory_headroom_again(self):
        with mock.patch.object(self.worker, "load", side_effect=RuntimeError("broken import")), \
                mock.patch.object(fy_worker, "limit_data", return_value=(True, None)) as limit:
            for _ in range(2):
                with self.assertRaises(fy_worker.Fail):
                    self.worker.init(self.message)
        self.assertEqual(limit.call_count, 1)

    def test_invalid_directories_do_not_change_process_limit(self):
        self.message["dirs"] = {}
        with mock.patch.object(fy_worker, "limit_data", return_value=(True, None)) as limit:
            with self.assertRaises(fy_worker.Fail):
                self.worker.init(self.message)
        limit.assert_not_called()

    def test_failed_limit_application_is_reported_by_startup(self):
        async def startup():
            with mock.patch.object(self.worker, "load", return_value=types.SimpleNamespace()), \
                    mock.patch.object(fy_worker, "limit_data", return_value=(False, "test denied")):
                return await self.worker.op_startup(1, self.message)

        result = asyncio.run(startup())
        self.assertFalse(result["limits_applied"])
        self.assertEqual(result["limits_reason"], "test denied")

    def test_unavailable_runtime_directory_is_preserved_as_none(self):
        self.message["dirs"]["runtime"] = None
        with mock.patch.object(self.worker, "load", return_value=types.SimpleNamespace()), \
                mock.patch.object(fy_worker, "limit_data", return_value=(True, None)):
            self.worker.init(self.message)
        self.assertIsNone(self.worker.dirs.runtime)
        for name in ("config", "data", "logs"):
            self.assertIsInstance(getattr(self.worker.dirs, name), Path)

    def test_input_io_failure_schedules_shutdown_and_closes_reader(self):
        loop, stream = mock.Mock(), mock.Mock()
        stream.readline.side_effect = OSError("input failed")
        worker = fy_worker.Worker(loop, -1)
        with mock.patch.object(fy_worker.os, "fdopen", return_value=stream):
            worker.read(123)
        stream.close.assert_called_once_with()
        loop.call_soon_threadsafe.assert_called_once_with(worker.term)


class WorkerHookExitParityTests(unittest.TestCase):
    def test_system_exit_in_async_lifecycle_hook_is_a_stage_error(self):
        for stage in ("startup", "migrate", "uninstall", "unload"):
            with self.subTest(stage=stage):
                root = harness.make_root()
                self.addCleanup(shutil.rmtree, root, True)
                plugin, _ = harness.make_plugin(root, "p", "ok")
                backend = plugin / "backend.py"
                backend.write_text("async def %s(ctx):\n    raise SystemExit('hook exit')\n" % stage)
                wire = harness.Wire(root)
                self.addCleanup(wire.close)
                startup = harness.startup_message(1, "p", backend, root)
                if stage in ("uninstall", "unload"):
                    self.assertTrue(wire.request(startup)["ok"])
                    message = {"op": stage, "id": 2}
                else:
                    message = dict(startup, op=stage)
                reply = wire.request(message)
                self.assertIsNotNone(reply, "SystemExit must not stop the worker event loop")
                self.assertFalse(reply["ok"])
                self.assertEqual(reply["error"]["code"], "exception")
                self.assertEqual(reply["error"]["stage"], stage)
                self.assertIn("hook exit", reply["error"]["message"])
                if stage == "unload":
                    self.assertEqual(wire.proc.wait(timeout=5), 0)
                else:
                    self.assertEqual(wire.request({"op": "ping", "id": 3})["result"], {"pong": True})
                wire.close()


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux privilege setup")
class WorkerPrivilegeParityTests(unittest.TestCase):
    def test_failed_no_new_privs_is_reported(self):
        libc = types.SimpleNamespace(prctl=mock.Mock(side_effect=[0, -1]))
        with mock.patch("ctypes.CDLL", return_value=libc), \
                mock.patch("ctypes.get_errno", return_value=1), \
                mock.patch.object(fy_worker.os, "getppid", return_value=123):
            applied, reason = fy_worker.privileges()
        self.assertFalse(applied)
        self.assertIn("no_new_privs", reason)
        self.assertIn("permitted", reason)

    def test_privilege_setup_success_is_reported(self):
        libc = types.SimpleNamespace(prctl=mock.Mock(return_value=0))
        with mock.patch("ctypes.CDLL", return_value=libc), \
                mock.patch.object(fy_worker.os, "getppid", return_value=123):
            self.assertEqual(fy_worker.privileges(), (True, None))

    def test_parent_death_during_registration_exits_before_loading_backend(self):
        libc = types.SimpleNamespace(prctl=mock.Mock(return_value=0))
        with mock.patch("ctypes.CDLL", return_value=libc), \
                mock.patch.object(fy_worker.os, "getppid", side_effect=[123, 1]), \
                mock.patch.object(fy_worker, "leave", side_effect=SystemExit) as leave:
            with self.assertRaises(SystemExit):
                fy_worker.privileges()
        leave.assert_called_once_with(0)

    def test_no_new_privs_failure_prevents_backend_import_and_reports_protocol_error(self):
        root = harness.make_root()
        self.addCleanup(shutil.rmtree, root, True)
        plugin, _ = harness.make_plugin(root, "p", "ok")
        message = harness.startup_message(1, "p", plugin / "backend.py", root)
        script = "\n".join((
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "import fy_worker",
            "fy_worker.privileges = lambda: (False, 'no_new_privs unavailable: test denied')",
            "fy_worker.main()",
        ))
        result = subprocess.run([sys.executable, "-I", "-c", script, str(root)],
                                input=fy_proto.encode(message), capture_output=True,
                                cwd=root, timeout=5)
        self.assertEqual(result.returncode, 3)
        reply = fy_proto.decode(result.stdout)
        self.assertEqual(reply["re"], 0)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["stage"], "import")
        self.assertIn("no_new_privs", reply["error"]["message"])
        self.assertFalse((root / "hooks.log").exists())

    def test_negative_inherited_niceness_is_reduced_to_nineteen(self):
        script = "\n".join((
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "import fy_worker",
            "def nice(delta):",
            "    print('nice delta: %s' % delta, file=sys.stderr, flush=True)",
            "    return -5 if delta == 0 else 19",
            "fy_worker.os.nice = nice",
            "fy_worker.privileges = lambda: (False, 'test stops before event loop')",
            "fy_worker.main()",
        ))
        result = subprocess.run([sys.executable, "-I", "-c", script, str(harness.REPO)],
                                input=b"", capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr.decode().splitlines(), ["nice delta: 0", "nice delta: 24"])


class WorkerEventAccountingParityTests(unittest.TestCase):
    def setUp(self):
        self.worker = fy_worker.Worker(None, -1)
        self.messages = []
        self.worker.write = self.messages.append

        def capture(data):
            self.messages.append(fy_proto.decode(data))
            return True

        self.worker.write_data = capture

    def test_reported_drop_count_is_carried_once_on_next_accepted_event(self):
        fy_worker.Rpc(self.worker).event_dropped(3)
        self.assertTrue(self.worker.emit("first", {"x": 1}))
        self.assertEqual(self.messages[-1]["dropped"], 3)
        self.assertTrue(self.worker.emit("second"))
        self.assertNotIn("dropped", self.messages[-1])

    def test_invalid_drop_counts_cannot_reduce_or_inflate_accounting(self):
        for value in (True, False, -1, 0, 1.5, "2", None):
            self.worker.event_dropped(value)
        self.worker.emit("fine")
        self.assertNotIn("dropped", self.messages[-1])

    def test_drop_count_saturates_without_growing_wire_integer(self):
        self.worker.event_dropped(1 << 80)
        self.worker.event_dropped(1)
        self.worker.emit("fine")
        self.assertEqual(self.messages[-1]["dropped"], (1 << 31) - 1)

    def test_worker_validation_failure_is_counted_on_next_event(self):
        self.assertFalse(self.worker.emit("bad name"))
        self.assertTrue(self.worker.emit("fine"))
        self.assertEqual(self.messages[-1]["dropped"], 1)

    def test_rate_rejection_count_survives_window_reset(self):
        with mock.patch.object(fy_worker, "EMIT_RATE", 1), \
                mock.patch.object(fy_worker.time, "monotonic", side_effect=[10., 10., 11.]):
            self.assertTrue(self.worker.emit("first"))
            self.assertFalse(self.worker.emit("rejected"))
            self.assertTrue(self.worker.emit("next_window"))
        self.assertEqual([message["name"] for message in self.messages], ["first", "next_window"])
        self.assertEqual(self.messages[-1]["dropped"], 1)


if __name__ == "__main__":
    unittest.main()
