"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { makeDom, ROOT } = require("./harness");

const repository = "https://github.com/chaosfox26/framey";
const draft = extra => Object.assign({ repository, title: "Framey: Example problem", body: "## Steps to reproduce\n1. Open Example.\n\n## Expected behavior\nWorks.\n\n## Actual behavior\nFails.", diagnostics: "Session-wide callback counters; not attributed to this plugin.\ncore.storage_failed: 2", notice: "Review before sharing.", warning: "" }, extra);
const clean = value => value.replace(/PRIVATE_[A-Z_]+/g, "[removed]");
const link = (payload, extra) => {
  const title = clean(payload.title), body = clean(payload.body) + (payload.diagnostics ? "\n\n## Optional diagnostics\n" + clean(payload.diagnostics) : "");
  const url = new URL(repository + "/issues/new");
  url.searchParams.set("title", title);
  url.searchParams.set("body", body);
  return Object.assign({ repository, title, body, url: url.href, code: "", warning: "" }, extra);
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const plain = value => JSON.parse(JSON.stringify(value));
const boot = (t, options = {}) => {
  const calls = [];
  const handlers = Object.assign({ report_draft: () => draft(), report_link: payload => link(payload), report_open: payload => Object.assign(link(payload), { requested: true }) }, options.handlers);
  const env = makeDom({ before(window) {
    window.FY_RUNTIME = Object.assign({ trusted_input: "off", report_repository: repository }, options.runtime);
    window.open = () => { throw new Error("No direct browser launch allowed"); };
    window.fyCall = encoded => {
      const request = JSON.parse(encoded);
      calls.push(request);
      if (!request.id) return;
      let value;
      try { value = handlers[request.method] ? handlers[request.method](plain(request.arg)) : { error: "unavailable", code: "unavailable" }; }
      catch (error) { value = Promise.reject(error); }
      Promise.resolve(value).then(result => window.Framey._reply(request.gen, request.id, result), () => window.Framey._reply(request.gen, request.id, { error: "unavailable", code: "unavailable" }));
    };
  }, loadFiles: ["fy-report.js"] });
  const { window, document } = env;
  t.after(() => window.close());
  const core = window.__S.K;
  const origin = document.createElement("button");
  origin.textContent = "Original plugin details";
  document.body.append(origin);
  origin.focus();
  const copied = [];
  Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { writeText: text => { copied.push(text); return Promise.resolve(); } } });
  const dialog = () => document.querySelector('[role="dialog"]');
  const button = name => {
    const node = [...dialog().querySelectorAll("button")].find(item => item.textContent === name);
    assert.ok(node, "Missing button " + name);
    return node;
  };
  const field = name => {
    const label = [...dialog().querySelectorAll("label")].find(item => item.firstElementChild && item.firstElementChild.textContent === name);
    const input = label && label.querySelector("input,textarea");
    assert.ok(input, "Missing field " + name);
    return input;
  };
  const set = (name, value) => {
    const node = field(name);
    node.value = value;
    node.dispatchEvent(new window.Event("input", { bubbles: true }));
  };
  const click = async name => { button(name).click(); await settle(); };
  const open = async context => { const promise = core.report(context || {}); await settle(); return { promise }; };
  return { env, core, window, document, origin, copied, handlers, dialog, button, field, set, click, open, sent: name => calls.filter(call => call.method === name), status: () => dialog().querySelector('[role="status"]').textContent };
};

test("helper loads before the management kit, opens only one modal and resolves only on close", async t => {
  const h = boot(t);
  assert.equal(h.core.kit, undefined);
  let resolved = false;
  const pending = h.core.report({ plugin: { id: "demo", name: "Example" } }).then(value => { resolved = true; return value; });
  await settle();
  assert.equal(resolved, false);
  const duplicate = h.core.report({ error: "other context" });
  assert.equal(h.document.querySelectorAll('[role="dialog"]').length, 1);
  assert.equal(h.sent("report_draft").length, 1);
  assert.ok(h.origin.hasAttribute("inert"));
  await h.click("Back");
  assert.deepEqual(plain(await pending), { closed: true });
  assert.deepEqual(plain(await duplicate), { closed: true });
  assert.equal(h.dialog(), null);
  assert.equal(h.document.activeElement, h.origin);
  assert.equal(h.origin.hasAttribute("inert"), false);
});

