"use strict";
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..", "..");
const TOK = "core-token-for-tests";
const LAZY = { picker: "fy-picker.js", dev: "fy-dev.js" };
const nativeLike = f => (typeof f === "function" ? f.bind(null) : f);
const winOf = new WeakMap();

const jsdom = () => {
  const base = process.env.FRAMEY_JSDOM;
  try {
    return require(base ? require.resolve("jsdom", { paths: [base] }) : "jsdom");
  } catch (e) {
    throw new Error("jsdom not found: set FRAMEY_JSDOM (or NODE_PATH) to a folder whose node_modules holds jsdom 30.1.2 (npm i jsdom@30.1.2 --prefix <folder>)");
  }
};

const makeDom = (options = {}) => {
  const { JSDOM } = jsdom();
  const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", {
    url: "https://steamloopback.host/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  const { document } = window;
  const calls = [];
  const timers = new Map();
  let tid = 0;
  let now = 0;
  const clock = {
    tick(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!due) break;
        timers.delete(due[0]);
        now = Math.max(now, due[1].at);
        due[1].fn();
      }
      now = end;
    },
    pending: () => timers.size,
  };
  if (options.fakeTimers !== false) {
    window.setTimeout = (fn, ms) => {
      timers.set(++tid, { fn, at: now + (ms | 0) });
      return tid;
    };
    window.clearTimeout = id => { timers.delete(id); };
  }
  window.fyCall = s => { calls.push(JSON.parse(s)); };
  if (options.before) options.before(window);
  let handler = window.fyCall;
  window.fyCall = nativeLike(s => handler(s));
  const text = file => fs.readFileSync(path.isAbsolute(file) ? file : path.join(ROOT, file), "utf8");
  const wrap = src => "(function(__fyt,__FY,__trusted){" + src + "\n})(" + JSON.stringify(TOK) + ",window.__S.FY,window.__S.trusted)";
  const ev = window.eval;
  const load = file => {
    if (file === "inject.js") { if (!window.Framey) (0, ev)("(function(__fyt){" + text(file) + "\n;window.__S={FY:__FY,trusted:__trusted,K:__FY&&__FY._k(__fyt)};window.__bindFramey=(...a)=>window.__S.K.bind(...a);})(" + JSON.stringify(TOK) + ")"); }
    else (0, ev)(wrap(text(file)));
  };
  const env = {
    window,
    document,
    calls,
    clock,
    load,
    answer(call, value) {
      window.Framey._reply(call.gen, call.id, value);
    },
    event(plugin, name, payload, dropped) {
      window.__S.K.event(plugin, name, payload, dropped);
    },
    plugin(id, meta = {}) {
      const h = window.__bindFramey(id, "tok-" + id + "-0000", Object.assign({ name: id, short: id, version: "1.0.0" }, meta));
      return h.register({ render() {} });
    },
    wrap,
    onCall(f) { handler = f; },
    loadPlugin(id, file, meta) {
      (0, ev)("(function(Framey){" + text(file) + "\n})(window.__bindFramey(" + JSON.stringify(id) + "," + JSON.stringify("tok-" + id + "-0000") + "," + JSON.stringify(Object.assign({ id }, meta)) + "))");
    },
    serve(name) {
      const c = calls.filter(x => x.method === "asset" && x.arg && x.arg.name === name).pop();
      load(LAZY[name]);
      if (c) window.Framey._reply(c.gen, c.id, { ok: true });
    },
    record(id) {
      return window.Framey.plugins.find(p => p.id === id);
    },
    sent(method) {
      return calls.filter(c => c.method === method);
    },
  };
  load("inject.js");
  winOf.set(window.Framey, window);
  (options.loadFiles || []).forEach(load);
  return env;
};

const registerAs = (FY, o) => winOf.get(FY).__bindFramey(o.id, "tok-" + o.id + "-0000", { name: o.name, short: o.short }).register(o);

const readLoaderConstants = names => {
  const program = `
import ast, json, sys
from pathlib import Path
tree = ast.parse(Path(sys.argv[1]).read_text())
assignments = {
    target.id: statement.value
    for statement in tree.body if isinstance(statement, ast.Assign)
    for target in statement.targets if isinstance(target, ast.Name)
}
def value(node):
    if isinstance(node, ast.Name):
        return value(assignments[node.id])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return value(node.left) + value(node.right)
    return ast.literal_eval(node)
print(json.dumps({name: value(assignments[name]) for name in sys.argv[2:]}))
`;
  const result = spawnSync(process.env.FRAMEY_PYTHON || "python3", ["-I", "-c", program, path.join(ROOT, "framey.py"), ...names], {
    encoding: "utf8",
    timeout: 10000,
    maxBuffer: 1024 * 1024,
  });
  if (result.error || result.status !== 0) throw result.error || new Error(result.stderr || "Could not read loader constants");
  return JSON.parse(result.stdout);
};

module.exports = { makeDom, ROOT, TOK, nativeLike, registerAs, readLoaderConstants };
