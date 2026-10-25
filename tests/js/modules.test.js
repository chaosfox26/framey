"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const mod = (id, name, extra) => Object.assign({ id, name, version: "1.0.0", commit: id + "abc123".slice(0, 6), enabled: true, nosource: "", pending: false, compat: { badge: "ok", msg: "" }, fail: null, stuck: false, linked: false }, extra);
const defaults = () => ({
  list: [mod("a", "Alpha"), mod("b", "Beta")],
  store: [],
  state_get: { order: ["a", "b"], sort: "custom", hidden: [], frozen: {}, prefs: { channel: "stable" }, safe: false },
});
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

const boot = (over = {}, trusted = "off", files = FILES) => {
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
    loadFiles: files,
  });
  const FY = env.window.Framey;
  const box = FY.el("");
  env.document.body.append(box);
  const sent = m => calls.filter(c => c.method === m);
  const last = m => sent(m).slice(-1)[0];
  const open = async which => {
    FY.pages[which].render(box, {});
    await settle();
  };
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
  const text = () => box.textContent;
  const progress = ev => env.event("_core", "progress", ev);
  return { env, FY, w: env.window, d: env.document, box, calls, R, sent, last, open, find, has, click, text, all, progress };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("renders cards in the saved order with name, version, commit and compatibility", async () => {
  const c = boot();
  c.R.state_get = Object.assign(defaults().state_get, { order: ["b", "a"] });
  await c.open("modules");
  const titles = [...c.box.querySelectorAll("div")].map(e => e.textContent).filter(s => /^(Alpha|Beta)  v1\.0\.0$/.test(s));
  assert.deepEqual(titles, ["Beta  v1.0.0", "Alpha  v1.0.0"]);
  assert.ok(c.text().includes("Compatible"));
  assert.ok(c.text().includes("commit "));
  assert.deepEqual(c.calls.map(x => x.method).sort(), ["list", "maint_get", "maint_prefs", "state_get", "status", "store"]);
});

t("unavailable list shows the loader error, nothing else breaks", async () => {
  const c = boot({ list: { error: "loader busy" } });
  await c.open("modules");
  assert.ok(c.text().includes("loader busy"));
});

t("module names are plain text, never markup", async () => {
  const c = boot({ list: [mod("a", "<img src=x onerror=alert(1)>")] });
  await c.open("modules");
  assert.equal(c.box.querySelectorAll("img").length, 0);
  assert.ok(c.text().includes("<img src=x onerror=alert(1)>"));
});

t("no timer or listener is created by the page itself", async () => {
  const c = boot();
  const before = c.env.clock.pending();
  await c.open("modules");
  assert.equal(c.env.clock.pending(), before);
});

t("sort toggle: Custom to Name and back, and Move buttons exist only in custom order", async () => {
  const c = boot();
  await c.open("modules");
  assert.ok(c.has("Sort: Custom"));
  await c.click("More");
  assert.ok(c.has("Move up") && c.has("Move down"));
  c.R.mod_sort = a => Object.assign(defaults().state_get, { sort: a });
  await c.click("Sort: Custom");
  assert.deepEqual(c.last("mod_sort").arg, "name");
  assert.ok(c.has("Sort: Name"));
  assert.ok(!c.has("Move up") && !c.has("Move down"));
  await c.click("Sort: Name");
  assert.deepEqual(c.last("mod_sort").arg, "custom");
  assert.ok(c.has("Sort: Custom"));
});

t("Move up and Move down send a delta, re-order the cards and keep the card open", async () => {
  const c = boot();
  await c.open("modules");
  await c.click("More");
  assert.equal(c.find("Move up").getAttribute("aria-disabled"), "true");
  assert.equal(c.find("Move down").getAttribute("aria-disabled"), "false");
  c.R.mod_order = () => Object.assign(defaults().state_get, { order: ["b", "a"] });
  await c.click("Move down");
  assert.deepEqual(c.last("mod_order").arg, { id: "a", delta: 1 });
  const titles = [...c.box.querySelectorAll("div")].map(e => e.textContent).filter(s => /^(Alpha|Beta)  v1\.0\.0$/.test(s));
  assert.deepEqual(titles, ["Beta  v1.0.0", "Alpha  v1.0.0"]);
  assert.ok(c.has("Move up") && c.has("Less"));
  assert.equal(c.find("Move down").getAttribute("aria-disabled"), "true");
  assert.equal(c.find("Move up").getAttribute("aria-disabled"), "false");
  assert.equal(c.sent("list").length, 1);
});

