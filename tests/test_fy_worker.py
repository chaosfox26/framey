import shutil
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "a"))

import harness
import fy_proto
import fy_worker

LINUX = unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux: prctl, resource limits, /proc")


class LimitDataTests(unittest.TestCase):
    def status(self, kb):
        text = "Name:\tpython\nVmData:\t%d kB\nVmRSS:\t1 kB\n" % kb
        return mock.patch("builtins.open", mock.mock_open(read_data=text))

    def resource(self, error=None):
        mod = types.ModuleType("resource")
        mod.RLIMIT_DATA, mod.calls = 2, []

        def setrlimit(which, value):
            if error is not None:
                raise error
            mod.calls.append((which, value))

        mod.setrlimit = setrlimit
        return mod

    def test_cap_is_growth_above_the_baseline(self):
        mod = self.resource()
        with self.status(20 << 10), mock.patch.dict(sys.modules, {"resource": mod}):
            self.assertEqual(fy_worker.limit_data(32), (True, None))
        self.assertEqual(mod.calls, [(2, ((20 << 20) + (32 << 20), (20 << 20) + (32 << 20)))])

    def test_large_baseline_is_not_skipped(self):
        mod = self.resource()
        with self.status(100 << 10), mock.patch.dict(sys.modules, {"resource": mod}):
            self.assertEqual(fy_worker.limit_data(16), (True, None))
        self.assertEqual(mod.calls[0][1][0], (100 << 20) + (16 << 20))

    def test_unreadable_vmdata_reports_reason(self):
        mod = self.resource()
        with mock.patch("builtins.open", side_effect=OSError("no proc")), mock.patch.dict(sys.modules, {"resource": mod}):
            self.assertEqual(fy_worker.limit_data(32), (False, "VmData unreadable"))
        self.assertEqual(mod.calls, [])

    def test_setrlimit_failure_reports_reason(self):
        mod = self.resource(error=OSError(1, "denied"))
        with self.status(20 << 10), mock.patch.dict(sys.modules, {"resource": mod}):
            applied, reason = fy_worker.limit_data(32)
        self.assertFalse(applied)
        self.assertIn("denied", reason)

    def test_no_resource_module_is_a_reported_noop(self):
        with mock.patch.dict(sys.modules, {"resource": None}):
            self.assertEqual(fy_worker.limit_data(32), (False, "unsupported platform"))


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.root = harness.make_root()
        self.wire = None
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.stop)

    def stop(self):
        if self.wire:
            self.wire.close()

    def boot(self, fixture, start=True, **extra):
        d, _ = harness.make_plugin(self.root, "p", fixture)
        self.stop()
        self.wire = harness.Wire(self.root)
        self.file = d / "backend.py"
        self.ids = iter(range(1, 1000))
        if not start:
            return None
        return self.start(**extra)

    def start(self, **extra):
        return self.wire.request(harness.startup_message(next(self.ids), "p", self.file, self.root, **extra))

    def op(self, **msg):
        msg["id"] = next(self.ids)
        return self.wire.request(msg)

    def call(self, method, arg=None):
        return self.op(op="call", method=method, arg=arg)

    def hooks_log(self):
        p = self.root / "hooks.log"
        return p.read_text().splitlines() if p.exists() else []

    def wait_exit(self, limit=10):
        return self.wire.proc.wait(timeout=limit)


