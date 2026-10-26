"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom, TOK } = require("./harness");
const fresh = options => makeDom(Object.assign({ loadFiles: ["fy-api.js"] }, options));
const plain = value => JSON.parse(JSON.stringify(value));
const cleanup = (t, e) => { t.after(() => e.window.close()); return e; };
const modulePage = (e, id) => e.window.__bindFramey(id, "tok-" + id + "-0000", { name: id, short: id }).register({ render(box) { box.textContent = "page " + id; } });
const state = (e, hidden) => e.answer(e.sent("state_get").at(-1), { order: ["a", "b"], hidden });

test("disposing a module settles its in-flight backend call and discards its late reply", async t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  const promise = api.call("read");
  const request = e.sent("read").at(-1);
  e.window.Framey.dispose("a", TOK);
  assert.deepEqual(plain(await promise), { error: "module disposed", code: "disposed" });
  assert.equal(e.window.__S.K.pending(), 0);
  e.answer(request, { ok: "stale" });
  assert.equal(e.window.__S.K.pending(), 0);
});

test("disposing a module rejects an in-flight scoped RPC with the disposal code", async t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  const promise = api._rpc("fetch", { url: "https://example.com" });
  const rejected = assert.rejects(promise, error => error.message === "module disposed" && error.code === "disposed");
  e.window.Framey.dispose("a", TOK);
  await rejected;
});

test("disposing a pending pre-registration proxy call also settles it", async t => {
  const e = cleanup(t, fresh());
  const proxy = e.window.__bindFramey("a", "tok-a-0000", { name: "a" });
  const promise = proxy.call("a", "read");
  e.window.__S.K.dispose("a");
  assert.equal((await promise).code, "disposed");
  assert.equal(e.window.__S.K.pending(), 0);
});

test("full disposal settles core-owned requests and clears their deadlines", async t => {
  const e = cleanup(t, fresh());
  const pending = e.window.__S.K.call("catalog_list");
  const request = e.sent("catalog_list").at(-1);
  e.window.__S.K.dispose();
  assert.deepEqual(plain(await pending), { error: "Framey panel disposed", code: "disposed" });
  assert.equal(e.clock.pending(), 0);
  e.answer(request, { order: ["stale"], hidden: [] });
  assert.equal(e.window.__S.K.pending(), 0);
});

test("a replaced module's reply cannot resolve a new instance's request", async t => {
  const e = cleanup(t, fresh()), first = e.plugin("a");
  const abandoned = first.call("read");
  const oldRequest = e.sent("read").at(-1);
  const second = e.plugin("a");
  assert.equal((await abandoned).code, "disposed");
  const next = second.call("read");
  const newRequest = e.sent("read").at(-1);
  e.answer(oldRequest, { value: "stale" });
  assert.equal(e.window.__S.K.pending(), 1);
  e.answer(newRequest, { value: "current" });
  assert.deepEqual(plain(await next), { value: "current" });
});

test("a timer rejected at the cap does not abandon accepted timer cleanup", t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  for (let i = 0; i < 256; i++) api.timeout(() => {}, 60000);
  assert.throws(() => api.timeout(() => {}, 60000), /too many timers/);
  assert.equal(e.clock.pending(), 256);
  e.window.Framey.dispose("a", TOK);
  assert.equal(e.clock.pending(), 0);
});

test("an interval rejected at the cap leaves every accepted interval managed", t => {
  const active = new Set();
  let next = 0;
  const e = cleanup(t, fresh({ before(w) {
    w.setInterval = () => { active.add(++next); return next; };
    w.clearInterval = handle => active.delete(handle);
  } }));
  const api = e.plugin("a");
  for (let i = 0; i < 256; i++) api.interval(() => {}, 60000);
  assert.throws(() => api.interval(() => {}, 60000), /too many intervals/);
  assert.equal(active.size, 256);
  e.window.Framey.dispose("a", TOK);
  assert.equal(active.size, 0);
});