t("controls are keyboard operable: Enter and Space press them, they are real focus targets", async () => {
  const c = boot();
  await c.open("modules");
  const b = c.find("More");
  assert.equal(b.getAttribute("tabindex"), "0");
  b.dispatchEvent(new c.w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await settle();
  assert.ok(c.has("Less"));
  c.find("Less").dispatchEvent(new c.w.KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
  await settle();
  assert.ok(c.has("More"));
});

t("hide and show keep the module (not disabled) and mark it Hidden", async () => {
  const c = boot();
  await c.open("modules");
  await c.click("More");
  assert.ok(!c.text().includes("Hidden"));
  c.R.mod_hide = a => Object.assign(defaults().state_get, { hidden: a.hidden ? [a.id] : [] });
  await c.click("Hide");
  assert.deepEqual(c.last("mod_hide").arg, { id: "a", hidden: true });
  assert.ok(c.text().includes("Hidden: not shown in the side menu"));
  assert.ok(c.has("Show") && !c.has("Hide"));
  assert.equal(c.sent("disable").length, 0);
  assert.equal(c.find("Enabled").getAttribute("aria-checked"), "true");
  await c.click("Show");
  assert.deepEqual(c.last("mod_hide").arg, { id: "a", hidden: false });
  assert.ok(!c.text().includes("Hidden:"));
});

t("freeze shows the version it froze at; Unfreeze removes it", async () => {
  const frozen = () => Object.assign(defaults().state_get, { frozen: { a: { at: 5, version: "1.0.0" } } });
  const c = boot({ state_get: frozen() });
  await c.open("modules");
  assert.ok(c.text().includes("Frozen at v1.0.0"));
  await c.click("More");
  assert.ok(c.has("Unfreeze"));
  c.R.mod_freeze = () => defaults().state_get;
  await c.click("Unfreeze");
  assert.deepEqual(c.last("mod_freeze").arg, { id: "a", frozen: false });
  assert.ok(!c.text().includes("Frozen at"));
  c.R.mod_freeze = () => frozen();
  await c.click("Freeze");
  assert.deepEqual(c.last("mod_freeze").arg, { id: "a", frozen: true });
  assert.ok(c.text().includes("Frozen at v1.0.0"));
});

t("a manual update of a frozen module asks to unfreeze first, and does nothing if cancelled", async () => {
  const c = boot({ state_get: Object.assign(defaults().state_get, { frozen: { a: { at: 5, version: "1.0.0" } } }) });
  c.R.mod_freeze = () => defaults().state_get;
  c.R.update = () => ({ ok: true, version: "1.1.0" });
  await c.open("modules");
  await c.click("Update");
  assert.ok(c.text().includes("Alpha is frozen at v1.0.0"));
  assert.equal(c.sent("update").length, 0);
  await c.click("Cancel");
  assert.equal(c.sent("update").length, 0);
  assert.ok(c.has("Sort: Custom"));
  await c.click("Update");
  await c.click("Unfreeze and continue");
  assert.deepEqual(c.last("mod_freeze").arg, { id: "a", frozen: false });
  assert.equal(c.sent("update").length, 0);
  await c.click("Install");
  assert.equal(c.last("update").arg, "a");
  assert.ok(c.text().includes("Updated Alpha to v1.1.0"));
});

t("Reload is available on every card and reloads only that module", async () => {
  const c = boot();
  c.R.reload_one = () => ({ ok: true });
  await c.open("modules");
  await c.click("More", 1);
  await c.click("Reload");
  assert.deepEqual(c.sent("reload_one").map(x => x.arg), ["b"]);
});

t("check for updates marks cards and summarises, update then runs and shows the result", async () => {
  const c = boot();
  c.R.check = () => ({ checked: { a: { state: "update", latest: "abcdef1" }, b: { state: "current" } } });
  c.R.update = () => ({ ok: true, version: "2.0.0" });
  await c.open("modules");
  await c.click("Check for updates");
  assert.ok(c.text().includes("1 update available"));
  assert.ok(c.text().includes("Update available: abcdef1"));
  assert.ok(c.text().includes("Up to date"));
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("Updated Alpha to v2.0.0"));
  await c.click("Back");
  assert.ok(c.has("Sort: Custom"));
  assert.ok(!c.text().includes("Update available: abcdef1"));
});

const checked = notes => ({ checked: { a: { state: "update", latest: "v2.0.0", notes }, b: { state: "current" } } });

t("single update: the first tap only asks, with versions and notes; nothing is installed", async () => {
  const c = boot();
  c.R.check = () => checked("Fixes the fan curve");
  c.R.update = () => ({ ok: true, version: "2.0.0" });
  await c.open("modules");
  await c.click("Check for updates");
  await c.click("Update");
  assert.equal(c.sent("update").length, 0);
  assert.ok(c.text().includes("Alpha: v1.0.0 to v2.0.0"));
  assert.ok(c.text().includes("Release notes:\nFixes the fan curve"));
  assert.ok(c.has("Install") && c.has("Cancel"));
  ["Install", "Cancel"].forEach(l => assert.ok(c.find(l).classList.contains("fy-c") && c.find(l).className.includes("fy-b")));
  assert.equal(c.sent("check").length, 1);
});

t("single update: hostile release notes stay text and are cut to 600 characters", async () => {
  const c = boot();
  const evil = "<img src=x onerror=alert(1)><script>alert(2)</script>";
  c.R.check = () => checked(evil + "x".repeat(900));
  await c.open("modules");
  await c.click("Check for updates");
  await c.click("Update");
  assert.equal(c.box.querySelectorAll("img,script").length, 0);
  assert.ok(c.text().includes(evil));
  const n = c.text().split("Release notes:\n")[1];
  assert.ok(n.startsWith(evil + "x".repeat(600 - evil.length) + "...Install"));
});

t("single update: without notes or a check there is no notes block, and the target reads unknown", async () => {
  const c = boot();
  await c.open("modules");
  await c.click("Update");
  assert.ok(!c.text().includes("Release notes:"));
  assert.ok(c.text().includes("Alpha: v1.0.0 to unknown"));
  assert.equal(c.sent("update").length, 0);
});

t("single update: Cancel restores the row and never installs", async () => {
  const c = boot();
  c.R.check = () => checked("n");
  await c.open("modules");
  await c.click("Check for updates");
  await c.click("Update");
  await c.click("Cancel");
  assert.equal(c.sent("update").length, 0);
  assert.ok(c.has("Sort: Custom") && c.has("Update"));
  assert.ok(c.text().includes("Update available: v2.0.0"));
});

t("single update: only the Install tap installs, once, and a second tap is ignored", async () => {
  const c = boot();
  c.R.check = () => checked("n");
  let release;
  c.R.update = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("Check for updates");
  await c.click("Update");
  const install = c.find("Install");
  install.click();
  install.click();
  await settle();
  install.click();
  await settle();
  assert.equal(c.sent("update").length, 1);
  assert.deepEqual(c.last("update").arg, "a");
  release({ ok: true, version: "2.0.0" });
  await settle();
  assert.ok(c.text().includes("Updated Alpha to v2.0.0"));
  assert.equal(c.sent("update").length, 1);
});

t("check failure is shown with the loader text", async () => {
  const c = boot();
  c.R.check = () => ({ checked: { a: { state: "error", msg: "GitHub rate limit reached" } } });
  await c.open("modules");
  await c.click("Check for updates");
  assert.ok(c.text().includes("1 check failed"));
  assert.ok(c.text().includes("Check failed: GitHub rate limit reached"));
});

const plan = () => ({
  items: [
    { id: "a", name: "Alpha", from: "1.0.0", to: "1.1.0", eligible: true, reason: "" },
    { id: "b", name: "Beta", from: "0.9.0", to: "abc1234", eligible: true, reason: "" },
    { id: "c", name: "Gamma", from: "1.0.0", to: "", eligible: false, reason: "frozen" },
  ],
});

t("Update all previews names with installed and target versions, then applies only the eligible ones", async () => {
  const c = boot();
  c.R.batch_plan = plan;
  await c.open("modules");
  await c.click("Update all");
  assert.equal(c.sent("batch_plan").length, 1);
  const s = c.text();
  assert.ok(s.includes("2 modules will be updated"));
  assert.ok(s.includes("Alpha: v1.0.0 to v1.1.0"));
  assert.ok(s.includes("Beta: v0.9.0 to abc1234"));
  assert.ok(s.includes("Gamma: not included. frozen"));
  assert.equal(c.sent("batch_apply").length, 0);
  assert.ok(c.has("Update 2"));
  let release;
  c.R.batch_apply = () => new Promise(r => { release = r; });
  await c.click("Update 2");
  assert.deepEqual(c.last("batch_apply").arg, { ids: ["a", "b"] });
  assert.ok(c.text().includes("Updating modules"));
  release({ results: [], ok: 0, failed: 0 });
  await settle();
});

t("nothing eligible: the Update button is disabled and the apply call can not be made", async () => {
  const c = boot();
  c.R.batch_plan = () => ({ items: [{ id: "c", name: "Gamma", from: "1", to: "", eligible: false, reason: "current" }] });
  await c.open("modules");
  await c.click("Update all");
  assert.ok(c.text().includes("Nothing to update"));
  assert.equal(c.find("Update 0").getAttribute("aria-disabled"), "true");
  await c.click("Update 0");
  assert.equal(c.sent("batch_apply").length, 0);
});

t("batch results: every module has its own row, a failure never hides the successes", async () => {
  const c = boot();
  c.R.batch_plan = plan;
  c.R.batch_apply = () => ({
    results: [
      { id: "a", name: "Alpha", state: "ok", from: "1.0.0", to: "1.1.0" },
      { id: "b", name: "Beta", state: "failed", from: "0.9.0", to: "", error: "download failed: timeout" },
      { id: "d", name: "Delta", state: "current", from: "1", to: "1" },
      { id: "e", name: "Echo", state: "skipped", reason: "incompatible: needs Framey 2" },
    ],
    ok: 1,
    failed: 1,
  });
  await c.open("modules");
  await c.click("Update all");
  await c.click("Update 2");
  const s = c.text();
  assert.ok(s.includes("1 updated, 1 failed"));
  assert.ok(s.includes("Alpha: updated to v1.1.0"));
  assert.ok(s.includes("Beta: failed. download failed: timeout"));
  assert.ok(s.includes("Delta: already up to date"));
  assert.ok(s.includes("Echo: skipped. incompatible: needs Framey 2"));
  const rows = [...c.box.querySelectorAll("div")].filter(e => /^(Alpha|Beta|Delta|Echo): /.test(e.textContent) && e.children.length === 0);
  assert.equal(rows.length, 4);
  assert.equal(rows.find(e => e.textContent.startsWith("Beta")).style.color, "rgb(255, 122, 122)");
  assert.equal(rows.find(e => e.textContent.startsWith("Alpha")).style.color, "rgb(122, 223, 154)");
  await c.click("Back");
  assert.ok(c.has("Sort: Custom"));
});

t("a whole-batch error is readable and leaves Back available", async () => {
  const c = boot();
  c.R.batch_plan = plan;
  c.R.batch_apply = () => ({ error: "Busy: another check or update is running" });
  await c.open("modules");
  await c.click("Update all");
  await c.click("Update 2");
  assert.ok(c.text().includes("Failed: Busy: another check or update is running"));
  assert.ok(c.has("Back"));
});

t("progress: steps and states are listed, a percent appears only when measured is real", async () => {
  const c = boot();
  c.R.batch_plan = plan;
  let release;
  c.R.batch_apply = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("Update all");
  await c.click("Update 2");
  const ev = (stage, index, state, measured, extra) => Object.assign({ job: 1, id: "a", stage, index, total: 6, label: stage[0].toUpperCase() + stage.slice(1), state, measured, error: null }, extra);
  c.progress(ev("check", 1, "running", null));
  assert.ok(c.text().includes("Alpha: Step 1 of 6: Check"));
  c.progress(ev("check", 1, "ok", null));
  c.progress(ev("download", 2, "running", null));
  assert.ok(c.text().includes("Step 2 of 6: Download"));
  assert.ok(c.text().includes("Check - Done"));
  assert.ok(c.text().includes("Download - Running"));
  assert.ok(!/\d+%/.test(c.text()));
  const bar = () => [...c.box.querySelectorAll("div")].find(e => e.style.height === "14px" && e.style.borderRadius === "7px");
  assert.equal(bar().style.display, "none");
  c.progress(ev("download", 2, "running", { done: 50, total: 200 }));
  assert.ok(c.text().includes("25%"));
  assert.equal(bar().style.display, "block");
  assert.equal(bar().firstChild.style.width, "25%");
  c.progress(ev("download", 2, "running", { done: 10, total: 0 }));
  assert.ok(!/\d+%/.test(c.text()));
  assert.equal(bar().style.display, "none");
  c.progress(ev("download", 2, "running", { done: 999, total: 10 }));
  assert.ok(c.text().includes("100%"));
  c.progress(ev("download", 2, "ok", null));
  assert.ok(!/\d+%/.test(c.text()));
  c.progress(ev("verify", 3, "fail", null, { error: "hash mismatch" }));
  assert.ok(c.text().includes("Verify - Failed: hash mismatch"));
  c.progress({ job: 2, id: "b", stage: "check", index: 1, total: 6, label: "Check", state: "running", measured: null, error: null });
  assert.ok(c.text().includes("Beta: Step 1 of 6: Check"));
  assert.ok(!c.text().includes("Download - "));
  c.progress({ nonsense: true });
  c.progress(null);
  release({ results: [], ok: 0, failed: 0 });
  await settle();
});

t("progress events are ignored once the job is over", async () => {
  const c = boot();
  c.R.update = () => ({ ok: true });
  await c.open("modules");
  await c.click("Update");
  await c.click("Install");
  const before = c.text();
  c.progress({ job: 9, id: "a", stage: "check", index: 1, total: 6, label: "Check", state: "running", measured: null, error: null });
  assert.equal(c.text(), before);
});

const versions = () => ({
  versions: [
    { tag: "v2.0.0", version: "2.0.0", commit: "c2", prerelease: false, published: "2026-09-01T10:00:00Z", current: false, compat: { badge: "unknown", msg: "Checked before install" }, downgrade: false, eligible: true, reason: "", label: "" },
    { tag: "v1.0.0", version: "1.0.0", commit: "c1", prerelease: false, published: "2026-08-01T10:00:00Z", current: true, compat: { badge: "ok", msg: "" }, downgrade: false, eligible: false, reason: "Installed: use Repair to reinstall it", label: "" },
    { tag: "v0.9.0", version: "0.9.0", commit: "c0", prerelease: false, published: "2026-07-01T10:00:00Z", current: false, compat: { badge: "unknown", msg: "Checked before install" }, downgrade: true, eligible: true, reason: "", label: "" },
    { tag: "v2.1.0-rc.1", version: "2.1.0-rc.1", commit: "cr", prerelease: true, published: "", current: false, compat: { badge: "unknown", msg: "" }, downgrade: false, eligible: false, reason: "Prerelease: switch the channel to Prerelease first", label: "Prerelease" },
  ],
  more: false,
});

t("version list shows installed, older and blocked entries; notes show 'No release notes' when empty", async () => {
  const c = boot();
  c.R.ver_list = versions;
  c.R.notes = () => ({ notes: "", url: "" });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  assert.deepEqual(c.last("ver_list").arg, { id: "a", page: 1 });
  assert.ok(c.text().includes("Versions of Alpha"));
  assert.ok(c.has("1.0.0  installed"));
  assert.ok(c.has("2.1.0-rc.1  Prerelease"));
  assert.ok(c.text().includes("2026-09-01"));
  assert.ok(c.text().includes("Older version"));
  await c.click("2.0.0");
  await c.click("Release notes");
  assert.deepEqual(c.last("notes").arg, { id: "a", tag: "v2.0.0" });
  assert.ok(c.text().includes("No release notes"));
  await c.click("Back");
  assert.ok(c.has("Install this version"));
  await c.click("Back");
  assert.ok(c.has("2.0.0"));
});

t("a blocked version explains why and its install button is disabled", async () => {
  const c = boot();
  c.R.ver_list = versions;
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.1.0-rc.1  Prerelease");
  assert.ok(c.text().includes("Prerelease: switch the channel to Prerelease first"));
  assert.equal(c.find("Install this version").getAttribute("aria-disabled"), "true");
  await c.click("Install this version");
  assert.equal(c.sent("ver_install").length, 0);
});

t("installing a newer version needs no confirmation and reports the result", async () => {
  const c = boot();
  c.R.ver_list = versions;
  c.R.ver_install = () => ({ ok: "a", version: "2.0.0" });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Install this version");
  assert.deepEqual(c.last("ver_install").arg, { id: "a", tag: "v2.0.0", confirm: false });
  assert.ok(c.text().includes("Installed 2.0.0"));
});

t("a downgrade explains the migration limits and needs an explicit confirm", async () => {
  const c = boot();
  c.R.ver_list = versions;
  c.R.ver_install = a => ({ ok: "a", version: "0.9.0" });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("0.9.0");
  assert.ok(c.text().includes("Data changes made by newer versions are not undone"));
  await c.click("Go back to this version");
  assert.equal(c.sent("ver_install").length, 0);
  assert.ok(c.text().includes("data migrations are not undone"));
  assert.ok(c.text().includes("The current copy is kept until you remove the saved version."));
  await c.click("Cancel");
  assert.equal(c.sent("ver_install").length, 0);
  assert.ok(c.has("Go back to this version"));
  await c.click("Go back to this version");
  await c.click("Go back to this version");
  assert.deepEqual(c.last("ver_install").arg, { id: "a", tag: "v0.9.0", confirm: true });
  assert.ok(c.text().includes("Installed 0.9.0"));
});

t("a confirm reply from the loader is shown and answered with confirm:true", async () => {
  const c = boot();
  c.R.ver_list = () => ({ versions: [versions().versions[0]], more: false });
  c.R.ver_install = a => (a.confirm ? { ok: "a", version: "2.0.0" } : { confirm: "Please confirm this unusual change." });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Install this version");
  assert.ok(c.text().includes("Please confirm this unusual change."));
  await c.click("Go back to this version");
  assert.deepEqual(c.sent("ver_install").map(x => x.arg.confirm), [false, true]);
});

t("installing a version of a frozen module asks to unfreeze first", async () => {
  const c = boot({ state_get: Object.assign(defaults().state_get, { frozen: { a: { at: 5, version: "1.0.0" } } }) });
  c.R.ver_list = versions;
  c.R.mod_freeze = () => defaults().state_get;
  c.R.ver_install = () => ({ ok: "a", version: "2.0.0" });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Install this version");
  assert.equal(c.sent("ver_install").length, 0);
  assert.ok(c.text().includes("Alpha is frozen at v1.0.0"));
  await c.click("Unfreeze and continue");
  assert.equal(c.sent("ver_install").length, 1);
});

t("version history pages: Show more versions asks for page 2 and appends", async () => {
  const c = boot();
  let n = 0;
  c.R.ver_list = a => (a.page === 1 ? { versions: [versions().versions[0]], more: true } : { versions: [versions().versions[2]], more: false, note: "History is limited." });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  assert.ok(c.has("Show more versions"));
  await c.click("Show more versions");
  assert.deepEqual(c.sent("ver_list").map(x => x.arg.page), [1, 2]);
  assert.ok(c.has("2.0.0") && c.has("0.9.0"));
  assert.ok(!c.has("Show more versions"));
  assert.ok(c.text().includes("History is limited."));
});

t("version list error is readable", async () => {
  const c = boot();
  c.R.ver_list = () => ({ error: "GitHub rate limit reached" });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  assert.ok(c.text().includes("GitHub rate limit reached"));
  assert.ok(c.has("Back"));
});

t("repair: confirm screen, keeps settings, shows the stage that failed", async () => {
  const c = boot();
  c.R.repair = () => ({ ok: "a" });
  await c.open("modules");
  await c.click("More");
  await c.click("Repair");
  assert.ok(c.text().includes("Your settings and data are kept."));
  assert.equal(c.sent("repair").length, 0);
  await c.click("Repair", 0);
  assert.deepEqual(c.last("repair").arg, { id: "a", reset: false, confirm: false });
  assert.ok(c.text().includes("Repaired Alpha"));
  await c.click("Back");
  await c.click("Repair");
  c.R.repair = () => ({ error: "hash mismatch", stage: "verify" });
  await c.click("Repair", 0);
  assert.ok(c.text().includes("Failed: hash mismatch"));
  assert.ok(c.text().includes("Failed at step: verify"));
});

t("repair and reset needs a second confirmation from the loader", async () => {
  const c = boot();
  c.R.repair = a => (a.confirm ? { ok: "a" } : { confirm: "Reset also deletes this module's saved settings and cannot be undone." });
  await c.open("modules");
  await c.click("More");
  await c.click("Repair");
  await c.click("Repair and reset settings");
  assert.deepEqual(c.last("repair").arg, { id: "a", reset: true, confirm: false });
  assert.ok(c.text().includes("cannot be undone"));
  await c.click("Reset and repair");
  assert.deepEqual(c.last("repair").arg, { id: "a", reset: true, confirm: true });
  assert.ok(c.text().includes("Repaired Alpha"));
});

t("Remove keeps data; Remove and delete data says what is deleted and purges", async () => {
  const c = boot();
  c.R.uninstall = () => ({ ok: true });
  await c.open("modules");
  await c.click("More");
  await c.click("Remove");
  assert.ok(c.text().includes("Its settings and data are kept."));
  assert.equal(c.sent("uninstall").length, 0);
  await c.click("Cancel");
  assert.equal(c.sent("uninstall").length, 0);
  await c.click("Remove");
  await c.click("Remove", 0);
  assert.deepEqual(c.last("uninstall").arg, { id: "a", purge: false });
  await c.click("More");
  await c.click("Remove and delete data");
  assert.ok(c.text().includes("This cannot be undone."));
  assert.equal(c.sent("uninstall").length, 1);
  await c.click("Delete everything");
  assert.deepEqual(c.last("uninstall").arg, { id: "a", purge: true });
  assert.equal(c.sent("uninstall").length, 2);
});

t("removing a linked module repeats the linked-module warning", async () => {
  const c = boot({ list: [mod("a", "Alpha", { linked: true })] });
  await c.open("modules");
  await c.click("More");
  await c.click("Remove");
  assert.ok(c.text().includes("Linked module: this only removes it from Framey"));
});

t("failed module: readable error, stage, and Disable / Reload / Recover / Remove", async () => {
  const fail = { what: "backend", msg: "ImportError: no module named x", at: 1760000000, stage: "startup" };
  const c = boot({ list: [mod("a", "Alpha", { fail }), mod("b", "Beta", { stuck: true })] });
  c.R.disable = () => ({ ok: true });
  c.R.reload_one = () => ({ ok: true });
  c.R.repair = () => ({ ok: "a" });
  await c.open("modules");
  const s = c.text();
  assert.ok(s.includes("Failed: backend (startup)"));
  assert.ok(s.includes("ImportError: no module named x"));
  assert.ok(s.includes("Failed: backend"));
  assert.ok(s.includes("module not responding"));
  ["Disable", "Reload", "Recover", "Remove"].forEach(l => assert.ok(c.has(l), l));
  await c.click("Disable");
  assert.equal(c.last("disable").arg, "a");
  await c.click("Reload");
  assert.equal(c.last("reload_one").arg, "a");
  await c.click("Recover");
  assert.ok(c.text().includes("Reinstalls the installed version"));
  await c.click("Repair", 0);
  assert.deepEqual(c.last("repair").arg, { id: "a", reset: false, confirm: false });
  await c.click("Back");
  await c.click("Remove");
  assert.ok(c.text().includes("Remove Alpha?"));
});

t("enable switch calls enable/disable and shows errors without breaking", async () => {
  const c = boot();
  c.R.disable = () => ({ ok: true });
  await c.open("modules");
  await c.click("Enabled");
  assert.equal(c.last("disable").arg, "a");
  c.R.disable = () => ({ error: "unknown plugin" });
  await c.click("Enabled");
  assert.ok(c.d.body.textContent.includes("unknown plugin"));
});

t("core switches ignore synthetic clicks only when trusted_input is require", async () => {
  const strict = boot({}, "require");
  strict.R.disable = () => ({ ok: true });
  await strict.open("modules");
  await strict.click("Enabled");
  assert.equal(strict.sent("disable").length, 0);
  const open = boot();
  open.R.disable = () => ({ ok: true });
  await open.open("modules");
  await open.click("Enabled");
  assert.equal(open.last("disable").arg, "a");
});

t("trusted_input require: synthetic clicks on Disable, Restore and Enabled do nothing", async () => {
  const c = boot({ list: [mod("a", "Alpha", { held: true, version: "1.0.0", fail: { what: "backend", msg: "x" } })] }, "require");
  c.R.disable = () => ({ ok: true });
  c.R.rollback = () => ({ ok: "a" });
  c.R.enable = () => ({ ok: true });
  await c.open("modules");
  await c.click("Disable");
  await c.click("Restore previous version");
  await c.click("Enabled");
  assert.equal(c.sent("disable").length, 0);
  assert.equal(c.sent("rollback").length, 0);
  assert.equal(c.sent("enable").length, 0);
  assert.ok(c.has("Disable") && c.has("Restore previous version"));
});

t("safe-mode banner appears only in safe mode and points at the file; there is no exit button", async () => {
  const c = boot();
  await c.open("modules");
  assert.ok(!c.text().includes("Safe mode is on"));
  const s = boot({ state_get: Object.assign(defaults().state_get, { safe: true }) });
  await s.open("modules");
  assert.ok(s.text().includes("Safe mode is on"));
  assert.ok(s.text().includes("No modules are loaded and no backends run."));
  assert.ok(s.text().includes("~/.config/framey/safe"));
  assert.throws(() => s.find("Turn off safe mode"));
  assert.equal(s.sent("prefs_set").length, 0);
});

t("partial update box keeps Retry, Check again and Restore previous version", async () => {
  const c = boot({ list: [mod("a", "Alpha", { pending: true })] });
  c.R.finish = () => ({ pending: true });
  c.R.retry = () => ({ ok: true });
  c.R.rollback = () => ({ ok: true });
  await c.open("modules");
  assert.ok(c.text().includes("Partial update"));
  assert.ok(!c.has("Update"));
  await c.click("Check again");
  assert.equal(c.last("finish").arg, "a");
  assert.ok(c.text().includes("Still incomplete"));
  await c.click("Back");
  await c.click("Retry protected step");
  assert.ok(c.text().includes("Update complete"));
  await c.click("Back");
  await c.click("Restore previous version");
  assert.ok(c.text().includes("Restored previous version of Alpha"));
});

t("held previous version: Restore asks first, then calls rollback once", async () => {
  const c = boot({ list: [mod("a", "Alpha", { held: true, version: "1.0.0" })] });
  c.R.rollback = () => ({ ok: "a" });
  await c.open("modules");
  assert.ok(c.text().includes("Older version installed") && c.text().includes("Alpha was set back to v1.0.0"));
  assert.ok(!c.text().includes("Partial update") && !c.has("Retry protected step") && !c.has("Check again"));
  await c.click("Restore previous version");
  assert.equal(c.sent("rollback").length, 0);
  assert.ok(c.text().includes("Restore the previous version of Alpha?"));
  await c.click("Cancel");
  assert.equal(c.sent("rollback").length, 0);
  await c.click("Restore previous version");
  await c.click("Restore");
  assert.equal(c.sent("rollback").length, 1);
  assert.equal(c.sent("finish").length, 0);
  assert.equal(c.last("rollback").arg, "a");
  assert.ok(c.text().includes("Restored previous version of Alpha"));
});

t("held previous version: Keep this version calls finish once and never rollback", async () => {
  const c = boot({ list: [mod("a", "Alpha", { held: true })] });
  c.R.finish = () => ({ ok: "a" });
  await c.open("modules");
  await c.click("Keep this version");
  assert.equal(c.sent("finish").length, 1);
  assert.equal(c.last("finish").arg, "a");
  assert.equal(c.sent("rollback").length, 0);
  assert.ok(c.text().includes("Keeping this version of Alpha"));
});

t("held previous version: an error reply is shown and a plain card has no box", async () => {
  const c = boot({ list: [mod("a", "Alpha", { held: true }), mod("b", "Beta")] });
  c.R.rollback = () => ({ error: "roll back failed: busy" });
  await c.open("modules");
  assert.equal(c.all().filter(e => e.getAttribute("aria-label") === "Restore previous version").length, 1);
  await c.click("Restore previous version");
  await c.click("Restore");
  assert.ok(c.text().includes("roll back failed: busy"));
  assert.ok(!c.text().includes("Restored previous version"));
});

t("held previous version: hostile names and ids stay text", async () => {
  const bad = '<img src=x onerror="window.pwn=1"><b>x</b>';
  const c = boot({ list: [mod("a", bad, { held: true })] });
  c.R.rollback = () => ({ ok: "a" });
  await c.open("modules");
  assert.equal(c.box.querySelectorAll("img,b").length, 0);
  assert.ok(c.text().includes(bad));
  await c.click("Restore previous version");
  assert.equal(c.box.querySelectorAll("img,b").length, 0);
  assert.ok(c.text().includes(bad));
  await c.click("Restore");
  assert.equal(c.box.querySelectorAll("img,b").length, 0);
  assert.equal(c.w.pwn, undefined);
});

t("held previous version: a protected row keeps the partial box and no held box", async () => {
  const c = boot({ list: [mod("a", "Alpha", { pending: true, held: true })] });
  await c.open("modules");
  assert.ok(c.text().includes("Partial update"));
  assert.ok(!c.text().includes("Older version installed") && !c.has("Keep this version"));
  assert.equal(c.all().filter(e => e.getAttribute("aria-label") === "Restore previous version").length, 1);
});

t("companion download appears only for this platform, runs on tap, reports the integrity note", async () => {
  const comp = [{ name: "tool", platform: "linux-aarch64" }, { name: "other", platform: "linux-x86_64" }];
  const c = boot({ list: [mod("a", "Alpha", { companions: comp })] });
  c.R.companion_fetch = () => ({ ok: "tool", path: "/x/tool", digest: "verified", note: "integrity check, not a signature" });
  await c.open("modules");
  await c.click("More");
  assert.ok(c.has("Download tool") && !c.has("Download other"));
  assert.equal(c.sent("companion_fetch").length, 0);
  await c.click("Download tool");
  assert.deepEqual(c.last("companion_fetch").arg, { id: "a", name: "tool" });
  assert.ok(c.text().includes("Downloaded tool."));
  assert.ok(c.text().includes("integrity check, not a signature"));
});

t("local store and link install still work", async () => {
  const c = boot({ store: [{ id: "s1", name: "Shelf", version: "0.1", installed: false, linked: false }] });
  c.R.add = () => ({ ok: "s1" });
  c.R.install = () => ({ ok: "web" });
  await c.open("modules");
  assert.ok(c.text().includes("Local store folder"));
  assert.ok(c.text().includes("Shelf  v0.1"));
  await c.click("Install", 0);
  assert.equal(c.last("add").arg, "s1");
  const input = c.box.querySelector("input");
  input.value = "https://example.com/m.zip";
  await c.click("Install", 1);
  assert.equal(c.sent("install").length, 0);
  await c.click("Install");
  assert.equal(c.last("install").arg, "https://example.com/m.zip");
  c.R.install = () => ({ error: "not a zip" });
  c.box.querySelector("input").value = "https://example.com/m.zip";
  await c.click("Install", 1);
  await c.click("Install");
  assert.ok(c.text().includes("Failed: not a zip"));
});

t("install from a link asks first, names the host, and Cancel has focus", async () => {
  const c = boot();
  c.R.install = () => ({ ok: "web" });
  await c.open("modules");
  c.box.querySelector("input").value = "https://example.com/m.zip";
  await c.click("Install");
  assert.equal(c.sent("install").length, 0);
  assert.ok(c.text().includes("example.com"));
  assert.ok(c.text().includes("The plugin and its backend run code with your user permissions."));
  assert.strictEqual(c.d.activeElement, c.find("Cancel"));
  await c.click("Cancel");
  assert.equal(c.sent("install").length, 0);
});

t("after an update finishes, focus lands on Back so panel keys keep working", async () => {
  const c = boot();
  c.R.check = () => checked("n");
  c.R.update = () => ({ ok: true, version: "2.0.0" });
  await c.open("modules");
  await c.click("Check for updates");
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("Updated Alpha to v2.0.0"));
  assert.strictEqual(c.d.activeElement, c.find("Back"));
});

