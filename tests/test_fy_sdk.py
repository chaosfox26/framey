import ast
import asyncio
import collections
import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tokenize
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fy_sdk
import fy_store

PYI = ROOT / "types" / "framey_api.pyi"
POSIX = os.name == "posix"
POSIX_ONLY = "POSIX only; run on Linux: python3 -I -m unittest discover -s tests -p test_fy_sdk.py"
CONTRACT_MEMBERS = {
    "PLUGIN", "API", "runtime", "dirs", "settings", "log", "events", "fetch", "resource_url",
    "resource_revoke", "list_dir", "migrate", "companion", "FrameyApiError",
}


def link_dir(target, link):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


class FakeRpc:
    def __init__(self):
        self.emitted = []
        self.calls = []
        self.replies = {}
        self.emit_result = True

    def emit(self, name, payload):
        self.emitted.append((name, payload))
        return self.emit_result

    async def host(self, op, arg):
        self.calls.append((op, arg))
        r = self.replies.get(op)
        if isinstance(r, BaseException):
            raise r
        return r(arg) if callable(r) else r


class HostFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fysdk-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dirs = fy_store.dirs("demo", home=self.tmp / "home", runtime_base=self.tmp / "run")
        self.rpc = FakeRpc()
        self.err = io.StringIO()
        self.meta = {"id": "demo", "name": "Demo", "version": "1.2.3", "api": 1, "data_version": 1}
        self.rt = {"framey": "1.1.0", "api": 1, "platform": "linux-aarch64", "python": "3.11.2"}
        self.api = self.make()

    def make(self, **kw):
        with contextlib.redirect_stderr(self.err):
            return fy_sdk.build("demo", self.meta, self.dirs._asdict(), self.rt, self.rpc, **kw)

    def run_async(self, coro):
        return asyncio.run(coro)


def sig_of_node(node):
    a = node.args
    pos = list(a.posonlyargs) + list(a.args)
    if pos and pos[0].arg == "self":
        pos = pos[1:]
        defaults = list(a.defaults)
    else:
        defaults = list(a.defaults)
    names = [x.arg for x in pos]
    has_default = {x.arg for x in pos[len(pos) - len(defaults):]} if defaults else set()
    if a.vararg:
        names.append("*" + a.vararg.arg)
    for k, d in zip(a.kwonlyargs, a.kw_defaults):
        names.append(k.arg)
        if d is not None:
            has_default.add(k.arg)
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return names, has_default


def sig_of_object(obj):
    names, has_default = [], set()
    for p in inspect.signature(obj).parameters.values():
        prefix = "*" if p.kind is p.VAR_POSITIONAL else "**" if p.kind is p.VAR_KEYWORD else ""
        names.append(prefix + p.name)
        if p.default is not p.empty:
            has_default.add(p.name)
    return names, has_default


