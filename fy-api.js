(() => {
  const FY = __FY;
  const TOK = typeof __fyt === "string" ? __fyt : "";
  const K = FY && FY._k && FY._k(TOK);
  if (!K || FY.apiReady) return;
  const O = Object;
  const P = Promise;
  const PR = Intl.PluralRules;
  const later = setTimeout;
  const unlater = clearTimeout;
  const raf = requestAnimationFrame;
  const caf = cancelAnimationFrame;
  FY.apiReady = true;
  const NAME = /^[a-z0-9_.-]{1,40}$/;
  const LANG = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/;
  const LEVELS = ["debug", "info", "warning", "error"];
  const OPEN = new Map([["open:modules", "#modules"], ["open:settings", "#settings"]]);
  const W = new WeakMap();
  const noop = () => {};
  const cut = (v, n) => String(v == null ? "" : v).slice(0, n);
  const safe = (f, ...a) => { try { return f(...a); } catch (e) {} };
  const RT = window.FY_RUNTIME || {};
  const rt = () => RT;

  const hub = (cap, onerr) => {
    const m = new Map();
    let n = 0;
    const drop = (name, r) => {
      const a = m.get(name);
      const i = a ? a.indexOf(r) : -1;
      if (i < 0) return;
      r.dead = true;
      a.splice(i, 1);
      n--;
      if (!a.length) m.delete(name);
    };
    return {
      get size() { return n; },
      on(name, fn, once) {
        if (typeof name !== "string" || !NAME.test(name) || typeof fn !== "function") return noop;
        if (n >= cap) throw new RangeError("too many event subscriptions");
        const r = { fn, once };
        if (!m.has(name)) m.set(name, []);
        m.get(name).push(r);
        n++;
        return () => drop(name, r);
      },
      off(name, fn) {
        const a = m.get(name);
        const r = a && a.find(x => x.fn === fn);
        if (r) drop(name, r);
      },
      emit(name, payload, info) {
        const a = m.get(name);
        if (!a) return;
        a.slice().forEach(r => {
          if (r.dead) return;
          if (r.once) drop(name, r);
          try { r.fn(payload, info); } catch (e) { onerr(name, e); }
        });
      },
      clear() {
        m.forEach(a => a.forEach(r => { r.dead = true; }));
        m.clear();
        n = 0;
      },
    };
  };

  const core = hub(64, noop);
  const byKey = new Map();
  K.listen = (name, fn) => core.on(name, fn, false);
  K.event = (plugin, name, payload, dropped) => {
    if (typeof name !== "string" || !NAME.test(name)) return;
    const info = { name, dropped: dropped | 0, open: FY.panelOpen, tab: K.tab() };
    if (plugin === "_core") {
      if (name === "notice") FY.notify(payload);
      core.emit(name, payload, info);
      return;
    }
    const s = byKey.get(plugin);
    if (s && !s.p.dead) s.hub.emit(name, payload, info);
  };

  let isPanel = false;
  O.defineProperty(FY, "panelOpen", { get: () => isPanel, enumerable: true });
  const pan = new Set();
  const vis = new Set();
  let last = false;
  const nowVisible = () => isPanel && document.visibilityState === "visible";
  const poke = () => {
    const v = nowVisible();
    if (v === last) return;
    last = v;
    [...vis].forEach(r => { if (vis.has(r)) safe(r.fn, v); });
  };
  const addVis = fn => {
    const r = { fn };
    if (!vis.size) {
      last = nowVisible();
      document.addEventListener("visibilitychange", poke);
    }
    vis.add(r);
    return () => {
      if (!vis.delete(r) || vis.size) return;
      document.removeEventListener("visibilitychange", poke);
    };
  };
  FY._panel = open => {
    open = !!open;
    if (open === isPanel) return;
    isPanel = open;
    [...pan].forEach(r => { if (pan.has(r)) safe(r.fn, open); });
    poke();
  };
  FY.onPanel = fn => {
    if (typeof fn !== "function") return noop;
    const r = { fn };
    pan.add(r);
    return () => { pan.delete(r); };
  };

  const strings = new Map();
  const rules = new Map();
  let userLang = "";
  const norm = l => (typeof l === "string" && LANG.test(l) ? l.toLowerCase() : "");
  const plural = (l, n) => {
    if (!rules.has(l)) rules.set(l, safe(() => new PR(l)) || null);
    const r = rules.get(l);
    return r ? r.select(n) : "other";
  };
  const fill = (s, v) => s.replace(/\{([A-Za-z0-9_]{1,32})\}/g, (m, k) => (O.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m));
  const addStr = (plugin, lang, dict) => {
    const l = norm(lang);
    if (!l || typeof plugin !== "string" || !dict || typeof dict !== "object") return 0;
    if (!strings.has(plugin)) {
      if (strings.size >= 64) return 0;
      strings.set(plugin, new Map());
    }
    const langs = strings.get(plugin);
    if (!langs.has(l)) {
      if (langs.size >= 16) return 0;
      langs.set(l, new Map());
    }
    const d = langs.get(l);
    let n = 0;
    for (const k of O.keys(dict)) {
      if (typeof dict[k] !== "string" || k.length > 128 || (d.size >= 2000 && !d.has(k))) continue;
      d.set(k, dict[k].slice(0, 1024));
      n++;
    }
    return n;
  };
  K.str = (lang, dict) => addStr("core", lang, dict);
  const chain = plugin => {
    const l = norm(I18N.lang());
    const p = FY.plugins.find(x => x.id === plugin);
    const d = norm(p && p.meta && p.meta.i18n && p.meta.i18n.default) || "en";
    const c = [l, l.split("-")[0], d, "en"];
    return c.filter((x, i) => x && c.indexOf(x) === i);
  };
  const I18N = (FY.i18n = {
    lang() { return userLang || (norm(rt().lang) && rt().lang) || (norm(navigator.language) && navigator.language) || "en"; },
    setLang(l) {
      if (l && !norm(l)) return false;
      userLang = l || "";
      return true;
    },
    add: (plugin, lang, dict) => (plugin === "core" ? 0 : addStr(plugin, lang, dict)),
    t(plugin, key, vars) {
      key = String(key);
      vars = vars && typeof vars === "object" ? vars : {};
      const n = typeof vars.count === "number" && isFinite(vars.count) ? vars.count : null;
      const langs = strings.get(plugin);
      for (const l of chain(plugin)) {
        const d = langs && langs.get(l);
        if (!d) continue;
        const ks = n === null ? [key] : [n === 0 ? key + "_zero" : "", key + "_" + plural(l, n), key + "_other", key];
        const k = ks.find(x => x && d.has(x));
        if (k) return fill(d.get(k), vars);
      }
      return key;
    },
  });
  addStr("core", "en", { "notify.dismiss": "Dismiss" });
  O.freeze(I18N);

  const ns = { vis: [], q: [], n: 0, host: null };
  const mk = (tag, css, text) => {
    const e = document.createElement(tag);
    e.style.cssText = css;
    if (text) e.textContent = text;
    return e;
  };
  const btn = (label, fn, css) => {
    const b = mk("button", "font:inherit;font-weight:700;color:#fff;background:#2a2a3a;border:0;border-radius:12px;padding:10px 18px;min-height:48px;" + (css || ""), label);
    b.type = "button";
    b.tabIndex = 0;
    b.addEventListener("click", fn);
    return b;
  };
  const stack = () => {
    if (!ns.host || !ns.host.isConnected) {
      ns.host = mk("div", "position:fixed;top:11px;left:11px;z-index:100000;display:flex;flex-direction:column;gap:8px;width:460px;max-width:90vw");
      ns.host.dataset.fy = "core";
      document.body.append(ns.host);
    }
    return ns.host;
  };
  const room = r => ns.vis.length < 4 && (r.p === null || (ns.vis.filter(x => x.p !== null).length < 3 && ns.vis.filter(x => x.p === r.p).length < 2));
  const pump = () => {
    for (let i = 0; i < ns.q.length; ) {
      if (room(ns.q[i])) show(ns.q.splice(i, 1)[0]);
      else i++;
    }
  };
  const close = (r, reason, silent) => {
    const i = ns.vis.indexOf(r);
    const j = ns.q.indexOf(r);
    if (i < 0 && j < 0) return;
    if (i >= 0) ns.vis.splice(i, 1);
    else ns.q.splice(j, 1);
    unlater(r.t);
    if (r.el) r.el.remove();
    pump();
    if (!ns.vis.length && ns.host) {
      ns.host.remove();
      ns.host = null;
    }
    if (!silent && r.onclose) {
      try { r.onclose(reason); } catch (e) { r.log("error", "notification onclose: " + e.message); }
    }
  };
  const pick = (r, a) => {
    if (OPEN.has(a.id)) safe(() => FY.open(OPEN.get(a.id)));
    if (r.onaction) {
      try { r.onaction(a.id); } catch (e) { r.log("error", "notification onaction: " + e.message); }
    }
    close(r, "action");
  };
  const draw = r => {
    const b = mk("div", "background:#101018;color:#fff;border-radius:14px;padding:14px 18px;font:20px sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.5)");
    b.dataset.fy = r.pid;
    b.setAttribute("role", "status");
    const h = mk("div", "display:flex;gap:10px;align-items:center;font-weight:700;font-size:22px");
    if (r.icon) h.append(mk("span", "", r.icon));
    if (r.title) h.append(mk("span", "word-break:break-word", r.title));
    if (h.childNodes.length) b.append(h);
    if (r.body) b.append(mk("div", "margin-top:6px;font-size:19px;white-space:pre-wrap;word-break:break-word;color:#d0d0e0", r.body));
    const row = mk("div", "display:flex;flex-wrap:wrap;gap:10px;margin-top:12px");
    r.acts.forEach(a => row.append(btn(a.label, ev => { if (__trusted(ev)) pick(r, a); }, "background:#5585ff")));
    row.append(btn(I18N.t("core", "notify.dismiss"), () => close(r, "dismiss")));
    b.append(row);
    return b;
  };
  const show = r => {
    r.el = draw(r);
    stack().append(r.el);
    ns.vis.push(r);
    if (r.ms) r.t = later(() => close(r, "timeout"), r.ms);
  };
  const notify = (p, pid, o, log) => {
    o = o && typeof o === "object" ? o : {};
    const title = cut(o.title, 80);
    const body = cut(o.body, 400);
    if (!title && !body) return { id: 0, dismiss: noop };
    const acts = (Array.isArray(o.actions) ? o.actions.slice(0, 16) : [])
      .map(a => (a && typeof a === "object" ? { id: cut(a.id, 40), label: cut(a.label, 40) } : null))
      .filter(a => a && a.id && a.label)
      .slice(0, 3);
    const d = Number(o.duration);
    const r = {
      id: ++ns.n, p, pid, title: p ? "[" + pid + "] " + title : title, body, acts,
      icon: cut(o.icon, 8),
      ms: o.duration == null || !isFinite(d) || d < 0 ? 4000 : Math.min(d, 60000),
      onaction: typeof o.onaction === "function" ? o.onaction : null,
      onclose: typeof o.onclose === "function" ? o.onclose : null,
      log: log || noop,
    };
    if (room(r)) show(r);
    else {
      ns.q.push(r);
      const mine = ns.q.filter(x => x.p === p);
      if (p !== null && mine.length > 3) close(mine[0], "dropped");
      else if (ns.q.length > 8) close(ns.q.find(x => x.p !== null) || ns.q[0], "dropped");
    }
    return { id: r.id, dismiss: () => close(r, "dismiss") };
  };
  FY.notify = o => notify(null, "_core", o);

  FY.ext.push((api, p, res, io) => {
    const s = { p, regs: 0, raf: new Set(), tok: new Map(), lt: 0, ln: 0 };
    const key = api.id;
    O.defineProperty(api, "id", { value: key, enumerable: true, writable: false, configurable: false });
    const alive = () => !p.dead && byKey.get(key) === s;
    const disposed = () => O.assign(new Error("module disposed"), { code: "disposed" });
    const revokeToken = token => io.post("resource", { revoke: token, pid: p.id });
    const rpc = (method, arg) => api._rpc(method, arg);
    s.hub = hub(64, (name, e) => s.log("error", "event " + name + ": " + (e && e.message)));
    s.log = (level, msg) => {
      const t = Date.now();
      if (t - s.lt >= 1000) {
        s.lt = t;
        s.ln = 0;
      }
      if (s.ln++ >= 5) return;
      io.post("log", { pid: p.id, level: LEVELS.includes(level) ? level : "info", msg: cut(msg, 2048) });
    };
    W.set(p, s);
    byKey.set(key, s);
    const put = f => {
      if (s.regs >= 256) throw new RangeError("too many registrations");
      s.regs++;
      res.push(f);
      return () => {
        const i = res.lastIndexOf(f);
        if (i < 0) return;
        res.splice(i, 1);
        s.regs--;
      };
    };
    res.push(() => {
      if (byKey.get(key) === s) byKey.delete(key);
      s.hub.clear();
      s.raf.forEach(h => caf(h));
      s.raf.clear();
      s.tok.forEach(revokeToken);
      s.tok.clear();
      ns.vis.concat(ns.q).filter(r => r.p === p).forEach(r => close(r, "dispose", true));
      if (p.id !== "core") strings.delete(p.id);
    });
    api.cleanup = fn => {
      if (typeof fn !== "function") return noop;
      if (p.dead) {
        safe(fn);
        return noop;
      }
      return put(() => fn());
    };
    api.call = (method, arg, to) => (alive()
      ? io.be(method, arg, to).then(r => alive() ? r : { error: "module disposed", code: "disposed" })
      : P.resolve({ error: "module disposed", code: "disposed" }));
    api.raf = fn => {
      if (p.dead || typeof fn !== "function") return 0;
      if (s.raf.size >= 256) {
        const o = s.raf.values().next().value;
        caf(o);
        s.raf.delete(o);
      }
      const h = raf(t => {
        s.raf.delete(h);
        if (!alive()) return;
        try { fn(t); } catch (e) { s.log("error", "animation frame: " + (e && e.message)); }
      });
      s.raf.add(h);
      return h;
    };
    api.events = {
      on: (name, fn) => (p.dead ? noop : s.hub.on(name, fn, false)),
      once: (name, fn) => (p.dead ? noop : s.hub.on(name, fn, true)),
      off: (name, fn) => { if (!p.dead) s.hub.off(name, fn); },
    };
    api.notify = o => (p.dead ? { id: 0, dismiss: noop } : notify(p, key, o, s.log));
    api.onVisible = fn => {
      if (p.dead || typeof fn !== "function") return noop;
      let off = noop;
      const d = put(() => off());
      off = addVis(v => {
        try { fn(v); } catch (e) { s.log("error", "visibility handler: " + e.message); }
      });
      return () => {
        d();
        off();
      };
    };
    api.t = (name, vars) => I18N.t(key, name, vars);
    api.i18n = {
      add: (lang, dict) => alive() ? addStr(key, lang, dict) : 0,
      load: lang => {
        if (!alive() || !norm(lang)) return P.resolve(false);
        return io.core("locale", { id: key, lang }).then(r => !!(alive() && r && r.strings && addStr(key, lang, r.strings)), () => false);
      },
    };
    api.fetch = (url, o) => rpc("fetch", O.assign({}, o, { url, pid: key })).then(r => {
      if (!alive()) throw disposed();
      return r;
    });
    api.resource = (root, path, ttl) => rpc("resource", { root, path, ttl, pid: key }).then(r => {
      const token = r && typeof r.token === "string" ? r.token : "";
      if (!alive()) {
        if (token) revokeToken(token);
        throw disposed();
      }
      if (!r || typeof r.url !== "string") {
        if (token) revokeToken(token);
        throw new Error("no resource url");
      }
      if (token) {
        const old = s.tok.get(r.url);
        if (old && old !== token) revokeToken(old);
        if (!old && s.tok.size >= 16) {
          const [url, stale] = s.tok.entries().next().value;
          s.tok.delete(url);
          revokeToken(stale);
        }
        s.tok.set(r.url, token);
      }
      return r.url;
    });
    api.revoke = url => {
      const t = s.tok.get(url);
      if (!t) return P.resolve(false);
      return rpc("resource", { revoke: t, pid: key }).then(r => {
        if (s.tok.get(url) === t) s.tok.delete(url);
        return !!(r && r.ok);
      }, () => false);
    };
    api.log = (level, msg) => { if (alive()) s.log(level, msg); };
    O.defineProperties(api, {
      visible: { enumerable: true, get: () => !p.dead && nowVisible() },
      lang: { enumerable: true, get: () => I18N.lang() },
      runtime: {
        enumerable: true,
        get: () => {
          const r = rt();
          const m = p.meta || {};
          return O.freeze({
            framey: typeof r.version === "string" ? r.version : "",
            api: Number.isInteger(r.api) ? r.api : 1,
            platform: typeof r.platform === "string" ? r.platform : "",
            lang: I18N.lang(),
            plugin: key,
            version: typeof m.version === "string" ? m.version : "",
          });
        },
      },
    });
  });

  FY.diag = () => {
    const plugins = FY.plugins.filter(p => !p.dead && W.has(p)).map(p => {
      const s = W.get(p);
      const a = K.apiOf(p) || {};
      const x = safe(() => a._diag()) || {};
      const c = safe(() => a._n()) || {};
      return {
        id: p.id,
        timers: c.timers | 0,
        intervals: c.intervals | 0,
        listeners: c.listeners | 0,
        subscriptions: s.hub.size,
        pages: x.pages | 0 || safe(() => a.pages.list().length) | 0,
        globals: x.globals | 0,
        targets: x.targets | 0,
        notifications: ns.vis.concat(ns.q).filter(r => r.p === p).length,
        nodes: [...document.querySelectorAll("[data-fy]")].filter(e => e.dataset.fy === p.id).length,
      };
    });
    const calls = K.pending();
    let subs = core.size;
    plugins.forEach(x => { subs += x.subscriptions; });
    const mem = window.performance && window.performance.memory;
    return { plugins, calls, notifications: ns.vis.length + ns.q.length, subs, heap: mem ? mem.usedJSHeapSize : null, untrusted_clicks: K.untrusted() };
  };

  FY.lazy = name => (name === "picker" || name === "dev" ? P.resolve() : P.reject(new Error("unknown asset " + cut(name, 20))));

  const bye = () => K.dispose();
  if (window.__fyBye) window.removeEventListener("pagehide", window.__fyBye);
  window.__fyBye = bye;
  window.addEventListener("pagehide", bye);
  K.lock(FY, "_panel", "onPanel", "notify", "diag", "lazy", "i18n");
})();
