"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { ROOT } = require("./harness");

const FILES = ["inject.js", "bar.js", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-modules.js", "fy-settings.js", "fy-badge.js", "fy-picker.js", "fy-dev.js", "fy-strings-en.js"];
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

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
  }
  console.log(pass + " passed, " + fail + " failed, " + (pass + fail) + " total");
  process.exit(fail ? 1 : 0);
})();
