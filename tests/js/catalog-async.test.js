"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"];
const DEFER = Symbol("deferred reply");
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const item = (id = "alpha", extra = {}) => Object.assign({
  id, name: id === "alpha" ? "Alpha utility" : "Beta utility", version: "1.0.0",
  description: "A generic catalog test utility.", category: "Utilities", author: "Example author",
  source: { repository: "https://example.com/" + id, commit: "0123456789abcdef", path: "" },
  identity: "review-" + id, channel: "stable", installed: false, eligible: true, compat: { state: "ok", badge: "ok", msg: "Compatible" },
}, extra);
const listing = (...rows) => ({
  rows, total: rows.length, offset: 0, limit: 50, categories: ["Utilities", "Games"],
  source: { id: "stable", label: "Stable", url: "https://example.com/catalog.json" },
  origin: "downloaded", stale: false, checked_at: 1790000000, downloaded_at: 1790000000,
});

const nativeSourceState = (source = listing().source, customURL = "") => ({
  source,
  sources: [listing().source, { id: "testing", label: "Testing", url: "https://example.com/testing.json" }, { id: "custom", label: "Custom", url: customURL }],
  decky_sources: [{ id: "decky_stable", label: "Decky Store", url: "https://example.com/decky-stable.json" }, { id: "decky_testing", label: "Decky Store Testing", url: "https://example.com/decky-testing.json" }],
  custom_url: customURL,
});

const boot = (overrides = {}) => {
  const calls = [];
  const rejected = new Map();
  const pagehide = { added: [], removed: [], live: new Set(), baseline: null };
  const replies = Object.assign({
    state_get: { order: [], hidden: [], prefs: {}, frozen: {}, safe: false },
    catalog_list: listing(item(), item("beta")),
    catalog_details: arg => ({ entry: item(arg.id), source: listing().source, origin: "downloaded", stale: false }),
    catalog_sources: nativeSourceState(),
    catalog_install: { ok: "alpha", version: "1.0.0" },
  }, overrides);
  const env = makeDom({
    before(w) {
      const add = w.addEventListener.bind(w);
      const remove = w.removeEventListener.bind(w);
      w.addEventListener = (name, listener, options) => {
        if (name === "pagehide") { pagehide.added.push(listener); pagehide.live.add(listener); }
        return add(name, listener, options);
      };
      w.removeEventListener = (name, listener, options) => {
        if (name === "pagehide") { pagehide.removed.push(listener); pagehide.live.delete(listener); }
        return remove(name, listener, options);
      };
      w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fyCall = raw => {
        const call = JSON.parse(raw);
        calls.push(call);
        if (!call.id) return;
        const reply = replies[call.method];
        const result = typeof reply === "function" ? reply(call.arg, call) : reply;
        if (result === DEFER) return;
        Promise.resolve(result === undefined ? { error: "unhandled " + call.method } : result)
          .then(value => w.Framey._reply(call.gen, call.id, value));
      };
    },
    loadFiles: FILES.slice(0, -1),
  });
  const FY = env.window.Framey;
  const K = env.window.__S.K;
  const realCall = K.call;
  K.call = (method, arg) => {
    if (rejected.has(method)) {
      const error = rejected.get(method);
      rejected.delete(method);
      return Promise.reject(error);
    }
    return realCall(method, arg);
  };
  pagehide.baseline = new Set(pagehide.live);
  env.load("fy-catalog.js");
  const panel = env.document.querySelector('[role="region"][aria-label="Framey"]');
  const box = () => panel.firstElementChild.firstElementChild;
  const controls = () => [...box().querySelectorAll('[role="button"],button')];
  const find = (label, index = 0) => {
    const hits = controls().filter(node => (node.getAttribute("aria-label") || node.textContent) === label);
    assert.ok(hits[index], "missing control " + label + "; controls: " + controls().map(node => node.getAttribute("aria-label") || node.textContent).join(" | "));
    return hits[index];
  };
  const sent = method => calls.filter(call => call.method === method);
  return {
    env, FY, K, w: env.window, d: env.document, replies, box, calls, sent, find, pagehide,
    last: method => sent(method).at(-1),
    text: () => box().textContent,
    body: () => env.document.body.textContent,
    has: label => controls().some(node => (node.getAttribute("aria-label") || node.textContent) === label),
    rejectNext: (method, error) => rejected.set(method, error),
    open: async () => { FY.open("#discover"); await settle(); },
    away: async () => { FY.open("#settings"); await settle(); },
    click: async (label, index = 0) => { find(label, index).click(); await settle(); },
    answer: async (call, value) => { env.answer(call, value); await settle(); },
    progress: async value => { env.event("_core", "progress", value); await settle(); },
  };
};

const tests = [];
const t = (name, run) => tests.push([name, run]);

t("a list reply after leaving Store cannot replace the destination page", async () => {
  const c = boot({ catalog_list: DEFER });
  await c.open();
  const pending = c.last("catalog_list");
  assert.ok(pending, "opening Store requests the catalog");
  await c.away();
  const destination = c.text();
  await c.answer(pending, listing(item("alpha", { name: "Obsolete list result" })));
  assert.equal(c.text(), destination);
  assert.ok(!c.body().includes("Obsolete list result"));
});

t("reopening Store ignores the older list reply even when it arrives last", async () => {
  const c = boot({ catalog_list: DEFER });
  await c.open();
  const first = c.last("catalog_list");
  await c.away();
  await c.open();
  const second = c.last("catalog_list");
  assert.notEqual(second.id, first.id);
  await c.answer(second, listing(item("beta", { name: "Current list result" })));
  assert.ok(c.text().includes("Current list result"));
  await c.answer(first, listing(item("alpha", { name: "Obsolete list result" })));
  assert.ok(c.text().includes("Current list result"));
  assert.ok(!c.text().includes("Obsolete list result"));
});

t("rerendering Store rejects a pending reply from the prior render", async () => {
  const c = boot({ catalog_list: DEFER });
  await c.open();
  const first = c.last("catalog_list");
  await c.open();
  const second = c.last("catalog_list");
  assert.notEqual(second.id, first.id);
  await c.answer(first, listing(item("alpha", { name: "Prior render result" })));
  assert.ok(!c.text().includes("Prior render result"));
  await c.answer(second, listing(item("beta", { name: "Latest render result" })));
  assert.ok(c.text().includes("Latest render result"));
});

