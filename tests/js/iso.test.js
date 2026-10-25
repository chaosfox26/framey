"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { ROOT, makeDom, TOK } = require("./harness");

const FILES = ["inject.js", "bar.js", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-modules.js", "fy-settings.js", "fy-badge.js", "fy-picker.js", "fy-dev.js", "fy-strings-en.js", "fy-catalog.js"];
const OWN = new Set(["Framey", "FYBar", "FYObs", "FY_ICON_URL", "FY_RUNTIME", "fyCall", "__fyBye", "performance", "navigator", "addEventListener", "removeEventListener"]);
const PAGE = /\b(SteamClient|opener|wrappedJSObject|g_PopupManager|MainWindowBrowserManager)\b|window\.(parent|top|frames)\b/;

const queue = [];
const t = (name, fn) => queue.push([name, fn]);
let pass = 0;
let fail = 0;

FILES.forEach(f => {
  const src = fs.readFileSync(path.join(ROOT, f), "utf8");
  t(f + ": reads only globals that Framey itself defines or the browser provides", () => {
    const seen = new Set([...src.matchAll(/\bwindow\.([A-Za-z_$][\w$]*)/g)].map(m => m[1]));
    const foreign = [...seen].filter(n => !OWN.has(n));
    assert.deepEqual(foreign, []);
  });
  t(f + ": uses nothing a page defines (an isolated world does not see page globals)", () => {
    assert.ok(!PAGE.test(src));
  });
});

t("shared APIs work when Steam page globals are absent from the isolated world", () => {
  const reads = [];
  const e = makeDom({
    loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js", "fy-settings.js", "fy-picker.js", "fy-dev.js"],
    before: window => {
      ["SteamClient", "g_PopupManager", "MainWindowBrowserManager", "wrappedJSObject"].forEach(name => {
        Object.defineProperty(window, name, { configurable: true, get() { reads.push(name); throw new Error("foreign page global unavailable"); } });
      });
    },
  });
  const a = e.plugin("a");
  let payload;
  a.events.on("tick", value => { payload = value; });
  e.event("a", "tick", 7);
  const page = a.pages.register({ id: "details", mode: "full", render(box) { box.textContent = "Details"; } });
  assert.strictEqual(page.open(), true);
  assert.strictEqual(payload, 7);
  assert.ok(e.document.querySelector('[role="dialog"]').textContent.includes("Details"));
  page.close();
  e.window.Framey.dispose("a", TOK);
  assert.deepStrictEqual(reads, []);
  assert.strictEqual(e.document.querySelector('[role="dialog"]'), null);
});

t("catalog assessment uses the core capability without consulting Steam page globals", async () => {
  const reads = [];
  const e = makeDom({
    loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"],
    before: window => {
      ["SteamClient", "g_PopupManager", "MainWindowBrowserManager", "wrappedJSObject"].forEach(name => {
        Object.defineProperty(window, name, { configurable: true, get() { reads.push(name); throw new Error("foreign page global unavailable"); } });
      });
    },
  });
  assert.strictEqual(typeof e.window.Framey.pages.store.render, "function");
  assert.strictEqual(typeof e.window.Framey.pages.decky_store.render, "function");
  const request = e.window.__S.K.call("catalog_assess", { id: "fixture", source: "decky_stable", confirm: true });
  const sent = e.sent("catalog_assess")[0];
  assert.strictEqual(sent.plugin, "_core");
  assert.strictEqual(sent.t, TOK);
  e.answer(sent, { read_only: true, assessment: { not_executed: true } });
  const result = await request;
  assert.strictEqual(result.read_only, true);
  assert.strictEqual(result.assessment.not_executed, true);
  assert.deepStrictEqual(reads, []);
  e.window.__S.K.dispose();
  e.window.close();
});

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
  }
  console.log(pass + " passed, " + fail + " failed, " + (pass + fail) + " total");
  process.exit(fail ? 1 : 0);
})();
