const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT } = require("./harness");

const flush = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 5; i++) await flush(); };

const boot = (options = {}) => {
  const env = makeDom({ loadFiles: ["fy-api.js"], before: w => { if (options.trusted) w.FY_RUNTIME = { trusted_input: "require" }; if (options.throwCall) w.fyCall = () => { throw new Error("link down"); }; } });
  const spy = { timers: 0, listeners: 0 };
  const w = env.window;
  ["setTimeout", "setInterval", "requestAnimationFrame"].forEach(k => {
    const f = w[k];
    w[k] = (...a) => { spy.timers++; return f.apply(w, a); };
  });
  [env.document, w].forEach(t => {
    const f = t.addEventListener.bind(t);
    t.addEventListener = (...a) => { spy.listeners++; return f(...a); };
  });
  env.load("fy-dev.js");
  const box = env.document.createElement("div");
  env.document.body.append(box);
  const answer = (method, value) => {
    const c = env.calls.find(x => x.method === method && !x.done);
    assert.ok(c, "no pending call " + method);
    c.done = true;
    env.answer(c, value);
  };
  const pending = method => env.calls.filter(x => x.method === method && !x.done).length;
  const buttons = () => [...box.querySelectorAll("button")];
  const button = text => buttons().find(b => b.textContent === text);
  const copied = [];
  Object.defineProperty(w.navigator, "clipboard", { configurable: true, value: { writeText: s => { copied.push(s); return Promise.resolve(); } } });
  return { env, w, d: env.document, FY: w.Framey, box, answer, pending, buttons, button, spy, copied };
};

const STATUS = (extra = {}) => Object.assign({ enabled: true, hot: [], exposure: { state: "closed", addresses: [] } }, extra);
const LIST = [{ id: "alpha", name: "Alpha" }, { id: "beta", name: "<b>Beta</b>" }];

const open = async (status = STATUS(), list = LIST) => {
  const h = boot();
  h.FY.dev.render(h.box);
  h.answer("dev_status", status);
  h.answer("list", list);
  await settle();
  return h;
};

let pass = 0, fail = 0;
const queue = [];
const t = (name, fn) => queue.push([name, fn]);

t("load: idempotent, exposes render and text, adds no listener or timer", async () => {
  const h = boot();
  assert.equal(typeof h.FY.dev.render, "function");
  assert.equal(typeof h.FY.dev.text, "function");
  const first = h.FY.dev;
  h.env.load("fy-dev.js");
  assert.strictEqual(h.FY.dev, first);
  assert.equal(h.FY.devReady, true);
  assert.equal(h.spy.timers, 0);
  assert.equal(h.spy.listeners, 0);
});

t("render asks for status and the module list, and draws module rows", async () => {
  const h = await open(STATUS({ hot: [{ id: "alpha", mode: "inotify", remaining_s: 1750, truncated: false }] }));
  assert.equal(h.env.calls.filter(c => c.plugin === "_core").map(c => c.method).slice(0, 2).sort().join(), "dev_status,list");
  const text = h.box.textContent;
  assert.ok(text.includes("Developer"));
  assert.ok(text.includes("Alpha  (inotify, 30 min left)"), text);
  assert.ok(text.includes("<b>Beta</b>"));
  assert.equal(h.box.querySelector("b"), null);
  const on = h.buttons().filter(b => b.textContent === "On");
  const off = h.buttons().filter(b => b.textContent === "Off");
  assert.equal(on.length, 1);
  assert.equal(off.length, 1);
  assert.equal(h.spy.timers, 0);
});

t("exposure banner: closed, loopback, exposed (red, with addresses), unknown", async () => {
  const cases = [
    ["closed", [], "Debug port is closed", false],
    ["loopback", ["127.0.0.1:8080"], "this device only", false],
    ["exposed", ["0.0.0.0:8080", "[::]:8080"], "open to other devices", true],
    ["unknown", [], "could not be checked", false],
  ];
  for (const [state, addresses, expect, red] of cases) {
    const h = await open(STATUS({ exposure: { state, addresses } }));
    const text = h.box.textContent;
    assert.ok(text.includes(expect), state + ": " + text);
    const row = [...h.box.querySelectorAll("div")].find(e => /border/.test(e.getAttribute("style") || ""));
    assert.ok(row, state);
    assert.ok(row.textContent.startsWith("Debug port"), state);
    assert.equal(/255, 122, 122|ff7a7a/i.test(row.getAttribute("style")), red, state);
    if (state === "exposed") {
      assert.ok(text.includes("0.0.0.0:8080, [::]:8080"));
      assert.ok(text.includes("Anyone who can reach this device"));
      assert.ok(text.includes("Framey does not open or close this port"));
    }
  }
  const h = await open(STATUS({ exposure: { state: "weird" } }));
  assert.ok(!h.box.textContent.includes("Debug port is"));
  const none = await open({ enabled: true, hot: [] });
  assert.ok(none.box.textContent.includes("Hot reload"));
});

