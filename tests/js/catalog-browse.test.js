"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"];
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };
const copy = value => value === undefined ? null : JSON.parse(JSON.stringify(value));
const entry = (id, name, extra = {}) => Object.assign({
  id, name, version: "2.0.0", description: name + " module description", category: "system", tags: ["utility"],
  source: { repository: "https://example.com/modules/" + id, commit: "a".repeat(40), path: "" },
  homepage: "https://example.com/" + id, compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] },
  installed: false, linked: false, eligible: true, reason: "", identity: "b".repeat(64),
  channel: "stable", platform: ["linux-aarch64"], platform_declared: true, kind: "module",
}, extra);
const source = id => ({ id, label: id === "testing" ? "Testing" : "Stable", url: "https://example.com/" + id + ".json" });
const listing = (rows = [entry("alpha", "Alpha"), entry("beta", "Beta", { category: "appearance" })], extra = {}) => Object.assign({
  rows, total: rows.length, offset: 0, limit: 50, categories: ["appearance", "system"], source: source("stable"),
  origin: "downloaded", stale: false, checked_at: 1800000000, downloaded_at: 1800000000, error: "", warning: "",
}, extra);
const details = (row, extra = {}) => Object.assign({ entry: row, source: source("stable"), origin: "downloaded", stale: false, warning: "", error: "" }, extra);
const defaults = () => ({
  catalog_sources: arg => ({ source: source(arg && arg.source || "stable"), sources: [source("stable"), source("testing"), { id: "custom", label: "Custom", url: "" }], custom_url: "" }),
  catalog_list: listing(),
  catalog_details: ({ id }) => details(entry(id, id === "alpha" ? "Alpha" : "Beta")),
  catalog_install: ({ id }) => ({ ok: id, version: "2.0.0" }),
});

const boot = replies => {
  const calls = [];
  const R = Object.assign(defaults(), replies);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fyCall = encoded => {
        const call = JSON.parse(encoded);
        calls.push(call);
        if (!call.id) return;
        const reply = call.method in R ? R[call.method] : { error: "unhandled " + call.method };
        Promise.resolve(typeof reply === "function" ? reply(call.arg, call) : reply).then(value => w.Framey._reply(call.gen, call.id, copy(value)));
      };
    },
    loadFiles: FILES,
  });
  const { window: w, document: d } = env;
  const FY = w.Framey;
  const box = d.createElement("div");
  d.body.append(box);
  const controls = () => [...box.querySelectorAll("[aria-label]")];
  const find = label => {
    const control = controls().find(node => node.getAttribute("aria-label") === label);
    assert.ok(control, "No control '" + label + "' among " + controls().map(node => node.getAttribute("aria-label")).join(" | "));
    return control;
  };
  const sent = method => calls.filter(call => call.method === method);
  const last = method => sent(method).slice(-1)[0];
  const open = async () => { FY.pages.store.render(box, {}); await settle(); };
  const click = async label => { find(label).click(); await settle(); };
  const set = async (label, value, type = "change") => {
    const control = find(label);
    control.value = value;
    control.dispatchEvent(new w.Event(type, { bubbles: true }));
    await settle();
  };
  const text = () => box.textContent;
  const has = label => controls().some(node => node.getAttribute("aria-label") === label);
  return { env, w, d, FY, box, R, calls, controls, find, sent, last, open, click, set, text, has };
};

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("Store loads catalog rows and performs only read calls while browsing", async () => {
  const c = boot();
  await c.open();
  assert.ok(c.text().includes("Store"));
  assert.ok(c.text().includes("Alpha") && c.text().includes("Beta"));
  assert.ok(c.text().includes("Alpha module description"));
  assert.ok(c.has("Details: Alpha") && c.has("Details: Beta"));
  assert.ok(c.has("Search") && c.has("Refresh") && c.has("Filters"));
  assert.ok(!/Coming soon/.test(c.text()));
  assert.equal(c.sent("catalog_sources").length, 1);
  assert.equal(c.sent("catalog_list").length, 1);
  assert.deepEqual(c.last("catalog_list").arg, { query: "", category: "all", sort: "name", source: "stable", offset: 0, limit: 50, refresh: false });
  assert.equal(c.sent("catalog_install").length, 0);
  assert.equal(c.sent("install").length, 0);
  assert.equal(c.sent("add").length, 0);
});

