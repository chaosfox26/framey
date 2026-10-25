"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT, TOK } = require("./harness");

const tests = [];
const t = (name, fn) => tests.push([name, fn]);
const fresh = options => makeDom(Object.assign({ loadFiles: ["fy-api.js"] }, options));
const flush = () => new Promise(r => setImmediate(r));
const wait = ms => new Promise(r => setTimeout(r, ms));
const json = v => JSON.parse(JSON.stringify(v));
const shown = e => e.document.querySelectorAll('[role="status"]');

t("dispose is idempotent and marks the plugin dead", () => {
  const e = fresh();
  const api = e.plugin("a");
  let n = 0;
  api.cleanup(() => n++);
  e.window.Framey.dispose("a", TOK);
  e.window.Framey.dispose("a", TOK);
  e.window.Framey.dispose(undefined, TOK);
  assert.strictEqual(n, 1);
  assert.strictEqual(e.record("a").dead, true);
});

const withDispose = (e, id, dispose) => e.window.__bindFramey(id, "tok-" + id + "-0000", { name: id, short: id }).register({ render() {}, dispose });

t("cleanup runs LIFO across every kind of registration, after the plugin's own dispose", () => {
  const e = fresh();
  const order = [];
  const api = withDispose(e, "a", () => order.push("own"));
  api.cleanup(() => order.push(1));
  api.onVisible(() => {});
  api.cleanup(() => order.push(2));
  const target = e.document.createElement("div");
  target.addEventListener = (type, fn) => {};
  target.removeEventListener = () => order.push("listener");
  api.on(target, "click", () => {});
  api.cleanup(() => order.push(3));
  e.window.Framey.dispose("a", TOK);
  assert.deepStrictEqual(order, ["own", 3, 2, 1, "listener"]);
});

t("a throwing disposer does not stop the others", () => {
  const e = fresh();
  const ran = [];
  const api = withDispose(e, "a", () => { throw new Error("own boom"); });
  api.cleanup(() => ran.push(1));
  api.cleanup(() => { throw new Error("boom"); });
  api.cleanup(() => ran.push(3));
  e.window.Framey.dispose("a", TOK);
  assert.deepStrictEqual(ran, [3, 1]);
  assert.strictEqual(e.record("a").dead, true);
});

t("pagehide disposes every plugin once", () => {
  const e = fresh();
  const a = e.plugin("a");
  const b = e.plugin("b");
  const ran = [];
  a.cleanup(() => ran.push("a"));
  b.cleanup(() => ran.push("b"));
  a.notify({ title: "x" });
  e.window.dispatchEvent(new e.window.Event("pagehide"));
  e.window.dispatchEvent(new e.window.Event("pagehide"));
  assert.deepStrictEqual(ran.sort(), ["a", "b"]);
  assert.strictEqual(shown(e).length, 0);
});

t("pagehide registration is single and idle cost is zero", () => {
  const seen = { window: [], document: [] };
  const e = fresh({
    before: w => {
      const wrap = (obj, key) => {
        const add = obj.addEventListener.bind(obj);
        obj.addEventListener = (type, ...r) => { seen[key].push(type); return add(type, ...r); };
      };
      wrap(w, "window");
      wrap(w.document, "document");
    },
  });
  e.load("fy-api.js");
  e.plugin("a");
  assert.deepStrictEqual(seen.window.filter(x => x === "pagehide"), ["pagehide"]);
  assert.deepStrictEqual(seen.document.filter(x => x === "visibilitychange"), []);
  assert.strictEqual(e.clock.pending(), 0);
});

t("reinjection replaces the pagehide handler: exactly one stays registered after N reinjections", () => {
  const live = new Set();
  const e = fresh({
    before: w => {
      const add = w.addEventListener.bind(w), rem = w.removeEventListener.bind(w);
      w.addEventListener = (type, fn, ...r) => { if (type === "pagehide") live.add(fn); return add(type, fn, ...r); };
      w.removeEventListener = (type, fn, ...r) => { if (type === "pagehide") live.delete(fn); return rem(type, fn, ...r); };
    },
  });
  e.load("fy-api.js");
  assert.strictEqual(live.size, 1);
  for (let i = 0; i < 3; i++) {
    e.window.eval("Framey.dispose(undefined, '" + TOK + "'); delete window.Framey");
    e.load("inject.js");
    e.load("fy-api.js");
    assert.strictEqual(live.size, 1);
  }
  let n = 0;
  e.plugin("a").cleanup(() => n++);
  e.window.dispatchEvent(new e.window.Event("pagehide"));
  assert.strictEqual(n, 1);
});

