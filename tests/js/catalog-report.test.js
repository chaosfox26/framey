"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js"];
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };
const clone = value => value === undefined ? null : JSON.parse(JSON.stringify(value));
const sources = [
  { id: "stable", label: "Stable", url: "https://example.com/native.json" },
  { id: "testing", label: "Testing", url: "https://example.com/native.json" },
  { id: "custom", label: "Custom", url: "" },
  { id: "decky_stable", label: "Decky Store", url: "https://example.com/decky.json" },
  { id: "decky_testing", label: "Decky Store Testing", url: "https://example.com/decky-testing.json" },
];
const selectedSource = id => sources.find(source => source.id === id);
const versions = [
  { name: "1.4.0", hash: "c".repeat(64), identity: "d".repeat(64), inspectable: true },
  { name: "1.3.0", hash: "e".repeat(64), identity: "f".repeat(64), inspectable: true },
];
const native = () => ({
  id: "palette", name: "Native palette", version: "2.0.0", description: "A native palette module", category: "appearance", tags: ["appearance"],
  kind: "plugin", source: { repository: "https://example.com/palette", commit: "a".repeat(40), path: "" },
  platform: ["linux-aarch64"], platform_declared: true, identity: "b".repeat(64), eligible: true, installed: false,
  compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] },
  compatibility: { status: "compatible", label: "Compatible", reason: "Declared requirements match", reasons: [], basis: "declared_requirements", runtime_verified: false },
});
const decky = () => ({
  id: "decky-41", name: "Palette helper", version: "1.4.0", description: "An upstream Decky palette tool", category: "appearance", tags: ["appearance"],
  format: "decky", kind: "decky", read_only: true, eligible: false, installed: false, identity: "a".repeat(64),
  reason: "Needs a Framey port; ARM64 compatibility is unverified", source: { repository: "", url: selectedSource("decky_stable").url },
  compat: { state: "incompatible", badge: "port", msg: "Decky format requires a Framey port", reasons: [] },
  compatibility: { status: "incompatible", label: "Not compatible", reason: "Built for Decky’s interface", reasons: [], basis: "decky_format", runtime_verified: false },
  architecture: "unknown", platform_declared: false, version_count: versions.length, versions,
});
const status = source => ({ source: selectedSource(source), origin: "downloaded", stale: false, warning: "", error: null, fetch_error: null });
const defaults = () => ({
  catalog_sources: arg => ({ source: selectedSource(arg && arg.source || "stable"), sources: sources.slice(0, 3), decky_sources: sources.slice(3), custom_url: "" }),
  catalog_list: arg => Object.assign(status(arg.source), { rows: [arg.source.startsWith("decky_") ? decky() : native()], total: 1, offset: 0, limit: 50, categories: ["appearance"] }),
  catalog_details: arg => Object.assign(status(arg.source), { entry: arg.source.startsWith("decky_") ? decky() : native() }),
  catalog_assess: arg => {
    const version = versions.find(item => item.identity === arg.version_identity);
    return { id: "decky-41", format: "decky", read_only: true, version: version.name, hash: version.hash,
      assessment: { state: "port_required", label: "Needs a Framey port", architecture: "unknown", native_binaries: [], has_frontend: true, has_backend: true,
        root_required: null, reasons: [], warnings: [], flags: [], dependencies: [], verified_sha256: true, not_executed: true, bytes: 1024, files: 2 } };
  },
  catalog_install: { error: "The selected package could not be installed", code: "install_failed" },
});
const boot = (replies = {}, options = {}) => {
  const calls = [], reports = [], reportClosers = [];
  const R = Object.assign(defaults(), replies);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      if (options.sharedReport) w.FY_RUNTIME.report_repository = "https://github.com/chaosfox26/framey";
      w.fyCall = encoded => {
        const request = JSON.parse(encoded);
        calls.push(request);
        if (!request.id) return;
        const handler = R[request.method];
        const response = typeof handler === "function" ? handler(request.arg, request) : handler;
        Promise.resolve(response === undefined ? { error: "Unhandled " + request.method } : response)
          .then(value => w.Framey._reply(request.gen, request.id, clone(value)));
      };
    },
    loadFiles: FILES,
  });
  const { window: w, document: d } = env;
  if (options.sharedReport) env.load("fy-report.js");
  else w.__S.K.report = context => {
      reports.push(clone(context));
      return new Promise(resolve => { reportClosers.push(() => resolve({ closed: true })); });
    };
  env.load("fy-catalog.js");
  const box = d.createElement("div");
  d.body.append(box);
  const controls = () => [...box.querySelectorAll("[aria-label]")];
  const find = label => {
    const node = controls().find(control => control.getAttribute("aria-label") === label);
    assert.ok(node, "No control '" + label + "' among " + controls().map(control => control.getAttribute("aria-label")).join(" | "));
    return node;
  };
  const open = async (page = "decky_store") => { w.Framey.pages[page].render(box, {}); await settle(); };
  const click = async label => { find(label).click(); await settle(); };
  const set = async (label, value) => {
    const node = find(label);
    node.value = value;
    node.dispatchEvent(new w.Event("change", { bubbles: true }));
    await settle();
  };
  const closeReport = async (index = reportClosers.length - 1) => { reportClosers[index](); await settle(); };
  const sent = method => calls.filter(request => request.method === method);
  const text = () => box.textContent;
  const has = label => controls().some(control => control.getAttribute("aria-label") === label);
  return { env, w, d, box, R, calls, reports, controls, find, open, click, set, closeReport, sent, text, has };
};