test("loading is visible until a response arrives, and an empty catalog gives a useful empty state", async () => {
  let resolveList;
  const c = boot({ catalog_list: () => new Promise(resolve => { resolveList = resolve; }) });
  c.FY.pages.store.render(c.box, {});
  await settle();
  assert.match(c.text(), /loading/i);
  assert.ok(!c.has("Details: Alpha"));
  resolveList(listing([]));
  await settle();
  assert.ok(!/loading/i.test(c.text()));
  assert.match(c.text(), /no modules|no results|nothing found/i);
  assert.ok(c.has("Search") && c.has("Refresh"));
  assert.equal(c.sent("catalog_install").length, 0);
});

test("catalog failure is readable and retry recovers by asking the backend again", async () => {
  const c = boot({ catalog_list: { error: "Catalog temporarily unavailable", code: "unavailable" } });
  await c.open();
  assert.ok(c.text().includes("Catalog temporarily unavailable"));
  assert.ok(c.has("Retry"));
  c.R.catalog_list = listing([entry("recovered", "Recovered module")]);
  await c.click("Retry");
  assert.equal(c.sent("catalog_list").length, 2);
  assert.ok(c.has("Details: Recovered module"));
  assert.ok(!c.text().includes("Catalog temporarily unavailable"));
});

test("search sends the typed query and displays the returned matching rows", async () => {
  const c = boot();
  await c.open();
  await c.set("Search modules", "  desktop colors  ", "input");
  assert.equal(c.sent("catalog_list").length, 1, "typing alone must not silently install or replace results");
  c.R.catalog_list = listing([entry("colors", "Color picker", { description: "Find a desktop palette", category: "appearance" })]);
  await c.click("Search");
  assert.equal(c.last("catalog_list").arg.query, "desktop colors");
  assert.equal(c.last("catalog_list").arg.offset, 0);
  assert.ok(c.has("Details: Color picker"));
  assert.ok(!c.has("Details: Alpha") && !c.has("Details: Beta"));
  assert.equal(c.sent("catalog_install").length, 0);
});

test("category and sort select values reach the server and returned order is preserved", async () => {
  const c = boot();
  await c.open();
  await c.click("Filters");
  c.R.catalog_list = listing([entry("zeta", "Zeta theme", { category: "appearance" }), entry("amber", "Amber theme", { category: "appearance" })]);
  await c.set("Category", "appearance");
  assert.equal(c.last("catalog_list").arg.category, "appearance");
  assert.equal(c.last("catalog_list").arg.offset, 0);
  await c.set("Sort", "updated");
  assert.equal(c.last("catalog_list").arg.category, "appearance");
  assert.equal(c.last("catalog_list").arg.sort, "updated");
  const titles = c.controls().map(node => node.getAttribute("aria-label")).filter(label => label.startsWith("Details: "));
  assert.deepEqual(titles, ["Details: Zeta theme", "Details: Amber theme"]);
  assert.ok(!c.has("Details: Alpha"));
});

test("changing source resets the result offset and passes the selected source", async () => {
  const c = boot();
  await c.open();
  await c.click("Filters");
  c.R.catalog_list = listing([entry("preview", "Preview tool", { channel: "testing" })], { source: source("testing") });
  await c.set("Source", "testing");
  assert.equal(c.last("catalog_list").arg.source, "testing");
  assert.equal(c.last("catalog_list").arg.offset, 0);
  assert.ok(c.has("Details: Preview tool"));
  assert.ok(!c.has("Details: Alpha"));
  assert.equal(c.sent("catalog_install").length, 0);
});

test("the saved source is read before the first list request", async () => {
  const c = boot({
    catalog_sources: { source: source("testing"), sources: [source("stable"), source("testing")], custom_url: "" },
    catalog_list: listing([entry("preview", "Saved preview", { channel: "testing" })], { source: source("testing") }),
  });
  await c.open();
  assert.deepEqual(c.calls.map(call => call.method), ["catalog_sources", "catalog_list"]);
  assert.equal(c.last("catalog_list").arg.source, "testing");
  assert.ok(c.has("Details: Saved preview"));
  await c.click("Filters");
  assert.equal(c.find("Source").value, "testing");
});

