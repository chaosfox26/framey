"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"];
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };
const clone = value => value === undefined ? null : JSON.parse(JSON.stringify(value));
const SOURCES = [
  { id: "stable", label: "Stable", url: "https://example.com/native.json" },
  { id: "testing", label: "Testing", url: "https://example.com/native.json" },
  { id: "custom", label: "Custom", url: "" },
  { id: "decky_stable", label: "Decky Store", url: "https://example.com/decky-stable.json" },
  { id: "decky_testing", label: "Decky Store Testing", url: "https://example.com/decky-testing.json" },
];
const catalogSource = id => SOURCES.find(source => source.id === id);
const version = (name, digit) => ({ name, hash: digit.repeat(64), identity: (digit === "c" ? "d" : "e").repeat(64), created: "2026-01-01T00:00:00Z", downloads: 100, updates: "Palette improvements" });
const VERSIONS = [version("1.4.0", "c"), version("1.3.0", "b")];
const decky = (extra = {}) => Object.assign({
  id: "decky-41", name: "Palette helper", version: "1.4.0", description: "A Decky tool for desktop palettes", author: "Module Team",
  tags: ["appearance"], category: "appearance", channel: "stable", version_count: 2,
  format: "decky", kind: "decky", read_only: true, eligible: false, reason: "Needs a Framey port; ARM64 compatibility is unverified",
  architecture: "unknown", platform_declared: false, source: { repository: "", url: catalogSource("decky_stable").url },
  identity: "a".repeat(64), installed: false, linked: false,
  compat: { state: "incompatible", badge: "port", msg: "Decky package format requires a Framey port", reasons: [] },
}, extra);
const status = source => ({ source: catalogSource(source), origin: "downloaded", stale: false, checked_at: 1800000000, downloaded_at: 1800000000, error: null, fetch_error: null, warning: "" });
const list = (rows = [decky()], source = "decky_stable") => Object.assign(status(source), { rows, total: rows.length, offset: 0, limit: 50, categories: ["appearance"] });
const details = (entry = decky(), source = "decky_stable") => Object.assign(status(source), { entry: Object.assign({}, entry, { versions: VERSIONS }) });
const assessed = (architecture = "unknown", extra = {}) => ({
  id: "decky-41", format: "decky", read_only: true, version: VERSIONS[0].name, hash: VERSIONS[0].hash,
  assessment: Object.assign({ state: "port_required", label: "Needs a Framey port", architecture, root_required: null,
    native_binaries: [], has_frontend: true, has_backend: true, flags: [], dependencies: [], reasons: [], warnings: [],
    verified_sha256: true, bytes: 2048, files: 3, not_executed: true,
  }, extra),
});
const defaults = () => ({
  catalog_sources: arg => ({ source: catalogSource(arg && arg.source || "stable"), sources: SOURCES.filter(source => !source.id.startsWith("decky_")), decky_sources: SOURCES.filter(source => source.id.startsWith("decky_")), custom_url: "" }),
  catalog_list: arg => list(arg.source.startsWith("decky_") ? [decky({ channel: arg.source === "decky_testing" ? "testing" : "stable" })] : [], arg.source),
  catalog_details: arg => details(decky(), arg.source),
  catalog_assess: assessed(),
});
const boot = (replies = {}) => {
  const calls = [];
  const R = Object.assign(defaults(), replies);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fyCall = encoded => {
        const request = JSON.parse(encoded);
        calls.push(request);
        if (!request.id) return;
        const handler = R[request.method];
        const result = typeof handler === "function" ? handler(request.arg, request) : handler;
        Promise.resolve(result === undefined ? { error: "Unhandled " + request.method } : result)
          .then(value => w.Framey._reply(request.gen, request.id, clone(value)));
      };
    },
    loadFiles: FILES,
  });
  const { window: w, document: d } = env;
  const box = d.createElement("div");
  d.body.append(box);
  const controls = () => [...box.querySelectorAll("[aria-label]")];
  const find = label => {
    const node = controls().find(control => control.getAttribute("aria-label") === label);
    assert.ok(node, "No control '" + label + "' among " + controls().map(control => control.getAttribute("aria-label")).join(" | "));
    return node;
  };
  const sent = method => calls.filter(request => request.method === method);
  const last = method => sent(method).slice(-1)[0];
  const open = async () => { w.Framey.pages.decky_store.render(box, {}); await settle(); };
  const openNative = async () => { w.Framey.pages.store.render(box, {}); await settle(); };
  const click = async label => { find(label).click(); await settle(); };
  const set = async (label, value, event = "change") => {
    const node = find(label);
    node.value = value;
    node.dispatchEvent(new w.Event(event, { bubbles: true }));
    await settle();
  };
  const text = () => box.textContent;
  const has = label => controls().some(node => node.getAttribute("aria-label") === label);
  const noInstall = () => {
    const labels = controls().map(node => node.getAttribute("aria-label"));
    assert.ok(!labels.some(label => /^(?:Install|Update)(?: |$)/.test(label)), "Decky entries must not expose native installation or review actions: " + labels.join(" | "));
    for (const method of ["catalog_install", "install", "add", "update"]) assert.equal(sent(method).length, 0, method + " must not be invoked for a Decky entry");
    assert.ok(!/Review installation/.test(text()));
  };
  return { env, w, d, box, R, calls, controls, find, sent, last, open, openNative, click, set, text, has, noInstall };
};

