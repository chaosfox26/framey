(() => {
  const FY = __FY;
  const K = FY && FY._k && FY._k(typeof __fyt === "string" ? __fyt : "");
  if (!K) return;
  Object.defineProperty(FY, "pages", { value: FY.pages || {} });
  if (K.kit) return;
  const GREY = "#8a8aa0", GOOD = "#7adf9a", BAD = "#ff7a7a", UP = "#8fb0ff", WARN = "#e0a030";
  const TITLE = "font-size:28px;font-weight:700;margin-bottom:12px;overflow-wrap:anywhere";
  const DANGER = { css: "flex:1;background:#5a3a3a" };
  const T = (key, vars) => FY.i18n.t("core", key, vars);
  const obj = r => (r && typeof r === "object" ? r : {});
  const call = (method, arg) => K.call(method, arg).then(obj);
  const tx = (css, text) => FY.el(css, text);
  const put = (p, ...c) => {
    p.append(...c.filter(Boolean));
    return p;
  };
  const col = (color, text, size, bold) =>
    tx("font-size:" + (size || 19) + "px;margin-top:6px;white-space:pre-wrap;word-break:break-word;color:" + color + (bold ? ";font-weight:700" : ""), text);
  const row = (...c) => put(tx("display:flex;gap:10px;margin-top:10px;align-items:stretch"), ...c);
  const say = text => {
    try { sessionStorage.fyMsg = text; } catch (e) {}
    FY.toast(text);
  };
  const warn = text => FY.toast(text, 4000);
  const day = x => String(x || "").slice(0, 10);
  const MT = {
    archived: ["mt.archived", "\u25A0", WARN],
    declared: ["mt.declared", "\u25B2", WARN],
    recent: ["mt.recent", "\u25CF", GOOD],
    none: ["mt.none", "\u25CB", GREY],
    unknown: ["mt.unknown", "\u25C7", GREY],
  };
  const mt = r => MT[obj(r).label] || MT.unknown;
  const when = s => (typeof s === "number" && s > 0 ? new Date(s * 1000).toLocaleString() : "");
  const yes = v => T(v === true ? "mt.yes" : "mt.no");
  let U;
  const ui = () => {
    if (!U) {
      const api = { id: "core" };
      (FY.ext || []).forEach(f => { try { f(api, { id: "core", meta: {}, dead: false }, []); } catch (e) {} });
      U = api.ui;
    }
    return U;
  };
  let watch = null;
  let live = null;
  K.listen("progress", ev => { if (watch) watch(ev); });
  const keep = v => {
    try {
      if (v) sessionStorage.fyRes = JSON.stringify(v);
      else sessionStorage.removeItem("fyRes");
    } catch (e) {}
  };
  const clip = (x, n) => String(x == null ? "" : x).slice(0, n);
  const rd = () => {
    let v = null;
    try { v = JSON.parse(sessionStorage.fyRes || "null"); } catch (e) {}
    if (!v || typeof v !== "object" || !Array.isArray(v.rows)) {
      if (v !== null) keep(null);
      return null;
    }
    const rows = v.rows.slice(0, 200).filter(r => Array.isArray(r) && typeof r[0] === "string" && typeof r[1] === "string").map(r => [r[0], clip(r[1], 300)]);
    return { good: v.good === true, text: clip(v.text, 300), rows };
  };
  const pack = (good, text, rows) => {
    const v = { good: !!good, text: clip(text, 300), rows: rows.slice(0, 32).map(r => [r[0], clip(r[1], 300)]) };
    while (v.rows.length && JSON.stringify(v).length > 8000) v.rows.pop();
    return v;
  };
  const GOODSTATE = ["ok", "current", "skipped"];
  const line = x => {
    const n = x.name || x.id;
    return x.state === "ok" ? ["ok", x.to ? T("m.r_ok", { name: n, to: vv(x.to) }) : T("m.r_changed", { name: n })]
      : x.state === "current" ? ["current", T("m.r_current", { name: n })]
      : x.state === "skipped" ? ["skipped", T("m.r_skipped", { name: n, reason: x.reason || "" })]
      : ["failed", T("m.r_failed", { name: n, error: x.error || T("c.noanswer") })];
  };
  const batchPack = r => {
    const base = Array.isArray(r.results) ? r.results : r.id ? [{ id: r.id, name: r.name, state: r.error ? "failed" : r.current ? "current" : "ok", to: r.version || r.to, error: r.error }] : [];
    const extra = (Array.isArray(r.failures) ? r.failures : [])
      .filter(f => f && typeof f === "object" && !base.some(x => x && x.id === f.id))
      .map(f => ({ id: f.id, name: f.name, state: "failed", error: f.error || f.msg || f.reason }));
    const rows = base.concat(extra).filter(x => x && typeof x === "object");
    const bad = rows.filter(x => !GOODSTATE.includes(x.state));
    const ordered = rows.length > 32 ? bad.concat(rows.filter(x => !bad.includes(x))) : rows;
    const okn = typeof r.ok === "number" ? r.ok : rows.filter(x => x.state === "ok").length;
    const failn = typeof r.failed === "number" ? r.failed : bad.length;
    return pack(!failn && !r.error, r.error && !rows.length ? T("c.failed", { msg: r.error }) : T("m.summary", { ok: okn, failed: failn }), ordered.map(line));
  };
  const paintRes = (k, out, v, done) => {
    const back = k.btn(T("c.back"), done);
    put(out, col(v.good ? GOOD : WARN, v.text, 21, true), row(back));
    back.focus();
    v.rows.forEach(x => put(out, col(x[0] === "ok" ? GOOD : x[0] === "failed" ? BAD : GREY, x[1], 18)));
  };
  K.listen("maint", ev => { if (live && live.maint) live.maint(obj(ev).ids); });
  K.listen("update_result", (ev, info) => {
    const v = batchPack(obj(ev));
    keep(v);
    const seen = live && live.box.isConnected && (!info.open || info.tab === "#modules") && live.idle();
    if (seen) live.show(v);
    if (!seen || !FY.panelOpen) FY.toast(v.text, 4000);
  });

  const owners = new WeakMap();
  const make = box => {
    box.tabIndex = -1;
    box.style.outline = "none";
    let held = false;
    let view = 0;
    const k = { names: () => "" };
    owners.set(box, k);
    k.alive = () => owners.get(box) === k && box.isConnected;
    k.guard = fn => {
      const at = view;
      return (...args) => { if (k.alive() && view === at) return fn(...args); };
    };
    k.cancel = () => {
      view++;
      held = false;
      box.style.opacity = "1";
      watch = null;
    };
    k.hold = on => {
      if (!k.alive()) return;
      held = on;
      box.style.opacity = on ? ".5" : "1";
    };
    let want = null;
    k.show = (...n) => {
      if (!k.alive()) return;
      k.cancel();
      const a = document.activeElement;
      if (a !== box && box.contains(a)) want = { label: a.getAttribute("aria-label") || "", at: [...box.querySelectorAll("[tabindex]")].indexOf(a) };
      box.replaceChildren(...n.filter(Boolean));
      if (want === null) return;
      const x = document.activeElement;
      if (x !== document.body && !box.contains(x)) {
        want = null;
        return;
      }
      const all = [...box.querySelectorAll("[tabindex]")];
      const same = all[Math.min(want.at, all.length - 1)];
      const t = all.find(e => e.getAttribute("aria-label") === want.label && e.tabIndex >= 0) || (same && same.tabIndex >= 0 ? same : box.querySelector("[tabindex='0']")) || box;
      t.focus();
      want = null;
    };
    box.onkeydown = ev => {
      const d = ev.key === "ArrowDown" || ev.key === "ArrowRight" ? 1 : ev.key === "ArrowUp" || ev.key === "ArrowLeft" ? -1 : 0;
      if (d) {
        if (ev.target.closest("input,textarea,select,[contenteditable=''],[contenteditable='true'],[role='slider'],[role='listbox'],[role='combobox']")) return;
        const all = [...box.querySelectorAll("[tabindex]")].filter(e => e.tabIndex >= 0 && e.getAttribute("aria-disabled") !== "true");
        const i = all.indexOf(ev.target);
        if (i < 0 || !all[i + d]) return;
        ev.preventDefault();
        all[i + d].focus();
        return;
      }
      const b = ev.key === "Escape" && box.querySelector("[data-fy-esc]");
      if (!b || b.getAttribute("aria-disabled") === "true") return;
      ev.preventDefault();
      b.click();
    };
    k.btn = (label, fn, o) => {
      const nav = label === T("c.back") || label === T("c.cancel");
      const e = ui().button(Object.assign({ label, css: "flex:1", nav, onclick: () => {
        if (!k.alive() || !e.isConnected || (held && !nav)) return;
        if (nav) k.cancel();
        return fn();
      } }, o));
      if (nav) e.dataset.fyEsc = "1";
      return e;
    };
    k.sw = (label, value, fn) => {
      const e = ui().toggle({ label, value, onchange: v => { if (!k.alive()) return; if (held) e.value = !v; else fn(v, e); } });
      return e;
    };
    k.ask = (title, text, acts, cancel) => {
      const c = k.btn(T("c.cancel"), cancel);
      want = null;
      k.show(tx(TITLE, title), col("#fff", text, 20), ...acts.map(a => row(k.btn(a[0], a[1], a[2]))), row(c));
      c.focus();
    };
    k.prog = () => {
      const bar = tx("display:none;height:14px;margin-top:10px;border-radius:7px;background:#2a2a3a;overflow:hidden");
      const fill = tx("height:100%;width:0;background:#5585ff");
      const head = col(UP, "", 21, true), list = tx(""), pct = col(GREY, "");
      head.setAttribute("role", "status");
      head.setAttribute("aria-live", "polite");
      head.setAttribute("aria-atomic", "true");
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      const lines = new Map();
      let job = null;
      bar.append(fill);
      return {
        el: put(tx(""), head, list, bar, pct),
        update(ev) {
          if (!ev || typeof ev.stage !== "string") return;
          if (ev.job !== job) {
            job = ev.job;
            lines.clear();
            list.replaceChildren();
          }
          const who = k.names(ev.id);
          const label = String(ev.label || ev.stage);
          const text = T("p.step", { who: who ? who + ": " : "", n: ev.index | 0, total: ev.total | 0, label });
          if (head.textContent !== text) head.textContent = text;
          let l = lines.get(ev.stage);
          if (!l) {
            l = col("#fff", "", 18);
            lines.set(ev.stage, l);
            list.append(l);
          }
          const state = T("p." + (ev.state === "ok" || ev.state === "fail" ? ev.state : "running"));
          l.textContent = ev.error ? T("p.line_err", { label, state, error: ev.error }) : T("p.line", { label, state });
          l.style.color = ev.state === "fail" ? BAD : ev.state === "ok" ? GOOD : "#fff";
          const m = ev.measured;
          const real = !!m && typeof m.done === "number" && typeof m.total === "number" && m.total > 0;
          const p = real ? Math.max(0, Math.min(100, Math.round((m.done / m.total) * 100))) : 0;
          bar.style.display = real ? "block" : "none";
          bar.setAttribute("aria-label", label);
          if (real) bar.setAttribute("aria-valuenow", String(p));
          else bar.removeAttribute("aria-valuenow");
          fill.style.width = p + "%";
          pct.textContent = real ? p + "%" : "";
        },
      };
    };
    k.work = (title, run, finish) => {
      const p = k.prog(), out = tx("");
      k.hold(false);
      const nav = typeof k.back === "function" ? put(tx(""), col(GREY, T("c.work_background"), 18), row(k.btn(T("c.back"), k.back))) : null;
      k.show(tx(TITLE, title), p.el, nav, out);
      const progress = k.guard(p.update);
      watch = progress;
      return run().then(
        k.guard(r => {
          if (watch === progress) watch = null;
          if (nav) nav.remove();
          finish(r, out);
        }),
        k.guard(e => {
          if (watch === progress) watch = null;
          if (nav) nav.remove();
          if (k.bad) k.bad(e);
        }),
      );
    };
    k.result = (out, r, okText, back) => {
      if (!r.error && okText && ("ok" in r || "staged" in r)) keep(pack(true, okText, []));
      const b = k.btn(T("c.back"), () => { keep(null); back(); });
      put(out, col(r.error ? BAD : GOOD, r.error ? T("c.failed", { msg: r.error }) : okText, 21, true), r.error && r.stage && col(GREY, T("c.stage", { stage: r.stage })), row(b));
      b.focus();
      return out;
    };
    k.notes = (id, tag, title, back) => {
      k.show(tx(TITLE, title), row(k.btn(T("c.back"), back)), col(GREY, T("c.loading")));
      return call("notes", { id, tag }).then(k.guard(r => k.show(tx(TITLE, title), row(k.btn(T("c.back"), back)), col(r.error ? BAD : "#fff", r.error || r.notes || T("v.nonotes")))));
    };
    k.versions = (id, title, back, gate) => {
      let rows = [], more = false, page = 1, info = "", err = "";
      const g = gate || (f => f());
      const eligible = v => v.eligible === true && v.current !== true;
      const reason = v => v.reason || T(v.current === true ? "v.installed" : "c.unavailable");
      const vlabel = v => v.version + (v.current ? "  " + T("v.installed") : "") + (v.label ? "  " + v.label : "");
      const vsub = v => [day(v.published), v.downgrade ? T("v.older") : "", !eligible(v) ? reason(v) : ""].filter(Boolean).join("  |  ");
      const list = () =>
        k.show(
          tx(TITLE, title),
          row(k.btn(T("c.back"), back)),
          info && col(GREY, info),
          err && col(BAD, err),
          ui().list({ items: rows.map(v => ({ id: v.tag, label: vlabel(v), sub: vsub(v) })), onselect: tag => detail(rows.find(v => v.tag === tag)), empty: T("v.none") }),
          more && row(k.btn(T("v.more"), () => load(page + 1))),
        );
      const detail = v => {
        const cm = obj(v.compat);
        k.show(
          tx(TITLE, vlabel(v)),
          v.published && col(GREY, T("v.published", { d: day(v.published) })),
          cm.msg && col(cm.badge === "ok" ? GOOD : GREY, cm.msg),
          v.downgrade && col(WARN, T("v.limits")),
          !eligible(v) && col(GREY, reason(v)),
          row(
            k.btn(T(v.downgrade ? "v.goback" : "v.install"), () => install(v, false), { disabled: !eligible(v), primary: !v.downgrade }),
            k.btn(T("v.notes"), () => k.notes(id, v.tag, vlabel(v), () => detail(v))),
          ),
          row(k.btn(T("c.back"), list)),
        );
      };
      const install = (v, ok) => {
        if (!eligible(v)) return;
        if (v.downgrade && !ok) return k.ask(T("v.confirm_title", { v: v.version }), T("v.confirm_text", { v: v.version }), [[T("v.goback"), () => install(v, true), DANGER]], () => detail(v));
        g(() =>
          k.work(T("v.installing", { v: v.version }), () => call("ver_install", { id, tag: v.tag, confirm: !!ok }), (r, out) => {
            if (r.confirm) return k.ask(T("v.confirm_title", { v: v.version }), r.confirm, [[T("v.goback"), () => install(v, true), DANGER]], () => detail(v));
            if (r.current === true) return k.result(out, {}, T("m.already"), back);
            const result = r.error || r.ok || r.staged ? r : { error: T("c.noanswer") };
            k.result(out, result, T("v.installed_ok", { v: typeof r.version === "string" && r.version ? r.version : v.version }), back);
          }),
        );
      };
      const load = pg => {
        k.hold(true);
        return call("ver_list", { id, page: pg }).then(k.guard(r => {
          k.hold(false);
          if (r.error) err = r.error;
          else {
            err = "";
            page = pg;
            rows = rows.concat((Array.isArray(r.versions) ? r.versions : []).filter(v => v && typeof v === "object" && typeof v.tag === "string"));
            more = !!r.more;
            info = r.note || info;
          }
          list();
        }));
      };
      k.show(tx(TITLE, title), row(k.btn(T("c.back"), back)), col(GREY, T("c.loading")));
      return load(1);
    };
    return k;
  };

  const COMPAT = new Map([
    ["ok", ["m.cp_ok", GOOD]],
    ["newer", ["m.cp_newer", WARN]],
    ["older", ["m.cp_older", WARN]],
    ["platform", ["m.cp_platform", WARN]],
    ["api", ["m.cp_api", WARN]],
  ]);
  const compat = cm => {
    const e = COMPAT.get(obj(cm).badge);
    return col(e ? e[1] : GREY, e ? T(e[0]) + (cm.msg ? ": " + cm.msg : "") : T("m.cp_unknown"), 17);
  };
  const verdict = (s, none) =>
    !s ? (none ? [none + "\n" + T("m.nosource_hint"), GREY] : ["", GREY])
      : s.state === "current" ? [T("m.current"), GOOD]
      : s.state === "update" ? [T("m.update_avail", { v: s.latest }) + (s.known === false ? " " + T("m.commit_unknown_short") : ""), UP]
      : s.state === "none" ? [s.msg + "\n" + T("m.nosource_hint"), GREY]
      : [T("m.check_failed", { msg: s.msg }), BAD];
  const vv = x => (x ? (/^\d/.test(x) ? T("c.ver", { v: x }) : x) : T("c.unknown"));
  const dmg = (k, state, back) => {
    const frame = "border:3px solid " + BAD + ";border-radius:14px;padding:12px;margin-bottom:12px";
    if (state === "unreadable") return put(tx(frame), col(BAD, T("sd.unreadable"), 20, true));
    if (state !== "corrupt") return null;
    const repair = () => k.ask(T("sd.repair_title"), T("sd.repair_text"), [[T("sd.repair_ok"), () => call("settings_repair").then(k.guard(r => { if (r.ok) say(T("sd.repaired")); else warn(r.error || T("c.noanswer")); back(); })), { primary: true }]], back);
    return put(tx(frame), col(BAD, T("sd.corrupt"), 20, true), row(k.btn(T("sd.repair"), repair, { primary: true })));
  };

  const modules = {
    render(box) {
      const k = make(box);
      const { btn, sw } = k;
      let data = { list: [], store: [], st: {}, mp: {}, maint: {} };
      let res = {}, more = "", notes = {}, line = null;
      k.names = id => {
        const m = Array.isArray(data.list) && data.list.find(x => x.id === id);
        return m ? m.name : "";
      };
      let onHome = false, onResult = false;
      let bad = 0;
      const fall = e => {
        k.show(tx(TITLE, T("m.title")), col(BAD, T("c.failed", { msg: clip(e && e.message, 200) || T("c.noanswer") })), row(btn(T("c.retry"), () => draw(), { primary: true }), btn(T("c.close"), () => FY.toggle())));
      };
      k.bad = fall;
      const show0 = k.show;
      k.show = (...n) => {
        onHome = false;
        onResult = false;
        return show0(...n);
      };
      const mrec = id => obj(obj(data.maint)[id]);
      const pull = ids =>
        ids.length ? call("maint_get", { ids: ids.slice(0, 64) }).then(k.guard(r => { if (r.records) Object.assign(data.maint, r.records); })) : Promise.resolve();
      const draw = () =>
        Promise.all([call("list"), call("store"), call("state_get"), call("maint_prefs"), call("status")])
          .then(k.guard(([list, store, st, mp, sts]) => {
            const kept = a => (Array.isArray(a) ? a.filter(x => x && typeof x === "object" && !Array.isArray(x)) : []);
            const lst = Array.isArray(list) ? list.filter(m => m && typeof m.id === "string") : list;
            const sto = kept(store).filter(s => typeof s.id === "string");
            const arr = a => (Array.isArray(a) ? a.filter(x => typeof x === "string") : []);
            const s = Object.assign({}, obj(st), { hidden: arr(obj(st).hidden), order: arr(obj(st).order) });
            bad = (Array.isArray(list) ? list.length - lst.length : 0) + (Array.isArray(store) ? store.length - sto.length : 0);
            data = { list: lst, store: sto, st: s, mp: obj(mp), maint: {}, ss: obj(sts).settings_state };
            return pull(Array.isArray(lst) ? lst.map(m => m.id) : []);
          }))
          .then(k.guard(() => home()))
          .catch(k.guard(fall));
      const back = () => draw();
      k.back = back;
      const quick = (method, arg, okText) => {
        k.hold(true);
        return call(method, arg).then(k.guard(r => {
          k.hold(false);
          if (r.error) warn(r.error);
          else if (Array.isArray(r.purge_failed) && r.purge_failed.length) warn(T("m.purge_failed", { count: r.purge_failed.length }));
          else if (okText) say(okText);
          if (!r.error && Array.isArray(r.order)) {
            data.st = r;
            return home();
          }
          return draw();
        }));
      };
      const thaw = (m, cont) => {
        const f = obj(data.st.frozen)[m.id];
        if (!f) return cont();
        k.ask(T("m.thaw_title"), T("m.thaw_text", { name: m.name, v: vv(f.version) }), [[T("m.thaw_ok"), () => call("mod_freeze", { id: m.id, frozen: false }).then(k.guard(r => { if (r.error) { warn(r.error); draw(); } else cont(); }))]], back);
      };
      const update = m =>
        k.work(T("m.updating", { name: m.name }), () => call("update", m.id), (r, out) => {
          if (r.current) {
            res[m.id] = { state: "current" };
            return k.result(out, {}, T("m.already"), back);
          }
          if (!r.error) delete res[m.id];
          k.result(out, r, T(r.version ? "m.updated_v" : "m.updated", { name: m.name, v: r.version }), back);
        });
      const updateAsk = m => {
        const s = obj(res[m.id]);
        const n = typeof s.notes === "string" ? (s.notes.length > 600 ? s.notes.slice(0, 600) + "..." : s.notes) : "";
        let used = false;
        k.show(
          tx(TITLE, T("m.update")),
          col("#fff", T("m.plan_row", { name: m.name, from: vv(m.version), to: vv(s.latest) }), 20),
          n && col(GREY, T("s.notes_head") + "\n" + n, 18),
          row(btn(T("m.install"), () => { if (!used) { used = true; update(m); } }, { primary: true }), btn(T("c.cancel"), back)),
        );
      };
      const fin = (method, m, title, okKey) =>
        k.work(title, () => call(method, m.id), (r, out) => k.result(out, r.pending ? { error: T("m.still") } : r, T(okKey, { name: m.name }), back));
      const repair = (m, reset, confirm) =>
        k.work(T("m.repairing", { name: m.name }), () => call("repair", { id: m.id, reset, confirm }), (r, out) => {
          if (r.confirm) return k.ask(T("m.repair_reset"), r.confirm, [[T("m.repair_reset_ok"), () => repair(m, true, true), DANGER]], back);
          k.result(out, r, T("m.repaired", { name: m.name }), back);
        });
      const repairAsk = m =>
        k.ask(T("m.repair_title", { name: m.name }), T("m.repair_text"), [[T("m.repair_ok"), () => repair(m, false, false), { primary: true }], [T("m.repair_reset"), () => repair(m, true, false), DANGER]], back);
      const removeAsk = (m, purge) =>
        k.ask(
          T(purge ? "m.rmd_title" : "m.rm_title", { name: m.name }),
          [T(purge ? "m.rmd_text" : "m.rm_text"), m.linked && T("m.linked_note"), m.privileged && T("m.privileged_remove")].filter(Boolean).join("\n"),
          [[T(purge ? "m.rmd_ok" : "m.rm_ok"), () => { more = ""; quick("uninstall", { id: m.id, purge }, T("m.removed", { name: m.name })); }, DANGER]],
          back,
        );
      const comp = (m, c) =>
        k.work(T("m.comp_dl", { name: c.name }), () => call("companion_fetch", { id: m.id, name: c.name }), (r, out) =>
          k.result(out, r, [T("m.comp_ok", { name: c.name }), r.digest, r.note].filter(Boolean).join("\n"), back),
        );
      const check = el => {
        k.hold(true);
        el.textContent = T("c.checking");
        el.style.color = GREY;
        call("check").then(k.guard(r => {
          k.hold(false);
          if (!r.checked) line = [T("m.check_failed", { msg: r.error || T("c.noanswer") }), BAD];
          else {
            res = obj(r.checked);
            const all = Object.values(obj(res)).filter(x => x && typeof x === "object");
            const n = all.filter(x => x.state === "update").length;
            const f = all.filter(x => x.state === "error").length;
            const parts = [f && T("m.n_failed", { count: f }), n && T("m.n_updates", { count: n })].filter(Boolean);
            line = [parts.length ? parts.join(", ") : T("m.all_current"), f ? BAD : n ? UP : GOOD];
          }
          home();
        }));
      };
      const results = (r, out) => {
        if (!Array.isArray(r.results)) return k.result(out, { error: r.error || T("c.noanswer") }, "", back);
        r.results.forEach(x => { if (x && (x.state === "ok" || x.state === "current")) delete res[x.id]; });
        const v = batchPack(r);
        keep(v);
        paintRes(k, out, v, () => { keep(null); back(); });
      };
      const plan = () => {
        k.show(tx(TITLE, T("m.updall")), row(btn(T("c.back"), back)), col(GREY, T("c.checking")));
        k.hold(true);
        return call("batch_plan", {}).then(k.guard(r => {
          k.hold(false);
          const items = (Array.isArray(r.items) ? r.items : []).filter(x => x && typeof x === "object").slice(0, 32);
          if (r.error || !items.length) return k.show(tx(TITLE, T("m.updall")), col(r.error ? BAD : GREY, r.error || T("m.none")), row(btn(T("c.back"), back)));
          const ok = items.filter(x => x.eligible === true);
          const rest = items.filter(x => x.eligible !== true);
          k.show(
            tx(TITLE, T("m.updall")),
            col(ok.length ? UP : GREY, T("m.plan_count", { count: ok.length }), 20, true),
            row(btn(T("m.apply", { count: ok.length }), () => k.work(T("m.updating_all"), () => call("batch_apply", { ids: ok.map(x => x.id) }), results), { disabled: !ok.length, primary: true }), btn(T("c.cancel"), back)),
            ...ok.map(x => col("#fff", T("m.plan_row", { name: x.name || x.id, from: vv(x.from), to: vv(x.to) }), 19)),
            ...rest.map(x => col(GREY, T("m.plan_skip", { name: x.name || x.id, reason: x.reason || "" }), 18)),
          );
        }));
      };
      const approval = m => {
        const b = tx("border:3px solid " + WARN + ";border-radius:14px;padding:12px;margin-top:12px");
        return put(
          b,
          col(WARN, T("m.partial"), 22, true),
          col("#fff", T("m.partial_text"), 18),
          col(GREY, T("m.partial_hint"), 17),
          row(btn(T("m.retry"), () => fin("retry", m, T("m.retrying"), "m.complete"), { primary: true })),
          row(btn(T("m.again"), () => fin("finish", m, T("c.checking"), "m.complete")), btn(T("m.restore"), () => fin("rollback", m, T("m.restoring"), "m.restored"), DANGER)),
        );
      };
      const restoreAsk = m =>
        k.ask(T("m.restore_title", { name: m.name }), T("m.restore_text", { name: m.name }), [[T("m.restore_ok"), () => fin("rollback", m, T("m.restoring"), "m.restored"), DANGER]], back);
      const heldbox = m =>
        put(
          tx("border:3px solid " + UP + ";border-radius:14px;padding:12px;margin-top:12px"),
          col(UP, T("m.held"), 22, true),
          col("#fff", T("m.held_text", { name: m.name, v: vv(m.version) }), 18),
          row(btn(T("m.restore"), () => restoreAsk(m), DANGER), btn(T("m.keep"), () => fin("finish", m, T("c.checking"), "m.kept"), { primary: true })),
        );
      const failbox = m => {
        const f = m.fail || { what: "backend", msg: T("m.noresp") };
        return put(
          tx("border:3px solid " + BAD + ";border-radius:14px;padding:12px;margin-top:12px"),
          col(BAD, T(f.stage ? "m.failed_stage" : "m.failed", { what: f.what, stage: f.stage }), 20, true),
          col("#fff", f.msg, 18),
          f.at && col(GREY, new Date(f.at * 1000).toLocaleTimeString(), 16),
          row(btn(T("m.disable"), () => quick("disable", m.id, T("m.disabled_ok", { name: m.name }))), btn(T("m.reload"), () => quick("reload_one", m.id, T("m.reloaded", { name: m.name })), { primary: true })),
          row(btn(T("m.recover"), () => repairAsk(m)), btn(T("m.remove"), () => removeAsk(m, false))),
        );
      };
      const morePanel = (m, i, n) => {
        const st = data.st, fz = !!obj(st.frozen)[m.id], hid = (st.hidden || []).includes(m.id);
        const plat = (window.FY_RUNTIME || {}).platform;
        const cs = (Array.isArray(m.companions) ? m.companions : []).filter(c => c && typeof c.name === "string" && (!c.platform || !plat || c.platform === plat)).slice(0, 4);
        return put(
          tx("margin-top:6px"),
          row(btn(T("m.reload"), () => quick("reload_one", m.id, T("m.reloaded", { name: m.name }))), btn(T(hid ? "m.show" : "m.hide"), () => quick("mod_hide", { id: m.id, hidden: !hid }))),
          row(btn(T(fz ? "m.unfreeze" : "m.freeze"), () => quick("mod_freeze", { id: m.id, frozen: !fz })), btn(T("m.repair"), () => repairAsk(m))),
          st.sort !== "name" && row(btn(T("m.up"), () => quick("mod_order", { id: m.id, delta: -1 }), { disabled: i === 0 }), btn(T("m.down"), () => quick("mod_order", { id: m.id, delta: 1 }), { disabled: i === n - 1 })),
          row(btn(T("m.versions"), () => k.versions(m.id, T("m.versions_title", { name: m.name }), back, c => thaw(m, c))), btn(T("m.remove"), () => removeAsk(m, false))),
          row(btn(T("m.remove_data"), () => removeAsk(m, true), DANGER)),
          ...cs.map(c => row(btn(T("m.comp_btn", { name: c.name }), () => comp(m, c)))),
        );
      };
      const chip = (m, r) => {
        const [key, glyph, color] = mt(r);
        const b = btn(T(key), () => details(m), { css: "flex:none;justify-content:flex-start;gap:8px;font-size:18px;overflow-wrap:anywhere;background:#101018;border:2px solid " + color + ";color:" + color });
        b.setAttribute("aria-label", T("mt.aria", { name: m.name, label: T(key), stale: r.stale ? " (" + T("mt.stale") + ")" : "" }));
        const g = tx("", glyph), l = tx("", T(key));
        g.setAttribute("aria-hidden", "true");
        l.dataset.mt = "label";
        b.replaceChildren(g, l);
        return b;
      };
      const maintRow = m => {
        const r = mrec(m.id);
        const s = tx("font-size:16px;color:" + GREY, T("mt.stale"));
        s.dataset.mt = "stale";
        return r.label && put(tx("display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px"), tx("font-size:16px;color:" + GREY, T("mt.caption")), chip(m, r), r.stale && s);
      };
      const details = m => {
        const r = mrec(m.id), [key, glyph, color] = mt(r), d = obj(r.declaration), f = obj(r.failure);
        const ev = (Array.isArray(r.evidence) ? r.evidence : []).slice(0, 12);
        const reason = clip(r.reason, 400);
        const days = T("mt.days", { count: r.window_days | 0 });
        const path = r.scope === "plugin path";
        const ln = (text, c) => text && col(c || "#fff", text, 18);
        k.show(
          tx(TITLE, T("mt.title", { name: m.name })),
          col(color, glyph + " " + T(key) + (r.stale ? "  (" + T("mt.stale") + ")" : ""), 22, true),
          ln(reason, "#fff"),
          r.label === "none" && !/does not establish abandonment/.test(reason) && ln(T("mt.none_note"), GREY),
          ln(r.repo && T("mt.repo", { repo: clip(r.repo, 200), branch: clip(r.branch, 100) || T("c.unknown") })),
          ln(r.source && T("mt.source", { source: clip(r.source, 200) })),
          ln(r.scope && (path ? T("mt.scope_path", { path: clip(r.path, 200) }) : T("mt.scope_repo"))),
          ln(r.repo && (r.commit_at ? T("mt.commit", { d: day(r.commit_at) }) : T("mt.no_commit"))),
          ln(r.repo && (r.release_at ? T("mt.release", { tag: clip(r.release_tag, 64) || T("c.unknown"), d: day(r.release_at) }) + (path ? " " + T("mt.release_repo") : "") : T("mt.no_release"))),
          ln(r.repo && T("mt.archived_flag", { v: yes(r.archived) })),
          ln(r.fork_of && T("mt.fork", { repo: clip(r.fork_of, 200) })),
          d.by && ln(T("mt.decl", { by: clip(d.by, 80), date: d.date ? " (" + clip(d.date, 10) + ")" : "" }), WARN),
          d.note && ln(T("mt.decl_note", { note: clip(d.note, 300) })),
          d.by && ln(T("mt.decl_src", { file: clip(d.file, 220), commit: clip(d.commit, 40).slice(0, 7) || T("c.unknown") }), GREY),
          ln(r.declaration_note && T("mt.decl_ignored", { why: clip(r.declaration_note, 300) }), GREY),
          ln(r.checked_at ? T("mt.checked", { t: when(r.checked_at) }) : T("mt.never"), GREY),
          ln(T("mt.window", { days }), GREY),
          r.stale && ln(T("mt.stale_note"), GREY),
          f.reason && ln(T("mt.failed", { reason: clip(f.reason, 300) }) + (when(f.at) ? " (" + when(f.at) + ")" : ""), BAD),
          r.retry_after * 1000 > Date.now() && ln(T("mt.retry", { t: when(r.retry_after) }), GREY),
          ev.length > 0 && col(GREY, T("mt.why"), 18, true),
          ...ev.map(x => ln("- " + clip(x, 300), "#fff")),
          row(btn(T("mt.refresh"), () => refresh(m), { primary: true }), btn(T("c.back"), () => home())),
          ln(T("mt.info"), GREY),
        );
      };
      const refresh = m => {
        k.hold(true);
        k.show(tx(TITLE, T("mt.title", { name: m.name })), row(btn(T("c.back"), () => home())), col(GREY, T("c.checking")));
        return call("maint_refresh", { id: m.id }).then(k.guard(r => {
          k.hold(false);
          if (r.error) warn(r.error);
          else data.maint[m.id] = r;
          details(m);
        }));
      };
      const refreshAll = el => {
        k.hold(true);
        el.textContent = T("c.checking");
        el.style.color = GREY;
        call("maint_refresh", { id: "all" }).then(k.guard(r => {
          k.hold(false);
          const n = Object.keys(obj(r.records)).length;
          if (r.error) line = [T("c.failed", { msg: r.error }), BAD];
          else {
            Object.assign(data.maint, r.records);
            line = [T("mt.refreshed", { count: n }), GOOD];
          }
          home();
        }));
      };
      const card = (m, i, n) => {
        const fz = obj(data.st.frozen)[m.id];
        const v = verdict(res[m.id], m.nosource);
        const c = tx("background:#181826;border-radius:14px;padding:12px;margin-bottom:12px");
        return put(
          c,
          tx("font-size:22px;font-weight:700;overflow-wrap:anywhere", m.name + (m.version ? "  " + T("c.ver", { v: m.version }) : "")),
          tx("font-size:17px;overflow-wrap:anywhere;color:" + GREY, m.commit ? T("m.commit", { c: m.commit }) : T("m.commit_unknown")),
          (data.st.hidden || []).includes(m.id) && col(GREY, T("m.hidden"), 18),
          fz && col(UP, T("m.frozen_at", { v: vv(fz.version) }), 18),
          v[0] && col(v[1], v[0]),
          notes[m.id] && col(notes[m.id][1], notes[m.id][0]),
          compat(m.compat),
          maintRow(m),
          (m.fail || m.stuck) && failbox(m),
          put(
            tx("display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;align-items:stretch"),
            !m.nosource && !m.pending && btn(T("m.update"), () => thaw(m, () => updateAsk(m)), { primary: !!(res[m.id] && res[m.id].state === "update") }),
            btn(T(more === m.id ? "m.less" : "m.more"), () => { more = more === m.id ? "" : m.id; home(); }),
            sw(T("m.enabled"), m.enabled, on => quick(on ? "enable" : "disable", m.id, T(on ? "m.enabled_ok" : "m.disabled_ok", { name: m.name }))),
          ),
          m.pending && approval(m),
          m.held && !m.pending && heldbox(m),
          more === m.id && morePanel(m, i, n),
        );
      };
      const tail = store => {
        const out = [];
        if (Array.isArray(store) && store.length) {
          out.push(tx("font-size:22px;font-weight:700;margin:16px 0 8px", T("m.store_title")));
          store.forEach(s =>
            out.push(
              row(
                tx("flex:1;min-width:0;overflow-wrap:anywhere;font-size:21px;align-self:center", s.name + (s.version ? "  " + T("c.ver", { v: s.version }) : "")),
                btn(T(s.installed ? "m.remove" : "m.install"), () => (s.installed ? removeAsk(s, false) : quick("add", s.id, T("m.installed_ok", { name: s.name }))), { css: "width:130px;flex:none" }),
              ),
            ),
          );
        }
        const url = document.createElement("input");
        url.dataset.fy = "core";
        url.placeholder = T("m.link_hint");
        url.style.cssText = "width:100%;box-sizing:border-box;height:56px;font-size:20px;border-radius:12px;border:0;padding:0 14px;margin-top:16px";
        const install = () => {
          const link = url.value;
          let host = link;
          try { host = new URL(link).host; } catch (e) {}
          k.ask(
            "Install from link?",
            "Install from " + host + "? The plugin and its backend run code with your user permissions.",
            [[T("m.install"), () => k.work(T("m.installing"), () => call("install", link), (r, o) => {
              if (r.error) return k.result(o, r, "", draw);
              say(T("m.installed_ok", { name: typeof r.ok === "string" ? r.ok : "" }));
              draw();
            }), { primary: true }]],
            draw,
          );
        };
        out.push(url, row(btn(T("m.install"), install, { primary: true })));
        return out;
      };
      const paint = () => {
        const { list, store, st } = data;
        if (!Array.isArray(list)) return k.show(tx(TITLE, T("m.title")), col(BAD, obj(list).error || T("c.unavailable")), row(btn(T("c.retry"), () => draw())));
        const custom = st.sort !== "name";
        const rank = new Map((st.order || []).map((id, i) => [id, i]));
        const mods = list.slice().sort((a, b) => (rank.has(a.id) ? rank.get(a.id) : 1e9) - (rank.has(b.id) ? rank.get(b.id) : 1e9));
        const sum = col(line ? line[1] : GREY, line ? line[0] : "", 19);
        const banner = () =>
          put(
            tx("border:3px solid " + WARN + ";border-radius:14px;padding:12px;margin-bottom:12px"),
            col(WARN, T("m.safe_title"), 22, true),
            col("#fff", T("m.safe_text"), 18),
          );
        k.show(
          tx(TITLE, T("m.title")),
          dmg(k, data.ss, back),
          st.safe && banner(),
          row(btn(T(custom ? "m.sort_custom" : "m.sort_name"), () => quick("mod_sort", custom ? "name" : "custom")), btn(T("m.check"), () => check(sum), { primary: true })),
          row(btn(T("m.updall"), plan), btn(T("mt.refresh_all"), () => refreshAll(sum))),
          sum,
          data.mp && data.mp["maint.window_days"] > 0 && col(GREY, T("mt.window_line", { days: T("mt.days", { count: data.mp["maint.window_days"] }) }), 16),
          bad > 0 && col(WARN, T("m.dropped", { count: bad }), 18),
          !mods.length && col(GREY, T("m.none"), 20),
          ...mods.map((m, i) => card(m, i, mods.length)),
          ...tail(store),
        );
        onHome = true;
      };
      const home = () => {
        try { paint(); } catch (e) { fall(e); }
      };
      const showRes = v => {
        k.hold(false);
        const out = tx("");
        k.show(tx(TITLE, T("m.res_title")), out);
        onResult = true;
        paintRes(k, out, v, () => { keep(null); draw(); });
      };
      live = {
        box,
        idle: () => onHome || onResult,
        show: showRes,
        maint: ids => {
          const have = Array.isArray(ids) && Array.isArray(data.list) ? data.list.map(m => m.id).filter(id => ids.includes(id)) : [];
          if (k.alive() && onHome && have.length) pull(have).then(k.guard(() => { if (onHome) home(); }));
        },
      };
      const pend = rd();
      if (pend) showRes(pend);
      else draw();
    },
  };
  K.kit = Object.freeze({ T, call, tx, put, col, row, ui, say, warn, obj, day, make, dmg, GREY, GOOD, BAD, UP, WARN, TITLE, DANGER });
  Object.defineProperty(FY.pages, "modules", { value: Object.freeze({ render: modules.render }), enumerable: true });
})();
