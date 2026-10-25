"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const prefs = (over = {}) => Object.assign({ autocheck: false, notify_loader: false, notify_plugins: false, channel: "stable", lang: "", safe: false, dev: { enabled: false } }, over);
const stateOf = over => ({ order: [], sort: "custom", hidden: [], frozen: {}, prefs: prefs(over), safe: !!(over && over.safe) });
const defaults = () => ({
  loader: { commit: "1234567", kept: false },
  state_get: stateOf(),
  autocheck_status: { enabled: false, last: 0, next_ok: 0, fails: 0 },
  loader_check: () => checked(),
});
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

const boot = (over = {}, trusted = "off") => {
  const calls = [];
  const R = Object.assign(defaults(), over);
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
  const last = m => sent(m).slice(-1)[0];
  const all = () => [...box.querySelectorAll('[role="button"],[role="switch"],[role="option"]')];
  const find = (label, n = 0) => {
    const hit = all().filter(e => e.getAttribute("aria-label") === label);
    if (!hit[n]) throw new Error("no control '" + label + "' in: " + all().map(e => e.getAttribute("aria-label")).join(" | "));
    return hit[n];
  };
  const has = label => all().some(e => e.getAttribute("aria-label") === label);
  const click = async (label, n = 0) => {
    find(label, n).click();
    await settle();
  };
  const open = async () => {
    FY.pages.settings.render(box, {});
    await settle();
  };
  const progress = ev => env.event("_core", "progress", ev);
  return { env, FY, w: env.window, d: env.document, box, calls, R, sent, last, find, has, click, open, all, text: () => box.textContent, progress };
};