const tests = [];
const test = (name, run) => tests.push([name, run]);

test("the separate Decky Store page identifies the upstream catalog and offers only Decky sources", async () => {
  const c = boot();
  await c.open();
  assert.equal(c.last("catalog_list").arg.source, "decky_stable");
  assert.ok(c.has("Details: Palette helper"));
  assert.equal(c.box.querySelector('[role="heading"]').textContent, "Decky Store");
  assert.match(c.text(), /upstream|deckbrew|Decky plugin catalog/i);
  await c.click("Filters");
  const choices = [...c.find("Source").options].map(option => [option.value, option.textContent]);
  assert.deepEqual(choices, SOURCES.filter(source => source.id.startsWith("decky_")).map(source => [source.id, source.label]));
  assert.equal(c.find("Source").value, "decky_stable");
  c.noInstall();
  assert.equal(c.sent("catalog_assess").length, 0);
});

test("Decky testing selection and search use catalog reads without inspecting archives", async () => {
  const c = boot();
  await c.open();
  await c.click("Filters");
  await c.set("Source", "decky_testing");
  assert.ok(!c.sent("catalog_sources").some(request => request.arg && /^decky_/.test(request.arg.source || "")), "Decky selection must not change the native catalog preference");
  assert.equal(c.last("catalog_list").arg.source, "decky_testing");
  await c.set("Search modules", "palette", "input");
  c.R.catalog_list = list([decky({ name: "Testing palette", channel: "testing" })], "decky_testing");
  await c.click("Search");
  assert.equal(c.last("catalog_list").arg.query, "palette");
  assert.equal(c.last("catalog_list").arg.source, "decky_testing");
  assert.ok(c.has("Details: Testing palette") && !c.has("Details: Palette helper"));
  assert.equal(c.sent("catalog_assess").length, 0);
  c.noInstall();
});