t("after a batch update finishes, focus lands on Back", async () => {
  const c = boot();
  c.R.batch_plan = () => ({ items: [{ id: "a", name: "Alpha", eligible: true, from: "1.0.0", to: "2.0.0" }] });
  c.R.batch_apply = () => ({ results: [{ id: "a", name: "Alpha", state: "ok", to: "2.0.0" }], ok: 1, failed: 0 });
  await c.open("modules");
  await c.click("Update all");
  await c.click("Update 1");
  assert.strictEqual(c.d.activeElement, c.find("Back"));
});

t("a late update result does not replace an open dialog; it shows as a toast", async () => {
  const c = boot();
  c.R.check = () => checked("n");
  await c.open("modules");
  await c.click("Check for updates");
  await c.click("Update");
  c.env.event("_core", "update_result", { ok: 1, failed: 0, results: [{ id: "b", name: "Beta", state: "ok", to: "3.0.0" }] });
  await settle();
  assert.ok(c.has("Install"));
  assert.ok(c.d.body.textContent.includes("1 updated, 0 failed"));
});

t("hold: a second tap while a call is running is ignored", async () => {
  const c = boot();
  let release;
  c.R.mod_sort = () => new Promise(r => { release = r; });
  await c.open("modules");
  const b = c.find("Sort: Custom");
  b.click();
  b.click();
  await settle();
  assert.equal(c.sent("mod_sort").length, 1);
  release(Object.assign(defaults().state_get, { sort: "name" }));
  await settle();
  assert.ok(c.has("Sort: Name"));
});

