import asyncio
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fy_targets as ft
from test_fy_targets import FakeHost, NODE_HARNESS, run


class TargetValidation(unittest.TestCase):
    def test_malformed_settings_do_not_grant_custom_targets(self):
        for settings in (None, [], 7, {"prefs": []}, {"prefs": {"dev": []}}, {"prefs": {"dev": 1}}):
            with self.subTest(settings=settings):
                host = FakeHost()
                host.settings = lambda: settings
                self.assertEqual(ft.Targets(host).names(), ("main", "bar"))

    def test_custom_match_is_not_shortened_or_control_bearing(self):
        host = FakeHost(dev={"enabled": True, "targets": {
            "long": "x" * 121, "control": "a\nb", "surrogate": "\ud800", "fine": "x" * 120,
        }})
        targets = ft.Targets(host)
        self.assertEqual(targets.names(), ("main", "bar", "fine"))
        self.assertEqual(targets.urlpart("fine"), "x" * 120)
        self.assertIsNone(targets.urlpart("long"))

    def test_invalid_remove_filters_report_errors_without_mutation(self):
        host = FakeHost()
        run(ft.target_add(host, {"pid": "p", "target": "main", "kind": "css", "key": "k", "code": "x"}))
        before = dict(host.targets.items)
        for field, value, error in (("target", [], "unknown_target"), ("target", "main\n", "unknown_target"),
                                    ("key", [], "bad_key"), ("key", "", "bad_key"), ("pid", "", "bad_plugin")):
            with self.subTest(field=field, value=value):
                result = run(ft.target_remove(host, {"pid": "p", field: value}))
                self.assertEqual(result["code"], error)
                self.assertEqual(host.targets.items, before)

    def test_disabled_target_remains_removable(self):
        host = FakeHost(dev={"enabled": True, "targets": {"extra": "matched"}})
        targets = ft.get(host)
        run(targets.add_css("p", "extra", "k", "x"))
        host.prefs["dev"]["enabled"] = False
        self.assertEqual(run(ft.target_remove(host, {"pid": "p", "target": "extra", "key": "k"})), {"ok": True})
        self.assertEqual(targets.items, {})

    def test_send_main_only_host_is_supported(self):
        sent = []
        async def send_main(target, expression):
            sent.append((target, expression))
        host = types.SimpleNamespace(settings=lambda: {}, attached=lambda _: True,
                                     send_main=send_main, log=lambda *args: None)
        run(ft.Targets(host).add_css("p", "main", "k", "x"))
        self.assertEqual(len(sent), 1)

    def test_attachment_error_is_logged_and_registration_retained(self):
        host = FakeHost()
        def attached(target):
            raise RuntimeError("closed")
        host.attached = attached
        targets = ft.Targets(host)
        run(targets.add_css("p", "main", "k", "x"))
        self.assertEqual(len(targets.items), 1)
        self.assertEqual(len(host.logs), 1)