test("Decky source selection stays isolated from the native Store across page navigation", async () => {
  const c = boot();
  await c.openNative();
  assert.equal(c.last("catalog_list").arg.source, "stable");
  await c.click("Filters");
  assert.deepEqual([...c.find("Source").options].map(option => option.value), ["stable", "testing", "custom"]);
  await c.set("Source", "testing");
  assert.equal(c.last("catalog_list").arg.source, "testing");
  c.R.catalog_sources = () => ({ source: catalogSource("testing"), sources: SOURCES.filter(source => !source.id.startsWith("decky_")), decky_sources: SOURCES.filter(source => source.id.startsWith("decky_")), custom_url: "" });
  await c.open();
  assert.equal(c.last("catalog_list").arg.source, "decky_stable");
  await c.click("Filters");
  await c.set("Source", "decky_testing");
  assert.equal(c.last("catalog_list").arg.source, "decky_testing");
  await c.openNative();
  assert.equal(c.last("catalog_list").arg.source, "testing");
  await c.click("Filters");
  assert.equal(c.find("Source").value, "testing");
  assert.ok(!c.sent("catalog_sources").some(request => request.arg && /^decky_/.test(request.arg.source || "")));
  assert.equal(c.sent("catalog_assess").length, 0);
});

test("Decky details show format, port requirement and unverified ARM64 state", async () => {
  const c = boot();
  await c.open();
  await c.click("Details: Palette helper");
  assert.deepEqual(c.last("catalog_details").arg, { id: "decky-41", source: "decky_stable" });
  assert.match(c.text(), /Decky plugin/i);
  assert.ok([...c.box.querySelectorAll("div")].some(node => node.childElementCount === 0 && node.textContent === "Not compatible"));
  assert.match(c.text(), /built for Decky|Decky.{0,20}interface/i);
  assert.match(c.text(), /Requires a Framey port|Needs a Framey port|needs a Framey version/i);
  assert.match(c.text(), /Not checked/i);
  assert.match(c.text(), /ARM64[^.]*unverified|ARM64[^.]*unknown|ARM64 compatibility has not/i);
  assert.ok(!/Framey-native module/.test(c.text()));
  assert.equal(c.sent("catalog_assess").length, 0);
  c.noInstall();
});

test("the version selector is read-only metadata until an explicit compatibility inspection", async () => {
  const c = boot();
  await c.open();
  await c.click("Details: Palette helper");
  const selector = c.find("Version");
  assert.deepEqual([...selector.options].map(option => option.textContent), VERSIONS.map(item => item.name));
  assert.equal(c.sent("catalog_assess").length, 0);
  assert.equal(c.sent("catalog_details").length, 1);
  await c.set("Version", selector.options[1].value);
  assert.equal(c.find("Version").value, selector.options[1].value);
  assert.equal(c.sent("catalog_assess").length, 0, "choosing a version must not download its archive");
  assert.equal(c.sent("catalog_details").length, 1);
  c.noInstall();
});

for (const bad of [
  { label: "forged eligibility", fields: { eligible: true, read_only: true } },
  { label: "contradictory update eligibility", fields: { eligible: true, read_only: false, installed: true, update_available: true } },
  { label: "missing Decky format markers", fields: { eligible: true, read_only: false, format: "plugin", kind: "plugin" } },
]) {
  test("Decky never exposes native installation with " + bad.label, async () => {
    const row = decky(Object.assign({}, bad.fields, { compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] } }));
    const c = boot({ catalog_list: list([row]), catalog_details: details(row) });
    await c.open();
    c.noInstall();
    await c.click("Details: Palette helper");
    c.noInstall();
    assert.match(c.text(), /Requires a Framey port|Needs a Framey port|needs a Framey version/i);
    assert.ok(![...c.box.querySelectorAll("div")].some(node => node.childElementCount === 0 && node.textContent === "Compatible"), "native compatibility text must not override the Decky format requirement");
    assert.equal(c.sent("catalog_assess").length, 0);
  });
}

