const __trust = {
	mode: ["off", "require"].includes((window.FY_RUNTIME || {}).trusted_input) ? window.FY_RUNTIME.trusted_input : "auto",
	seen: false,
	n: 0,
};
const __trusted = ev => {
	if (__trust.mode === "off" || (__trust.mode === "auto" && !__trust.seen) || (ev && ev.isTrusted === true))
		return true;
	if (__trust.n < 100000) __trust.n++;
	return false;
};
const __FY = (() => {
	if (window.Framey) {
		console.warn("Framey: window.Framey already defined; not injecting");
		return null;
	}
	const fyCall = window.fyCall;

	const serializeJSON = JSON.stringify;
	const defineProperty = Object.defineProperty;
	const freezeObject = Object.freeze;
	const PromiseConstructor = Promise;
	const scheduleTimeout = setTimeout;
	const cancelTimeout = clearTimeout;
	const scheduleInterval = setInterval;
	const cancelInterval = clearInterval;
	const functionToString = Function.prototype.toString;
	const DIAGNOSTIC_CODES = [
		"core.callback_failed",
		"core.cleanup_failed",
		"core.extension_failed",
		"core.storage_failed",
		"core.binding_failed",
		"core.transport_failed",
		"core.timer_failed",
		"core.event_failed",
		"core.visibility_failed",
		"core.panel_failed",
		"api.resource_failed",
		"api.locale_failed",
		"api.notification_failed",
		"page.callback_failed",
		"page.render_failed",
		"page.cleanup_failed",
		"page.navigation_failed",
		"page.focus_failed",
		"ui.callback_failed",
		"ui.pointer_capture_failed",
		"ui.pointer_release_failed",
		"picker.cleanup_failed",
		"picker.request_failed",
		"picker.focus_failed",
	];
	const diagnosticCounts = [];
	const diagnostic = code => {
		try {
			if (typeof code !== "string") return;
			for (let i = 0; i < DIAGNOSTIC_CODES.length && i < 32; i++) {
				if (code !== DIAGNOSTIC_CODES[i]) continue;
				const count = diagnosticCounts[i] || 0;
				if (count < 9999) diagnosticCounts[i] = count + 1;
				return;
			}
		} catch (error) {}
	};
	const diagnostics = () => {
		try {
			const rows = [];
			for (let i = 0; i < DIAGNOSTIC_CODES.length && i < 32; i++) {
				if (diagnosticCounts[i]) rows.push({ code: DIAGNOSTIC_CODES[i], count: diagnosticCounts[i] });
			}
			return rows;
		} catch (error) {
			return [];
		}
	};
	if (typeof fyCall !== "function" || !/\[native code\]/.test(functionToString.call(fyCall))) {
		const m = document.createElement("div");
		m.dataset.fy = "";
		m.setAttribute("role", "alert");
		m.style.cssText =
			"position:fixed;top:11px;right:11px;z-index:99999;background:#101018;color:#fff;border-radius:14px;padding:14px 24px;font:20px sans-serif";
		m.textContent = "Framey could not start safely";
		document.body.append(m);
		return null;
	}
	try {
		defineProperty(window, "fyCall", { value: fyCall, writable: false, configurable: true });
	} catch (error) {
		diagnostic("core.binding_failed");
	}
	const mark = ev => {
		if (ev && ev.isTrusted === true) __trust.seen = true;
	};
	["click", "keydown", "pointerdown"].forEach(t => window.addEventListener(t, mark, true));
	const TOK = typeof __fyt === "string" ? __fyt : "";
	const ID = /^[a-z0-9_-]{1,32}$/;
	const RESERVED = ["core", "modules", "settings", "store", "discover", "decky-store", "developer", "dev"];
	const codeIssues = new Map();
	const calls = {},
		LONG = new Set([
			"check",
			"update",
			"finish",
			"retry",
			"rollback",
			"loader_update",
			"loader_done",
			"loader_roll",
			"install",
			"add",
			"enable",
			"disable",
			"uninstall",
			"reload",
			"reload_one",
			"ver_list",
			"ver_install",
			"repair",
			"notes",
			"batch_plan",
			"batch_apply",
			"loader_check",
			"companion_fetch",
			"maint_refresh",
			"settings_repair",
			"fetch",
			"catalog_install",
		]);
	const READ = new Set(["resource", "catalog_sources", "catalog_list", "catalog_details", "catalog_assess"]);
	const limit = (plugin, method, to) =>
		plugin !== "_core" ? (to || 50000) + 10000 : LONG.has(method) ? 600000 : READ.has(method) ? 55000 : 15000;
	const gen = Math.random().toString(36).slice(2);
	const bound = new Map();
	const mods = [],
		gone = new WeakSet(),
		gonePages = new WeakSet();
	const APIS = new WeakMap();
	const inst = {};
	let requestId = 0;
	let toastBox;
	let owner = "";
	let currentTab = "";
	let pageState = {};
	let returnTo = null;
	let extLocked = false,
		closing = false,
		closed = false;
	const safe = (fn, code = "core.callback_failed") => {
		try {
			return fn();
		} catch (error) {
			diagnostic(code);
		}
	};
	const cleanup = fn => safe(fn, "core.cleanup_failed");
	const sessionValue = (k, v) =>
		safe(() => (v === undefined ? sessionStorage[k] : (sessionStorage[k] = v)), "core.storage_failed");
	const stamp = pl => gen + ":" + pl + "." + (inst[pl] | 0);
	const el = (css, text) => {
		const e = document.createElement("div");
		e.dataset.fy = owner;
		e.style.cssText = css;
		if (text !== undefined) e.textContent = text;
		return e;
	};
	const owned = id => [...document.querySelectorAll("[data-fy]")].filter(e => e.dataset.fy === id);
	const lock = (o, ...ks) =>
		ks.forEach(k => defineProperty(o, k, { value: o[k], writable: false, configurable: false }));
	const wire = (o, t) => (t === undefined ? serializeJSON(o) : '{"t":"' + t + '",' + serializeJSON(o).slice(1));
	const disposed = who => ({
		error: who === "_core" ? "Framey panel disposed" : "module disposed",
		code: "disposed",
	});
	const cleanupRevoke = (method, arg, t, who) =>
		arg &&
		typeof arg === "object" &&
		!Array.isArray(arg) &&
		arg.pid === who &&
		ID.test(who) &&
		bound.get(who) === t &&
		Object.keys(arg).length === 2 &&
		((method === "resource" &&
			Object.keys(arg).every(k => k === "pid" || k === "revoke") &&
			typeof arg.revoke === "string" &&
			/^[A-Za-z0-9_-]{1,128}$/.test(arg.revoke)) ||
			(method === "decky_code_revoke" &&
				Object.keys(arg).every(k => k === "pid" || k === "token") &&
				typeof arg.token === "string" &&
				/^[0-9a-f]{64}$/.test(arg.token)));
	const post = (method, arg, t, who) => {
		if ((closing || closed) && !cleanupRevoke(method, arg, t, who)) return;
		safe(() => fyCall(wire({ id: 0, plugin: "_core", method, arg }, t)), "core.transport_failed");
	};
	const retireCodeReceipt = (record, value) => {
		if (
			!value ||
			typeof value !== "object" ||
			Array.isArray(value) ||
			Object.prototype.hasOwnProperty.call(value, "error") ||
			value.ttl !== 600 ||
			typeof value.token !== "string" ||
			!/^[0-9a-f]{64}$/.test(value.token)
		)
			return;
		const pattern = new RegExp("^http://127\\.0\\.0\\.1:([1-9][0-9]{0,4})/g/" + value.token + "/$");
		const match = typeof value.root === "string" && pattern.exec(value.root);
		if (!match || Number(match[1]) > 65535 || value.entry !== value.root + "dist/index.js") return;
		post("decky_code_revoke", { pid: record.p, token: value.token }, record.auth, record.p);
	};
	const send = (plugin, method, arg, t, who, to) =>
		closing || closed
			? PromiseConstructor.resolve(disposed(who))
			: method === "decky_code_issue" && plugin === "_core" && codeIssues.size >= 128
				? PromiseConstructor.resolve({ error: "Module requests are busy", code: "busy" })
				: new PromiseConstructor(r => {
						const id = ++requestId;
						const g = stamp(who);
						const c = { r, g, p: who, t: 0 };
						if (plugin === "_core" && method === "decky_code_issue")
							codeIssues.set(id, { g, p: who, auth: t, abandoned: false });
						calls[id] = c;
						c.t = scheduleTimeout(
							() => {
								if (calls[id] === c) {
									delete calls[id];
									if (codeIssues.has(id)) codeIssues.get(id).abandoned = true;
									r({
										error:
											"Framey " +
											(plugin === "_core" ? "core" : "module") +
											" did not reply in " +
											limit(plugin, method, to) / 1000 +
											" s",
									});
								}
							},
							limit(plugin, method, to),
						);
						try {
							fyCall(wire({ gen: g, id, plugin, method, arg }, t));
						} catch (e) {
							diagnostic("core.transport_failed");
							cancelTimeout(c.t);
							delete calls[id];
							codeIssues.delete(id);
							r({ error: String((e && e.message) || e) });
						}
					});
	const io = (id, tok) => ({
		core: (method, arg) => send("_core", method, arg, tok, id),
		be: (method, arg, to) => send(id, method, arg, tok, id, to),
		post: (method, arg) => post(method, arg, tok, id),
	});
	const adopt = v => {
		if (!v || !Array.isArray(v.order) || !Array.isArray(v.hidden)) return;
		const was = serializeJSON(pageState);
		pageState = { order: v.order, hidden: v.hidden };
		if (isOpen() && serializeJSON(pageState) !== was) {
			const visible = shown();
			if (!visible.some(t => key(t) === currentTab)) show(visible[0]);
			else paint();
		}
	};
	const reg = (i, p) => {
		if (closing || closed) throw new Error("Framey panel disposed");
		if (i.used) throw new Error("this module is already registered");
		if (!p || typeof p !== "object") throw new Error("register needs a module object");
		if (p.id !== undefined && p.id !== i.id) throw new Error("module id does not match " + i.id);
		i.used = true;
		bound.set(i.id, i.tok);
		const id = i.id;
		p.id = id;
		p.meta = i.meta;
		p.name = String(p.name || i.meta.name || id);
		p.short = String(p.short || i.meta.short || p.name.split(" ")[0]);
		const res = [];
		const tm = new Set();
		const iv = new Set();
		const ls = new Set();
		const room = (m, what) => {
			if (m.size >= 256) throw new RangeError("too many " + what);
		};
		const runTimer = fn => {
			try {
				return fn();
			} catch (error) {
				diagnostic("core.timer_failed");
				throw error;
			}
		};
		const api = {
			id,
			timeout(fn, ms) {
				if (p.dead) return 0;
				room(tm, "timers");
				const h = scheduleTimeout(() => {
					tm.delete(h);
					if (!p.dead) runTimer(fn);
				}, ms);
				tm.add(h);
				return h;
			},
			interval(fn, ms) {
				if (p.dead) return 0;
				room(iv, "intervals");
				const h = scheduleInterval(() => {
					if (p.dead) cancelInterval(h);
					else runTimer(fn);
				}, ms);
				iv.add(h);
				return h;
			},
			clear(h) {
				const t = tm.delete(h);
				const i = iv.delete(h);
				if (t || !i) cancelTimeout(h);
				if (i || !t) cancelInterval(h);
			},
			on(t, type, fn, o) {
				if (p.dead) return () => {};
				if (ls.size >= 256) throw new RangeError("too many listeners");
				t.addEventListener(type, fn, o);
				const off = () => {
					if (ls.delete(off)) t.removeEventListener(type, fn, o);
				};
				ls.add(off);
				return off;
			},
			_clear() {
				const a = res.splice(0);
				while (a.length) cleanup(a.pop());
			},
		};
		APIS.set(p, api);
		defineProperty(api, "_n", {
			value: () => ({ timers: tm.size, intervals: iv.size, listeners: ls.size }),
		});
		res.push(() => {
			tm.forEach(cancelTimeout);
			iv.forEach(cancelInterval);
			tm.clear();
			iv.clear();
			[...ls].forEach(f => f());
		});
		const x = io(id, i.tok);
		defineProperty(api, "_rpc", {
			value: (method, arg) =>
				(p.dead
					? PromiseConstructor.reject(Object.assign(new Error("module disposed"), { code: "disposed" }))
					: x.core(method, arg)
				).then(r => {
					if (r && r.error) {
						const e = new Error(String(r.error));
						e.code = r.code;
						throw e;
					}
					return r;
				}),
		});
		FY.ext.forEach(extend => {
			try {
				extend(api, p, res, x);
			} catch (error) {
				diagnostic("core.extension_failed");
			}
		});
		i.events = api.events;
		defineProperty(p, "dead", { get: () => gone.has(p) || undefined, enumerable: true });
		freezeObject(p);
		const k = mods.findIndex(q => q.id === id);
		if (k < 0) mods.push(p);
		else {
			disposeMods(id);
			mods[k] = p;
		}
		return api;
	};
	const proxy = i => {
		const o = Object.create(null);
		const put = (k, v) => defineProperty(o, k, { value: v, enumerable: true });
		put("register", p => reg(i, p));
		put("call", (pid, method, arg, to) => send(i.id, method, arg, i.tok, i.id, to));
		put("dispose", () => disposeMods(i.id));
		put("listen", (name, fn) => (i.events ? i.events.on(name, fn) : () => {}));
		put("_meta", i.meta);
		["el", "button", "toast", "fail", "notify", "onPanel", "lazy", "open", "toggle", "restore", "redraw"].forEach(k =>
			put(k, FY[k]),
		);
		defineProperty(o, "panelOpen", { get: () => FY.panelOpen, enumerable: true });
		return freezeObject(o);
	};
	const bindPlugin = (id, tok, meta) => {
		if (closing || closed) throw new Error("Framey panel disposed");
		if (typeof id !== "string" || !ID.test(id)) throw new Error("invalid module id");
		if (RESERVED.includes(id)) throw new Error("module id " + id + " is reserved");
		if (typeof tok !== "string" || tok.length < 8 || tok.length > 256) throw new Error("invalid module token");
		if (bound.has(id) ? bound.get(id) !== tok : bound.size >= 64)
			throw new Error("module id " + id + " is already in use");
		if (!extLocked) {
			freezeObject(FY.ext);
			extLocked = true;
		}
		const given = meta && typeof meta === "object" ? meta : {};
		return proxy({ id, tok, meta: freezeObject(Object.assign({}, given, { id })), used: false });
	};
	const apiOf = t => APIS.get(t);
	const disposeMods = only => {
		if (closing || closed) return;
		if (!only) closing = true;
		try {
			if (!only) {
				if (K.cancelLauncher) K.cancelLauncher();
				["click", "keydown", "pointerdown"].forEach(t => window.removeEventListener(t, mark, true));
				closeReport();
				cleanup(() =>
					all()
						.filter(t => !t.plugin && !apiOf(t))
						.forEach(t => {
							if (t.dispose) cleanup(t.dispose);
						}),
				);
			}
			mods.forEach(p => {
				if (gone.has(p) || (only && p.id !== only)) return;
				gone.add(p);
				if (p.dispose) cleanup(() => p.dispose());
				cleanup(() => APIS.get(p)._clear());
				cleanup(() => owned(p.id).forEach(e => e.remove()));
				inst[p.id] = (inst[p.id] | 0) + 1;
			});
			Object.keys(calls).forEach(k => {
				const c = calls[k];
				if (only && c.p !== only) return;
				cancelTimeout(c.t);
				delete calls[k];
				if (codeIssues.has(Number(k))) codeIssues.get(Number(k)).abandoned = true;
				c.r(disposed(c.p));
			});
		} finally {
			if (!only) {
				inst._core = (inst._core | 0) + 1;
				closed = true;
				closing = false;
			}
		}
	};
	const FY = {
		ext: [],
		dispose(only, tok) {
			if (TOK && tok === TOK) disposeMods(only);
		},
		redraw(only) {
			if (closing || closed || !isOpen()) return;
			const t = find(currentTab);
			if (!t) show(shown()[0]);
			else if (only ? only === currentTab : apiOf(t)) show(t);
			else paint();
		},
		call: (plugin, method, arg) =>
			plugin === "_core"
				? PromiseConstructor.resolve({ error: "not authorized" })
				: send(plugin, method, arg, undefined, plugin),
		_reply(g, id, v) {
			if (!Number.isSafeInteger(id) || id <= 0) return;
			const c = calls[id];
			if (!c) {
				const retired = codeIssues.get(id);
				if (retired && retired.abandoned && g === retired.g) {
					codeIssues.delete(id);
					retireCodeReceipt(retired, v);
				}
				return;
			}
			if (g !== c.g || g !== stamp(c.p)) return;
			cancelTimeout(c.t);
			delete calls[id];
			codeIssues.delete(id);
			if (c.p === "_core") adopt(v);
			c.r(v);
		},
		toast(text, ms) {
			if (closing || closed) return;
			if (!toastBox || !toastBox.isConnected) {
				toastBox = el(
					"position:fixed;left:50%;bottom:40px;transform:translateX(-50%);z-index:99999;display:flex;flex-direction:column;align-items:center;gap:8px;pointer-events:none",
				);
				toastBox.setAttribute("role", "status");
				document.body.append(toastBox);
			}
			while (toastBox.childElementCount >= 4) toastBox.firstElementChild.remove();
			const t = el(
				"background:#101018;color:#fff;border-radius:14px;padding:14px 24px;font:22px sans-serif",
				String(text).slice(0, 200),
			);
			toastBox.append(t);
			scheduleTimeout(() => t.remove(), Math.min(ms || 2500, 60000));
		},
		fail(id, e) {
			FY.toast("Module " + id + " failed: " + (e && e.message), 4000);
		},
	};
	const K = {
		call: (method, arg) => send("_core", method, arg, TOK, "_core"),
		post: (method, arg) => post(method, arg, TOK, "_core"),
		pending: () => Object.keys(calls).length,
		diagnostic,
		diagnostics,
		lock,
		tab: () => currentTab,
		untrusted: () => __trust.n,
		apiOf: p => APIS.get(p),
		dispose: only => disposeMods(only),
		bind: bindPlugin,
		reply: FY._reply,
	};
	const closeReport = () => K.report && cleanup(() => K.report.close());
	FY._k = t => (TOK && t === TOK ? K : null);
	let pageNavigation = null,
		refreshLanding = null,
		launcherTimer = 0,
		launcherSequence = 0,
		launcherBusy = false,
		launcherIndex = 0;
	const navigationCallback = fn =>
		safe(() => {
			const result = fn();
			if (result && typeof result.then === "function")
				PromiseConstructor.resolve(result).catch(() => diagnostic("core.panel_failed"));
		}, "core.panel_failed");
	const landingIndex = { "#plugins": 0, "#browse": 0 };
	const landingFocus = { "#plugins": "", "#browse": "" };
	const panel = el(
		"position:fixed;top:11px;right:11px;z-index:99999;box-sizing:border-box;max-width:calc(100vw - 22px);overflow:hidden;background:#101018;color:#fff;border-radius:16px;padding:12px;display:none;flex-direction:column;gap:10px;font:20px sans-serif;box-shadow:0 12px 36px #0006",
	);
	panel.setAttribute("role", "region");
	panel.setAttribute("aria-label", "Framey");
	panel.className = "fy-panel";
	K.singleWindow = () => panel.dataset.fyNavigation === "single";
	const content = el("flex:1;min-width:0;min-height:0;overflow:auto;overscroll-behavior:contain;padding:4px;order:2");
	const rail = el("display:flex;flex:none;gap:6px;min-width:0;order:1");
	const header = el("display:flex;align-items:center;gap:8px;min-width:0;flex:none;order:0");
	header.className = "fy-panel-header";
	const title = el(
		"font-size:20px;font-weight:700;line-height:1.25;flex:1;min-width:0;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden",
		"Framey",
	);
	content.id = "fy-content-" + gen;
	content.setAttribute("role", "tabpanel");
	rail.setAttribute("role", "tablist");
	rail.setAttribute("aria-label", "Framey destinations");
	rail.setAttribute("aria-orientation", "horizontal");
	panel.append(header, rail, content);
	K.setPanelLayout = layout => {
		const value = layout === "compact" ? "compact" : "window";
		const changed = panel.dataset.fyLayout !== value;
		panel.dataset.fyLayout = value;
		panel.dataset.fyNavigation =
			value === "window" && (window.FY_RUNTIME || {}).panel_navigation === "launcher" ? "single" : "legacy";
		panel.style.top = value === "compact" ? "auto" : "11px";
		panel.style.bottom = value === "compact" ? "11px" : "auto";
		if (currentTab !== "#launcher")
			panel.style.width = value === "compact" ? "360px" : K.singleWindow() ? "778px" : "520px";
		else renderLauncher();
		panel.style.height =
			currentTab === "#launcher" ? "72px" : value === "compact" ? "298px" : K.singleWindow() ? "698px" : "585px";
		panel.style.maxHeight =
			"min(" + (value === "compact" ? "298px" : K.singleWindow() ? "698px" : "585px") + ",calc(100vh - 22px))";
		if (changed && pageNavigation && typeof pageNavigation.onLayout === "function")
			navigationCallback(() => pageNavigation.onLayout(value));
		else if (changed && refreshLanding && landingIndex[currentTab] !== undefined && content.firstChild)
			refreshLanding(content.firstChild);
		return value;
	};
	K.panelLayout = () => panel.dataset.fyLayout;
	K.setPanelLayout((window.FY_RUNTIME || {}).panel_layout);
	const press = (t, fn) => {
		t.addEventListener("click", ev => fn(ev));
		t.addEventListener("keydown", ev => {
			if (ev.target !== t || (ev.key !== "Enter" && ev.key !== " ")) return;
			ev.preventDefault();
			if (!ev.repeat) fn(ev);
		});
	};
	const tile = (css, text, fn, role) => {
		const t = el("display:flex;align-items:center;border-radius:14px;background:#2a2a3a;" + css, text);
		t.tabIndex = 0;
		t.setAttribute("role", role);
		press(t, fn);
		return t;
	};
	FY.el = el;
	FY.button = (text, css, fn) =>
		tile("justify-content:center;font-weight:700;" + css, text, ev => __trusted(ev) && fn(ev), "button");
	const style = document.createElement("style");
	style.dataset.fy = "core";
	style.textContent =
		".fy-r{-webkit-appearance:none;height:14px;border-radius:7px;background:#2a2a3a;flex:1;margin:0 10px}" +
		".fy-r::-webkit-slider-thumb{-webkit-appearance:none;width:40px;height:40px;border-radius:20px;background:#5585ff}";
	style.textContent +=
		".fy-panel [role=button],.fy-panel [role=tab]{min-height:48px;min-width:48px;cursor:pointer;box-sizing:border-box}" +
		".fy-panel [role=button]:hover,.fy-panel [role=tab]:hover{filter:brightness(1.15)}" +
		".fy-panel :focus-visible{outline:3px solid #9bbcff;outline-offset:-3px}" +
		'.fy-panel [aria-label="Page navigation"] [aria-disabled="true"]{opacity:.45;cursor:default}' +
		".fy-panel ::selection{background:#3658a8;color:#fff}.fy-panel{scrollbar-color:#64729a #181824;caret-color:#9bbcff}" +
		".fy-panel [data-fy-route]{white-space:normal;overflow-wrap:anywhere;text-align:left;justify-content:flex-start}" +
		'.fy-panel[data-fy-layout="compact"] .fy-page-heading{display:none}';
	style.textContent += `
.fy-panel[data-fy-launcher="true"]{height:72px!important;border-radius:6px!important;padding:8px!important;background:#0e141b!important;box-shadow:inset 0 1px 0 #ffffff24,inset 0 -1px 2px #0008,0 5px 14px #0006!important;overflow:visible!important}
.fy-panel[data-fy-launcher="true"]>[role="tabpanel"],.fy-panel[data-fy-launcher="true"]>[role="tablist"],.fy-panel[data-fy-launcher="true"]>[role="navigation"],.fy-panel[data-fy-launcher="true"]>div:first-child{display:none!important}
.fy-launcher{display:none;align-items:stretch;gap:6px;width:100%;height:56px;transform-origin:50% 100%;isolation:isolate}
.fy-panel[data-fy-launcher="true"] .fy-launcher{display:flex}
.fy-launcher[data-phase="opening"]{animation:fy-fan-open 300ms cubic-bezier(.16,1,.3,1) both}
.fy-launcher[data-phase="closing"]{animation:fy-fan-close 220ms cubic-bezier(.7,0,.84,0) forwards;pointer-events:none}
.fy-launcher [role="button"]{border-radius:5px!important;border:1px solid #707d8b;background:linear-gradient(170deg,#303b47 0%,#242f3b 45%,#1d2732 100%);box-shadow:inset 0 1px 0 #ffffff33,0 2px 2px #202126,0 4px 5px #0005;color:#fff;transition:transform 90ms,box-shadow 90ms;user-select:none}
.fy-launcher [data-fy-launch]{position:relative;flex:1;min-width:0;justify-content:center;padding:8px;border-radius:5px}
.fy-launcher [role="button"]:hover{border-color:#5585ff}
.fy-launcher [role="button"]:active{transform:translateY(2px);filter:none;border-color:#536170;background:#0e141b;box-shadow:inset 0 2px 4px #0007,inset 0 -1px 0 #ffffff12}
.fy-launcher [role="button"][aria-disabled="true"]{opacity:.4;cursor:default}
.fy-launcher .fy-launcher-tooltip{position:absolute;bottom:calc(100% + 12px);left:50%;transform:translateX(-50%);width:max-content;max-width:180px;padding:6px 9px;background:#19191f;color:#fff;border-radius:6px;box-shadow:0 3px 8px #0006;font-size:12px;font-weight:400;line-height:1.3;text-align:center;overflow-wrap:anywhere;pointer-events:none;visibility:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.fy-launcher [data-fy-launch]:first-child .fy-launcher-tooltip{left:0;transform:none}
.fy-launcher [data-fy-launch]:last-child .fy-launcher-tooltip{left:auto;right:0;transform:none}
.fy-launcher [data-fy-launch]:hover .fy-launcher-tooltip,.fy-launcher [data-fy-launch]:focus-visible .fy-launcher-tooltip{visibility:visible}
.fy-launcher .fy-launcher-arrow{flex:none;width:48px;border-radius:5px}
.fy-launcher .fy-launcher-icon{width:26px;height:26px;flex:none;object-fit:contain}
.fy-launcher .fy-launcher-error{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:8px;padding:8px;color:#fff;background:#101018;border-radius:12px;font-size:15px}
@keyframes fy-fan-open{0%{opacity:0;transform:translateY(26px) scale(.14,.25)}35%{opacity:1;transform:translateY(0) scale(.32,1)}100%{opacity:1;transform:translateY(0) scale(1)}}
@keyframes fy-fan-close{0%{opacity:1;transform:translateY(0) scale(1)}65%{opacity:1;transform:translateY(0) scale(.25,1)}100%{opacity:0;transform:translateY(26px) scale(.12,.2)}}
@media(prefers-reduced-motion:reduce){.fy-launcher[data-phase]{animation:none}.fy-launcher[data-phase="closing"]{opacity:0}.fy-launcher [role="button"]{transition:none}}
`;
	document.head.append(style);
	const back = FY.button("Back", "padding:8px 12px;flex:none", () => {
		if (pageNavigation && typeof pageNavigation.onBack === "function") {
			navigationCallback(() => pageNavigation.onBack());
			return;
		}
		const from = currentTab;
		landingFocus[section(from)] = from;
		show(find(section(from)));
		const target = [...content.querySelectorAll("[data-fy-route]")].find(node => node.dataset.fyRoute === from);
		if (target) target.focus();
		else focusTab();
	});
	const close = FY.button("Close", "padding:8px 12px;flex:none", () => go(false));
	back.setAttribute("aria-label", "Back");
	close.setAttribute("aria-label", "Close");
	header.append(back);
	if (typeof window.FY_ICON_URL === "string" && window.FY_ICON_URL) {
		const icon = document.createElement("img");
		icon.dataset.fy = "";
		icon.alt = "";
		icon.setAttribute("aria-hidden", "true");
		icon.src = window.FY_ICON_URL;
		icon.style.cssText = "width:32px;height:32px;flex:none";
		header.append(icon);
	}
	header.append(title, close);
	const footer = el("display:none;flex:none;align-items:center;gap:8px;order:3");
	footer.setAttribute("role", "navigation");
	footer.setAttribute("aria-label", "Page navigation");
	const pageCount = el(
		"flex:1;min-width:0;text-align:center;font-size:16px;color:#ff7fdc;white-space:nowrap;overflow:hidden",
	);
	pageCount.setAttribute("aria-live", "polite");
	const turnPage = delta => {
		const nav = pageNavigation;
		if (!nav || nav.route !== currentTab || typeof nav.onPage !== "function") return;
		const index = nav.index + delta;
		if (index < 0 || index >= nav.count) return;
		navigationCallback(() => nav.onPage(index));
	};
	const previous = FY.button("Previous", "padding:8px 12px;font-size:17px;flex:none", () => turnPage(-1));
	const next = FY.button("Next", "padding:8px 12px;font-size:17px;flex:none", () => turnPage(1));
	previous.setAttribute("aria-label", "Previous");
	next.setAttribute("aria-label", "Next");
	footer.append(previous, pageCount, next);
	panel.append(footer);
	const routeHeader = () => {
		const current = find(currentTab);
		title.textContent =
			K.singleWindow() && current
				? label(current)
				: currentTab === section(currentTab)
					? "Framey"
					: current
						? label(current)
						: "Framey";
		back.style.display = currentTab === section(currentTab) ? "none" : "flex";
		if (pageNavigation && pageNavigation.title) title.textContent = String(pageNavigation.title);
		if (pageNavigation && typeof pageNavigation.onBack === "function") back.style.display = "flex";
	};
	K.clearPageNavigation = () => {
		pageNavigation = null;
		footer.style.display = "none";
		content.style.overflow = "auto";
		routeHeader();
	};
	K.setPageNavigation = options => {
		if (
			!options ||
			!Number.isInteger(options.count) ||
			options.count < 1 ||
			!Number.isInteger(options.index) ||
			options.index < 0 ||
			options.index >= options.count
		)
			return false;
		pageNavigation = Object.assign({}, options, { route: currentTab });
		footer.style.display = options.count > 1 ? "flex" : "none";
		footer.dataset.fyPage = String(options.index);
		footer.dataset.fyPages = String(options.count);
		content.style.overflow = "hidden";
		previous.setAttribute("aria-disabled", String(options.index === 0));
		next.setAttribute("aria-disabled", String(options.index === options.count - 1));
		pageCount.textContent = options.index + 1 + " of " + options.count;
		pageCount.setAttribute("aria-label", (options.label ? String(options.label) + " " : "") + pageCount.textContent);
		routeHeader();
		if (options.title) title.textContent = String(options.title);
		if (typeof options.onBack === "function") back.style.display = "flex";
		return true;
	};
	const launcher = el("");
	launcher.className = "fy-launcher";
	launcher.setAttribute("role", "toolbar");
	launcher.setAttribute("aria-label", "Framey launcher");
	panel.append(launcher);
	const reducedMotion = () => parseFloat(getComputedStyle(launcher).animationDuration) === 0;
	const cancelLauncher = () => {
		if (launcherTimer) cancelTimeout(launcherTimer);
		launcherTimer = 0;
		launcherSequence++;
		launcherBusy = false;
	};
	K.cancelLauncher = cancelLauncher;
	let launcherIconSequence = 0;
	const launcherIcon = kind => {
		const ns = "http://www.w3.org/2000/svg",
			svg = document.createElementNS(ns, "svg"),
			path = document.createElementNS(ns, "path");
		svg.classList.add("fy-launcher-icon");
		svg.setAttribute("viewBox", "0 0 24 24");
		svg.setAttribute("aria-hidden", "true");
		path.setAttribute(
			"d",
			{
				updates: "M20 7a8 8 0 1 0 0 10M20 3v5h-5",
				maintenance: "m14 4 2 2-4 4-2-2-7 7 3 3 7-7-2-2 4-4 2 2 2-2-5-5Z",
				worker: "M7 7h10v10H7zM9 2v5m6-5v5M9 17v5m6-5v5M2 9h5m-5 6h5m10-6h5m-5 6h5",
				support: "M5 4h14v13H9l-4 4V4Zm5 4a2 2 0 0 1 4 0c0 2-2 1-2 4m0 2v1",
			}[kind] ||
				(kind === "previous"
					? "m15 5-7 7 7 7"
					: kind === "next"
						? "m9 5 7 7-7 7"
						: kind === "fan"
							? "M11 10C6 5 9 1 13 3C17 4 16 8 14 10M14 11C20 9 23 14 19 17C16 20 13 17 12.5 14M10 13C8 19 2 19 2 14C2 10 6 9 10 11"
							: kind === "settings"
								? "M5 4v16M12 4v16M19 4v16M2 8h6m1 8h6m1-8h6"
								: kind === "store"
									? "M5 8h14l1 13H4L5 8Zm3 0V6a4 4 0 0 1 8 0v2"
									: kind === "decky"
										? "m3 7 9-4 9 4-9 4-9-4Zm0 5 9 4 9-4M3 17l9 4 9-4"
										: "M8 3h8v5h5v8h-5v5H8v-5H3V8h5V3Z"),
		);
		let paint = "currentColor";
		if (kind !== "previous" && kind !== "next") {
			const defs = document.createElementNS(ns, "defs"),
				gradient = document.createElementNS(ns, "linearGradient"),
				id = "fy-launcher-mark-" + gen + "-" + ++launcherIconSequence;
			gradient.id = id;
			gradient.setAttribute("x2", "100%");
			gradient.setAttribute("y2", "100%");
			["#5585ff", "#ff37c7"].forEach((color, index) => {
				const stop = document.createElementNS(ns, "stop");
				stop.setAttribute("offset", index ? "100%" : "0%");
				stop.setAttribute("stop-color", color);
				gradient.append(stop);
			});
			defs.append(gradient);
			svg.append(defs);
			paint = "url(#" + id + ")";
		}
		path.setAttribute("fill", "none");
		path.setAttribute("stroke", paint);
		path.setAttribute("stroke-width", "1.8");
		path.setAttribute("stroke-linecap", "round");
		path.setAttribute("stroke-linejoin", "round");
		svg.append(path);
		if (kind === "fan") {
			const center = document.createElementNS(ns, "circle");
			center.setAttribute("cx", "12");
			center.setAttribute("cy", "12");
			center.setAttribute("r", "2");
			center.setAttribute("fill", paint);
			svg.append(center);
		}
		return svg;
	};
	const launcherItems = () => [
		...shown()
			.filter(t => apiOf(t) || t.plugin)
			.map(t => {
				const parent = t.plugin && FY.plugins.find(plugin => plugin.id === t.plugin);
				return {
					route: key(t),
					text: parent ? label(parent) + " / " + label(t) : label(t),
					caption: parent ? String(parent.short || label(parent)) + " / " + label(t) : label(t),
					kind: key(t) === "fan" ? "fan" : "plugin",
				};
			}),
		{ route: "#discover", text: "Framey Store", kind: "store" },
		{ route: "#decky-store", text: "Decky catalog", kind: "decky" },
		{ route: "#settings", text: "Options", kind: "settings" },
		{ route: "#modules", text: "Manage plugins", kind: "plugin" },
		...(K.windowPages || [])
			.filter(t => key(t) !== "#settings:preferences")
			.map(t => ({ route: key(t), text: label(t), kind: key(t).split(":")[1] })),
	];
	const launchRoute = route => {
		if (launcherBusy || currentTab !== "#launcher" || closing || closed || !isOpen()) return;
		launcherBusy = true;
		const sequence = ++launcherSequence;
		launcher.dataset.phase = "closing";
		launcher.setAttribute("aria-busy", "true");
		const dispatch = () => {
			launcherTimer = 0;
			if (sequence !== launcherSequence || currentTab !== "#launcher" || !isOpen() || closing || closed) return;
			K.call("panel_launch", { route }).then(result => {
				if (sequence !== launcherSequence || currentTab !== "#launcher" || !isOpen() || closing || closed) return;
				if (result && !result.error && result.ok === true) return;
				launcherBusy = false;
				launcher.removeAttribute("aria-busy");
				launcher.dataset.phase = "ready";
				launcher.replaceChildren();
				const message = el("", "Could not open this page.");
				message.className = "fy-launcher-error";
				message.setAttribute("role", "status");
				const retry = FY.button("Retry", "padding:8px;font-size:16px;flex:1", () => launchRoute(route));
				retry.setAttribute("aria-label", "Retry opening page");
				retry.style.background = "";
				const menu = FY.button("Back", "padding:8px;font-size:16px;flex:1", () => renderLauncher(false, route));
				menu.setAttribute("aria-label", "Back to shortcuts");
				menu.style.background = "";
				launcher.append(message, retry, menu);
				retry.focus({ preventScroll: true });
			});
		};
		if (reducedMotion()) dispatch();
		else launcherTimer = scheduleTimeout(dispatch, 220);
	};
	K.launcherWidth = () => 462;
	const renderLauncher = (opening = false, restoreRoute = "") => {
		if (launcherBusy) return;
		const focused = restoreRoute || (document.activeElement && document.activeElement.dataset.fyLaunch);
		const items = launcherItems();
		const available = Math.max(118, innerWidth - 22);
		const size = Math.max(1, Math.min(4, Math.floor((available - 118) / 64)));
		const count = Math.max(1, Math.ceil(items.length / size));
		panel.style.width = size * 64 + 118 + "px";
		panel.style.left = "50%";
		panel.style.right = "auto";
		panel.style.transform = "translateX(-50%)";
		if (restoreRoute) {
			const position = items.findIndex(item => item.route === restoreRoute);
			if (position >= 0) launcherIndex = Math.floor(position / size);
		}
		launcherIndex = Math.max(0, Math.min(count - 1, launcherIndex));
		launcher.replaceChildren();
		launcher.dataset.phase = opening || launcher.dataset.phase === "opening" ? "opening" : "ready";
		launcher.dataset.page = String(launcherIndex);
		launcher.dataset.pages = String(count);
		const arrow = (direction, disabled) => {
			const button = FY.button("", "justify-content:center", () => {
				if (disabled || launcherBusy) return;
				launcherIndex += direction;
				renderLauncher();
				const target = launcher.querySelector("[data-fy-launch]");
				if (target) target.focus({ preventScroll: true });
			});
			button.className = "fy-launcher-arrow";
			button.style.background = "";
			button.setAttribute("aria-label", direction < 0 ? "Previous shortcuts" : "Next shortcuts");
			button.setAttribute("aria-disabled", String(disabled));
			button.append(launcherIcon(direction < 0 ? "previous" : "next"));
			return button;
		};
		launcher.append(arrow(-1, launcherIndex === 0));
		items.slice(launcherIndex * size, launcherIndex * size + size).forEach(item => {
			const button = FY.button("", "", () => launchRoute(item.route));
			button.style.background = "";
			button.dataset.fyLaunch = item.route;
			button.setAttribute("aria-label", item.text);
			button.title = item.text;
			const tooltip = el("", item.text);
			tooltip.className = "fy-launcher-tooltip";
			tooltip.setAttribute("role", "tooltip");
			tooltip.setAttribute("aria-hidden", "true");
			const icon = launcherIcon(item.kind);
			button.append(icon, tooltip);
			launcher.append(button);
		});
		launcher.append(arrow(1, launcherIndex === count - 1));
		if (focused) {
			const target =
				[...launcher.querySelectorAll("[data-fy-launch]")].find(button => button.dataset.fyLaunch === focused) ||
				(restoreRoute && launcher.querySelector("[data-fy-launch]"));
			if (target) target.focus({ preventScroll: true });
		}
	};
	launcher.addEventListener("animationend", event => {
		if (event.animationName === "fy-fan-open" && launcher.dataset.phase === "opening") launcher.dataset.phase = "ready";
	});
	launcher.addEventListener("keydown", event => {
		if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
		const buttons = [...launcher.querySelectorAll('[role="button"]')].filter(
			button => button.getAttribute("aria-disabled") !== "true",
		);
		const index = buttons.indexOf(event.target);
		if (index < 0) return;
		event.preventDefault();
		const at =
			event.key === "Home"
				? 0
				: event.key === "End"
					? buttons.length - 1
					: Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowRight" ? 1 : -1)));
		buttons[at].focus({ preventScroll: true });
	});
	const launcherPage = {
		id: "#launcher",
		name: "Launcher",
		render() {
			renderLauncher(true);
		},
	};
	const page = (id, name, k) => ({
		id,
		name,
		short: name,
		render(box) {
			const m = FY.pages && FY.pages[k];
			if (m && typeof m.render === "function") return m.render(box);
			const missing = el("font-size:22px;font-weight:700;line-height:1.25", name + " is not available.");
			const recover = k === "store" || k === "decky_store";
			const description = el(
				"font-size:18px;line-height:1.3;color:#c1c5d7",
				"Reload modules, or use Modules to manage installed plugins.",
			);
			const button = FY.button("Open Modules", "min-height:48px;padding:8px 14px", () => FY.open("#modules"));
			const screens = recover ? [missing, description, button] : [missing];
			const display = index => {
				const count = K.panelLayout() === "compact" ? screens.length : 1;
				const at = Math.min(index, count - 1);
				box.replaceChildren(...(count === 1 ? screens : [screens[at]]));
				K.setPageNavigation({ index: at, count, onPage: display, onLayout: () => display(at) });
			};
			display(0);
		},
		leave() {
			const m = FY.pages && FY.pages[k];
			if (m && typeof m.leave === "function") return m.leave();
		},
		dispose() {
			const m = FY.pages && FY.pages[k];
			if (!m || gonePages.has(m)) return;
			gonePages.add(m);
			if (typeof m.dispose === "function") return m.dispose();
		},
	});
	const store = page("#discover", "Store", "store");
	const deckyStore = page("#decky-store", "Decky Store", "decky_store");
	const dev = {
		id: "#developer",
		name: "Developer",
		short: "Dev",
		render(box) {
			FY.dev.render(box);
		},
	};
	const key = t => t.id || t.short;
	const label = t => String(t.name || t.short || t.id || "").trim() || "?";
	const section = id =>
		id === "#browse" || id === "#discover" || id === "#decky-store"
			? "#browse"
			: id === "#settings" || id === "#developer"
				? "#settings"
				: "#plugins";
	const routeButton = (t, text) => {
		const button = FY.button(
			text || label(t),
			"min-height:52px;padding:12px 14px;font-size:19px;line-height:1.35;background:#222234;justify-content:flex-start;text-align:left",
			() => {
				landingFocus[section(key(t))] = key(t);
				show(t);
			},
		);
		const textLabel = text || label(t);
		button.setAttribute("aria-label", textLabel);
		button.title = textLabel;
		button.dataset.fyRoute = key(t);
		return button;
	};
	const destinations = [
		{
			id: "#plugins",
			name: "Plugins",
			render(box) {
				renderPlugins(box);
			},
		},
		{
			id: "#browse",
			name: "Browse",
			render(box) {
				renderLanding(box, "#browse");
			},
		},
		page("#settings", "Settings", "settings"),
	];
	const landingItems = route =>
		route === "#browse"
			? [
					{ page: store, text: "Framey Store" },
					{ page: deckyStore, text: "Decky catalog" },
				]
			: [
					...shown()
						.filter(t => apiOf(t) || t.plugin)
						.map(page => ({ page })),
					{ page: find("#modules"), text: "Manage plugins" },
				];
	const renderLanding = (box, route = currentTab, target = "") => {
		const hadFocus = box.contains(document.activeElement);
		const focused =
			target === false
				? ""
				: target || (box.contains(document.activeElement) && document.activeElement.dataset.fyRoute);
		const items = landingItems(route);
		const size = K.panelLayout() === "compact" ? 1 : 3;
		const count = Math.max(1, Math.ceil(items.length / size));
		if (focused) {
			const position = items.findIndex(item => key(item.page) === focused);
			if (position >= 0) landingIndex[route] = Math.floor(position / size);
		}
		const index = Math.max(0, Math.min(count - 1, landingIndex[route] || 0));
		landingIndex[route] = index;
		box.replaceChildren();
		const heading = el("font-size:24px;font-weight:700;margin:4px 0 14px", route === "#plugins" ? "Plugins" : "Browse");
		heading.className = "fy-page-heading";
		box.append(heading);
		const list = el("display:flex;flex-direction:column;gap:8px");
		items.slice(index * size, index * size + size).forEach(item => {
			const button = routeButton(item.page, item.text);
			button.style.fontSize = "18px";
			button.style.lineHeight = "1.25";
			button.style.padding = "10px 14px";
			if (item.page.plugin) {
				button.style.marginLeft = "16px";
				button.style.background = "#191925";
			}
			const text = el(
				"display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden",
				button.textContent,
			);
			button.replaceChildren(text);
			list.append(button);
		});
		box.append(list);
		K.setPageNavigation({
			index,
			count,
			onPage(value) {
				landingIndex[route] = value;
				renderLanding(box, route, false);
			},
			onLayout() {
				renderLanding(
					box,
					route,
					(content.contains(document.activeElement) && document.activeElement.dataset.fyRoute) ||
						(box.querySelector("[data-fy-route]") && box.querySelector("[data-fy-route]").dataset.fyRoute) ||
						landingFocus[route],
				);
			},
		});
		if (focused && hadFocus) {
			const node = [...box.querySelectorAll("[data-fy-route]")].find(item => item.dataset.fyRoute === focused);
			if (node) node.focus({ preventScroll: true });
			else focusTab();
		}
	};
	const renderPlugins = box => renderLanding(box, "#plugins", landingFocus["#plugins"]);
	refreshLanding = box => renderLanding(box, currentTab);
	const isOpen = () => panel.style.display !== "none";
	const all = () => {
		const o = pageState.order || [];
		const rank = p => {
			const i = o.indexOf(p.id);
			return i < 0 ? o.length : i;
		};
		const pg = FY.panelPages ? FY.panelPages() : [];
		return [
			...FY.plugins
				.filter(p => !p.dead)
				.sort((a, b) => rank(a) - rank(b))
				.flatMap(p => [p, ...pg.filter(g => g.plugin === p.id)]),
			page("#modules", "Modules", "modules"),
			store,
			deckyStore,
			...destinations,
			...(K.windowPages || []),
			launcherPage,
			...(FY.dev ? [dev] : []),
		];
	};
	const find = id => all().find(t => key(t) === id);
	const shown = () => all().filter(t => !(pageState.hidden || []).includes(t.plugin || t.id));
	const tabs = () => [...rail.children];
	const stop = t => tabs().forEach(x => (x.tabIndex = x === t ? 0 : -1));
	const reveal = t => {
		if (t && typeof t.scrollIntoView === "function")
			safe(() => t.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" }), "core.panel_failed");
	};
	const focusTab = () => {
		if (currentTab === "#launcher") {
			const button = launcher.querySelector("[data-fy-launch]") || launcher.querySelector('[role="button"]');
			if (button) button.focus({ preventScroll: true });
			return;
		}
		if (K.singleWindow()) {
			if (!focusFirst()) close.focus({ preventScroll: true });
			return;
		}
		const t = tabs().find(x => x.getAttribute("aria-selected") === "true") || tabs()[0];
		if (t) {
			stop(t);
			t.focus({ preventScroll: true });
			reveal(t);
		}
	};
	const focusFirst = () => {
		const c = content.querySelector('[tabindex="0"],input,textarea,select,button');
		if (!c) return false;
		c.focus();
		return true;
	};
	const paint = () => {
		if (currentTab === "#launcher") {
			renderLauncher();
			return;
		}
		const had = rail.contains(document.activeElement);
		const o = owner;
		owner = "";
		rail.replaceChildren();
		content.removeAttribute("aria-labelledby");
		destinations.forEach(t => {
			const on = key(t) === section(currentTab);
			const b = tile(
				"height:48px;flex:1;min-width:0;justify-content:center;text-align:center;font-weight:700;font-size:18px;padding:8px 4px;background:" +
					(on ? "#3658a8" : "#222234"),
				label(t),
				ev => __trusted(ev) && show(t),
				"tab",
			);
			b.id = "fy-tab-" + gen + "-" + rail.childElementCount;
			b.setAttribute("aria-controls", content.id);
			b.setAttribute("aria-selected", String(on));
			if (on && !K.singleWindow()) content.setAttribute("aria-labelledby", b.id);
			b.tabIndex = on ? 0 : -1;
			rail.append(b);
		});
		routeHeader();
		if (landingIndex[currentTab] !== undefined && content.firstChild) refreshLanding(content.firstChild);
		owner = o;
		if (had) focusTab();
	};
	rail.addEventListener("keydown", ev => {
		const a = tabs();
		const i = a.indexOf(ev.target);
		if (i < 0) return;
		if (ev.key === "ArrowDown") {
			ev.preventDefault();
			focusFirst();
			return;
		}
		if (!["ArrowRight", "ArrowLeft", "ArrowUp", "Home", "End"].includes(ev.key)) return;
		const j =
			ev.key === "ArrowRight"
				? i + 1
				: ev.key === "ArrowLeft" || ev.key === "ArrowUp"
					? i - 1
					: ev.key === "Home"
						? 0
						: a.length - 1;
		ev.preventDefault();
		const t = a[Math.max(0, Math.min(a.length - 1, j))];
		stop(t);
		t.focus({ preventScroll: true });
		reveal(t);
	});
	panel.addEventListener("keydown", ev => {
		if (ev.defaultPrevented) return;
		if (ev.key === "Escape") {
			ev.preventDefault();
			if (__trusted(ev)) {
				if (!K.singleWindow() && pageNavigation && typeof pageNavigation.onBack === "function")
					navigationCallback(() => pageNavigation.onBack());
				else go(false);
			}
		} else if (
			ev.key === "ArrowRight" &&
			content.contains(ev.target) &&
			!/^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)
		) {
			ev.preventDefault();
			focusTab();
		}
	});
	const show = t => {
		if (currentTab !== key(t)) closeReport();
		const inside = content.contains(document.activeElement);
		const old = currentTab !== key(t) && find(currentTab);
		if (old && old.leave) cleanup(old.leave);
		cancelLauncher();
		currentTab = key(t);
		panel.dataset.fyLauncher = String(currentTab === "#launcher");
		if (currentTab !== "#launcher") {
			panel.style.left = "auto";
			panel.style.right = "11px";
			panel.style.removeProperty("transform");
			panel.style.width = K.panelLayout() === "compact" ? "360px" : K.singleWindow() ? "778px" : "520px";
		}
		panel.style.height =
			currentTab === "#launcher"
				? "72px"
				: K.panelLayout() === "compact"
					? "298px"
					: K.singleWindow()
						? "698px"
						: "585px";
		K.clearPageNavigation();
		content.setAttribute("aria-label", String(t.name || t.short || currentTab));
		sessionValue("fyTab", currentTab);
		content.replaceChildren();
		paint();
		const body = el("");
		content.append(body);
		owner = t.plugin || (apiOf(t) ? t.id : "");
		const source = owner || "_core",
			generation = stamp(source);
		const failed = () => {
			if (!body.isConnected || body.parentNode !== content || currentTab !== key(t) || generation !== stamp(source))
				return;
			diagnostic("core.callback_failed");
			body.textContent = "Module error: render failed.";
			if (t.plugin || apiOf(t)) post("fail", { id: t.plugin || t.id, what: "render", msg: "render failed" }, TOK);
		};
		try {
			const result = t.render(body, apiOf(t));
			if (result && typeof result.then === "function") PromiseConstructor.resolve(result).catch(failed);
		} catch (error) {
			failed();
		}
		owner = "";
		if (inside && !focusFirst()) focusTab();
	};
	const note = () => post("panel", isOpen(), TOK);
	const go = (on, id) => {
		if (closing || closed) return;
		if (!on) {
			closeReport();
			cancelLauncher();
		}
		const was = isOpen();
		if (on && !was) returnTo = document.activeElement;
		panel.style.display = on ? "flex" : "none";
		if (on) {
			const visible = shown();
			const remembered = sessionValue("fyTab");
			const requested = id || (K.panelLayout() === "compact" ? "#launcher" : "");
			show(
				(requested && find(requested)) ||
					visible.find(t => key(t) === remembered) ||
					(remembered ? visible[0] : find("#plugins")),
			);
		}
		if (on === was) return;
		sessionValue("fyOpen", on ? "1" : "");
		if (on) {
			focusTab();
			K.call("state_get");
		} else if (returnTo) {
			const o = returnTo;
			returnTo = null;
			safe(() => o.focus(), "core.panel_failed");
		}
		note();
		if (FY._panel) FY._panel(on);
	};
	K.openLauncher = () => go(true, "#launcher");
	K.closePanel = () => go(false);
	FY.toggle = () => go(!isOpen());
	FY.open = id => go(true, id);
	FY.restore = () => {
		if (closing || closed) return;
		if (sessionValue("fyOpen")) go(true, sessionValue("fyRes") ? "#modules" : undefined);
		else note();
		const m = sessionValue("fyMsg");
		if (m) {
			FY.toast(m);
			sessionValue("fyMsg", "");
		}
	};
	defineProperty(FY, "plugins", { get: () => freezeObject(mods.slice()), enumerable: true });
	lock(FY, "dispose", "call", "_reply", "_k", "toast", "fail", "el", "button", "ext");
	defineProperty(window, "Framey", { value: FY, writable: false, configurable: true });
	document.body.append(panel);
	return FY;
})();
