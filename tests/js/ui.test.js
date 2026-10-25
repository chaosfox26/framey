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

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
