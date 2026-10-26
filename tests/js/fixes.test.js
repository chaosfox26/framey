"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT, TOK, registerAs } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const CATALOG_FILES = FILES.flatMap(file => file === "fy-settings.js" ? ["fy-catalog.js", file] : [file]);
const RM = "document.querySelectorAll('[data-fy]').forEach(e=>e.remove());";
const WIPE = RM + "delete window.Framey";
const flush = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };
const read = f => fs.readFileSync(path.join(ROOT, f), "utf8");

const boot = (options = {}) => {
  const answers = Object.assign({
    state_get: { order: [], hidden: [], sort: "custom", frozen: {}, prefs: {}, safe: false },
    list: [{ id: "alpha", name: "Alpha", version: "1.0.0", commit: "abc1234", enabled: true, nosource: "", pending: false, compat: { badge: "ok", msg: "" }, fail: null, stuck: false, linked: false }],
    store: [],
    fetch: { status: 200, text: "" },
    resource: { url: "http://127.0.0.1:1/m/x" },
  }, options.answers);
  const env = makeDom({ loadFiles: options.files || FILES, before: options.before });
  env.onCall(s => {
    const c = JSON.parse(s);
    env.calls.push(c);
    if (!c.id) return;
    const f = c.method in answers ? answers[c.method] : { error: "unhandled " + c.method };
    Promise.resolve(typeof f === "function" ? f(c.arg, c) : f).then(v => env.window.Framey && env.window.Framey._reply(c.gen, c.id, v === undefined ? null : JSON.parse(JSON.stringify(v))));
  });
  env.answers = answers;
  env.FY = env.window.Framey;
  env.K = () => env.window.Framey._k(TOK);
  env.panel = () => [...env.document.body.children].find(c => c.style.width === "520px");
  env.content = () => env.panel().children[0];
  env.rail = () => env.panel().children[1];
  env.tabs = () => [...env.rail().children];
  env.tab = name => env.tabs().find(t => t.textContent === name);
  env.key = (el, k, extra) => {
    const ev = new env.window.KeyboardEvent("keydown", Object.assign({ key: k, bubbles: true, cancelable: true }, extra));
    el.dispatchEvent(ev);
    return ev;
  };
  env.control = label => [...env.content().querySelectorAll("[aria-label]")].find(e => e.getAttribute("aria-label") === label);
  return env;
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("auth: core calls carry the core token, plugin calls the plugin token, legacy calls none, and the global core call is refused", async () => {
  const e = boot();
  const api = e.plugin("alpha");
  e.K().call("state_get");
  const core = e.calls[e.calls.length - 1];
  assert.strictEqual(core.plugin, "_core");
  assert.strictEqual(core.t, TOK);
  api.fetch("https://example.org/", { pid: "spoof" });
  const f = e.sent("fetch")[0];
  assert.strictEqual(f.t, "tok-alpha-0000");
  assert.strictEqual(f.arg.pid, "alpha");
  api.call("go", 1);
  const b = e.sent("go")[0];
  assert.deepStrictEqual([b.plugin, b.t], ["alpha", "tok-alpha-0000"]);
  api.log("info", "x");
  assert.strictEqual(e.sent("log")[0].t, "tok-alpha-0000");
  e.FY.call("alpha", "legacy", 2);
  const l = e.sent("legacy")[0];
  assert.strictEqual(l.plugin, "alpha");
  assert.ok(!("t" in l));
  const n = e.calls.length;
  const r = await e.FY.call("_core", "install", { url: "https://x" });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { error: "not authorized" });
  assert.strictEqual(e.calls.length, n);
  assert.strictEqual(e.K !== undefined && e.window.Framey._k("wrong"), null);
  assert.strictEqual(e.window.Framey._k(""), null);
});

t("auth: tokens are not reachable from Framey, a registered api, the window or the DOM", async () => {
  const e = boot();
  const api = e.plugin("alpha");
  const seen = new Set();
  const dump = (o, depth) => {
    if (!o || (typeof o !== "object" && typeof o !== "function") || seen.has(o) || depth > 4) return "";
    seen.add(o);
    let s = "";
    Reflect.ownKeys(o).forEach(k => {
      let v;
      try { v = o[k]; } catch (x) { return; }
      s += String(k) + "=" + (typeof v === "string" ? v : "") + ";";
      s += dump(v, depth + 1);
    });
    return s;
  };
  const all = dump(e.window.Framey, 0) + dump(api, 0) + dump(e.FY.plugins, 0) + e.document.documentElement.outerHTML + JSON.stringify(Object.keys(e.window));
  assert.ok(!all.includes(TOK));
  assert.ok(!all.includes("tok-alpha-0000"));
  assert.strictEqual(e.window.__fyt, undefined);
});

t("auth: __bindFramey returns a frozen scope bound to its id: register, call and dispose stay on that id", async () => {
  const e = boot();
  const FY = e.FY;
  const seen = e.window.__bindFramey("beta", "tok-beta-00000", { name: "Beta", short: "Bt" });
  assert.strictEqual(Object.isFrozen(seen), true);
  assert.strictEqual(seen.toast, FY.toast);
  ["plugins", "ext", "_k", "_reply", "_event", "pages", "diag", "i18n"].forEach(k => assert.strictEqual(k in seen, false, k));
  const api = seen.register({ render() {} });
  assert.strictEqual(api.id, "beta");
  assert.strictEqual(FY.plugins.find(p => p.id === "beta").short, "Bt");
  assert.strictEqual(seen._meta.name, "Beta");
  assert.strictEqual("_meta" in FY, false);
  assert.strictEqual("_bind" in FY, false);
  seen.call("other", "ping", {});
  const sent = e.calls.filter(c => c.method === "ping").pop();
  assert.strictEqual(sent.plugin, "beta");
  assert.strictEqual(sent.t, "tok-beta-00000");
});

t("registration safety: one plugin cannot take or dispose another plugin's slot", async () => {
  const e = boot();
  const FY = e.FY;
  const victim = e.plugin("victim");
  let cleaned = 0;
  victim.cleanup(() => cleaned++);
  assert.throws(() => e.window.__bindFramey("victim", "forged-token-1"), /already in use/);
  const evil = e.window.__bindFramey("evil", "tok-evil-00000");
  assert.throws(() => evil.register({ id: "victim", render() {} }), /does not match evil/);
  assert.strictEqual(cleaned, 0);
  assert.strictEqual(FY.plugins.filter(p => p.id === "victim").length, 1);
  assert.strictEqual(FY.plugins.find(p => p.id === "victim").dead, undefined);
  ["", "A", "a b", 'x"]', "a".repeat(40), 5, null].forEach(id => assert.throws(() => e.window.__bindFramey(id, "tok-xxxxxxxx"), /invalid module id/));
  ["core", "modules", "settings", "store"].forEach(id => assert.throws(() => e.window.__bindFramey(id, "tok-xxxxxxxx"), /is reserved/));
  assert.throws(() => e.window.__bindFramey("ok1", "short"), /invalid module token/);
  const again = e.window.__bindFramey("victim", "tok-victim-0000").register({ render() {} });
  assert.strictEqual(again.id, "victim");
  assert.strictEqual(cleaned, 1);
  assert.strictEqual(FY.plugins.filter(p => p.id === "victim").length, 1);
});