class LifecycleTests(WorkerCase):
    def test_startup_reply_and_call(self):
        rep = self.boot("ok")
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(sorted(rep["result"]["hooks"]), ["call", "migrate", "startup", "uninstall", "unload"])
        self.assertFalse(rep["result"]["migrated"])
        self.assertIn("limits_applied", rep["result"])
        self.assertEqual(self.call("echo", {"a": [1, 2]})["result"], {"echo": {"a": [1, 2]}})

    def test_call_before_startup_is_not_ready(self):
        self.boot("ok", start=False)
        rep = self.call("echo")
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["error"]["code"], "not_ready")

    def test_ping_pong(self):
        self.boot("ok")
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})

    def test_migrate_runs_before_startup_with_versions(self):
        self.boot("ok", start=False)
        rep = self.op(**dict(harness.startup_message(0, "p", self.file, self.root), op="migrate", from_version="1.0.0", to_version="1.1.0", data_from=1, data_to=2))
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["result"], {"migrated": True})
        rep = self.start()
        self.assertTrue(rep["result"]["migrated"])
        self.assertEqual(self.hooks_log()[0], "migrate 1.0.0 1.1.0 1 2")
        self.assertTrue(self.hooks_log()[1].startswith("startup p"))

    def test_ctx_has_every_documented_member(self):
        self.boot("ok")
        line = self.hooks_log()[0]
        for name in ("plugin", "runtime", "dirs", "settings", "log", "from_version", "to_version", "data_from", "data_to", "purge", "reason"):
            self.assertIn(repr(name), line)

    def test_uninstall_hook_flag_and_worker_stays_alive(self):
        self.boot("ok")
        rep = self.op(op="uninstall", purge=True)
        self.assertEqual(rep["result"], {"ran": True})
        self.assertIn("uninstall purge=True", self.hooks_log())
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})
        self.assertIsNone(self.wire.proc.poll())

    def test_unload_runs_hook_replies_then_exits_zero(self):
        self.boot("ok")
        rep = self.op(op="unload", reason="idle")
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(self.wait_exit(), 0)
        self.assertIn("unload idle", self.hooks_log())

    def test_unload_without_startup_still_exits(self):
        self.boot("ok", start=False)
        self.assertTrue(self.op(op="unload", reason="lru")["ok"])
        self.assertEqual(self.wait_exit(), 0)

    def test_hook_arity_no_argument_hooks(self):
        rep = self.boot("legacy")
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(self.call("x")["result"], {"legacy": "x"})
        self.op(op="unload", reason="reload")
        self.wait_exit()
        self.assertEqual(self.hooks_log(), ["startup noarg", "unload noarg"])

    def test_unload_hook_slow_async_reports_timeout_and_still_exits(self):
        self.boot("unload_slow")
        started = time.monotonic()
        rep = self.op(op="unload", reason="reload")
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["error"]["code"], "timeout")
        self.assertEqual(rep["error"]["stage"], "unload")
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(self.wait_exit(), 0)

    def test_eof_on_stdin_exits(self):
        self.boot("ok")
        self.wire.proc.stdin.close()
        self.assertEqual(self.wait_exit(), 0)


class StageTests(WorkerCase):
    def test_startup_failure_has_stage_and_trace(self):
        rep = self.boot("startup_fail")
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["error"]["stage"], "startup")
        self.assertEqual(rep["error"]["code"], "exception")
        self.assertIn("startup boom", rep["error"]["message"])
        self.assertIn("RuntimeError", rep["error"]["trace"])
        self.assertLessEqual(len(rep["error"]["trace"]), 600)
        self.assertEqual(self.call("x")["error"]["code"], "not_ready")

    def test_startup_hook_timeout(self):
        rep = self.boot("startup_hang", limits={"startup_timeout": 1, "timeout": 5, "concurrency": 1, "memory_mb": 64})
        self.assertEqual(rep["error"]["code"], "timeout")
        self.assertEqual(rep["error"]["stage"], "startup")

    def test_import_failure_has_import_stage(self):
        rep = self.boot("import_fail")
        self.assertEqual(rep["error"]["stage"], "import")
        self.assertIn("no such thing", rep["error"]["message"])

    def test_migrate_failure_has_migrate_stage(self):
        self.boot("migrate_fail", start=False)
        rep = self.op(**dict(harness.startup_message(0, "p", self.file, self.root), op="migrate", from_version="1", to_version="2"))
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["error"]["stage"], "migrate")
        self.assertIn("migrate boom", rep["error"]["message"])

    def test_bad_startup_message_is_reported_not_fatal(self):
        self.boot("ok", start=False)
        rep = self.op(op="startup", plugin="Bad Id", file="x", dirs={})
        self.assertEqual(rep["error"]["stage"], "import")
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})

    def test_second_startup_refused(self):
        self.boot("ok")
        rep = self.start()
        self.assertFalse(rep["ok"])


