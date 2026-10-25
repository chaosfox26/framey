const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { JSDOM } = require(process.env.FRAMEY_JSDOM ? require.resolve("jsdom", { paths: [process.env.FRAMEY_JSDOM] }) : "jsdom");
const { makeDom, TOK } = require("./harness");

const root = path.join(__dirname, "..", "..");
const read = f => fs.readFileSync(path.join(root, f), "utf8");
const norm = x => (x === undefined ? x : JSON.parse(JSON.stringify(x)));
const eq = (a, b) => assert.deepStrictEqual(norm(a), norm(b));
const RM = "document.querySelectorAll('[data-fy]').forEach(e=>e.remove());";

const make = () => {
  const h = makeDom({ loadFiles: ["fy-pages.js"] });
  const w = h.window;
  const env = { w, d: w.document, calls: h.calls, opened: [], redraws: 0, keys: [] };
  const FY = (env.FY = w.Framey);
  FY.open = id => env.opened.push(id);
  FY.redraw = key => { env.redraws++; env.keys.push(key); };
  env.plugin = (id, pre) => {
    const p = { render() {} };
    if (pre) pre(p);
    const api = w.__bindFramey(id, "tok-" + id + "-0000", { id, name: id, short: id }).register(p);
    api.p = FY.plugins.find(x => x.api === api);
    return api;
  };
  env.fy = () => [...env.d.querySelectorAll("[data-fy]")];
  env.calls_for = m => env.calls.filter(c => c.method === m).map(c => c.arg);
  return env;
};

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("fy-pages is idempotent and exposes the contract surface", () => {
  const e = make();
  const n = e.FY.ext.length;
  e.w.eval("(function(__FY,__trusted){" + read("fy-pages.js") + "\n})(window.__S.FY, window.__S.trusted)");
  assert.strictEqual(e.FY.ext.length, n);
  assert.strictEqual(typeof e.FY.panelPages, "function");
  eq(e.FY.panelPages(), []);
  assert.strictEqual(typeof e.FY.pages, "object");
  const api = e.plugin("a");
  eq(Object.keys(api.pages).sort(), ["close", "list", "open", "register"]);
  eq(Object.keys(api.global), ["mount"]);
  assert.strictEqual(typeof api.target, "function");
  eq(Object.keys(api.target("main")).sort(), ["css", "js", "remove"]);
});

test("loads without a Framey object and without ext", () => {
  const dom = new JSDOM("<body></body>", { runScripts: "outside-only" });
  dom.window.eval("(function(__FY,__trusted){" + read("fy-pages.js") + "\n})(undefined, undefined)");
  assert.strictEqual(dom.window.Framey, undefined);
});

test("panel page: register, tab object, open, close, remove", () => {
  const e = make();
  const api = e.plugin("a");
  const closed = [];
  const renders = [];
  const h = api.pages.register({ id: "home", title: "Home page", render: (box, ctx) => renders.push([box, ctx]), onclose: () => closed.push(1) });
  eq(Object.keys(h).sort(), ["close", "open", "remove"]);
  assert.strictEqual(e.redraws, 1);
  const tabs = e.FY.panelPages();
  assert.strictEqual(tabs.length, 1);
  assert.strictEqual(tabs[0].id, "a:home");
  assert.strictEqual(tabs[0].plugin, "a");
  assert.strictEqual(tabs[0].short, "Home");
  assert.strictEqual(tabs[0].api, undefined);
  assert.strictEqual(e.FY.panelPages()[0], tabs[0]);
  eq(api.pages.list(), [{ id: "home", key: "a:home", title: "Home page", mode: "panel", open: false }]);
  assert.strictEqual(h.open(), true);
  eq(e.opened, ["a:home"]);
  const box = e.d.createElement("div");
  tabs[0].render(box, api);
  assert.strictEqual(renders.length, 1);
  assert.strictEqual(renders[0][0], box);
  assert.strictEqual(renders[0][1].id, "home");
  assert.strictEqual(renders[0][1].mode, "panel");
  assert.strictEqual(api.pages.list()[0].open, true);
  h.close();
  assert.strictEqual(closed.length, 1);
  eq(e.opened, ["a:home", "a"]);
  h.close();
  assert.strictEqual(closed.length, 1);
  tabs[0].render(box, api);
  assert.strictEqual(h.remove(), true);
  assert.strictEqual(closed.length, 2);
  eq(api.pages.list(), []);
  eq(e.FY.panelPages(), []);
  assert.strictEqual(h.remove(), false);
  assert.strictEqual(h.open(), false);
  assert.strictEqual(h.close(), false);
});

