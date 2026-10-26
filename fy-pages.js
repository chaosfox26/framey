(() => {
	const framey = __FY;
	if (!framey || framey.panelPages || !Array.isArray(framey.ext) || Object.isFrozen(framey.ext)) return;
	const privateApi = typeof framey._k === "function" ? framey._k(typeof __fyt === "string" ? __fyt : "") : null;
	const MAX = 8;
	const RESERVED = ["core", "modules", "settings", "store", "discover", "developer", "dev"];
	const ID = /^[A-Za-z0-9_.-]{1,40}$/;
	const ZONES = new Map([
		[
			"corner",
			"position:fixed;right:16px;bottom:16px;z-index:99991;display:flex;flex-direction:column;align-items:flex-end;gap:8px",
		],
		["overlay", "position:fixed;top:0;left:0;right:0;bottom:0;z-index:99989;pointer-events:none"],
		["bar", "position:fixed;top:0;left:0;right:0;z-index:99990;display:flex;flex-direction:column"],
	]);
	const FULL =
		"position:fixed;top:0;left:0;right:0;bottom:0;z-index:99998;box-sizing:border-box;overflow:auto;padding:24px;background:#101018;color:#fff;font:20px sans-serif";
	const BACK = "height:56px;width:160px;margin-bottom:16px";
	const pages = new Map();
	const zones = {};
	let full = [];
	let redrawing = false;
	const diagnose = code => {
		if (privateApi && typeof privateApi.diagnostic === "function") privateApi.diagnostic(code);
	};
	const isolateCallback = (callback, argument, code = "page.callback_failed") => {
		try {
			const result = callback(argument);
			if (result && typeof result.then === "function") Promise.resolve(result).catch(() => diagnose(code));
			return result;
		} catch {
			diagnose(code);
		}
	};
	const changed = e => {
		if (redrawing || !framey.redraw) return;
		redrawing = true;
		try {
			isolateCallback(framey.redraw, e.key, "page.navigation_failed");
		} finally {
			redrawing = false;
		}
	};
	const zone = name => {
		let z = zones[name];
		if (!z || !z.isConnected) {
			z = zones[name] = framey.el(ZONES.get(name));
			z.dataset.fy = "core";
			z.dataset.fyZone = name;
			document.body.append(z);
		}
		return z;
	};
	const ctx = e => ({
		id: e.id,
		plugin: e.plugin,
		mode: e.mode,
		api: e.api,
		close: () => leave(e),
	});
	const rendered = (el, layer) => {
		for (let node = el; node; node = node.parentElement) {
			const style = getComputedStyle(node);
			if (
				style.display === "none" ||
				style.visibility === "hidden" ||
				style.visibility === "collapse" ||
				style.contentVisibility === "hidden"
			)
				return false;
			if (node === layer) return true;
		}
		return false;
	};
	const focusable = layer =>
		[...layer.querySelectorAll("button,input,select,textarea,a[href],[tabindex],[contenteditable='true']")].filter(
			el =>
				el.tabIndex >= 0 &&
				!el.matches(":disabled") &&
				!el.closest("[hidden],[inert],[aria-hidden='true']") &&
				rendered(el, layer),
		);
	const top = () => full[full.length - 1];
	const syncFull = () =>
		full.forEach(e => {
			const active = e === top();
			e.layer.toggleAttribute("inert", !active);
			e.layer.setAttribute("aria-modal", String(active));
			if (active) e.layer.removeAttribute("aria-hidden");
			else e.layer.setAttribute("aria-hidden", "true");
		});
	const focusFull = (e, preferred) => {
		const items = focusable(e.layer);
		const to = items.includes(preferred) ? preferred : items[0] || e.layer;
		isolateCallback(() => to.focus(), undefined, "page.focus_failed");
	};
	const finish = e => {
		if (!e.shown) return;
		const modal = e.mode === "full",
			wasTop = e === top(),
			reopen = modal && e.panel;
		const from = e.from;
		e.shown = false;
		e.generation++;
		e.panel = false;
		full = full.filter(x => x !== e);
		if (e.layer) {
			e.layer.remove();
			e.layer = null;
		}
		if (reopen && full.length) full[0].panel = true;
		syncFull();
		if (modal && top()) {
			if (wasTop || !top().layer.contains(document.activeElement)) focusFull(top(), from);
		} else {
			if (reopen && framey.open) isolateCallback(framey.open, undefined, "page.navigation_failed");
			if (from && from.isConnected) isolateCallback(() => from.focus(), undefined, "page.focus_failed");
		}
		e.from = null;
		if (e.onclose) isolateCallback(e.onclose, undefined, "page.cleanup_failed");
	};
	const leave = e => {
		const was = e.shown;
		finish(e);
		if (was && e.mode === "panel" && framey.open) isolateCallback(framey.open, e.plugin, "page.navigation_failed");
	};
	const drop = (e, quiet) => {
		finish(e);
		if (pages.get(e.key) === e) pages.delete(e.key);
		if (e.mode === "panel" && !quiet) changed(e);
		return true;
	};
	const back = e => {
		const ui = e.api.ui;
		return ui && ui.button
			? ui.button({ label: "Back", onclick: () => leave(e) })
			: framey.button("Back", BACK, () => leave(e));
	};
	const open = e => {
		if (pages.get(e.key) !== e) return false;
		if (e.mode === "panel") return !!framey.open && (framey.open(e.key), true);
		if (e.shown) return false;
		e.from = document.activeElement;
		e.panel = !!framey.panelOpen;
		if (e.panel) framey.toggle();
		const layer = framey.el(FULL);
		layer.setAttribute("role", "dialog");
		layer.setAttribute("aria-modal", "true");
		layer.setAttribute("aria-label", e.title);
		layer.tabIndex = -1;
		layer.addEventListener("keydown", ev => {
			if (e !== top()) return;
			if (ev.key === "Escape") {
				ev.preventDefault();
				ev.stopPropagation();
				leave(e);
			} else if (ev.key === "Tab") {
				const items = focusable(layer),
					first = items[0] || layer,
					last = items[items.length - 1] || layer;
				if (
					!items.includes(document.activeElement) ||
					(ev.shiftKey && document.activeElement === first) ||
					(!ev.shiftKey && document.activeElement === last)
				) {
					ev.preventDefault();
					isolateCallback(() => (ev.shiftKey ? last : first).focus(), undefined, "page.focus_failed");
				}
			}
		});
		const body = framey.el("");
		const b = back(e);
		layer.dataset.fy = body.dataset.fy = b.dataset.fy = e.plugin;
		layer.append(b, body);
		document.body.append(layer);
		e.layer = layer;
		e.shown = true;
		full.push(e);
		syncFull();
		isolateCallback(() => b.focus(), undefined, "page.focus_failed");
		render(e, body);
		return true;
	};
	const render = (e, box) => {
		const generation = ++e.generation;
		const failed = error => {
			if (pages.get(e.key) !== e || !e.shown || e.generation !== generation) return;
			diagnose("page.render_failed");
			if (e.mode === "full") leave(e);
			else {
				box.textContent = "Module error: " + String((error && error.message) || error);
				finish(e);
			}
			isolateCallback(() => framey.fail(e.plugin, error));
		};
		try {
			const result = e.render(box, ctx(e));
			if (result && typeof result.then === "function") Promise.resolve(result).catch(failed);
		} catch (error) {
			failed(error);
		}
	};
	const tab = e =>
		e.tab ||
		(e.tab = {
			id: e.key,
			plugin: e.plugin,
			name: e.title,
			short: e.title.split(" ")[0],
			leave: () => finish(e),
			render(box) {
				if (pages.get(e.key) !== e || e.p.dead) return;
				e.shown = true;
				render(e, box);
			},
		});
	Object.defineProperty(framey, "pages", { value: framey.pages || {} });
	Object.defineProperty(framey, "panelPages", {
		value: () => framey.plugins.flatMap(p => [...pages.values()].filter(e => e.mode === "panel" && e.p === p).map(tab)),
	});
	framey.ext.push((api, p, res, io) => {
		const pid = p.id;
		const mounts = new Map();
		let used = false;
		const err = error => ({ error });
		const own = () => [...pages.values()].filter(e => e.p === p);
		const unmount = m => {
			if (mounts.get(m.id) !== m) return;
			mounts.delete(m.id);
			m.el.remove();
			const z = zones[m.zone];
			if (z && !z.childElementCount) {
				z.remove();
				delete zones[m.zone];
			}
		};
		const send = (method, arg) => {
			if (p.dead) return Promise.resolve(err("module disposed"));
			used = true;
			return io.core(method, arg);
		};
		api.pages = {
			register(o) {
				o = o || {};
				const mode = o.mode === undefined ? "panel" : o.mode;
				if (p.dead) return err("module disposed");
				if (typeof o.id !== "string" || !ID.test(o.id) || RESERVED.includes(o.id))
					return err("invalid or reserved page id");
				if (typeof o.render !== "function") return err("render must be a function");
				if (mode !== "panel" && mode !== "full") return err("mode must be panel or full");
				const key = pid + ":" + o.id;
				const old = pages.get(key);
				if (!old && own().length >= MAX) return err("page limit reached (" + MAX + ")");
				if (old) drop(old, true);

				if (p.dead) return err("module disposed");
				if (pages.has(key)) return err("page changed during replacement");
				if (own().length >= MAX) return err("page limit reached (" + MAX + ")");
				const e = {
					id: o.id,
					key,
					plugin: pid,
					p,
					api,
					title:
						String(o.title === undefined ? o.id : o.title)
							.slice(0, 80)
							.trim() || o.id,
					mode,
					render: o.render,
					onclose: typeof o.onclose === "function" ? o.onclose : null,
					shown: false,
					generation: 0,
					layer: null,
					tab: null,
				};
				pages.set(key, e);
				if (mode === "panel") changed(e);
				const live = () => pages.get(key) === e;
				return {
					open: () => live() && open(e),
					close: () => live() && (leave(e), true),
					remove: () => live() && drop(e),
				};
			},
			open(id) {
				const e = pages.get(pid + ":" + id);
				return !!e && open(e);
			},
			close() {
				own().forEach(leave);
			},
			list() {
				return own().map(e => ({
					id: e.id,
					key: e.key,
					title: e.title,
					mode: e.mode,
					open: e.shown,
				}));
			},
		};
		api.global = {
			mount(id, o) {
				o = o || {};
				const name = o.zone === undefined ? "corner" : o.zone;
				if (p.dead) return err("module disposed");
				if (typeof id !== "string" || !ID.test(id)) return err("invalid mount id");
				if (typeof o.render !== "function") return err("render must be a function");
				if (!ZONES.has(name)) return err("unknown zone");
				const old = mounts.get(id);
				if (!old && mounts.size >= MAX) return err("mount limit reached (" + MAX + ")");
				if (old) unmount(old);
				const m = {
					id,
					zone: name,
					el: framey.el(name === "overlay" ? "pointer-events:auto" : ""),
				};
				m.el.dataset.fy = pid;
				m.el.dataset.fyMount = id;
				mounts.set(id, m);
				zone(name).append(m.el);
				const failed = error => {
					if (mounts.get(id) !== m || p.dead) return;
					diagnose("page.render_failed");
					unmount(m);
					isolateCallback(() => framey.fail(pid, error));
				};
				try {
					const result = o.render(m.el);
					if (result && typeof result.then === "function") Promise.resolve(result).catch(failed);
				} catch (x) {
					diagnose("page.render_failed");
					unmount(m);
					isolateCallback(() => framey.fail(pid, x));
					return err(String((x && x.message) || x));
				}
				return { el: m.el, unmount: () => unmount(m) };
			},
		};
		api.target = name => ({
			css: (key, text) => send("target_add", { pid, target: name, kind: "css", key, code: text }),
			js: (key, code, teardown) => send("target_add", { pid, target: name, kind: "js", key, code, teardown }),
			remove: key => send("target_remove", { pid, target: name, key }),
		});
		res.push(() => {
			own().forEach(e => drop(e, true));
			[...mounts.values()].forEach(unmount);
			if (used) isolateCallback(() => io.core("target_remove", { pid }), undefined, "page.cleanup_failed");
		});
	});
})();
