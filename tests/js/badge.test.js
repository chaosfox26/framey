"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");

const boot = (opts = {}) => {
  const spy = { listeners: 0, observers: 0 };
  const env = makeDom({
    before(w) {
      w.FY_ICON_URL = "icon.svg";
      w.Element.prototype.getBoundingClientRect = function () {
        const width = this.tagName === "IMG" ? 50 : 300;
        return { left: 0, top: 0, right: width, bottom: 40, width, height: 40 };
      };
      if (!opts.unmounted) {
        const bar = w.document.createElement("div");
        bar.id = "bar";
        const img = w.document.createElement("img");
        img.className = "avatar";
        bar.append(img, w.document.createElement("span"), w.document.createElement("span"));
        w.document.body.append(bar);
      }
    },
  });
  const w = env.window;
  const d = w.document;
  env.load("bar.js");
  const timersAfterBar = env.clock.pending();
  const orig = w.EventTarget.prototype.addEventListener;
  w.EventTarget.prototype.addEventListener = function (...a) {
    spy.listeners++;
    return orig.apply(this, a);
  };
  const OM = w.MutationObserver;
  w.MutationObserver = function (...a) {
    spy.observers++;
    return new OM(...a);
  };
  env.load("fy-badge.js");
  const btn = () => d.querySelector(".fy-bb");
  const dots = () => d.querySelectorAll(".fy-bdg");
  return { env, w, d, spy, btn, dots, bar: w.FYBar, timersAfterBar };
};

const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("badge(n>0) adds one 10px corner dot with the warm colour and updates the label; 0 removes it", () => {
  const { btn, dots, bar, d } = boot();
  assert.ok(btn().parentNode);
  assert.equal(dots().length, 0);
  assert.equal(btn().getAttribute("aria-label"), "Framey");
  bar.badge(3);
  assert.equal(dots().length, 1);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 3 updates");
  assert.equal(dots()[0].parentNode, btn());
  assert.equal(dots()[0].tagName, "B");
  assert.equal(dots()[0].getAttribute("aria-hidden"), "true");
  const css = d.getElementById("fy-badge-style").textContent;
  assert.ok(css.includes("width:10px") && css.includes("height:10px") && css.includes("#ffb347") && css.includes("pointer-events:none"));
  bar.badge(1);
  assert.equal(dots().length, 1);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 1 update");
  bar.badge(0);
  assert.equal(dots().length, 0);
  assert.equal(btn().getAttribute("aria-label"), "Framey");
});

t("the badge is independent of the open state: set(true/false) neither adds nor removes it", () => {
  const { btn, dots, bar } = boot();
  bar.badge(2);
  bar.set(true);
  assert.ok(btn().classList.contains("fy-on"));
  assert.equal(btn().getAttribute("aria-pressed"), "true");
  assert.equal(dots().length, 1);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 2 updates");
  bar.set(false);
  assert.ok(!btn().classList.contains("fy-on"));
  assert.equal(dots().length, 1);
  bar.badge(0);
  bar.set(true);
  assert.ok(btn().classList.contains("fy-on"));
  assert.equal(dots().length, 0);
  assert.equal(btn().getAttribute("aria-label"), "Framey");
  bar.set(false);
  bar.badge(4);
  assert.ok(!btn().classList.contains("fy-on"));
  assert.equal(dots().length, 1);
});

t("the badge never uses the fy-on class or touches the button's other attributes", () => {
  const { btn, dots, bar } = boot();
  const cls = btn().className;
  bar.badge(5);
  assert.equal(btn().className, cls);
  assert.ok(!dots()[0].classList.contains("fy-on"));
  assert.equal(dots()[0].className, "fy-bdg");
  assert.equal(btn().getAttribute("role"), "button");
  assert.equal(btn().getAttribute("aria-pressed"), "false");
  assert.equal(btn().children.length, 3);
});

