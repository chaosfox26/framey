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

  const pick = (api, o, folder) => new Promise((resolve, reject) => {
    if (busy) return resolve(null);
    o = o || {};
    const ui = api.ui, back = document.activeElement;
    const exts = !folder && Array.isArray(o.exts) && o.exts.length ? o.exts.slice(0, 16).map(x => String(x).toLowerCase()) : undefined;
    const name = String(o.title || "").slice(0, 80) || (folder ? "Choose a folder" : "Choose a file");
    let root = "", path = "", parent = null, seq = 0, done = false, entries = new Map(), chosen = null, known = [], untrack = null;
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
    const rootRow = ui.group();
    const lst = ui.list({ empty: "Nothing here", onselect: n => open(n) });
    lst.style.maxHeight = "55vh";
    const up = ui.button({ label: "Up", disabled: true, onclick: () => load(root, parent) });
    const ok = ui.button({ label: "Select", primary: true, disabled: !folder, onclick: () => finish(folder ? { root, path } : chosen == null ? null : { root, path: join(path, chosen), name: chosen }) });
    const no = ui.button({ label: "Cancel", onclick: () => finish(null) });
    const body = ui.group(box("font-size:28px;font-weight:700", name), crumb, err, rootRow, lst, ui.group(up, ok, no));
    body.style.flexDirection = "column";
    view.append(body);

    const finish = v => {
      if (done) return;
      done = true;
      busy = false;
      view.remove();
      if (typeof untrack === "function") untrack();
      if (back && back.isConnected && back.focus) back.focus();
      resolve(v);
    };
    const open = n => {
      const x = entries.get(n);
      if (!x) return;
      if (x.type === "dir") load(root, join(path, n));
      else {
        chosen = n;
        ok.disabled = false;
      }
    };
    const settle = () => {
      const a = document.activeElement;
      if (!view.contains(a) || a.getAttribute("aria-disabled") === "true") ui.focus(body);
    };
    const none = () => {
      err.textContent = "No folders are available to this module. Add one in loader.json (file_roots).";
      err.style.display = "";
      lst.setItems([]);
      rootRow.replaceChildren();
      crumb.textContent = "";
      entries = new Map();
      up.disabled = true;
      ok.disabled = true;
      settle();
    };
    const load = (r, p) => {
      const n = ++seq;
      api._rpc("files_list", { root: r, path: p, dirs_only: folder, exts, pid: api.id })
        .then(res => res, e => ({ error: e && e.message, code: e && e.code }))
        .then(res => {
          if (done || n !== seq) return;
          const good = !!res && typeof res === "object";
          if (good && Array.isArray(res.roots)) known = rootsOf(res.roots);
          if (r == null && !known.length && (!good || !res.error || res.code === "bad_root")) return none();
          if (!good || res.error || !Array.isArray(res.entries)) {
            err.textContent = String((good && res.error) || "Could not read this folder.").slice(0, 300);
            err.style.display = "";
            return settle();
          }
          if (r == null) {
            const pref = String(o.root || "");
            const at = known.some(x => x.id === pref) ? pref : (known.find(x => x.id === "data") || known[0]).id;
            const sp = String(o.path || "");
            if (at !== res.root || sp) return load(at, sp);
          }
          err.textContent = "";
          err.style.display = "none";
          root = String(res.root || r);
          path = String(res.path == null ? p || "" : res.path);
          parent = typeof res.parent === "string" ? res.parent : null;
          chosen = null;
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
          ui.focus(lst);
          settle();
        });
    };
    view.addEventListener("keydown", ev => {
      if (ev.key !== "Escape") return;
      ev.stopPropagation();
      finish(null);
    });
    let armed = false, early = false;
    try { untrack = track(api, () => (armed ? finish(null) : (early = true))); } catch (e) { return resolve(null); }
    if (early) return reject(new Error("module disposed"));
    armed = true;
    busy = true;
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
