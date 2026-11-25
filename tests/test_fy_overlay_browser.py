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
    async def test_explicit_launcher_width_reaches_compact_creation_request(self):
        fake = host()
        try:
            result = await browser.OverlayBrowser(fake).ensure("compact", launcher_width=462)
        except TypeError:
            self.fail("compact creation does not accept the launcher width")
        self.assertTrue(result["ok"])
        self.assertIn('"width": 462', fake.socket.sent[0]["params"]["expression"])

    async def test_invalid_launcher_width_is_rejected_before_browser_access(self):
        for layout in ("compact", "window"):
            for width in (None, False, True, 381, 663, 382.0, "462", [], {}):
                with self.subTest(layout=layout, width=width):
                    fake = host()

                    def forbidden_owner_check():
                        self.fail("invalid launcher width reached the browser")

                    fake.cdp_owner_ok = forbidden_owner_check
                    result = await browser.OverlayBrowser(fake).ensure(layout, width)
                    self.assertEqual(result["code"], "invalid_launcher_width")
                    self.assertFalse(fake.opened)

    async def test_active_target_returns_only_verified_steam_enum(self):
        controller = browser.OverlayBrowser(host(Socket(value={"ok": True, "target": "steam"})))
        self.assertTrue(callable(getattr(controller, "active_target", None)))
        self.assertEqual(await controller.active_target(), {"ok": True, "target": "steam"})
        request = controller.host.socket.sent[0]
        self.assertIn('"action": "target"', request["params"]["expression"])
        self.assertFalse(request["params"]["userGesture"])
        self.assertTrue(controller.host.socket.closed)

    async def test_active_target_rejects_malformed_or_untrusted_target_replies(self):
        values = [{"ok": True}, {"ok": True, "target": "app"},
                  {"ok": True, "target": {}}, {"ok": True, "target": True},
                  {"ok": True, "target": "steam", "appid": "private"},
                  {"ok": False, "code": "ownership_conflict"},
                  {"ok": False, "code": "target_context_unavailable", "appid": "private"}]
        for value in values:
            with self.subTest(value=value):
                result = await browser.OverlayBrowser(host(Socket(value=value)))._run("target")
                self.assertEqual(result["code"], "debugger_reply_failed")
                self.assertNotIn("private", json.dumps(result))

    async def test_active_target_preserves_only_its_fixed_failure_codes(self):
        for code in ("app_target_unavailable", "target_context_unavailable"):
            with self.subTest(code=code):
                result = await browser.OverlayBrowser(host(Socket(value={"ok": False, "code": code})))._run("target")
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], code)
                self.assertNotIn("target", result)

    async def test_compact_layout_reaches_browser_request(self):
        fake = host()
        result = await browser.OverlayBrowser(fake).ensure(layout="compact")
        self.assertEqual(result, {"ok": True, "key": "framey.panel", "created": True, "requested": "hide"})
        self.assertIn('"layout": "compact"', fake.socket.sent[0]["params"]["expression"])

    async def test_invalid_layout_fails_before_browser_discovery_or_connection(self):
        for layout in (None, False, 3, "", "Compact", "sidebar", [], {}, "window\n"):
            with self.subTest(layout=layout):
                fake = host()

                def forbidden_owner_check():
                    self.fail("invalid layout reached browser ownership check")

                async def forbidden_inventory():
                    self.fail("invalid layout reached browser inventory")

                fake.cdp_owner_ok = forbidden_owner_check
                fake.tabs = forbidden_inventory
                result = await browser.OverlayBrowser(fake).ensure(layout=layout)
                self.assertEqual(result, {"ok": False,
                    "error": "Framey's separate panel is unavailable; use the Steam panel.",
                    "code": "invalid_layout"})
                self.assertFalse(fake.opened)

    async def test_layout_conflict_has_fixed_transport_error(self):
        fake = host(Socket(value={"ok": False, "code": "layout_conflict", "error": "private"}))
        result = await browser.OverlayBrowser(fake).ensure()
        self.assertEqual(result, {"ok": False,
            "error": "Framey's separate panel is unavailable; use the Steam panel.", "code": "layout_conflict"})

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
  results.windowLayout = basic.context.__frameyPanelHost.layout;
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
  const differentLayout = make();
  await run(differentLayout,'ensure');
  differentLayout.context.__frameyPanelHost.layout = 'compact';
  const beforeConflict = differentLayout.calls.length;
  results.layoutConflict = await run(differentLayout,'ensure');
  results.layoutConflictNoCommands = differentLayout.calls.length === beforeConflict;
  results.compactProbe = await run(differentLayout,'probe');
  results.compactShow = await run(differentLayout,'show');
  results.compactHide = await run(differentLayout,'hide');
  results.compactClose = await run(differentLayout,'close');
  const legacy = make();
  await run(legacy,'ensure');
  delete legacy.context.__frameyPanelHost.layout;
  results.legacyWindow = await run(legacy,'ensure');
  const beforeLegacyConflict = legacy.calls.length;
  results.legacyCompact = await run(legacy,'ensureCompact');
  results.legacyCompactNoCommands = legacy.calls.length === beforeLegacyConflict;
  const compact = make();
  results.compactEnsure = await run(compact,'ensureCompact');
  results.compactLayout = compact.context.__frameyPanelHost.layout;
  results.compactAgain = await run(compact,'ensureCompact');
  const beforeWindowConflict = compact.calls.length;
  results.windowConflict = await run(compact,'ensure');
  results.windowConflictNoCommands = compact.calls.length === beforeWindowConflict;
  results.compactCalls = compact.calls;
  let finishCompact;
  const pendingCompact = make({create: result => new Promise(resolve => finishCompact = () => resolve(result))});
  const compactFirst = run(pendingCompact,'ensureCompact');
  results.pendingWindowConflict = await run(pendingCompact,'ensure');
  finishCompact();
  results.pendingCompact = await compactFirst;
  results.pendingCompactCreates = pendingCompact.calls.filter(x => Array.isArray(x)).length;
  results.sizedLaunchers = [];
  for (const width of [382,383,462,542,662]) {
    const sized = make();
    await run(sized,'compactWidth'+width);
    await run(sized,'compactWidth'+width);
    results.sizedLaunchers.push({width, calls:sized.calls,
      storedWidth:sized.context.__frameyPanelHost.width ?? null});
  }
  const widthConflict = make();
  await run(widthConflict,'ensureCompact');
  const beforeWidthConflict = widthConflict.calls.length;
  results.widthConflict = await run(widthConflict,'compactWidth462');
  results.widthConflictNoCommands = widthConflict.calls.length === beforeWidthConflict;
  const legacyCompactWidth = make();
  await run(legacyCompactWidth,'ensureCompact');
  delete legacyCompactWidth.context.__frameyPanelHost.width;
  results.legacyCompactWidth = await run(legacyCompactWidth,'ensureCompact');
  const beforeLegacyWidthConflict = legacyCompactWidth.calls.length;
  results.legacyCompactWidthConflict = await run(legacyCompactWidth,'compactWidth462');
  results.legacyCompactWidthNoCommands = legacyCompactWidth.calls.length === beforeLegacyWidthConflict;
  const wideWindow = make();
  await run(wideWindow,'windowWideLauncher');
  results.wideWindowCalls = wideWindow.calls.slice();
  results.wideWindowStoredWidth = wideWindow.context.__frameyPanelHost.width ?? null;
  delete wideWindow.context.__frameyPanelHost.width;
  results.legacyWindowWidth = await run(wideWindow,'windowWideLauncher');
  const widthLifecycle = make();
  await run(widthLifecycle,'compactWidth662');
  results.widthLifecycle = [];
  for (const action of ['probe','show','hide','close']) results.widthLifecycle.push(await run(widthLifecycle,action));
  process.stdout.write(JSON.stringify(results));
}).on('error', () => process.exit(1));
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for browser-expression behavior")
class PopupExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expressions = {action: browser._expression(action) for action in ("probe", "ensure", "show", "hide", "close")}
        expressions["ensureCompact"] = browser._expression("ensure", "compact")
        for width in (382, 383, 462, 542, 662):
            expressions["compactWidth" + str(width)] = browser._expression("ensure", "compact", width)
        expressions["windowWideLauncher"] = browser._expression("ensure", "window", 662)
        result = subprocess.run([shutil.which("node"), "--unhandled-rejections=strict", "-e", NODE_SCRIPT],
                                input=json.dumps(expressions), text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise AssertionError("Popup expression behavior failed: " + result.stderr[:1000])
        cls.results = json.loads(result.stdout)

    def test_probe_does_not_create_or_show(self):
        self.assertEqual(self.results["probe"], {"ok": True, "available": True})
        self.assertEqual(self.results["probeCalls"], 0)
        self.assertFalse(self.results["unavailable"]["available"])

    def test_expression_rejects_unsupported_layouts(self):
        for layout in (None, False, 3, "", "Compact", "sidebar", [], {}, "window\n"):
            with self.subTest(layout=layout):
                with self.assertRaises(ValueError):
                    browser._expression("ensure", layout)

    def test_expression_rejects_invalid_launcher_width(self):
        for layout in ("compact", "window"):
            for width in (None, False, True, 381, 663, 382.0, "462", [], {}):
                with self.subTest(layout=layout, width=width):
                    with self.assertRaises(ValueError):
                        browser._expression("ensure", layout, width)

    def test_compact_width_controls_creation_and_owned_record_reuse(self):
        for result in self.results["sizedLaunchers"]:
            with self.subTest(width=result["width"]):
                self.assertEqual(result["calls"], [["create", "Framey_Panel", {
                    "browserType": 1, "eCreationFlags": 2, "strVROverlayKey": "framey.panel", "title": "Framey",
                    "dimensions": {"width": result["width"], "height": 140, "left": 0, "top": 0}}],
                    "HideWindow", "HideWindow"])
                self.assertEqual(result["storedWidth"], result["width"])

    def test_owned_compact_width_conflict_does_not_issue_native_commands(self):
        self.assertEqual(self.results["widthConflict"], {"ok": False, "code": "layout_conflict"})
        self.assertTrue(self.results["widthConflictNoCommands"])

    def test_legacy_compact_width_is_382_and_conflicts_with_grown_launcher(self):
        self.assertEqual(self.results["legacyCompactWidth"], {"ok": True, "created": False, "requested": "hide"})
        self.assertEqual(self.results["legacyCompactWidthConflict"], {"ok": False, "code": "layout_conflict"})
        self.assertTrue(self.results["legacyCompactWidthNoCommands"])

    def test_window_dimensions_ignore_valid_launcher_width_and_use_larger_window(self):
        self.assertEqual(self.results["wideWindowCalls"], [["create", "Framey_Panel", {
            "browserType": 1, "eCreationFlags": 2, "strVROverlayKey": "framey.panel", "title": "Framey",
            "dimensions": {"width": 800, "height": 720, "left": 0, "top": 0}}], "HideWindow"])
        self.assertEqual(self.results["wideWindowStoredWidth"], 800)
        self.assertEqual(self.results["legacyWindowWidth"], {"ok": True, "created": False, "requested": "hide"})

    def test_lifecycle_actions_ignore_compact_width_conflict(self):
        self.assertEqual(self.results["widthLifecycle"][0], {"ok": True, "available": True})
        for action, result in zip(("show", "hide", "close"), self.results["widthLifecycle"][1:]):
            with self.subTest(action=action):
                self.assertEqual(result, {"ok": True, "created": False, "requested": action})

    def test_creation_configuration_and_initial_hide(self):
        create = self.results["basicCalls"][0]
        self.assertEqual(create, ["create", "Framey_Panel", {
            "browserType": 1, "eCreationFlags": 2, "strVROverlayKey": "framey.panel", "title": "Framey",
            "dimensions": {"width": 800, "height": 720, "left": 0, "top": 0}}])
        self.assertEqual(self.results["basicCalls"][1], "HideWindow")
        self.assertTrue(self.results["ensure"]["created"])
        self.assertFalse(self.results["ensureAgain"]["created"])

    def test_default_creation_remembers_window_layout(self):
        self.assertEqual(self.results.get("windowLayout"), "window")

    def test_ensure_rejects_other_layout_without_native_commands(self):
        self.assertEqual(self.results["layoutConflict"], {"ok": False, "code": "layout_conflict"})
        self.assertTrue(self.results["layoutConflictNoCommands"])

    def test_lifecycle_actions_ignore_record_layout(self):
        self.assertEqual(self.results["compactProbe"], {"ok": True, "available": True})
        for action in ("show", "hide", "close"):
            self.assertEqual(self.results["compact" + action.title()],
                             {"ok": True, "created": False, "requested": action})

    def test_legacy_record_without_layout_remains_window(self):
        self.assertEqual(self.results["legacyWindow"], {"ok": True, "created": False, "requested": "hide"})
        self.assertEqual(self.results["legacyCompact"], {"ok": False, "code": "layout_conflict"})
        self.assertTrue(self.results["legacyCompactNoCommands"])

    def test_compact_creation_uses_fixed_dimensions_and_is_reused(self):
        self.assertEqual(self.results["compactCalls"], [["create", "Framey_Panel", {
            "browserType": 1, "eCreationFlags": 2, "strVROverlayKey": "framey.panel", "title": "Framey",
            "dimensions": {"width": 382, "height": 140, "left": 0, "top": 0}}], "HideWindow", "HideWindow"])
        self.assertEqual(self.results["compactLayout"], "compact")
        self.assertEqual(self.results["compactEnsure"], {"ok": True, "created": True, "requested": "hide"})
        self.assertEqual(self.results["compactAgain"], {"ok": True, "created": False, "requested": "hide"})

    def test_window_request_does_not_resize_or_replace_compact_popup(self):
        self.assertEqual(self.results["windowConflict"], {"ok": False, "code": "layout_conflict"})
        self.assertTrue(self.results["windowConflictNoCommands"])

    def test_conflicting_ensure_does_not_wait_for_pending_other_layout(self):
        self.assertEqual(self.results["pendingWindowConflict"], {"ok": False, "code": "layout_conflict"})
        self.assertTrue(self.results["pendingCompact"]["ok"])
        self.assertEqual(self.results["pendingCompactCreates"], 1)

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


TARGET_NODE_SCRIPT = r"""
const vm = require('node:vm');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const expression = JSON.parse(input);
  const names = ['GetFocusedAppID', 'GetFocusedWindowID', 'GetFocusedOverlayAppID', 'GetFocusedOverlayPID'];
  async function run(change=()=>{}, values=[0,0,0,0]) {
    let ownershipReads=0, popupReads=0, ownershipWrites=0;
    const getterCalls=[];
    const context={location:{origin:'https://steamloopback.host'}};
    context.window=context;
    const store={};
    names.forEach((name,index)=>store[name]=function(){
      if(this!==store)throw Error('lost focus-store receiver');
      getterCalls.push(name);
      return values[index];
    });
    context.FocusedAppWindowStore=store;
    Object.defineProperty(context,'__frameyPanelHost',{
      get(){ownershipReads++;throw Error('popup ownership read');},
      set(){ownershipWrites++;},configurable:true
    });
    Object.defineProperty(context,'g_PopupManager',{
      get(){popupReads++;throw Error('popup manager read');},configurable:true
    });
    change(context,store);
    const descriptor=Object.getOwnPropertyDescriptor(context,'__frameyPanelHost');
    let value;
    try{value=await vm.runInContext(expression,vm.createContext(context),{timeout:1000});}
    catch{value={threw:true};}
    const after=Object.getOwnPropertyDescriptor(context,'__frameyPanelHost');
    return {value,getterCalls,ownershipReads,popupReads,ownershipWrites,
      ownershipUnchanged:descriptor.get===after.get&&descriptor.set===after.set};
  }
  const empty=[];
  for(const values of [[0,0,0,0],[undefined,null,0,'0'],['',0,undefined,null]])empty.push(await run(undefined,values));
  const foreign=[];
  for(let i=0;i<4;i++){const values=[0,0,0,0];values[i]=i%2?'foreign-window':71;foreign.push(await run(undefined,values));}
  const invalid=[];
  for(const value of [{},[],false,true,NaN,Infinity,-Infinity,()=>1,Symbol('id'),0n,Promise.resolve(0)]){
    invalid.push(await run(undefined,[0,0,0,value]));
  }
  const missingStore=[];
  for(const value of [undefined,null,0,[],()=>{}])missingStore.push(await run(context=>context.FocusedAppWindowStore=value));
  const missingGetters=[];
  for(const name of names)missingGetters.push(await run((context,store)=>delete store[name]));
  missingGetters.push(await run((context,store)=>store.GetFocusedWindowID='not-callable'));
  const throwing=[];
  for(const name of names)throwing.push(await run((context,store)=>store[name]=()=>{throw Error('private focus identity');}));
  throwing.push(await run((context,store)=>Object.defineProperty(store,'GetFocusedAppID',{get(){throw Error('private');}})));
  throwing.push(await run(context=>Object.defineProperty(context,'FocusedAppWindowStore',{get(){throw Error('private');}})));
  const invalidAlongsideApp=await run(undefined,[71,0,0,{}]);
  const wrongOrigin=await run(context=>context.location.origin='https://unrelated.invalid');
  process.stdout.write(JSON.stringify({empty,foreign,invalid,missingStore,missingGetters,throwing,invalidAlongsideApp,wrongOrigin}));
});
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for target-expression behavior")
class ActiveTargetExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([shutil.which("node"), "--unhandled-rejections=strict", "-e", TARGET_NODE_SCRIPT],
                                input=json.dumps(browser._expression("target")), text=True,
                                capture_output=True, timeout=10)
        if result.returncode:
            raise AssertionError("Target expression behavior failed: " + result.stderr[:1000])
        cls.results = json.loads(result.stdout)

    def test_all_verified_empty_focus_getters_allow_only_steam(self):
        for result in self.results["empty"]:
            with self.subTest(result=result):
                self.assertEqual(result["value"], {"ok": True, "target": "steam"})
                self.assertEqual(result["getterCalls"], ["GetFocusedAppID", "GetFocusedWindowID",
                                                       "GetFocusedOverlayAppID", "GetFocusedOverlayPID"])

    def test_each_nonempty_focus_identity_refuses_unverified_app_mapping(self):
        for result in self.results["foreign"]:
            with self.subTest(result=result):
                self.assertEqual(result["value"], {"ok": False, "code": "app_target_unavailable"})

    def test_missing_throwing_or_invalid_focus_context_is_unavailable(self):
        results = self.results["invalid"] + self.results["missingStore"] + self.results["missingGetters"] + self.results["throwing"]
        results += [self.results["invalidAlongsideApp"], self.results["wrongOrigin"]]
        for result in results:
            with self.subTest(result=result):
                self.assertEqual(result["value"], {"ok": False, "code": "target_context_unavailable"})

    def test_target_probe_never_reads_or_mutates_popup_ownership(self):
        for group in self.results.values():
            for result in group if isinstance(group, list) else [group]:
                with self.subTest(result=result):
                    self.assertEqual(result["ownershipReads"], 0)
                    self.assertEqual(result["popupReads"], 0)
                    self.assertEqual(result["ownershipWrites"], 0)
                    self.assertTrue(result["ownershipUnchanged"])


if __name__ == "__main__":
    unittest.main()
