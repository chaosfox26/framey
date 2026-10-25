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
  assert.ok(e.document.body.textContent.includes("Module error: render unavailable"));
  const failure = e.sent("fail").at(-1);
  assert.deepEqual(failure.arg, { id: "a", what: "render", msg: "render unavailable" });
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
