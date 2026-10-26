const assert = require("assert");
const { makeDom, registerAs, TOK } = require("./harness");

const boot = (require = false) => {
  const spy = { timers: 0, listeners: 0 };
  const env = makeDom({
    before(w) {
      if (require) w.FY_RUNTIME = { trusted_input: "require" };
      ["setTimeout", "setInterval", "requestAnimationFrame"].forEach(k => {
        const f = w[k];
        w[k] = (...a) => { spy.timers++; return f.apply(w, a); };
      });
    },
  });
  const w = env.window, d = env.document, FY = w.Framey;
  [d, w].forEach(t => {
    const f = t.addEventListener.bind(t);
    t.addEventListener = (...a) => { spy.listeners++; return f(...a); };
  });
  env.load("fy-ui.js");
  const api = env.plugin("t");
  const mount = e => d.body.append(e) || e;
  const key = (e, k) => {
    const ev = new w.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true });
    e.dispatchEvent(ev);
    return ev;
  };
  return { w, d, FY, api, spy, load: env.load, mount, key };
};

let pass = 0, fail = 0;
const t = (name, fn) => {
  try { fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
};
const rowsOf = l => [...l.querySelectorAll('[role="option"]')];

t("style: one injected sheet, ring and 48px targets, idempotent load", () => {
  const { d, load, FY } = boot();
  load("fy-ui.js");
  const s = d.querySelectorAll("#fy-ui-style");
  assert.equal(s.length, 1);
  assert.equal(s[0].dataset.fy, "core");
  assert.ok(s[0].textContent.includes(":focus-visible"));
  assert.ok(s[0].textContent.includes("min-height:48px") && s[0].textContent.includes("min-width:48px"));
  assert.equal(FY.ext.length, 1);
});

t("button: role, label as text only, activation by click Enter Space", () => {
  const { api, mount, key } = boot();
  let n = 0;
  const b = mount(api.ui.button({ label: "<b>Go</b>", onclick: () => n++ }));
  assert.equal(b.getAttribute("role"), "button");
  assert.equal(b.getAttribute("tabindex"), "0");
  assert.equal(b.getAttribute("aria-label"), "<b>Go</b>");
  assert.equal(b.textContent, "<b>Go</b>");
  assert.equal(b.children.length, 0);
  assert.equal(b.dataset.fy, "t");
  assert.equal(b.getAttribute("aria-disabled"), "false");
  b.click();
  assert.equal(n, 1);
  assert.ok(key(b, "Enter").defaultPrevented);
  assert.ok(key(b, " ").defaultPrevented);
  assert.equal(n, 3);
  key(b, "a");
  key(b, "ArrowDown");
  assert.equal(n, 3);
});

t("button: primary, css option, no handler is safe", () => {
  const { api, mount } = boot();
  const p = mount(api.ui.button({ label: "x", primary: true, css: "margin-top:5px" }));
  assert.ok(p.className.includes("fy-p"));
  assert.ok(p.style.cssText.includes("margin-top: 5px"));
  p.click();
  assert.ok(!mount(api.ui.button({ label: "y" })).className.includes("fy-p"));
});

t("button: disabled state ignores input, leaves tab order, can be re-enabled", () => {
  const { api, mount, key } = boot();
  let n = 0;
  const b = mount(api.ui.button({ label: "x", disabled: true, onclick: () => n++ }));
  assert.equal(b.getAttribute("aria-disabled"), "true");
  assert.equal(b.getAttribute("tabindex"), "-1");
  assert.equal(b.disabled, true);
  b.click();
  key(b, "Enter");
  key(b, " ");
  assert.equal(n, 0);
  b.disabled = false;
  assert.equal(b.getAttribute("aria-disabled"), "false");
  assert.equal(b.getAttribute("tabindex"), "0");
  b.click();
  assert.equal(n, 1);
});

t("toggle: switch role, aria-checked, value property, activation", () => {
  const { api, mount, key } = boot();
  const seen = [];
  const s = mount(api.ui.toggle({ label: "Dark", onchange: v => seen.push(v) }));
  assert.equal(s.getAttribute("role"), "switch");
  assert.equal(s.getAttribute("aria-label"), "Dark");
  assert.equal(s.getAttribute("aria-checked"), "false");
  assert.equal(s.value, false);
  assert.equal(s.getAttribute("tabindex"), "0");
  s.click();
  assert.equal(s.value, true);
  assert.equal(s.getAttribute("aria-checked"), "true");
  key(s, "Enter");
  key(s, " ");
  assert.deepEqual(seen, [true, false, true]);
  s.value = false;
  assert.equal(s.getAttribute("aria-checked"), "false");
  assert.deepEqual(seen, [true, false, true]);
  assert.equal(mount(api.ui.toggle({ label: "x", value: 1 })).getAttribute("aria-checked"), "true");
});

t("toggle: disabled ignores input and is skipped", () => {
  const { api, mount, key } = boot();
  let n = 0;
  const s = mount(api.ui.toggle({ label: "x", disabled: true, onchange: () => n++ }));
  assert.equal(s.getAttribute("aria-disabled"), "true");
  assert.equal(s.getAttribute("tabindex"), "-1");
  s.click();
  key(s, "Enter");
  assert.equal(n, 0);
  assert.equal(s.value, false);
});

t("slider: aria range, format, arrows step and clamp, callbacks", () => {
  const { api, mount, key, w } = boot();
  const inp = [], chg = [];
  const s = mount(api.ui.slider({ label: "Vol", min: 0, max: 10, step: 2, value: 4, oninput: v => inp.push(v), onchange: v => chg.push(v), format: v => v + "%" }));
  assert.equal(s.getAttribute("role"), "slider");
  assert.equal(s.getAttribute("aria-label"), "Vol");
  assert.equal(s.getAttribute("aria-valuemin"), "0");
  assert.equal(s.getAttribute("aria-valuemax"), "10");
  assert.equal(s.getAttribute("aria-valuenow"), "4");
  assert.equal(s.getAttribute("aria-valuetext"), "4%");
  assert.equal(s.querySelector(".fy-v").textContent, "4%");
  assert.ok(key(s, "ArrowRight").defaultPrevented);
  assert.equal(s.value, 6);
  assert.equal(s.getAttribute("aria-valuenow"), "6");
  assert.equal(s.querySelector(".fy-v").textContent, "6%");
  key(s, "ArrowLeft");
  key(s, "ArrowLeft");
  key(s, "ArrowLeft");
  key(s, "ArrowLeft");
  assert.equal(s.value, 0);
  assert.deepEqual(inp, [6, 4, 2, 0]);
  assert.deepEqual(chg, []);
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "ArrowLeft", bubbles: true }));
  assert.deepEqual(chg, [0]);
  for (let i = 0; i < 9; i++) key(s, "ArrowRight");
  assert.equal(s.value, 10);
  assert.equal(inp.length, 4 + 5);
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  assert.deepEqual(chg, [0, 10]);
});

