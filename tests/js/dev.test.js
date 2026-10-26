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

t("hot reload controls name their module and reject duplicate writes while pending", async () => {
  const h = await open();
  const off = h.buttons().find(b => b.textContent === "Off");
  assert.equal(off.getAttribute("aria-label"), "Hot reload for Alpha: off");
  assert.equal(off.getAttribute("aria-pressed"), "false");
  off.click();
  off.click();
  assert.equal(h.env.sent("dev_set").length, 1);
  assert.equal(off.disabled, true);
  assert.equal(off.getAttribute("aria-busy"), "true");
  h.answer("dev_set", STATUS());
  await settle();
  h.answer("dev_status", STATUS({ hot: [{ id: "alpha", mode: "inotify", remaining_s: 60 }] }));
  h.answer("list", LIST);
  await settle();
  const on = h.buttons().find(b => b.textContent === "On");
  assert.equal(on.disabled, false);
  assert.equal(on.getAttribute("aria-pressed"), "true");
});

t("an old status request cannot overwrite a newer refresh", async () => {
  const h = await open();
  h.button("Refresh status").click();
  h.button("Refresh status").click();
  const statuses = h.env.sent("dev_status").filter(c => !c.done);
  const lists = h.env.sent("list").filter(c => !c.done);
  assert.equal(statuses.length, 2);
  const reply = (call, value) => { call.done = true; h.env.answer(call, value); };
  reply(statuses[1], STATUS({ hot: [{ id: "beta", mode: "inotify", remaining_s: 60 }] }));
  reply(lists[1], LIST);
  await settle();
  assert.ok(h.box.textContent.includes("<b>Beta</b>  (inotify, 1 min left)"));
  reply(statuses[0], STATUS({ enabled: false }));
  reply(lists[0], []);
  await settle();
  assert.ok(h.box.textContent.includes("<b>Beta</b>  (inotify, 1 min left)"));
  assert.ok(!h.box.textContent.includes("Developer mode is off."));
});

t("leaving the developer page suppresses a late write result and its refresh", async () => {
  const h = await open();
  h.buttons().find(b => b.textContent === "Off").click();
  h.box.replaceChildren(h.d.createElement("div"));
  h.answer("dev_set", { error: "late write error" });
  await settle();
  assert.equal(h.env.sent("dev_status").length, 1);
  assert.ok(!h.d.body.textContent.includes("late write error"));
});

t("a repeated render does not let old diagnostics populate either view", async () => {
  const h = await open();
  h.button("Run diagnostics").click();
  const old = h.box.querySelector("textarea");
  h.FY.dev.render(h.box);
  const current = h.box.querySelector("textarea");
  assert.notStrictEqual(current, old);
  h.answer("diag", { stale: true });
  h.answer("dev_status", STATUS());
  h.answer("list", LIST);
  await settle();
  assert.equal(old.value, "");
  assert.equal(current.value, "");
  assert.equal(h.box.querySelectorAll("textarea").length, 1);
});

t("debug lookup and diagnostics cannot be submitted twice while pending", async () => {
  const h = await open();
  h.button("Find debug targets").click();
  h.button("Find debug targets").click();
  assert.equal(h.env.sent("devtools_info").length, 1);
  assert.equal(h.button("Find debug targets").getAttribute("aria-busy"), "true");
  h.button("Run diagnostics").click();
  h.button("Run diagnostics").click();
  assert.equal(h.env.sent("diag").length, 1);
  h.answer("devtools_info", { available: false, error: "closed", targets: [] });
  h.answer("diag", { error: "could not read status" });
  await settle();
  assert.equal(h.button("Find debug targets").disabled, false);
  assert.equal(h.button("Run diagnostics").disabled, false);
  assert.ok(h.box.textContent.includes("Diagnostics failed: could not read status"));
  assert.ok(!h.box.textContent.includes("Diagnostics ready."));
  assert.equal(h.box.querySelector("textarea").getAttribute("aria-label"), "Diagnostics output");
});