test("compatibility inspection sends both reviewed identities for the selected version", async () => {
  const c = boot({ catalog_assess: Object.assign(assessed(), { version: VERSIONS[1].name, hash: VERSIONS[1].hash }) });
  await c.open();
  await c.click("Details: Palette helper");
  const options = [...c.find("Version").options];
  await c.set("Version", options[1].value);
  assert.equal(c.sent("catalog_assess").length, 0);
  await c.click("Inspect compatibility");
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.deepEqual(c.last("catalog_assess").arg, { id: "decky-41", source: "decky_stable", identity: "a".repeat(64), version_identity: VERSIONS[1].identity, confirm: true });
  assert.match(c.text(), /Needs a Framey port/i);
  assert.match(c.text(), /not executed|without running plugin code|no plugin code runs|nothing was executed|no code was executed/i);
  c.noInstall();
});

test("older-version architecture evidence is labeled with that selected version and never applied to the latest browse row", async () => {
  const response = Object.assign(assessed("x86_64", {
    native_binaries: [{ path: "bin/older-helper", format: "ELF", architecture: "x86_64" }],
  }), { version: VERSIONS[1].name, hash: VERSIONS[1].hash });
  const c = boot({ catalog_assess: response });
  await c.open();
  await c.click("Details: Palette helper");
  await c.set("Version", VERSIONS[1].identity);
  await c.click("Inspect compatibility");
  assert.equal(c.last("catalog_assess").arg.version_identity, VERSIONS[1].identity);
  const finding = [...c.box.querySelectorAll("div")].find(node => node.childElementCount === 0 && node.textContent === "Needs an ARM64 build");
  assert.ok(finding, "the x86 archive finding must remain visible");
  assert.ok(finding.parentElement.textContent.includes("Selected version 1.3.0"), "the prominent architecture finding must identify the inspected version");
  assert.ok(!finding.parentElement.textContent.includes("Selected version 1.4.0"));
  assert.equal(c.find("Version").value, VERSIONS[1].identity);
  await c.click("Back");
  assert.ok(c.has("Details: Palette helper"));
  assert.ok(c.text().includes("v1.4.0"));
  assert.ok(!c.text().includes("Needs an ARM64 build"), "an older-version archive must not determine the latest catalog row's architecture state");
  assert.match(c.text(), /Not checked/);
  c.noInstall();
});

test("a different archive with the same version name cannot supply the default build's architecture evidence", async () => {
  const otherBuild = Object.assign({}, VERSIONS[1], { name: VERSIONS[0].name });
  const detailResponse = details();
  detailResponse.entry.versions = [VERSIONS[0], otherBuild];
  const inspectionResponse = Object.assign(assessed("x86_64", {
    native_binaries: [{ path: "bin/alternate-build", format: "ELF", architecture: "x86_64" }],
  }), { version: otherBuild.name, hash: otherBuild.hash });
  const c = boot({ catalog_details: detailResponse, catalog_assess: inspectionResponse });
  await c.open();
  await c.click("Details: Palette helper");
  assert.equal(c.find("Version").value, VERSIONS[0].identity);
  await c.set("Version", otherBuild.identity);
  await c.click("Inspect compatibility");
  assert.equal(c.last("catalog_assess").arg.version_identity, otherBuild.identity);
  assert.ok(c.text().includes("Needs an ARM64 build"));
  assert.ok(c.text().includes("bin/alternate-build"));
  await c.click("Back");
  assert.ok(c.has("Details: Palette helper") && c.text().includes("v1.4.0"));
  assert.ok(!c.text().includes("Needs an ARM64 build"), "matching display names do not make different archive hashes or identities interchangeable");
  assert.match(c.text(), /Not checked/);
  c.noInstall();
});

