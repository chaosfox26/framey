"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js", "fy-settings.js"];
const source = () => ({ id: "stable", label: "Stable", url: "https://catalog.example.invalid/stable.json" });
const entry = extra => Object.assign({
  id: "sample-module", name: "Sample module", version: "1.0.0",
  description: "A sample module.", category: "Utilities", tags: ["sample"],
  source: { repository: "https://github.com/example/sample-module", commit: "a".repeat(40), path: "" },
  homepage: "https://example.invalid/module", compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] },
  installed: false, linked: false, eligible: true, reason: "", identity: "b".repeat(64),
  channel: "stable", platform: ["linux-aarch64"], platform_declared: true, kind: "plugin",
}, extra);
const list = rows => ({ rows, total: rows.length, offset: 0, limit: 50, categories: ["Utilities"], source: source(), origin: "downloaded", stale: false, error: "", warning: "" });
const deckySource = () => ({ id: "decky_stable", label: "Decky stable", url: "https://plugins.deckbrew.xyz/plugins" });
const deckyEntry = extra => ({
  id: "decky-1", name: "Sample Decky module", author: "Example author", description: "A sample Decky module.",
  tags: ["utility"], category: "utility", version: "1.0.0", version_count: 1,
  format: "decky", kind: "decky", read_only: true, eligible: false,
  reason: "Needs a Framey port; ARM64 compatibility is unverified", identity: "c".repeat(64),
  compat: { state: "incompatible", badge: "port", msg: "Decky package format requires a Framey port", reasons: [] },
  architecture: "unknown", platform_declared: false, source: { repository: "", url: deckySource().url },
  versions: [{ name: "1.0.0", hash: "d".repeat(64), created: "2026-10-20T18:57:24Z", downloads: 0, updates: 0, identity: "e".repeat(64) }],
  ...extra,
});
const deckyAssessment = extra => ({
  id: "decky-1", format: "decky", read_only: true, version: "1.0.0", hash: "d".repeat(64),
  assessment: {
    state: "port_required", label: "Needs a Framey port", architecture: "unknown", root_required: null,
    native_binaries: [], has_frontend: true, has_backend: false, flags: [], dependencies: [],
    reasons: ["ARM64 compatibility is unverified"], warnings: [], verified_sha256: true,
    bytes: 1024, files: 2, not_executed: true, ...extra,
  },
});
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };

const boot = (rows, detail, overrides = {}, pageName = "store") => {
  const calls = [], network = [], errors = [];
  const replies = Object.assign({
    catalog_sources: { source: source(), sources: [source()], custom_url: "" },
    catalog_list: list(rows),
    catalog_details: { entry: detail || rows.find(r => r && typeof r.id === "string"), source: source(), origin: "downloaded", stale: false, error: "", warning: "" },
    catalog_install: { error: "Unexpected installation in metadata test" },
  }, overrides);
  const env = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fetch = (...args) => { network.push(args); return Promise.reject(new Error("Unexpected page fetch")); };
      w.XMLHttpRequest = class { open(...args) { network.push(args); } send() {} };
      w.addEventListener("error", ev => errors.push(ev.error || ev.message));
      w.fyCall = encoded => {
        const c = JSON.parse(encoded);
        calls.push(c);
        if (!c.id) return;
        const value = Object.prototype.hasOwnProperty.call(replies, c.method) ? replies[c.method] : { error: "Unhandled " + c.method };
        Promise.resolve(typeof value === "function" ? value(c.arg) : value).then(result => w.Framey._reply(c.gen, c.id, JSON.parse(JSON.stringify(result))));
      };
    },
    loadFiles: FILES,
  });
  const box = env.window.Framey.el("");
  env.document.body.append(box);
  const controls = () => [...box.querySelectorAll('[role="button"]')];
  const find = label => {
    const control = controls().find(e => e.getAttribute("aria-label") === label);
    assert.ok(control, "Missing control: " + label);
    return control;
  };
  const click = async label => { find(label).click(); await settle(); };
  return {
    env, box, calls, network, errors, find, click,
    text: () => box.textContent,
    open: async () => { env.window.Framey.pages[pageName].render(box, {}); await settle(); },
    details: async name => click("Details: " + name),
    sent: method => calls.filter(c => c.method === method),
  };
};

