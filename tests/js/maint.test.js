"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { makeDom, ROOT, TOK } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const LABELS = { archived: "Archived", declared: "Unmaintained (declared)", recent: "Recent activity", none: "No recent activity", unknown: "Unknown" };
const mod = (id, name, extra) => Object.assign({ id, name, version: "1.0.0", commit: id + "abc123".slice(0, 6), enabled: true, nosource: "", pending: false, compat: { badge: "ok", msg: "" }, fail: null, stuck: false, linked: false }, extra);
const rec = (label, id, over) =>
  Object.assign(
    {
      id,
      label,
      title: label,
      reason: "A reason sentence.",
      evidence: [],
      repo: "acme/widget",
      branch: "main",
      path: "",
      scope: "whole repository",
      fork_of: "",
      archived: false,
      commit_at: "2026-09-30T10:00:00Z",
      release_at: "",
      release_tag: "",
      declaration: null,
      declaration_note: "",
      checked_at: 1790000000,
      stale: false,
      failure: null,
      retry_after: 0,
      window_days: 365,
      source: "https://github.com/acme/widget",
    },
    over,
  );
const defaults = () => ({
  list: [mod("a", "Alpha"), mod("b", "Beta")],
  store: [],
  state_get: { order: ["a", "b"], sort: "custom", hidden: [], frozen: {}, prefs: { channel: "stable" }, safe: false },
  maint_prefs: { "maint.enabled_background": false, "maint.window_days": 365 },
  maint_get: arg => ({ records: Object.fromEntries(arg.ids.map(id => [id, rec("recent", id)])) }),
});
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

