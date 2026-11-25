"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom, TOK } = require("./harness");
const boot = (t, requireTrusted = false) => {
  const e = makeDom({ loadFiles: ["fy-api.js", "fy-pages.js"], before(w) {
    w.FY_RUNTIME = { panel_layout: "compact", trusted_input: requireTrusted ? "require" : "off" };
    w.matchMedia = () => ({ matches: false });
  } });
  t.after(() => e.window.close());
  e.FY = e.window.Framey; e.K = e.FY._k(TOK);
  e.panel = e.document.querySelector('[role="region"][aria-label="Framey"]');
  e.menu = () => e.panel.querySelector('[role="toolbar"][aria-label="Framey launcher"]');
  e.buttons = () => [...e.menu().querySelectorAll('[role="button"]')];
  e.tile = route => e.buttons().find(node => node.dataset.fyLaunch === route);
  e.button = label => e.buttons().find(node => node.getAttribute("aria-label") === label);
  return e;
};
const plugin = (e, id, render = () => {}) => e.window.__bindFramey(id, "fixture-token-" + id, { name: "Plugin " + id }).register({ render });
const flush = () => new Promise(resolve => setImmediate(resolve));

test("ordinary compact opening is a launcher rather than the full navigation panel", t => {
  const e = boot(t);
  e.FY.toggle();
  assert.equal(e.K.tab(), "#launcher");
  assert.ok(e.menu());
  assert.equal(e.panel.dataset.fyLauncher, "true");
  assert.equal(e.panel.style.height, "72px");
  assert.equal(e.K.openLauncher instanceof e.window.Function, true);
  assert.equal(e.FY.openLauncher, undefined);
});

test("paged launcher grows with ordered plugin menus and preserves core destinations", t => {
  const e = boot(t);
  for (let i = 0; i < 9; i++) {
    const api = plugin(e, "p" + i);
    if (i === 0) api.pages.register({ id: "advanced", title: "Advanced fan options", render() {} });
  }
  e.K.openLauncher();
  e.answer(e.sent("state_get").at(-1), { order: ["p8", "p0"], hidden: ["p4"] });
  const routes = [];
  for (let batch = 0; batch < 7; batch++) {
    routes.push(...e.buttons().filter(node => node.dataset.fyLaunch).map(node => node.dataset.fyLaunch));
    const next = e.button("Next shortcuts");
    if (next.getAttribute("aria-disabled") === "true") break;
    next.click();
  }
  assert.deepEqual(routes, ["p8", "p0", "p0:advanced", "p1", "p2", "p3", "p5", "p6", "p7", "#discover", "#decky-store", "#settings", "#modules"]);
});

test("activation collapses before exactly one native handoff and never renders the page locally", async t => {
  const e = boot(t); let renders = 0;
  plugin(e, "p", () => renders++);
  e.K.openLauncher();
  e.tile("p").click(); e.tile("p").click();
  assert.equal(e.sent("panel_launch").length, 0);
  assert.equal(e.menu().dataset.phase, "closing");
  e.clock.tick(220);
  assert.equal(e.sent("panel_launch").length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(e.sent("panel_launch")[0].arg)), { route: "p" });
  e.answer(e.sent("panel_launch")[0], { ok: true });
  await flush();
  assert.equal(e.K.tab(), "#launcher");
  assert.equal(renders, 0);
  assert.equal(e.menu().dataset.phase, "closing");
});

test("failed launch stays in the launcher with a bounded recovery action", async t => {
  const e = boot(t);
  e.K.openLauncher();
  e.tile("#discover").click(); e.clock.tick(220);
  e.answer(e.sent("panel_launch")[0], { error: "fixture-sensitive-native-details" });
  await flush();
  assert.equal(e.K.tab(), "#launcher");
  assert.equal(e.menu().textContent.includes("fixture-sensitive"), false);
  assert.ok(e.button("Retry opening page"));
  e.button("Retry opening page").click(); e.clock.tick(220);
  assert.equal(e.sent("panel_launch").length, 2);
});

test("untrusted launch and closing during collapse cannot issue a native handoff", t => {
  const strict = boot(t, true);
  strict.K.openLauncher();
  strict.tile("#discover").click(); strict.clock.tick(220);
  assert.equal(strict.sent("panel_launch").length, 0);
  const e = boot(t);
  e.K.openLauncher(); e.tile("#discover").click();
  e.K.closePanel(); e.clock.tick(220);
  assert.equal(e.sent("panel_launch").length, 0);
});

test("submenu shortcuts identify their owning plugin", t => {
  const e = boot(t);
  plugin(e, "sound").pages.register({ id: "advanced", title: "Advanced", render() {} });
  e.K.openLauncher();
  assert.equal(e.tile("sound:advanced").getAttribute("aria-label"), "Plugin sound / Advanced");
  assert.equal(e.tile("sound:advanced").title, "Plugin sound / Advanced");
});

test("accordion arrows keep all default shortcuts reachable", t => {
  const e = boot(t); plugin(e, "fan"); e.K.openLauncher();
  const seen = [];
  for (let i=0;i<10;i++) {
    seen.push(...e.buttons().filter(n=>n.dataset.fyLaunch).map(n=>n.dataset.fyLaunch));
    assert.ok(e.button("Previous shortcuts")); const next=e.button("Next shortcuts"); assert.ok(next);
    if(next.getAttribute("aria-disabled")==="true") break; next.click();
  }
  assert.deepEqual(seen,["fan","#discover","#decky-store","#settings","#modules"]);
  assert.equal(e.panel.style.left,"50%");
});

test("accordion uses a stable popup width and at most four shortcuts per page", t => {
  const e=boot(t); assert.equal(e.K.launcherWidth(),462);
  for(let i=0;i<10;i++) plugin(e,"p"+i);
  Object.defineProperty(e.window,"innerWidth",{value:462,configurable:true}); e.K.openLauncher();
  assert.equal(e.K.launcherWidth(),462);
  assert.equal(e.buttons().filter(n=>n.dataset.fyLaunch).length,4);
  assert.ok(e.button("Next shortcuts"));
});

test("returning from launch recovery restores the selected shortcut on a later batch", async t => {
  const e = boot(t);
  for (let i = 0; i < 8; i++) plugin(e, "p" + i);
  e.K.openLauncher();
  while (!e.tile("#settings")) e.button("Next shortcuts").click();
  e.tile("#settings").click(); e.clock.tick(220);
  e.answer(e.sent("panel_launch")[0], { error: "target unavailable" });
  await flush();
  e.button("Back to shortcuts").focus();
  e.button("Back to shortcuts").dispatchEvent(new e.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  assert.equal(e.document.activeElement.dataset.fyLaunch, "#settings");
  e.document.activeElement.dispatchEvent(new e.window.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true }));
  assert.equal(e.document.activeElement.dataset.fyLaunch, "#decky-store");
});
