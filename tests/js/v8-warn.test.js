"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { JSDOM } = require(process.env.FRAMEY_JSDOM ? require.resolve("jsdom", { paths: [process.env.FRAMEY_JSDOM] }) : "jsdom");
const { TOK, ROOT } = require("./harness");

const MSG = "Framey: window.Framey already defined; not injecting";
const src = fs.readFileSync(path.join(ROOT, "inject.js"), "utf8");

const run = (pre) => {
  const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", { url: "https://steamloopback.host/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const warns = [];
  w.console.warn = (...a) => warns.push(a.join(" "));
  w.fyCall = (function (s) { return s; }).bind(null);
  if (pre !== undefined) w.Framey = pre;
  (0, w.eval)("(function(__fyt){" + src + "\n})(" + JSON.stringify(TOK) + ")");
  return { w, warns };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("pre-defined window.Framey: one warn, nothing overwritten", () => {
  const sentinel = { page: true };
  const { w, warns } = run(sentinel);
  assert.deepStrictEqual(warns, [MSG]);
  assert.strictEqual(w.Framey, sentinel);
  assert.deepStrictEqual(Object.keys(sentinel), ["page"]);
});

t("normal first injection: no warn", () => {
  const { w, warns } = run();
  assert.deepStrictEqual(warns, []);
  assert.strictEqual(typeof w.Framey.dispose, "function");
});

(async () => {
  let pass = 0, fail = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      pass++;
      console.log("ok   " + name);
    } catch (e) {
      fail++;
      console.log("FAIL " + name + "\n" + (e.stack || e));
    }
  }
  console.log(pass + " passed, " + fail + " failed, " + tests.length + " total");
  process.exit(fail ? 1 : 0);
})();