test("draft context is allowlisted and fixed dotted session counters reach backend without attribution", async t => {
  const h = boot(t);
  h.core.diagnostics = () => [{ code: "core.storage_failed", count: 2, private: "discard" }, { code: "page.render_failed", count: 12000 }, { code: "private fixture", count: 1 }, { code: "core.event_failed", count: true }];
  await h.open({ plugin: { id: "_loader", name: "Framey", version: "1.0.2", source: "native", extra: "discard" }, error: "Context", code: "settings_support", raw_log: "discard" });
  const context = plain(h.sent("report_draft")[0].arg);
  assert.deepEqual(Object.keys(context).sort(), ["code", "diagnostics", "error", "plugin"]);
  assert.deepEqual(Object.keys(context.plugin).sort(), ["name", "source", "version"]);
  assert.deepEqual(context.diagnostics, [{ code: "core.storage_failed", count: 2 }, { code: "page.render_failed", count: 9999 }]);
  assert.ok(h.field("Diagnostics (editable)").value.includes("Session-wide"));
});

test("initial preview keeps focus on the header without scrolling or focusing an editor", async t => {
  const pending = deferred();
  const h = boot(t, { handlers: { report_draft: () => pending.promise } });
  const focus = h.window.HTMLElement.prototype.focus;
  const calls = [];
  h.window.HTMLElement.prototype.focus = function (options) {
    calls.push({ tag: this.tagName, text: this.textContent, options });
    focus.call(this, options);
    const dialog = h.dialog();
    if (dialog && !(options && options.preventScroll)) dialog.scrollTop = 500;
  };
  h.core.report({});
  const dialog = h.dialog();
  assert.equal(h.document.activeElement, h.button("Close"));
  assert.deepEqual(plain(calls[0].options), { preventScroll: true });
  assert.equal(dialog.scrollTop, 0);
  dialog.scrollTop = 300;
  pending.resolve(draft());
  await settle();
  assert.equal(dialog.scrollTop, 0);
  assert.equal(h.document.activeElement, h.button("Close"));
  assert.ok(!calls.some(call => call.tag === "INPUT" || call.tag === "TEXTAREA"));
});

test("retry completion preserves the user's focus and scroll choice", async t => {
  const h = boot(t, { handlers: { report_draft: () => null } });
  await h.open();
  const pending = deferred();
  h.handlers.report_draft = () => pending.promise;
  h.button("Retry draft").click();
  h.button("Close").focus({ preventScroll: true });
  h.dialog().scrollTop = 180;
  pending.resolve(draft());
  await settle();
  assert.equal(h.document.activeElement, h.button("Close"));
  assert.equal(h.dialog().scrollTop, 180);
});

test("edited fields are re-sanitized before copy and merged diagnostics never replace the editor", async t => {
  const h = boot(t);
  await h.open();
  h.set("Issue title", "Edited PRIVATE_TITLE");
  h.set("Report details", "Edited PRIVATE_BODY");
  h.set("Diagnostics (editable)", "Edited PRIVATE_DIAGNOSTIC");
  await h.click("Copy sanitized report");
  assert.equal(h.copied.length, 1);
  assert.equal(h.copied[0], "Edited [removed]\n\nEdited [removed]\n\n## Optional diagnostics\nEdited [removed]");
  assert.equal(h.field("Report details").value, "Edited PRIVATE_BODY");
  assert.equal(h.field("Diagnostics (editable)").value, "Edited PRIVATE_DIAGNOSTIC");
  await h.click("Copy sanitized report");
  assert.equal((h.copied[1].match(/Optional diagnostics/g) || []).length, 1);
  assert.equal(h.sent("report_open").length, 0);
});

test("diagnostics can be edited, opted out, and removed before sharing", async t => {
  const h = boot(t);
  await h.open();
  const checkbox = h.dialog().querySelector('input[type="checkbox"]');
  checkbox.checked = false;
  checkbox.dispatchEvent(new h.window.Event("change", { bubbles: true }));
  await h.click("Copy sanitized report");
  assert.equal(h.sent("report_link")[0].arg.diagnostics, "");
  assert.ok(!h.copied[0].includes("Optional diagnostics"));
  checkbox.checked = true;
  await h.click("Remove diagnostics");
  assert.equal(h.field("Diagnostics (editable)").value, "");
  assert.equal(checkbox.checked, false);
  await h.click("Open GitHub draft");
  assert.equal(h.sent("report_open")[0].arg.diagnostics, "");
});

test("browser request uses original captured editor payload and never claims issue creation", async t => {
  const h = boot(t);
  await h.open();
  h.set("Report details", "Edited PRIVATE_BODY");
  h.set("Diagnostics (editable)", "PRIVATE_DIAGNOSTIC");
  await h.click("Open GitHub draft");
  assert.equal(h.sent("report_open").length, 1);
  assert.equal(h.sent("report_open")[0].arg.body, "Edited PRIVATE_BODY");
  assert.equal(h.sent("report_open")[0].arg.diagnostics, "PRIVATE_DIAGNOSTIC");
  assert.ok(!h.sent("report_open")[0].arg.body.includes("Optional diagnostics"));
  assert.ok(h.status().includes("Browser launch requested"));
  assert.ok(h.status().includes("Framey has not created an issue"));
  assert.ok(h.dialog().textContent.includes("Opening GitHub sends the draft to GitHub"));
});

