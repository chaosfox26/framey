"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const CORRUPT = "Settings file is damaged. Framey is running with no plugins enabled until it is repaired.";
const UNREADABLE = "Settings cannot be read (permissions or disk error). Plugins stay off.";
const mod = (id, name) => ({ id, name, version: "1.0.0", commit: "abc123", enabled: true, nosource: "", pending: false, compat: { badge: "ok", msg: "" }, fail: null, stuck: false, linked: false });
const prefs = { autocheck: false, notify_loader: false, notify_plugins: false, channel: "stable", lang: "", safe: false, dev: { enabled: false } };
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };

const boot = (over = {}, trusted = "off") => {
  const calls = [];
  const R = Object.assign({
    list: [mod("a", "Alpha")],
    store: [],
    state_get: { order: ["a"], sort: "custom", hidden: [], frozen: {}, prefs, safe: false },
    maint_prefs: {},
    loader: { commit: "1234567", kept: false },
    autocheck_status: { enabled: false, last: 0, next_ok: 0, fails: 0 },
    status: { settings_state: "ok" },
    settings_repair: { ok: true },
  }, over);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: trusted };
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
  const sent = m => calls.filter(c => c.method === m);
  const all = () => [...box.querySelectorAll('[role="button"],[role="switch"],[role="option"]')];
  const find = (label, n = 0) => {
    const hit = all().filter(e => e.getAttribute("aria-label") === label);
    if (!hit[n]) throw new Error("no control '" + label + "' in: " + all().map(e => e.getAttribute("aria-label")).join(" | "));
    return hit[n];
  };
  const has = label => all().some(e => e.getAttribute("aria-label") === label);
  const click = async label => {
    find(label).click();
    await settle();
  };
  const open = async which => {
    FY.pages[which].render(box, {});
    await settle();
  };
  const toasts = () => [...env.document.querySelectorAll('[role="status"]')].map(e => e.textContent).join(" | ");
  return { FY, box, calls, R, sent, has, find, click, open, toasts, text: () => box.textContent };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("modules page: corrupt settings shows the banner and a Repair settings button", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  await c.open("modules");
  assert.ok(c.text().includes(CORRUPT));
  assert.ok(c.has("Repair settings"));
  assert.equal(c.sent("status").length, 1);
});

t("settings page: corrupt settings shows the banner and a Repair settings button", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  await c.open("settings");
  assert.ok(c.text().includes(CORRUPT));
  assert.ok(c.has("Repair settings"));
});

t("ok settings: no banner on either page", async () => {
  const c = boot({ status: { settings_state: "ok" } });
  await c.open("modules");
  assert.ok(!c.text().includes(CORRUPT) && !c.text().includes(UNREADABLE));
  assert.ok(!c.has("Repair settings"));
  await c.open("settings");
  assert.ok(!c.text().includes(CORRUPT) && !c.text().includes(UNREADABLE));
  assert.ok(!c.has("Repair settings"));
});

t("unreadable settings: message shown on both pages and no repair button", async () => {
  const c = boot({ status: { settings_state: "unreadable" } });
  await c.open("modules");
  assert.ok(c.text().includes(UNREADABLE));
  assert.ok(!c.has("Repair settings"));
  await c.open("settings");
  assert.ok(c.text().includes(UNREADABLE));
  assert.ok(!c.has("Repair settings"));
});

t("repair flow: confirm calls settings_repair once, refreshes the page and shows a toast", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  await c.open("modules");
  await c.click("Repair settings");
  assert.ok(c.text().includes("The damaged file is kept aside as a backup and defaults are used."));
  c.R.status = { settings_state: "ok" };
  const lists = c.sent("list").length;
  await c.click("Repair");
  assert.equal(c.sent("settings_repair").length, 1);
  assert.ok(c.sent("list").length > lists, "lists are reloaded");
  assert.ok(!c.text().includes(CORRUPT));
  assert.ok(c.toasts().includes("Settings repaired"));
});

t("repair flow on settings page calls settings_repair once and refreshes", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  await c.open("settings");
  await c.click("Repair settings");
  c.R.status = { settings_state: "ok" };
  await c.click("Repair");
  assert.equal(c.sent("settings_repair").length, 1);
  assert.ok(c.sent("status").length >= 2);
  assert.ok(!c.text().includes(CORRUPT));
});

t("repair failure shows the error and still reloads", async () => {
  const c = boot({ status: { settings_state: "corrupt" }, settings_repair: { ok: false, error: "disk full" } });
  await c.open("modules");
  await c.click("Repair settings");
  await c.click("Repair");
  assert.equal(c.sent("settings_repair").length, 1);
  assert.ok(c.toasts().includes("disk full"));
  assert.ok(c.has("Repair settings"));
});

t("Cancel does nothing: no settings_repair call and the banner comes back", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  await c.open("modules");
  await c.click("Repair settings");
  await c.click("Cancel");
  assert.equal(c.sent("settings_repair").length, 0);
  assert.ok(c.text().includes(CORRUPT));
  assert.ok(c.has("Repair settings"));
});

t("untrusted click on Repair settings does nothing under trusted_input require", async () => {
  const c = boot({ status: { settings_state: "corrupt" } }, "require");
  await c.open("modules");
  await c.click("Repair settings");
  assert.ok(!c.text().includes("The damaged file is kept aside"));
  await c.click("Repair settings");
  assert.equal(c.sent("settings_repair").length, 0);
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