t("leaving and rerendering Store do not leak subscriptions", async () => {
  const c = boot();
  const baseline = c.FY.diag().subs;
  await c.open();
  const active = c.FY.diag().subs;
  assert.ok(active >= baseline);
  await c.open();
  assert.equal(c.FY.diag().subs, active);
  await c.away();
  assert.equal(c.FY.diag().subs, baseline);
  await c.open();
  assert.equal(c.FY.diag().subs, active);
  c.w.dispatchEvent(new c.w.Event("pagehide"));
  await settle();
  assert.equal(c.FY.diag().subs, baseline);
  c.w.dispatchEvent(new c.w.Event("pagehide"));
  await settle();
  assert.equal(c.FY.diag().subs, baseline);
});

const details = (entry, extra = {}) => Object.assign({ entry, source: listing().source, origin: "downloaded", stale: false }, extra);
const search = async (c, value) => {
  const input = c.box().querySelector('input[aria-label="Search modules"]');
  assert.ok(input, "search input exists");
  input.value = value;
  input.dispatchEvent(new c.w.Event("input", { bubbles: true }));
  await c.click("Search");
};
const select = async (c, label, value) => {
  const control = c.box().querySelector('select[aria-label="' + label + '"]');
  assert.ok(control, label + " filter exists");
  control.value = value;
  control.dispatchEvent(new c.w.Event("change", { bubbles: true }));
  await settle();
};
const startInstall = async c => {
  await c.click("Details: Alpha utility");
  await c.click("Install Alpha utility");
  await c.click("Install");
};

t("out-of-order search replies preserve the latest query and its results", async () => {
  const c = boot();
  await c.open();
  c.replies.catalog_list = DEFER;
  await search(c, "first query");
  const first = c.last("catalog_list");
  await search(c, "second query");
  const second = c.last("catalog_list");
  assert.equal(first.arg.query, "first query");
  assert.equal(second.arg.query, "second query");
  await c.answer(second, listing(item("beta", { name: "Second query result" })));
  await c.answer(first, listing(item("alpha", { name: "First query result" })));
  assert.ok(c.text().includes("Second query result"));
  assert.ok(!c.text().includes("First query result"));
  assert.equal(c.box().querySelector('input[aria-label="Search modules"]').value, "second query");
});

t("list error and Retry retain the current query, category and sort", async () => {
  const c = boot();
  await c.open();
  await search(c, "portable utility");
  await c.click("Filters");
  await select(c, "Category", "Utilities");
  await select(c, "Sort", "updated");
  c.replies.catalog_list = { error: "Example catalog unavailable", code: "network" };
  await c.click("Refresh");
  assert.ok(c.text().includes("Example catalog unavailable"));
  assert.equal(c.box().querySelector('input[aria-label="Search modules"]').value, "portable utility");
  assert.equal(c.box().querySelector('select[aria-label="Category"]').value, "Utilities");
  assert.equal(c.box().querySelector('select[aria-label="Sort"]').value, "updated");
  c.replies.catalog_list = listing(item());
  await c.click("Retry");
  assert.equal(c.last("catalog_list").arg.query, "portable utility");
  assert.equal(c.last("catalog_list").arg.category, "Utilities");
  assert.equal(c.last("catalog_list").arg.sort, "updated");
  assert.equal(c.last("catalog_list").arg.refresh, true);
  assert.ok(c.text().includes("Alpha utility"));
});

t("a rejected list request becomes a retryable error without losing the query", async () => {
  const c = boot();
  await c.open();
  c.rejectNext("catalog_list", new Error("Example transport rejection"));
  await search(c, "retained query");
  assert.ok(c.text().includes("Example transport rejection"));
  assert.equal(c.box().querySelector('input[aria-label="Search modules"]').value, "retained query");
  await c.click("Retry");
  assert.equal(c.last("catalog_list").arg.query, "retained query");
  assert.ok(c.text().includes("Alpha utility"));
});

t("late details cannot replace the current selection or its reviewed install identity", async () => {
  const c = boot({ catalog_details: DEFER, catalog_install: DEFER });
  await c.open();
  await c.click("Details: Alpha utility");
  const first = c.last("catalog_details");
  await c.click("Back");
  await c.click("Details: Beta utility");
  const second = c.last("catalog_details");
  await c.answer(second, details(item("beta", { description: "Current beta details", identity: "reviewed-beta-snapshot" })));
  await c.answer(first, details(item("alpha", { description: "Obsolete alpha details", identity: "obsolete-alpha-snapshot" })));
  assert.ok(c.text().includes("Current beta details"));
  assert.ok(!c.text().includes("Obsolete alpha details"));
  await c.click("Install Beta utility");
  assert.ok(c.text().includes("Beta utility"));
  await c.click("Install");
  assert.deepEqual(c.last("catalog_install").arg, { id: "beta", source: "stable", identity: "reviewed-beta-snapshot", confirm: true });
});

t("a details reply after navigation or rerender cannot resurrect the old module", async () => {
  const c = boot({ catalog_details: DEFER });
  await c.open();
  await c.click("Details: Alpha utility");
  const first = c.last("catalog_details");
  await c.away();
  const destination = c.text();
  await c.answer(first, details(item("alpha", { description: "Late navigation details" })));
  assert.equal(c.text(), destination);
  await c.open();
  await c.click("Details: Alpha utility");
  const second = c.last("catalog_details");
  await c.open();
  await c.answer(second, details(item("alpha", { description: "Late rerender details" })));
  assert.ok(c.has("Details: Beta utility"));
  assert.ok(!c.text().includes("Late rerender details"));
});

t("failed and rejected detail requests stay on the selected module and retry it", async () => {
  const c = boot({ catalog_details: { error: "Example detail failure", code: "network" } });
  await c.open();
  await c.click("Details: Beta utility");
  assert.ok(c.text().includes("Example detail failure"));
  assert.equal(c.sent("catalog_install").length, 0);
  c.rejectNext("catalog_details", new Error("Example detail rejection"));
  await c.click("Retry");
  assert.ok(c.text().includes("Example detail rejection"));
  c.replies.catalog_details = arg => details(item(arg.id));
  await c.click("Retry");
  assert.equal(c.last("catalog_details").arg.id, "beta");
  assert.ok(c.has("Install Beta utility"));
  assert.ok(!c.has("Install Alpha utility"));
});

