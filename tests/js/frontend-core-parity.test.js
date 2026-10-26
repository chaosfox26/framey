"use strict";
const assert = require("assert");
const { makeDom, TOK } = require("./harness");

const cases = [];
const test = (name, run) => cases.push([name, run]);
const plain = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
const bounded = async run => {
  let timer;
  try {
    await Promise.race([run(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("integration test did not settle")), 2000); })]);
  } finally { clearTimeout(timer); }
};

const setup = () => {
  const h = makeDom({
    before(w) { w.FY_RUNTIME = { trusted_input: "off", platform: "linux-aarch64", api: 1, version: "1.0.2" }; },
    loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js"],
  });
  h.onCall(raw => {
    const call = typeof raw === "string" ? JSON.parse(raw) : raw;
    h.calls.push(call);
    if (call.id) queueMicrotask(() => h.answer(call, call.method === "state_get" ? { order: ["alpha", "beta"], hidden: [] } : {}));
  });
  return Object.assign(h, { FY: h.window.Framey, K: h.window.__S.K });
};

test("Store uses the current core renderer and leaves it when navigating to a plugin", async () => {
  const h = setup();
  try {
    h.plugin("alpha");
    let renders = 0, leaves = 0;
    h.FY.pages.store = {
      render(box) { renders++; box.append(h.K.ui.button({ label: "Browse verified catalog", onclick() {} })); },
      leave() { leaves++; },
    };
    h.FY.open("#discover");
    await settle();
    assert.strictEqual(h.K.tab(), "#discover");
    assert.strictEqual(renders, 1);
    assert.ok(h.document.body.textContent.includes("Browse verified catalog"));
    assert.ok(!h.document.body.textContent.includes("Coming soon"));
    h.FY.open("alpha");
    assert.strictEqual(leaves, 1);
    assert.ok(!h.document.body.textContent.includes("Browse verified catalog"));
    h.FY.open("#discover");
    assert.strictEqual(renders, 2);
  } finally { h.K.dispose(); h.window.close(); }
});

test("a Store renderer registered after injection is used on the next open", async () => {
  const h = setup();
  try {
    h.FY.open("#discover");
    await settle();
    h.FY.pages.store = { render(box) { box.textContent = "Catalog is ready"; } };
    h.FY.open("#discover");
    assert.strictEqual(h.K.tab(), "#discover");
    assert.ok(h.document.body.textContent.includes("Catalog is ready"));
  } finally { h.K.dispose(); h.window.close(); }
});

test("Framey and Decky stores have separate renderers and lifecycle hooks", async () => {
  const h = setup();
  try {
    const activity = { nativeRender: 0, nativeLeave: 0, nativeDispose: 0, deckyRender: 0, deckyLeave: 0, deckyDispose: 0 };
    h.FY.pages.store = {
      render(box) { activity.nativeRender++; box.textContent = "Framey-native modules"; },
      leave() { activity.nativeLeave++; },
      dispose() { activity.nativeDispose++; },
    };
    h.FY.pages.decky_store = {
      render(box) { activity.deckyRender++; box.textContent = "Decky community catalog"; },
      leave() { activity.deckyLeave++; },
      dispose() { activity.deckyDispose++; },
    };
    h.FY.open("#discover");
    await settle();
    assert.ok(h.document.body.textContent.includes("Framey-native modules"));
    assert.ok(!h.document.body.textContent.includes("Decky community catalog"));
    h.FY.open("#decky-store");
    assert.strictEqual(h.K.tab(), "#decky-store");
    assert.strictEqual(activity.nativeLeave, 1);
    assert.ok(h.document.body.textContent.includes("Decky community catalog"));
    assert.ok(!h.document.body.textContent.includes("Framey-native modules"));
    h.FY.open("#discover");
    assert.strictEqual(activity.deckyLeave, 1);
    assert.strictEqual(activity.nativeRender, 2);
    h.K.dispose();
    h.K.dispose();
    assert.strictEqual(activity.nativeDispose, 1);
    assert.strictEqual(activity.deckyDispose, 1);
  } finally { h.K.dispose(); h.window.close(); }
});