t("Back leaves a pending version lookup and its late reply cannot reopen the list", async () => {
  const c = boot();
  let release;
  c.R.ver_list = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  assert.ok(c.has("Back"));
  assert.equal(c.box.style.opacity, "0.5");
  await c.click("Back");
  assert.ok(c.has("More"));
  assert.equal(c.box.style.opacity, "1");
  release(versions());
  await settle();
  assert.ok(c.has("More"));
  assert.ok(!c.has("2.0.0"));
});

t("Back leaves pending release notes and ignores their late reply", async () => {
  const c = boot({ ver_list: versions });
  let release;
  c.R.notes = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Release notes");
  await c.click("Back");
  assert.ok(c.has("Install this version"));
  release({ notes: "late notes that belong to the closed view" });
  await settle();
  assert.ok(c.has("Install this version"));
  assert.ok(!c.text().includes("late notes that belong to the closed view"));
});

t("an update can continue after Back without taking over the new view", async () => {
  const c = boot();
  let release;
  c.R.update = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("You can go back while this operation continues."));
  await c.click("Back");
  await c.click("More");
  assert.ok(c.has("Less"));
  release({ ok: "a", version: "2.0.0" });
  await settle();
  assert.ok(c.has("Less"));
  assert.ok(!c.text().includes("Updated Alpha"));
});