t("duplicate confirmation taps and reopening a running install issue one install RPC", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await c.click("Details: Alpha utility");
  await c.click("Install Alpha utility");
  const confirm = c.find("Install");
  confirm.click();
  confirm.click();
  await settle();
  confirm.click();
  await settle();
  assert.equal(c.sent("catalog_install").length, 1);
  await c.away();
  await c.open();
  await c.click("Details: Alpha utility");
  assert.ok(c.text().includes("Installing"));
  assert.ok(!c.has("Install Alpha utility"));
  assert.equal(c.sent("catalog_install").length, 1);
});

t("navigation during an install leaves it running and never invents cancellation", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await startInstall(c);
  const install = c.last("catalog_install");
  assert.ok(c.text().includes("continues when you leave"));
  await c.away();
  const destination = c.text();
  assert.ok(!c.calls.some(call => /cancel/i.test(call.method)));
  assert.ok(!/cancelled|canceled/i.test(c.body()));
  await c.answer(install, { ok: "alpha", version: "1.0.0" });
  assert.equal(c.text(), destination);
  assert.ok(!/cancelled|canceled/i.test(c.body()));
});

t("an old install completion cannot replace another module's details", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await startInstall(c);
  const install = c.last("catalog_install");
  await c.click("Back");
  await c.click("Details: Beta utility");
  await c.answer(install, { ok: "alpha", version: "1.0.0" });
  assert.ok(c.has("Install Beta utility"));
  assert.ok(!c.has("Install Alpha utility"));
  assert.ok(!c.text().includes("Installation complete"));
});

t("failed and rejected installs keep the reviewed module available for retry", async () => {
  const c = boot({ catalog_install: { error: "Example install failure", code: "download" } });
  await c.open();
  await startInstall(c);
  assert.ok(c.text().includes("Example install failure"));
  assert.ok(c.has("Install Alpha utility"));
  c.rejectNext("catalog_install", new Error("Example install rejection"));
  await c.click("Install Alpha utility");
  await c.click("Install");
  assert.ok(c.text().includes("Example install rejection"));
  c.replies.catalog_install = { ok: "alpha", version: "1.0.0" };
  c.replies.catalog_details = arg => details(item(arg.id, { installed: true, eligible: false, reason: "already installed" }));
  await c.click("Install Alpha utility");
  await c.click("Install");
  assert.ok(c.text().includes("Installation complete"));
  assert.ok(c.has("Installed"));
  assert.equal(c.sent("catalog_install").length, 2);
});

t("install completion uses refreshed installed state instead of inventing it", async () => {
  const c = boot();
  await c.open();
  await startInstall(c);
  assert.ok(c.text().includes("Installation complete"));
  assert.ok(c.has("Install Alpha utility"));
  assert.ok(!c.has("Installed"));
  assert.equal(c.sent("catalog_details").length, 2);
});

t("generic progress for another operation or job never appears as catalog install progress", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await startInstall(c);
  for (const event of [
    { id: "beta", job: "other-module-job", stage: "download", label: "Unrelated beta work" },
    { id: "alpha", job: "unrelated-alpha-job", stage: "download", label: "Unrelated alpha work" },
    { id: "alpha", stage: "verify", label: "Uncorrelated verification" },
    { job: "unknown-job", stage: "install", label: "Uncorrelated install" },
  ]) await c.progress(event);
  assert.ok(c.text().includes("Installing"));
  assert.ok(!c.text().includes("Unrelated"));
  assert.ok(!c.text().includes("Uncorrelated"));
});

t("a prior render's install result cannot replace the reopened Store selection", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await startInstall(c);
  const install = c.last("catalog_install");
  await c.away();
  await c.open();
  await c.click("Details: Beta utility");
  const current = c.text();
  await c.answer(install, { error: "Prior install failed", code: "network" });
  assert.equal(c.text(), current);
  assert.ok(!c.text().includes("Prior install failed"));
  assert.ok(c.has("Install Beta utility"));
});

t("disposal ignores deferred list and detail replies and removes keyboard handlers", async () => {
  const c = boot({ catalog_list: DEFER });
  await c.open();
  const first = c.last("catalog_list");
  const oldBox = c.box();
  c.FY.pages.store.dispose();
  await c.answer(first, listing(item("alpha", { name: "Disposed list result" })));
  assert.ok(!oldBox.textContent.includes("Disposed list result"));
  assert.equal(oldBox.onkeydown, null);
  c.replies.catalog_list = listing(item());
  c.replies.catalog_details = DEFER;
  await c.open();
  await c.click("Details: Alpha utility");
  const detailCall = c.last("catalog_details");
  const detailBox = c.box();
  c.FY.pages.store.dispose();
  await c.answer(detailCall, details(item("alpha", { description: "Disposed detail result" })));
  assert.ok(!detailBox.textContent.includes("Disposed detail result"));
  assert.equal(detailBox.onkeydown, null);
});

const edit = async (c, label, value) => {
  const input = c.box().querySelector('input[aria-label="' + label + '"]');
  assert.ok(input, label + " input exists");
  input.value = value;
  input.dispatchEvent(new c.w.Event("input", { bubbles: true }));
  await settle();
};

