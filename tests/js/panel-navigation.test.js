"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom, TOK } = require("./harness");
const boot = (t, layout, trusted = "off") => {
  const e = makeDom({ loadFiles: ["fy-api.js", "fy-pages.js"], before(w) { w.FY_RUNTIME = { panel_layout: layout, trusted_input: trusted }; } });
  t.after(() => e.window.close());
  e.FY = e.window.Framey;
  e.K = e.FY._k(TOK);
  e.panel = e.document.querySelector('[role="region"][aria-label="Framey"]');
  e.content = e.panel.querySelector('[role="tabpanel"]');
  e.button = text => [...e.panel.querySelectorAll('[role="button"]')].find(x => x.textContent === text);
  e.tabs = () => [...e.panel.querySelectorAll('[role="tab"]')];
  return e;
};
const register = (e, id, name, events = []) => e.window.__bindFramey(id, "tok-" + id + "-0000", { name, short: id }).register({ render(box) { events.push("render"); box.textContent = "Content of " + id; }, leave() { events.push("leave"); } });

test("group navigation keeps installed plugins and catalogs out of the permanent tabs", t => {
  const e = boot(t);
  register(e, "sound", "Sound controls for a very long plugin name");
  e.FY.open("#plugins");
  assert.deepEqual(e.tabs().map(x => x.textContent), ["Plugins", "Browse", "Settings"]);
  assert.ok(e.button("Sound controls for a very long plugin name"));
  assert.ok(e.button("Manage plugins"));
  assert.equal(e.button("Framey Store"), undefined);
  e.tabs()[1].click();
  assert.equal(e.K.tab(), "#browse");
  e.button("Framey Store").click();
  assert.equal(e.K.tab(), "#discover");
  e.button("Back").click();
  assert.equal(e.K.tab(), "#browse");
  e.button("Decky catalog").click();
  assert.equal(e.K.tab(), "#decky-store");
  assert.equal(e.tabs()[1].getAttribute("aria-selected"), "true");
});

test("nested plugin routes stay grouped and Back returns focus to the plugin destination", t => {
  const e = boot(t), events = [];
  const api = register(e, "sound", "Sound controls", events);
  api.pages.register({ id: "advanced", title: "Advanced sound", render(box) { box.textContent = "Advanced content"; }, onclose() { events.push("subpage close"); } });
  e.FY.open("#plugins");
  e.button("Advanced sound").click();
  assert.equal(e.K.tab(), "sound:advanced");
  assert.equal(e.content.textContent, "Advanced content");
  e.button("Back").click();
  assert.equal(e.K.tab(), "#plugins");
  assert.deepEqual(events, ["subpage close"]);
  assert.equal(e.document.activeElement.textContent, "Advanced sound");
  e.button("Sound controls").click();
  e.button("Back").click();
  assert.deepEqual(events, ["subpage close", "render", "leave"]);
});

test("saved ordering and hidden preferences update plugin shortcuts while direct routes remain available", t => {
  const e = boot(t);
  register(e, "a", "Alpha");
  const b = register(e, "b", "Beta");
  b.pages.register({ id: "extra", title: "Beta details", render(box) { box.textContent = "Private details"; } });
  e.FY.open("#plugins");
  e.answer(e.sent("state_get").at(-1), { order: ["b", "a"], hidden: [] });
  assert.deepEqual([...e.content.querySelectorAll('[data-fy-route]')].map(x => x.textContent), ["Beta", "Beta details", "Alpha"]);
  e.K.call("state_get");
  e.answer(e.sent("state_get").at(-1), { order: ["b", "a"], hidden: ["b"] });
  assert.equal(e.button("Beta"), undefined);
  assert.equal(e.button("Beta details"), undefined);
  e.FY.open("b:extra");
  assert.equal(e.content.textContent, "Private details");
  e.button("Back").click();
  assert.equal(e.button("Beta details"), undefined);
});