const cand = (over = {}) => Object.assign({ tag: "v1.2.0", commit: "abcdef0123456789abcdef0123456789abcdef01", prerelease: false, published: "2026-10-01T12:00:00Z", label: "", channel: "stable" }, over);
const checked = (over = {}) => {
  const candidate = cand(over.candidate);
  return {
    loader: Object.assign({ state: "update", latest: candidate.tag, installed: "1234567", candidate, downgrade: false, notes: "Fixes and polish." }, over.loader),
    candidate,
    installed: { version: "1.1.0", commit: "1234567", tag: "" },
  };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("shows installed identity from the runtime and the loader, and 'Not checked yet'", async () => {
  const c = boot();
  await c.open();
  assert.deepEqual(c.calls.map(x => x.method).sort(), ["autocheck_status", "loader", "maint_prefs", "state_get", "status"]);
  assert.ok(c.text().includes("Installed: 1.1.0, commit 1234567"));
  assert.ok(c.text().includes("Not checked yet"));
  assert.ok(c.text().includes("Language: English. No other languages are available yet."));
});

t("check shows the candidate identity next to the installed one: tag, commit7, date, prerelease flag", async () => {
  const c = boot();
  c.R.loader_check = () => checked({ candidate: { prerelease: true, label: "Testing build", tag: "v1.3.0-rc.1" } });
  await c.open();
  await c.click("Check for updates");
  const s = c.text();
  assert.ok(s.includes("Installed: 1.1.0, commit 1234567"));
  assert.ok(s.includes("Available: v1.3.0-rc.1, commit abcdef0, published 2026-10-01"));
  assert.ok(s.includes("Testing build"));
  assert.ok(s.includes("Update available"));
  assert.equal(c.sent("loader_update").length, 0);
});

t("check: current, error and an older offer are all readable", async () => {
  const c = boot();
  c.R.loader_check = () => checked({ loader: { state: "current" } });
  await c.open();
  await c.click("Check for updates");
  assert.ok(c.text().includes("Up to date"));
  c.R.loader_check = () => ({ loader: { state: "error", msg: "GitHub rate limit reached" } });
  await c.click("Check for updates");
  assert.ok(c.text().includes("Check failed: GitHub rate limit reached"));
  assert.ok(c.text().includes("Not checked yet"));
  c.R.loader_check = () => checked({ loader: { downgrade: true } });
  await c.click("Check for updates");
  assert.ok(c.text().includes("An older version is offered"));
  c.R.loader_check = () => ({ error: "busy" });
  await c.click("Check for updates");
  assert.ok(c.text().includes("Failed: busy"));
});

t("channel is read-only text that names the file; Settings never writes it", async () => {
  const c = boot();
  await c.open();
  assert.ok(c.text().includes("Update channel: stable. It is set in ~/.config/framey/loader.json"));
  assert.ok(!c.has("Stable") && !c.has("Prerelease"));
  const p = boot({ state_get: stateOf({ channel: "prerelease" }) });
  await p.open();
  assert.ok(p.text().includes("Update channel: prerelease."));
  assert.equal(c.sent("prefs_set").length + p.sent("prefs_set").length, 0);
});

t("Update asks first with the identity and release notes, then runs with confirm:false", async () => {
  const c = boot();
  c.R.loader_check = () => checked();
  c.R.loader_update = () => ({ ok: true });
  await c.open();
  await c.click("Check for updates");
  await c.click("Update");
  assert.equal(c.sent("loader_update").length, 0);
  assert.ok(c.text().includes("Update Framey?"));
  assert.ok(c.text().includes("Available: v1.2.0, commit abcdef0, published 2026-10-01"));
  assert.ok(c.text().includes("Fixes and polish."));
  await c.click("Update now");
  assert.deepEqual(c.last("loader_update").arg, { confirm: false, candidate: { tag: "v1.2.0", commit: "abcdef0123456789abcdef0123456789abcdef01" } });
  assert.ok(c.text().includes("Updated, Framey is restarting"));
});

t("Update without a check resolves and confirms an exact candidate first", async () => {
  const c = boot();
  c.R.loader_update = () => ({ current: true });
  await c.open();
  await c.click("Update");
  assert.equal(c.sent("loader_check").length, 1);
  assert.ok(c.text().includes("Available: v1.2.0, commit abcdef0"));
  assert.equal(c.sent("loader_update").length, 0);
  await c.click("Cancel");
  assert.ok(c.has("Check for updates"));
  await c.click("Update");
  await c.click("Update now");
  assert.ok(c.text().includes("Already up to date"));
});

t("an older Framey needs the loader's confirm, then runs with confirm:true", async () => {
  const c = boot();
  c.R.loader_check = () => checked({ loader: { downgrade: true } });
  c.R.loader_update = a => (a.confirm ? { ok: true } : { confirm: "Going back to v1.0.0 removes nothing from your data." });
  await c.open();
  await c.click("Check for updates");
  await c.click("Update");
  assert.ok(c.text().includes("Data changes made by newer versions are not undone"));
  await c.click("Update now");
  assert.ok(c.text().includes("Go back to an older Framey?"));
  assert.ok(c.text().includes("Going back to v1.0.0 removes nothing from your data."));
  assert.deepEqual(c.sent("loader_update").map(x => x.arg.confirm), [false]);
  await c.click("Update now");
  assert.deepEqual(c.sent("loader_update").map(x => x.arg.confirm), [false, true]);
});

t("update failure is readable and keeps Back", async () => {
  const c = boot();
  c.R.loader_update = () => ({ error: "Update refused: the package is incomplete" });
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  assert.ok(c.text().includes("Failed: Update refused: the package is incomplete"));
  await c.click("Back");
  assert.ok(c.has("Check for updates"));
});

t("loader update progress: steps listed, no percent unless measured", async () => {
  const c = boot();
  let release;
  c.R.loader_update = () => new Promise(r => { release = r; });
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  const ev = (stage, index, state, measured) => ({ job: 3, id: "_loader", stage, index, total: 6, label: stage, state, measured, error: null });
  c.progress(ev("check", 1, "ok", null));
  c.progress(ev("download", 2, "running", null));
  assert.ok(c.text().includes("Framey: Step 2 of 6: download"));
  assert.ok(!/\d+%/.test(c.text()));
  c.progress(ev("download", 2, "running", { done: 3, total: 4 }));
  assert.ok(c.text().includes("75%"));
  release({ ok: true });
  await settle();
  assert.ok(c.text().includes("Updated, Framey is restarting"));
});

t("release notes: loads the candidate's notes, shows 'No release notes' when empty, disabled before a check", async () => {
  const c = boot();
  c.R.loader_check = () => checked();
  c.R.notes = () => ({ notes: "Line one\nLine two", url: "" });
  await c.open();
  assert.equal(c.find("Release notes").getAttribute("aria-disabled"), "true");
  await c.click("Release notes");
  assert.equal(c.sent("notes").length, 0);
  await c.click("Check for updates");
  await c.click("Release notes");
  assert.deepEqual(c.last("notes").arg, { id: "_loader", tag: "v1.2.0" });
  assert.ok(c.text().includes("Release notes v1.2.0"));
  assert.ok(c.text().includes("Line one\nLine two"));
  c.R.notes = () => ({ notes: "", url: "" });
  await c.click("Back");
  await c.click("Release notes");
  assert.ok(c.text().includes("No release notes"));
  await c.click("Back");
  assert.ok(c.has("Check for updates"));
});

t("history lists loader releases, selecting one shows notes and a guarded install", async () => {
  const c = boot();
  c.R.ver_list = () => ({
    versions: [
      { tag: "v1.2.0", version: "1.2.0", commit: "c2", prerelease: false, published: "2026-10-01T00:00:00Z", current: false, compat: { badge: "unknown", msg: "" }, downgrade: false, eligible: true, reason: "", label: "" },
      { tag: "v1.0.0", version: "1.0.0", commit: "c0", prerelease: false, published: "2026-08-01T00:00:00Z", current: false, compat: { badge: "unknown", msg: "" }, downgrade: true, eligible: true, reason: "", label: "" },
    ],
    more: false,
  });
  c.R.ver_install = () => ({ ok: true });
  await c.open();
  await c.click("History");
  assert.deepEqual(c.last("ver_list").arg, { id: "_loader", page: 1 });
  assert.ok(c.has("1.2.0") && c.has("1.0.0"));
  await c.click("1.0.0");
  await c.click("Go back to this version");
  assert.equal(c.sent("ver_install").length, 0);
  assert.ok(c.text().includes("data migrations are not undone"));
  await c.click("Go back to this version");
  assert.deepEqual(c.last("ver_install").arg, { id: "_loader", tag: "v1.0.0", confirm: true });
});

t("notification and automatic-check preferences are separate, saved one key at a time", async () => {
  const c = boot();
  c.R.prefs_set = a => {
    c.R.state_get = stateOf(Object.assign({}, ...c.sent("prefs_set").map(x => x.arg).filter(x => !("dev.enabled" in x))));
    return c.R.state_get;
  };
  await c.open();
  ["Check automatically", "Tell me about Framey updates", "Tell me about module updates"].forEach(l => assert.equal(c.find(l).getAttribute("aria-checked"), "false", l));
  await c.click("Tell me about Framey updates");
  assert.deepEqual(c.last("prefs_set").arg, { notify_loader: true });
  assert.equal(c.find("Tell me about Framey updates").getAttribute("aria-checked"), "true");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "false");
  await c.click("Tell me about module updates");
  assert.deepEqual(c.last("prefs_set").arg, { notify_plugins: true });
  await c.click("Tell me about Framey updates");
  assert.deepEqual(c.last("prefs_set").arg, { notify_loader: false });
  assert.equal(c.find("Tell me about Framey updates").getAttribute("aria-checked"), "false");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "true");
  assert.equal(c.find("Check automatically").getAttribute("aria-checked"), "false");
  assert.equal(c.sent("loader_update").length, 0);
});

