"use strict";
const assert = require("assert");
const { test } = require("node:test");
const { makeDom } = require("./harness");

const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
const boot = () => {
  const bridge = { bar: null };
  const mainCalls = [], barCalls = [];
  const main = makeDom({
    before(w) {
      w.FY_RUNTIME = { version: "1.0.2", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "off" };
      w.fyCall = encoded => {
        const request = JSON.parse(encoded);
        mainCalls.push(request);
        if (request.method === "panel" && bridge.bar) bridge.bar.set(request.arg);
        if (!request.id) return;
        let response = {};
        if (request.method === "state_get") response = { order: [], hidden: [], frozen: {}, prefs: {}, safe: false };
        if (request.method === "catalog_sources") response = {
          source: { id: "stable", label: "Stable" },
          sources: [{ id: "stable", label: "Stable" }, { id: "testing", label: "Testing" }, { id: "custom", label: "Custom" }],
          decky_sources: [{ id: "decky_stable", label: "Decky Store" }, { id: "decky_testing", label: "Decky Store Testing" }],
        };
        if (request.method === "catalog_list") response = {
          source: { id: request.arg.source, label: request.arg.source === "decky_stable" ? "Decky Store" : "Stable" },
          origin: "downloaded", stale: false, total: 1, offset: 0, limit: 50,
          rows: [{ id: "decky-example", name: "Example plugin", version: "1.0.0", format: "decky", read_only: true,
            eligible: false, platform_declared: false, architecture: "unknown", identity: "a".repeat(64),
            compat: { state: "incompatible", badge: "port", msg: "Needs a Framey port" } }],
        };
        Promise.resolve().then(() => w.Framey._reply(request.gen, request.id, response));
      };
    },
    loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"],
  });
  const bar = makeDom({
    before(w) {
      w.FY_ICON_URL = "icon.svg";
      w.Element.prototype.getBoundingClientRect = function () {
        const width = this.tagName === "IMG" ? 50 : 300;
        return { left: 0, top: 0, right: width, bottom: 48, width, height: 48 };
      };
      const host = w.document.createElement("div"), avatar = w.document.createElement("img");
      avatar.className = "avatar";
      host.append(avatar, w.document.createElement("span"), w.document.createElement("span"));
      w.document.body.append(host);
      w.fyCall = encoded => {
        const request = JSON.parse(encoded);
        barCalls.push(request);
        if (request.method === "toggle") main.window.Framey.toggle();
      };
    },
    loadFiles: ["bar.js", "fy-badge.js"],
  });
  bridge.bar = bar.window.FYBar;
  return {
    main, bar, mainCalls, barCalls, control: bridge.bar,
    close() { bridge.bar.dispose(); main.window.dispatchEvent(new main.window.Event("pagehide")); bar.window.close(); main.window.close(); },
  };
};

test("the original bar toggle follows panel state across the native and Decky Store tabs", async () => {
  const e = boot();
  try {
    e.control.badge(3);
    const button = e.control.button;
    button.click();
    await settle();
    assert.equal(e.main.window.Framey.panelOpen, true);
    assert.equal(button.getAttribute("aria-pressed"), "true");
    e.main.window.Framey.open("#decky-store");
    await settle();
    const panel = e.main.document.querySelector('[role="tabpanel"]');
    assert.match(panel.textContent, /Decky Store/);
    assert.match(panel.textContent, /Not compatible/);
    assert.match(panel.textContent, /ARM64: Not checked/);
    assert.equal(button.getAttribute("aria-label"), "Framey, 3 updates");
    assert.equal(button.querySelectorAll(".fy-bdg").length, 1);
    e.main.window.Framey.open("#discover");
    await settle();
    assert.equal(button.getAttribute("aria-label"), "Framey, 3 updates");
    button.click();
    assert.equal(e.main.window.Framey.panelOpen, false);
    assert.equal(button.getAttribute("aria-pressed"), "false");
    assert.equal(button.getAttribute("aria-label"), "Framey, 3 updates");
    assert.equal(e.barCalls.filter(request => request.method === "toggle").length, 2);
    assert.equal(e.mainCalls.some(request => request.method === "catalog_install"), false);
  } finally { e.close(); }
});

test("unassessed Decky catalog entries never create a bottom-bar update badge", async () => {
  const e = boot();
  try {
    e.main.window.Framey.open("#decky-store");
    await settle();
    assert.match(e.main.document.querySelector('[role="tabpanel"]').textContent, /Example plugin/);
    assert.equal(e.control.button.getAttribute("aria-label"), "Framey");
    assert.equal(e.control.button.querySelectorAll(".fy-bdg").length, 0);
    e.control.set(false);
    assert.equal(e.control.button.getAttribute("aria-label"), "Framey");
  } finally { e.close(); }
});