t("slider: defaults, float steps stay exact, setter clamps silently", () => {
  const { api, mount, key } = boot();
  const d = mount(api.ui.slider({ label: "d", value: 7 }));
  assert.equal(d.getAttribute("aria-valuetext"), "7");
  assert.equal(d.getAttribute("aria-valuemax"), "100");
  let calls = 0;
  const f = mount(api.ui.slider({ label: "f", min: 0, max: 1, step: 0.1, value: 0.2, oninput: () => calls++ }));
  key(f, "ArrowRight");
  assert.equal(f.value, 0.3);
  key(f, "ArrowLeft");
  key(f, "ArrowLeft");
  assert.equal(f.value, 0.1);
  f.value = 99;
  assert.equal(f.value, 1);
  f.value = -4;
  assert.equal(f.value, 0);
  assert.equal(calls, 3);
});

t("slider: disabled ignores keys and pointer", () => {
  const { w, api, mount, key } = boot();
  const s = mount(api.ui.slider({ label: "x", value: 5, disabled: true }));
  assert.equal(s.getAttribute("tabindex"), "-1");
  key(s, "ArrowRight");
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  rw.dispatchEvent(new w.MouseEvent("pointerdown", { clientX: 90, bubbles: true }));
  assert.equal(s.value, 5);
  s.disabled = false;
  key(s, "ArrowRight");
  assert.equal(s.value, 6);
});

t("slider: pointer drag sets value, onchange once on release", () => {
  const { w, api, mount } = boot();
  const inp = [], chg = [];
  const s = mount(api.ui.slider({ label: "x", min: 0, max: 10, step: 1, value: 0, oninput: v => inp.push(v), onchange: v => chg.push(v) }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 200 });
  rw.setPointerCapture = () => {};
  const fire = (type, x) => rw.dispatchEvent(new w.MouseEvent(type, { clientX: x, bubbles: true }));
  fire("pointermove", 100);
  assert.equal(s.value, 0);
  fire("pointerdown", 100);
  assert.equal(s.value, 5);
  fire("pointermove", 150);
  assert.equal(s.value, 8);
  assert.deepEqual(chg, []);
  fire("pointerup", 150);
  assert.deepEqual(chg, [8]);
  fire("pointermove", 20);
  assert.equal(s.value, 8);
  assert.deepEqual(inp, [5, 8]);
  fire("pointerdown", 150);
  fire("pointercancel", 150);
  assert.deepEqual(chg, [8]);
});

t("list: roles, roving tabindex, aria, sub text, arrows skip disabled", () => {
  const { api, mount, key, d } = boot();
  const got = [];
  const l = mount(api.ui.list({ items: [{ id: "a", label: "A", sub: "first" }, { id: "b", label: "B", disabled: true }, { id: "c", label: "C" }], selected: "c", onselect: v => got.push(v) }));
  const r = rowsOf(l);
  assert.equal(l.getAttribute("role"), "listbox");
  assert.equal(r.length, 3);
  assert.deepEqual(r.map(x => x.getAttribute("tabindex")), ["-1", "-1", "0"]);
  assert.deepEqual(r.map(x => x.getAttribute("aria-selected")), ["false", "false", "true"]);
  assert.deepEqual(r.map(x => x.getAttribute("aria-disabled")), ["false", "true", "false"]);
  assert.equal(r[0].getAttribute("aria-label"), "A");
  assert.equal(r[0].querySelector(".fy-sub").textContent, "first");
  assert.equal(l.selected, "c");
  r[0].focus();
  assert.deepEqual(r.map(x => x.getAttribute("tabindex")), ["0", "-1", "-1"]);
  assert.ok(key(r[0], "ArrowDown").defaultPrevented);
  assert.equal(d.activeElement, r[2]);
  key(r[2], "ArrowUp");
  assert.equal(d.activeElement, r[0]);
  assert.equal(key(r[0], "ArrowUp").defaultPrevented, false);
  assert.equal(d.activeElement, r[0]);
  key(r[0], "Enter");
  assert.deepEqual(got, ["a"]);
  assert.deepEqual(r.map(x => x.getAttribute("aria-selected")), ["true", "false", "false"]);
  r[1].click();
  key(r[1], "Enter");
  assert.deepEqual(got, ["a"]);
  r[2].click();
  assert.deepEqual(got, ["a", "c"]);
  key(r[2], " ");
  assert.deepEqual(got, ["a", "c", "c"]);
});

