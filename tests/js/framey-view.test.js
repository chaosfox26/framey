"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { JSDOM } = require("jsdom");
const fs = require("node:fs");
const path = require("node:path");
const runtimePath = path.resolve(__dirname, "../../decky_runtime/compat/framey-view.js");
const load = () => {
  assert.ok(fs.existsSync(runtimePath), "Framey must provide its independent DOM runtime");
  return require(runtimePath).createFrameyViewRuntime;
};
function setup(t, options) {
  const window = new JSDOM('<!doctype html><div id="app"></div><div id="portal"></div>').window;
  const runtime = load()(window.document, options);
  t.after(() => { runtime.dispose(); window.close(); });
  return { ...runtime, window, document: window.document, box: window.document.getElementById("app") };
}

test("state, reducer and text input handlers update DOM without replacing a focused input", t => {
  const e = setup(t), R = e.React, h = R.createElement;
  let set;
  function Editor() {
    const [value, update] = R.useState("start");
    const [count, increment] = R.useReducer((n, delta) => n + delta, 2);
    set = update;
    return h("section", null, h("input", { value, onChange: event => update(event.target.value) }),
      h("button", { onClick: () => increment(3) }, String(count)), h("output", null, value));
  }
  const root = e.ReactDOM.createRoot(e.box);
  root.render(h(Editor));
  const input = e.box.querySelector("input"); input.focus();
  input.value = "typed"; input.dispatchEvent(new e.window.Event("input", { bubbles: true }));
  assert.equal(e.box.querySelector("output").textContent, "typed");
  assert.equal(e.box.querySelector("input"), input);
  assert.equal(e.document.activeElement, input);
  e.box.querySelector("button").click();
  assert.equal(e.box.querySelector("button").textContent, "5");
  root.unmount(); set("stale");
  assert.equal(e.box.textContent, "");
});

test("effects clean previous dependencies and clean once on disposal after DOM commit", t => {
  const e = setup(t), R = e.React, h = R.createElement, calls = [];
  function View({ value }) {
    R.useLayoutEffect(() => { calls.push("layout:" + e.box.textContent); return () => calls.push("unlayout:" + value); }, [value]);
    R.useEffect(() => { calls.push("effect:" + value); return () => calls.push("clean:" + value); }, [value]);
    return h("p", null, value);
  }
  const root = e.ReactDOM.createRoot(e.box);
  root.render(h(View, { value: "one" })); root.render(h(View, { value: "one" })); root.render(h(View, { value: "two" }));
  assert.deepEqual(calls, ["layout:one", "effect:one", "unlayout:one", "layout:two", "clean:one", "effect:two"]);
  e.dispose(); e.dispose();
  assert.deepEqual(calls.slice(-2), ["unlayout:two", "clean:two"]);
  assert.equal(e.box.childNodes.length, 0);
  assert.throws(() => root.render(h("p")), /disposed|unmounted/i);
});

test("keyed components retain state and input identity across reorder and removal", t => {
  const e = setup(t), R = e.React, h = R.createElement;
  function Row({ id }) { const [value, set] = R.useState(id); return h("input", { "data-id": id, value, onChange: ev => set(ev.target.value) }); }
  const root = e.ReactDOM.createRoot(e.box), draw = ids => root.render(h("div", null, ids.map(id => h(Row, { key: id, id }))));
  draw(["a", "b", "c"]);
  const b = e.box.querySelector('[data-id="b"]'); b.value = "saved"; b.dispatchEvent(new e.window.Event("input"));
  draw(["c", "b", "a"]);
  assert.equal(e.box.querySelector('[data-id="b"]'), b);
  assert.deepEqual([...e.box.querySelectorAll("input")].map(n => n.value), ["c", "saved", "a"]);
  draw(["b", "d"]); assert.equal(e.box.querySelector("input"), b);
});