t("registration safety: a throwing dispose does not stop the cleanup of the others and ids never reach selectors", async () => {
  const e = boot();
  const FY = e.FY;
  const reg = (id, dispose) => e.window.__bindFramey(id, "tok-" + id + "-0000", { name: id, short: id }).register({ render() {}, dispose });
  const a = reg("a", () => { throw new Error("boom"); });
  const b = reg("b");
  const c = reg("c");
  let ran = 0;
  c.cleanup(() => ran++);
  const mk = id => { const x = e.document.createElement("div"); x.dataset.fy = id; e.document.body.append(x); return x; };
  const nodes = ["a", "b", "c"].map(mk);
  FY.dispose(undefined, TOK);
  assert.strictEqual(ran, 1);
  assert.ok(nodes.every(n => !n.isConnected));
  assert.ok(FY.plugins.every(p => p.dead));
  assert.ok(a && b);
});

t("timers and listeners: the registry shrinks when they fire or are cleared and is bounded", async () => {
  const e = boot();
  const api = e.plugin("a");
  const F = e.FY;
  for (let i = 0; i < 256; i++) api.timeout(() => {}, 1);
  assert.throws(() => api.timeout(() => {}, 1), { name: "RangeError" });
  assert.strictEqual(F.diag().plugins[0].timers, 256);
  e.clock.tick(5);
  assert.strictEqual(F.diag().plugins[0].timers, 0);
  const h = api.timeout(() => {}, 100);
  const i = api.interval(() => {}, 100);
  assert.deepStrictEqual([F.diag().plugins[0].timers, F.diag().plugins[0].intervals], [1, 1]);
  api.clear(h);
  api.clear(i);
  assert.deepStrictEqual([F.diag().plugins[0].timers, F.diag().plugins[0].intervals], [0, 0]);
  const offs = [];
  for (let k = 0; k < 256; k++) offs.push(api.on(e.document, "click", () => {}));
  assert.throws(() => api.on(e.document, "click", () => {}), { name: "RangeError" });
  assert.strictEqual(F.diag().plugins[0].listeners, 256);
  offs.forEach(f => f());
  assert.strictEqual(F.diag().plugins[0].listeners, 0);
  let fired = 0;
  const kept = api.timeout(() => fired++, 10);
  const other = api.interval(() => fired++, 10);
  F.dispose("a", TOK);
  e.clock.tick(50);
  assert.strictEqual(fired, 0);
  assert.ok(kept && other !== undefined);
});

