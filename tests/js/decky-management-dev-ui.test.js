"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom } = require("./harness");

const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const open = async t => {
  const env = makeDom({ loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-dev.js"] });
  const { window: w, document: d } = env;
  t.after(() => w.close());
  const answer = (method, value) => {
    const request = env.sent(method).find(call => !call.done);
    assert.ok(request, "No pending " + method);
    request.done = true;
    env.answer(request, value);
  };
  Object.defineProperty(w.navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.resolve() } });
  w.Framey.pages.decky_store = { render(box) { box.textContent = "Decky catalog test destination"; } };
  w.Framey.open("#developer");
  answer("dev_status", { enabled: true, hot: [], exposure: { state: "loopback", addresses: ["127.0.0.1:8080"] } });
  answer("list", [{ id: "demo", name: "Demo" }]);
  await settle();
  const area = d.querySelector("textarea");
  assert.ok(area);
  const box = area.parentNode.parentNode;
  const button = name => {
    const found = [...box.querySelectorAll("button")].find(node => node.textContent === name);
    assert.ok(found, "No button " + name);
    return found;
  };
  const feedback = () => box.querySelector('[role="status"]').textContent;
  const ready = async () => {
    button("Run diagnostics").click();
    answer("diag", { loader: { version: "test" } });
    await settle();
  };
  return { env, w, d, box, area, button, answer, ready, feedback };
};

test("an older clipboard failure cannot overwrite a newer successful copy", async t => {
  const h = await open(t);
  await h.ready();
  const first = deferred();
  let calls = 0;
  h.w.navigator.clipboard.writeText = () => ++calls === 1 ? first.promise : Promise.resolve();
  h.button("Copy").click();
  h.button("Copy").click();
  await settle();
  assert.equal(h.feedback(), "Copied.");
  first.reject(new Error("old permission failure"));
  await settle();
  assert.equal(h.feedback(), "Copied.");
});

test("an older copy cannot hide a newer diagnostics network failure", async t => {
  const h = await open(t);
  await h.ready();
  const pending = deferred();
  h.w.navigator.clipboard.writeText = () => pending.promise;
  h.button("Copy").click();
  h.button("Run diagnostics").click();
  h.answer("diag", { error: "Network status is unavailable", code: "network" });
  await settle();
  assert.equal(h.feedback(), "Diagnostics failed: Network status is unavailable");
  pending.resolve();
  await settle();
  assert.equal(h.feedback(), "Diagnostics failed: Network status is unavailable");
  assert.equal(JSON.parse(h.area.value).loader.code, "network");
});

test("copying a debug target reports the actual clipboard denial", async t => {
  const h = await open(t);
  const address = "http://127.0.0.1:8080/devtools/inspector.html?ws=127.0.0.1:8080/devtools/page/demo";
  h.button("Find debug targets").click();
  h.answer("devtools_info", { available: true, targets: [{ name: "main", title: "Steam", inspector: address, url: "https://steamloopback.host/" }] });
  await settle();
  let copied;
  h.w.navigator.clipboard.writeText = value => { copied = value; return Promise.reject(new Error("Clipboard access denied")); };
  h.button("Copy address").click();
  await settle();
  assert.equal(copied, address);
  assert.equal(h.feedback(), "Copy failed: Clipboard access denied");
});

test("global navigation to Decky Store ignores late developer replies and copy results", async t => {
  const h = await open(t);
  await h.ready();
  const pending = deferred();
  h.w.navigator.clipboard.writeText = () => pending.promise;
  h.button("Copy").click();
  h.button("Run diagnostics").click();
  h.button("Find debug targets").click();
  const previousText = h.area.value;
  const previousFeedback = h.feedback();
  h.w.Framey.open("#decky-store");
  h.answer("diag", { error: "Late diagnostics failure", code: "network" });
  h.answer("devtools_info", { available: false, error: "Late debug target failure", targets: [] });
  pending.reject(new Error("Late clipboard denial"));
  await settle();
  assert.equal(h.box.isConnected, false);
  assert.equal(h.area.value, previousText);
  assert.equal(h.feedback(), previousFeedback);
  assert.ok(h.d.body.textContent.includes("Decky catalog test destination"));
  assert.ok(!h.d.body.textContent.includes("Late"));
  assert.equal(h.env.sent("dev_status").length, 1);
});
