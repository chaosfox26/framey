import asyncio
import json
import shutil
import subprocess
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_steam_data as steam_data
from fy_steam_data import SteamReadProvider


def wire(value):
    return {"result": {"type": "object", "value": value}}


class FakeTransport:
    """Fake only the external CDP boundary; no network or device connection."""
    def __init__(self, pages=None):
        self.pages = pages if pages is not None else ["main", "full", "shared"]
        self.current = set(self.pages)
        self.results = {"main": {"apps": False, "focus": False},
                        "full": {"apps": True, "focus": False},
                        "shared": {"apps": False, "focus": True}}
        self.value = {"status": "empty", "focusedAppId": None,
                      "focusedWindowId": 0, "focusedOverlayAppId": "0", "focusedOverlayPid": ""}
        self.after = None
        self.failed = False

    async def list_targets(self):
        return tuple(self.pages)

    def is_current(self, ref):
        return ref in self.current

    async def evaluate(self, ref, expression):
        if self.failed:
            raise ConnectionError("PRIVATE_URL_DO_NOT_LEAK")
        if "framey-steam-probe-v1" in expression:
            return wire(self.results.get(ref, {"apps": False, "focus": False}))
        if self.after:
            self.after()
        return wire(self.value)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_selection_reads_empty_focus_without_scene_inference(self):
        result = await SteamReadProvider(FakeTransport()).read("focus")
        self.assertEqual(result, {"ok": True, "available": True, "source": "steam-focus-cache", "data": {
            "status": "empty", "focusedAppId": None, "focusedWindowId": 0,
            "focusedOverlayAppId": "0", "focusedOverlayPid": ""}})

    async def test_missing_context_is_explicit(self):
        self.assertEqual((await SteamReadProvider(FakeTransport([])).read("focus"))["code"], "target_unavailable")

    async def test_unknown_operation_rejects_arbitrary_js(self):
        self.assertEqual((await SteamReadProvider(FakeTransport()).read("window.alert(1)"))["code"], "unknown_operation")

    async def test_appid_requires_positive_uint32_integer(self):
        for value in (True, 0, -1, 1.5, "42", None, 2**32):
            with self.subTest(value=value):
                result = await SteamReadProvider(FakeTransport()).read("app", {"appId": value})
                self.assertEqual(result["code"], "invalid_arguments")

    async def test_extra_args_reject_raw_expression_and_target_authority(self):
        for args in ({"expression": "1"}, {"target": "full"}, {"appId": 42}, []):
            self.assertEqual((await SteamReadProvider(FakeTransport()).read("focus", args))["code"], "invalid_arguments")

    async def test_app_null_is_not_found_not_provider_missing(self):
        t = FakeTransport(); t.value = {"status": "not_found", "app": None}
        self.assertEqual((await SteamReadProvider(t).read("app", {"appId": 42})).get("data"), t.value)

    async def test_visible_library_empty_and_truncation_are_preserved(self):
        t = FakeTransport(); t.value = {"status": "empty", "apps": [], "visibleCount": 0, "truncated": False}
        self.assertEqual((await SteamReadProvider(t).read("visibleApps")).get("data"), t.value)

    async def test_ambiguous_capability_fails_closed(self):
        t = FakeTransport(); t.results["main"] = {"apps": True, "focus": False}
        self.assertEqual((await SteamReadProvider(t).read("visibleApps"))["code"], "ambiguous_context")

    async def test_target_close_during_read_discards_result(self):
        t = FakeTransport(); t.after = lambda: t.current.remove("shared")
        self.assertEqual((await SteamReadProvider(t).read("focus"))["code"], "stale_context")

    async def test_provider_disposal_during_read_discards_result(self):
        t = FakeTransport(); p = SteamReadProvider(t); t.after = p.dispose
        self.assertEqual((await p.read("focus"))["code"], "provider_disposed")

    async def test_epoch_invalidation_during_read_discards_result(self):
        t = FakeTransport(); p = SteamReadProvider(t); t.after = p.invalidate
        self.assertEqual((await p.read("focus"))["code"], "stale_context")

    async def test_errors_are_sanitized_and_do_not_become_empty_success(self):
        t = FakeTransport(); t.failed = True
        self.assertEqual(await SteamReadProvider(t).read("focus"), {"ok": False, "available": False, "code": "context_unavailable"})

    async def test_bad_focus_scalar_rejected(self):
        for bad in (False, {}, [], float("nan")):
            t = FakeTransport(); t.value["focusedAppId"] = bad
            self.assertEqual((await SteamReadProvider(t).read("focus"))["code"], "invalid_provider_result")

    async def test_rejects_mismatched_status_and_unknown_output_fields(self):
        t = FakeTransport(); t.value["status"] = "present"
        self.assertEqual((await SteamReadProvider(t).read("focus"))["code"], "invalid_provider_result")
        t.value["status"] = "empty"; t.value["name"] = "PRIVATE"
        self.assertEqual((await SteamReadProvider(t).read("focus"))["code"], "invalid_provider_result")

    async def test_lookup_result_must_match_requested_appid(self):
        t = FakeTransport(); t.value = {"status": "present", "app": {"appId": 43,
            "steamDeckCategory": 3, "steamOsCategory": 0, "steamMachineCategory": 1, "steamFrameCategory": 2}}
        self.assertEqual((await SteamReadProvider(t).read("app", {"appId": 42}))["code"], "invalid_provider_result")

    async def test_timeout_is_explicit(self):
        class Slow(FakeTransport):
            async def list_targets(self):
                await asyncio.sleep(1)
        self.assertEqual((await SteamReadProvider(Slow(), timeout=0.01).read("focus"))["code"], "provider_timeout")

    async def test_failed_unknown_probe_does_not_hide_ambiguous_context(self):
        class FailedOne(FakeTransport):
            async def evaluate(self, ref, expression):
                if ref == "main":
                    raise ConnectionError("unknown context")
                return await super().evaluate(ref, expression)
        self.assertEqual((await SteamReadProvider(FailedOne()).read("focus")).get("code"), "context_unavailable")

    async def test_cdp_exception_and_remote_reference_do_not_escape(self):
        class BadWire(FakeTransport):
            async def evaluate(self, ref, expression):
                if "framey-steam-probe-v1" in expression:
                    return await super().evaluate(ref, expression)
                return {"exceptionDetails": {"text": "PRIVATE"}, "result": {"objectId": "PRIVATE"}}
        self.assertEqual((await SteamReadProvider(BadWire()).read("focus"))["code"], "provider_read_failed")

    async def test_closed_provider_does_not_resurrect_on_later_read(self):
        p = SteamReadProvider(FakeTransport()); p.dispose()
        self.assertEqual((await p.read("focus"))["code"], "provider_disposed")

    async def test_positive_focus_is_focus_only(self):
        t = FakeTransport(); t.value["focusedAppId"] = "synthetic-fixture"; t.value["status"] = "present"
        result = await SteamReadProvider(t).read("focus")
        self.assertEqual(result["data"]["status"], "present")
        self.assertNotIn("running", result["data"])
        self.assertNotIn("scene", result["data"])

    async def test_partial_app_categories_stay_null_not_zero_or_tier(self):
        t=FakeTransport(); t.value={"status":"partial", "app": {"appId":42,
            "steamDeckCategory":3,"steamOsCategory":None,"steamMachineCategory":1,"steamFrameCategory":2}}
        self.assertEqual((await SteamReadProvider(t).read("app", {"appId":42}))["data"],t.value)

    async def test_duplicate_apps_and_inconsistent_count_fail_closed(self):
        app={"appId":42,"steamDeckCategory":3,"steamOsCategory":0,"steamMachineCategory":1,"steamFrameCategory":2}
        for apps,count in (([app,app],2),([app],2)):
            t=FakeTransport();t.value={"status":"present","apps":apps,"visibleCount":count,"truncated":False}
            self.assertEqual((await SteamReadProvider(t).read("visibleApps"))["code"],"invalid_provider_result")

    async def test_visible_apps_accepts_valid_whitelisted_records(self):
        t=FakeTransport();t.value={"status":"present","apps":[{"appId":42,"steamDeckCategory":3,
            "steamOsCategory":0,"steamMachineCategory":1,"steamFrameCategory":2}],"visibleCount":1,"truncated":False}
        self.assertEqual((await SteamReadProvider(t).read("visibleApps"))["data"],t.value)

    async def test_invalidate_and_dispose_promptly_cancel_and_drain_blocked_reads(self):
        for stage in ("discovery", "evaluation"):
            for action, code in (("invalidate", "stale_context"), ("dispose", "provider_disposed")):
                with self.subTest(stage=stage, action=action):
                    entered, drained = asyncio.Event(), asyncio.Event()
                    class Blocked(FakeTransport):
                        async def block(self):
                            entered.set()
                            try:
                                await asyncio.Event().wait()
                            finally:
                                drained.set()
                        async def list_targets(self):
                            if stage == "discovery":
                                await self.block()
                            return await super().list_targets()
                        async def evaluate(self, ref, expression):
                            if stage == "evaluation" and "framey-steam-probe-v1" not in expression:
                                await self.block()
                            return await super().evaluate(ref, expression)
                    p = SteamReadProvider(Blocked(), timeout=1)
                    pending = asyncio.create_task(p.read("focus"))
                    await asyncio.wait_for(entered.wait(), .5)
                    getattr(p, action)()
                    done, _ = await asyncio.wait({pending}, timeout=.1)
                    try:
                        self.assertIn(pending, done, "lifecycle change left read blocked until timeout")
                        self.assertEqual(pending.result(), {"ok":False,"available":False,"code":code})
                        self.assertTrue(drained.is_set(), "read returned before owned transport wait drained")
                    finally:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)

    async def test_caller_cancellation_drains_only_that_read(self):
        entered, drained, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Blocked(FakeTransport):
            async def list_targets(self):
                entered.set()
                try:
                    await release.wait()
                finally:
                    drained.set()
                return await super().list_targets()
        p = SteamReadProvider(Blocked())
        cancelled = asyncio.create_task(p.read("focus"))
        await asyncio.wait_for(entered.wait(), .5)
        survivor = asyncio.create_task(p.read("focus"))
        unrelated = asyncio.create_task(release.wait())
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertTrue(drained.is_set())
        self.assertFalse(survivor.done())
        self.assertFalse(unrelated.done())
        release.set()
        self.assertTrue((await survivor)["ok"])
        await unrelated

    async def test_invalidation_allows_new_read_after_retiring_old_read(self):
        entered, drained = asyncio.Event(), asyncio.Event()
        class BlockOnce(FakeTransport):
            blocked = False
            async def list_targets(self):
                if not self.blocked:
                    self.blocked = True
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        drained.set()
                return await super().list_targets()
        p = SteamReadProvider(BlockOnce())
        old = asyncio.create_task(p.read("focus"))
        await asyncio.wait_for(entered.wait(), .5)
        p.invalidate()
        try:
            done, _ = await asyncio.wait({old}, timeout=.1)
            self.assertIn(old,done)
            self.assertEqual(old.result()["code"],"stale_context")
            self.assertTrue(drained.is_set())
            self.assertTrue((await p.read("focus"))["ok"])
        finally:
            old.cancel()
            await asyncio.gather(old,return_exceptions=True)

    async def test_lifecycle_change_before_caller_resume_discards_completed_child_reply(self):
        for action,code in (("invalidate","stale_context"),("dispose","provider_disposed")):
            with self.subTest(action=action):
                t=FakeTransport(); p=SteamReadProvider(t)
                t.after=lambda: asyncio.get_running_loop().call_soon(getattr(p,action))
                self.assertEqual((await p.read("focus")).get("code"),code)

    async def test_revoked_lease_on_evaluate_exception_preserves_stale_context(self):
        for stage in ("probe", "read"):
            with self.subTest(stage=stage):
                class Revoked(FakeTransport):
                    async def evaluate(self,ref,expression):
                        if ref == "shared" and (("framey-steam-probe-v1" in expression) == (stage == "probe")):
                            self.current.remove(ref)
                            raise ConnectionError("PRIVATE raw transport error")
                        return await super().evaluate(ref,expression)
                self.assertEqual((await SteamReadProvider(Revoked()).read("focus")).get("code"),"stale_context")

    async def test_invalidation_retires_all_owned_reads_and_leaves_unrelated_task(self):
        entered=asyncio.Event(); drained=[]
        class Blocked(FakeTransport):
            count=0
            async def list_targets(self):
                self.count+=1
                if self.count == 2:
                    entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    drained.append(True)
        p=SteamReadProvider(Blocked())
        reads=[asyncio.create_task(p.read("focus")) for _ in range(2)]
        unrelated=asyncio.create_task(asyncio.Event().wait())
        try:
            await asyncio.wait_for(entered.wait(),.5)
            p.invalidate()
            done,_=await asyncio.wait(reads,timeout=.1)
            self.assertEqual(len(done),2)
            self.assertEqual([t.result()["code"] for t in reads],["stale_context","stale_context"])
            self.assertEqual(len(drained),2)
            self.assertFalse(unrelated.done())
        finally:
            for task in [*reads,unrelated]:
                task.cancel()
            await asyncio.gather(*reads,unrelated,return_exceptions=True)

    async def test_repeated_lifecycle_signals_do_not_interrupt_async_cleanup(self):
        for first,second,code in (("dispose","dispose","provider_disposed"),
                                  ("invalidate","dispose","provider_disposed"),
                                  ("invalidate","invalidate","stale_context")):
            with self.subTest(first=first,second=second):
                entered,cleaning,gate,drained=[asyncio.Event() for _ in range(4)]
                class Cleanup(FakeTransport):
                    async def list_targets(self):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cleaning.set()
                            await gate.wait()
                            drained.set()
                p=SteamReadProvider(Cleanup(),timeout=1)
                pending=asyncio.create_task(p.read("focus"))
                try:
                    await asyncio.wait_for(entered.wait(),.5)
                    getattr(p,first)()
                    await asyncio.wait_for(cleaning.wait(),.5)
                    getattr(p,second)()
                    done,_=await asyncio.wait({pending},timeout=.01)
                    self.assertFalse(done,"repeat lifecycle signal interrupted transport cleanup")
                    self.assertFalse(drained.is_set())
                    gate.set()
                    self.assertEqual((await asyncio.wait_for(pending,.5))["code"],code)
                    self.assertTrue(drained.is_set())
                finally:
                    gate.set()
                    pending.cancel()
                    await asyncio.gather(pending,return_exceptions=True)

    async def test_original_deadline_does_not_interrupt_lifecycle_cleanup(self):
        for action,code in (("invalidate","stale_context"),("dispose","provider_disposed")):
            with self.subTest(action=action):
                entered,cleaning,gate,drained=[asyncio.Event() for _ in range(4)]
                class Cleanup(FakeTransport):
                    async def list_targets(self):
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cleaning.set()
                            await gate.wait()
                            drained.set()
                p=SteamReadProvider(Cleanup(),timeout=.02)
                pending=asyncio.create_task(p.read("focus"))
                try:
                    await asyncio.wait_for(entered.wait(),.5)
                    getattr(p,action)()
                    await asyncio.wait_for(cleaning.wait(),.5)
                    done,_=await asyncio.wait({pending},timeout=.06)
                    self.assertFalse(done,"original deadline interrupted lifecycle cleanup")
                    gate.set()
                    self.assertEqual((await asyncio.wait_for(pending,.5))["code"],code)
                    self.assertTrue(drained.is_set())
                finally:
                    gate.set()
                    pending.cancel()
                    await asyncio.gather(pending,return_exceptions=True)

    async def test_repeated_caller_cancellation_waits_for_lifecycle_cleanup(self):
        entered,cleaning,gate,drained=[asyncio.Event() for _ in range(4)]
        class Cleanup(FakeTransport):
            async def list_targets(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await gate.wait()
                    drained.set()
        p=SteamReadProvider(Cleanup())
        pending=asyncio.create_task(p.read("focus"))
        try:
            await asyncio.wait_for(entered.wait(),.5)
            p.invalidate()
            await asyncio.wait_for(cleaning.wait(),.5)
            for _ in range(2):
                pending.cancel()
                done,_=await asyncio.wait({pending},timeout=.01)
                self.assertFalse(done,"caller cancellation abandoned transport cleanup")
            gate.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertTrue(drained.is_set())
        finally:
            gate.set()
            pending.cancel()
            await asyncio.gather(pending,return_exceptions=True)


NODE_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const expressions = JSON.parse(fs.readFileSync(0, 'utf8'));
let passed = 0;
function test(name, fn) { fn(); passed++; console.log('ok - ' + name); }
function run(kind, window) {
  // Each fixture is a fresh context, has no document/network/timers, and executes
  // the exact Python adapter expression, rather than a second implementation.
  return JSON.parse(JSON.stringify(vm.runInNewContext(expressions[kind], {window}, {timeout:100})));
}
const makeApp = (id=42) => ({appid:id, steam_deck_compat_category:3,
  steam_os_compat_category:0, steam_machine_compat_category:1,
  steam_frame_compat_category:2, name:'synthetic-private-name', secret:'never-return'});
const makeFull = apps => ({SteamClient:{Apps:{}}, appStore:{
  allApps:apps, GetAppOverviewByAppID(id) { return apps.find(a => a.appid === id) || null; },
  RegisterForAppDetails() { throw new Error('mutator/subscription must not run'); },
  UpdateAppOverview() { throw new Error('mutator must not run'); }
}});
const makeFocus = values => ({FocusedAppWindowStore:{
  GetFocusedAppID:() => values[0], GetFocusedWindowID:() => values[1],
  GetFocusedOverlayAppID:() => values[2], GetFocusedOverlayPID:() => values[3],
  RegisterForChange() { throw new Error('subscription must not run'); }
}});

test('absent globals produce false capabilities', () => assert.deepEqual(run('probe', {}), {apps:false,focus:false}));
test('full appStore capability ignores target names', () => assert.deepEqual(run('probe', makeFull([])), {apps:true,focus:false}));
test('null native Apps object is absent capability', () => {
  const w=makeFull([]); w.SteamClient.Apps=null;
  assert.deepEqual(run('probe',w), {apps:false,focus:false});
});
test('focus capability independent of appStore', () => assert.deepEqual(run('probe', makeFocus([null,0,'0',''])), {apps:false,focus:true}));
test('missing focus remains unavailable', () => assert.deepEqual(run('focus', {}), {providerError:'capability_unavailable'}));
test('empty focus is explicit without target/scene inference', () => assert.deepEqual(run('focus', makeFocus([undefined,0,'0',''])), {
  status:'empty', focusedAppId:null, focusedWindowId:0, focusedOverlayAppId:'0', focusedOverlayPid:''}));
test('nonempty fixture gives focus snapshot only', () => assert.deepEqual(run('focus', makeFocus(['synthetic',0,null,''])), {
  status:'present', focusedAppId:'synthetic', focusedWindowId:0, focusedOverlayAppId:null, focusedOverlayPid:''}));
for (const value of [false, {}, [], Infinity, NaN, 'x'.repeat(257)]) {
  test('invalid focus scalar is rejected: ' + typeof value, () => assert.deepEqual(run('focus', makeFocus([value,0,0,0])), {providerError:'invalid_provider_result'}));
}
test('throwing cached focus getter gives explicit failure', () => {
  const w = makeFocus([null,0,0,0]); w.FocusedAppWindowStore.GetFocusedAppID = () => { throw new Error('private'); };
  assert.deepEqual(run('focus', w), {providerError:'provider_read_failed'});
});
test('visible list retains only whitelisted cached fields', () => assert.deepEqual(run('visibleApps', makeFull([makeApp()])), {
  status:'present', apps:[{appId:42,steamDeckCategory:3,steamOsCategory:0,steamMachineCategory:1,steamFrameCategory:2}],visibleCount:1,truncated:false}));
test('empty visible list remains empty snapshot', () => assert.deepEqual(run('visibleApps', makeFull([])), {status:'empty',apps:[],visibleCount:0,truncated:false}));
test('missing categories are partial, never substituted as rating zero', () => {
  const app=makeApp(); delete app.steam_frame_compat_category;
  const result=run('app', makeFull([app])); assert.equal(result.status,'partial'); assert.equal(result.app.steamFrameCategory,null);
});
test('missing app is not_found cached lookup', () => assert.deepEqual(run('app', makeFull([])), {status:'not_found',app:null}));
test('positive lookup projects matching fixture', () => {
  const r=run('app', makeFull([makeApp()])); assert.equal(r.status,'present'); assert.equal(r.app.appId,42); assert.equal(Object.keys(r.app).length,5);
});
test('visible snapshot bounds output and reports truncation', () => {
  const result=run('visibleApps', makeFull(Array.from({length:4100},(_,i)=>makeApp(i+1))));
  assert.equal(result.apps.length,4096); assert.equal(result.visibleCount,4100); assert.equal(result.truncated,true);
});
test('invalid model identifiers are not filtered into empty success', () => assert.deepEqual(run('visibleApps', makeFull([makeApp(true)])), {providerError:'provider_read_failed'}));
test('invalid category values do not become compatibility ratings', () => {
  const app=makeApp(); app.steam_frame_compat_category='verified';
  assert.deepEqual(run('app', makeFull([app])), {providerError:'provider_read_failed'});
});
console.log('PASS ' + passed + ' fixed-expression fixture checks; no real CDP/device integration exercised.');
"""


class FixedExpressionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for expression fixture checks")
    def test_fixed_cached_expressions_execute_with_strict_shapes(self):
        expressions = {"probe": steam_data.PROBE, "focus": steam_data.FOCUS,
                       "visibleApps": steam_data.VISIBLE_APPS,
                       "app": steam_data._LOOKUP.replace("APP_ID", "42")}
        result = subprocess.run([shutil.which("node"), "-e", NODE_HARNESS],
                                input=json.dumps(expressions), text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
