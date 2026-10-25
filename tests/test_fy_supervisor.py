import asyncio
import os
import shutil
import signal
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "a"))

import harness
import fy_proto
import fy_supervisor
from fy_supervisor import BackendError, EventHub, Supervisor

LINUX = unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux: process groups, SIGTERM semantics, /proc")

FAST_LIMITS = {
    "timeout": (10, 0.2, 60),
    "idle": (120, 0.2, 3600),
    "concurrency": (1, 1, 8),
    "memory_mb": (32, 16, 64),
    "startup_timeout": (10, 0.5, 60),
}


class SupCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = harness.make_root()
        self.host = harness.FakeHost(self.root)
        self.sup = Supervisor(self.host)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.root / "run")})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (("PING_GRACE", 0.5), ("TERM_WAIT", 1.0), ("KILL_WAIT", 1.0), ("UNLOAD_WAIT", 1.5), ("SLOT_WAIT", 0.6), ("HOOK_GRACE", 0.3)):
            patcher = mock.patch.object(fy_supervisor, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(fy_supervisor.LIMITS, FAST_LIMITS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addAsyncCleanup(self.sup.shutdown, 2.0)

    def reg(self, pid, fixture, **backend):
        manifest = {"backend": backend} if backend else {}
        d, m = harness.make_plugin(self.root, pid, fixture, manifest)
        self.sup.register(pid, d, m)
        return d, m

    def worker(self, pid):
        return self.sup._ws[pid]

    def state(self, pid):
        return self.sup.status()[pid]["state"]

    def data_dir(self, pid):
        return self.host.HOME / ".local" / "share" / "framey" / pid

    def hooks(self, pid):
        p = self.data_dir(pid) / "hooks.log"
        return p.read_text().splitlines() if p.exists() else []

    async def until(self, predicate, limit=8):
        end = time.monotonic() + limit
        while not predicate():
            if time.monotonic() > end:
                self.fail("condition not reached")
            await asyncio.sleep(0.02)


class LazyStartTests(SupCase):
    async def test_register_costs_nothing(self):
        calls = []
        sup = Supervisor(self.host, spawn=lambda *a: calls.append(a))
        d, m = harness.make_plugin(self.root, "a", "ok")
        sup.register("a", d, m)
        self.assertEqual(calls, [])
        self.assertEqual(sup._tasks, set())
        w = sup._ws["a"]
        self.assertEqual((w.state, w.proc, w.timer, w.os_pid), ("stopped", None, None, None))
        self.assertEqual(sup.status()["a"]["state"], "stopped")
        self.assertFalse((self.root / "home").exists())

    async def test_first_call_starts_one_process_second_reuses_it(self):
        self.reg("a", "ok")
        self.assertIsNone(self.sup.status()["a"]["os_pid"])
        first = await self.sup.call("a", "pid", None)
        self.assertEqual(self.state("a"), "ready")
        self.assertEqual(await self.sup.call("a", "pid", None), first)
        self.assertEqual(self.sup.status()["a"]["os_pid"], first)
        self.assertNotEqual(first, os.getpid())

    async def test_unregistered_and_missing_backend(self):
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("nope", "x", None)
        self.assertEqual(ctx.exception.code, "no_backend")
        d, m = harness.make_plugin(self.root, "b", "ok")
        (d / "backend.py").unlink()
        self.sup.register("b", d, m)
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("b", "x", None)
        self.assertEqual(ctx.exception.code, "no_backend")
        self.assertEqual(self.state("b"), "stopped")

    async def test_bad_plugin_id_rejected(self):
        for bad in ("../x", "A", "", "x" * 33, None, 5):
            with self.assertRaises(ValueError):
                self.sup.register(bad, self.root, {})

    async def test_spawn_injection_receives_contract_arguments(self):
        seen = []

        def spawn(argv, env, cwd):
            seen.append((argv, env, cwd))
            raise OSError("no process for you")

        sup = Supervisor(self.host, spawn=spawn)
        d, m = harness.make_plugin(self.root, "a", "ok")
        sup.register("a", d, m)
        with self.assertRaises(BackendError) as ctx:
            await sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "spawn")
        argv, env, cwd = seen[0]
        self.assertEqual(argv, [sys.executable, "-I", "-B", str(self.root / "fy_worker.py")])
        self.assertEqual(env["FRAMEY_PLUGIN"], "a")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(Path(cwd), self.data_dir("a"))
        self.assertEqual(sup.status()["a"]["state"], "crashed")
        with self.assertRaises(BackendError) as ctx:
            await sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "backoff")
        self.assertEqual(len(seen), 1)