t("events: on, off, once, order, dropped info, bad names", () => {
  const e = fresh();
  const api = e.plugin("a");
  const F = e.window.Framey;
  const got = [];
  const off1 = api.events.on("tick", (p, i) => got.push(["1", p, i.name, i.dropped]));
  api.events.on("tick", p => got.push(["2", p]));
  api.events.once("tick", p => got.push(["once", p]));
  e.event("a", "tick", { n: 1 }, 3);
  e.event("a", "tick", { n: 2 }, 0);
  assert.deepStrictEqual(json(got), [
    ["1", { n: 1 }, "tick", 3], ["2", { n: 1 }], ["once", { n: 1 }],
    ["1", { n: 2 }, "tick", 0], ["2", { n: 2 }],
  ]);
  off1();
  got.length = 0;
  e.event("a", "tick", 3);
  assert.deepStrictEqual(json(got), [["2", 3]]);
  const h = () => got.push("h");
  api.events.on("x", h);
  api.events.on("x", h);
  api.events.off("x", h);
  got.length = 0;
  e.event("a", "x", null);
  assert.strictEqual(got.length, 1);
  api.events.on("Bad Name", () => got.push("bad"));
  api.events.on("", () => got.push("bad"));
  api.events.on("y", "not a function");
  e.event("a", "Bad Name", 1);
  e.event("a", "y", 1);
  e.event("nobody", "tick", 1);
  e.event("a", 5, 1);
  assert.strictEqual(got.length, 1);
});

t("events: unsubscribing inside a handler takes effect immediately", () => {
  const e = fresh();
  const api = e.plugin("a");
  const got = [];
  let offB;
  api.events.on("e", () => { got.push("a"); offB(); });
  offB = api.events.on("e", () => got.push("b"));
  e.event("a", "e", 1);
  e.event("a", "e", 1);
  assert.deepStrictEqual(got, ["a", "a"]);
});

t("events after dispose are ignored; subscriptions after dispose are ignored", () => {
  const e = fresh();
  const api = e.plugin("a");
  const got = [];
  api.events.on("e", () => got.push("old"));
  e.window.Framey.dispose("a", TOK);
  e.event("a", "e", 1);
  api.events.on("e", () => got.push("late"));
  api.events.once("e", () => got.push("late-once"));
  e.event("a", "e", 1);
  assert.deepStrictEqual(got, []);
  const again = e.plugin("a");
  again.events.on("e", () => got.push("new"));
  e.event("a", "e", 1);
  assert.deepStrictEqual(got, ["new"]);
});

t("a throwing subscriber is caught, logged, and rate limited to 5 per second", () => {
  const e = fresh();
  const api = e.plugin("a");
  let now = 1000;
  e.window.Date.now = () => now;
  const got = [];
  api.events.on("e", () => { throw new Error("bad handler"); });
  api.events.on("e", () => got.push(1));
  for (let i = 0; i < 20; i++) e.event("a", "e", i);
  assert.strictEqual(got.length, 20);
  let logs = e.sent("log");
  assert.strictEqual(logs.length, 5);
  assert.strictEqual(logs[0].id, 0);
  assert.strictEqual(logs[0].plugin, "_core");
  assert.strictEqual(logs[0].arg.pid, "a");
  assert.strictEqual(logs[0].arg.level, "error");
  assert.ok(/bad handler/.test(logs[0].arg.msg));
  now += 1000;
  e.event("a", "e", 0);
  assert.strictEqual(e.sent("log").length, 6);
});

t("events: subscription cap is 64", () => {
  const e = fresh();
  const api = e.plugin("a");
  for (let i = 0; i < 64; i++) api.events.on("e", () => {});
  assert.throws(() => api.events.on("e", () => {}), { name: "RangeError" });
});

t("core events: core dispatcher gets them, plugin listeners only get events addressed to them, notice becomes a notification", () => {
  const e = fresh();
  const F = e.window.Framey;
  const got = [];
  const off = e.window.__S.K.listen("progress", (p, i) => got.push([json(p), i.dropped]));
  const api = e.plugin("a");
  const mine = [];
  api.events.on("progress", p => mine.push(json(p)));
  assert.strictEqual("listen" in F, false);
  e.event("_core", "progress", { stage: "download" }, 2);
  assert.deepStrictEqual(got, [[{ stage: "download" }, 2]]);
  assert.deepStrictEqual(mine, []);
  e.event("a", "progress", { mine: 1 }, 0);
  assert.deepStrictEqual(mine, [{ mine: 1 }]);
  assert.strictEqual(got.length, 1);
  off();
  e.event("_core", "progress", {});
  assert.strictEqual(got.length, 1);
  e.event("_core", "notice", { title: "Update", body: "available", actions: [{ id: "open:settings", label: "Open Settings" }] });
  assert.strictEqual(shown(e).length, 1);
  assert.strictEqual(shown(e)[0].dataset.fy, "_core");
});

t("notify: text only, never markup", () => {
  const e = fresh();
  const api = e.plugin("a");
  const evil = '<img src=x onerror="window.pwned=1"><b>bold</b>';
  api.notify({ title: evil, body: evil, icon: "<i>", actions: [{ id: "go", label: evil }] });
  const box = shown(e)[0];
  assert.strictEqual(box.querySelector("img"), null);
  assert.strictEqual(box.querySelector("b"), null);
  assert.strictEqual(box.querySelector("i"), null);
  assert.ok(box.textContent.includes(evil));
  assert.strictEqual(e.window.pwned, undefined);
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML|document\.write|\beval\(\s*[a-z]/.test(fs.readFileSync(path.join(ROOT, "fy-api.js"), "utf8").replace("(0, eval)(text)", "")));
});

