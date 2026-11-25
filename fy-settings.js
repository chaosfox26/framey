(() => {
	const framey = __FY;
	const privateApi = framey && framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
	if (!privateApi || !privateApi.kit || privateApi.settingsReady) return;
	privateApi.settingsReady = true;
	const { T, call, tx, col, row, say, warn, obj, day, make, ui, GREY, BAD, WARN, TITLE, DANGER } = privateApi.kit;
	const knownPanelMode = mode => ["compact", "window", "embedded", "dashboard", "floating"].includes(mode);
	const knownPanelLayout = layout => layout === "compact" || layout === "window";
	let stopPanelUpdates = null;
	const leaveSettings = () => {
		const stop = stopPanelUpdates;
		stopPanelUpdates = null;
		if (stop) stop();
	};

	const settings = {
		render(box, requestedCategory) {
			leaveSettings();
			const k = make(box);
			const { btn, sw } = k;
			const windowLayout =
				!!box.closest(".fy-panel") && typeof privateApi.singleWindow === "function" && privateApi.singleWindow();
			let settingsCategory = windowLayout
					? typeof requestedCategory === "string"
						? requestedCategory
						: "Preferences"
					: requestedCategory === "Decky"
						? "Decky"
						: "",
				settingsPage = 0,
				categoryPage = 0,
				supportPage = 0,
				settingsHomeVisible = false,
				settingsScreenSerial = 0,
				loaderCheckSerial = 0;
			const categoryNames = ["Updates", "Preferences", "Decky", "Maintenance", "Worker", "Support"];
			k.bounded = !!box.closest(".fy-panel");
			const show = k.show;
			const showScreen = (...nodes) => {
				settingsHomeVisible = true;
				settingsScreenSerial++;
				return (k.rawShow || show)(...nodes);
			};
			k.show = (...nodes) => {
				settingsHomeVisible = false;
				settingsScreenSerial++;
				if (typeof privateApi.clearPageNavigation === "function") privateApi.clearPageNavigation();
				return windowLayout ? windowShow(...nodes) : show(...nodes);
			};
			if (typeof k.pages === "function") {
				const pages = k.pages;
				k.pages = (...args) => {
					settingsHomeVisible = false;
					settingsScreenSerial++;
					return windowLayout ? windowPages(...args) : pages(...args);
				};
			}
			let preserveWindowView = false;
			const windowShow = (...nodes) => {
				const result = preserveWindowView
					? box.replaceChildren(...nodes.filter(Boolean))
					: (k.rawShow || show)(...nodes);
				const large = box.clientWidth >= 650;
				box.style.cssText += ";display:flex;flex-direction:column;gap:8px;min-height:0;overflow:hidden";
				for (const node of box.querySelectorAll("*")) {
					if (node.matches("[role=button],[role=switch],[role=slider],input")) {
						node.style.minHeight = large ? "48px" : "44px";
						node.style.boxSizing = "border-box";
						node.style.flexShrink = "0";
						node.style.fontSize = large ? "18px" : "16px";
					} else if (!node.childElementCount) {
						node.style.fontSize = node.style.fontSize === "28px" ? "22px" : large ? "18px" : "15px";
						node.style.lineHeight = "1.3";
						node.style.margin = "0";
						node.style.overflowWrap = "anywhere";
						if (node.textContent.length > 650) {
							const full = node.textContent;
							const preserve = preserveWindowView;
							node.textContent = full.slice(0, 240) + "…";
							node.after(
								row(
									btn("Read full details", () => {
										const context = Array.from(box.children);
										reader(
											"Details",
											full,
											() => {
												preserveWindowView = preserve;
												try {
													k.show(...context);
												} finally {
													preserveWindowView = false;
												}
											},
											preserve,
										);
									}),
								),
							);
						}
					}
				}
				return result;
			};
			const input = (label, type = "search") => {
				const node = tx(
					"height:44px;min-width:0;width:100%;padding:8px;border:1px solid #556575;border-radius:5px;background:#0e141b;color:#fff;font-size:16px",
				);
				const control = document.createElement("input");
				control.type = type;
				control.setAttribute("aria-label", label);
				control.placeholder = label;
				control.style.cssText = node.style.cssText;
				return control;
			};
			const reader = (title, value, close, preserve = false) => {
				const text = String(value || T("v.nonotes"));
				const search = input("Find in text");
				const position = input("Text position", "number");
				position.style.width = "110px";
				position.style.flex = "none";
				const parts = [];
				let part = "",
					lines = 0;
				for (const character of text) {
					part += character;
					if (character === "\n" || character === "\r" || character === "\u2028" || character === "\u2029") lines++;
					if (part.length >= 200 || lines >= 5) {
						parts.push(part);
						part = "";
						lines = 0;
					}
				}
				if (part) parts.push(part);
				position.min = "1";
				position.max = String(Math.max(1, parts.length));
				position.value = "1";
				const excerpt = tx("white-space:pre-wrap;height:234px;overflow:hidden", "");
				const status = tx("");
				status.setAttribute("role", "status");
				const paint = () => {
					const at = Math.max(0, Math.min(Number(position.max) - 1, Math.floor(Number(position.value) || 1) - 1));
					excerpt.textContent = parts[at];
					status.textContent = "Text position " + (at + 1) + " of " + position.max;
				};
				position.oninput = paint;
				search.oninput = () => {
					const at = text.toLocaleLowerCase().indexOf(search.value.toLocaleLowerCase());
					if (at < 0) {
						status.textContent = "No matching text";
						return;
					}
					let end = 0;
					position.value = String(
						parts.findIndex(part => {
							end += part.length;
							return at < end;
						}) + 1,
					);
					paint();
				};
				preserveWindowView = preserve;
				try {
					k.show(
						tx(TITLE, title),
						search,
						row(tx("flex:1", "Text position"), position),
						status,
						excerpt,
						row(btn(preserve ? "Return to progress details" : T("c.back"), close)),
					);
				} finally {
					preserveWindowView = false;
				}
				paint();
				search.focus();
			};
			const windowPages = (title, nodes, options = {}) => {
				preserveWindowView = !!options.preserve;
				try {
					k.show(
						title && tx(TITLE, title),
						...nodes,
						options.back && row(btn(options.preserveBack ? "Return to progress" : T("c.back"), options.back)),
					);
				} finally {
					preserveWindowView = false;
				}
			};
			if (windowLayout) {
				k.ask = (title, text, actions, cancel) => {
					const ask = () => {
						const value = String(text || "");
						const cancelButton = btn(T("c.cancel"), cancel);
						k.show(
							tx(TITLE, title),
							col("#fff", value.length > 650 ? value.slice(0, 240) + "…" : value),
							value.length > 650 && row(btn("Read full details", () => reader(title, value, ask))),
							...actions.map(action => row(btn(action[0], action[1], action[2]))),
							row(cancelButton),
						);
						cancelButton.focus();
					};
					ask();
				};
				k.notes = (id, tag, title, close) => {
					k.show(tx(TITLE, title), col(GREY, T("c.loading")), row(btn(T("c.back"), close)));
					return call("notes", { id, tag }).then(
						k.guard(reply => reader(title, reply.error || reply.notes || T("v.nonotes"), close)),
						k.guard(k.bad),
					);
				};
			}
			const textScreens = (text, color = "#fff") => {
				let rest = String(text || "");
				const pages = [];
				while (rest) {
					let end = Math.min(72, rest.length);
					if (end < rest.length) end = rest.lastIndexOf(" ", end) > 24 ? rest.lastIndexOf(" ", end) : end;
					const part = rest.slice(0, end);
					pages.push(() => [tx("font-size:18px;line-height:1.35;overflow-wrap:anywhere;color:" + color, part)]);
					rest = rest.slice(end).trimStart();
				}
				return pages;
			};
			const setNavigation = (count, title, onPage, onBack) => {
				if (!windowLayout && typeof privateApi.setPageNavigation === "function")
					privateApi.setPageNavigation({ index: settingsPage, count, title, onPage, onBack, onLayout: () => home() });
			};
			const panelSection = tx("");
			panelSection.setAttribute("role", "group");
			panelSection.setAttribute("aria-label", "Panel diagnostics");
			panelSection.tabIndex = -1;
			let panelState = null,
				panelError = "",
				panelReport = null,
				panelBusy = true,
				panelMoving = false,
				panelAttempted = false,
				panelInFlight = false,
				panelSerial = 0,
				panelClosed = false;
			const panelStatus = value => {
				if (
					!value ||
					typeof value !== "object" ||
					Array.isArray(value) ||
					!knownPanelMode(value.mode) ||
					typeof value.available !== "boolean" ||
					typeof value.active !== "boolean"
				)
					return null;
				if (
					(value.pending !== undefined && typeof value.pending !== "boolean") ||
					(value.pending === true && !knownPanelMode(value.requested_mode))
				)
					return null;
				if (
					(value.layout !== undefined && !knownPanelLayout(value.layout)) ||
					(value.placements !== undefined &&
						(!value.placements ||
							typeof value.placements !== "object" ||
							Array.isArray(value.placements) ||
							typeof value.placements.compact !== "boolean" ||
							typeof value.placements.window !== "boolean"))
				)
					return null;
				if (
					(value.error !== undefined && typeof value.error !== "string") ||
					(value.input_notice !== undefined && typeof value.input_notice !== "string")
				)
					return null;
				return {
					mode: value.mode,
					layout: value.layout,
					placements: value.placements
						? { compact: value.placements.compact, window: value.placements.window }
						: { compact: value.available, window: false },
					available: value.available,
					active: value.active,
					pending: value.pending === true,
					requested_mode: value.requested_mode,
					error: (value.error || "").slice(0, 240),
					input_notice: (value.input_notice || "").slice(0, 240),
				};
			};
			const panelProblem = reply =>
				typeof reply.error === "string" && reply.error && !/^(unknown|unhandled\b)/i.test(reply.error)
					? reply.error.slice(0, 240)
					: "Panel controls are unavailable. Select Retry to check again.";
			const paintPanel = () => {
				if (panelClosed || !k.alive() || settingsCategory !== "Panel status") return;
				const focused = panelSection.contains(document.activeElement);
				const status = panelBusy
					? panelMoving
						? "Panel operation pending."
						: T("c.loading")
					: panelError
						? "Panel status unavailable."
						: "Panel status confirmed.";
				const pages = [
					() => {
						const line = col(panelError ? WARN : "#fff", status, 18);
						line.setAttribute("role", "status");
						line.setAttribute("aria-live", "polite");
						return [line];
					},
				];
				if (panelState) {
					const host =
						panelState.mode === "embedded"
							? "Recovery: Framey is inside Steam."
							: panelState.mode === "dashboard"
								? "Legacy panel host: Steam dashboard."
								: panelState.mode === "floating"
									? "Legacy panel host: floating."
									: "Panel host: " + panelState.mode + ".";
					pages.push(
						...textScreens(host),
						...textScreens(
							"Native window available: " +
								(panelState.available ? "yes" : "no") +
								". Active: " +
								(panelState.active ? "yes" : "no") +
								".",
						),
					);
				}
				if (panelError) pages.push(...textScreens(panelError, WARN));
				if (panelState && panelState.input_notice) pages.push(...textScreens(panelState.input_notice));
				pages.push(() => [
					row(
						btn(T("c.retry"), requestPanel, { disabled: panelInFlight }),
						panelError && k.reportButton(panelReport || { error: panelError, code: "panel_status_failed" }),
					),
				]);
				settingsPage = Math.min(settingsPage, pages.length - 1);
				if (windowLayout) {
					const detail = [
						status,
						panelState &&
							"Panel host: " +
								panelState.mode +
								". Native window available: " +
								(panelState.available ? "yes" : "no") +
								". Active: " +
								(panelState.active ? "yes" : "no") +
								".",
						panelError,
						panelState && panelState.input_notice,
					]
						.filter(Boolean)
						.join("\n\n");
					panelSection.replaceChildren(
						col(panelError ? WARN : "#fff", detail.length > 500 ? detail.slice(0, 240) + "…" : detail),
						row(btn(T("c.retry"), requestPanel, { disabled: panelInFlight })),
						detail.length > 500 ? row(btn("Read full details", () => reader("Panel status", detail, home))) : tx(""),
						panelError
							? row(k.reportButton(panelReport || { error: panelError, code: "panel_status_failed" }))
							: tx(""),
						row(
							btn(T("c.back"), () => {
								settingsCategory = "Support";
								home();
							}),
						),
					);
					windowShow(panelSection);
				} else panelSection.replaceChildren(...pages[settingsPage]());
				setNavigation(
					pages.length,
					"Panel status",
					page => {
						settingsPage = page;
						paintPanel();
					},
					() => {
						settingsCategory = "Support";
						settingsPage = supportPage;
						home();
					},
				);
				if (focused) (panelSection.querySelector('[role="button"][aria-disabled="false"]') || panelSection).focus();
			};
			const requestPanel = () => {
				if (panelClosed || !k.alive() || panelInFlight) return;
				const serial = ++panelSerial;
				const wasMoving = panelMoving;
				panelAttempted = panelInFlight = panelBusy = true;
				panelError = "";
				panelReport = null;
				paintPanel();
				const finishPanel = reply => {
					if (panelClosed || !k.alive() || serial !== panelSerial) return;
					panelInFlight = false;
					const actual = panelStatus(reply);
					if (actual) {
						panelState = actual;
						panelBusy = panelMoving = actual.pending;
						panelError = actual.error;
						if (knownPanelLayout(actual.layout) && typeof privateApi.setPanelLayout === "function")
							privateApi.setPanelLayout(actual.layout);
					} else {
						panelBusy = panelMoving = wasMoving;
						panelError = reply.error
							? panelProblem(reply)
							: "Framey did not confirm the panel status. Select Retry to check again.";
					}
					panelReport = panelError ? supportError(panelError, "panel_status_failed") : null;
					paintPanel();
				};
				return call("panel_status").then(finishPanel, error =>
					finishPanel({ error: String((error && error.message) || T("c.noanswer")) }),
				);
			};
			let ld = {},
				st = { prefs: {} },
				auto = {},
				mp = {},
				ss = null,
				lres = null,
				cand = null,
				info = null,
				line = null;
			const saving = new Set();
			const supportContext = { error: "", code: "settings_support" };
			const supportError = (error, code) => {
				supportContext.error = String(error || T("c.noanswer")).slice(0, 300);
				supportContext.code = code;
				return { ...supportContext };
			};
			k.names = id => (id === "_loader" ? T("s.framey") : "");
			const draw = () => {
				k.show(tx(TITLE, T("s.title")), col(GREY, T("c.loading")));
				return Promise.all([
					call("loader"),
					call("state_get"),
					call("autocheck_status"),
					call("maint_prefs"),
					call("status"),
				]).then(
					k.guard(([l, s, a, m, x]) => {
						if (l.error || s.error || !s.prefs || typeof s.prefs !== "object" || Array.isArray(s.prefs))
							return k.bad(new Error(l.error || s.error || T("c.noanswer")));
						ld = l;
						st = s;
						auto = a;
						mp = m;
						ss = obj(x).settings_state;
						home();
					}),
					k.guard(e => k.bad(e)),
				);
			};
			const back = () => draw();
			k.back = back;
			k.bad = e => {
				const context = supportError((e && e.message) || e, "settings_error");
				k.show(
					tx(TITLE, T("s.title")),
					col(BAD, T("c.failed", { msg: context.error.slice(0, 200) })),
					row(btn(T("c.retry"), draw), k.reportButton(context)),
				);
			};
			const write = (method, key, value, el, previous, apply, after) => {
				if (saving.has(key)) {
					if (el) el.value = previous;
					return;
				}
				saving.add(key);
				if (el) {
					el.disabled = true;
					el.setAttribute("aria-busy", "true");
				}
				const done = r => {
					if (panelClosed || !k.alive()) return;
					const values = method === "prefs_set" ? r.prefs : r;
					const saved = values && values[key];
					if (el) {
						el.disabled = false;
						el.removeAttribute("aria-busy");
					}
					if (r.error || typeof saved !== typeof value) {
						if (el) el.value = previous;
						supportError(r.error, "settings_save_failed");
						warn(r.error || T("c.noanswer"));
						return;
					}
					apply(saved);
					if (el) el.value = saved;
					if (after) after();
				};
				return call(method, { [key]: value }).then(
					r => {
						saving.delete(key);
						done(r);
					},
					e => {
						saving.delete(key);
						done({ error: String((e && e.message) || T("c.noanswer")) });
					},
				);
			};
			const savePref = (key, value, el, after) =>
				write(
					"prefs_set",
					key,
					value,
					el,
					obj(st.prefs)[key],
					saved => {
						st = { ...st, prefs: { ...obj(st.prefs), [key]: saved } };
					},
					after,
				);
			const pref = (label, key, value, after) => sw(label, value, (v, el) => savePref(key, v, el, after));
			const checkedCandidate = c =>
				c &&
				typeof c.tag === "string" &&
				/^[A-Za-z0-9._-]{1,64}$/.test(c.tag) &&
				typeof c.commit === "string" &&
				/^[0-9a-f]{40}$/.test(c.commit);
			const check = (el, after) => {
				const serial = ++loaderCheckSerial;
				const screen = settingsScreenSerial;
				k.hold(true);
				if (el) {
					el.textContent = T("c.checking");
					el.style.color = GREY;
				}
				const accept = r => {
					if (panelClosed || !k.alive() || serial !== loaderCheckSerial) return;
					const sameScreen = screen === settingsScreenSerial;
					if (sameScreen) k.hold(false);
					lres = r.loader || null;
					cand = r.candidate || (lres && lres.candidate) || null;
					info = r.installed || null;
					line = lres ? null : T("c.failed", { msg: r.error || T("c.noanswer") });
					if (r.error || (lres && lres.state === "error")) supportError(r.error || lres.msg, "loader_check_failed");
					if (after && sameScreen && settingsHomeVisible && checkedCandidate(cand) && lres && lres.state !== "error")
						return after();
					if (after && !line && (!lres || lres.state !== "current") && !checkedCandidate(cand))
						line = T("c.failed", { msg: (lres && lres.msg) || T("c.noanswer") });
					if (settingsHomeVisible && settingsCategory === "Updates") home();
				};
				return call("loader_check").then(accept, error =>
					accept({ error: String((error && error.message) || T("c.noanswer")) }),
				);
			};
			const loaderUpdate = (confirm, selected) =>
				k.work(
					T("s.updating"),
					() => call("loader_update", { confirm, candidate: selected }),
					(r, out) => {
						if (r.confirm)
							return k.ask(
								T("s.downgrade_title"),
								r.confirm,
								[[T("s.update_now"), () => loaderUpdate(true, selected), DANGER]],
								back,
							);
						if (r.current) {
							lres = { state: "current" };
							return k.result(out, {}, T("s.already"), back);
						}
						if (r.ok) {
							try {
								sessionStorage.fyMsg = T("s.updated");
							} catch (e) {
								if (typeof privateApi.diagnostic === "function") privateApi.diagnostic("core.storage_failed");
							}
							const result = k.result(out, {}, T("s.restarting"), back);
							if (k.bounded) {
								const b = box.closest(".fy-panel")?.querySelector('[aria-label="Back"]');
								if (b && b.isConnected && b.style.display !== "none") b.focus();
							}
							return result;
						}
						k.result(out, r, "", back, r.error ? supportError(r.error, "loader_update_failed") : undefined);
					},
				);
			const identity = c =>
				[
					T("s.candidate", { tag: c.tag, c: String(c.commit || "").slice(0, 7) }),
					day(c.published) && T("s.published", { d: day(c.published) }),
				]
					.filter(Boolean)
					.join(", ");
			const updateAsk = () => {
				if (!checkedCandidate(cand)) return check(null, updateAsk);
				const selected = { tag: cand.tag, commit: cand.commit };
				return k.ask(
					T("s.update_title"),
					[
						cand ? identity(cand) : T("s.update_nocheck"),
						lres && lres.downgrade && T("v.limits"),
						lres && lres.notes && T("s.notes_head") + "\n" + lres.notes,
					]
						.filter(Boolean)
						.join("\n\n"),
					[[T("s.update_now"), () => loaderUpdate(false, selected), { primary: true }]],
					back,
				);
			};
			const lazyDev = () => {
				k.hold(true);
				framey.lazy("dev").then(
					k.guard(() => {
						k.hold(false);
						if (framey.dev && framey.open) framey.open("#developer");
						else warn("Restart Framey to open Developer tools.");
					}),
					k.guard(e => {
						k.hold(false);
						warn(e.message);
					}),
				);
			};
			const maintPref = key => (v, el) =>
				write("maint_prefs", key, v, el, mp[key], saved => {
					mp = { ...mp, [key]: saved };
				});
			const maintBox = () => {
				if (mp.error || typeof mp["maint.window_days"] !== "number") return [col(GREY, T("mt.unavailable"), 16)];
				const window = ui().slider({
					label: T("mt.window_label"),
					min: 30,
					max: 1825,
					step: 5,
					value: mp["maint.window_days"],
					format: v => T("mt.days", { count: v }),
					onchange: v => maintPref("maint.window_days")(v, window),
				});
				return [
					sw(T("mt.bg"), mp["maint.enabled_background"] === true, maintPref("maint.enabled_background")),
					col(GREY, T("mt.bg_text"), 16),
					window,
				];
			};
			const workerBox = () => {
				const value = obj(st.prefs).workers_max;
				if (!Number.isInteger(value) || value < 1 || value > 3) return [];
				const control = ui().slider({
					label: T("s.workers"),
					min: 1,
					max: 3,
					step: 1,
					value,
					onchange: v => savePref("workers_max", v, control),
				});
				control.style.flexWrap = windowLayout && box.clientWidth >= 650 ? "nowrap" : "wrap";
				control.style.rowGap = "0";
				control.querySelector(".fy-l").style.flex = windowLayout && box.clientWidth >= 650 ? "1" : "0 0 100%";
				return [control, col(GREY, T("s.workers_text"), 16)];
			};
			const finish = (method, title, okKey) =>
				k.work(
					title,
					() => call(method),
					(r, out) => k.result(out, r, T(okKey), back, r.error ? supportError(r.error, method + "_failed") : undefined),
				);
			const categoryHome = () => {
				settingsCategory = "";
				settingsPage = categoryPage;
				home();
			};
			const repairSettings = () =>
				k.ask(
					T("sd.repair_title"),
					T("sd.repair_text"),
					[
						[
							T("sd.repair_ok"),
							() =>
								call("settings_repair").then(
									k.guard(reply => {
										if (reply.ok) say(T("sd.repaired"));
										else warn(reply.error || T("c.noanswer"));
										back();
									}),
								),
							{ primary: true },
						],
					],
					back,
				);
			const windowHistory = () => {
				let versions = [],
					page = 0,
					more = false,
					busy = false,
					error = "",
					note = "",
					query = "",
					offset = 0;
				const eligible = value => value.eligible === true && value.current !== true;
				const label = value => value.version || value.tag;
				const install = (value, confirm) => {
					if (!eligible(value)) return;
					if (value.downgrade && !confirm)
						return k.ask(
							T("v.confirm_title", { v: label(value) }),
							T("v.confirm_text", { v: label(value) }),
							[[T("v.goback"), () => install(value, true), DANGER]],
							() => detail(value),
						);
					return k.work(
						T("v.installing", { v: label(value) }),
						() => call("ver_install", { id: "_loader", tag: value.tag, confirm: !!confirm }),
						(reply, out) => {
							if (reply.confirm)
								return k.ask(
									T("v.confirm_title", { v: label(value) }),
									reply.confirm,
									[[T("v.goback"), () => install(value, true), DANGER]],
									() => detail(value),
								);
							if (reply.current === true) return k.result(out, {}, T("m.already"), home);
							const result = reply.error || reply.ok || reply.staged ? reply : { error: T("c.noanswer") };
							return k.result(
								out,
								result,
								T("v.installed_ok", { v: reply.version || label(value) }),
								home,
								result.error ? supportError(result.error, "version_install_failed") : undefined,
							);
						},
					);
				};
				const detail = value => {
					const compatibility = obj(value.compat);
					const metadata = [
						label(value),
						value.tag,
						value.published && T("v.published", { d: day(value.published) }),
						compatibility.msg,
						value.downgrade && T("v.limits"),
						!eligible(value) && (value.reason || T(value.current ? "v.installed" : "c.unavailable")),
						value.label,
					]
						.filter(Boolean)
						.join("\n\n");
					k.show(
						tx(TITLE, label(value)),
						col(GREY, metadata.length > 500 ? metadata.slice(0, 240) + "…" : metadata),
						metadata.length > 500 &&
							row(btn("Read full details", () => reader(label(value), metadata, () => detail(value)))),
						row(
							btn(T(value.downgrade ? "v.goback" : "v.install"), () => install(value, false), {
								disabled: !eligible(value),
								primary: !value.downgrade,
							}),
							btn(T("v.notes"), () => k.notes("_loader", value.tag, label(value), () => detail(value))),
						),
						row(btn(T("c.back"), list)),
					);
				};
				const list = () => {
					const search = input("Find a version");
					search.value = query;
					const position = input("Result position", "number");
					position.style.width = "110px";
					position.style.flex = "none";
					position.min = "1";
					position.value = String(offset + 1);
					const results = tx("display:flex;flex-direction:column;gap:8px");
					const count = col(GREY, "", 15);
					const paint = () => {
						const found = versions.filter(value =>
							[value.version, value.tag, value.label, value.reason, value.published]
								.join(" ")
								.toLocaleLowerCase()
								.includes(query.toLocaleLowerCase()),
						);
						offset = Math.max(0, Math.min(Math.max(0, found.length - 1), offset));
						position.max = String(Math.max(1, found.length));
						position.value = String(offset + 1);
						count.textContent = found.length + " matching versions · " + versions.length + " loaded";
						results.replaceChildren(
							...found.slice(offset, offset + 3).map(value => {
								const button = btn(label(value), () => detail(value));
								button.style.cssText +=
									";height:44px;min-height:44px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:16px";
								return button;
							}),
							!found.length ? col(GREY, T("v.none")) : tx(""),
						);
					};
					search.oninput = () => {
						query = search.value;
						offset = 0;
						paint();
					};
					position.oninput = () => {
						offset = Math.floor(Number(position.value) || 1) - 1;
						paint();
					};
					k.show(
						tx(TITLE, T("s.history")),
						search,
						row(col(GREY, "Result position", 15), position),
						count,
						results,
						(error || note) &&
							row(
								btn(error ? "History error details" : "History details", () =>
									reader(T("s.history"), error || note, list),
								),
							),
						row(
							more && btn(T("v.more"), () => load(page + 1), { disabled: busy }),
							error && btn(T("c.retry"), () => load(page + 1), { disabled: busy }),
							btn(T("c.back"), home),
						),
					);
					paint();
				};
				const load = next => {
					if (busy) return;
					busy = true;
					list();
					const accept = reply => {
						busy = false;
						if (reply.error) error = String(reply.error);
						else {
							error = "";
							page = next;
							more = !!reply.more;
							note = reply.note || note;
							versions = versions.concat(
								(Array.isArray(reply.versions) ? reply.versions : []).filter(
									value => value && typeof value === "object" && typeof value.tag === "string",
								),
							);
						}
						list();
					};
					return call("ver_list", { id: "_loader", page: next }).then(
						k.guard(accept),
						k.guard(reason => accept({ error: String(reason.message || reason) })),
					);
				};
				return load(1);
			};
			let deckyState = null,
				deckyPending = false,
				deckyAttempted = false,
				deckyRequestSerial = 0,
				deckyAction = "";
			const deckyError = value => {
				const error = obj(value).error;
				return String(typeof error === "string" ? error : obj(error).message || "").slice(0, 300);
			};
			const deckyReply = reply => {
				const value = obj(reply);
				if (typeof value.installed !== "boolean" || typeof value.enabled !== "boolean")
					return { ...obj(deckyState), phase: "error", error: deckyError(value) || T("s.decky_unavailable") };
				return value;
			};
			const runDecky = (method, args) => {
				const cancelling = method === "decky_runtime_enable" && args.enabled === false && deckyAction === "enable";
				if (deckyPending && !cancelling) return;
				const requestSerial = ++deckyRequestSerial;
				deckyPending = true;
				deckyAttempted = true;
				deckyAction = method === "decky_runtime_enable" ? (args.enabled ? "enable" : "disable") : method;
				home();
				return call(method, args).then(
					reply => {
						if (requestSerial !== deckyRequestSerial) return;
						const value = deckyReply(reply);
						const manager = privateApi.optionalRuntime;
						const previous = manager && typeof manager.state === "function" ? manager.state() : null;
						if (
							manager &&
							typeof manager.invalidate === "function" &&
							(method !== "decky_runtime_status" ||
								value.enabled !== true ||
								(previous && Number.isSafeInteger(value.generation) && value.generation > previous.generation))
						)
							manager.invalidate(value);
						if (panelClosed || !k.alive()) return;
						deckyPending = false;
						deckyState = value;
						if (settingsCategory === "Decky") home();
					},
					error => {
						if (requestSerial !== deckyRequestSerial || panelClosed || !k.alive()) return;
						deckyPending = false;
						deckyState = {
							...obj(deckyState),
							error: String((error && error.message) || T("c.noanswer")),
							phase: "error",
						};
						if (settingsCategory === "Decky") home();
					},
				);
			};
			const deckyBox = () => {
				if (!deckyAttempted)
					Promise.resolve().then(() => {
						if (!panelClosed && k.alive() && !deckyAttempted) runDecky("decky_runtime_status", {});
					});
				const value = obj(deckyState);
				const enabled = value.enabled === true;
				const phaseBusy = ["enabling", "installing", "disabling", "removing"].includes(value.phase);
				const known = typeof value.enabled === "boolean" && typeof value.installed === "boolean";
				const toggle = sw(T("s.decky_enable"), enabled, (next, el) => {
					el.value = enabled;
					if (!deckyPending) runDecky("decky_runtime_enable", { enabled: next });
				});
				toggle.disabled = deckyPending || phaseBusy || !known;
				if (deckyPending) toggle.setAttribute("aria-busy", "true");
				const error = deckyError(value);
				const phaseText = {
					enabling: "s.decky_enabling",
					installing: "s.decky_installing",
					disabling: "s.decky_disabling",
					removing: "s.decky_removing",
				}[value.phase];
				const state = deckyPending
					? T(
							deckyAction === "enable"
								? "s.decky_enabling"
								: deckyAction === "disable"
									? "s.decky_disabling"
									: "s.decky_working",
						)
					: phaseText
						? T(phaseText)
						: !known
							? T("s.decky_unavailable")
							: enabled
								? T("s.decky_on")
								: value.installed
									? T("s.decky_off_installed")
									: T("s.decky_off");
				return [
					toggle,
					col(GREY, state),
					typeof value.version === "string" && value.version && col(GREY, T("s.decky_version", { v: value.version })),
					col(GREY, T("s.decky_explain"), 15),
					error && col(BAD, error),
					((deckyPending && deckyAction === "enable") || ["enabling", "installing"].includes(value.phase)) &&
						btn(T("s.decky_cancel"), () => runDecky("decky_runtime_enable", { enabled: false })),
					row(
						btn(T("s.decky_refresh"), () => runDecky("decky_runtime_status", {}), { disabled: deckyPending }),
						value.installed === true &&
							btn(T("s.decky_remove"), () => runDecky("decky_runtime_remove", {}), {
								disabled: deckyPending || phaseBusy,
							}),
					),
				].filter(Boolean);
			};
			const windowHome = () => {
				const p = obj(st.prefs);
				const dev = p.dev && p.dev.enabled === true;
				const text = (value, color = GREY) => col(color, value, 15);
				const detail = (label, value) => btn(label, () => reader(label, value, home));
				let nodes;
				if (settingsCategory === "Panel status") {
					if (!panelSection.isConnected) showScreen(panelSection);
					paintPanel();
					if (!panelAttempted) requestPanel();
					return;
				}
				if (settingsCategory === "Decky") nodes = deckyBox();
				else if (settingsCategory === "Preferences") {
					const unavailable =
						auto.error ||
						typeof auto.enabled !== "boolean" ||
						(auto.enabled &&
							(!Number.isFinite(auto.last) || auto.last < 0 || !Number.isFinite(new Date(auto.last * 1000).getTime())));
					const status = unavailable
						? T("c.unavailable")
						: auto.enabled
							? auto.last
								? T("s.auto_last", { d: new Date(auto.last * 1000).toLocaleString() })
								: T("s.auto_never")
							: T("s.auto_off");
					nodes = [
						pref(T("s.auto"), "autocheck", p.autocheck === true, draw),
						text(status),
						pref(T("s.notify_loader"), "notify_loader", p.notify_loader === true),
						pref(T("s.notify_plugins"), "notify_plugins", p.notify_plugins === true),
						text(T("s.lang")),
						text(T("s.safe_ro", { state: T(st.safe === true ? "s.on" : "s.off") })),
						row(
							detail("Safe mode details", T("s.safe_text")),
							dev && btn(T("s.dev_open"), lazyDev),
							dev && btn(T("s.diag"), lazyDev),
						),
						text(T("s.dev_ro", { state: T(dev ? "s.on" : "s.off") })),
						unavailable && auto.error && row(detail("Automatic check details", auto.error)),
					];
				} else if (settingsCategory === "Updates") {
					const state = lres && lres.state;
					const status =
						line ||
						(state === "current"
							? T("s.current")
							: state === "update"
								? T(lres.downgrade ? "s.older" : "s.update_avail")
								: state === "error"
									? T("s.check_failed", { msg: lres.msg })
									: T("s.not_checked"));
					const installed = (info && info.version) || (window.FY_RUNTIME || {}).version;
					nodes = [
						row(
							btn(T("s.check"), () => check(null), { primary: true }),
							btn(T("s.update"), updateAsk, { primary: state === "update" }),
						),
						text(status.length > 160 ? status.slice(0, 160) + "…" : status, line || state === "error" ? BAD : "#fff"),
						status.length > 160 && row(detail("Read check details", status)),
						text(
							installed
								? T("s.installed", { v: installed, c: ld.commit || T("c.unknown") })
								: T("s.installed_c", { c: ld.commit || T("c.unknown") }),
						),
						text(cand ? identity(cand) : T("s.not_checked")),
						cand && cand.prerelease && text(cand.label || T("s.prerelease"), WARN),
						text(T("s.ch_ro", { channel: p.channel === "prerelease" ? "prerelease" : "stable" })),
						row(
							btn(T("s.notes"), () => k.notes("_loader", cand.tag, T("s.notes_title", { tag: cand.tag }), home), {
								disabled: !cand,
							}),
							btn(T("s.history"), () => windowHistory()),
						),
						ld.kept && text(T("s.kept")),
						ld.kept &&
							row(
								btn(T("s.done"), () => finish("loader_done", T("s.working"), "s.done_ok")),
								btn(T("s.rollback"), () =>
									k.ask(
										T("s.rollback_title"),
										T("s.rollback_text"),
										[[T("s.rollback"), () => finish("loader_roll", T("s.rolling"), "s.rolled"), DANGER]],
										back,
									),
								),
							),
						(line || state === "error") && row(k.reportButton(supportContext)),
					];
				} else if (settingsCategory === "Maintenance") nodes = maintBox();
				else if (settingsCategory === "Worker") {
					const parts = workerBox();
					nodes = parts.length ? parts : [text("Worker controls are unavailable.")];
				} else {
					nodes = [
						ss === "corrupt" && text(T("sd.corrupt"), BAD),
						ss === "corrupt" && row(btn(T("sd.repair"), repairSettings, { primary: true })),
						ss === "unreadable" && text(T("sd.unreadable"), BAD),
						row(
							k.reportButton(supportContext),
							btn("Panel status", () => {
								settingsCategory = "Panel status";
								home();
							}),
						),
						text(T("s.trust")),
						row(
							btn(T("s.reload"), () => {
								k.hold(true);
								call("reload").then(
									k.guard(reply => {
										k.hold(false);
										if (reply.error) {
											supportError(reply.error, "module_reload_failed");
											warn(reply.error);
										} else say(T("s.reloaded"));
									}),
									k.guard(error => {
										k.hold(false);
										warn(error.message);
									}),
								);
							}),
						),
					];
				}
				showScreen(...nodes);
				windowShow(...Array.from(box.children));
			};
			const home = () => {
				if (windowLayout) return windowHome();
				const p = obj(st.prefs);
				const dev = p.dev && p.dev.enabled === true;
				const names = dev ? categoryNames.concat("Developer") : categoryNames;
				if (!settingsCategory) {
					settingsPage = Math.min(settingsPage, names.length - 1);
					categoryPage = settingsPage;
					const categories = tx("");
					categories.setAttribute("role", "group");
					categories.setAttribute("aria-label", "Settings categories");
					categories.append(
						btn(names[settingsPage], () => {
							settingsCategory = names[settingsPage];
							settingsPage = 0;
							home();
						}),
					);
					showScreen(categories);
					setNavigation(names.length, T("s.title"), page => {
						settingsPage = page;
						home();
					});
				} else if (settingsCategory === "Panel status") {
					if (panelSection.parentNode !== box) showScreen(panelSection);
					paintPanel();
				} else {
					let pages;
					if (settingsCategory === "Updates") {
						const state = lres && lres.state;
						const status =
							line ||
							(state === "current"
								? T("s.current")
								: state === "update"
									? lres.downgrade
										? T("s.older")
										: T("s.update_avail")
									: state === "error"
										? T("s.check_failed", { msg: lres.msg })
										: T("s.not_checked"));
						pages = [
							() => [
								row(
									btn(T("s.check"), () => check(null), { primary: true }),
									btn(T("s.update"), updateAsk, { primary: state === "update" }),
								),
							],
							...textScreens(status, line || state === "error" ? BAD : "#fff"),
							...textScreens(
								(info && info.version) || (window.FY_RUNTIME || {}).version
									? T("s.installed", {
											v: (info && info.version) || window.FY_RUNTIME.version,
											c: ld.commit || T("c.unknown"),
										})
									: T("s.installed_c", { c: ld.commit || T("c.unknown") }),
							),
							...textScreens(cand ? identity(cand) : T("s.not_checked")),
							...(cand && cand.prerelease ? textScreens(cand.label || T("s.prerelease"), WARN) : []),
							...textScreens(T("s.ch_ro", { channel: p.channel === "prerelease" ? "prerelease" : "stable" })),
							() => [
								row(
									btn(T("s.notes"), () => k.notes("_loader", cand.tag, T("s.notes_title", { tag: cand.tag }), home), {
										disabled: !cand,
									}),
									btn(T("s.history"), () => k.versions("_loader", T("s.history"), back)),
								),
							],
						];
						if (line || state === "error") pages.push(() => [row(k.reportButton(supportContext))]);
						if (ld.kept)
							pages.push(...textScreens(T("s.kept")), () => [
								row(
									btn(T("s.done"), () => finish("loader_done", T("s.working"), "s.done_ok")),
									btn(T("s.rollback"), () =>
										k.ask(
											T("s.rollback_title"),
											T("s.rollback_text"),
											[[T("s.rollback"), () => finish("loader_roll", T("s.rolling"), "s.rolled"), DANGER]],
											back,
										),
									),
								),
							]);
					} else if (settingsCategory === "Decky") {
						pages = deckyBox().map(node => () => [node]);
					} else if (settingsCategory === "Preferences") {
						const unavailable =
							auto.error ||
							typeof auto.enabled !== "boolean" ||
							(auto.enabled &&
								(!Number.isFinite(auto.last) ||
									auto.last < 0 ||
									!Number.isFinite(new Date(auto.last * 1000).getTime())));
						const autoText = unavailable
							? T("c.unavailable") +
								(auto.error ? " — " + T("c.failed", { msg: String(auto.error).slice(0, 200) }) : "")
							: auto.enabled
								? auto.last
									? T("s.auto_last", { d: new Date(auto.last * 1000).toLocaleString() })
									: T("s.auto_never")
								: T("s.auto_off");
						if (unavailable && !supportContext.error)
							supportError(auto.error || T("c.unavailable"), "autocheck_status_unavailable");
						pages = [
							() => [pref(T("s.auto"), "autocheck", p.autocheck === true, draw)],
							...textScreens(autoText),
							() => [pref(T("s.notify_loader"), "notify_loader", p.notify_loader === true)],
							() => [pref(T("s.notify_plugins"), "notify_plugins", p.notify_plugins === true)],
							...textScreens(T("s.lang")),
							...textScreens(T("s.safe_ro", { state: T(st.safe === true ? "s.on" : "s.off") })),
							...textScreens(T("s.safe_text")),
							...textScreens(T("s.dev_ro", { state: T(dev ? "s.on" : "s.off") })),
						];
					} else if (settingsCategory === "Maintenance") {
						const parts = maintBox();
						pages =
							mp.error || typeof mp["maint.window_days"] !== "number"
								? textScreens(T("mt.unavailable"))
								: [() => [parts[0]], ...textScreens(T("mt.bg_text")), () => [parts[2]]];
					} else if (settingsCategory === "Worker") {
						const parts = workerBox();
						pages = parts.length
							? [() => [parts[0]], ...textScreens(T("s.workers_text"))]
							: textScreens("Worker controls are unavailable.");
					} else if (settingsCategory === "Developer") {
						pages = [() => [row(btn(T("s.dev_open"), lazyDev), btn(T("s.diag"), lazyDev))]];
					} else {
						pages = [
							() => [row(k.reportButton(supportContext))],
							() => [
								row(
									btn("Panel status", () => {
										supportPage = settingsPage;
										settingsCategory = "Panel status";
										settingsPage = 0;
										home();
									}),
								),
							],
							...textScreens(T("s.trust")),
							() => [
								row(
									btn(T("s.reload"), () => {
										k.hold(true);
										call("reload").then(
											k.guard(r => {
												k.hold(false);
												if (r.error) {
													supportError(r.error, "module_reload_failed");
													warn(r.error);
												} else say(T("s.reloaded"));
											}),
										);
									}),
								),
							],
						];
						if (ss === "corrupt")
							pages.unshift(...textScreens(T("sd.corrupt"), BAD), () => [
								row(btn(T("sd.repair"), repairSettings, { primary: true })),
							]);
						if (ss === "unreadable") pages.unshift(...textScreens(T("sd.unreadable"), BAD));
					}
					settingsPage = Math.min(settingsPage, pages.length - 1);
					showScreen(...pages[settingsPage]());
					setNavigation(
						pages.length,
						settingsCategory,
						page => {
							settingsPage = page;
							home();
						},
						categoryHome,
					);
				}
				if (settingsCategory === "Panel status" && !panelAttempted) requestPanel();
			};
			let unlisten = null;
			try {
				unlisten = privateApi.listen("panel_mode", value => {
					if (panelClosed || !k.alive()) return;
					const actual = panelStatus(value);
					if (!actual) return;
					panelSerial++;
					panelAttempted = true;
					panelInFlight = false;
					panelState = actual;
					panelBusy = panelMoving = actual.pending;
					panelError = actual.error;
					panelReport = panelError ? supportError(panelError, "panel_mode_failed") : null;
					if (knownPanelLayout(actual.layout) && typeof privateApi.setPanelLayout === "function")
						privateApi.setPanelLayout(actual.layout);
					paintPanel();
				});
			} catch (error) {
				if (typeof privateApi.diagnostic === "function") privateApi.diagnostic("core.event_failed");
			}
			stopPanelUpdates = () => {
				panelClosed = true;
				if (typeof privateApi.clearPageNavigation === "function") privateApi.clearPageNavigation();
				panelSerial++;
				if (typeof unlisten === "function") unlisten();
			};
			draw();
		},
	};
	privateApi.windowPages = (privateApi.windowPages || []).concat(
		["Preferences", "Updates", "Decky", "Maintenance", "Worker", "Support"].map(name =>
			Object.freeze({
				id: "#settings:" + name.toLowerCase(),
				name,
				short: name,
				render: box => settings.render(box, name),
				leave: leaveSettings,
			}),
		),
	);
	Object.defineProperty(framey.pages, "settings", {
		value: Object.freeze({ render: settings.render, leave: leaveSettings, dispose: leaveSettings }),
		enumerable: true,
	});
	Object.freeze(framey.pages);
})();