const boot = (over = {}) => {
  const calls = [];
  const R = Object.assign(defaults(), over);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en" };
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
  const all = () => [...box.querySelectorAll('[role="button"],[role="switch"],[role="option"],[role="slider"]')];
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
  const open = async which => {
    FY.pages[which || "modules"].render(box, {});
    await settle();
  };
  const text = () => box.textContent;
  const chips = () => [...box.querySelectorAll('[data-mt="label"]')].map(e => e.textContent);
  return { env, FY, w: env.window, d: env.document, box, calls, R, sent, last, open, find, has, click, text, all, chips };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("each status shows its exact label with its own icon and colour, and the text is always there", async () => {
  const seen = new Map();
  for (const label of Object.keys(LABELS)) {
    const c = boot({ list: [mod("a", "Alpha")], maint_get: a => ({ records: { a: rec(label, "a") } }) });
    await c.open();
    assert.deepEqual(c.chips(), [LABELS[label]], label);
    const chip = c.find("Upstream project status of Alpha: " + LABELS[label]);
    assert.equal(chip.textContent.endsWith(LABELS[label]), true);
    const glyph = chip.firstChild;
    assert.equal(glyph.getAttribute("aria-hidden"), "true");
    assert.ok(glyph.textContent.length >= 1 && glyph.textContent !== LABELS[label]);
    seen.set(label, [glyph.textContent, chip.style.color]);
    assert.ok(chip.className.includes("fy-c") && chip.getAttribute("role") === "button" && chip.tabIndex === 0);
  }
  assert.equal(new Set([...seen.values()].map(v => v[0])).size, 5);
  assert.equal(seen.get("none")[1], seen.get("unknown")[1]);
  assert.notEqual(seen.get("none")[1], seen.get("archived")[1]);
  assert.notEqual(seen.get("recent")[1], seen.get("none")[1]);
  assert.notEqual(seen.get("recent")[1], seen.get("archived")[1]);
  const c = boot();
  await c.open();
  const css = c.d.getElementById("fy-ui-style").textContent;
  assert.ok(/\.fy-c\{[^}]*min-height:48px/.test(css));
});

t("none and unknown are never drawn in the warning colour", async () => {
  const c = boot({ list: [mod("a", "Alpha"), mod("b", "Beta")], maint_get: () => ({ records: { a: rec("none", "a"), b: rec("unknown", "b") } }) });
  await c.open();
  const warn = c.find("Upstream project status of Alpha: No recent activity").style.color;
  const archived = boot({ list: [mod("a", "Alpha")], maint_get: () => ({ records: { a: rec("archived", "a") } }) });
  await archived.open();
  const real = archived.find("Upstream project status of Alpha: Archived").style.color;
  assert.notEqual(warn, real);
  assert.equal(c.find("Upstream project status of Beta: Unknown").style.color, warn);
});

t("three separate indicators: Framey compatibility, installed-version freshness, project maintenance", async () => {
  const c = boot({ list: [mod("a", "Alpha")] });
  c.R.check = { checked: { a: { state: "current", latest: "abc" } } };
  await c.open();
  await c.click("Check for updates");
  const t0 = c.text();
  assert.ok(t0.includes("Compatible"));
  assert.ok(t0.includes("All up to date"));
  assert.ok(t0.includes("Upstream project"));
  assert.deepEqual(c.chips(), ["Recent activity"]);
  const mine = ["Archived", "Unmaintained (declared)", "Recent activity", "No recent activity", "Unknown"];
  const theirs = ["Compatible", "Needs a newer Framey", "Built for an older Framey", "Not for this platform", "Needs a different API generation", "Compatibility not declared", "Up to date", "Update available", "All up to date"];
  mine.forEach(m => theirs.forEach(o => assert.notEqual(m, o)));
  const src = fs.readFileSync(path.join(ROOT, "fy-strings-en.js"), "utf8");
  const strings = Object.fromEntries([...src.matchAll(/^\s+"([a-z.]+)":\s*"((?:[^"\\]|\\.)*)"/gm)].map(m => [m[1], m[2]]));
  mine.forEach(m => assert.equal(Object.entries(strings).filter(([k, v]) => v === m).length, 1, m));
});

t("the stale marker appears only for stale records, beside the chip", async () => {
  const c = boot({ list: [mod("a", "Alpha"), mod("b", "Beta")], maint_get: () => ({ records: { a: rec("recent", "a", { stale: true }), b: rec("recent", "b") } }) });
  await c.open();
  const marks = [...c.box.querySelectorAll('[data-mt="stale"]')];
  assert.equal(marks.length, 1);
  assert.equal(marks[0].textContent, "stale");
  assert.equal(marks[0].parentNode, c.find("Upstream project status of Alpha: Recent activity (stale)").parentNode);
  assert.ok(c.has("Upstream project status of Beta: Recent activity"));
  await c.click("Upstream project status of Alpha: Recent activity (stale)");
  assert.ok(c.text().includes("Stale: this information is older than 24 hours"));
  assert.ok(c.text().includes("(stale)"));
});

t("details: repository, branch, scope, dates, archived flag, declaration, fork, check time, window, evidence, informational line", async () => {
  const r = rec("declared", "a", {
    repo: "acme/widget",
    branch: "stable",
    path: "plugins/demo",
    scope: "plugin path",
    commit_at: "2024-01-02T03:04:05Z",
    release_at: "2023-05-06T00:00:00Z",
    release_tag: "v2.3.0",
    archived: false,
    fork_of: "upstream/widget",
    declaration: { by: "example-maintainer", note: "No further fixes planned.", date: "2026-08-01", file: "plugins/demo/plugin.json", commit: "a".repeat(40) },
    declaration_note: "",
    evidence: ["Declared by example-maintainer", "Last commit 2024-01-02"],
    reason: "The maintainer declared this module unmaintained.",
  });
  const c = boot({ list: [mod("a", "Alpha")], maint_get: () => ({ records: { a: r } }) });
  await c.open();
  await c.click("Upstream project status of Alpha: Unmaintained (declared)");
  const x = c.text();
  assert.ok(x.includes("Upstream project status: Alpha"));
  assert.ok(x.includes("Unmaintained (declared)"));
  assert.ok(x.includes("The maintainer declared this module unmaintained."));
  assert.ok(x.includes("Repository: acme/widget, branch stable"));
  assert.ok(x.includes("Recorded source: https://github.com/acme/widget"));
  assert.ok(x.includes("Scope: this module's folder plugins/demo only"));
  assert.ok(x.includes("Latest commit: 2024-01-02"));
  assert.ok(x.includes("Latest release: v2.3.0, 2023-05-06 (whole repository, not used for this module's status)"));
  assert.ok(x.includes("Archived on GitHub: no"));
  assert.ok(x.includes("Fork of upstream/widget"));
  assert.ok(x.includes("Declared unmaintained by example-maintainer (2026-08-01)"));
  assert.ok(x.includes("Note: No further fixes planned."));
  assert.ok(x.includes("Read from plugins/demo/plugin.json at commit aaaaaaa"));
  assert.ok(x.includes("Last checked: "));
  assert.ok(x.includes("Activity window: 365 days"));
  assert.ok(x.includes("Why"));
  assert.ok(x.includes("- Declared by example-maintainer"));
  assert.ok(x.includes("- Last commit 2024-01-02"));
  assert.ok(x.includes("Informational only. Framey never disables, removes, replaces or blocks a module"));
  assert.deepEqual(c.all().map(e => e.getAttribute("aria-label")), ["Refresh", "Back"]);
});

t("details: whole-repository scope explains itself, archived is shown, no release says so", async () => {
  const c = boot({ list: [mod("a", "Alpha")], maint_get: () => ({ records: { a: rec("archived", "a", { archived: true, reason: "GitHub reports the repository as archived." }) } }) });
  await c.open();
  await c.click("Upstream project status of Alpha: Archived");
  const x = c.text();
  assert.ok(x.includes("Scope: whole repository. No folder is recorded for this module"));
  assert.ok(x.includes("including parts that are not this module"));
  assert.ok(x.includes("Archived on GitHub: yes"));
  assert.ok(x.includes("Latest release: none"));
});

t("details: no recent activity always says it does not establish abandonment", async () => {
  for (const reason of ["The newest activity is old. It does not establish abandonment.", "The newest activity is old."]) {
    const c = boot({ list: [mod("a", "Alpha")], maint_get: () => ({ records: { a: rec("none", "a", { reason }) } }) });
    await c.open();
    await c.click("Upstream project status of Alpha: No recent activity");
    assert.ok(/does not establish abandonment/.test(c.text()), reason);
  }
});

t("details: unknown shows the reason, the failure and the retry time", async () => {
  const future = Math.floor(Date.now() / 1000) + 3600;
  const cases = [
    [rec("unknown", "a", { reason: "Installed from a ZIP with no upstream source, so there is nothing to check.", repo: "", scope: "", checked_at: 0, stale: false, source: "" }), ["Installed from a ZIP with no upstream source", "Never checked"], ["Last refresh failed"]],
    [rec("unknown", "a", { reason: "Could not check: GitHub could not be reached or gave an unusable answer.", repo: "", scope: "", checked_at: 0, stale: true, failure: { reason: "Could not check: network", at: 1790000000 } }), ["Could not check: GitHub could not be reached", "Last refresh failed: Could not check: network", "stale"], []],
    [rec("unknown", "a", { reason: "Could not check: GitHub rate limit reached. Try again later.", repo: "", scope: "", checked_at: 0, stale: true, failure: { reason: "Could not check: GitHub rate limit reached.", at: 1790000000 }, retry_after: future }), ["rate limit reached", "Next attempt allowed after "], []],
  ];
  for (const [r, yes, no] of cases) {
    const c = boot({ list: [mod("a", "Alpha")], maint_get: () => ({ records: { a: r } }) });
    await c.open();
    await c.click(c.all().find(e => e.getAttribute("aria-label").startsWith("Upstream project status of Alpha")).getAttribute("aria-label"));
    yes.forEach(s => assert.ok(c.text().includes(s), s));
    no.forEach(s => assert.ok(!c.text().includes(s), s));
  }
});

t("Refresh asks for that module only, shows the new record, and Back returns to the list", async () => {
  const c = boot({ list: [mod("a", "Alpha")] });
  c.R.maint_refresh = a => rec("none", a.id, { reason: "Nothing recent. It does not establish abandonment." });
  await c.open();
  await c.click("Upstream project status of Alpha: Recent activity");
  await c.click("Refresh");
  assert.deepEqual(c.sent("maint_refresh").map(x => x.arg), [{ id: "a" }]);
  assert.ok(c.text().includes("No recent activity"));
  assert.ok(c.text().includes("Nothing recent."));
  await c.click("Back");
  assert.deepEqual(c.chips(), ["No recent activity"]);
  assert.ok(c.has("Update"));
});

t("a refresh error keeps the old record and says why", async () => {
  const c = boot({ list: [mod("a", "Alpha")] });
  c.R.maint_refresh = { error: "A maintenance check is already running", code: "busy" };
  await c.open();
  await c.click("Upstream project status of Alpha: Recent activity");
  await c.click("Refresh");
  assert.ok(c.text().includes("Upstream project status: Alpha"));
  assert.ok(c.text().includes("Recent activity"));
  assert.ok(c.d.body.textContent.includes("A maintenance check is already running"));
});

t("Refresh all forces every module, merges the records and reports the count", async () => {
  const c = boot();
  c.R.maint_refresh = () => ({ records: { a: rec("archived", "a"), b: rec("none", "b") } });
  await c.open();
  assert.deepEqual(c.chips(), ["Recent activity", "Recent activity"]);
  await c.click("Refresh all");
  assert.deepEqual(c.sent("maint_refresh").map(x => x.arg), [{ id: "all" }]);
  assert.deepEqual(c.chips(), ["Archived", "No recent activity"]);
  assert.ok(c.text().includes("2 modules refreshed"));
  c.R.maint_refresh = { error: "x down" };
  await c.click("Refresh all");
  assert.ok(c.text().includes("Failed: x down"));
  assert.deepEqual(c.chips(), ["Archived", "No recent activity"]);
});

t("the activity window is shown on the Modules page", async () => {
  const c = boot({ maint_prefs: { "maint.enabled_background": false, "maint.window_days": 90 } });
  await c.open();
  assert.ok(c.text().includes("Project activity window: 90 days"));
  const d = boot();
  await d.open();
  assert.ok(d.text().includes("Project activity window: 365 days"));
});

t("a maint event refreshes the chips on the list, and leaves an open details screen alone", async () => {
  const c = boot({ list: [mod("a", "Alpha")] });
  await c.open();
  assert.deepEqual(c.chips(), ["Recent activity"]);
  c.R.maint_get = a => ({ records: Object.fromEntries(a.ids.map(id => [id, rec("archived", id)])) });
  c.FY._k(TOK).event("_core", "maint", { ids: ["a"] }, 0);
  await settle();
  assert.deepEqual(c.chips(), ["Archived"]);
  await c.click("Upstream project status of Alpha: Archived");
  const before = c.sent("maint_get").length;
  c.R.maint_get = a => ({ records: { a: rec("none", "a") } });
  c.FY._k(TOK).event("_core", "maint", { ids: ["a"] }, 0);
  await settle();
  assert.equal(c.sent("maint_get").length, before);
  assert.ok(c.text().includes("Upstream project status: Alpha"));
  c.FY._k(TOK).event("_core", "maint", { ids: ["zzz", 5, null] }, 0);
  c.FY._k(TOK).event("_core", "maint", "junk", 0);
  await settle();
  assert.equal(c.sent("maint_get").length, before);
});

t("maintenance status never gates, disables, hides or reorders anything", async () => {
  const c = boot({ maint_get: a => ({ records: { a: rec("archived", "a", { archived: true }), b: rec("declared", "b", { declaration: { by: "someone", note: "", date: "", file: "plugin.json", commit: "" } }) } }) });
  c.R.update = { ok: "a", version: "1.1.0" };
  c.R.check = { checked: { a: { state: "update", latest: "2.0.0", known: true }, b: { state: "update", latest: "2.0.0", known: true } } };
  await c.open();
  assert.equal(c.find("Update", 0).getAttribute("aria-disabled"), "false");
  assert.equal(c.find("Update", 1).getAttribute("aria-disabled"), "false");
  assert.equal(c.find("Enabled", 0).getAttribute("aria-disabled"), "false");
  const order = () => [...c.box.querySelectorAll("div")].map(e => e.textContent).filter(s => /^(Alpha|Beta)  v1\.0\.0$/.test(s));
  assert.deepEqual(order(), ["Alpha  v1.0.0", "Beta  v1.0.0"]);
  await c.click("Check for updates");
  assert.equal(c.find("Update", 0).getAttribute("aria-disabled"), "false");
  await c.click("Update", 0);
  assert.ok(c.has("Install"));
  await c.click("Install");
  assert.deepEqual(c.sent("update").map(x => x.arg), ["a"]);
  const forbidden = ["mod_hide", "mod_freeze", "disable", "uninstall", "mod_order", "mod_sort", "batch_apply"];
  assert.deepEqual(c.calls.filter(x => forbidden.includes(x.method)), []);
  await c.click("Back");
  assert.deepEqual(order(), ["Alpha  v1.0.0", "Beta  v1.0.0"]);
});

t("the Update all plan is not changed by maintenance either", async () => {
  const c = boot({ maint_get: a => ({ records: { a: rec("archived", "a"), b: rec("none", "b") } }) });
  c.R.batch_plan = { items: [{ id: "a", name: "Alpha", from: "1.0.0", to: "1.1.0", eligible: true, reason: "" }, { id: "b", name: "Beta", from: "1.0.0", to: "1.1.0", eligible: true, reason: "" }] };
  await c.open();
  await c.click("Update all");
  assert.ok(c.has("Update 2"));
  assert.ok(!/archived|abandon|unmaintained/i.test(c.text()));
});

t("an unavailable maintenance module degrades silently: no chips, no errors, the rest works", async () => {
  const c = boot({ maint_get: { error: "fy_maint unavailable" }, maint_prefs: { error: "fy_maint unavailable" } });
  await c.open();
  assert.deepEqual(c.chips(), []);
  assert.ok(c.text().includes("Alpha"));
  assert.ok(!c.text().includes("fy_maint"));
  assert.ok(!c.text().includes("Project activity window"));
  assert.ok(c.has("Refresh all"));
  assert.equal(c.find("Update", 0).getAttribute("aria-disabled"), "false");
  const s = boot({ maint_prefs: { error: "fy_maint unavailable" } });
  await s.open("settings");
  assert.ok(s.text().includes("Project maintenance status is unavailable."));
  assert.ok(!s.has("Check project maintenance in the background"));
});

t("a missing or hostile record never breaks the card; hostile text stays text", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const c = boot({
    list: [mod("a", "Alpha"), mod("b", "Beta"), mod("c", "Gamma")],
    maint_get: () => ({ records: { a: rec("recent", "a", { reason: evil, repo: evil, evidence: [evil, 5, null], declaration: { by: evil, note: evil, date: evil, file: evil, commit: evil }, failure: { reason: evil, at: "x" }, source: evil, fork_of: evil, release_tag: evil, release_at: evil, commit_at: evil }), b: rec("bogus-label", "b"), c: null } }),
  });
  await c.open();
  assert.deepEqual(c.chips(), ["Recent activity", "Unknown"]);
  await c.click("Upstream project status of Alpha: Recent activity");
  assert.equal(c.box.querySelectorAll("img").length, 0);
  assert.ok(c.text().includes(evil));
  assert.ok(![...c.box.querySelectorAll("*")].some(e => [...e.attributes].some(a => /^on/i.test(a.name))));
  await c.click("Back");
  await c.click("Upstream project status of Beta: Unknown");
  assert.ok(c.text().includes("Upstream project status: Beta"));
});