test("automatic reopen skips a hidden remembered tab while explicit opens remain available", t => {
  const e = cleanup(t, fresh());
  modulePage(e, "a"); modulePage(e, "b");
  e.window.Framey.open("b");
  state(e, ["a"]);
  e.window.sessionStorage.fyTab = "a";
  e.window.Framey.toggle();
  e.window.Framey.toggle();
  assert.equal(e.window.sessionStorage.fyTab, "b");
  e.window.Framey.open("a");
  assert.equal(e.window.sessionStorage.fyTab, "a");
  assert.ok(e.document.body.textContent.includes("page a"));
  e.window.Framey.toggle();
  e.window.Framey.toggle();
  assert.equal(e.window.sessionStorage.fyTab, "b");
});

test("hiding the currently selected module moves the panel to a visible tab", t => {
  const e = cleanup(t, fresh());
  modulePage(e, "a"); modulePage(e, "b");
  e.window.Framey.open("a");
  state(e, ["a"]);
  assert.equal(e.window.sessionStorage.fyTab, "b");
  assert.ok(e.document.body.textContent.includes("page b"));
});

test("Store delegates render, leave and an idempotent dispose to its loaded page", t => {
  const e = cleanup(t, fresh());
  const seen = [];
  e.window.Framey.pages = { store: {
    render(box) { seen.push("render"); box.textContent = "Available modules"; },
    leave() { seen.push("leave"); },
    dispose() { seen.push("dispose"); },
  } };
  e.window.Framey.open("#discover");
  assert.ok(e.document.body.textContent.includes("Available modules"));
  e.window.Framey.open("#modules");
  e.window.__S.K.dispose();
  e.window.__S.K.dispose();
  assert.deepEqual(seen, ["render", "leave", "dispose"]);
});

test("a missing Store module offers a working route back to module management", t => {
  const e = cleanup(t, fresh());
  e.window.Framey.open("#discover");
  assert.ok(e.document.body.textContent.includes("Store is not available."));
  const button = [...e.document.querySelectorAll('[role="button"]')].find(x => x.textContent === "Open Modules");
  assert.ok(button);
  button.click();
  assert.equal(e.window.sessionStorage.fyTab, "#modules");
});

test("selected rail text meets normal-text contrast against its background", t => {
  const e = cleanup(t, fresh());
  e.window.Framey.open("#modules");
  const selected = e.document.querySelector('[role="tab"][aria-selected="true"]');
  const rgb = e.window.getComputedStyle(selected).backgroundColor.match(/\d+/g).map(Number);
  const lin = rgb.map(x => x / 255).map(x => x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
  const luminance = lin[0] * 0.2126 + lin[1] * 0.7152 + lin[2] * 0.0722;
  assert.ok(1.05 / (luminance + 0.05) >= 4.5);
});


test("scoped RPCs initiated after disposal preserve the disposal error code", async t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  e.window.Framey.dispose("a", TOK);
  await assert.rejects(api._rpc("fetch", {}), error => error.message === "module disposed" && error.code === "disposed");
});

test("catalog metadata reads have a bounded network deadline and installs retain their transaction deadline", async t => {
  const e = cleanup(t, fresh()), core = e.window.__S.K;
  const reads = ["catalog_sources", "catalog_list", "catalog_details", "catalog_assess"].map(method => core.call(method));
  const install = core.call("catalog_install");
  e.clock.tick(54999);
  assert.equal(core.pending(), 5);
  e.clock.tick(1);
  assert.equal(core.pending(), 1);
  for (const answer of await Promise.all(reads)) assert.match(answer.error, /55 s$/);
  e.clock.tick(545000);
  assert.equal(core.pending(), 0);
  assert.match((await install).error, /600 s$/);
});


test("a current plugin render rejection is contained and reported as a render failure", async t => {
  const e = cleanup(t, fresh());
  e.window.__bindFramey("a", "tok-a-0000", { name: "a" }).register({
    render(box) { box.textContent = "Loading a"; return Promise.reject(new Error("render unavailable")); },
  });
  e.window.Framey.open("a");
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(e.document.body.textContent.includes("Module error: render failed."));
  const failure = e.sent("fail").at(-1);
  assert.deepEqual(failure.arg, { id: "a", what: "render", msg: "render failed" });
});