test("panel pages are grouped by plugin order", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  b.pages.register({ id: "b1", title: "B1", render() {} });
  a.pages.register({ id: "a1", title: "A1", render() {} });
  a.pages.register({ id: "a2", title: "A2", render() {} });
  eq(e.FY.panelPages().map(t => t.id), ["a:a1", "a:a2", "b:b1"]);
});

test("api.pages.open only opens own pages", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  a.pages.register({ id: "x", render() {} });
  assert.strictEqual(b.pages.open("x"), false);
  assert.strictEqual(a.pages.open("x"), true);
  assert.strictEqual(a.pages.open("missing"), false);
});

test("full page: layer, Back control, render, close order", () => {
  const e = make();
  const api = e.plugin("a");
  const log = [];
  const h = api.pages.register({ id: "big", mode: "full", title: "Big", render: (box, ctx) => { log.push("render"); box.textContent = "hello"; assert.strictEqual(ctx.mode, "full"); }, onclose: () => log.push("close") });
  assert.strictEqual(e.redraws, 0);
  eq(e.FY.panelPages(), []);
  assert.strictEqual(e.d.body.querySelector("[data-fy]:not([data-fy=''])"), null);
  assert.strictEqual(h.open(), true);
  const layer = e.d.querySelector("[data-fy='a']");
  assert.ok(layer);
  assert.strictEqual(layer.style.position, "fixed");
  assert.strictEqual(layer.style.zIndex, "99998");
  assert.ok(Number(layer.style.zIndex) < 99999);
  assert.strictEqual(layer.textContent.includes("hello"), true);
  const back = [...layer.querySelectorAll("div")].find(x => x.textContent === "Back");
  assert.ok(back);
  assert.strictEqual(back.tabIndex, 0);
  assert.strictEqual(back.getAttribute("role"), "button");
  assert.strictEqual(back.dataset.fy, "a");
  assert.strictEqual(h.open(), false);
  back.click();
  eq(log, ["render", "close"]);
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
  eq(e.opened, []);
  assert.strictEqual(h.open(), true);
  back.dispatchEvent(new e.w.KeyboardEvent("keydown", { key: "Enter" }));
  eq(log, ["render", "close", "render", "close"]);
});

test("Back keyboard activation on the fallback control", () => {
  const e = make();
  const api = e.plugin("a");
  let closes = 0;
  api.pages.register({ id: "p", mode: "full", render() {}, onclose: () => closes++ }).open();
  const back = [...e.d.querySelectorAll("[data-fy='a'] div")].find(x => x.textContent === "Back");
  back.dispatchEvent(new e.w.KeyboardEvent("keydown", { key: " " }));
  assert.strictEqual(closes, 1);
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
});

test("full page prefers api.ui.button when present", () => {
  const e = make();
  const api = e.plugin("a");
  const seen = [];
  api.ui = { button: o => { seen.push(o.label); const b = e.d.createElement("button"); b.textContent = o.label; b.onclick = o.onclick; return b; } };
  api.pages.register({ id: "p", mode: "full", render() {} }).open();
  eq(seen, ["Back"]);
  const b = e.d.querySelector("[data-fy='a'] button");
  assert.strictEqual(b.dataset.fy, "a");
  b.click();
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
});

