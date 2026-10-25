const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT, TOK } = require("./harness");

const STARTER = path.join(ROOT, "examples", "starter");
const MANIFEST = JSON.parse(fs.readFileSync(path.join(STARTER, "plugin.json"), "utf8"));
const LOCALE = JSON.parse(fs.readFileSync(path.join(STARTER, "locales", "en.json"), "utf8"));
const MAIN = fs.readFileSync(path.join(STARTER, "main.js"), "utf8");

const flush = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };

const boot = (options = {}) => {
  const env = makeDom({ loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-picker.js"] });
  const { window: w, document: d } = env;
  const FY = w.Framey;
  const opened = [];
  FY.open = id => opened.push(id);
  FY.redraw = () => {};
  w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en" };
  FY.i18n.setLang("en");
  env.loadPlugin("starter", "examples/starter/main.js", MANIFEST);
  const p = FY.plugins.find(x => x.id === "starter");
  const api = p && FY._k(TOK).apiOf(p);
  const box = d.createElement("div");
  d.body.append(box);
  const answer = (method, value, plugin) => {
    const c = env.calls.find(x => x.method === method && !x.done && (!plugin || x.plugin === plugin));
    assert.ok(c, "no pending call " + method);
    c.done = true;
    env.answer(c, value);
  };
  const count = method => env.calls.filter(c => c.method === method).length;
  const find = (root, sel, text) => [...root.querySelectorAll(sel)].find(e => e.textContent === text || e.getAttribute("aria-label") === text);
  const h = { env, w, d, FY, p, api, box, opened, answer, count, find };
  h.show = async (reply = { enabled: true, level: 50, mode: "normal" }) => {
    p.render(box, api);
    answer("locale", { strings: LOCALE }, "_core");
    answer("get", reply, "starter");
    await settle();
  };
  h.btn = text => find(box, '[role="button"]', text);
  h.toggle = text => find(box, '[role="switch"]', text);
  h.slider = () => box.querySelector('[role="slider"]');
  h.option = text => find(box, '[role="option"]', text);
  h.press = (el, key) => el.dispatchEvent(new w.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  h.texts = () => box.textContent;
  h.toasts = () => [...d.querySelectorAll('[role="status"][data-fy="starter"]')];
  return h;
};

let pass = 0, fail = 0;
const queue = [];
const t = (name, fn) => queue.push([name, fn]);

t("registers with the manifest identity and draws nothing until translations are loaded", async () => {
  const h = boot();
  assert.ok(h.p, "plugin registered");
  assert.equal(h.p.name, "Starter");
  assert.equal(h.p.short, "Starter");
  assert.equal(h.env.calls.length, 0);
  h.p.render(h.box, h.api);
  assert.equal(h.box.children.length, 0);
  assert.deepEqual(h.env.calls.map(c => [c.plugin, c.method]), [["_core", "locale"], ["starter", "get"]]);
  assert.deepEqual(h.env.calls[0].arg, { id: "starter", lang: "en" });
  h.answer("locale", { strings: LOCALE }, "_core");
  h.answer("get", { enabled: true, level: 50, mode: "normal" }, "starter");
  await settle();
  assert.equal(h.box.children[0].textContent, "Starter");
  assert.ok(h.toggle("Feature enabled"));
  assert.ok(h.slider());
  assert.ok(h.box.querySelector('[role="listbox"]'));
  ["Save", "Start job", "Fetch example page", "Choose a file", "Open details page"].forEach(l => assert.ok(h.btn(l), l));
});

t("saved backend values are applied to the controls", async () => {
  const h = boot();
  await h.show({ enabled: false, level: 70, mode: "eco" });
  assert.equal(h.toggle("Feature enabled").getAttribute("aria-checked"), "false");
  assert.equal(h.slider().getAttribute("aria-valuenow"), "70");
  assert.equal(h.option("Eco").getAttribute("aria-selected"), "true");
  assert.equal(h.option("Normal").getAttribute("aria-selected"), "false");
  assert.equal(h.slider().getAttribute("aria-valuetext"), "70%");
});

t("garbage or failed backend values leave the defaults", async () => {
  for (const reply of [{ error: "boom" }, { enabled: "yes", level: "70", mode: "turbo" }, { level: -1 }, { level: 101 }, { level: 2.5 }, { level: true }, null]) {
    const h = boot();
    await h.show(reply);
    assert.equal(h.toggle("Feature enabled").getAttribute("aria-checked"), "true");
    assert.equal(h.slider().getAttribute("aria-valuenow"), "50");
    assert.equal(h.option("Normal").getAttribute("aria-selected"), "true");
  }
});

t("controls work from the keyboard and Save sends their values", async () => {
  const h = boot();
  await h.show();
  h.press(h.toggle("Feature enabled"), "Enter");
  h.press(h.slider(), "ArrowRight");
  h.press(h.slider(), "ArrowRight");
  h.option("Boost").click();
  h.press(h.btn("Save"), " ");
  const sent = h.env.sent("set");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].plugin, "starter");
  assert.deepEqual(sent[0].arg, { enabled: false, level: 60, mode: "boost" });
  h.answer("set", { ok: true }, "starter");
  await settle();
  assert.ok(h.texts().includes("Saved"));
  assert.equal(h.env.sent("log").length, 1);
  assert.equal(h.env.sent("log")[0].arg.level, "info");
  assert.equal(h.env.sent("log")[0].arg.pid, "starter");
  h.env.clock.tick(3000);
  assert.ok(!h.texts().includes("Saved"));
  h.btn("Save").click();
  h.answer("set", { error: "level must be a whole number from 0 to 100" }, "starter");
  await settle();
  assert.ok(h.texts().includes("Could not save: level must be a whole number from 0 to 100"));
  h.btn("Save").click();
  h.answer("set", undefined, "starter");
  await settle();
  assert.ok(h.texts().includes("Saved"));
});

t("a newer note is not cleared by an older timer", async () => {
  const h = boot();
  await h.show();
  h.btn("Save").click();
  h.answer("set", { ok: true }, "starter");
  await settle();
  h.env.clock.tick(2000);
  h.btn("Fetch example page").click();
  h.answer("fetch_demo", { status: 200, bytes: 12 }, "starter");
  await settle();
  h.env.clock.tick(1500);
  assert.ok(h.texts().includes("Fetched 12 bytes (status 200)"));
  h.env.clock.tick(2000);
  assert.ok(!h.texts().includes("Fetched"));
});

t("job events update the page only while it is visible", async () => {
  const h = boot();
  await h.show();
  assert.ok(h.texts().includes("No job running"));
  assert.ok(h.texts().includes("Panel hidden"));
  h.btn("Start job").click();
  assert.equal(h.env.sent("start").length, 1);
  h.answer("start", { ok: true }, "starter");
  await settle();
  h.FY._k(TOK).event("starter", "progress", { step: 2, total: 5 });
  assert.ok(h.texts().includes("No job running"));
  h.FY._panel(true);
  assert.ok(h.texts().includes("Step 2 of 5"));
  assert.ok(h.texts().includes("Panel visible"));
  h.FY._k(TOK).event("starter", "progress", { step: 3, total: 5 });
  assert.ok(h.texts().includes("Step 3 of 5"));
  h.FY._panel(false);
  assert.ok(h.texts().includes("Panel hidden"));
  h.FY._k(TOK).event("starter", "progress", { step: 4, total: 5 });
  h.FY._k(TOK).event("starter", "progress", null);
  h.FY._k(TOK).event("other", "progress", { step: 1, total: 1 });
  h.FY._panel(true);
  assert.ok(h.texts().includes("Step 0 of 0"));
});

t("a refused start shows the reason", async () => {
  const h = boot();
  await h.show();
  h.btn("Start job").click();
  h.answer("start", { error: "a job is already running" }, "starter");
  await settle();
  assert.ok(h.texts().includes("Job failed: a job is already running"));
});

t("done raises a notification with two actions", async () => {
  const h = boot();
  await h.show();
  h.FY._k(TOK).event("starter", "done", { steps: 5 });
  const [n] = h.toasts();
  assert.ok(n);
  assert.ok(n.textContent.includes("Job finished"));
  assert.ok(n.textContent.includes("5 steps done"));
  const labels = [...n.querySelectorAll("button")].map(b => b.textContent);
  assert.deepEqual(labels, ["Run again", "Open Modules", "Dismiss"]);
  [...n.querySelectorAll("button")][0].click();
  assert.equal(h.env.sent("start").length, 1);
  assert.equal(h.toasts().length, 0);
  h.FY._k(TOK).event("starter", "done", { steps: 1 });
  assert.ok(h.toasts()[0].textContent.includes("1 step done"));
  [...h.toasts()[0].querySelectorAll("button")][1].click();
  assert.deepEqual(h.opened, ["#modules"]);
});

t("file picker: choose and cancel through the bound api", async () => {
  const h = boot();
  await h.show();
  assert.equal(typeof h.api.pickFile, "function");
  h.btn("Choose a file").click();
  await settle();
  const listing = { root: "data", path: "", parent: null, entries: [{ name: "a.txt", type: "file" }, { name: "docs", type: "dir" }], roots: ["data"] };
  assert.ok(!h.env.sent("files_list")[0].arg.root || h.env.sent("files_list")[0].arg.root === "data");
  assert.equal(h.env.sent("files_list")[0].arg.pid, "starter");
  h.answer("files_list", listing, "_core");
  await settle();
  const dialog = h.d.querySelector('[role="dialog"]');
  assert.ok(dialog);
  assert.equal(dialog.getAttribute("aria-label"), "Choose a file");
  h.find(dialog, '[role="button"]', "Cancel").click();
  await settle();
  assert.equal(h.d.querySelector('[role="dialog"]'), null);
  assert.ok(h.texts().includes("No file chosen"));
  h.btn("Choose a file").click();
  await settle();
  h.answer("files_list", listing, "_core");
  await settle();
  const dlg = h.d.querySelector('[role="dialog"]');
  h.find(dlg, '[role="option"]', "a.txt").click();
  h.find(dlg, '[role="button"]', "Select").click();
  await settle();
  assert.ok(h.texts().includes("Chosen: a.txt"));
});

t("fetch demo reports bytes and errors", async () => {
  const h = boot();
  await h.show();
  h.btn("Fetch example page").click();
  h.answer("fetch_demo", { status: 200, bytes: 1256 }, "starter");
  await settle();
  assert.ok(h.texts().includes("Fetched 1256 bytes (status 200)"));
  h.btn("Fetch example page").click();
  h.answer("fetch_demo", { error: "That address is not allowed", code: "blocked_address" }, "starter");
  await settle();
  assert.ok(h.texts().includes("Fetch failed: That address is not allowed"));
});

t("details page: registered once, opens full screen, Back closes it", async () => {
  const h = boot();
  await h.show();
  assert.deepEqual(h.api.pages.list(), [{ id: "starter-details", key: "starter:starter-details", title: "Details", mode: "full", open: false }]);
  h.btn("Open details page").click();
  const layer = h.d.querySelector('[data-fy="starter"][style*="99998"]');
  assert.ok(layer, "full page layer");
  assert.ok(layer.textContent.includes("Details"));
  assert.ok(layer.textContent.includes("registered with api.pages.register"));
  assert.equal(h.api.pages.list()[0].open, true);
  h.find(layer, '[role="button"]', "Back").click();
  assert.equal(h.d.querySelector('[data-fy="starter"][style*="99998"]'), null);
  assert.equal(h.api.pages.list()[0].open, false);
});

t("corner badge toggle mounts and unmounts a global component", async () => {
  const h = boot();
  await h.show();
  assert.equal(h.d.querySelector("[data-fy-zone]"), null);
  h.press(h.toggle("Show corner badge"), "Enter");
  const zone = h.d.querySelector('[data-fy-zone="corner"]');
  assert.ok(zone);
  assert.ok(zone.textContent.includes("Starter is on"));
  assert.equal(zone.firstChild.dataset.fy, "starter");
  const again = h.d.createElement("div");
  h.d.body.append(again);
  h.p.render(again, h.api);
  await settle();
  assert.equal(h.find(again, '[role="switch"]', "Show corner badge").getAttribute("aria-checked"), "true");
  h.press(h.toggle("Show corner badge"), "Enter");
  assert.equal(h.d.querySelector("[data-fy-zone]"), null);
});

t("rendering again does not repeat setup", async () => {
  const h = boot();
  await h.show();
  for (let i = 0; i < 3; i++) {
    const b = h.d.createElement("div");
    h.d.body.append(b);
    h.p.render(b, h.api);
    await settle();
    assert.ok(b.textContent.includes("Starter"));
  }
  assert.equal(h.count("locale"), 1);
  assert.equal(h.count("get"), 1);
  const me = h.FY.diag().plugins.find(x => x.id === "starter");
  assert.equal(me.subscriptions, 2);
  assert.equal(me.pages, 1);
  h.FY._k(TOK).event("starter", "done", { steps: 2 });
  assert.equal(h.toasts().length, 1);
});

t("late initialization cannot draw into an older render", async () => {
  const h = boot();
  h.p.render(h.box, h.api);
  const next = h.d.createElement("div");
  h.d.body.append(next);
  h.p.render(next, h.api);
  h.answer("locale", { strings: LOCALE }, "_core");
  h.answer("get", { enabled: false, level: 25, mode: "eco" }, "starter");
  await settle();
  assert.equal(h.box.children.length, 0);
  assert.ok(next.textContent.includes("Starter"));
  assert.equal(next.querySelector('[role="slider"]').getAttribute("aria-valuenow"), "25");
  assert.equal(h.count("get"), 1);
});

t("disposal during initialization cannot recreate nodes or subscriptions", async () => {
  const h = boot();
  h.p.render(h.box, h.api);
  const pending = h.env.calls.slice();
  h.FY.dispose("starter", TOK);
  for (const c of pending) h.env.answer(c, c.method === "locale" ? { strings: LOCALE } : { level: 70 });
  await settle();
  assert.equal(h.box.children.length, 0);
  assert.equal(h.d.querySelectorAll('[data-fy="starter"]').length, 0);
  assert.equal(h.env.clock.pending(), 0);
});

t("Hello uses scoped controls and ignores stale or disposed backend replies", async () => {
  const env = makeDom({ loadFiles: ["fy-api.js", "fy-ui.js"] });
  const meta = JSON.parse(fs.readFileSync(path.join(ROOT, "examples/hello-plugin/plugin.json"), "utf8"));
  env.loadPlugin("hello", "examples/hello-plugin/main.js", meta);
  const p = env.record("hello");
  const api = env.window.Framey._k(TOK).apiOf(p);
  const first = env.document.createElement("div");
  const second = env.document.createElement("div");
  env.document.body.append(first, second);
  p.render(first, api);
  p.render(second, api);
  const requests = env.sent("get");
  env.answer(requests[0], { level: 10 });
  env.answer(requests[1], { level: 70 });
  await settle();
  assert.equal(first.querySelector('[role="slider"]').value, 50);
  const slider = second.querySelector('[role="slider"]');
  assert.equal(slider.value, 70);
  slider.dispatchEvent(new env.window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  slider.dispatchEvent(new env.window.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  const save = env.sent("set")[0];
  assert.deepEqual(save.arg, { level: 75 });
  env.answer(save, { ok: true });
  await settle();
  assert.ok(second.textContent.includes("Saved"));
  const greet = [...second.querySelectorAll('[role="button"]')].find(e => e.textContent === "Greet");
  greet.click();
  const call = env.sent("greet")[0];
  env.window.Framey.dispose("hello", TOK);
  env.answer(call, { text: "late greeting" });
  await settle();
  assert.equal(env.document.querySelectorAll('[data-fy="hello"]').length, 0);
  assert.equal(env.clock.pending(), 0);
});

t("nothing is called on the backend before the first render", async () => {
  const h = boot();
  h.FY._panel(true);
  h.FY._panel(false);
  assert.equal(h.env.calls.length, 0);
  assert.equal(h.env.clock.pending(), 0);
});

t("dispose removes every element, timer and subscription", async () => {
  const h = boot();
  await h.show();
  h.btn("Save").click();
  h.answer("set", { ok: true }, "starter");
  await settle();
  h.press(h.toggle("Show corner badge"), "Enter");
  h.btn("Open details page").click();
  h.FY._k(TOK).event("starter", "done", { steps: 3 });
  assert.ok(h.env.clock.pending() > 0);
  assert.ok(h.d.querySelectorAll('[data-fy="starter"]').length > 0);
  h.FY.dispose("starter", TOK);
  assert.equal(h.d.querySelectorAll('[data-fy="starter"]').length, 0);
  assert.equal(h.d.querySelector("[data-fy-zone]"), null);
  assert.equal(h.toasts().length, 0);
  assert.equal(h.env.clock.pending(), 0);
  assert.equal(h.FY.diag().plugins.find(x => x.id === "starter"), undefined);
  h.FY._k(TOK).event("starter", "done", { steps: 3 });
  h.FY._k(TOK).event("starter", "progress", { step: 1, total: 2 });
  assert.equal(h.toasts().length, 0);
  h.FY.dispose("starter", TOK);
});

t("reloading the module replaces the old copy cleanly", async () => {
  const h = boot();
  await h.show();
  h.press(h.toggle("Show corner badge"), "Enter");
  h.env.loadPlugin("starter", "examples/starter/main.js", MANIFEST);
  assert.equal(h.FY.plugins.filter(x => x.id === "starter").length, 1);
  assert.equal(h.d.querySelector("[data-fy-zone]"), null);
});

t("every translation key used exists, and every string is used", async () => {
  const used = new Set([...MAIN.matchAll(/\bt\("([a-z][a-z0-9.]*[a-z0-9])"/g)].map(m => m[1]));
  ["eco", "normal", "boost"].forEach(m => used.add("mode." + m));
  assert.ok(MAIN.includes('api.t("mode." + id)'));
  assert.ok(used.size > 20);
  for (const key of used) assert.ok(key in LOCALE || key + "_other" in LOCALE, "missing " + key);
  for (const key of Object.keys(LOCALE)) assert.ok(used.has(key.replace(/_(one|other|zero)$/, "")), "unused " + key);
  for (const v of Object.values(LOCALE)) assert.ok(typeof v === "string" && v.length < 200);
  assert.ok("done.body_one" in LOCALE && "done.body_other" in LOCALE);
});

t("only members of the real platform API are used", async () => {
  const h = boot();
  await h.show();
  const api = h.api;
  const members = new Set();
  for (const m of MAIN.matchAll(/\bapi\.([A-Za-z0-9]+)(?:\.([A-Za-z]+))?/g)) members.add(m[1] + (m[2] ? "." + m[2] : ""));
  const nested = new Set(["ui", "events", "pages", "global", "i18n"]);
  assert.ok(members.size > 15, [...members].join());
  for (const m of members) {
    const [a, b] = m.split(".");
    assert.ok(a in api, "api." + a);
    if (b && nested.has(a)) assert.ok(b in api[a], "api." + m);
  }
  for (const m of MAIN.matchAll(/\bui\.([a-z]+)\(/g)) assert.ok(m[1] in api.ui, "ui." + m[1]);
  for (const m of MAIN.matchAll(/\bFramey\.([A-Za-z]+)/g)) assert.ok(m[1] === "register" || m[1] in h.FY, "Framey." + m[1]);
});

t("the starter demonstrates the main generation 1 workflows", async () => {
  [
    "Framey.register", "api.call(", "api.events.on(", "api.notify(", "api.onVisible(", "api.visible", "api.t(", "api.i18n.load(", "api.lang",
    "ui.toggle(", "ui.slider(", "ui.list(", "ui.button(", "ui.group(", "api.pickFile(",
    "api.pages.register(", "api.global.mount(", "api.cleanup(", "api.timeout(", "api.log(",
  ].forEach(s => assert.ok(MAIN.includes(s), s));
});

t("source: no innerHTML, no comments, no direct network or storage, no eval", async () => {
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML|document\.write/.test(MAIN));
  assert.ok(!/^\s*(\/\/|\/\*)/m.test(MAIN));
  assert.ok(!/\bfetch\(|XMLHttpRequest|localStorage|sessionStorage|\beval\(|new Function|setTimeout|setInterval|requestAnimationFrame|window\./.test(MAIN));
  MAIN.split("\n").forEach((line, i) => assert.ok(line.length < 260, "line " + (i + 1)));
});

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
  }
  console.log(pass + " passed, " + fail + " failed, " + (pass + fail) + " total");
  process.exit(fail ? 1 : 0);
})();
