// Framey-owned optional Store UI. Loaded only from a verified admitted runtime bundle.
const frameyDeckyStoreImports = new Map();
let frameyDeckyStoreGeneration = null;
const frameyDeckyStoreOwner = { active: null };
function createFrameyDeckyStore(context) {
	if (!context || !context.kit || !context.alive || !context.alive())
		throw new Error("Decky Store view is no longer active");
	if (frameyDeckyStoreGeneration !== context.generation) {
		frameyDeckyStoreImports.clear();
		frameyDeckyStoreGeneration = context.generation;
	}
	const privateApi = context;
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
			.then(() => {
				if (!privateApi.alive()) throw new Error("Decky Store view is no longer active");
				return privateApi.call(name, arg);
			})
			.then(object);
	const problem = v => text(object(v).error, 600) || text(object(v).message, 600) || TEXT.noanswer;
	const jobs = new Map();
	const importJobs = frameyDeckyStoreImports;
	const recordStorageFailure = () => {
		try {
			if (typeof privateApi.diagnostic === "function") privateApi.diagnostic("core.storage_failed");
			return true;
		} catch (e) {
			return false;
		}
	};
	const leave = () => {
		if (frameyDeckyStoreOwner.active) frameyDeckyStoreOwner.active.dispose();
		frameyDeckyStoreOwner.active = null;
	};
	const render = (box, deckyPage) => {
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
			resultHistory = [],
			turnScreen = null;
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
		const allowedSources = deckyPage ? ["decky_stable", "decky_testing"] : ["stable", "testing", "custom"];
		let sources = deckyPage
			? [
					{ id: "decky_stable", label: "Decky Store" },
					{ id: "decky_testing", label: "Decky Store Testing" },
				]
			: [
					{ id: "stable", label: "Stable" },
					{ id: "testing", label: "Testing" },
					{ id: "custom", label: "Custom" },
				];
		let customURL = "",
			sourceError = "",
			sourceBusy = false,
			reviewed = false,
			sourceSerial = 0;
		let importReview = null;
		let inspection = { version: "", pending: false, error: "", result: null };
		const inspections = new Map();
		let rememberedSource = "decky_stable";
		if (deckyPage) {
			try {
				if (allowedSources.includes(sessionStorage.fyDeckyCatalogSource))
					rememberedSource = sessionStorage.fyDeckyCatalogSource;
			} catch (e) {
				recordStorageFailure();
			}
		}
		let form = {
			query: "",
			category: "all",
			source: deckyPage ? rememberedSource : "stable",
			sort: "name",
		};
		const state = {
			dispose() {
				if (dead) return;
				dead = true;
				serial++;
				sourceSerial++;
				box.onkeydown = null;
				if (frameyDeckyStoreOwner.active === state) privateApi.clearPageNavigation();
			},
		};
		frameyDeckyStoreOwner.active = state;
		const alive = () => !dead && frameyDeckyStoreOwner.active === state && box.isConnected && privateApi.alive();
		const scoped = n => alive() && n === serial;
		const fresh = name => {
			if (name !== "confirm") importReview = null;
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
		const subheading = (title, level) => {
			const h = col("#fff", title, level === 3 ? 22 : 21, true);
			h.setAttribute("role", "heading");
			h.setAttribute("aria-level", String(level));
			return h;
		};
		const importAvailable = () => privateApi.importEnabled === true && privateApi.current();
		const importLimit =
			"Partial compatibility profile. Installation does not mean full Steam plugin functionality. New imports only; upgrades are not supported.";
		const deckyDisclosure = () =>
			col(
				MUTED,
				importAvailable()
					? "Experimental compatibility; results vary. " +
							importLimit +
							" Framey is independent and is not affiliated with or endorsed by Decky Loader. Report Framey issues to Framey, not Decky or plugin authors."
					: "Experimental compatibility; results vary. Framey is independent and is not affiliated with or endorsed by Decky Loader. This tab only browses and inspects original packages; it cannot install or run them. Report Framey issues to Framey, not Decky or plugin authors.",
				17,
			);
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
			const selectedVersion =
				item && decky(item) ? versions(item).find(version => version.identity === inspection.version) : null;
			const source = item ? item._catalogSource : form.source;
			const context = {
				plugin: {
					name: item ? text(item.name, 200) : "",
					version: item ? text(selectedVersion ? selectedVersion.name : item.version, 200) : "",
					source: source === "decky_stable" || source === "decky_testing" ? source : "framey",
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
				view === "confirm"
					? importReview
						? "Review experimental import"
						: TEXT.review
					: selected
						? selected.name
						: "Module details",
				21,
				true,
			);
			heading.style.cssText += ";margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
			const identity = col(
				MUTED,
				importReview
					? "Selected version " + importReview.version + " · SHA-256 " + importReview.hash.slice(0, 12)
					: selected
						? (view === "confirm" ? selected.name + " · " : "") + summary(selected)
						: "",
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
				if (importReview) nodes.push(col(WARN, "Partial profile; full Steam plugin functionality is unverified.", 17));
			} else {
				if (deckyPage)
					nodes.push(
						col(
							WARN,
							importAvailable()
								? "Experimental import: partial compatibility; full Steam features unverified."
								: "Requires a Framey port. Inspect only; cannot install.",
							17,
						),
					);
				if (inspection.pending) nodes.push(status("Inspecting archive…", UP));
				else if (inspection.result) nodes.push(status("Inspection complete. Read details for the findings."));
			}
			if (detailError || inspection.error || reportError || (selected && jobFor(selected)?.error))
				nodes.push(col(BAD, "An action needs attention. Read details for the full message.", 17));
			else if (selected && jobFor(selected))
				nodes.push(
					status(
						jobFor(selected).kind === "decky-import"
							? jobFor(selected).pending
								? "Importing…"
								: "Import complete."
							: jobFor(selected).pending
								? TEXT.installing
								: TEXT.success,
					),
				);
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
			turnScreen = renderScreen;
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
		const importIdentity = (item, version) => "decky-import\n" + identity(item) + "\n" + version;
		const jobFor = item =>
			decky(item) ? importJobs.get(importIdentity(item, inspection.version)) : jobs.get(identity(item));
		const decky = item =>
			deckyPage ||
			item.format === "decky" ||
			item.kind === "decky" ||
			item._catalogSource === "decky_stable" ||
			item._catalogSource === "decky_testing";
		const readOnly = item => decky(item) || item.read_only === true;
		const formatLabel = item => (decky(item) ? "Decky plugin" : "Framey-native module");
		const compat = item => {
			const out = tx("margin-top:8px");
			if (decky(item)) {
				put(
					out,
					col(WARN, importAvailable() ? "Experimental profile" : "Not compatible", 21, true),
					col(
						"#fff",
						importAvailable()
							? "Original Decky package; partial Framey compatibility is available. Full Steam features are not verified."
							: "Built for Decky’s interface; it needs a Framey version before it can run here.",
						18,
					),
				);
				const inspectingSelection = selected === item && view === "details";
				const cached = object(item._latestAssessment);
				const defaultVersion = versions(item).find(v => v.name === item.version) || versions(item)[0];
				const bound =
					defaultVersion &&
					cached.entry_key === identity(item) &&
					cached.version_identity === defaultVersion.identity &&
					cached.hash === defaultVersion.hash;
				const evidence = inspectingSelection ? inspection.result : bound ? cached.assessment : null;
				const assessment = object(evidence);
				const binaries = Array.isArray(assessment.native_binaries) ? assessment.native_binaries : [];
				const x86 =
					assessment.architecture === "x86_64" || binaries.some(binary => object(binary).architecture === "x86_64");
				const arm =
					assessment.architecture === "aarch64" || binaries.some(binary => object(binary).architecture === "aarch64");
				const mixed = assessment.architecture === "mixed" || (x86 && arm);
				if (evidence && inspectingSelection) {
					const version = versions(item).find(v => v.identity === inspection.version);
					if (version)
						out.append(
							col(
								MUTED,
								"Selected version " + text(version.name, 200) + " · SHA-256 " + text(version.hash, 12),
								17,
								true,
							),
						);
				}
				if (!mixed && !arm && (assessment.architecture_status === "incompatible" || x86))
					put(
						out,
						col(WARN, "Needs an ARM64 build", 19, true),
						col("#fff", "Includes a program built for Intel/AMD processors; your headset needs an ARM64 build.", 18),
					);
				else {
					const failed = selected === item && view === "details" && inspection.error;
					const reason = failed
						? "The archive inspection failed: " + inspection.error
						: offline()
							? "This catalog may be out of date. Refresh it before inspecting an archive for ARM64 support."
							: mixed
								? "This archive contains mixed native architectures. A manual porting review is needed to determine which programs it uses on ARM64."
								: arm
									? "ARM64 binaries were found, but running this plugin in Framey has not been verified."
									: "ARM64 compatibility is unverified. The catalog alone cannot confirm support for your headset.";
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
			if (deckyPage) info.push(...copyUnits("About Decky", deckyDisclosure()));
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
			if (deckyPage) nodes.push(col(MUTED, "Independent of Decky. Compatibility varies; Framey support only.", 17));
			if (offline()) nodes.push(col(WARN, "Saved catalog. Refresh before installing or inspecting.", 17));
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
							(deckyPage ? " plugins" : " modules"),
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
			if (deckyPage) units.push(...copyUnits("About Decky", deckyDisclosure()));
			screen(deckyPage ? "Decky Store" : TEXT.title, units);
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
			inspection = { version: "", pending: false, error: "", result: null };
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
			if (decky(item)) restoreInspection(item);
			const focused = document.activeElement && document.activeElement.getAttribute("aria-label");
			if (focused && ["Version", "Inspect compatibility"].includes(focused)) focusName = focused;
			const job = jobFor(item);
			const reportFailure = detailError || inspection.error || (job && job.error) || text(data.fetch_error, 600);
			const reportCode = detailError
				? detailCode
				: inspection.error
					? inspection.code
					: job && job.error
						? job.code
						: "";
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
			if (readOnly(item)) units.push(...deckyDetails(item));
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
			if (decky(item)) units.push(...copyUnits("About Decky", deckyDisclosure()));
			units.push(actionUnit("Report", reportAction(item, reportFailure, reportCode)));
			screen(item.name, units, back);
		};
		const versions = item => {
			const values = (Array.isArray(item.versions) ? item.versions : [])
				.slice(0, 100)
				.map(object)
				.filter(
					v =>
						typeof v.name === "string" &&
						v.name &&
						typeof v.identity === "string" &&
						v.identity &&
						v.identity.length <= 512 &&
						typeof v.hash === "string" &&
						/^[a-f0-9]{64}$/i.test(v.hash),
				);
			return [...new Map(values.map(v => [v.identity, v])).values()];
		};
		const restoreInspection = item => {
			const available = versions(item);
			let version = available.find(v => v.identity === inspection.version);
			if (!version) {
				version = available.find(v => v.name === item.version) || available[0];
				if (version) {
					const key = identity(item) + "\n" + version.identity;
					inspection = inspections.get(key) || {
						version: version.identity,
						pending: false,
						error: "",
						result: null,
					};
				}
			}
			return version;
		};
		const versionInfo = version =>
			put(
				tx(""),
				col(MUTED, "Version: " + text(version.name, 200), 18),
				text(version.hash, 128) && col(MUTED, "SHA-256: " + text(version.hash, 128), 16),
				text(version.created, 100) && col(MUTED, "Published: " + text(version.created, 100), 16),
				Number.isFinite(version.downloads) &&
					version.downloads >= 0 &&
					col(MUTED, "Downloads: " + Math.floor(version.downloads), 16),
				text(version.updates, 3000) && col("#fff", text(version.updates, 3000), 18),
			);
		const assessmentView = assessment => {
			const out = put(
				tx("margin-top:16px"),
				subheading("Archive inspection", 3),
				deckyDisclosure(),
				col(
					WARN,
					importAvailable() ? "Inspection does not verify runtime compatibility" : "Requires a Framey port",
					20,
					true,
				),
			);
			if (text(assessment.label, 600))
				out.append(
					col(
						MUTED,
						importAvailable() && assessment.state === "port_required"
							? "Original Decky runtime detected; experimental profile support remains unverified."
							: text(assessment.label, 600),
						18,
					),
				);
			const architecture = {
				unknown: "ARM64 compatibility remains unverified.",
				aarch64: "AArch64 native binaries were found. This does not establish Framey compatibility.",
				x86_64: "x86-64 native binaries were found. These binaries cannot run natively on ARM64.",
				mixed: "Mixed native binary architectures were found. Review each binary before an ARM64 port.",
				other: "Other native binary architectures were found. ARM64 support remains unverified.",
			};
			out.append(
				col(
					assessment.architecture === "x86_64" || assessment.architecture === "mixed" ? WARN : MUTED,
					architecture[assessment.architecture] || architecture.unknown,
					18,
				),
			);
			out.append(
				col(
					MUTED,
					"No code was executed. This inspection reads only the selected archive. Archive contents alone do not prove ARM64 or Framey compatibility.",
					18,
				),
			);
			const details = [
				"Frontend: " +
					(assessment.has_frontend === true
						? importAvailable()
							? "present; original Decky UI; experimental profile support is unverified"
							: "present; Decky UI requires a Framey port"
						: assessment.has_frontend === false
							? "not detected"
							: "unknown"),
				"Backend: " +
					(assessment.has_backend === true
						? "present; review backend API and dependencies"
						: assessment.has_backend === false
							? "not detected"
							: "unknown"),
				"Root access: " +
					(assessment.root_required === true
						? "required by package metadata"
						: assessment.root_required === false
							? "not declared by package metadata"
							: "unknown"),
				assessment.verified_sha256 === true ? "Archive SHA-256 verified." : "",
				Number.isFinite(assessment.bytes) && assessment.bytes >= 0
					? "Archive size: " + Math.floor(assessment.bytes) + " bytes"
					: "",
				Number.isFinite(assessment.files) && assessment.files >= 0
					? "Files inspected: " + Math.floor(assessment.files)
					: "",
			].filter(Boolean);
			out.append(col(MUTED, details.join("\n"), 17));
			const sections = [
				["Porting assessment", assessment.reasons],
				["Inspection limits and warnings", assessment.warnings],
				["Flags", assessment.flags],
				["Dependencies", assessment.dependencies],
			];
			sections.forEach(([title, values]) => {
				const lines = (Array.isArray(values) ? values : [])
					.slice(0, 40)
					.map(v => text(v, 500))
					.map(line =>
						importAvailable() &&
						line === "Built for Decky’s interface; it needs a Framey version before it can run here."
							? "Original frontend targets Decky’s interface and loader APIs; the experimental profile does not verify full Steam functionality."
							: line,
					)
					.filter(Boolean);
				if (lines.length) out.append(subheading(title, 4), col(MUTED, lines.join("\n"), 18));
			});
			const binaries = (Array.isArray(assessment.native_binaries) ? assessment.native_binaries : [])
				.slice(0, 60)
				.map(object)
				.map(binary =>
					[text(binary.path, 300), text(binary.format, 40), text(binary.architecture, 80)].filter(Boolean).join(" · "),
				)
				.filter(Boolean);
			if (binaries.length) out.append(subheading("Native binaries", 4), col(MUTED, binaries.join("\n"), 17));
			else
				out.append(
					col(
						MUTED,
						"No recognized native binaries were reported. Unsupported or nested content may still require review.",
						17,
					),
				);
			return out;
		};
		const deckyDetails = item => {
			if (!decky(item))
				return copyUnits(
					"Read-only entry",
					text(item.reason, 600) || "This catalog entry is available for browsing only.",
				);
			const units = copyUnits(
				importAvailable() ? "Experimental import" : importAvailable() ? "Experimental import" : "Framey port required",
				importAvailable()
					? importLimit
					: "Requires a Framey port. Decky plugins cannot be installed in Framey. Their frontend, backend APIs and runtime need a separate port.",
			);
			const available = versions(item);
			if (!available.length) {
				units.push(...copyUnits("Versions", "No inspectable versions are available for this entry."));
				return units;
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
				if (!alive() || view !== "details" || selected !== item) return;
				importReview = null;
				const key = identity(item) + "\n" + choice.value;
				inspection = inspections.get(key) || {
					version: choice.value,
					pending: false,
					error: "",
					result: null,
				};
				details();
			});
			const inspect = button("Inspect compatibility", () => assess(item, version), {
				disabled: inspection.pending || !!detailError || !reviewed || offline() || version.inspectable === false,
			});
			if (inspection.pending) {
				inspect.tabIndex = 0;
				inspect.addEventListener("keydown", ev => {
					if (ev.key !== "ArrowUp" && ev.key !== "ArrowLeft") return;
					ev.preventDefault();
					ev.stopPropagation();
					const at = screenUnits.findIndex(unit => unit.node.contains(choice));
					if (at >= 0 && turnScreen) turnScreen(Math.floor(at / (privateApi.panelLayout() === "compact" ? 1 : 3)));
					choice.focus({ preventScroll: true });
				});
			}
			units.push(actionUnit("Version", field("Version", choice)), actionUnit("Inspection", inspect));
			if (importAvailable()) {
				units.push(
					actionUnit(
						"Experimental import",
						button("Review experimental import", () => reviewImport(item, version), {
							primary: true,
							disabled: importBlocked(item, version),
						}),
					),
				);
				const job = jobFor(item);
				if (job) units.push(...copyUnits("Experimental import", [...progress(job).children]));
			}
			units.push(...copyUnits("Version", [...versionInfo(version).children]));
			if (item.versions_truncated === true)
				units.push(...copyUnits("Versions", "The catalog contains more versions than are shown here."));
			units.push(
				...copyUnits(
					"Inspection",
					"Inspect compatibility downloads only the selected archive for a bounded, read-only inspection. No plugin code runs.",
				),
			);
			if (version.inspectable === false)
				units.push(...copyUnits("Inspection", "No supported package link is available for this version.", WARN));
			if (inspection.pending) units.push(...copyUnits("Inspection", "Inspecting archive…", UP));
			if (inspection.error) units.push(...copyUnits("Inspection", ["Inspection failed.", inspection.error], BAD));
			if (inspection.result)
				units.push(...copyUnits("Archive inspection", [...assessmentView(inspection.result).children]));
			if (offline()) units.push(...copyUnits("Inspection", "Refresh this catalog before inspecting an archive.", WARN));
			return units;
		};

		const assess = (item, version) => {
			if (
				!decky(item) ||
				view !== "details" ||
				!selected ||
				identity(selected) !== identity(item) ||
				inspection.pending ||
				inspection.version !== version.identity ||
				version.inspectable === false ||
				!reviewed ||
				detailError ||
				offline()
			)
				return;
			const current = inspection;
			const key = identity(item) + "\n" + version.identity;
			const available = versions(item);
			const defaultVersion = available.find(v => v.name === item.version) || available[0];
			const isDefault =
				defaultVersion && version.identity === defaultVersion.identity && version.hash === defaultVersion.hash;
			if (isDefault) delete item._latestAssessment;
			if (!inspections.has(key) && inspections.size >= 100) {
				for (const [oldKey, entry] of inspections) {
					if (!entry.pending) {
						inspections.delete(oldKey);
						break;
					}
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
			current.code = "";
			current.result = null;
			details();
			const request = Object.assign(
				{ id: item.id, identity: item.identity, version_identity: version.identity, confirm: true },
				sourceArgs(item),
			);
			return call("catalog_assess", request).then(
				r => {
					current.pending = false;
					const result = object(r.assessment);
					if (r.error) {
						current.error = problem(r);
						current.code = text(r.code, 80);
					} else if (
						r.id !== item.id ||
						r.format !== "decky" ||
						r.read_only !== true ||
						r.version !== version.name ||
						r.hash !== version.hash ||
						result.not_executed !== true ||
						result.verified_sha256 !== true
					) {
						current.error = "The inspection result could not be confirmed. Select Inspect compatibility to retry.";
						current.code = "unconfirmed_assessment";
					} else {
						current.result = result;
						if (isDefault)
							item._latestAssessment = {
								entry_key: identity(item),
								version_identity: version.identity,
								hash: version.hash,
								assessment: result,
							};
					}
					if (
						alive() &&
						view === "details" &&
						current === inspection &&
						selected &&
						identity(selected) === identity(item)
					)
						details();
				},
				err => {
					current.pending = false;
					current.error = problem(err);
					current.code = text(object(err).code, 80);
					if (
						alive() &&
						view === "details" &&
						current === inspection &&
						selected &&
						identity(selected) === identity(item)
					)
						details();
				},
			);
		};
		const progress = job => {
			const out = tx("margin-top:14px");
			out.append(
				status(
					job.kind === "decky-import"
						? job.pending
							? "Importing…"
							: job.error
								? "Import failed."
								: "Import complete."
						: job.pending
							? TEXT.installing
							: job.error
								? TEXT.installFailed
								: TEXT.success,
					job.pending ? UP : job.error ? BAD : GOOD,
				),
			);
			if (job.message) out.append(col(job.error ? BAD : MUTED, job.message, 18));
			if (job.pending) out.append(col(MUTED, TEXT.ongoing, 17));
			return out;
		};
		const importBlocked = (item, version) => {
			const job = version && importJobs.get(importIdentity(item, version.identity));
			return (
				!importAvailable() ||
				!decky(item) ||
				!["decky_stable", "decky_testing"].includes(item._catalogSource || form.source) ||
				selected !== item ||
				!version ||
				version.identity !== inspection.version ||
				version.inspectable === false ||
				!reviewed ||
				!!detailError ||
				offline() ||
				item.installed === true ||
				!!(job && (job.pending || !job.error))
			);
		};
		const reviewImport = (item, version) => {
			if (view !== "details" || importBlocked(item, version)) return;
			const snapshot = Object.freeze({
				key: importIdentity(item, version.identity),
				name: text(item.name, 200),
				version: text(version.name, 200),
				hash: version.hash,
				request: Object.freeze({
					source: item._catalogSource || form.source,
					id: item.id,
					identity: item.identity,
					version_identity: version.identity,
					profile: 1,
					confirm: true,
				}),
			});
			fresh("confirm");
			importReview = snapshot;
			const cancel = () => {
				if (view !== "confirm" || importReview !== snapshot) return;
				fresh("details");
				details();
			};
			focusName = TEXT.cancel;
			screen(
				"Review experimental import",
				[
					...copyUnits("Review", [
						snapshot.name,
						"Selected version " + snapshot.version,
						"SHA-256: " + snapshot.hash,
						"Source: " + snapshot.request.source,
					]),
					...copyUnits("Experimental profile", importLimit, WARN),
					...copyUnits("Permissions warning", TEXT.risk, "#fff"),
					actionUnit(
						"Confirm experimental import",
						button("Import experimental profile", () => importSelected(item, version, snapshot), { primary: true }),
					),
					actionUnit("Cancel experimental import", button(TEXT.cancel, cancel)),
				],
				cancel,
			);
		};
		const importSelected = (item, version, snapshot) => {
			if (view !== "confirm" || importReview !== snapshot || importBlocked(item, version)) return;
			if (importJobs.size >= 128 && !importJobs.has(snapshot.key)) {
				for (const [key, job] of importJobs) {
					if (!job.pending) {
						importJobs.delete(key);
						break;
					}
				}
				if (importJobs.size >= 128) return;
			}
			const job = { kind: "decky-import", pending: true, message: "", error: "", code: "" };
			importJobs.set(snapshot.key, job);
			fresh("details");
			details();
			return call("catalog_import", snapshot.request).then(
				result => {
					job.pending = false;
					job.error = result.error
						? problem(result)
						: result.ok !== snapshot.request.id
							? "The import result could not be confirmed. Refresh the catalog before retrying."
							: "";
					job.code = result.error ? text(result.code, 80) : job.error ? "unconfirmed_import" : "";
					job.message = job.error || "Imported experimental profile. " + importLimit;
					if (frameyDeckyStoreOwner.active && frameyDeckyStoreOwner.active.settleImport)
						frameyDeckyStoreOwner.active.settleImport(snapshot.key, job);
				},
				error => {
					job.pending = false;
					job.error = problem(error);
					job.code = text(object(error).code, 80);
					job.message = job.error;
					if (frameyDeckyStoreOwner.active && frameyDeckyStoreOwner.active.settleImport)
						frameyDeckyStoreOwner.active.settleImport(snapshot.key, job);
				},
			);
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
					if (frameyDeckyStoreOwner.active && frameyDeckyStoreOwner.active.settleJob)
						frameyDeckyStoreOwner.active.settleJob(identity(item), item);
				},
				err => {
					job.pending = false;
					job.error = problem(err);
					job.code = text(object(err).code, 80);
					job.message = job.error;
					if (frameyDeckyStoreOwner.active && frameyDeckyStoreOwner.active.settleJob)
						frameyDeckyStoreOwner.active.settleJob(identity(item), item);
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
			if (deckyPage) {
				try {
					sessionStorage.fyDeckyCatalogSource = form.source;
				} catch (e) {
					recordStorageFailure();
				}
				sourceError = "";
				return load(false);
			}
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
		state.settleImport = (key, job) => {
			if (
				!alive() ||
				view !== "details" ||
				!selected ||
				!decky(selected) ||
				importIdentity(selected, inspection.version) !== key ||
				importJobs.get(key) !== job
			)
				return;
			details();
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
		call("catalog_sources", {}).then(
			r => {
				if (!scoped(boot)) return;
				const saved = object(r.source);
				const sourceList = deckyPage ? r.decky_sources : r.sources;
				if (Array.isArray(sourceList)) {
					const incoming = sourceList
						.slice(0, 20)
						.map(object)
						.filter(s => allowedSources.includes(s.id))
						.map(s => ({ id: s.id, label: text(s.label, 150) || s.id }));
					if (incoming.length === allowedSources.length) sources = [...new Map(incoming.map(s => [s.id, s])).values()];
				}
				if (!deckyPage && sources.some(s => s.id === saved.id)) form.source = saved.id;
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
	const dispose = render(context.box, true);
	return Object.freeze({ dispose, leave: dispose });
}