test("a second full page opens over the first, which stays open", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  const log = [];
  const h1 = a.pages.register({ id: "one", mode: "full", render() {}, onclose: () => log.push("one") });
  const h2 = b.pages.register({ id: "two", mode: "full", render() {}, onclose: () => log.push("two") });
  h1.open();
  h2.open();
  eq(log, []);
  assert.strictEqual(e.d.querySelectorAll("[data-fy='a']").length > 0, true);
  assert.strictEqual(e.d.querySelectorAll("[data-fy='b']").length > 0, true);
  eq(a.pages.list()[0].open, true);
  eq(b.pages.list()[0].open, true);
});

test("full page render error is contained and cleaned", () => {
  const e = make();
  const api = e.plugin("a");
  let closed = 0;
  api.pages.register({ id: "bad", mode: "full", render() { throw new Error("boom"); }, onclose: () => closed++ }).open();
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
  assert.strictEqual(closed, 1);
  assert.strictEqual(api.pages.list()[0].open, false);
});

test("throwing onclose does not break close", () => {
  const e = make();
  const api = e.plugin("a");
  const h = api.pages.register({ id: "p", mode: "full", render() {}, onclose() { throw new Error("x"); } });
  h.open();
  h.close();
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
  assert.strictEqual(h.open(), true);
  assert.strictEqual(h.remove(), true);
});

test("removing an open full page closes it", () => {
  const e = make();
  const api = e.plugin("a");
  let closed = 0;
  const h = api.pages.register({ id: "p", mode: "full", render() {}, onclose: () => closed++ });
  h.open();
  h.remove();
  assert.strictEqual(closed, 1);
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
});

test("api.pages.close closes this plugin's open pages only", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  a.pages.register({ id: "pa", mode: "full", render() {} }).open();
  b.pages.close();
  assert.strictEqual(a.pages.list()[0].open, true);
  a.pages.close();
  assert.strictEqual(a.pages.list()[0].open, false);
});

test("page ids are namespaced per plugin: the same id in two plugins does not collide", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  let closed = 0;
  a.pages.register({ id: "home", title: "A", render() {}, onclose: () => closed++ });
  const r = b.pages.register({ id: "home", title: "B", render() {} });
  assert.strictEqual(typeof r.open, "function");
  assert.strictEqual(closed, 0);
  eq(e.FY.panelPages().map(t => [t.id, t.name]), [["a:home", "A"], ["b:home", "B"]]);
  eq(b.pages.list().map(x => x.key), ["b:home"]);
  assert.strictEqual(b.pages.open("home"), true);
  eq(e.opened, ["b:home"]);
});

test("blank titles fall back to the id for the rail label", () => {
  const e = make();
  const a = e.plugin("a");
  a.pages.register({ id: "p1", title: "   ", render() {} });
  a.pages.register({ id: "p2", title: "", render() {} });
  eq(e.FY.panelPages().map(t => t.short), ["p1", "p2"]);
});

test("registering a panel page repaints only that page: the redraw names the changed page", () => {
  const e = make();
  const a = e.plugin("a");
  a.pages.register({ id: "home", title: "A", render() {} });
  eq(e.keys, ["a:home"]);
});

test("leaving a panel page through the rail closes it: onclose fires and open() works again", () => {
  const e = make();
  const a = e.plugin("a");
  let closed = 0;
  a.pages.register({ id: "home", title: "A", render() {}, onclose: () => closed++ });
  const tab = e.FY.panelPages()[0];
  tab.render(e.d.createElement("div"));
  assert.strictEqual(a.pages.list()[0].open, true);
  tab.leave();
  assert.strictEqual(closed, 1);
  assert.strictEqual(a.pages.list()[0].open, false);
  assert.strictEqual(a.pages.open("home"), true);
  tab.render(e.d.createElement("div"));
  assert.strictEqual(a.pages.open("home"), true);
});

