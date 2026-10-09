(() => {
  if (window.Framey) return;
  const calls = {};
  let n = 0;
  const el = (css, text) => {
    const e = document.createElement("div");
    e.dataset.fy = "";
    e.style.cssText = css;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const FY = (window.Framey = {
    plugins: [],
    register(p) {
      p.name = p.name || FY._meta.name;
      p.short = p.short || FY._meta.short || p.name.split(" ")[0];
      FY.plugins.push(p);
    },
    call(plugin, method, arg) {
      return new Promise(r => {
        const id = ++n;
        calls[id] = r;
        window.fyCall(JSON.stringify({ id, plugin, method, arg }));
      });
    },
    _reply(id, v) { calls[id](v); delete calls[id]; },
    toast(text, ms) {
      const t = el("position:fixed;left:50%;bottom:40px;transform:translateX(-50%);z-index:99999;background:#101018;color:#fff;border-radius:14px;padding:14px 24px;font:22px sans-serif", text);
      document.body.append(t);
      setTimeout(() => t.remove(), ms || 2500);
    },
    fail(id, e) { FY.toast("Module " + id + " failed: " + e.message, 4000); },
  });
  const panel = el("position:fixed;top:11px;right:11px;z-index:99999;box-sizing:border-box;width:560px;max-height:650px;background:#101018;color:#fff;border-radius:16px;padding:14px;display:none;gap:12px;font:20px sans-serif;transform:scale(.9);transform-origin:top right");
  const content = el("flex:1;min-width:0;overflow:auto;max-height:620px");
  const rail = el("width:104px;display:flex;flex-direction:column;gap:10px");
  panel.append(content, rail);
  const tile = (css, text, fn) => {
    const t = el("display:flex;align-items:center;border-radius:14px;background:#2a2a3a;" + css, text);
    t.onclick = fn;
    return t;
  };
  FY.el = el;
  FY.button = (text, css, fn) => tile("justify-content:center;font-weight:700;" + css, text, fn);
  const style = document.createElement("style");
  style.dataset.fy = "core";
  style.textContent =
    ".fy-r{-webkit-appearance:none;height:14px;border-radius:7px;background:#2a2a3a;flex:1;margin:0 10px}" +
    ".fy-r::-webkit-slider-thumb{-webkit-appearance:none;width:40px;height:40px;border-radius:20px;background:#5585ff}";
  document.head.append(style);
  const LINKED = "Linked plugin: this only removes it from Framey and does not undo anything it installed on the system.";
  const settings = {
    name: "Settings",
    short: "Settings",
    render(box) {
      const status = el("font-size:18px;color:#8a8aa0;margin-top:10px", "Modules run inside Steam's UI. Install only ones you trust.");
      FY.call("_core", "list").then(list => {
        box.replaceChildren();
        list.forEach(m => {
          const row = el("display:flex;align-items:center;gap:10px;margin-bottom:10px");
          const del = tile("width:60px;height:56px;justify-content:center;font-size:24px", "x", () => {
            if (del.textContent === "x") { del.textContent = "?"; if (m.linked) FY.toast(LINKED, 7000); return; }
            FY.call("_core", "uninstall", m.id);
          });
          row.append(
            el("flex:1;font-size:21px", m.name + (m.version ? "  v" + m.version : "")),
            tile("width:90px;height:56px;justify-content:center;font-weight:700;background:" + (m.enabled ? "#5585ff" : "#2a2a3a"), m.enabled ? "On" : "Off", () => FY.call("_core", m.enabled ? "disable" : "enable", m.id)),
            del,
          );
          box.append(row);
        });
        const url = document.createElement("input");
        url.placeholder = "https:// link to a module .zip";
        url.style.cssText = "width:100%;box-sizing:border-box;height:56px;font-size:20px;border-radius:12px;border:0;padding:0 14px;margin-top:8px";
        box.append(
          url,
          tile("height:60px;justify-content:center;font-weight:700;background:#5585ff;margin-top:10px", "Install", () => {
            status.textContent = "Installing...";
            FY.call("_core", "install", url.value).then(r => { status.textContent = r.error ? "Failed: " + r.error : "Installed " + r.ok; });
          }),
          tile("height:60px;justify-content:center;font-weight:700;margin-top:10px", "Reload modules", () => FY.call("_core", "reload")),
          status,
        );
      });
    },
  };
  const store = {
    name: "Store",
    short: "Store",
    render(box) {
      FY.call("_core", "store").then(list => {
        if (!list.length) box.append(el("font-size:20px;color:#8a8aa0;text-align:center;padding:80px 0", "No modules yet"));
        list.forEach(m => {
          const row = el("display:flex;align-items:center;gap:10px;margin-bottom:10px");
          let armed = !m.installed || !m.linked;
          row.append(
            el("flex:1;font-size:21px", m.name + (m.version ? "  v" + m.version : "")),
            tile("width:130px;height:56px;justify-content:center;font-weight:700;background:" + (m.installed ? "#2a2a3a" : "#5585ff"), m.installed ? "Remove" : "Install", () => {
              if (!armed) { armed = true; FY.toast(LINKED, 7000); return; }
              FY.call("_core", m.installed ? "uninstall" : "add", m.id);
            }),
          );
          box.append(row);
        });
      });
    },
  };
  const tabs = () => [...FY.plugins, settings, store];
  const show = i => {
    sessionStorage.fyTab = tabs()[i].short;
    rail.replaceChildren();
    tabs().forEach((p, k) => rail.append(tile("height:78px;justify-content:center;text-align:center;font-weight:700;font-size:19px;padding:0 4px;background:" + (k === i ? "#5585ff" : "#2a2a3a"), p.short, () => show(k))));
    content.replaceChildren();
    const body = el("");
    content.append(body);
    try { tabs()[i].render(body); } catch (e) { body.textContent = "Module error: " + e.message; }
  };
  FY.toggle = () => {
    const shut = panel.style.display === "none";
    panel.style.display = shut ? "flex" : "none";
    sessionStorage.fyOpen = shut ? "1" : "";
    if (shut) show(Math.max(0, tabs().findIndex(p => p.short === sessionStorage.fyTab)));
  };
  FY.restore = () => { if (sessionStorage.fyOpen) FY.toggle(); };
  document.body.append(panel);
})();
