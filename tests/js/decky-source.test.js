"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const { createDeckySourceAdapter } = require("../../compat/decky-source");
const { makeDom, TOK } = require("./harness");

const tests = [];
const test = (name, run) => tests.push([name, run]);
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const stub = () => {
  const calls = [], handlers = new Map(), cleanups = new Set(), logs = [];
  const api = {
    call(method, args) { calls.push({ method, args }); return Promise.resolve(args); },
    cleanup(fn) { cleanups.add(fn); return () => cleanups.delete(fn); },
    events: {
      on(name, fn) {
        if (!handlers.has(name)) handlers.set(name, new Set());
        handlers.get(name).add(fn);
        return () => { handlers.get(name).delete(fn); };
      },
    },
    log(level, message) { logs.push({ level, message }); },
  };
  return {
    api, calls, handlers, cleanups, logs,
    emit(name, payload) { [...(handlers.get(name) || [])].forEach(fn => fn(payload)); },
    dispose() { [...cleanups].forEach(fn => fn()); },
  };
};

test("plain source stays within each plugin wrapper and needs no module loader", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../compat/decky-source.js"), "utf8");
  const context = vm.createContext({});
  const before = Object.keys(context);
  const factory = vm.runInContext("(function () {" + source + ";return createDeckySourceAdapter;})()", context);
  assert.strictEqual(typeof factory, "function");
  assert.deepStrictEqual(Object.keys(context), before);
  const other = vm.runInContext("(function () {" + source + ";return createDeckySourceAdapter;})()", context);
  assert.notStrictEqual(factory, other);
});

test("constructor validates the native API before registering cleanup", () => {
  [null, {}, { call() {} }, { call() {}, cleanup() {}, events: {} }].forEach(api => {
    assert.throws(() => createDeckySourceAdapter(api), TypeError);
  });
  const e = stub();
  assert.ok(Object.isFrozen(createDeckySourceAdapter(e.api)));
  assert.strictEqual(e.cleanups.size, 1);
});

test("call and callable preserve positional arrays, no-argument calls and raw results", async () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api);
  const sum = decky.callable("sum");
  assert.deepStrictEqual(await sum(2, { n: 3 }, [4]), [2, { n: 3 }, [4]]);
  assert.deepStrictEqual(await decky.call("get"), []);
  assert.deepStrictEqual(e.calls, [
    { method: "sum", args: [2, { n: 3 }, [4]] }, { method: "get", args: [] },
  ]);
  const envelope = { success: true, result: 8 };
  e.api.call = () => envelope;
  assert.strictEqual(await decky.call("get"), envelope);
  e.api.call = () => null;
  assert.strictEqual(await decky.call("get"), null);
  e.api.call = () => 0;
  assert.strictEqual(await decky.call("get"), 0);
});

test("backend errors preserve string codes, and transport failures reject unchanged", async () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api);
  e.api.call = () => ({ error: "failed", code: "backend_failed" });
  await assert.rejects(decky.call("get"), error => error.message === "failed" && error.code === "backend_failed");
  e.api.call = () => ({ error: "failed", code: 12 });
  await assert.rejects(decky.call("get"), error => error.code === undefined);
  const transport = new Error("transport failed");
  e.api.call = () => Promise.reject(transport);
  await assert.rejects(decky.call("get"), error => error === transport);
  e.api.call = () => { throw transport; };
  await assert.rejects(decky.call("get"), error => error === transport);
  e.api.call = () => ({ error: { toString: null } });
  await assert.rejects(decky.call("get"), TypeError);
});

test("invalid methods fail without dispatch and invalid events fail without subscribing", async () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api);
  for (const method of [null, "", 8, {}]) {
    await assert.rejects(decky.call(method), TypeError);
    assert.throws(() => decky.callable(method), TypeError);
  }
  for (const name of ["Bad Name", "", "A", "x".repeat(41), 5]) {
    assert.throws(() => decky.addEventListener(name, () => {}), TypeError);
  }
  assert.throws(() => decky.addEventListener("ok", {}), TypeError);
  assert.strictEqual(e.calls.length, 0);
  assert.strictEqual(e.handlers.size, 0);
});

test("events spread array payloads and treat other payloads as one argument", () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api), got = [];
  decky.addEventListener("state.changed", (...args) => got.push(args));
  e.emit("state.changed", [1, "two"]);
  e.emit("state.changed", []);
  e.emit("state.changed", [[1, 2]]);
  e.emit("state.changed", { n: 3 });
  e.emit("state.changed", null);
  assert.deepStrictEqual(got, [[1, "two"], [], [[1, 2]], [{ n: 3 }], [null]]);
});

test("listeners return their original identity, deduplicate, and remove per event", () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api), got = [];
  const listener = value => got.push(value);
  assert.strictEqual(decky.addEventListener("a", listener), listener);
  assert.strictEqual(decky.addEventListener("a", listener), listener);
  decky.addEventListener("b", listener);
  assert.strictEqual(e.handlers.get("a").size, 1);
  e.emit("a", [1]);
  decky.removeEventListener("a", listener);
  decky.removeEventListener("a", listener);
  e.emit("a", [2]);
  e.emit("b", [3]);
  decky.addEventListener("a", listener);
  e.emit("a", [4]);
  assert.deepStrictEqual(got, [1, 3, 4]);
});

