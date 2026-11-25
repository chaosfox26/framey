(() => {
	const framey = __FY;
	const privateApi = framey && framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
	if (!privateApi) return;
	Object.defineProperty(framey, "pages", { value: framey.pages || {} });
	if (privateApi.kit) return;
	const GREY = "#8a8aa0",
		GOOD = "#7adf9a",
		BAD = "#ff7a7a",
		UP = "#8fb0ff",
		WARN = "#e0a030";
	const TITLE = "font-size:28px;font-weight:700;margin-bottom:12px;overflow-wrap:anywhere";
	const DANGER = { css: "flex:1;background:#5a3a3a" };
	const T = (key, vars) => framey.i18n.t("core", key, vars);
	const recordDiagnostic = code => {
		if (typeof privateApi.diagnostic === "function") privateApi.diagnostic(code);
	};
	const obj = r => (r && typeof r === "object" ? r : {});
	const call = (method, arg) => privateApi.call(method, arg).then(obj);
	const tx = (css, text) => framey.el(css, text);
	const put = (p, ...c) => {
		p.append(...c.filter(Boolean));
		return p;
	};
	const col = (color, text, size, bold) =>
		tx(
			"font-size:" +
				(size || 19) +
				"px;margin-top:6px;white-space:pre-wrap;word-break:break-word;color:" +
				color +
				(bold ? ";font-weight:700" : ""),
			text,
		);
	const row = (...c) => put(tx("display:flex;gap:10px;margin-top:10px;align-items:stretch"), ...c);
	const say = text => {
		try {
			sessionStorage.fyMsg = text;
		} catch (error) {
			recordDiagnostic("core.storage_failed");
		}
		framey.toast(text);
	};
	const warn = text => framey.toast(text, 4000);
	const day = x => String(x || "").slice(0, 10);
	const MT = {
		archived: ["mt.archived", "\u25A0", WARN],
		declared: ["mt.declared", "\u25B2", WARN],
		recent: ["mt.recent", "\u25CF", GOOD],
		none: ["mt.none", "\u25CB", GREY],
		unknown: ["mt.unknown", "\u25C7", GREY],
	};
	const mt = r => MT[obj(r).label] || MT.unknown;
	const when = s => (typeof s === "number" && s > 0 ? new Date(s * 1000).toLocaleString() : "");
	const yes = v => T(v === true ? "mt.yes" : "mt.no");
	let U;
	const ui = () => {
		if (!U) {
			const api = { id: "core" };
			(framey.ext || []).forEach(extension => {
				try {
					extension(api, { id: "core", meta: {}, dead: false }, []);
				} catch (error) {
					recordDiagnostic("core.callback_failed");
				}
			});
			U = api.ui;
		}
		return U;
	};
	let watch = null;
	let live = null;
	privateApi.listen("progress", ev => {
		if (watch) watch(ev);
	});
	let recentResult = null;
	const keep = v => {
		recentResult = v && v.fullRows ? v : null;
		try {
			if (v) sessionStorage.fyRes = JSON.stringify(v);
			else sessionStorage.removeItem("fyRes");
		} catch (error) {
			recordDiagnostic("core.storage_failed");
		}
	};
	const clip = (x, n) => String(x == null ? "" : x).slice(0, n);
	const reportContext = (plugin, error, code) => {
		const context = {
			error: typeof error === "string" ? error.slice(0, 600) : "",
			code: typeof code === "string" ? code.slice(0, 64) : "management_error",
		};
		if (plugin && typeof plugin === "object") {
			const metadata = {};
			for (const key of ["name", "version"]) {
				if (typeof plugin[key] === "string") metadata[key] = plugin[key].slice(0, 120);
			}
			if (Object.keys(metadata).length) {
				metadata.source = ["decky_stable", "decky_testing", "framey", "local"].includes(plugin.source)
					? plugin.source
					: typeof plugin.source === "string" && plugin.source
						? "framey"
						: "local";
				context.plugin = metadata;
			}
		}
		return context;
	};
	const openReport = context => {
		const unavailable = () => {
			warn(T("c.report") + ": " + T("c.unavailable"));
		};
		if (typeof privateApi.report !== "function") return Promise.resolve(unavailable());
		try {
			return Promise.resolve(privateApi.report(context)).catch(() => {
				recordDiagnostic("core.callback_failed");
				return unavailable();
			});
		} catch (error) {
			recordDiagnostic("core.callback_failed");
			return Promise.resolve(unavailable());
		}
	};
	const rd = () => {
		if (recentResult) return recentResult;
		let v = null;
		try {
			v = JSON.parse(sessionStorage.fyRes || "null");
		} catch (error) {
			recordDiagnostic("core.storage_failed");
		}
		if (!v || typeof v !== "object" || !Array.isArray(v.rows)) {
			if (v !== null) keep(null);
			return null;
		}
		const rows = v.rows
			.slice(0, 200)
			.filter(r => Array.isArray(r) && typeof r[0] === "string" && typeof r[1] === "string")
			.map(r => [r[0], clip(r[1], 300)]);
		return { good: v.good === true, text: clip(v.text, 300), rows, savedSummary: true };
	};
	const pack = (good, text, rows) => {
		const v = {
			good: !!good,
			text: clip(text, 300),
			rows: rows.slice(0, 32).map(r => [r[0], clip(r[1], 300)]),
		};
		while (v.rows.length && JSON.stringify(v).length > 8000) v.rows.pop();
		return v;
	};
	const GOODSTATE = ["ok", "current", "skipped"];
	const line = x => {
		const n = x.name || x.id;
		return x.state === "ok"
			? ["ok", x.to ? T("m.r_ok", { name: n, to: vv(x.to) }) : T("m.r_changed", { name: n })]
			: x.state === "current"
				? ["current", T("m.r_current", { name: n })]
				: x.state === "skipped"
					? ["skipped", T("m.r_skipped", { name: n, reason: x.reason || "" })]
					: ["failed", T("m.r_failed", { name: n, error: x.error || T("c.noanswer") })];
	};
	const batchPack = r => {
		const base = Array.isArray(r.results)
			? r.results
			: r.id
				? [
						{
							id: r.id,
							name: r.name,
							state: r.error ? "failed" : r.current ? "current" : "ok",
							to: r.version || r.to,
							error: r.error,
						},
					]
				: [];
		const extra = (Array.isArray(r.failures) ? r.failures : [])
			.filter(f => f && typeof f === "object" && !base.some(x => x && x.id === f.id))
			.map(f => ({ id: f.id, name: f.name, state: "failed", error: f.error || f.msg || f.reason }));
		const rows = base.concat(extra).filter(x => x && typeof x === "object");
		const bad = rows.filter(x => !GOODSTATE.includes(x.state));
		const ordered = rows.length > 32 ? bad.concat(rows.filter(x => !bad.includes(x))) : rows;
		const okn = typeof r.ok === "number" ? r.ok : rows.filter(x => x.state === "ok").length;
		const failn = typeof r.failed === "number" ? r.failed : bad.length;
		const result = pack(
			!failn && !r.error,
			r.error && !rows.length ? T("c.failed", { msg: r.error }) : T("m.summary", { ok: okn, failed: failn }),
			ordered.map(line),
		);
		Object.defineProperty(result, "fullRows", { value: rows.map(line) });
		Object.defineProperty(result, "fullText", {
			value: r.error && !rows.length ? T("c.failed", { msg: r.error }) : result.text,
		});
		return result;
	};
	const paintRes = (k, out, v, done) => {
		if (k.bounded)
			return k.show(
				tx(TITLE, T("m.res_title")),
				col(v.good ? GOOD : WARN, k.windowFocused && v.fullText ? v.fullText : v.text, 21, true),
				k.windowFocused &&
					v.savedSummary &&
					col(GREY, "Saved summary. Details from an earlier session may be shortened.", 17),
				row(k.btn(T("c.back"), done)),
				!v.good && k.reportButton(reportContext(null, v.text, "management_batch_failed")),
				...(k.windowFocused && v.fullRows ? v.fullRows : v.rows).map(x =>
					col(x[0] === "ok" ? GOOD : x[0] === "failed" ? BAD : GREY, x[1], 18),
				),
			);
		const back = k.btn(T("c.back"), done);
		put(
			out,
			col(v.good ? GOOD : WARN, v.text, 21, true),
			row(back, !v.good && k.reportButton(reportContext(null, v.text, "management_batch_failed"))),
		);
		back.focus();
		v.rows.forEach(x => put(out, col(x[0] === "ok" ? GOOD : x[0] === "failed" ? BAD : GREY, x[1], 18)));
	};
	privateApi.listen("maint", ev => {
		if (live && live.maint) live.maint(obj(ev).ids);
	});
	privateApi.listen("update_result", (ev, info) => {
		const v = batchPack(obj(ev));
		keep(v);
		const seen = live && live.box.isConnected && (!info.open || info.tab === "#modules") && live.idle();
		if (seen) live.show(v);
		if (!seen || !framey.panelOpen) framey.toast(v.text, 4000);
	});

	const owners = new WeakMap();
	const make = box => {
		box.tabIndex = -1;
		box.style.outline = "none";
		let held = false;
		let view = 0;
		const k = { names: () => "" };
		const navigation = new WeakMap();
		let lastFocused = null;
		box.onfocusin = ev => {
			lastFocused = ev.target;
		};
		k.bounded = false;
		owners.set(box, k);
		k.alive = () => owners.get(box) === k && box.isConnected;
		k.guard = fn => {
			const at = view;
			return (...args) => {
				if (k.alive() && view === at) return fn(...args);
			};
		};
		k.cancel = () => {
			view++;
			held = false;
			box.style.opacity = "1";
			watch = null;
		};
		k.hold = on => {
			if (!k.alive()) return;
			held = on;
			box.style.opacity = on ? ".5" : "1";
		};
		let want = null;
		const showNodes = (...n) => {
			if (!k.alive()) return;
			if (typeof privateApi.clearPageNavigation === "function") privateApi.clearPageNavigation();
			k.cancel();
			const a = document.activeElement;
			if (a !== box && box.contains(a))
				want = {
					label: a.getAttribute("aria-label") || "",
					at: [...box.querySelectorAll("[tabindex]")].indexOf(a),
				};
			box.replaceChildren(...n.filter(Boolean));
			if (want === null) return;
			const x = document.activeElement;
			if (x !== document.body && !box.contains(x)) {
				want = null;
				return;
			}
			const all = [...box.querySelectorAll("[tabindex]")];
			const same = all[Math.min(want.at, all.length - 1)];
			const t =
				all.find(e => e.getAttribute("aria-label") === want.label && e.tabIndex >= 0) ||
				(same && same.tabIndex >= 0 ? same : box.querySelector("[tabindex='0']")) ||
				box;
			t.focus();
			want = null;
		};
		k.rawShow = showNodes;
		k.pages = (title, nodes, options) => {
			const o = options || {};
			const focused = box.contains(document.activeElement)
				? document.activeElement
				: box.contains(lastFocused)
					? lastFocused
					: null;
			const previousLabel = focused && focused.getAttribute("aria-label");
			const replacementLabel =
				previousLabel === T("m.more") ? T("m.less") : previousLabel === T("m.less") ? T("m.more") : previousLabel;
			const units = [];
			let back = o.back;
			const add = node => {
				if (!node) return;
				if (node.nodeType === 3) {
					if (node.textContent.trim()) add(col("#fff", node.textContent));
					return;
				}
				if (node.matches("[data-fy-esc]")) {
					back = back || navigation.get(node);
					return;
				}
				if (node.matches(".fy-c,input,textarea,select,[data-fy-live-work],[role='progressbar'],[role='status']")) {
					if (node.matches("[role='button']") && node.textContent.length > 64) {
						const full = node.textContent;
						add(col("#fff", full));
						node.textContent = full.slice(0, 48) + "…";
					}
					node.style.flex = "none";
					node.style.width = "100%";
					node.style.boxSizing = "border-box";
					if (node.matches("input,textarea,select")) node.style.minHeight = "48px";
					units.push(node);
					return;
				}
				if (node.childElementCount) {
					[...node.childNodes].forEach(add);
					return;
				}
				const value = node.textContent;
				if (!value.trim()) return;
				let remaining = value;
				while (remaining.length) {
					let end = Math.min(64, remaining.length);
					const lineBreak = remaining.search(/[\r\n\u2028\u2029]/);
					if (lineBreak >= 0 && lineBreak < end)
						end = lineBreak + (remaining[lineBreak] === "\r" && remaining[lineBreak + 1] === "\n" ? 2 : 1);
					else if (end < remaining.length) {
						const space = remaining.lastIndexOf(" ", end);
						if (space > 32) end = space + 1;
					}
					const part = tx(node.style.cssText, remaining.slice(0, end));
					part.style.margin = "0";
					part.style.lineHeight = "1.25";
					units.push(part);
					remaining = remaining.slice(end);
				}
			};
			(nodes || []).forEach(add);
			const restoreFocus = units.find(node => node.getAttribute("aria-label") === replacementLabel && replacementLabel);
			if (!o.preserve) showNodes();
			const at = view;
			let first = true;
			let index = Math.max(0, o.index | 0);
			const render = (next, anchor) => {
				if (!k.alive() || view !== at) return;
				const size = privateApi.panelLayout && privateApi.panelLayout() === "window" ? 4 : 1;
				const screens = [];
				let screen = [];
				for (const unit of units) {
					if (
						screen.length &&
						(screen.length >= size ||
							unit.hasAttribute("data-fy-live-work") ||
							screen[0].hasAttribute("data-fy-live-work"))
					) {
						screens.push(screen);
						screen = [];
					}
					screen.push(unit);
				}
				if (screen.length || !screens.length) screens.push(screen);
				const count = screens.length;
				if (anchor) {
					const found = screens.findIndex(nodes => nodes.some(node => node === anchor || node.contains(anchor)));
					if (found >= 0) next = found;
				} else if (first && restoreFocus) next = screens.findIndex(nodes => nodes.includes(restoreFocus));
				index = Math.max(0, Math.min(count - 1, next | 0));
				const active = document.activeElement;
				box.replaceChildren(...screens[index]);
				box.style.display = "flex";
				box.style.flexDirection = "column";
				box.style.gap = "8px";
				if (typeof privateApi.setPageNavigation === "function")
					privateApi.setPageNavigation({
						index,
						count,
						title,
						onPage: render,
						onBack:
							back &&
							(() => {
								if (!k.alive() || view !== at) return;
								if (!o.preserveBack) k.cancel();
								return back();
							}),
						onLayout: () =>
							render(index, box.contains(document.activeElement) ? document.activeElement : box.firstElementChild),
					});
				if (anchor && anchor.isConnected) anchor.focus();
				else if (first && restoreFocus) restoreFocus.focus();
				else if (active && !active.isConnected && active !== document.body)
					(box.querySelector("[tabindex='0'],input,textarea,select") || box).focus();
				first = false;
			};
			render(index);
		};
		k.show = (...nodes) => {
			if (!k.bounded) return showNodes(...nodes);
			const n = nodes.filter(Boolean);
			const heading = n[0] && n[0].style.fontSize === "28px" ? n.shift().textContent : "";
			return k.pages(heading, n);
		};
		box.onkeydown = ev => {
			const d =
				ev.key === "ArrowDown" || ev.key === "ArrowRight" ? 1 : ev.key === "ArrowUp" || ev.key === "ArrowLeft" ? -1 : 0;
			if (d) {
				if (
					ev.target.closest(
						"input,textarea,select,[contenteditable=''],[contenteditable='true'],[role='slider'],[role='listbox'],[role='combobox']",
					)
				)
					return;
				const all = [...box.querySelectorAll("[tabindex]")].filter(
					e => e.tabIndex >= 0 && e.getAttribute("aria-disabled") !== "true",
				);
				const i = all.indexOf(ev.target);
				if (i < 0 || !all[i + d]) return;
				ev.preventDefault();
				all[i + d].focus();
				return;
			}
			const b = ev.key === "Escape" && box.querySelector("[data-fy-esc]");
			if (!b || b.getAttribute("aria-disabled") === "true") return;
			ev.preventDefault();
			b.click();
		};
		k.btn = (label, fn, o) => {
			const nav = label === T("c.back") || label === T("c.cancel");
			const e = ui().button(
				Object.assign(
					{
						label,
						css: "flex:1",
						nav,
						onclick: () => {
							if (!k.alive() || !e.isConnected || (held && !nav)) return;
							if (nav) k.cancel();
							return fn();
						},
					},
					o,
				),
			);
			if (nav) {
				e.dataset.fyEsc = "1";
				navigation.set(e, fn);
			}
			return e;
		};
		k.reportButton = context => k.btn(T("c.report"), () => openReport(context));
		k.sw = (label, value, fn) => {
			const e = ui().toggle({
				label,
				value,
				onchange: v => {
					if (!k.alive()) return;
					if (held) e.value = !v;
					else fn(v, e);
				},
			});
			return e;
		};
		k.ask = (title, text, acts, cancel) => {
			const c = k.btn(T("c.cancel"), cancel);
			want = null;
			k.show(tx(TITLE, title), col("#fff", text, 20), ...acts.map(a => row(k.btn(a[0], a[1], a[2]))), row(c));
			c.focus();
		};
		k.prog = () => {
			const bar = tx("display:none;height:14px;margin-top:10px;border-radius:7px;background:#2a2a3a;overflow:hidden");
			const fill = tx("height:100%;width:0;background:#5585ff");
			const head = col(UP, "", 21, true),
				list = tx(""),
				pct = col(GREY, "");
			head.setAttribute("role", "status");
			head.setAttribute("aria-live", "polite");
			head.setAttribute("aria-atomic", "true");
			bar.setAttribute("role", "progressbar");
			bar.setAttribute("aria-valuemin", "0");
			bar.setAttribute("aria-valuemax", "100");
			const progressBox = put(tx(""), head, list, bar, pct);
			if (k.bounded) progressBox.dataset.fyLiveWork = "1";
			const lines = new Map();
			let job = null;
			bar.append(fill);
			return {
				el: progressBox,
				details: () => [...lines.values()].map(node => col(node.style.color, node.textContent, 18)),
				update(ev) {
					if (!ev || typeof ev.stage !== "string") return;
					if (ev.job !== job) {
						job = ev.job;
						lines.clear();
						list.replaceChildren();
					}
					const who = k.names(ev.id);
					const label = String(ev.label || ev.stage);
					const text = T("p.step", {
						who: who ? who + ": " : "",
						n: ev.index | 0,
						total: ev.total | 0,
						label,
					});
					if (head.textContent !== text) head.textContent = text;
					let l = lines.get(ev.stage);
					if (!l) {
						l = col("#fff", "", 18);
						lines.set(ev.stage, l);
						list.append(l);
					}
					const state = T("p." + (ev.state === "ok" || ev.state === "fail" ? ev.state : "running"));
					l.textContent = ev.error ? T("p.line_err", { label, state, error: ev.error }) : T("p.line", { label, state });
					l.style.color = ev.state === "fail" ? BAD : ev.state === "ok" ? GOOD : "#fff";
					const m = ev.measured;
					const real = !!m && typeof m.done === "number" && typeof m.total === "number" && m.total > 0;
					const p = real ? Math.max(0, Math.min(100, Math.round((m.done / m.total) * 100))) : 0;
					bar.style.display = real ? "block" : "none";
					bar.setAttribute("aria-label", label);
					if (real) bar.setAttribute("aria-valuenow", String(p));
					else bar.removeAttribute("aria-valuenow");
					fill.style.width = p + "%";
					pct.textContent = real ? p + "%" : "";
					if (k.bounded) {
						list.replaceChildren();
						head.textContent =
							(ev.index | 0) +
							"/" +
							(ev.total | 0) +
							" " +
							clip(label, 24) +
							" — " +
							state +
							(real ? " (" + p + "%)" : "");
						head.style.margin = "0";
						bar.style.marginTop = "8px";
						pct.textContent = "";
					}
				},
			};
		};
		k.work = (title, run, finish) => {
			const p = k.prog(),
				out = tx("");
			k.hold(false);
			const nav =
				typeof k.back === "function"
					? put(tx(""), col(GREY, T("c.work_background"), 18), row(k.btn(T("c.back"), k.back)))
					: null;
			if (k.bounded) {
				const progressScreen = (preserve = false) =>
					k.pages(
						title,
						[
							p.el,
							k.btn("Progress details", () =>
								k.pages(title, p.details(), {
									back: () => progressScreen(true),
									preserve: true,
									preserveBack: true,
								}),
							),
						],
						{ back: typeof k.back === "function" ? k.back : undefined, preserve },
					);
				progressScreen();
			} else k.show(tx(TITLE, title), p.el, nav, out);
			const progress = k.guard(p.update);
			watch = progress;
			return run().then(
				k.guard(r => {
					if (watch === progress) watch = null;
					if (nav) nav.remove();
					finish(r, out);
				}),
				k.guard(e => {
					if (watch === progress) watch = null;
					if (nav) nav.remove();
					if (k.bad) k.bad(e);
				}),
			);
		};
		k.result = (out, r, okText, back, context) => {
			if (!r.error && okText && ("ok" in r || "staged" in r)) keep(pack(true, okText, []));
			const b = k.btn(T("c.back"), () => {
				keep(null);
				back();
			});
			const report = r.error && k.reportButton(reportContext(obj(context).plugin, r.error, obj(context).code));
			if (k.bounded)
				return k.show(
					col(r.error ? BAD : GOOD, r.error ? T("c.failed", { msg: r.error }) : okText, 21, true),
					r.error && r.stage && col(GREY, T("c.stage", { stage: r.stage })),
					row(b, report),
				);
			put(
				out,
				col(r.error ? BAD : GOOD, r.error ? T("c.failed", { msg: r.error }) : okText, 21, true),
				r.error && r.stage && col(GREY, T("c.stage", { stage: r.stage })),
				row(b, report),
			);
			b.focus();
			return out;
		};
		k.notes = (id, tag, title, back) => {
			k.show(tx(TITLE, title), row(k.btn(T("c.back"), back)), col(GREY, T("c.loading")));
			return call("notes", { id, tag })
				.then(
					k.guard(r =>
						k.show(
							tx(TITLE, title),
							row(k.btn(T("c.back"), back)),
							col(r.error ? BAD : "#fff", r.error || r.notes || T("v.nonotes")),
						),
					),
				)
				.catch(
					k.guard(error => {
						if (k.bad) k.bad(error);
					}),
				);
		};
		k.versions = (id, title, back, gate, plugin) => {
			let rows = [],
				more = false,
				page = 1,
				info = "",
				err = "";
			const g = gate || (f => f());
			const eligible = v => v.eligible === true && v.current !== true;
			const reason = v => v.reason || T(v.current === true ? "v.installed" : "c.unavailable");
			const vlabel = v => v.version + (v.current ? "  " + T("v.installed") : "") + (v.label ? "  " + v.label : "");
			const vsub = v =>
				[day(v.published), v.downgrade ? T("v.older") : "", !eligible(v) ? reason(v) : ""]
					.filter(Boolean)
					.join("  |  ");
			const list = () =>
				k.show(
					tx(TITLE, title),
					row(k.btn(T("c.back"), back)),
					info && col(GREY, info),
					err && col(BAD, err),
					err &&
						row(
							k.reportButton(reportContext(plugin || (id !== "_loader" ? { id } : null), err, "version_list_failed")),
						),
					k.bounded
						? put(tx(""), ...rows.map(v => k.btn(vlabel(v), () => detail(v))), !rows.length && col(GREY, T("v.none")))
						: ui().list({
								items: rows.map(v => ({ id: v.tag, label: vlabel(v), sub: vsub(v) })),
								onselect: tag => detail(rows.find(v => v.tag === tag)),
								empty: T("v.none"),
							}),
					more && row(k.btn(T("v.more"), () => load(page + 1))),
				);
			const detail = v => {
				const cm = obj(v.compat);
				k.show(
					tx(TITLE, vlabel(v)),
					k.bounded && col(GREY, vsub(v)),
					v.published && col(GREY, T("v.published", { d: day(v.published) })),
					cm.msg && col(cm.badge === "ok" ? GOOD : GREY, cm.msg),
					v.downgrade && col(WARN, T("v.limits")),
					!eligible(v) && col(GREY, reason(v)),
					row(
						k.btn(T(v.downgrade ? "v.goback" : "v.install"), () => install(v, false), {
							disabled: !eligible(v),
							primary: !v.downgrade,
						}),
						k.btn(T("v.notes"), () => k.notes(id, v.tag, vlabel(v), () => detail(v))),
					),
					row(k.btn(T("c.back"), list)),
				);
			};
			const install = (v, ok) => {
				if (!eligible(v)) return;
				if (v.downgrade && !ok)
					return k.ask(
						T("v.confirm_title", { v: v.version }),
						T("v.confirm_text", { v: v.version }),
						[[T("v.goback"), () => install(v, true), DANGER]],
						() => detail(v),
					);
				g(() =>
					k.work(
						T("v.installing", { v: v.version }),
						() => call("ver_install", { id, tag: v.tag, confirm: !!ok }),
						(r, out) => {
							if (r.confirm)
								return k.ask(
									T("v.confirm_title", { v: v.version }),
									r.confirm,
									[[T("v.goback"), () => install(v, true), DANGER]],
									() => detail(v),
								);
							if (r.current === true) return k.result(out, {}, T("m.already"), back);
							const result = r.error || r.ok || r.staged ? r : { error: T("c.noanswer") };
							k.result(
								out,
								result,
								T("v.installed_ok", {
									v: typeof r.version === "string" && r.version ? r.version : v.version,
								}),
								back,
								reportContext(plugin || (id !== "_loader" ? { id } : null), "", "version_install_failed"),
							);
						},
					),
				);
			};
			const load = pg => {
				k.hold(true);
				return call("ver_list", { id, page: pg })
					.then(
						k.guard(r => {
							k.hold(false);
							if (r.error) err = r.error;
							else {
								err = "";
								page = pg;
								rows = rows.concat(
									(Array.isArray(r.versions) ? r.versions : []).filter(
										v => v && typeof v === "object" && typeof v.tag === "string",
									),
								);
								more = !!r.more;
								info = r.note || info;
							}
							list();
						}),
					)
					.catch(
						k.guard(error => {
							k.hold(false);
							if (k.bad) k.bad(error);
						}),
					);
			};
			k.show(tx(TITLE, title), row(k.btn(T("c.back"), back)), col(GREY, T("c.loading")));
			return load(1);
		};
		return k;
	};

	const COMPAT = new Map([
		["ok", ["m.cp_ok", GOOD]],
		["newer", ["m.cp_newer", WARN]],
		["older", ["m.cp_older", WARN]],
		["platform", ["m.cp_platform", WARN]],
		["api", ["m.cp_api", WARN]],
	]);
	const compat = cm => {
		const e = COMPAT.get(obj(cm).badge);
		return col(e ? e[1] : GREY, e ? T(e[0]) + (cm.msg ? ": " + cm.msg : "") : T("m.cp_unknown"), 17);
	};
	const verdict = (s, none) =>
		!s
			? none
				? [none + "\n" + T("m.nosource_hint"), GREY]
				: ["", GREY]
			: s.state === "current"
				? [T("m.current"), GOOD]
				: s.state === "update"
					? [T("m.update_avail", { v: s.latest }) + (s.known === false ? " " + T("m.commit_unknown_short") : ""), UP]
					: s.state === "none"
						? [s.msg + "\n" + T("m.nosource_hint"), GREY]
						: [T("m.check_failed", { msg: s.msg }), BAD];
	const vv = x => (x ? (/^\d/.test(x) ? T("c.ver", { v: x }) : x) : T("c.unknown"));
	const dmg = (k, state, back) => {
		const frame = "border:3px solid " + BAD + ";border-radius:14px;padding:12px;margin-bottom:12px";
		if (state === "unreadable") return put(tx(frame), col(BAD, T("sd.unreadable"), 20, true));
		if (state !== "corrupt") return null;
		const repair = () =>
			k.ask(
				T("sd.repair_title"),
				T("sd.repair_text"),
				[
					[
						T("sd.repair_ok"),
						() =>
							call("settings_repair").then(
								k.guard(r => {
									if (r.ok) say(T("sd.repaired"));
									else warn(r.error || T("c.noanswer"));
									back();
								}),
							),
						{ primary: true },
					],
				],
				back,
			);
		return put(tx(frame), col(BAD, T("sd.corrupt"), 20, true), row(k.btn(T("sd.repair"), repair, { primary: true })));
	};

	const managementWindow = (k, box) => {
		k.windowFocused = true;
		const control = (node, label) => {
			node.dataset.fy = "core";
			node.setAttribute("aria-label", label);
			node.tabIndex = 0;
			node.style.cssText =
				"width:100%;min-width:0;box-sizing:border-box;height:44px;flex:none;background:#18232e;color:#fff;border:1px solid #586776;border-radius:5px;padding:7px 10px;font:inherit;font-size:18px";
			return node;
		};
		const input = (label, value = "") => {
			const node = control(document.createElement("input"), label);
			node.placeholder = label;
			node.value = value;
			return node;
		};
		const select = (label, values, change) => {
			const node = control(document.createElement("select"), label);
			values.forEach(([value, text]) => {
				const option = document.createElement("option");
				option.value = value;
				option.textContent = text;
				node.append(option);
			});
			node.onchange = () => {
				if (k.alive()) change(node.value);
			};
			return node;
		};
		const small = text => tx("font-size:17px;line-height:1.25;color:#b9c6d2;overflow-wrap:anywhere", text);
		const heading = text => tx("font-size:23px;font-weight:700;line-height:1.2;overflow-wrap:anywhere", text);
		const grid = nodes =>
			put(
				tx(
					"display:grid;grid-template-columns:repeat(" +
						(nodes.filter(Boolean).length === 1 ? 1 : 2) +
						",minmax(0,1fr));gap:8px",
				),
				...nodes,
			);
		const mount = (nodes, preserve = false) => {
			if (!k.alive()) return;
			if (typeof k.windowContext === "function") k.windowContext();
			if (preserve) box.replaceChildren(...nodes.filter(Boolean));
			else k.rawShow(...nodes);
			box.style.cssText += ";display:flex;flex-direction:column;gap:8px;min-height:0;overflow:hidden";
			box.querySelectorAll("[role='button'],[role='switch']").forEach(node => {
				node.style.minHeight = "44px";
				node.style.height = "auto";
				node.style.margin = "0";
				node.style.padding = "7px 10px";
				node.style.fontSize = "18px";
				node.style.lineHeight = "1.2";
				node.style.borderRadius = "5px";
				node.style.minWidth = "0";
				node.style.overflowWrap = "anywhere";
			});
		};
		const pieces = value => {
			const parts = [];
			let text = String(value == null ? "" : value);
			while (text.length) {
				let end = Math.min(box.clientWidth >= 640 ? 240 : 96, text.length);
				if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1]) && /[\uDC00-\uDFFF]/.test(text[end])) end--;
				if (text[end - 1] === "\r" && text[end] === "\n") end--;
				const breaks = [...text.slice(0, end).matchAll(/\r\n|[\n\r\u2028\u2029]/g)];
				if (breaks.length > 2) end = breaks[2].index + breaks[2][0].length;
				parts.push(text.slice(0, end));
				text = text.slice(end);
			}
			return parts.length ? parts : [""];
		};
		const reader = value => {
			const parts = pieces(value);
			const body = tx(
				"font-size:18px;line-height:1.3;white-space:pre-wrap;overflow-wrap:anywhere;min-height:118px",
				parts[0],
			);
			const pick =
				parts.length > 1 &&
				select(
					"Text segment",
					parts.map((_, i) => [i === parts.length - 1 ? "last" : String(i), i + 1 + " / " + parts.length]),
					value => {
						body.textContent = parts[value === "last" ? parts.length - 1 : Number(value)];
					},
				);
			return [pick, body].filter(Boolean);
		};
		const actions = nodes => {
			const slot = tx(""),
				labels = nodes.map(node => node.getAttribute("aria-label") || node.textContent);
			const choose = value => {
				const node = nodes[Number(value)];
				node.textContent = "Run action";
				node.setAttribute("aria-label", "Run action");
				slot.replaceChildren(node);
			};
			const picker = select(
				"Actions",
				labels.map((label, i) => [String(i), label]),
				choose,
			);
			[...picker.options].forEach((option, i) => {
				option.value = labels[i];
			});
			picker.onchange = () => {
				if (k.alive()) choose(String(picker.selectedIndex));
			};
			choose("0");
			return [picker, slot];
		};
		const inspect = (title, values, back) => {
			const fields = [];
			const add = (value, path) => {
				if (value && typeof value === "object") {
					const entries = Object.entries(value);
					if (!entries.length) fields.push([path, Array.isArray(value) ? "[]" : "{}"]);
					else
						entries.forEach(([key, child]) =>
							add(child, path ? path + " · " + key.replace(/_/g, " ") : key.replace(/_/g, " ")),
						);
				} else fields.push([path || "Detail", value == null ? "—" : String(value)]);
			};
			add(values, "");
			const query = input("Find a detail");
			const content = tx("display:flex;flex-direction:column;gap:8px;min-height:0");
			const render = () => {
				const term = query.value.trim().toLocaleLowerCase();
				const found = fields.filter(([key, value]) => (key + " " + value).toLocaleLowerCase().includes(term));
				if (!found.length) return content.replaceChildren(small("No matching details."));
				const value = tx("display:flex;flex-direction:column;gap:8px");
				const show = i => value.replaceChildren(...reader(found[Number(i)][1]));
				const picker = select(
					"Detail",
					found.map(([key], i) => [String(i), key]),
					show,
				);
				show(0);
				content.replaceChildren(picker, value);
			};
			query.oninput = render;
			render();
			mount([heading(title.length > 52 ? "Details" : title), query, content, k.btn(T("c.back"), back)]);
			query.focus();
		};
		const show = (title, nodes, options = {}) => {
			const buttons = [],
				inputs = [],
				text = [];
			let back = options.back;
			const add = node => {
				if (!node) return;
				if (node.nodeType === 3) {
					if (node.textContent.trim()) text.push(node.textContent);
					return;
				}
				if (node.matches("[data-fy-esc]")) {
					buttons.push(node);
					return;
				}
				if (node.matches("[role='button'],[role='switch']")) {
					buttons.push(node);
					return;
				}
				if (node.matches("input,textarea,select")) {
					inputs.push(control(node, node.getAttribute("aria-label") || node.placeholder || "Value"));
					return;
				}
				if (node.hasAttribute("data-fy-live-work")) {
					inputs.push(node);
					return;
				}
				if (node.childNodes.length) [...node.childNodes].forEach(add);
			};
			nodes.filter(Boolean).forEach(add);
			const nav = buttons.filter(node => node.hasAttribute("data-fy-esc"));
			const acts = buttons.filter(node => !node.hasAttribute("data-fy-esc"));
			if (back && !nav.length) {
				const button = options.preserveBack
					? ui().button({
							label: T("c.back"),
							nav: true,
							onclick: () => {
								if (k.alive()) back();
							},
						})
					: k.btn(T("c.back"), back);
				button.dataset.fyEsc = "1";
				nav.push(button);
			}
			const full = text.join("\n");
			const content = [];
			if (title) content.push(heading(title.length > 52 ? "Details" : title));
			if (full || title.length > 52) content.push(...reader((title.length > 52 ? title + "\n" : "") + full));
			content.push(...inputs);
			if (acts.length <= 4 && acts.every(node => (node.getAttribute("aria-label") || "").length < 34))
				content.push(grid(acts));
			else if (acts.length) content.push(...actions(acts));
			if (nav.length) content.push(grid(nav));
			mount(content, options.preserve);
		};
		k.pages = show;
		k.show = (...nodes) => {
			const n = nodes.filter(Boolean);
			const title = n[0] && n[0].style.fontSize === "28px" ? n.shift().textContent : "";
			show(title, n);
		};
		return { control, input, select, small, heading, grid, mount, reader, actions, inspect };
	};

	const modules = {
		render(box) {
			const k = make(box);
			k.bounded = !!box.closest(".fy-panel");
			const windowUI =
				k.bounded && privateApi.singleWindow && privateApi.singleWindow() ? managementWindow(k, box) : null;
			const { btn, sw } = k;
			let data = { list: [], store: [], st: {}, mp: {}, maint: {} };
			let selection = "";
			let windowQuery = "";
			let lastAction = null;
			let res = {},
				more = "",
				line = null;
			k.names = id => {
				const m = Array.isArray(data.list) && data.list.find(x => x.id === id);
				return m ? m.name : "";
			};
			let onHome = false,
				onResult = false;
			let bad = 0;
			k.windowContext = () => {
				onHome = false;
				onResult = false;
			};
			const fall = e => {
				const error = (windowUI ? String((e && e.message) || "") : clip(e && e.message, 200)) || T("c.noanswer");
				k.show(
					tx(TITLE, T("m.title")),
					col(BAD, T("c.failed", { msg: error })),
					row(
						btn(T("c.retry"), () => draw(), { primary: true }),
						btn(T("c.close"), () => framey.toggle()),
					),
					row(k.reportButton(reportContext(null, error, "management_view_failed"))),
				);
			};
			k.bad = fall;
			if (windowUI) {
				const pages0 = k.pages;
				k.pages = (...args) => {
					onHome = false;
					onResult = false;
					return pages0(...args);
				};
			}
			const show0 = k.show;
			k.show = (...n) => {
				onHome = false;
				onResult = false;
				return show0(...n);
			};
			const mrec = id => obj(obj(data.maint)[id]);
			const pull = ids => {
				if (!windowUI) ids = ids.slice(0, 64);
				const groups = [];
				for (let at = 0; at < ids.length; at += 64) groups.push(ids.slice(at, at + 64));
				return Promise.all(
					groups.map(group =>
						call("maint_get", { ids: group }).then(
							k.guard(r => {
								if (r.records) Object.assign(data.maint, r.records);
							}),
						),
					),
				);
			};
			const draw = () =>
				Promise.all([call("list"), call("store"), call("state_get"), call("maint_prefs"), call("status")])
					.then(
						k.guard(([list, store, st, mp, sts]) => {
							const kept = a =>
								Array.isArray(a) ? a.filter(x => x && typeof x === "object" && !Array.isArray(x)) : [];
							const lst = Array.isArray(list) ? list.filter(m => m && typeof m.id === "string") : list;
							const sto = kept(store).filter(s => typeof s.id === "string");
							const arr = a => (Array.isArray(a) ? a.filter(x => typeof x === "string") : []);
							const s = Object.assign({}, obj(st), {
								hidden: arr(obj(st).hidden),
								order: arr(obj(st).order),
							});
							bad =
								(Array.isArray(list) ? list.length - lst.length : 0) +
								(Array.isArray(store) ? store.length - sto.length : 0);
							data = {
								list: lst,
								store: sto,
								st: s,
								mp: obj(mp),
								maint: {},
								ss: obj(sts).settings_state,
							};
							return pull(Array.isArray(lst) ? lst.map(m => m.id) : []);
						}),
					)
					.then(k.guard(() => home()))
					.catch(k.guard(fall));
			const back = () => draw();
			k.back = back;
			const quick = (method, arg, okText) => {
				k.hold(true);
				return call(method, arg)
					.then(
						k.guard(r => {
							k.hold(false);
							lastAction = { id: typeof arg === "string" ? arg : obj(arg).id, result: r };
							if (r.error) warn(r.error);
							else if (Array.isArray(r.purge_failed) && r.purge_failed.length)
								warn(T("m.purge_failed", { count: r.purge_failed.length }));
							else if (okText) say(okText);
							if (!r.error && Array.isArray(r.order)) {
								data.st = r;
								return home();
							}
							return draw();
						}),
					)
					.catch(k.guard(fall));
			};
			const thaw = (m, cont) => {
				const f = obj(data.st.frozen)[m.id];
				if (!f) return cont();
				k.ask(
					T("m.thaw_title"),
					T("m.thaw_text", { name: m.name, v: vv(f.version) }),
					[
						[
							T("m.thaw_ok"),
							() =>
								call("mod_freeze", { id: m.id, frozen: false }).then(
									k.guard(r => {
										if (r.error) {
											warn(r.error);
											draw();
										} else cont();
									}),
								),
						],
					],
					back,
				);
			};
			const update = m =>
				k.work(
					T("m.updating", { name: m.name }),
					() => call("update", m.id),
					(r, out) => {
						if (!r.error && r.current === true) {
							res[m.id] = { state: "current" };
							return k.result(out, {}, T("m.already"), back);
						}
						const valid = r.ok === true || r.ok === m.id;
						const result = r.error || valid ? r : { error: T("c.noanswer") };
						if (!result.error) delete res[m.id];
						k.result(
							out,
							result,
							T(r.version ? "m.updated_v" : "m.updated", { name: m.name, v: r.version }),
							back,
							reportContext(m, "", "plugin_update_failed"),
						);
					},
				);
			const updateAsk = m => {
				const s = obj(res[m.id]);
				const n =
					typeof s.notes === "string"
						? windowUI
							? s.notes
							: s.notes.length > 600
								? s.notes.slice(0, 600) + "..."
								: s.notes
						: "";
				let used = false;
				k.show(
					tx(TITLE, T("m.update")),
					col("#fff", T("m.plan_row", { name: m.name, from: vv(m.version), to: vv(s.latest) }), 20),
					n && col(GREY, T("s.notes_head") + "\n" + n, 18),
					row(
						btn(
							T("m.install"),
							() => {
								if (!used) {
									used = true;
									update(m);
								}
							},
							{ primary: true },
						),
						btn(T("c.cancel"), back),
					),
				);
			};
			const fin = (method, m, title, okKey) =>
				k.work(
					title,
					() => call(method, m.id),
					(r, out) =>
						k.result(
							out,
							r.pending ? { error: T("m.still") } : r,
							T(okKey, { name: m.name }),
							back,
							reportContext(m, "", "plugin_recovery_failed"),
						),
				);
			const repair = (m, reset, confirm) =>
				k.work(
					T("m.repairing", { name: m.name }),
					() => call("repair", { id: m.id, reset, confirm }),
					(r, out) => {
						if (r.confirm)
							return k.ask(
								T("m.repair_reset"),
								r.confirm,
								[[T("m.repair_reset_ok"), () => repair(m, true, true), DANGER]],
								back,
							);
						k.result(out, r, T("m.repaired", { name: m.name }), back, reportContext(m, "", "plugin_repair_failed"));
					},
				);
			const repairAsk = m =>
				k.ask(
					T("m.repair_title", { name: m.name }),
					T("m.repair_text"),
					[
						[T("m.repair_ok"), () => repair(m, false, false), { primary: true }],
						[T("m.repair_reset"), () => repair(m, true, false), DANGER],
					],
					back,
				);
			const removeAsk = (m, purge) =>
				k.ask(
					T(purge ? "m.rmd_title" : "m.rm_title", { name: m.name }),
					[
						T(purge ? "m.rmd_text" : "m.rm_text"),
						m.linked && T("m.linked_note"),
						m.privileged && T("m.privileged_remove"),
					]
						.filter(Boolean)
						.join("\n"),
					[
						[
							T(purge ? "m.rmd_ok" : "m.rm_ok"),
							() => {
								more = "";
								quick("uninstall", { id: m.id, purge }, T("m.removed", { name: m.name }));
							},
							DANGER,
						],
					],
					back,
				);
			const comp = (m, c) =>
				k.work(
					T("m.comp_dl", { name: c.name }),
					() => call("companion_fetch", { id: m.id, name: c.name }),
					(r, out) =>
						k.result(
							out,
							r,
							[T("m.comp_ok", { name: c.name }), r.digest, r.note].filter(Boolean).join("\n"),
							back,
							reportContext(m, "", "plugin_companion_failed"),
						),
				);
			const check = el => {
				k.hold(true);
				el.textContent = T("c.checking");
				el.style.color = GREY;
				call("check")
					.then(
						k.guard(r => {
							k.hold(false);
							if (!r.checked) line = [T("m.check_failed", { msg: r.error || T("c.noanswer") }), BAD];
							else {
								res = obj(r.checked);
								const all = Object.values(obj(res)).filter(x => x && typeof x === "object");
								const n = all.filter(x => x.state === "update").length;
								const f = all.filter(x => x.state === "error").length;
								const parts = [f && T("m.n_failed", { count: f }), n && T("m.n_updates", { count: n })].filter(Boolean);
								line = [parts.length ? parts.join(", ") : T("m.all_current"), f ? BAD : n ? UP : GOOD];
							}
							home();
						}),
					)
					.catch(k.guard(fall));
			};
			const results = (r, out) => {
				if (!Array.isArray(r.results)) return k.result(out, { error: r.error || T("c.noanswer") }, "", back);
				r.results.forEach(x => {
					if (x && (x.state === "ok" || x.state === "current")) delete res[x.id];
				});
				const v = batchPack(r);
				keep(v);
				paintRes(k, out, v, () => {
					keep(null);
					back();
				});
			};
			const plan = () => {
				k.show(tx(TITLE, T("m.updall")), row(btn(T("c.back"), back)), col(GREY, T("c.checking")));
				k.hold(true);
				return call("batch_plan", {}).then(
					k.guard(r => {
						k.hold(false);
						const items = (Array.isArray(r.items) ? r.items : [])
							.filter(x => x && typeof x === "object")
							.slice(0, windowUI ? undefined : 32);
						if (r.error || !items.length)
							return k.show(
								tx(TITLE, T("m.updall")),
								col(r.error ? BAD : GREY, r.error || T("m.none")),
								row(btn(T("c.back"), back)),
							);
						const ok = items.filter(x => x.eligible === true);
						const rest = items.filter(x => x.eligible !== true);
						k.show(
							tx(TITLE, T("m.updall")),
							col(ok.length ? UP : GREY, T("m.plan_count", { count: ok.length }), 20, true),
							row(
								btn(
									T("m.apply", { count: ok.length }),
									() => k.work(T("m.updating_all"), () => call("batch_apply", { ids: ok.map(x => x.id) }), results),
									{ disabled: !ok.length, primary: true },
								),
								btn(T("c.cancel"), back),
							),
							...ok.map(x =>
								col("#fff", T("m.plan_row", { name: x.name || x.id, from: vv(x.from), to: vv(x.to) }), 19),
							),
							...rest.map(x => col(GREY, T("m.plan_skip", { name: x.name || x.id, reason: x.reason || "" }), 18)),
						);
					}),
				);
			};
			const approval = m => {
				const b = tx("border:3px solid " + WARN + ";border-radius:14px;padding:12px;margin-top:12px");
				return put(
					b,
					col(WARN, T("m.partial"), 22, true),
					col("#fff", T("m.partial_text"), 18),
					col(GREY, T("m.partial_hint"), 17),
					row(
						btn(T("m.retry"), () => fin("retry", m, T("m.retrying"), "m.complete"), {
							primary: true,
						}),
					),
					row(
						btn(T("m.again"), () => fin("finish", m, T("c.checking"), "m.complete")),
						btn(T("m.restore"), () => fin("rollback", m, T("m.restoring"), "m.restored"), DANGER),
					),
				);
			};
			const restoreAsk = m =>
				k.ask(
					T("m.restore_title", { name: m.name }),
					T("m.restore_text", { name: m.name }),
					[[T("m.restore_ok"), () => fin("rollback", m, T("m.restoring"), "m.restored"), DANGER]],
					back,
				);
			const heldbox = m =>
				put(
					tx("border:3px solid " + UP + ";border-radius:14px;padding:12px;margin-top:12px"),
					col(UP, T("m.held"), 22, true),
					col("#fff", T("m.held_text", { name: m.name, v: vv(m.version) }), 18),
					row(
						btn(T("m.restore"), () => restoreAsk(m), DANGER),
						btn(T("m.keep"), () => fin("finish", m, T("c.checking"), "m.kept"), { primary: true }),
					),
				);
			const failbox = m => {
				const f = m.fail || { what: "backend", msg: T("m.noresp") };
				return put(
					tx("border:3px solid " + BAD + ";border-radius:14px;padding:12px;margin-top:12px"),
					col(BAD, T(f.stage ? "m.failed_stage" : "m.failed", { what: f.what, stage: f.stage }), 20, true),
					col("#fff", f.msg, 18),
					f.at && col(GREY, new Date(f.at * 1000).toLocaleTimeString(), 16),
					row(
						btn(T("m.disable"), () => quick("disable", m.id, T("m.disabled_ok", { name: m.name }))),
						btn(T("m.reload"), () => quick("reload_one", m.id, T("m.reloaded", { name: m.name })), {
							primary: true,
						}),
					),
					row(
						btn(T("m.recover"), () => repairAsk(m)),
						btn(T("m.remove"), () => removeAsk(m, false)),
					),
					row(
						k.reportButton(
							reportContext(
								m,
								f.msg,
								["load", "render", "backend"].includes(f.what) ? "plugin_" + f.what + "_failed" : "plugin_failed",
							),
						),
					),
				);
			};
			const morePanel = (m, i, n) => {
				const st = data.st,
					fz = !!obj(st.frozen)[m.id],
					hid = (st.hidden || []).includes(m.id);
				const plat = (window.FY_RUNTIME || {}).platform;
				const cs = (Array.isArray(m.companions) ? m.companions : [])
					.filter(c => c && typeof c.name === "string" && (!c.platform || !plat || c.platform === plat))
					.slice(0, windowUI ? undefined : 4);
				return put(
					tx("margin-top:6px"),
					row(
						btn(T("m.reload"), () => quick("reload_one", m.id, T("m.reloaded", { name: m.name }))),
						btn(T(hid ? "m.show" : "m.hide"), () => quick("mod_hide", { id: m.id, hidden: !hid })),
					),
					row(
						btn(T(fz ? "m.unfreeze" : "m.freeze"), () => quick("mod_freeze", { id: m.id, frozen: !fz })),
						btn(T("m.repair"), () => repairAsk(m)),
					),
					st.sort !== "name" &&
						row(
							btn(T("m.up"), () => quick("mod_order", { id: m.id, delta: -1 }), {
								disabled: i === 0,
							}),
							btn(T("m.down"), () => quick("mod_order", { id: m.id, delta: 1 }), {
								disabled: i === n - 1,
							}),
						),
					row(
						btn(T("m.versions"), () =>
							k.versions(m.id, T("m.versions_title", { name: m.name }), back, c => thaw(m, c), m),
						),
						btn(T("m.remove"), () => removeAsk(m, false)),
					),
					row(btn(T("m.remove_data"), () => removeAsk(m, true), DANGER)),
					row(k.reportButton(reportContext(m, "", "plugin_details"))),
					...cs.map(c => row(btn(T("m.comp_btn", { name: c.name }), () => comp(m, c)))),
				);
			};
			const chip = (m, r) => {
				const [key, glyph, color] = mt(r);
				const b = btn(T(key), () => details(m), {
					css:
						"flex:none;justify-content:flex-start;gap:8px;font-size:18px;overflow-wrap:anywhere;background:#101018;border:2px solid " +
						color +
						";color:" +
						color,
				});
				b.setAttribute(
					"aria-label",
					T("mt.aria", {
						name: m.name,
						label: T(key),
						stale: r.stale ? " (" + T("mt.stale") + ")" : "",
					}),
				);
				const g = tx("", glyph),
					l = tx("", T(key));
				g.setAttribute("aria-hidden", "true");
				l.dataset.mt = "label";
				b.replaceChildren(g, l);
				return b;
			};
			const maintRow = m => {
				const r = mrec(m.id);
				const s = tx("font-size:16px;color:" + GREY, T("mt.stale"));
				s.dataset.mt = "stale";
				return (
					r.label &&
					put(
						tx("display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px"),
						tx("font-size:16px;color:" + GREY, T("mt.caption")),
						chip(m, r),
						r.stale && s,
					)
				);
			};
			const details = m => {
				if (windowUI) return windowUI.inspect(T("mt.title", { name: m.name }), mrec(m.id), home);
				const r = mrec(m.id),
					[key, glyph, color] = mt(r),
					d = obj(r.declaration),
					f = obj(r.failure);
				const ev = (Array.isArray(r.evidence) ? r.evidence : []).slice(0, 12);
				const reason = clip(r.reason, 400);
				const days = T("mt.days", { count: r.window_days | 0 });
				const path = r.scope === "plugin path";
				const ln = (text, c) => text && col(c || "#fff", text, 18);
				k.show(
					tx(TITLE, T("mt.title", { name: m.name })),
					col(color, glyph + " " + T(key) + (r.stale ? "  (" + T("mt.stale") + ")" : ""), 22, true),
					ln(reason, "#fff"),
					r.label === "none" && !/does not establish abandonment/.test(reason) && ln(T("mt.none_note"), GREY),
					ln(
						r.repo &&
							T("mt.repo", {
								repo: clip(r.repo, 200),
								branch: clip(r.branch, 100) || T("c.unknown"),
							}),
					),
					ln(r.source && T("mt.source", { source: clip(r.source, 200) })),
					ln(r.scope && (path ? T("mt.scope_path", { path: clip(r.path, 200) }) : T("mt.scope_repo"))),
					ln(r.repo && (r.commit_at ? T("mt.commit", { d: day(r.commit_at) }) : T("mt.no_commit"))),
					ln(
						r.repo &&
							(r.release_at
								? T("mt.release", {
										tag: clip(r.release_tag, 64) || T("c.unknown"),
										d: day(r.release_at),
									}) + (path ? " " + T("mt.release_repo") : "")
								: T("mt.no_release")),
					),
					ln(r.repo && T("mt.archived_flag", { v: yes(r.archived) })),
					ln(r.fork_of && T("mt.fork", { repo: clip(r.fork_of, 200) })),
					d.by &&
						ln(
							T("mt.decl", {
								by: clip(d.by, 80),
								date: d.date ? " (" + clip(d.date, 10) + ")" : "",
							}),
							WARN,
						),
					d.note && ln(T("mt.decl_note", { note: clip(d.note, 300) })),
					d.by &&
						ln(
							T("mt.decl_src", {
								file: clip(d.file, 220),
								commit: clip(d.commit, 40).slice(0, 7) || T("c.unknown"),
							}),
							GREY,
						),
					ln(r.declaration_note && T("mt.decl_ignored", { why: clip(r.declaration_note, 300) }), GREY),
					ln(r.checked_at ? T("mt.checked", { t: when(r.checked_at) }) : T("mt.never"), GREY),
					ln(T("mt.window", { days }), GREY),
					r.stale && ln(T("mt.stale_note"), GREY),
					f.reason &&
						ln(T("mt.failed", { reason: clip(f.reason, 300) }) + (when(f.at) ? " (" + when(f.at) + ")" : ""), BAD),
					r.retry_after * 1000 > Date.now() && ln(T("mt.retry", { t: when(r.retry_after) }), GREY),
					ev.length > 0 && col(GREY, T("mt.why"), 18, true),
					...ev.map(x => ln("- " + clip(x, 300), "#fff")),
					row(
						btn(T("mt.refresh"), () => refresh(m), { primary: true }),
						btn(T("c.back"), () => home()),
					),
					row(
						k.reportButton(
							reportContext(
								m,
								typeof f.reason === "string" ? f.reason : "",
								f.reason ? "plugin_maintenance_failed" : "plugin_details",
							),
						),
					),
					ln(T("mt.info"), GREY),
				);
			};
			const refresh = m => {
				k.hold(true);
				k.show(
					tx(TITLE, T("mt.title", { name: m.name })),
					row(btn(T("c.back"), () => home())),
					col(GREY, T("c.checking")),
				);
				return call("maint_refresh", { id: m.id })
					.then(
						k.guard(r => {
							k.hold(false);
							if (r.error) warn(r.error);
							else data.maint[m.id] = r;
							details(m);
						}),
					)
					.catch(k.guard(fall));
			};
			const refreshAll = el => {
				k.hold(true);
				el.textContent = T("c.checking");
				el.style.color = GREY;
				call("maint_refresh", { id: "all" })
					.then(
						k.guard(r => {
							k.hold(false);
							const n = Object.keys(obj(r.records)).length;
							if (r.error) line = [T("c.failed", { msg: r.error }), BAD];
							else {
								Object.assign(data.maint, r.records);
								line = [T("mt.refreshed", { count: n }), GOOD];
							}
							home();
						}),
					)
					.catch(k.guard(fall));
			};
			const card = (m, i, n) => {
				const fz = obj(data.st.frozen)[m.id];
				const v = verdict(res[m.id], m.nosource);
				const c = tx("background:#181826;border-radius:14px;padding:12px;margin-bottom:12px");
				return put(
					c,
					tx(
						"font-size:22px;font-weight:700;overflow-wrap:anywhere",
						m.name + (m.version ? "  " + T("c.ver", { v: m.version }) : ""),
					),
					tx(
						"font-size:17px;overflow-wrap:anywhere;color:" + GREY,
						m.commit ? T("m.commit", { c: m.commit }) : T("m.commit_unknown"),
					),
					(data.st.hidden || []).includes(m.id) && col(GREY, T("m.hidden"), 18),
					fz && col(UP, T("m.frozen_at", { v: vv(fz.version) }), 18),
					v[0] && col(v[1], v[0]),
					compat(m.compat),
					m.decky_runtime_available === false &&
						col(WARN, String(m.decky_runtime_reason || T("m.decky_off")).slice(0, 300)),
					maintRow(m),
					(m.fail || m.stuck) && failbox(m),
					put(
						tx("display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;align-items:stretch"),
						!m.nosource &&
							!m.pending &&
							btn(T("m.update"), () => thaw(m, () => updateAsk(m)), {
								primary: !!(res[m.id] && res[m.id].state === "update"),
							}),
						btn(T(more === m.id ? "m.less" : "m.more"), () => {
							more = more === m.id ? "" : m.id;
							home();
						}),
						sw(T("m.enabled"), m.enabled, on =>
							quick(on ? "enable" : "disable", m.id, T(on ? "m.enabled_ok" : "m.disabled_ok", { name: m.name })),
						),
					),
					m.pending && approval(m),
					m.held && !m.pending && heldbox(m),
					more === m.id && morePanel(m, i, n),
				);
			};
			const tail = store => {
				const out = [];
				if (Array.isArray(store) && store.length) {
					out.push(tx("font-size:22px;font-weight:700;margin:16px 0 8px", T("m.store_title")));
					store.forEach(s =>
						out.push(
							row(
								tx(
									"flex:1;min-width:0;overflow-wrap:anywhere;font-size:21px;align-self:center",
									s.name + (s.version ? "  " + T("c.ver", { v: s.version }) : ""),
								),
								btn(
									T(s.installed ? "m.remove" : "m.install"),
									() => (s.installed ? removeAsk(s, false) : quick("add", s.id, T("m.installed_ok", { name: s.name }))),
									{ css: "width:130px;flex:none" },
								),
							),
						),
					);
				}
				const url = document.createElement("input");
				url.dataset.fy = "core";
				url.placeholder = T("m.link_hint");
				url.style.cssText =
					"width:100%;box-sizing:border-box;height:56px;font-size:20px;border-radius:12px;border:0;padding:0 14px;margin-top:16px";
				const install = () => {
					const link = url.value;
					let host = link;
					try {
						host = new URL(link).host;
					} catch (e) {}
					k.ask(
						"Install from link?",
						"Install from " + host + "? The plugin and its backend run code with your user permissions.",
						[
							[
								T("m.install"),
								() =>
									k.work(
										T("m.installing"),
										() => call("install", link),
										(r, o) => {
											if (r.error) return k.result(o, r, "", draw);
											say(T("m.installed_ok", { name: typeof r.ok === "string" ? r.ok : "" }));
											draw();
										},
									),
								{ primary: true },
							],
						],
						draw,
					);
				};
				out.push(url, row(btn(T("m.install"), install, { primary: true })));
				return out;
			};
			const windowCollection = (title, entries, queryLabel, backTo, choose, initial = "", remember) => {
				const w = windowUI;
				const query = w.input(queryLabel, initial);
				const found = tx("display:flex;flex-direction:column;gap:8px");
				const filter = () => {
					if (remember) remember(query.value);
					const term = query.value.trim().toLocaleLowerCase();
					const matches = entries.filter(m => [m.name, m.id, m.version].join(" ").toLocaleLowerCase().includes(term));
					if (term)
						matches.sort(
							(a, b) =>
								Number(String(b.id).toLocaleLowerCase() === term) - Number(String(a.id).toLocaleLowerCase() === term),
						);
					const capacity = box.clientWidth >= 640 && box.clientHeight >= 560 ? 6 : 4;
					const status = w.small(
						matches.length +
							" / " +
							entries.length +
							(matches.length > capacity ? " · Refine search to find a plugin" : ""),
					);
					const buttons = matches.slice(0, capacity).map(m => {
						const b = btn(m.name || m.id, () => choose(m));
						b.style.cssText +=
							";height:44px;min-height:44px;margin:0;padding:7px 10px;font-size:18px;line-height:1.2;border-radius:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block";
						b.title = (m.name || m.id) + " · " + m.id;
						return b;
					});
					found.replaceChildren(
						status,
						...buttons,
						!matches.length ? w.small(T("m.none")) : document.createTextNode(""),
					);
				};
				query.oninput = filter;
				filter();
				const nodes = [title && w.heading(title), query, found];
				if (backTo) nodes.push(btn(T("c.back"), backTo));
				return { nodes, query };
			};
			const windowPlugin = (m, mods) => {
				const w = windowUI;
				const latestAction = lastAction && lastAction.id === m.id ? lastAction.result : {};
				const inspect = () => {
					onHome = false;
					return w.inspect(
						"Details",
						{
							plugin: m,
							"last action result": latestAction,
							"update status": obj(res[m.id]),
							"upstream project": mrec(m.id),
							"hidden in launcher": data.st.hidden.includes(m.id),
							"frozen version": obj(data.st.frozen)[m.id] || false,
						},
						() => windowPlugin(m, mods),
					);
				};
				const choices = [];
				const collect = node => {
					if (!node) return;
					if (node.matches("[role='button']")) choices.push(node);
					else [...node.children].forEach(collect);
				};
				collect(morePanel(m, mods.indexOf(m), mods.length));
				if (typeof latestAction.error === "string") {
					const reportAt = choices.findIndex(node => node.getAttribute("aria-label") === T("c.report"));
					if (reportAt >= 0)
						choices[reportAt] = k.reportButton(reportContext(m, latestAction.error, "plugin_management_failed"));
				}
				if (m.pending) collect(approval(m));
				else if (m.held) collect(heldbox(m));
				if (m.fail || m.stuck) collect(failbox(m));
				choices.push(btn(T("mt.refresh"), () => refresh(m)));
				const main = [
					sw(T("m.enabled"), m.enabled, on =>
						quick(on ? "enable" : "disable", m.id, T(on ? "m.enabled_ok" : "m.disabled_ok", { name: m.name })),
					),
					!m.nosource && !m.pending
						? btn(T("m.update"), () => thaw(m, () => updateAsk(m)), { primary: true })
						: btn(T("m.recover"), () => repairAsk(m)),
				];
				const name = m.name || m.id;
				const state =
					m.decky_runtime_available === false
						? String(m.decky_runtime_reason || T("m.decky_off")).slice(0, 300)
						: m.pending
							? T("m.partial")
							: m.held
								? T("m.held")
								: m.fail || m.stuck
									? T("m.failed", { what: obj(m.fail).what || "backend" })
									: verdict(res[m.id], m.nosource)[0] || compat(m.compat).textContent;
				w.mount([
					w.heading(name.length > 52 ? name.slice(0, 49) + "…" : name),
					...w.reader(
						[
							m.version && T("c.ver", { v: m.version }),
							latestAction.error || state,
							box.clientWidth >= 640 && m.commit ? T("m.commit", { c: m.commit }) : "",
						]
							.filter(Boolean)
							.join(" · "),
					),
					w.grid(main),
					w.grid([
						btn("Details", inspect),
						btn(T("c.back"), () => {
							selection = "";
							home();
						}),
					]),
					...w.actions(choices),
				]);
				onHome = true;
			};
			const windowStore = () => {
				const w = windowUI;
				const collection = windowCollection(T("m.store_title"), data.store, "Search available plugins", home, m => {
					k.show(
						tx(TITLE, m.name || m.id),
						col(GREY, m.version || ""),
						btn("Details", () => w.inspect("Details", m, windowStore)),
						btn(T(m.installed ? "m.remove" : "m.install"), () =>
							m.installed ? removeAsk(m, false) : quick("add", m.id, T("m.installed_ok", { name: m.name })),
						),
						btn(T("c.back"), windowStore),
					);
				});
				w.mount(collection.nodes);
			};
			const windowTools = () => {
				const w = windowUI;
				const controls = [
					btn(T(data.st.sort !== "name" ? "m.sort_custom" : "m.sort_name"), () =>
						quick("mod_sort", data.st.sort !== "name" ? "name" : "custom"),
					),
					btn(T("mt.refresh_all"), () => refreshAll(tx(""))),
					btn(T("m.store_title"), windowStore),
					btn("Install from link", () =>
						k.show(tx(TITLE, "Install from link"), ...tail([]), btn(T("c.back"), windowTools)),
					),
					btn("Details", () =>
						w.inspect(
							"Details",
							{
								settings: data.st,
								status: data.ss,
								maintenance: data.mp,
								notice: line && line[0],
								"invalid records": bad,
							},
							windowTools,
						),
					),
				];
				if (line && line[1] === BAD)
					controls.push(k.reportButton(reportContext(null, line[0], "management_check_failed")));
				const damage = dmg(k, data.ss, back);
				if (damage) [...damage.querySelectorAll("[role='button']")].forEach(node => controls.push(node));
				k.show(
					tx(TITLE, "Manage plugins"),
					data.st.safe && col(WARN, T("m.safe_text")),
					(data.ss === "corrupt" || data.ss === "unreadable") &&
						col(BAD, T(data.ss === "corrupt" ? "sd.corrupt" : "sd.unreadable")),
					...controls,
					btn(T("c.back"), home),
				);
			};
			const windowHome = mods => {
				const selected = mods.find(m => m.id === selection);
				if (selected) return windowPlugin(selected, mods);
				selection = "";
				const w = windowUI;
				const collection = windowCollection(
					"",
					mods,
					"Search installed plugins",
					null,
					m => {
						selection = m.id;
						home();
					},
					windowQuery,
					value => {
						windowQuery = value;
					},
				);
				w.mount([
					...collection.nodes,
					w.grid([btn(T("m.check"), () => check(tx("")), { primary: true }), btn(T("m.updall"), plan)]),
					w.grid([
						btn("More tools", windowTools),
						btn("Details", () =>
							w.inspect(
								"Details",
								{
									notice: line && line[0],
									"safe mode": !!data.st.safe,
									"settings status": data.ss,
									"maintenance window": data.mp,
									"invalid records": bad,
								},
								home,
							),
						),
					]),
					(line || data.st.safe || data.ss === "corrupt" || data.ss === "unreadable" || bad) &&
						w.small(
							line
								? line[0].length > 96
									? line[0].slice(0, 93) + "…"
									: line[0]
								: data.st.safe
									? T("m.safe_title")
									: bad
										? T("m.dropped", { count: bad })
										: T(data.ss === "corrupt" ? "sd.corrupt" : "sd.unreadable"),
						),
				]);
				onHome = true;
			};
			const paint = () => {
				const { list, store, st } = data;
				if (!Array.isArray(list))
					return k.show(
						tx(TITLE, T("m.title")),
						col(BAD, obj(list).error || T("c.unavailable")),
						row(
							btn(T("c.retry"), () => draw()),
							k.reportButton(reportContext(null, obj(list).error || T("c.unavailable"), "management_list_failed")),
						),
					);
				const custom = st.sort !== "name";
				const rank = new Map((st.order || []).map((id, i) => [id, i]));
				const mods = list
					.slice()
					.sort((a, b) => (rank.has(a.id) ? rank.get(a.id) : 1e9) - (rank.has(b.id) ? rank.get(b.id) : 1e9));
				const sum = col(line ? line[1] : GREY, line ? line[0] : "", 19);
				const banner = () =>
					put(
						tx("border:3px solid " + WARN + ";border-radius:14px;padding:12px;margin-bottom:12px"),
						col(WARN, T("m.safe_title"), 22, true),
						col("#fff", T("m.safe_text"), 18),
					);
				if (windowUI) return windowHome(mods);
				if (k.bounded) {
					const selected = mods.find(m => m.id === selection);
					if (selected) {
						k.show(
							tx(TITLE, selected.name),
							row(
								btn(T("c.back"), () => {
									selection = "";
									home();
								}),
							),
							card(selected, mods.indexOf(selected), mods.length),
						);
						onHome = true;
						return;
					}
					const tools = () =>
						k.show(
							tx(TITLE, "Manage plugins"),
							row(btn(T("c.back"), home)),
							dmg(k, data.ss, back),
							st.safe && banner(),
							btn(T(custom ? "m.sort_custom" : "m.sort_name"), () => quick("mod_sort", custom ? "name" : "custom")),
							btn(T("m.check"), () => check(sum), { primary: true }),
							btn(T("m.updall"), plan),
							btn(T("mt.refresh_all"), () => refreshAll(sum)),
							sum,
							line && line[1] === BAD && k.reportButton(reportContext(null, line[0], "management_check_failed")),
							...tail(store),
						);
					k.pages(T("m.title"), [
						...mods.map(m =>
							btn(m.name, () => {
								selection = m.id;
								home();
							}),
						),
						btn("Manage plugins", tools),
						!mods.length && col(GREY, T("m.none")),
					]);
					onHome = true;
					return;
				}
				k.show(
					tx(TITLE, T("m.title")),
					dmg(k, data.ss, back),
					st.safe && banner(),
					row(
						btn(T(custom ? "m.sort_custom" : "m.sort_name"), () => quick("mod_sort", custom ? "name" : "custom")),
						btn(T("m.check"), () => check(sum), { primary: true }),
					),
					row(
						btn(T("m.updall"), plan),
						btn(T("mt.refresh_all"), () => refreshAll(sum)),
					),
					sum,
					line && line[1] === BAD && row(k.reportButton(reportContext(null, line[0], "management_check_failed"))),
					data.mp &&
						data.mp["maint.window_days"] > 0 &&
						col(GREY, T("mt.window_line", { days: T("mt.days", { count: data.mp["maint.window_days"] }) }), 16),
					bad > 0 && col(WARN, T("m.dropped", { count: bad }), 18),
					!mods.length && col(GREY, T("m.none"), 20),
					...mods.map((m, i) => card(m, i, mods.length)),
					...tail(store),
				);
				onHome = true;
			};
			const home = () => {
				try {
					paint();
				} catch (e) {
					fall(e);
				}
			};
			const showRes = v => {
				k.hold(false);
				const out = tx("");
				k.show(tx(TITLE, T("m.res_title")), out);
				paintRes(k, out, v, () => {
					keep(null);
					draw();
				});
				onResult = true;
			};
			live = {
				box,
				idle: () => onHome || onResult,
				show: showRes,
				maint: ids => {
					const have =
						Array.isArray(ids) && Array.isArray(data.list)
							? data.list.map(m => m.id).filter(id => ids.includes(id))
							: [];
					if (k.alive() && onHome && have.length)
						pull(have).then(
							k.guard(() => {
								if (onHome) home();
							}),
						);
				},
			};
			const pend = rd();
			if (pend) showRes(pend);
			else draw();
		},
	};
	privateApi.kit = Object.freeze({
		T,
		call,
		tx,
		put,
		col,
		row,
		ui,
		say,
		warn,
		obj,
		day,
		make,
		dmg,
		GREY,
		GOOD,
		BAD,
		UP,
		WARN,
		TITLE,
		DANGER,
	});
	Object.defineProperty(framey.pages, "modules", {
		value: Object.freeze({ render: modules.render }),
		enumerable: true,
	});
})();
