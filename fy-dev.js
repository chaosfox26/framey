(() => {
  const FY = __FY;
  const K = FY && FY._k && FY._k(typeof __fyt === "string" ? __fyt : "");
  if (!K || FY.devReady) return;
  FY.devReady = true;
  const RED = "#ff7a7a", GREEN = "#7adf9a", GREY = "#8a8aa0", BLUE = "#5585ff";
  const EXPOSURE = {
    exposed: ["Debug port is open to other devices", RED],
    loopback: ["Debug port is reachable from this device only", GREEN],
    closed: ["Debug port is closed", GREY],
    unknown: ["Debug port state could not be checked", GREY],
  };
  const cut = (v, n) => String(v == null ? "" : v).slice(0, n);
  const failure = e => ({ error: cut(e && e.message || e || "no answer", 200) });
  const ask = (method, arg) => {
    try {
      return Promise.resolve(K.call(method, arg)).then(
        r => (r && typeof r === "object" ? r : { error: "no answer" }),
        failure,
      );
    } catch (e) { return Promise.resolve(failure(e)); }
  };
  const safe = f => { try { return f(); } catch (e) { return { error: cut(e && e.message, 200) }; } };
  const mk = (css, text) => {
    const e = document.createElement("div");
    e.dataset.fy = "core";
    e.style.cssText = css;
    if (text) e.textContent = text;
    return e;
  };
  const btn = (label, fn, css) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.fy = "core";
    b.tabIndex = 0;
    b.style.cssText = "font:inherit;font-weight:700;color:#fff;background:#2a2a3a;border:0;border-radius:12px;padding:10px 18px;min-height:48px;" + (css || "");
    b.textContent = label;
    b.addEventListener("click", ev => __trusted(ev) && fn(ev));
    return b;
  };
  const copy = async (text, report) => {
    if (!text) return report("Nothing to copy yet.", true);
    try {
      if (!window.navigator.clipboard || typeof window.navigator.clipboard.writeText !== "function") throw new Error("Clipboard is unavailable");
      await window.navigator.clipboard.writeText(text);
      report("Copied.", false);
    } catch (e) { report("Copy failed: " + failure(e).error, true); }
  };
  const head = text => mk("font-size:22px;font-weight:700;margin-bottom:6px", text);
  const note = (text, color) => mk("font-size:18px;overflow-wrap:anywhere;color:" + (color || GREY), text);

  const paintExposure = (box, st) => {
    box.replaceChildren();
    const e = EXPOSURE[st && st.exposure && st.exposure.state];
    if (!e) return;
    const row = mk("border:3px solid " + e[1] + ";border-radius:14px;padding:12px");
    row.append(mk("font-size:20px;font-weight:700;color:" + e[1], e[0]));
    const addr = (st.exposure.addresses || []).slice(0, 16).join(", ");
    if (st.exposure.state === "exposed") {
      row.append(note("Listening on " + addr + ". Anyone who can reach this device on your network could control Steam's interface. Framey does not open or close this port; Steam owns it."));
    } else if (addr) row.append(note(addr));
    box.append(row);
  };

  const paintHot = (box, st, list, again, view) => {
    st = st && typeof st === "object" ? st : {};
    box.replaceChildren(head("Hot reload"));
    if (st.error) return box.append(note("Failed: " + st.error, RED));
    if (st.warning) box.append(note(cut(st.warning, 200), "#e0a030"));
    if (!st.enabled) return box.append(note("Developer mode is off."));
    box.append(note("Reloads a module when its files change. Each switch turns itself off after an hour without changes."));
    if (list && list.error) return box.append(note("Could not load modules: " + cut(list.error, 200), RED));
    const on = new Map((Array.isArray(st.hot) ? st.hot : []).filter(h => h && typeof h === "object").map(h => [h.id, h]));
    (Array.isArray(list) ? list : []).filter(m => m && typeof m === "object" && typeof m.id === "string").slice(0, 64).forEach(m => {
      const h = on.get(m.id);
      const label = cut(m.name || m.id, 60) + (h ? "  (" + cut(h.mode, 20) + ", " + Math.ceil((+h.remaining_s || 0) / 60) + " min left)" : "");
      const row = mk("display:flex;align-items:center;gap:10px;margin-top:8px");
      const toggle = btn(h ? "On" : "Off", () => {
        if (!view.alive() || view.pending.has(m.id)) return;
        view.pending.add(m.id);
        toggle.disabled = true;
        toggle.setAttribute("aria-busy", "true");
        ask("dev_set", { id: m.id, hot: !h }).then(r => {
          view.pending.delete(m.id);
          if (!view.alive()) return;
          if (r.error) FY.toast("Failed: " + cut(r.error, 120), 4000);
          else if (r.warning) FY.toast(cut(r.warning, 200), 4000);
          again();
        });
      }, "width:90px;" + (h ? "background:" + BLUE : ""));
      toggle.setAttribute("aria-label", "Hot reload for " + cut(m.name || m.id, 60) + ": " + (h ? "on" : "off"));
      toggle.setAttribute("aria-pressed", String(!!h));
      toggle.disabled = view.pending.has(m.id);
      if (toggle.disabled) toggle.setAttribute("aria-busy", "true");
      row.append(mk("flex:1;font-size:20px;overflow-wrap:anywhere", label), toggle);
      box.append(row);
    });
  };

  const paintTargets = (box, r, view) => {
    box.replaceChildren();
    if (r.error) box.append(note(cut(r.error, 200), RED));
    const targets = (Array.isArray(r.targets) ? r.targets : []).filter(t => t && typeof t === "object").slice(0, 64);
    targets.forEach(t => {
      const row = mk("margin-top:8px;background:#181826;border-radius:12px;padding:10px");
      const inspector = cut(t.inspector, 4096);
      const button = btn("Copy address", () => { if (view.alive()) copy(inspector, view.reporter()); }, "margin-top:6px");
      button.disabled = !inspector;
      row.append(mk("font-size:20px;font-weight:700", cut(t.name || "other", 80) + (t.title ? ": " + cut(t.title, 200) : "")), note(cut(t.url, 4096)), note(inspector, "#8fb0ff"), button);
      box.append(row);
    });
    if (r.available && !targets.length) box.append(note("The port answered but lists no targets."));
    if (r.hint) box.append(note(r.hint));
  };

  const snapshot = () =>
    ask("diag").then(core => {
      try {
        const page = safe(() => FY.diag());
        const out = JSON.stringify({ loader: core, page }, null, 2);
        return { text: out.length > 60000 ? out.slice(0, 60000) + "\n[cut]" : out, error: core.error || page && page.error };
      } catch (e) {
        const error = "Diagnostics could not be formatted: " + failure(e).error;
        return { text: JSON.stringify({ error }), error };
      }
    });
  const text = () => snapshot().then(r => r.text);
  let live = null;

  const render = box => {
    const view = { pending: new Set(), refresh: 0, feedback: 0 };
    live = view;
    box.replaceChildren(mk("font-size:28px;font-weight:700", "Developer"), note("For plugin authors. Nothing here runs by itself; it updates when you press a button."));
    const expo = mk("margin-top:12px"), hot = mk("margin-top:16px"), dbg = mk("margin-top:16px"), dg = mk("margin-top:16px");
    view.alive = () => live === view && box.isConnected && box.contains(hot);
    const feedback = note("");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    view.reporter = () => {
      const request = ++view.feedback;
      return (message, failed) => {
        if (!view.alive() || request !== view.feedback) return;
        feedback.textContent = message;
        feedback.style.color = failed ? RED : GREEN;
      };
    };
    const targets = mk("");
    const find = btn("Find debug targets", () => {
      if (!view.alive() || find.disabled) return;
      find.disabled = true;
      find.setAttribute("aria-busy", "true");
      targets.replaceChildren(note("Finding debug targets…"));
      ask("devtools_info").then(r => {
        if (!view.alive()) return;
        find.disabled = false;
        find.removeAttribute("aria-busy");
        paintTargets(targets, r, view);
      });
    });
    dbg.append(head("Debug targets"), find, targets);
    const out = document.createElement("textarea");
    out.readOnly = true;
    out.setAttribute("aria-label", "Diagnostics output");
    out.rows = 12;
    out.dataset.fy = "core";
    out.style.cssText = "width:100%;box-sizing:border-box;margin-top:8px;font:14px monospace;background:#101018;color:#fff;border:1px solid #2a2a3a;border-radius:8px";
    const run = btn("Run diagnostics", () => {
      if (!view.alive() || run.disabled) return;
      run.disabled = true;
      run.setAttribute("aria-busy", "true");
      const report = view.reporter();
      report("Running diagnostics…", false);
      snapshot().then(r => {
        if (!view.alive()) return;
        run.disabled = false;
        run.removeAttribute("aria-busy");
        out.value = r.text;
        report(r.error ? "Diagnostics failed: " + cut(r.error, 200) : "Diagnostics ready.", !!r.error);
      });
    });
    dg.append(head("Diagnostics"), note("Versions, status, counters and recent log lines. Recognized sensitive values are hidden; review before sharing."), run, btn("Copy", () => { if (view.alive()) copy(out.value, view.reporter()); }, "margin-left:10px"), out);
    const reload = btn("Refresh status", () => { if (view.alive()) refresh(); });
    box.append(feedback, reload, expo, hot, dbg, dg);
    const refresh = () => {
      const request = ++view.refresh;
      return Promise.all([ask("dev_status"), ask("list")]).then(([st, list]) => {
        if (!view.alive() || request !== view.refresh) return;
        const at = [...hot.querySelectorAll("button")].indexOf(document.activeElement);
        paintExposure(expo, st);
        paintHot(hot, st, list, refresh, view);
        if (at >= 0) (hot.querySelectorAll("button")[at] || box.querySelector("button")).focus();
      });
    };
    return refresh();
  };

  Object.defineProperty(FY, "dev", { value: { render, text } });
})();
