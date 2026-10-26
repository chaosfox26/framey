"use strict";
const assert = require("assert");
const path = require("path");
const { execFileSync } = require("child_process");
const { ROOT } = require("./harness");

const { JSDOM } = require(process.env.FRAMEY_JSDOM ? require.resolve("jsdom", { paths: [process.env.FRAMEY_JSDOM] }) : "jsdom");

const sleep = ms => new Promise(r => setTimeout(r, ms));
const dump = () =>
  JSON.parse(execFileSync(process.env.FRAMEY_PYTHON || "python", [path.join(ROOT, "tests", "fixtures", "j", "e2e_dump.py")], { encoding: "utf8", maxBuffer: 1 << 26 }));

const d = dump();
const TOKS = [d.core, ...Object.values(d.tokens)];
const url = "https://steamloopback.host/";

const CATALOG_SOURCE = { id: "stable", label: "Stable", url: "https://catalog.example/index.json" };
const CATALOG_ROW = { id: "demo", name: "Demo", version: "1.0.0", description: "Example module", category: "Tools", tags: [], source: { repository: "https://github.com/example/demo", commit: "a".repeat(40), path: "" }, homepage: "", compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] }, installed: false, linked: false, eligible: true, reason: "", identity: "b".repeat(64), channel: "stable", platform: ["linux-aarch64"], platform_declared: true, kind: "plugin" };

const DECKY_SOURCE = { id: "decky_stable", label: "Decky Store", url: "https://plugins.deckbrew.xyz/plugins", format: "decky" };
const DECKY_ROW = { id: "decky-1", name: "Example Decky plugin", version: "1.0.0", description: "Community plugin", category: "utilities", tags: [], author: "Example maintainer", format: "decky", read_only: true, kind: "decky", eligible: false, reason: "Needs a Framey port; ARM64 compatibility is unverified", architecture: "unknown", platform_declared: false, version_count: 1, identity: "c".repeat(64), compat: { state: "incompatible", badge: "port", msg: "Decky package format requires a Framey port", reasons: [] }, source: { repository: "", url: DECKY_SOURCE.url }, installed: false, linked: false };
const DECKY_VERSION = { name: "1.0.0", hash: "d".repeat(64), created: "", downloads: 1, updates: 0, identity: "e".repeat(64) };
const isDecky = arg => arg && ["decky_stable", "decky_testing"].includes(arg.source);

const CANNED = {
  list: () => [{ id: "fan", name: "Fan", short: "Fan", version: "1.0.0" }],
  store: () => [],
  catalog_sources: () => ({ source: CATALOG_SOURCE, sources: [{ id: "stable", label: "Stable" }, { id: "testing", label: "Testing" }, { id: "custom", label: "Custom" }], decky_sources: [DECKY_SOURCE, { id: "decky_testing", label: "Decky Store Testing", url: "https://testing.deckbrew.xyz/plugins", format: "decky" }], custom_url: "" }),
  catalog_list: arg => ({ rows: [isDecky(arg) ? DECKY_ROW : CATALOG_ROW], total: 1, categories: [isDecky(arg) ? "utilities" : "Tools"], source: isDecky(arg) ? DECKY_SOURCE : CATALOG_SOURCE, origin: "downloaded", stale: false, error: null, warning: "" }),
  catalog_details: arg => ({ entry: isDecky(arg) ? { ...DECKY_ROW, versions: [DECKY_VERSION] } : CATALOG_ROW, source: isDecky(arg) ? DECKY_SOURCE : CATALOG_SOURCE, origin: "downloaded", stale: false, error: null, warning: "" }),
  catalog_assess: () => ({ id: DECKY_ROW.id, format: "decky", read_only: true, version: DECKY_VERSION.name, hash: DECKY_VERSION.hash, assessment: { state: "port_required", label: "Needs a Framey port", architecture: "unknown", root_required: null, native_binaries: [], has_frontend: true, has_backend: true, flags: [], dependencies: [], reasons: ["Adapt Decky frontend APIs"], warnings: [], verified_sha256: true, bytes: 10, files: 2, not_executed: true } }),
  state_get: () => ({ order: [], hidden: [], prefs: { channel: "stable", autocheck: false, notify_loader: false, notify_plugins: false, dev: { enabled: false } } }),
  loader: () => ({ commit: "a".repeat(40), kept: false }),
  maint_get: () => ({ records: { fan: { label: "recent" } } }),
  files_list: () => ({ roots: [{ root: "data", label: "Data" }, { root: "home", label: "Home" }], entries: [] }),
};