test("full page is a dialog, takes focus, closes on Escape and gives focus back", () => {
  const e = make();
  const a = e.plugin("a");
  const opener = e.d.createElement("button");
  e.d.body.append(opener);
  opener.focus();
  let closed = 0;
  const h = a.pages.register({ id: "big", mode: "full", title: "Big", render() {}, onclose: () => closed++ });
  h.open();
  const layer = e.d.querySelector("[role=dialog]");
  assert.ok(layer && layer.getAttribute("aria-modal") === "true");
  assert.ok(layer.contains(e.d.activeElement));
  layer.dispatchEvent(new e.w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.strictEqual(closed, 1);
  assert.strictEqual(e.d.querySelector("[role=dialog]"), null);
  assert.strictEqual(e.d.activeElement, opener);
});

test("same plugin replaces after onclose", () => {
  const e = make();
  const a = e.plugin("a");
  const log = [];
  const first = a.pages.register({ id: "home", title: "One", mode: "full", render() {}, onclose: () => log.push("close1") });
  first.open();
  const second = a.pages.register({ id: "home", title: "Two", render() { log.push("render2"); } });
  eq(log, ["close1"]);
  assert.strictEqual(first.open(), false);
  assert.strictEqual(first.remove(), false);
  assert.strictEqual(a.pages.list().length, 1);
  assert.strictEqual(a.pages.list()[0].title, "Two");
  assert.strictEqual(a.pages.list()[0].mode, "panel");
  assert.strictEqual(second.open(), true);
});

test("page cap of 8 per plugin, replacing still allowed, removal frees a slot", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  const hs = [];
  for (let i = 0; i < 8; i++) hs.push(a.pages.register({ id: "p" + i, render() {} }));
  eq(a.pages.register({ id: "p8", render() {} }), { error: "page limit reached (8)" });
  assert.ok(a.pages.register({ id: "p0", render() {} }).open);
  assert.ok(b.pages.register({ id: "other", render() {} }).open);
  hs[1].remove();
  assert.ok(a.pages.register({ id: "p8", render() {} }).open);
  assert.strictEqual(a.pages.list().length, 8);
});

test("invalid page definitions are rejected", () => {
  const e = make();
  const a = e.plugin("a");
  const bad = [undefined, null, {}, { id: "x" }, { id: "", render() {} }, { id: "a b", render() {} }, { id: "x".repeat(41), render() {} }, { id: 5, render() {} }, { id: "ok", render: "no" }, { id: "ok", render() {}, mode: "window" }, { id: "a:b", render() {} }];
  bad.forEach(o => assert.strictEqual(typeof a.pages.register(o).error, "string", JSON.stringify(o)));
  eq(a.pages.list(), []);
  assert.strictEqual(e.d.body.querySelector("[data-fy]:not([data-fy=''])"), null);
});

test("hostile title is kept as text and truncated", () => {
  const e = make();
  const a = e.plugin("a");
  a.pages.register({ id: "p", title: "<img src=x onerror=alert(1)>".repeat(10), render() {} });
  assert.strictEqual(a.pages.list()[0].title.length, 80);
  assert.strictEqual(e.d.querySelector("img"), null);
});

test("dispose: closes full page, drops pages, mounts, zones; idempotent", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  const log = [];
  a.pages.register({ id: "full", mode: "full", render() {}, onclose: () => log.push("full") }).open();
  a.pages.register({ id: "side", render() {}, onclose: () => log.push("side") });
  a.global.mount("m", { zone: "bar", render(el) { el.textContent = "x"; } });
  a.global.mount("n", { zone: "corner", render() {} });
  b.global.mount("m", { zone: "corner", render() {} });
  b.pages.register({ id: "keep", render() {} });
  e.FY.dispose("a", TOK);
  eq(log, ["full"]);
  eq(e.FY.panelPages().map(t => t.id), ["b:keep"]);
  assert.strictEqual(e.d.querySelectorAll("[data-fy='a']").length, 0);
  assert.strictEqual(e.d.querySelector("[data-fy-zone='bar']"), null);
  assert.strictEqual(e.d.querySelectorAll("[data-fy-zone='corner'] [data-fy='b']").length, 1);
  assert.strictEqual(e.redraws, 2);
  e.FY.dispose("a", TOK);
  eq(log, ["full"]);
  eq(a.pages.register({ id: "late", render() {} }), { error: "module disposed" });
  eq(a.global.mount("late", { render() {} }), { error: "module disposed" });
});