t("copy reports an unavailable or rejected clipboard instead of silent success", async () => {
  const h = await open();
  h.button("Copy").click();
  assert.ok(h.box.textContent.includes("Nothing to copy yet."));
  h.button("Run diagnostics").click();
  h.answer("diag", {});
  await settle();
  h.w.navigator.clipboard.writeText = () => Promise.reject(new Error("permission denied"));
  h.button("Copy").click();
  await settle();
  assert.ok(h.box.textContent.includes("Copy failed: permission denied"));
  Object.defineProperty(h.w.navigator, "clipboard", { configurable: true, value: undefined });
  h.button("Copy").click();
  await settle();
  assert.ok(h.box.textContent.includes("Copy failed: Clipboard is unavailable"));
  assert.ok(!h.box.textContent.includes("Copied."));
});

t("module-list failure is visible and a status refresh can recover it", async () => {
  const h = await open(STATUS(), { error: "module list unavailable" });
  assert.ok(h.box.textContent.includes("Could not load modules: module list unavailable"));
  h.button("Refresh status").click();
  h.answer("dev_status", STATUS());
  h.answer("list", LIST);
  await settle();
  assert.ok(h.box.textContent.includes("Alpha"));
  assert.ok(!h.box.textContent.includes("module list unavailable"));
});

t("partial debug lookup errors remain visible alongside usable targets", async () => {
  const h = await open(STATUS(), [null, false, ...LIST]);
  h.button("Find debug targets").click();
  h.answer("devtools_info", { available: true, error: "Some targets could not be read", targets: [null, { name: "main", inspector: "http://127.0.0.1:8080/" }] });
  await settle();
  assert.ok(h.box.textContent.includes("Some targets could not be read"));
  assert.equal(h.buttons().filter(b => b.textContent === "Copy address").length, 1);
  assert.ok(h.box.textContent.includes("recent log lines"));
  assert.ok(h.box.textContent.includes("review before sharing"));
});

t("hot reload persistence warnings remain visible without pretending the choice was saved", async () => {
  const warning = "Hot reload is active for this session, but its settings could not be saved.";
  const h = await open(STATUS({ warning }));
  assert.ok(h.box.textContent.includes(warning));
  h.buttons().find(b => b.textContent === "Off").click();
  h.answer("dev_set", STATUS({ warning }));
  await settle();
  assert.ok(h.d.body.textContent.includes(warning));
  h.answer("dev_status", STATUS({ warning, hot: [{ id: "alpha", mode: "inotify", remaining_s: 60 }] }));
  h.answer("list", LIST);
  await settle();
  assert.ok(h.box.textContent.includes(warning));
  assert.equal(h.buttons().find(b => b.textContent === "On").disabled, false);
});

t("Report to Framey is manual and keeps raw diagnostics out of its context", async () => {
  const h = await open();
  const contexts = [];
  h.w.__S.K.report = context => { contexts.push(context); return Promise.resolve({ closed: true }); };
  assert.ok(h.button("Report to Framey"));
  assert.equal(contexts.length, 0);
  assert.ok(h.box.textContent.includes("does not submit an issue automatically"));
  h.button("Run diagnostics").click();
  h.answer("diag", { error: "PRIVATE_SAMPLE /home/fixture-user/secret", credentials: "PRIVATE_SAMPLE" });
  await settle();
  assert.ok(h.box.querySelector("textarea").value.includes("PRIVATE_SAMPLE"));
  assert.equal(contexts.length, 0);
  h.button("Report to Framey").click();
  await settle();
  assert.equal(contexts.length, 1);
  assert.equal(contexts[0].code, "developer_diagnostics_failed");
  assert.equal(contexts[0].error, "The Developer page could not collect diagnostics.");
  assert.equal("plugin" in contexts[0], false);
  assert.equal("diagnostics" in contexts[0], false);
  assert.ok(!JSON.stringify(contexts[0]).includes("PRIVATE_SAMPLE"));
  assert.ok(!JSON.stringify(contexts[0]).includes("/home/"));
  assert.ok(h.box.textContent.includes("Report review closed. Framey did not submit an issue."));
});