t("an old Modules draw cannot replace Settings on the same container", async () => {
  const c = boot();
  let release;
  c.R.list = () => new Promise(r => { release = r; });
  await c.open("modules");
  c.R.loader = {};
  await c.open("settings");
  const shown = c.text();
  release([mod("a", "Late module")]);
  await settle();
  assert.equal(c.text(), shown);
  assert.ok(!c.text().includes("Late module"));
});

t("module navigation leaves arrow keys to text editing controls", async () => {
  const c = boot();
  await c.open("modules");
  const input = c.box.querySelector("input");
  input.tabIndex = 0;
  input.focus();
  const key = new c.w.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
  input.dispatchEvent(key);
  assert.equal(key.defaultPrevented, false);
  assert.equal(c.d.activeElement, input);
});

t("partial data removal warns instead of reporting complete removal", async () => {
  const c = boot({ uninstall: { ok: true, purge_failed: ["data", "logs"] } });
  await c.open("modules");
  await c.click("More");
  await c.click("Remove and delete data");
  await c.click("Delete everything");
  assert.ok(c.d.body.textContent.includes("2 data locations could not be deleted"));
  assert.ok(!c.w.sessionStorage.fyMsg);
});

t("removing a protected interface explains that privileged components remain", async () => {
  const c = boot({ list: [mod("a", "Alpha", { privileged: { helper: "/etc/example/update" } })] });
  await c.open("modules");
  await c.click("More");
  await c.click("Remove");
  assert.ok(c.text().includes("does not remove its privileged helper or restore system settings"));
  assert.equal(c.sent("uninstall").length, 0);
});

