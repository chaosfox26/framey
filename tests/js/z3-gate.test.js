"use strict";
const assert = require("assert");
const { makeDom, TOK } = require("./harness");

const tests = [];
const t = (name, fn) => tests.push([name, fn]);
const flush = () => new Promise(r => setImmediate(r));
const TRUST_TYPES = ["click", "keydown", "pointerdown"];

const boot = (mode, files) => {
  const trustListeners = {};
  const env = makeDom({
    loadFiles: files || ["fy-api.js"],
    before: w => {
      const add = w.addEventListener.bind(w);
      w.addEventListener = (type, fn, cap) => {
        if (cap === true && TRUST_TYPES.includes(type)) trustListeners[type] = fn;
        return add(type, fn, cap);
      };
      if (mode !== undefined) w.FY_RUNTIME = { version: "1.1.0", api: 1, platform: "linux", lang: "en", trusted_input: mode };
    },
  });
  env.trustListeners = trustListeners;
  return env;
};

const fireTrustedCapture = (e, type) => e.trustListeners[type]({ type, isTrusted: true });
const clickSynthetic = el => el.click();
const keySynthetic = (w, el) => el.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
const button = (e, fn) => {
  const b = e.window.Framey.button("Go", "", fn);
  e.window.document.body.append(b);
  return b;
};
const bind = (e, id) => e.window.__bindFramey(id, "tok-" + id + "-0000", { name: id, short: id });

t("trust auto (default): synthetic clicks and keys run until a genuine trusted event has been seen", () => {
  const e = boot();
  const F = e.window.Framey;
  let n = 0;
  const b = button(e, () => n++);
  clickSynthetic(b);
  keySynthetic(e.window, b);
  assert.strictEqual(n, 2);
  assert.strictEqual(F.diag().untrusted_clicks, 0);
});

TRUST_TYPES.forEach(type => {
  t("trust auto: a genuine " + type + " flips the page to require; a later synthetic click is refused", () => {
    const e = boot();
    const F = e.window.Framey;
    let n = 0;
    const b = button(e, () => n++);
    clickSynthetic(b);
    assert.strictEqual(n, 1);
    fireTrustedCapture(e, type);
    clickSynthetic(b);
    assert.strictEqual(n, 1);
    assert.strictEqual(F.diag().untrusted_clicks, 1);
    b.dispatchEvent(new e.window.Event("click"));
    assert.strictEqual(n, 1);
    assert.strictEqual(F.diag().untrusted_clicks, 2);
  });
});

t("trust require: synthetic clicks and keys are refused from the start, and a trusted event still passes", () => {
  const e = boot("require");
  const F = e.window.Framey;
  let n = 0;
  const b = button(e, () => n++);
  clickSynthetic(b);
  keySynthetic(e.window, b);
  assert.strictEqual(n, 0);
  assert.strictEqual(F.diag().untrusted_clicks, 2);
  fireTrustedCapture(e, "click");
  clickSynthetic(b);
  assert.strictEqual(n, 0);
});

t("trust off: synthetic input always runs and nothing is counted, even after a trusted event", () => {
  const e = boot("off");
  const F = e.window.Framey;
  let n = 0;
  const b = button(e, () => n++);
  clickSynthetic(b);
  keySynthetic(e.window, b);
  fireTrustedCapture(e, "click");
  clickSynthetic(b);
  assert.strictEqual(n, 3);
  assert.strictEqual(F.diag().untrusted_clicks, 0);
});

t("trust gate: the seen flag and gate mode are not reachable from plugin code", () => {
  const e = boot();
  const F = e.window.Framey;
  ["seen", "mode", "untrusted", "__trust", "__trusted"].forEach(k => assert.strictEqual(k in F, false, k));
  assert.strictEqual(e.window.__trust, undefined);
  assert.strictEqual(e.window.__trusted, undefined);
});

t("trust gate: handlers are listeners, not own properties, so el.onclick({isTrusted: true}) cannot run a gated action", () => {
  const e = boot("require");
  let n = 0;
  const b = button(e, () => n++);
  assert.strictEqual(b.onclick, null);
  assert.strictEqual(b.onkeydown, null);
  assert.strictEqual(n, 0);
});

t("modules: plugin B cannot set A's dead flag, rename itself to A, dispose A, or reach A's api", () => {
  const e = boot();
  const F = e.window.Framey;
  let ticksA = 0;
  let disposedA = 0;
  const apiA = bind(e, "a").register({ render() {}, dispose() { disposedA++; } });
  const apiB = bind(e, "b").register({ render() {} });
  const pa = F.plugins.find(p => p.id === "a");
  const pb = F.plugins.find(p => p.id === "b");
  assert.strictEqual(Reflect.set(pa, "dead", true), false);
  assert.strictEqual(pa.dead, undefined);
  assert.strictEqual(Reflect.set(pb, "id", "a"), false);
  assert.strictEqual(pb.id, "b");
  assert.strictEqual(Reflect.set(pa, "render", () => {}), false);
  assert.strictEqual(Reflect.set(pa, "dispose", () => {}), false);
  assert.ok(Object.isFrozen(pa) && Object.isFrozen(pb));
  assert.ok(!F.plugins.some(p => Object.values(p).includes(apiA)), "an api is reachable from a module view");
  F.dispose("a");
  F.dispose("a", "not-the-core-token");
  assert.strictEqual(pa.dead, undefined);
  assert.strictEqual(disposedA, 0);
  apiA.timeout(() => ticksA++, 10);
  e.clock.tick(10);
  assert.strictEqual(ticksA, 1);
  const seenA = [];
  const seenB = [];
  apiA.events.on("tick", () => seenA.push(1));
  apiB.events.on("tick", () => seenB.push(1));
  e.event("b", "tick", {});
  assert.deepStrictEqual([seenA.length, seenB.length], [0, 1]);
  e.event("a", "tick", {});
  assert.deepStrictEqual([seenA.length, seenB.length], [1, 1]);
  F.dispose("a", TOK);
  assert.strictEqual(pa.dead, true);
  assert.strictEqual(disposedA, 1);
});