test("class lifecycle, context, forwarded imperative refs and SVG attributes work", t => {
  const e = setup(t), R = e.React, h = R.createElement, ctx = R.createContext("default"), ref = R.createRef(), lifecycle = [];
  class Sketch extends R.PureComponent {
    static contextType = ctx;
    constructor(props) { super(props); this.state = { color: "red" }; }
    componentDidMount() { lifecycle.push("mount"); }
    componentWillUnmount() { lifecycle.push("unmount"); }
    render() { return h("svg", { viewBox: "0 0 20 20", onPointerDown: () => this.setState({ color: "blue" }) },
      h("path", { strokeWidth: 2, strokeLinecap: "round", fill: this.state.color, d: this.context })); }
  }
  const Forward = R.forwardRef((props, exposed) => {
    const value = R.useContext(ctx), local = R.useRef(null);
    R.useImperativeHandle(exposed, () => ({ value, node: local.current }), [value]);
    return h("div", { ref: local }, h(Sketch, { ref: props.classRef }));
  });
  const classRef = R.createRef(), root = e.ReactDOM.createRoot(e.box);
  root.render(h(ctx.Provider, { value: "M 0 0" }, h(Forward, { ref, classRef })));
  const svg = e.box.querySelector("svg"), p = e.box.querySelector("path");
  assert.equal(svg.namespaceURI, "http://www.w3.org/2000/svg");
  assert.equal(svg.getAttribute("viewBox"), "0 0 20 20"); assert.equal(p.getAttribute("stroke-width"), "2");
  assert.equal(p.getAttribute("stroke-linecap"), "round"); assert.equal(p.getAttribute("d"), "M 0 0");
  assert.equal(ref.current.value, "M 0 0"); assert.equal(ref.current.node, e.box.firstChild);
  svg.dispatchEvent(new e.window.Event("pointerdown")); assert.equal(p.getAttribute("fill"), "blue");
  assert.ok(classRef.current instanceof Sketch);
  root.unmount(); assert.equal(ref.current, null); assert.equal(classRef.current, null); assert.deepEqual(lifecycle, ["mount", "unmount"]);
});

test("JSX, cloneElement, Children, memo and context consumer compose with portals", t => {
  const e = setup(t), R = e.React, h = R.createElement, C = R.createContext("fallback"), destination = e.document.getElementById("portal");
  const Label = R.memo(({ children }) => e.JSX.jsxs("span", { children: [children, "!"] }));
  const element = h(Label, { key: "label" }, "hello"), cloned = R.cloneElement(element, null, "world");
  assert.ok(R.isValidElement(cloned)); assert.equal(cloned.key, "label");
  assert.equal(R.Children.count([null, element, ["x"]]), 3);
  assert.deepEqual(R.Children.toArray([null, false, "x", element]).length, 2);
  const instance = e.ReactDOM.render(h(R.Fragment, null, cloned, h(C.Consumer, null, v => h("b", null, v)), e.ReactDOM.createPortal(h("i", null, "elsewhere"), destination)), e.box);
  assert.equal(instance, null); assert.equal(e.box.textContent, "world!fallback"); assert.equal(destination.textContent, "elsewhere");
  assert.equal(e.ReactDOM.unmountComponentAtNode(e.box), true); assert.equal(destination.textContent, "");
  assert.equal(e.ReactDOM.unmountComponentAtNode(e.box), false);
  assert.throws(() => R.lazy(() => Promise.resolve({ default: Label })), /unsupported/i);
});

test("memo values, callbacks and ids stay stable while refs and callback refs clean up", t => {
  const e = setup(t), R = e.React, h = R.createElement, refs = []; let set, snapshot, builds = 0;
  function View() {
    const [value, update] = R.useState(0); set = update;
    const id = R.useId(), object = R.useMemo(() => ({ n: ++builds }), []), callback = R.useCallback(() => value, [value]);
    snapshot = { id, object, callback };
    return h("input", { id, ref: node => refs.push(node), style: { marginTop: 2, opacity: 0.5 }, "aria-hidden": false });
  }
  const root = e.ReactDOM.createRoot(e.box); root.render(h(View)); const old = snapshot, input = e.box.firstChild;
  set(1); assert.equal(snapshot.id, old.id); assert.equal(snapshot.object, old.object); assert.notEqual(snapshot.callback, old.callback);
  assert.equal(input.style.marginTop, "2px"); assert.equal(input.getAttribute("aria-hidden"), "false");
  root.unmount(); assert.equal(refs.at(-1), null);
});

test("moving keyed rows preserves focused text selection", t => {
  const e = setup(t), R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box);
  const draw = order => root.render(h("div", null, order.map(key => h("input", { key, defaultValue: key + " text" }))));
  draw(["a", "b"]); const input = e.box.firstChild.lastChild; input.focus(); input.setSelectionRange(1, 4);
  draw(["b", "a"]);
  assert.equal(e.document.activeElement, input); assert.equal(input.selectionStart, 1); assert.equal(input.selectionEnd, 4);
});

test("effect-triggered updates settle after commit and stale setters stay inert after replacement", t => {
  const e = setup(t), R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box); let oldSet;
  function Settled() {
    const [value, set] = R.useState(0); oldSet = set;
    R.useEffect(() => { set(1); }, []);
    return h("p", null, value);
  }
  root.render(h(Settled)); assert.equal(e.box.textContent, "1");
  root.render(h("h1", null, "new")); oldSet(2); assert.equal(e.box.textContent, "new");
});