const answer = m => {
  if (m.plugin === "fan" && m.method === "get") return { applied: {}, profiles: [], rpm: null, temp: null, zone: null, pwm: null };
  if (m.plugin === "_core" && CANNED[m.method]) return CANNED[m.method](m.arg);
  return {};
};

const reach = (v, toks) => {
  const seen = new Set();
  const go = (x, depth) => {
    if (typeof x === "string") return toks.some(t => x.includes(t));
    if (!x || (typeof x !== "object" && typeof x !== "function") || seen.has(x) || depth > 4) return false;
    seen.add(x);
    let keys = [];
    try { keys = Reflect.ownKeys(x); } catch (e) { return false; }
    return keys.some(k => {
      let y;
      try { y = x[k]; } catch (e) { return false; }
      return go(y, depth + 1);
    });
  };
  return go(v, 0);
};

const leaks = (w, toks) => {
  const hits = [];
  for (const k of Reflect.ownKeys(w)) {
    let v;
    try { v = w[k]; } catch (e) { continue; }
    if (reach(v, toks)) hits.push("window." + String(k));
  }
  if (toks.some(t => w.document.documentElement.outerHTML.includes(t))) hits.push("document");
  for (const name of ["sessionStorage", "localStorage"]) {
    let s = null;
    try { s = w[name]; } catch (e) { s = null; }
    if (!s) continue;
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (toks.some(t => k.includes(t) || String(s.getItem(k)).includes(t))) hits.push(name + "." + k);
    }
  }
  return hits;
};

const spyJSON = (w, S) => {
  const orig = w.JSON.stringify;
  w.__bindFramey = (...a) => {
    S.spyCalls++;
    if (reach(a, TOKS)) S.spyHits.push("__bindFramey");
  };
  w.JSON.stringify = function (v, ...rest) {
    if (reach(v, TOKS)) S.spyHits.push("JSON.stringify input");
    const s = orig.call(this, v, ...rest);
    if (TOKS.some(t => s.includes(t))) S.spyHits.push("JSON.stringify output");
    return s;
  };
};

const spyGlobals = (w, S) => {
  const f = w.Framey;
  const spy = {};
  for (const k of Reflect.ownKeys(f)) {
    const v = f[k];
    spy[k] = typeof v === "function" ? (...a) => {
      if (reach(a, TOKS)) S.spyHits.push("Framey." + String(k));
      return v.apply(f, a);
    } : v;
  }
  Object.defineProperty(w, "Framey", { value: spy, writable: false, configurable: true });
};

