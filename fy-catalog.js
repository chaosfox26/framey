(() => {
  const FY = __FY;
  const K = FY && FY._k && FY._k(typeof __fyt === "string" ? __fyt : "");
  if (!K || !K.kit || FY.pages && FY.pages.store) return;
  const { tx, put, col, row, make, GOOD, BAD, UP, WARN } = K.kit;
  const MUTED = "#b8b8cc";
  const TITLE = "font-size:28px;font-weight:700;margin:0 0 12px;overflow-wrap:anywhere";
  const TEXT = {
    title: "Store", search: "Search", searchLabel: "Search modules", refresh: "Refresh",
    filters: "Filters", reset: "Reset filters", category: "Category", source: "Source",
    sort: "Sort", all: "All", loading: "Loading modules…",
    detailsLoading: "Loading module details…", retry: "Retry", back: "Back", cancel: "Cancel",
    empty: "No modules match these filters.", emptyHint: "Try another search or reset the filters.",
    none: "No modules are available.", noneHint: "Refresh to check the catalog again.",
    failed: "The catalog could not be loaded.", noanswer: "No response was received.",
    offline: "Showing saved catalog data that may be out of date.", cached: "Showing saved catalog data.",
    offlineInstall: "Refresh this catalog before installing.",
    installed: "Installed", installing: "Installing…", install: "Install",
    review: "Review installation", risk: "This module and its backend can run code with your user permissions. Install only if you trust the source.",
    unknown: "Compatibility has not been confirmed.", compatible: "Compatible", incompatible: "Not compatible",
    success: "Installation complete.", installFailed: "Installation failed.",
    ongoing: "Installation continues when you leave this page.", description: "Description",
    permissions: "Permissions", noDescription: "No description is available.",
  };
  const text = (v, max = 600) => typeof v === "string" ? v.slice(0, max) : "";
  const object = v => v && typeof v === "object" && !Array.isArray(v) ? v : {};
  const call = (name, arg) => Promise.resolve().then(() => K.call(name, arg)).then(object);
  const problem = v => text(object(v).error, 600) || text(object(v).message, 600) || TEXT.noanswer;
  const jobs = new Map();
  let active = null;
  const leave = () => {
    if (active) active.dispose();
    active = null;
  };
  const render = (box, deckyPage) => {
      leave();
      const k = make(box);
      const ordinaryKey = box.onkeydown;
      box.onkeydown = ev => {
        if (/^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName)) return;
        ordinaryKey(ev);
      };
      let dead = false, serial = 0, view = "browse", selected = null, filterOpen = false;
      let data = {}, rows = [], listError = "", detailError = "", loading = false, focusName = "";
      const allowedSources = deckyPage ? ["decky_stable", "decky_testing"] : ["stable", "testing", "custom"];
      let sources = deckyPage ? [{ id: "decky_stable", label: "Decky Store" }, { id: "decky_testing", label: "Decky Store Testing" }] : [{ id: "stable", label: "Stable" }, { id: "testing", label: "Testing" }, { id: "custom", label: "Custom" }];
      let customURL = "", sourceError = "", sourceBusy = false, reviewed = false, sourceSerial = 0;
      let inspection = { version: "", pending: false, error: "", result: null };
      const inspections = new Map();
      let rememberedSource = "decky_stable";
      if (deckyPage) { try { if (allowedSources.includes(sessionStorage.fyDeckyCatalogSource)) rememberedSource = sessionStorage.fyDeckyCatalogSource; } catch (e) {} }
      let form = { query: "", category: "all", source: deckyPage ? rememberedSource : "stable", sort: "name" };
      const state = {
        dispose() {
          if (dead) return;
          dead = true;
          serial++;
          sourceSerial++;
          box.onkeydown = null;
        },
      };
      active = state;
      const alive = () => !dead && active === state && box.isConnected;
      const scoped = n => alive() && n === serial;
      const fresh = name => {
        view = name;
        serial++;
        return serial;
      };
      const button = (label, fn, options) => {
        const nav = label === TEXT.back || label === TEXT.cancel;
        const e = k.btn(label, () => { if (alive()) return fn(); }, Object.assign({ nav }, options));
        if (nav) e.dataset.fyEsc = "1";
        return e;
      };
      const heading = title => {
        const h = tx(TITLE, title);
        h.setAttribute("role", "heading");
        h.setAttribute("aria-level", "2");
        return h;
      };
      const subheading = (title, level) => {
        const h = col("#fff", title, level === 3 ? 22 : 21, true);
        h.setAttribute("role", "heading");
        h.setAttribute("aria-level", String(level));
        return h;
      };
      const status = (message, color = MUTED) => {
        const e = col(color, message, 18);
        e.setAttribute("role", "status");
        e.setAttribute("aria-live", "polite");
        return e;
      };
      const show = (...nodes) => {
        if (!alive()) return;
        k.show(...nodes);
        if (focusName) {
          const target = [...box.querySelectorAll("[aria-label]")].find(e => e.getAttribute("aria-label") === focusName && e.tabIndex >= 0);
          if (target) target.focus();
          focusName = "";
        }
      };
      const field = (label, control) => {
        const holder = tx("display:flex;flex-direction:column;gap:6px;flex:1;min-width:130px");
        const caption = document.createElement("label");
        caption.style.cssText = "font-size:17px;color:" + MUTED;
        caption.textContent = label;
        caption.append(control);
        holder.append(caption);
        return holder;
      };
      const inputStyle = "box-sizing:border-box;width:100%;min-height:48px;border:2px solid #70708b;border-radius:12px;background:#1a1a28;color:#fff;font:20px sans-serif;padding:10px 12px;margin-top:6px;caret-color:#8fb0ff";
      const fieldFocus = e => {
        e.addEventListener("focus", () => { e.style.outline = "4px solid #8fb0ff"; e.style.outlineOffset = "2px"; });
        e.addEventListener("blur", () => { e.style.outline = ""; e.style.outlineOffset = ""; });
      };
      const names = (values, items, prop) => {
        const entries = new Map();
        (Array.isArray(values) ? values : []).slice(0, 200).forEach(v => {
          const id = typeof v === "string" ? text(v, 150) : text(object(v).id || object(v).value, 150);
          if (id) entries.set(id, typeof v === "string" ? id : text(v.name || v.label, 150) || id);
        });
        items.forEach(item => {
          const id = text(item[prop], 150);
          if (id && !entries.has(id)) entries.set(id, id);
        });
        return [...entries];
      };
      const select = (label, key, options) => {
        const e = document.createElement("select");
        e.dataset.fy = "core";
        e.style.cssText = inputStyle;
        e.setAttribute("aria-label", label);
        fieldFocus(e);
        e.tabIndex = 0;
        options.forEach(([value, labelText]) => {
          const o = document.createElement("option");
          o.value = value;
          o.textContent = labelText;
          e.append(o);
        });
        e.value = form[key];
        e.addEventListener("change", () => {
          form[key] = e.value;
          if (key === "source") configureSource();
          else load(false);
        });
        return field(label, e);
      };
      const sourceArgs = item => {
        const source = item ? item._catalogSource : form.source;
        return Object.assign({ source }, source === "custom" ? { url: item ? item._catalogURL : customURL } : {});
      };
      const offline = () => data.stale === true || data.origin === "bundled";
      const identity = item => [item._catalogSource || form.source, item._catalogSource === "custom" ? item._catalogURL || "" : "", text(object(item.source).repository, 1000), text(item.id, 200), typeof item.identity === "string" ? item.identity : ""].join("\n");
      const jobFor = item => jobs.get(identity(item));
      const decky = item => deckyPage || item.format === "decky" || item.kind === "decky" || item._catalogSource === "decky_stable" || item._catalogSource === "decky_testing";
      const readOnly = item => decky(item) || item.read_only === true;
      const formatLabel = item => decky(item) ? "Decky plugin" : "Framey-native module";
      const compat = item => {
        const out = tx("margin-top:8px");
        if (decky(item)) {
          put(out, col(WARN, "Not compatible", 21, true), col("#fff", "Built for Decky’s interface; it needs a Framey version before it can run here.", 18));
          const inspectingSelection = selected === item && view === "details";
          const cached = object(item._latestAssessment);
          const defaultVersion = versions(item).find(v => v.name === item.version) || versions(item)[0];
          const bound = defaultVersion && cached.entry_key === identity(item) && cached.version_identity === defaultVersion.identity && cached.hash === defaultVersion.hash;
          const evidence = inspectingSelection ? inspection.result : bound ? cached.assessment : null;
          const assessment = object(evidence);
          const binaries = Array.isArray(assessment.native_binaries) ? assessment.native_binaries : [];
          const x86 = assessment.architecture === "x86_64" || binaries.some(binary => object(binary).architecture === "x86_64");
          const arm = assessment.architecture === "aarch64" || binaries.some(binary => object(binary).architecture === "aarch64");
          const mixed = assessment.architecture === "mixed" || x86 && arm;
          if (evidence && inspectingSelection) {
            const version = versions(item).find(v => v.identity === inspection.version);
            if (version) out.append(col(MUTED, "Selected version " + text(version.name, 200) + " · SHA-256 " + text(version.hash, 12), 17, true));
          }
          if (!mixed && !arm && (assessment.architecture_status === "incompatible" || x86)) put(out, col(WARN, "Needs an ARM64 build", 19, true), col("#fff", "Includes a program built for Intel/AMD processors; your headset needs an ARM64 build.", 18));
          else {
            const failed = selected === item && view === "details" && inspection.error;
            const reason = failed ? "The archive inspection failed: " + inspection.error : offline() ? "This catalog may be out of date. Refresh it before inspecting an archive for ARM64 support." : mixed ? "This archive contains mixed native architectures. A manual porting review is needed to determine which programs it uses on ARM64." : arm ? "ARM64 binaries were found, but running this plugin in Framey has not been verified." : "ARM64 compatibility is unverified. The catalog alone cannot confirm support for your headset.";
            put(out, col(MUTED, "ARM64: Not checked", 19, true), col(MUTED, reason, 18));
          }
          return out;
        }
        const declared = object(item.compatibility);
        const cm = object(item.compat);
        const badge = text(cm.badge || cm.state, 40);
        let verdict = declared.status;
        let reason = text(declared.reason, 600);
        if (offline()) {
          verdict = "unverified";
          reason = "This catalog may be out of date. Refresh it before checking current compatibility.";
        } else if (verdict === "compatible" && (item.platform_declared !== true || declared.basis !== "declared_requirements")) {
          verdict = "unverified";
          reason = "Platform support is not declared clearly enough to check this module on your headset.";
        } else if (!["compatible", "incompatible", "unverified"].includes(verdict)) {
          if (cm.state === "incompatible" || ["platform", "api", "incompatible", "blocked", "newer", "older"].includes(badge)) verdict = "incompatible";
          else if ((badge === "ok" || badge === "compatible") && item.platform_declared === true) verdict = "compatible";
          else verdict = "unverified";
          reason = verdict === "unverified" ? "Platform or compatibility requirements are missing. ARM64 compatibility has not been checked." : text(cm.msg || cm.message, 600);
        }
        const label = verdict === "compatible" ? "Compatible" : verdict === "incompatible" ? "Not compatible" : "Not checked";
        const color = verdict === "compatible" ? GOOD : verdict === "incompatible" ? WARN : MUTED;
        put(out, col(color, label, 21, true), col("#fff", reason || (verdict === "compatible" ? "Meets the module’s declared requirements." : verdict === "incompatible" ? "The module’s requirements do not match this device." : "There is not enough compatibility information to check this module."), 18));
        if (verdict === "compatible") out.append(col(MUTED, "Meets declared Framey, API and platform requirements. This is not a runtime test.", 16));
        return out;
      };
      const summary = item => [text(item.category, 100), text(item.channel, 100), item.version && "v" + text(item.version, 100)].filter(Boolean).join(" · ");
      const updatable = item => item.installed === true && item.update_available === true && item.eligible === true;
      const blocked = item => readOnly(item) || item.installed === true && !updatable(item) || item.eligible !== true || offline() || !!(jobFor(item) && jobFor(item).pending);
      const installLabel = item => jobFor(item) && jobFor(item).pending ? TEXT.installing : updatable(item) ? "Update " + item.name : item.installed === true ? TEXT.installed : TEXT.install + " " + item.name;
      const installHint = item => jobFor(item) && jobFor(item).pending ? TEXT.ongoing : offline() ? TEXT.offlineInstall : item.eligible !== true ? text(item.reason, 600) || (item.installed === true ? TEXT.installed : TEXT.incompatible) : updatable(item) ? "An update is available." : item.installed === true ? TEXT.installed : "";
      const detailName = item => "Details: " + item.name;
      const normal = value => {
        const it = object(value);
        const id = text(it.id, 200);
        if (!id) return null;
        return Object.assign({}, it, { id, name: text(it.name, 200) || id, _catalogSource: form.source, _catalogURL: form.source === "custom" ? customURL : "" });
      };
      const snapshot = () => put(tx(""),
        data.origin === "bundled" ? status("Showing the bundled catalog. Refresh to check for current releases.", WARN)
          : data.origin === "cache" || data.stale === true ? status(data.stale === true ? TEXT.offline : TEXT.cached, WARN) : null,
        text(data.warning, 600) && col(WARN, text(data.warning, 600), 17),
        Number.isFinite(data.downloaded_at) && data.downloaded_at > 0 && col(MUTED, "Catalog saved: " + new Date(data.downloaded_at * 1000).toLocaleString(), 16),
        Number.isFinite(data.checked_at) && data.checked_at > 0 && col(MUTED, "Last checked: " + new Date(data.checked_at * 1000).toLocaleString(), 16),
      );
      const back = () => {
        fresh("browse");
        focusName = selected ? detailName(selected) : TEXT.search;
        draw();
      };
      const filterPanel = () => {
        const out = put(tx("display:flex;flex-wrap:wrap;gap:12px;margin:14px 0"),
          select(TEXT.category, "category", [["all", TEXT.all], ...names(data.categories, rows, "category").filter(v => v[0] !== "all")]),
          select(TEXT.source, "source", sources.map(s => [s.id, s.label])),
          select(TEXT.sort, "sort", [["name", "Name"], ["updated", "Recently updated"], ["relevance", "Relevance"]]),
        );
        if (form.source === "custom") {
          const url = document.createElement("input");
          url.type = "url";
          url.dataset.fy = "core";
          url.style.cssText = inputStyle;
          url.setAttribute("aria-label", "Custom catalog URL");
          fieldFocus(url);
          url.maxLength = 2000;
          url.value = customURL;
          url.tabIndex = 0;
          const apply = button("Apply source", configureSource, { disabled: sourceBusy });
          url.addEventListener("input", () => {
            customURL = url.value.slice(0, 2000);
            serial++;
            sourceSerial++;
            sourceBusy = false;
            loading = false;
            apply.disabled = false;
            const pending = out.querySelector('[data-catalog-source-saving="true"]');
            if (pending) pending.textContent = "Apply the source to use this catalog URL.";
          });
          out.append(field("Custom catalog URL", url), apply);
        }
        if (sourceBusy) {
          const pending = status("Saving catalog source…");
          pending.dataset.catalogSourceSaving = "true";
          out.append(pending);
        }
        if (sourceError) out.append(status(sourceError, BAD), button("Retry source", configureSource));
        return out;
      };
      const draw = () => {
        const search = document.createElement("input");
        search.type = "search";
        search.dataset.fy = "core";
        search.style.cssText = inputStyle;
        search.setAttribute("aria-label", TEXT.searchLabel);
        fieldFocus(search);
        search.placeholder = TEXT.searchLabel;
        search.maxLength = 120;
        search.value = form.query;
        search.tabIndex = 0;
        const submit = () => { form.query = search.value.slice(0, 120).trim(); return load(false); };
        search.addEventListener("input", () => {
          form.query = search.value.slice(0, 120);
          serial++;
          if (loading) {
            loading = false;
            const pending = box.querySelector('[data-catalog-loading="true"]');
            if (pending) pending.textContent = "Press Search to find modules.";
          }
        });
        search.addEventListener("keydown", ev => {
          if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); submit(); }
        });
        const filters = button(TEXT.filters, () => { filterOpen = !filterOpen; draw(); });
        filters.setAttribute("aria-expanded", String(filterOpen));
        const actions = row(button(TEXT.search, submit, { primary: true }), filters, button(TEXT.refresh, () => load(true)));
        actions.style.flexWrap = "wrap";
        const reset = () => { form.query = ""; form.category = "all"; form.sort = "name"; return load(false); };
        const filtered = !!(form.query || form.category !== "all");
        const list = tx("display:flex;flex-direction:column;margin-top:12px");
        if (loading) {
          const pending = status(TEXT.loading);
          pending.dataset.catalogLoading = "true";
          list.append(pending);
        }
        else if (listError) list.append(status(TEXT.failed, BAD), col(BAD, listError, 18), row(button(TEXT.retry, () => load(true), { primary: true })));
        else if (!rows.length) list.append(col("#fff", filtered ? TEXT.empty : TEXT.none, 21, true), col(MUTED, filtered ? TEXT.emptyHint : TEXT.noneHint, 18), filtered && row(button(TEXT.reset, reset)) || tx(""));
        else {
          list.append(status(rows.length + (Number.isFinite(data.total) && data.total > rows.length ? " of " + data.total : "") + (deckyPage ? rows.length === 1 ? " plugin" : " plugins" : rows.length === 1 ? " module" : " modules")));
          rows.forEach(item => {
            const wrap = tx("padding:16px 0;border-bottom:1px solid #39394f;min-width:0");
            const title = col("#fff", item.name, 23, true);
            const details = button(detailName(item), () => open(item), { css: "flex:1;margin-top:10px" });
            put(wrap, title, col(UP, formatLabel(item), 16), col(MUTED, summary(item), 16), text(item.description, 600) && col("#fff", text(item.description, 600), 18), compat(item), !readOnly(item) && item.installed === true && col(GOOD, TEXT.installed, 17), !readOnly(item) && jobFor(item) && jobFor(item).pending && status(TEXT.installing, UP), details);
            list.append(wrap);
          });
          if (Number.isFinite(data.total) && data.total > rows.length) list.append(row(button("Load more", () => load(false, true))));
        }
        show(heading(deckyPage ? "Decky Store" : TEXT.title), deckyPage && col(MUTED, "Browse the upstream Decky Store. Framey is not affiliated with the Decky project.", 17), field(TEXT.searchLabel, search), actions, filterOpen && filterPanel(), filtered && row(button(TEXT.reset, reset)), col(MUTED, "Catalog: " + (text(object(data.source).label, 200) || sources.find(s => s.id === form.source)?.label || form.source), 17), snapshot(), list);
      };
      const load = (refresh, more) => {
        const seq = fresh("browse");
        const offset = more ? rows.length : 0;
        loading = true;
        listError = "";
        draw();
        return call("catalog_list", Object.assign({}, form, sourceArgs(), { offset, limit: 50, refresh: !!refresh })).then(r => {
          if (!scoped(seq)) return;
          loading = false;
          data = Object.assign({}, data, r);
          listError = r.error ? problem(r) : "";
          if (!r.error) {
            const incoming = (Array.isArray(r.rows) ? r.rows : []).slice(0, 50).map(normal).filter(Boolean);
            const unique = new Map((more ? rows : []).concat(incoming).map(item => [identity(item), item]));
            rows = [...unique.values()].slice(0, 500);
          }
          draw();
        }, err => {
          if (!scoped(seq)) return;
          loading = false;
          listError = problem(err);
          draw();
        });
      };
      const open = item => {
        const seq = fresh("details");
        selected = item;
        inspection = { version: "", pending: false, error: "", result: null };
        reviewed = false;
        detailError = "";
        show(heading(item.name), row(button(TEXT.back, back)), status(TEXT.detailsLoading));
        return call("catalog_details", Object.assign({ id: item.id }, sourceArgs(item))).then(r => {
          if (!scoped(seq)) return;
          if (r.error) detailError = problem(r);
          else {
            const entry = normal(r.entry);
            if (!entry || entry.id !== item.id) detailError = "Module details were not returned. Retry before installing.";
            else {
              entry._catalogSource = item._catalogSource;
              entry._catalogURL = item._catalogURL;
              selected = entry;
              rows = rows.map(row => identity(row) === identity(item) ? entry : row);
              reviewed = typeof entry.identity === "string" && !!entry.identity;
            }
            if (r.stale === true) data.stale = true;
            if (r.origin) data.origin = r.origin;
            if (r.warning) data.warning = r.warning;
          }
          details();
        }, err => {
          if (!scoped(seq)) return;
          detailError = problem(err);
          details();
        });
      };
      const details = () => {
        const item = selected;
        if (!item) return back();
        if (decky(item)) restoreInspection(item);
        const job = jobFor(item);
        const install = !readOnly(item) && button(installLabel(item), review, { primary: true, disabled: blocked(item) || !!detailError || !reviewed });
        const description = col("#fff", text(item.description, 12000) || TEXT.noDescription, 19);
        const permissions = Array.isArray(item.permissions) ? item.permissions.slice(0, 64).map(p => text(p, 300)).filter(Boolean).join("\n") : text(item.permissions, 4000);
        const source = object(item.source);
        show(heading(item.name), row(button(TEXT.back, back)), snapshot(), col(UP, formatLabel(item), 18), col(MUTED, summary(item), 17), item.author && col(MUTED, "By " + text(item.author, 200), 17), compat(item), detailError && status(detailError, BAD), detailError && row(button(TEXT.retry, () => open(item))), col("#fff", TEXT.description, 21, true), description, Array.isArray(item.tags) && col(MUTED, item.tags.slice(0, 40).map(t => text(t, 100)).filter(Boolean).join(" · "), 17), col("#fff", TEXT.source, 21, true), col(MUTED, [text(object(data.source).label, 200), text(object(data.source).url, 2000), text(source.repository, 2000), text(source.url, 2000), source.commit && "Commit: " + text(source.commit, 200), source.path && "Path: " + text(source.path, 1000), text(item.homepage, 2000)].filter(Boolean).join("\n"), 17), permissions && col("#fff", TEXT.permissions, 21, true), permissions && col(MUTED, permissions, 18),
          readOnly(item) ? deckyDetails(item) : put(tx(""), installHint(item) && status(installHint(item), item.installed === true ? GOOD : WARN), !reviewed && !detailError && col(WARN, "A current catalog identity is required before installing. Refresh the catalog and retry.", 17), row(install), job && progress(job)));
      };
      const versions = item => {
        const values = (Array.isArray(item.versions) ? item.versions : []).slice(0, 100).map(object).filter(v => typeof v.name === "string" && v.name && typeof v.identity === "string" && v.identity && v.identity.length <= 512 && typeof v.hash === "string" && /^[a-f0-9]{64}$/i.test(v.hash));
        return [...new Map(values.map(v => [v.identity, v])).values()];
      };
      const restoreInspection = item => {
        const available = versions(item);
        let version = available.find(v => v.identity === inspection.version);
        if (!version) {
          version = available.find(v => v.name === item.version) || available[0];
          if (version) {
            const key = identity(item) + "\n" + version.identity;
            inspection = inspections.get(key) || { version: version.identity, pending: false, error: "", result: null };
          }
        }
        return version;
      };
      const versionInfo = version => put(tx(""),
        col(MUTED, "Version: " + text(version.name, 200), 18),
        text(version.hash, 128) && col(MUTED, "SHA-256: " + text(version.hash, 128), 16),
        text(version.created, 100) && col(MUTED, "Published: " + text(version.created, 100), 16),
        Number.isFinite(version.downloads) && version.downloads >= 0 && col(MUTED, "Downloads: " + Math.floor(version.downloads), 16),
        text(version.updates, 3000) && col("#fff", text(version.updates, 3000), 18),
      );
      const assessmentView = assessment => {
        const out = put(tx("margin-top:16px"), subheading("Archive inspection", 3), col(WARN, "Requires a Framey port", 20, true));
        if (text(assessment.label, 600)) out.append(col(MUTED, text(assessment.label, 600), 18));
        const architecture = {
          unknown: "ARM64 compatibility remains unverified.",
          aarch64: "AArch64 native binaries were found. This does not establish Framey compatibility.",
          x86_64: "x86-64 native binaries were found. These binaries cannot run natively on ARM64.",
          mixed: "Mixed native binary architectures were found. Review each binary before an ARM64 port.",
          other: "Other native binary architectures were found. ARM64 support remains unverified.",
        };
        out.append(col(assessment.architecture === "x86_64" || assessment.architecture === "mixed" ? WARN : MUTED, architecture[assessment.architecture] || architecture.unknown, 18));
        out.append(col(MUTED, "No code was executed. This inspection reads only the selected archive. Archive contents alone do not prove ARM64 or Framey compatibility.", 18));
        const details = [
          "Frontend: " + (assessment.has_frontend === true ? "present; Decky UI requires a Framey port" : assessment.has_frontend === false ? "not detected" : "unknown"),
          "Backend: " + (assessment.has_backend === true ? "present; review backend API and dependencies" : assessment.has_backend === false ? "not detected" : "unknown"),
          "Root access: " + (assessment.root_required === true ? "required by package metadata" : assessment.root_required === false ? "not declared by package metadata" : "unknown"),
          assessment.verified_sha256 === true ? "Archive SHA-256 verified." : "",
          Number.isFinite(assessment.bytes) && assessment.bytes >= 0 ? "Archive size: " + Math.floor(assessment.bytes) + " bytes" : "",
          Number.isFinite(assessment.files) && assessment.files >= 0 ? "Files inspected: " + Math.floor(assessment.files) : "",
        ].filter(Boolean);
        out.append(col(MUTED, details.join("\n"), 17));
        const sections = [["Porting assessment", assessment.reasons], ["Inspection limits and warnings", assessment.warnings], ["Flags", assessment.flags], ["Dependencies", assessment.dependencies]];
        sections.forEach(([title, values]) => {
          const lines = (Array.isArray(values) ? values : []).slice(0, 40).map(v => text(v, 500)).filter(Boolean);
          if (lines.length) out.append(subheading(title, 4), col(MUTED, lines.join("\n"), 18));
        });
        const binaries = (Array.isArray(assessment.native_binaries) ? assessment.native_binaries : []).slice(0, 60).map(object).map(binary => [text(binary.path, 300), text(binary.format, 40), text(binary.architecture, 80)].filter(Boolean).join(" · ")).filter(Boolean);
        if (binaries.length) out.append(subheading("Native binaries", 4), col(MUTED, binaries.join("\n"), 17));
        else out.append(col(MUTED, "No recognized native binaries were reported. Unsupported or nested content may still require review.", 17));
        return out;
      };
      const deckyDetails = item => {
        if (!decky(item)) return put(tx("margin-top:16px"), col(WARN, "Read-only entry", 22, true), col("#fff", text(item.reason, 600) || "This catalog entry is available for browsing only.", 19));
        const out = put(tx("margin-top:16px"), col(WARN, "Requires a Framey port", 22, true), col("#fff", "Decky plugins cannot be installed in Framey. Their frontend, backend APIs and runtime need a separate port.", 19));
        const available = versions(item);
        if (!available.length) {
          out.append(col(MUTED, "No inspectable versions are available for this entry.", 18));
          return out;
        }
        const version = restoreInspection(item);
        const choice = document.createElement("select");
        choice.dataset.fy = "core";
        choice.style.cssText = inputStyle;
        choice.setAttribute("aria-label", "Version");
        fieldFocus(choice);
        choice.tabIndex = 0;
        available.forEach(v => {
          const option = document.createElement("option");
          option.value = v.identity;
          option.textContent = text(v.name, 200);
          choice.append(option);
        });
        choice.value = version.identity;
        choice.addEventListener("change", () => {
          const key = identity(item) + "\n" + choice.value;
          inspection = inspections.get(key) || { version: choice.value, pending: false, error: "", result: null };
          details();
        });
        const inspect = button("Inspect compatibility", () => assess(item, version), { disabled: inspection.pending || !!detailError || !reviewed || offline() || version.inspectable === false });
        if (inspection.pending) {
          inspect.tabIndex = 0;
          inspect.addEventListener("keydown", ev => {
            if (ev.key !== "ArrowUp" && ev.key !== "ArrowLeft") return;
            ev.preventDefault();
            ev.stopPropagation();
            choice.focus({ preventScroll: true });
          });
        }
        out.append(subheading("Versions", 3), field("Version", choice), versionInfo(version), item.versions_truncated === true && col(MUTED, "The catalog contains more versions than are shown here.", 17) || tx(""), col(MUTED, "Inspect compatibility downloads only the selected archive for a bounded, read-only inspection. No plugin code runs.", 18), row(inspect));
        if (version.inspectable === false) out.append(col(WARN, "No supported package link is available for this version.", 18));
        if (inspection.pending) out.append(status("Inspecting archive…", UP));
        if (inspection.error) out.append(status("Inspection failed.", BAD), col(BAD, inspection.error, 18));
        if (inspection.result) out.append(assessmentView(inspection.result));
        if (offline()) out.append(col(WARN, "Refresh this catalog before inspecting an archive.", 17));
        return out;
      };
      const assess = (item, version) => {
        if (!decky(item) || view !== "details" || !selected || identity(selected) !== identity(item) || inspection.pending || inspection.version !== version.identity || version.inspectable === false || !reviewed || detailError || offline()) return;
        const current = inspection;
        const key = identity(item) + "\n" + version.identity;
        const available = versions(item);
        const defaultVersion = available.find(v => v.name === item.version) || available[0];
        const isDefault = defaultVersion && version.identity === defaultVersion.identity && version.hash === defaultVersion.hash;
        if (isDefault) delete item._latestAssessment;
        if (!inspections.has(key) && inspections.size >= 100) {
          for (const [oldKey, entry] of inspections) {
            if (!entry.pending) { inspections.delete(oldKey); break; }
          }
          if (inspections.size >= 100) {
            current.error = "Other archive inspections are still running. Wait for one to finish, then retry.";
            details();
            return;
          }
        }
        inspections.set(key, current);
        current.pending = true;
        current.error = "";
        current.result = null;
        details();
        const request = Object.assign({ id: item.id, identity: item.identity, version_identity: version.identity, confirm: true }, sourceArgs(item));
        return call("catalog_assess", request).then(r => {
          current.pending = false;
          const result = object(r.assessment);
          if (r.error) current.error = problem(r);
          else if (r.id !== item.id || r.format !== "decky" || r.read_only !== true || r.version !== version.name || r.hash !== version.hash || result.not_executed !== true || result.verified_sha256 !== true) current.error = "The inspection result could not be confirmed. Select Inspect compatibility to retry.";
          else {
            current.result = result;
            if (isDefault) item._latestAssessment = { entry_key: identity(item), version_identity: version.identity, hash: version.hash, assessment: result };
          }
          if (alive() && view === "details" && current === inspection && selected && identity(selected) === identity(item)) details();
        }, err => {
          current.pending = false;
          current.error = problem(err);
          if (alive() && view === "details" && current === inspection && selected && identity(selected) === identity(item)) details();
        });
      };
      const progress = job => {
        const out = tx("margin-top:14px");
        out.append(status(job.pending ? TEXT.installing : job.error ? TEXT.installFailed : TEXT.success, job.pending ? UP : job.error ? BAD : GOOD));
        if (job.message) out.append(col(job.error ? BAD : MUTED, job.message, 18));
        if (job.pending) out.append(col(MUTED, TEXT.ongoing, 17));
        return out;
      };
      const review = () => {
        const item = selected;
        if (!item || blocked(item) || detailError || !reviewed) return;
        fresh("confirm");
        const cancel = button(TEXT.cancel, () => { fresh("details"); details(); });
        show(heading(TEXT.review), col("#fff", item.name, 23, true), col(MUTED, summary(item), 18), col(MUTED, text(object(item.source).repository, 2000), 17), col(MUTED, "Commit: " + text(object(item.source).commit, 200), 17), compat(item), col("#fff", TEXT.risk, 19), row(button(TEXT.install, () => install(item), { primary: true }), cancel));
        cancel.focus();
      };
      const install = item => {
        if (view !== "confirm" || blocked(item)) return;
        const job = { pending: true, message: "", error: "" };
        if (jobs.size >= 128) {
          for (const [key, value] of jobs) {
            if (!value.pending) { jobs.delete(key); break; }
          }
        }
        jobs.set(identity(item), job);
        fresh("details");
        details();
        const request = Object.assign({ id: item.id, identity: item.identity, confirm: true }, sourceArgs(item));
        return call("catalog_install", request).then(r => {
          job.pending = false;
          job.error = r.error ? problem(r) : r.ok !== item.id ? "The installation result could not be confirmed. Refresh the catalog to check its state." : "";
          job.message = job.error || text(r.message, 600);
          if (active && active.settleJob) active.settleJob(identity(item), item);
        }, err => {
          job.pending = false;
          job.error = problem(err);
          job.message = job.error;
          if (active && active.settleJob) active.settleJob(identity(item), item);
        });
      };
      const configureSource = () => {
        serial++;
        sourceSerial++;
        sourceBusy = false;
        rows = [];
        data = {};
        loading = false;
        if (deckyPage) {
          try { sessionStorage.fyDeckyCatalogSource = form.source; } catch (e) {}
          sourceError = "";
          return load(false);
        }
        if (form.source === "custom" && !customURL) { sourceError = "Enter a custom catalog URL, then apply the source."; draw(); return; }
        sourceBusy = true;
        sourceError = "";
        const seq = sourceSerial;
        const requestedSource = form.source;
        const requestedURL = customURL;
        draw();
        return call("catalog_sources", sourceArgs()).then(r => {
          if (!alive() || seq !== sourceSerial || requestedSource !== form.source || requestedURL !== customURL) return;
          sourceBusy = false;
          if (r.error) { sourceError = problem(r); draw(); }
          else if (view === "browse") return load(false);
        }, err => { if (alive() && seq === sourceSerial) { sourceBusy = false; sourceError = problem(err); if (view === "browse") draw(); } });
      };
      state.settleJob = key => {
        if (!alive() || deckyPage) return;
        if (view === "details" && selected && identity(selected) === key) {
          const job = jobs.get(key);
          if (job && !job.error) open(selected);
          else details();
        } else if (view === "browse") load(false);
      };
      loading = true;
      draw();
      const boot = serial;
      call("catalog_sources", {}).then(r => {
        if (!scoped(boot)) return;
        const saved = object(r.source);
        const sourceList = deckyPage ? r.decky_sources : r.sources;
        if (Array.isArray(sourceList)) {
          const incoming = sourceList.slice(0, 20).map(object).filter(s => allowedSources.includes(s.id)).map(s => ({ id: s.id, label: text(s.label, 150) || s.id }));
          if (incoming.length === allowedSources.length) sources = [...new Map(incoming.map(s => [s.id, s])).values()];
        }
        if (!deckyPage && sources.some(s => s.id === saved.id)) form.source = saved.id;
        else if (!sources.some(s => s.id === form.source)) form.source = sources[0].id;
        customURL = text(r.custom_url, 2000) || (saved.id === "custom" ? text(saved.url, 2000) : "");
        if (r.error) sourceError = problem(r);
        load(false);
      }, () => { if (scoped(boot)) load(false); });
      return state.dispose;
  };
  const page = deckyPage => Object.freeze({
    render(box) { return render(box, deckyPage); },
    leave,
    dispose() {
      leave();
      window.removeEventListener("pagehide", leave);
    },
  });
  Object.defineProperty(FY.pages, "store", { value: page(false), enumerable: true });
  Object.defineProperty(FY.pages, "decky_store", { value: page(true), enumerable: true });
  window.addEventListener("pagehide", leave);
})();