t("hot reload switches send dev_set and refresh", async () => {
  const h = await open(STATUS({ hot: [{ id: "alpha", mode: "mtime", remaining_s: 60, truncated: false }] }));
  h.button("Off").click();
  const set = h.env.sent("dev_set");
  assert.equal(set.length, 1);
  assert.deepEqual(set[0].arg, { id: "beta", hot: true });
  assert.equal(set[0].plugin, "_core");
  h.answer("dev_set", STATUS());
  await settle();
  assert.equal(h.pending("dev_status"), 1);
  assert.equal(h.pending("list"), 1);
  h.answer("dev_status", STATUS({ hot: [{ id: "beta", mode: "inotify", remaining_s: 3600, truncated: false }] }));
  h.answer("list", LIST);
  await settle();
  assert.ok(h.box.textContent.includes("<b>Beta</b>  (inotify, 60 min left)"));
  h.button("On").click();
  assert.deepEqual(h.env.sent("dev_set")[1].arg, { id: "beta", hot: false });
});

t("trusted_input require: synthetic clicks on the developer buttons send nothing", async () => {
  const h = boot({ trusted: true });
  h.FY.dev.render(h.box);
  h.answer("dev_status", STATUS({ hot: [] }));
  h.answer("list", LIST);
  await settle();
  h.button("Off").click();
  h.button("Find debug targets").click();
  h.button("Run diagnostics").click();
  await settle();
  assert.equal(h.env.sent("dev_set").length, 0);
  assert.equal(h.env.sent("devtools_info").length, 0);
  assert.equal(h.env.sent("diag").length, 0);
  assert.equal(h.FY.diag().untrusted_clicks, 3);
});

t("a refused switch shows a toast and still refreshes", async () => {
  const h = await open();
  h.buttons().find(b => b.textContent === "Off").click();
  h.answer("dev_set", { error: "Developer mode is off", code: "dev_off" });
  await settle();
  assert.ok(h.d.body.textContent.includes("Failed: Developer mode is off"));
  assert.equal(h.pending("dev_status"), 1);
});

t("developer mode off: message and no switches", async () => {
  const h = await open(STATUS({ enabled: false }));
  assert.ok(h.box.textContent.includes("Developer mode is off."));
  assert.equal(h.buttons().filter(b => b.textContent === "On" || b.textContent === "Off").length, 0);
});

t("status failure is shown as text", async () => {
  const h = await open({ error: "no status" });
  assert.ok(h.box.textContent.includes("Failed: no status"));
});

t("a rejected call is handled", async () => {
  const h = boot({ throwCall: true });
  h.FY.dev.render(h.box);
  await settle();
  assert.ok(h.box.textContent.includes("Failed: link down"));
  const out = await h.FY.dev.text();
  assert.ok(out.includes("link down"));
});

t("debug targets: lists names, urls and inspector addresses, copy works", async () => {
  const h = await open();
  h.button("Find debug targets").click();
  assert.equal(h.env.sent("devtools_info").length, 1);
  const inspector = "http://127.0.0.1:8080/devtools/inspector.html?ws=127.0.0.1:8080/devtools/page/AAA1";
  h.answer("devtools_info", {
    available: true, port: 8080, error: null,
    hint: "From a PC run: ssh -L 8080:127.0.0.1:8080 steamos@<headset>, then open it.",
    targets: [{ name: "main", title: "Steam <i>main</i>", url: "https://steamloopback.host/x", inspector }, { name: "", title: "", url: "u2", inspector: inspector + "2" }],
  });
  await settle();
  const text = h.box.textContent;
  assert.ok(text.includes("main: Steam <i>main</i>"));
  assert.ok(text.includes("other"));
  assert.ok(text.includes(inspector));
  assert.ok(text.includes("ssh -L 8080:127.0.0.1:8080 steamos@<headset>"));
  assert.equal(h.box.querySelector("i"), null);
  const copies = h.buttons().filter(b => b.textContent === "Copy address");
  assert.equal(copies.length, 2);
  copies[0].click();
  assert.deepEqual(h.copied, [inspector]);
});