test("a late render rejection from a previous view cannot replace or report against the current view", async t => {
  const e = cleanup(t, fresh());
  let reject;
  e.window.__bindFramey("a", "tok-a-0000", { name: "a" }).register({
    render(box) { box.textContent = "Loading a"; return new Promise((resolve, no) => { reject = no; }); },
  });
  modulePage(e, "b");
  e.window.Framey.open("a");
  e.window.Framey.open("b");
  reject(new Error("late render failure"));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(e.document.body.textContent.includes("page b"));
  assert.ok(!e.document.body.textContent.includes("late render failure"));
  assert.equal(e.sent("fail").length, 0);
});

test("a render rejection from a disposed instance is ignored even while its body stays attached", async t => {
  const e = cleanup(t, fresh());
  let reject, body;
  e.window.__bindFramey("a", "tok-a-0000", { name: "a" }).register({
    render(box) { body = box; box.textContent = "Loading a"; return new Promise((resolve, no) => { reject = no; }); },
  });
  e.window.Framey.open("a");
  e.window.Framey.dispose("a", TOK);
  assert.equal(body.isConnected, true);
  reject(new Error("disposed render failure"));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(!e.document.body.textContent.includes("disposed render failure"));
  assert.equal(e.sent("fail").length, 0);
});


test("Decky Store is a distinct core route with independent page lifecycle", t => {
  const e = cleanup(t, fresh()), seen = [];
  e.window.Framey.pages = {
    store: { render(box) { seen.push("framey render"); box.textContent = "Framey catalog"; }, leave() { seen.push("framey leave"); }, dispose() { seen.push("framey dispose"); } },
    decky_store: { render(box) { seen.push("decky render"); box.textContent = "Decky catalog"; }, leave() { seen.push("decky leave"); }, dispose() { seen.push("decky dispose"); } },
  };
  e.window.Framey.open("#discover");
  assert.ok(e.document.body.textContent.includes("Framey catalog"));
  e.window.Framey.open("#decky-store");
  assert.ok(e.document.body.textContent.includes("Decky catalog"));
  assert.equal(e.window.sessionStorage.fyTab, "#decky-store");
  e.window.Framey.open("#modules");
  e.window.__S.K.dispose(); e.window.__S.K.dispose();
  assert.deepEqual(seen, ["framey render", "framey leave", "decky render", "decky leave", "framey dispose", "decky dispose"]);
});

test("Decky Store has a reserved id and a missing-renderer recovery route", t => {
  const e = cleanup(t, fresh());
  assert.throws(() => e.window.__bindFramey("decky-store", "tok-decky-store-0000", {}), /reserved/);
  e.window.Framey.open("#decky-store");
  assert.ok(e.document.body.textContent.includes("Decky Store is not available."));
  const recover = [...e.document.querySelectorAll('[role="button"]')].find(x => x.textContent === "Open Modules");
  assert.ok(recover); recover.click();
  assert.equal(e.window.sessionStorage.fyTab, "#modules");
});

test("a remembered Decky Store route survives automatic reopen and unrelated plugin hiding", t => {
  const e = cleanup(t, fresh());
  modulePage(e, "a"); modulePage(e, "b");
  e.window.Framey.open("#decky-store");
  state(e, ["a", "b"]);
  e.window.Framey.toggle(); e.window.Framey.toggle();
  assert.equal(e.window.sessionStorage.fyTab, "#decky-store");
  const labels = [...e.document.querySelectorAll('[role="tab"]')].map(x => x.textContent);
  assert.deepEqual(labels, ["Modules", "Store", "Decky Store", "Settings"]);
});

test("a stale Decky Store render failure cannot overwrite the Framey Store", async t => {
  const e = cleanup(t, fresh());
  let reject;
  e.window.Framey.pages = {
    store: { render(box) { box.textContent = "Framey catalog"; } },
    decky_store: { render(box) { box.textContent = "Loading Decky catalog"; return new Promise((resolve, no) => { reject = no; }); } },
  };
  e.window.Framey.open("#decky-store");
  e.window.Framey.open("#discover");
  reject(new Error("late Decky result"));
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(e.document.body.textContent.includes("Framey catalog"));
  assert.ok(!e.document.body.textContent.includes("late Decky result"));
});