test("a page id is free again after the owner is disposed", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  a.pages.register({ id: "home", render() {} });
  e.FY.dispose("a", TOK);
  assert.ok(b.pages.register({ id: "home", render() {} }).open);
});

test("re-registering the plugin disposes the old pages", () => {
  const e = make();
  const first = e.plugin("a");
  let closed = 0;
  first.pages.register({ id: "p", mode: "full", render() {}, onclose: () => closed++ }).open();
  const second = e.plugin("a");
  assert.strictEqual(closed, 1);
  assert.strictEqual(e.d.querySelector("[data-fy='a']"), null);
  assert.ok(second.pages.register({ id: "p", render() {} }).open);
  assert.strictEqual(second.pages.list().length, 1);
});

test("wipe: dispose all, remove data-fy, delete Framey leaves nothing", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  a.pages.register({ id: "full", mode: "full", render(box) { box.textContent = "x"; } }).open();
  a.global.mount("m", { zone: "overlay", render() {} });
  b.global.mount("m", { zone: "bar", render() {} });
  b.pages.register({ id: "p", render() {} });
  assert.ok(e.fy().length > 0);
  e.w.eval("window.Framey&&window.Framey.dispose&&window.Framey.dispose(undefined," + JSON.stringify(TOK) + ");" + RM + "delete window.Framey");
  assert.strictEqual(e.fy().length, 0);
  assert.strictEqual(e.w.Framey, undefined);
  eq(e.FY.panelPages(), []);
});

test("wipe without dispose: removed zones are recreated on the next mount", () => {
  const e = make();
  const a = e.plugin("a");
  a.global.mount("m", { zone: "corner", render() {} });
  e.w.eval(RM);
  assert.strictEqual(e.fy().length, 0);
  const m = a.global.mount("again", { zone: "corner", render(el) { el.textContent = "back"; } });
  assert.strictEqual(m.el.isConnected, true);
  assert.strictEqual(e.d.querySelectorAll("[data-fy-zone='corner']").length, 1);
  assert.strictEqual(m.unmount(), undefined);
  assert.strictEqual(e.d.querySelector("[data-fy-zone]"), null);
});

test("global mount: zones are fixed containers created on demand", () => {
  const e = make();
  const a = e.plugin("a");
  assert.strictEqual(e.d.querySelector("[data-fy-zone]"), null);
  const corner = a.global.mount("c", { render(el) { el.textContent = "C"; } });
  const overlay = a.global.mount("o", { zone: "overlay", render() {} });
  const bar = a.global.mount("b", { zone: "bar", render() {} });
  ["corner", "overlay", "bar"].forEach(z => {
    const el = e.d.querySelector("[data-fy-zone='" + z + "']");
    assert.ok(el, z);
    assert.strictEqual(el.style.position, "fixed");
    assert.strictEqual(el.dataset.fy, "core");
    assert.ok(Number(el.style.zIndex) < 99998);
  });
  assert.strictEqual(corner.el.dataset.fy, "a");
  assert.strictEqual(corner.el.dataset.fyMount, "c");
  assert.strictEqual(corner.el.textContent, "C");
  assert.strictEqual(overlay.el.style.pointerEvents, "auto");
  assert.strictEqual(e.d.querySelector("[data-fy-zone='overlay']").style.pointerEvents, "none");
  assert.strictEqual(bar.el.parentNode.dataset.fyZone, "bar");
  overlay.unmount();
  assert.strictEqual(e.d.querySelector("[data-fy-zone='overlay']"), null);
  bar.unmount();
  bar.unmount();
  assert.strictEqual(e.d.querySelector("[data-fy-zone='bar']"), null);
  assert.ok(e.d.querySelector("[data-fy-zone='corner']"));
});