t("measured progress has accessible values and unmeasured progress drops its value", async () => {
  const c = boot();
  let release;
  c.R.update = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("Update");
  await c.click("Install");
  c.progress({ job: 1, id: "a", stage: "download", index: 2, total: 6, label: "Downloading", state: "running", measured: { done: 1, total: 4 } });
  const bar = c.box.querySelector('[role="progressbar"]');
  assert.equal(bar.getAttribute("aria-valuenow"), "25");
  assert.equal(bar.getAttribute("aria-valuemin"), "0");
  assert.equal(bar.getAttribute("aria-valuemax"), "100");
  assert.equal(bar.getAttribute("aria-label"), "Downloading");
  assert.equal(c.box.querySelector('[role="status"]').getAttribute("aria-live"), "polite");
  c.progress({ job: 1, id: "a", stage: "verify", index: 3, total: 6, label: "Verifying", state: "running", measured: null });
  assert.equal(bar.hasAttribute("aria-valuenow"), false);
  release({ ok: "a" });
  await settle();
});

t("a settings repair reply cannot close a newer dialog after Cancel", async () => {
  const c = boot({ status: { settings_state: "corrupt" } });
  let release;
  c.R.settings_repair = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("Repair settings");
  await c.click("Repair");
  await c.click("Cancel");
  await c.click("More");
  await c.click("Remove");
  const shown = c.text();
  const draws = c.sent("list").length;
  release({ ok: true });
  await settle();
  assert.equal(c.text(), shown);
  assert.equal(c.sent("list").length, draws);
  assert.ok(!c.d.body.textContent.includes("Settings repaired"));
});