test("the remembered Decky Store route is restored independently and its module id stays reserved", async () => {
  const h = setup();
  try {
    h.plugin("alpha");
    h.FY.pages.decky_store = { render(box) { box.textContent = "Community catalog ready"; } };
    h.window.sessionStorage.fyTab = "#decky-store";
    h.FY.toggle();
    await settle();
    assert.strictEqual(h.K.tab(), "#decky-store");
    assert.ok(h.document.body.textContent.includes("Community catalog ready"));
    const labels = [...h.document.querySelector('[role="tablist"]').children].map(node => node.textContent);
    assert.deepStrictEqual(labels.slice(-4), ["Modules", "Store", "Decky Store", "Settings"]);
    assert.throws(() => h.window.__bindFramey("decky-store", "reserved-token-0000", { name: "Reserved" }), /reserved/);
  } finally { h.K.dispose(); h.window.close(); }
});

test("plugin proxies cannot mint a core capability for catalog operations", async () => {
  const h = setup();
  try {
    const proxy = h.window.__bindFramey("alpha", "tok-alpha-0000", { name: "Alpha" });
    const api = proxy.register({ render() {} });
    assert.strictEqual(proxy._k, undefined);
    assert.strictEqual(proxy.pages, undefined);
    assert.strictEqual(h.FY._k("not-the-core-token"), null);
    assert.ok(api.pages.register({ id: "discover", render() {} }).error);
    assert.deepStrictEqual(plain(await h.FY.call("_core", "catalog_install", { id: "sample" })), { error: "not authorized" });
    assert.strictEqual(h.sent("catalog_install").length, 0);
    await proxy.call("_core", "catalog_install", { id: "sample" });
    const backend = h.sent("catalog_install")[0];
    assert.strictEqual(backend.plugin, "alpha");
    assert.strictEqual(backend.t, "tok-alpha-0000");
    await api._rpc("catalog_list", {});
    const scoped = h.sent("catalog_list")[0];
    assert.strictEqual(scoped.plugin, "_core");
    assert.strictEqual(scoped.t, "tok-alpha-0000");
    assert.notStrictEqual(scoped.t, TOK);
  } finally { h.K.dispose(); h.window.close(); }
});

test("disposing a plugin clears its pages, mounts, timers and listeners while preserving peers and Store", async () => {
  const h = setup();
  try {
    const a = h.plugin("alpha"), b = h.plugin("beta");
    const seen = { timer: 0, listener: 0, alphaEvent: 0, betaEvent: 0, peerButton: 0 };
    h.FY.pages.store = { render(box) { box.textContent = "Catalog home"; } };
    a.timeout(() => seen.timer++, 50);
    a.on(h.window, "plugin-activity", () => seen.listener++);
    a.events.on("updated", () => seen.alphaEvent++);
    b.events.on("updated", () => seen.betaEvent++);
    a.global.mount("status", { render(box) { box.append(a.ui.button({ label: "Alpha action", onclick() {} })); } });
    const peer = b.global.mount("status", { render(box) { box.append(b.ui.button({ label: "Beta action", onclick() { seen.peerButton++; } })); } });
    b.pages.register({ id: "details", title: "Beta details", render(box) { box.textContent = "Peer page"; } });
    const full = a.pages.register({ id: "options", title: "Alpha settings", mode: "full", render(box) { box.append(a.ui.toggle({ label: "Option", value: false })); } });
    h.FY.open("#discover");
    await settle();
    full.open();
    assert.strictEqual(h.FY.panelOpen, false);
    h.FY.dispose("alpha", TOK);
    await settle();
    h.clock.tick(100);
    h.window.dispatchEvent(new h.window.Event("plugin-activity"));
    h.event("alpha", "updated", {});
    h.event("beta", "updated", {});
    peer.el.querySelector('[role="button"]').click();
    assert.strictEqual(h.document.querySelectorAll('[data-fy="alpha"]').length, 0);
    assert.deepStrictEqual(plain(a.pages.list()), []);
    assert.strictEqual(full.open(), false);
    assert.strictEqual(b.pages.list().length, 1);
    assert.strictEqual(peer.el.isConnected, true);
    assert.strictEqual(h.FY.panelOpen, true);
    assert.ok(h.document.body.textContent.includes("Catalog home"));
    assert.deepStrictEqual(seen, { timer: 0, listener: 0, alphaEvent: 0, betaEvent: 1, peerButton: 1 });
  } finally { h.K.dispose(); h.window.close(); }
});