test("long URLs offer complete sanitized copy without a browser request", async t => {
  const full = "Full sanitized body ".repeat(450);
  const h = boot(t, { handlers: { report_link: payload => link(payload, { title: "Long draft", body: full, url: null, code: "too_long" }) } });
  await h.open();
  await h.click("Open GitHub draft");
  assert.equal(h.sent("report_open").length, 0);
  assert.ok(h.status().includes("too long"));
  assert.equal(h.dialog().querySelector('[aria-label="Sanitized report to copy"]').value, "Long draft\n\n" + full);
  await h.click("Copy sanitized report");
  assert.equal(h.copied[0], "Long draft\n\n" + full);
});

test("clipboard rejection exposes selectable sanitized text only", async t => {
  const h = boot(t);
  await h.open();
  h.set("Report details", "PRIVATE_BODY");
  h.window.navigator.clipboard.writeText = () => Promise.reject(new Error("private system error"));
  await h.click("Copy sanitized report");
  const fallback = h.dialog().querySelector('[aria-label="Sanitized report to copy"]');
  assert.equal(fallback.hidden, false);
  assert.ok(fallback.value.includes("[removed]"));
  assert.ok(!fallback.value.includes("PRIVATE_BODY"));
  assert.equal(h.document.activeElement, fallback);
  assert.equal(fallback.selectionEnd, fallback.value.length);
  assert.ok(!h.status().includes("private system error"));
});

test("missing clipboard support offers the same sanitized fallback", async t => {
  const h = boot(t);
  await h.open();
  Object.defineProperty(h.window.navigator, "clipboard", { value: undefined });
  await h.click("Copy sanitized report");
  assert.ok(h.status().includes("Copy failed"));
  assert.equal(h.dialog().querySelector('[aria-label="Sanitized report to copy"]').hidden, false);
});

test("offline preview exposes only a generic basic-template fallback with no raw context", async t => {
  const h = boot(t, { handlers: { report_draft: () => ({ error: "PRIVATE_BACKEND_ERROR", code: "unavailable" }) } });
  await h.open({ plugin: { name: "PRIVATE_PLUGIN_NAME" }, error: "PRIVATE_CONTEXT_ERROR" });
  assert.equal(h.field("Issue title").value, "Framey problem report");
  assert.ok(!h.field("Report details").value.includes("PRIVATE_"));
  assert.equal(h.button("Open GitHub draft").disabled, true);
  assert.equal(h.button("Copy sanitized report").disabled, true);
  await h.click("Copy basic template");
  assert.ok(!h.copied[0].includes("PRIVATE_"));
  assert.ok(h.copied[0].includes("Steps to reproduce"));
  assert.equal(h.sent("report_link").length, 0);
});

test("missing repository preserves a sanitized draft and copy while disabling only browser opening", async t => {
  const h = boot(t, { runtime: { report_repository: "" }, handlers: { report_draft: () => draft({ repository: "", warning: "Repository unavailable" }), report_link: payload => link(payload, { repository: "", url: null, code: "repository_missing" }) } });
  await h.open();
  assert.equal(h.field("Issue title").value, "Framey: Example problem");
  assert.ok(h.field("Report details").value.includes("Open Example"));
  assert.equal(h.button("Open GitHub draft").disabled, true);
  assert.equal(h.button("Copy sanitized report").disabled, false);
  await h.click("Copy sanitized report");
  assert.ok(h.copied[0].includes("Open Example"));
  assert.equal(h.sent("report_open").length, 0);
});

test("a missing preview method requires an explicit retry and never automatically repeats", async t => {
  const h = boot(t, { handlers: { report_draft: () => null } });
  await h.open();
  assert.equal(h.sent("report_draft").length, 1);
  h.handlers.report_draft = () => draft();
  await h.click("Retry draft");
  assert.equal(h.sent("report_draft").length, 2);
  assert.equal(h.button("Open GitHub draft").disabled, false);
});