t("list: multi select toggles and reports an array", () => {
  const { api, mount } = boot();
  const got = [];
  const l = mount(api.ui.list({ multi: true, items: [{ id: 1, label: "one" }, { id: 2, label: "two" }], selected: [2], onselect: v => got.push(v) }));
  const r = rowsOf(l);
  assert.equal(l.getAttribute("aria-multiselectable"), "true");
  assert.deepEqual(l.selected, [2]);
  r[0].click();
  r[1].click();
  assert.deepEqual(got, [[2, 1], [1]]);
  assert.deepEqual(r.map(x => x.getAttribute("aria-selected")), ["true", "false"]);
});

t("list: setItems replaces rows, prunes selection, empty text, caps, text-only labels", () => {
  const { api, mount } = boot();
  const l = mount(api.ui.list({ items: [{ id: "a", label: "A" }], selected: "a", empty: "Nothing" }));
  l.setItems([{ id: "x", label: "<img src=x onerror=alert(1)>", sub: "<i>s</i>" }, { id: "y", label: "Y" }]);
  assert.equal(l.selected, null);
  assert.equal(rowsOf(l).length, 2);
  assert.equal(l.querySelectorAll("img,i").length, 0);
  assert.equal(rowsOf(l)[0].getAttribute("tabindex"), "0");
  l.setItems([]);
  assert.equal(rowsOf(l).length, 0);
  assert.equal(l.querySelector(".fy-em").textContent, "Nothing");
  l.setItems(Array.from({ length: 600 }, (_, i) => ({ id: i, label: "n" + i })));
  assert.equal(rowsOf(l).length, 500);
  assert.equal(l.querySelector(".fy-em"), null);
  l.setItems(null);
  assert.equal(rowsOf(l).length, 0);
  const none = mount(api.ui.list({ items: [{ id: 1, label: "only", disabled: true }] }));
  assert.equal(rowsOf(none)[0].getAttribute("tabindex"), "-1");
});

t("group: roving tabindex, arrows skip disabled, resync on disable", () => {
  const { api, mount, key, d } = boot();
  const b = ["1", "2", "3"].map((x, i) => api.ui.button({ label: x, disabled: i === 1 }));
  const g = mount(api.ui.group(...b));
  assert.equal(g.getAttribute("role"), "group");
  assert.deepEqual(b.map(x => x.getAttribute("tabindex")), ["0", "-1", "-1"]);
  b[0].focus();
  assert.ok(key(b[0], "ArrowRight").defaultPrevented);
  assert.equal(d.activeElement, b[2]);
  assert.deepEqual(b.map(x => x.getAttribute("tabindex")), ["-1", "-1", "0"]);
  assert.equal(key(b[2], "ArrowRight").defaultPrevented, false);
  key(b[2], "ArrowLeft");
  assert.equal(d.activeElement, b[0]);
  key(b[0], "ArrowDown");
  assert.equal(d.activeElement, b[2]);
  key(b[2], "ArrowUp");
  assert.equal(d.activeElement, b[0]);
  b[1].disabled = false;
  assert.deepEqual(b.map(x => x.getAttribute("tabindex")), ["0", "-1", "-1"]);
  b[0].disabled = true;
  assert.deepEqual(b.map(x => x.getAttribute("tabindex")), ["-1", "0", "-1"]);
  b[1].disabled = true;
  b[2].disabled = true;
  assert.equal(g.querySelectorAll('[tabindex="0"]').length, 0);
  assert.equal(d.querySelectorAll("[data-fy=t]").length, 4);
});

t("group: slider keeps left/right, up/down leave; list passes through at its ends", () => {
  const { api, mount, key, d } = boot();
  const first = api.ui.button({ label: "first" }), last = api.ui.button({ label: "last" });
  const s = api.ui.slider({ label: "s", value: 5 });
  const l = api.ui.list({ items: [{ id: "a", label: "A" }, { id: "b", label: "B" }] });
  const g = mount(api.ui.group(first, s, l, last));
  assert.equal(g.querySelectorAll('[tabindex="0"]').length, 2);
  assert.equal(first.getAttribute("tabindex"), "0");
  assert.equal(s.getAttribute("tabindex"), "-1");
  s.focus();
  key(s, "ArrowRight");
  assert.equal(s.value, 6);
  assert.equal(d.activeElement, s);
  key(s, "ArrowDown");
  const r = rowsOf(l);
  assert.equal(d.activeElement, r[0]);
  key(r[0], "ArrowRight");
  assert.equal(d.activeElement, r[0]);
  key(r[0], "ArrowDown");
  assert.equal(d.activeElement, r[1]);
  key(r[1], "ArrowDown");
  assert.equal(d.activeElement, last);
  key(last, "ArrowUp");
  assert.equal(d.activeElement, r[1]);
  key(r[1], "ArrowUp");
  key(r[0], "ArrowUp");
  assert.equal(d.activeElement, s);
  key(s, "ArrowUp");
  assert.equal(d.activeElement, first);
});

t("focus helper: group, list, plain control, disabled and missing", () => {
  const { api, mount, d } = boot();
  const a = api.ui.button({ label: "a", disabled: true }), b = api.ui.button({ label: "b" });
  const g = mount(api.ui.group(a, b));
  api.ui.focus(g);
  assert.equal(d.activeElement, b);
  d.activeElement.blur();
  api.ui.focus(a);
  assert.notEqual(d.activeElement, a);
  const l = mount(api.ui.list({ items: [{ id: 1, label: "x" }, { id: 2, label: "y" }], selected: 2 }));
  api.ui.focus(l);
  assert.equal(d.activeElement, rowsOf(l)[1]);
  api.ui.focus(null);
  api.ui.focus(mount(api.ui.list({ items: [] })));
  assert.equal(d.activeElement, rowsOf(l)[1]);
});