t("a selected version that becomes current reports no new installation", async () => {
  const c = boot({ ver_list: versions, ver_install: { current: true } });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Install this version");
  assert.ok(c.text().includes("Already up to date"));
  assert.ok(!c.text().includes("Installed 2.0.0"));
});

t("version completion reports the returned installed version", async () => {
  const c = boot({ ver_list: versions, ver_install: { ok: "a", version: "2.0.1" } });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Install this version");
  assert.ok(c.text().includes("Installed 2.0.1"));
  assert.ok(!c.text().includes("Installed 2.0.0"));
});

t("an empty version-install reply never claims installation succeeded", async () => {
  const c = boot({ ver_list: versions, ver_install: {} });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  await c.click("Install this version");
  assert.ok(c.text().includes("Failed: No answer from Framey"));
  assert.ok(!c.text().includes("Installed 2.0.0"));
});

t("a compatibility assessment alone cannot enable a version installation", async () => {
  for (const flag of [undefined, "true", "false", 1]) {
    const entry = Object.assign({}, versions().versions[0], { compat: { badge: "ok", msg: "Meets declared requirements" }, eligible: flag });
    const c = boot({ ver_list: { versions: [entry], more: false } });
    await c.open("modules");
    await c.click("More");
    await c.click("Versions");
    await c.click("2.0.0");
    assert.equal(c.find("Install this version").getAttribute("aria-disabled"), "true");
    await c.click("Install this version");
    assert.equal(c.sent("ver_install").length, 0);
    assert.ok(c.text().includes("Unavailable"));
  }
});

t("batch plans require explicit eligibility rather than truthy assessment data", async () => {
  const c = boot({ batch_plan: { items: [{ id: "a", name: "Alpha", from: "1.0.0", to: "2.0.0", eligible: "false", reason: "Not eligible" }] } });
  await c.open("modules");
  await c.click("Update all");
  assert.equal(c.find("Update 0").getAttribute("aria-disabled"), "true");
  await c.click("Update 0");
  assert.equal(c.sent("batch_apply").length, 0);
});

t("a detached version button cannot start another installation after completion", async () => {
  const c = boot({ ver_list: versions, ver_install: { ok: "a", version: "2.0.0" } });
  await c.open("modules");
  await c.click("More");
  await c.click("Versions");
  await c.click("2.0.0");
  const original = c.find("Install this version");
  original.click();
  await settle();
  original.click();
  await settle();
  assert.equal(c.sent("ver_install").length, 1);
});

