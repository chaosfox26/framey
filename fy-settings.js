(() => {
	const framey = __FY;
	const privateApi = framey && framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
	if (!privateApi || !privateApi.kit || privateApi.settingsReady) return;
	privateApi.settingsReady = true;
	const { T, call, tx, put, col, row, say, warn, obj, day, make, dmg, ui, GREY, GOOD, BAD, UP, WARN, TITLE, DANGER } =
		privateApi.kit;
	const panelModes = [
		{ id: "embedded", label: "Inside Steam" },
		{ id: "dashboard", label: "Beside Steam dashboard" },
		{ id: "floating", label: "Floating during games" },
	];
	let stopPanelUpdates = null;
	const leaveSettings = () => {
		const stop = stopPanelUpdates;
		stopPanelUpdates = null;
		if (stop) stop();
	};

	const settings = {
		render(box) {
			leaveSettings();
			const k = make(box);
			const { btn, sw } = k;
			const panelSection = tx("margin:20px 0");
			panelSection.setAttribute("role", "group");
			panelSection.setAttribute("aria-label", "Framey panel");
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
					!panelModes.some(mode => mode.id === value.mode) ||
					typeof value.available !== "boolean" ||
					typeof value.active !== "boolean"
				)
					return null;
				if (
					(value.pending !== undefined && typeof value.pending !== "boolean") ||
					(value.pending === true && !panelModes.some(mode => mode.id === value.requested_mode))
				)
					return null;
				if (
					(value.error !== undefined && typeof value.error !== "string") ||
					(value.input_notice !== undefined && typeof value.input_notice !== "string")
				)
					return null;
				return {
					mode: value.mode,
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
				if (panelClosed || !k.alive() || !panelSection.isConnected) return;
				const focused = panelSection.contains(document.activeElement);
				const choices = ui().list({
					items: panelModes.map(mode => ({
						...mode,
						disabled: panelBusy || !panelState || (mode.id !== "embedded" && !panelState.available),
					})),
					selected: panelState && panelState.mode,
					onselect: mode => {
						if (!panelBusy && panelState && mode !== panelState.mode) requestPanel("panel_mode", { mode }, mode);
					},
				});
				choices.setAttribute("aria-label", "Framey panel mode");
				choices.setAttribute("aria-busy", String(panelBusy));
				const status = col(
					panelError ? WARN : GREY,
					panelMoving ? "Moving Framey panel…" : panelBusy ? T("c.loading") : panelError ? T("c.unavailable") : "",
					18,
				);
				status.setAttribute("role", "status");
				status.setAttribute("aria-live", "polite");
				panelSection.replaceChildren(
					...[
						tx("font-size:22px;font-weight:700;margin-bottom:10px", "Framey panel"),
						choices,
						status,
						panelError && col(WARN, panelError, 18),
						panelState &&
							!panelState.available &&
							col(GREY, "Native panel modes are unavailable. Inside Steam remains available.", 17),
						col(GREY, "Floating panels remain visible after the Steam menu closes.", 17),
						col(
							GREY,
							(panelState && panelState.input_notice) || "Controller pointers may interrupt game controls.",
							17,
						),
						col(GREY, "Open Framey using its bottom-bar button through the Steam System menu in 2D or VR.", 17),
						(panelError || panelMoving || (panelState && !panelState.available)) &&
							row(
								btn(T("c.retry"), () => requestPanel("panel_status"), { disabled: panelInFlight }),
								panelError && k.reportButton(panelReport || { error: panelError, code: "panel_status_failed" }),
							),
					].filter(Boolean),
				);
				if (focused) {
					const target = panelBusy
						? panelSection
						: panelSection.querySelector('[role="option"][aria-selected="true"][aria-disabled="false"]') ||
							panelSection.querySelector('[role="option"][aria-disabled="false"]') ||
							panelSection;
					target.focus();
				}
			};
			const requestPanel = (method, arg, requested) => {
				if (panelClosed || !k.alive() || !panelSection.isConnected || panelInFlight) return;
				const serial = ++panelSerial;
				const wasMoving = panelMoving;
				panelAttempted = panelInFlight = panelBusy = true;
				if (method === "panel_mode") panelMoving = true;
				panelError = "";
				panelReport = null;
				paintPanel();
				let applied = false;
				const accept = reply => {
					if (panelClosed || !k.alive() || serial !== panelSerial || !panelSection.isConnected) return;
					applied = true;
					const actual = panelStatus(reply);
					if (actual && (!actual.pending || !requested || actual.requested_mode === requested)) {
						panelState = actual;
						panelBusy = panelMoving = actual.pending;
						panelError =
							actual.error ||
							(!actual.pending && requested && actual.mode !== requested
								? "The panel stayed in its previous mode. Select Retry to check its current state."
								: "");
					} else {
						panelBusy = panelMoving = method === "panel_status" && wasMoving;
						panelError = reply.error
							? panelProblem(reply)
							: "Framey did not confirm the panel mode. The last confirmed choice is shown; select Retry to check again.";
					}
					if (panelError) {
						const mode = panelModes.find(mode => mode.id === (requested || (actual && actual.requested_mode)));
						panelReport = supportError(
							panelError + (mode ? " Requested panel mode: " + mode.label + "." : ""),
							method + "_failed",
						);
					}
					paintPanel();
				};
				const finishPanel = reply => {
					if (panelClosed || serial !== panelSerial) return;
					panelInFlight = false;
					accept(reply);
					if (!applied) {
						panelBusy = panelMoving;
						panelError = "Panel status is unavailable. Select Retry to check its current state.";
						paintPanel();
					}
				};
				return call(method, arg).then(finishPanel, error =>
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
				const done = k.guard(r => {
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
				});
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
				k.hold(true);
				if (el) {
					el.textContent = T("c.checking");
					el.style.color = GREY;
				}
				call("loader_check").then(
					k.guard(r => {
						k.hold(false);
						lres = r.loader || null;
						cand = r.candidate || (lres && lres.candidate) || null;
						info = r.installed || null;
						line = lres ? null : T("c.failed", { msg: r.error || T("c.noanswer") });
						if (r.error || (lres && lres.state === "error")) supportError(r.error || lres.msg, "loader_check_failed");
						if (after && checkedCandidate(cand) && lres && lres.state !== "error") return after();
						if (after && !line && (!lres || lres.state !== "current"))
							line = T("c.failed", { msg: (lres && lres.msg) || T("c.noanswer") });
						home();
					}),
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
							const b = k.btn(T("c.back"), back);
							put(out, col(GOOD, T("s.restarting"), 21, true), row(b));
							b.focus();
							return out;
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
				return [control, col(GREY, T("s.workers_text"), 16)];
			};
			const finish = (method, title, okKey) =>
				k.work(
					title,
					() => call(method),
					(r, out) => k.result(out, r, T(okKey), back, r.error ? supportError(r.error, method + "_failed") : undefined),
				);
			const home = () => {
				const p = obj(st.prefs);
				const autoUnavailable =
					auto.error ||
					typeof auto.enabled !== "boolean" ||
					(auto.enabled &&
						(!Number.isFinite(auto.last) || auto.last < 0 || !Number.isFinite(new Date(auto.last * 1000).getTime())));
				const autoText = autoUnavailable
					? T("c.unavailable") + (auto.error ? " — " + T("c.failed", { msg: String(auto.error).slice(0, 200) }) : "")
					: auto.enabled
						? auto.last
							? T("s.auto_last", { d: new Date(auto.last * 1000).toLocaleString() })
							: T("s.auto_never")
						: T("s.auto_off");
				if (autoUnavailable && !supportContext.error)
					supportError(auto.error || T("c.unavailable"), "autocheck_status_unavailable");
				const dev = p.dev && p.dev.enabled === true;
				const rv = (window.FY_RUNTIME || {}).version;
				const iv = (info && info.version) || rv;
				const c = tx("background:#181826;border-radius:14px;padding:12px;margin-bottom:12px");
				const sum = col(GREY, "", 19);
				const state = lres && lres.state;
				const v = line
					? [line, BAD]
					: state === "current"
						? [T("s.current"), GOOD]
						: state === "update"
							? lres.downgrade
								? [T("s.older"), WARN]
								: [T("s.update_avail"), UP]
							: state === "error"
								? [T("s.check_failed", { msg: lres.msg }), BAD]
								: ["", GREY];
				sum.textContent = v[0];
				sum.style.color = v[1];
				put(
					c,
					tx("font-size:22px;font-weight:700", T("s.framey")),
					col(
						GREY,
						iv
							? T("s.installed", { v: iv, c: ld.commit || T("c.unknown") })
							: T("s.installed_c", { c: ld.commit || T("c.unknown") }),
					),
					col("#fff", cand ? identity(cand) : T("s.not_checked"), 18),
					cand && cand.prerelease && col(WARN, cand.label || T("s.prerelease"), 18),
					sum,
					(line || state === "error") && row(k.reportButton(supportContext)),
					col(GREY, T("s.ch_ro", { channel: p.channel === "prerelease" ? "prerelease" : "stable" }), 16),
					row(
						btn(T("s.check"), () => check(sum), { primary: true }),
						btn(T("s.update"), updateAsk, { primary: state === "update" }),
					),
					row(
						btn(T("s.notes"), () => k.notes("_loader", cand.tag, T("s.notes_title", { tag: cand.tag }), home), {
							disabled: !cand,
						}),
						btn(T("s.history"), () => k.versions("_loader", T("s.history"), back)),
					),
					ld.kept && col("#fff", T("s.kept"), 19, true),
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
				);
				k.show(
					tx(TITLE, T("s.title")),
					dmg(k, ss, back),
					c,
					panelSection,
					pref(T("s.auto"), "autocheck", p.autocheck === true, draw),
					col(GREY, autoText, 16),
					pref(T("s.notify_loader"), "notify_loader", p.notify_loader === true),
					pref(T("s.notify_plugins"), "notify_plugins", p.notify_plugins === true),
					...maintBox(),
					...workerBox(),
					col(GREY, T("s.lang"), 16),
					col(GREY, T("s.safe_ro", { state: T(st.safe === true ? "s.on" : "s.off") }), 16),
					col(GREY, T("s.safe_text"), 16),
					col(GREY, T("s.dev_ro", { state: T(dev ? "s.on" : "s.off") }), 16),
					dev && row(btn(T("s.dev_open"), lazyDev), btn(T("s.diag"), lazyDev)),
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
					col(GREY, T("s.trust"), 18),
					!line && state !== "error" && row(k.reportButton(supportContext)),
				);
				paintPanel();
				if (!panelAttempted) requestPanel("panel_status");
			};
			let unlisten = null;
			try {
				unlisten = privateApi.listen("panel_mode", value => {
					if (panelClosed || !k.alive() || !panelSection.isConnected) return;
					const actual = panelStatus(value);
					if (!actual) return;
					panelSerial++;
					panelAttempted = true;
					panelInFlight = false;
					panelState = actual;
					panelBusy = panelMoving = actual.pending;
					panelError = actual.error;
					panelReport = panelError ? supportError(panelError, "panel_mode_failed") : null;
					paintPanel();
				});
			} catch (error) {
				if (typeof privateApi.diagnostic === "function") privateApi.diagnostic("core.event_failed");
			}
			stopPanelUpdates = () => {
				panelClosed = true;
				panelSerial++;
				if (typeof unlisten === "function") unlisten();
			};
			draw();
		},
	};
	Object.defineProperty(framey.pages, "settings", {
		value: Object.freeze({ render: settings.render, leave: leaveSettings, dispose: leaveSettings }),
		enumerable: true,
	});
	Object.freeze(framey.pages);
})();