test("Close and Escape restore host focus and navigation obeys trusted input", t => {
  const e = boot(t, "compact");
  const host = e.document.createElement("button");
  e.document.body.append(host);
  host.focus();
  e.FY.open("#plugins");
  e.button("Close").click();
  assert.equal(e.panel.style.display, "none");
  assert.equal(e.document.activeElement, host);
  e.FY.open("#browse");
  e.panel.dispatchEvent(new e.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(e.document.activeElement, host);
  const strict = boot(t, "window", "require");
  strict.FY.open("#plugins");
  strict.tabs()[1].click();
  strict.button("Close").click();
  strict.panel.dispatchEvent(new strict.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(strict.K.tab(), "#plugins");
  assert.equal(strict.panel.style.display, "flex");
});

test("presentation changes keep the active plugin, focus and lifecycle while bounding both sizes", t => {
  const e = boot(t, "compact"), events = [];
  register(e, "a", "Alpha", events);
  e.FY.open("a");
  const active = e.document.activeElement, body = e.content.firstChild;
  assert.equal(e.panel.dataset.fyLayout, "compact");
  assert.equal(e.panel.style.bottom, "11px");
  assert.equal(e.panel.style.top, "auto");
  assert.equal(e.panel.style.width, "360px");
  assert.equal(e.K.setPanelLayout("window"), "window");
  assert.equal(e.panel.dataset.fyLayout, "window");
  assert.equal(e.panel.style.top, "11px");
  assert.equal(e.panel.style.bottom, "auto");
  assert.equal(e.panel.style.width, "520px");
  assert.equal(e.content.firstChild, body);
  assert.equal(e.document.activeElement, active);
  assert.equal(e.K.tab(), "a");
  assert.deepEqual(events, ["render"]);
  assert.equal(e.K.setPanelLayout("unknown"), "window");
  assert.equal(e.FY.setPanelLayout, undefined);
  assert.equal(e.panel.querySelector('[role="tablist"]').style.overflowY, "");
  assert.ok(e.panel.style.maxWidth.includes("100vw"));
  assert.ok(e.panel.style.maxHeight.includes("100vh"));
  const legacy = boot(t);
  assert.equal(legacy.panel.dataset.fyLayout, "window");
});

test("shortcut redraw preserves keyboard focus when another plugin adds a page", t => {
  const e = boot(t);
  register(e, "a", "Alpha");
  const b = register(e, "b", "Beta");
  e.FY.open("#plugins");
  e.button("Alpha").focus();
  b.pages.register({ id: "new", title: "Beta details", render() {} });
  assert.equal(e.document.activeElement.textContent, "Alpha");
  assert.ok(e.button("Beta details"));
});

test("new destination route names do not revoke previously valid plugin identities", t => {
  const e = boot(t);
  register(e, "plugins", "Plugin tools");
  register(e, "browse", "Browser tools");
  e.FY.open("#plugins");
  e.button("Plugin tools").click();
  assert.equal(e.K.tab(), "plugins");
  assert.equal(e.content.textContent, "Content of plugins");
  e.FY.open("browse");
  assert.equal(e.K.tab(), "browse");
  assert.equal(e.content.textContent, "Content of browse");
});

test("compact shortcuts use explicit pages so every plugin and subpage is reachable without scrolling", t => {
  const e = boot(t, "compact");
  for (let i = 0; i < 17; i++) {
    const api = register(e, "p" + i, "Installed plugin " + i);
    if (i === 0) api.pages.register({ id: "details", title: "Plugin zero details", render() {} });
  }
  e.FY.open("#plugins");
  const seen = [];
  for (let page = 0; page < 19; page++) {
    const routes = [...e.content.querySelectorAll('[data-fy-route]')];
    assert.equal(routes.length, 1);
    seen.push(routes[0].dataset.fyRoute);
    assert.equal(e.content.style.overflow, "hidden");
    if (page < 18) e.button("Next").click();
  }
  assert.deepEqual(seen, ["p0", "p0:details", ...Array.from({ length: 16 }, (_, i) => "p" + (i + 1)), "#modules"]);
  assert.equal(e.button("Next").getAttribute("aria-disabled"), "true");
  e.button("Previous").click();
  assert.equal(e.content.querySelector('[data-fy-route]').dataset.fyRoute, "p16");
});

test("Back and presentation reflow retain a shortcut reached on a later page", t => {
  const e = boot(t, "compact"), events = [];
  for (let i = 0; i < 8; i++) register(e, "p" + i, "Plugin " + i, events);
  e.FY.open("#plugins");
  for (let i = 0; i < 6; i++) e.button("Next").click();
  e.button("Plugin 6").click();
  e.button("Back").click();
  assert.equal(e.document.activeElement.textContent, "Plugin 6");
  e.K.setPanelLayout("window");
  assert.ok(e.button("Plugin 6"));
  assert.equal(e.document.activeElement.textContent, "Plugin 6");
  assert.deepEqual(events, ["render", "leave"]);
});

test("private builtin pager reserves fixed navigation and cannot keep a previous route callback alive", t => {
  const e = boot(t, "compact"), pages = [];
  e.FY.open("#settings");
  const paint = index => {
    pages.push(index);
    e.content.textContent = "Screen " + index;
    e.K.setPageNavigation({ index, count: 3, onPage: paint, label: "Preferences" });
  };
  paint(0);
  const next = e.button("Next");
  next.focus();
  next.click();
  assert.equal(e.content.textContent, "Screen 1");
  assert.equal(e.document.activeElement, next);
  assert.equal(e.content.style.overflow, "hidden");
  e.FY.open("#plugins");
  next.click();
  assert.deepEqual(pages, [0, 1]);
  assert.equal(e.FY.setPageNavigation, undefined);
});

test("builtin Back and Escape use the fixed header callback even when there is only one screen", t => {
  const e = boot(t, "compact");
  let returned = 0;
  e.FY.open("#settings");
  e.K.setPageNavigation({ index: 0, count: 1, title: "Placement", onBack() { returned++; } });
  assert.equal(e.button("Back").style.display, "flex");
  e.button("Back").click();
  e.panel.dispatchEvent(new e.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(returned, 2);
  assert.equal(e.panel.style.display, "flex");
});

test("builtin layout callbacks run once per actual presentation change", t => {
  const e = boot(t, "compact"), changes = [];
  e.FY.open("#settings");
  e.K.setPageNavigation({ index: 0, count: 1, onLayout(layout) { changes.push(layout); } });
  e.K.setPanelLayout("compact");
  e.K.setPanelLayout("window");
  e.K.setPanelLayout("window");
  assert.deepEqual(changes, ["window"]);
});

test("pager callback failures are contained and diagnosed without rendering their error details", async t => {
  const e = boot(t, "compact");
  e.FY.open("#settings");
  e.K.setPageNavigation({ index: 0, count: 2, onPage() { throw new Error("private fixture failure"); } });
  assert.doesNotThrow(() => e.button("Next").click());
  e.K.setPageNavigation({ index: 0, count: 2, onPage() { return Promise.reject(new Error("private fixture rejection")); } });
  e.button("Next").click();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(JSON.parse(JSON.stringify(e.K.diagnostics())), [{ code: "core.panel_failed", count: 2 }]);
  assert.equal(e.panel.textContent.includes("private fixture"), false);
});

test("presentation reflow keeps the current shortcut page when its Next button has focus", t => {
  const e = boot(t, "compact");
  for (let i = 0; i < 9; i++) register(e, "p" + i, "Plugin " + i);
  e.FY.open("#plugins");
  for (let i = 0; i < 6; i++) e.button("Next").click();
  e.button("Next").focus();
  e.K.setPanelLayout("window");
  assert.ok(e.button("Plugin 6"));
  assert.equal(e.document.activeElement.textContent, "Next");
});

test("Next changes the shortcut screen even when a shortcut retains keyboard focus", t => {
  const e = boot(t, "compact");
  register(e, "a", "Alpha"); register(e, "b", "Beta");
  e.FY.open("#plugins");
  e.button("Alpha").focus();
  e.button("Next").click();
  assert.ok(e.button("Beta"));
  assert.equal(e.button("Alpha"), undefined);
});

test("missing compact catalog keeps recovery reachable through pages without scrolling", t => {
  const e = boot(t, "compact");
  e.FY.open("#discover");
  assert.equal(e.content.style.overflow, "hidden");
  assert.ok(e.content.textContent.includes("Store is not available"));
  e.button("Next").click();
  e.button("Next").click();
  e.button("Open Modules").click();
  assert.equal(e.K.tab(), "#modules");
});