t("the core listen channel is reserved: a plugin exhausting its own listen cannot disable Modules or Settings", async () => {
  const e = boot({ files: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js"] });
  const F = e.FY;
  const h = e.window.__bindFramey("noisy", "tok-noisy-0000");
  h.register({ render() {} });
  let n = 0;
  try { for (;;) { h.listen("progress", () => {}); n++; } } catch (x) { assert.strictEqual(x.name, "RangeError"); }
  assert.strictEqual(n, 64);
  e.load("fy-modules.js");
  e.load("fy-settings.js");
  assert.ok(F.pages.modules && F.pages.settings);
});

t("redraw: another plugin registering a page does not replace the displayed Modules screen", async () => {
  const e = boot();
  const F = e.FY;
  e.plugin("a");
  F.toggle();
  e.tab("Modules").click();
  await settle();
  const marker = e.content().firstChild;
  const other = e.plugin("b");
  other.pages.register({ id: "p", title: "Extra", render() {} });
  assert.strictEqual(e.content().firstChild, marker);
  assert.ok(e.tab("Extra"));
  other.pages.register({ id: "q", title: "More", render() {} });
  assert.strictEqual(e.content().firstChild, marker);
  e.tab("Extra").click();
  const shown = e.content().firstChild;
  other.pages.register({ id: "r", title: "Third", render() {} });
  assert.strictEqual(e.content().firstChild, shown);
});

t("rail: tiles are focusable tabs with roving tabindex, aria-selected and Enter, Space and arrow keys", async () => {
  const e = boot();
  e.plugin("a");
  e.FY.toggle();
  await settle();
  const tabs = e.tabs();
  assert.ok(tabs.length >= 4);
  assert.strictEqual(e.rail().getAttribute("role"), "tablist");
  tabs.forEach(x => assert.strictEqual(x.getAttribute("role"), "tab"));
  assert.strictEqual(tabs.filter(x => x.tabIndex === 0).length, 1);
  assert.strictEqual(tabs.filter(x => x.getAttribute("aria-selected") === "true").length, 1);
  assert.strictEqual(e.document.activeElement.getAttribute("aria-selected"), "true");
  const first = tabs[0];
  first.focus();
  e.key(first, "ArrowDown");
  assert.strictEqual(e.document.activeElement, tabs[1]);
  assert.strictEqual(tabs[1].tabIndex, 0);
  assert.strictEqual(first.tabIndex, -1);
  e.key(tabs[1], "End");
  assert.strictEqual(e.document.activeElement, tabs[tabs.length - 1]);
  e.key(tabs[tabs.length - 1], "ArrowDown");
  assert.strictEqual(e.document.activeElement, tabs[tabs.length - 1]);
  e.key(tabs[tabs.length - 1], "Home");
  assert.strictEqual(e.document.activeElement, tabs[0]);
  const modules = e.tab("Modules");
  modules.focus();
  e.key(modules, "Enter");
  await settle();
  assert.ok(e.tab("Modules").getAttribute("aria-selected") === "true");
  assert.strictEqual(e.document.activeElement.textContent, "Modules");
  const settings = e.tab("Settings");
  settings.focus();
  e.key(settings, " ");
  await settle();
  assert.strictEqual(e.FY.panelOpen, true);
  assert.ok(e.tab("Settings").getAttribute("aria-selected") === "true");
  const settingsTab = e.tab("Settings");
  settingsTab.focus();
  const repeats = e.calls.length;
  e.key(settingsTab, "Enter", { repeat: true });
  assert.strictEqual(e.calls.length, repeats);
});

t("keyboard: Escape closes the panel, ArrowLeft goes into the page and ArrowRight comes back", async () => {
  const e = boot();
  e.FY.toggle();
  e.tab("Modules").click();
  await settle();
  const tab = e.tab("Modules");
  tab.focus();
  e.key(tab, "ArrowLeft");
  assert.ok(e.content().contains(e.document.activeElement));
  e.key(e.content(), "ArrowRight");
  assert.strictEqual(e.document.activeElement.getAttribute("aria-selected"), "true");
  e.key(e.document.activeElement, "Escape");
  assert.strictEqual(e.FY.panelOpen, false);
  assert.strictEqual(e.panel().style.display, "none");
});

t("Modules: Escape closes the open sub-screen first, the panel only at the top, and focus never drops to the body", async () => {
  const e = boot();
  e.FY.toggle();
  e.tab("Modules").click();
  await settle();
  const more = e.control("More");
  more.focus();
  more.click();
  await flush();
  assert.notStrictEqual(e.document.activeElement, e.document.body);
  assert.strictEqual(e.document.activeElement.getAttribute("aria-label"), "Less");
  e.control("Versions").focus();
  e.control("Versions").click();
  await settle();
  assert.ok(e.content().textContent.includes("Loading") || e.control("Back"));
  assert.notStrictEqual(e.document.activeElement, e.document.body);
  e.answers.ver_list = { versions: [], more: false };
  await settle();
  e.key(e.document.activeElement === e.document.body ? e.content() : e.document.activeElement, "Escape");
  await settle();
  assert.strictEqual(e.FY.panelOpen, true);
  assert.ok(e.control("Update all"));
  assert.notStrictEqual(e.document.activeElement, e.document.body);
  e.key(e.document.activeElement, "Escape");
  assert.strictEqual(e.FY.panelOpen, false);
});

t("sizing: no scale transform, the rail scrolls and every tile and control is at least 48 px", async () => {
  const e = boot();
  e.FY.toggle();
  const style = e.panel().style;
  assert.ok(!/transform/.test(style.cssText));
  assert.ok(/overflow-y:\s*auto/.test(e.rail().style.cssText));
  assert.ok(/max-height/.test(e.rail().style.cssText));
  e.tabs().forEach(x => assert.ok(parseInt(x.style.height, 10) >= 48));
  assert.ok(/min-height:48px/.test(read("fy-ui.js")));
  const many = [];
  for (let i = 0; i < 12; i++) many.push(e.plugin("p" + i));
  e.FY.redraw();
  assert.ok(e.tabs().length >= 15);
});

t("toasts: at most 4 at once, oldest dropped, text cut, status role, no pointer events", () => {
  const e = boot();
  for (let i = 0; i < 20; i++) e.FY.toast("t" + i + "x".repeat(500), 1000);
  const box = e.document.querySelector('[role="status"]');
  assert.strictEqual(box.children.length, 4);
  assert.ok(box.children[0].textContent.startsWith("t16"));
  assert.ok(box.children[0].textContent.length <= 200);
  assert.ok(/pointer-events:\s*none/.test(box.style.cssText));
});

t("disposed plugins: api calls return an error, fetch rejects, rail omits them", async () => {
  const e = boot();
  const api = e.plugin("a");
  e.plugin("b");
  e.FY.toggle();
  assert.ok(e.tab("a"));
  e.FY.dispose("a", TOK);
  const n = e.calls.length;
  assert.strictEqual((await api.call("x")).error, "module disposed");
  await assert.rejects(api.fetch("https://example.org/"), /module disposed/);
  await assert.rejects(api.resource("home", "x"), /module disposed/);
  assert.strictEqual(e.calls.length, n);
  e.FY.redraw();
  assert.ok(!e.tab("a"));
  assert.ok(e.tab("b"));
});

t("key repeat does not re-activate ui controls", () => {
  const e = boot();
  const api = e.plugin("a");
  let n = 0;
  const b = api.ui.button({ label: "x", onclick: () => n++ });
  e.document.body.append(b);
  e.key(b, "Enter");
  e.key(b, "Enter", { repeat: true });
  e.key(b, " ", { repeat: true });
  assert.strictEqual(n, 1);
});

t("ui.list ignores null and primitive items instead of throwing", () => {
  const e = boot();
  const api = e.plugin("a");
  const l = api.ui.list({ items: [null, { id: 1, label: "one" }, 5, undefined, { label: "two" }] });
  assert.strictEqual(l.querySelectorAll('[role="option"]').length, 2);
  l.setItems(null);
  assert.strictEqual(l.querySelectorAll('[role="option"]').length, 0);
});

t("the public Framey surface cannot be overwritten by a plugin", () => {
  const e = boot();
  const F = e.FY;
  const before = ["dispose", "call", "_reply", "_k", "toast", "notify", "diag", "lazy", "i18n", "plugins", "ext"].map(k => F[k]);
  const attempt = e.window.eval("(function(F){['dispose','call','_reply','_k','toast','listen','notify','diag','lazy','i18n','plugins','ext'].forEach(function(k){ try { F[k] = function(){}; } catch (x) {} }); try { F.panelOpen = true; } catch (x) {}})");
  attempt(F);
  const after = ["dispose", "call", "_reply", "_k", "toast", "notify", "diag", "lazy", "i18n", "plugins", "ext"].map(k => F[k]);
  assert.deepStrictEqual(after, before);
  assert.strictEqual(F.panelOpen, false);
  assert.strictEqual(Object.isFrozen(F.i18n), true);
  assert.strictEqual(F.i18n.add("core", "en", { "notify.dismiss": "Keep it" }), 0);
});

t("lazy: window.eval and Function are never involved, built-in assets resolve at once", async () => {
  const e = boot();
  const w = e.window;
  const seen = [];
  const orig = w.eval;
  w.eval = function (s) { seen.push(String(s)); return orig.call(w, s); };
  const origFn = w.Function;
  w.Function = function (...a) { seen.push(a.join(",")); return origFn(...a); };
  await e.FY.lazy("picker");
  await e.FY.lazy("dev");
  assert.strictEqual(e.sent("asset").length, 0);
  assert.strictEqual(seen.length, 0);
  assert.ok(!seen.join("").includes(TOK));
  const src = read("fy-api.js") + read("inject.js") + FILES.map(read).join("");
  assert.ok(!/eval|new Function|Function\(/.test(src));
});

t("fyCall hardening: replacing window.fyCall after start does not intercept anything", async () => {
  const e = boot();
  const w = e.window;
  const spied = [];
  Object.defineProperty(w, "fyCall", { value: s => spied.push(s), configurable: true, writable: true });
  const api = e.plugin("a");
  e.K().call("state_get");
  api.fetch("https://example.org/");
  e.FY.call("a", "legacy");
  assert.strictEqual(spied.length, 0);
  assert.ok(e.sent("state_get").length === 1 && e.sent("fetch").length === 1 && e.sent("legacy").length === 1);
});

t("fyCall hardening: a non-native fyCall before start makes the panel refuse with a visible notice", () => {
  const { JSDOM } = (() => { const base = process.env.FRAMEY_JSDOM; return require(base ? require.resolve("jsdom", { paths: [base] }) : "jsdom"); })();
  const dom = new JSDOM("<!doctype html><body></body>", { url: "https://steamloopback.host/", runScripts: "outside-only" });
  const w = dom.window;
  w.fyCall = s => {};
  w.eval(read("inject.js"));
  assert.strictEqual(w.Framey, undefined);
  assert.ok(w.document.body.textContent.includes("Framey could not start safely"));
  assert.strictEqual(w.document.querySelector("[role=alert]").dataset.fy, "");
  const w2 = new JSDOM("<!doctype html><body></body>", { url: "https://steamloopback.host/", runScripts: "outside-only" }).window;
  w2.eval(read("inject.js"));
  assert.strictEqual(w2.Framey, undefined);
  const w3 = new JSDOM("<!doctype html><body></body>", { url: "https://steamloopback.host/", runScripts: "outside-only" }).window;
  w3.fyCall = (s => {}).bind(null);
  w3.eval(read("inject.js"));
  assert.ok(w3.Framey);
});

t("fyCall hardening: the bar refuses a non-native fyCall and keeps using the captured one", () => {
  const { JSDOM } = (() => { const base = process.env.FRAMEY_JSDOM; return require(base ? require.resolve("jsdom", { paths: [base] }) : "jsdom"); })();
  const mk = f => {
    const w = new JSDOM("<!doctype html><body></body>", { url: "https://steamloopback.host/", runScripts: "outside-only" }).window;
    w.FY_ICON_URL = "icon.svg";
    w.Element.prototype.getBoundingClientRect = function () { const width = this.tagName === "IMG" ? 50 : 300; return { left: 0, top: 0, right: width, bottom: 40, width, height: 40 }; };
    const row = w.document.createElement("div");
    const img = w.document.createElement("img");
    img.className = "avatar";
    row.append(img, w.document.createElement("span"), w.document.createElement("span"));
    w.document.body.append(row);
    w.fyCall = f;
    w.eval(read("bar.js"));
    return w;
  };
  const plain = mk(s => {});
  assert.strictEqual(plain.FYBar, undefined);
  assert.strictEqual(plain.document.querySelector(".fy-bb"), null);
  const got = [];
  const w = mk((s => got.push(JSON.parse(s))).bind(null));
  assert.ok(w.FYBar);
  const spy = [];
  Object.defineProperty(w, "fyCall", { value: s => spy.push(s), configurable: true });
  const btn = w.document.querySelector(".fy-bb");
  btn.click();
  btn.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true, repeat: true }));
  assert.strictEqual(spy.length, 0);
  assert.deepStrictEqual(got.map(c => [c.plugin, c.method]), [["_core", "toggle"]]);
  assert.ok(!("t" in got[0]));
});