test("global mount twice with the same id replaces", () => {
  const e = make();
  const a = e.plugin("a");
  const one = a.global.mount("m", { zone: "corner", render(el) { el.textContent = "one"; } });
  const two = a.global.mount("m", { zone: "bar", render(el) { el.textContent = "two"; } });
  assert.strictEqual(one.el.isConnected, false);
  assert.strictEqual(two.el.isConnected, true);
  assert.strictEqual(e.d.querySelector("[data-fy-zone='corner']"), null);
  assert.strictEqual(e.d.querySelectorAll("[data-fy='a']").length, 1);
  one.unmount();
  assert.strictEqual(two.el.isConnected, true);
  assert.strictEqual(a.global.mount("m", { render() {} }).el.isConnected, true);
  assert.strictEqual(e.d.querySelectorAll("[data-fy='a']").length, 1);
});

test("global mounts of different plugins with the same id coexist", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  a.global.mount("m", { render() {} });
  b.global.mount("m", { render() {} });
  assert.strictEqual(e.d.querySelectorAll("[data-fy-zone='corner'] > div").length, 2);
  e.FY.dispose("a", TOK);
  assert.strictEqual(e.d.querySelectorAll("[data-fy-zone='corner'] > div").length, 1);
});

test("global mount: caps, validation, render errors", () => {
  const e = make();
  const a = e.plugin("a");
  for (let i = 0; i < 8; i++) assert.ok(a.global.mount("m" + i, { render() {} }).el);
  eq(a.global.mount("m8", { render() {} }), { error: "mount limit reached (8)" });
  assert.ok(a.global.mount("m0", { render() {} }).el);
  eq(a.global.mount("x", { zone: "nowhere", render() {} }), { error: "unknown zone" });
  eq(a.global.mount("x", { zone: "constructor", render() {} }), { error: "unknown zone" });
  assert.strictEqual(typeof a.global.mount("", { render() {} }).error, "string");
  assert.strictEqual(typeof a.global.mount("x", {}).error, "string");
  assert.strictEqual(typeof a.global.mount("x").error, "string");
  assert.strictEqual(typeof a.global.mount(5, { render() {} }).error, "string");
  const b = e.plugin("b");
  const r = b.global.mount("boom", { zone: "bar", render() { throw new Error("nope"); } });
  eq(r, { error: "nope" });
  assert.strictEqual(e.d.querySelector("[data-fy-zone='bar']"), null);
  assert.strictEqual(e.d.querySelectorAll("[data-fy='b']").length, 0);
});

test("target api sends _core calls with the plugin id", () => {
  const e = make();
  const a = e.plugin("a");
  const t = a.target("bar");
  const hostile = "x'\"\n </style>";
  assert.ok(t.css("k", hostile) instanceof e.w.Promise);
  t.js("j", "run()", "stop()");
  t.js("j2", "run()");
  t.remove("k");
  a.target("main").remove();
  eq(e.calls_for("target_add"), [
    { pid: "a", target: "bar", kind: "css", key: "k", code: hostile },
    { pid: "a", target: "bar", kind: "js", key: "j", code: "run()", teardown: "stop()" },
    { pid: "a", target: "bar", kind: "js", key: "j2", code: "run()" },
  ]);
  eq(e.calls_for("target_remove"), [{ pid: "a", target: "bar", key: "k" }, { pid: "a", target: "main" }]);
  assert.ok(e.calls.every(c => c.plugin === "_core"));
});

test("target api resolves with the loader reply", async () => {
  const e = make();
  const a = e.plugin("a");
  const pending = a.target("main").css("k", "a{}");
  const c = e.calls[0];
  e.FY._reply(c.gen, c.id, { ok: true });
  eq(await pending, { ok: true });
});

test("target cleanup on dispose: one target_remove for the plugin, only when used", () => {
  const e = make();
  const a = e.plugin("a");
  const b = e.plugin("b");
  b.target("main").css("k", "a{}");
  e.calls.length = 0;
  e.FY.dispose("a", TOK);
  eq(e.calls_for("target_remove"), []);
  e.FY.dispose("b", TOK);
  eq(e.calls_for("target_remove"), [{ pid: "b" }]);
  e.FY.dispose("b", TOK);
  assert.strictEqual(e.calls_for("target_remove").length, 1);
});