t("no timers, no document or window listeners, no raf", () => {
  const { w, api, mount, key, spy } = boot();
  const b = mount(api.ui.button({ label: "b", onclick() {} }));
  const s = mount(api.ui.slider({ label: "s" }));
  const l = mount(api.ui.list({ items: [{ id: 1, label: "x" }, { id: 2, label: "y" }] }));
  const g = mount(api.ui.group(api.ui.toggle({ label: "t" }), api.ui.button({ label: "c" })));
  b.click();
  key(s, "ArrowRight");
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  rw.dispatchEvent(new w.MouseEvent("pointerdown", { clientX: 10, bubbles: true }));
  rw.dispatchEvent(new w.MouseEvent("pointerup", { bubbles: true }));
  rowsOf(l)[0].focus();
  key(rowsOf(l)[0], "ArrowDown");
  key(g.children[0], "ArrowRight");
  assert.equal(spy.timers, 0);
  assert.equal(spy.listeners, 0);
});

t("disposal: owner mechanism removes all controls, keeps style and other plugins", () => {
  const { FY, api, mount, d } = boot();
  const other = registerAs(FY, { id: "u" });
  [api.ui.button({ label: "a" }), api.ui.toggle({ label: "b" }), api.ui.slider({ label: "c" }), api.ui.list({ items: [{ id: 1, label: "d" }] }), api.ui.group(api.ui.button({ label: "e" }))].forEach(mount);
  mount(other.ui.button({ label: "keep" }));
  const mine = d.querySelectorAll('[data-fy="t"]').length;
  assert.ok(mine > 5);
  assert.ok(d.querySelectorAll("*").length > mine);
  FY.dispose("t", TOK);
  assert.equal(d.querySelectorAll('[data-fy="t"]').length, 0);
  assert.equal(d.querySelectorAll('[data-fy="u"]').length > 0, true);
  assert.equal(d.querySelectorAll("#fy-ui-style").length, 1);
  FY.dispose("t", TOK);
  FY.dispose(undefined, TOK);
  assert.equal(d.querySelectorAll('[data-fy="u"]').length, 0);
});

t("render path: controls built inside a tab render carry the plugin owner and vanish on dispose", () => {
  const { w, FY, d } = boot();
  let made;
  registerAs(FY, { id: "r", name: "R", short: "R", render(box, api) { made = box.appendChild(api.ui.button({ label: "in tab" })); } });
  w.sessionStorage.fyTab = "r";
  FY.toggle();
  assert.ok(made && made.isConnected);
  assert.equal(made.dataset.fy, "r");
  assert.equal(made.textContent, "in tab");
  FY.dispose("r", TOK);
  assert.equal(made.isConnected, false);
  assert.equal(d.querySelectorAll('[data-fy="r"]').length, 0);
});

t("each registration gets its own ui bound to its own id; re-register replaces", () => {
  const { FY, mount } = boot();
  const a = registerAs(FY, { id: "a" }), b = registerAs(FY, { id: "b" });
  assert.notEqual(a.ui, b.ui);
  assert.equal(mount(a.ui.button({ label: "x" })).dataset.fy, "a");
  assert.equal(mount(b.ui.button({ label: "x" })).dataset.fy, "b");
  assert.deepEqual(Object.keys(a.ui).sort(), ["button", "focus", "group", "list", "slider", "toggle"]);
});

t("trusted_input off: plugin control handlers run on a synthetic click and on Enter", () => {
  const { api, mount, key } = boot();
  let n = 0;
  const b = mount(api.ui.button({ label: "Go", onclick: () => n++ }));
  b.click();
  key(b, "Enter");
  assert.equal(n, 2);
});

t("trusted_input require: synthetic clicks and keys on plugin controls do nothing, core nav still works", () => {
  const { w, api, mount, key } = boot(true);
  let n = 0;
  const go = mount(api.ui.button({ label: "Go", onclick: () => n++ }));
  const back = mount(api.ui.button({ label: "Back", nav: true, onclick: () => n++ }));
  const sw = mount(api.ui.toggle({ label: "On", onchange: () => n++ }));
  const sl = mount(api.ui.slider({ label: "Vol", value: 5, onchange: () => n++ }));
  go.click();
  key(go, "Enter");
  back.click();
  key(sw, " ");
  sw.click();
  key(sl, "ArrowRight");
  assert.equal(n, 0);
  assert.equal(sw.getAttribute("aria-checked"), "false");
  assert.equal(sl.value, 5);
  const K = w.__S.K;
  const coreBack = mount(K.ui.button({ label: "Back", nav: true, onclick: () => n++ }));
  const coreGo = mount(K.ui.button({ label: "Go", onclick: () => n++ }));
  coreBack.click();
  coreGo.click();
  assert.equal(n, 1);
});

t("slider: scientific steps remain selectable and Home End page keys commit once", () => {
  const { api, mount, key, w } = boot();
  const fine = mount(api.ui.slider({ label: "Fine", min: 0, max: 0.000001, step: 1e-7, value: 2e-7 }));
  key(fine, "ArrowRight");
  assert.equal(fine.value, 3e-7);
  const changes = [];
  const s = mount(api.ui.slider({ label: "Level", min: 2, max: 32, step: 2, value: 4, onchange: v => changes.push(v) }));
  key(s, "PageUp");
  assert.equal(s.value, 24);
  key(s, "End");
  assert.equal(s.value, 32);
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "End", bubbles: true }));
  assert.deepEqual(changes, [32]);
  key(s, "PageDown");
  assert.equal(s.value, 12);
  key(s, "Home");
  assert.equal(s.value, 2);
  s.blur();
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "Home", bubbles: true }));
  assert.deepEqual(changes, [32, 2]);
});