class ProcessPolicyTests(SupCase):
    async def test_environment_allowlist(self):
        self.reg("a", "ok")
        with mock.patch.dict(os.environ, {"FRAMEY_SECRET_TEST": "s3cret", "GITHUB_TOKEN": "t"}):
            env = await self.sup.call("a", "env", None)
        allowed = {k.upper() for k in fy_supervisor.ENV_KEEP} | {"FRAMEY_PLUGIN", "PYTHONDONTWRITEBYTECODE"}
        extra = {k for k in env if k.upper() not in allowed}
        self.assertEqual(extra - {"__PYVENV_LAUNCHER__", "PYTHONIOENCODING", "PYTHONUTF8", "LC_CTYPE"}, set())
        self.assertNotIn("FRAMEY_SECRET_TEST", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["FRAMEY_PLUGIN"], "a")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    async def test_cwd_is_the_plugin_data_dir_and_stderr_goes_to_worker_err(self):
        self.reg("a", "noisy")
        logs = self.host.HOME / ".local" / "state" / "framey" / "a"
        logs.mkdir(parents=True)
        (logs / "worker.err").write_bytes(b"old\n" * 30000)
        await self.sup.call("a", "x", None)
        self.assertTrue(self.data_dir("a").is_dir())
        for _ in range(100):
            err = (logs / "worker.err").read_bytes()
            if b"import time noise" in err:
                break
            await asyncio.sleep(0.02)
        self.assertLessEqual(len(err), 65536)
        self.assertIn(b"import time noise", err)
        self.assertNotIn(b"old", err)

    async def test_an_oversized_worker_err_is_rotated_to_one_backup_and_a_small_one_is_appended(self):
        logs = self.root / "logs"
        logs.mkdir()
        err, backup = logs / "worker.err", logs / "worker.err.1"
        err.write_bytes(b"first\n" * 20000)
        backup.write_bytes(b"older backup\n")
        self.sup._errlog(logs, b"small\n")
        self.assertEqual((err.read_bytes(), backup.read_bytes()), (b"small\n", b"first\n" * 20000))
        self.sup._errlog(logs, b"more\n")
        self.assertEqual(err.read_bytes(), b"small\nmore\n")
        self.assertEqual(sorted(p.name for p in logs.iterdir()), ["worker.err", "worker.err.1"])
        for _ in range(20):
            self.sup._errlog(logs, b"x" * 4096)
        self.assertLessEqual(err.stat().st_size, fy_supervisor.ERR_MAX + 4096)
        self.assertLessEqual(backup.stat().st_size, fy_supervisor.ERR_MAX + 4096)
        self.assertEqual(sorted(p.name for p in logs.iterdir()), ["worker.err", "worker.err.1"])

    async def test_noise_does_not_corrupt_protocol(self):
        self.reg("a", "noisy")
        for _ in range(3):
            self.assertEqual(await self.sup.call("a", "x", None), {"ok": True})
        self.assertEqual(self.sup.status()["a"]["crashes"], 0)

    async def test_manifest_limits_are_clamped_and_hostile_types_ignored(self):
        lim = fy_supervisor._limits({"backend": {"timeout": True, "idle": "x", "concurrency": 99, "memory_mb": 1e9, "keepalive": "yes", "startup_timeout": float("nan")}})
        self.assertEqual((lim["timeout"], lim["idle"], lim["concurrency"], lim["memory_mb"], lim["keepalive"], lim["startup_timeout"]), (10, 120, 8, 64, False, 10))
        self.assertEqual(fy_supervisor._limits({})["memory_mb"], 32)
        with mock.patch.dict(fy_supervisor.LIMITS, {"timeout": (10, 1, 60), "idle": (120, 10, 3600), "startup_timeout": (10, 1, 60)}):
            lim = fy_supervisor._limits({"timeout": 500, "backend": {"idle": 1, "memory_mb": 1, "concurrency": 0, "keepalive": True}})
            self.assertEqual((lim["timeout"], lim["idle"], lim["memory_mb"], lim["concurrency"], lim["keepalive"]), (60, 10, 16, 1, True))
            lim = fy_supervisor._limits({"timeout": 5, "backend": {"timeout": 7}})
            self.assertEqual(lim["timeout"], 7)
            self.assertEqual(fy_supervisor._limits({"timeout": 5, "backend": "x"})["timeout"], 5)
            self.assertEqual(fy_supervisor._limits({})["timeout"], 10)

    async def test_status_shape(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        st = self.sup.status()["a"]
        self.assertEqual(set(st), {"state", "os_pid", "rss_kb", "calls", "crashes", "last_error", "last_used", "limits_applied", "limits_warning"})
        self.assertEqual((st["calls"], st["crashes"], st["last_error"]), (1, 0, None))
        self.assertEqual(st["last_used"], self.host.clock)
        self.assertIsInstance(st["limits_applied"], bool)
        self.assertEqual(st["limits_warning"] is None, st["limits_applied"])
        if sys.platform.startswith("linux"):
            self.assertGreater(st["rss_kb"], 1000)
        else:
            self.assertIsNone(st["rss_kb"])


class TimeoutTests(SupCase):
    async def test_slow_call_times_out_but_worker_answers_ping_and_survives(self):
        self.reg("a", "slow", timeout=0.3)
        await self.sup.call("a", "x", 0)
        pid = self.sup.status()["a"]["os_pid"]
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            await self.sup.call("a", "x", 5)
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(str(ctx.exception), "backend call timed out after 0.3 s; module not responding")
        self.assertIsInstance(ctx.exception, BackendError)
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertTrue(self.sup.stuck("a"))
        self.assertEqual(self.state("a"), "hung")
        await self.until(lambda: not self.sup.stuck("a"))
        self.assertEqual(self.state("a"), "ready")
        self.assertEqual(self.sup.status()["a"]["os_pid"], pid)
        self.assertEqual(self.sup.status()["a"]["crashes"], 0)
        self.assertEqual(await self.sup.call("a", "x", 0), {"slept": 0})

    async def test_hung_sync_worker_is_killed_within_a_bound_and_counts_one_crash(self):
        self.reg("a", "hang_sync", timeout=0.3)
        self.assertEqual(await self.sup.call("a", "quick", None), "quick")
        old = self.sup.status()["a"]["os_pid"]
        started = time.monotonic()
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "hang", None)
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertTrue(self.sup.stuck("a"))
        await self.until(lambda: self.state("a") == "crashed", 10)
        bound = fy_supervisor.PING_GRACE + fy_supervisor.TERM_WAIT + fy_supervisor.KILL_WAIT + 2.5
        self.assertLess(time.monotonic() - started, 0.3 + bound)
        st = self.sup.status()["a"]
        self.assertEqual((st["crashes"], st["os_pid"]), (1, None))
        self.assertIn("stopped responding", st["last_error"]["message"])
        self.assertFalse(self.sup.stuck("a"))
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "quick", None)
        self.assertEqual(ctx.exception.code, "backoff")
        self.host.clock += 1.1
        self.assertEqual(await self.sup.call("a", "quick", None), "quick")
        self.assertNotEqual(self.sup.status()["a"]["os_pid"], old)

    async def test_queued_calls_fail_when_a_hung_worker_is_killed(self):
        self.reg("a", "hang_sync", timeout=0.3)
        await self.sup.call("a", "quick", None)
        first = asyncio.ensure_future(self.sup.call("a", "hang", None))
        await asyncio.sleep(0.05)
        second = asyncio.ensure_future(self.sup.call("a", "quick", None))
        with self.assertRaises(BackendError):
            await first
        with self.assertRaises(BackendError) as ctx:
            await asyncio.wait_for(second, 12)
        self.assertEqual(ctx.exception.code, "exit")

    @LINUX
    async def test_sigterm_ignored_escalates_to_sigkill_in_bounded_time(self):
        self.reg("a", "term_ignore", timeout=0.3)
        started = time.monotonic()
        with self.assertRaises(BackendError):
            await self.sup.call("a", "x", None)
        await self.until(lambda: self.state("a") == "crashed", 10)
        elapsed = time.monotonic() - started
        self.assertGreater(elapsed, 0.3 + fy_supervisor.PING_GRACE + fy_supervisor.TERM_WAIT - 0.2)
        self.assertLess(elapsed, 0.3 + fy_supervisor.PING_GRACE + fy_supervisor.TERM_WAIT + fy_supervisor.KILL_WAIT + 2.5)

    @LINUX
    async def test_children_die_with_the_worker_process_group(self):
        self.reg("a", "forkbomb", timeout=0.3)
        child = await self.sup.call("a", "spawn", None)
        os.kill(child, 0)
        with self.assertRaises(BackendError):
            await self.sup.call("a", "hang", None)
        await self.until(lambda: self.state("a") == "crashed", 10)

        def gone():
            try:
                os.kill(child, 0)
                with open("/proc/%d/stat" % child) as f:
                    return f.read().split(")")[1].split()[0] == "Z"
            except OSError:
                return True

        await self.until(gone, 5)