const boot = (spy = false) => {
  const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", { url, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  w.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; }
    observe(el) { queueMicrotask(() => this.cb([{ isIntersecting: true, target: el }])); }
    unobserve() {}
    disconnect() {}
  };
  const sent = [];
  const S = { core: null, failures: [], spyHits: [], spyCalls: 0 };
  const objs = new Map();
  let seq = 0;
  const wrap = v => {
    if (v !== null && (typeof v === "object" || typeof v === "function")) {
      const objectId = "obj" + ++seq;
      objs.set(objectId, v);
      return { objectId };
    }
    return { value: v };
  };
  const cdp = (method, params) => {
    try {
      if (method === "Runtime.evaluate") return { result: wrap(w.eval(params.expression)) };
      const self = params.objectId ? objs.get(params.objectId) : undefined;
      const args = (params.arguments || []).map(a => ("objectId" in a ? objs.get(a.objectId) : a.value));
      return { result: wrap(w.eval("(" + params.functionDeclaration + ")").apply(self, args)) };
    } catch (e) {
      return { exceptionDetails: { exception: { description: String((e && e.message) || e) } } };
    }
  };
  w.fyCall = (function (s) {
    const m = JSON.parse(s);
    sent.push(m);
    if (m.gen !== undefined) queueMicrotask(() => cdp("Runtime.callFunctionOn", { objectId: S.core, functionDeclaration: d.consts.REPLY, arguments: [{ value: m.gen }, { value: m.id }, { value: answer(m) }] }));
  }).bind(null);
  if (spy) spyJSON(w, S);
  const panel = cdp("Runtime.evaluate", { expression: d.main[0].expr });
  if (panel.exceptionDetails) S.failures.push(["#core", panel.exceptionDetails]);
  S.core = panel.result && panel.result.objectId;
  if (spy) spyGlobals(w, S);
  for (const e of d.main.slice(1)) {
    if (e.expr !== undefined) {
      const r = cdp("Runtime.evaluate", { expression: e.expr });
      if (r.exceptionDetails) S.failures.push([e.id, r.exceptionDetails]);
      continue;
    }
    if (!S.core) break;
    const src = cdp("Runtime.evaluate", { expression: "(function(Framey){" + e.src + "\n})" });
    if (src.exceptionDetails) {
      S.failures.push([e.id, src.exceptionDetails]);
      continue;
    }
    const bound = cdp("Runtime.callFunctionOn", { objectId: S.core, functionDeclaration: d.consts.BIND, arguments: [{ value: e.meta.id }, { value: e.tok }, { value: e.meta }] });
    if (bound.exceptionDetails) {
      S.failures.push([e.id, bound.exceptionDetails]);
      continue;
    }
    const ran = cdp("Runtime.callFunctionOn", { objectId: src.result.objectId, functionDeclaration: d.consts.RUN, arguments: [{ objectId: bound.result.objectId }] });
    if (ran.exceptionDetails) S.failures.push([e.id, ran.exceptionDetails]);
  }
  const deliver = ev => cdp(ev.method, { objectId: S.core, functionDeclaration: ev.functionDeclaration, arguments: ev.arguments });
  return { w, sent, S, deliver, cdp, core: S.core };
};

const rail = w => w.document.querySelector("[role=tablist]");
const tileNamed = (w, text) => [...rail(w).children].find(x => x.textContent.trim() === text);
const body = w => w.document.body.textContent;

const results = [];
const check = async (name, fn) => {
  try {
    await fn();
    results.push(["ok", name]);
  } catch (e) {
    results.push(["FAIL", name + ": " + ((e && e.message) || e)]);
  }
};

