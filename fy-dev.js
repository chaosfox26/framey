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
  const ask = (method, arg) =>
    K.call(method, arg).then(
      r => (r && typeof r === "object" ? r : { error: "no answer" }),
      e => ({ error: cut(e && e.message, 200) }),
    );
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
  const copy = text => safe(() => window.navigator.clipboard.writeText(text).catch(() => {}));
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

  const paintHot = (box, st, list, again) => {
    st = st && typeof st === "object" ? st : {};
    box.replaceChildren(head("Hot reload"));
    if (st.error) return box.append(note("Failed: " + st.error, RED));
    if (!st.enabled) return box.append(note("Developer mode is off."));
    box.append(note("Reloads a module when its files change. Each switch turns itself off after an hour without changes."));
    const on = new Map((Array.isArray(st.hot) ? st.hot : []).filter(h => h && typeof h === "object").map(h => [h.id, h]));
    (Array.isArray(list) ? list : []).slice(0, 64).forEach(m => {
      const h = on.get(m.id);
      const label = cut(m.name || m.id, 60) + (h ? "  (" + cut(h.mode, 20) + ", " + Math.ceil((+h.remaining_s || 0) / 60) + " min left)" : "");
      const row = mk("display:flex;align-items:center;gap:10px;margin-top:8px");
      row.append(mk("flex:1;font-size:20px;overflow-wrap:anywhere", label), btn(h ? "On" : "Off", () => ask("dev_set", { id: m.id, hot: !h }).then(r => { if (r.error) FY.toast("Failed: " + cut(r.error, 120), 4000); again(); }), "width:90px;" + (h ? "background:" + BLUE : "")));
      box.append(row);
    });
  };

  const paintTargets = (box, r) => {
    box.replaceChildren();
    if (r.error && !r.available) box.append(note(r.error, RED));
    (Array.isArray(r.targets) ? r.targets : []).filter(t => t && typeof t === "object").forEach(t => {
      const row = mk("margin-top:8px;background:#181826;border-radius:12px;padding:10px");
      row.append(mk("font-size:20px;font-weight:700", (t.name || "other") + (t.title ? ": " + t.title : "")), note(t.url), note(t.inspector, "#8fb0ff"), btn("Copy address", () => copy(t.inspector), "margin-top:6px"));
      box.append(row);
    });
    if (r.available && !(Array.isArray(r.targets) && r.targets.length)) box.append(note("The port answered but lists no targets."));
    if (r.hint) box.append(note(r.hint));
  };

  const text = () =>
    ask("diag").then(core => {
      const out = JSON.stringify({ loader: core, page: safe(() => FY.diag()) }, null, 2);
      return out.length > 60000 ? out.slice(0, 60000) + "\n[cut]" : out;
    });

  const render = box => {
    box.append(mk("font-size:28px;font-weight:700", "Developer"), note("For plugin authors. Nothing here runs by itself; it updates when you press a button."));
    const expo = mk("margin-top:12px"), hot = mk("margin-top:16px"), dbg = mk("margin-top:16px"), dg = mk("margin-top:16px");
    const targets = mk("");
    dbg.append(head("Debug targets"), btn("Find debug targets", () => ask("devtools_info").then(r => paintTargets(targets, r))), targets);
    const out = document.createElement("textarea");
    out.readOnly = true;
    out.rows = 12;
    out.dataset.fy = "core";
    out.style.cssText = "width:100%;box-sizing:border-box;margin-top:8px;font:14px monospace;background:#101018;color:#fff;border:1px solid #2a2a3a;border-radius:8px";
    dg.append(head("Diagnostics"), note("Counts and versions only. No environment, tokens or other users' paths."), btn("Run diagnostics", () => text().then(t => { out.value = t; })), btn("Copy", () => copy(out.value), "margin-left:10px"), out);
    box.append(expo, hot, dbg, dg);
    const refresh = () => Promise.all([ask("dev_status"), ask("list")]).then(([st, list]) => {
      const at = [...hot.querySelectorAll("button")].indexOf(document.activeElement);
      paintExposure(expo, st);
      paintHot(hot, st, list, refresh);
      if (at >= 0) (hot.querySelectorAll("button")[at] || box.querySelector("button")).focus();
    });
    return refresh();
  };

  Object.defineProperty(FY, "dev", { value: { render, text } });
})();