class CallTests(WorkerCase):
    def test_exception_in_call(self):
        self.boot("ok")
        rep = self.call("raise")
        self.assertEqual(rep["error"]["code"], "exception")
        self.assertEqual(rep["error"]["message"], "bad thing")
        self.assertIn("ValueError", rep["error"]["trace"])
        self.assertEqual(self.call("echo", 1)["result"], {"echo": 1})

    def test_sys_exit_in_call_is_exit_error_and_worker_survives(self):
        self.boot("exit")
        for method in ("x", "asyncexit"):
            rep = self.call(method)
            self.assertEqual(rep["error"]["code"], "exit", rep)
            self.assertEqual(rep["error"]["message"], "backend called exit")
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})

    def test_bad_result_names_the_type(self):
        self.boot("bigresult")
        rep = self.call("obj")
        self.assertEqual(rep["error"]["code"], "bad_result")
        self.assertIn("object", rep["error"]["message"])
        self.assertEqual(self.call("nan")["error"]["code"], "bad_result")

    def test_result_cap(self):
        self.boot("bigresult")
        self.assertEqual(len(self.call("s", fy_proto.PAYLOAD_MAX - 100)["result"]), fy_proto.PAYLOAD_MAX - 100)
        rep = self.call("s", fy_proto.PAYLOAD_MAX)
        self.assertEqual(rep["error"]["code"], "too_large")
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})

    def test_async_call_does_not_block_ping_and_cancel_drops_reply(self):
        self.boot("slow")
        self.wire.send({"op": "call", "id": 100, "method": "x", "arg": 30})
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})
        rep = self.op(op="cancel", target=100)
        self.assertEqual(rep["result"], {"cancelled": True})
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})
        self.assertEqual(self.op(op="cancel", target=100)["result"], {"cancelled": False})

    def test_stdout_noise_never_reaches_the_protocol(self):
        self.boot("noisy")
        rep = self.call("x")
        self.assertEqual(rep, {"re": rep["re"], "ok": True, "result": {"ok": True}})
        self.op(op="unload", reason="reload")
        self.wait_exit()
        err = self.wire.proc.stderr.read().decode()
        for text in ("import time noise", "more noise", "raw noise", "forged"):
            self.assertIn(text, err)

    def test_backend_cannot_read_the_protocol_from_stdin(self):
        self.boot("ok")
        self.assertEqual(self.call("stdin")["result"], "")
        self.assertEqual(self.call("echo", 2)["result"], {"echo": 2})

    def test_loader_module_is_not_importable_by_backends(self):
        self.boot("ok")
        mods = self.call("sysmods")["result"]
        self.assertIn("framey_api", mods)
        self.assertNotIn("framey", mods)

    def test_unknown_op_and_missing_id(self):
        self.boot("ok")
        self.assertEqual(self.op(op="nope")["error"]["code"], "exception")
        self.wire.send({"op": "ping"})
        self.assertEqual(self.wire.recv()["re"], 0)


class ProtocolViolationTests(WorkerCase):
    def final(self):
        msg = self.wire.recv()
        self.assertEqual(msg["re"], 0)
        self.assertFalse(msg["ok"])
        self.assertEqual(self.wait_exit(), 3)
        return msg

    def test_oversize_line(self):
        self.boot("ok")
        self.wire.send_raw(b"x" * (fy_proto.LINE_MAX + 100) + b"\n")
        self.assertEqual(self.final()["error"]["code"], "too_large")

    def test_oversize_line_without_newline(self):
        self.boot("ok")
        self.wire.send_raw(b"y" * (fy_proto.LINE_MAX + 100))
        self.assertEqual(self.final()["error"]["code"], "too_large")

    def test_line_of_exactly_the_cap_is_accepted(self):
        self.boot("ok")
        pad = fy_proto.LINE_MAX - len(b'{"op":"ping","id":9,"pad":""}')
        self.wire.send_raw(b'{"op":"ping","id":9,"pad":"' + b"p" * pad + b'"}\n')
        self.assertEqual(self.wire.recv()["result"], {"pong": True})

    def test_bad_json_and_non_object(self):
        for line in (b"not json\n", b"[1]\n"):
            self.boot("ok")
            self.wire.send_raw(line)
            self.assertEqual(self.final()["error"]["code"], "exception")


class EventAndHostTests(WorkerCase):
    def events(self, rep_id):
        out = []
        while True:
            msg = self.wire.recv()
            if msg.get("re") == rep_id:
                return out, msg
            out.append(msg)

    def test_emit_validates_and_rate_limits(self):
        self.boot("emit_flood")
        self.wire.send({"op": "call", "id": 50, "method": "bad", "arg": None})
        out, rep = self.events(50)
        self.assertEqual(rep["result"], [False, False, True])
        self.assertEqual([e["name"] for e in out], ["fine"])
        self.wire.send({"op": "call", "id": 51, "method": "flood", "arg": 500})
        out, rep = self.events(51)
        self.assertLessEqual(len(out), 100)
        self.assertEqual(len(out), rep["result"])
        self.assertTrue(all(e["ev"] == "emit" and e["name"] == "tick" for e in out))
        self.assertLess(len(out), 500)

    def test_host_call_round_trip(self):
        self.boot("host_fetch")
        self.wire.send({"op": "call", "id": 60, "method": "x", "arg": "https://example.test/a"})
        ask = self.wire.recv()
        self.assertEqual(ask["host"], "fetch")
        self.assertEqual(ask["arg"], {"url": "https://example.test/a"})
        self.wire.send({"op": "host_reply", "id": ask["id"], "ok": True, "result": {"status": 200}})
        self.assertEqual(self.wire.recv(), {"re": 60, "ok": True, "result": {"status": 200}})

    def test_host_call_error_reaches_backend_with_code(self):
        self.boot("host_fetch")
        self.wire.send({"op": "call", "id": 61, "method": "x", "arg": "https://example.test/a"})
        ask = self.wire.recv()
        self.wire.send({"op": "host_reply", "id": ask["id"], "ok": False, "error": {"code": "busy", "message": "too many"}})
        rep = self.wire.recv()
        self.assertEqual(rep["result"], {"failed": "busy", "message": "too many"})

    def test_host_reply_for_unknown_id_is_ignored(self):
        self.boot("ok")
        self.wire.send({"op": "host_reply", "id": 999, "ok": True, "result": 1})
        self.assertEqual(self.op(op="ping")["result"], {"pong": True})