t("debug targets: closed port shows the error and the tunnel hint", async () => {
  const h = await open();
  h.button("Find debug targets").click();
  h.answer("devtools_info", { available: false, port: 8080, targets: [], error: "Nothing is listening on port 8080.", hint: "From a PC run: ssh -L 8080:127.0.0.1:8080 steamos@<headset>" });
  await settle();
  assert.ok(h.box.textContent.includes("Nothing is listening on port 8080."));
  assert.ok(h.box.textContent.includes("ssh -L"));
  assert.equal(h.buttons().filter(b => b.textContent === "Copy address").length, 0);
  h.button("Find debug targets").click();
  h.answer("devtools_info", { available: true, port: 8080, targets: [], error: null, hint: "" });
  await settle();
  assert.ok(h.box.textContent.includes("lists no targets"));
  assert.ok(!h.box.textContent.includes("Nothing is listening"));
});

t("diagnostics: merges the loader snapshot with the page counts", async () => {
  const h = await open();
  h.env.plugin("alpha");
  h.button("Run diagnostics").click();
  assert.equal(h.env.sent("diag").length, 1);
  const core = { loader: { version: "1.1.0", api: 1 }, workers: { fan: { state: "ready" } } };
  h.answer("diag", core);
  await settle();
  const area = h.box.querySelector("textarea");
  assert.equal(area.readOnly, true);
  const out = JSON.parse(area.value);
  assert.deepEqual(out.loader, core);
  assert.deepEqual(Object.keys(out.page).sort(), ["calls", "heap", "notifications", "plugins", "subs", "untrusted_clicks"]);
  assert.equal(out.page.plugins[0].id, "alpha");
  h.button("Copy").click();
  assert.equal(h.copied.length, 1);
  assert.equal(h.copied[0], area.value);
});

t("diagnostics text: error replies and the size cut", async () => {
  const h = boot();
  const p = h.FY.dev.text();
  h.answer("diag", { error: "nope" });
  assert.deepEqual(JSON.parse(await p).loader, { error: "nope" });
  const q = h.FY.dev.text();
  h.answer("diag", { blob: "x".repeat(70000) });
  const big = await q;
  assert.ok(big.length <= 60006);
  assert.ok(big.endsWith("[cut]"));
});

t("diagnostics text carries the loader answer and the page diag", async () => {
  const h = boot();
  const p = h.FY.dev.text();
  h.answer("diag", { ok: 1 });
  const out = JSON.parse(await p);
  assert.deepEqual(out.loader, { ok: 1 });
  assert.ok(Array.isArray(out.page.plugins));
});

t("elements are owned by core and nothing is polled", async () => {
  const h = await open(STATUS({ hot: [{ id: "alpha", mode: "inotify", remaining_s: 5, truncated: false }] }));
  h.button("Find debug targets").click();
  h.answer("devtools_info", { available: true, targets: [], hint: "" });
  h.button("Run diagnostics").click();
  h.answer("diag", {});
  await settle();
  assert.ok([...h.box.querySelectorAll("div, button, textarea")].every(e => e.dataset.fy === "core" || e === h.box));
  assert.ok(h.buttons().every(b => b.dataset.fy === "core" && b.tabIndex === 0 && b.type === "button"));
  h.env.clock.tick(10 * 60 * 1000);
  assert.equal(h.spy.timers, 0);
  assert.equal(h.spy.listeners, 0);
  assert.equal(h.env.calls.filter(c => !c.done).length, 0);
});

t("source: no innerHTML, no comments, no timers", async () => {
  const src = fs.readFileSync(path.join(ROOT, "fy-dev.js"), "utf8");
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(src));
  assert.ok(!/^\s*(\/\/|\/\*)/m.test(src));
  assert.ok(!/setTimeout|setInterval|requestAnimationFrame/.test(src));
});

t("On/Off keeps focus on the switched row after the page re-renders", async () => {
  const hot = [{ id: "alpha", mode: "inotify", remaining_s: 60, truncated: false }];
  const h = await open(STATUS({ hot }));
  const hotButtons = () => h.buttons().filter(b => b.textContent === "On" || b.textContent === "Off");
  hotButtons()[1].focus();
  hotButtons()[1].click();
  h.answer("dev_set", {});
  await settle();
  h.answer("dev_status", STATUS({ hot: hot.concat([{ id: "beta", mode: "inotify", remaining_s: 60, truncated: false }]) }));
  h.answer("list", LIST);
  await settle();
  assert.equal(hotButtons().length, 2);
  assert.strictEqual(h.d.activeElement, hotButtons()[1]);
});

t("no K.ready hook call is left in fy-dev.js", () => {
  assert.ok(!/K\.ready/.test(fs.readFileSync(path.join(ROOT, "fy-dev.js"), "utf8")));
});

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
  }
  console.log(pass + " passed, " + fail + " failed, " + (pass + fail) + " total");
  process.exit(fail ? 1 : 0);
})();