class CrashTests(SupCase):
    async def test_crash_exit_code_and_backoff_then_failed_after_three(self):
        self.reg("a", "crash")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "exit")
        self.assertIn("code 7", str(ctx.exception))
        self.assertEqual(self.state("a"), "crashed")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "backoff")
        self.host.clock += 0.9
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "backoff")
        self.host.clock += 0.3
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "exit")
        self.host.clock += 4.0
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "backoff")
        self.host.clock += 1.5
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "exit")
        self.assertEqual(self.state("a"), "failed")
        self.assertEqual(self.sup.status()["a"]["crashes"], 3)
        self.host.clock += 10000
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "failed")

    async def test_second_backoff_step_is_five_seconds(self):
        self.reg("a", "crash")
        w = self.worker("a")
        w.state = "crashed"
        w.crashes.extend([self.host.clock - 10, self.host.clock - 1])
        w.last_error = {"code": "crash", "message": "m", "stage": None}
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "backoff")
        self.assertIn("4 s", str(ctx.exception))

    async def test_old_crashes_leave_the_window(self):
        self.reg("a", "crash")
        for _ in range(4):
            with self.assertRaises(BackendError) as ctx:
                await self.sup.call("a", "x", None)
            self.assertEqual(ctx.exception.code, "exit")
            self.host.clock += fy_supervisor.CRASH_WINDOW + 1
        self.assertEqual(self.state("a"), "crashed")
        self.assertEqual(self.sup.status()["a"]["crashes"], 4)

    async def test_restart_clears_failed_and_backoff(self):
        self.reg("a", "crash")
        w = self.worker("a")
        w.state = "failed"
        w.crashes.extend([self.host.clock] * 3)
        w.last_error = {"code": "crash", "message": "m", "stage": None}
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "failed")
        await self.sup.restart("a")
        self.assertEqual(self.state("a"), "stopped")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "exit")

    async def test_sys_exit_in_call_is_not_a_crash(self):
        self.reg("a", "exit")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual((ctx.exception.code, str(ctx.exception)), ("exit", "backend called exit"))
        self.assertEqual(self.state("a"), "ready")
        self.assertEqual(self.sup.status()["a"]["crashes"], 0)

    async def test_exception_in_call_is_not_a_failure(self):
        self.reg("a", "ok")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "raise", None)
        self.assertEqual((ctx.exception.code, str(ctx.exception)), ("exception", "bad thing"))
        self.assertIn("ValueError", ctx.exception.trace)
        self.assertEqual(self.state("a"), "ready")


class RogueWorkerTests(SupCase):
    def rogue(self, body):
        code = "import sys, time, json" + chr(10) + "sys.stdin.buffer.readline()" + chr(10) + body

        async def spawn(argv, env, cwd):
            return await asyncio.create_subprocess_exec(sys.executable, "-c", code, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=fy_proto.LINE_MAX + 2)

        sup = Supervisor(self.host, spawn=spawn)
        d, m = harness.make_plugin(self.root, "r", "ok")
        sup.register("r", d, m)
        self.addAsyncCleanup(sup.shutdown, 1.0)
        return sup

    async def crashed_with(self, body, text):
        sup = self.rogue(body)
        w = sup._ws["r"]
        with self.assertRaises(BackendError):
            await sup.call("r", "x", None)
        await self.until(lambda: w.proc is None, 10)
        self.assertIn(text, w.last_error["message"])
        self.assertEqual((w.state, w.crash_total), ("crashed", 1))

    async def test_bad_json_line_is_a_crash(self):
        await self.crashed_with("sys.stdout.write('garbage' + chr(10)); sys.stdout.flush(); time.sleep(60)", "protocol violation")

    async def test_non_object_line_is_a_crash(self):
        await self.crashed_with("sys.stdout.write('[1]' + chr(10)); sys.stdout.flush(); time.sleep(60)", "protocol violation")

    async def test_oversize_line_from_worker_is_a_crash(self):
        await self.crashed_with("sys.stdout.write('x' * 1100000 + chr(10)); sys.stdout.flush(); time.sleep(60)", "oversize")

    async def test_unknown_message_is_a_crash(self):
        await self.crashed_with("sys.stdout.write(json.dumps({'zzz': 1}) + chr(10)); sys.stdout.flush(); time.sleep(60)", "unknown message")

    async def test_exit_during_startup_is_a_crash(self):
        await self.crashed_with("sys.exit(5)", "code 5")

    async def test_worker_reported_final_error_is_kept_as_the_reason(self):
        await self.crashed_with("sys.stdout.write(json.dumps({'re': 0, 'ok': False, 'error': {'code': 'too_large', 'message': 'line too large'}}) + chr(10)); sys.stdout.flush(); sys.exit(3)", "line too large")

    async def test_silent_worker_hits_the_outside_startup_timeout(self):
        sup = self.rogue("time.sleep(60)")
        w = sup._ws["r"]
        w.lim["startup_timeout"] = 0.4
        started = time.monotonic()
        with self.assertRaises(BackendError) as ctx:
            await sup.call("r", "x", None)
        self.assertEqual((ctx.exception.code, ctx.exception.stage), ("timeout", "startup"))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(w.state, "failed")
        self.assertIsNone(w.proc)
        self.assertEqual(w.crash_total, 0)


class ResultCapTests(SupCase):
    async def test_oversize_result_and_unserialisable_result(self):
        self.reg("a", "bigresult")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "s", fy_proto.PAYLOAD_MAX)
        self.assertEqual(ctx.exception.code, "too_large")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "obj", None)
        self.assertEqual(ctx.exception.code, "bad_result")
        self.assertEqual(len(await self.sup.call("a", "s", 1000)), 1000)
        self.assertEqual(self.state("a"), "ready")

    async def test_oversize_argument_is_refused_before_any_process(self):
        self.reg("a", "bigresult")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "s", "x" * fy_proto.PAYLOAD_MAX)
        self.assertEqual(ctx.exception.code, "too_large")
        self.assertEqual(self.state("a"), "stopped")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "s", object())
        self.assertEqual(ctx.exception.code, "bad_arg")
        for method in ("", 5, "m" * 200):
            with self.assertRaises(BackendError) as ctx:
                await self.sup.call("a", method, None)
            self.assertEqual(ctx.exception.code, "bad_method")
        self.assertEqual(self.state("a"), "stopped")

    async def test_argument_just_under_the_cap_round_trips(self):
        self.reg("a", "ok")
        arg = "y" * (fy_proto.PAYLOAD_MAX - 200)
        self.assertEqual(await self.sup.call("a", "echo", arg), {"echo": arg})


