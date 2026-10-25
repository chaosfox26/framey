import asyncio
import json
import os
import shutil
import subprocess
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fy_targets as ft

HOSTILE = "a\"b'c\\d\n\r  </style></script>*/`${x}\x00\ud800end"

NODE_HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const nodes = [];
const mk = () => {
  const a = {};
  return {
    attrs: a,
    textContent: "",
    setAttribute(k, v) { a[k] = String(v); },
    get dataset() { return { fyOwner: a["data-fy-owner"], fyKey: a["data-fy-key"] }; },
    remove() { const i = nodes.indexOf(this); if (i >= 0) nodes.splice(i, 1); },
  };
};
const document = {
  head: { appendChild(n) { nodes.push(n); } },
  createElement: () => mk(),
  querySelectorAll: () => nodes.slice(),
};
const ctx = { document };
ctx.window = ctx;
vm.createContext(ctx);
const out = [];
for (const step of input) {
  vm.runInContext(step, ctx);
  out.push({ styles: nodes.map(n => [n.attrs["data-fy"], n.attrs["data-fy-key"], n.textContent]), hit: ctx.hit || 0, td: ctx.td || 0, s: ctx.s === undefined ? null : ctx.s });
}
process.stdout.write(JSON.stringify(out));
"""


class FakeHost:
    def __init__(self, attached=("main", "bar"), dev=None):
        self.sent = []
        self.logs = []
        self.live = set(attached)
        self.prefs = {"dev": dev or {"enabled": False, "targets": {}}}
        self.fail = False

    def settings(self):
        return {"prefs": self.prefs}

    def attached(self, target):
        return target in self.live

    async def send(self, target, expression):
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append((target, expression))

    def log(self, *a):
        self.logs.append(a)


def run(coro):
    return asyncio.run(coro)


class Caps(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.t = ft.Targets(self.host)

    def test_item_count_cap(self):
        for i in range(8):
            run(self.t.add_css("p", "main", "k%d" % i, "a{}"))
        with self.assertRaises(ft.TargetError) as c:
            run(self.t.add_css("p", "main", "k8", "a{}"))
        self.assertEqual(c.exception.code, "too_many")
        run(self.t.add_css("p", "main", "k0", "b{}"))
        self.assertEqual(len([k for k in self.t.items if k[0] == "p"]), 8)
        run(self.t.add_css("q", "main", "k0", "a{}"))

    def test_count_spans_targets_and_kinds(self):
        for i in range(4):
            run(self.t.add_css("p", "main", "c%d" % i, "a{}"))
            run(self.t.add_js("p", "bar", "j%d" % i, "1"))
        with self.assertRaises(ft.TargetError):
            run(self.t.add_js("p", "main", "extra", "1"))

    def test_item_size_cap(self):
        run(self.t.add_css("p", "main", "ok", "x" * ft.MAX_ITEM))
        with self.assertRaises(ft.TargetError) as c:
            run(self.t.add_css("p", "main", "big", "x" * (ft.MAX_ITEM + 1)))
        self.assertEqual(c.exception.code, "too_large")
        with self.assertRaises(ft.TargetError):
            run(self.t.add_js("p", "main", "big2", "x" * 40000, "y" * 30000))

    def test_size_counts_bytes(self):
        with self.assertRaises(ft.TargetError):
            run(self.t.add_css("p", "main", "u", "é" * (ft.MAX_ITEM // 2 + 1)))

    def test_plugin_total_cap(self):
        for i in range(4):
            run(self.t.add_css("p", "main", "k%d" % i, "x" * ft.MAX_ITEM))
        with self.assertRaises(ft.TargetError) as c:
            run(self.t.add_css("p", "main", "k4", "x"))
        self.assertEqual(c.exception.code, "too_large")
        run(self.t.add_css("p", "main", "k0", "y"))
        run(self.t.add_css("p", "main", "k4", "x" * 1000))
        run(self.t.add_css("other", "main", "k0", "x" * ft.MAX_ITEM))

    def test_plugin_count_cap(self):
        for i in range(ft.MAX_PLUGINS):
            run(self.t.add_css("p%d" % i, "main", "k", "a{}"))
        with self.assertRaises(ft.TargetError):
            run(self.t.add_css("late", "main", "k", "a{}"))
        run(self.t.add_css("p0", "main", "k2", "a{}"))
        run(self.t.remove("p1"))
        run(self.t.add_css("late", "main", "k", "a{}"))

    def test_failed_add_registers_nothing(self):
        with self.assertRaises(ft.TargetError):
            run(self.t.add_css("p", "main", "bad key", "a{}"))
        self.assertEqual(self.t.items, {})
        self.assertEqual(self.host.sent, [])


class Validation(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.t = ft.Targets(self.host)

    def bad(self, code, *args):
        with self.assertRaises(ft.TargetError) as c:
            run(self.t.add(*args))
        self.assertEqual(c.exception.code, code)
        self.assertEqual(self.t.items, {})

    def test_unknown_target(self):
        self.bad("unknown_target", "p", "store", "css", "k", "a{}")
        self.bad("unknown_target", "p", "", "css", "k", "a{}")
        self.bad("unknown_target", "p", None, "css", "k", "a{}")
        self.bad("unknown_target", "p", ["main"], "css", "k", "a{}")
        self.bad("unknown_target", "p", "main\n", "css", "k", "a{}")

    def test_bad_fields(self):
        self.bad("bad_plugin", None, "main", "css", "k", "a{}")
        self.bad("bad_plugin", "Bad Id", "main", "css", "k", "a{}")
        self.bad("bad_kind", "p", "main", "html", "k", "a{}")
        self.bad("bad_key", "p", "main", "css", "", "a{}")
        self.bad("bad_key", "p", "main", "css", "k" * 41, "a{}")
        self.bad("bad_key", "p", "main", "css", 5, "a{}")
        self.bad("bad_code", "p", "main", "css", "k", 5)
        self.bad("bad_code", "p", "main", "js", "k", "1", 7)

    def test_dev_targets_gated(self):
        self.bad("unknown_target", "p", "settings", "css", "k", "a{}")
        self.host.prefs["dev"] = {"enabled": False, "targets": {"settings": "gamepadui.settings"}}
        self.bad("unknown_target", "p", "settings", "css", "k", "a{}")
        self.host.prefs["dev"] = {"enabled": True, "targets": {"settings": "gamepadui.settings"}}
        run(self.t.add_css("p", "settings", "k", "a{}"))
        self.assertEqual(self.t.urlpart("settings"), "gamepadui.settings")
        self.assertEqual(self.t.wanted(), ["settings"])

    def test_dev_table_garbage(self):
        self.host.prefs["dev"] = {"enabled": True, "targets": {"main": "x", "Bad Name": "x", "ok": "", "fine": "u", "n": 5}}
        self.assertEqual(self.t.names(), ("main", "bar", "fine"))
        self.host.prefs["dev"] = {"enabled": True, "targets": ["a"]}
        self.assertEqual(self.t.names(), ("main", "bar"))
        self.host.prefs = {}
        self.assertEqual(self.t.names(), ("main", "bar"))
        self.host.prefs = {"dev": {"enabled": "yes", "targets": {"a": "u"}}}
        self.assertEqual(self.t.names(), ("main", "bar"))

    def test_dev_off_stops_replay_and_wanted(self):
        self.host.prefs["dev"] = {"enabled": True, "targets": {"settings": "u"}}
        run(self.t.add_css("p", "settings", "k", "a{}"))
        self.assertEqual(len(self.t.replay("settings")), 1)
        self.host.prefs["dev"]["enabled"] = False
        self.assertEqual(self.t.replay("settings"), [])
        self.assertEqual(self.t.wanted(), [])

    def test_methods_table(self):
        self.assertEqual(sorted(ft.METHODS), ["target_add", "target_remove"])
        r = run(ft.METHODS["target_add"](self.host, {"pid": "p", "target": "main", "kind": "css", "key": "k", "code": "a{}"}))
        self.assertEqual(r, {"ok": True})
        self.assertIs(ft.get(self.host), ft.get(self.host))
        r = run(ft.METHODS["target_add"](self.host, {"pid": "p", "target": "nope", "kind": "css", "key": "k", "code": "a{}"}))
        self.assertEqual(r["code"], "unknown_target")
        self.assertIn("error", r)
        self.assertEqual(run(ft.METHODS["target_add"](self.host, "junk"))["code"], "bad_plugin")
        self.assertEqual(run(ft.METHODS["target_add"](self.host, None))["code"], "bad_plugin")
        self.assertEqual(run(ft.METHODS["target_remove"](self.host, {}))["code"], "bad_plugin")
        self.assertEqual(run(ft.METHODS["target_remove"](self.host, {"pid": "p"})), {"ok": True})
        self.assertEqual(ft.get(self.host).items, {})


class Collision(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.t = ft.Targets(self.host)

    def test_same_key_replaces(self):
        run(self.t.add_css("p", "main", "k", "one"))
        run(self.t.add_css("p", "main", "k", "two"))
        self.assertEqual(len(self.t.items), 1)
        self.assertEqual(self.t.items[("p", "main", "css", "k")][0], "two")
        self.assertEqual(len(self.t.replay("main")), 1)
        self.assertIn(json.dumps("two"), self.t.replay("main")[0][0])

    def test_plugins_do_not_collide_or_overwrite(self):
        run(self.t.add_css("p", "main", "k", "mine"))
        run(self.t.add_css("q", "main", "k", "theirs"))
        self.assertEqual(self.t.items[("p", "main", "css", "k")][0], "mine")
        self.assertEqual(self.t.items[("q", "main", "css", "k")][0], "theirs")
        run(self.t.remove("q", "main", "k"))
        self.assertEqual(self.t.items[("p", "main", "css", "k")][0], "mine")

    def test_kinds_and_targets_are_separate_slots(self):
        run(self.t.add_css("p", "main", "k", "a{}"))
        run(self.t.add_js("p", "main", "k", "1"))
        run(self.t.add_css("p", "bar", "k", "a{}"))
        self.assertEqual(len(self.t.items), 3)


class Sending(unittest.TestCase):
    def test_sent_when_attached(self):
        host = FakeHost()
        t = ft.Targets(host)
        run(t.add_css("p", "bar", "k", "a{}"))
        self.assertEqual(len(host.sent), 1)
        self.assertEqual(host.sent[0][0], "bar")
        self.assertIn(json.dumps("a{}"), host.sent[0][1])

    def test_not_sent_when_detached_but_remembered(self):
        host = FakeHost(attached=())
        t = ft.Targets(host)
        run(t.add_js("p", "main", "k", "1", "2"))
        self.assertEqual(host.sent, [])
        self.assertEqual(len(t.items), 1)
        self.assertEqual(len(t.replay("main")), 1)

    def test_send_failure_is_logged_not_raised(self):
        host = FakeHost()
        host.fail = True
        t = ft.Targets(host)
        run(t.add_css("p", "main", "k", "a{}"))
        self.assertEqual(len(t.items), 1)
        self.assertTrue(host.logs)


class Replay(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(attached=())
        self.t = ft.Targets(self.host)

    def test_replay_only_matching_target_with_pid(self):
        run(self.t.add_css("p", "main", "a", "x{}"))
        run(self.t.add_js("q", "main", "b", "1", "2"))
        run(self.t.add_css("p", "bar", "c", "y{}"))
        main = self.t.replay("main")
        self.assertEqual([pid for _, pid in main], ["p", "q"])
        self.assertEqual([pid for _, pid in self.t.replay("bar")], ["p"])
        self.assertEqual(self.t.replay("settings"), [])
        self.assertEqual(self.t.replay("nope"), [])

    def test_replay_matches_original_expression(self):
        run(self.t.add_js("p", "main", "k", "code()", "down()"))
        expr, pid = self.t.replay("main")[0]
        self.assertEqual(expr, ft.add_expr("js", "p", "k", "code()", "down()"))

    def test_replay_after_reattach_sequence(self):
        run(self.t.add_css("p", "main", "a", "x{}"))
        first = self.t.replay("main")
        second = self.t.replay("main")
        self.assertEqual(first, second)
        run(self.t.remove("p"))
        self.assertEqual(self.t.replay("main"), [])

    def test_replay_is_pure(self):
        run(self.t.add_css("p", "main", "a", "x{}"))
        before = dict(self.t.items)
        self.t.replay("main")
        self.assertEqual(before, self.t.items)
        self.assertEqual(self.host.sent, [])


class Removal(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.t = ft.Targets(self.host)
        run(self.t.add_css("p", "main", "a", "x{}"))
        run(self.t.add_js("p", "main", "b", "1", "2"))
        run(self.t.add_css("p", "bar", "a", "y{}"))
        run(self.t.add_css("q", "main", "a", "z{}"))
        self.host.sent.clear()

    def test_remove_one_key(self):
        self.assertEqual(run(self.t.remove("p", "main", "a")), 1)
        self.assertNotIn(("p", "main", "css", "a"), self.t.items)
        self.assertIn(("p", "main", "js", "b"), self.t.items)
        self.assertEqual(len(self.host.sent), 1)

    def test_remove_by_target_and_all(self):
        self.assertEqual(run(self.t.remove("p", "main")), 2)
        self.assertEqual(run(self.t.remove("p")), 1)
        self.assertEqual({k[0] for k in self.t.items}, {"q"})

    def test_remove_is_idempotent_and_silent_second_time(self):
        self.assertEqual(run(self.t.remove("p")), 3)
        sent = len(self.host.sent)
        self.assertEqual(run(self.t.remove("p")), 0)
        self.assertEqual(len(self.host.sent), sent)
        self.assertEqual(run(self.t.remove("nobody")), 0)

    def test_ownership(self):
        run(self.t.remove_plugin("p"))
        self.assertEqual(list(self.t.items), [("q", "main", "css", "a")])
        self.assertEqual(run(self.t.remove("p", None, "a")), 0)

    def test_one_send_per_target(self):
        run(self.t.remove("p"))
        self.assertEqual(sorted(t for t, _ in self.host.sent), ["bar", "main"])

    def test_remove_all(self):
        run(self.t.remove_all())
        self.assertEqual(self.t.items, {})
        run(self.t.remove_all())

    def test_detached_removal_does_not_send(self):
        self.host.live = set()
        self.assertEqual(run(self.t.remove("p")), 3)
        self.assertEqual(self.host.sent, [])

    def test_removal_expression_text(self):
        css = ft.remove_expr("css", "p", "a")
        js = ft.remove_expr("js", "p", "b")
        self.assertTrue(css.endswith('("p","a")'))
        self.assertTrue(js.endswith('("p","b")'))


class Ownership(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.t = ft.Targets(self.host)

    def test_slots_are_keyed_on_the_passed_plugin_id(self):
        run(self.t.add_css("alpha", "main", "k", "a{}"))
        run(self.t.add_css("beta", "main", "k", "b{}"))
        self.assertEqual(sorted(k[0] for k in self.t.items), ["alpha", "beta"])
        self.assertEqual(self.t.items[("alpha", "main", "css", "k")][0], "a{}")
        self.assertEqual(run(self.t.remove("beta")), 1)
        self.assertEqual(list(self.t.items), [("alpha", "main", "css", "k")])

    def test_one_plugin_cannot_use_up_the_quota_of_another(self):
        for i in range(8):
            run(self.t.add_css("beta", "main", "k%d" % i, "x"))
        with self.assertRaises(ft.TargetError):
            run(self.t.add_css("beta", "main", "k8", "x"))
        run(self.t.add_css("alpha", "main", "k", "x"))
        self.assertEqual(len([k for k in self.t.items if k[0] == "alpha"]), 1)

    def test_methods_act_only_on_the_pid_the_caller_passes(self):
        run(ft.METHODS["target_add"](self.host, {"pid": "alpha", "target": "main", "kind": "css", "key": "k", "code": "a{}"}))
        run(ft.METHODS["target_add"](self.host, {"pid": "beta", "target": "main", "kind": "css", "key": "k", "code": "b{}"}))
        run(ft.METHODS["target_remove"](self.host, {"pid": "beta"}))
        self.assertEqual([k[0] for k in self.host.targets.items], ["alpha"])
        self.assertEqual(run(ft.METHODS["target_remove"](self.host, {}))["code"], "bad_plugin")


class Quoting(unittest.TestCase):
    def test_hostile_strings_are_json_literals(self):
        for kind in ("css", "js"):
            expr = ft.add_expr(kind, "p", "k.1", HOSTILE, HOSTILE)
            self.assertIn(json.dumps(HOSTILE), expr)
            self.assertNotIn("\n", expr.split(ft.JS_ADD if kind == "js" else ft.CSS_ADD)[1])
            self.assertTrue(expr.isascii())
            for ch in (" ", " ", "\x00", "\r"):
                self.assertNotIn(ch, expr)

    def test_expression_is_call_of_fixed_template(self):
        expr = ft.add_expr("js", "p", "k", HOSTILE, HOSTILE)
        self.assertTrue(expr.startswith(ft.JS_ADD + "("))
        self.assertEqual(expr[len(ft.JS_ADD):], "(" + ",".join(json.dumps(x) for x in ("p", "k", HOSTILE, HOSTILE)) + ")")

    def test_no_teardown_becomes_empty_string(self):
        self.assertTrue(ft.add_expr("js", "p", "k", "1", None).endswith(',"1","")'))

    def test_css_never_carries_teardown(self):
        host = FakeHost()
        t = ft.Targets(host)
        run(t.add("p", "main", "css", "k", "a{}", "ignored()"))
        self.assertIsNone(t.items[("p", "main", "css", "k")][1])
        self.assertNotIn("ignored", host.sent[0][1])


@unittest.skipUnless(shutil.which("node"), "node not available")
class NodeEvaluation(unittest.TestCase):
    def evaluate(self, steps):
        r = subprocess.run(["node", "-e", NODE_HARNESS], input=json.dumps(steps), capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_css_add_replace_remove_idempotent(self):
        add1 = ft.add_expr("css", "p", "k", "first", None)
        add2 = ft.add_expr("css", "p", "k", HOSTILE, None)
        other = ft.add_expr("css", "q", "k", "other", None)
        rm = ft.remove_expr("css", "p", "k")
        out = self.evaluate([add1, add1, add2, other, rm, rm, rm])
        self.assertEqual(out[0]["styles"], [["p", "k", "first"]])
        self.assertEqual(out[1]["styles"], [["p", "k", "first"]])
        self.assertEqual(out[2]["styles"], [["p", "k", HOSTILE]])
        self.assertEqual(len(out[3]["styles"]), 2)
        self.assertEqual(out[4]["styles"], [["q", "k", "other"]])
        self.assertEqual(out[5], out[4])
        self.assertEqual(out[6], out[4])

    def test_js_add_replace_teardown_remove_idempotent(self):
        code = "globalThis.hit=(globalThis.hit||0)+1"
        down = "globalThis.td=(globalThis.td||0)+1"
        add = ft.add_expr("js", "p", "k", code, down)
        rm = ft.remove_expr("js", "p", "k")
        out = self.evaluate([add, add, rm, rm, rm, add, rm])
        got = [(o["hit"], o["td"]) for o in out]
        self.assertEqual(got, [(1, 0), (2, 1), (2, 2), (2, 2), (2, 2), (3, 2), (3, 3)])

    def test_js_without_teardown_and_missing_removal(self):
        out = self.evaluate([ft.remove_expr("js", "p", "k"), ft.add_expr("js", "p", "k", "globalThis.hit=1", None), ft.remove_expr("js", "p", "k"), ft.remove_expr("css", "p", "k")])
        self.assertEqual([o["hit"] for o in out], [0, 1, 1, 1])

    def test_hostile_js_string_round_trip(self):
        code = "globalThis.s=" + json.dumps(HOSTILE)
        out = self.evaluate([ft.add_expr("js", "p", "k", code, None)])
        self.assertEqual(out[0]["s"], HOSTILE)


if __name__ == "__main__":
    unittest.main()
