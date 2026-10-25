import ast
import asyncio
import inspect
import io
import json
import os
import sys
import tempfile
import tokenize
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fy_diag
import fy_media
import fy_supervisor
import fy_targets

LINUX = sys.platform.startswith("linux")
SECRET = "s3cr3t-value-xyz-0042"
TOP = {"settings_state", "loader", "sessions", "modules", "workers", "events", "caps", "failures", "updates", "targets", "media", "logs"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_host(**kw):
    state = {
        "disabled": ["off"],
        "prefs": {"workers_max": 2, "safe": False, "dev": {"enabled": True}},
        "modules": {
            "hidden": ["hid"],
            "frozen": {"fro": {"at": 1, "version": "1.0.0"}},
            "state": {
                "good": {"ver": "1.0.0", "data_v": 1, "ok_at": 5, "fails": 0, "last_error": ""},
                "bad": {"ver": "1.0.0", "data_v": 1, "ok_at": 0, "fails": 2, "last_error": "boom"},
            },
        },
        "updates": {
            "last": 99,
            "fails": 1,
            "loader": {"tag": "v1.2.0", "state": "update"},
            "plugins": {"a": {"latest": "2", "state": "update"}, "fro": {"state": "update"}, "off": {"state": "update"}, "c": {"state": "current"}},
        },
    }
    host = types.SimpleNamespace(
        VERSION="1.1.0",
        API_GENERATION=1,
        PLATFORM="linux-aarch64",
        HOME="/home/deck",
        now=lambda: 1000.0,
        settings=lambda: state,
        manifests=lambda: [{"id": "a"}, {"id": "bad"}, {"id": "Bad Id"}, {"nope": 1}, "x"],
        attached=lambda name: name == "main",
        supervisor=None,
        log_tail=lambda: ["line %d" % i for i in range(100)],
    )
    for k, v in kw.items():
        setattr(host, k, v)
    return host


class ShapeTests(unittest.TestCase):
    def test_top_level_shape_and_types(self):
        snap = fy_diag.snapshot(make_host(), 400.0)
        self.assertEqual(set(snap), TOP)
        self.assertEqual({k for k in snap["loader"]}, {"version", "api", "platform", "python", "uptime_s", "rss_kb", "threads", "fds", "py_threads"})
        self.assertEqual(snap["loader"]["version"], "1.1.0")
        self.assertEqual(snap["loader"]["api"], 1)
        self.assertEqual(snap["loader"]["platform"], "linux-aarch64")
        self.assertEqual(snap["loader"]["python"], "%d.%d.%d" % sys.version_info[:3])
        self.assertEqual(snap["loader"]["uptime_s"], 600)
        self.assertEqual(snap["sessions"], {"main": True, "bar": False})
        json.dumps(snap)

    def test_settings_state_is_shown_and_bounded(self):
        import types
        self.assertEqual(fy_diag.snapshot(types.SimpleNamespace(settings_state=lambda: "corrupt"))["settings_state"], "corrupt")
        self.assertEqual(fy_diag.snapshot(types.SimpleNamespace(settings_state=lambda: "bogus"))["settings_state"], "unknown")
        self.assertEqual(fy_diag.snapshot(types.SimpleNamespace())["settings_state"], "unknown")

    def test_uptime_unknown_without_start_time(self):
        self.assertIsNone(fy_diag.snapshot(make_host())["loader"]["uptime_s"])
        self.assertIsNone(fy_diag.snapshot(make_host(), 2000.0)["loader"]["uptime_s"])
        self.assertIsNone(fy_diag.snapshot(make_host(), "x")["loader"]["uptime_s"])

    def test_modules_and_caps(self):
        snap = fy_diag.snapshot(make_host(), 0)
        self.assertEqual(snap["modules"], {"count": 2, "disabled": 1, "hidden": 1, "frozen": 1, "safe": False, "ids": ["a", "bad"]})
        self.assertEqual(snap["caps"]["workers_max"], 2)
        self.assertEqual((snap["caps"]["workers_hard"], snap["caps"]["worker_idle_s"]), (3, 120))
        self.assertEqual(snap["caps"]["event_queue_per_plugin"], 64)
        self.assertEqual(snap["caps"]["calls_queued_per_worker"], 16)
        for bad in (0, 9, "3", True, None):
            host = make_host(settings=lambda bad=bad: {"prefs": {"workers_max": bad}})
            self.assertEqual(fy_diag.snapshot(host, 0)["caps"]["workers_max"], 3)

    def test_failures_and_updates(self):
        snap = fy_diag.snapshot(make_host(), 0)
        self.assertEqual(snap["failures"], {"bad": {"fails": 2, "last_error": "boom"}})
        self.assertEqual(snap["updates"], {"last": 99, "fails": 1, "badge": {"n": 2, "loader": True}})

    def test_empty_and_garbage_settings(self):
        for value in ({}, None, [], "x", {"modules": 5, "prefs": [], "updates": "u", "disabled": 3}, {"modules": {"state": []}, "updates": {"plugins": 4, "loader": 5}}):
            snap = fy_diag.snapshot(make_host(settings=lambda value=value: value), 0)
            self.assertEqual(set(snap), TOP)
            self.assertEqual(snap["failures"], {})
            self.assertEqual(snap["updates"]["badge"], {"n": 0, "loader": False})
            json.dumps(snap)

    def test_broken_host_parts_do_not_break_the_snapshot(self):
        boom = mock.Mock(side_effect=RuntimeError("x"))
        host = make_host(settings=boom, manifests=boom, attached=boom, now=boom, log_tail=boom, supervisor=types.SimpleNamespace(status=boom, events=None))
        snap = fy_diag.snapshot(host, 0)
        self.assertEqual(set(snap), TOP)
        self.assertEqual(snap["sessions"], {"main": False, "bar": False})
        self.assertEqual(snap["workers"], {})
        self.assertEqual(snap["logs"], {"tail": []})
        bare = types.SimpleNamespace()
        self.assertEqual(set(fy_diag.snapshot(bare)), TOP)

    def test_logs_tail_is_bounded_and_ordered(self):
        snap = fy_diag.snapshot(make_host(), 0)
        tail = snap["logs"]["tail"]
        self.assertEqual(len(tail), 40)
        self.assertEqual(tail[0], "line 60")
        self.assertEqual(tail[-1], "line 99")
        snap = fy_diag.snapshot(make_host(log_tail=lambda: ["x" * 1000]), 0)
        self.assertEqual(len(snap["logs"]["tail"][0]), 300)

    def test_method_is_closed_while_developer_mode_is_off(self):
        off = {"error": "Developer mode is off", "code": "dev_off"}
        for value in ({"prefs": {}}, {"prefs": {"dev": {"enabled": False}}}, {"prefs": {"dev": {"enabled": 1}}}, {"prefs": {"dev": []}}, {}, None, "x"):
            with self.subTest(value=value):
                self.assertEqual(asyncio.run(fy_diag.METHODS["diag"](make_host(settings=lambda value=value: value), None)), off)
        boom = mock.Mock(side_effect=RuntimeError)
        self.assertEqual(asyncio.run(fy_diag.METHODS["diag"](make_host(settings=boom), None)), off)
        self.assertEqual(asyncio.run(fy_diag.METHODS["diag"](types.SimpleNamespace(), None)), off)
        self.assertEqual(set(asyncio.run(fy_diag.METHODS["diag"](make_host(), None))), TOP)
        self.assertEqual(set(fy_diag.snapshot(make_host(settings=lambda: {}), 0)), TOP)

    def test_method_uses_the_host_start_time(self):
        host = make_host(started_at=900.0)
        snap = asyncio.run(fy_diag.METHODS["diag"](host, None))
        self.assertEqual(snap["loader"]["uptime_s"], 100)
        self.assertEqual(set(fy_diag.METHODS), {"diag"})
        self.assertTrue(inspect.iscoroutinefunction(fy_diag.METHODS["diag"]))


class WorkerTests(unittest.TestCase):
    def sup(self, rows):
        return types.SimpleNamespace(status=lambda: rows, events=None)

    def test_limits_warning_is_shown_when_the_memory_cap_was_not_applied(self):
        rows = {"fan": {"state": "ready", "limits_applied": False, "limits_warning": "memory cap not applied: setrlimit failed /home/bob/x"}}
        snap = fy_diag.snapshot(make_host(supervisor=self.sup(rows)), 0)
        self.assertIn("memory cap not applied", snap["workers"]["fan"]["limits_warning"])
        self.assertNotIn("bob", json.dumps(snap))

    def test_rows_keep_only_known_fields(self):
        rows = {
            "fan": {
                "state": "ready", "os_pid": 321, "rss_kb": 8000, "calls": 4, "crashes": 1, "last_used": 12.5, "limits_applied": True,
                "last_error": {"code": "exception", "stage": "startup", "message": "bad", "trace": "Traceback /home/bob/x"},
                "env": {"TOKEN": SECRET}, "secret": SECRET,
            }
        }
        snap = fy_diag.snapshot(make_host(supervisor=self.sup(rows)), 0)
        row = snap["workers"]["fan"]
        self.assertEqual(set(row), {"state", "os_pid", "rss_kb", "calls", "crashes", "last_used", "limits_applied", "limits_warning", "last_error"})
        self.assertEqual(row["last_error"], {"code": "exception", "stage": "startup", "message": "bad"})
        self.assertEqual((row["os_pid"], row["rss_kb"], row["calls"], row["limits_applied"]), (321, 8000, 4, True))
        self.assertNotIn(SECRET, json.dumps(snap))
        self.assertNotIn("Traceback", json.dumps(snap))

    def test_odd_values_become_none_or_zero(self):
        rows = {"a": {"state": "x", "os_pid": "9", "rss_kb": None, "calls": -3, "crashes": True, "last_used": True, "limits_applied": "yes", "last_error": 5}}
        row = fy_diag.snapshot(make_host(supervisor=self.sup(rows)), 0)["workers"]["a"]
        self.assertEqual((row["os_pid"], row["rss_kb"], row["calls"], row["crashes"], row["last_used"], row["limits_applied"], row["last_error"]), (None, None, 0, 0, None, None, None))

    def test_row_count_and_ids_are_bounded(self):
        rows = {"w%d" % i: {"state": "ready"} for i in range(40)}
        rows["../etc"] = {"state": "ready"}
        rows[5] = {"state": "ready"}
        snap = fy_diag.snapshot(make_host(supervisor=self.sup(rows)), 0)
        self.assertEqual(len(snap["workers"]), 16)
        self.assertNotIn("../etc", snap["workers"])
        self.assertEqual(fy_diag.snapshot(make_host(supervisor=self.sup([1, 2])), 0)["workers"], {})

    def test_real_supervisor_status_has_the_fields_the_snapshot_reads(self):
        class Host:
            pass

        h = types.SimpleNamespace(ROOT=ROOT, HOME="/h", VERSION="1", PLATFORM="p", API_GENERATION=1, log=lambda *a: None, mono=lambda: 0.0, now=lambda: 0.0, settings=lambda: {}, loop=lambda: None)
        sup = fy_supervisor.Supervisor(h)
        with tempfile.TemporaryDirectory() as d:
            sup.register("fan", __import__("pathlib").Path(d), {"id": "fan"})
            rows = sup.status()
        self.assertTrue({"state", "os_pid", "rss_kb", "calls", "crashes", "last_error", "last_used", "limits_applied"} <= set(rows["fan"]))
        snap = fy_diag.snapshot(make_host(supervisor=sup), 0)
        self.assertEqual(snap["workers"]["fan"]["state"], rows["fan"]["state"])


class QueueTests(unittest.TestCase):
    def test_real_event_hub_counts(self):
        clock = Clock()
        hub = fy_supervisor.EventHub(lambda: None, clock=clock)
        for i in range(70):
            clock.t += 0.1
            hub.put("fan", "tick", {"i": i})
        sup = types.SimpleNamespace(status=lambda: {}, events=hub)
        snap = fy_diag.snapshot(make_host(supervisor=sup), 0)
        self.assertEqual(snap["events"], {"queued": 64, "dropped": 6})
        hub.take(10)
        self.assertEqual(fy_diag.snapshot(make_host(supervisor=sup), 0)["events"]["queued"], 54)

    def test_no_supervisor_means_zero(self):
        self.assertEqual(fy_diag.snapshot(make_host(), 0)["events"], {"queued": 0, "dropped": 0})

    def test_real_targets_and_media_objects(self):
        host = make_host()
        host.attached = lambda n: False
        host.send = None
        host.log = lambda *a: None
        targets = fy_targets.Targets(host)
        asyncio.run(targets.add_css("fan", "main", "k", "a{}"))
        asyncio.run(targets.add_js("fan", "bar", "j", "1"))
        host.targets = targets
        host.media = fy_media.Media()
        snap = fy_diag.snapshot(host, 0)
        self.assertEqual(snap["targets"], 2)
        self.assertEqual(snap["media"], {"tokens": 0, "server": False})

    def test_media_counts_from_fake_state(self):
        fake = types.SimpleNamespace(_tokens={"a": 1, "b": 2}, _server=object())
        self.assertEqual(fy_diag.snapshot(make_host(media=fake), 0)["media"], {"tokens": 2, "server": True})


class LeakTests(unittest.TestCase):
    def test_environment_never_appears(self):
        env = {"FRAMEY_TEST_SECRET": SECRET, "GITHUB_TOKEN": SECRET, "PATH": "/opt/secret-bin:/usr/bin", "XDG_RUNTIME_DIR": "/run/user/4242"}
        with mock.patch.dict(os.environ, env):
            snap = fy_diag.snapshot(make_host(supervisor=types.SimpleNamespace(status=lambda: {"fan": {"state": "ready"}}, events=None)), 0)
        text = json.dumps(snap)
        for needle in (SECRET, "/opt/secret-bin", "/run/user/4242", "GITHUB_TOKEN", "FRAMEY_TEST_SECRET"):
            self.assertNotIn(needle, text)

    def test_free_text_is_scrubbed_everywhere(self):
        lines = ["failed /home/bob/.ssh/id at /home/deck/.config/x", "token=abc123 more", "x Authorization: Bearer zzz9", "password: hunter2", "ghp_" + "A" * 30, "api_key=k1", "cookie=a=b; c=d"]
        dirty = lines[0]
        rows = {"fan": {"state": "ready", "last_error": {"code": "exception", "message": dirty, "stage": "call"}}}
        state = {"modules": {"state": {"fan": {"fails": 1, "last_error": dirty}}}}
        host = make_host(supervisor=types.SimpleNamespace(status=lambda: rows, events=None), settings=lambda: state, log_tail=lambda: lines)
        text = json.dumps(fy_diag.snapshot(host, 0))
        for needle in ("/home/bob", "abc123", "zzz9", "hunter2", "ghp_", "k1", "c=d", "a=b"):
            self.assertNotIn(needle, text)
        self.assertIn("/home/<user>", text)
        self.assertIn("~/.config/x", text)
        self.assertNotIn("/home/deck", text)

    def test_scrub_table(self):
        s = fy_diag.scrub
        self.assertEqual(s("plain text"), "plain text")
        self.assertEqual(s("/home/deck/a", "/home/deck"), "~/a")
        self.assertEqual(s("/home/deck/a /home/bob/b", "/home/deck"), "~/a /home/<user>/b")
        self.assertEqual(s("x /home/deck/a", ""), "x /home/<user>/a")
        self.assertEqual(s("tokens used: 5"), "tokens used: 5")
        self.assertEqual(s("token: 5"), "token: <hidden>")
        self.assertEqual(s("Bearer abc.def"), "<hidden>")
        self.assertEqual(s("a\x00b\x1bc\nd"), "a b c d")
        self.assertEqual(len(s("y" * 1000)), 300)
        self.assertEqual(s(None), "None")
        self.assertEqual(s("github_pat_" + "x" * 30), "<hidden>")
        self.assertEqual(s("/x", "/"), "/x")

    def test_scrub_hides_suffixed_secret_names_and_url_userinfo(self):
        s = fy_diag.scrub
        for text, want in (
            ("access_token=abc123", "access_token=<hidden>"),
            ("refresh_token: r1 tail", "refresh_token: <hidden>"),
            ("id-token=zz", "id-token=<hidden>"),
            ("client_secret = s3", "client_secret = <hidden>"),
            ("secret_key=k9", "secret_key=<hidden>"),
            ("x-api-key: k", "x-api-key: <hidden>"),
            ("stripe_key=sk_live", "stripe_key=<hidden>"),
            ('{"access_token": "abc"}', '{"access_token": <hidden>'),
            ("fetch https://user:pass@example.com/p?x=1 failed", "fetch https://<hidden>@example.com/p?x=1 failed"),
            ("hotkey: F5", "hotkey: F5"),
            ("monkey=banana", "monkey=banana"),
            ("the key was lost", "the key was lost"),
            ("see https://example.com/a@b", "see https://example.com/a@b"),
        ):
            with self.subTest(text=text):
                self.assertEqual(s(text), want)
        text = json.dumps(fy_diag.snapshot(make_host(log_tail=lambda: ["access_token=" + SECRET, "https://bob:" + SECRET + "@h/x", "client_secret: " + SECRET]), 0))
        self.assertNotIn(SECRET, text)

    def test_ids_are_validated(self):
        state = {"modules": {"state": {"../x": {"fails": 3}, "ok": {"fails": 1}}}}
        snap = fy_diag.snapshot(make_host(settings=lambda: state), 0)
        self.assertEqual(list(snap["failures"]), ["ok"])


class BoundTests(unittest.TestCase):
    def test_worst_case_stays_under_the_cap(self):
        big = "z" * 400
        rows = {"w%02d" % i: {"state": "crashed", "os_pid": 99999, "rss_kb": 99999999, "calls": 10**9, "crashes": 10**9, "last_used": 1.5e9, "limits_applied": False,
                                "last_error": {"code": "exception", "stage": "startup", "message": big}} for i in range(40)}
        state = {"modules": {"state": {"m%02d" % i: {"fails": 9, "last_error": big} for i in range(40)}}}
        manifests = [{"id": "m%02d" % i} for i in range(200)]
        host = make_host(supervisor=types.SimpleNamespace(status=lambda: rows, events=None), settings=lambda: state, log_tail=lambda: [big] * 500, manifests=lambda: manifests)
        snap = fy_diag.snapshot(host, 0)
        size = len(json.dumps(snap, separators=(",", ":")))
        self.assertLessEqual(size, fy_diag.CAP)
        self.assertEqual(len(snap["logs"]["tail"]), 40)
        self.assertEqual(len(snap["modules"]["ids"]), 64)
        self.assertEqual(snap["modules"]["count"], 200)
        self.assertEqual(len(snap["failures"]), 16)

    def test_fit_drops_whole_sections_when_over_the_cap(self):
        host = make_host(log_tail=lambda: ["y" * 300] * 40)
        with mock.patch.object(fy_diag, "CAP", 3000):
            snap = fy_diag.snapshot(host, 0)
        self.assertTrue(snap["truncated"])
        self.assertEqual(snap["logs"], {"tail": []})
        self.assertLessEqual(len(json.dumps(snap, separators=(",", ":"))), 3000)
        with mock.patch.object(fy_diag, "CAP", 100):
            snap = fy_diag.snapshot(host, 0)
        self.assertTrue(snap["truncated"])
        self.assertEqual(snap["failures"], {})
        self.assertEqual(snap["workers"], {})
        json.dumps(snap)


class ProcTests(unittest.TestCase):
    def test_reads_status_and_fd_count(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "status"), "w") as f:
                f.write("Name:\tpython\nVmPeak:\t 99 kB\nVmRSS:\t   30124 kB\nThreads:\t7\n")
            os.mkdir(os.path.join(d, "fd"))
            for i in range(5):
                open(os.path.join(d, "fd", str(i)), "w").close()
            self.assertEqual(fy_diag.proc_stats(d), (30124, 7, 5))

    def test_missing_or_garbled_proc(self):
        self.assertEqual(fy_diag.proc_stats(os.path.join(tempfile.gettempdir(), "no-such-dir-xyz")), (None, None, None))
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "status"), "w") as f:
                f.write("VmRSS:\n")
            self.assertEqual(fy_diag.proc_stats(d), (None, None, None))
            with open(os.path.join(d, "status"), "w") as f:
                f.write("VmRSS: abc kB\nThreads: 4\n")
            self.assertEqual(fy_diag.proc_stats(d)[1:], (None, None))

    @unittest.skipUnless(LINUX, "needs Linux /proc (pending Linux run)")
    def test_this_process(self):
        rss, threads, fds = fy_diag.proc_stats()
        self.assertGreater(rss, 0)
        self.assertGreaterEqual(threads, 1)
        self.assertGreaterEqual(fds, 3)
        snap = fy_diag.snapshot(make_host(), 0)
        self.assertEqual(snap["loader"]["rss_kb"] > 0, True)

    @unittest.skipIf(LINUX, "off-Linux values")
    def test_values_are_none_off_linux(self):
        loader = fy_diag.snapshot(make_host(), 0)["loader"]
        self.assertEqual((loader["rss_kb"], loader["threads"], loader["fds"]), (None, None, None))


class HygieneTests(unittest.TestCase):
    path = os.path.join(ROOT, "fy_diag.py")

    def source(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_no_comments_or_docstrings(self):
        src = self.source()
        self.assertNotIn(tokenize.COMMENT, {t.type for t in tokenize.generate_tokens(io.StringIO(src).readline)})
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(ast.get_docstring(node))

    def test_stdlib_only_no_environment_no_loader_import(self):
        names = set()
        for node in ast.walk(ast.parse(self.source())):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module.split(".")[0])
        self.assertLessEqual(names, {"json", "os", "re", "sys", "threading"})
        self.assertNotIn("environ", self.source())
        self.assertNotIn("getenv", self.source())

    def test_python_39_syntax(self):
        ast.parse(self.source(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