t("editing a custom URL during source save immediately re-enables Apply and dismisses Saving", async () => {
  const c = boot();
  await c.open();
  await c.click("Filters");
  await select(c, "Source", "custom");
  await edit(c, "Custom catalog URL", "https://example.com/catalog-a.json");
  c.replies.catalog_sources = DEFER;
  await c.click("Apply source");
  const firstSave = c.last("catalog_sources");
  assert.equal(firstSave.arg.url, "https://example.com/catalog-a.json");
  assert.equal(c.find("Apply source").getAttribute("aria-disabled"), "true");
  assert.ok(c.text().includes("Saving catalog source"));
  await edit(c, "Custom catalog URL", "https://example.com/catalog-b.json");
  assert.equal(c.find("Apply source").getAttribute("aria-disabled"), "false");
  assert.ok(!c.text().includes("Saving catalog source"));
  const listsBefore = c.sent("catalog_list").length;
  await c.answer(firstSave, nativeSourceState({ id: "custom", label: "Custom", url: firstSave.arg.url }, firstSave.arg.url));
  assert.equal(c.sent("catalog_list").length, listsBefore);
  assert.equal(c.find("Apply source").getAttribute("aria-disabled"), "false");
  assert.ok(!c.text().includes("Saving catalog source"));
  await c.click("Apply source");
  const secondSave = c.last("catalog_sources");
  assert.notEqual(secondSave.id, firstSave.id);
  assert.equal(secondSave.arg.url, "https://example.com/catalog-b.json");
  await c.answer(secondSave, nativeSourceState({ id: "custom", label: "Custom", url: secondSave.arg.url }, secondSave.arg.url));
  assert.equal(c.last("catalog_list").arg.url, "https://example.com/catalog-b.json");
  assert.ok(c.has("Details: Alpha utility"));
  assert.ok(!c.text().includes("Saving catalog source"));
});

t("editing a draft search while a list is pending stops claiming that draft is loading", async () => {
  const c = boot();
  await c.open();
  c.replies.catalog_list = DEFER;
  await c.click("Refresh");
  const obsolete = c.last("catalog_list");
  assert.ok(c.text().includes("Loading modules"));
  await edit(c, "Search modules", "new draft query");
  assert.ok(!c.text().includes("Loading modules"));
  assert.equal(c.find("Search").getAttribute("aria-disabled"), "false");
  await c.answer(obsolete, listing(item("alpha", { name: "Obsolete pending result" })));
  assert.ok(!c.text().includes("Obsolete pending result"));
  assert.ok(!c.text().includes("Loading modules"));
  assert.equal(c.box().querySelector('input[aria-label="Search modules"]').value, "new draft query");
  await c.click("Search");
  const current = c.last("catalog_list");
  assert.equal(current.arg.query, "new draft query");
  assert.ok(c.text().includes("Loading modules"));
  await c.answer(current, listing(item("beta", { name: "Submitted draft result" })));
  assert.ok(c.text().includes("Submitted draft result"));
  assert.ok(!c.text().includes("Loading modules"));
});

t("an install finishing from a disposed render refreshes the reopened same entry", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await startInstall(c);
  const install = c.last("catalog_install");
  await c.away();
  await c.open();
  await c.click("Details: Alpha utility");
  assert.ok(c.text().includes("Installing"));
  const detailsBefore = c.sent("catalog_details").length;
  c.replies.catalog_details = arg => details(item(arg.id, { installed: true, eligible: false, reason: "already installed", description: "Authoritative installed state" }));
  await c.answer(install, { ok: "alpha", version: "1.0.0" });
  assert.equal(c.sent("catalog_details").length, detailsBefore + 1);
  assert.ok(c.has("Installed"));
  assert.ok(c.text().includes("Authoritative installed state"));
  assert.ok(!c.text().includes("Installing"));
  assert.ok(!c.text().includes("continues when you leave"));
});

t("authoritative installed details update the existing browse row when going Back", async () => {
  const c = boot({ catalog_install: DEFER });
  await c.open();
  await startInstall(c);
  const install = c.last("catalog_install");
  const listsBefore = c.sent("catalog_list").length;
  c.replies.catalog_details = arg => details(item(arg.id, { installed: true, eligible: false, reason: "already installed" }));
  await c.answer(install, { ok: "alpha", version: "1.0.0" });
  assert.ok(c.has("Installed"));
  await c.click("Back");
  assert.equal(c.sent("catalog_list").length, listsBefore);
  const row = c.find("Details: Alpha utility").parentElement;
  assert.ok(row.textContent.includes("Installed"));
  assert.ok(!row.textContent.includes("Installing"));
  assert.ok(c.has("Details: Beta utility"));
});

t("custom catalog URLs with the same entry and repository have independent install jobs", async () => {
  const urlA = "https://example.com/catalog-a.json";
  const urlB = "https://example.com/catalog-b.json";
  const c = boot({
    catalog_install: DEFER,
    catalog_sources: arg => nativeSourceState({ id: "custom", label: "Custom", url: arg.url || urlA }, arg.url || urlA),
  });
  await c.open();
  await startInstall(c);
  const installA = c.last("catalog_install");
  assert.equal(installA.arg.url, urlA);
  await c.click("Back");
  await c.click("Filters");
  await edit(c, "Custom catalog URL", urlB);
  await c.click("Apply source");
  assert.equal(c.last("catalog_list").arg.url, urlB);
  await c.click("Details: Alpha utility");
  assert.ok(c.has("Install Alpha utility"));
  assert.ok(!c.text().includes("Installing"));
  await c.click("Install Alpha utility");
  await c.click("Install");
  const installB = c.last("catalog_install");
  assert.equal(c.sent("catalog_install").length, 2);
  assert.equal(installB.arg.url, urlB);
  assert.equal(installA.arg.identity, installB.arg.identity);
  const detailsBefore = c.sent("catalog_details").length;
  await c.answer(installA, { ok: "alpha", version: "1.0.0" });
  assert.equal(c.sent("catalog_details").length, detailsBefore);
  assert.ok(c.text().includes("Installing"));
  c.replies.catalog_details = arg => details(item(arg.id, { installed: true, eligible: false }));
  await c.answer(installB, { ok: "alpha", version: "1.0.0" });
  assert.equal(c.last("catalog_details").arg.url, urlB);
  assert.ok(c.has("Installed"));
  assert.ok(!c.text().includes("Installing"));
});

