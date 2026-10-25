const __trust = { mode: ["off", "require"].includes((window.FY_RUNTIME || {}).trusted_input) ? window.FY_RUNTIME.trusted_input : "auto", seen: false, n: 0 };
const __trusted = ev => {
  if (__trust.mode === "off" || (__trust.mode === "auto" && !__trust.seen) || (ev && ev.isTrusted === true)) return true;
  if (__trust.n < 100000) __trust.n++;
  return false;
};
const __FY = (() => {
  if (window.Framey) { console.warn("Framey: window.Framey already defined; not injecting"); return null; }
  const fyCall = window.fyCall;
  const stringify = JSON.stringify;
  const defProp = Object.defineProperty;
  const freeze = Object.freeze;
  const O = Object;
  const P = Promise;
  const later = setTimeout;
  const unlater = clearTimeout;
  const every = setInterval;
  const unevery = clearInterval;
  const fnText = Function.prototype.toString;
  if (typeof fyCall !== "function" || !/\[native code\]/.test(fnText.call(fyCall))) {
    const m = document.createElement("div");
    m.dataset.fy = "";
    m.setAttribute("role", "alert");
    m.style.cssText = "position:fixed;top:11px;right:11px;z-index:99999;background:#101018;color:#fff;border-radius:14px;padding:14px 24px;font:20px sans-serif";
    m.textContent = "Framey could not start safely";
    document.body.append(m);
    return null;
  }
  try { defProp(window, "fyCall", { value: fyCall, writable: false, configurable: true }); } catch (e) {}
  const mark = ev => { if (ev && ev.isTrusted === true) __trust.seen = true; };
  ["click", "keydown", "pointerdown"].forEach(t => window.addEventListener(t, mark, true));
  const TOK = typeof __fyt === "string" ? __fyt : "";
  const ID = /^[a-z0-9_-]{1,32}$/;
  const RESERVED = ["core", "modules", "settings", "store", "discover", "developer", "dev"];
  const calls = {}, LONG = new Set(["check", "update", "finish", "retry", "rollback", "loader_update", "loader_done", "loader_roll", "install", "add", "enable", "disable", "uninstall", "reload", "reload_one", "ver_list", "ver_install", "repair", "notes", "batch_plan", "batch_apply", "loader_check", "companion_fetch", "maint_refresh", "settings_repair", "fetch"]);
  const limit = (plugin, method, to) => (plugin !== "_core" ? (to || 50000) + 10000 : LONG.has(method) ? 600000 : method === "resource" ? 55000 : 15000);
  const gen =Math.random().toString(36).slice(2);
  const bound = new Map();
  const mods = [], gone = new WeakSet();
  const APIS = new WeakMap();
  const inst = {};
  let n = 0;
  let tc;
  let owner = "";
  let cur = "";
  let st = {};
  let returnTo = null;
  let extLocked = false;
  const safe = f => { try { return f(); } catch (e) {} };
  const ss = (k, v) => safe(() => (v === undefined ? sessionStorage[k] : (sessionStorage[k] = v)));
  const stamp = pl => gen + ":" + pl + "." + (inst[pl] | 0);
  const el = (css, text) => {
    const e = document.createElement("div");
    e.dataset.fy = owner;
    e.style.cssText = css;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const owned = id => [...document.querySelectorAll("[data-fy]")].filter(e => e.dataset.fy === id);
  const lock = (o, ...ks) => ks.forEach(k => defProp(o, k, { value: o[k], writable: false, configurable: false }));
  const wire = (o, t) => (t === undefined ? stringify(o) : '{"t":"' + t + '",' + stringify(o).slice(1));
  const post = (method, arg, t) => safe(() => fyCall(wire({ id: 0, plugin: "_core", method, arg }, t)));
  const send = (plugin, method, arg, t, who, to) =>
    new P(r => {
      const id = ++n;
      const g = stamp(who);
      const c = { r, g, p: who, t: 0 }; calls[id] = c;
      c.t = later(() => { if (calls[id] === c) { delete calls[id]; r({ error: "Framey " + (plugin === "_core" ? "core" : "module") + " did not reply in " + limit(plugin, method, to) / 1000 + " s" }); } }, limit(plugin, method, to));
      try { fyCall(wire({ gen: g, id, plugin, method, arg }, t)); } catch (e) {
        unlater(c.t);
        delete calls[id];
        r({ error: String((e && e.message) || e) });
      }
    });
  const io = (id, tok) => ({
    core: (method, arg) => send("_core", method, arg, tok, id),
    be: (method, arg, to) => send(id, method, arg, tok, id, to),
    post: (method, arg) => post(method, arg, tok),
  });
  const adopt = v => {
    if (!v || !Array.isArray(v.order) || !Array.isArray(v.hidden)) return;
    const was = stringify(st);
    st = { order: v.order, hidden: v.hidden };
    if (isOpen() && stringify(st) !== was) paint();
  };
  const reg = (i, p) => {
    if (i.used) throw new Error("this module is already registered");
    if (!p || typeof p !== "object") throw new Error("register needs a module object");
    if (p.id !== undefined && p.id !== i.id) throw new Error("module id does not match " + i.id);
    i.used = true;
    bound.set(i.id, i.tok);
    const id = i.id;
    p.id = id;
    p.meta = i.meta;
    p.name = String(p.name || i.meta.name || id);
    p.short = String(p.short || i.meta.short || p.name.split(" ")[0]);
    const res = [];
    const tm = new Set();
    const iv = new Set();
    const ls = new Set();
    const keep = (m, h) => {
      if (m.size >= 256) m.delete(m.values().next().value);
      m.add(h);
    };
    const api = {
      id,
      timeout(fn, ms) {
        if (p.dead) return 0;
        const h = later(() => {
          tm.delete(h);
          if (!p.dead) fn();
        }, ms);
        keep(tm, h);
        return h;
      },
      interval(fn, ms) {
        if (p.dead) return 0;
        const h = every(() => {
          if (p.dead) unevery(h);
          else fn();
        }, ms);
        keep(iv, h);
        return h;
      },
      clear(h) {
        const t = tm.delete(h);
        const i = iv.delete(h);
        if (t || !i) unlater(h);
        if (i || !t) unevery(h);
      },
      on(t, type, fn, o) {
        if (p.dead) return () => {};
        if (ls.size >= 256) throw new RangeError("too many listeners");
        t.addEventListener(type, fn, o);
        const off = () => {
          if (ls.delete(off)) t.removeEventListener(type, fn, o);
        };
        ls.add(off);
        return off;
      },
      _clear() {
        const a = res.splice(0);
        while (a.length) safe(a.pop());
      },
    };
    APIS.set(p, api);
    defProp(api, "_n", { value: () => ({ timers: tm.size, intervals: iv.size, listeners: ls.size }) });
    res.push(() => {
      tm.forEach(unlater);
      iv.forEach(unevery);
      tm.clear();
      iv.clear();
      [...ls].forEach(f => f());
    });
    const x = io(id, i.tok);
    defProp(api, "_rpc", {
      value: (method, arg) =>
        (p.dead ? P.reject(new Error("module disposed")) : x.core(method, arg)).then(r => {
          if (r && r.error) {
            const e = new Error(String(r.error));
            e.code = r.code;
            throw e;
          }
          return r;
        }),
    });
    FY.ext.forEach(f => { try { f(api, p, res, x); } catch (e) {} });
    i.events = api.events;
    defProp(p, "dead", { get: () => gone.has(p) || undefined, enumerable: true }); freeze(p);
    const k = mods.findIndex(q => q.id === id);
    if (k < 0) mods.push(p);
    else {
      disposeMods(id);
      mods[k] = p;
    }
    return api;
  };
  const proxy = i => {
    const o = O.create(null);
    const put = (k, v) => defProp(o, k, { value: v, enumerable: true });
    put("register", p => reg(i, p));
    put("call", (pid, method, arg, to) => send(i.id, method, arg, i.tok, i.id, to));
    put("dispose", () => disposeMods(i.id));
    put("listen", (name, fn) => (i.events ? i.events.on(name, fn) : () => {}));
    put("_meta", i.meta);
    ["el", "button", "toast", "fail", "notify", "onPanel", "lazy", "open", "toggle", "restore", "redraw"].forEach(k => put(k, FY[k]));
    defProp(o, "panelOpen", { get: () => FY.panelOpen, enumerable: true });
    return freeze(o);
  };
  const bindPlugin = (id, tok, meta) => {
    if (typeof id !== "string" || !ID.test(id)) throw new Error("invalid module id");
    if (RESERVED.includes(id)) throw new Error("module id " + id + " is reserved");
    if (typeof tok !== "string" || tok.length < 8 || tok.length > 256) throw new Error("invalid module token");
    if (bound.has(id) ? bound.get(id) !== tok : bound.size >= 64) throw new Error("module id " + id + " is already in use");
    if (!extLocked) {
      freeze(FY.ext);
      extLocked = true;
    }
    const given = meta && typeof meta === "object" ? meta : {};
    return proxy({ id, tok, meta: freeze(O.assign({}, given, { id })), used: false });
  };
  const apiOf = t => APIS.get(t);
  const disposeMods = only => {
    if (!only) ["click", "keydown", "pointerdown"].forEach(t => window.removeEventListener(t, mark, true));
    mods.forEach(p => {
      if (gone.has(p) || (only && p.id !== only)) return;
      gone.add(p);
      try { if (p.dispose) p.dispose(); } catch (e) { safe(() => FY.toast("Module " + p.id + " cleanup failed: " + (e && e.message), 4000)); }
      safe(() => APIS.get(p)._clear());
      safe(() => owned(p.id).forEach(e => e.remove()));
      safe(() => O.keys(calls).forEach(k => { if (calls[k].p === p.id) { unlater(calls[k].t); delete calls[k]; } }));
      inst[p.id] = (inst[p.id] | 0) + 1;
    });
  };
  const FY = {
    ext: [],
    dispose(only, tok) {
      if (TOK && tok === TOK) disposeMods(only);
    },
    redraw(only) {
      if (!isOpen()) return;
      const t = find(cur);
      if (!t) show(shown()[0]);
      else if (only ? only === cur : apiOf(t)) show(t);
      else paint();
    },
    call: (plugin, method, arg) => (plugin === "_core" ? P.resolve({ error: "not authorized" }) : send(plugin, method, arg, undefined, plugin)),
    _reply(g, id, v) {
      const c = calls[id];
      if (!c || g !== c.g || g !== stamp(c.p)) return;
      unlater(c.t);
      delete calls[id];
      if (c.p === "_core") adopt(v);
      c.r(v);
    },
    toast(text, ms) {
      if (!tc || !tc.isConnected) {
        tc = el("position:fixed;left:50%;bottom:40px;transform:translateX(-50%);z-index:99999;display:flex;flex-direction:column;align-items:center;gap:8px;pointer-events:none");
        tc.setAttribute("role", "status");
        document.body.append(tc);
      }
      while (tc.childElementCount >= 4) tc.firstElementChild.remove();
      const t = el("background:#101018;color:#fff;border-radius:14px;padding:14px 24px;font:22px sans-serif", String(text).slice(0, 200));
      tc.append(t);
      later(() => t.remove(), Math.min(ms || 2500, 60000));
    },
    fail(id, e) { FY.toast("Module " + id + " failed: " + (e && e.message), 4000); },
  };
  const K = {
    call: (method, arg) => send("_core", method, arg, TOK, "_core"),
    post: (method, arg) => post(method, arg, TOK),
    pending: () => O.keys(calls).length,
    lock,
    tab: () => cur,
    untrusted: () => __trust.n,
    apiOf: p => APIS.get(p),
    dispose: only => disposeMods(only),
    bind: bindPlugin,
    reply: FY._reply,
  };
  FY._k = t => (TOK && t === TOK ? K : null);
  const panel = el("position:fixed;top:11px;right:11px;z-index:99999;box-sizing:border-box;width:520px;max-height:585px;background:#101018;color:#fff;border-radius:16px;padding:14px;display:none;gap:12px;font:20px sans-serif");
  panel.setAttribute("role", "region");
  panel.setAttribute("aria-label", "Framey");
  const content = el("flex:1;min-width:0;overflow:auto;max-height:557px;padding:6px");
  const rail = el("box-sizing:border-box;width:104px;display:flex;flex-direction:column;gap:8px;overflow-y:auto;max-height:557px;padding:6px");
  rail.setAttribute("role", "tablist");
  rail.setAttribute("aria-orientation", "vertical");
  panel.append(content, rail);
  const press = (t, fn) => {
    t.addEventListener("click", ev => fn(ev));
    t.addEventListener("keydown", ev => {
      if (ev.target !== t || (ev.key !== "Enter" && ev.key !== " ")) return;
      ev.preventDefault();
      if (!ev.repeat) fn(ev);
    });
  };
  const tile = (css, text, fn, role) => {
    const t = el("display:flex;align-items:center;border-radius:14px;background:#2a2a3a;" + css, text);
    t.tabIndex = 0;
    t.setAttribute("role", role);
    press(t, fn);
    return t;
  };
  FY.el = el;
  FY.button = (text, css, fn) => tile("justify-content:center;font-weight:700;" + css, text, ev => __trusted(ev) && fn(ev), "button");
  const style = document.createElement("style");
  style.dataset.fy = "core";
  style.textContent =
    ".fy-r{-webkit-appearance:none;height:14px;border-radius:7px;background:#2a2a3a;flex:1;margin:0 10px}" +
    ".fy-r::-webkit-slider-thumb{-webkit-appearance:none;width:40px;height:40px;border-radius:20px;background:#5585ff}";
  document.head.append(style);
  const page = (id, name, k) => ({
    id,
    name,
    short: name,
    render(box) {
      const m = FY.pages && FY.pages[k];
      if (m) m.render(box);
      else box.textContent = name + " is not available.";
    },
  });
  const store = {
    id: "#discover",
    name: "Store",
    short: "Store",
    render(box) {
      box.append(
        el("font-size:28px;font-weight:700;margin-bottom:12px", "Store"),
        el("font-size:22px;font-weight:700;color:#5585ff;margin-bottom:12px", "Coming soon"),
        el("font-size:19px;color:#8a8aa0", "Module discovery and search will arrive in a later update. Until then, install and manage modules from the Modules page."),
      );
    },
  };
  const dev = {
    id: "#developer",
    name: "Developer",
    short: "Dev",
    render(box) { FY.dev.render(box); },
  };
  const key = t => t.id || t.short;
  const label = t => String(t.short || t.name || t.id || "").trim() || "?";
  const isOpen = () => panel.style.display !== "none";
  const all = () => {
    const o = st.order || [];
    const rank = p => { const i = o.indexOf(p.id); return i < 0 ? o.length : i; };
    const pg = FY.panelPages ? FY.panelPages() : [];
    return [...FY.plugins.filter(p => !p.dead).sort((a, b) => rank(a) - rank(b)).flatMap(p => [p, ...pg.filter(g => g.plugin === p.id)]), page("#modules", "Modules", "modules"), store, page("#settings", "Settings", "settings"), ...(FY.dev ? [dev] : [])];
  };
  const find = id => all().find(t => key(t) === id);
  const shown = () => all().filter(t => !(st.hidden || []).includes(t.plugin || t.id));
  const tabs = () => [...rail.children];
  const stop = t => tabs().forEach(x => (x.tabIndex = x === t ? 0 : -1));
  const focusTab = () => {
    const t = tabs().find(x => x.getAttribute("aria-selected") === "true") || tabs()[0];
    if (t) t.focus();
  };
  const focusFirst = () => {
    const c = content.querySelector('[tabindex="0"],input,textarea,select,button');
    if (c) c.focus();
  };
  const paint = () => {
    const had = rail.contains(document.activeElement);
    const o = owner;
    owner = "";
    rail.replaceChildren();
    shown().forEach(t => {
      const on = key(t) === cur;
      const txt = label(t);
      const b = tile("height:72px;flex:none;box-sizing:border-box;overflow:hidden;overflow-wrap:anywhere;justify-content:center;text-align:center;font-weight:700;font-size:" + (txt.length > 8 ? 14 : 17) + "px;padding:0 4px;background:" + (on ? "#5585ff" : "#2a2a3a"), txt, () => show(t), "tab");
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      rail.append(b);
    });
    owner = o;
    if (rail.firstChild && !tabs().some(x => x.tabIndex === 0)) rail.firstChild.tabIndex = 0;
    if (had) focusTab();
  };
  rail.addEventListener("keydown", ev => {
    const a = tabs();
    const i = a.indexOf(ev.target);
    if (i < 0) return;
    if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      focusFirst();
      return;
    }
    const j = ev.key === "ArrowDown" ? i + 1 : ev.key === "ArrowUp" ? i - 1 : ev.key === "Home" ? 0 : ev.key === "End" ? a.length - 1 : -1;
    if (j < 0) return;
    ev.preventDefault();
    const t = a[Math.max(0, Math.min(a.length - 1, j))];
    stop(t);
    t.focus();
  });
  panel.addEventListener("keydown", ev => {
    if (ev.defaultPrevented) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      go(false);
    } else if (ev.key === "ArrowRight" && content.contains(ev.target) && !/^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)) {
      ev.preventDefault();
      focusTab();
    }
  });
  const show = t => {
    const inside = content.contains(document.activeElement);
    const old = cur !== key(t) && find(cur);
    if (old && old.leave) safe(old.leave);
    cur = key(t);
    ss("fyTab", cur);
    paint();
    content.replaceChildren();
    const body = el("");
    content.append(body);
    owner = t.plugin || (apiOf(t) ? t.id : "");
    try { t.render(body, apiOf(t)); } catch (e) {
      body.textContent = "Module error: " + (e && e.message);
      if (t.plugin || apiOf(t)) post("fail", { id: t.plugin || t.id, what: "render", msg: String(e && e.message) }, TOK);
    }
    owner = "";
    if (inside) focusFirst();
  };
  const note = () => post("panel", isOpen(), TOK);
  const go = (on, id) => {
    const was = isOpen();
    if (on && !was) returnTo = document.activeElement;
    panel.style.display = on ? "flex" : "none";
    if (on) show(find(id) || find(ss("fyTab")) || shown()[0]);
    if (on === was) return;
    ss("fyOpen", on ? "1" : "");
    if (on) {
      focusTab();
      K.call("state_get");
    } else if (returnTo) {
      const o = returnTo;
      returnTo = null;
      safe(() => o.focus());
    }
    note();
    if (FY._panel) FY._panel(on);
  };
  FY.toggle = () => go(!isOpen());
  FY.open = id => go(true, id);
  FY.restore = () => {
    if (ss("fyOpen")) go(true, ss("fyRes") ? "#modules" : undefined);
    else note();
    const m = ss("fyMsg");
    if (m) {
      FY.toast(m);
      ss("fyMsg", "");
    }
  };
  defProp(FY, "plugins", { get: () => freeze(mods.slice()), enumerable: true });
  lock(FY, "dispose", "call", "_reply", "_k", "toast", "fail", "el", "button", "ext");
  defProp(window, "Framey", { value: FY, writable: false, configurable: true });
  document.body.append(panel);
  return FY;
})();