test("target calls after dispose are refused locally", async () => {
  const e = make();
  const a = e.plugin("a");
  const t = a.target("main");
  e.FY.dispose("a", TOK);
  e.calls.length = 0;
  eq(await t.css("k", "a{}"), { error: "module disposed" });
  eq(await t.remove("k"), { error: "module disposed" });
  assert.strictEqual(e.calls.length, 0);
});

test("no timers or intervals are used by pages, mounts or targets", () => {
  const e = make();
  let n = 0;
  e.w.setTimeout = e.w.setInterval = e.w.requestAnimationFrame = () => { n++; return 0; };
  const a = e.plugin("a");
  a.pages.register({ id: "p", mode: "full", render() {} }).open();
  a.pages.register({ id: "q", render() {} });
  a.global.mount("m", { render() {} });
  a.target("main").css("k", "a{}");
  e.FY.dispose("a", TOK);
  assert.strictEqual(n, 0);
});

test("plugin code cannot replace FY.pages or FY.panelPages, and its own page stays listed", () => {
  const e = make();
  const pages = e.FY.pages;
  const panel = e.FY.panelPages;
  const api = e.plugin("a");
  api.pages.register({ id: "home", render() {} });
  e.w.eval("(function(F){F.pages={};F.panelPages=()=>[];try{Object.defineProperty(F,'panelPages',{value:()=>[]})}catch(x){}})(window.Framey)");
  assert.strictEqual(e.FY.pages, pages);
  assert.strictEqual(e.FY.panelPages, panel);
  eq(e.FY.panelPages().map(t => t.id), ["a:home"]);
});

test("plugin code cannot overwrite or remove another plugin's entry in FY.plugins", () => {
  const e = make();
  e.plugin("a");
  e.plugin("b");
  const [a, b] = e.FY.plugins;
  e.w.eval("(function(F){F.plugins[1]={id:'b',render(){}};F.plugins.length=1})(window.Framey)");
  assert.strictEqual(e.FY.plugins.length, 2);
  assert.strictEqual(e.FY.plugins[0], a);
  assert.strictEqual(e.FY.plugins[1], b);
});

test("a full page opened while the panel is open closes the panel and reopens it when the page closes", () => {
  const e = make();
  let open = true, toggles = 0;
  Object.defineProperty(e.FY, "panelOpen", { get: () => open, configurable: true });
  e.FY.toggle = () => { toggles++; open = !open; };
  const page = e.plugin("a").pages.register({ id: "full", mode: "full", render() {} });
  assert.ok(page.open());
  assert.equal(toggles, 1);
  assert.equal(open, false);
  e.d.querySelector('[role="dialog"]').dispatchEvent(new e.w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(e.d.querySelector('[role="dialog"]'), null);
  assert.equal(e.opened.length, 1);
});

test("full pages close in LIFO order: Escape leaves the top page and the one under it stays", () => {
  const e = make();
  const api = e.plugin("a");
  const under = api.pages.register({ id: "under", mode: "full", title: "Under", render() {} });
  const top = api.pages.register({ id: "top", mode: "full", title: "Top", render() {} });
  under.open();
  top.open();
  const layers = () => [...e.d.querySelectorAll('[role="dialog"]')].map(x => x.getAttribute("aria-label"));
  eq(layers(), ["Under", "Top"]);
  e.d.querySelectorAll('[role="dialog"]')[1].dispatchEvent(new e.w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  eq(layers(), ["Under"]);
  e.d.querySelector('[role="dialog"]').dispatchEvent(new e.w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  eq(layers(), []);
});

(async () => {
  let pass = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      pass++;
    } catch (err) {
      console.error("FAIL " + name + "\n" + (err && err.stack || err));
      process.exitCode = 1;
    }
  }
  console.log(pass + "/" + tests.length + " passed");
})();