t("notify: container ownership, role, truncation, three actions", () => {
  const e = fresh();
  const api = e.plugin("a");
  api.notify({
    title: "t".repeat(200), body: "b".repeat(1000), icon: "123456789012",
    actions: [{ id: "1", label: "one" }, { id: "2", label: "two" }, { id: "3", label: "three" }, { id: "4", label: "four" }, { id: "", label: "bad" }],
  });
  const box = shown(e)[0];
  assert.strictEqual(box.dataset.fy, "a");
  assert.strictEqual(box.getAttribute("role"), "status");
  const texts = [...box.querySelectorAll("span,div")].map(x => x.textContent);
  assert.ok(texts.includes("[a] " + "t".repeat(80)));
  assert.ok(texts.includes("b".repeat(400)));
  assert.ok(texts.includes("12345678"));
  const labels = [...box.querySelectorAll("button")].map(b => b.textContent);
  assert.deepStrictEqual(labels, ["one", "two", "three", "Dismiss"]);
  assert.deepStrictEqual(json(api.notify({})), { id: 0 });
  assert.strictEqual(shown(e).length, 1);
});

t("notify: one plugin gets 2 visible slots and 3 queued, oldest of its own queue dropped, queue promotes", () => {
  const e = fresh();
  const api = e.plugin("a");
  const closed = [];
  const hs = [];
  for (let i = 1; i <= 14; i++) hs.push(api.notify({ title: "n" + i, duration: 0, onclose: r => closed.push([i, r]) }));
  assert.strictEqual(shown(e).length, 2);
  assert.deepStrictEqual(closed.map(c => c[0]), [3, 4, 5, 6, 7, 8, 9, 10, 11]);
  assert.ok(closed.every(c => c[1] === "dropped"));
  assert.deepStrictEqual(e.window.Framey.diag().notifications, 5);
  hs[0].dismiss();
  assert.strictEqual(shown(e).length, 2);
  assert.ok([...shown(e)].some(x => x.textContent.includes("n12")));
  assert.ok(![...shown(e)].some(x => x.textContent.includes("n1" + "1")));
  hs[0].dismiss();
  assert.deepStrictEqual(closed.filter(c => c[0] === 1), [[1, "dismiss"]]);
});

t("notify: plugins cannot fill the last visible slot and cannot starve core notices", () => {
  const e = fresh();
  const F = e.window.Framey;
  const a = e.plugin("a");
  const b = e.plugin("b");
  const c = e.plugin("c");
  for (let i = 0; i < 6; i++) {
    a.notify({ title: "a" + i, duration: 0 });
    b.notify({ title: "b" + i, duration: 0 });
    c.notify({ title: "c" + i, duration: 0 });
  }
  assert.strictEqual(shown(e).length, 3);
  F.notify({ title: "core update", duration: 0 });
  assert.strictEqual(shown(e).length, 4);
  assert.ok([...shown(e)].some(x => x.textContent.includes("core update")));
  F.notify({ title: "core two", duration: 0 });
  assert.strictEqual(shown(e).length, 4);
  const q = F.diag().notifications;
  assert.ok(q <= 4 + 8 + 1);
});

t("notify: Dismiss and actions are focusable buttons and route", () => {
  const e = fresh();
  const api = e.plugin("a");
  const opened = [];
  e.window.Framey.open = id => opened.push(id);
  const got = [];
  api.notify({
    title: "x", duration: 0,
    actions: [{ id: "open:modules", label: "Modules" }, { id: "mine", label: "Mine" }],
    onaction: id => got.push(["action", id]),
    onclose: r => got.push(["close", r]),
  });
  const buttons = [...shown(e)[0].querySelectorAll("button")];
  assert.strictEqual(buttons.length, 3);
  buttons.forEach(b => {
    assert.strictEqual(b.tabIndex, 0);
    assert.strictEqual(b.type, "button");
    assert.strictEqual(b.disabled, false);
    b.focus();
    assert.strictEqual(e.document.activeElement, b);
  });
  buttons[0].click();
  assert.deepStrictEqual(opened, ["#modules"]);
  assert.deepStrictEqual(got, [["action", "open:modules"], ["close", "action"]]);
  assert.strictEqual(shown(e).length, 0);
  got.length = 0;
  api.notify({ title: "y", duration: 0, actions: [{ id: "mine", label: "Mine" }], onaction: id => got.push(id), onclose: r => got.push(r) });
  shown(e)[0].querySelectorAll("button")[0].click();
  assert.deepStrictEqual(got, ["mine", "action"]);
  api.notify({ title: "z", duration: 0, onclose: r => got.push(r) });
  shown(e)[0].querySelectorAll("button")[0].click();
  assert.deepStrictEqual(got.slice(2), ["dismiss"]);
  assert.deepStrictEqual(opened, ["#modules"]);
});

