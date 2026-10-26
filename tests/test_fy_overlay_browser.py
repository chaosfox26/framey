import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fy_overlay_browser as browser


def target(**changes):
    value = {"id": "ABC123", "type": "page", "title": "SharedJSContext",
             "url": "https://steamloopback.host/routes/settings/power",
             "webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/ABC123"}
    value.update(changes)
    return value


class Socket:
    def __init__(self, replies=None, value=None, block=False):
        self.replies = list(replies or [])
        self.value = {"ok": True, "created": True, "requested": "hide"} if value is None else value
        self.block = block
        self.sent = []
        self.closed = False

    async def send(self, value):
        self.sent.append(value)

    async def recv(self):
        if self.block:
            await asyncio.Event().wait()
        if self.replies:
            return self.replies.pop(0)
        return json.dumps({"id": self.sent[-1]["id"], "result": {"result": {"value": self.value}}})

    def close(self):
        self.closed = True


def host(socket=None, targets=None, owned=True):
    result = SimpleNamespace(opened=[], socket=socket or Socket())

    async def tabs():
        return [target()] if targets is None else targets

    async def open_socket(endpoint):
        result.opened.append(endpoint)
        return result.socket

    result.tabs = tabs
    result.WS = SimpleNamespace(open=open_socket)
    result.cdp_owner_ok = lambda: owned
    return result


class BrowserTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_uses_dedicated_main_context_and_closes_transport(self):
        fake = host()
        result = await browser.OverlayBrowser(fake).ensure()
        self.assertEqual(result, {"ok": True, "key": "framey.panel", "created": True, "requested": "hide"})
        self.assertTrue(fake.socket.closed)
        request = fake.socket.sent[0]
        self.assertEqual(request["method"], "Runtime.evaluate")
        self.assertTrue(request["params"]["awaitPromise"])
        self.assertTrue(request["params"]["returnByValue"])
        self.assertTrue(request["params"]["userGesture"])
        self.assertNotIn("contextId", request["params"])

    async def test_probe_has_boolean_availability_and_no_ensure_command(self):
        fake = host(Socket(value={"ok": True, "available": True}))
        result = await browser.OverlayBrowser(fake).probe()
        self.assertIs(result["available"], True)
        self.assertIn('"action": "probe"', fake.socket.sent[0]["params"]["expression"])
        unavailable = await browser.OverlayBrowser(host(owned=False)).probe()
        self.assertIs(unavailable["available"], False)

    async def test_owner_guard_fails_before_inventory_or_socket(self):
        fake = host(owned=False)

        async def forbidden():
            self.fail("discovery called without an owned debugger")

        fake.tabs = forbidden
        result = await browser.OverlayBrowser(fake).ensure()
        self.assertEqual(result["code"], "debugger_unavailable")
        self.assertEqual(fake.opened, [])

    async def test_unique_exact_shared_context_required(self):
        choices = [[], [target(), target(id="OTHER")], [target(title="Other")],
                   [target(type="worker")], [target(url="https://steamloopback.host.evil/path")],
                   [target(url="https://user@steamloopback.host/path")],
                   [target(url="http://steamloopback.host/path")],
                   [target(url="https://steamloopback.host:443/path")],
                   [target(url="https://steamloopback.host/\npath")],
                   [target()] * (browser.TARGET_MAX + 1), {}]
        for targets in choices:
            with self.subTest(targets=str(targets)[:100]):
                fake = host(targets=targets)
                result = await browser.OverlayBrowser(fake).ensure()
                self.assertEqual(result["code"], "context_unavailable")
                self.assertFalse(fake.opened)

    async def test_websocket_authority_and_exact_target_path_are_checked(self):
        invalid = ["wss://127.0.0.1:8080/devtools/page/ABC123",
                   "ws://localhost:8080/devtools/page/ABC123", "ws://127.0.0.1:8081/devtools/page/ABC123",
                   "ws://user@127.0.0.1:8080/devtools/page/ABC123", "ws://127.0.0.1:8080/devtools/browser/ABC123",
                   "ws://127.0.0.1:8080/devtools/page/OTHER", "ws://127.0.0.1:8080/devtools/page/ABC123?q=x",
                   "ws://127.0.0.1:8080/devtools/page/ABC123#x", "ws://127.0.0.1:8080/devtools/page/ABC123\r\nX:1"]
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint):
                fake = host(targets=[target(webSocketDebuggerUrl=endpoint)])
                result = await browser.OverlayBrowser(fake).ensure()
                self.assertEqual(result["code"], "endpoint_unavailable")
                self.assertFalse(fake.opened)

    async def test_events_and_unrelated_ids_are_ignored(self):
        fake = host(Socket(replies=[json.dumps({"method": "Page.loadEventFired"}),
                                    json.dumps({"id": 987, "error": {}}),
                                    json.dumps({"id": True, "result": {"result": {"value": {"ok": True}}}})]))
        self.assertTrue((await browser.OverlayBrowser(fake).ensure())["ok"])

    async def test_cdp_errors_and_malformed_replies_are_fixed_private_errors(self):
        private = "token=private-token /home/private-user/secret"
        responses = [None, "not json", "[]", "x" * (browser.MESSAGE_MAX + 1),
                     json.dumps({"id": 1, "error": {"message": private}}),
                     json.dumps({"id": 1, "error": {}, "result": {"result": {"value": {"ok": True}}}}),
                     json.dumps({"id": 1, "result": {"exceptionDetails": {"text": private}}}),
                     json.dumps({"id": 1, "result": {"result": {"description": private}}}),
                     json.dumps({"id": 1, "result": {"result": {"value": {"ok": 1}}}})]
        for response in responses:
            with self.subTest(response=str(response)[:80]):
                fake = host(Socket(replies=[response]))
                result = await browser.OverlayBrowser(fake).ensure()
                self.assertIs(result["ok"], False)
                self.assertNotIn(private, json.dumps(result))
                self.assertTrue(fake.socket.closed)

    async def test_unrecognized_browser_fields_are_not_returned(self):
        fake = host(Socket(value={"ok": True, "created": True, "popup": "private", "token": "private", "requested": "hide"}))
        self.assertEqual(await browser.OverlayBrowser(fake).ensure(),
                         {"ok": True, "key": "framey.panel", "created": True, "requested": "hide"})

    async def test_action_specific_acknowledgement_is_required(self):
        for value in ({"ok": True}, {"ok": True, "absent": True},
                      {"ok": True, "requested": "hide"},
                      {"ok": True, "created": False, "requested": "show"}):
            with self.subTest(value=value):
                result = await browser.OverlayBrowser(host(Socket(value=value))).ensure()
                self.assertEqual(result["code"], "debugger_reply_failed")
        fake = host(Socket(value={"ok": True, "created": False, "requested": "show"}))
        self.assertEqual((await browser.OverlayBrowser(fake).hide())["code"], "debugger_reply_failed")

    async def test_fixed_browser_errors_only(self):
        for code in ("ownership_conflict", "popup_closing", "factory_unavailable", "secret token=private", {}):
            fake = host(Socket(value={"ok": False, "code": code, "error": "/home/private"}))
            result = await browser.OverlayBrowser(fake).ensure()
            self.assertFalse(result["ok"])
            self.assertNotIn("private", json.dumps(result))

    async def test_event_flood_is_bounded(self):
        fake = host(Socket(replies=[json.dumps({"method": "event"})] * browser.EVENT_MAX))
        self.assertEqual((await browser.OverlayBrowser(fake).ensure())["code"], "debugger_reply_failed")
        self.assertTrue(fake.socket.closed)

    async def test_deadline_covers_discovery_and_receive(self):
        fake = host()

        async def blocked():
            await asyncio.Event().wait()

        fake.tabs = blocked
        with patch.object(browser, "DEADLINE", .02):
            self.assertEqual((await browser.OverlayBrowser(fake).ensure())["code"], "timeout")
            waiting = host(Socket(block=True))
            self.assertEqual((await browser.OverlayBrowser(waiting).show())["code"], "timeout")
        self.assertFalse(fake.opened)
        self.assertTrue(waiting.socket.closed)

    async def test_deadline_also_covers_lock_queue(self):
        controller = browser.OverlayBrowser(host())
        await controller._lock.acquire()
        try:
            with patch.object(browser, "DEADLINE", .02):
                result = await controller.ensure()
            self.assertEqual(result["code"], "timeout")
            self.assertFalse(controller.host.opened)
        finally:
            controller._lock.release()

    async def test_cancellation_propagates_and_closes_socket(self):
        fake = host(Socket(block=True))
        controller = browser.OverlayBrowser(fake)
        task = asyncio.create_task(controller.ensure())
        while not fake.socket.sent:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(fake.socket.closed)
        self.assertFalse(controller._lock.locked())

    async def test_lifecycle_methods_issue_only_fixed_actions(self):
        fake = host()
        controller = browser.OverlayBrowser(fake)
        for action in ("show", "hide", "close"):
            fake.socket.value = {"ok": True, "created": False, "requested": action}
            await getattr(controller, action)()
            self.assertIn('"action": "' + action + '"', fake.socket.sent[-1]["params"]["expression"])


