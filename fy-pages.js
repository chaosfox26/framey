(() => {
  const FY = __FY;
  if (!FY || FY.panelPages) return;
  const MAX = 8;
  const RESERVED = ["modules", "settings", "discover", "developer"];
  const ID = /^[A-Za-z0-9_.-]{1,40}$/;
  const ZONES = new Map([
    ["corner", "position:fixed;right:16px;bottom:16px;z-index:99991;display:flex;flex-direction:column;align-items:flex-end;gap:8px"],
    ["overlay", "position:fixed;top:0;left:0;right:0;bottom:0;z-index:99989;pointer-events:none"],
    ["bar", "position:fixed;top:0;left:0;right:0;z-index:99990;display:flex;flex-direction:column"],
  ]);
  const FULL = "position:fixed;top:0;left:0;right:0;bottom:0;z-index:99998;box-sizing:border-box;overflow:auto;padding:24px;background:#101018;color:#fff;font:20px sans-serif";
  const BACK = "height:56px;width:160px;margin-bottom:16px";
  const pages = new Map();
  const zones = {};
  let full = [];
  let redrawing = false;
  const safe = (fn, a) => { try { return fn(a); } catch (e) {} };
  const changed = e => {
    if (redrawing || !FY.redraw) return;
    redrawing = true;
    safe(FY.redraw, e.key);
    redrawing = false;
  };
  const zone = name => {
    let z = zones[name];
    if (!z || !z.isConnected) {
      z = zones[name] = FY.el(ZONES.get(name));
      z.dataset.fy = "core";
      z.dataset.fyZone = name;
      document.body.append(z);
    }
    return z;
  };
  const ctx = e => ({ id: e.id, plugin: e.plugin, mode: e.mode, api: e.api, close: () => leave(e) });
  const finish = e => {
    if (!e.shown) return;
    e.shown = false;
    full = full.filter(x => x !== e);
    if (e.layer) { e.layer.remove(); e.layer = null; }
    if (e.from && e.from.isConnected) safe(() => e.from.focus());
    e.from = null;
    if (e.onclose) safe(e.onclose);
  };
  const leave = e => {
    const was = e.shown, reopen = e.panel;
    e.panel = false;
    finish(e);
    if (was && e.mode === "panel" && FY.open) safe(FY.open, e.plugin);
    else if (reopen && FY.open) safe(FY.open);
  };
  const drop = (e, quiet) => {
    finish(e);
    if (pages.get(e.key) === e) pages.delete(e.key);
    if (e.mode === "panel" && !quiet) changed(e);
    return true;
  };
  const back = e => {
    const ui = e.api.ui;
    return ui && ui.button ? ui.button({ label: "Back", onclick: () => leave(e) }) : FY.button("Back", BACK, () => leave(e));
  };
  const open = e => {
    if (pages.get(e.key) !== e) return false;
    if (e.mode === "panel") return !!FY.open && (FY.open(e.key), true);
    if (e.shown) return false;
    e.from = document.activeElement;
    e.panel = !!FY.panelOpen;
    if (e.panel) FY.toggle();
    const layer = FY.el(FULL);
    layer.setAttribute("role", "dialog");
    layer.setAttribute("aria-modal", "true");
    layer.setAttribute("aria-label", e.title);
    layer.addEventListener("keydown", ev => {
      if (ev.key !== "Escape") return;
      ev.stopPropagation();
      leave(e);
    });
    const body = FY.el("");
    const b = back(e);
    layer.dataset.fy = body.dataset.fy = b.dataset.fy = e.plugin;
    layer.append(b, body);
    document.body.append(layer);
    e.layer = layer;
    e.shown = true;
    full.push(e);
    b.focus();
    try { e.render(body, ctx(e)); } catch (x) {
      leave(e);
      safe(() => FY.fail(e.plugin, x));
    }
    return true;
  };
  const tab = e => e.tab || (e.tab = {
    id: e.key,
    plugin: e.plugin,
    name: e.title,
    short: e.title.split(" ")[0],
    leave: () => finish(e),
    render(box) { e.shown = true; e.render(box, ctx(e)); },
  });
  Object.defineProperty(FY, "pages", { value: FY.pages || {} });
  Object.defineProperty(FY, "panelPages", { value: () => FY.plugins.flatMap(p => [...pages.values()].filter(e => e.mode === "panel" && e.p === p).map(tab)) });
  FY.ext.push((api, p, res, io) => {
    const pid = p.id;
    const mounts = new Map();
    let used = false;
    const err = error => ({ error });
    const own = () => [...pages.values()].filter(e => e.p === p);
    const unmount = m => {
      if (mounts.get(m.id) !== m) return;
      mounts.delete(m.id);
      m.el.remove();
      const z = zones[m.zone];
      if (z && !z.childElementCount) { z.remove(); delete zones[m.zone]; }
    };
    const send = (method, arg) => {
      if (p.dead) return Promise.resolve(err("module disposed"));
      used = true;
      return io.core(method, arg);
    };
    api.pages = {
      register(o) {
        o = o || {};
        const mode = o.mode === undefined ? "panel" : o.mode;
        if (p.dead) return err("module disposed");
        if (typeof o.id !== "string" || !ID.test(o.id) || RESERVED.includes(o.id)) return err("invalid or reserved page id");
        if (typeof o.render !== "function") return err("render must be a function");
        if (mode !== "panel" && mode !== "full") return err("mode must be panel or full");
        const key = pid + ":" + o.id;
        const old = pages.get(key);
        if (!old && own().length >= MAX) return err("page limit reached (" + MAX + ")");
        if (old) drop(old, true);
        const e = {
          id: o.id,
          key,
          plugin: pid,
          p,
          api,
          title: String(o.title === undefined ? o.id : o.title).slice(0, 80).trim() || o.id,
          mode,
          render: o.render,
          onclose: typeof o.onclose === "function" ? o.onclose : null,
          shown: false,
          layer: null,
          tab: null,
        };
        pages.set(key, e);
        if (mode === "panel") changed(e);
        const live = () => pages.get(key) === e;
        return { open: () => live() && open(e), close: () => live() && (leave(e), true), remove: () => live() && drop(e) };
      },
      open(id) {
        const e = pages.get(pid + ":" + id);
        return !!e && open(e);
      },
      close() { own().forEach(leave); },
      list() { return own().map(e => ({ id: e.id, key: e.key, title: e.title, mode: e.mode, open: e.shown })); },
    };
    api.global = {
      mount(id, o) {
        o = o || {};
        const name = o.zone === undefined ? "corner" : o.zone;
        if (p.dead) return err("module disposed");
        if (typeof id !== "string" || !ID.test(id)) return err("invalid mount id");
        if (typeof o.render !== "function") return err("render must be a function");
        if (!ZONES.has(name)) return err("unknown zone");
        const old = mounts.get(id);
        if (!old && mounts.size >= MAX) return err("mount limit reached (" + MAX + ")");
        if (old) unmount(old);
        const m = { id, zone: name, el: FY.el(name === "overlay" ? "pointer-events:auto" : "") };
        m.el.dataset.fy = pid;
        m.el.dataset.fyMount = id;
        mounts.set(id, m);
        zone(name).append(m.el);
        try { o.render(m.el); } catch (x) {
          unmount(m);
          safe(() => FY.fail(pid, x));
          return err(String(x && x.message || x));
        }
        return { el: m.el, unmount: () => unmount(m) };
      },
    };
    api.target = name => ({
      css: (key, text) => send("target_add", { pid, target: name, kind: "css", key, code: text }),
      js: (key, code, teardown) => send("target_add", { pid, target: name, kind: "js", key, code, teardown }),
      remove: key => send("target_remove", { pid, target: name, key }),
    });
    res.push(() => {
      own().forEach(e => drop(e, true));
      [...mounts.values()].forEach(unmount);
      if (used) safe(() => io.core("target_remove", { pid }));
    });
  });
})();