test("rail focus reveals the selected tab and stays bounded at keyboard edges", t => {
  const revealed = [];
  const e = cleanup(t, fresh({ before(w) { w.Element.prototype.scrollIntoView = function(options) { revealed.push([this.textContent, options.block]); }; } }));
  e.window.Framey.open("#decky-store");
  const selected = e.document.querySelector('[role="tab"][aria-selected="true"]');
  assert.equal(e.document.activeElement, selected);
  assert.ok(revealed.some(x => x[0] === "Decky Store" && x[1] === "nearest"));
  selected.dispatchEvent(new e.window.KeyboardEvent("keydown", { key: "Home", bubbles: true, cancelable: true }));
  const first = e.document.activeElement;
  const up = new e.window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true });
  first.dispatchEvent(up);
  assert.equal(up.defaultPrevented, true);
  assert.equal(e.document.activeElement, first);
  first.dispatchEvent(new e.window.KeyboardEvent("keydown", { key: "End", bubbles: true, cancelable: true }));
  assert.equal(e.document.activeElement.textContent, "Settings");
  assert.deepEqual(revealed.at(-1), ["Settings", "nearest"]);
});

test("rail and panel keep a scrolling viewport bound and associate tabs with their content", t => {
  const e = cleanup(t, fresh());
  e.window.Framey.open("#decky-store");
  const panel = e.document.querySelector('[role="region"][aria-label="Framey"]');
  const rail = e.document.querySelector('[role="tablist"]');
  const selected = rail.querySelector('[aria-selected="true"]');
  const content = e.document.querySelector('[role="tabpanel"]');
  assert.equal(rail.style.overflowY, "auto");
  assert.equal(parseFloat(rail.style.minHeight), 0);
  assert.ok(rail.style.maxHeight.includes("100vh"));
  assert.ok(panel.style.maxHeight.includes("100vh"));
  assert.ok(panel.style.maxWidth.includes("100vw"));
  assert.equal(selected.getAttribute("aria-controls"), content.id);
  assert.equal(content.getAttribute("aria-labelledby"), selected.id);
});

test("global cleanup cannot reopen a disposed catalog synchronously or from queued cleanup work", async t => {
  const e = cleanup(t, fresh());
  const F = e.window.Framey, core = e.window.__S.K;
  let renders = 0, during;
  F.pages = { decky_store: {
    render() { renders++; },
    dispose() { during = core.call("catalog_sources"); F.open("#decky-store"); },
  } };
  e.window.__bindFramey("a", "tok-a-0000", {}).register({ render() {}, dispose() {
    F.open("#decky-store");
    Promise.resolve().then(() => { F.open("#decky-store"); core.call("catalog_sources"); });
  } });
  F.open("#decky-store");
  const before = e.calls.length;
  core.dispose();
  assert.equal((await during).code, "disposed");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(renders, 1);
  assert.equal(e.calls.length, before);
  assert.equal(core.pending(), 0);
  assert.equal(e.clock.pending(), 0);
});

test("an old globally disposed instance permanently blocks navigation, binding and ordinary RPC", async t => {
  const e = cleanup(t, fresh()), F = e.window.Framey, core = e.window.__S.K;
  const proxy = e.window.__bindFramey("a", "tok-a-0000", {});
  F.open("#modules");
  F.toggle();
  core.dispose();
  const before = e.calls.length;
  F.open("#decky-store"); F.toggle(); F.restore(); F.redraw();
  core.post("toggle"); core.post("panel", true);
  assert.equal((await core.call("catalog_sources")).code, "disposed");
  assert.equal((await proxy.call("a", "read")).code, "disposed");
  assert.throws(() => core.bind("b", "tok-b-0000", {}), /panel disposed/);
  assert.throws(() => proxy.register({ render() {} }), /panel disposed/);
  assert.equal(e.calls.length, before);
  assert.equal(core.pending(), 0);
  assert.equal(e.document.querySelector('[role="region"][aria-label="Framey"]').style.display, "none");
});