t("fyCall hardening: a patched JSON.stringify never sees a payload that carries a token", async () => {
  const e = boot();
  const w = e.window;
  const seen = [];
  const orig = w.JSON.stringify;
  w.JSON.stringify = function (...a) { seen.push(String(orig.apply(w.JSON, a))); return orig.apply(w.JSON, a); };
  const api = e.plugin("a");
  e.K().call("state_get");
  const fetch = api.fetch("https://example.org/").catch(error => error);
  e.FY.toggle();
  e.FY.dispose(undefined, TOK);
  assert.match((await fetch).message, /module disposed/);
  w.JSON.stringify = orig;
  assert.ok(!seen.join("\n").includes(TOK));
  assert.ok(!seen.join("\n").includes("tok-a-0000"));
  assert.ok(e.sent("state_get")[0].t === TOK);
});

t("no token-bound function is reachable from window.Framey: every reachable function, called with a core method name, sends nothing chosen by the caller", async () => {
  const e = boot();
  const w = e.window;
  e.FY.toggle();
  e.FY.lazy("dev");
  e.serve("dev");
  await settle();
  e.FY.lazy("picker");
  e.serve("picker");
  await settle();
  e.FY.toggle();
  const fixed = new Set(["state_get", "panel", "list", "store", "dev_status", "diag", "asset", "loader", "autocheck_status", "maint_get", "maint_prefs", "status", "settings_repair"]);
  const roots = [e.FY, w.Framey.pages, w.Framey.dev, w.Framey.i18n];
  const seen = new Set();
  const fns = [];
  const walk = (o, depth, trail) => {
    if (!o || (typeof o !== "object" && typeof o !== "function") || seen.has(o) || depth > 5) return;
    seen.add(o);
    for (let cur = o; cur && cur !== w.Object.prototype && cur !== w.Function.prototype; cur = Object.getPrototypeOf(cur)) {
      Reflect.ownKeys(cur).forEach(k => {
        if (k === "constructor" || k === "prototype" || k === "__proto__") return;
        let v;
        try { v = o[k]; } catch (x) { return; }
        if (typeof v === "function") fns.push([trail + "." + String(k), v, o]);
        walk(v, depth + 1, trail + "." + String(k));
      });
    }
  };
  roots.forEach((r, i) => walk(r, 0, "Framey" + i));
  assert.ok(fns.length > 30, String(fns.length));
  assert.ok(!fns.some(f => /.(kit|make|call)$/.test(f[0]) && /pages/.test(f[0])));
  const names = new Set(fns.map(f => f[0]));
  assert.ok(![...names].some(n => /kit|.make/.test(n)));
  const marker = "install-" + Math.random();
  const before = e.calls.length;
  const quiet = () => {};
  process.on("unhandledRejection", quiet);
  for (const [name, fn, self] of fns) {
    if (/dispose|toggle|open/.test(name)) continue;
    const n = e.calls.length;
    try { await Promise.resolve(fn.call(self, "install", { url: marker }, marker)).catch(() => {}); } catch (x) {}
    try { await Promise.resolve(fn.call(self, marker)).catch(() => {}); } catch (x) {}
    e.calls.slice(n).filter(c => c.t || c.plugin === "_core").forEach(c => {
      assert.ok(!JSON.stringify(c).includes(marker), name + " forwarded caller input: " + JSON.stringify(c));
      assert.notStrictEqual(c.method, "install", name);
      if (c.t) assert.ok(fixed.has(c.method), name + " sent " + c.method + " with a token");
    });
  }
  await settle();
  process.off("unhandledRejection", quiet);
  assert.ok(e.calls.length >= before);
  const pages = w.Framey.pages;
  assert.ok(Object.isFrozen(pages) && Object.isFrozen(pages.modules) && Object.isFrozen(pages.settings) && Object.isFrozen(w.Framey.i18n));
  assert.deepStrictEqual(Object.keys(pages.modules), ["render"]);
  assert.deepStrictEqual(Object.keys(pages.settings), ["render"]);
  assert.strictEqual(w.Framey._k(marker), null);
  assert.strictEqual(Object.keys(w).filter(k => /fyt|tok/i.test(k)).length, 0);
});