t("one maint_get call carries the module ids, at most 64", async () => {
  const many = Array.from({ length: 70 }, (_, i) => mod("m" + i, "M" + i));
  const c = boot({ list: many });
  await c.open();
  const calls = c.sent("maint_get");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].arg.ids.length, 64);
  assert.equal(calls[0].arg.ids[0], "m0");
  const e = boot({ list: [] });
  await e.open();
  assert.equal(e.sent("maint_get").length, 0);
});

t("opening the page, refreshing and reading details create no timer and no listener", async () => {
  const c = boot();
  const before = c.env.clock.pending();
  await c.open();
  await c.click("Upstream project status of Alpha: Recent activity");
  c.R.maint_refresh = a => rec("recent", a.id);
  await c.click("Refresh");
  await c.click("Back");
  await c.click("Refresh all");
  assert.equal(c.env.clock.pending(), before);
});

t("Settings: the background toggle reads maint_prefs, saves one key and reverts on error", async () => {
  const c = boot();
  c.R.maint_prefs = a => (a && a["maint.enabled_background"] !== undefined ? (c.fail ? { error: "could not save", code: "write_failed" } : { "maint.enabled_background": a["maint.enabled_background"], "maint.window_days": 365 }) : { "maint.enabled_background": false, "maint.window_days": 365 });
  c.R.autocheck_status = { enabled: false, last: 0, next_ok: 0, fails: 0 };
  c.R.loader = { commit: "1234567", kept: false };
  await c.open("settings");
  const sw = c.find("Check project maintenance in the background");
  assert.equal(sw.getAttribute("aria-checked"), "false");
  assert.ok(c.text().includes("Off by default."));
  assert.ok(c.text().includes("with the automatic update check"));
  assert.ok(c.text().includes("A manual Check for updates also asks GitHub for maintenance information"));
  assert.ok(c.text().includes("GitHub sees your IP address"));
  sw.click();
  await settle();
  assert.deepEqual(c.sent("maint_prefs").filter(x => x.arg).map(x => x.arg), [{ "maint.enabled_background": true }]);
  assert.equal(sw.getAttribute("aria-checked"), "true");
  c.fail = true;
  sw.click();
  await settle();
  assert.equal(sw.getAttribute("aria-checked"), "true");
  c.R.maint_prefs = () => ({ error: "could not save", code: "write_failed" });
  sw.click();
  await settle();
  assert.equal(sw.getAttribute("aria-checked"), "true");
  assert.ok(c.d.body.textContent.includes("could not save"));
});

