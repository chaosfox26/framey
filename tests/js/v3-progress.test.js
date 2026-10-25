"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const mod = (id, name) => ({ id, name, version: "1.0.0", commit: "abc123", enabled: true, nosource: "", pending: false, compat: { badge: "ok", msg: "" }, fail: null, stuck: false, linked: false });
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

const boot = over => {
  const calls = [];
  const R = Object.assign({
    list: [mod("a", "Alpha"), mod("b", "Beta")],
    store: [],
    state_get: { order: ["a", "b"], sort: "custom", hidden: [], frozen: {}, prefs: { channel: "stable" }, safe: false },
  }, over);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fyCall = s => {
        const c = JSON.parse(s);
        calls.push(c);
        if (!c.id) return;
        const f = c.method in R ? R[c.method] : { error: "unhandled " + c.method };
        Promise.resolve(typeof f === "function" ? f(c.arg, c) : f).then(v => w.Framey._reply(c.gen, c.id, v === undefined ? null : JSON.parse(JSON.stringify(v))));
      };
    },
    loadFiles: FILES,
  });
  const FY = env.window.Framey;
  const box = FY.el("");
  env.document.body.append(box);
  const all = () => [...box.querySelectorAll('[role="button"],[role="switch"],[role="option"]')];
  const click = async label => {
    const hit = all().find(e => e.getAttribute("aria-label") === label);
    if (!hit) throw new Error("no control '" + label + "' in: " + all().map(e => e.getAttribute("aria-label")).join(" | "));
    hit.click();
    await settle();
  };
  const text = () => box.textContent;
  const progress = ev => env.event("_core", "progress", ev);
  const open = async () => {
    FY.pages.modules.render(box, {});
    await settle();
  };
  return { calls, R, open, click, text, progress };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("single module Update shows the stage steps and a percent only when measured", async () => {
  const c = boot();
  let release;
  c.R.update = () => new Promise(r => { release = r; });
  await c.open();
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("Updating Alpha"));
  const ev = (stage, index, state, measured) => ({ job: 1, id: "a", stage, index, total: 6, label: stage[0].toUpperCase() + stage.slice(1), state, measured, error: null });
  c.progress(ev("check", 1, "ok", null));
  c.progress(ev("download", 2, "running", null));
  assert.ok(c.text().includes("Alpha: Step 2 of 6: Download"));
  assert.ok(c.text().includes("Check - Done"));
  assert.ok(!/\d+%/.test(c.text()));
  c.progress(ev("download", 2, "running", { done: 50, total: 200 }));
  assert.ok(c.text().includes("25%"));
  c.progress(ev("verify", 3, "fail", null));
  assert.ok(c.text().includes("Verify - Failed"));
  release({ ok: "a", version: "1.0.1" });
  await settle();
  assert.ok(!c.text().includes("Step 2 of 6"));
});

t("single module Update with no stage events keeps the busy title and shows no steps", async () => {
  const c = boot();
  let release;
  c.R.update = () => new Promise(r => { release = r; });
  await c.open();
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("Updating Alpha"));
  assert.ok(!c.text().includes("Step "));
  assert.ok(!/\d+%/.test(c.text()));
  release({ ok: "a", version: "1.0.1" });
  await settle();
  assert.ok(c.text().includes("Updated Alpha to v1.0.1"));
});

(async () => {
  let pass = 0, fail = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      pass++;
      console.log("ok   " + name);
    } catch (e) {
      fail++;
      console.log("FAIL " + name + "\n" + (e.stack || e));
    }
  }
  console.log(pass + " passed, " + fail + " failed, " + tests.length + " total");
  process.exit(fail ? 1 : 0);
})();