t("tab leave preserves the catalog pagehide handler and full disposal removes only that handler", async () => {
  const c = boot();
  const baseline = c.pagehide.baseline;
  assert.ok(baseline.size > 0, "the core owns a separate pagehide handler");
  const catalogHandlers = [...c.pagehide.live].filter(listener => !baseline.has(listener));
  assert.equal(catalogHandlers.length, 1);
  const catalogHandler = catalogHandlers[0];
  const removedBefore = c.pagehide.removed.length;
  await c.open();
  await c.away();
  assert.ok(c.pagehide.live.has(catalogHandler));
  assert.equal(c.pagehide.removed.length, removedBefore);
  await c.open();
  assert.ok(c.has("Details: Alpha utility"));
  assert.ok(c.pagehide.live.has(catalogHandler));
  assert.equal(c.pagehide.added.filter(listener => listener === catalogHandler).length, 1);
  const activeBox = c.box();
  c.K.dispose();
  await settle();
  assert.equal(activeBox.onkeydown, null);
  assert.ok(!c.pagehide.live.has(catalogHandler));
  assert.deepEqual([...c.pagehide.live], [...baseline]);
  for (const listener of c.pagehide.removed.slice(removedBefore)) assert.strictEqual(listener, catalogHandler);
  assert.ok(c.pagehide.removed.slice(removedBefore).includes(catalogHandler));
  c.FY.pages.store.dispose();
  c.K.dispose();
  await settle();
  assert.deepEqual([...c.pagehide.live], [...baseline]);
  for (const listener of c.pagehide.removed.slice(removedBefore)) assert.strictEqual(listener, catalogHandler);
});

const DECKY_SOURCES = [
  { id: "stable", label: "Stable", url: "https://example.com/framey-stable.json" },
  { id: "testing", label: "Testing", url: "https://example.com/framey-testing.json" },
  { id: "custom", label: "Custom", url: "" },
  { id: "decky_stable", label: "Decky stable", url: "https://example.com/decky-stable.json" },
  { id: "decky_testing", label: "Decky testing", url: "https://example.com/decky-testing.json" },
];
const deckySource = id => DECKY_SOURCES.find(source => source.id === id);
const deckyEntry = (id = "decky-1", extra = {}) => Object.assign({
  id, name: id === "decky-1" ? "Decky alpha utility" : "Decky beta utility", version: "2.0.0",
  description: "A generic Decky catalog test utility.", category: "Utilities", tags: [],
  source: { repository: "", url: "https://example.com/decky-stable.json" },
  format: "decky", kind: "decky", read_only: true, eligible: false, installed: false, version_count: 2,
  identity: (id === "decky-1" ? "1" : "2").repeat(64), architecture: "unknown",
  compat: { state: "incompatible", badge: "port", msg: "Decky package format requires a Framey port" },
  versions: [
    { name: "2.0.0", hash: "a".repeat(64), created: "2026-10-01T00:00:00Z", downloads: 100, updates: "Generic current version", identity: "c".repeat(64) },
    { name: "1.0.0", hash: "b".repeat(64), created: "2026-09-01T00:00:00Z", downloads: 200, updates: "Generic older version", identity: "d".repeat(64) },
  ],
}, extra);
const deckyListing = source => {
  const rows = [deckyEntry(), deckyEntry("decky-2")].map(({ versions, ...entry }) => Object.assign(entry, { source: { repository: "", url: deckySource(source).url } }));
  return Object.assign(listing(...rows), { source: deckySource(source) });
};
const deckyDetails = (entry, source = "decky_stable") => ({
  entry: Object.assign({}, entry, { source: { repository: "", url: deckySource(source).url } }),
  source: deckySource(source), origin: "downloaded", stale: false,
});
const deckyAssessment = (id = "decky-1", extra = {}) => Object.assign({
  id, format: "decky", read_only: true, version: "2.0.0", hash: "a".repeat(64),
  assessment: {
    state: "port_required", label: "Needs a Framey port", architecture: "unknown", root_required: null,
    native_binaries: [], has_frontend: true, has_backend: true, flags: [], dependencies: [], reasons: [],
    warnings: [], verified_sha256: true, bytes: 1024, files: 3, not_executed: true,
  },
}, extra);
const deckyBoot = (overrides = {}) => {
  const c = boot(Object.assign({
    catalog_sources: arg => ({
      source: deckySource(arg.source || "stable"),
      sources: DECKY_SOURCES.filter(source => !source.id.startsWith("decky_")),
      decky_sources: DECKY_SOURCES.filter(source => source.id.startsWith("decky_")),
      custom_url: "",
    }),
    catalog_list: arg => arg.source.startsWith("decky_") ? deckyListing(arg.source) : listing(item(), item("beta")),
    catalog_details: arg => arg.source.startsWith("decky_") ? deckyDetails(deckyEntry(arg.id), arg.source) : details(item(arg.id)),
    catalog_assess: DEFER,
  }, overrides));
  c.openNative = c.open;
  c.open = async () => { c.FY.open("#decky-store"); await settle(); };
  return c;
};
const selectDeckyVersion = async (c, name) => {
  const control = c.box().querySelector('select[aria-label="Version"]');
  assert.ok(control, "Decky version selector exists");
  const option = [...control.options].find(option => option.textContent.includes(name));
  assert.ok(option, "Decky version " + name + " is available");
  await select(c, "Version", option.value);
};
const selectedDeckyVersion = c => {
  const control = c.box().querySelector('select[aria-label="Version"]');
  assert.ok(control, "Decky version selector exists");
  return control.selectedOptions[0].textContent;
};
const startInspection = async c => {
  await c.click("Details: Decky alpha utility");
  await c.click("Inspect compatibility");
};

t("late Decky details and versions cannot replace a different selected entry", async () => {
  const c = deckyBoot({ catalog_details: DEFER });
  await c.open();
  await c.click("Details: Decky alpha utility");
  const first = c.last("catalog_details");
  await c.click("Back");
  await c.click("Details: Decky beta utility");
  const second = c.last("catalog_details");
  const beta = deckyEntry("decky-2", { version: "3.0.0", versions: [{ name: "3.0.0", hash: "e".repeat(64), identity: "f".repeat(64) }] });
  await c.answer(second, deckyDetails(beta));
  await c.answer(first, deckyDetails(deckyEntry("decky-1", { description: "Obsolete Decky alpha details", versions: [{ name: "9.9.9", hash: "a".repeat(64), identity: "a".repeat(64) }] })));
  assert.ok(selectedDeckyVersion(c).includes("3.0.0"));
  assert.ok(!c.text().includes("9.9.9"));
  assert.ok(!c.text().includes("Obsolete Decky alpha details"));
  await c.click("Inspect compatibility");
  assert.deepEqual(c.last("catalog_assess").arg, { id: "decky-2", source: "decky_stable", identity: beta.identity, version_identity: "f".repeat(64), confirm: true });
});