class TargetOrdering(unittest.IsolatedAsyncioTestCase):
    async def test_remove_cannot_overtake_an_inflight_add(self):
        host = FakeHost()
        targets = ft.Targets(host)
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(target, expression):
            if not entered.is_set():
                entered.set()
                await release.wait()
            host.sent.append((target, expression))
        host.send = send
        add = asyncio.create_task(targets.add_css("p", "main", "k", "old"))
        await asyncio.wait_for(entered.wait(), 1)
        remove = asyncio.create_task(targets.remove("p"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(add, remove), 1)
        self.assertIn('"old"', host.sent[0][1])
        self.assertEqual(host.sent[1][1], ft.remove_expr("css", "p", "k", "main"))
        self.assertEqual(targets.items, {})

    async def test_multi_page_removal_finishes_before_new_registration(self):
        host = FakeHost()
        targets = ft.Targets(host)
        await targets.add_css("p", "main", "k", "old")
        await targets.add_css("p", "bar", "k", "old")
        host.sent.clear()
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(target, expression):
            if target == "main" and not entered.is_set():
                entered.set()
                await release.wait()
            host.sent.append((target, expression))
        host.send = send
        remove = asyncio.create_task(targets.remove("p"))
        await asyncio.wait_for(entered.wait(), 1)
        add = asyncio.create_task(targets.add_css("p", "bar", "k", "new"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(remove, add), 1)
        self.assertEqual([name for name, _ in host.sent], ["main", "bar", "bar"])
        self.assertIn('"new"', host.sent[-1][1])
        self.assertEqual(targets.items[("p", "bar", "css", "k")][0], "new")

    async def test_reconnect_replay_finishes_before_queued_removal(self):
        host = FakeHost()
        targets = ft.Targets(host)
        await targets.add_js("p", "main", "k", "old()", "down()")
        host.sent.clear()
        expected = targets.reconcile_expr("main") + ";" + targets.replay("main")[0][0]
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(target, expression):
            if not entered.is_set():
                entered.set()
                await release.wait()
            host.sent.append((target, expression))
        host.send = send
        reconcile = asyncio.create_task(targets.reconcile("main"))
        await asyncio.wait_for(entered.wait(), 1)
        remove = asyncio.create_task(targets.remove("p"))
        await asyncio.sleep(0)
        self.assertIn(("p", "main", "js", "k"), targets.items)
        release.set()
        await asyncio.wait_for(asyncio.gather(reconcile, remove), 1)
        self.assertEqual(host.sent, [("main", expected), ("main", ft.remove_expr("js", "p", "k", "main"))])
        self.assertEqual(targets.items, {})

    async def test_reconnect_snapshot_waits_for_queued_replacement(self):
        host = FakeHost()
        targets = ft.Targets(host)
        await targets.add_css("p", "main", "k", "old")
        host.sent.clear()
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(target, expression):
            if not entered.is_set():
                entered.set()
                await release.wait()
            host.sent.append((target, expression))
        host.send = send
        first = asyncio.create_task(targets.add_css("p", "main", "k", "middle"))
        await asyncio.wait_for(entered.wait(), 1)
        replacement = asyncio.create_task(targets.add_css("p", "main", "k", "new"))
        await asyncio.sleep(0)
        reconcile = asyncio.create_task(targets.reconcile("main"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, replacement, reconcile), 1)
        self.assertEqual(len(host.sent), 3)
        self.assertEqual(host.sent[-1], ("main", targets.reconcile_expr("main") + ";" + targets.replay("main")[0][0]))
        self.assertIn('"new"', host.sent[-1][1])
        self.assertNotIn('"old"', host.sent[-1][1])
        self.assertNotIn('"middle"', host.sent[-1][1])

    async def test_reconnect_unknown_target_is_inert(self):
        host = FakeHost()
        targets = ft.Targets(host)
        await targets.reconcile("unknown")
        self.assertEqual(host.sent, [])
        await targets.reconcile("main")
        self.assertEqual(host.sent, [("main", targets.reconcile_expr("main"))])

    async def test_hung_send_is_bounded_and_next_operation_proceeds(self):
        host = FakeHost()
        targets = ft.Targets(host)
        entered = asyncio.Event()
        async def send(target, expression):
            if not entered.is_set():
                entered.set()
                await asyncio.Event().wait()
            host.sent.append((target, expression))
        host.send = send
        with mock.patch.object(ft, "SEND_TIMEOUT", 0.02):
            add = asyncio.create_task(targets.add_css("p", "main", "k", "old"))
            await asyncio.wait_for(entered.wait(), 1)
            remove = asyncio.create_task(targets.remove("p"))
            await asyncio.wait_for(asyncio.gather(add, remove), 1)
        self.assertEqual(targets.items, {})
        self.assertEqual(len(host.logs), 1)
        self.assertEqual(host.sent, [("main", ft.remove_expr("css", "p", "k", "main"))])

    async def test_cancellation_releases_sending_lock(self):
        host = FakeHost()
        targets = ft.Targets(host)
        entered = asyncio.Event()
        async def send(target, expression):
            entered.set()
            await asyncio.Event().wait()
        host.send = send
        task = asyncio.create_task(targets.add_css("p", "main", "k", "old"))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        host.send = FakeHost.send.__get__(host)
        await asyncio.wait_for(targets.remove("p"), 1)
        self.assertEqual(targets.items, {})


@unittest.skipUnless(shutil.which("node"), "node not available")
class TargetReconciliation(unittest.TestCase):
    def evaluate(self, steps):
        harness = NODE_HARNESS.replace('styles: nodes.map', 'scopes: nodes.map(n => n.attrs["data-fy-scope"] || ""), targets: Object.keys(ctx.__framey_targets || {}).sort(), styles: nodes.map')
        result = subprocess.run(["node", "-e", harness], input=json.dumps(steps), capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_reconcile_prunes_stale_slots_preserves_live_and_other_scope(self):
        host = FakeHost(attached=())
        targets = ft.Targets(host)
        run(targets.add_css("p", "main", "keep", "live"))
        run(targets.add_js("p", "main", "keep", "globalThis.hit=1", "globalThis.td=(globalThis.td||0)+1"))
        steps = [
            ft.add_expr("css", "gone", "old", "stale", None, "main"),
            ft.add_expr("js", "gone", "old", "1", "globalThis.td=(globalThis.td||0)+1", "main"),
            ft.add_expr("css", "q", "other", "other", None, "bar"),
            ft.add_expr("js", "q", "other", "1", "globalThis.td=99", "bar"),
        ] + [expression for expression, _ in targets.replay("main")]
        steps += [targets.reconcile_expr("main"), targets.reconcile_expr("main")]
        out = self.evaluate(steps)
        self.assertEqual(out[-1], out[-2])
        self.assertEqual(out[-1]["td"], 1)
        self.assertEqual(out[-1]["styles"], [["q", "other", "other"], ["p", "keep", "live"]])
        self.assertEqual(out[-1]["targets"], ["bar|q|other", "main|p|keep"])
        self.assertEqual(out[-1]["hit"], 1)

    def test_reconcile_cleans_detached_removal_before_replay(self):
        host = FakeHost(attached=())
        targets = ft.Targets(host)
        run(targets.add_js("p", "main", "k", "globalThis.hit=(globalThis.hit||0)+1", "globalThis.td=(globalThis.td||0)+1"))
        old = targets.replay("main")[0][0]
        run(targets.remove("p"))
        out = self.evaluate([old, targets.reconcile_expr("main"), targets.reconcile_expr("main")])
        self.assertEqual(out[-1]["targets"], [])
        self.assertEqual(out[-1]["td"], 1)
        self.assertEqual(out[-1], out[-2])

    def test_scoped_add_remove_does_not_collide_on_same_document(self):
        out = self.evaluate([
            ft.add_expr("css", "p", "k", "main", None, "main"),
            ft.add_expr("css", "p", "k", "bar", None, "bar"),
            ft.add_expr("js", "p", "k", "1", "globalThis.td=(globalThis.td||0)+1", "main"),
            ft.add_expr("js", "p", "k", "1", "globalThis.td=99", "bar"),
            ft.remove_expr("css", "p", "k", "main"),
            ft.remove_expr("js", "p", "k", "main"),
        ])
        self.assertEqual(out[-1]["scopes"], ["bar"])
        self.assertEqual(out[-1]["targets"], ["bar|p|k"])
        self.assertEqual(out[-1]["td"], 1)

    def test_legacy_entries_are_adopted_and_orphans_are_pruned(self):
        targets = ft.Targets(FakeHost(attached=()))
        run(targets.add_css("p", "main", "k", "new"))
        run(targets.add_js("p", "main", "k", "globalThis.hit=1", "globalThis.td=2"))
        steps = [
            ft.add_expr("css", "p", "k", "legacy", None),
            ft.add_expr("js", "p", "k", "1", "globalThis.td=(globalThis.td||0)+1"),
            ft.add_expr("css", "gone", "old", "orphan", None),
            ft.add_expr("js", "gone", "old", "1", "throw Error('ignored')"),
            targets.reconcile_expr("main"),
        ] + [expression for expression, _ in targets.replay("main")]
        out = self.evaluate(steps)
        self.assertEqual(out[-1]["styles"], [["p", "k", "new"]])
        self.assertEqual(out[-1]["scopes"], ["main"])
        self.assertEqual(out[-1]["targets"], ["main|p|k"])
        self.assertEqual(out[-1]["td"], 1)

    def test_live_legacy_slot_of_other_target_survives_pruning(self):
        targets = ft.Targets(FakeHost(attached=()))
        run(targets.add_css("p", "bar", "k", "keep"))
        run(targets.add_js("p", "bar", "k", "1", "globalThis.td=99"))
        out = self.evaluate([
            ft.add_expr("css", "p", "k", "legacy", None),
            ft.add_expr("js", "p", "k", "1", "globalThis.td=99"),
            targets.reconcile_expr("main"),
        ])
        self.assertEqual(out[-1]["styles"], [["p", "k", "legacy"]])
        self.assertEqual(out[-1]["targets"], ["p|k"])
        self.assertEqual(out[-1]["td"], 0)

    def test_reconcile_unknown_target_is_inert_and_snapshot_is_pure(self):
        targets = ft.Targets(FakeHost(attached=()))
        run(targets.add_css("p", "main", "k", "x"))
        before = dict(targets.items)
        self.assertEqual(targets.reconcile_expr("unknown"), "")
        self.assertEqual(targets.reconcile_expr("main"), targets.reconcile_expr("main"))
        self.assertEqual(targets.items, before)
        self.assertEqual(targets.host.sent, [])


if __name__ == "__main__":
    unittest.main()