t("a rejected preference flips the switch back and shows the error", async () => {
  const c = boot();
  c.R.prefs_set = () => ({ error: "state too large" });
  await c.open();
  await c.click("Tell me about module updates");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "false");
  assert.ok(c.d.body.textContent.includes("state too large"));
});

t("automatic checks: off by default with an honest sentence; on shows last check", async () => {
  const c = boot();
  c.R.prefs_set = () => (c.R.state_get = stateOf({ autocheck: true }));
  await c.open();
  assert.ok(c.text().includes("Off. Checks only run when you tap Check for updates."));
  assert.ok(c.text().includes("never installs anything"));
  c.R.autocheck_status = () => ({ enabled: true, last: 1760000000, next_ok: 0, fails: 0 });
  await c.click("Check automatically");
  assert.deepEqual(c.last("prefs_set").arg, { autocheck: true });
  assert.equal(c.find("Check automatically").getAttribute("aria-checked"), "true");
  assert.ok(c.text().includes("On. Last check: "));
  c.R.autocheck_status = () => ({ enabled: true, last: 0, next_ok: 0, fails: 0 });
  c.R.state_get = stateOf({ autocheck: true });
  await c.open();
  assert.ok(c.text().includes("On. Not checked yet."));
});

t("automatic-check status errors stay unavailable while the saved switch remains on", async () => {
  const c = boot({ state_get: stateOf({ autocheck: true }), autocheck_status: { error: "Framey did not reply in 15 s" } });
  await c.open();
  assert.equal(c.find("Check automatically").getAttribute("aria-checked"), "true");
  assert.ok(c.text().includes("Unavailable"));
  assert.ok(c.text().includes("Framey did not reply in 15 s"));
  assert.ok(!c.text().includes("Off. Checks only run when you tap Check for updates."));
  assert.equal(c.sent("prefs_set").length, 0);
});