test("re-registering a module disposes the old extension state without removing the replacement", async () => {
  const h = setup();
  try {
    const first = h.plugin("alpha");
    let oldEvents = 0, newEvents = 0, oldTimers = 0;
    first.events.on("updated", () => oldEvents++);
    first.timeout(() => oldTimers++, 20);
    const oldPage = first.pages.register({ id: "details", render() {} });
    const oldMount = first.global.mount("status", { render(box) { box.textContent = "Old instance"; } });
    const replacement = h.plugin("alpha");
    replacement.events.on("updated", () => newEvents++);
    replacement.pages.register({ id: "details", render(box) { box.textContent = "New instance"; } });
    const newMount = replacement.global.mount("status", { render(box) { box.textContent = "New status"; } });
    h.clock.tick(30);
    h.event("alpha", "updated", {});
    assert.strictEqual(oldMount.el.isConnected, false);
    assert.strictEqual(newMount.el.isConnected, true);
    assert.strictEqual(oldPage.open(), false);
    assert.strictEqual(first.pages.list().length, 0);
    assert.strictEqual(replacement.pages.list().length, 1);
    assert.strictEqual(h.FY.plugins.filter(p => p.id === "alpha" && !p.dead).length, 1);
    assert.deepStrictEqual({ oldEvents, newEvents, oldTimers }, { oldEvents: 0, newEvents: 1, oldTimers: 0 });
    await settle();
  } finally { h.K.dispose(); h.window.close(); }
});