t("repeated calls keep exactly one dot; junk values mean none; large values are capped", () => {
  const { btn, dots, bar } = boot();
  bar.badge(2);
  bar.badge(2);
  bar.badge(2);
  assert.equal(dots().length, 1);
  ["x", NaN, -3, null, undefined, {}, Infinity].forEach(v => {
    bar.badge(v);
    assert.equal(dots().length, 0, String(v));
    assert.equal(btn().getAttribute("aria-label"), "Framey");
  });
  bar.badge("7");
  assert.equal(btn().getAttribute("aria-label"), "Framey, 7 updates");
  bar.badge(1000);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 99 updates");
  bar.badge(2.9);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 2 updates");
});

t("no listener, observer or timer is added by loading the file or by calling badge", () => {
  const { env, bar, spy, timersAfterBar } = boot();
  assert.equal(spy.listeners, 0);
  assert.equal(spy.observers, 0);
  bar.badge(3);
  bar.set(true);
  bar.badge(0);
  assert.equal(spy.listeners, 0);
  assert.equal(spy.observers, 0);
  assert.equal(env.clock.pending(), timersAfterBar);
});

t("loading twice is harmless: one style, one wrapper, one dot", () => {
  const { env, bar, d, dots } = boot();
  const first = bar.set;
  env.load("fy-badge.js");
  assert.equal(bar.set, first);
  assert.equal(d.querySelectorAll("#fy-badge-style").length, 1);
  bar.badge(2);
  env.load("fy-badge.js");
  assert.equal(dots().length, 1);
});

t("a button that is not on the page yet gets its dot and label when bar.js mounts it", async () => {
  const { env, d, bar, btn, dots } = boot({ unmounted: true });
  assert.equal(btn(), null);
  bar.badge(2);
  const host = d.createElement("div");
  const img = d.createElement("img");
  img.className = "avatar";
  host.append(img, d.createElement("span"), d.createElement("span"));
  d.body.append(host);
  await new Promise(r => setImmediate(r));
  env.clock.tick(400);
  assert.ok(btn());
  assert.equal(dots().length, 1);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 2 updates");
  bar.set(false);
  assert.equal(dots().length, 1);
  assert.equal(btn().getAttribute("aria-label"), "Framey, 2 updates");
});

t("bar re-injection (wipe then bar.js again) gets a fresh badge function and the old style is replaced", () => {
  const { env, w, d } = boot();
  d.querySelectorAll("[data-fy]").forEach(e => e.remove());
  w.FYObs.disconnect();
  delete w.FYBar;
  assert.equal(d.querySelectorAll(".fy-bb").length, 0);
  env.load("bar.js");
  assert.equal(typeof w.FYBar.badge, "undefined");
  env.load("fy-badge.js");
  assert.equal(typeof w.FYBar.badge, "function");
  w.FYBar.badge(2);
  assert.equal(d.querySelectorAll(".fy-bdg").length, 1);
  assert.equal(d.querySelectorAll("#fy-badge-style").length, 1);
  assert.equal(d.querySelector(".fy-bb").getAttribute("aria-label"), "Framey, 2 updates");
});

t("without FYBar the file does nothing and throws nothing", () => {
  const env = makeDom({});
  env.load("fy-badge.js");
  assert.equal(env.window.FYBar, undefined);
  assert.equal(env.document.getElementById("fy-badge-style"), null);
});

t("dispose() disconnects the page observer that bar.js started", () => {
  const { w, bar } = boot();
  let off = 0;
  const obs = w.FYObs;
  const disconnect = obs.disconnect.bind(obs);
  obs.disconnect = () => { off++; disconnect(); };
  bar.dispose();
  assert.equal(off, 1);
});

t("dispose() removes the Framey button and its style", () => {
  const { btn, d, bar } = boot();
  bar.dispose();
  assert.equal(btn(), null);
  assert.equal(d.getElementById("fy-bar-style"), null);
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