t("late Decky details and versions cannot cross a catalog source change", async () => {
  const c = deckyBoot({ catalog_details: DEFER });
  await c.open();
  await c.click("Details: Decky alpha utility");
  const stable = c.last("catalog_details");
  await c.click("Back");
  await c.click("Filters");
  await select(c, "Source", "decky_testing");
  await c.click("Details: Decky alpha utility");
  const testing = c.last("catalog_details");
  assert.equal(stable.arg.source, "decky_stable");
  assert.equal(testing.arg.source, "decky_testing");
  const current = deckyEntry("decky-1", { identity: "e".repeat(64), version: "3.0.0", versions: [{ name: "3.0.0", hash: "e".repeat(64), identity: "f".repeat(64) }] });
  await c.answer(testing, deckyDetails(current, "decky_testing"));
  await c.answer(stable, deckyDetails(deckyEntry("decky-1", { description: "Obsolete stable Decky metadata" })));
  assert.ok(selectedDeckyVersion(c).includes("3.0.0"));
  assert.ok(!c.text().includes("Obsolete stable Decky metadata"));
  await c.click("Inspect compatibility");
  assert.deepEqual(c.last("catalog_assess").arg, { id: "decky-1", source: "decky_testing", identity: "e".repeat(64), version_identity: "f".repeat(64), confirm: true });
});

t("repeated inspection clicks send one assessment while the archive is pending", async () => {
  const c = deckyBoot();
  await c.open();
  await c.click("Details: Decky alpha utility");
  await selectDeckyVersion(c, "1.0.0");
  const inspect = c.find("Inspect compatibility");
  inspect.click();
  inspect.click();
  await settle();
  inspect.click();
  await settle();
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.equal(c.last("catalog_assess").arg.version_identity, "d".repeat(64));
  assert.ok(c.text().includes("Inspecting archive"));
  await c.answer(c.last("catalog_assess"), { error: "Example archive unavailable", code: "network" });
  assert.equal(c.find("Inspect compatibility").getAttribute("aria-disabled"), "false");
  assert.ok(selectedDeckyVersion(c).includes("1.0.0"));
});

t("failed and rejected inspections retry the same selected version and source", async () => {
  const c = deckyBoot();
  await c.open();
  await c.click("Filters");
  await select(c, "Source", "decky_testing");
  await c.click("Details: Decky alpha utility");
  await selectDeckyVersion(c, "1.0.0");
  await c.click("Inspect compatibility");
  const first = c.last("catalog_assess");
  assert.equal(first.arg.source, "decky_testing");
  assert.equal(first.arg.version_identity, "d".repeat(64));
  await c.answer(first, { error: "Example inspection failed", code: "archive" });
  assert.ok(c.text().includes("Example inspection failed"));
  assert.ok(c.text().includes("ARM64: Not checked"));
  assert.ok(!c.text().includes("Archive SHA-256 verified"));
  assert.ok(selectedDeckyVersion(c).includes("1.0.0"));
  c.rejectNext("catalog_assess", new Error("Example inspection rejection"));
  await c.click("Inspect compatibility");
  assert.ok(c.text().includes("Example inspection rejection"));
  assert.ok(c.text().includes("ARM64: Not checked"));
  assert.ok(!c.text().includes("Archive SHA-256 verified"));
  assert.ok(selectedDeckyVersion(c).includes("1.0.0"));
  await c.click("Inspect compatibility");
  const retry = c.last("catalog_assess");
  assert.notEqual(retry.id, first.id);
  assert.deepEqual(retry.arg, first.arg);
  await c.answer(retry, deckyAssessment("decky-1", { version: "1.0.0", hash: "b".repeat(64) }));
  assert.ok(c.text().includes("Archive inspection"));
  assert.ok(c.text().includes("Archive SHA-256 verified"));
  assert.ok(selectedDeckyVersion(c).includes("1.0.0"));
  assert.ok(!c.text().includes("Example inspection rejection"));
  assert.equal(c.sent("catalog_install").length, 0);
});

t("a late archive inspection cannot repaint another entry's details", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const pending = c.last("catalog_assess");
  await c.click("Back");
  await c.click("Details: Decky beta utility");
  const current = c.text();
  const result = deckyAssessment();
  result.assessment.reasons = ["Obsolete alpha archive assessment"];
  await c.answer(pending, result);
  assert.equal(c.text(), current);
  assert.ok(!c.text().includes("Obsolete alpha archive assessment"));
  assert.equal(c.sent("catalog_install").length, 0);
});

t("pending archive inspections stay bound to their original Decky source", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const stable = c.last("catalog_assess");
  await c.click("Back");
  await c.click("Filters");
  await select(c, "Source", "decky_testing");
  await c.click("Details: Decky alpha utility");
  await c.click("Inspect compatibility");
  const testing = c.last("catalog_assess");
  assert.notEqual(testing.id, stable.id);
  assert.equal(stable.arg.source, "decky_stable");
  assert.equal(testing.arg.source, "decky_testing");
  const oldResult = deckyAssessment();
  oldResult.assessment.reasons = ["Obsolete stable archive assessment"];
  await c.answer(stable, oldResult);
  assert.ok(c.text().includes("Inspecting archive"));
  assert.ok(!c.text().includes("Obsolete stable archive assessment"));
  const currentResult = deckyAssessment();
  currentResult.assessment.reasons = ["Current testing archive assessment"];
  await c.answer(testing, currentResult);
  assert.ok(c.text().includes("Current testing archive assessment"));
  assert.ok(!c.text().includes("Obsolete stable archive assessment"));
});

t("leaving read-only archive inspection never reports an install or invented cancellation", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const pending = c.last("catalog_assess");
  await c.away();
  const destination = c.text();
  await c.answer(pending, deckyAssessment());
  assert.equal(c.text(), destination);
  assert.ok(!/cancelled|canceled|Installation complete/i.test(c.body()));
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(!c.calls.some(call => /cancel|^install$|^add$/.test(call.method)));
});

