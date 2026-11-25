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
  panel_status: { mode: "compact", layout: "compact", placements: { compact: true, window: false }, available: true, active: false },
});
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

const boot = (over = {}, trusted = "off", connected = false) => {
  const calls = [];
  const R = Object.assign(defaults(), over);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: trusted, panel_layout: "compact" };
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
  let navigation = null;
  const setNavigation = env.window.__S.K.setPageNavigation;
  const clearNavigation = env.window.__S.K.clearPageNavigation;
  env.window.__S.K.setPageNavigation = value => { navigation = value; return setNavigation(value); };
  env.window.__S.K.clearPageNavigation = () => { navigation = null; return clearNavigation(); };
  const box = connected ? env.document.querySelector(".fy-panel [role=tabpanel]") : FY.el("");
  if (!connected) env.document.body.append(box);
  const sent = m => calls.filter(c => c.method === m);
  const last = m => sent(m).slice(-1)[0];
  const all = () => [...box.querySelectorAll('[role="button"],[role="switch"],[role="option"],[role="slider"]')];
  const scan = read => {
    if (!navigation) return [read()];
    const first = navigation.index;
    const values = [];
    for (let index = 0, count = navigation.count; index < count; index++) {
      navigation.onPage(index);
      values.push(read());
    }
    navigation.onPage(first);
    return values;
  };
  const localFind = (label, n = 0) => {
    let hit = all().filter(e => e.getAttribute("aria-label") === label)[n];
    if (hit || !navigation) return hit;
    const first = navigation.index;
    for (let index = 0, count = navigation.count; index < count; index++) {
      navigation.onPage(index);
      hit = all().filter(e => e.getAttribute("aria-label") === label)[n];
      if (hit) return hit;
    }
    navigation.onPage(first);
  };
  const go = category => {
    if (navigation && navigation.title === category) return;
    for (let depth = 0; navigation && navigation.onBack && navigation.title !== category && depth < 3; depth++) navigation.onBack();
    if (navigation && navigation.title === category) return;
    if (category === "Panel status") { go("Support"); const item = localFind("Panel status"); if (item) item.click(); return; }
    const item = localFind(category);
    if (item) item.click();
  };
  const categories = {
    "Check for updates": "Updates", Update: "Updates", "Release notes": "Updates", History: "Updates", Done: "Updates", "Roll back": "Updates",
    "Panel status": "Support",
    "Check automatically": "Preferences", "Tell me about Framey updates": "Preferences", "Tell me about module updates": "Preferences",
    "Activity window": "Maintenance", "Check project maintenance in the background": "Maintenance", "Modules running at once": "Worker",
    "Developer tools": "Developer", Diagnostics: "Developer", "Reload modules": "Support", "Report to Framey": "Support",
  };
  const find = (label, n = 0) => {
    let hit = localFind(label, n);
    if (!hit && navigation && categories[label]) { go(categories[label]); hit = localFind(label, n); }
    if (!hit) throw new Error("no control '" + label + "' in: " + all().map(e => e.getAttribute("aria-label")).join(" | "));
    return hit;
  };
  const has = label => {
    try { return !!find(label); } catch { return false; }
  };
  const click = async (label, n = 0) => { find(label, n).click(); await settle(); };
  const open = async (category = "Updates") => {
    if (connected) FY.open("#settings");
    else FY.pages.settings.render(box, {});
    await settle();
    if (category) go(category);
    await settle();
  };
  const progress = ev => env.event("_core", "progress", ev);
  return { env, FY, w: env.window, d: env.document, box, calls, R, sent, last, find, has, click, open, all, text: () => scan(() => box.textContent).join(" "), visibleText: () => box.textContent, scan, go, progress };
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
  c.go("Preferences");
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
  c.go("Preferences");
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
  c.go("Preferences");
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
  c.go("Preferences");
  assert.ok(c.text().includes("not protection"));
  assert.ok(c.text().includes("Safe mode is off."));
  assert.ok(c.text().includes("~/.config/framey/safe"));
  assert.ok(c.text().includes("Developer mode is off."));
  assert.ok(c.text().includes("~/.config/framey/developer"));
  assert.ok(!c.has("Safe mode") && !c.has("Developer mode"));
  assert.ok(!c.has("Developer tools") && !c.has("Diagnostics"));
  const d = boot({ state_get: stateOf({ dev: { enabled: true }, safe: true }) });
  await d.open();
  d.go("Preferences");
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

