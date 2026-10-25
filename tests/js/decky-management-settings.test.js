"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js", "fy-settings.js"];
const DEFER = Symbol("deferred reply");
const SHA = "a".repeat(40);
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };
const sources = [
  { id: "stable", label: "Stable", url: "https://example.com/native.json" },
  { id: "testing", label: "Testing", url: "https://example.com/native.json" },
  { id: "custom", label: "Custom", url: "" },
];
const deckySources = [
  { id: "decky_stable", label: "Decky Store", url: "https://example.com/decky.json" },
  { id: "decky_testing", label: "Decky Store Testing", url: "https://example.com/decky-testing.json" },
];
const candidate = { tag: "v1.2.0", commit: SHA, published: "2026-01-01T00:00:00Z", prerelease: false };
const checked = () => ({ loader: { state: "update", candidate }, candidate, installed: { version: "1.1.0" } });
const prefs = () => ({ autocheck: false, notify_loader: false, notify_plugins: false, channel: "stable", workers_max: 3, dev: { enabled: false } });
const state = () => ({ order: [], hidden: [], frozen: {}, prefs: prefs(), safe: false });
const deckyEntry = () => ({
  id: "decky-1", name: "Example Decky utility", version: "1.0.0", description: "Generic integration fixture", category: "Utilities",
  format: "decky", kind: "decky", read_only: true, eligible: true, identity: "b".repeat(64), installed: false,
  compat: { state: "ok", badge: "ok", msg: "A fixture declares compatible requirements" }, platform_declared: true,
  compatibility: { status: "compatible", basis: "declared_requirements", reason: "Fixture declaration only" },
  source: { url: "https://example.com/decky.json" }, versions: [],
});

const boot = (overrides = {}) => {
  const calls = [];
  const replies = Object.assign({
    loader: { commit: "1234567", kept: false }, state_get: state(), status: { settings_state: "ok" },
    autocheck_status: { enabled: false }, maint_prefs: { "maint.enabled_background": false, "maint.window_days": 365 },
    catalog_sources: { source: sources[0], sources, decky_sources: deckySources, custom_url: "" },
    catalog_list: arg => ({ source: [...sources, ...deckySources].find(source => source.id === arg.source), rows: /^decky_/.test(arg.source) ? [deckyEntry()] : [], categories: ["Utilities"], total: /^decky_/.test(arg.source) ? 1 : 0, stale: false, origin: "downloaded" }),
    loader_check: checked(), loader_update: { ok: true },
  }, overrides);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fyCall = raw => {
        const call = JSON.parse(raw);
        calls.push(call);
        if (!call.id) return;
        const reply = replies[call.method];
        const value = typeof reply === "function" ? reply(call.arg, call) : reply;
        if (value === DEFER) return;
        Promise.resolve(value === undefined ? { error: "unhandled " + call.method } : value).then(result => w.Framey._reply(call.gen, call.id, result));
      };
    },
    loadFiles: FILES,
  });
  const FY = env.window.Framey;
  const panel = env.document.querySelector('[role="region"][aria-label="Framey"]');
  const box = () => panel.firstElementChild.firstElementChild;
  const controls = () => [...box().querySelectorAll('[role="button"],[role="switch"],button,select,input')];
  const find = label => {
    const found = controls().find(node => (node.getAttribute("aria-label") || node.textContent) === label);
    assert.ok(found, "missing control " + label);
    return found;
  };
  return {
    env, FY, replies, box, controls, find, calls,
    text: () => box().textContent,
    sent: method => calls.filter(call => call.method === method),
    last: method => calls.filter(call => call.method === method).at(-1),
    open: async id => { FY.open(id); await settle(); },
    click: async label => { find(label).click(); await settle(); },
    answer: async (call, value) => { env.answer(call, value); await settle(); },
  };
};

const tests = [];
const test = (name, run) => tests.push([name, run]);

test("a pending Settings check cannot repaint the real Decky Store destination", async () => {
  const c = boot({ loader_check: DEFER });
  await c.open("#settings");
  await c.click("Check for updates");
  const pending = c.last("loader_check");
  await c.open("#decky-store");
  const destination = c.text();
  assert.ok(destination.includes("Example Decky utility"));
  await c.answer(pending, checked());
  assert.equal(c.text(), destination);
  assert.equal(c.sent("loader_update").length, 0);
});

test("a pending Settings write cannot change the Decky page and a revisit reads confirmed settings", async () => {
  const c = boot({ prefs_set: DEFER });
  await c.open("#settings");
  await c.click("Tell me about module updates");
  const pending = c.last("prefs_set");
  await c.open("#decky-store");
  const destination = c.text();
  await c.answer(pending, { error: "Could not save this setting" });
  assert.equal(c.text(), destination);
  await c.open("#settings");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "false");
});

test("Decky testing selection never changes the Framey release channel", async () => {
  const c = boot();
  await c.open("#decky-store");
  await c.click("Filters");
  const choice = c.find("Source");
  choice.value = "decky_testing";
  choice.dispatchEvent(new c.env.window.Event("change", { bubbles: true }));
  await settle();
  assert.equal(c.last("catalog_list").arg.source, "decky_testing");
  assert.ok(!c.sent("catalog_sources").some(call => call.arg && call.arg.source));
  await c.open("#settings");
  assert.ok(c.text().includes("Update channel: stable."));
  assert.equal(c.sent("prefs_set").length, 0);
  assert.equal(c.sent("loader_update").length, 0);
});

test("declared compatibility and eligibility cannot turn a Decky listing into an install permission", async () => {
  const c = boot();
  await c.open("#decky-store");
  assert.ok(c.text().includes("Example Decky utility"));
  assert.ok(!c.controls().some(node => /^(Install|Update)( |$)/.test(node.getAttribute("aria-label") || node.textContent)));
  assert.equal(c.sent("catalog_install").length, 0);
  assert.equal(c.sent("catalog_assess").length, 0);
  assert.equal(c.sent("loader_update").length, 0);
});

test("the checked loader identity is still pinned after a Decky Store visit", async () => {
  const c = boot();
  await c.open("#decky-store");
  await c.open("#settings");
  await c.click("Update");
  assert.equal(c.sent("loader_check").length, 1);
  assert.equal(c.sent("loader_update").length, 0);
  await c.click("Update now");
  assert.deepEqual(c.last("loader_update").arg, { confirm: false, candidate: { tag: "v1.2.0", commit: SHA } });
});

(async () => {
  let failed = 0;
  for (const [name, run] of tests) {
    try { await run(); console.log("ok   " + name); }
    catch (error) { failed++; console.error("FAIL " + name + "\n" + (error.stack || error)); }
  }
  console.log((tests.length - failed) + " passed, " + failed + " failed, " + tests.length + " total");
  process.exitCode = failed ? 1 : 0;
})();