const assertPassive = c => {
  assert.equal(c.box.querySelectorAll("img,script,iframe,object,embed,video,audio,source,link").length, 0, "Catalog metadata must not create resource or executable elements");
  assert.equal(c.box.querySelectorAll("[onclick],[onerror],[onload],[src],[srcset]").length, 0, "Catalog metadata must not create active attributes");
  assert.ok([...c.box.querySelectorAll("a[href]")].every(a => /^https:\/\//i.test(a.getAttribute("href"))), "Only safe explicit links may become anchors");
  assert.deepEqual(c.network, [], "Rendering metadata must not make page-side network requests");
  assert.deepEqual(c.errors, []);
};

const bootDecky = (item, overrides = {}) => boot([item], item, {
  catalog_sources: {
    source: source(), sources: [source(), { id: "testing", label: "Testing", url: "https://catalog.example.invalid/testing.json" }, { id: "custom", label: "Custom", url: "" }],
    decky_sources: [deckySource(), { id: "decky_testing", label: "Decky testing", url: "https://testing.deckbrew.xyz/plugins" }], custom_url: "",
  },
  catalog_list: { ...list([item]), source: deckySource() },
  catalog_details: { entry: item, source: deckySource(), origin: "downloaded", stale: false, error: "", warning: "" },
  catalog_assess: deckyAssessment(),
  ...overrides,
}, "decky_store");

const reportSpy = c => {
  const reports = [];
  c.env.window.__S.K.report = context => {
    reports.push(JSON.parse(JSON.stringify(context)));
    return new Promise(() => {});
  };
  return reports;
};
const assertReportContext = context => {
  assert.deepEqual(Object.keys(context).sort(), ["code", "error", "plugin"]);
  assert.deepEqual(Object.keys(context.plugin).sort(), ["name", "source", "version"]);
  for (const [value, limit] of [[context.plugin.name, 200], [context.plugin.version, 200], [context.error, 600], [context.code, 80]]) {
    assert.equal(typeof value, "string");
    assert.ok(value.length <= limit, "Report context must be bounded before it reaches the shared preview");
  }
  assert.ok(["framey", "decky_stable", "decky_testing"].includes(context.plugin.source));
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("names, descriptions and source strings remain plain text in browse and details", async () => {
  const name = '<b data-catalog-marker="name">Module name</b>';
  const description = '<img src="https://example.invalid/picture.png"><script>window.catalogMarker=true</script> Description';
  const repository = '<span data-catalog-marker="source">https://github.com/example/sample-module</span>';
  const item = entry({ name, description, category: "<em>Utilities</em>", source: { repository, commit: "a".repeat(40), path: "<strong>module</strong>" } });
  const c = boot([item]);
  await c.open();
  assert.ok(c.text().includes(name));
  assertPassive(c);
  await c.details(name);
  assert.ok(c.text().includes(name));
  assert.ok(c.text().includes(description));
  assert.ok(c.text().includes(repository), "The reviewed source must remain visible as text");
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],em,strong").length, 0);
  assert.equal(c.env.window.catalogMarker, undefined);
  assertPassive(c);
  assert.equal(c.sent("catalog_install").length, 0);
});

t("scriptlike homepage and repository values never become executable links", async () => {
  const item = entry({
    homepage: "javascript:catalogMarker=true",
    source: { repository: "javascript:catalogMarker=true", commit: "a".repeat(40), path: "" },
    eligible: false, reason: "Unsupported source",
  });
  const c = boot([item]);
  await c.open();
  await c.details(item.name);
  assert.ok(c.text().includes("Unsupported source"));
  assert.equal(c.find("Install " + item.name).getAttribute("aria-disabled"), "true");
  await c.click("Install " + item.name);
  assert.equal(c.sent("catalog_install").length, 0);
  assert.equal(c.env.window.catalogMarker, undefined);
  assertPassive(c);
});

t("malformed rows and non-text metadata do not break valid entries", async () => {
  const good = entry({ description: { unexpected: "object" }, category: ["Unexpected"], tags: [null, {}, "sample"], homepage: { unexpected: true } });
  const c = boot([null, false, 7, "unexpected", [], {}, { id: null }, { id: { unexpected: true } }, good], good);
  await c.open();
  assert.ok(c.text().includes(good.name));
  assert.ok(!c.text().includes("[object Object]"));
  await c.details(good.name);
  assert.ok(!c.text().includes("[object Object]"));
  assert.ok(c.text().includes("No description is available."));
  assertPassive(c);
  assert.equal(c.sent("catalog_install").length, 0);
});

t("oversized metadata is bounded while the module remains navigable", async () => {
  const item = entry({
    name: "Module " + "N".repeat(50000), description: "Description " + "D".repeat(100000),
    category: "Category " + "C".repeat(20000), version: "V".repeat(20000),
    source: { repository: "Repository " + "R".repeat(50000), commit: "a".repeat(40000), path: "P".repeat(50000) },
  });
  const c = boot([item]);
  await c.open();
  assert.ok(c.text().length < 6000, "Browse must bound individual metadata fields");
  const detailButton = [...c.box.querySelectorAll('[role="button"]')].find(e => e.getAttribute("aria-label").startsWith("Details: Module "));
  assert.ok(detailButton);
  detailButton.click();
  await settle();
  assert.ok(c.text().includes("Description "));
  assert.ok(c.text().length < 20000, "Details must bound oversized metadata");
  assert.ok(c.box.querySelectorAll("*").length < 300, "One entry must not expand into an unbounded DOM");
  assert.ok(c.find("Back"));
  assertPassive(c);
});

t("ineligible reason metadata is readable text and cannot force installation", async () => {
  const reason = '<b data-catalog-marker="reason">Requires a protected installer</b>';
  const item = entry({ eligible: false, reason, kind: "setup", source: { repository: "https://github.com/example/sample-module", commit: "", path: "" }, compat: { state: "incompatible", badge: "platform", msg: "<i>Unsupported platform</i>", reasons: [reason] } });
  const c = boot([item]);
  await c.open();
  await c.details(item.name);
  assert.ok(c.text().includes(reason));
  assert.ok(c.text().includes("<i>Unsupported platform</i>"));
  const install = c.find("Install " + item.name);
  assert.equal(install.getAttribute("aria-disabled"), "true");
  install.click();
  install.dispatchEvent(new c.env.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await settle();
  assert.equal(c.sent("catalog_install").length, 0);
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],i").length, 0);
  assertPassive(c);
});

t("Decky author, description, tags and version metadata remain literal and passive", async () => {
  const author = '<a href="javascript:catalogMarker=true">Example author</a>';
  const description = '<img src="https://example.invalid/picture.png"><script>window.catalogMarker=true</script> Decky description';
  const tag = '<b data-catalog-marker="tag">utility</b>';
  const version = '<i data-catalog-marker="version">1.0.0</i>';
  const item = deckyEntry({ author, description, tags: [tag], version, versions: [{ ...deckyEntry().versions[0], name: version }] });
  const c = bootDecky(item);
  await c.open();
  assertPassive(c);
  assert.equal(c.sent("catalog_assess").length, 0);
  await c.details(item.name);
  for (const value of [author, description, tag, version]) assert.ok(c.text().includes(value), "Expected literal Decky metadata: " + value);
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],a,i,b").length, 0);
  assert.equal(c.env.window.catalogMarker, undefined);
  assert.equal(c.sent("catalog_assess").length, 0, "Opening details must not inspect or download an archive");
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("Decky archive inspection is explicit and its assessment text remains literal", async () => {
  const label = '<b data-catalog-marker="assessment">Needs a Framey port</b>';
  const reason = '<img src="https://example.invalid/reason.png"> ARM64 remains unverified';
  const warning = '<a href="javascript:catalogMarker=true">Inspection warning</a>';
  const flag = '<em data-catalog-marker="flag">Review package metadata</em>';
  const dependency = '<strong data-catalog-marker="dependency">Sample dependency</strong>';
  const binaryPath = '<code data-catalog-marker="binary">bin/sample</code>';
  const item = deckyEntry();
  const c = bootDecky(item, { catalog_assess: deckyAssessment({ label, reasons: [reason], warnings: [warning], flags: [flag], dependencies: [dependency], native_binaries: [{ path: binaryPath, format: "ELF", architecture: "unknown" }] }) });
  await c.open();
  await c.details(item.name);
  assert.equal(c.sent("catalog_assess").length, 0);
  await c.click("Inspect compatibility");
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.deepEqual(c.sent("catalog_assess")[0].arg, { id: item.id, source: "decky_stable", identity: item.identity, version_identity: item.versions[0].identity, confirm: true });
  for (const value of [label, reason, warning, flag, dependency, binaryPath]) assert.ok(c.text().includes(value), "Expected literal assessment text: " + value);
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],a,b,em,strong,code").length, 0);
  assert.equal(c.env.window.catalogMarker, undefined);
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("Decky inspection errors stay literal and never trigger automatic retries", async () => {
  const error = '<img src="https://example.invalid/error.png"><b data-catalog-marker="error">Archive unavailable</b>';
  const item = deckyEntry();
  const c = bootDecky(item, { catalog_assess: { error, code: "archive_unavailable" } });
  await c.open();
  await c.details(item.name);
  assert.equal(c.sent("catalog_assess").length, 0);
  await c.click("Inspect compatibility");
  assert.ok(c.text().includes(error));
  assert.equal(c.sent("catalog_assess").length, 1);
  await settle();
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],b").length, 0);
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("Decky source and format each block installation despite malformed eligibility", async () => {
  const formatOnly = deckyEntry({ eligible: true, read_only: false, kind: "plugin" });
  const sourceOnly = deckyEntry({ eligible: true, read_only: false, kind: "plugin", format: undefined });
  for (const c of [bootDecky(formatOnly), boot([formatOnly], formatOnly), bootDecky(sourceOnly)]) {
    await c.open();
    const details = [...c.box.querySelectorAll('[role="button"]')].find(e => e.getAttribute("aria-label") === "Details: Sample Decky module");
    if (details) {
      details.click();
      await settle();
      assert.ok(c.text().includes("Framey port"));
    }
    const installActions = [...c.box.querySelectorAll('[role="button"]')].filter(e => /^(Install(?: |$)|Update(?: |$)|Review(?: |$))/.test(e.getAttribute("aria-label")));
    assert.equal(installActions.length, 0, "A Decky source or format must never expose the Framey install path");
    assert.equal(c.sent("catalog_install").length, 0);
    assert.equal(c.sent("catalog_assess").length, 0);
    assertPassive(c);
  }
});

t("unverified Decky ARM64 support stays unknown before and after inspection", async () => {
  const item = deckyEntry();
  const c = bootDecky(item);
  await c.open();
  await c.details(item.name);
  const assertUnknown = () => {
    assert.ok(/unknown|unverified/i.test(c.text()));
    const relevant = [...c.box.querySelectorAll("div")].filter(e => e.childElementCount === 0 && /ARM64|unknown|unverified/i.test(e.textContent));
    assert.ok(relevant.length, "The architecture uncertainty must be visible");
    assert.ok(relevant.every(e => e.style.color !== "rgb(122, 223, 154)"), "Unverified architecture must not receive the compatible color");
    assert.ok(![...c.box.querySelectorAll("div")].some(e => e.childElementCount === 0 && /^(Compatible|ARM64 compatible)$/i.test(e.textContent)));
    assert.equal(c.sent("catalog_install").length, 0);
  };
  assertUnknown();
  await c.click("Inspect compatibility");
  assertUnknown();
  assertPassive(c);
});

t("missing native compatibility metadata stays Not checked in cards and details", async () => {
  const reason = '<b data-catalog-marker="compatibility">Platform requirements have not been declared.</b>';
  const rows = [
    entry({ compatibility: undefined, compat: undefined, platform: undefined, platform_declared: undefined }),
    entry({ platform: undefined, platform_declared: false, compat: { state: "unknown", badge: "unknown", msg: "" }, compatibility: { status: "unverified", label: "Not checked", reason, reasons: [reason], basis: "declared_requirements", runtime_verified: false } }),
  ];
  for (const item of rows) {
    const c = boot([item], item);
    const assertUnknown = () => {
      const labels = [...c.box.querySelectorAll("div")].filter(e => e.childElementCount === 0);
      assert.ok(labels.some(e => e.textContent === "Not checked"));
      assert.ok(!labels.some(e => e.textContent === "Compatible"));
      assert.ok(labels.filter(e => /Not checked|missing|not declared/i.test(e.textContent)).every(e => e.style.color !== "rgb(122, 223, 154)"));
    };
    await c.open();
    assertUnknown();
    await c.details(item.name);
    assertUnknown();
    if (item.compatibility) assert.ok(c.text().includes(reason));
    assert.equal(c.box.querySelectorAll("[data-catalog-marker],b").length, 0);
    assertPassive(c);
  }
});

t("Decky cards and details force Not compatible despite a compatible payload", async () => {
  const item = deckyEntry({
    eligible: true, read_only: false, platform_declared: true,
    compat: { state: "compatible", badge: "ok", msg: "Compatible", reasons: [] },
    compatibility: { status: "compatible", label: "Compatible", reason: "Declared requirements match.", reasons: [], basis: "declared_requirements", runtime_verified: false },
  });
  const c = bootDecky(item);
  const assertIncompatible = () => {
    const labels = [...c.box.querySelectorAll("div")].filter(e => e.childElementCount === 0);
    assert.ok(labels.some(e => e.textContent === "Not compatible"));
    assert.ok(!labels.some(e => e.textContent === "Compatible"));
    assert.ok(c.text().includes("Built for Decky"));
    assert.ok(c.text().includes("Framey version"));
  };
  await c.open();
  assertIncompatible();
  await c.details(item.name);
  assertIncompatible();
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("mixed architecture evidence preserves ARM64 findings and readable manual review text", async () => {
  const reason = '<b data-catalog-marker="mixed">Both x86_64 and aarch64 files need manual review.</b>';
  const item = deckyEntry();
  const c = bootDecky(item, { catalog_assess: deckyAssessment({
    status: "incompatible", architecture: "mixed", architecture_status: "evidence_only", reasons: [reason],
    native_binaries: [{ path: "bin/sample-x86", format: "ELF", architecture: "x86_64" }, { path: "bin/sample-arm", format: "ELF", architecture: "aarch64" }],
  }) });
  await c.open();
  await c.details(item.name);
  await c.click("Inspect compatibility");
  assert.ok(c.text().includes("Not compatible"));
  assert.ok(c.text().includes(reason));
  assert.ok(/manual review|evidence only/i.test(c.text()));
  assert.ok(!/Needs an ARM64 build|ARM64 (?:build |support )?(?:is )?(?:missing|absent)/i.test(c.text()), "Mixed evidence must not claim the ARM64 build is missing");
  assert.ok(c.text().includes("bin/sample-x86") && c.text().includes("bin/sample-arm"));
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],b").length, 0);
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("Mach-O x86 evidence is described without claiming it is a Linux program", async () => {
  const item = deckyEntry();
  const c = bootDecky(item, { catalog_assess: deckyAssessment({
    status: "incompatible", architecture: "x86_64", architecture_status: "incompatible",
    reasons: ["An Intel/AMD native program was found."],
    native_binaries: [{ path: "bin/sample-macos", format: "Mach-O", architecture: "x86_64" }],
  }) });
  await c.open();
  await c.details(item.name);
  await c.click("Inspect compatibility");
  assert.ok(c.text().includes("Not compatible"));
  assert.ok(c.text().includes("Mach-O"));
  assert.ok(c.text().includes("Includes a program built for Intel/AMD processors; your headset needs an ARM64 build."), "The CPU compatibility reason must distinguish processors from graphics hardware");
  assert.ok(!/Linux program/i.test(c.text()), "Mach-O evidence cannot establish that a binary is a Linux program");
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("archive inspection report exposes its heading hierarchy for navigation", async () => {
  const item = deckyEntry();
  const c = bootDecky(item, { catalog_assess: deckyAssessment({
    status: "incompatible", architecture_status: "unverified", warnings: ["Manual review is needed."],
    flags: ["Review package metadata"], dependencies: ["sample-dependency"],
    native_binaries: [{ path: "bin/sample", format: "ELF", architecture: "other" }],
  }) });
  await c.open();
  await c.details(item.name);
  await c.click("Inspect compatibility");
  const heading = label => [...c.box.querySelectorAll('[role="heading"]')].find(e => e.textContent === label);
  assert.ok(heading("Archive inspection"), "The inspection report needs a navigable heading");
  assert.equal(heading("Archive inspection").getAttribute("aria-level"), "3");
  for (const title of ["Porting assessment", "Inspection limits and warnings", "Flags", "Dependencies", "Native binaries"]) {
    assert.ok(heading(title), "Missing report section heading: " + title);
    assert.equal(heading(title).getAttribute("aria-level"), "4");
  }
  assertPassive(c);
});

t("native report context contains only bounded selected metadata", async () => {
  const name = '<b data-catalog-marker="report-name">Sample module</b>' + "N".repeat(2000);
  const version = '<i data-catalog-marker="report-version">1.0.0</i>' + "V".repeat(2000);
  const omitted = "synthetic-unrelated-private-marker";
  const item = entry({ name, version, logs: omitted, token: omitted, diagnostics: { detail: omitted }, private_query: omitted, extra_entry: { detail: omitted } });
  const c = boot([item], item);
  const reports = reportSpy(c);
  await c.open();
  await c.details(name.slice(0, 200));
  assert.equal(reports.length, 0);
  assertPassive(c);
  await c.click("Report to Framey");
  assert.equal(reports.length, 1);
  assertReportContext(reports[0]);
  assert.equal(reports[0].plugin.name, name.slice(0, 200));
  assert.equal(reports[0].plugin.version, version.slice(0, 200));
  assert.equal(reports[0].plugin.source, "framey");
  assert.equal(reports[0].error, "");
  assert.ok(!JSON.stringify(reports[0]).includes(omitted));
  assert.ok(c.text().includes(name.slice(0, 200)), "Opening the shared preview must preserve the selected details underneath it");
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],b,i").length, 0);
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("Decky report uses the selected version and source with a bounded literal inspection error", async () => {
  const selectedName = '<i data-catalog-marker="selected-version">2.0.0</i>' + "V".repeat(1000);
  const error = '<img src="https://example.invalid/error.png"><b data-catalog-marker="inspection-error">Inspection unavailable</b>' + "E".repeat(2000);
  const omitted = "synthetic-unrelated-private-marker";
  const selectedVersion = { ...deckyEntry().versions[0], name: selectedName, hash: "f".repeat(64), identity: "a".repeat(64) };
  const testing = { id: "decky_testing", label: "Decky testing", url: "https://testing.deckbrew.xyz/plugins" };
  const item = deckyEntry({ versions: [deckyEntry().versions[0], selectedVersion], version_count: 2, source: { repository: "", url: testing.url }, logs: omitted, token: omitted, diagnostics: { detail: omitted } });
  const c = bootDecky(item, {
    catalog_list: { ...list([item]), source: testing },
    catalog_details: { entry: item, source: testing, origin: "downloaded", stale: false, error: "", warning: "" },
    catalog_assess: { error, code: "C".repeat(500), logs: omitted, diagnostics: { detail: omitted } },
  });
  c.env.window.sessionStorage.fyDeckyCatalogSource = "decky_testing";
  const reports = reportSpy(c);
  await c.open();
  await c.details(item.name);
  const choice = c.box.querySelector('select[aria-label="Version"]');
  assert.ok(choice);
  choice.value = selectedVersion.identity;
  choice.dispatchEvent(new c.env.window.Event("change", { bubbles: true }));
  await settle();
  assert.equal(reports.length, 0);
  assert.equal(c.sent("catalog_assess").length, 0);
  await c.click("Inspect compatibility");
  assert.equal(reports.length, 0);
  assert.ok(c.text().includes(error.slice(0, 600)));
  assertPassive(c);
  await c.click("Report to Framey");
  assert.equal(reports.length, 1);
  assertReportContext(reports[0]);
  assert.equal(reports[0].plugin.name, item.name);
  assert.equal(reports[0].plugin.version, selectedName.slice(0, 200));
  assert.equal(reports[0].plugin.source, "decky_testing");
  assert.equal(reports[0].error, error.slice(0, 600));
  assert.ok(!JSON.stringify(reports[0]).includes(omitted));
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],b,i").length, 0);
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

t("native details errors stay literal and report only concise context", async () => {
  const error = '<img src="https://example.invalid/error.png"><b data-catalog-marker="details-error">Details unavailable</b>' + "E".repeat(2000);
  const omitted = "synthetic-unrelated-private-marker";
  const item = entry({ logs: omitted, diagnostics: { detail: omitted }, token: omitted });
  const c = boot([item], item, { catalog_details: { error, code: "C".repeat(500), logs: omitted, token: omitted, diagnostics: { detail: omitted } } });
  const reports = reportSpy(c);
  await c.open();
  await c.details(item.name);
  assert.ok(c.text().includes(error.slice(0, 600)));
  assert.equal(reports.length, 0);
  assertPassive(c);
  await c.click("Report to Framey");
  assert.equal(reports.length, 1);
  assertReportContext(reports[0]);
  assert.deepEqual(reports[0].plugin, { name: item.name, version: item.version, source: "framey" });
  assert.equal(reports[0].error, error.slice(0, 600));
  assert.ok(!JSON.stringify(reports[0]).includes(omitted));
  assert.equal(c.box.querySelectorAll("[data-catalog-marker],b").length, 0);
  assert.equal(c.sent("catalog_install").length, 0);
  assertPassive(c);
});

(async () => {
  let passed = 0, failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); passed++; console.log("ok   " + name); }
    catch (error) { failed++; console.log("FAIL " + name + "\n" + (error.stack || error)); }
  }
  console.log(passed + " passed, " + failed + " failed, " + tests.length + " total");
  process.exit(failed ? 1 : 0);
})();