test("retained controls from a disposed module cannot trigger actions or navigation", async () => {
  const h = setup();
  try {
    const api = h.plugin("alpha");
    let actions = 0, movedFocus = 0;
    const button = api.ui.button({ label: "Action", onclick() { actions++; } });
    const toggle = api.ui.toggle({ label: "Option", value: false, onchange() { actions++; } });
    const slider = api.ui.slider({ label: "Amount", min: 0, max: 10, value: 2, oninput() { actions++; }, onchange() { actions++; } });
    const list = api.ui.list({ items: [{ id: "one", label: "One" }, { id: "two", label: "Two" }], selected: "one", onselect() { actions++; } });
    const target = api.ui.button({ label: "Next", onclick() { actions++; } });
    target.focus = () => { movedFocus++; };
    const group = api.ui.group(button, target);
    api.global.mount("controls", { render(box) { box.append(group, toggle, slider, list); } });
    const option = list.querySelectorAll('[role="option"]')[1];
    const before = { toggle: toggle.value, slider: slider.value, selected: plain(list.selected) };
    h.FY.dispose("alpha", TOK);
    button.click();
    toggle.click();
    slider.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    slider.dispatchEvent(new h.window.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
    option.click();
    button.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    assert.strictEqual(actions, 0);
    assert.strictEqual(movedFocus, 0);
    assert.deepStrictEqual({ toggle: toggle.value, slider: slider.value, selected: plain(list.selected) }, before);
    await settle();
  } finally { h.K.dispose(); h.window.close(); }
});

test("private diagnostics accept fixed codes only, saturate counts and expose detached snapshots", () => {
  const h = setup();
  try {
    const proxy = h.window.__bindFramey("alpha", "tok-alpha-0000", { name: "Alpha" });
    const api = proxy.register({ render() {} });
    assert.strictEqual(typeof h.K.diagnostic, "function");
    assert.strictEqual(typeof h.K.diagnostics, "function");
    for (const value of [h.FY, proxy, api]) {
      assert.strictEqual(value.diagnostic, undefined);
      assert.strictEqual(value.diagnostics, undefined);
    }
    const marker = "PRIVATE_CALLBACK_DETAIL_MUST_NOT_BE_COLLECTED";
    let coerced = 0, consoleCalls = 0;
    const opaque = { toString() { coerced++; throw new Error(marker); } };
    h.window.console.error = h.window.console.warn = () => { consoleCalls++; };
    const before = { calls: h.calls.length, timers: h.clock.pending(), html: h.document.body.innerHTML };
    for (const code of [marker, "unknown.callback", "__proto__", null, undefined, opaque]) h.K.diagnostic(code);
    assert.deepStrictEqual(plain(h.K.diagnostics()), []);
    for (let i = 0; i < 10020; i++) h.K.diagnostic("core.event_failed", { message: marker });
    const snapshot = h.K.diagnostics();
    assert.ok(snapshot.length <= 32);
    assert.deepStrictEqual(plain(snapshot), [{ code: "core.event_failed", count: 9999 }]);
    Reflect.set(snapshot[0], "count", -1);
    Reflect.set(snapshot, snapshot.length, { code: marker, count: 1 });
    assert.deepStrictEqual(plain(h.K.diagnostics()), [{ code: "core.event_failed", count: 9999 }]);
    assert.strictEqual(coerced, 0);
    assert.strictEqual(consoleCalls, 0);
    assert.strictEqual(h.calls.length, before.calls);
    assert.strictEqual(h.clock.pending(), before.timers);
    assert.strictEqual(h.document.body.innerHTML, before.html);
    assert.ok(!JSON.stringify(h.K.diagnostics()).includes(marker));
    const old = h.K;
    old.dispose();
    assert.deepStrictEqual(plain(old.diagnostics()), [{ code: "core.event_failed", count: 9999 }]);
    h.window.eval('document.querySelectorAll("[data-fy]").forEach(node => node.remove());delete window.Framey');
    h.load("inject.js");
    assert.deepStrictEqual(plain(h.window.__S.K.diagnostics()), []);
    h.window.__S.K.dispose();
  } finally { h.K.dispose(); h.window.close(); }
});

test("repeated extension initialization preserves callback order and isolates core event failures privately", () => {
  const h = setup();
  try {
    const extensions = h.FY.ext.length;
    for (const file of ["fy-api.js", "fy-ui.js", "fy-pages.js"]) h.load(file);
    assert.strictEqual(h.FY.ext.length, extensions);
    const order = [];
    const marker = "PRIVATE_EVENT_DETAIL_MUST_NOT_BE_COLLECTED";
    const firstOff = h.K.listen("progress", () => { order.push("first"); throw new Error(marker); });
    const lastOff = h.K.listen("progress", () => order.push("last"));
    const before = { calls: h.calls.length, timers: h.clock.pending(), html: h.document.body.innerHTML };
    h.event("_core", "progress", { privateDetail: marker });
    h.event("_core", "progress", { privateDetail: marker });
    assert.deepStrictEqual(order, ["first", "last", "first", "last"]);
    assert.strictEqual(h.K.diagnostics().find(row => row.code === "core.event_failed").count, 2);
    firstOff();
    lastOff();
    h.event("_core", "progress", {});
    assert.deepStrictEqual(order, ["first", "last", "first", "last"]);
    assert.strictEqual(h.calls.length, before.calls);
    assert.strictEqual(h.clock.pending(), before.timers);
    assert.strictEqual(h.document.body.innerHTML, before.html);
    assert.ok(!JSON.stringify(h.K.diagnostics()).includes(marker));
  } finally { h.K.dispose(); h.window.close(); }
});

test("panel callback isolation records a fixed code without suppressing later callbacks or leaking the error", async () => {
  const h = setup();
  try {
    const marker = "PRIVATE_PANEL_DETAIL_MUST_NOT_BE_COLLECTED";
    const order = [];
    const badOff = h.FY.onPanel(open => { order.push("first:" + open); throw new Error(marker); });
    const goodOff = h.FY.onPanel(open => order.push("last:" + open));
    h.FY.open("#modules");
    await settle();
    h.FY.toggle();
    assert.deepStrictEqual(order, ["first:true", "last:true", "first:false", "last:false"]);
    assert.strictEqual(h.K.diagnostics().find(row => row.code === "core.panel_failed").count, 2);
    assert.ok(!h.document.body.textContent.includes(marker));
    assert.ok(!JSON.stringify(h.calls).includes(marker));
    assert.ok(!JSON.stringify(h.K.diagnostics()).includes(marker));
    assert.strictEqual(h.sent("log").length, 0);
    assert.strictEqual(h.document.querySelectorAll('[role="alert"],[role="status"]').length, 0);
    badOff();
    goodOff();
    h.FY.open("#modules");
    await settle();
    assert.strictEqual(order.length, 4);
    assert.strictEqual(h.K.diagnostics().find(row => row.code === "core.panel_failed").count, 2);
  } finally { h.K.dispose(); h.window.close(); }
});

(async () => {
  let passed = 0;
  for (const [name, run] of cases) {
    try { await bounded(run); passed++; }
    catch (error) { console.error("FAIL " + name + "\n" + (error && error.stack || error)); process.exitCode = 1; }
  }
  console.log(passed + "/" + cases.length + " frontend core parity integration tests passed");
})();