t("trusted_input require blocks synthetic category navigation and update writes", async () => {
  const c = boot({ loader: { commit: "1234567", kept: true } }, "require");
  await c.open(null);
  await c.click("Updates");
  assert.equal(c.sent("loader_done").length, 0);
  assert.equal(c.sent("loader_roll").length, 0);
  assert.equal(c.sent("loader_update").length, 0);
  assert.equal(c.visibleText(), "Updates");
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
  c.go("Support");
  assert.ok(c.text().includes("Modules run inside Steam's UI. Install only ones you trust."));
  assert.equal(c.box.querySelectorAll("b").length, 0);
});

t("Modules page still works next to Settings (shared helpers, one progress listener)", async () => {
  const c = boot({ list: [], store: [] });
  c.FY.pages.modules.render(c.box, {});
  await settle();
  assert.ok(c.text().includes("No modules installed"));
  await c.open();
  c.go("Updates");
  assert.ok(c.has("Check for updates"));
});

t("after a Framey update the restart screen puts focus on Back", async () => {
  const c = boot();
  c.R.loader_update = () => ({ ok: true });
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  assert.strictEqual(c.d.activeElement, c.find("Back"));
});

t("connected bounded update success renders its confirmation and focuses visible Back", async () => {
  const c = boot({ loader_update: { ok: true } }, "off", true);
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  assert.ok(c.box.closest(".fy-panel"));
  assert.ok(c.visibleText().includes("Updated, Framey is restarting"));
  const back = c.d.querySelector('.fy-panel [aria-label="Back"]');
  assert.notEqual(back.style.display, "none");
  assert.equal(c.d.activeElement, back);
  back.click();
  await settle();
  assert.ok(c.has("Check for updates"));
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

t("connected pending check survives Next and refreshes the current Updates page", async () => {
  let release;
  const c = boot({ loader_check: () => new Promise(resolve => { release = resolve; }) }, "off", true);
  await c.open();
  await c.click("Check for updates");
  const next = [...c.d.querySelectorAll('[role="navigation"] [role="button"]')].find(node => node.textContent === "Next");
  next.click();
  await settle();
  assert.ok(c.visibleText().includes("Not checked yet"));
  release(checked());
  await settle();
  assert.ok(c.visibleText().includes("Update available"));
  assert.ok(c.text().includes("Available: v1.2.0"));
  assert.equal(c.sent("loader_check").length, 1);
});

t("connected Update metadata is cached across category reading without opening confirmation", async () => {
  let release;
  const c = boot({ loader_check: () => new Promise(resolve => { release = resolve; }) }, "off", true);
  await c.open();
  await c.click("Update");
  c.go("Preferences");
  assert.equal(c.find("Check automatically").getAttribute("aria-checked"), "false");
  release(checked());
  await settle();
  assert.ok(c.visibleText().includes("Check automatically"));
  assert.ok(!c.visibleText().includes("Update Framey?"));
  c.go("Updates");
  assert.ok(c.text().includes("Available: v1.2.0"));
  assert.equal(c.sent("loader_update").length, 0);
});

t("connected newer checks supersede older replies after internal pagination", async () => {
  const releases = [];
  const c = boot({ loader_check: () => new Promise(resolve => { releases.push(resolve); }) }, "off", true);
  await c.open();
  await c.click("Check for updates");
  c.text();
  await c.click("Check for updates");
  assert.equal(releases.length, 2);
  releases[1](checked({ candidate: { tag: "v1.3.0", commit: "b".repeat(40) } }));
  await settle();
  releases[0](checked());
  await settle();
  const text = c.text();
  assert.ok(text.includes("Available: v1.3.0"));
  assert.ok(!text.includes("Available: v1.2.0"));
});

t("connected check completion cannot replace a bounded operation started on another Updates screen", async () => {
  let releaseCheck, releaseDone;
  const c = boot({
    loader: { commit: "1234567", kept: true },
    loader_check: () => new Promise(resolve => { releaseCheck = resolve; }),
    loader_done: () => new Promise(resolve => { releaseDone = resolve; }),
  }, "off", true);
  await c.open();
  await c.click("Check for updates");
  await c.click("Done");
  assert.ok(c.text().includes("Progress details"));
  releaseCheck(checked());
  await settle();
  assert.ok(c.text().includes("Progress details"));
  releaseDone({ ok: true });
  await settle();
  assert.ok(c.text().includes("Previous version removed"));
});

t("connected pending Update metadata cannot repaint a different core route", async () => {
  let release;
  const c = boot({ loader_check: () => new Promise(resolve => { release = resolve; }) }, "off", true);
  await c.open();
  await c.click("Update");
  c.FY.open("#plugins");
  await settle();
  const destination = c.visibleText();
  release(checked());
  await settle();
  assert.equal(c.w.__S.K.tab(), "#plugins");
  assert.equal(c.visibleText(), destination);
  assert.equal(c.sent("loader_update").length, 0);
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
  c.go("Maintenance");
  c.find("Activity window");
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
  c.go("Worker");
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

const panelSection = c => { c.go("Panel status"); return c.box.querySelector('[role="group"][aria-label="Panel diagnostics"]'); };
const panelEvent = (c, status) => c.env.event("_core", "panel_mode", status);
const modernPanel = (mode, over = {}) => Object.assign({ mode, layout: mode, placements: { compact: true, window: true }, available: true, active: false }, over);

t("Settings starts with paged categories and no placement decision", async () => {
  const c = boot();
  await c.open(null);
  assert.deepEqual(c.all().map(node => node.getAttribute("aria-label")), ["Updates"]);
  assert.deepEqual(c.scan(() => c.all().map(node => node.getAttribute("aria-label"))).flat(), ["Updates", "Preferences", "Decky", "Maintenance", "Worker", "Support"]);
  assert.ok(!c.box.textContent.includes("Above bottom bar"));
  const next = [...c.d.querySelectorAll('[role="navigation"] [role="button"]')].find(node => node.textContent === "Next");
  next.click();
  await settle();
  assert.ok(c.has("Preferences"));
  await c.click("Preferences");
  assert.equal(c.all().length, 1);
  assert.equal(c.find("Check automatically").getAttribute("aria-checked"), "false");
  assert.equal(c.sent("panel_status").length, 0);
  assert.equal(c.sent("panel_mode").length, 0);
});

t("damaged Settings separates recovery text from its repair control screen", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  await c.open("Support");
  const screens = c.scan(() => ({ text: c.visibleText(), repair: c.all().some(node => node.getAttribute("aria-label") === "Repair settings") }));
  assert.ok(screens.some(screen => screen.text.includes("Settings file is damaged") && !screen.repair));
  assert.ok(screens.some(screen => screen.repair && !screen.text.includes("Settings file is damaged")));
});

t("ordinary Options categories never expose or request panel mode choices", async () => {
  const c = boot();
  await c.open(null);
  for (const category of ["Updates", "Preferences", "Decky", "Maintenance", "Worker", "Support"]) {
    c.go(category);
    const screens = c.scan(() => c.visibleText());
    assert.ok(screens.every(text => !text.includes("Above bottom bar") && !text.includes("Beside active app")));
  }
  assert.equal(c.sent("panel_status").length, 0);
  assert.equal(c.sent("panel_mode").length, 0);
});

t("Support panel diagnostics are lazy read-only pages with manual Retry", async () => {
  const c = boot();
  await c.open("Support");
  assert.equal(c.sent("panel_status").length, 0);
  await c.click("Panel status");
  await settle();
  assert.ok(c.text().includes("Panel host: compact"));
  assert.ok(c.scan(() => panelSection(c).querySelectorAll('[role="option"]').length).every(count => count === 0));
  await c.click("Retry");
  assert.equal(c.sent("panel_status").length, 2);
  assert.equal(c.sent("panel_mode").length, 0);
});

t("legacy panel hosts are described without claiming active-app placement", async () => {
  for (const [mode, expected] of [["embedded", "Recovery"], ["dashboard", "Steam dashboard"], ["floating", "floating"]]) {
    const c = boot({ panel_status: { mode, available: true, active: true } });
    await c.open("Panel status");
    assert.ok(c.text().includes(expected));
    assert.ok(!c.text().includes("Beside active app"));
    assert.equal(c.sent("panel_mode").length, 0);
  }
});

t("an unavailable diagnostic read stays bounded and Retry can recover it", async () => {
  const c = boot({ panel_status: { error: "unknown" } });
  await c.open("Panel status");
  assert.ok(c.text().includes("unavailable"));
  assert.ok(!c.text().includes("unknown"));
  c.R.panel_status = { mode: "embedded", available: false, active: true };
  await c.click("Retry");
  assert.ok(c.text().includes("Recovery"));
  assert.equal(c.sent("panel_mode").length, 0);
  assert.ok(c.has("Check for updates"));
});

t("a pending diagnostic read never blocks an unrelated preference save", async () => {
  let read;
  const c = boot({ panel_status: () => new Promise(resolve => { read = resolve; }) });
  c.R.prefs_set = () => stateOf({ notify_plugins: true });
  await c.open("Panel status");
  assert.equal(c.find("Retry").getAttribute("aria-disabled"), "true");
  await c.click("Retry");
  assert.equal(c.sent("panel_status").length, 1);
  await c.click("Tell me about module updates");
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "true");
  read(modernPanel("window"));
  await settle();
  c.go("Panel status");
  assert.ok(c.text().includes("Panel host: window"));
  assert.equal(c.sent("panel_mode").length, 0);
});

t("malformed diagnostic status never replaces the confirmed host", async () => {
  for (const reply of [{}, { ok: true }, { mode: "compact", active: true }, modernPanel("invalid"), modernPanel("compact", { placements: [] }), modernPanel("compact", { placements: { compact: true, window: "yes" } }), modernPanel("compact", { layout: "dashboard" }), modernPanel("compact", { pending: true, requested_mode: "invalid" }), modernPanel("compact", { input_notice: {} })]) {
    const c = boot();
    await c.open("Panel status");
    c.R.panel_status = reply;
    await c.click("Retry");
    assert.ok(c.text().includes("Panel host: compact"));
    assert.ok(c.text().includes("did not confirm"));
  }
});

t("pending host status can be confirmed by an event or one manual Retry without polling", async () => {
  const c = boot({ panel_status: modernPanel("window", { requested_mode: "compact", pending: true }) });
  await c.open("Panel status");
  assert.ok(c.text().includes("pending"));
  panelEvent(c, modernPanel("compact"));
  await settle();
  assert.ok(c.text().includes("Panel host: compact"));
  c.R.panel_status = modernPanel("window");
  await c.click("Retry");
  assert.ok(c.text().includes("Panel host: window"));
  assert.equal(c.sent("panel_status").length, 2);
  c.env.clock.tick(60000);
  await settle();
  assert.equal(c.sent("panel_status").length, 2);
});

t("diagnostic pointer notices remain supplied, bounded, and rendered as text", async () => {
  const notice = "<img src=x onerror=alert(1)>" + "x".repeat(300);
  const c = boot({ panel_status: modernPanel("compact", { input_notice: notice }) });
  await c.open("Panel status");
  assert.ok(c.scan(() => panelSection(c).textContent).join("").includes(notice.slice(0, 240)));
  assert.ok(!c.scan(() => panelSection(c).textContent).join("").includes(notice.slice(0, 241)));
  assert.equal(panelSection(c).querySelector("img"), null);
});

t("only validated private layouts update presentation from diagnostic replies or events", async () => {
  const c = boot();
  const layouts = [];
  const original = c.w.__S.K.setPanelLayout;
  c.w.__S.K.setPanelLayout = layout => { layouts.push(layout); return original(layout); };
  await c.open("Panel status");
  assert.deepEqual(layouts, ["compact"]);
  panelEvent(c, modernPanel("window"));
  await settle();
  assert.deepEqual(layouts, ["compact", "window"]);
  panelEvent(c, modernPanel("compact", { layout: "dashboard" }));
  await settle();
  assert.deepEqual(layouts, ["compact", "window"]);
  assert.ok(c.text().includes("Panel host: window"));
});

t("a missing presentation helper keeps diagnostics and Options usable", async () => {
  const c = boot();
  delete c.w.__S.K.setPanelLayout;
  await c.open("Panel status");
  assert.ok(c.text().includes("Panel host: compact"));
  assert.ok(c.has("Check for updates"));
});

t("diagnostic replies and events after leaving Options cannot repaint the destination", async () => {
  let finish;
  const c = boot({ panel_status: () => new Promise(resolve => { finish = resolve; }), list: [], store: [] });
  await c.open("Panel status");
  c.FY.pages.settings.leave();
  c.FY.pages.modules.render(c.box);
  await settle();
  const destination = c.text();
  const layout = c.d.querySelector(".fy-panel").dataset.fyLayout;
  finish(modernPanel("compact"));
  panelEvent(c, modernPanel("window"));
  await settle();
  assert.equal(c.text(), destination);
  assert.equal(c.d.querySelector(".fy-panel").dataset.fyLayout, layout);
  assert.ok(c.text().includes("No modules installed"));
});

t("panel subscriptions are replaced on rerender and removed on leave/dispose", async () => {
  const c = boot();
  const baseline = c.FY.diag().subs;
  await c.open();
  assert.equal(c.FY.diag().subs, baseline + 1);
  await c.open();
  assert.equal(c.FY.diag().subs, baseline + 1);
  c.FY.pages.settings.leave();
  assert.equal(c.FY.diag().subs, baseline);
  await c.open();
  c.FY.pages.settings.dispose();
  assert.equal(c.FY.diag().subs, baseline);
});

const captureReports = c => {
  const reports = [];
  c.w.__S.K.report = context => {
    reports.push(JSON.parse(JSON.stringify(context)));
    return Promise.resolve({ closed: true });
  };
  return reports;
};

t("Settings offers one manual Report to Framey action without opening a draft automatically", async () => {
  const c = boot();
  const reports = captureReports(c);
  await c.open();
  c.find("Report to Framey");
  assert.equal(c.all().filter(node => node.getAttribute("aria-label") === "Report to Framey").length, 1);
  assert.equal(reports.length, 0);
  const page = c.text();
  await c.click("Report to Framey");
  assert.deepEqual(reports, [{ error: "", code: "settings_support" }]);
  assert.equal(c.text(), page);
  assert.equal(c.sent("loader_update").length, 0);
});

t("a Settings read failure exposes a bounded manual report context beside Retry", async () => {
  const error = "Read failure " + "x".repeat(500);
  const c = boot({ state_get: { error } });
  const reports = captureReports(c);
  await c.open();
  assert.ok(c.has("Retry") && c.has("Report to Framey"));
  assert.equal(reports.length, 0);
  await c.click("Report to Framey");
  assert.equal(reports[0].code, "settings_error");
  assert.equal(reports[0].error, error.slice(0, 300));
  assert.equal(reports[0].plugin, undefined);
});

t("a failed loader check has exactly one manual reporting action with the actual error", async () => {
  const c = boot({ loader_check: { loader: { state: "error", msg: "Release lookup unavailable" } } });
  const reports = captureReports(c);
  await c.open();
  await c.click("Check for updates");
  assert.equal(reports.length, 0);
  c.find("Report to Framey");
  assert.equal(c.all().filter(node => node.getAttribute("aria-label") === "Report to Framey").length, 1);
  await c.click("Report to Framey");
  assert.deepEqual(reports, [{ error: "Release lookup unavailable", code: "loader_check_failed" }]);
});

t("a failed loader update can be reported manually while keeping Back and the pinned update", async () => {
  const c = boot({ loader_update: { error: "Framey release changed; check for updates again" } });
  const reports = captureReports(c);
  await c.open();
  await c.click("Update");
  await c.click("Update now");
  assert.equal(reports.length, 0);
  assert.ok(c.has("Back") && c.has("Report to Framey"));
  await c.click("Report to Framey");
  assert.deepEqual(reports, [{ error: "Framey release changed; check for updates again", code: "loader_update_failed" }]);
  assert.deepEqual(c.last("loader_update").arg.candidate, { tag: "v1.2.0", commit: "abcdef0123456789abcdef0123456789abcdef01" });
  await c.click("Back");
  assert.ok(c.has("Check for updates"));
});

t("rollback and backup-removal errors route manual reports through Framey's preview helper", async () => {
  for (const [method, action] of [["loader_roll", "Roll back"], ["loader_done", "Done"]]) {
    const c = boot({ loader: { commit: "1234567", kept: true }, [method]: { error: "Recovery operation could not finish" } });
    const reports = captureReports(c);
    await c.open();
    await c.click(action);
    if (method === "loader_roll") await c.click(action);
    assert.equal(reports.length, 0);
    await c.click("Report to Framey");
    assert.deepEqual(reports, [{ error: "Recovery operation could not finish", code: method + "_failed" }]);
    assert.ok(c.has("Back"));
  }
});

t("the existing Settings support action picks up a later save error without extra report controls", async () => {
  const c = boot({ prefs_set: { error: "Settings write failed" } });
  const reports = captureReports(c);
  await c.open();
  await c.click("Tell me about module updates");
  assert.equal(reports.length, 0);
  c.find("Report to Framey");
  assert.equal(c.all().filter(node => node.getAttribute("aria-label") === "Report to Framey").length, 1);
  await c.click("Report to Framey");
  assert.deepEqual(reports, [{ error: "Settings write failed", code: "settings_save_failed" }]);
  assert.equal(c.find("Tell me about module updates").getAttribute("aria-checked"), "false");
});

t("diagnostic failures populate manual reports only after explicit activation", async () => {
  const message = "Panel status could not be checked.";
  const c = boot({ panel_status: { error: message } });
  const reports = captureReports(c);
  await c.open("Panel status");
  assert.equal(reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(reports, [{ error: message, code: "panel_status_failed" }]);
  assert.equal(c.sent("panel_mode").length, 0);
});

t("a failed optional restart-notice cache records a fixed diagnostic and keeps the success screen", async () => {
  const c = boot({ loader_update: { ok: true } });
  const diagnostics = [];
  c.w.__S.K.diagnostic = code => diagnostics.push(code);
  await c.open();
  Object.defineProperty(c.w, "sessionStorage", { configurable: true, get() { throw new Error("Storage unavailable"); } });
  await c.click("Update");
  await c.click("Update now");
  assert.ok(c.text().includes("Updated, Framey is restarting"));
  assert.ok(c.has("Back"));
  assert.deepEqual(diagnostics, ["core.storage_failed"]);
});

t("an unavailable or rejecting report helper preserves Settings and shows Unavailable", async () => {
  for (const mode of ["missing", "rejected"]) {
    const c = boot();
    if (mode === "rejected") c.w.__S.K.report = () => Promise.reject(new Error("Preview unavailable"));
    else delete c.w.__S.K.report;
    await c.open();
    c.go("Support");
    const page = c.text();
    await c.click("Report to Framey");
    assert.equal(c.text(), page);
    assert.ok(c.d.body.textContent.includes("Report to Framey: Unavailable"));
    assert.ok(c.has("Check for updates"));
  }
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