(async () => {
  const { w, sent, S, deliver, cdp } = boot(false);

  await check("main expression: the whole panel is one body and returns the core handle", async () => {
    assert.ok(S.core, "the panel did not return a core handle: " + JSON.stringify(S.failures));
    assert.ok(w.Framey && typeof w.Framey.open === "function", "window.Framey is not defined after the panel expression");
  });

  await check("generated panel module errors log a fixed message without exposing the failure and later modules initialize", async () => {
    const secret = "PANEL_INIT_FIXTURE_SECRET_MUST_NOT_BE_LOGGED";
    const script = [
      "import asyncio,json,pathlib,sys",
      "sys.path.insert(0,sys.argv[1])",
      "import jharness as H",
      "loader=H.Loader()",
      "try:",
      "    parts=json.loads(sys.argv[2])",
      "    parts[1]=(loader.root/'inject.js').read_text()",
      "    print(loader.fy.panel_js(parts))",
      "finally:",
      "    asyncio.run(loader.close())",
    ].join("\n");
    const parts = [
      "window.__moduleOrder=['before'];",
      "",
      "window.__moduleOrder.push('failing');throw new Error(" + JSON.stringify(secret) + ")",
      "window.__moduleOrder.push('after');window.__laterModuleInitialized=true;",
    ];
    const expression = execFileSync(process.env.FRAMEY_PYTHON || "python", ["-c", script, path.join(ROOT, "tests", "fixtures", "j"), JSON.stringify(parts)], { encoding: "utf8", maxBuffer: 1 << 26 });
    const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", { url, runScripts: "outside-only", pretendToBeVisual: true });
    const logged = [];
    let held;
    try {
      dom.window.fyCall = (function () {}).bind(null);
      dom.window.console.error = (...args) => logged.push(args);
      held = dom.window.eval(expression);
      assert.ok(held && typeof held.dispose === "function", "the generated expression did not return its core handle");
      assert.deepStrictEqual(Array.from(dom.window.__moduleOrder), ["before", "failing", "after"]);
      assert.strictEqual(dom.window.__laterModuleInitialized, true, "a failed module prevented the next module from initializing");
      assert.deepStrictEqual(logged, [["[Framey] Panel module initialization failed"]]);
      assert.ok(!JSON.stringify(logged).includes(secret), "the module exception detail was logged");
    } finally {
      if (held && typeof held.dispose === "function") held.dispose();
      dom.window.close();
    }
  });

  await check("plugin load: fan and beta register and every loader step runs without throwing", async () => {
    assert.deepStrictEqual(S.failures, [], "a loader step threw");
    assert.ok(w.Framey.plugins.find(p => p.id === "fan"), "fan did not register");
    assert.ok(w.Framey.plugins.find(p => p.id === "beta"), "beta did not register");
  });

  await check("the panel opens and the rail shows Modules, Store, Decky Store, Settings in order", async () => {
    w.Framey.toggle();
    await sleep(60);
    const labels = [...rail(w).children].map(x => x.textContent.trim());
    const at = ["Modules", "Store", "Decky Store", "Settings"].map(n => labels.indexOf(n));
    assert.ok(at.every(i => i >= 0) && at[0] < at[1] && at[1] < at[2] && at[2] < at[3], "rail was " + JSON.stringify(labels));
  });

  await check("Modules renders its list from the canned list reply and the maintenance chip", async () => {
    tileNamed(w, "Modules").click();
    await sleep(80);
    assert.ok(body(w).includes("Fan"), "module row missing");
    assert.ok(body(w).includes("Recent activity"), "maintenance chip missing");
    assert.ok(!body(w).includes("Modules is not available"), "modules page not built");
    assert.ok(sent.some(m => m.plugin === "_core" && m.method === "list"), "list was not requested");
  });

  await check("Settings renders its page", async () => {
    tileNamed(w, "Settings").click();
    await sleep(80);
    assert.ok(body(w).includes("Check for updates"), "settings page not built");
    assert.ok(sent.some(m => m.plugin === "_core" && m.method === "loader"), "loader was not requested");
  });

  await check("Store renders its catalog through the core bridge", async () => {
    tileNamed(w, "Store").click();
    await sleep(40);
    assert.ok(body(w).includes("Demo"), "catalog entry missing");
    assert.ok(body(w).includes("Search"), "catalog search missing");
    assert.ok(sent.some(m => m.plugin === "_core" && m.method === "catalog_list"), "catalog_list was not requested");
  });

  await check("Decky Store delegates to the upstream community page without changing the Framey catalog", async () => {
    tileNamed(w, "Decky Store").click();
    await sleep(60);
    assert.ok(body(w).includes("Example Decky plugin"), "Decky community entry missing");
    assert.ok(body(w).includes("Framey is independent") && body(w).includes("not affiliated with or endorsed by Decky Loader"), "community ownership notice missing");
    assert.ok(body(w).includes("Experimental compatibility; results vary"), "experimental compatibility notice missing");
    assert.ok(body(w).includes("only browses and inspects original packages; it cannot install or run them"), "browse-only capability notice missing");
    assert.ok(body(w).includes("Report Framey issues to Framey, not Decky or plugin authors"), "Framey-owned support guidance missing");
    assert.ok(body(w).includes("Not compatible") && body(w).includes("needs a Framey version before it can run here"), "port requirement missing");
    assert.ok(body(w).includes("ARM64: Not checked"), "unverified architecture was not stated");
    assert.ok(sent.some(m => m.plugin === "_core" && m.method === "catalog_list" && isDecky(m.arg)), "Decky source was not requested");
    assert.ok(!sent.some(m => m.method === "catalog_sources" && isDecky(m.arg)), "browsing Decky changed the saved Framey source");
    const details = [...w.document.querySelectorAll('[role="button"]')].find(x => x.getAttribute("aria-label") === "Details: Example Decky plugin");
    assert.ok(details, "Decky details control missing");
    details.click();
    await sleep(50);
    assert.ok(body(w).includes("By Example maintainer"), "upstream author attribution missing");
    assert.ok(body(w).includes("Decky plugins cannot be installed in Framey"), "read-only port explanation missing");
    assert.ok([...w.document.querySelectorAll('[role="button"]')].some(x => x.getAttribute("aria-label") === "Inspect compatibility"), "explicit archive inspection is unavailable");
    assert.ok(![...w.document.querySelectorAll('[role="button"]')].some(x => /^(Install|Review installation)$/.test(x.getAttribute("aria-label") || "")), "Decky package offered direct installation");
    assert.ok(!sent.some(m => m.method === "catalog_assess"), "opening details automatically inspected an archive");
    [...w.document.querySelectorAll('[role="button"]')].find(x => x.getAttribute("aria-label") === "Inspect compatibility").click();
    await sleep(40);
    const inspections = sent.filter(m => m.method === "catalog_assess");
    assert.equal(inspections.length, 1, "an explicit inspection must issue one request");
    assert.equal(inspections[0].t, d.core, "inspection did not use the held core capability");
    assert.deepStrictEqual(inspections[0].arg, { id: DECKY_ROW.id, identity: DECKY_ROW.identity, version_identity: DECKY_VERSION.identity, confirm: true, source: "decky_stable" });
    assert.ok(body(w).includes("Archive SHA-256 verified."), "verified fixture assessment was not rendered");
    assert.ok(body(w).includes("ARM64: Not checked"), "unknown architecture became a compatibility claim");
    assert.ok(!sent.some(m => m.method === "catalog_install"), "read-only Decky flow installed a package");
    tileNamed(w, "Store").click();
    await sleep(50);
    assert.ok(body(w).includes("Demo"), "Framey catalog did not return");
    assert.ok(!body(w).includes("Example Decky plugin"), "Decky content remained in the Framey Store");
  });

  await check("the picker opens through a bound plugin api and lists the roots", async () => {
    w.__betaApi.pickFile({ root: "home", title: "Pick" });
    await sleep(80);
    assert.ok(body(w).includes("Data") && body(w).includes("Home"), "root list missing");
    assert.ok(sent.some(m => m.method === "files_list"), "files_list was not sent");
  });

  await check("the legacy frame-fan plugin backend call carries its own token", async () => {
    tileNamed(w, "Fan").click();
    await sleep(80);
    const gets = sent.filter(m => m.plugin === "fan" && m.method === "get");
    assert.ok(gets.length > 0, "fan backend was not called");
    assert.ok(gets.every(m => m.t === d.tokens.fan), "fan call used a different token");
  });

  await check("a second plugin cannot act as the first: beta calls carry only beta's token", async () => {
    const fromBeta = sent.filter(m => m.plugin === "beta");
    assert.ok(fromBeta.length > 0, "beta sent nothing");
    assert.ok(fromBeta.every(m => m.t === d.tokens.beta), "beta used another token");
    assert.ok(!sent.some(m => m.plugin === "beta" && m.t === d.tokens.fan), "beta used fan's token");
  });

  await check("update_result from the loader reaches the core listeners", async () => {
    let got = null;
    w.Framey._k(d.core).listen("update_result", p => { got = p; });
    deliver(d.events[0]);
    assert.equal(JSON.stringify(got), JSON.stringify({ ok: true, name: "Fan" }));
  });

  await check("a loader event reaches the plugin that owns it", async () => {
    deliver(d.events[1]);
    assert.deepStrictEqual(w.__ping, { n: 7 });
  });

  await check("bounded callback diagnostics remain behind the held core handle without issuing requests or changing the page", async () => {
    assert.equal(w.Framey.diagnostic, undefined);
    assert.equal(w.Framey.diagnostics, undefined);
    assert.equal(w.__betaApi.diagnostic, undefined);
    assert.equal(w.__betaApi.diagnostics, undefined);
    const before = { requests: sent.length, markup: w.document.body.innerHTML };
    const result = cdp("Runtime.callFunctionOn", {
      objectId: S.core,
      functionDeclaration: "function(){this.diagnostic('unknown.PRIVATE_DETAIL');this.diagnostic('core.event_failed',{message:'PRIVATE_DETAIL'});return JSON.stringify(this.diagnostics())}",
    });
    assert.ok(!result.exceptionDetails, "held diagnostic operation threw");
    const rows = JSON.parse(result.result.value);
    assert.ok(rows.length <= 32);
    assert.ok(rows.every(row => Object.keys(row).sort().join() === "code,count" && Number.isInteger(row.count) && row.count > 0 && row.count <= 9999));
    assert.ok(rows.some(row => row.code === "core.event_failed"));
    assert.ok(!result.result.value.includes("PRIVATE_DETAIL"));
    assert.equal(sent.length, before.requests, "recording a callback counter sent a request");
    assert.equal(w.document.body.innerHTML, before.markup, "recording a callback counter changed the page");
  });

  await check("no core or plugin token is in any window property, the document or storage", async () => {
    assert.deepStrictEqual(leaks(w, TOKS), []);
  });

  await check("a plugin cannot reach the core handle, a bind function, or any token through its own code", async () => {
    assert.ok(!S.failures.some(([who]) => who === "evil" || who === "snoop"), "evil or snoop threw");
    assert.ok(w.__leak === undefined || w.__leak === w, "the breakout reached a non-global this");
    assert.equal(typeof w.__bindFramey, "undefined", "window.__bindFramey exists");
    assert.ok(Array.isArray(w.__snoop) && w.__snoop.length === 1, "snoop did not see the bind arguments");
    assert.deepStrictEqual(leaks(w, TOKS), []);
  });

  await check("uninstall disposes only that plugin, through the held core handle", async () => {
    const u = boot(false);
    const beta = u.w.Framey.plugins.find(p => p.id === "beta");
    u.cdp("Runtime.callFunctionOn", { objectId: u.core, functionDeclaration: d.consts.DISPOSE, arguments: [{ value: "beta" }] });
    assert.equal(u.w.__betaGone, 1, "beta was not disposed");
    assert.equal(beta.dead, true, "beta module is still live");
    assert.ok(u.w.Framey.plugins.find(p => p.id === "fan"), "fan was disposed with beta");
  });

  await check("WIPE after a held dispose-all runs plugin cleanup and removes every node and global", async () => {
    const x = boot(false);
    x.cdp("Runtime.callFunctionOn", { objectId: x.core, functionDeclaration: d.consts.DISPOSE, arguments: [{ value: null }] });
    assert.equal(x.cdp("Runtime.evaluate", { expression: d.consts.WIPE }).exceptionDetails, undefined, "WIPE threw");
    assert.equal(x.w.__betaGone, 1, "beta cleanup did not run once");
    assert.equal(x.w.document.querySelectorAll("[data-fy]").length, 0, "data-fy nodes left behind");
    assert.equal(typeof x.w.Framey, "undefined", "window.Framey still defined");
  });

  await check("page spies on JSON.stringify, window.Framey and __bindFramey see no token during injection, plugin load, events, uninstall or WIPE", async () => {
    const spied = boot(true);
    spied.deliver(d.events[0]);
    spied.deliver(d.events[1]);
    spied.cdp("Runtime.callFunctionOn", { objectId: spied.core, functionDeclaration: d.consts.DISPOSE, arguments: [{ value: "beta" }] });
    spied.cdp("Runtime.callFunctionOn", { objectId: spied.core, functionDeclaration: d.consts.DISPOSE, arguments: [{ value: null }] });
    spied.cdp("Runtime.evaluate", { expression: d.consts.WIPE });
    assert.deepStrictEqual(spied.S.failures, [], "a loader step threw under the spies");
    assert.deepStrictEqual(spied.S.spyHits, []);
    assert.equal(spied.S.spyCalls, 0, "__bindFramey was called");
  });

  await check("the bar expressions mount the button and badge", async () => {
    const dom = new JSDOM("<!doctype html><html><head></head><body><div id=\"bar\"><img class=\"avatar\" src=\"data:image/gif;base64,R0lGODlhAQABAAAAACw=\"><span></span><span></span></div></body></html>", { url, runScripts: "outside-only", pretendToBeVisual: true });
    const bw = dom.window;
    bw.fyCall = (function () {}).bind(null);
    bw.Element.prototype.getBoundingClientRect = function () {
      const width = this.tagName === "IMG" ? 50 : 300;
      return { left: 0, top: 0, right: width, bottom: 40, width, height: 40 };
    };
    for (const b of d.bar) bw.eval(b.expr);
    const btn = bw.document.querySelector(".fy-bb");
    assert.ok(btn, "bar button not mounted");
    bw.FYBar.badge(2);
    assert.equal(bw.document.querySelectorAll(".fy-bdg").length, 1);
    assert.deepStrictEqual(leaks(bw, TOKS), []);
  });

  let bad = 0;
  for (const [status, msg] of results) {
    if (status !== "ok") bad++;
    console.log(status + " " + msg);
  }
  console.log((results.length - bad) + "/" + results.length + " e2e loader expression checks");
  process.exit(bad ? 1 : 0);
})();