const pointer = (w, e, type, id, x, button = 0) => {
  const ev = new w.MouseEvent(type, { clientX: x, button, bubbles: true, cancelable: true });
  Object.defineProperty(ev, "pointerId", { value: id });
  e.dispatchEvent(ev);
  return ev;
};

t("slider: capture failures are safe and only the active primary pointer can drag", () => {
  const { api, mount, w, d } = boot();
  const changes = [];
  const s = mount(api.ui.slider({ label: "Level", min: 0, max: 10, onchange: v => changes.push(v) }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  rw.setPointerCapture = () => { throw new Error("capture unavailable"); };
  pointer(w, rw, "pointerdown", 7, 40, 1);
  assert.equal(s.value, 0);
  pointer(w, rw, "pointerdown", 7, 40);
  assert.equal(s.value, 4);
  assert.equal(d.activeElement, s);
  pointer(w, rw, "pointerdown", 8, 90);
  pointer(w, rw, "pointermove", 8, 90);
  pointer(w, rw, "pointerup", 8, 90);
  assert.equal(s.value, 4);
  assert.deepEqual(changes, []);
  pointer(w, rw, "pointermove", 7, 60);
  pointer(w, rw, "pointerup", 7, 60);
  assert.equal(s.value, 6);
  assert.deepEqual(changes, [6]);
});

t("slider: disabled or programmatic replacement cancels pending user commits", () => {
  const { api, mount, w, key } = boot();
  const changes = [];
  const s = mount(api.ui.slider({ label: "Level", min: 0, max: 10, onchange: v => changes.push(v) }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  pointer(w, rw, "pointerdown", 1, 40);
  s.disabled = true;
  pointer(w, rw, "pointermove", 1, 90);
  pointer(w, rw, "pointerup", 1, 90);
  assert.equal(s.value, 4);
  assert.deepEqual(changes, []);
  s.disabled = false;
  pointer(w, rw, "pointerdown", 2, 60);
  s.value = 3;
  pointer(w, rw, "pointermove", 2, 90);
  pointer(w, rw, "pointerup", 2, 90);
  assert.equal(s.value, 3);
  key(s, "ArrowRight");
  s.value = 8;
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  assert.deepEqual(changes, []);
});

t("slider: losing capture ends and commits a drag only once", () => {
  const { api, mount, w } = boot();
  const changes = [], released = [];
  const s = mount(api.ui.slider({ label: "Level", min: 0, max: 10, onchange: v => changes.push(v) }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  rw.releasePointerCapture = id => { released.push(id); pointer(w, rw, "lostpointercapture", id, 0); };
  pointer(w, rw, "pointerdown", 1, 40);
  pointer(w, rw, "lostpointercapture", 1, 40);
  pointer(w, rw, "pointerup", 1, 40);
  pointer(w, rw, "pointermove", 1, 90);
  assert.equal(s.value, 4);
  assert.deepEqual(changes, [4]);
  assert.deepEqual(released, [1]);
});

t("list: Home and End focus eligible endpoints without selecting", () => {
  const { api, mount, key, d } = boot();
  const values = [];
  const l = mount(api.ui.list({ items: [{ id: 0, label: "disabled", disabled: true }, { id: 1, label: "one" }, { id: 2, label: "two" }, { id: 3, label: "disabled", disabled: true }], onselect: v => values.push(v) }));
  const rows = rowsOf(l);
  rows[1].focus();
  assert.ok(key(rows[1], "End").defaultPrevented);
  assert.equal(d.activeElement, rows[2]);
  assert.ok(key(rows[2], "Home").defaultPrevented);
  assert.equal(d.activeElement, rows[1]);
  assert.deepEqual(values, []);
});

t("list: refreshing options retains keyboard focus by id without stealing external focus", () => {
  const { api, mount, d } = boot();
  const l = mount(api.ui.list({ items: [{ id: "a", label: "A" }, { id: "b", label: "B" }], selected: "a" }));
  rowsOf(l)[1].focus();
  l.setItems([{ id: "b", label: "B refreshed" }, { id: "a", label: "A" }]);
  assert.equal(d.activeElement, rowsOf(l)[0]);
  assert.equal(l.selected, "a");
  l.setItems([{ id: "a", label: "A" }, { id: "c", label: "C" }]);
  assert.equal(d.activeElement, rowsOf(l)[0]);
  const outside = mount(api.ui.button({ label: "Outside" }));
  outside.focus();
  l.setItems([{ id: "c", label: "C" }]);
  assert.equal(d.activeElement, outside);
});

t("list: replaced rows cannot select stale options or call handlers", () => {
  const { api, mount, key } = boot();
  const values = [];
  const l = mount(api.ui.list({ items: [{ id: "a", label: "Old A" }], onselect: v => values.push(v) }));
  const old = rowsOf(l)[0];
  l.setItems([{ id: "a", label: "New A" }, { id: "b", label: "B" }]);
  old.click();
  key(old, "Enter");
  assert.deepEqual(values, []);
  assert.equal(l.selected, null);
  rowsOf(l)[1].click();
  assert.deepEqual(values, ["b"]);
});

t("list: duplicate or invalid ids cannot create ambiguous selected rows", () => {
  const { api, mount } = boot();
  const l = mount(api.ui.list({ items: [{ id: "a", label: "First" }, { id: "a", label: "Duplicate" }, { id: {}, label: "Invalid" }, { id: NaN, label: "Invalid" }, { id: 1, label: "One" }, { label: "Default id" }], selected: "a" }));
  assert.equal(rowsOf(l).length, 3);
  assert.deepEqual(rowsOf(l).map(r => r.getAttribute("aria-selected")), ["true", "false", "false"]);
  assert.deepEqual(rowsOf(l).map(r => r.textContent), ["First", "One", "Default id"]);
});

t("disposed controls: retained nodes cannot activate, change selection or move focus", () => {
  const { api, mount, key, FY, w, d } = boot();
  let calls = 0;
  const b = mount(api.ui.button({ label: "Action", onclick: () => calls++ }));
  const toggle = mount(api.ui.toggle({ label: "Toggle", onchange: () => calls++ }));
  const slider = mount(api.ui.slider({ label: "Level", value: 4, onchange: () => calls++ }));
  const list = mount(api.ui.list({ items: [{ id: "a", label: "A" }, { id: "b", label: "B" }], onselect: () => calls++ }));
  const rows = rowsOf(list), rw = slider.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  FY.dispose("t", TOK);
  b.click(); key(b, "Enter"); toggle.click(); key(slider, "ArrowRight");
  pointer(w, rw, "pointerdown", 1, 90); pointer(w, rw, "pointerup", 1, 90);
  rows[1].click(); key(rows[0], "ArrowDown");
  api.ui.focus(b);
  assert.equal(calls, 0);
  assert.equal(toggle.value, false);
  assert.equal(slider.value, 4);
  assert.equal(list.selected, null);
  assert.notEqual(d.activeElement, b);
});

t("detached controls: replacing a live view makes retained controls inert", () => {
  const { api, mount, key, w, d } = boot();
  let calls = 0;
  const box = mount(d.createElement("div"));
  const button = api.ui.button({ label: "Inspect compatibility", onclick: () => calls++ });
  const slider = api.ui.slider({ label: "Level", value: 4, oninput: () => calls++ });
  const toggle = api.ui.toggle({ label: "Option", onchange: () => calls++ });
  const list = api.ui.list({ items: [{ id: "first", label: "First" }], onselect: () => calls++ });
  box.append(button, slider, toggle, list);
  const option = rowsOf(list)[0], rail = slider.querySelector(".fy-rw");
  rail.getBoundingClientRect = () => ({ left: 0, width: 100 });
  box.replaceChildren(api.ui.button({ label: "Current route" }));
  button.click(); key(button, "Enter"); toggle.click(); option.click(); key(slider, "ArrowRight");
  pointer(w, rail, "pointerdown", 1, 90); pointer(w, rail, "pointerup", 1, 90);
  assert.equal(calls, 0);
  assert.equal(toggle.value, false);
  assert.equal(slider.value, 4);
  assert.equal(list.selected, null);
});

t("detached controls: value and options can still be prepared before mounting", () => {
  const { api, mount } = boot();
  let changes = 0;
  const slider = api.ui.slider({ label: "Level", value: 4, oninput: () => changes++ });
  const toggle = api.ui.toggle({ label: "Option", onchange: () => changes++ });
  const list = api.ui.list({ items: [{ id: "old", label: "Old" }], onselect: () => changes++ });
  slider.value = 7;
  toggle.value = true;
  list.setItems([{ id: "new", label: "New" }]);
  assert.equal(slider.value, 7);
  assert.equal(toggle.value, true);
  assert.equal(rowsOf(list)[0].textContent, "New");
  assert.equal(changes, 0);
  mount(list); rowsOf(list)[0].click();
  assert.equal(list.selected, "new");
  assert.equal(changes, 1);
});

t("group: native input and selection controls retain their editing keys", () => {
  const { api, mount, key, d } = boot();
  const input = d.createElement("input");
  input.tabIndex = 0; input.value = "Search modules";
  const select = d.createElement("select");
  select.tabIndex = 0;
  const option = d.createElement("option"); option.textContent = "Stable"; select.append(option);
  const editor = d.createElement("div"); editor.setAttribute("contenteditable", "true"); editor.tabIndex = 0;
  const button = api.ui.button({ label: "Search" });
  mount(api.ui.group(input, select, editor, button));
  for (const control of [input, select, editor]) {
    control.focus();
    for (const arrow of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
      assert.equal(key(control, arrow).defaultPrevented, false);
      assert.equal(d.activeElement, control);
    }
  }
});

const diagnosticCount = (w, code) => {
  const row = w.__S.K.diagnostics().find(row => row.code === code);
  return row ? row.count : 0;
};

t("initialization: style insertion failure leaves the guard clear and retry succeeds", () => {
  const env = makeDom(), append = env.document.head.append;
  env.document.head.append = () => { throw new Error("fixture initialization failure"); };
  assert.throws(() => env.load("fy-ui.js"), /fixture initialization failure/);
  assert.equal(env.window.Framey.uiReady, undefined);
  assert.equal(env.window.Framey.ext.length, 0);
  env.document.head.append = append;
  env.load("fy-ui.js");
  assert.equal(env.window.Framey.uiReady, true);
  assert.equal(env.window.Framey.ext.length, 1);
  assert.equal(env.document.querySelectorAll("#fy-ui-style").length, 1);
  assert.equal(typeof env.plugin("ready").ui.button, "function");
});

t("initialization: failed extension registration removes the partial style before retry", () => {
  const env = makeDom(), extensions = env.window.Framey.ext, push = extensions.push;
  extensions.push = () => { throw new Error("fixture extension registration failure"); };
  assert.throws(() => env.load("fy-ui.js"), /fixture extension registration failure/);
  assert.equal(env.window.Framey.uiReady, undefined);
  assert.equal(env.document.querySelectorAll("#fy-ui-style").length, 0);
  extensions.push = push;
  env.load("fy-ui.js");
  assert.equal(env.window.Framey.uiReady, true);
  assert.equal(extensions.length, 1);
  assert.equal(env.document.querySelectorAll("#fy-ui-style").length, 1);
});

t("pointer capture: inactive pointers and already-released capture stay quiet", () => {
  const { api, mount, w, d } = boot();
  const s = mount(api.ui.slider({ label: "Level", min: 0, max: 10 }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  rw.setPointerCapture = () => { throw new w.DOMException("fixture inactive pointer", "NotFoundError"); };
  let releases = 0;
  rw.hasPointerCapture = () => false;
  rw.releasePointerCapture = () => { releases++; throw new Error("unexpected release"); };
  pointer(w, rw, "pointerdown", 1, 40);
  pointer(w, rw, "pointerup", 1, 40);
  assert.equal(s.value, 4);
  assert.equal(releases, 0);
  assert.equal(diagnosticCount(w, "ui.pointer_capture_failed"), 0);
  assert.equal(diagnosticCount(w, "ui.pointer_release_failed"), 0);
  assert.equal(d.querySelector('[role="status"]'), null);
});

t("pointer failures: unexpected failures count only fixed codes and preserve commit", () => {
  const { api, mount, w, d } = boot(), values = [];
  const s = mount(api.ui.slider({ label: "Level", min: 0, max: 10, onchange: value => values.push(value) }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  rw.setPointerCapture = () => { throw new Error("fixture sensitive capture detail"); };
  rw.hasPointerCapture = () => true;
  rw.releasePointerCapture = () => { throw new Error("fixture sensitive release detail"); };
  pointer(w, rw, "pointerdown", 1, 60);
  pointer(w, rw, "pointerup", 1, 60);
  assert.deepEqual(values, [6]);
  assert.equal(diagnosticCount(w, "ui.pointer_capture_failed"), 1);
  assert.equal(diagnosticCount(w, "ui.pointer_release_failed"), 1);
  const snapshot = JSON.stringify(w.__S.K.diagnostics());
  assert.ok(!snapshot.includes("fixture") && !snapshot.includes("sensitive") && !snapshot.includes("Level"));
  assert.equal(d.querySelector('[role="status"]'), null);
});

t("pointer release: removing a captured control is not reported as a failure", () => {
  const { api, mount, w } = boot();
  const s = mount(api.ui.slider({ label: "Level", min: 0, max: 10 }));
  const rw = s.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  let releases = 0;
  rw.releasePointerCapture = () => { releases++; throw new Error("fixture detached pointer"); };
  pointer(w, rw, "pointerdown", 1, 40);
  s.remove();
  pointer(w, rw, "lostpointercapture", 1, 40);
  assert.equal(releases, 0);
  assert.equal(diagnosticCount(w, "ui.pointer_release_failed"), 0);
});

t("callback isolation: throwing input preserves commit ordering and later actions", () => {
  const { api, mount, key, w, d } = boot(), events = [];
  const s = mount(api.ui.slider({ label: "Level", value: 4, oninput(value) { assert.equal(this.oninput instanceof Function, true); events.push("input " + value); throw new Error("fixture sensitive callback detail"); }, onchange: value => events.push("change " + value) }));
  key(s, "ArrowRight");
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  key(s, "ArrowLeft");
  s.dispatchEvent(new w.KeyboardEvent("keyup", { key: "ArrowLeft", bubbles: true }));
  assert.deepEqual(events, ["input 5", "change 5", "input 4", "change 4"]);
  assert.equal(diagnosticCount(w, "ui.callback_failed"), 2);
  assert.equal(d.querySelector('[role="status"]'), null);
  assert.ok(!JSON.stringify(w.__S.K.diagnostics()).includes("fixture"));
});

t("callback isolation: toggle and list failures preserve value and stay out of popups", () => {
  const { api, mount, w, d } = boot();
  const toggle = mount(api.ui.toggle({ label: "Option", onchange() { throw new Error("fixture toggle failure"); } }));
  const list = mount(api.ui.list({ items: [{ id: "selected", label: "Choice" }], onselect() { throw new Error("fixture list failure"); } }));
  toggle.click(); rowsOf(list)[0].click();
  assert.equal(toggle.value, true);
  assert.equal(list.selected, "selected");
  assert.equal(diagnosticCount(w, "ui.callback_failed"), 2);
  assert.equal(d.querySelector('[role="status"]'), null);
});

t("callback reentrancy: programmatic input replacement cancels the user commit", () => {
  const { api, mount, key, w } = boot(), changes = [];
  let slider;
  slider = mount(api.ui.slider({ label: "Level", value: 4, oninput() { slider.value = 2; }, onchange: value => changes.push(value) }));
  key(slider, "ArrowRight");
  slider.dispatchEvent(new w.KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  assert.equal(slider.value, 2);
  assert.deepEqual(changes, []);
});

t("callback reentrancy: commit may disable a slider before a new drag begins", () => {
  const { api, mount, key, w } = boot(), inputs = [];
  let slider;
  slider = mount(api.ui.slider({ label: "Level", value: 4, oninput: value => inputs.push(value), onchange() { slider.disabled = true; } }));
  const rw = slider.querySelector(".fy-rw");
  rw.getBoundingClientRect = () => ({ left: 0, width: 100 });
  key(slider, "ArrowRight");
  pointer(w, rw, "pointerdown", 1, 90);
  assert.equal(slider.disabled, true);
  assert.equal(slider.value, 5);
  assert.deepEqual(inputs, [5]);
});

t("formatter failure: a numeric fallback keeps the slider usable and records no raw detail", () => {
  const { api, mount, w, key } = boot();
  const slider = mount(api.ui.slider({ label: "Level", value: 4, format() { throw new Error("fixture sensitive format detail"); } }));
  assert.equal(slider.getAttribute("aria-valuetext"), "4");
  key(slider, "ArrowRight");
  assert.equal(slider.getAttribute("aria-valuetext"), "5");
  assert.equal(diagnosticCount(w, "ui.callback_failed"), 2);
  assert.ok(!JSON.stringify(w.__S.K.diagnostics()).includes("fixture"));
});

const asyncTest = async (name, fn) => {
  try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
};
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await asyncTest("button: pending action blocks duplicates and restores interactivity", async () => {
    const { api, mount, key } = boot();
    let resolve, calls = 0;
    const pending = new Promise(done => { resolve = done; });
    const b = mount(api.ui.button({ label: "Install", onclick: () => { calls++; return pending; } }));
    b.click(); b.click(); key(b, "Enter");
    assert.equal(calls, 1);
    assert.equal(b.disabled, true);
    assert.equal(b.getAttribute("aria-busy"), "true");
    assert.equal(b.tabIndex, -1);
    resolve(); await flush();
    assert.equal(b.disabled, false);
    assert.equal(b.getAttribute("aria-busy"), "false");
    assert.equal(b.tabIndex, 0);
    b.click(); await flush();
    assert.equal(calls, 2);
  });
  await asyncTest("button: manual disabled state survives completion and cannot bypass pending action", async () => {
    const { api, mount } = boot();
    let resolve, calls = 0;
    const pending = new Promise(done => { resolve = done; });
    const b = mount(api.ui.button({ label: "Apply", onclick: () => { calls++; return pending; } }));
    b.click(); b.disabled = false; b.click();
    assert.equal(calls, 1);
    b.disabled = true;
    resolve(); await flush();
    assert.equal(b.disabled, true);
    assert.equal(b.getAttribute("aria-busy"), "false");
    b.click(); assert.equal(calls, 1);
  });
  await asyncTest("button: rejection is reported once and a retry remains possible", async () => {
    const { api, mount, d } = boot();
    const b = mount(api.ui.button({ label: "Retry", onclick: () => Promise.reject(new Error("Could not apply")) }));
    b.click(); await flush();
    const errors = d.querySelector('[role="status"]');
    assert.equal(errors.childElementCount, 1);
    assert.equal(errors.firstElementChild.textContent, "Action failed. Try again or report the problem to Framey.");
    assert.equal(b.disabled, false);
    b.click(); await flush();
    assert.equal(errors.childElementCount, 2);
  });
  await asyncTest("button: disposed pending action settles without showing a stale failure", async () => {
    const { api, mount, FY, d } = boot();
    let reject;
    const pending = new Promise((resolve, fail) => { reject = fail; });
    const b = mount(api.ui.button({ label: "Apply", onclick: () => pending }));
    b.click(); FY.dispose("t", TOK); reject(new Error("Old action")); await flush();
    assert.equal(b.getAttribute("aria-busy"), "false");
    assert.equal(d.querySelector('[role="status"]'), null);
  });
  await asyncTest("button: intentional action cancellation clears busy without a failure toast", async () => {
    const { api, mount, d, w } = boot();
    let reject;
    const pending = new Promise((resolve, fail) => { reject = fail; });
    const button = mount(api.ui.button({ label: "Inspect compatibility", onclick: () => pending }));
    button.click();
    assert.equal(button.disabled, true);
    reject(new w.DOMException("Inspection cancelled", "AbortError"));
    await flush();
    assert.equal(button.disabled, false);
    assert.equal(button.getAttribute("aria-busy"), "false");
    assert.equal(d.querySelector('[role="status"]'), null);
  });
  await asyncTest("callback diagnostics: rejected control promises are isolated without popups", async () => {
    const { api, mount, d, w } = boot();
    const toggle = mount(api.ui.toggle({ label: "Option", onchange() { return Promise.reject(new Error("fixture sensitive asynchronous detail")); } }));
    toggle.click(); await flush();
    assert.equal(toggle.value, true);
    assert.equal(diagnosticCount(w, "ui.callback_failed"), 1);
    assert.equal(d.querySelector('[role="status"]'), null);
    assert.ok(!JSON.stringify(w.__S.K.diagnostics()).includes("fixture"));
  });
  await asyncTest("button diagnostics: hostile thrown getters cannot escape or disclose raw details", async () => {
    const { api, mount, d, w } = boot();
    const failure = Object.create(null, { name: { get() { throw new Error("fixture sensitive getter detail"); } }, message: { get() { throw new Error("message must not be read"); } } });
    const button = mount(api.ui.button({ label: "Apply", onclick() { throw failure; } }));
    button.click(); await flush();
    assert.equal(button.disabled, false);
    assert.equal(diagnosticCount(w, "ui.callback_failed"), 1);
    const visible = d.querySelector('[role="status"]').textContent;
    assert.equal(visible, "Action failed. Try again or report the problem to Framey.");
    const snapshot = JSON.stringify(w.__S.K.diagnostics());
    assert.ok(!snapshot.includes("fixture") && !snapshot.includes("Apply") && !snapshot.includes("sensitive"));
  });
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