t("malformed automatic-check status is not presented as Off or a made-up last check", async () => {
  for (const status of [null, {}, { enabled: 0 }, { enabled: "false" }, { enabled: true }, { enabled: true, last: "later" }, { enabled: true, last: -1 }, { enabled: true, last: 1e300 }]) {
    const c = boot({ state_get: stateOf({ autocheck: true }), autocheck_status: status });
    await c.open();
    assert.equal(c.find("Check automatically").getAttribute("aria-checked"), "true");
    assert.ok(c.text().includes("Unavailable"), JSON.stringify(status));
    assert.ok(!c.text().includes("Off. Checks only run when you tap Check for updates."));
    assert.ok(!c.text().includes("On. Not checked yet.") && !c.text().includes("Invalid Date"));
    assert.equal(c.sent("prefs_set").length, 0);
  }
});

t("safe mode and developer mode are read-only text that name their files; Developer tools only when on", async () => {
  const c = boot();
  await c.open();
  assert.ok(c.text().includes("not protection"));
  assert.ok(c.text().includes("Safe mode is off."));
  assert.ok(c.text().includes("~/.config/framey/safe"));
  assert.ok(c.text().includes("Developer mode is off."));
  assert.ok(c.text().includes("~/.config/framey/developer"));
  assert.ok(!c.has("Safe mode") && !c.has("Developer mode"));
  assert.ok(!c.has("Developer tools") && !c.has("Diagnostics"));
  const d = boot({ state_get: stateOf({ dev: { enabled: true }, safe: true }) });
  await d.open();
  assert.ok(d.text().includes("Developer mode is on."));
  assert.ok(d.text().includes("Safe mode is on."));
  assert.ok(d.has("Developer tools") && d.has("Diagnostics"));
  assert.equal(c.sent("prefs_set").length + d.sent("prefs_set").length, 0);
  assert.equal(c.sent("asset").length, 0);
});

t("Developer tools opens the developer page with no asset request", async () => {
  const c = boot({ state_get: stateOf({ dev: { enabled: true } }) });
  let opened = "";
  c.FY.open = id => { opened = id; };
  Object.defineProperty(c.FY, "dev", { value: { render() {}, text() {} } });
  await c.open();
  await c.click("Developer tools");
  assert.equal(c.sent("asset").length, 0);
  assert.equal(opened, "#developer");
});

t("kept previous version: Done removes it, Roll back asks first", async () => {
  const c = boot({ loader: { commit: "1234567", kept: true } });
  c.R.loader_done = () => ({ ok: true });
  c.R.loader_roll = () => ({ ok: true });
  await c.open();
  assert.ok(c.text().includes("Framey updated. Previous version kept."));
  await c.click("Roll back");
  assert.ok(c.text().includes("Roll back Framey?"));
  assert.equal(c.sent("loader_roll").length, 0);
  await c.click("Cancel");
  await c.click("Roll back");
  await c.click("Roll back", 0);
  assert.equal(c.sent("loader_roll").length, 1);
  assert.ok(c.text().includes("Rolled back, Framey is restarting"));
  await c.click("Back");
  await c.click("Done");
  assert.equal(c.sent("loader_done").length, 1);
  assert.ok(c.text().includes("Previous version removed"));
});

t("restart screen after an update has a Back button that returns to Settings", async () => {
  const c = boot();
  c.R.loader_check = () => checked();
  c.R.loader_update = () => ({ ok: true });
  await c.open();
  await c.click("Check for updates");
  await c.click("Update");
  await c.click("Update now");
  assert.ok(c.text().includes("Updated, Framey is restarting"));
  await c.click("Back");
  assert.ok(c.has("Check for updates"));
  assert.ok(!c.text().includes("Updated, Framey is restarting"));
});