test("scoped plugin disposal still restores the panel hidden by its full page", t => {
  const e = cleanup(t, fresh({ loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js"] }));
  const api = e.plugin("a");
  const page = api.pages.register({ id: "full", mode: "full", render(box) { box.textContent = "Full page"; } });
  e.window.Framey.open("#decky-store");
  page.open();
  const panel = e.document.querySelector('[role="region"][aria-label="Framey"]');
  assert.equal(panel.style.display, "none");
  e.window.Framey.dispose("a", TOK);
  assert.equal(panel.style.display, "flex");
  assert.equal(e.window.sessionStorage.fyTab, "#decky-store");
});

test("global cleanup still sends already-owned resource token revocations", async t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  const resource = api.resource("data", "cover.png");
  e.answer(e.sent("resource").at(-1), { url: "http://127.0.0.1/resource/test", token: "owned-token" });
  await resource;
  e.window.__S.K.dispose();
  const revokes = e.sent("resource").filter(x => x.arg && x.arg.revoke);
  assert.equal(revokes.length, 1);
  assert.equal(revokes[0].arg.revoke, "owned-token");
  assert.equal(e.window.__S.K.pending(), 0);
});

test("a resource resolved just before global teardown revokes its token from the queued continuation", async t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  const token = "c".repeat(32);
  const pending = api.resource("data", "cover.png");
  const request = e.sent("resource").at(-1);
  e.answer(request, { url: "http://127.0.0.1/resource/queued", token });
  const rejected = assert.rejects(pending, error => error.code === "disposed");
  e.window.__S.K.dispose();
  await rejected;
  const revoke = e.sent("resource").filter(x => x.arg && x.arg.revoke);
  assert.equal(revoke.length, 1);
  assert.equal(revoke[0].id, 0);
  assert.equal(revoke[0].t, request.t);
  assert.deepEqual(revoke[0].arg, { revoke: token, pid: "a" });
  assert.equal(e.window.__S.K.pending(), 0);
  assert.equal(e.clock.pending(), 0);
});

test("the dead-instance cleanup exception accepts only bounded owner-scoped resource revocations", t => {
  const e = cleanup(t, fresh());
  let pluginPost;
  e.window.Framey.ext.push((api, p, res, io) => { pluginPost = io.post; });
  e.plugin("a");
  const core = e.window.__S.K, token = "d".repeat(32);
  core.dispose();
  const before = e.calls.length;
  for (const [method, arg] of [
    ["catalog_sources", { revoke: token, pid: "a" }],
    ["resource", { revoke: token, pid: "b" }],
    ["resource", { revoke: token, pid: "a", path: "cover.png" }],
    ["resource", { revoke: token, pid: "a", root: "data" }],
    ["resource", { revoke: "x".repeat(129), pid: "a" }],
    ["resource", { revoke: "", pid: "a" }],
    ["resource", { revoke: "../bad", pid: "a" }],
    ["resource", { revoke: token }],
    ["resource", [token, "a"]],
  ]) pluginPost(method, arg);
  core.post("resource", { revoke: token, pid: "a" });
  assert.equal(e.calls.length, before);
  pluginPost("resource", { revoke: token, pid: "a" });
  assert.equal(e.calls.length, before + 1);
  assert.equal(e.calls.at(-1).id, 0);
  assert.equal(e.calls.at(-1).t, "tok-a-0000");
  assert.deepEqual(e.calls.at(-1).arg, { revoke: token, pid: "a" });
  assert.equal(core.pending(), 0);
});

test("diagnostics accept only fixed primitive codes, saturate, and return detached private snapshots", t => {
  const e = cleanup(t, fresh()), core = e.window.__S.K;
  const privateValue = { toString() { throw new Error("must not coerce diagnostic input"); } };
  for (const invalid of [privateValue, null, undefined, 7, Symbol("private-value"), "core.unknown", "private callback detail"]) {
    assert.doesNotThrow(() => core.diagnostic(invalid));
  }
  assert.deepEqual(plain(core.diagnostics()), []);
  const beforeCalls = e.calls.length, beforeTimers = e.clock.pending();
  for (let i = 0; i < 12000; i++) core.diagnostic("core.callback_failed");
  const snapshot = core.diagnostics();
  assert.deepEqual(plain(snapshot), [{ code: "core.callback_failed", count: 9999 }]);
  snapshot[0].code = "private-value"; snapshot[0].count = -1; snapshot.push({ code: "private-value", count: 10 });
  assert.deepEqual(plain(core.diagnostics()), [{ code: "core.callback_failed", count: 9999 }]);
  assert.equal(e.calls.length, beforeCalls);
  assert.equal(e.clock.pending(), beforeTimers);
  assert.equal(e.document.querySelectorAll('[role="status"],[role="alert"]').length, 0);
  core.dispose();
  assert.deepEqual(plain(core.diagnostics()), [{ code: "core.callback_failed", count: 9999 }]);
});

test("extension failure is counted without inspecting the exception or skipping the next extension", t => {
  const e = cleanup(t, fresh());
  let inspected = 0, continued = 0;
  const failure = { get message() { inspected++; return "private callback detail"; }, toString() { inspected++; return "private callback detail"; } };
  e.window.Framey.ext.push(() => { throw failure; });
  e.window.Framey.ext.push(() => { continued++; });
  const api = e.plugin("a");
  assert.equal(api.id, "a");
  assert.equal(continued, 1);
  assert.equal(inspected, 0);
  assert.deepEqual(plain(e.window.__S.K.diagnostics()), [{ code: "core.extension_failed", count: 1 }]);
  assert.equal(e.document.querySelectorAll('[role="status"],[role="alert"]').length, 0);
});

test("cleanup failures remain isolated and count fixed codes without raw-error popups", t => {
  const e = cleanup(t, fresh());
  let inspected = 0, cleaned = 0;
  const failure = { get message() { inspected++; return "private cleanup detail"; }, toString() { inspected++; return "private cleanup detail"; } };
  const api = e.window.__bindFramey("a", "tok-a-0000", {}).register({ render() {}, dispose() { throw failure; } });
  api.cleanup(() => { cleaned++; });
  api.cleanup(() => { throw failure; });
  e.window.__S.K.dispose();
  assert.equal(cleaned, 1);
  assert.equal(inspected, 0);
  assert.deepEqual(plain(e.window.__S.K.diagnostics()), [{ code: "core.cleanup_failed", count: 2 }]);
  assert.equal(e.document.querySelectorAll('[role="status"],[role="alert"]').length, 0);
});

test("timer diagnostics preserve thrown identity and leave later callbacks and cleanup working", t => {
  const intervals = new Map();
  let sequence = 0, cleared = 0, inspected = 0;
  const e = cleanup(t, fresh({ before(w) {
    w.setInterval = fn => { intervals.set(++sequence, fn); return sequence; };
    w.clearInterval = handle => { if (intervals.delete(handle)) cleared++; };
  } }));
  const api = e.plugin("a"), failure = { get message() { inspected++; return "private timer detail"; } };
  api.timeout(() => { throw failure; }, 1);
  assert.throws(() => e.clock.tick(1), error => error === failure);
  let completed = 0;
  api.timeout(() => { completed++; }, 1);
  e.clock.tick(1);
  let intervalTicks = 0;
  const handle = api.interval(() => { if (++intervalTicks === 1) throw failure; completed++; }, 10);
  assert.throws(() => intervals.get(handle)(), error => error === failure);
  intervals.get(handle)();
  assert.equal(completed, 2);
  assert.equal(inspected, 0);
  assert.deepEqual(plain(e.window.__S.K.diagnostics()), [{ code: "core.timer_failed", count: 2 }]);
  e.window.Framey.dispose("a", TOK);
  assert.equal(cleared, 1);
  assert.equal(e.clock.pending(), 0);
});

test("timer diagnostics do not inspect promise-like return values", t => {
  const e = cleanup(t, fresh()), api = e.plugin("a");
  let thenReads = 0;
  api.timeout(() => ({ get then() { thenReads++; throw new Error("must not inspect timer return"); } }), 1);
  e.clock.tick(1);
  assert.equal(thenReads, 0);
  assert.deepEqual(plain(e.window.__S.K.diagnostics()), []);
});

test("storage failures are counted without exposing exception fields or stopping navigation", t => {
  const e = cleanup(t, fresh());
  let inspected = 0;
  const failure = { get message() { inspected++; return "private storage detail"; } };
  Object.defineProperty(e.window, "sessionStorage", { configurable: true, get() { throw failure; } });
  assert.doesNotThrow(() => e.window.Framey.open("#modules"));
  assert.equal(e.document.querySelector('[role="tab"][aria-selected="true"]').textContent, "Modules");
  const counters = plain(e.window.__S.K.diagnostics());
  assert.ok(counters.some(row => row.code === "core.storage_failed" && row.count > 0));
  assert.equal(inspected, 0);
  assert.ok(!JSON.stringify(counters).includes("private storage detail"));
});

test("the diagnostic recorder is initialized before a best-effort binding property failure", t => {
  let inspected = 0;
  const failure = { get message() { inspected++; return "private binding detail"; } };
  const e = cleanup(t, fresh({ before(w) {
    const define = w.Object.defineProperty;
    w.Object.defineProperty = (object, name, descriptor) => {
      if (object === w && name === "fyCall") throw failure;
      return define(object, name, descriptor);
    };
  } }));
  assert.deepEqual(plain(e.window.__S.K.diagnostics()), [{ code: "core.binding_failed", count: 1 }]);
  assert.equal(e.plugin("a").id, "a");
  assert.equal(inspected, 0);
});

test("captured request serialization and Promise construction survive later page replacements", async t => {
  const e = cleanup(t, fresh()), core = e.window.__S.K;
  let stringifyReads = 0, promiseCalls = 0;
  const originalStringify = e.window.JSON.stringify;
  e.window.JSON.stringify = (...args) => { stringifyReads++; return originalStringify(...args); };
  e.window.Promise = function ReplacedPromise() { promiseCalls++; throw new Error("page Promise replacement"); };
  const pending = core.call("state_get");
  const request = e.sent("state_get").at(-1);
  e.answer(request, { order: [], hidden: [] });
  assert.deepEqual(plain(await pending), { order: [], hidden: [] });
  assert.equal(stringifyReads, 0);
  assert.equal(promiseCalls, 0);
});

test("render containment never inspects a thrown value with a hostile message getter", t => {
  const e = cleanup(t, fresh());
  let inspected = 0;
  const failure = Object.create(null, { message: { get() { inspected++; throw new Error("hostile error getter"); } } });
  e.window.__bindFramey("a", "tok-a-0000", { name: "a" }).register({ render() { throw failure; } });
  assert.doesNotThrow(() => e.window.Framey.open("a"));
  assert.equal(inspected, 0);
  assert.ok(e.document.body.textContent.includes("Module error: render failed."));
  assert.deepEqual(e.sent("fail").at(-1).arg, { id: "a", what: "render", msg: "render failed" });
  assert.ok(plain(e.window.__S.K.diagnostics()).some(row => row.code === "core.callback_failed" && row.count === 1));
});

test("global teardown closes the real report helper and settles its modal and pending draft", async t => {
  const e = cleanup(t, fresh({ loadFiles: ["fy-api.js", "fy-report.js"] }));
  const F = e.window.Framey, core = e.window.__S.K;
  F.open("#modules");
  e.answer(e.sent("state_get").at(-1), { order: [], hidden: [] });
  const pending = core.report({ code: "fixture" });
  const request = e.sent("report_draft").at(-1);
  const panel = e.document.querySelector('[role="region"][aria-label="Framey"]');
  assert.ok(e.document.querySelector('[role="dialog"]'));
  assert.equal(panel.hasAttribute("inert"), true);
  core.dispose();
  assert.deepEqual(plain(await pending), { closed: true });
  assert.equal(e.document.querySelector('[role="dialog"]'), null);
  assert.equal(panel.hasAttribute("inert"), false);
  assert.equal(core.pending(), 0);
  const count = e.calls.length;
  e.answer(request, { title: "Late fixture", body: "Late fixture", diagnostics: "" });
  await new Promise(resolve => setImmediate(resolve));
  core.dispose();
  assert.equal(e.document.querySelector('[role="dialog"]'), null);
  assert.equal(e.calls.length, count);
  assert.equal(core.pending(), 0);
});