test("a repository disappearing after preview is not reported as a length error", async t => {
  const h = boot(t, { handlers: { report_link: payload => link(payload, { repository: "", url: null, code: "repository_missing" }) } });
  await h.open();
  await h.click("Open GitHub draft");
  assert.ok(h.status().includes("destination is unavailable"));
  assert.ok(!h.status().includes("too long"));
  assert.equal(h.sent("report_open").length, 0);
  assert.equal(h.button("Open GitHub draft").disabled, true);
  assert.equal(h.button("Copy sanitized report").disabled, false);
  await h.click("Copy sanitized report");
  assert.ok(h.copied[0].includes("Open Example"));
});

test("browser-opening failures retain sanitized fallback without submission claims", async t => {
  const h = boot(t, { handlers: { report_open: () => ({ error: "Browser unavailable", code: "browser_unavailable" }) } });
  await h.open();
  await h.click("Open GitHub draft");
  assert.ok(h.status().includes("Browser launch was not confirmed"));
  assert.equal(h.dialog().querySelector('[aria-label="Sanitized report to copy"]').hidden, false);
  assert.equal(h.sent("report_open").length, 1);
});

test("double click holds editable controls and launches at most one browser request", async t => {
  const pending = deferred();
  const h = boot(t, { handlers: { report_link: () => pending.promise } });
  await h.open();
  h.button("Open GitHub draft").click();
  h.button("Open GitHub draft").click();
  assert.equal(h.sent("report_link").length, 1);
  assert.equal(h.field("Issue title").disabled, true);
  assert.equal(h.button("Back").disabled, false);
  const payload = plain(h.sent("report_link")[0].arg);
  pending.resolve(link(payload));
  await settle();
  assert.equal(h.sent("report_open").length, 1);
  assert.equal(h.field("Issue title").disabled, false);
});

test("closing during preparation resolves the preview and ignores late data", async t => {
  const pending = deferred();
  const h = boot(t, { handlers: { report_draft: () => pending.promise } });
  const { promise } = await h.open();
  await h.click("Back");
  assert.deepEqual(plain(await promise), { closed: true });
  pending.resolve(draft());
  await settle();
  assert.equal(h.dialog(), null);
  assert.equal(h.sent("report_link").length, 0);
  assert.equal(h.origin.hasAttribute("inert"), false);
});

test("closing before link verification prevents a late browser request", async t => {
  const pending = deferred();
  const h = boot(t, { handlers: { report_link: () => pending.promise } });
  const { promise } = await h.open();
  h.button("Open GitHub draft").click();
  const payload = plain(h.sent("report_link")[0].arg);
  await h.click("Close");
  pending.resolve(link(payload));
  await settle();
  assert.deepEqual(plain(await promise), { closed: true });
  assert.equal(h.sent("report_open").length, 0);
});

test("wrong preview repository never replaces the configured Framey destination", async t => {
  const h = boot(t, { handlers: { report_draft: () => draft({ repository: "https://github.com/upstream/plugin", body: "PRIVATE_WRONG_DRAFT" }) } });
  await h.open({ plugin: { source: "https://github.com/upstream/plugin" } });
  assert.equal(h.field("Issue title").value, "Framey problem report");
  assert.ok(!h.dialog().textContent.includes("PRIVATE_WRONG_DRAFT"));
  assert.equal(h.field("Destination").value, repository + "/issues/new");
  assert.equal(h.button("Open GitHub draft").disabled, true);
});

test("wrong linked repository cannot open or copy that response", async t => {
  const h = boot(t, { handlers: { report_link: payload => link(payload, { repository: "https://github.com/upstream/plugin", body: "PRIVATE_WRONG_DRAFT" }) } });
  await h.open();
  await h.click("Open GitHub draft");
  assert.equal(h.sent("report_open").length, 0);
  assert.ok(!h.dialog().textContent.includes("PRIVATE_WRONG_DRAFT"));
  assert.equal(h.copied.length, 0);
});

test("unsafe or mismatched issue URLs are rejected without a browser request", async t => {
  for (const url of ["javascript:alert(1)", "https://github.com/upstream/plugin/issues/new", "https://user:pass@github.com/chaosfox26/framey/issues/new", repository + "/issues/new?redirect=elsewhere", repository + "/issues/new#fragment", repository + "/issues/new?body=" + "x".repeat(7600), repository + "/issues/new?body=" + "\u4e2d".repeat(900)]) {
    const h = boot(t, { handlers: { report_link: payload => link(payload, { url }) } });
    await h.open();
    await h.click("Open GitHub draft");
    assert.equal(h.sent("report_open").length, 0, url.slice(0, 90));
    await h.click("Back");
  }
});

const showRuntimePage = h => {
  const framey = h.window.Framey;
  if (!framey.pages) framey.pages = {};
  const render = name => box => {
    const button = h.document.createElement("button");
    button.textContent = name + " action";
    box.append(button);
  };
  framey.pages.modules = { render: render("Modules") };
  framey.pages.settings = { render: render("Settings") };
  framey.open("#modules");
  h.document.querySelector('[role="tabpanel"] button').focus();
  return framey;
};