test("Refresh asks for a fresh response while preserving the current browse filters", async () => {
  const c = boot();
  await c.open();
  await c.set("Search modules", "palette", "input");
  await c.click("Search");
  await c.click("Filters");
  await c.set("Category", "appearance");
  await c.set("Sort", "relevance");
  c.R.catalog_list = listing([entry("new", "New palette", { category: "appearance" })]);
  await c.click("Refresh");
  assert.deepEqual(c.last("catalog_list").arg, { query: "palette", category: "appearance", sort: "relevance", source: "stable", offset: 0, limit: 50, refresh: true });
  assert.equal(c.find("Search modules").value, "palette");
  assert.equal(c.find("Category").value, "appearance");
  assert.equal(c.find("Sort").value, "relevance");
  assert.ok(c.has("Details: New palette"));
  assert.equal(c.sent("catalog_install").length, 0);
});

test("Reset filters clears query, category and sort and shows the unfiltered response", async () => {
  const c = boot();
  await c.open();
  await c.set("Search modules", "missing item", "input");
  c.R.catalog_list = listing([]);
  await c.click("Search");
  await c.click("Filters");
  await c.set("Category", "appearance");
  await c.set("Sort", "updated");
  assert.match(c.text(), /no modules match|no results/i);
  c.R.catalog_list = listing();
  await c.click("Reset filters");
  assert.deepEqual(c.last("catalog_list").arg, { query: "", category: "all", sort: "name", source: "stable", offset: 0, limit: 50, refresh: false });
  assert.equal(c.find("Search modules").value, "");
  assert.equal(c.find("Category").value, "all");
  assert.equal(c.find("Sort").value, "name");
  assert.ok(c.has("Details: Alpha") && c.has("Details: Beta"));
});

test("a custom catalog URL stays bound to list, details and the confirmed install", async () => {
  const row = entry("custom", "Custom tool");
  const url = "https://example.com/catalog/custom.json";
  const customSource = { id: "custom", label: "Custom", url };
  const c = boot();
  await c.open();
  await c.click("Filters");
  const before = c.sent("catalog_list").length;
  await c.set("Source", "custom");
  assert.equal(c.sent("catalog_list").length, before, "a custom source needs its URL before it is fetched");
  assert.equal(c.find("Custom catalog URL").value, "");
  await c.set("Custom catalog URL", url, "input");
  c.R.catalog_list = listing([row], { source: customSource });
  c.R.catalog_details = details(row, { source: customSource });
  await c.click("Apply source");
  assert.deepEqual(c.last("catalog_sources").arg, { source: "custom", url });
  assert.equal(c.last("catalog_list").arg.source, "custom");
  assert.equal(c.last("catalog_list").arg.url, url);
  await c.click("Details: Custom tool");
  assert.deepEqual(c.last("catalog_details").arg, { id: "custom", source: "custom", url });
  await c.click("Install Custom tool");
  assert.equal(c.sent("catalog_install").length, 0);
  await c.click("Install");
  assert.deepEqual(c.last("catalog_install").arg, { id: "custom", source: "custom", url, identity: row.identity, confirm: true });
});

test("details uses the selected id and Back restores the existing query and results", async () => {
  const row = entry("palette", "Palette", { description: "Pick desktop colors", category: "appearance" });
  const c = boot({ catalog_details: details(row) });
  await c.open();
  await c.set("Search modules", "palette", "input");
  c.R.catalog_list = listing([row]);
  await c.click("Search");
  const reads = c.sent("catalog_list").length;
  await c.click("Details: Palette");
  assert.deepEqual(c.last("catalog_details").arg, { id: "palette", source: "stable" });
  assert.ok(c.text().includes("Pick desktop colors"));
  assert.ok(c.text().includes("2.0.0"));
  assert.ok(c.has("Install Palette") && c.has("Back"));
  assert.equal(c.sent("catalog_install").length, 0);
  await c.click("Back");
  assert.equal(c.find("Search modules").value, "palette");
  assert.ok(c.has("Details: Palette") && !c.has("Details: Alpha"));
  assert.equal(c.sent("catalog_list").length, reads);
});

test("installed modules are identified and cannot start a second installation", async () => {
  const row = entry("alpha", "Alpha", { installed: true, eligible: false, reason: "Already installed" });
  const c = boot({ catalog_list: listing([row]), catalog_details: details(row) });
  await c.open();
  assert.match(c.text(), /installed/i);
  await c.click("Details: Alpha");
  assert.match(c.text(), /installed/i);
  if (c.has("Install Alpha")) {
    const install = c.find("Install Alpha");
    assert.equal(install.getAttribute("aria-disabled"), "true");
    install.click();
    await settle();
  }
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(!c.has("Cancel"), "an installed entry must not open an install confirmation");
});