t("report context names the failed developer operation without blaming a plugin", async () => {
  const h = await open();
  const contexts = [];
  h.w.__S.K.report = context => { contexts.push(context); return Promise.resolve({ closed: true }); };
  h.button("Find debug targets").click();
  h.answer("devtools_info", { error: "debug target private detail", targets: [] });
  await settle();
  h.button("Report to Framey").click();
  await settle();
  assert.equal(contexts[0].code, "developer_debug_targets_failed");
  assert.equal("plugin" in contexts[0], false);
  assert.ok(!JSON.stringify(contexts[0]).includes("private detail"));
});

t("a recovered developer failure does not remain in a later report", async () => {
  const h = await open();
  const contexts = [];
  h.w.__S.K.report = context => { contexts.push(context); return Promise.resolve({ closed: true }); };
  h.button("Run diagnostics").click();
  h.answer("diag", { error: "first failed" });
  await settle();
  h.button("Run diagnostics").click();
  h.answer("diag", {});
  await settle();
  h.button("Report to Framey").click();
  await settle();
  assert.equal(contexts[0].code, "developer_diagnostics");
});

t("reporting without the core helper gives visible recovery instructions", async () => {
  const h = await open();
  delete h.w.__S.K.report;
  h.button("Report to Framey").click();
  assert.ok(h.box.textContent.includes("Reporting is unavailable. Refresh Framey and try again."));
  assert.equal(h.button("Report to Framey").disabled, false);
  assert.equal(h.env.sent("report_prepare").length, 0);
});

t("a report stays single-flight until close and makes no submission claim", async () => {
  const h = await open();
  const contexts = [];
  let close;
  h.w.__S.K.report = context => { contexts.push(context); return new Promise(resolve => { close = resolve; }); };
  const button = h.button("Report to Framey");
  button.click();
  button.click();
  assert.equal(contexts.length, 1);
  assert.equal(button.disabled, true);
  assert.equal(button.getAttribute("aria-busy"), "true");
  close({ closed: true });
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(button.hasAttribute("aria-busy"), false);
  assert.ok(h.box.textContent.includes("Framey did not submit an issue"));
  assert.ok(!h.box.textContent.includes("Issue created"));
});

t("report helper throws and rejections are visible without copying their private messages", async () => {
  const h = await open();
  for (const helper of [() => { throw new Error("PRIVATE_REPORT_FAILURE"); }, () => Promise.reject(new Error("PRIVATE_REPORT_FAILURE"))]) {
    h.w.__S.K.report = helper;
    h.button("Report to Framey").click();
    await settle();
    assert.ok(h.box.textContent.includes("report review could not be confirmed"));
    assert.ok(!h.box.textContent.includes("PRIVATE_REPORT_FAILURE"));
    assert.equal(h.button("Report to Framey").disabled, false);
  }
});

t("closing an old report cannot repaint a newer developer render", async () => {
  const h = await open();
  let close;
  h.w.__S.K.report = () => new Promise(resolve => { close = resolve; });
  h.button("Report to Framey").click();
  h.FY.dev.render(h.box);
  h.answer("dev_status", STATUS());
  h.answer("list", LIST);
  await settle();
  close({ closed: true });
  await settle();
  assert.ok(!h.box.textContent.includes("Report review closed"));
  assert.equal(h.button("Report to Framey").disabled, false);
});

t("trusted-input mode blocks synthetic reporting clicks", async () => {
  const h = boot({ trusted: true });
  let reports = 0;
  h.w.__S.K.report = () => { reports++; return Promise.resolve({ closed: true }); };
  h.FY.dev.render(h.box);
  h.answer("dev_status", STATUS());
  h.answer("list", LIST);
  await settle();
  h.button("Report to Framey").click();
  await settle();
  assert.equal(reports, 0);
});

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
  }
  console.log(pass + " passed, " + fail + " failed, " + (pass + fail) + " total");
  process.exit(fail ? 1 : 0);
})();