test("same-route redraw preserves report edits and diagnostics are merged once when copied", async t => {
  const h = boot(t), framey = showRuntimePage(h);
  const { promise } = await h.open();
  h.set("Issue title", "Reviewed title");
  h.set("Report details", "Reviewed body");
  h.set("Diagnostics (editable)", "Reviewed counters");
  framey.redraw();
  framey.open("#modules");
  await settle();
  assert.ok(h.dialog());
  assert.equal(h.field("Issue title").value, "Reviewed title");
  assert.equal(h.field("Report details").value, "Reviewed body");
  await h.click("Copy sanitized report");
  assert.equal(h.copied[0], "Reviewed title\n\nReviewed body\n\n## Optional diagnostics\nReviewed counters");
  assert.equal((h.copied[0].match(/Optional diagnostics/g) || []).length, 1);
  await h.click("Back");
  assert.deepEqual(plain(await promise), { closed: true });
  assert.equal(h.document.activeElement.getAttribute("role"), "tab");
  assert.equal(h.document.activeElement.getAttribute("aria-selected"), "true");
});

test("true core navigation closes the report immediately and removes background inert state", async t => {
  const h = boot(t), framey = showRuntimePage(h);
  t.after(() => h.core.report.close());
  let closed = false;
  h.core.report({}).then(() => { closed = true; });
  await settle();
  framey.open("#settings");
  await settle();
  assert.equal(closed, true);
  assert.equal(h.dialog(), null);
  assert.equal(h.document.querySelector('[role="region"][aria-label="Framey"]').hasAttribute("inert"), false);
  assert.ok(h.document.querySelector('[role="tabpanel"]').textContent.includes("Settings action"));
});

test("panel toggle-close and full core disposal settle the report without leaving inert content", async t => {
  const h = boot(t), framey = showRuntimePage(h);
  t.after(() => h.core.report.close());
  let closed = false;
  h.core.report({}).then(() => { closed = true; });
  await settle();
  framey.toggle();
  await settle();
  assert.equal(closed, true);
  assert.equal(h.dialog(), null);
  assert.equal(h.origin.hasAttribute("inert"), false);
  framey.open("#modules");
  closed = false;
  h.core.report({}).then(() => { closed = true; });
  await settle();
  h.core.dispose();
  await settle();
  assert.equal(closed, true);
  assert.equal(h.dialog(), null);
  assert.equal(h.origin.hasAttribute("inert"), false);
});

test("teardown removal closes the promise and restores prior inert attributes", async t => {
  const h = boot(t);
  h.origin.setAttribute("inert", "original");
  const { promise } = await h.open();
  h.dialog().parentNode.remove();
  await settle();
  assert.deepEqual(plain(await promise), { closed: true });
  assert.equal(h.origin.getAttribute("inert"), "original");
});

test("focus or observer cleanup failures cannot strand the close promise", async t => {
  const h = boot(t);
  const errors = [];
  h.core.diagnostic = code => errors.push(code);
  const disconnect = h.window.MutationObserver.prototype.disconnect;
  h.window.MutationObserver.prototype.disconnect = function () { disconnect.call(this); throw new Error("private fixture error"); };
  const { promise } = await h.open();
  h.origin.focus = () => { throw new Error("private fixture focus"); };
  await h.click("Back");
  assert.deepEqual(plain(await promise), { closed: true });
  assert.equal(h.dialog(), null);
  assert.deepEqual(errors, ["core.cleanup_failed", "core.cleanup_failed"]);
});

test("Escape and focus wrapping keep the report review in one accessible modal", async t => {
  const h = boot(t);
  const { promise } = await h.open();
  const controls = [...h.dialog().querySelectorAll("button,input,textarea")].filter(node => !node.disabled && !node.hidden);
  controls[controls.length - 1].focus();
  const tab = new h.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
  h.document.activeElement.dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, true);
  assert.equal(h.document.activeElement, controls[0]);
  h.dialog().dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.deepEqual(plain(await promise), { closed: true });
  assert.equal(h.document.activeElement, h.origin);
});

test("source uses core backend opening only and never stores report drafts", () => {
  const source = fs.readFileSync(path.join(ROOT, "fy-report.js"), "utf8");
  assert.ok(!/window\.open|SteamClient|sessionStorage|localStorage|innerHTML|insertAdjacentHTML/.test(source));
  assert.ok(!/setTimeout|setInterval|fetch\(/.test(source));
});