test("backend-ineligible compatibility blocks installation even when source metadata is present", async () => {
  const row = entry("other", "Other platform", {
    eligible: false, reason: "Requires a different platform", platform: ["windows-x86_64"],
    compat: { state: "incompatible", badge: "bad", msg: "Requires a different platform", reasons: ["platform"] },
  });
  const c = boot({ catalog_list: listing([row]), catalog_details: details(row) });
  await c.open();
  await c.click("Details: Other platform");
  assert.ok(c.text().includes("Requires a different platform"));
  if (c.has("Install Other platform")) {
    assert.equal(c.find("Install Other platform").getAttribute("aria-disabled"), "true");
    await c.click("Install Other platform");
  }
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(!c.has("Cancel"));
});

for (const cached of [
  { origin: "cache", stale: true, warning: "Catalog refresh failed; using saved entries", notice: /offline|saved catalog/i },
  { origin: "bundled", stale: false, warning: "Connect to check for current releases", notice: /bundled catalog/i },
]) {
  test(cached.origin + " catalog identifies its saved state and blocks installs until refreshed", async () => {
    const row = entry("alpha", "Alpha");
    const state = { origin: cached.origin, stale: cached.stale, warning: cached.warning };
    const c = boot({
      catalog_list: listing([row], state),
      catalog_details: details(row, state),
    });
    await c.open();
    assert.match(c.text(), cached.notice);
    assert.ok(c.text().includes(cached.warning));
    assert.ok(c.has("Details: Alpha"), "saved entries remain browsable");
    await c.click("Details: Alpha");
    assert.equal(c.find("Install Alpha").getAttribute("aria-disabled"), "true");
    await c.click("Install Alpha");
    assert.equal(c.sent("catalog_install").length, 0);
    assert.ok(!c.has("Cancel"));
    assert.match(c.text(), /refresh.*before installing|connect.*refresh/i);
  });
}

test("a current cached entry remains installable and is not described as offline", async () => {
  const row = entry("alpha", "Alpha");
  const c = boot({ catalog_list: listing([row], { origin: "cache", stale: false }), catalog_details: details(row, { origin: "cache", stale: false }) });
  await c.open();
  assert.match(c.text(), /saved catalog/i);
  assert.ok(!/offline/i.test(c.text()));
  await c.click("Details: Alpha");
  assert.equal(c.find("Install Alpha").getAttribute("aria-disabled"), "false");
  await c.click("Install Alpha");
  assert.ok(c.has("Install") && c.has("Cancel"));
  assert.equal(c.sent("catalog_install").length, 0);
});

test("installation requires explicit review confirmation, and Cancel leaves the entry available", async () => {
  const row = entry("alpha", "Alpha");
  const c = boot({ catalog_list: listing([row]), catalog_details: details(row) });
  await c.open();
  await c.click("Details: Alpha");
  await c.click("Install Alpha");
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(c.text().includes("Alpha") && c.text().includes("2.0.0"));
  assert.ok(c.has("Install") && c.has("Cancel"));
  assert.strictEqual(c.d.activeElement, c.find("Cancel"));
  await c.click("Cancel");
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(c.has("Install Alpha"));
  await c.click("Install Alpha");
  await c.click("Install");
  assert.equal(c.sent("catalog_install").length, 1);
  assert.deepEqual(c.last("catalog_install").arg, { id: "alpha", source: "stable", identity: "b".repeat(64), confirm: true });
  assert.equal(c.sent("install").length, 0);
  assert.equal(c.sent("add").length, 0);
});