const eventPayload = (over = {}) => Object.assign({
  method: "batch_apply", id: null, ok: 1, failed: 2,
  results: [
    { id: "alpha", name: "Alpha", state: "ok", from: "1.0.0", to: "1.1.0" },
    { id: "beta", name: "Beta", state: "failed", error: "download failed" },
    { id: "gamma", name: "Gamma", state: "failed", error: "start failed" },
    { id: "delta", name: "Delta", state: "skipped", reason: "frozen" },
  ],
}, over);

t("update result: the event is stored, shown after the panel was wiped and re-injected, and stays until dismissed", async () => {
  const e = boot();
  const w = e.window;
  e.FY.toggle();
  e.tab("Modules").click();
  await settle();
  e.FY._k(TOK).event("_core", "update_result", eventPayload(), 0);
  await settle();
  assert.ok(e.content().textContent.includes("1 updated, 2 failed"));
  assert.ok(e.content().textContent.includes("Beta"));
  const stored = JSON.parse(w.sessionStorage.fyRes);
  assert.ok(stored.rows.length === 4 && stored.rows.some(r => r[0] === "failed"));
  assert.ok(JSON.stringify(stored).length < 8000);
  for (let round = 0; round < 2; round++) {
    w.eval(WIPE);
    assert.strictEqual(w.Framey, undefined);
    ["inject.js"].concat(FILES).forEach(f => e.load(f));
    w.eval("Framey.restore()");
    await settle();
    assert.strictEqual(w.Framey.panelOpen, true);
    const text = w.document.body.textContent;
    assert.ok(text.includes("Last update result"), "round " + round);
    assert.ok(text.includes("download failed") && text.includes("start failed"));
    assert.ok(text.includes("Updated Alpha") || text.includes("Alpha"));
  }
  const back = [...w.document.querySelectorAll("[aria-label]")].find(x => x.getAttribute("aria-label") === "Back");
  back.click();
  await settle();
  assert.strictEqual(w.sessionStorage.fyRes, undefined);
  assert.ok(!w.document.body.textContent.includes("Last update result"));
  w.eval(WIPE);
  ["inject.js"].concat(FILES).forEach(f => e.load(f));
  w.eval("Framey.restore()");
  await settle();
  assert.ok(!w.document.body.textContent.includes("Last update result"));
});

t("update result: failures of a batch stay visible, the stored copy is bounded and failures are kept first", async () => {
  const e = boot();
  const rows = [];
  for (let i = 0; i < 100; i++) rows.push({ id: "m" + i, name: "Module" + i, state: i === 99 ? "failed" : "ok", to: "2.0.0", error: i === 99 ? "x".repeat(2000) : undefined });
  e.FY._k(TOK).event("_core", "update_result", { method: "batch_apply", id: null, ok: 99, failed: 1, results: rows }, 0);
  const stored = JSON.parse(e.window.sessionStorage.fyRes);
  assert.ok(stored.rows.length <= 32);
  assert.strictEqual(stored.rows[0][0], "failed");
  assert.ok(stored.rows[0][1].length <= 300);
  assert.ok(JSON.stringify(stored).length <= 8000);
  e.FY._k(TOK).event("_core", "update_result", { method: "update", id: "alpha", ok: true, failed: 0, results: [{ id: "alpha", name: "Alpha", state: "ok", to: "3.0.0" }] }, 0);
  assert.strictEqual(JSON.parse(e.window.sessionStorage.fyRes).rows.length, 1);
  e.FY._k(TOK).event("_core", "update_result", { method: "update", id: "alpha", ok: false, failed: 1, results: [], failures: [{ id: "alpha", name: "Alpha", error: "bad zip" }] }, 0);
  const f = JSON.parse(e.window.sessionStorage.fyRes);
  assert.strictEqual(f.good, false);
  assert.ok(f.rows[0][1].includes("bad zip"));
});

t("update result: without a panel the event raises a toast and keeps the result for the next visit; a result screen on display is not replaced", async () => {
  const e = boot();
  e.FY._k(TOK).event("_core", "update_result", eventPayload(), 0);
  assert.ok(e.document.querySelector('[role="status"]').textContent.includes("1 updated, 2 failed"));
  e.FY.toggle();
  e.tab("Modules").click();
  await settle();
  assert.ok(e.content().textContent.includes("Last update result"));
  e.FY._k(TOK).event("_core", "update_result", eventPayload({ ok: 3, failed: 0, results: [{ id: "a", name: "A", state: "ok", to: "1" }] }), 0);
  assert.ok(e.content().textContent.includes("3 updated, 0 failed"));
});

t("update result: the reply screen of a successful update is persisted and its Back clears it", async () => {
  const e = boot({ answers: { update: { ok: true, version: "1.2.0" }, check: { checked: { alpha: { state: "update", latest: "1.2.0" } } } } });
  e.FY.toggle();
  e.tab("Modules").click();
  await settle();
  e.control("Check for updates").click();
  await settle();
  e.control("Update").click();
  await settle();
  e.control("Install").click();
  await settle();
  assert.ok(e.content().textContent.includes("Updated"));
  assert.ok(e.window.sessionStorage.fyRes);
  e.control("Back").click();
  await settle();
  assert.strictEqual(e.window.sessionStorage.fyRes, undefined);
});

t("restore opens Modules when a result is waiting and the panel was open", async () => {
  const e = boot();
  const w = e.window;
  e.FY.toggle();
  e.tab("Settings").click();
  await settle();
  e.FY._k(TOK).event("_core", "update_result", eventPayload(), 0);
  w.eval(WIPE);
  ["inject.js"].concat(FILES).forEach(f => e.load(f));
  w.eval("Framey.restore()");
  await settle();
  assert.strictEqual(w.sessionStorage.fyTab, "#modules");
});

t("versions: a failed page is not skipped on retry and the error clears after a success", async () => {
  const e = boot();
  e.FY.toggle();
  e.tab("Modules").click();
  await settle();
  let pages = 0;
  const seen = [];
  e.answers.ver_list = a => {
    seen.push(a.page);
    pages++;
    if (pages === 2) return { error: "network" };
    return { versions: [{ tag: "v" + pages, version: "1." + pages, published: "2026-10-01" }], more: true };
  };
  e.control("More").click();
  await flush();
  e.control("Versions").click();
  await settle();
  e.control("Show more versions").click();
  await settle();
  assert.ok(e.content().textContent.includes("network"));
  e.control("Show more versions").click();
  await settle();
  assert.deepStrictEqual(seen, [1, 2, 2]);
  assert.ok(!e.content().textContent.includes("network"));
});