t("core events never reach plugin listen; the core dispatcher still gets update_result and progress", () => {
  const e = boot();
  const F = e.window.Framey;
  const h = bind(e, "a");
  h.register({ render() {} });
  const plugin = [];
  h.listen("update_result", p => plugin.push(["update_result", p]));
  h.listen("progress", p => plugin.push(["progress", p]));
  const core = [];
  e.window.__S.K.listen("update_result", p => core.push(p));
  e.event("_core", "update_result", { ok: true }, 0);
  e.event("_core", "progress", { stage: "download" }, 0);
  assert.deepStrictEqual(plugin, []);
  assert.deepStrictEqual(core, [{ ok: true }]);
  assert.strictEqual("listen" in F, false);
  e.event("a", "progress", { mine: 1 }, 0);
  assert.deepStrictEqual(plugin, [["progress", { mine: 1 }]]);
});

t("core reply: a lost _core reply resolves with an error after 15 s and frees its slot; a late reply is ignored", async () => {
  const e = boot();
  const K = e.window.__S.K;
  let res;
  K.call("state_get").then(v => { res = v; });
  assert.strictEqual(K.pending(), 1);
  e.clock.tick(14999);
  await flush();
  assert.strictEqual(res, undefined);
  assert.strictEqual(K.pending(), 1);
  e.clock.tick(1);
  await flush();
  assert.match(res.error, /did not reply in 15 s/);
  assert.strictEqual(K.pending(), 0);
  const late = e.calls[e.calls.length - 1];
  e.answer(late, { late: true });
  assert.strictEqual(K.pending(), 0);
});

t("core reply: a 100 s update call resolves on its reply and clears its timer", async () => {
  const e = boot();
  const K = e.window.__S.K;
  let res;
  K.call("update", { id: "a" }).then(v => { res = v; });
  e.clock.tick(100000);
  await flush();
  assert.strictEqual(res, undefined);
  assert.strictEqual(K.pending(), 1);
  e.answer(e.sent("update")[0], { ok: true });
  await flush();
  assert.deepStrictEqual(res, { ok: true });
  assert.strictEqual(K.pending(), 0);
  assert.strictEqual(e.clock.pending(), 0);
});

t("core reply: settings_repair and fetch calls stay pending past 60 s", async () => {
  const e = boot();
  const K = e.window.__S.K;
  K.call("settings_repair");
  K.call("fetch", { url: "https://github.com/x" });
  e.clock.tick(100000);
  await flush();
  assert.strictEqual(K.pending(), 2);
});

t("core reply: a backend call with a declared 50 s timeout resolves on a reply at 40 s", async () => {
  const e = boot();
  const api = bind(e, "a").register({ render() {} });
  const K = e.window.__S.K;
  const p = api.call("slow", undefined, 50000);
  e.clock.tick(40000);
  assert.strictEqual(K.pending(), 1);
  e.answer(e.sent("slow")[0], "done");
  assert.strictEqual(await p, "done");
  assert.strictEqual(e.clock.pending(), 0);
});

t("core reply: a backend call with no declared timeout gives up after 60 s", async () => {
  const e = boot();
  const api = bind(e, "a").register({ render() {} });
  const K = e.window.__S.K;
  let res;
  api.call("slow").then(v => { res = v; });
  e.clock.tick(59999);
  await flush();
  assert.strictEqual(res, undefined);
  e.clock.tick(1);
  await flush();
  assert.match(res.error, /module did not reply in 60 s/);
  assert.strictEqual(K.pending(), 0);
});

t("notify: plugin toasts carry the plugin id in brackets, core notices do not", () => {
  const e = boot();
  const F = e.window.Framey;
  bind(e, "a").register({ render() {} }).notify({ title: "Update available", duration: 0 });
  F.notify({ title: "Update available", duration: 0 });
  const boxes = [...e.document.querySelectorAll('[role="status"]')];
  const texts = boxes.map(b => b.textContent);
  assert.ok(texts.some(x => x.includes("[a] Update available")), texts.join("|"));
  assert.ok(boxes.some(b => b.dataset.fy === "_core" && b.textContent.includes("Update available") && !b.textContent.includes("[")));
});

const main = async () => {
  let pass = 0;
  const failed = [];
  for (const [name, fn] of tests) {
    try {
      await fn();
      pass++;
      console.log("ok   " + name);
    } catch (err) {
      failed.push(name);
      console.log("FAIL " + name + "\n     " + String(err && err.stack || err).split("\n").slice(0, 6).join("\n     "));
    }
  }
  console.log("\n" + pass + " passed, " + failed.length + " failed, " + tests.length + " total");
  process.exit(failed.length ? 1 : 0);
};

main();