test("cleanup continues across throwing effects and removes listeners retained by outside code", t => {
  const e = setup(t), R = e.React, h = R.createElement, clean = []; let clicks = 0;
  function Child({ id }) {
    R.useEffect(() => () => { clean.push(id); if (id === "first") throw new Error("cleanup failed"); }, []);
    return h("button", { onClick: () => clicks++ }, id);
  }
  e.ReactDOM.createRoot(e.box).render(h("div", null, h(Child, { id: "first" }), h(Child, { id: "second" })));
  const button = e.box.querySelector("button"); button.click();
  assert.throws(() => e.dispose(), /cleanup failed/);
  assert.deepEqual(clean, ["first", "second"]); assert.equal(e.box.childNodes.length, 0);
  button.click(); assert.equal(clicks, 1);
});

test("failed initial rendering releases class resources and any partial portal mount", t => {
  const e = setup(t), R = e.React, h = R.createElement, clean = [];
  class Resource extends R.Component {
    componentWillUnmount() { clean.push("released"); }
    render() { return h("b", null, "resource"); }
  }
  function Broken() { throw new Error("render failed"); }
  const target = e.document.getElementById("portal");
  assert.throws(() => e.ReactDOM.createRoot(e.box).render(h(R.Fragment, null,
    e.ReactDOM.createPortal(h(Resource), target), h(Broken))), /render failed/);
  assert.equal(target.childNodes.length, 0); assert.deepEqual(clean, ["released"]);
  e.ReactDOM.createRoot(e.box).render(h("p", null, "recovered")); assert.equal(e.box.textContent, "recovered");
});

test("changing a reused input type remaps a stable onChange handler to the native event", t => {
  const e = setup(t), h = e.React.createElement, root = e.ReactDOM.createRoot(e.box); const seen = [];
  const changed = event => seen.push(event.target.type + ":" + event.type);
  root.render(h("input", { type: "text", onChange: changed })); const input = e.box.firstChild;
  input.dispatchEvent(new e.window.Event("input"));
  root.render(h("input", { type: "checkbox", onChange: changed }));
  input.dispatchEvent(new e.window.Event("change")); input.dispatchEvent(new e.window.Event("input"));
  assert.deepEqual(seen, ["text:input", "checkbox:change"]);
});

test("empty Children values count zero while empty slots in arrays remain traversable", t => {
  const e = setup(t), C = e.React.Children, seen = [];
  assert.equal(C.count(null), 0); assert.equal(C.count(undefined), 0);
  C.forEach(null, value => seen.push(value)); assert.deepEqual(seen, []);
  C.forEach([null, false, "text"], value => seen.push(value));
  assert.deepEqual(seen, [null, null, "text"]);
});

test("separate plugin runtimes allocate distinct ids in the shared document", t => {
  const e = setup(t), other = load()(e.document), box = e.document.createElement("div"); e.document.body.append(box);
  t.after(() => other.dispose());
  function One() { return e.React.createElement("input", { id: e.React.useId() }); }
  function Two() { return other.React.createElement("input", { id: other.React.useId() }); }
  e.ReactDOM.createRoot(e.box).render(e.React.createElement(One));
  other.ReactDOM.createRoot(box).render(other.React.createElement(Two));
  assert.notEqual(e.box.firstChild.id, box.firstChild.id);
});

test("external store snapshots update real DOM and unsubscribe on unmount", t => {
  const e = setup(t), R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box);
  let value = "initial", subscriptions = 0, unsubscriptions = 0, stale, renders = 0;
  const listeners = new Set();
  const subscribe = callback => {
    subscriptions++; listeners.add(callback); stale = callback;
    return () => { unsubscriptions++; listeners.delete(callback); };
  };
  const getSnapshot = () => value;
  function View() { renders++; return h("output", null, R.useSyncExternalStore(subscribe, getSnapshot)); }
  root.render(h(View)); const output = e.box.firstChild;
  assert.equal(output.textContent, "initial"); assert.equal(subscriptions, 1);
  value = "updated"; for (const notify of listeners) notify();
  assert.equal(e.box.firstChild, output); assert.equal(output.textContent, "updated");
  const before = renders; for (const notify of listeners) notify(); assert.equal(renders, before);
  root.unmount(); assert.equal(unsubscriptions, 1); assert.equal(listeners.size, 0);
  value = "stale"; stale(); assert.equal(e.box.childNodes.length, 0); assert.equal(renders, before);
});