t("notify: duration default, sticky, clamp, no timer left behind", () => {
  const e = fresh();
  const api = e.plugin("a");
  const closed = [];
  api.notify({ title: "default", onclose: r => closed.push(r) });
  e.clock.tick(3999);
  assert.strictEqual(shown(e).length, 1);
  e.clock.tick(1);
  assert.strictEqual(shown(e).length, 0);
  assert.deepStrictEqual(closed, ["timeout"]);
  const sticky = api.notify({ title: "sticky", duration: 0 });
  assert.strictEqual(e.clock.pending(), 0);
  e.clock.tick(10 * 60 * 1000);
  assert.strictEqual(shown(e).length, 1);
  sticky.dismiss();
  api.notify({ title: "long", duration: 999999 });
  e.clock.tick(59999);
  assert.strictEqual(shown(e).length, 1);
  e.clock.tick(1);
  assert.strictEqual(shown(e).length, 0);
  api.notify({ title: "neg", duration: -5 });
  e.clock.tick(4000);
  assert.strictEqual(shown(e).length, 0);
  const h = api.notify({ title: "x", duration: 5000 });
  h.dismiss();
  assert.strictEqual(e.clock.pending(), 0);
  assert.strictEqual(e.document.querySelectorAll('div[data-fy="core"]').length, 0);
});

t("notify: dispose removes notifications, queue and timers without callbacks; later notify is ignored", () => {
  const e = fresh();
  const api = e.plugin("a");
  const other = e.plugin("b");
  const closed = [];
  for (let i = 0; i < 8; i++) api.notify({ title: "a" + i, onclose: r => closed.push(r) });
  closed.length = 0;
  other.notify({ title: "b-queued", duration: 0 });
  e.window.Framey.dispose("a", TOK);
  assert.strictEqual(closed.length, 0);
  assert.strictEqual(e.document.querySelectorAll('[data-fy="a"]').length, 0);
  assert.strictEqual(shown(e).length, 1);
  assert.strictEqual(shown(e)[0].dataset.fy, "b");
  assert.strictEqual(e.clock.pending(), 0);
  assert.deepStrictEqual(json(api.notify({ title: "late" })), { id: 0 });
  assert.strictEqual(shown(e).length, 1);
  assert.strictEqual(e.window.Framey.diag().notifications, 1);
});

t("visibility truth table", () => {
  const e = fresh();
  const api = e.plugin("a");
  const F = e.window.Framey;
  let state = "visible";
  Object.defineProperty(e.document, "visibilityState", { configurable: true, get: () => state });
  const rows = [
    [false, "visible", false], [false, "hidden", false], [false, "prerender", false],
    [true, "visible", true], [true, "hidden", false], [true, "prerender", false],
  ];
  rows.forEach(([panel, doc, want]) => {
    F._panel(panel);
    state = doc;
    assert.strictEqual(api.visible, want, panel + "/" + doc);
  });
  assert.strictEqual(F.panelOpen, true);
});

t("onVisible fires only on change and removes its document listener", () => {
  const added = [];
  const removed = [];
  const e = fresh({
    before: w => {
      const add = w.document.addEventListener.bind(w.document);
      const rem = w.document.removeEventListener.bind(w.document);
      w.document.addEventListener = (type, ...r) => { added.push(type); return add(type, ...r); };
      w.document.removeEventListener = (type, ...r) => { removed.push(type); return rem(type, ...r); };
    },
  });
  const api = e.plugin("a");
  const F = e.window.Framey;
  let state = "visible";
  Object.defineProperty(e.document, "visibilityState", { configurable: true, get: () => state });
  const seen = [];
  const off1 = api.onVisible(v => seen.push(v));
  const off2 = api.onVisible(v => { throw new Error("subscriber failure"); });
  assert.deepStrictEqual(added, ["visibilitychange"]);
  F._panel(true);
  F._panel(true);
  state = "hidden";
  e.document.dispatchEvent(new e.window.Event("visibilitychange"));
  e.document.dispatchEvent(new e.window.Event("visibilitychange"));
  state = "visible";
  e.document.dispatchEvent(new e.window.Event("visibilitychange"));
  F._panel(false);
  assert.deepStrictEqual(seen, [true, false, true, false]);
  assert.ok(e.sent("log").some(c => /subscriber failure/.test(c.arg.msg)));
  off1();
  off1();
  assert.deepStrictEqual(removed, []);
  off2();
  assert.deepStrictEqual(removed, ["visibilitychange"]);
  assert.deepStrictEqual(added, ["visibilitychange"]);
  api.onVisible(() => {});
  e.window.Framey.dispose("a", TOK);
  assert.deepStrictEqual(removed, ["visibilitychange", "visibilitychange"]);
  assert.strictEqual(api.visible, false);
  assert.strictEqual(typeof api.onVisible(() => {}), "function");
  assert.strictEqual(added.length, 2);
});