t("trusted_input require: synthetic clicks on Done, Roll back and Update do nothing; the gate passes trusted events", async () => {
  const c = boot({ loader: { commit: "1234567", kept: true } }, "require");
  c.R.loader_done = () => ({ ok: true });
  c.R.loader_roll = () => ({ ok: true });
  c.R.loader_update = () => ({ ok: true });
  await c.open();
  await c.click("Done");
  await c.click("Roll back");
  await c.click("Update");
  assert.equal(c.sent("loader_done").length, 0);
  assert.equal(c.sent("loader_roll").length, 0);
  assert.equal(c.sent("loader_update").length, 0);
  assert.ok(!c.text().includes("Roll back Framey?") && !c.text().includes("Update Framey?"));
  assert.ok(c.has("Done"));
  assert.strictEqual(c.w.__S.trusted({ isTrusted: true }), true);
  assert.strictEqual(c.w.__S.trusted({ isTrusted: false }), false);
});

t("Reload modules is a single tap and reports the loader error if any", async () => {
  const c = boot();
  c.R.reload = () => ({ ok: true });
  await c.open();
  await c.click("Reload modules");
  assert.equal(c.sent("reload").length, 1);
  c.R.reload = () => ({ error: "Busy: another check or update is running" });
  await c.click("Reload modules");
  assert.ok(c.d.body.textContent.includes("Busy: another check or update is running"));
});

t("the page keeps the trust sentence, creates no timers and uses plain text only", async () => {
  const c = boot({ loader: { commit: "<b>x</b>", kept: false } });
  const before = c.env.clock.pending();
  await c.open();
  assert.equal(c.env.clock.pending(), before);
  assert.ok(c.text().includes("Modules run inside Steam's UI. Install only ones you trust."));
  assert.equal(c.box.querySelectorAll("b").length, 0);
});

t("Modules page still works next to Settings (shared helpers, one progress listener)", async () => {
  const c = boot({ list: [], store: [] });
  c.FY.pages.modules.render(c.box, {});
  await settle();
  assert.ok(c.text().includes("No modules installed"));
  await c.open();
  assert.ok(c.text().includes("Settings"));
});

t("after a Framey update the restart screen puts focus on Back", async () => {
  const c = boot();
  c.R.loader_update = () => ({ ok: true });
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  assert.strictEqual(c.d.activeElement, c.find("Back"));
});

t("Developer tools without the Developer file says to restart instead of doing nothing", async () => {
  const c = boot();
  c.R.state_get = stateOf({ dev: { enabled: true } });
  await c.open();
  await c.click("Developer tools");
  assert.ok(c.d.body.textContent.includes("Restart Framey to open Developer tools"));
});

t("a failed settings read offers Retry instead of editable guessed preferences", async () => {
  const c = boot({ state_get: { error: "Settings could not be read" } });
  await c.open();
  assert.ok(c.text().includes("Settings could not be read"));
  assert.ok(c.has("Retry"));
  assert.ok(!c.has("Check automatically"));
  c.R.state_get = stateOf();
  await c.click("Retry");
  assert.ok(c.has("Check automatically"));
});

t("an older settings read cannot repaint a newer Settings render", async () => {
  let release;
  const c = boot({ loader: () => new Promise(r => { release = r; }) });
  await c.open();
  c.R.loader = { commit: "7654321", kept: false };
  await c.open();
  release({ commit: "old0000", kept: true });
  await settle();
  assert.ok(c.text().includes("commit 7654321"));
  assert.ok(!c.text().includes("old0000"));
  assert.ok(!c.has("Roll back"));
});

t("a pending preference disables its switch and cannot enqueue duplicate writes", async () => {
  const c = boot();
  let release;
  c.R.prefs_set = () => new Promise(r => { release = r; });
  await c.open();
  await c.click("Tell me about module updates");
  const control = c.find("Tell me about module updates");
  assert.equal(control.getAttribute("aria-busy"), "true");
  assert.equal(control.getAttribute("aria-disabled"), "true");
  await c.click("Tell me about module updates");
  assert.equal(c.sent("prefs_set").length, 1);
  release({ error: "Read-only settings" });
  await settle();
  assert.equal(control.getAttribute("aria-checked"), "false");
  assert.equal(control.getAttribute("aria-disabled"), "false");
  assert.equal(control.hasAttribute("aria-busy"), false);
  assert.ok(c.d.body.textContent.includes("Read-only settings"));
});