t("generic operation progress cannot be confused with a pending archive inspection", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  await c.progress({ id: "decky-1", job: "unrelated-operation", stage: "install", label: "Unrelated Decky installation", state: "ok" });
  await c.progress({ id: "decky-1", stage: "inspect", label: "Uncorrelated archive progress" });
  assert.ok(c.text().includes("Inspecting archive"));
  assert.ok(!c.text().includes("Unrelated Decky installation"));
  assert.ok(!c.text().includes("Uncorrelated archive progress"));
  assert.equal(c.sent("catalog_install").length, 0);
});

t("full disposal ignores a pending Decky archive assessment", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const pending = c.last("catalog_assess");
  const oldBox = c.box();
  const before = oldBox.textContent;
  c.K.dispose();
  const result = deckyAssessment();
  result.assessment.reasons = ["Disposed archive assessment"];
  await c.answer(pending, result);
  assert.equal(oldBox.onkeydown, null);
  assert.equal(oldBox.textContent, before);
  assert.ok(!c.body().includes("Disposed archive assessment"));
  assert.equal(c.sent("catalog_install").length, 0);
});

t("changing the selected version invalidates the pending archive assessment", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const currentVersion = c.last("catalog_assess");
  await selectDeckyVersion(c, "1.0.0");
  assert.ok(!c.text().includes("Inspecting archive"));
  await c.click("Inspect compatibility");
  const olderVersion = c.last("catalog_assess");
  assert.equal(currentVersion.arg.version_identity, "c".repeat(64));
  assert.equal(olderVersion.arg.version_identity, "d".repeat(64));
  const obsolete = deckyAssessment();
  obsolete.assessment.reasons = ["Obsolete version assessment"];
  await c.answer(currentVersion, obsolete);
  assert.ok(c.text().includes("Inspecting archive"));
  assert.ok(!c.text().includes("Obsolete version assessment"));
  const current = deckyAssessment("decky-1", { version: "1.0.0", hash: "b".repeat(64) });
  current.assessment.reasons = ["Selected older version assessment"];
  await c.answer(olderVersion, current);
  assert.ok(selectedDeckyVersion(c).includes("1.0.0"));
  assert.ok(c.text().includes("Selected older version assessment"));
  assert.ok(!c.text().includes("Obsolete version assessment"));
});

t("inspection results with the wrong entry, version or hash remain unconfirmed and retryable", async () => {
  for (const changed of [{ id: "decky-2" }, { version: "9.0.0" }, { hash: "f".repeat(64) }]) {
    const c = deckyBoot();
    await c.open();
    await startInspection(c);
    const result = deckyAssessment("decky-1", changed);
    result.assessment.reasons = ["Unconfirmed assessment content"];
    await c.answer(c.last("catalog_assess"), result);
    assert.ok(c.text().includes("Inspection failed"));
    assert.ok(c.text().includes("could not be confirmed"));
    assert.ok(!c.text().includes("Unconfirmed assessment content"));
    assert.ok(!c.text().includes("Archive SHA-256 verified"));
    assert.equal(c.find("Inspect compatibility").getAttribute("aria-disabled"), "false");
    assert.equal(c.sent("catalog_install").length, 0);
  }
});

t("a stale Decky inspection cannot repaint native Store or change either page's source", async () => {
  const c = deckyBoot();
  await c.open();
  await c.click("Filters");
  await select(c, "Source", "decky_testing");
  await startInspection(c);
  const pending = c.last("catalog_assess");
  assert.equal(pending.arg.source, "decky_testing");
  assert.equal(c.sent("catalog_sources").filter(call => call.arg && call.arg.source).length, 0);
  await c.openNative();
  assert.equal(c.last("catalog_list").arg.source, "stable");
  assert.ok(c.has("Details: Alpha utility"));
  const native = c.text();
  const result = deckyAssessment();
  result.assessment.reasons = ["Obsolete Decky archive result"];
  await c.answer(pending, result);
  assert.equal(c.text(), native);
  assert.ok(!c.text().includes("Obsolete Decky archive result"));
  await c.open();
  assert.equal(c.last("catalog_list").arg.source, "decky_testing");
  await c.click("Filters");
  const deckySelect = c.box().querySelector('select[aria-label="Source"]');
  assert.equal(deckySelect.value, "decky_testing");
  assert.deepEqual([...deckySelect.options].map(option => option.value).sort(), ["decky_stable", "decky_testing"]);
  await c.openNative();
  assert.equal(c.last("catalog_list").arg.source, "stable");
  await c.click("Filters");
  const nativeSelect = c.box().querySelector('select[aria-label="Source"]');
  assert.equal(nativeSelect.value, "stable");
  assert.deepEqual([...nativeSelect.options].map(option => option.value).sort(), ["custom", "stable", "testing"]);
  assert.equal(c.sent("catalog_sources").filter(call => call.arg && call.arg.source).length, 0);
});

t("a failed reinspection clears the prior archive finding and explains why ARM64 is not checked", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const verified = deckyAssessment();
  verified.assessment.architecture = "x86_64";
  verified.assessment.reasons = ["Previous verified native finding"];
  await c.answer(c.last("catalog_assess"), verified);
  assert.ok(c.text().includes("Previous verified native finding"));
  assert.ok(c.text().includes("Needs an ARM64 build"));
  await c.click("Inspect compatibility");
  await c.answer(c.last("catalog_assess"), { error: "Example archive could not be downloaded", code: "network" });
  assert.ok(c.text().includes("Inspection failed"));
  assert.ok(c.text().includes("Example archive could not be downloaded"));
  assert.ok(c.text().includes("ARM64: Not checked"));
  assert.ok(!c.text().includes("Previous verified native finding"));
  assert.ok(!c.text().includes("Needs an ARM64 build"));
  assert.ok(!c.text().includes("Archive SHA-256 verified"));
  assert.equal(c.sent("catalog_install").length, 0);
});