test("listener exceptions and rejected promises are contained and logged", async () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api), got = [];
  decky.addEventListener("changed", () => { throw new Error("sync failed"); });
  decky.addEventListener("changed", () => Promise.reject(new Error("async failed")));
  decky.addEventListener("changed", value => got.push(value));
  e.emit("changed", [2]);
  await flush();
  assert.deepStrictEqual(got, [2]);
  assert.deepStrictEqual(e.logs.map(entry => entry.level), ["error", "error"]);
  assert.match(e.logs[0].message, /sync failed/);
  assert.match(e.logs[1].message, /async failed/);
});

test("definePlugin defers invocation, forwards arguments and preserves returned values", () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api);
  let invoked = 0;
  const result = { name: "Example", onDismount() { invoked += 10; } };
  const factory = decky.definePlugin((...args) => {
    invoked++;
    assert.deepStrictEqual(args, ["arg", 2]);
    return result;
  });
  assert.strictEqual(invoked, 0);
  assert.strictEqual(factory("arg", 2), result);
  assert.strictEqual(invoked, 1);
  assert.throws(() => decky.definePlugin({}), TypeError);
  decky.cleanup();
  assert.strictEqual(invoked, 1);
  assert.throws(() => factory("arg", 2), error => error.code === "adapter_disposed");
});

test("cleanup rejects pending and future calls, ignores late replies and releases registrations", async () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api), slow = deferred();
  e.api.call = () => slow.promise;
  const callable = decky.callable("slow");
  let seen = 0;
  const listener = decky.addEventListener("tick", () => seen++);
  const request = callable();
  const rejected = assert.rejects(request, error => error.code === "adapter_disposed");
  decky.cleanup();
  decky.cleanup();
  await rejected;
  slow.resolve(8);
  await flush();
  e.emit("tick", []);
  assert.strictEqual(seen, 0);
  assert.strictEqual(e.cleanups.size, 0);
  assert.strictEqual(e.handlers.get("tick").size, 0);
  await assert.rejects(callable(), error => error.code === "adapter_disposed");
  await assert.rejects(decky.call("late"), error => error.code === "adapter_disposed");
  assert.throws(() => decky.callable("late"), error => error.code === "adapter_disposed");
  assert.throws(() => decky.addEventListener("tick", listener), error => error.code === "adapter_disposed");
  decky.removeEventListener("tick", listener);
});

test("native cleanup and already disposed API cleanup are both supported", async () => {
  const e = stub(), decky = createDeckySourceAdapter(e.api);
  e.dispose();
  await assert.rejects(decky.call("get"), error => error.code === "adapter_disposed");
  const lateApi = stub();
  lateApi.api.cleanup = fn => { fn(); return () => {}; };
  const late = createDeckySourceAdapter(lateApi.api);
  await assert.rejects(late.call("get"), error => error.code === "adapter_disposed");
});

test("multiple adapters isolate listeners and cleanup without changing API objects", () => {
  const e = stub(), keys = Object.keys(e.api), got = [];
  const first = createDeckySourceAdapter(e.api), second = createDeckySourceAdapter(e.api);
  const original = value => got.push(value);
  e.api.events.on("tick", original);
  first.addEventListener("tick", value => got.push("first" + value));
  second.addEventListener("tick", value => got.push("second" + value));
  first.cleanup();
  e.emit("tick", [1]);
  assert.deepStrictEqual(got, [[1], "second1"]);
  assert.deepStrictEqual(Object.keys(e.api), keys);
  assert.strictEqual(e.cleanups.size, 1);
});

test("Framey bridge scopes positional calls/events and unload rejects adapter calls", async () => {
  const e = makeDom({ loadFiles: ["fy-api.js"] });
  const first = createDeckySourceAdapter(e.plugin("source_a"));
  const second = createDeckySourceAdapter(e.plugin("source_b"));
  const got = [];
  first.addEventListener("changed", (...args) => got.push(args));
  second.addEventListener("changed", () => got.push("other"));
  const result = first.callable("sum")(2, 4);
  const call = e.sent("sum")[0];
  assert.strictEqual(call.plugin, "source_a");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(call.arg)), [2, 4]);
  e.answer(call, 6);
  assert.strictEqual(await result, 6);
  e.event("source_a", "changed", [6, 1], 2);
  assert.deepStrictEqual(got, [[6, 1]]);
  const slow = first.call("slow");
  const rejection = assert.rejects(slow, error => error.code === "adapter_disposed");
  e.window.Framey.dispose("source_a", TOK);
  await rejection;
  e.event("source_a", "changed", [7]);
  e.event("source_b", "changed", [8]);
  assert.deepStrictEqual(got, [[6, 1], "other"]);
  e.window.Framey.dispose(undefined, TOK);
  e.window.close();
});

(async () => {
  let failed = 0;
  for (const [name, run] of tests) {
    try { await run(); console.log("ok   " + name); }
    catch (error) { failed++; console.error("FAIL " + name + "\n" + error.stack); }
  }
  console.log((tests.length - failed) + " passed, " + failed + " failed");
  process.exitCode = failed ? 1 : 0;
})();
