"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT, TOK } = require("./harness");

const HEAD = ["inject.js", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js"];
const TAIL = ["fy-modules.js", "fy-settings.js"];
const SAFE = ["inject.js", "fy-api.js", "fy-ui.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const RM = "document.querySelectorAll('[data-fy]').forEach(e=>e.remove());";
const WIPE = RM + "delete window.Framey";
const WIPE_BAR = "window.FYBar&&window.FYBar.dispose&&window.FYBar.dispose();window.FYObs&&window.FYObs.disconnect();" + RM + "delete window.FYBar;delete window.FYObs";
const wipe = w => { w.Framey._k(TOK).dispose(); w.eval(WIPE); };
const RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en" };

const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };
const read = f => fs.readFileSync(path.join(ROOT, f), "utf8");

const plugin = (id, name, code) => ({
  meta: { id, name, short: name.split(" ")[0], version: "1.0.0" },
  code: code || "Framey.register({ render(box) { box.append(Framey.el('', 'home of " + id + "')); } });",
});
const FAN = plugin("fan", "Fan Control", [
  "const api = Framey.register({ render(box) { box.append(Framey.el('', 'home of fan')); } });",
  "window.fanApi = api;",
  "api.pages.register({ id: 'cfg', title: 'Curve editor', render(box) { box.append(Framey.el('', 'curve page')); } });",
].join("\n"));
const ALPHA = plugin("alpha", "Alpha");
const BETA = plugin("beta", "Beta");

const lib = (id, name, extra) => Object.assign({ id, name, version: "1.0.0", commit: "abc1234", enabled: true, nosource: "", pending: false, compat: { badge: "ok", msg: "" }, fail: null, stuck: false, linked: false }, extra);

const loader = (over = {}) => {
  const S = Object.assign({ order: ["alpha", "beta", "fan"], sort: "custom", hidden: [], frozen: {}, prefs: { channel: "stable", autocheck: false, notify_loader: false, notify_plugins: false, dev: { enabled: false } }, safe: false }, over.state);
  const shape = () => JSON.parse(JSON.stringify(S));
  const R = {
    state_get: () => shape(),
    mod_order: a => {
      if (a.ids) S.order = a.ids.slice();
      else {
        const i = S.order.indexOf(a.id);
        const j = i + a.delta;
        if (i >= 0 && j >= 0 && j < S.order.length) S.order.splice(j, 0, S.order.splice(i, 1)[0]);
      }
      return shape();
    },
    mod_hide: a => {
      S.hidden = S.hidden.filter(x => x !== a.id).concat(a.hidden ? [a.id] : []);
      return shape();
    },
    prefs_set: a => {
      Object.keys(a).forEach(k => { if (k === "safe") S.safe = a[k]; });
      return shape();
    },
    list: () => [lib("alpha", "Alpha"), lib("beta", "Beta"), lib("fan", "Fan Control", { fail: { what: "backend", msg: "kaput 9", at: 1700000000 } })],
    store: () => [],
    loader: () => ({ commit: "aaaaaaa", kept: false }),
    autocheck_status: () => ({ enabled: false, last: 0, next_ok: 0, fails: 0 }),
    reload: () => ({ ok: true }),
    panel: () => ({}),
    asset: a => {
      R.env.load(a.name === "dev" ? "fy-dev.js" : "fy-picker.js");
      return { ok: true };
    },
    dev_status: () => ({ enabled: true, hot: [], exposure: { state: "closed", addresses: [] } }),
  };
  return Object.assign(R, over.methods);
};

const listeners = w => {
  const live = new Set();
  const ids = new WeakMap();
  let next = 0;
  const id = f => {
    if (!ids.has(f)) ids.set(f, ++next);
    return ids.get(f);
  };
  const tid = new WeakMap();
  const tag = t => {
    if (!tid.has(t)) tid.set(t, t === w ? "window" : t === w.document ? "document" : "n" + ++next);
    return tid.get(t);
  };
  const add = w.EventTarget.prototype.addEventListener;
  const rem = w.EventTarget.prototype.removeEventListener;
  w.EventTarget.prototype.addEventListener = function (type, fn, o) {
    if (typeof fn === "function") live.add(tag(this) + "|" + type + "|" + id(fn) + "|" + (o === true || (o && o.capture) ? 1 : 0));
    return add.call(this, type, fn, o);
  };
  w.EventTarget.prototype.removeEventListener = function (type, fn, o) {
    if (typeof fn === "function") live.delete(tag(this) + "|" + type + "|" + id(fn) + "|" + (o === true || (o && o.capture) ? 1 : 0));
    return rem.call(this, type, fn, o);
  };
  let base = new Set();
  return {
    mark: () => { base = new Set(live); },
    count: (target, type) => [...live].filter(k => !base.has(k) && k.startsWith(target + "|" + type + "|")).length,
    page: () => [...live].filter(k => !base.has(k) && /^(window|document)\|/.test(k)).map(k => k.split("|").slice(0, 2).join("|")).sort(),
  };
};

const chain = (env, files, plugins, tail) => {
  files.forEach(f => env.load(f));
  (plugins || []).forEach(p => env.window.eval("(function(Framey){" + p.code + "\n})(window.__bindFramey(" + JSON.stringify(p.meta.id) + "," + JSON.stringify("tok-" + p.meta.id + "-0000") + "," + JSON.stringify(p.meta) + "))"));
  (tail || []).forEach(f => env.load(f));
  env.window.eval("Framey.restore()");
};

const apiOf = (m, id) => m.FY._k(TOK).apiOf(m.FY.plugins.find(p => p.id === id));

const main = (opts = {}) => {
  const R = loader(opts);
  let spy;
  const env = makeDom({
    before(w) {
      spy = listeners(w);
      w.FY_RUNTIME = RUNTIME;
      w.FY_ICON_URL = "data:image/svg+xml,x";
    },
  });
  const w = env.window;
  const d = env.document;
  R.env = env;
  spy.mark();
  env.onCall(s => {
    const c = JSON.parse(s);
    env.calls.push(c);
    if (!c.id) return;
    const f = c.method in R ? R[c.method] : () => ({ error: "unhandled " + c.method });
    Promise.resolve(f(c.arg, c)).then(v => w.Framey._reply(c.gen, c.id, v === undefined ? null : JSON.parse(JSON.stringify(v))));
  });
  const safe = opts.safe === true;
  chain(env, safe ? SAFE.slice(0, -2) : HEAD, opts.plugins === undefined ? [FAN, ALPHA, BETA] : opts.plugins, safe ? SAFE.slice(-2) : opts.bare ? [] : TAIL);
  const FY = w.Framey;
  const panel = () => [...d.body.children].find(c => c.style.width === "520px");
  const content = () => panel().children[0];
  const rail = () => [...panel().children[1].children].map(c => c.textContent);
  const goto = name => [...panel().children[1].children].find(c => c.textContent === name).click();
  const controls = () => [...content().querySelectorAll('[role="button"],[role="switch"],[role="option"]')];
  const control = label => {
    const hit = controls().find(e => e.getAttribute("aria-label") === label);
    if (!hit) throw new Error("no control '" + label + "' in " + controls().map(e => e.getAttribute("aria-label")).join(" | "));
    return hit;
  };
  const sent = m => env.calls.filter(c => c.method === m);
  return { env, w, d, FY, R, panel, content, rail, goto, control, controls, sent, spy, text: () => content().textContent };
};

const barPage = (opts = {}) => {
  const env = makeDom({
    before(w) {
      w.FY_ICON_URL = "data:image/svg+xml,x";
      w.Element.prototype.getBoundingClientRect = function () {
        const width = this.tagName === "IMG" ? 50 : 300;
        return { left: 0, top: 0, right: width, bottom: 40, width, height: 40 };
      };
      if (!opts.late) {
        const row = w.document.createElement("div");
        const img = w.document.createElement("img");
        img.className = "avatar";
        row.append(img, w.document.createElement("span"), w.document.createElement("span"));
        w.document.body.append(row);
      }
    },
  });
  const w = env.window;
  const d = env.document;
  const live = new Set();
  const OM = w.MutationObserver;
  w.MutationObserver = function (...a) {
    const o = new OM(...a);
    live.add(o);
    const dis = o.disconnect.bind(o);
    o.disconnect = () => { live.delete(o); return dis(); };
    return o;
  };
  const inject = () => {
    env.load("bar.js");
    env.load("fy-badge.js");
  };
  inject();
  return {
    env,
    w,
    d,
    inject,
    observers: () => live.size,
    button: () => d.querySelector(".fy-bb"),
    dots: () => d.querySelectorAll(".fy-bb>.fy-bdg").length,
    late() {
      const row = d.createElement("div");
      const img = d.createElement("img");
      img.className = "avatar";
      row.append(img, d.createElement("span"), d.createElement("span"));
      d.body.append(row);
      return settle().then(() => env.clock.tick(400));
    },
  };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("rail: saved order from state_get, hidden tab omitted, builtin pages last", async () => {
  const m = main({ state: { order: ["fan", "beta", "alpha"], hidden: ["beta"] } });
  m.FY.toggle();
  assert.deepEqual(m.rail(), ["Fan", "Curve", "Alpha", "Beta", "Modules", "Store", "Settings"], "before the reply: registration order");
  await settle();
  assert.deepEqual(m.rail(), ["Fan", "Curve", "Alpha", "Modules", "Store", "Settings"]);
  assert.equal(m.sent("state_get").length, 1);
});

t("rail before the state reply uses registration order and keeps the pages grouped under their plugin", async () => {
  const m = main();
  m.FY.toggle();
  assert.deepEqual(m.rail(), ["Fan", "Curve", "Alpha", "Beta", "Modules", "Store", "Settings"]);
  await settle();
  assert.deepEqual(m.rail(), ["Alpha", "Beta", "Fan", "Curve", "Modules", "Store", "Settings"]);
});

t("hidden plugin keeps running, its tab and pages are not in the rail, Framey.open still reaches it", async () => {
  const m = main({ state: { hidden: ["fan"] } });
  m.FY.toggle();
  await settle();
  assert.ok(m.FY.plugins.some(p => p.id === "fan" && !p.dead));
  assert.deepEqual(m.rail(), ["Alpha", "Beta", "Modules", "Store", "Settings"]);
  m.FY.open("fan");
  assert.ok(m.text().includes("home of fan"));
  m.FY.open("fan:cfg");
  assert.ok(m.text().includes("curve page"));
  assert.deepEqual(m.rail(), ["Alpha", "Beta", "Modules", "Store", "Settings"]);
});

t("reorder and hide replies from the Modules methods update the open rail without reopening", async () => {
  const m = main();
  m.FY.toggle();
  await settle();
  m.FY._k(TOK).call("mod_order", { id: "fan", delta: -1 });
  await settle();
  assert.deepEqual(m.rail(), ["Alpha", "Fan", "Curve", "Beta", "Modules", "Store", "Settings"]);
  m.FY._k(TOK).call("mod_hide", { id: "alpha", hidden: true });
  await settle();
  assert.deepEqual(m.rail(), ["Fan", "Curve", "Beta", "Modules", "Store", "Settings"]);
  assert.equal(m.sent("state_get").length, 1);
  m.FY._k(TOK).call("list");
  await settle();
  assert.deepEqual(m.rail(), ["Fan", "Curve", "Beta", "Modules", "Store", "Settings"]);
});

t("a junk state reply is ignored and the rail keeps its order", async () => {
  const m = main({ methods: { state_get: () => ({ error: "state unavailable", code: "x" }) } });
  m.FY.toggle();
  await settle();
  assert.deepEqual(m.rail(), ["Fan", "Curve", "Alpha", "Beta", "Modules", "Store", "Settings"]);
  const n = main({ methods: { state_get: () => ({ order: "no", hidden: {} }) } });
  n.FY.toggle();
  await settle();
  assert.equal(n.rail().length, 7);
});

t("Modules, Store and Settings render through delegation to fy-modules.js and fy-settings.js", async () => {
  const m = main();
  assert.ok(Object.isFrozen(m.FY.pages) && Object.isFrozen(m.FY.pages.modules) && Object.isFrozen(m.FY.pages.settings));
  assert.deepEqual(Object.keys(m.FY.pages.modules), ["render"]);
  assert.deepEqual(Object.keys(m.FY.pages.settings), ["render"]);
  m.FY.toggle();
  await settle();
  m.goto("Modules");
  await settle();
  assert.ok(m.text().includes("Check for updates") && m.text().includes("Update all"));
  assert.ok(m.text().includes("Fan Control") && m.text().includes("Alpha") && m.text().includes("Beta"));
  assert.ok(m.text().includes("kaput 9") && m.controls().some(e => e.getAttribute("aria-label") === "Disable") && m.controls().some(e => e.getAttribute("aria-label") === "Reload"), "failed module row keeps Disable and Reload");
  assert.equal(m.w.sessionStorage.fyTab, "#modules");
  m.goto("Store");
  assert.ok(m.text().includes("Coming soon") && m.text().includes("Store"));
  assert.equal(m.w.sessionStorage.fyTab, "#discover");
  m.goto("Settings");
  await settle();
  assert.ok(m.text().includes("Settings") && m.text().includes("Framey") && m.text().includes("Installed"));
  assert.equal(m.w.sessionStorage.fyTab, "#settings");
  assert.ok(m.sent("list").length >= 1 && m.sent("loader").length >= 1);
  assert.ok(!m.text().includes("Tap again to confirm"));
});

t("without fy-modules.js and fy-settings.js the panel still opens and says the page is missing", async () => {
  const m = main({ plugins: [ALPHA], bare: true });
  m.FY.toggle();
  m.goto("Modules");
  assert.ok(m.text().includes("Modules is not available"));
  m.goto("Store");
  assert.ok(m.text().includes("Coming soon"));
  m.goto("Settings");
  assert.ok(m.text().includes("Settings is not available"));
  m.goto("Alpha");
  assert.ok(m.text().includes("home of alpha"));
});

t("inject.js alone: panel, rail and Store work, nothing throws", () => {
  const env = makeDom();
  const FY = env.window.Framey;
  FY.toggle();
  const panel = [...env.document.body.children].find(c => c.style.width === "520px");
  assert.deepEqual([...panel.children[1].children].map(c => c.textContent), ["Modules", "Store", "Settings"]);
  FY.toggle();
  assert.equal(panel.style.display, "none");
});

t("plugin: keys, plain ids, #modules and #settings open the right page and open a closed panel", async () => {
  const m = main();
  m.FY.open("fan:cfg");
  assert.ok(m.panel().style.display !== "none" && m.text().includes("curve page"));
  assert.equal(m.w.sessionStorage.fyTab, "fan:cfg");
  assert.ok(m.d.querySelector('[data-fy="fan"]'), "page content is owned by the plugin");
  m.FY.open("alpha");
  assert.ok(m.text().includes("home of alpha"));
  m.FY.open("#settings");
  await settle();
  assert.ok(m.text().includes("Installed"));
  m.FY.open("#modules");
  await settle();
  assert.ok(m.text().includes("Check for updates"));
  m.FY.toggle();
  m.FY.open("#settings");
  await settle();
  assert.ok(m.panel().style.display !== "none" && m.w.sessionStorage.fyTab === "#settings");
  assert.equal(m.sent("panel").map(c => c.arg).join(), "false,true,false,true");
});

t("closing a panel page returns to the plugin tab, removing it drops the rail entry", async () => {
  const m = main();
  m.FY.toggle();
  m.goto("Curve");
  assert.ok(m.text().includes("curve page"));
  m.w.fanApi.pages.close();
  assert.ok(m.text().includes("home of fan"));
  m.w.fanApi.pages.register({ id: "other", title: "Second", render(b) { b.append(m.FY.el("", "second")); } });
  assert.ok(m.rail().includes("Second"));
  assert.equal(m.w.fanApi.pages.list().length, 2);
});

t("saved tab is restored when the panel opens again and falls back to the first tab", async () => {
  const m = main();
  m.w.sessionStorage.fyTab = "#discover";
  m.FY.toggle();
  await settle();
  assert.equal(m.w.sessionStorage.fyTab, "#discover");
  m.FY.toggle();
  m.w.sessionStorage.fyTab = "gone";
  m.FY.toggle();
  assert.equal(m.w.sessionStorage.fyTab, "alpha");
  assert.equal(m.rail()[0], "Alpha");
});

t("a panel page registered while the panel is open appears at once", async () => {
  const m = main({ plugins: [ALPHA] });
  m.FY.toggle();
  assert.deepEqual(m.rail(), ["Alpha", "Modules", "Store", "Settings"]);
  const api = apiOf(m, "alpha");
  api.pages.register({ id: "p", title: "Extra page", render(b) { b.append(m.FY.el("", "extra")); } });
  assert.deepEqual(m.rail(), ["Alpha", "Extra", "Modules", "Store", "Settings"]);
});

t("open and close push Framey._panel, the loader notice and api.visible; reopening an open panel does not repeat them", async () => {
  const m = main();
  const seen = [];
  m.FY.onPanel(v => seen.push(["panel", v]));
  const alpha = apiOf(m, "alpha");
  alpha.onVisible(v => seen.push(["visible", v]));
  assert.equal(m.FY.panelOpen, false);
  assert.equal(alpha.visible, false);
  m.FY.toggle();
  assert.equal(m.FY.panelOpen, true);
  assert.equal(alpha.visible, true);
  m.FY.open("#settings");
  m.FY.open("alpha");
  m.FY.toggle();
  assert.equal(m.FY.panelOpen, false);
  assert.equal(alpha.visible, false);
  m.FY.toggle();
  m.FY.toggle();
  assert.deepEqual(seen.map(x => x.join(":")), ["panel:true", "visible:true", "panel:false", "visible:false", "panel:true", "visible:true", "panel:false", "visible:false"]);
  assert.deepEqual(m.sent("panel").map(c => c.arg), [false, true, false, true, false]);
  assert.equal(m.sent("panel")[0].id, 0);
});

t("restore opens the panel again when the session says so and shows the saved message once", async () => {
  const m = main();
  m.w.sessionStorage.fyOpen = "1";
  m.w.sessionStorage.fyMsg = "Framey updated";
  m.w.sessionStorage.fyTab = "#settings";
  m.w.eval("Framey.restore()");
  await settle();
  assert.equal(m.FY.panelOpen, true);
  assert.ok(m.text().includes("Installed"));
  assert.ok(m.d.body.textContent.includes("Framey updated"));
  assert.equal(m.w.sessionStorage.fyMsg, "");
});

t("a notification action open:settings opens the panel on Settings", async () => {
  const m = main();
  m.FY.notify({ title: "Update", actions: [{ id: "open:settings", label: "Open Settings" }], duration: 0 });
  const b = [...m.d.querySelectorAll("button")].find(x => x.textContent === "Open Settings");
  b.click();
  await settle();
  assert.equal(m.FY.panelOpen, true);
  assert.equal(m.w.sessionStorage.fyTab, "#settings");
  assert.ok(m.text().includes("Installed"));
  m.FY._k(TOK).event("_core", "notice", { title: "Modules", actions: [{ id: "open:modules", label: "Open Modules" }], duration: 0 });
  [...m.d.querySelectorAll("button")].find(x => x.textContent === "Open Modules").click();
  await settle();
  assert.equal(m.w.sessionStorage.fyTab, "#modules");
});

t("every file in the chain extends the plugin api through FY.ext, and a throwing extension cannot break register", async () => {
  const m = main({ plugins: [] });
  m.FY.ext.push(() => { throw new Error("bad extension"); });
  const api = m.w.__bindFramey("alpha", "tok-alpha-0000", ALPHA.meta).register({ render() {} });
  ["ui", "pages", "global", "events", "i18n", "runtime"].forEach(k => assert.ok(api[k], k));
  ["cleanup", "call", "raf", "notify", "onVisible", "t", "fetch", "resource", "revoke", "log", "target"].forEach(k => assert.equal(typeof api[k], "function", k));
  ["button", "toggle", "slider", "list", "group", "focus"].forEach(k => assert.equal(typeof api.ui[k], "function", k));
  assert.equal(api.runtime.framey, "1.1.0");
  assert.equal(api.runtime.platform, "linux-aarch64");
  assert.equal(m.FY.plugins.length, 1);
});

t("Framey.lazy resolves at once for picker and dev, which are in the core scope; the Developer tab appears and opens", async () => {
  const m = main();
  m.R.env.load("fy-picker.js");
  m.FY.toggle();
  await settle();
  assert.ok(!m.rail().includes("Dev"));
  m.R.env.load("fy-dev.js");
  assert.equal(typeof apiOf(m, "fan").pickFile, "function");
  await m.FY.lazy("picker");
  await m.FY.lazy("dev");
  await m.FY.lazy("dev");
  assert.deepEqual(m.sent("asset"), []);
  m.FY.open("#developer");
  await settle();
  assert.ok(m.text().includes("Developer"));
  assert.equal(m.rail().filter(x => x === "Dev").length, 1);
  assert.equal(m.w.sessionStorage.fyTab, "#developer");
  await assert.rejects(m.FY.lazy("evil"));
});

t("Settings reaches the lazy Developer page through Framey.lazy and Framey.open", async () => {
  const m = main({ state: { prefs: { channel: "stable", dev: { enabled: true } } } });
  m.R.env.load("fy-dev.js");
  m.FY.toggle();
  m.goto("Settings");
  await settle();
  m.control("Developer tools").click();
  await settle();
  assert.equal(m.w.sessionStorage.fyTab, "#developer");
  assert.ok(m.text().includes("Developer"));
});

t("pagehide disposes every plugin once: nodes, timers, subscriptions, pending calls and notifications", async () => {
  const m = main();
  m.FY.toggle();
  await settle();
  const alpha = m.FY.plugins.find(p => p.id === "alpha");
  let ticks = 0;
  const ax = apiOf(m, "alpha");
  ax.timeout(() => { ticks++; }, 5000);
  ax.events.on("ping", () => { ticks++; });
  ax.notify({ title: "Hi", duration: 0 });
  m.FY.call("alpha", "slow");
  const before = m.env.clock.pending();
  assert.ok(before >= 1);
  m.w.dispatchEvent(new m.w.Event("pagehide"));
  assert.ok(m.FY.plugins.every(p => p.dead));
  assert.equal(m.d.querySelectorAll('[data-fy="alpha"],[data-fy="fan"],[data-fy="beta"]').length, 0);
  m.FY._k(TOK).event("alpha", "ping", {});
  m.env.clock.tick(6000);
  assert.equal(ticks, 0);
  assert.equal(m.spy.count("window", "pagehide"), 1);
  m.w.dispatchEvent(new m.w.Event("pagehide"));
  assert.ok(m.FY.plugins.every(p => p.dead));
});

t("reinjection after the loader's wipe, three times: one panel, one style set, no duplicate nodes or handlers", async () => {
  const m = main();
  const count = () => ({
    panel: m.d.querySelectorAll("div[style*='520px']").length,
    styles: m.d.querySelectorAll("style").length,
    uiStyle: m.d.querySelectorAll("#fy-ui-style").length,
    owned: m.d.querySelectorAll("[data-fy]").length,
    page: m.spy.page().join(),
  });
  const first = count();
  assert.equal(first.panel, 1);
  assert.equal(first.uiStyle, 1);
  m.FY.toggle();
  await settle();
  m.FY.toggle();
  for (let i = 0; i < 3; i++) {
    wipe(m.w);
    assert.equal(m.w.Framey, undefined);
    assert.equal(m.d.querySelectorAll("[data-fy]").length, 0, "wipe leaves nothing owned");
    chain(m.env, HEAD, [FAN, ALPHA, BETA], TAIL);
  }
  const again = count();
  assert.equal(again.panel, 1);
  assert.equal(again.styles, first.styles);
  assert.equal(again.uiStyle, 1);
  assert.equal(again.owned, first.owned);
  assert.equal(m.w.Framey.plugins.length, 3);
  m.w.Framey.toggle();
  await settle();
  assert.equal(m.w.Framey.panelOpen, true);
  assert.equal(m.d.querySelectorAll("div[style*='520px']").length, 1);
  assert.equal([...m.d.querySelectorAll("div[style*='520px']")][0].children[1].children.length, 7);
  m.w.eval("Framey.dispose(undefined,'" + TOK + "')");
  const before = m.w.Framey;
  chain(m.env, HEAD, [], []);
  assert.equal(m.w.Framey, before, "a second injection without wipe is a no-op");
  assert.equal(m.d.querySelectorAll("div[style*='520px']").length, 1);
});

t("reinjection: window and document listeners do not pile up, and the pagehide handler of an old page does nothing", async () => {
  const m = main();
  const idle = m.spy.page();
  assert.deepEqual(idle, ["window|pagehide"]);
  const first = m.FY.plugins[0];
  for (let i = 0; i < 2; i++) {
    wipe(m.w);
    chain(m.env, HEAD, [ALPHA], TAIL);
  }
  assert.equal(first.dead, true, "wipe disposed the first page");
  const live = m.w.Framey.plugins[0];
  m.w.dispatchEvent(new m.w.Event("pagehide"));
  assert.equal(live.dead, true, "one pagehide disposes the current page");
  assert.equal(m.spy.count("window", "pagehide"), 1, "stale handlers removed themselves, the current one stays");
  assert.deepEqual(m.spy.page(), ["window|pagehide"]);
});

t("idle: opening, visiting every page and closing leaves no timer, interval or extra listener behind", async () => {
  const m = main();
  const base = m.env.clock.pending();
  const idle = m.spy.page();
  m.FY.toggle();
  await settle();
  ["Alpha", "Fan", "Curve", "Modules", "Store", "Settings"].forEach(n => { m.goto(n); });
  await settle();
  m.FY.toggle();
  assert.equal(m.env.clock.pending(), base);
  assert.deepEqual(m.spy.page(), idle);
  const inject = read("inject.js");
  assert.equal((inject.match(/setInterval\b/g) || []).length, 1, "setInterval is captured once");
  assert.equal((inject.match(/setTimeout\b/g) || []).length, 1, "setTimeout is captured once");
  assert.ok(!/requestAnimationFrame/.test(inject + read("bar.js")));
  assert.ok(!/setInterval/.test(read("bar.js")));
});

t("safe mode: only the panel files are injected, no plugins, the Modules page shows the banner and the exit works", async () => {
  const m = main({ safe: true, plugins: [], state: { safe: true }, methods: { list: () => [] } });
  m.FY.toggle();
  await settle();
  assert.deepEqual(m.rail(), ["Modules", "Store", "Settings"]);
  assert.equal(m.FY.plugins.length, 0);
  assert.equal(typeof m.FY.pages, "object");
  assert.equal(m.FY.panelPages, undefined);
  assert.ok(m.text().includes("Safe mode is on"));
  assert.ok(m.text().includes("~/.config/framey/safe"));
  assert.throws(() => m.control("Turn off safe mode"));
  assert.equal(m.sent("prefs_set").length, 0);
  m.goto("Settings");
  await settle();
  assert.ok(m.text().includes("Safe mode"));
});

t("the stale reply rule still holds across the new state adoption: a reply from an older page cannot change the rail", async () => {
  const m = main();
  m.FY.toggle();
  await settle();
  const old = m.FY._k(TOK).call("state_get");
  const req = m.env.calls[m.env.calls.length - 1];
  await settle();
  m.FY._reply("forged.gen:_core.0", req.id, { order: ["beta"], hidden: ["alpha"] });
  await settle();
  assert.deepEqual(m.rail(), ["Alpha", "Beta", "Fan", "Curve", "Modules", "Store", "Settings"]);
  assert.ok(await Promise.race([old.then(() => true), new Promise(r => setImmediate(() => r(true)))]));
});

t("bar.js: the button follows the panel state sent by the loader, and a click asks the loader to toggle", async () => {
  const m = main();
  const b = barPage();
  m.env.onCall(s => {
    const c = JSON.parse(s);
    m.env.calls.push(c);
    if (c.method === "panel") b.w.FYBar.set(c.arg);
  });
  b.env.onCall(s => {
    const c = JSON.parse(s);
    b.env.calls.push(c);
    if (c.method === "toggle") m.w.Framey.toggle();
  });
  const btn = b.button();
  assert.ok(btn && !btn.classList.contains("fy-on") && btn.getAttribute("aria-pressed") === "false");
  btn.click();
  assert.equal(m.FY.panelOpen, true);
  assert.ok(btn.classList.contains("fy-on") && btn.getAttribute("aria-pressed") === "true");
  btn.click();
  assert.equal(m.FY.panelOpen, false);
  assert.ok(!btn.classList.contains("fy-on") && btn.getAttribute("aria-pressed") === "false");
  m.w.Framey.open("#settings");
  assert.ok(btn.classList.contains("fy-on"));
  assert.equal(b.env.calls.filter(c => c.method === "toggle").length, 2);
});

t("bar badge: shown while the panel is closed, kept while it opens and closes, removed at 0", async () => {
  const b = barPage();
  const btn = b.button();
  b.w.FYBar.badge(3);
  assert.equal(b.dots(), 1);
  assert.equal(btn.getAttribute("aria-label"), "Framey, 3 updates");
  b.w.FYBar.set(true);
  assert.equal(b.dots(), 1);
  assert.ok(btn.classList.contains("fy-on"));
  b.w.FYBar.set(false);
  assert.equal(b.dots(), 1);
  assert.ok(!btn.classList.contains("fy-on"));
  b.w.FYBar.badge(0);
  assert.equal(b.dots(), 0);
  assert.equal(btn.getAttribute("aria-label"), "Framey");
});

t("bar badge: a badge pushed before the button is mounted appears when bar.js mounts it, with the active state", async () => {
  const b = barPage({ late: true });
  assert.equal(b.button(), null);
  b.w.FYBar.set(true);
  b.w.FYBar.badge(2);
  await b.late();
  assert.ok(b.button());
  assert.equal(b.dots(), 1);
  assert.equal(b.button().getAttribute("aria-label"), "Framey, 2 updates");
  assert.ok(b.button().classList.contains("fy-on"));
  assert.equal(b.button().getAttribute("aria-pressed"), "true");
});

t("bar reinjection after the loader's wipe: one button, one label, one style each, one observer", async () => {
  const b = barPage();
  b.w.FYBar.badge(1);
  const state = () => ({
    buttons: b.d.querySelectorAll(".fy-bb").length,
    labels: b.d.querySelectorAll(".fy-bb>span").length,
    icons: b.d.querySelectorAll(".fy-bb>i").length,
    barStyles: b.d.querySelectorAll("#fy-bar-style").length,
    badgeStyles: b.d.querySelectorAll("#fy-badge-style").length,
    observers: b.observers(),
  });
  const first = state();
  assert.deepEqual(first, { buttons: 1, labels: 1, icons: 1, barStyles: 1, badgeStyles: 1, observers: 1 });
  for (let i = 0; i < 3; i++) {
    b.w.eval(WIPE_BAR);
    assert.equal(b.d.querySelectorAll(".fy-bb,#fy-bar-style,#fy-badge-style").length, 0);
    assert.equal(b.observers(), 0);
    b.inject();
  }
  assert.deepEqual(state(), first);
  assert.equal(b.dots(), 0, "a fresh bar has no badge until the loader pushes it again");
  b.w.FYBar.badge(4);
  assert.equal(b.dots(), 1);
  b.inject();
  assert.deepEqual(state(), first);
  assert.equal(b.dots(), 1);
});

t("loader constants used by this test still match framey.py", () => {
  const py = read("framey.py");
  assert.ok(py.includes('RM = "' + RM + '"'));
  assert.ok(py.includes('WIPE = RM + "delete window.Framey"'));
  assert.ok(py.includes('WIPE_BAR = "window.FYBar&&window.FYBar.dispose&&window.FYBar.dispose();window.FYObs&&window.FYObs.disconnect();" + RM + "delete window.FYBar;delete window.FYObs"'));
});

t("size and hygiene of the glue: no comments, no innerHTML, textContent only", () => {
  ["inject.js", "bar.js"].forEach(f => {
    const s = read(f);
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(s), f);
    assert.ok(!/^\s*\/\/|\/\*/m.test(s.replace(/"[^"\n]*"/g, '""')), f + " has no comments");
    assert.ok(!/\r?\n\s*\r?\n\s*\r?\n/.test(s), f);
  });
  assert.ok(read("inject.js").split("\n").length < 440);
});

(async () => {
  let pass = 0;
  let fail = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      pass++;
      console.log("ok   " + name);
    } catch (e) {
      fail++;
      console.log("FAIL " + name + "\n" + (e && e.stack));
    }
  }
  console.log("\n" + pass + " passed, " + fail + " failed, " + tests.length + " total");
  process.exit(fail ? 1 : 0);
})();