class LinuxOnlyTests(WorkerCase):
    @LINUX
    def test_no_new_privs_set(self):
        self.boot("ok")
        self.assertEqual(self.call("nnp")["result"], "1")

    @LINUX
    def test_rlimits_and_nice(self):
        self.boot("ok")
        limits = self.call("limits")["result"]
        self.assertEqual(limits["RLIMIT_CORE"], [0, 0])
        self.assertEqual(limits["RLIMIT_NOFILE"], [256, 256])
        self.assertEqual(limits["RLIMIT_FSIZE"], [64 << 20, 64 << 20])
        self.assertEqual(self.call("nice")["result"], 19)

    @LINUX
    def test_data_limit_applied_and_enforced(self):
        rep = self.boot("ok")
        self.assertTrue(rep["result"]["limits_applied"], rep["result"])
        self.assertIsNone(rep["result"]["limits_reason"])
        self.assertEqual(self.call("hog", 400 << 20)["error"]["code"], "exception")
        self.assertEqual(self.call("echo", 3)["result"], {"echo": 3})

    @LINUX
    def test_pdeathsig_worker_dies_when_parent_dies(self):
        script = "\n".join((
            "import subprocess, sys, time",
            "p = subprocess.Popen([sys.executable, '-I', sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE)",
            "p.stdin.write(b'{\"op\":\"ping\",\"id\":1}\\n')",
            "p.stdin.flush()",
            "p.stdout.readline()",
            "print(p.pid, flush=True)",
            "time.sleep(60)",
        ))
        import subprocess
        parent = subprocess.Popen([sys.executable, "-c", script, str(self.root / "fy_worker.py")], stdout=subprocess.PIPE)
        self.addCleanup(parent.wait)
        child = int(parent.stdout.readline())
        self.assertTrue(Path("/proc/%d" % child).exists())
        parent.kill()
        parent.wait()
        deadline = time.monotonic() + 5
        while Path("/proc/%d" % child).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(Path("/proc/%d" % child).exists())


class HelperTests(unittest.TestCase):
    def test_takes_arg(self):
        self.assertFalse(fy_worker.takes_arg(lambda: 0))
        self.assertTrue(fy_worker.takes_arg(lambda x: 0))
        self.assertTrue(fy_worker.takes_arg(lambda *a: 0))
        self.assertFalse(fy_worker.takes_arg(lambda **k: 0))

    def test_clamp_rejects_bool_nan_and_text(self):
        self.assertEqual(fy_worker.clamp(True, 10, 1, 60), 10)
        self.assertEqual(fy_worker.clamp(float("nan"), 10, 1, 60), 10)
        self.assertEqual(fy_worker.clamp("5", 10, 1, 60), 10)
        self.assertEqual(fy_worker.clamp(500, 10, 1, 60), 60)
        self.assertEqual(fy_worker.clamp(0, 10, 1, 60), 1)

    def test_describe_truncates(self):
        try:
            raise ValueError("m" * 1000)
        except ValueError as e:
            err = fy_worker.describe(e, "startup")
        self.assertEqual(len(err["message"]), 300)
        self.assertLessEqual(len(err["trace"]), 600)
        self.assertEqual(err["stage"], "startup")


class TermTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_runs_a_bounded_unload_and_exits_zero(self):
        import asyncio
        import os
        from unittest import mock
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        worker = fy_worker.Worker(asyncio.get_running_loop(), w)
        with mock.patch.object(fy_worker, "leave") as leave:
            worker.term()
            await asyncio.sleep(0.05)
        os.close(w)
        leave.assert_called_once_with(0)
        self.assertIn(b'"unloaded":true', os.read(r, 4096))


if __name__ == "__main__":
    unittest.main()