t("onPanel reports changes only", () => {
  const e = fresh();
  const F = e.window.Framey;
  const seen = [];
  const off = F.onPanel(v => seen.push(v));
  F._panel(true);
  F._panel(true);
  F._panel(false);
  off();
  F._panel(true);
  assert.deepStrictEqual(seen, [true, false]);
});

t("i18n fallback chain: exact, base, plugin default, en, key", () => {
  const e = fresh();
  const I = e.window.Framey.i18n;
  const api = e.plugin("p", { i18n: { default: "de" } });
  I.setLang("pt-BR");
  assert.strictEqual(I.lang(), "pt-BR");
  I.add("p", "pt-BR", { a: "exact" });
  I.add("p", "pt", { a: "base", b: "base-b" });
  I.add("p", "de", { a: "de-a", b: "de-b", c: "de-c" });
  I.add("p", "en", { a: "en-a", b: "en-b", c: "en-c", d: "en-d" });
  assert.strictEqual(api.t("a"), "exact");
  assert.strictEqual(api.t("b"), "base-b");
  assert.strictEqual(api.t("c"), "de-c");
  assert.strictEqual(api.t("d"), "en-d");
  assert.strictEqual(api.t("zzz"), "zzz");
  assert.strictEqual(api.lang, "pt-BR");
  I.setLang("PT-br");
  assert.strictEqual(api.t("a"), "exact");
  assert.strictEqual(I.setLang("not a lang!"), false);
  assert.strictEqual(I.lang(), "PT-br");
  assert.strictEqual(I.setLang(""), true);
  assert.ok(/^[a-z]{2,3}(-[A-Za-z0-9]+)?$/i.test(I.lang()));
  const noDefault = e.plugin("q");
  I.setLang("fr");
  I.add("q", "en", { k: "q-en" });
  assert.strictEqual(noDefault.t("k"), "q-en");
});

t("i18n: runtime language comes from FY_RUNTIME, setLang wins, core strings merge", () => {
  const e = fresh({ before: w => { w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "es-MX" }; } });
  const I = e.window.Framey.i18n;
  assert.strictEqual(I.lang(), "es-MX");
  I.setLang("it");
  assert.strictEqual(I.lang(), "it");
  assert.strictEqual(I.t("core", "notify.dismiss"), "Dismiss");
  assert.strictEqual(I.add("core", "en", { "notify.dismiss": "Keep it" }), 0);
  assert.strictEqual(I.t("core", "notify.dismiss"), "Dismiss");
  assert.strictEqual(Object.isFrozen(I), true);
});

t("i18n plural and interpolation", () => {
  const e = fresh();
  const I = e.window.Framey.i18n;
  const api = e.plugin("p");
  I.add("p", "en", { items_one: "{count} item", items_other: "{count} items", items_zero: "no items", only_other: "{count} things", plain: "Hello {name}, {missing}" });
  I.setLang("en");
  assert.strictEqual(api.t("items", { count: 1 }), "1 item");
  assert.strictEqual(api.t("items", { count: 5 }), "5 items");
  assert.strictEqual(api.t("items", { count: 0 }), "no items");
  assert.strictEqual(api.t("only_other", { count: 1 }), "1 things");
  assert.strictEqual(api.t("plain", { name: "Ana" }), "Hello Ana, {missing}");
  assert.strictEqual(api.t("plain", { name: "{name}" }), "Hello {name}, {missing}");
  assert.strictEqual(api.t("plain", { constructor: 1, toString: 2 }), "Hello {name}, {missing}");
  I.add("p", "en", { nz_one: "{count} one", nz_other: "{count} many" });
  assert.strictEqual(api.t("nz", { count: 0 }), "0 many");
  I.add("p", "pl", { it_one: "{count} rzecz", it_few: "{count} rzeczy", it_many: "{count} rzeczy (many)", it_other: "{count} rzeczy (other)" });
  I.setLang("pl");
  assert.strictEqual(api.t("it", { count: 1 }), "1 rzecz");
  assert.strictEqual(api.t("it", { count: 3 }), "3 rzeczy");
  assert.strictEqual(api.t("it", { count: 5 }), "5 rzeczy (many)");
  assert.strictEqual(api.t("it", { count: 22 }), "22 rzeczy");
  assert.strictEqual(api.t("items", { count: 2 }), "2 items");
  assert.strictEqual(api.t("items", { count: "2" }), "items");
  I.add("p", "pl", { only_en_has_one: "pl-plain" });
  assert.strictEqual(api.t("only_en_has_one", { count: 1 }), "pl-plain");
});

