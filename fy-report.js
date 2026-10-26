(() => {
  const framey = __FY;
  const core = framey && framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
  if (!core || core.report) return;

  const blankTitle = "Framey problem report";
  const blankBody = "## Steps to reproduce\n1. \n\n## Expected behavior\n\n## Actual behavior\n";
  const basicTemplate = blankTitle + "\n\n" + blankBody;
  const limitText = (value, limit) => typeof value === "string" ? value.slice(0, limit) : "";
  const repositoryUrl = value => {
    if (typeof value !== "string" || value.length > 240) return null;
    const match = /^https:\/\/github\.com\/([A-Za-z0-9][A-Za-z0-9-]{0,38})\/([A-Za-z0-9_.-]{1,100})\/?$/.exec(value);
    return match && ![".", ".."].includes(match[2]) ? "https://github.com/" + match[1] + "/" + match[2] : null;
  };
  const configuredRepository = () => repositoryUrl((window.FY_RUNTIME || {}).report_repository);
  const sameRepository = (first, second) => !!first && !!second && first.toLowerCase() === second.toLowerCase();
  const validIssueUrl = (value, repository) => {
    if (typeof value !== "string" || value.length > 7500 || !repository) return false;
    try {
      const url = new URL(value);
      return url.href.length <= 7500 && url.origin === "https://github.com" && !url.username && !url.password && !url.hash &&
        url.pathname.toLowerCase() === new URL(repository).pathname.toLowerCase() + "/issues/new" &&
        [...url.searchParams.keys()].every(key => key === "title" || key === "body");
    } catch (error) { return false; }
  };
  const request = (method, payload) => {
    try {
      return Promise.resolve(core.call(method, payload)).then(
        value => value && typeof value === "object" && !Array.isArray(value) ? value : { error: true, code: "unavailable" },
        () => ({ error: true, code: "unavailable" }),
      );
    } catch (error) { return Promise.resolve({ error: true, code: "unavailable" }); }
  };
  const reportContext = value => {
    const context = value && typeof value === "object" ? value : {};
    const result = {};
    if (context.plugin && typeof context.plugin === "object") {
      result.plugin = {};
      for (const key of ["name", "version"]) result.plugin[key] = limitText(context.plugin[key], 160);
      result.plugin.source = ["decky_stable", "decky_testing", "framey", "local", "native"].includes(context.plugin.source) ? context.plugin.source : "";
    }
    if (typeof context.error === "string") result.error = context.error.slice(0, 1000);
    if (typeof context.code === "string") result.code = context.code.slice(0, 64);
    let records = context.diagnostics;
    if (typeof core.diagnostics === "function") {
      try { records = core.diagnostics(); } catch (error) { records = []; }
    }
    if (Array.isArray(records)) result.diagnostics = records.slice(0, 24)
      .filter(record => record && typeof record === "object" && /^[a-z][a-z0-9_.]{0,63}$/.test(record.code) && Number.isInteger(record.count) && record.count > 0)
      .map(record => ({ code: record.code, count: Math.min(record.count, 9999) }));
    return result;
  };
  const element = (tag, style, text) => {
    const node = document.createElement(tag);
    node.dataset.fy = "core";
    node.style.cssText = style || "";
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (label, action) => {
    const node = element("button", "font:inherit;font-size:19px;font-weight:700;color:#fff;background:#2a2a3a;border:0;border-radius:12px;padding:10px 14px;min-height:48px", label);
    node.type = "button";
    node.addEventListener("click", event => { if (__trusted(event)) action(); });
    return node;
  };
  const labelled = (labelText, input) => {
    const label = element("label", "display:flex;flex-direction:column;gap:6px;margin-top:14px;font-size:19px");
    label.append(element("span", "font-weight:700", labelText), input);
    return label;
  };
  const inputStyle = "box-sizing:border-box;width:100%;min-height:48px;font:inherit;font-size:18px;line-height:1.45;color:#fff;background:#181826;border:1px solid #62627a;border-radius:8px;padding:10px;resize:vertical";
  let active = null;
  const cleanup = action => {
    try { action(); } catch (error) {
      // Teardown must finish even when the bounded diagnostics sink is unavailable.
      try { if (typeof core.diagnostic === "function") core.diagnostic("core.cleanup_failed"); } catch (unavailable) {}
    }
  };

  const close = () => {
    if (!active) return;
    const view = active;
    active = null;
    view.closed = true;
    try {
      cleanup(() => { if (view.observer) view.observer.disconnect(); });
      cleanup(() => window.removeEventListener("pagehide", close));
      cleanup(() => view.root.remove());
      view.background.forEach(([node, inert]) => cleanup(() => {
        if (inert === null) node.removeAttribute("inert");
        else node.setAttribute("inert", inert);
      }));
      cleanup(() => {
        const target = view.origin && view.origin.isConnected ? view.origin : view.originRegion && view.originRegion.querySelector('[role="tab"][aria-selected="true"]');
        if (target && typeof target.focus === "function") target.focus();
      });
    } finally { view.resolve({ closed: true }); }
  };

  const report = context => {
    if (active && active.root.isConnected) {
      active.back.focus();
      return active.promise;
    }
    close();
    const origin = document.activeElement;
    const originRegion = document.querySelector('[role="region"][aria-label="Framey"]');
    const sourceTab = typeof core.tab === "function" ? core.tab() : null;
    const root = element("div", "position:fixed;inset:0;z-index:100001;display:flex;align-items:flex-start;justify-content:flex-end;box-sizing:border-box;padding:11px;background:rgba(0,0,0,.65)");
    const dialog = element("section", "box-sizing:border-box;width:520px;max-width:100%;max-height:calc(100vh - 22px);overflow:auto;overscroll-behavior:contain;background:#101018;color:#fff;border-radius:16px;padding:18px;font:20px/1.45 sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.4)");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "fy-report-heading");
    const heading = element("h2", "font-size:27px;line-height:1.2;margin:0;flex:1", "Report to Framey");
    heading.id = "fy-report-heading";
    const top = element("div", "display:flex;align-items:center;gap:12px");
    const closeButton = button("Close", close);
    closeButton.setAttribute("aria-label", "Close report");
    top.append(heading, closeButton);
    const privacy = element("p", "font-size:18px;color:#b9b9cc;margin:12px 0", "Review and edit this draft, including any diagnostics. Opening GitHub sends the draft to GitHub. It becomes public only if you submit it there.");
    const destination = element("input", inputStyle);
    destination.readOnly = true;
    destination.setAttribute("aria-label", "Framey issue destination");
    const title = element("input", inputStyle);
    title.maxLength = 160;
    title.value = blankTitle;
    const body = element("textarea", inputStyle);
    body.rows = 7;
    body.maxLength = 10000;
    body.value = blankBody;
    const diagnostics = element("textarea", inputStyle);
    diagnostics.rows = 4;
    diagnostics.maxLength = 1200;
    const include = element("input", "width:28px;height:28px;flex:none");
    include.type = "checkbox";
    const includeLabel = element("label", "display:flex;gap:12px;align-items:center;min-height:48px;font-size:19px");
    includeLabel.append(include, element("span", "", "Include diagnostics"));
    const removeDiagnostics = button("Remove diagnostics", () => {
      diagnostics.value = "";
      include.checked = false;
      changed();
    });
    const diagnosticChoices = element("div", "display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:6px");
    diagnosticChoices.append(includeLabel, removeDiagnostics);
    const status = element("div", "font-size:18px;margin-top:14px;overflow-wrap:anywhere", "Preparing a sanitized draft…");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const warning = element("div", "font-size:18px;color:#ffd08a;overflow-wrap:anywhere");
    const fallback = element("textarea", inputStyle + ";margin-top:10px");
    fallback.readOnly = true;
    fallback.rows = 6;
    fallback.hidden = true;
    fallback.setAttribute("aria-label", "Sanitized report to copy");
    const actions = element("div", "display:flex;gap:10px;flex-wrap:wrap;margin-top:16px");
    const back = button("Back", close);
    const openButton = button("Open GitHub draft", () => perform("open"));
    openButton.style.background = "#3f68d0";
    const copyButton = button("Copy sanitized report", () => perform("copy"));
    const retry = button("Retry draft", () => prepare());
    retry.hidden = true;
    const basic = button("Copy basic template", () => copyText(basicTemplate));
    basic.hidden = true;
    actions.append(back, openButton, copyButton, retry, basic);
    dialog.append(top, privacy, labelled("Destination", destination), labelled("Issue title", title), labelled("Report details", body), labelled("Diagnostics (editable)", diagnostics), diagnosticChoices, warning, status, fallback, actions);
    root.append(dialog);
    const view = { root, origin, originRegion, back, closed: false, busy: false, loading: true, ready: false, repository: configuredRepository(), background: [...document.body.children].map(node => [node, node.getAttribute("inert")]) };
    view.promise = new Promise(resolve => { view.resolve = resolve; });
    active = view;
    const alive = () => {
      if (active !== view || view.closed) return false;
      if (!root.isConnected || sourceTab !== null && typeof core.tab === "function" && core.tab() !== sourceTab) {
        close();
        return false;
      }
      return true;
    };
    const showStatus = (message, failed) => {
      if (!alive()) return;
      status.textContent = message;
      status.style.color = failed ? "#ff9c9c" : "#b9b9cc";
    };
    const syncControls = () => {
      const locked = view.loading || view.busy;
      [title, body, diagnostics, include].forEach(node => { node.disabled = locked; });
      removeDiagnostics.disabled = locked || !diagnostics.value;
      openButton.disabled = locked || !view.ready || !view.repository;
      copyButton.disabled = locked || !view.ready;
      retry.disabled = basic.disabled = locked;
      dialog.setAttribute("aria-busy", String(locked));
      destination.value = view.repository ? view.repository + "/issues/new" : "Framey issue repository is not configured";
    };
    const changed = () => {
      if (!alive()) return;
      fallback.hidden = true;
      fallback.value = "";
      warning.textContent = "";
      syncControls();
      showStatus("Review your changes before opening or copying the draft.", false);
    };
    [title, body, diagnostics].forEach(node => node.addEventListener("input", changed));
    include.addEventListener("change", changed);
    const copyText = async text => {
      if (!alive() || view.busy) return;
      view.busy = true;
      syncControls();
      try {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") throw new Error("unavailable");
        await navigator.clipboard.writeText(text);
        showStatus("Copied. Review it before sharing; Framey has not submitted an issue.", false);
      } catch (error) {
        if (!alive()) return;
        fallback.value = text;
        fallback.hidden = false;
        showStatus("Copy failed. Select and copy the reviewed text below.", true);
        fallback.focus();
        fallback.select();
      } finally {
        if (alive()) { view.busy = false; syncControls(); }
      }
    };
    const prepare = async (initial = false) => {
      if (!alive() || view.busy) return;
      view.loading = true;
      view.ready = false;
      syncControls();
      retry.hidden = true;
      basic.hidden = true;
      fallback.hidden = true;
      fallback.value = "";
      showStatus("Preparing a sanitized draft…", false);
      let payload;
      try { payload = reportContext(context); } catch (error) { payload = {}; }
      const draft = await request("report_draft", payload);
      if (!alive()) return;
      const repository = repositoryUrl(draft.repository);
      const expected = configuredRepository() || view.repository;
      if (draft.error || draft.repository && !repository || expected && repository && !sameRepository(expected, repository) || typeof draft.title !== "string" || typeof draft.body !== "string" || draft.diagnostics != null && typeof draft.diagnostics !== "string") {
        title.value = blankTitle;
        body.value = blankBody;
        diagnostics.value = "";
        include.checked = false;
        retry.hidden = false;
        basic.hidden = false;
        showStatus(["repository_missing", "missing_repository"].includes(draft.code) ? "The Framey issue repository is not configured. Copy a basic template or retry after configuration is restored." : "Could not prepare the draft. Retry, or copy a basic template without plugin details.", true);
      } else {
        view.repository = repository;
        view.ready = true;
        title.value = draft.title.slice(0, 160);
        body.value = draft.body.slice(0, 10000);
        diagnostics.value = limitText(draft.diagnostics, 1200);
        include.checked = !!diagnostics.value;
        warning.textContent = limitText(draft.warning, 500);
        showStatus(repository ? "Review every field before opening GitHub or copying the report." : "The Framey issue repository is not configured. You can still copy the sanitized report.", !repository);
      }
      view.loading = false;
      syncControls();
      if (initial) dialog.scrollTop = 0;
    };
    const perform = async kind => {
      if (!alive() || view.busy || view.loading || !view.ready || kind === "open" && !view.repository) return;
      const payload = { title: title.value.slice(0, 160), body: body.value.slice(0, 10000), diagnostics: include.checked ? diagnostics.value.slice(0, 1200) : "" };
      if (!payload.title.trim()) { showStatus("Add an issue title first.", true); title.focus(); return; }
      view.busy = true;
      syncControls();
      fallback.hidden = true;
      fallback.value = "";
      showStatus("Checking the edited draft…", false);
      const linked = await request("report_link", payload);
      if (!alive()) return;
      const repository = repositoryUrl(linked.repository);
      const expected = configuredRepository() || view.repository;
      const missingRepository = !linked.repository && linked.code === "repository_missing" && linked.url == null;
      const valid = (missingRepository || repository && (!expected || sameRepository(repository, expected))) && typeof linked.title === "string" && linked.title.length <= 160 && typeof linked.body === "string" && linked.body.length <= 20000;
      if (!valid || linked.error && linked.code !== "too_long" || linked.url != null && !validIssueUrl(linked.url, repository || view.repository)) {
        view.busy = false;
        basic.hidden = false;
        syncControls();
        showStatus("Could not verify a sanitized Framey draft. No browser was opened. Retry, or copy a basic template without plugin details.", true);
        return;
      }
      const sanitizedText = linked.title + "\n\n" + linked.body;
      if (missingRepository) view.repository = null;
      else if (repository) view.repository = repository;
      warning.textContent = limitText(linked.warning, 500);
      if (kind === "copy") {
        view.busy = false;
        await copyText(sanitizedText);
        return;
      }
      if (!linked.url) {
        view.busy = false;
        syncControls();
        fallback.value = sanitizedText;
        fallback.hidden = false;
        showStatus(missingRepository ? "The Framey issue destination is unavailable. Copy the sanitized report instead; no browser was opened." : linked.code === "too_long" ? "This report is too long for a GitHub link. Copy the sanitized report instead; no browser was opened." : "A GitHub draft link could not be prepared. Copy the sanitized report instead; no browser was opened.", true);
        return;
      }
      const opened = await request("report_open", payload);
      if (!alive()) return;
      const invalidDestination = opened.repository != null && !sameRepository(repositoryUrl(opened.repository), view.repository) || opened.url != null && !validIssueUrl(opened.url, view.repository);
      if (opened.error || opened.requested !== true || invalidDestination) {
        fallback.value = sanitizedText;
        fallback.hidden = false;
        showStatus("Browser launch was not confirmed. Copy the sanitized report and open the Framey issue destination shown above.", true);
      } else showStatus("Browser launch requested. Sign in if needed, review the draft on GitHub, and submit it yourself. Framey has not created an issue.", false);
      view.busy = false;
      syncControls();
    };
    dialog.addEventListener("keydown", event => {
      if (!__trusted(event)) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (event.key !== "Tab") return;
      const controls = [...dialog.querySelectorAll("button,input,textarea")].filter(node => !node.disabled && !node.hidden && node.tabIndex >= 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    view.observer = new MutationObserver(() => { if (!root.isConnected && active === view) close(); });
    view.background.forEach(([node]) => node.setAttribute("inert", ""));
    document.body.append(root);
    view.observer.observe(document.body, { childList: true });
    window.addEventListener("pagehide", close);
    syncControls();
    closeButton.focus({ preventScroll: true });
    dialog.scrollTop = 0;
    prepare(true);
    return view.promise;
  };
  report.close = report.dispose = close;
  Object.defineProperty(core, "report", { value: report });
})();