test("a managed update uses reviewed catalog identity and reloads installed state after confirmation", async () => {
  let updated = false;
  const available = entry("alpha", "Alpha", { installed: true, installed_version: "1.0.0", update_available: true, eligible: true });
  const current = entry("alpha", "Alpha", { installed: true, installed_version: "2.0.0", update_available: false, eligible: false, reason: "Already installed" });
  const c = boot({
    catalog_list: listing([available]),
    catalog_details: () => details(updated ? current : available),
    catalog_install: ({ id }) => { updated = true; return { ok: id, version: "2.0.0" }; },
  });
  await c.open();
  await c.click("Details: Alpha");
  assert.ok(c.has("Update Alpha"));
  await c.click("Update Alpha");
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(c.has("Install") && c.has("Cancel"));
  await c.click("Install");
  assert.deepEqual(c.last("catalog_install").arg, { id: "alpha", source: "stable", identity: available.identity, confirm: true });
  assert.equal(c.sent("catalog_details").length, 2, "the final card must come from an authoritative details reply");
  assert.ok(!c.has("Update Alpha"));
  assert.match(c.text(), /installed/i);
  assert.equal(c.sent("update").length, 0, "catalog updates must stay bound to the reviewed catalog entry");
});

test("unsupported native architecture has a plain incompatibility reason in the list and details", async () => {
  const reason = "Built for x86-64 Linux; this device uses ARM64 Linux.";
  const row = entry("native-other", "Native helper", {
    kind: "plugin", platform: ["linux-x86_64"], platform_declared: true, eligible: false, reason,
    compat: { state: "incompatible", badge: "platform", msg: reason, reasons: [reason] },
    compatibility: { status: "incompatible", label: "Not compatible", reason, reasons: [reason], basis: "declared_requirements", runtime_verified: false },
  });
  const c = boot({ catalog_list: listing([row]), catalog_details: details(row) });
  await c.open();
  assert.ok(c.text().includes("Not compatible") && c.text().includes(reason));
  await c.click("Details: Native helper");
  assert.ok(c.text().includes("Not compatible") && c.text().includes(reason));
  assert.equal(c.find("Install Native helper").getAttribute("aria-disabled"), "true");
  await c.click("Install Native helper");
  assert.equal(c.sent("catalog_install").length, 0);
});

test("a compatible native declaration explains its basis without claiming runtime verification", async () => {
  const reason = "Declared Framey, API and ARM64 Linux requirements match this device.";
  const row = entry("native-match", "Native palette", {
    kind: "plugin", platform: ["linux-aarch64"], platform_declared: true,
    compatibility: { status: "compatible", label: "Compatible", reason, reasons: [], basis: "declared_requirements", runtime_verified: false },
  });
  const c = boot({ catalog_list: listing([row]), catalog_details: details(row) });
  await c.open();
  assert.ok(c.text().includes("Compatible") && c.text().includes(reason));
  await c.click("Details: Native palette");
  assert.ok(c.text().includes("Compatible") && c.text().includes(reason));
  assert.match(c.text(), /declared (?:requirements|Framey)/i);
  assert.match(c.text(), /runtime.{0,50}(?:not|unverified)|(?:not|unverified).{0,50}runtime|(?:not|has not).{0,30}tested/i);
  assert.equal(c.find("Install Native palette").getAttribute("aria-disabled"), "false");
  assert.equal(c.sent("catalog_install").length, 0);
});

test("missing native platform declarations stay Not checked even when legacy compatibility says ok", async () => {
  const reason = "This entry does not declare ARM64 Linux support.";
  const row = entry("native-unknown", "Unchecked helper", {
    kind: "plugin", platform: [], platform_declared: false, eligible: true,
    compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] },
    compatibility: { status: "unverified", label: "Not checked", reason, reasons: [reason], basis: "declared_requirements", runtime_verified: false },
  });
  const c = boot({ catalog_list: listing([row]), catalog_details: details(row) });
  await c.open();
  assert.ok(c.text().includes("Not checked") && c.text().includes(reason));
  await c.click("Details: Unchecked helper");
  assert.ok(c.text().includes("Not checked") && c.text().includes(reason));
  assert.ok(![...c.box.querySelectorAll("div")].some(node => node.childElementCount === 0 && node.textContent === "Compatible"));
  assert.equal(c.find("Install Unchecked helper").getAttribute("aria-disabled"), "false");
  assert.equal(c.sent("catalog_install").length, 0);
});

(async () => {
  let passed = 0, failed = 0;
  for (const [name, run] of tests) {
    try {
      await run();
      passed++;
      console.log("ok   " + name);
    } catch (error) {
      failed++;
      console.log("FAIL " + name + "\n" + (error.stack || error));
    }
  }
  console.log(passed + " passed, " + failed + " failed, " + tests.length + " total");
  process.exit(failed ? 1 : 0);
})();