class StageTests(SupCase):
    async def test_startup_failure_marks_failed_with_stage_and_stops_process(self):
        self.reg("a", "startup_fail")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.stage, "startup")
        self.assertIn("startup boom", str(ctx.exception))
        st = self.sup.status()["a"]
        self.assertEqual((st["state"], st["os_pid"], st["crashes"]), ("failed", None, 0))
        self.assertEqual(st["last_error"]["stage"], "startup")
        self.assertEqual(self.host.states, {})
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual((ctx.exception.code, ctx.exception.stage), ("failed", "startup"))

    async def test_startup_timeout_is_enforced_and_fails_the_module(self):
        self.reg("a", "startup_hang", startup_timeout=0.5)
        started = time.monotonic()
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual((ctx.exception.code, ctx.exception.stage), ("timeout", "startup"))
        self.assertEqual(self.state("a"), "failed")
        self.assertIsNone(self.sup.status()["a"]["os_pid"])

    async def test_import_failure_has_import_stage(self):
        self.reg("a", "import_fail")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.stage, "import")
        self.assertEqual(self.state("a"), "failed")

    async def test_fresh_install_has_no_migration_and_records_state(self):
        self.reg("a", "ok", )
        await self.sup.call("a", "echo", 1)
        self.assertEqual([l for l in self.hooks("a") if l.startswith("migrate")], [])
        self.assertEqual(self.host.snapshots, [])
        self.assertEqual(self.host.states["a"]["ver"], "1.0.0")
        self.assertIsNone(self.host.states["a"]["data_v"])

    async def test_migration_due_on_version_or_data_version_change(self):
        d, m = self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        await self.sup.stop("a")
        m.update(version="1.1.0", data_version=2)
        self.sup.register("a", d, m)
        await self.sup.call("a", "echo", 1)
        lines = self.hooks("a")
        mig = [i for i, l in enumerate(lines) if l.startswith("migrate")]
        self.assertEqual([lines[i] for i in mig], ["migrate 1.0.0 1.1.0 None 2"])
        self.assertTrue(lines[mig[0] + 1].startswith("startup"))
        self.assertEqual(self.host.snapshots, ["a"])
        self.assertEqual(self.host.dropped, ["a"])
        self.assertEqual((self.host.states["a"]["ver"], self.host.states["a"]["data_v"]), ("1.1.0", 2))
        await self.sup.stop("a")
        self.sup.register("a", d, m)
        await self.sup.call("a", "echo", 1)
        self.assertEqual(len([l for l in self.hooks("a") if l.startswith("migrate")]), 1)
        m.update(data_version=3)
        await self.sup.stop("a")
        self.sup.register("a", d, m)
        await self.sup.call("a", "echo", 1)
        self.assertIn("migrate 1.1.0 1.1.0 2 3", self.hooks("a"))

    async def test_migrate_failure_keeps_record_and_snapshot_and_fails(self):
        self.host.states["a"] = {"ver": "0.9.0", "data_v": 1}
        self.reg("a", "migrate_fail")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.stage, "migrate")
        self.assertIn("migrate boom", str(ctx.exception))
        self.assertEqual(self.host.states["a"], {"ver": "0.9.0", "data_v": 1})
        self.assertEqual(self.host.snapshots, ["a"])
        self.assertEqual(self.host.dropped, [])
        self.assertEqual(self.host.restored, ["a"])
        st = self.sup.status()["a"]
        self.assertEqual((st["state"], st["os_pid"], st["last_error"]["stage"]), ("failed", None, "migrate"))

    async def test_failed_restore_is_logged_and_the_migrate_error_still_surfaces(self):
        self.host.states["a"] = {"ver": "0.9.0", "data_v": 1}
        self.host.restore_snapshot = mock.Mock(side_effect=OSError("read only"))
        self.reg("a", "migrate_fail")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.stage, "migrate")
        self.assertTrue(any("could not restore" in line for line in self.host.logged))

    async def test_startup_failure_without_a_migration_restores_nothing(self):
        self.reg("a", "startup_fail")
        with self.assertRaises(BackendError):
            await self.sup.call("a", "x", None)
        self.assertEqual((self.host.snapshots, self.host.restored), ([], []))

    async def test_snapshot_failure_blocks_the_migration(self):
        self.host.states["a"] = {"ver": "0.9.0"}
        self.host.snapshot_settings = mock.Mock(side_effect=OSError("disk full"))
        self.reg("a", "ok")
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "echo", 1)
        self.assertEqual(ctx.exception.stage, "migrate")
        self.assertEqual([l for l in self.hooks("a") if l.startswith("migrate")], [])
        self.assertEqual(self.host.restored, [])

    async def test_arity_hooks_legacy_unload_without_argument(self):
        self.reg("a", "legacy")
        self.assertEqual(await self.sup.call("a", "x", None), {"legacy": "x"})
        await self.sup.stop("a", "update")
        self.assertEqual(self.hooks("a"), ["startup noarg", "unload noarg"])