const tests = [];
const test = (name, run) => tests.push([name, run]);
const disclosure = c => {
  const notice = [...c.box.querySelectorAll("div")].find(node => node.childElementCount === 0 && /independen/i.test(node.textContent) && /Decky Loader/.test(node.textContent));
  assert.ok(notice, "the Decky independence notice must be visible in this view");
  assert.notEqual(c.w.getComputedStyle(notice).display, "none");
  assert.ok(!notice.closest("[hidden]"));
  const text = notice.textContent;
  assert.match(text, /not affiliated with or endorsed by Decky Loader/i);
  assert.match(text, /Experimental compatibility; results vary/i);
  assert.match(text, /This tab only browses and inspects original packages; it cannot install or run them/i);
  assert.match(text, /Report Framey issues to Framey, not Decky or plugin authors/i);
  assert.ok(text.split(/\s+/).length <= 90, "the headset notice should remain concise");
};
const noAutomaticReport = c => {
  assert.equal(c.reports.length, 0);
  assert.equal(c.sent("catalog_install").length, 0);
  const expected = new Set(["catalog_sources", "catalog_list", "catalog_details", "catalog_assess"]);
  assert.deepEqual(c.calls.filter(request => !expected.has(request.method)).map(request => request.method), [], "opening views and reading errors must not open or publish an issue");
};

test("Decky browsing visibly explains independence, variable compatibility and Framey-owned support", async () => {
  const c = boot();
  await c.open();
  disclosure(c);
  assert.equal(c.sent("catalog_assess").length, 0);
  noAutomaticReport(c);
});

test("Decky details retain the truthful disclosure before any archive inspection", async () => {
  const c = boot();
  await c.open();
  await c.click("Details: Palette helper");
  disclosure(c);
  assert.equal(c.sent("catalog_assess").length, 0);
  noAutomaticReport(c);
});

test("a static archive assessment retains the disclosure and never implies installation or execution", async () => {
  const c = boot();
  await c.open();
  await c.click("Details: Palette helper");
  await c.click("Inspect compatibility");
  disclosure(c);
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.match(c.text(), /No code was executed/);
  noAutomaticReport(c);
});

for (const failure of [
  { name: "catalog load", method: "catalog_list", message: "The upstream catalog is temporarily unavailable" },
  { name: "plugin details", method: "catalog_details", message: "The selected details are temporarily unavailable", details: true },
  { name: "archive inspection", method: "catalog_assess", message: "The selected archive could not be inspected", details: true, inspect: true },
]) {
  test(failure.name + " errors keep the disclosure visible without automatically reporting", async () => {
    const c = boot({ [failure.method]: { error: failure.message, code: "temporarily_unavailable" } });
    await c.open();
    if (failure.details) await c.click("Details: Palette helper");
    if (failure.inspect) await c.click("Inspect compatibility");
    assert.ok(c.text().includes(failure.message));
    disclosure(c);
    noAutomaticReport(c);
  });
}

const noPublicIssueCalls = c => {
  const allowed = new Set(["catalog_sources", "catalog_list", "catalog_details", "catalog_assess", "catalog_install"]);
  assert.deepEqual(c.calls.filter(request => !allowed.has(request.method)).map(request => request.method), []);
  assert.ok(!/issue (?:created|submitted)|report (?:sent|submitted)|successfully published/i.test(c.text()));
};