t("the settings page shows channel, safe mode and developer mode as read-only text", async () => {
  const e = boot({ answers: { loader: { commit: "1234567", kept: false } } });
  e.FY.toggle();
  e.tab("Settings").click();
  await settle();
  assert.ok(e.content().textContent.includes("~/.config/framey/loader.json"));
  assert.ok(e.content().textContent.includes("~/.config/framey/safe"));
  assert.ok(e.content().textContent.includes("~/.config/framey/developer"));
  assert.strictEqual(e.sent("prefs_set").length, 0);
});

t("independently authored legacy-style fixture: render and backend calls stay bound to its own id and token", async () => {
  const e = boot({ answers: { sample: { value: 1 } } });
  const fixture = `Framey.register({
    short: "Fan",
    render(box) {
      box.append(Framey.el("", "Temperature sensor"));
      Framey.call("fan", "sample", { fixture: true });
    }
  });`;
  e.window.eval("(function(Framey){" + fixture + "\n})(window.__bindFramey(\"fan\", \"tok-fan-00000000\", {id: \"fan\", name: \"Legacy fixture\", short: \"Fan\", version: \"1.0.0\"}))");
  e.FY.toggle();
  await settle();
  assert.strictEqual(e.FY.plugins.find(p => p.id === "fan").short, "Fan");
  assert.ok(e.document.body.textContent.includes("Temperature sensor"));
  const rendered = e.sent("sample");
  assert.strictEqual(rendered.length, 1);
  assert.strictEqual(rendered[0].plugin, "fan");
  assert.strictEqual(rendered[0].t, "tok-fan-00000000");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(rendered[0].arg)), { fixture: true });
  e.window.eval("window.__bindFramey(\"fan\", \"tok-fan-00000000\", {}).call(\"victim\", \"set\", {})");
  const last = e.calls.filter(c => c.method === "set").pop();
  assert.strictEqual(last.plugin, "fan");
  assert.strictEqual(last.t, "tok-fan-00000000");
});

t("a plugin cannot reach another plugin's api, extend Framey.ext after load, or take a built-in id", () => {
  const e = boot();
  const F = e.FY;
  e.plugin("a");
  const b = e.plugin("b");
  assert.strictEqual(F.plugins.every(p => !("api" in p)), true);
  assert.throws(() => F.ext.push(() => {}), /not extensible/);
  assert.strictEqual(b.id, "b");
  assert.throws(() => e.window.__bindFramey("modules", "tok-modules-0000"), /reserved/);
  assert.throws(() => e.window.__bindFramey("settings", "tok-settings-0"), /reserved/);
});

t("events are accepted only through the loader's token", () => {
  const e = boot();
  const seen = [];
  e.FY._k(TOK).listen("progress", p => seen.push(p));
  assert.strictEqual(e.FY._k("forged-token-x"), null);
  assert.strictEqual(e.FY._event, undefined);
  e.FY._k(TOK).event("_core", "progress", { stage: "y" }, 0);
  assert.deepStrictEqual(seen, [{ stage: "y" }]);
});

t("trusted_input off (default): button clicks and keys run the handler with the event, nothing is counted", () => {
  const e = boot();
  const got = [];
  const b = e.FY.button("Go", "", ev => got.push(ev && ev.type));
  e.window.document.body.append(b);
  b.click();
  e.key(b, "Enter");
  assert.deepStrictEqual(got, ["click", "keydown"]);
  assert.strictEqual(e.FY.diag().untrusted_clicks, 0);
});