t("i18n: hostile keys and bounds", () => {
  const e = fresh();
  const I = e.window.Framey.i18n;
  const api = e.plugin("p");
  assert.strictEqual(I.add("p", "en", JSON.parse('{"__proto__":"x","constructor":"c","ok":"fine","n":5}')), 3);
  assert.strictEqual(api.t("ok"), "fine");
  assert.strictEqual(api.t("toString"), "toString");
  assert.strictEqual(I.add("p", "bad lang", { a: "b" }), 0);
  const big = {};
  for (let i = 0; i < 2500; i++) big["k" + i] = "v";
  I.add("p", "fr", big);
  I.setLang("fr");
  assert.strictEqual(api.t("k1999"), "v");
  assert.strictEqual(api.t("k2000"), "k2000");
  for (let i = 0; i < 20; i++) I.add("p", "x" + String.fromCharCode(97 + i) + "z", { a: "b" });
  assert.strictEqual(I.add("p", "xtz", { a: "c" }) >= 0, true);
  assert.strictEqual(I.add("p", "yyy", { a: "z" }), 0);
  assert.strictEqual(api.t("big", { count: 1 }), "big");
  I.add("p", "en", { long: "x".repeat(5000) });
  assert.strictEqual(I.t("p", "long").length, 1024);
});

t("api.i18n.load goes through _core locale and falls back quietly", async () => {
  const e = fresh();
  const api = e.plugin("p");
  const ok = api.i18n.load("fr");
  const call = e.sent("locale")[0];
  assert.deepStrictEqual(json(call.arg), { id: "p", lang: "fr" });
  assert.strictEqual(call.plugin, "_core");
  e.answer(call, { strings: { hello: "Bonjour" } });
  assert.strictEqual(await ok, true);
  e.window.Framey.i18n.setLang("fr");
  assert.strictEqual(api.t("hello"), "Bonjour");
  const bad = api.i18n.load("de");
  e.answer(e.sent("locale")[1], { error: "no such file" });
  assert.strictEqual(await bad, false);
  assert.strictEqual(await api.i18n.load("../../etc"), false);
  assert.strictEqual(e.sent("locale").length, 2);
  assert.strictEqual(api.i18n.add("de", { x: "y" }), 1);
});

t("only English ships", () => {
  const found = [];
  const walk = dir => {
    fs.readdirSync(dir, { withFileTypes: true }).forEach(d => {
      if (d.name === "node_modules" || d.name === ".git") return;
      const full = path.join(dir, d.name);
      if (d.isDirectory()) return walk(full);
      if (/^fy-strings-/.test(d.name) && d.name !== "fy-strings-en.js") found.push(full);
      if (path.basename(path.dirname(full)) === "locales" && /\.json$/.test(d.name) && d.name !== "en.json") found.push(full);
    });
  };
  walk(ROOT);
  assert.deepStrictEqual(found, []);
});