test("a selected version marked uninspectable disables inspection and explains the missing package link", async () => {
  const response = details();
  response.entry.versions = [Object.assign({}, VERSIONS[0], { inspectable: true }), Object.assign({}, VERSIONS[1], { inspectable: false })];
  const c = boot({ catalog_details: response });
  await c.open();
  await c.click("Details: Palette helper");
  assert.equal(c.find("Inspect compatibility").getAttribute("aria-disabled"), "false");
  await c.set("Version", VERSIONS[1].identity);
  const inspect = c.find("Inspect compatibility");
  assert.equal(inspect.getAttribute("aria-disabled"), "true");
  assert.ok(c.text().includes("No supported package link is available for this version."));
  await c.click("Inspect compatibility");
  inspect.dispatchEvent(new c.w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await settle();
  assert.equal(c.sent("catalog_assess").length, 0);
  await c.set("Version", VERSIONS[0].identity);
  assert.equal(c.find("Inspect compatibility").getAttribute("aria-disabled"), "false");
  assert.ok(!c.text().includes("No supported package link is available for this version."));
  assert.equal(c.sent("catalog_assess").length, 0);
  c.noInstall();
});

for (const evidence of [
  { architecture: "x86_64", binaries: [{ path: "bin/helper", format: "ELF", architecture: "x86_64" }], reason: "Contains x86-64 native binaries that cannot run on ARM64" },
  { architecture: "aarch64", binaries: [{ path: "bin/helper", format: "ELF", architecture: "aarch64" }], reason: "AArch64 evidence does not prove that the Decky plugin runs in Framey" },
  { architecture: "unknown", binaries: [], reason: "No native binaries were found; runtime compatibility remains unverified" },
]) {
  test(evidence.architecture + " archive evidence preserves the Framey port requirement", async () => {
    const c = boot({ catalog_assess: assessed(evidence.architecture, { native_binaries: evidence.binaries, reasons: [evidence.reason] }) });
    await c.open();
    await c.click("Details: Palette helper");
    await c.click("Inspect compatibility");
    assert.ok(c.text().includes(evidence.reason));
    if (evidence.architecture === "x86_64") assert.match(c.text(), /Needs an ARM64 build/i);
    if (evidence.binaries.length) {
      assert.ok(c.text().includes("bin/helper"));
      assert.ok(c.text().includes("ELF"));
      assert.ok(c.text().includes(evidence.architecture));
    } else {
      assert.match(c.text(), /No recognized native binaries|No native binaries/i);
      assert.match(c.text(), /unsupported|unverified|unknown/i);
    }
    assert.match(c.text(), /Needs a Framey port|Requires a Framey port|needs a Framey version/i);
    assert.ok(!/Framey compatible|ready to run|safe to run/i.test(c.text()));
    c.noInstall();
  });
}

for (const condition of ["no versions", "no entry identity", "no version identity", "stale source"]) {
  test(condition + " prevents an archive inspection", async () => {
    const response = details();
    if (condition === "no versions") response.entry.versions = [];
    if (condition === "no entry identity") delete response.entry.identity;
    if (condition === "no version identity") response.entry.versions = [Object.assign({}, VERSIONS[0], { identity: "" })];
    if (condition === "stale source") Object.assign(response, { origin: "cache", stale: true, warning: "Saved catalog; refresh failed" });
    const c = boot({ catalog_details: response });
    await c.open();
    await c.click("Details: Palette helper");
    if (c.has("Inspect compatibility")) {
      assert.equal(c.find("Inspect compatibility").getAttribute("aria-disabled"), "true");
      await c.click("Inspect compatibility");
    }
    assert.equal(c.sent("catalog_assess").length, 0);
    c.noInstall();
  });
}

for (const field of ["verified_sha256", "not_executed"]) {
  test("an assessment without " + field + " proof is rejected visibly", async () => {
    const response = assessed("aarch64", { [field]: false, native_binaries: [{ path: "unconfirmed/bin/helper", format: "ELF", architecture: "aarch64" }] });
    const c = boot({ catalog_assess: response });
    await c.open();
    await c.click("Details: Palette helper");
    await c.click("Inspect compatibility");
    assert.match(c.text(), /inspection failed/i);
    assert.match(c.text(), /could not be confirmed|invalid|unverified/i);
    assert.ok(!c.text().includes("unconfirmed/bin/helper"));
    assert.ok(!c.text().includes("Archive SHA-256 verified"));
    c.noInstall();
  });
}

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