t("Settings: the activity window is shown as 365 days and changes through maint_prefs", async () => {
  const c = boot();
  c.R.autocheck_status = { enabled: false, last: 0, next_ok: 0, fails: 0 };
  c.R.loader = { commit: "1234567", kept: false };
  c.R.maint_prefs = a => ({ "maint.enabled_background": false, "maint.window_days": a && a["maint.window_days"] ? a["maint.window_days"] : 365 });
  await c.open("settings");
  const slider = c.find("Activity window");
  assert.equal(slider.getAttribute("aria-valuetext"), "365 days");
  assert.equal(slider.getAttribute("aria-valuemin"), "30");
  assert.equal(slider.getAttribute("aria-valuemax"), "1825");
  slider.dispatchEvent(new c.w.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
  slider.dispatchEvent(new c.w.KeyboardEvent("keyup", { key: "ArrowLeft", bubbles: true }));
  await settle();
  assert.deepEqual(c.sent("maint_prefs").filter(x => x.arg).map(x => x.arg), [{ "maint.window_days": 360 }]);
  assert.equal(slider.getAttribute("aria-valuetext"), "360 days");
});

t("source hygiene: the new code adds no comments, markup injection, timers or listeners", async () => {
  ["fy-modules.js", "fy-settings.js", "fy-strings-en.js"].forEach(f => {
    const src = fs.readFileSync(path.join(ROOT, f), "utf8");
    assert.ok(!/(^|[^:"'\\])\/\/[^\n]*$/m.test(src.replace(/"(?:[^"\\]|\\.)*"/g, '""')), f + " comment");
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|setTimeout|setInterval/.test(src), f);
  });
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