t("trusted_input require: synthetic clicks and keys are ignored and counted in diag", () => {
  const e = boot({ before: w => { w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux", lang: "en", trusted_input: "require" }; } });
  let n = 0;
  const b = e.FY.button("Go", "", () => n++);
  e.window.document.body.append(b);
  b.click();
  e.key(b, "Enter");
  b.dispatchEvent(new e.window.MouseEvent("click", { bubbles: true }));
  assert.strictEqual(n, 0);
  assert.strictEqual(e.FY.diag().untrusted_clicks, 3);
});

t("closing the panel returns focus to the element that opened it", () => {
  const e = boot();
  const bar = e.document.createElement("button");
  e.document.body.append(bar);
  bar.focus();
  e.FY.toggle();
  e.key(e.document.activeElement, "Escape");
  assert.strictEqual(e.document.activeElement, bar);
});

t("reload isolation: old page and mount handles cannot remove a replacement or another plugin's content", () => {
  const e = boot();
  const old = e.plugin("a");
  const neighbor = e.plugin("b");
  const oldPage = old.pages.register({ id: "tools", mode: "full", render(box) { box.textContent = "old tools"; } });
  const oldMount = old.global.mount("status", { render(box) { box.textContent = "old status"; } });
  const otherMount = neighbor.global.mount("status", { render(box) { box.textContent = "neighbor status"; } });
  oldPage.open();
  e.FY.dispose("a", TOK);
  const current = e.plugin("a");
  const currentPage = current.pages.register({ id: "tools", mode: "full", render(box) { box.textContent = "current tools"; } });
  const currentMount = current.global.mount("status", { render(box) { box.textContent = "current status"; } });
  currentPage.open();
  assert.strictEqual(oldPage.close(), false);
  assert.strictEqual(oldPage.remove(), false);
  oldMount.unmount();
  assert.strictEqual(current.pages.list()[0].open, true);
  assert.ok(e.document.querySelector('[role="dialog"]').textContent.includes("current tools"));
  assert.ok(currentMount.el.isConnected);
  assert.ok(otherMount.el.isConnected);
  assert.strictEqual(otherMount.el.textContent, "neighbor status");
  e.FY.dispose("a", TOK);
  assert.strictEqual(currentMount.el.isConnected, false);
  assert.strictEqual(e.document.querySelector('[role="dialog"]'), null);
  assert.ok(otherMount.el.isConnected);
});

t("reload isolation: stale unsubscribe and cleanup removers leave replacement registrations intact", () => {
  const e = boot();
  const previous = e.plugin("a");
  const got = [];
  const oldOff = previous.events.on("tick", () => got.push("previous"));
  const oldRemoveCleanup = previous.cleanup(() => got.push("previous cleanup"));
  const current = e.plugin("a");
  current.events.on("tick", value => got.push("current " + value));
  current.cleanup(() => got.push("current cleanup"));
  oldOff();
  oldRemoveCleanup();
  previous.events.off("tick", () => {});
  e.event("a", "tick", 1);
  assert.deepStrictEqual(got, ["previous cleanup", "current 1"]);
  e.FY.dispose("a", TOK);
  assert.deepStrictEqual(got, ["previous cleanup", "current 1", "current cleanup"]);
});

t("event disposal is immediate for its remaining callbacks and does not clear another plugin", () => {
  const e = boot();
  const a = e.plugin("a");
  const b = e.plugin("b");
  const got = [];
  a.events.on("tick", () => { got.push("a first"); e.FY.dispose("a", TOK); });
  a.events.on("tick", () => got.push("a after disposal"));
  b.events.on("tick", value => got.push("b " + value));
  e.event("a", "tick", 1);
  e.event("b", "tick", 2);
  e.event("a", "tick", 3);
  assert.deepStrictEqual(got, ["a first", "b 2"]);
  assert.strictEqual(e.FY.diag().plugins.find(p => p.id === "b").subscriptions, 1);
});

t("reload isolation: cleanup of one module removes its listeners without changing a neighbor's listener", () => {
  const e = boot();
  const a = e.plugin("a");
  const b = e.plugin("b");
  const target = e.document.createElement("button");
  e.document.body.append(target);
  const got = [];
  const oldOff = a.on(target, "click", () => got.push("previous"));
  b.on(target, "click", () => got.push("neighbor"));
  const current = e.plugin("a");
  current.on(target, "click", () => got.push("current"));
  oldOff();
  target.click();
  assert.deepStrictEqual(got, ["neighbor", "current"]);
  e.FY.dispose("a", TOK);
  target.click();
  assert.deepStrictEqual(got, ["neighbor", "current", "neighbor"]);
});

t("catalog isolation: scoped assessment requests never acquire the catalog's core capability", async () => {
  const e = boot({ files: CATALOG_FILES, answers: {
    catalog_assess: (arg, request) => request.t === TOK ? { read_only: true } : { error: "not authorized", code: "forbidden" },
  } });
  const api = e.plugin("a");
  const n = e.calls.length;
  const denied = await e.FY.call("_core", "catalog_assess", { id: "fixture", source: "decky_stable" });
  assert.strictEqual(denied.error, "not authorized");
  assert.strictEqual(e.calls.length, n);
  await assert.rejects(api._rpc("catalog_assess", { id: "fixture", source: "decky_stable" }), error => error.code === "forbidden");
  const scoped = e.sent("catalog_assess").at(-1);
  assert.strictEqual(scoped.plugin, "_core");
  assert.strictEqual(scoped.t, "tok-a-0000");
  assert.notStrictEqual(scoped.t, TOK);
  assert.strictEqual(e.FY._k(scoped.t), null);
  assert.strictEqual("catalog_assess" in api, false);
  const allowed = await e.K().call("catalog_assess", { id: "fixture", source: "decky_stable" });
  assert.strictEqual(allowed.read_only, true);
  assert.strictEqual(e.sent("catalog_assess").at(-1).t, TOK);
});

t("catalog isolation: loaded catalog pages cannot be replaced through the public page registry", () => {
  const e = boot({ files: CATALOG_FILES });
  const a = e.window.__bindFramey("a", "tok-a-0000", { name: "a" });
  a.register({ render() {} });
  const before = e.FY.pages.decky_store;
  const replace = e.window.eval(`(function(FY, scope) {
    try { FY.pages.decky_store = { render() {} }; } catch (error) {}
    try { Object.defineProperty(FY.pages, "decky_store", { value: { render() {} } }); } catch (error) {}
    try { FY.pages = { decky_store: { render() {} } }; } catch (error) {}
    try { scope.pages = FY.pages; } catch (error) {}
  })`);
  replace(e.FY, a);
  assert.strictEqual(e.FY.pages.decky_store, before);
  assert.strictEqual(Object.isFrozen(before), true);
  assert.strictEqual("pages" in a, false);
  for (const id of ["store", "discover", "decky-store"]) {
    assert.throws(() => e.window.__bindFramey(id, "tok-reserved-0000", {}), /reserved/);
  }
  assert.strictEqual(typeof e.FY.pages.store.render, "function");
  assert.strictEqual(typeof e.FY.pages.decky_store.render, "function");
});

t("catalog isolation: switching native and Decky stores preserves peers and hides all tabs of a hidden module", async () => {
  const source = id => ({ id, label: id, url: "https://example.com/catalog.json" });
  const e = boot({ files: CATALOG_FILES, answers: {
    state_get: { order: ["a", "b"], hidden: ["a"], sort: "custom", frozen: {}, prefs: {}, safe: false },
    catalog_sources: { source: source("stable"), sources: [source("stable"), source("testing"), source("custom")], decky_sources: [source("decky_stable"), source("decky_testing")], custom_url: "" },
    catalog_list: arg => ({ rows: [], total: 0, offset: 0, limit: 50, categories: [], source: source(arg.source), origin: "downloaded", stale: false, error: null }),
  } });
  const a = e.plugin("a"), b = e.plugin("b");
  a.pages.register({ id: "extra", title: "Hidden extra", render(box) { box.textContent = "Hidden body"; } });
  const am = a.global.mount("status", { render(box) { box.textContent = "A status"; } });
  const bm = b.global.mount("status", { render(box) { box.textContent = "B status"; } });
  a.notify({ title: "A notice", duration: 0 });
  b.notify({ title: "B notice", duration: 0 });
  const target = e.document.createElement("button");
  e.document.body.append(target);
  let events = 0, clicks = 0;
  b.events.on("tick", () => events++);
  b.on(target, "click", () => clicks++);
  e.FY.open("#discover");
  await settle();
  e.FY.open("#decky-store");
  await settle();
  assert.ok(!e.tab("a"));
  assert.ok(!e.tab("Hidden"));
  assert.ok(e.tab("b"));
  assert.ok(am.el.isConnected && bm.el.isConnected);
  e.FY.dispose("a", TOK);
  assert.strictEqual(am.el.isConnected, false);
  assert.strictEqual(bm.el.isConnected, true);
  e.event("b", "tick", {});
  target.click();
  assert.strictEqual(events, 1);
  assert.strictEqual(clicks, 1);
  const notices = [...e.document.querySelectorAll('[role="status"]')].filter(node => node.dataset.fy === "a" || node.dataset.fy === "b");
  assert.strictEqual(notices.length, 1);
  assert.strictEqual(notices[0].dataset.fy, "b");
  assert.strictEqual(e.sent("catalog_assess").length, 0);
  assert.strictEqual(e.sent("catalog_install").length, 0);
});

t("event lifecycle: once is removed before recursive dispatch and peer ordering is preserved", () => {
  const e = boot({ files: ["fy-api.js"] });
  const a = e.plugin("a"), b = e.plugin("b"), seen = [];
  a.events.once("tick", value => { seen.push("once " + value); e.event("a", "tick", "nested"); });
  a.events.on("tick", value => seen.push("a " + value));
  b.events.on("tick", value => seen.push("b " + value));
  e.event("a", "tick", "outer");
  e.event("b", "tick", "peer");
  assert.deepStrictEqual(seen, ["once outer", "a nested", "a outer", "b peer"]);
  assert.strictEqual(e.FY.diag().plugins.find(plugin => plugin.id === "a").subscriptions, 1);
});

t("event lifecycle: subscriptions added during dispatch wait and removed callbacks stay removed", () => {
  const e = boot({ files: ["fy-api.js"] });
  const a = e.plugin("a"), seen = [];
  let changed = false, offRemoved;
  a.events.on("tick", value => {
    seen.push("first " + value);
    if (!changed) {
      changed = true;
      a.events.on("tick", next => seen.push("added " + next));
      offRemoved();
    }
  });
  offRemoved = a.events.on("tick", value => seen.push("removed " + value));
  a.events.on("tick", value => seen.push("last " + value));
  e.event("a", "tick", "one");
  e.event("a", "tick", "two");
  assert.deepStrictEqual(seen, ["first one", "last one", "first two", "last two", "added two"]);
});

t("event lifecycle: rebind during a callback kills the old dispatch without losing the new instance or peer", () => {
  const e = boot({ files: ["fy-api.js"] });
  const a = e.plugin("a"), b = e.plugin("b"), seen = [];
  a.events.on("tick", value => {
    seen.push("old " + value);
    const replacement = e.plugin("a");
    replacement.events.on("tick", next => seen.push("new " + next));
    e.event("a", "tick", "nested");
  });
  a.events.on("tick", () => seen.push("old after replacement"));
  b.events.on("tick", value => seen.push("b " + value));
  e.event("a", "tick", "outer");
  e.event("a", "tick", "next");
  e.event("b", "tick", "peer");
  assert.deepStrictEqual(seen, ["old outer", "new nested", "new next", "b peer"]);
});

t("event lifecycle: callback errors with hostile message accessors cannot escape or expose their details", () => {
  const e = boot({ files: ["fy-api.js"] });
  const a = e.plugin("private_fixture_module"), b = e.plugin("b"), seen = [];
  const privateText = "token=fixture-secret /home/private-fixture/device 192.0.2.23";
  let messageReads = 0;
  const error = Object.defineProperty({}, "message", { get() { messageReads++; throw new Error(privateText); } });
  a.events.on("tick", () => { throw error; });
  a.events.on("tick", value => seen.push("a " + value));
  b.events.on("tick", value => seen.push("b " + value));
  const calls = e.calls.length;
  assert.doesNotThrow(() => e.event("private_fixture_module", "tick", "after failure"));
  e.event("b", "tick", "peer");
  assert.deepStrictEqual(seen, ["a after failure", "b peer"]);
  assert.strictEqual(messageReads, 0);
  assert.strictEqual(e.calls.length, calls);
  const diagnostics = e.K().diagnostics();
  assert.ok(diagnostics.some(row => row.count > 0));
  for (const row of diagnostics) {
    assert.deepStrictEqual(Object.keys(row).sort(), ["code", "count"]);
    assert.strictEqual(typeof row.code, "string");
    assert.ok(Number.isInteger(row.count) && row.count >= 1 && row.count <= 9999);
  }
  const exposed = JSON.stringify(diagnostics);
  for (const value of [privateText, "fixture-secret", "private_fixture_module", "private-fixture", "192.0.2.23"]) assert.ok(!exposed.includes(value));
  assert.strictEqual(e.document.querySelector('[role="status"],[role="alert"]'), null);
});

t("event lifecycle: fixed diagnostics are bounded immutable snapshots and reject arbitrary values without coercion", () => {
  const e = boot({ files: ["fy-api.js"] });
  const api = e.plugin("a");
  api.events.on("tick", () => { throw new Error("fixture-private-detail"); });
  e.event("a", "tick", null);
  const core = e.K();
  const row = core.diagnostics().find(entry => entry.count > 0);
  assert.ok(row);
  let coerced = false;
  const before = JSON.stringify(core.diagnostics());
  const values = ["fixture-private-detail", "__proto__", "constructor", "a", "", null, 1, Symbol("private"), { toString() { coerced = true; throw new Error("do not coerce"); } }];
  for (const value of values) assert.doesNotThrow(() => core.diagnostic(value));
  assert.strictEqual(coerced, false);
  assert.strictEqual(JSON.stringify(core.diagnostics()), before);
  const calls = e.calls.length;
  for (let i = 0; i < 12000; i++) core.diagnostic(row.code);
  const snapshot = core.diagnostics();
  assert.ok(snapshot.length <= 24);
  assert.strictEqual(snapshot.find(entry => entry.code === row.code).count, 9999);
  try { snapshot[0].count = -1; snapshot[0].code = "injected"; snapshot.push({ code: "injected", count: 1 }); } catch (error) {}
  assert.strictEqual(core.diagnostics().find(entry => entry.code === row.code).count, 9999);
  assert.ok(!core.diagnostics().some(entry => entry.code === "injected"));
  assert.strictEqual(e.calls.length, calls);
  assert.strictEqual(e.clock.pending(), 0);
  assert.strictEqual("diagnostic" in api, false);
  assert.strictEqual("diagnostics" in e.FY, false);
});

t("event lifecycle: diagnostic counters survive module cleanup and reset with a fresh injection", () => {
  const e = boot({ files: ["fy-api.js"] });
  const a = e.plugin("a");
  a.events.on("tick", () => { throw new Error("private-first-error"); });
  e.event("a", "tick", null);
  const previous = e.K(), record = previous.diagnostics().find(row => row.count > 0);
  assert.ok(record);
  e.FY.dispose("a", TOK);
  assert.strictEqual(previous.diagnostics().find(row => row.code === record.code).count, record.count);
  const replacement = e.plugin("a");
  replacement.events.on("tick", () => { throw new Error("private-second-error"); });
  e.event("a", "tick", null);
  assert.strictEqual(previous.diagnostics().find(row => row.code === record.code).count, record.count + 1);
  e.FY.dispose(undefined, TOK);
  assert.ok(previous.diagnostics().length > 0);
  e.window.eval("delete window.Framey");
  e.load("inject.js");
  e.load("fy-api.js");
  const current = e.window.Framey._k(TOK);
  assert.notStrictEqual(current, previous);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(current.diagnostics())), []);
  e.window.close();
});

const main = async () => {
  let pass = 0;
  const failed = [];
  for (const [name, fn] of tests) {
    try {
      await fn();
      pass++;
      console.log("ok   " + name);
    } catch (err) {
      failed.push(name);
      console.log("FAIL " + name + "\n     " + String((err && err.stack) || err).split("\n").slice(0, 7).join("\n     "));
    }
  }
  console.log("\n" + pass + " passed, " + failed.length + " failed, " + tests.length + " total");
  process.exit(failed.length ? 1 : 0);
};

main();