NODE_SCRIPT = r"""
const vm = require('node:vm');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const expressions = JSON.parse(input);
  function make(options={}) {
    const calls = [];
    const popup = {closed: false};
    const delays = [];
    const native = {};
    for (const method of ['ShowWindow','HideWindow','Close']) {
      native[method] = function() {
        if (this !== native) throw Error('lost native receiver');
        calls.push(method);
        if (options.reject === method) return Promise.reject(Error('token=private /home/private'));
        if (method === 'Close' && !options.delayedClose) popup.closed = true;
      };
    }
    popup.SteamClient = {Window:native};
    const context = {location:{origin:'https://steamloopback.host'}, setTimeout(callback, delay) {
      delays.push(delay);
      Promise.resolve().then(() => {
        if (options.closeAfterTicks === delays.length) popup.closed = true;
        if (options.replaceAfterTicks === delays.length) context.__frameyPanelHost = {owner:'replacement'};
        callback();
      });
    }};
    context.window = context;
    context.g_PopupManager = {constructor:{CreatePopup(name, config) {
      calls.push(['create', name, config]);
      return options.create ? options.create({popup, element:{}}) : {popup, element:{}};
    }}};
    return {context:vm.createContext(context), calls, popup, delays};
  }
  const run = (h, action) => vm.runInContext(expressions[action], h.context, {timeout:1000});
  const results = {};
  const basic = make();
  results.probe = await run(basic,'probe');
  results.probeCalls = basic.calls.length;
  results.ensure = await run(basic,'ensure');
  results.ensureAgain = await run(basic,'ensure');
  results.show = await run(basic,'show');
  results.hide = await run(basic,'hide');
  results.close = await run(basic,'close');
  results.absentClose = await run(basic,'close');
  results.basicCalls = basic.calls;
  results.deleted = !Object.hasOwn(basic.context,'__frameyPanelHost');
  const foreign = make();
  foreign.context.__frameyPanelHost = {owner:'other',key:'framey.panel',popup:foreign.popup};
  results.foreign = await run(foreign,'close');
  results.foreignCalls = foreign.calls.length;
  const origin = make(); origin.context.location.origin = 'https://unrelated.invalid';
  results.origin = await run(origin,'ensure'); results.originCalls = origin.calls.length;
  const missing = make(); delete missing.context.g_PopupManager;
  results.unavailable = await run(missing,'probe');
  results.missing = await run(missing,'ensure');
  const absent = make(); results.absentShow = await run(absent,'show');
  const delayed = make({delayedClose:true});
  await run(delayed,'ensure'); await run(delayed,'close');
  results.closing = await run(delayed,'ensure');
  results.closingCreates = delayed.calls.filter(x => Array.isArray(x)).length;
  results.closingDelays = delayed.delays;
  delayed.popup.closed = true;
  results.recreated = await run(delayed,'ensure');
  results.recreatedCount = delayed.calls.filter(x => Array.isArray(x)).length;
  const closesSoon = make({delayedClose:true, closeAfterTicks:2});
  await run(closesSoon,'ensure'); await run(closesSoon,'close');
  results.delayedDeleted = !Object.hasOwn(closesSoon.context,'__frameyPanelHost');
  results.delayedDelays = closesSoon.delays;
  const replacedClosing = make({delayedClose:true, closeAfterTicks:2, replaceAfterTicks:1});
  await run(replacedClosing,'ensure'); await run(replacedClosing,'close');
  results.closingReplacement = replacedClosing.context.__frameyPanelHost.owner;
  results.replacementDelays = replacedClosing.delays;
  const closedBeforeProbe = make({delayedClose:true});
  await run(closedBeforeProbe,'ensure'); await run(closedBeforeProbe,'close');
  closedBeforeProbe.popup.closed = true;
  const callsBeforeProbe = closedBeforeProbe.calls.length;
  const delaysBeforeProbe = closedBeforeProbe.delays.length;
  results.closedProbe = await run(closedBeforeProbe,'probe');
  results.closedProbeDeleted = !Object.hasOwn(closedBeforeProbe.context,'__frameyPanelHost');
  results.closedProbeNoCommands = callsBeforeProbe === closedBeforeProbe.calls.length && delaysBeforeProbe === closedBeforeProbe.delays.length;
  let release;
  const pending = make({create: result => new Promise(resolve => release = () => resolve(result))});
  const first = run(pending,'ensure'), second = run(pending,'ensure');
  release(); results.concurrent = await Promise.all([first,second]);
  results.concurrentCreates = pending.calls.filter(x => Array.isArray(x)).length;
  const failed = make({reject:'ShowWindow'}); await run(failed,'ensure');
  results.failed = await run(failed,'show');
  results.failedRecordRetained = Boolean(failed.context.__frameyPanelHost);
  let finish;
  const replaced = make({create: result => new Promise(resolve => finish = () => resolve(result))});
  const replacing = run(replaced,'ensure');
  replaced.context.__frameyPanelHost = {owner:'foreign'}; finish();
  results.replaced = await replacing;
  results.replacedClosedOnlyOwn = replaced.calls.includes('Close') && replaced.popup.closed;
  const self = make();
  let hostCalls = 0;
  self.context.SteamClient = {Window:{HideWindow(){hostCalls++;},Close(){hostCalls++;}}};
  vm.runInContext('g_PopupManager.constructor.CreatePopup = () => ({popup:window})', self.context);
  results.self = await run(self,'ensure');
  results.selfClose = await run(self,'close');
  results.hostCalls = hostCalls;
  process.stdout.write(JSON.stringify(results));
}).on('error', () => process.exit(1));
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for browser-expression behavior")
class PopupExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expressions = {action: browser._expression(action) for action in ("probe", "ensure", "show", "hide", "close")}
        result = subprocess.run([shutil.which("node"), "--unhandled-rejections=strict", "-e", NODE_SCRIPT],
                                input=json.dumps(expressions), text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise AssertionError("Popup expression behavior failed: " + result.stderr[:1000])
        cls.results = json.loads(result.stdout)

    def test_probe_does_not_create_or_show(self):
        self.assertEqual(self.results["probe"], {"ok": True, "available": True})
        self.assertEqual(self.results["probeCalls"], 0)
        self.assertFalse(self.results["unavailable"]["available"])

    def test_creation_configuration_and_initial_hide(self):
        create = self.results["basicCalls"][0]
        self.assertEqual(create, ["create", "Framey_Panel", {
            "browserType": 1, "eCreationFlags": 2, "strVROverlayKey": "framey.panel", "title": "Framey",
            "dimensions": {"width": 542, "height": 607, "left": 0, "top": 0}}])
        self.assertEqual(self.results["basicCalls"][1], "HideWindow")
        self.assertTrue(self.results["ensure"]["created"])
        self.assertFalse(self.results["ensureAgain"]["created"])

    def test_lifecycle_and_idempotent_close_keep_native_receiver(self):
        self.assertEqual(self.results["basicCalls"][1:], ["HideWindow", "HideWindow", "ShowWindow", "HideWindow", "Close"])
        self.assertTrue(self.results["deleted"])
        self.assertTrue(self.results["absentClose"]["absent"])
        self.assertEqual(self.results["absentShow"]["code"], "popup_missing")

    def test_wrong_origin_and_foreign_global_are_never_mutated(self):
        self.assertEqual(self.results["foreign"]["code"], "ownership_conflict")
        self.assertEqual(self.results["foreignCalls"], 0)
        self.assertEqual(self.results["origin"]["code"], "context_changed")
        self.assertEqual(self.results["originCalls"], 0)
        self.assertEqual(self.results["missing"]["code"], "factory_unavailable")

    def test_creation_is_retained_and_deduplicated_across_overlapping_requests(self):
        self.assertEqual(self.results["concurrentCreates"], 1)
        self.assertTrue(all(value["ok"] for value in self.results["concurrent"]))

    def test_acknowledged_close_does_not_permit_duplicate_until_window_is_closed(self):
        self.assertEqual(self.results["closing"]["code"], "popup_closing")
        self.assertEqual(self.results["closingCreates"], 1)
        self.assertTrue(self.results["recreated"]["created"])
        self.assertEqual(self.results["recreatedCount"], 2)
        self.assertEqual(self.results["closingDelays"], [25] * 10)

    def test_close_releases_owned_record_when_window_closes_within_finite_wait(self):
        self.assertTrue(self.results["delayedDeleted"])
        self.assertEqual(self.results["delayedDelays"], [25, 25])

    def test_close_poll_stops_without_deleting_replacement(self):
        self.assertEqual(self.results["closingReplacement"], "replacement")
        self.assertEqual(self.results["replacementDelays"], [25])

    def test_probe_releases_already_closed_record_without_native_commands_or_timer(self):
        self.assertTrue(self.results["closedProbe"]["available"])
        self.assertTrue(self.results["closedProbeDeleted"])
        self.assertTrue(self.results["closedProbeNoCommands"])

    def test_native_rejection_is_fixed_and_retains_owned_reference(self):
        self.assertEqual(self.results["failed"], {"ok": False, "code": "window_command_failed"})
        self.assertTrue(self.results["failedRecordRetained"])

    def test_replaced_global_closes_only_newly_created_owned_reference(self):
        self.assertEqual(self.results["replaced"]["code"], "ownership_conflict")
        self.assertTrue(self.results["replacedClosedOnlyOwn"])

    def test_factory_cannot_return_shared_host_as_owned_popup(self):
        self.assertEqual(self.results["self"]["code"], "creation_failed")
        self.assertTrue(self.results["selfClose"]["absent"])
        self.assertEqual(self.results["hostCalls"], 0)


if __name__ == "__main__":
    unittest.main()