for (const plugin of [
  { page: "decky_store", name: "Palette helper", version: "1.4.0", source: "decky_stable" },
  { page: "store", name: "Native palette", version: "2.0.0", source: "framey" },
]) {
  test(plugin.source + " details report only the selected plugin after an explicit action", async () => {
    const c = boot();
    await c.open(plugin.page);
    await c.click("Details: " + plugin.name);
    assert.ok(c.has("Report to Framey"));
    assert.equal(c.reports.length, 0);
    await c.click("Report to Framey");
    assert.deepEqual(c.reports, [{ plugin: { name: plugin.name, version: plugin.version, source: plugin.source }, error: "", code: "" }]);
    assert.equal(c.sent("catalog_assess").length, 0);
    noPublicIssueCalls(c);
    await c.closeReport();
  });
}

test("reporting an older selected Decky version does not substitute the latest catalog version", async () => {
  const c = boot();
  await c.open();
  await c.click("Details: Palette helper");
  await c.set("Version", versions[1].identity);
  assert.equal(c.reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(c.reports, [{ plugin: { name: "Palette helper", version: "1.3.0", source: "decky_stable" }, error: "", code: "" }]);
  assert.equal(c.sent("catalog_assess").length, 0);
  noPublicIssueCalls(c);
  await c.closeReport();
});

test("a details failure is reportable with its selected plugin and concise backend error", async () => {
  const error = "The selected plugin details could not be loaded";
  const c = boot({ catalog_details: { error, code: "details_unavailable" } });
  await c.open();
  await c.click("Details: Palette helper");
  assert.ok(c.text().includes(error));
  assert.ok(c.has("Report to Framey"));
  assert.equal(c.reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(c.reports, [{ plugin: { name: "Palette helper", version: "1.4.0", source: "decky_stable" }, error, code: "details_unavailable" }]);
  noPublicIssueCalls(c);
  await c.closeReport();
});

test("inspection failures report the inspected version and preserve static-inspection-only disclosure", async () => {
  const error = "The selected archive could not be inspected";
  const c = boot({ catalog_assess: { error, code: "archive_unavailable" } });
  await c.open();
  await c.click("Details: Palette helper");
  await c.set("Version", versions[1].identity);
  await c.click("Inspect compatibility");
  disclosure(c);
  assert.equal(c.reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(c.reports, [{ plugin: { name: "Palette helper", version: "1.3.0", source: "decky_stable" }, error, code: "archive_unavailable" }]);
  assert.equal(c.sent("catalog_install").length, 0);
  noPublicIssueCalls(c);
  await c.closeReport();
});

test("native installation failure reports Framey-owned context without publishing or retrying installation", async () => {
  const error = "The selected package could not be installed";
  const c = boot({ catalog_install: { error, code: "install_failed" } });
  await c.open("store");
  await c.click("Details: Native palette");
  await c.click("Install Native palette");
  await c.click("Install");
  assert.ok(c.text().includes(error));
  assert.equal(c.reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(c.reports, [{ plugin: { name: "Native palette", version: "2.0.0", source: "framey" }, error, code: "install_failed" }]);
  assert.equal(c.sent("catalog_install").length, 1);
  noPublicIssueCalls(c);
  await c.closeReport();
});

for (const catalog of [{ page: "decky_store", source: "decky_stable" }, { page: "store", source: "framey" }]) {
  test(catalog.source + " catalog failure reports its source without inventing a selected plugin", async () => {
    const error = "The selected catalog could not be refreshed";
    const c = boot({ catalog_list: { error, code: "catalog_unavailable" } });
    await c.open(catalog.page);
    assert.ok(c.text().includes(error));
    assert.ok(c.has("Report to Framey"));
    assert.equal(c.reports.length, 0);
    await c.click("Report to Framey");
    assert.deepEqual(c.reports, [{ plugin: { name: "", version: "", source: catalog.source }, error, code: "catalog_unavailable" }]);
    assert.equal(c.sent("catalog_details").length, 0);
    assert.equal(c.sent("catalog_assess").length, 0);
    noPublicIssueCalls(c);
    await c.closeReport();
  });
}

test("source-configuration failure offers an explicit report without switching away from Framey support", async () => {
  const error = "The testing source is unavailable";
  const readSource = defaults().catalog_sources;
  const c = boot({ catalog_sources: arg => arg.source ? { error, code: "source_unavailable" } : readSource(arg) });
  await c.open("store");
  await c.click("Filters");
  await c.set("Source", "testing");
  assert.ok(c.text().includes(error));
  assert.ok(c.has("Report to Framey"));
  assert.equal(c.reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(c.reports, [{ plugin: { name: "", version: "", source: "framey" }, error, code: "source_unavailable" }]);
  noPublicIssueCalls(c);
  await c.closeReport();
});

test("a saved catalog fetch failure remains browsable and reports only its concise error", async () => {
  const error = "The upstream catalog refresh timed out";
  const c = boot({ catalog_list: arg => Object.assign(status(arg.source), { rows: [decky()], total: 1, offset: 0, limit: 50, categories: ["appearance"],
    origin: "cache", stale: true, warning: "Saved catalog; refresh failed", fetch_error: error }) });
  await c.open();
  disclosure(c);
  assert.ok(c.has("Details: Palette helper"));
  assert.ok(c.has("Report to Framey"));
  assert.equal(c.reports.length, 0);
  await c.click("Report to Framey");
  assert.deepEqual(c.reports, [{ plugin: { name: "", version: "", source: "decky_stable" }, error, code: "" }]);
  assert.equal(c.sent("catalog_assess").length, 0);
  noPublicIssueCalls(c);
  await c.closeReport();
});

test("a missing shared report helper gives a visible unavailable message", async () => {
  const c = boot();
  delete c.w.__S.K.report;
  await c.open();
  await c.click("Details: Palette helper");
  assert.ok(c.has("Report to Framey"));
  await c.click("Report to Framey");
  assert.match(c.d.body.textContent, /Reporting is unavailable/i);
  assert.equal(c.reports.length, 0);
  assert.equal(c.sent("catalog_assess").length, 0);
  noPublicIssueCalls(c);
});

test("the real shared report preview preserves Decky details and requires a manual close", async () => {
  const repository = "https://github.com/chaosfox26/framey";
  const c = boot({ report_draft: {
    repository, title: "Framey compatibility: Palette helper 1.3.0",
    body: "Plugin: Palette helper\nVersion: 1.3.0\nSource: decky_stable\n\n## Steps to reproduce\n1. \n\n## Expected behavior\n\n## Actual behavior\n",
    diagnostics: "", warning: "",
  } }, { sharedReport: true });
  c.w.__S.K.diagnostics = () => [{ code: "core.storage_failed", count: 2 }];
  await c.open();
  await c.click("Details: Palette helper");
  await c.set("Version", versions[1].identity);
  assert.equal(c.sent("report_draft").length, 0);
  const sourceText = c.text();
  const report = c.find("Report to Framey");
  report.focus();
  await c.click("Report to Framey");
  const dialog = c.d.querySelector('[role="dialog"]');
  assert.ok(dialog, "the real helper must open its manual review overlay");
  assert.equal(dialog.getAttribute("aria-modal"), "true");
  assert.ok(!c.box.contains(dialog), "report review must not replace the source details");
  assert.ok(c.box.isConnected);
  assert.equal(c.text(), sourceText);
  assert.equal(c.find("Version").value, versions[1].identity);
  assert.equal(report.getAttribute("aria-disabled"), "true", "the catalog waits for the real preview's close promise");
  assert.equal(c.sent("report_draft").length, 1);
  assert.deepEqual(c.sent("report_draft")[0].arg.plugin, { name: "Palette helper", version: "1.3.0", source: "decky_stable" });
  assert.deepEqual(c.sent("report_draft")[0].arg.diagnostics, [{ code: "core.storage_failed", count: 2 }]);
  assert.equal(dialog.querySelector('[aria-label="Framey issue destination"]').value, repository + "/issues/new");
  const editableBody = [...dialog.querySelectorAll("textarea")].find(node => node.value.includes("Version: 1.3.0"));
  assert.ok(editableBody && !editableBody.readOnly && !editableBody.disabled);
  assert.match(dialog.textContent, /Review and edit this draft/i);
  assert.equal(c.sent("report_link").length, 0);
  assert.equal(c.sent("report_open").length, 0);
  dialog.querySelector('[aria-label="Close report"]').click();
  await settle();
  assert.equal(c.d.querySelector('[role="dialog"]'), null);
  assert.equal(report.getAttribute("aria-disabled"), "false", "manual close resolves the helper promise and unlocks the caller");
  assert.strictEqual(c.d.activeElement, report);
  assert.equal(c.text(), sourceText);
  assert.equal(c.find("Version").value, versions[1].identity);
  assert.equal(c.sent("report_draft").length, 1);
  assert.equal(c.sent("report_link").length, 0);
  assert.equal(c.sent("report_open").length, 0);
  assert.equal(c.sent("catalog_assess").length, 0);
  assert.equal(c.sent("catalog_install").length, 0);
});

(async () => {
  let passed = 0, failed = 0;
  for (const [name, run] of tests) {
    try { await run(); passed++; console.log("ok   " + name); }
    catch (error) { failed++; console.log("FAIL " + name + "\n" + (error.stack || error)); }
  }
  console.log(passed + " passed, " + failed + " failed, " + tests.length + " total");
  process.exit(failed ? 1 : 0);
})();
