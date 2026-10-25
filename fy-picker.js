(() => {
  const FY = __FY;
  const K = FY && FY._k && FY._k(typeof __fyt === "string" ? __fyt : "");
  if (!FY || FY.pickerReady) return;
  FY.pickerReady = true;
  let busy = false;
  const track = (api, fn) => (api.cleanup ? api.cleanup(fn) : api.on({ addEventListener() {}, removeEventListener: fn }, "fy-picker", fn));
  const join = (p, n) => (p ? p + "/" + n : n);
  const rootsOf = r =>
    (Array.isArray(r) ? r : [])
      .slice(0, 16)
      .map(x => (typeof x === "string" ? { id: x, label: x } : x && { id: x.root || x.id || x.name, label: x.label || x.name || x.root || x.id }))
      .filter(x => x && x.id)
      .map(x => ({ id: String(x.id), label: String(x.label).slice(0, 60) }));

  const concealed = e => {
    if (!e || !e.isConnected) return true;
    for (let at = e; at; at = at.parentElement) {
      if (at.hidden || at.getAttribute("aria-hidden") === "true") return true;
      const css = getComputedStyle(at);
      if (css.display === "none" || css.visibility === "hidden" || css.visibility === "collapse" || css.contentVisibility === "hidden" || css.opacity === "0") return true;
    }
    return false;
  };
  const focusable = e => {
    if (!e || e.tabIndex < 0 || e.disabled || concealed(e)) return false;
    for (let at = e; at; at = at.parentElement) {
      if (at.inert || at.hasAttribute("inert") || at.getAttribute("aria-disabled") === "true" || at.matches("fieldset[disabled]")) return false;
    }
    return true;
  };

  const pick = (api, o, folder) => new Promise(resolve => {
    if (busy) return resolve(null);
    o = o || {};
    const ui = api.ui, back = document.activeElement;
    const origin = back && back !== document.body && back !== document.documentElement ? back : null;
    const exts = !folder && Array.isArray(o.exts) && o.exts.length ? o.exts.slice(0, 16).map(x => String(x).toLowerCase()) : undefined;
    const name = String(o.title || "").slice(0, 80) || (folder ? "Choose a folder" : "Choose a file");
    let root = "", path = "", parent = null, seq = 0, done = false, loading = false, ready = false, entries = new Map(), chosen = null, known = [], untrack = null, last = null, observer = null, unpanel = null;
    const box = (css, text) => {
      const e = FY.el(css, text);
      e.dataset.fy = api.id;
      return e;
    };
    const view = box("position:fixed;top:0;left:0;right:0;bottom:0;z-index:100000;box-sizing:border-box;padding:24px;background:#101018;color:#fff;font:20px sans-serif;overflow:auto");
    view.setAttribute("role", "dialog");
    view.setAttribute("aria-modal", "true");
    view.setAttribute("aria-label", name);
    const crumb = box("font-size:18px;color:#8a8aa0;overflow-wrap:anywhere");
    const err = box("font-size:19px;color:#ff7a7a;overflow-wrap:anywhere;display:none");
    err.setAttribute("role", "alert");
    const status = box("font-size:19px;color:#c8c8d8");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const errorActions = ui.group();
    errorActions.style.display = "none";
    const rootRow = ui.group();
    const lst = ui.list({ empty: "Nothing here", onselect: n => open(n) });
    lst.style.maxHeight = "55vh";
    const up = ui.button({ label: "Up", disabled: true, onclick: () => load(root, parent) });
    const ok = ui.button({ label: "Select", primary: true, disabled: true, onclick: () => {
      if (!alive() || !ready || loading || !root) return;
      if (folder) finish({ root, path });
      else if (chosen != null && entries.get(chosen)?.type === "file") finish({ root, path: join(path, chosen), name: chosen });
    } });
    const no = ui.button({ label: "Cancel", onclick: () => finish(null) });
    const body = ui.group(box("font-size:28px;font-weight:700", name), crumb, status, err, errorActions, rootRow, lst, ui.group(up, ok, no));
    body.style.flexDirection = "column";
    view.append(body);

    const finish = v => {
      if (done) return;
      done = true;
      ++seq;
      busy = false;
      if (observer) observer.disconnect();
      if (typeof unpanel === "function") unpanel();
      view.remove();
      if (typeof untrack === "function") untrack();
      const active = document.activeElement;
      if (!(active && active !== document.body && focusable(active))) {
        if (back && focusable(back)) back.focus();
        else {
          const panel = document.querySelector('[role="region"][aria-label="Framey"]');
          const target = panel && [...panel.querySelectorAll('[role="tabpanel"] [tabindex], [role="tabpanel"] button, [role="tabpanel"] input, [role="tabpanel"] select, [role="tabpanel"] textarea, [role="tab"][aria-selected="true"]')].find(focusable);
          if (target) target.focus();
        }
      }
      resolve(v);
    };
    const alive = () => {
      if (done) return false;
      if (origin && concealed(origin)) {
        finish(null);
        return false;
      }
      return true;
    };
    const open = n => {
      const x = entries.get(n);
      if (!alive() || !x || loading) return;
      if (x.type === "dir") load(root, join(path, n));
      else if (!folder && ready && x.type === "file") {
        chosen = n;
        ok.disabled = false;
        const active = document.activeElement;
        ok.focus();
        if (active && view.contains(active)) active.focus();
      }
    };
    const settle = () => {
      const a = document.activeElement;
      if (!view.contains(a) || a.getAttribute("aria-disabled") === "true") ui.focus(body);
    };
    const failure = message => {
      loading = false;
      ready = false;
      chosen = null;
      view.setAttribute("aria-busy", "false");
      lst.setAttribute("aria-busy", "false");
      status.textContent = "";
      ok.disabled = true;
      up.disabled = parent === null;
      err.textContent = String(message || "Could not read this folder.").slice(0, 300);
      err.style.display = "";
      errorActions.replaceChildren(ui.button({ label: "Retry", onclick: () => { if (last) load(last.root, last.path); } }));
      errorActions.style.display = "";
      errorActions._fySync();
      settle();
    };
    const none = () => {
      err.textContent = "No folders are available to this module. Add one in loader.json (file_roots).";
      err.style.display = "";
      lst.setItems([]);
      rootRow.replaceChildren();
      crumb.textContent = "";
      entries = new Map();
      ready = false;
      up.disabled = true;
      ok.disabled = true;
      settle();
    };
    const load = (r, p) => {
      if (!alive()) return;
      const n = ++seq;
      last = { root: r, path: p };
      loading = true;
      ready = false;
      chosen = null;
      ok.disabled = true;
      up.disabled = true;
      view.setAttribute("aria-busy", "true");
      lst.setAttribute("aria-busy", "true");
      status.textContent = "Loading folders…";
      err.textContent = "";
      err.style.display = "none";
      errorActions.replaceChildren();
      errorActions.style.display = "none";
      Promise.resolve().then(() => !alive() || n !== seq ? null : api._rpc("files_list", { root: r, path: p, dirs_only: folder, exts, pid: api.id }))
        .then(res => res, e => ({ error: e && e.message, code: e && e.code }))
        .then(res => {
          if (!alive() || n !== seq) return;
          loading = false;
          view.setAttribute("aria-busy", "false");
          lst.setAttribute("aria-busy", "false");
          status.textContent = "";
          const good = !!res && typeof res === "object";
          if (good && Array.isArray(res.roots)) known = rootsOf(res.roots);
          if (r == null && !known.length && (!good || !res.error || res.code === "bad_root")) return none();
          if (!good || res.error || !Array.isArray(res.entries)) {
            return failure(good && res.error);
          }
          if (r == null) {
            const pref = String(o.root || "");
            const at = known.some(x => x.id === pref) ? pref : (known.find(x => x.id === "data") || known[0]).id;
            const sp = String(o.path || "");
            if (at !== res.root || sp) return load(at, sp);
          }
          err.textContent = "";
          err.style.display = "none";
          root = String(res.root || r || "");
          if (!root) return failure("That location is not available.");
          path = String(res.path == null ? p || "" : res.path);
          parent = typeof res.parent === "string" ? res.parent : null;
          chosen = null;
          ready = true;
          const rows = res.entries.slice(0, 500).filter(x => x && typeof x.name === "string" && x.name);
          entries = new Map(rows.map(x => [x.name, x]));
          lst.setItems(rows.map(x => ({ id: x.name, label: x.name, sub: x.type === "dir" ? "Folder" : "", disabled: !(x.type === "dir" || (x.type === "file" && !folder)) })));
          if (known.length > 1) {
            rootRow.replaceChildren(...known.map(x => ui.button({ label: x.label, primary: x.id === root, onclick: () => load(x.id, "") })));
            rootRow._fySync();
          }
          up.disabled = parent === null;
          ok.disabled = !folder;
          crumb.textContent = [root].concat(path.split("/").filter(Boolean)).join(" / ") + (res.truncated ? "  (list shortened)" : "");
          if (folder) ok.focus();
          ui.focus(lst);
          settle();
        }).catch(e => {
          if (!done && n === seq) failure(e && e.message);
        });
    };
    view.addEventListener("keydown", ev => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        finish(null);
      } else if (ev.key === "Tab") {
        const targets = [...view.querySelectorAll("[tabindex],button,input,select,textarea,a[href]")]
          .filter(focusable);
        const first = targets[0], last = targets[targets.length - 1], active = document.activeElement;
        if (!first || (ev.shiftKey ? active === first || !targets.includes(active) : active === last || !targets.includes(active))) {
          ev.preventDefault();
          if (first) (ev.shiftKey ? last : first).focus();
          else ui.focus(body);
        }
      }
    });
    let armed = false, early = false;
    try { untrack = track(api, () => (armed ? finish(null) : (early = true))); } catch (e) { return resolve(null); }
    if (early) return resolve(null);
    armed = true;
    busy = true;
    if (!alive()) return;
    if (origin && typeof MutationObserver === "function") {
      observer = new MutationObserver(() => { alive(); });
      for (let at = origin; at; at = at.parentElement) {
        observer.observe(at, { childList: true, attributes: true, attributeFilter: ["hidden", "aria-hidden", "style", "class"] });
      }
    }
    if (typeof FY.onPanel === "function") unpanel = FY.onPanel(open => { if (!open) finish(null); });
    document.body.append(view);
    ui.focus(body);
    load(undefined, "");
  });

  const attach = api => {
    api.pickFile = o => pick(api, o, false);
    api.pickFolder = o => pick(api, o, true);
  };
  FY.plugins.forEach(p => { const a = K && K.apiOf(p); if (a) attach(a); });
  if (Object.isExtensible(FY.ext)) FY.ext.push(attach);
})();