t("Back to browse cannot resurrect an old x86 finding after latest reinspection fails", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const verified = deckyAssessment();
  verified.assessment.architecture = "x86_64";
  verified.assessment.reasons = ["Old latest-version architecture finding"];
  await c.answer(c.last("catalog_assess"), verified);
  assert.ok(c.text().includes("Needs an ARM64 build"));
  await c.click("Inspect compatibility");
  await c.answer(c.last("catalog_assess"), { error: "Latest archive inspection failed", code: "network" });
  assert.ok(c.text().includes("Latest archive inspection failed"));
  assert.ok(c.text().includes("ARM64: Not checked"));
  await c.click("Back");
  const row = c.find("Details: Decky alpha utility").parentElement;
  assert.ok(row.textContent.includes("ARM64: Not checked"));
  assert.ok(!row.textContent.includes("Needs an ARM64 build"));
  assert.ok(!row.textContent.includes("Old latest-version architecture finding"));
});

t("returning to a version whose inspection is pending cannot start a duplicate archive download", async () => {
  const c = deckyBoot();
  await c.open();
  await c.click("Details: Decky alpha utility");
  await selectDeckyVersion(c, "1.0.0");
  await c.click("Inspect compatibility");
  const pending = c.last("catalog_assess");
  assert.equal(pending.arg.version_identity, "d".repeat(64));
  await selectDeckyVersion(c, "2.0.0");
  assert.ok(!c.text().includes("Inspecting archive"));
  await selectDeckyVersion(c, "1.0.0");
  assert.ok(c.text().includes("Inspecting archive"));
  await c.click("Inspect compatibility");
  await c.click("Inspect compatibility");
  assert.equal(c.sent("catalog_assess").length, 1);
  const result = deckyAssessment("decky-1", { version: "1.0.0", hash: "b".repeat(64) });
  result.assessment.reasons = ["Original pending version inspection completed"];
  await c.answer(pending, result);
  assert.ok(c.text().includes("Original pending version inspection completed"));
  assert.ok(!c.text().includes("Inspecting archive"));
  assert.ok(selectedDeckyVersion(c).includes("1.0.0"));
  assert.equal(c.sent("catalog_assess").length, 1);
});

t("focused Inspect stays near the inspection controls while pending and after completion", async () => {
  const c = deckyBoot();
  await c.open();
  await c.click("Details: Decky alpha utility");
  const inspect = c.find("Inspect compatibility");
  inspect.focus();
  assert.strictEqual(c.d.activeElement, inspect);
  const scroller = c.box().parentElement;
  scroller.scrollTop = 280;
  await c.click("Inspect compatibility");
  const pendingFocus = c.d.activeElement;
  assert.ok(c.box().contains(pendingFocus));
  assert.ok(["Version", "Inspect compatibility"].includes(pendingFocus.getAttribute("aria-label")) || /Inspecting archive/.test(pendingFocus.textContent) && pendingFocus !== c.box());
  assert.notStrictEqual(pendingFocus, c.find("Back"));
  assert.equal(scroller.scrollTop, 280);
  const result = deckyAssessment();
  result.assessment.reasons = ["Focused inspection report"];
  await c.answer(c.last("catalog_assess"), result);
  const completedFocus = c.d.activeElement;
  assert.ok(c.box().contains(completedFocus));
  assert.ok(["Version", "Inspect compatibility"].includes(completedFocus.getAttribute("aria-label")) || /Archive inspection/.test(completedFocus.textContent) && completedFocus !== c.box());
  assert.notStrictEqual(completedFocus, c.find("Back"));
  assert.equal(scroller.scrollTop, 280);
  assert.ok(c.text().includes("Focused inspection report"));
});

t("reopening cached latest-version inspection keeps the top verdict consistent with its verified report", async () => {
  const c = deckyBoot();
  await c.open();
  await startInspection(c);
  const verified = deckyAssessment();
  verified.assessment.architecture = "x86_64";
  verified.assessment.reasons = ["Cached selected-package x86 finding"];
  await c.answer(c.last("catalog_assess"), verified);
  await c.click("Back");
  await c.click("Details: Decky alpha utility");
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.ok(selectedDeckyVersion(c).includes("2.0.0"));
  const text = c.text();
  const top = text.slice(0, text.indexOf("Description"));
  const report = text.slice(text.indexOf("Archive inspection"));
  assert.ok(top.includes("Needs an ARM64 build"));
  assert.ok(top.includes("Selected version 2.0.0"));
  assert.ok(top.includes("a".repeat(12)));
  assert.ok(!top.includes("ARM64: Not checked"));
  assert.ok(report.includes("x86-64 native binaries were found"));
  assert.ok(report.includes("Archive SHA-256 verified"));
  assert.ok(report.includes("Cached selected-package x86 finding"));
});

t("ArrowUp and ArrowLeft move from focused pending Inspect to Version without scrolling or another request", async () => {
  const c = deckyBoot();
  await c.open();
  await c.click("Details: Decky alpha utility");
  c.find("Inspect compatibility").focus();
  await c.click("Inspect compatibility");
  const pending = c.find("Inspect compatibility");
  const choice = c.box().querySelector('select[aria-label="Version"]');
  assert.strictEqual(c.d.activeElement, pending);
  assert.equal(pending.getAttribute("aria-disabled"), "true");
  const focusOptions = [];
  const focus = choice.focus;
  choice.focus = function(options) { focusOptions.push(options); return focus.call(this, options); };
  const scroller = c.box().parentElement;
  scroller.scrollTop = 280;
  for (const key of ["ArrowUp", "ArrowLeft"]) {
    pending.focus();
    const event = new c.w.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
    pending.dispatchEvent(event);
    await settle();
    assert.ok(event.defaultPrevented);
    assert.strictEqual(c.d.activeElement, choice);
    assert.equal(focusOptions.at(-1).preventScroll, true);
    assert.equal(scroller.scrollTop, 280);
    assert.equal(c.sent("catalog_assess").length, 1);
    assert.ok(c.text().includes("Inspecting archive"));
  }
  pending.click();
  await settle();
  assert.equal(c.sent("catalog_assess").length, 1);
  assert.equal(c.sent("catalog_install").length, 0);
  assert.ok(!c.calls.some(call => /cancel/.test(call.method)));
});

(async () => {
  let pass = 0, fail = 0;
  for (const [name, run] of tests) {
    try { await run(); pass++; console.log("ok   " + name); }
    catch (error) { fail++; console.log("FAIL " + name + "\n" + (error.stack || error)); }
  }
  console.log(pass + " passed, " + fail + " failed, " + tests.length + " total");
  process.exit(fail ? 1 : 0);
})();
