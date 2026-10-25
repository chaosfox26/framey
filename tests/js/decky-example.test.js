"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { makeDom, ROOT, TOK } = require("./harness");

const DIRECTORY = path.join(ROOT, "examples", "decky-style");
const MANIFEST = JSON.parse(fs.readFileSync(path.join(DIRECTORY, "plugin.json"), "utf8"));
const plain = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const tests = [];
const test = (name, run) => tests.push([name, run]);

function boot(mainFile = "examples/decky-style/main.js") {
  const env = makeDom({ loadFiles: ["fy-api.js", "fy-ui.js"] });
  env.loadPlugin("decky-style", mainFile, MANIFEST);
  const plugin = env.record("decky-style");
  const api = env.window.Framey._k(TOK).apiOf(plugin);
  const box = env.document.createElement("div");
  env.document.body.append(box);
  const answer = (method, result) => {
    const call = env.calls.find(item => item.method === method && !item.done);
    assert.ok(call, "pending " + method);
    call.done = true;
    env.answer(call, result);
    return call;
  };
  const button = label => [...box.querySelectorAll('[role="button"]')].find(node => node.textContent === label);
  const show = async (snapshot = { goal: "Practice", count: 0 }) => {
    const rendering = plugin.render(box, api);
    answer("read_settings", snapshot);
    await rendering;
    await settle();
  };
  return { env, plugin, api, box, answer, button, show };
}

test("generated main loads through the normal plugin wrapper with no imports or globals", async () => {
  const h = boot();
  assert.equal(h.plugin.name, "Session Check-ins");
  assert.equal(h.env.calls.length, 0);
  await h.show({ goal: "Read", count: 4 });
  assert.ok(h.box.textContent.includes("Read: 4 check-ins"));
  assert.equal(h.box.querySelector('[role="option"][aria-selected="true"]').textContent, "Read");
  for (const label of ["Save goal", "Check in", "Reset count", "Refresh"]) assert.ok(h.button(label), label);
  assert.equal(h.env.window.createDeckySourceAdapter, undefined);
  assert.deepEqual(plain(h.env.sent("read_settings")[0].arg), []);
});

test("only the three native runtime files are needed after copying the package", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "fy-decky-native-"));
  try {
    for (const name of ["plugin.json", "main.js", "backend.py"]) {
      fs.copyFileSync(path.join(DIRECTORY, name), path.join(directory, name));
    }
    assert.deepEqual(fs.readdirSync(directory).sort(), ["backend.py", "main.js", "plugin.json"]);
    const h = boot(path.join(directory, "main.js"));
    await h.show({ goal: "Plan", count: 2 });
    assert.ok(h.box.textContent.includes("Plan: 2 check-ins"));
    h.button("Check in").click();
    assert.deepEqual(plain(h.env.sent("check_in")[0].arg), []);
    h.env.event("decky-style", "check_in", [{ goal: "Plan", count: 3 }]);
    h.answer("check_in", { goal: "Plan", count: 3 });
    await settle();
    assert.ok(h.box.textContent.includes("Plan: 3 check-ins"));
    h.env.window.Framey.dispose("decky-style", TOK);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("source-style calls use positional arrays and apply saved snapshots", async () => {
  const h = boot();
  await h.show();
  const plan = [...h.box.querySelectorAll('[role="option"]')].find(node => node.textContent === "Plan");
  plan.click();
  h.button("Save goal").click();
  assert.deepEqual(plain(h.env.sent("save_goal")[0].arg), ["Plan"]);
  h.answer("save_goal", { goal: "Plan", count: 0 });
  await settle();
  assert.ok(h.box.textContent.includes("Plan: 0 check-ins"));
  h.button("Check in").click();
  assert.deepEqual(plain(h.env.sent("check_in")[0].arg), []);
  h.answer("check_in", { goal: "Plan", count: 1 });
  await settle();
  assert.ok(h.box.textContent.includes("Plan: 1 check-ins"));
  h.button("Reset count").click();
  h.answer("reset_count", { goal: "Plan", count: 0 });
  await settle();
  assert.ok(h.box.textContent.includes("Count reset."));
});

test("native backend events are spread once and other plugin events stay isolated", async () => {
  const h = boot();
  await h.show();
  h.env.event("other", "check_in", [{ goal: "Read", count: 20 }]);
  assert.ok(h.box.textContent.includes("Practice: 0 check-ins"));
  h.env.event("decky-style", "check_in", [{ goal: "Read", count: 7 }]);
  assert.ok(h.box.textContent.includes("Read: 7 check-ins"));
  assert.ok(h.box.textContent.includes("Backend event received: check-in 7."));
  h.box.replaceChildren();
  await h.plugin.render(h.box, h.api);
  assert.equal(h.env.sent("read_settings").length, 1);
  h.env.event("decky-style", "check_in", [{ goal: "Read", count: 8 }]);
  assert.ok(h.box.textContent.includes("Read: 8 check-ins"));
  assert.equal(h.env.clock.pending(), 0);
});

test("a failed load offers Refresh and errors stay literal text", async () => {
  const h = boot();
  await h.show({ error: "<img src=x onerror=alert(1)>" });
  assert.ok(h.box.textContent.includes("Could not complete: <img src=x onerror=alert(1)>"));
  assert.equal(h.box.querySelector("img"), null);
  h.button("Refresh").click();
  h.answer("read_settings", { goal: "Read", count: 5 });
  await settle();
  assert.ok(h.box.textContent.includes("Read: 5 check-ins"));
  h.button("Check in").click();
  h.answer("check_in", { error: "worker unavailable" });
  await settle();
  assert.ok(h.box.textContent.includes("Could not complete: worker unavailable"));
  assert.ok(h.box.textContent.includes("Read: 5 check-ins"));
});

test("busy clicks do not duplicate work and late replies cannot revive a disposed page", async () => {
  const h = boot();
  await h.show();
  h.button("Check in").click();
  h.button("Check in").click();
  h.button("Save goal").click();
  assert.equal(h.env.sent("check_in").length, 1);
  assert.equal(h.env.sent("save_goal").length, 0);
  h.env.window.Framey.dispose("decky-style", TOK);
  const text = h.box.textContent;
  h.env.event("decky-style", "check_in", [{ goal: "Plan", count: 30 }]);
  h.answer("check_in", { goal: "Plan", count: 30 });
  await settle();
  assert.equal(h.box.textContent, text);
  assert.equal(h.env.clock.pending(), 0);
  assert.equal(h.env.record("decky-style").dead, true);
});

test("disposal during the initial call prevents asynchronous rendering", async () => {
  const h = boot();
  const rendering = h.plugin.render(h.box, h.api);
  h.env.window.Framey.dispose("decky-style", TOK);
  h.answer("read_settings", { goal: "Read", count: 1 });
  await rendering;
  await settle();
  assert.equal(h.box.children.length, 0);
});

(async () => {
  let failures = 0;
  for (const [name, run] of tests) {
    try { await run(); console.log("ok   " + name); }
    catch (error) { failures++; console.error("FAIL " + name + "\n" + error.stack); }
  }
  console.log((tests.length - failures) + " passed, " + failures + " failed");
  process.exitCode = failures ? 1 : 0;
})();