class PyiAgreementTest(Base):
    def setUp(self):
        super().setUp()
        self.tree = ast.parse(PYI.read_text(encoding="utf-8"))
        self.classes = {n.name: n for n in self.tree.body if isinstance(n, ast.ClassDef)}

    def check_function(self, node, obj, label):
        self.assertTrue(callable(obj), label)
        self.assertEqual(isinstance(node, ast.AsyncFunctionDef), inspect.iscoroutinefunction(obj), label)
        self.assertEqual(sig_of_node(node), sig_of_object(obj), label)

    def check_members(self, cls, obj, label):
        for n in cls.body:
            if isinstance(n, ast.AnnAssign):
                self.assertTrue(hasattr(obj, n.target.id), "%s.%s" % (label, n.target.id))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.check_function(n, getattr(obj, n.name), "%s.%s" % (label, n.name))

    def test_public_names_equal_contract(self):
        declared = set()
        for n in self.tree.body:
            if isinstance(n, ast.AnnAssign):
                declared.add(n.target.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                declared.add(n.name)
        self.assertEqual(declared, CONTRACT_MEMBERS)
        actual = {k for k in vars(self.api) if not k.startswith("_")}
        self.assertEqual(actual, CONTRACT_MEMBERS)

    def test_every_pyi_member_exists_and_agrees(self):
        simple = {"str": str, "int": int}
        for n in self.tree.body:
            if isinstance(n, ast.AnnAssign):
                name, ann = n.target.id, n.annotation.id
                obj = getattr(self.api, name)
                if ann in simple:
                    self.assertIsInstance(obj, simple[ann], name)
                else:
                    self.check_members(self.classes[ann], obj, name)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.check_function(n, getattr(self.api, n.name), n.name)
            elif isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                cls = getattr(self.api, n.name)
                self.assertTrue(issubclass(cls, Exception))
                init = next(m for m in n.body if isinstance(m, ast.FunctionDef) and m.name == "__init__")
                self.assertEqual(sig_of_node(init), sig_of_object(cls))
                self.check_members(n, cls("c", "m"), n.name)

    def test_pyi_is_plain_declarations(self):
        for n in ast.walk(self.tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertEqual(len(n.body), 1)
                self.assertIsInstance(n.body[0].value, ast.Constant)
                self.assertIs(n.body[0].value.value, Ellipsis)
        self.assertNotIn("#", PYI.read_text(encoding="utf-8"))

    def test_sources_follow_project_rules(self):
        allowed = {"collections", "errno", "hashlib", "json", "os", "re", "shutil", "stat", "sys", "tempfile", "threading", "time", "types",
                   "datetime", "pathlib", "typing", "fy_store"}
        for name in ("fy_store.py", "fy_sdk.py"):
            path = ROOT / name
            src = path.read_text(encoding="utf-8")
            with open(path, "rb") as f:
                kinds = {t.type for t in tokenize.tokenize(f.readline)}
            self.assertNotIn(tokenize.COMMENT, kinds, name)
            tree = ast.parse(src)
            for n in ast.walk(tree):
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.assertIsNone(ast.get_docstring(n), name)
                if isinstance(n, ast.Import):
                    self.assertTrue({a.name.split(".")[0] for a in n.names} <= allowed, name)
                if isinstance(n, ast.ImportFrom):
                    self.assertIn(n.module.split(".")[0], allowed, name)
            self.assertNotIn("sleep", src)
            self.assertNotIn("asyncio", src)


class RuntimeTest(Base):
    def test_shape_and_values(self):
        r = self.api.runtime
        self.assertEqual(
            sorted(vars(r)),
            ["api", "framey", "platform", "plugin", "plugin_dir", "python", "version"],
        )
        self.assertEqual((r.framey, r.api, r.platform, r.python, r.plugin, r.version), ("1.1.0", 1, "linux-aarch64", "3.11.2", "demo", "1.2.3"))
        self.assertIsNone(r.plugin_dir)
        self.assertEqual(self.api.PLUGIN, "demo")
        self.assertEqual(self.api.API, 1)

    def test_plugin_dir_from_meta_or_runtime(self):
        self.meta["plugin_dir"] = str(self.tmp / "p")
        self.assertEqual(self.make().runtime.plugin_dir, self.tmp / "p")
        self.rt["plugin_dir"] = str(self.tmp / "q")
        self.assertEqual(self.make().runtime.plugin_dir, self.tmp / "q")

    def test_defaults_when_loader_sends_little(self):
        api = fy_sdk.build("demo", None, self.dirs._asdict(), None, self.rpc)
        r = api.runtime
        self.assertEqual((r.framey, r.platform, r.version, api.API), ("", "", "", 1))
        self.assertEqual(r.python, "%d.%d.%d" % sys.version_info[:3])

    def test_no_environment_or_secrets(self):
        env = {"FRAMEY_TOKEN": "s3cr3t-token", "GITHUB_TOKEN": "ghp_xxx", "FRAMEY_PLUGIN": "demo"}
        with mock.patch.dict(os.environ, env):
            api = self.make()
        text = repr(vars(api.runtime)) + repr(vars(api.events))
        for secret in ("s3cr3t-token", "ghp_xxx"):
            self.assertNotIn(secret, text)
        for key in ("env", "environ", "environment", "token", "tokens"):
            self.assertFalse(hasattr(api.runtime, key))
            self.assertFalse(hasattr(api, key))

    def test_dirs_accepts_namedtuple_and_object(self):
        a = fy_sdk.build("demo", self.meta, self.dirs, self.rt, self.rpc)
        self.assertEqual(a.dirs.logs, self.dirs.logs)
        a = fy_sdk.build("demo", self.meta, mock.Mock(**{k: str(v) for k, v in self.dirs._asdict().items()}), self.rt, self.rpc)
        self.assertEqual(a.dirs.config, self.dirs.config)

    def test_unavailable_runtime_does_not_prevent_other_sdk_use(self):
        dirs = self.dirs._replace(runtime=None)
        for supplied in (dirs, dirs._asdict()):
            api = fy_sdk.build("demo", self.meta, supplied, self.rt, self.rpc)
            self.assertEqual(api.runtime.platform, "linux-aarch64")
            self.assertTrue(api.events.emit("ready"))
            api.settings.set("available", True)
            self.assertTrue(api.settings.get("available"))
            self.assertEqual(api.dirs.data, self.dirs.data)
            with self.assertRaisesRegex(RuntimeError, "runtime directory unavailable"):
                _ = api.dirs.runtime
        self.assertFalse(self.tmp.joinpath("run").exists())


class DirsTest(Base):
    def test_nothing_exists_until_used(self):
        self.assertFalse((self.tmp / "home").exists())
        self.assertFalse((self.tmp / "run").exists())
        _ = self.api.settings
        _ = self.api.log
        _ = self.api.runtime
        self.assertFalse((self.tmp / "home").exists())

    def test_access_creates_and_returns_paths(self):
        for name in fy_store.Dirs._fields:
            p = getattr(self.api.dirs, name)
            self.assertIsInstance(p, Path)
            self.assertEqual(p, getattr(self.dirs, name))
            self.assertTrue(p.is_dir())

    @unittest.skipUnless(POSIX, POSIX_ONLY)
    def test_access_creates_mode_0700(self):
        for name in fy_store.Dirs._fields:
            p = getattr(self.api.dirs, name)
            self.assertEqual(p.stat().st_mode & 0o777, 0o700, name)

    def test_never_inside_plugin_code_dir(self):
        plugin_dir = self.tmp / "plugins" / "demo"
        for name in fy_store.Dirs._fields:
            self.assertNotIn(plugin_dir, getattr(self.dirs, name).parents)

    def test_settings_and_log_use_standard_locations(self):
        self.api.settings.set("k", "v")
        self.assertEqual(json.loads((self.dirs.config / "settings.json").read_text()), {"k": "v"})
        self.api.log.info("hi")
        self.assertTrue((self.dirs.logs / "plugin.log").is_file())
        self.assertFalse(self.dirs.data.exists())

    def test_log_error_reaches_captured_stderr(self):
        self.api.log.error("bad %s", "thing")
        self.api.log.info("quiet")
        self.assertEqual(self.err.getvalue(), "[demo] bad thing\n")


class EventsTest(Base):
    def test_emit_forwards(self):
        self.assertTrue(self.api.events.emit("progress", {"n": 1}))
        self.assertTrue(self.api.events.emit("tick"))
        self.assertEqual(self.rpc.emitted, [("progress", {"n": 1}), ("tick", None)])

    def test_rpc_refusal_is_returned(self):
        self.rpc.emit_result = False
        self.assertFalse(self.api.events.emit("x", 1))

    def test_name_rules(self):
        for bad in ("", "A", "has space", "x" * 41, "a/b", "é", None, 5, "a\n"):
            self.assertFalse(self.api.events.emit(bad, 1), repr(bad))
        self.assertTrue(self.api.events.emit("a-b_c.d9", 1))
        self.assertTrue(self.api.events.emit("x" * 40, 1))
        self.assertEqual(len(self.rpc.emitted), 2)

    def test_payload_rules(self):
        limit = 16384
        base = len(json.dumps({"s": ""}, separators=(",", ":")))
        self.assertTrue(self.api.events.emit("a", {"s": "x" * (limit - base)}))
        self.assertFalse(self.api.events.emit("a", {"s": "x" * (limit - base + 1)}))
        self.assertFalse(self.api.events.emit("a", {"s": object()}))
        self.assertFalse(self.api.events.emit("a", float("nan")))
        self.assertFalse(self.api.events.emit("a", {1, 2}))
        self.assertEqual(len(self.rpc.emitted), 1)

    def test_non_ascii_counts_as_wire_size(self):
        self.assertFalse(self.api.events.emit("a", {"s": "é" * 3000}))
        self.assertTrue(self.api.events.emit("a", {"s": "é" * 2000}))

    def test_rate_cap(self):
        api = self.make(max_events=5)
        now = [1000.0]
        with mock.patch.object(fy_sdk.time, "monotonic", lambda: now[0]):
            results = [api.events.emit("e", i) for i in range(12)]
            self.assertEqual(results, [True] * 5 + [False] * 7)
            now[0] += 0.9
            self.assertFalse(api.events.emit("e", 1))
            now[0] += 0.2
            self.assertTrue(api.events.emit("e", 1))
        self.assertEqual(len(self.rpc.emitted), 6)

    def test_default_rate_is_fifty_per_second(self):
        now = [1000.0]
        with mock.patch.object(fy_sdk.time, "monotonic", lambda: now[0]):
            ok = sum(self.api.events.emit("e", i) for i in range(80))
        self.assertEqual(ok, 50)

    def test_internal_hook_counts_each_sdk_rejection_once(self):
        self.rpc.event_dropped = mock.Mock()
        api = self.make(max_events=1)
        cycle = []
        cycle.append(cycle)
        for name, payload in (("Bad Name", 1), ("e", object()), ("e", cycle),
                              ("e", float("nan")), ("e", "x" * fy_sdk.EVENT_MAX)):
            self.assertFalse(api.events.emit(name, payload))
        with mock.patch.object(fy_sdk.time, "monotonic", return_value=1000.0):
            self.assertTrue(api.events.emit("e", 1))
            self.assertFalse(api.events.emit("e", 2))
            self.assertFalse(api.events.emit("e", 3))
        self.assertEqual(self.rpc.event_dropped.call_args_list, [mock.call(1)] * 7)
        self.assertEqual(self.rpc.emitted, [("e", 1)])

    def test_rpc_rejection_is_not_counted_by_the_sdk_hook(self):
        self.rpc.event_dropped = mock.Mock()
        self.rpc.emit_result = False
        self.assertFalse(self.api.events.emit("e", 1))
        self.rpc.event_dropped.assert_not_called()

    def test_drop_hook_is_optional_and_preserves_false_return(self):
        self.assertFalse(hasattr(self.rpc, "event_dropped"))
        self.assertFalse(self.api.events.emit("Bad Name"))
        self.rpc.event_dropped = None
        self.assertFalse(self.api.events.emit("Bad Name"))
        self.rpc.event_dropped = mock.Mock(side_effect=RuntimeError("hook unavailable"))
        self.assertFalse(self.api.events.emit("Bad Name"))
        self.rpc.event_dropped.assert_called_once_with(1)


class FetchTest(Base):
    def test_defaults_and_forwarding(self):
        reply = {"status": 200, "headers": {}, "text": "hi", "b64": None, "bytes": 2, "truncated": False, "path": None}
        self.rpc.replies["fetch"] = reply
        got = self.run_async(self.api.fetch("https://example.org/x"))
        self.assertEqual(got, reply)
        self.assertEqual(self.rpc.calls, [("fetch", {"url": "https://example.org/x", "method": "GET", "timeout": 15, "max_bytes": 524288})])

    def test_all_options(self):
        self.rpc.replies["fetch"] = {"status": 204}
        self.run_async(self.api.fetch("https://e.org", method="POST", headers={"A": "b"}, body="{}", timeout=3.5, max_bytes=10, dest="data:f.bin"))
        self.assertEqual(
            self.rpc.calls[0][1],
            {"url": "https://e.org", "method": "POST", "timeout": 3.5, "max_bytes": 10, "headers": {"A": "b"}, "body": "{}", "dest": "data:f.bin"},
        )

    def test_headers_accept_declared_mapping_contract(self):
        self.rpc.replies["fetch"] = {"status": 204}
        for headers in (types.MappingProxyType({"A": "b"}), collections.UserDict({"A": "b"})):
            self.run_async(self.api.fetch("https://e.org", headers=headers))
            sent = self.rpc.calls[-1][1]["headers"]
            self.assertIs(type(sent), dict)
            self.assertEqual(sent, {"A": "b"})
            self.assertIsNot(sent, headers)

    def test_positional_options_are_rejected(self):
        with self.assertRaises(TypeError):
            self.run_async(self.api.fetch("https://e.org", "POST"))

    def test_bad_arguments_never_reach_the_host(self):
        cases = [
            (5, {}),
            (None, {}),
            ("https://e.org", {"method": 5}),
            ("https://e.org", {"headers": [("a", "b")]}),
            ("https://e.org", {"body": b"bytes"}),
            ("https://e.org", {"dest": 3}),
            ("https://e.org", {"timeout": "5"}),
            ("https://e.org", {"max_bytes": 1.5}),
        ]
        for url, kw in cases:
            with self.assertRaises(fy_sdk.FrameyApiError) as cm:
                self.run_async(self.api.fetch(url, **kw))
            self.assertEqual(cm.exception.code, "bad_argument")
        self.assertEqual(self.rpc.calls, [])

    def test_host_exception_with_code(self):
        self.rpc.replies["fetch"] = HostFailure("blocked_address", "That address is not allowed")
        with self.assertRaises(fy_sdk.FrameyApiError) as cm:
            self.run_async(self.api.fetch("https://e.org"))
        self.assertEqual((cm.exception.code, cm.exception.message), ("blocked_address", "That address is not allowed"))
        self.assertEqual(str(cm.exception), "That address is not allowed")

    def test_plain_exception_is_wrapped(self):
        self.rpc.replies["fetch"] = RuntimeError("pipe closed")
        with self.assertRaises(fy_sdk.FrameyApiError) as cm:
            self.run_async(self.api.fetch("https://e.org"))
        self.assertEqual((cm.exception.code, cm.exception.message), ("host_error", "pipe closed"))

    def test_error_reply_shapes(self):
        for reply, expect in (
            ({"error": "too big", "code": "too_large"}, ("too_large", "too big")),
            ({"error": {"code": "busy", "message": "slow down"}}, ("busy", "slow down")),
            ({"error": "plain"}, ("host_error", "plain")),
        ):
            self.rpc.replies["fetch"] = reply
            with self.assertRaises(fy_sdk.FrameyApiError) as cm:
                self.run_async(self.api.fetch("https://e.org"))
            self.assertEqual((cm.exception.code, cm.exception.message), expect)

    def test_non_dict_reply(self):
        self.rpc.replies["fetch"] = "oops"
        with self.assertRaises(fy_sdk.FrameyApiError) as cm:
            self.run_async(self.api.fetch("https://e.org"))
        self.assertEqual(cm.exception.code, "bad_result")

    def test_cancellation_is_not_swallowed(self):
        self.rpc.replies["fetch"] = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.run_async(self.api.fetch("https://e.org"))

    def test_error_message_is_capped(self):
        self.rpc.replies["fetch"] = HostFailure("x", "m" * 1000)
        with self.assertRaises(fy_sdk.FrameyApiError) as cm:
            self.run_async(self.api.fetch("https://e.org"))
        self.assertEqual(len(cm.exception.message), 300)


class ResourceAndFilesTest(Base):
    def test_resource_url_and_revoke(self):
        self.rpc.replies["resource"] = lambda arg: {"url": "http://127.0.0.1:1/m/tok", "kind": "http", "expires": 1, "token": "tok"} if "root" in arg else {"revoked": True}
        url = self.run_async(self.api.resource_url("data", "a.mp4", 60))
        self.assertEqual(url, "http://127.0.0.1:1/m/tok")
        self.assertEqual(self.rpc.calls[0], ("resource", {"root": "data", "path": "a.mp4", "ttl": 60}))
        self.assertTrue(self.run_async(self.api.resource_revoke(url)))
        self.assertEqual(self.rpc.calls[1], ("resource", {"revoke": "tok"}))
        self.assertFalse(self.run_async(self.api.resource_revoke(url)))
        self.assertEqual(len(self.rpc.calls), 2)

    def test_default_ttl(self):
        self.rpc.replies["resource"] = {"url": "data:text/plain;base64,eA==", "kind": "data", "expires": 1, "token": None}
        self.run_async(self.api.resource_url("data", "a.txt"))
        self.assertEqual(self.rpc.calls[0][1]["ttl"], 600)

    def test_data_url_has_nothing_to_revoke(self):
        self.rpc.replies["resource"] = {"url": "data:image/png;base64,AA==", "kind": "data", "expires": 1, "token": None}
        url = self.run_async(self.api.resource_url("data", "a.png"))
        self.assertFalse(self.run_async(self.api.resource_revoke(url)))
        self.assertEqual(len(self.rpc.calls), 1)

    def test_unknown_url_is_not_revoked(self):
        self.assertFalse(self.run_async(self.api.resource_revoke("http://x/m/zzz")))
        self.assertEqual(self.rpc.calls, [])

    def test_revoke_reply_false_and_failed_call_keep_the_token(self):
        n = [0]

        def reply(arg):
            if "root" in arg:
                return {"url": "u1", "token": "t1"}
            n[0] += 1
            if n[0] == 1:
                raise HostFailure("busy", "later")
            return {"ok": n[0] > 2}

        self.rpc.replies["resource"] = reply
        self.run_async(self.api.resource_url("data", "a"))
        with self.assertRaises(fy_sdk.FrameyApiError):
            self.run_async(self.api.resource_revoke("u1"))
        self.assertFalse(self.run_async(self.api.resource_revoke("u1")))
        self.assertTrue(self.run_async(self.api.resource_revoke("u1")))
        self.assertFalse(self.run_async(self.api.resource_revoke("u1")))
        self.assertEqual([c[1] for c in self.rpc.calls if "revoke" in c[1]], [{"revoke": "t1"}] * 3)

    def test_revoke_does_not_forget_a_replacement_token(self):
        async def scenario():
            pending = asyncio.get_running_loop().create_future()
            started = asyncio.Event()
            minted = 0

            async def host(op, arg):
                nonlocal minted
                self.rpc.calls.append((op, arg))
                if "root" in arg:
                    minted += 1
                    return {"url": "u1", "token": "t%d" % minted}
                if arg["revoke"] == "t1":
                    started.set()
                    return await pending
                return {"ok": True}

            self.rpc.host = host
            await self.api.resource_url("data", "a")
            first = asyncio.create_task(self.api.resource_revoke("u1"))
            await started.wait()
            await self.api.resource_url("data", "a")
            pending.set_result({"ok": True})
            self.assertTrue(await first)
            self.assertTrue(await self.api.resource_revoke("u1"))
            self.assertEqual(self.rpc.calls[-1], ("resource", {"revoke": "t2"}))

        self.run_async(scenario())

    def test_token_map_is_bounded(self):
        counter = [0]

        def reply(arg):
            counter[0] += 1
            return {"url": "u%d" % counter[0], "token": "t%d" % counter[0]}

        self.rpc.replies["resource"] = reply
        for _ in range(100):
            self.run_async(self.api.resource_url("data", "a"))
        self.assertFalse(self.run_async(self.api.resource_revoke("u1")))
        self.assertEqual(len(self.rpc.calls), 100)
        self.assertTrue(self.run_async(self.api.resource_revoke("u100")))

    def test_bad_resource_reply(self):
        for bad in (None, "x", {"kind": "data"}, {"url": 5}):
            self.rpc.replies["resource"] = bad
            with self.assertRaises(fy_sdk.FrameyApiError) as cm:
                self.run_async(self.api.resource_url("data", "a"))
            self.assertEqual(cm.exception.code, "bad_result")

    def test_list_dir_forwards_only_given_options(self):
        listing = {"root": "home", "path": "", "parent": None, "entries": [], "truncated": False}
        self.rpc.replies["files"] = listing
        self.assertEqual(self.run_async(self.api.list_dir("home")), listing)
        self.run_async(self.api.list_dir("home", "Downloads", hidden=True, dirs_only=False, exts=("mp3", "ogg")))
        self.assertEqual(self.rpc.calls[0], ("files", {"root": "home", "path": ""}))
        self.assertEqual(self.rpc.calls[1], ("files", {"root": "home", "path": "Downloads", "hidden": True, "dirs_only": False, "exts": ["mp3", "ogg"]}))

    def test_list_dir_rejects_unknown_options_and_maps_errors(self):
        with self.assertRaises(TypeError):
            self.run_async(self.api.list_dir("home", bogus=1))
        self.rpc.replies["files"] = {"error": "That folder is outside the allowed locations", "code": "outside_root"}
        with self.assertRaises(fy_sdk.FrameyApiError) as cm:
            self.run_async(self.api.list_dir("home", "../x"))
        self.assertEqual(cm.exception.code, "outside_root")
        self.rpc.replies["files"] = [1]
        with self.assertRaises(fy_sdk.FrameyApiError):
            self.run_async(self.api.list_dir("home"))


class MigrateTest(Base):
    def test_keys_renames_in_settings_with_one_write(self):
        s = self.api.settings
        s.update({"old_a": 1, "old_b": 2, "keep": 3, "new_b": "mine"})
        with mock.patch.object(fy_store, "write_json_atomic", wraps=fy_store.write_json_atomic) as w:
            r = self.api.migrate.keys({"old_a": "new_a", "old_b": "new_b", "absent": "z"})
        self.assertEqual(r, {"renamed": [["old_a", "new_a"]]})
        self.assertEqual(w.call_count, 1)
        self.assertEqual(s.all(), {"new_a": 1, "old_b": 2, "keep": 3, "new_b": "mine"})
        with mock.patch.object(fy_store, "write_json_atomic") as w:
            self.assertEqual(self.api.migrate.keys({"old_a": "new_a", "old_b": "new_b"}), {"renamed": []})
        w.assert_not_called()

    def test_keys_crash_before_replace_changes_nothing(self):
        s = self.api.settings
        s.set("old", 1)
        with mock.patch.object(fy_store.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.api.migrate.keys({"old": "new"})
        self.assertEqual(s.all(), {"old": 1})
        self.assertEqual(self.api.migrate.keys({"old": "new"}), {"renamed": [["old", "new"]]})
        self.assertEqual(s.all(), {"new": 1})

    def test_keys_bad_mapping(self):
        with self.assertRaises(ValueError):
            self.api.migrate.keys({"a": 1})
        self.assertFalse(self.dirs.config.exists())

    def test_move_and_copy_once(self):
        src = self.tmp / "legacy" / "settings.json"
        src.parent.mkdir()
        src.write_text("{}")
        dst = self.dirs.data / "imported.json"
        self.assertEqual(self.api.migrate.copy_once(src, dst, ".done"), True)
        self.assertEqual(self.api.migrate.copy_once(src, dst, ".done"), False)
        self.assertTrue(src.exists())
        moved = self.dirs.data / "moved.json"
        self.assertEqual(self.api.migrate.move(src, moved), {"moved": True, "reason": "ok"})
        self.assertEqual(self.api.migrate.move(src, moved), {"moved": False, "reason": "missing_source"})
        self.assertEqual(self.api.migrate.move(dst, moved), {"moved": False, "reason": "exists"})
        self.assertTrue(self.api.migrate.move(dst, moved, overwrite=True)["moved"])

    def test_snapshot(self):
        self.assertIsNone(self.api.migrate.snapshot())
        self.api.settings.set("a", 1)
        snap = self.api.migrate.snapshot()
        self.assertEqual(snap, self.dirs.config / "settings.json.premigrate")
        self.api.settings.set("a", 2)
        self.assertEqual(fy_store.read_json(snap), {"a": 1})
        self.assertTrue(fy_store.restore_snapshot(self.dirs.config))
        self.assertEqual(self.api.settings.get("a"), 1)


class CompanionTest(Base):
    def install(self, folder, exe=None):
        d = self.dirs.data / "bin" / folder
        d.mkdir(parents=True)
        if exe:
            (d / exe).write_text("bin")
        return d

    def test_not_installed(self):
        with self.assertRaises(fy_sdk.FrameyApiError) as cm:
            self.api.companion("tool")
        self.assertEqual(cm.exception.code, "not_installed")
        self.install("tool-extra-1.0", "tool")
        self.install("toolbox-1.0", "tool")
        self.install("tool-v1", "tool")
        with self.assertRaises(fy_sdk.FrameyApiError):
            self.api.companion("tool")

    def test_returns_executable_when_present_else_folder(self):
        d = self.install("tool-1.2.0", "tool")
        self.assertEqual(self.api.companion("tool"), d / "tool")
        d2 = self.install("other-0.1", "run.sh")
        self.assertEqual(self.api.companion("other"), d2)

    def test_prefers_most_recently_installed(self):
        old = self.install("tool-1.0.0", "tool")
        new = self.install("tool-1.1.0", "tool")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        self.assertEqual(self.api.companion("tool"), new / "tool")
        os.utime(old, (3_000_000, 3_000_000))
        self.assertEqual(self.api.companion("tool"), old / "tool")

    def test_ignores_plain_files(self):
        (self.dirs.data / "bin").mkdir(parents=True)
        (self.dirs.data / "bin" / "tool-1.0").write_text("not a folder")
        with self.assertRaises(fy_sdk.FrameyApiError):
            self.api.companion("tool")

    def test_skips_entries_removed_during_the_scan(self):
        d = self.install("tool-1.0", "tool")
        with os.scandir(d.parent) as entries:
            good = list(entries)
        vanished_dir = mock.Mock(name="vanished_dir")
        vanished_dir.name = "tool-2.0"
        vanished_dir.is_dir.side_effect = FileNotFoundError()
        vanished_stat = mock.Mock(name="vanished_stat")
        vanished_stat.name = "tool-3.0"
        vanished_stat.is_dir.return_value = True
        vanished_stat.stat.side_effect = FileNotFoundError()
        with mock.patch.object(fy_sdk.os, "scandir", return_value=[vanished_dir, vanished_stat] + good):
            self.assertEqual(self.api.companion("tool"), d / "tool")

    def test_bad_names(self):
        for bad in ("", "../x", "a/b", "a b", None, 5, "x" * 65):
            with self.assertRaises(fy_sdk.FrameyApiError) as cm:
                self.api.companion(bad)
            self.assertEqual(cm.exception.code, "bad_argument")

    def test_does_not_create_directories(self):
        with self.assertRaises(fy_sdk.FrameyApiError):
            self.api.companion("tool")
        self.assertFalse(self.dirs.data.exists())

    def test_linked_folder_is_not_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "tool").write_text("evil")
        (self.dirs.data / "bin").mkdir(parents=True)
        try:
            link_dir(outside, self.dirs.data / "bin" / "tool-9.9")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no directory link could be created here; run on Linux: python3 -I -m unittest discover -s tests -p test_fy_sdk.py")
        if not (self.dirs.data / "bin" / "tool-9.9").is_symlink():
            self.skipTest("junction not reported as a symlink by this Python; run on Linux: python3 -I -m unittest discover -s tests -p test_fy_sdk.py")
        with self.assertRaises(fy_sdk.FrameyApiError):
            self.api.companion("tool")


class ErrorAndModuleTest(Base):
    def test_error_type(self):
        e = fy_sdk.FrameyApiError("not_installed", "x" * 500)
        self.assertEqual(e.code, "not_installed")
        self.assertEqual(len(e.message), 300)
        self.assertEqual(str(e), e.message)
        self.assertIsInstance(e, Exception)
        self.assertEqual(fy_sdk.FrameyApiError("c").message, "")

    def test_same_class_on_module(self):
        self.assertIs(self.api.FrameyApiError, fy_sdk.FrameyApiError)

    def test_module_is_importable_under_its_public_name(self):
        with mock.patch.dict(sys.modules, {"framey_api": self.api}):
            import framey_api

            self.assertIs(framey_api, self.api)
        self.assertEqual(self.api.__name__, "framey_api")

    def test_two_plugins_do_not_share_state(self):
        other_dirs = fy_store.dirs("other", home=self.tmp / "home", runtime_base=self.tmp / "run")
        with contextlib.redirect_stderr(io.StringIO()):
            other = fy_sdk.build("other", {"version": "9"}, other_dirs, self.rt, FakeRpc())
        self.api.settings.set("k", "demo")
        other.settings.set("k", "other")
        self.assertEqual(self.api.settings.get("k"), "demo")
        self.assertEqual((other.PLUGIN, other.runtime.version), ("other", "9"))
        self.assertNotEqual(self.api.dirs.config, other.dirs.config)

    def test_build_has_no_side_effects(self):
        before = set(os.listdir(self.tmp))
        self.make()
        self.assertEqual(set(os.listdir(self.tmp)), before)


if __name__ == "__main__":
    unittest.main()