t("a malformed save reply does not report an unsaved preference as enabled", async () => {
  const c = boot();
  c.R.prefs_set = () => ({});
  await c.open();
  await c.click("Tell me about module updates");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "false");
  assert.ok(c.d.body.textContent.includes("No answer from Framey"));
});

t("concurrent different preference replies preserve each confirmed value", async () => {
  const c = boot();
  let release;
  c.R.prefs_set = arg => arg.notify_loader === true
    ? new Promise(r => { release = r; })
    : arg.notify_plugins === true ? stateOf({ notify_plugins: true }) : { error: "Cannot save" };
  await c.open();
  await c.click("Tell me about Framey updates");
  await c.click("Tell me about module updates");
  release(stateOf({ notify_loader: true, notify_plugins: false }));
  await settle();
  await c.click("Tell me about module updates");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "true");
});

t("a delayed update check cannot replace a later Modules page", async () => {
  const c = boot({ list: [], store: [] });
  let release;
  c.R.loader_check = () => new Promise(r => { release = r; });
  await c.open();
  await c.click("Check for updates");
  c.FY.pages.modules.render(c.box);
  await settle();
  release(checked());
  await settle();
  assert.ok(c.text().includes("No modules installed"));
  assert.ok(!c.text().includes("Available: v1.2.0"));
});

t("a maintenance window save failure restores the confirmed slider value", async () => {
  const initial = { "maint.enabled_background": false, "maint.window_days": 365 };
  const c = boot({ maint_prefs: arg => arg ? { error: "Maintenance settings could not be saved" } : initial });
  await c.open();
  const slider = c.box.querySelector('[role="slider"][aria-label="Activity window"]');
  slider.dispatchEvent(new c.w.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  slider.dispatchEvent(new c.w.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  await settle();
  assert.deepEqual(c.last("maint_prefs").arg, { "maint.window_days": 370 });
  assert.equal(slider.value, 365);
  assert.equal(slider.getAttribute("aria-valuenow"), "365");
  assert.ok(c.d.body.textContent.includes("Maintenance settings could not be saved"));
});

t("the worker limit is adjustable only when supported and rolls back a refused change", async () => {
  const c = boot({ state_get: stateOf({ workers_max: 3 }) });
  c.R.prefs_set = () => ({ error: "Cannot save worker limit" });
  await c.open();
  const slider = c.box.querySelector('[role="slider"][aria-label="Modules running at once"]');
  assert.ok(slider);
  assert.equal(slider.getAttribute("aria-valuemin"), "1");
  assert.equal(slider.getAttribute("aria-valuemax"), "3");
  slider.dispatchEvent(new c.w.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
  slider.dispatchEvent(new c.w.KeyboardEvent("keyup", { key: "ArrowLeft", bubbles: true }));
  await settle();
  assert.deepEqual(c.last("prefs_set").arg, { workers_max: 2 });
  assert.equal(slider.value, 3);
  const old = boot();
  await old.open();
  assert.equal(old.box.querySelector('[role="slider"][aria-label="Modules running at once"]'), null);
});

t("a missing or malformed candidate never starts an unreviewed loader install", async () => {
  const c = boot({ loader_check: () => checked({ candidate: { commit: "bad" } }) });
  await c.open();
  await c.click("Update");
  assert.equal(c.sent("loader_check").length, 1);
  assert.equal(c.sent("loader_update").length, 0);
  assert.ok(!c.has("Update now"));
});

t("both downgrade confirmations retain the exact checked release identity", async () => {
  const c = boot({ loader_check: () => checked({ loader: { downgrade: true } }) });
  c.R.loader_update = arg => arg.confirm ? { ok: true } : { confirm: "Confirm the older release" };
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  await c.click("Update now");
  assert.deepEqual(c.sent("loader_update").map(x => x.arg), [false, true].map(confirm => ({ confirm, candidate: { tag: "v1.2.0", commit: "abcdef0123456789abcdef0123456789abcdef01" } })));
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