t("runtime: shape, read-only copy, no environment", () => {
  const e = fresh({ before: w => { w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", token: "secret", env: { A: 1 } }; } });
  const api = e.plugin("fan", { version: "2.3.4", secret: "s3" });
  const r = api.runtime;
  assert.deepStrictEqual(json(r), { framey: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", plugin: "fan", version: "2.3.4" });
  assert.strictEqual(Object.isFrozen(r), true);
  assert.notStrictEqual(api.runtime, r);
  assert.ok(!/secret|s3|env|token/.test(JSON.stringify(r)));
  assert.throws(() => { "use strict"; r.api = 2; });
  assert.strictEqual(api.runtime.api, 1);
  const bare = fresh();
  const rb = bare.plugin("x").runtime;
  assert.deepStrictEqual(json(rb), { framey: "", api: 1, platform: "", lang: rb.lang, plugin: "x", version: "1.0.0" });
});

t("api.call, api.fetch, api.resource, api.revoke use _core with pid", async () => {
  const e = fresh();
  const api = e.plugin("a");
  const c = api.call("set", { v: 1 });
  const first = e.calls[0];
  assert.strictEqual(first.plugin, "a");
  assert.strictEqual(first.method, "set");
  e.answer(first, { ok: 1 });
  assert.deepStrictEqual(json(await c), { ok: 1 });
  const f = api.fetch("https://example.org/x", { method: "GET", pid: "spoof" });
  const fc = e.sent("fetch")[0];
  assert.strictEqual(fc.plugin, "_core");
  assert.deepStrictEqual(json(fc.arg), { method: "GET", pid: "a", url: "https://example.org/x" });
  e.answer(fc, { status: 200, text: "hi" });
  assert.strictEqual((await f).text, "hi");
  const f2 = api.fetch("http://127.0.0.1/");
  e.answer(e.sent("fetch")[1], { error: "That address is not allowed", code: "blocked_address" });
  await assert.rejects(f2, err => err.code === "blocked_address" && /not allowed/.test(err.message));
  const r = api.resource("home", "x.png", 60);
  const rc = e.sent("resource")[0];
  assert.deepStrictEqual(json(rc.arg), { root: "home", path: "x.png", ttl: 60, pid: "a" });
  e.answer(rc, { url: "http://127.0.0.1:1/m/abc", kind: "http", expires: 1, token: "tok1" });
  const url = await r;
  assert.strictEqual(url, "http://127.0.0.1:1/m/abc");
  assert.strictEqual(await api.revoke("data:nothing"), false);
  const rv = api.revoke(url);
  const rvc = e.sent("resource")[1];
  assert.deepStrictEqual(json(rvc.arg), { revoke: "tok1", pid: "a" });
  e.answer(rvc, { ok: true });
  assert.strictEqual(await rv, true);
  assert.strictEqual(await api.revoke(url), false);
  const bad = api.resource("home", "y");
  e.answer(e.sent("resource")[2], {});
  await assert.rejects(bad);
});

t("live resource tokens are revoked on dispose, fire and forget", async () => {
  const e = fresh();
  const api = e.plugin("a");
  const r = api.resource("home", "x.mp4");
  e.answer(e.sent("resource")[0], { url: "http://127.0.0.1:1/m/t", token: "tokX" });
  await r;
  e.window.Framey.dispose("a", TOK);
  const last = e.sent("resource")[1];
  assert.deepStrictEqual(json(last), { id: 0, plugin: "_core", method: "resource", arg: { revoke: "tokX", pid: "a" }, t: "tok-a-0000" });
});

t("api.log: levels, truncation, 5 per second, fire and forget", () => {
  const e = fresh();
  const api = e.plugin("a");
  let now = 5000;
  e.window.Date.now = () => now;
  for (let i = 0; i < 9; i++) api.log(i === 0 ? "warning" : "nonsense", "m".repeat(5000));
  const logs = e.sent("log");
  assert.strictEqual(logs.length, 5);
  assert.strictEqual(logs[0].arg.level, "warning");
  assert.strictEqual(logs[1].arg.level, "info");
  assert.strictEqual(logs[0].arg.msg.length, 2048);
  assert.ok(logs.every(c => c.id === 0));
  now += 999;
  api.log("info", "still limited");
  assert.strictEqual(e.sent("log").length, 5);
  now += 1;
  api.log("debug", "ok");
  assert.strictEqual(e.sent("log").length, 6);
});

t("cleanup: remover, immediate run after dispose, bad input, cap", () => {
  const e = fresh();
  const api = e.plugin("a");
  let n = 0;
  const rm = api.cleanup(() => n++);
  rm();
  rm();
  assert.strictEqual(api.cleanup("x")(), undefined);
  const same = () => n++;
  api.cleanup(same);
  api.cleanup(same);
  e.window.Framey.dispose("a", TOK);
  assert.strictEqual(n, 2);
  api.cleanup(() => n++);
  assert.strictEqual(n, 3);
  const b = e.plugin("b");
  for (let i = 0; i < 256; i++) b.cleanup(() => {});
  assert.throws(() => b.cleanup(() => {}), { name: "RangeError" });
  const off = b.cleanup;
  e.window.Framey.dispose("b", TOK);
  assert.strictEqual(typeof off, "function");
});

t("timers and listeners registered after dispose are refused; raf is tracked", async () => {
  const e = fresh();
  const api = e.plugin("a");
  let frames = 0;
  const h = api.raf(() => frames++);
  assert.ok(h > 0);
  await wait(60);
  assert.strictEqual(frames, 1);
  api.raf(() => frames++);
  e.window.Framey.dispose("a", TOK);
  await wait(60);
  assert.strictEqual(frames, 1);
  assert.strictEqual(api.raf(() => frames++), 0);
  assert.strictEqual(api.timeout(() => {}, 10), 0);
  assert.strictEqual(api.interval(() => {}, 10), 0);
  assert.strictEqual(e.clock.pending(), 0);
});

t("diag shape and counts", async () => {
  const e = fresh();
  const api = e.plugin("a");
  const F = e.window.Framey;
  api.timeout(() => {}, 100);
  api.interval(() => {}, 100);
  api.on(e.document, "click", () => {});
  api.events.on("e", () => {});
  api.events.on("f", () => {});
  api.notify({ title: "n", duration: 0 });
  api.events.on("progress", () => {});
  const el = e.document.createElement("div");
  el.dataset.fy = "a";
  e.document.body.append(el);
  api.call("x");
  const d = F.diag();
  assert.deepStrictEqual(Object.keys(d).sort(), ["calls", "heap", "notifications", "plugins", "subs", "untrusted_clicks"]);
  assert.deepStrictEqual(json(d.plugins), [{
    id: "a", timers: 1, intervals: 1, listeners: 1, subscriptions: 3, pages: 0, globals: 0, targets: 0, notifications: 1, nodes: 2,
  }]);
  assert.strictEqual(d.calls, 1);
  assert.strictEqual(d.notifications, 1);
  assert.strictEqual(d.subs, 3);
  assert.ok(d.heap === null || typeof d.heap === "number");
  e.answer(e.sent("x")[0], 1);
  await flush();
  assert.strictEqual(F.diag().calls, 0);
  api._diag = () => ({ pages: 2, globals: 1, targets: 3 });
  const x = F.diag().plugins[0];
  assert.deepStrictEqual([x.pages, x.globals, x.targets], [2, 1, 3]);
  F.dispose("a", TOK);
  assert.deepStrictEqual(json(F.diag().plugins), []);
  assert.strictEqual(F.diag().notifications, 0);
});

t("lazy: built-in assets resolve at once, unknown names reject, the loader is never asked", async () => {
  const e = fresh();
  const F = e.window.Framey;
  await assert.rejects(F.lazy("../x"), /unknown asset/);
  await assert.rejects(F.lazy("constructor"), /unknown asset/);
  await F.lazy("picker");
  await F.lazy("dev");
  assert.strictEqual(e.calls.length, 0);
});

t("existing inject.js behaviour is kept", async () => {
  const e = fresh();
  const F = e.window.Framey;
  const api = e.plugin("a");
  F.toast("hello", 1000);
  assert.ok(e.document.body.textContent.includes("hello"));
  const p = api.call("m");
  const c = e.calls[e.calls.length - 1];
  F.dispose("a", TOK);
  F._reply(c.gen, c.id, "late");
  let settled = false;
  p.then(() => { settled = true; });
  await flush();
  assert.strictEqual(settled, false);
  const again = e.plugin("a");
  const p2 = again.call("m");
  e.answer(e.calls[e.calls.length - 1], "fresh");
  assert.strictEqual(await p2, "fresh");
  e.load("fy-api.js");
  assert.strictEqual(F.apiReady, true);
});

t("types/framey.d.ts members exist on a registered api and on Framey", async () => {
  const src = fs.readFileSync(path.join(ROOT, "types", "framey.d.ts"), "utf8");
  assert.strictEqual((src.match(/\{/g) || []).length, (src.match(/\}/g) || []).length);
  assert.ok(!/\/\/|\/\*/.test(src));
  const iface = name => {
    const m = src.match(new RegExp("interface " + name + " \\{\\r?\\n([\\s\\S]*?)\\r?\\n\\}\\r?\\n"));
    assert.ok(m, "interface " + name);
    return m[1].split(/\r?\n/).map(l => /^  (readonly )?([A-Za-z_]\w*)(\?)?(\()?/.exec(l)).filter(Boolean)
      .map(m2 => ({ name: m2[2], fn: !!m2[4], ro: !!m2[1], opt: !!m2[3] }));
  };
  const e = fresh({ loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js"], before: w => { w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en" }; } });
  const api = e.plugin("a");
  const F = e.window.Framey;
  const check = (label, obj, members) => {
    assert.ok(members.length > 0, label);
    members.forEach(m => {
      if (m.opt && !(m.name in obj)) return;
      assert.ok(m.name in obj, label + "." + m.name);
      if (m.fn) assert.strictEqual(typeof obj[m.name], "function", label + "." + m.name);
    });
  };
  const apiMembers = iface("FrameyApi");
  check("api", api, apiMembers);
  check("api.events", api.events, iface("FrameyEvents"));
  check("api.i18n", api.i18n, iface("FrameyApiI18n"));
  check("Framey", F, iface("FrameyGlobal"));
  check("Framey.i18n", F.i18n, iface("FrameyI18n"));
  check("runtime", api.runtime, iface("FrameyRuntime"));
  const declared = new Set(apiMembers.map(m => m.name));
  Object.keys(api).filter(k => k !== "_clear" && k !== "_diag" && k !== "_n").forEach(k => assert.ok(declared.has(k), "undeclared api." + k));
  const typeOf = { id: "string", visible: "boolean", lang: "string", runtime: "object", events: "object", i18n: "object", ui: "object", pages: "object", global: "object" };
  apiMembers.filter(m => !m.fn).forEach(m => assert.strictEqual(typeof api[m.name], typeOf[m.name], m.name));
  assert.deepStrictEqual(Object.keys(api.runtime).sort(), iface("FrameyRuntime").map(m => m.name).sort());
  const diagKeys = Object.keys(F.diag());
  assert.deepStrictEqual(diagKeys.sort(), iface("FrameyDiag").map(m => m.name).sort());
  F.notify({ title: "x" });
  const n = api.notify({ title: "y" });
  assert.deepStrictEqual(Object.keys(n).sort(), iface("FrameyNotification").map(m => m.name).sort());
  api.timeout(() => {}, 1);
  api.interval(() => {}, 1);
  api.cleanup(() => {});
  api.onVisible(() => {});
  api.t("k");
  api.log("info", "m");
  api.call("m");
  api.raf(() => {});
  api.events.on("e", () => {});
  api.i18n.add("en", {});
  F.onPanel(() => {})();
  api.events.on("e", () => {})();
  F._panel(true);
  e.event("a", "e", 1);
  F.dispose(undefined, TOK);
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
      console.log("FAIL " + name + "\n     " + String(err && err.stack || err).split("\n").slice(0, 6).join("\n     "));
    }
  }
  console.log("\n" + pass + " passed, " + failed.length + " failed, " + tests.length + " total");
  process.exit(failed.length ? 1 : 0);
};

main();