t("a module update completion cannot replace the separate Decky Store tab", async () => {
  const files = FILES.slice(0, -1).concat("fy-catalog.js", FILES.slice(-1));
  const c = boot({
    catalog_sources: { sources: [], decky_sources: [{ id: "decky_stable", label: "Decky Store" }] },
    catalog_list: { rows: [], total: 0, origin: "downloaded", stale: false, source: { id: "decky_stable" } },
  }, "off", files);
  let release;
  c.R.update = () => new Promise(r => { release = r; });
  await c.open("modules");
  await c.click("Update");
  await c.click("Install");
  await c.open("decky_store");
  const shown = c.text();
  assert.equal(c.box.querySelector('[role="heading"]').textContent, "Decky Store");
  release({ ok: "a", version: "2.0.0" });
  await settle();
  assert.equal(c.text(), shown);
  assert.ok(!c.text().includes("Updated Alpha"));
});

t("ordinary update rejects empty or unrecognised completion replies", async () => {
  for (const reply of [null, {}, [], { ok: false }, { ok: {} }, { ok: 1 }, { current: "false" }, { staged: "yes" }, { staged: true }]) {
    const c = boot({ update: reply });
    await c.open("modules");
    await c.click("Update");
    await c.click("Install");
    assert.equal(c.sent("update").length, 1);
    assert.ok(c.text().includes("Failed: No answer from Framey"));
    assert.ok(!c.text().includes("Updated Alpha"));
    assert.ok(!c.w.sessionStorage.fyRes);
  }
});

t("ordinary update preserves explicit boolean and module-id successes", async () => {
  for (const marker of [{ ok: true }, { ok: "a" }]) {
    const c = boot({ update: Object.assign({ version: "2.0.0" }, marker) });
    await c.open("modules");
    await c.click("Update");
    await c.click("Install");
    assert.ok(c.text().includes("Updated Alpha to v2.0.0"));
    assert.ok(!c.text().includes("Failed:"));
  }
});

t("ordinary update preserves the already-current result", async () => {
  const c = boot({ update: { current: true } });
  await c.open("modules");
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("Already up to date"));
  assert.ok(!c.text().includes("Updated Alpha"));
});

t("ordinary update preserves a backend error rather than claiming success", async () => {
  const c = boot({ update: { error: "The package could not be verified", ok: true } });
  await c.open("modules");
  await c.click("Update");
  await c.click("Install");
  assert.ok(c.text().includes("Failed: The package could not be verified"));
  assert.ok(!c.text().includes("Updated Alpha"));
});

t("strings: every key used by the pages exists in the English table, none is unused", async () => {
  const c = boot();
  const used = new Set();
  ["fy-modules.js", "fy-settings.js"].forEach(f => {
    const src = fs.readFileSync(path.join(ROOT, f), "utf8");
    for (const m of src.matchAll(/"((?:[cmpvs]|mt)\.[a-z_]+)"/g)) used.add(m[1]);
  });
  ["p.running", "p.ok", "p.fail"].forEach(k => used.add(k));
  const src = fs.readFileSync(path.join(ROOT, "fy-strings-en.js"), "utf8");
  const keys = new Set([...src.matchAll(/^\s+"((?:[cmpvs]|mt)\.[a-z_]+)":/gm)].map(m => m[1]));
  const base = k => k.replace(/_(zero|one|other)$/, "");
  const defined = new Set([...keys].map(base));
  const missing = [...used].filter(k => !defined.has(base(k)));
  assert.deepEqual(missing, []);
  const unused = [...keys].filter(k => !used.has(base(k)) && !used.has(k));
  assert.deepEqual(unused, []);
  keys.forEach(k => assert.equal(c.FY.i18n.t("core", k, { count: 1 }) === k, false, k));
  assert.ok(c.FY.i18n.t("core", "m.n_updates", { count: 2 }) === "2 updates available");
  assert.ok(c.FY.i18n.t("core", "m.n_updates", { count: 1 }) === "1 update available");
  assert.ok(c.FY.i18n.t("core", "m.plan_count", { count: 0 }) === "Nothing to update");
});

t("strings: fallback to English for another language, to the key for unknown keys; only en ships", async () => {
  const c = boot();
  c.FY.i18n.setLang("pt-BR");
  assert.equal(c.FY.i18n.t("core", "c.back"), "Back");
  assert.equal(c.FY.i18n.t("core", "no.such.key"), "no.such.key");
  const bad = [];
  const walk = d => fs.readdirSync(d, { withFileTypes: true }).forEach(e => {
    if (e.name === "node_modules" || e.name === ".git") return;
    const p = path.join(d, e.name);
    if (e.isDirectory()) return walk(p);
    if (/^fy-strings-(?!en\.)/.test(e.name) || (/[\\/]locales[\\/]/.test(p) && !/en\.json$/.test(p) && !p.includes("fixtures"))) bad.push(p);
  });
  walk(ROOT);
  assert.deepEqual(bad.filter(p => !/examples/.test(p)), []);
});

t("source hygiene: no comments, no innerHTML, no timers or intervals in the page files", async () => {
  ["fy-modules.js", "fy-settings.js", "fy-badge.js", "fy-strings-en.js"].forEach(f => {
    const src = fs.readFileSync(path.join(ROOT, f), "utf8");
    assert.ok(!/(^|[^:"'\\])\/\/[^\n]*$/m.test(src.replace(/"(?:[^"\\]|\\.)*"/g, '""')), f + " comment");
    assert.ok(!/\/\*/.test(src.replace(/"(?:[^"\\]|\\.)*"/g, '""')), f + " block comment");
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(src), f + " html injection");
    assert.ok(!/setInterval|setTimeout|requestAnimationFrame|MutationObserver|addEventListener/.test(src), f + " timer or listener");
  });
});

t("plugin code cannot replace, delete or add to the built-in Modules and Settings pages", async () => {
  const { w, FY, all, open, text } = boot();
  const pages = FY.pages;
  const modules = pages.modules;
  const settings = pages.settings;
  w.eval("(function(F){F.pages={};F.pages.modules={render(){}};F.pages.extra={render(){}};delete F.pages.settings;F.pages.settings=null;try{Object.defineProperty(F.pages,'modules',{value:{render(){}}})}catch(x){}})(window.Framey)");
  assert.strictEqual(FY.pages, pages);
  assert.strictEqual(FY.pages.modules, modules);
  assert.strictEqual(FY.pages.settings, settings);
  assert.ok(!("extra" in FY.pages));
  assert.ok(Object.isFrozen(FY.pages));
  await open("modules");
  assert.ok(all().length > 0, "the built-in Modules page still renders");
  assert.ok(text().length > 0);
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