class StopTests(SupCase):
    async def test_stop_runs_unload_with_reason_and_leaves_registration(self):
        self.reg("a", "ok")
        self.reg("b", "ok")
        await self.sup.call("a", "echo", 1)
        keep = await self.sup.call("b", "pid", None)
        await self.sup.stop("a", "disable")
        self.assertIn("unload disable", self.hooks("a"))
        self.assertEqual((self.state("a"), self.sup.status()["a"]["os_pid"]), ("stopped", None))
        self.assertEqual(await self.sup.call("b", "pid", None), keep)
        self.assertEqual(await self.sup.call("a", "echo", 2), {"echo": 2})

    async def test_unknown_reason_becomes_reload(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        await self.sup.stop("a", "banana")
        self.assertIn("unload reload", self.hooks("a"))

    async def test_unload_hang_is_killed_in_bounded_time(self):
        self.reg("a", "unload_hang")
        await self.sup.call("a", "x", None)
        started = time.monotonic()
        await self.sup.stop("a")
        self.assertLess(time.monotonic() - started, fy_supervisor.UNLOAD_WAIT + fy_supervisor.EXIT_WAIT + fy_supervisor.TERM_WAIT + fy_supervisor.KILL_WAIT + 3)
        self.assertEqual((self.state("a"), self.sup.status()["a"]["os_pid"], self.sup.status()["a"]["crashes"]), ("stopped", None, 0))

    async def test_stop_of_hung_worker_kills_it(self):
        self.reg("a", "hang_sync", timeout=0.3)
        await self.sup.call("a", "quick", None)
        with self.assertRaises(BackendError):
            await self.sup.call("a", "hang", None)
        await self.sup.stop("a")
        self.assertEqual(self.state("a"), "stopped")
        self.assertEqual(self.sup.status()["a"]["crashes"], 0)

    async def test_restart_rereads_manifest_and_keeps_others(self):
        d, m = self.reg("a", "slow", timeout=0.3)
        self.reg("b", "ok")
        keep = await self.sup.call("b", "pid", None)
        await self.sup.call("a", "x", 0)
        m["backend"] = {"timeout": 2}
        (d / "plugin.json").write_text(__import__("json").dumps(m))
        await self.sup.restart("a")
        self.assertEqual(self.worker("a").lim["timeout"], 2)
        self.assertEqual(self.state("a"), "stopped")
        self.assertEqual(await self.sup.call("b", "pid", None), keep)

    async def test_forget_removes_and_stops(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        os_pid = self.sup.status()["a"]["os_pid"]
        w = self.worker("a")
        self.sup.forget("a")
        self.assertNotIn("a", self.sup.status())
        await asyncio.wait_for(w.exited, 8)
        self.assertIn("unload uninstall", self.hooks("a"))
        with self.assertRaises(BackendError):
            await self.sup.call("a", "x", None)


class UninstallTests(SupCase):
    async def test_hook_before_unload_and_purge_flag(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        out = await self.sup.uninstall("a", purge=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["purged"])
        self.assertEqual(out["hook"], "ran")
        lines = self.hooks("a")
        self.assertEqual(lines[-2:], ["uninstall purge=True", "unload uninstall"])
        self.assertEqual(self.host.purged, ["a"])
        self.assertEqual(self.state("a"), "stopped")

    async def test_no_purge_keeps_data(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        out = await self.sup.uninstall("a")
        self.assertEqual((out["ok"], out["purged"]), (True, False))
        self.assertEqual(self.host.purged, [])
        self.assertIn("uninstall purge=False", self.hooks("a"))

    async def test_uninstall_starts_a_stopped_worker_to_run_the_hook(self):
        self.reg("a", "ok")
        out = await self.sup.uninstall("a", purge=False)
        self.assertEqual(out["hook"], "ran")
        self.assertIn("uninstall purge=False", self.hooks("a"))

    async def test_broken_backend_does_not_block_removal(self):
        self.reg("a", "startup_fail")
        out = await self.sup.uninstall("a", purge=True)
        self.assertEqual((out["ok"], out["purged"], out["hook"]), (True, True, "skipped"))
        self.assertIn("startup boom", out["error"])

    async def test_unregistered_plugin_can_still_be_purged(self):
        out = await self.sup.uninstall("ghost", purge=True)
        self.assertEqual((out["ok"], out["purged"]), (True, True))
        self.assertEqual(self.host.purged, ["ghost"])

    async def test_purge_failure_is_reported_not_raised(self):
        self.host.purge = lambda pid: {"removed": [], "failed": ["data"]}
        out = await self.sup.uninstall("ghost", purge=True)
        self.assertFalse(out["purged"])


class CapTests(SupCase):
    def live(self):
        return sorted(p for p, s in self.sup.status().items() if s["state"] == "ready")

    async def test_lru_eviction_at_cap(self):
        self.host.prefs["workers_max"] = 2
        for pid in "abc":
            self.reg(pid, "ok")
        await self.sup.call("a", "echo", 1)
        await self.sup.call("b", "echo", 1)
        await self.sup.call("a", "echo", 2)
        await self.sup.call("c", "echo", 1)
        self.assertEqual(self.live(), ["a", "c"])
        self.assertIn("unload lru", self.hooks("b"))
        self.assertNotIn("unload lru", self.hooks("a"))
        await self.sup.call("b", "echo", 1)
        self.assertEqual(self.live(), ["b", "c"])
        self.assertIn("unload lru", self.hooks("a"))

    async def test_keepalive_is_evicted_last(self):
        self.host.prefs["workers_max"] = 2
        self.reg("a", "ok", keepalive=True)
        self.reg("b", "ok")
        self.reg("c", "ok")
        await self.sup.call("a", "echo", 1)
        await self.sup.call("b", "echo", 1)
        await self.sup.call("c", "echo", 1)
        self.assertEqual(self.live(), ["a", "c"])

    async def test_keepalive_is_evicted_when_nothing_else_is_idle(self):
        self.host.prefs["workers_max"] = 1
        self.reg("a", "ok", keepalive=True)
        self.reg("b", "ok")
        await self.sup.call("a", "echo", 1)
        await self.sup.call("b", "echo", 1)
        self.assertEqual(self.live(), ["b"])

    async def test_memory_sum_evicts_least_recent_idle_workers(self):
        for pid in "abc":
            self.reg(pid, "ok")
            await self.sup.call(pid, "echo", 1)
        self.assertEqual(self.sup._memory(), 96)
        self.reg("d", "ok", memory_mb=64)
        await self.sup.call("d", "echo", 1)
        self.assertEqual(self.live(), ["c", "d"])
        self.assertIn("unload lru", self.hooks("a"))
        self.assertIn("unload lru", self.hooks("b"))
        self.assertEqual(self.sup._memory(), 96)

    async def test_memory_sum_refuses_when_running_workers_cannot_be_evicted(self):
        self.reg("a", "slow", timeout=30, memory_mb=64)
        self.reg("b", "ok", memory_mb=64)
        running = asyncio.ensure_future(self.sup.call("a", "x", 3))
        await asyncio.sleep(0.8)
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("b", "echo", 1)
        self.assertEqual(ctx.exception.code, "memory")
        self.assertIn("100 MB", ctx.exception.message)
        self.assertEqual(self.state("a"), "ready")
        running.cancel()

    async def test_declared_memory_above_64_is_clamped_and_logged(self):
        self.reg("a", "ok", memory_mb=500)
        self.assertEqual(self.worker("a").lim["memory_mb"], 64)
        self.assertTrue(any("memory_mb 500 clamped to 64" in line for line in self.host.logged))

    async def test_cap_values(self):
        for value, expect in ((None, 3), (0, 3), (5, 3), (True, 3), ("2", 3), (1, 1), (3, 3), (4, 3)):
            self.host.prefs.clear()
            if value is not None:
                self.host.prefs["workers_max"] = value
            self.assertEqual(self.sup._cap(), expect, value)

    async def test_hard_cap_three_never_exceeded(self):
        self.host.prefs["workers_max"] = 4
        for pid in "abcde":
            self.reg(pid, "ok")
        for pid in "abcde":
            await self.sup.call(pid, "echo", 1)
            self.assertLessEqual(len(self.live()), 3)
        self.assertEqual(len(self.live()), 3)

    async def test_busy_workers_when_nobody_can_be_evicted(self):
        self.host.prefs["workers_max"] = 1
        self.reg("a", "slow", timeout=30)
        self.reg("b", "ok")
        running = asyncio.ensure_future(self.sup.call("a", "x", 3))
        await asyncio.sleep(0.8)
        started = time.monotonic()
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("b", "echo", 1)
        self.assertEqual(ctx.exception.code, "busy_workers")
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(self.state("a"), "ready")
        running.cancel()

    async def test_waits_for_a_slot_that_frees_up(self):
        self.host.prefs["workers_max"] = 1
        self.reg("a", "slow", timeout=30)
        self.reg("b", "ok")
        with mock.patch.object(fy_supervisor, "SLOT_WAIT", 8.0):
            running = asyncio.ensure_future(self.sup.call("a", "x", 1.0))
            await asyncio.sleep(0.8)
            self.assertEqual(await self.sup.call("b", "echo", 1), {"echo": 1})
            await running
        self.assertEqual(self.live(), ["b"])

    async def test_queue_cap_is_sixteen_then_busy(self):
        self.reg("a", "slow", timeout=30)
        await self.sup.call("a", "x", 0)
        calls = [asyncio.ensure_future(self.sup.call("a", "x", 0.05)) for _ in range(18)]
        results = await asyncio.gather(*calls, return_exceptions=True)
        busy = [r for r in results if isinstance(r, BackendError) and r.code == "busy"]
        done = [r for r in results if isinstance(r, dict)]
        self.assertEqual((len(busy), len(done)), (1, 17))

    async def test_concurrency_two_runs_calls_in_parallel(self):
        self.reg("a", "slow", timeout=30, concurrency=2)
        await self.sup.call("a", "x", 0)
        started = time.monotonic()
        await asyncio.gather(self.sup.call("a", "x", 0.8), self.sup.call("a", "x", 0.8))
        self.assertLess(time.monotonic() - started, 1.5)

    async def test_concurrency_one_serialises(self):
        self.reg("a", "slow", timeout=30)
        await self.sup.call("a", "x", 0)
        started = time.monotonic()
        await asyncio.gather(self.sup.call("a", "x", 0.4), self.sup.call("a", "x", 0.4))
        self.assertGreaterEqual(time.monotonic() - started, 0.8)

    async def test_cancelled_waiter_does_not_leak_a_slot(self):
        self.reg("a", "slow", timeout=30)
        await self.sup.call("a", "x", 0)
        first = asyncio.ensure_future(self.sup.call("a", "x", 0.5))
        await asyncio.sleep(0.1)
        second = asyncio.ensure_future(self.sup.call("a", "x", 0))
        await asyncio.sleep(0.1)
        second.cancel()
        await first
        self.assertEqual(self.worker("a").inflight, 0)
        self.assertEqual(len(self.worker("a").waiters), 0)
        self.assertEqual(await self.sup.call("a", "x", 0), {"slept": 0})


class IdleTests(SupCase):
    async def test_idle_stop_with_unload_reason_and_restart_on_next_call(self):
        self.reg("a", "ok", idle=0.4)
        await self.sup.call("a", "echo", 1)
        w = self.worker("a")
        self.assertIsNotNone(w.timer)
        await self.until(lambda: self.state("a") == "stopped", 6)
        self.assertIn("unload idle", self.hooks("a"))
        self.assertIsNone(w.timer)
        self.assertEqual(await self.sup.call("a", "echo", 2), {"echo": 2})

    async def test_timer_is_cancelled_while_a_call_runs_and_rearmed_after(self):
        self.reg("a", "slow", idle=0.5, timeout=30)
        await self.sup.call("a", "x", 0)
        w = self.worker("a")
        long_call = asyncio.ensure_future(self.sup.call("a", "x", 1.2))
        await asyncio.sleep(0.1)
        self.assertIsNone(w.timer)
        await long_call
        self.assertEqual(self.state("a"), "ready")
        self.assertIsNotNone(w.timer)

    async def test_keepalive_never_arms_a_timer(self):
        self.reg("a", "ok", idle=0.3, keepalive=True)
        await self.sup.call("a", "echo", 1)
        self.assertIsNone(self.worker("a").timer)
        await asyncio.sleep(0.8)
        self.assertEqual(self.state("a"), "ready")

    async def test_no_timer_without_a_worker(self):
        self.reg("a", "ok", idle=0.3)
        self.assertIsNone(self.worker("a").timer)
        self.assertEqual(self.sup._tasks, set())

    async def test_idle_stop_uses_one_call_later_per_worker(self):
        scheduled = []
        real = self.host.loop

        class Loop:
            def __getattr__(self, name):
                return getattr(real(), name)

            def call_later(self, delay, cb, *args):
                scheduled.append(delay)
                return real().call_later(delay, cb, *args)

        self.host.loop = Loop
        self.reg("a", "ok", idle=0.5)
        await self.sup.call("a", "echo", 1)
        await self.sup.call("a", "echo", 2)
        await self.sup.call("a", "echo", 3)
        self.assertEqual(scheduled, [0.5, 0.5, 0.5])
        self.assertIsNotNone(self.worker("a").timer)


class ShutdownTests(SupCase):
    async def test_shutdown_is_bounded_even_with_a_hanging_unload(self):
        self.reg("a", "ok")
        self.reg("b", "unload_hang")
        self.reg("c", "unload_hang")
        for pid in "abc":
            await self.sup.call(pid, "x", None)
        started = time.monotonic()
        await self.sup.shutdown(total=0.6)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.6 + 1.0 + 1.0 + 1.0)
        self.assertEqual({s["os_pid"] for s in self.sup.status().values()}, {None})
        self.assertIn("unload shutdown", self.hooks("a"))
        with self.assertRaises(BackendError) as ctx:
            await self.sup.call("a", "x", None)
        self.assertEqual(ctx.exception.code, "shutdown")

    async def test_shutdown_with_nothing_running_is_instant(self):
        self.reg("a", "ok")
        started = time.monotonic()
        await self.sup.shutdown()
        self.assertLess(time.monotonic() - started, 0.2)

    async def test_shutdown_fails_queued_calls(self):
        self.reg("a", "slow", timeout=30)
        await self.sup.call("a", "x", 0)
        running = asyncio.ensure_future(self.sup.call("a", "x", 30))
        await asyncio.sleep(0.1)
        queued = asyncio.ensure_future(self.sup.call("a", "x", 0))
        await asyncio.sleep(0.1)
        await self.sup.shutdown(total=1.0)
        with self.assertRaises(BackendError):
            await queued
        with self.assertRaises(BackendError):
            await running


class EventTests(SupCase):
    async def test_backend_emits_reach_the_hub_with_caps(self):
        self.reg("a", "emit_flood")
        self.assertEqual(await self.sup.call("a", "bad", None), [False, False, True])
        got = self.sup.events.take(10)
        self.assertEqual([(e["plugin"], e["name"], e["payload"]) for e in got], [("a", "fine", {"a": 1})])
        accepted = await self.sup.call("a", "flood", 500)
        self.assertLessEqual(accepted, 100)
        total = 0
        while True:
            batch = self.sup.events.take(10)
            if not batch:
                break
            total += len(batch)
        self.assertLessEqual(total, fy_supervisor.EVENT_QUEUE)
        self.assertGreater(total, 0)

    async def test_notify_fires_once_when_the_queue_becomes_non_empty(self):
        self.reg("a", "emit_flood")
        await self.sup.call("a", "flood", 5)
        self.assertEqual(self.host.ready_signals, 1)
        self.sup.events.take(10)
        await self.sup.call("a", "flood", 5)
        self.assertEqual(self.host.ready_signals, 2)


class HubTests(unittest.TestCase):
    def hub(self):
        self.clock = [0.0]
        self.signals = []
        return EventHub(lambda: self.signals.append(1), clock=lambda: self.clock[0])

    def test_put_and_take_oldest_first_across_plugins(self):
        h = self.hub()
        for i in range(3):
            h.put("a", "e", i)
            h.put("b", "e", 10 + i)
        got = h.take(4)
        self.assertEqual([(e["plugin"], e["payload"]) for e in got], [("a", 0), ("b", 10), ("a", 1), ("b", 11)])
        self.assertEqual(len(h.take(10)), 2)
        self.assertEqual(h.take(), [])

    def test_queue_cap_drops_oldest_and_counts_once(self):
        h = self.hub()
        for i in range(100):
            self.clock[0] += 0.1
            self.assertTrue(h.put("a", "e", i))
        got = h.take(100)
        self.assertEqual(len(got), 64)
        self.assertEqual([e["payload"] for e in got], list(range(36, 100)))
        self.assertEqual(got[0]["dropped"], 36)
        self.assertTrue(all(e["dropped"] == 0 for e in got[1:]))
        h.put("a", "e", 1)
        self.assertEqual(h.take()[0]["dropped"], 0)

    def test_rate_cap_fifty_per_second_per_plugin(self):
        h = self.hub()
        results = [h.put("a", "e", i) for i in range(80)]
        self.assertEqual(results.count(True), 50)
        self.assertTrue(h.put("b", "e", 1))
        self.clock[0] += 1.0
        self.assertTrue(h.put("a", "e", 1))
        first = h.take(1)[0]
        self.assertEqual(first["dropped"], 30)

    def test_invalid_names_and_payloads_are_dropped_and_counted(self):
        h = self.hub()
        self.assertFalse(h.put("a", "Bad Name", 1))
        self.assertFalse(h.put("a", "ok", "x" * fy_proto.EVENT_MAX))
        self.assertFalse(h.put("a", "ok", object()))
        self.assertEqual(h.take(), [])
        h.put("a", "ok", 1)
        self.assertEqual(h.take()[0]["dropped"], 3)

    def test_notify_only_when_going_from_empty_to_non_empty(self):
        h = self.hub()
        h.put("a", "e", 1)
        h.put("a", "e", 2)
        self.assertEqual(len(self.signals), 1)
        h.take(10)
        h.put("b", "e", 1)
        self.assertEqual(len(self.signals), 2)

    def test_clear_one_and_all(self):
        h = self.hub()
        h.put("a", "e", 1)
        h.put("b", "e", 2)
        h.clear("a")
        self.assertEqual([e["plugin"] for e in h.take(10)], ["b"])
        h.put("a", "e", 1)
        h.clear()
        self.assertEqual(h.take(), [])
        h.put("a", "e", 1)
        self.assertEqual(len(self.signals), 3)

    def test_take_limit(self):
        h = self.hub()
        for i in range(30):
            h.put("a", "e", i)
        self.assertEqual(len(h.take()), 10)
        self.assertEqual(len(h.take(0)), 0)
        self.assertEqual(len(h.take(5)), 5)


class HostCallTests(SupCase):
    async def test_fetch_routed_through_hostcalls_with_plugin_id(self):
        seen = []

        async def fetch(pid, arg):
            seen.append((pid, arg))
            return {"status": 200, "text": "hi"}

        self.host.hostcalls["fetch"] = fetch
        self.reg("a", "host_fetch")
        self.assertEqual(await self.sup.call("a", "x", "https://example.test/"), {"status": 200, "text": "hi"})
        self.assertEqual(seen, [("a", {"url": "https://example.test/"})])

    async def test_sync_hostcall_and_error_codes(self):
        class Boom(Exception):
            code = "blocked_address"
            message = "that address is not allowed"

        def fetch(pid, arg):
            raise Boom()

        self.host.hostcalls["fetch"] = fetch
        self.reg("a", "host_fetch")
        out = await self.sup.call("a", "x", "http://127.0.0.1:8080/")
        self.assertEqual(out, {"failed": "blocked_address", "message": "that address is not allowed"})

    async def test_unknown_and_unavailable_host_calls(self):
        self.reg("a", "host_fetch")
        self.assertEqual(await self.sup.call("a", "raw", "nope"), {"failed": "bad_op"})
        self.assertEqual(await self.sup.call("a", "raw", "files"), {"failed": "unavailable"})

    async def test_oversize_host_result_is_an_error_not_a_crash(self):
        async def fetch(pid, arg):
            return "z" * fy_proto.PAYLOAD_MAX

        self.host.hostcalls["fetch"] = fetch
        self.reg("a", "host_fetch")
        self.assertEqual(await self.sup.call("a", "raw", "fetch"), {"failed": "too_large"})
        self.assertEqual(self.state("a"), "ready")

    async def test_at_most_four_outstanding_host_calls_fifth_is_busy(self):
        gate = asyncio.Event()
        started = []

        async def fetch(pid, arg):
            started.append(arg["i"])
            await gate.wait()
            return {"i": arg["i"]}

        self.host.hostcalls["fetch"] = fetch
        self.reg("a", "host_fetch", timeout=20)
        call = asyncio.ensure_future(self.sup.call("a", "many", None))
        await self.until(lambda: len(started) >= 4)
        await asyncio.sleep(0.3)
        self.assertEqual(len(started), 4)
        gate.set()
        out = await call
        self.assertEqual(sum(1 for r in out if r == "err:busy"), 2)
        self.assertEqual(sorted(r["i"] for r in out if isinstance(r, dict)), sorted(started))

    async def test_host_call_timeout_is_bounded(self):
        async def fetch(pid, arg):
            await asyncio.sleep(30)

        self.host.hostcalls["fetch"] = fetch
        self.reg("a", "host_fetch", timeout=20)
        with mock.patch.object(fy_supervisor, "HOST_TIMEOUT", 0.3):
            self.assertEqual(await self.sup.call("a", "raw", "fetch"), {"failed": "timeout"})


WEDGE = """
import sys, json, subprocess
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.DEVNULL)
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    m = json.loads(line)
    sys.stdout.write(json.dumps({"re": m["id"], "ok": True, "result": {"pid": child.pid}}) + chr(10))
    sys.stdout.flush()
    if m["op"] == "unload":
        break
"""


def kill_pid(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


class LifecycleFixTests(SupCase):
    def gated(self):
        gate, procs = asyncio.Event(), []

        async def spawn(argv, env, cwd):
            await gate.wait()
            proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=fy_proto.LINE_MAX + 2)
            procs.append(proc)
            return proc

        sup = Supervisor(self.host, spawn=spawn)
        d, m = harness.make_plugin(self.root, "r", "ok")
        sup.register("r", d, m)
        self.addAsyncCleanup(sup.shutdown, 1.0)
        self.addCleanup(lambda: [p.kill() for p in procs if p.returncode is None])
        return sup, d, m, gate, procs

    async def test_a_child_holding_the_pipe_does_not_wedge_the_module(self):
        async def spawn(argv, env, cwd):
            return await asyncio.create_subprocess_exec(sys.executable, "-c", WEDGE, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=fy_proto.LINE_MAX + 2)

        sup = Supervisor(self.host, spawn=spawn)
        d, m = harness.make_plugin(self.root, "r", "ok")
        sup.register("r", d, m)
        self.addAsyncCleanup(sup.shutdown, 1.0)
        child = (await sup.call("r", "x", None))["pid"]
        self.addCleanup(kill_pid, child)
        w = sup._ws["r"]
        started = time.monotonic()
        with mock.patch.object(fy_supervisor, "EXIT_WAIT", 0.4):
            await asyncio.wait_for(sup.stop("r"), 8)
            await self.until(lambda: w.proc is None, 5)
        self.assertEqual(w.state, "stopped")
        self.assertLess(time.monotonic() - started, 4)

    async def test_group_is_swept_after_a_normal_exit(self):
        calls = []
        with mock.patch.object(os, "killpg", lambda pg, sig: calls.append((pg, sig)), create=True), mock.patch.object(fy_supervisor.signal, "SIGKILL", 9, create=True):
            self.reg("a", "ok")
            await self.sup.call("a", "echo", 1)
            pid = self.sup.status()["a"]["os_pid"]
            await self.sup.stop("a")
            await self.until(lambda: (pid, 9) in calls)

    @LINUX
    async def test_grandchildren_die_on_a_normal_unload(self):
        self.reg("a", "forkbomb", timeout=5)
        child = await self.sup.call("a", "spawn", None)
        os.kill(child, 0)
        await self.sup.stop("a")

        def gone():
            try:
                os.kill(child, 0)
                with open("/proc/%d/stat" % child) as f:
                    return f.read().split(")")[1].split()[0] == "Z"
            except OSError:
                return True

        await self.until(gone, 5)

    async def test_reload_during_boot_kills_the_booting_worker_and_starts_no_second_one(self):
        sup, d, m, gate, procs = self.gated()
        old = sup._ws["r"]
        call = asyncio.ensure_future(sup.call("r", "x", None))
        await self.until(lambda: old.starting and old.state == "starting")
        sup.forget("r")
        sup.register("r", d, m)
        gate.set()
        with self.assertRaises(BackendError) as ctx:
            await call
        self.assertEqual(ctx.exception.code, "forgotten")
        await self.until(lambda: len(procs) == 1 and procs[0].returncode is not None)
        self.assertEqual((old.proc, old.state), (None, "stopped"))
        self.assertIsNot(sup._ws["r"], old)
        self.assertIsNone(sup._ws["r"].proc)
        self.assertEqual(len(procs), 1)

    async def test_stop_during_boot_cancels_the_boot(self):
        sup, d, m, gate, procs = self.gated()
        w = sup._ws["r"]
        call = asyncio.ensure_future(sup.call("r", "x", None))
        await self.until(lambda: w.starting and w.state == "starting")
        await sup.stop("r", "disable")
        gate.set()
        with self.assertRaises(BackendError) as ctx:
            await call
        self.assertEqual(ctx.exception.code, "stopped")
        await self.until(lambda: len(procs) == 1 and procs[0].returncode is not None)
        self.assertEqual((w.proc, w.state), (None, "stopped"))

    async def test_shutdown_kills_a_worker_that_is_still_booting(self):
        sup, d, m, gate, procs = self.gated()
        w = sup._ws["r"]
        call = asyncio.ensure_future(sup.call("r", "x", None))
        await self.until(lambda: w.starting and w.state == "starting")
        asyncio.get_running_loop().call_later(0.2, gate.set)
        await sup.shutdown(total=3.0)
        self.assertEqual(len(procs), 1)
        self.assertIsNotNone(procs[0].returncode)
        self.assertIsNone(w.proc)
        with self.assertRaises(BackendError) as ctx:
            await call
        self.assertEqual(ctx.exception.code, "shutdown")

    async def test_crashes_outside_the_window_are_pruned_when_counting(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        w = self.worker("a")
        w.crashes.extend([self.host.clock - 10, self.host.clock - 5])
        self.host.clock += 3600
        w.proc.kill()
        await self.until(lambda: w.proc is None, 5)
        self.assertEqual(self.state("a"), "crashed")
        self.assertEqual(len(w.crashes), 1)

    async def test_worker_writes_no_bytecode(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        await self.sup.stop("a")
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    async def test_uninstall_hook_sees_the_truthful_purge_flag_but_data_is_kept_without_do_purge(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        out = await self.sup.uninstall("a", purge=True, do_purge=False)
        self.assertIn("uninstall purge=True", self.hooks("a"))
        self.assertEqual(self.host.purged, [])
        self.assertEqual((out["ok"], out["purged"], out["hook"]), (True, False, "ran"))
        out = await self.sup.uninstall("a", purge=True)
        self.assertEqual((out["purged"], self.host.purged), (True, ["a"]))

    async def test_host_calls_are_attributed_to_the_process_never_to_the_payload(self):
        seen = []
        self.host.hostcalls["files"] = lambda pid, arg: seen.append((pid, arg)) or {"entries": []}
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        w = self.worker("a")
        await self.sup._host_run(w, w.proc, "files", 77, {"pid": "victim", "root": "data"})
        self.assertEqual(seen, [("a", {"pid": "victim", "root": "data"})])


class FakeStdout:
    def __init__(self):
        self.eof = 0

    def feed_eof(self):
        self.eof += 1


class ExitedProc:
    def __init__(self):
        self.pid, self.returncode, self.stdout = 4242, 0, FakeStdout()

    async def wait(self):
        await asyncio.Event().wait()


class SweepTests(SupCase):
    async def test_reap_sweeps_and_finishes_when_wait_never_returns(self):
        calls, proc = [], ExitedProc()
        reader = asyncio.ensure_future(asyncio.Event().wait())
        async def exited(p):
            return None

        with mock.patch.object(os, "killpg", lambda pg, sig: calls.append((pg, sig)), create=True), mock.patch.object(fy_supervisor.signal, "SIGKILL", 9, create=True), mock.patch.object(fy_supervisor, "EXIT_WAIT", 0.01), mock.patch.object(self.sup, "_exited", exited):
            await asyncio.wait_for(self.sup._reap(fy_supervisor._W("a"), proc, reader), 2)
        reader.cancel()
        self.assertEqual(calls, [(4242, 9)])
        self.assertEqual(proc.stdout.eof, 1)

    async def test_down_sweeps_the_group_of_an_exited_worker(self):
        calls, proc, w = [], ExitedProc(), fy_supervisor._W("a")
        w.proc, w.stopping = proc, True
        with mock.patch.object(os, "killpg", lambda pg, sig: calls.append((pg, sig)), create=True), mock.patch.object(fy_supervisor.signal, "SIGKILL", 9, create=True):
            self.sup._down(w, proc)
        self.assertEqual(calls, [(4242, 9)])
        self.assertIsNone(w.proc)

    @LINUX
    async def test_exited_wakes_for_a_real_process(self):
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await asyncio.wait_for(self.sup._exited(proc), 5)
        self.assertEqual(await asyncio.wait_for(proc.wait(), 5), 0)


class FixTests(SupCase):
    async def test_missing_xdg_runtime_fails_closed_without_tmp_fallback(self):
        self.reg("a", "ok")
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(fy_supervisor.fy_store, "runtime_root", return_value=None):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            with self.assertRaises(BackendError) as cm:
                await self.sup.call("a", "echo", 1)
            self.assertEqual(cm.exception.code, "runtime")
            with self.assertRaises(BackendError):
                await self.sup.call("a", "echo", 1)
        self.assertEqual(len([m for m in self.host.logged if "XDG_RUNTIME_DIR" in m]), 1)
        self.assertNotEqual(self.sup.status()["a"]["state"], "starting")

    async def test_crash_wakes_slot_waiters(self):
        fut = asyncio.get_running_loop().create_future()
        self.sup._freed.append(fut)
        self.sup._crashed(fy_supervisor._W("a"), "could not start backend")
        self.assertTrue(fut.done())

    async def test_forgotten_live_worker_counts_toward_memory(self):
        w = fy_supervisor._W("a")
        w.state, w.lim = "ready", {"memory_mb": 32}
        self.sup._old.add(w)
        self.assertEqual(self.sup._memory(), 32)

    async def test_hard_kill_skipped_when_exit_landed_after_timeout(self):
        proc = types.SimpleNamespace(pid=1, returncode=None)

        async def wait():
            await asyncio.Event().wait()

        def soft(w, p, hard):
            if not hard:
                p.returncode = 0

        proc.wait = wait
        with mock.patch.object(fy_supervisor, "TERM_WAIT", 0.01), mock.patch.object(self.sup, "_signal", side_effect=soft) as sig:
            await self.sup._kill(fy_supervisor._W("a"), proc)
        self.assertEqual([c.args[2] for c in sig.call_args_list], [False])


class LinuxProcessTests(SupCase):
    @LINUX
    async def test_worker_is_a_session_leader_with_no_new_privs_and_low_priority(self):
        self.reg("a", "ok")
        pid = await self.sup.call("a", "pid", None)
        self.assertEqual(os.getpgid(pid), pid)
        self.assertEqual(await self.sup.call("a", "nnp", None), "1")
        self.assertEqual(await self.sup.call("a", "nice", None), 19)

    @LINUX
    async def test_sudo_cannot_gain_privileges(self):
        self.reg("a", "sudo")
        out = await self.sup.call("a", "x", None)
        self.assertTrue(out.get("error") or out.get("code") != 0)

    @LINUX
    async def test_memory_cap_is_applied_and_reported(self):
        self.reg("a", "ok")
        await self.sup.call("a", "echo", 1)
        limits = await self.sup.call("a", "limits", None)
        st = self.sup.status()["a"]
        self.assertTrue(st["limits_applied"], st)
        self.assertIsNone(st["limits_warning"])
        self.assertGreater(limits["RLIMIT_DATA"][0], 32 << 20)


if __name__ == "__main__":
    unittest.main()
