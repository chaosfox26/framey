(() => {
	const framey = __FY;
	const privateApi = framey && framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
	if (!privateApi || !privateApi.kit || (framey.pages && framey.pages.store)) return;
	const { tx, put, col, row, make, GOOD, BAD, UP, WARN } = privateApi.kit;
	const MUTED = "#b8b8cc";
	const TEXT = {
		title: "Store",
		search: "Search",
		searchLabel: "Search modules",
		refresh: "Refresh",
		filters: "Filters",
		reset: "Reset filters",
		category: "Category",
		source: "Source",
		sort: "Sort",
		all: "All",
		loading: "Loading modules…",
		detailsLoading: "Loading module details…",
		retry: "Retry",
		back: "Back",
		cancel: "Cancel",
		empty: "No modules match these filters.",
		emptyHint: "Try another search or reset the filters.",
		none: "No modules are available.",
		noneHint: "Refresh to check the catalog again.",
		failed: "The catalog could not be loaded.",
		noanswer: "No response was received.",
		offline: "Showing saved catalog data that may be out of date.",
		cached: "Showing saved catalog data.",
		offlineInstall: "Refresh this catalog before installing.",
		installed: "Installed",
		installing: "Installing…",
		install: "Install",
		review: "Review installation",
		risk: "This module and its backend can run code with your user permissions. Install only if you trust the source.",
		unknown: "Compatibility has not been confirmed.",
		compatible: "Compatible",
		incompatible: "Not compatible",
		success: "Installation complete.",
		installFailed: "Installation failed.",
		ongoing: "Installation continues when you leave this page.",
		description: "Description",
		permissions: "Permissions",
		noDescription: "No description is available.",
	};
	const text = (v, max = 600) => (typeof v === "string" ? v.slice(0, max) : "");
	const object = v => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
	const call = (name, arg) =>
		Promise.resolve()
			.then(() => privateApi.call(name, arg))
			.then(object);
	const problem = v => text(object(v).error, 600) || text(object(v).message, 600) || TEXT.noanswer;
	const jobs = new Map();
	let active = null;
	const leave = () => {
		if (active) active.dispose();
		active = null;
	};
	const render = box => {
		leave();
		const k = make(box);
		const windowLayout = !!(privateApi.singleWindow && privateApi.singleWindow());
		let readerLabel = "";
		const ordinaryKey = box.onkeydown;
		box.onkeydown = ev => {
			if (/^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName) && (!windowLayout || ev.key !== "Escape")) return;
			if (ev.target.closest("[data-catalog-reader]") && ev.key !== "Escape") return;
			ordinaryKey(ev);
		};
		let dead = false,
			serial = 0,
			view = "browse",
			selected = null,
			filterOpen = false;
		let screenIndex = 0,
			screenUnits = [],
			screenTitle = "",
			screenBack = null,
			resultOffset = 0,
			resultHistory = [];
		let data = {},
			rows = [],
			listError = "",
			detailError = "",
			loading = false,
			focusName = "";
		let listCode = "",
			detailCode = "",
			sourceCode = "",
			reportPending = false,
			reportError = "";
		const allowedSources = ["stable", "testing", "custom"];
		let sources = [
			{ id: "stable", label: "Stable" },
			{ id: "testing", label: "Testing" },
			{ id: "custom", label: "Custom" },
		];
		let customURL = "",
			sourceError = "",
			sourceBusy = false,
			reviewed = false,
			sourceSerial = 0;
		let form = {
			query: "",
			category: "all",
			source: "stable",
			sort: "name",
		};
		const state = {
			dispose() {
				if (dead) return;
				dead = true;
				serial++;
				sourceSerial++;
				box.onkeydown = null;
				if (active === state) privateApi.clearPageNavigation();
			},
		};
		active = state;
		const alive = () => !dead && active === state && box.isConnected;
		const scoped = n => alive() && n === serial;
		const fresh = name => {
			if (view !== name) {
				screenIndex = 0;
				readerLabel = "";
			}
			view = name;
			serial++;
			reportError = "";
			return serial;
		};
		const button = (label, fn, options) => {
			const nav = label === TEXT.back || label === TEXT.cancel;
			const e = k.btn(
				label,
				() => {
					if (alive()) return fn();
				},
				Object.assign({ nav }, options),
			);
			if (nav) e.dataset.fyEsc = "1";
			if (label.length > 32)
				e.textContent = label.startsWith("Details:")
					? "Open details"
					: label.startsWith("Update ")
						? "Update module"
						: label.startsWith("Install ")
							? "Install module"
							: label.slice(0, 28) + "…";
			e.style.cssText += ";min-height:48px;min-width:0;max-width:100%;overflow-wrap:anywhere";
			return e;
		};
		const status = (message, color = MUTED) => {
			const e = col(color, message, 18);
			e.setAttribute("role", "status");
			e.setAttribute("aria-live", "polite");
			return e;
		};
		const screenNodes = selector => {
			const nodes = [...box.querySelectorAll(selector)];
			for (const unit of screenUnits) {
				if (unit.node.matches(selector)) nodes.push(unit.node);
				nodes.push(...unit.node.querySelectorAll(selector));
			}
			return [...new Set(nodes)];
		};
		const syncReportControls = () => {
			if (!alive()) return;
			screenNodes('[data-catalog-report="true"]').forEach(control => {
				control.disabled = reportPending;
			});
			if (windowLayout && view === "browse") return draw();
			const existingError = screenUnits.some(unit => unit.label === "Report unavailable");
			if (reportError && !existingError) {
				screenIndex = 0;
				screen(screenTitle, [...copyUnits("Report unavailable", reportError, BAD), ...screenUnits], screenBack);
			} else if (!reportError && existingError) {
				screen(
					screenTitle,
					screenUnits.filter(unit => unit.label !== "Report unavailable"),
					screenBack,
				);
			}
		};
		const report = (item, error, code) => {
			if (reportPending || !alive()) return;
			const at = serial;
			if (typeof privateApi.report !== "function") {
				reportError = "Reporting is unavailable. Reopen Framey and try again.";
				syncReportControls();
				return;
			}
			const context = {
				plugin: {
					name: item ? text(item.name, 200) : "",
					version: item ? text(item.version, 200) : "",
					source: "framey",
				},
				error: text(error, 600),
				code: text(code, 80),
			};
			reportPending = true;
			reportError = "";
			syncReportControls();
			return Promise.resolve()
				.then(() => privateApi.report(context))
				.then(
					() => {
						reportPending = false;
						syncReportControls();
					},
					() => {
						reportPending = false;
						if (scoped(at)) reportError = "The report preview could not be opened. Try Report to Framey again.";
						syncReportControls();
					},
				);
		};
		const reportAction = (item, error = "", code = "") => {
			const control = button("Report to Framey", () => report(item, error, code), {
				disabled: reportPending,
			});
			control.dataset.catalogReport = "true";
			return control;
		};
		// Screens are composed from semantic copy and controls, never cloned DOM.
		const copyUnits = (label, values, color = MUTED) => {
			const units = [];
			for (const value of [].concat(values || [])) {
				if (value && typeof value !== "string" && value.getAttribute("role") === "heading") {
					units.push(actionUnit(label, value));
					continue;
				}
				const message = typeof value === "string" ? value : value && value.textContent;
				if (!message) continue;
				if (windowLayout) {
					const node = col(color, message, 18);
					node.style.cssText += ";white-space:pre-wrap;overflow-wrap:anywhere;margin:0;line-height:1.25";
					units.push({ label, node });
					continue;
				}
				// A compact step fits at most four 18px lines, including long URLs.
				const points = Array.from(message);
				for (let at = 0; at < points.length;) {
					let end = Math.min(at + 64, points.length);
					// Hard line breaks consume visual rows even in very short copy.
					// Keep each break (including CRLF) with its preceding text so
					// concatenating the reading steps preserves the exact content.
					for (let index = at; index < end; index++) {
						if (!/[\r\n\u2028\u2029]/.test(points[index])) continue;
						end = index + (points[index] === "\r" && points[index + 1] === "\n" ? 2 : 1);
						break;
					}
					const node = col(color, points.slice(at, end).join(""), 18);
					node.style.cssText += ";white-space:pre-wrap;overflow-wrap:anywhere;margin:0;line-height:1.25";
					units.push({ label, node });
					at = end;
				}
			}
			return units;
		};
		const actionUnit = (label, node) => ({ label, node });
		// Window routes own a single destination. Long copy is read contextually,
		// while compact hosts retain their existing small reading steps below.
		const windowShow = (...nodes) => {
			if (!alive()) return;
			const out = tx("display:flex;flex-direction:column;gap:10px;min-width:0;width:100%;box-sizing:border-box");
			out.dataset.catalogWindow = "true";
			put(out, ...nodes);
			k.rawShow(out);
			if (focusName) {
				const target = [...box.querySelectorAll("[aria-label]")].find(
					node => node.getAttribute("aria-label") === focusName,
				);
				if (target) target.focus({ preventScroll: true });
				focusName = "";
			}
		};
		const windowRow = (...nodes) =>
			put(tx("display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;min-width:0"), ...nodes);
		const readDetails = (units, done) => {
			const grouped = new Map();
			for (const unit of units) {
				if (
					unit.node.matches("[role='button'],button") ||
					unit.node.querySelector("input,select,textarea,[role='button'],button")
				)
					continue;
				grouped.set(
					unit.label,
					(grouped.has(unit.label) ? grouped.get(unit.label) + "\n\n" : "") + unit.node.textContent,
				);
			}
			if (!grouped.size) grouped.set("Details", "No information available.");
			if (!grouped.has(readerLabel)) readerLabel = grouped.keys().next().value;
			const choice = document.createElement("select");
			choice.dataset.fy = "core";
			choice.style.cssText = inputStyle;
			choice.setAttribute("aria-label", "Details to read");
			fieldFocus(choice);
			for (const label of grouped.keys()) {
				const option = document.createElement("option");
				option.value = option.textContent = label;
				choice.append(option);
			}
			choice.value = readerLabel;
			const full = document.createElement("pre");
			full.dataset.fy = "core";
			full.dataset.catalogReader = "true";
			full.style.cssText =
				inputStyle +
				";height:250px;min-height:0;resize:none;white-space:pre-wrap;overflow:auto;font-size:18px;line-height:1.35";
			full.setAttribute("aria-label", "Full details");
			full.setAttribute("role", "textbox");
			full.setAttribute("aria-readonly", "true");
			full.tabIndex = 0;
			full.textContent = grouped.get(readerLabel);
			fieldFocus(full);
			choice.addEventListener("change", () => {
				readerLabel = choice.value;
				full.textContent = grouped.get(readerLabel);
				full.scrollTop = 0;
			});
			const close = button("Done reading", () => {
				readerLabel = "";
				done();
			});
			close.dataset.fyEsc = "1";
			windowShow(field("Details to read", choice), full, close);
		};
		const windowScreen = () => {
			if (readerLabel) return readDetails(screenUnits, windowScreen);
			const actions = screenUnits.filter(
				unit => unit.node.matches("[role='button'],button") || unit.node.querySelector("input,select,textarea"),
			);
			const heading = col(
				"#fff",
				view === "confirm" ? TEXT.review : selected ? selected.name : "Module details",
				21,
				true,
			);
			heading.style.cssText += ";margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
			const identity = col(
				MUTED,
				selected ? (view === "confirm" ? selected.name + " · " : "") + summary(selected) : "",
				17,
			);
			identity.style.cssText += ";margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
			const read = button("Read details", () => {
				readerLabel = "Name";
				readDetails(screenUnits, windowScreen);
			});
			const nodes = [heading, identity];
			if (view === "confirm") {
				const risk = col("#fff", TEXT.risk, 17);
				risk.style.cssText += ";margin:0;line-height:1.3";
				nodes.push(risk);
			}
			if (detailError || reportError || (selected && jobFor(selected)?.error))
				nodes.push(col(BAD, "An action needs attention. Read details for the full message.", 17));
			else if (selected && jobFor(selected))
				nodes.push(status(jobFor(selected).pending ? TEXT.installing : TEXT.success));
			for (let at = 0; at < actions.length; at += 2)
				nodes.push(windowRow(...actions.slice(at, at + 2).map(unit => unit.node)));
			nodes.push(windowRow(read, screenBack && button(TEXT.back, screenBack)));
			windowShow(...nodes);
		};
		const screen = (title, units, onBack) => {
			if (!alive()) return;
			screenTitle = title.length > 32 ? "Module details" : title;
			screenUnits = units.filter(Boolean);
			screenBack = onBack || null;
			if (windowLayout) return windowScreen();
			const renderScreen = index => {
				if (!alive()) return;
				const size = privateApi.panelLayout() === "compact" ? 1 : 3;
				const groups = [];
				for (let at = 0; at < screenUnits.length; at += size) groups.push(screenUnits.slice(at, at + size));
				if (!groups.length) groups.push(copyUnits(title, "No information available."));
				screenIndex = Math.max(0, Math.min(index, groups.length - 1));
				const visible = groups[screenIndex];
				visible.forEach(unit => {
					unit.node.dataset.catalogStep = unit.label;
				});
				k.show(...visible.map(unit => unit.node));
				privateApi.setPageNavigation({
					index: screenIndex,
					count: groups.length,
					title: screenTitle,
					label: visible.every(unit => unit.label === visible[0].label) ? visible[0].label : "Catalog",
					onBack: screenBack,
					onPage: renderScreen,
					onLayout: () => renderScreen(screenIndex),
				});
			};
			if (focusName) {
				const at = screenUnits.findIndex(
					unit =>
						unit.node.getAttribute("aria-label") === focusName ||
						[...unit.node.querySelectorAll("[aria-label]")].some(node => node.getAttribute("aria-label") === focusName),
				);
				if (at >= 0) screenIndex = Math.floor(at / (privateApi.panelLayout() === "compact" ? 1 : 3));
			}
			renderScreen(screenIndex);
			if (focusName) {
				const target = [...box.querySelectorAll("[aria-label]")].find(
					node => node.getAttribute("aria-label") === focusName,
				);
				if (target) target.focus({ preventScroll: true });
				focusName = "";
			}
		};
		const field = (label, control) => {
			const holder = tx("display:flex;flex-direction:column;min-width:0");
			const caption = document.createElement("label");
			caption.style.cssText = "font-size:17px;color:" + MUTED;
			caption.textContent = label;
			caption.append(control);
			holder.append(caption);
			return holder;
		};
		const inputStyle = windowLayout
			? "box-sizing:border-box;width:100%;min-height:48px;border:1px solid #516071;border-radius:5px;background:#171f2a;color:#fff;font:18px sans-serif;padding:8px 10px;margin-top:4px;caret-color:#8fb0ff"
			: "box-sizing:border-box;width:100%;min-height:48px;border:2px solid #70708b;border-radius:12px;background:#1a1a28;color:#fff;font:20px sans-serif;padding:10px 12px;margin-top:6px;caret-color:#8fb0ff";
		const fieldFocus = e => {
			e.addEventListener("focus", () => {
				e.style.outline = "4px solid #8fb0ff";
				e.style.outlineOffset = "2px";
			});
			e.addEventListener("blur", () => {
				e.style.outline = "";
				e.style.outlineOffset = "";
			});
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
				focusName = label;
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
		const identity = item =>
			[
				item._catalogSource || form.source,
				item._catalogSource === "custom" ? item._catalogURL || "" : "",
				text(object(item.source).repository, 1000),
				text(item.id, 200),
				typeof item.identity === "string" ? item.identity : "",
			].join("\n");
		const jobFor = item => jobs.get(identity(item));
		const readOnly = item => item.read_only === true;
		const formatLabel = () => "Framey-native module";
		const readOnlyDetails = item =>
			copyUnits("Read-only entry", text(item.reason, 600) || "This catalog entry is available for browsing only.");
		const compat = item => {
			const out = tx("margin-top:8px");
			const declared = object(item.compatibility);
			const cm = object(item.compat);
			const badge = text(cm.badge || cm.state, 40);
			let verdict = declared.status;
			let reason = text(declared.reason, 600);
			if (offline()) {
				verdict = "unverified";
				reason = "This catalog may be out of date. Refresh it before checking current compatibility.";
			} else if (
				verdict === "compatible" &&
				(item.platform_declared !== true || declared.basis !== "declared_requirements")
			) {
				verdict = "unverified";
				reason = "Platform support is not declared clearly enough to check this module on your headset.";
			} else if (!["compatible", "incompatible", "unverified"].includes(verdict)) {
				if (
					cm.state === "incompatible" ||
					["platform", "api", "incompatible", "blocked", "newer", "older"].includes(badge)
				)
					verdict = "incompatible";
				else if ((badge === "ok" || badge === "compatible") && item.platform_declared === true) verdict = "compatible";
				else verdict = "unverified";
				reason =
					verdict === "unverified"
						? "Platform or compatibility requirements are missing. ARM64 compatibility has not been checked."
						: text(cm.msg || cm.message, 600);
			}
			const label =
				verdict === "compatible" ? "Compatible" : verdict === "incompatible" ? "Not compatible" : "Not checked";
			const color = verdict === "compatible" ? GOOD : verdict === "incompatible" ? WARN : MUTED;
			put(
				out,
				col(color, label, 21, true),
				col(
					"#fff",
					reason ||
						(verdict === "compatible"
							? "Meets the module’s declared requirements."
							: verdict === "incompatible"
								? "The module’s requirements do not match this device."
								: "There is not enough compatibility information to check this module."),
					18,
				),
			);
			if (verdict === "compatible")
				out.append(col(MUTED, "Meets declared Framey, API and platform requirements. This is not a runtime test.", 16));
			return out;
		};
		const summary = item =>
			[text(item.category, 100), text(item.channel, 100), item.version && "v" + text(item.version, 100)]
				.filter(Boolean)
				.join(" · ");
		const updatable = item => item.installed === true && item.update_available === true && item.eligible === true;
		const blocked = item =>
			readOnly(item) ||
			(item.installed === true && !updatable(item)) ||
			item.eligible !== true ||
			offline() ||
			!!(jobFor(item) && jobFor(item).pending);
		const installLabel = item =>
			jobFor(item) && jobFor(item).pending
				? TEXT.installing
				: updatable(item)
					? "Update " + item.name
					: item.installed === true
						? TEXT.installed
						: TEXT.install + " " + item.name;
		const installHint = item =>
			jobFor(item) && jobFor(item).pending
				? TEXT.ongoing
				: offline()
					? TEXT.offlineInstall
					: item.eligible !== true
						? text(item.reason, 600) || (item.installed === true ? TEXT.installed : TEXT.incompatible)
						: updatable(item)
							? "An update is available."
							: item.installed === true
								? TEXT.installed
								: "";
		const detailName = item => "Details: " + item.name;
		const normal = value => {
			const it = object(value);
			if (it.format === "decky" || it.kind === "decky") return null;
			const id = text(it.id, 200);
			if (!id) return null;
			return Object.assign({}, it, {
				id,
				name: text(it.name, 200) || id,
				_catalogSource: form.source,
				_catalogURL: form.source === "custom" ? customURL : "",
			});
		};
		const snapshot = item =>
			put(
				tx(""),
				data.origin === "bundled"
					? status("Showing the bundled catalog. Refresh to check for current releases.", WARN)
					: data.origin === "cache" || data.stale === true
						? status(data.stale === true ? TEXT.offline : TEXT.cached, WARN)
						: null,
				text(data.warning, 600) && col(WARN, text(data.warning, 600), 17),
				text(data.fetch_error, 600) && data.fetch_error !== data.warning && col(WARN, text(data.fetch_error, 600), 17),
				!item && text(data.fetch_error, 600) && row(reportAction(null, data.fetch_error)),
				text(data.fetch_error, 600) &&
					Number.isFinite(data.retry_after) &&
					data.retry_after > 0 &&
					col(MUTED, "Try again after: " + new Date(data.retry_after * 1000).toLocaleString(), 17),
				Number.isFinite(data.downloaded_at) &&
					data.downloaded_at > 0 &&
					col(MUTED, "Catalog saved: " + new Date(data.downloaded_at * 1000).toLocaleString(), 16),
				Number.isFinite(data.checked_at) &&
					data.checked_at > 0 &&
					col(MUTED, "Last checked: " + new Date(data.checked_at * 1000).toLocaleString(), 16),
			);
		const back = () => {
			fresh("browse");
			focusName = selected ? detailName(selected) : TEXT.search;
			draw();
		};
		const filterPanel = () => {
			const out = put(
				tx("display:flex;flex-direction:column;gap:12px"),
				select(TEXT.category, "category", [
					["all", TEXT.all],
					...names(data.categories, rows, "category").filter(v => v[0] !== "all"),
				]),
				select(
					TEXT.source,
					"source",
					sources.map(s => [s.id, s.label]),
				),
				select(TEXT.sort, "sort", [
					["name", "Name"],
					["updated", "Recently updated"],
					["relevance", "Relevance"],
				]),
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
					const pending = screenNodes('[data-catalog-source-saving="true"]')[0];
					if (pending) pending.textContent = "Apply the source to use this catalog URL.";
				});
				out.append(field("Custom catalog URL", url), apply);
			}
			if (sourceBusy) {
				const pending = status("Saving catalog source…");
				pending.dataset.catalogSourceSaving = "true";
				out.append(pending);
			}
			if (sourceError)
				out.append(
					status(sourceError, BAD),
					button("Retry source", configureSource),
					reportAction(null, sourceError, sourceCode),
				);
			return out;
		};
		let matchPosition = 0;
		const windowBrowse = (search, submit, filters, reset) => {
			if (filterOpen) {
				const panel = filterPanel();
				const controls = [...panel.children].filter(
					node => node.querySelector("input,select") || node.matches("[role='button'],button"),
				);
				const done = button("Done filtering", () => {
					filterOpen = false;
					focusName = TEXT.filters;
					draw();
				});
				done.dataset.fyEsc = "1";
				const nodes = [];
				for (let at = 0; at < controls.length; at += 2) nodes.push(windowRow(...controls.slice(at, at + 2)));
				if (sourceBusy) {
					const pending = status("Saving catalog source…");
					pending.dataset.catalogSourceSaving = "true";
					nodes.push(pending);
				}
				if (sourceError) {
					nodes.push(
						button("Read source error", () =>
							readDetails(
								[...copyUnits("Source error", sourceError, BAD), ...copyUnits("Report unavailable", reportError, BAD)],
								draw,
							),
						),
					);
				}
				nodes.push(windowRow(button(TEXT.reset, reset), done));
				windowShow(...nodes);
				return;
			}
			const info = [
				...copyUnits(
					"Catalog source",
					"Catalog: " +
						(text(object(data.source).label, 200) || sources.find(s => s.id === form.source)?.label || form.source),
				),
				...copyUnits("Catalog source", [...snapshot().children]),
			];
			if (reportError) info.push(...copyUnits("Report unavailable", reportError, BAD));
			if (listError) info.push(...copyUnits("Catalog error", [TEXT.failed, listError], BAD));
			const nodes = [search, windowRow(button(TEXT.search, submit, { primary: true }), filters)];
			if (loading) {
				const pending = status(TEXT.loading);
				pending.dataset.catalogLoading = "true";
				nodes.push(pending);
			} else if (listError)
				nodes.push(
					status(TEXT.failed, BAD),
					windowRow(
						button(TEXT.retry, () => load(true)),
						reportAction(null, listError, listCode),
					),
				);
			else if (!rows.length) nodes.push(status(form.query || form.category !== "all" ? TEXT.empty : TEXT.none));
			else {
				const total = Number.isFinite(data.total) ? data.total : rows.length;
				const visible = box.clientWidth > 0 && box.clientWidth < 650 ? 2 : 3;
				matchPosition = Math.max(0, Math.min(matchPosition, Math.max(0, total - 1)));
				const matches = rows.slice(
					Math.max(0, matchPosition - resultOffset),
					Math.max(0, matchPosition - resultOffset) + visible,
				);
				const count = col(
					MUTED,
					"Showing " +
						matches.length +
						" of " +
						total +
						" matches. " +
						(total > matches.length
							? "Refine your search or filters to find more."
							: "Choose a match to read its details."),
					17,
				);
				count.style.cssText += ";margin:0;line-height:1.3";
				nodes.push(count);
				if (total > visible) {
					const range = document.createElement("input"),
						number = document.createElement("input");
					range.type = "range";
					number.type = "number";
					range.setAttribute("aria-label", "Browse matches");
					number.setAttribute("aria-label", "First match");
					for (const input of [range, number]) {
						input.min = "1";
						input.max = String(total);
						input.step = "1";
						input.value = String(matchPosition + 1);
						input.tabIndex = 0;
					}
					range.style.cssText = "width:100%;min-width:0;min-height:44px;accent-color:#5585ff";
					number.style.cssText = inputStyle + ";width:90px;min-width:0";
					const choose = (value, label) => {
						if (!Number.isFinite(value)) return;
						const position = Math.max(0, Math.min(total - 1, Math.round(value) - 1));
						if (position >= resultOffset && position < resultOffset + rows.length) {
							matchPosition = position;
							focusName = label;
							draw();
						} else load(false, Math.floor(position / 50) * 50, position, label);
					};
					range.oninput = () => {
						number.value = range.value;
					};
					range.onchange = () => choose(+range.value, "Browse matches");
					number.onchange = () => choose(+number.value, "First match");
					const position = windowRow(range, number);
					position.style.gridTemplateColumns = "minmax(0,1fr) 90px";
					nodes.push(position);
				}
				for (const item of matches) {
					const control = button(detailName(item), () => open(item));
					// The complete name remains the accessible label and in Read details.
					control.textContent = item.name;
					control.dataset.catalogResult = "true";
					control.style.cssText +=
						";display:block;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
					nodes.push(control);
				}
			}
			if (reportError) nodes.push(col(BAD, "The report could not be opened. Read Catalog details.", 17));
			if (offline()) nodes.push(col(WARN, "Saved catalog. Refresh before installing.", 17));
			const footer = windowRow(
				button(TEXT.refresh, () => load(true)),
				button("Catalog details", () => readDetails(info, draw)),
			);
			if (text(data.fetch_error, 600) && !listError) {
				footer.style.gridTemplateColumns = "repeat(3,minmax(0,1fr))";
				footer.append(reportAction(null, data.fetch_error));
			}
			nodes.push(footer);
			windowShow(...nodes);
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
			const submit = () => {
				form.query = search.value.slice(0, 120).trim();
				return load(false);
			};
			search.addEventListener("input", () => {
				form.query = search.value.slice(0, 120);
				serial++;
				if (loading) {
					loading = false;
					const pending = screenNodes('[data-catalog-loading="true"]')[0];
					if (pending) pending.textContent = "Press Search to find modules.";
				}
			});
			search.addEventListener("keydown", ev => {
				if (ev.key === "Enter" && !ev.isComposing) {
					ev.preventDefault();
					submit();
				}
			});
			const filters = button(TEXT.filters, () => {
				filterOpen = !filterOpen;
				focusName = filterOpen ? TEXT.category : TEXT.filters;
				draw();
			});
			filters.setAttribute("aria-expanded", String(filterOpen));
			const reset = () => {
				form.query = "";
				form.category = "all";
				form.sort = "name";
				return load(false);
			};
			if (windowLayout) return windowBrowse(search, submit, filters, reset);
			const filtered = !!(form.query || form.category !== "all");
			const units = [
				actionUnit("Search", field(TEXT.searchLabel, search)),
				actionUnit("Search", button(TEXT.search, submit, { primary: true })),
				actionUnit("Filters", filters),
				actionUnit(
					"Refresh",
					button(TEXT.refresh, () => load(true)),
				),
			];
			if (filterOpen) {
				const panel = filterPanel();
				for (const node of [...panel.children]) {
					if (
						node.dataset.catalogSourceSaving ||
						node.querySelector("input,select") ||
						node.getAttribute("role") === "button" ||
						node.tagName === "BUTTON"
					)
						units.push(actionUnit("Filters", node));
					else units.push(...copyUnits("Source", node));
				}
			}
			if (filtered) units.push(actionUnit("Filters", button(TEXT.reset, reset)));
			if (loading) {
				const pending = status(TEXT.loading);
				pending.dataset.catalogLoading = "true";
				units.push(actionUnit("Results", pending));
			} else if (listError)
				units.push(
					...copyUnits("Catalog error", [TEXT.failed, listError], BAD),
					actionUnit(
						"Catalog error",
						button(TEXT.retry, () => load(true), { primary: true }),
					),
					actionUnit("Catalog error", reportAction(null, listError, listCode)),
				);
			else if (!rows.length)
				units.push(
					...copyUnits("Results", [filtered ? TEXT.empty : TEXT.none, filtered ? TEXT.emptyHint : TEXT.noneHint]),
				);
			else {
				units.push(
					...copyUnits(
						"Results",
						resultOffset +
							1 +
							"–" +
							(resultOffset + rows.length) +
							" of " +
							(Number.isFinite(data.total) ? data.total : rows.length) +
							" modules",
					),
				);
				for (const item of rows) {
					if (item.name.length > 32) units.push(...copyUnits("Name", item.name, "#fff"));
					units.push(
						actionUnit(
							"Results",
							button(detailName(item), () => open(item)),
						),
					);
					units.push(...copyUnits("Module info", [formatLabel(item), summary(item), text(item.description, 600)]));
					units.push(...copyUnits("Module info", [...compat(item).children]));
					if (!readOnly(item) && item.installed === true) units.push(...copyUnits("Module info", TEXT.installed, GOOD));
					if (!readOnly(item) && jobFor(item) && jobFor(item).pending)
						units.push(...copyUnits("Module info", TEXT.installing, UP));
				}
				if (resultOffset > 0)
					units.push(
						actionUnit(
							"Results",
							button("Previous results", () => load(false, resultHistory.pop() || 0)),
						),
					);
				if (Number.isFinite(data.total) && data.total > resultOffset + rows.length)
					units.push(
						actionUnit(
							"Results",
							button("Next results", () => {
								resultHistory.push(resultOffset);
								return load(false, resultOffset + rows.length);
							}),
						),
					);
			}
			units.push(
				...copyUnits(
					"Catalog source",
					"Catalog: " +
						(text(object(data.source).label, 200) || sources.find(s => s.id === form.source)?.label || form.source),
				),
			);
			const provenance = snapshot();
			for (const node of [...provenance.children]) {
				if (node.querySelector("[data-catalog-report]")) units.push(actionUnit("Catalog source", node));
				else units.push(...copyUnits("Catalog source", node));
			}
			screen(TEXT.title, units);
		};
		const load = (refresh, offset = 0, position = offset, focus = "") => {
			const seq = fresh("browse");
			if (!offset) resultHistory = [];
			resultOffset = offset;
			matchPosition = position;
			screenIndex = 0;
			loading = true;
			listError = "";
			listCode = "";
			draw();
			return call(
				"catalog_list",
				Object.assign({}, form, sourceArgs(), { offset, limit: 50, refresh: !!refresh }),
			).then(
				r => {
					if (!scoped(seq)) return;
					loading = false;
					data = Object.assign({}, data, r);
					listError = r.error ? problem(r) : "";
					listCode = r.error ? text(r.code, 80) : "";
					if (!r.error) {
						const incoming = (Array.isArray(r.rows) ? r.rows : []).slice(0, 50).map(normal).filter(Boolean);
						const unique = new Map(incoming.map(item => [identity(item), item]));
						rows = [...unique.values()].slice(0, 512);
					}
					if (focus) focusName = focus;
					draw();
				},
				err => {
					if (!scoped(seq)) return;
					loading = false;
					listError = problem(err);
					listCode = text(object(err).code, 80);
					draw();
				},
			);
		};
		const open = item => {
			const seq = fresh("details");
			readerLabel = "";
			selected = item;
			reviewed = false;
			detailError = "";
			detailCode = "";
			screen(item.name, copyUnits("Details", TEXT.detailsLoading), back);
			return call("catalog_details", Object.assign({ id: item.id }, sourceArgs(item))).then(
				r => {
					if (!scoped(seq)) return;
					if (r.error) {
						detailError = problem(r);
						detailCode = text(r.code, 80);
					} else {
						const entry = normal(r.entry);
						if (!entry || entry.id !== item.id) {
							detailError = "Module details were not returned. Retry before installing.";
							detailCode = "invalid_details";
						} else {
							entry._catalogSource = item._catalogSource;
							entry._catalogURL = item._catalogURL;
							selected = entry;
							rows = rows.map(row => (identity(row) === identity(item) ? entry : row));
							reviewed = typeof entry.identity === "string" && !!entry.identity;
						}
						if (r.stale === true) data.stale = true;
						if (r.origin) data.origin = r.origin;
						if (typeof r.warning === "string") data.warning = r.warning;
						if (Object.prototype.hasOwnProperty.call(r, "fetch_error")) data.fetch_error = r.fetch_error;
						if (Object.prototype.hasOwnProperty.call(r, "retry_after")) data.retry_after = r.retry_after;
					}
					details();
				},
				err => {
					if (!scoped(seq)) return;
					detailError = problem(err);
					detailCode = text(object(err).code, 80);
					details();
				},
			);
		};
		const details = () => {
			const item = selected;
			if (!item) return back();
			const job = jobFor(item);
			const reportFailure = detailError || (job && job.error) || text(data.fetch_error, 600);
			const reportCode = detailError ? detailCode : job && job.error ? job.code : "";
			const units = [
				...copyUnits("Name", item.name, "#fff"),
				...copyUnits("Module", [formatLabel(item), summary(item), item.author && "By " + text(item.author, 200)]),
			];
			if (!readOnly(item))
				units.push(
					actionUnit(
						"Installation",
						button(installLabel(item), review, {
							primary: true,
							disabled: blocked(item) || !!detailError || !reviewed,
						}),
					),
				);
			if (detailError)
				units.push(
					...copyUnits("Details error", detailError, BAD),
					actionUnit(
						"Details error",
						button(TEXT.retry, () => open(item)),
					),
				);
			units.push(...copyUnits("Compatibility", [...compat(item).children]));
			if (readOnly(item)) units.push(...readOnlyDetails(item));
			else {
				units.push(
					...copyUnits("Installation", [
						installHint(item),
						!reviewed &&
							!detailError &&
							"A current catalog identity is required before installing. Refresh the catalog and retry.",
					]),
				);
				if (job) units.push(...copyUnits("Installation", [...progress(job).children]));
			}
			units.push(...copyUnits(TEXT.description, text(item.description, 12000) || TEXT.noDescription, "#fff"));
			if (Array.isArray(item.tags))
				units.push(
					...copyUnits(
						"Tags",
						item.tags
							.slice(0, 40)
							.map(t => text(t, 100))
							.filter(Boolean)
							.join(" · "),
					),
				);
			const source = object(item.source);
			units.push(
				...copyUnits(TEXT.source, [
					text(object(data.source).label, 200),
					text(object(data.source).url, 2000),
					text(source.repository, 2000),
					text(source.url, 2000),
					source.commit && "Commit: " + text(source.commit, 200),
					source.path && "Path: " + text(source.path, 1000),
					text(item.homepage, 2000),
				]),
			);
			const permissions = Array.isArray(item.permissions)
				? item.permissions
						.slice(0, 64)
						.map(p => text(p, 300))
						.filter(Boolean)
				: text(item.permissions, 4000);
			units.push(...copyUnits(TEXT.permissions, permissions));
			units.push(...copyUnits("Catalog source", [...snapshot(item).children]));
			units.push(actionUnit("Report", reportAction(item, reportFailure, reportCode)));
			screen(item.name, units, back);
		};
		const progress = job => {
			const out = tx("margin-top:14px");
			out.append(
				status(
					job.pending ? TEXT.installing : job.error ? TEXT.installFailed : TEXT.success,
					job.pending ? UP : job.error ? BAD : GOOD,
				),
			);
			if (job.message) out.append(col(job.error ? BAD : MUTED, job.message, 18));
			if (job.pending) out.append(col(MUTED, TEXT.ongoing, 17));
			return out;
		};
		const review = () => {
			const item = selected;
			if (!item || blocked(item) || detailError || !reviewed) return;
			fresh("confirm");
			const cancel = button(TEXT.cancel, () => {
				fresh("details");
				details();
			});
			focusName = TEXT.cancel;
			screen(
				TEXT.review,
				[
					...copyUnits("Review", [
						item.name,
						summary(item),
						text(object(item.source).repository, 2000),
						"Commit: " + text(object(item.source).commit, 200),
					]),
					...copyUnits("Compatibility", [...compat(item).children]),
					...copyUnits("Permissions warning", TEXT.risk, "#fff"),
					actionUnit(
						"Confirm installation",
						button(TEXT.install, () => install(item), { primary: true }),
					),
					actionUnit("Cancel installation", cancel),
				],
				() => {
					fresh("details");
					details();
				},
			);
		};
		const install = item => {
			if (view !== "confirm" || blocked(item)) return;
			const job = { pending: true, message: "", error: "" };
			if (jobs.size >= 128) {
				for (const [key, value] of jobs) {
					if (!value.pending) {
						jobs.delete(key);
						break;
					}
				}
			}
			jobs.set(identity(item), job);
			fresh("details");
			details();
			const request = Object.assign({ id: item.id, identity: item.identity, confirm: true }, sourceArgs(item));
			return call("catalog_install", request).then(
				r => {
					job.pending = false;
					job.error = r.error
						? problem(r)
						: r.ok !== item.id
							? "The installation result could not be confirmed. Refresh the catalog to check its state."
							: "";
					job.code = r.error ? text(r.code, 80) : r.ok !== item.id ? "unconfirmed_install" : "";
					job.message = job.error || text(r.message, 600);
					if (active && active.settleJob) active.settleJob(identity(item), item);
				},
				err => {
					job.pending = false;
					job.error = problem(err);
					job.code = text(object(err).code, 80);
					job.message = job.error;
					if (active && active.settleJob) active.settleJob(identity(item), item);
				},
			);
		};
		const configureSource = () => {
			serial++;
			sourceSerial++;
			sourceBusy = false;
			rows = [];
			data = {};
			loading = false;
			sourceCode = "";
			if (form.source === "custom" && !customURL) {
				sourceError = "Enter a custom catalog URL, then apply the source.";
				draw();
				return;
			}
			sourceBusy = true;
			sourceError = "";
			sourceCode = "";
			const seq = sourceSerial;
			const requestedSource = form.source;
			const requestedURL = customURL;
			draw();
			return call("catalog_sources", sourceArgs()).then(
				r => {
					if (!alive() || seq !== sourceSerial || requestedSource !== form.source || requestedURL !== customURL) return;
					sourceBusy = false;
					if (r.error) {
						sourceError = problem(r);
						sourceCode = text(r.code, 80);
						draw();
					} else if (view === "browse") return load(false);
				},
				err => {
					if (alive() && seq === sourceSerial) {
						sourceBusy = false;
						sourceError = problem(err);
						sourceCode = text(object(err).code, 80);
						if (view === "browse") draw();
					}
				},
			);
		};
		state.settleJob = key => {
			if (!alive()) return;
			if (view === "details" && selected && identity(selected) === key) {
				const job = jobs.get(key);
				if (job && !job.error) open(selected);
				else details();
			} else if (view === "browse") load(false);
		};
		loading = true;
		draw();
		const boot = serial;
		call("catalog_sources", {}).then(
			r => {
				if (!scoped(boot)) return;
				const saved = object(r.source);
				const sourceList = r.sources;
				if (Array.isArray(sourceList)) {
					const incoming = sourceList
						.slice(0, 20)
						.map(object)
						.filter(s => allowedSources.includes(s.id))
						.map(s => ({ id: s.id, label: text(s.label, 150) || s.id }));
					if (incoming.length === allowedSources.length) sources = [...new Map(incoming.map(s => [s.id, s])).values()];
				}
				if (sources.some(s => s.id === saved.id)) form.source = saved.id;
				else if (!sources.some(s => s.id === form.source)) form.source = sources[0].id;
				customURL = text(r.custom_url, 2000) || (saved.id === "custom" ? text(saved.url, 2000) : "");
				if (r.error) {
					sourceError = problem(r);
					sourceCode = text(r.code, 80);
				}
				load(false);
			},
			() => {
				if (scoped(boot)) load(false);
			},
		);
		return state.dispose;
	};
	const disposeCatalog = () => {
		leave();
		window.removeEventListener("pagehide", leave);
	};
	const page = Object.freeze({ render, leave, dispose: disposeCatalog });
	const renderDecky = box => {
		leave();
		let dead = false,
			handle = null;
		const state = {
			dispose() {
				if (dead) return;
				dead = true;
				if (handle) handle.dispose();
				privateApi.clearPageNavigation();
			},
		};
		active = state;
		const alive = () => !dead && active === state && box.isConnected;
		const k = make(box);
		const T = privateApi.kit.T;
		const unavailable = (reason, disabled = false) => {
			if (!alive()) return;
			const nodes = [
				col(WARN, T(disabled ? "s.decky_store_off" : "s.decky_store_unavailable")),
				col(MUTED, T(disabled ? "s.decky_explain" : "s.decky_store_recover"), 17),
			];
			if (reason) nodes.push(col(BAD, String(reason).slice(0, 300)));
			nodes.push(
				k.btn(T("s.decky_settings"), () => {
					framey.open("#settings:decky");
				}),
			);
			k.show(...nodes);
		};
		k.show(col(MUTED, TEXT.loading));
		call("decky_runtime_status", {})
			.then(async status => {
				if (!alive()) return;
				const reason = text(status.error, 300) || text(object(status.error).message, 300);
				if (status.enabled === false) return unavailable(reason, true);
				if (status.enabled !== true || status.installed !== true)
					return unavailable(reason || T("s.decky_unavailable"));
				const manager = privateApi.optionalRuntime;
				if (!manager || typeof manager.store !== "function") return unavailable(T("s.decky_unavailable"));
				const allowed = new Set([
					"catalog_sources",
					"catalog_list",
					"catalog_details",
					"catalog_assess",
					"catalog_import",
				]);
				const scopedCall = (name, arg) => {
					if (!alive() || !allowed.has(name)) return Promise.reject(new Error("Decky Store request is unavailable"));
					return privateApi.call(name, arg);
				};
				const context = {
					box,
					kit: { ...privateApi.kit, call: scopedCall },
					call: scopedCall,
					diagnostic: privateApi.diagnostic,
					report: privateApi.report,
					panelLayout: privateApi.panelLayout,
					singleWindow: privateApi.singleWindow,
					setPageNavigation: privateApi.setPageNavigation,
					clearPageNavigation: privateApi.clearPageNavigation,
					importEnabled: true,
					alive,
				};
				const value = await manager.store(context);
				if (!alive()) {
					if (value && typeof value.dispose === "function") value.dispose();
					return;
				}
				if (!value || typeof value.dispose !== "function") throw new Error(T("s.decky_unavailable"));
				handle = value;
			})
			.catch(error => unavailable((error && error.message) || error));
		return state.dispose;
	};
	Object.defineProperty(framey.pages, "store", { value: page, enumerable: true });
	Object.defineProperty(framey.pages, "decky_store", {
		value: Object.freeze({ render: renderDecky, leave, dispose: disposeCatalog }),
		enumerable: true,
	});
	window.addEventListener("pagehide", leave);
})();