test("external store callbacks use a changed snapshot getter and replace subscriptions", t => {
  const e = setup(t), R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box);
  const store = { a: "one", b: "two" }, events = []; let firstNotify, secondNotify;
  const first = callback => { events.push("first subscribe"); firstNotify = callback; return () => events.push("first unsubscribe"); };
  const second = callback => { events.push("second subscribe"); secondNotify = callback; return () => events.push("second unsubscribe"); };
  const a = () => store.a, b = () => store.b;
  function View({ subscribe, getter }) { return h("output", null, R.useSyncExternalStore(subscribe, getter)); }
  root.render(h(View, { subscribe: first, getter: a })); assert.equal(e.box.textContent, "one");
  root.render(h(View, { subscribe: first, getter: b })); assert.equal(e.box.textContent, "two");
  assert.deepEqual(events, ["first subscribe"]);
  store.b = "three"; firstNotify(); assert.equal(e.box.textContent, "three");
  root.render(h(View, { subscribe: second, getter: b }));
  assert.deepEqual(events, ["first subscribe", "first unsubscribe", "second subscribe"]);
  store.b = "four"; firstNotify(); assert.equal(e.box.textContent, "three");
  secondNotify(); assert.equal(e.box.textContent, "four");
  e.dispose(); assert.equal(events.at(-1), "second unsubscribe");
});

test("external stores recheck snapshots after subscribing to catch changes during commit", t => {
  const e = setup(t), R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box); let value = 1;
  const subscribe = () => { value = 2; return () => {}; };
  function View() { return h("output", null, R.useSyncExternalStore(subscribe, () => value)); }
  root.render(h(View)); assert.equal(e.box.textContent, "2");
});

test("conditional empty slots retain the following unkeyed component state on insertion and removal", t => {
  const e = setup(t), R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box);
  function Row({ label }) {
    const [value, set] = R.useState(label);
    return h("input", { "data-label": label, value, onChange: event => set(event.target.value) });
  }
  const draw = show => root.render(h("div", null,
    show ? h(Row, { label: "Optional" }) : null,
    false,
    h(Row, { label: "Always" })));
  draw(false); const always = e.box.querySelector("input"); always.value = "user value";
  always.dispatchEvent(new e.window.Event("input"));
  draw(true);
  assert.equal(e.box.querySelector('[data-label="Always"]'), always);
  assert.equal(always.value, "user value");
  const optional = e.box.querySelector('[data-label="Optional"]'); assert.notEqual(optional, always);
  assert.equal(optional.value, "Optional"); optional.value = "temporary";
  optional.dispatchEvent(new e.window.Event("input"));
  draw(false); assert.equal(e.box.querySelector("input"), always); assert.equal(always.value, "user value");
  draw(true); assert.equal(e.box.querySelector('[data-label="Optional"]').value, "Optional");
  assert.equal(e.box.querySelector('[data-label="Always"]'), always);
  assert.equal(always.value, "user value");
});

test("fatal event-driven rerenders report the original error once after root cleanup", t => {
  const errors = [], cleanups = [], expected = new Error("rerender failed"); let set, e;
  e = setup(t, { onError(error) { errors.push(error); assert.equal(e.box.childNodes.length, 0); assert.deepEqual(cleanups, ["clean"]); e.dispose(); } });
  const R = e.React, h = R.createElement, root = e.ReactDOM.createRoot(e.box);
  function View() {
    const [fail, update] = R.useState(false); set = update;
    R.useEffect(() => () => cleanups.push("clean"), []);
    if (fail) throw expected;
    return h("button", { onClick: () => update(true) }, "break");
  }
  root.render(h(View)); const button = e.box.firstChild;
  button.click();
  assert.deepEqual(errors, [expected]); assert.deepEqual(cleanups, ["clean"]);
  assert.equal(e.box.childNodes.length, 0);
  set(false); button.click(); root.unmount(); e.dispose();
  assert.deepEqual(errors, [expected]); assert.deepEqual(cleanups, ["clean"]);
});

test("initial render failures still throw directly when onError is configured", t => {
  const errors = [], e = setup(t, { onError: error => errors.push(error) }), R = e.React;
  const expected = new Error("initial render failed");
  function Broken() { throw expected; }
  assert.throws(() => e.ReactDOM.createRoot(e.box).render(R.createElement(Broken)), error => error === expected);
  assert.deepEqual(errors, []); assert.equal(e.box.childNodes.length, 0);
});

test("fatal rerenders without an error callback still throw the original error", t => {
  const e = setup(t), R = e.React, expected = new Error("unhandled rerender failed"); let set;
  function View() { const [fail, update] = R.useState(false); set = update; if (fail) throw expected; return R.createElement("p", null, "mounted"); }
  e.ReactDOM.createRoot(e.box).render(R.createElement(View));
  assert.throws(() => set(true), error => error === expected); assert.equal(e.box.childNodes.length, 0);
  assert.doesNotThrow(() => set(false));
});
