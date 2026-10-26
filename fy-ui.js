(() => {
	const framey = __FY;
	const privateApi = framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
	if (framey.uiReady) return;
	const style = document.createElement("style");
	style.id = "fy-ui-style";
	style.dataset.fy = "core";
	style.textContent = [
		".fy-c{box-sizing:border-box;display:flex;align-items:center;gap:12px;min-width:48px;min-height:48px;padding:0 16px;overflow-wrap:anywhere;border-radius:14px;background:#2a2a3a;color:#fff;font:700 20px sans-serif;cursor:pointer;user-select:none;outline:0}",
		".fy-c:focus-visible{outline:4px solid #8fb0ff;outline-offset:2px}",
		".fy-c[aria-disabled=true]{opacity:.4;cursor:default}",
		".fy-b{justify-content:center;text-align:center}",
		".fy-p{background:#5585ff}",
		".fy-l{flex:1;min-width:0;overflow-wrap:anywhere}",
		".fy-tr{display:flex;box-sizing:border-box;width:64px;height:32px;padding:4px;border-radius:16px;background:#101018}",
		".fy-kn{width:24px;height:24px;border-radius:12px;background:#8a8aa0}",
		".fy-sw[aria-checked=true] .fy-tr{justify-content:flex-end;background:#5585ff}",
		".fy-sw[aria-checked=true] .fy-kn{background:#fff}",
		".fy-rw{position:relative;flex:2;height:48px;margin:0 20px;display:flex;align-items:center;touch-action:none}",
		".fy-ra{width:100%;height:14px;border-radius:7px;background:#101018;overflow:hidden}",
		".fy-fi{height:100%;background:#5585ff}",
		".fy-th{position:absolute;top:4px;width:40px;height:40px;margin-left:-20px;border-radius:20px;background:#5585ff}",
		".fy-v{min-width:64px;text-align:right;color:#8fb0ff}",
		".fy-ls{display:flex;flex-direction:column;gap:6px;overflow:auto}",
		".fy-o{flex-direction:column;align-items:flex-start;justify-content:center;gap:2px;padding:8px 16px;font-weight:400}",
		".fy-o[aria-selected=true]{background:#5585ff}",
		".fy-sub{font-size:16px;color:#c8c8d8}",
		".fy-o>div{min-width:0;max-width:100%;overflow-wrap:anywhere}",
		".fy-em{padding:12px;font:18px sans-serif;color:#8a8aa0}",
		".fy-g{display:flex;gap:10px;align-items:stretch}",
	].join("");

	const diagnostic = code => {
		if (privateApi && typeof privateApi.diagnostic === "function") privateApi.diagnostic(code);
	};
	const namedError = (error, name) => {
		try {
			return !!error && error.name === name;
		} catch (ignored) {
			return false;
		}
	};
	const callbackFailed = error => {
		if (namedError(error, "AbortError")) return false;
		diagnostic("ui.callback_failed");
		return true;
	};
	const invoke = (options, name, ...args) => {
		try {
			const callback = options[name];
			if (typeof callback !== "function") return;
			const result = callback.apply(options, args);
			if (result && typeof result.then === "function") Promise.resolve(result).then(undefined, callbackFailed);
		} catch (error) {
			callbackFailed(error);
		}
	};

	const txt = x => (x == null ? "" : String(x).slice(0, 300));
	const num = (x, d) => (x == null || x === "" || !isFinite(x) ? d : +x);
	const dp = n => {
		const parts = String(n).toLowerCase().split("e");
		return Math.max(0, (parts[0].split(".")[1] || "").length - num(parts[1], 0));
	};
	const mk = (id, cls, css, text) => {
		const e = framey.el(css, text);
		e.className = cls;
		if (!e.dataset.fy) e.dataset.fy = id;
		return e;
	};
	const off = e => e.getAttribute("aria-disabled") === "true";
	const active = e => e.isConnected && (!e._fyAlive || e._fyAlive());
	const lock = (e, v) => {
		e.setAttribute("aria-disabled", v ? "true" : "false");
		e.tabIndex = v ? -1 : 0;
		if (e.parentNode && e.parentNode._fySync) e.parentNode._fySync();
	};
	const gate = (e, v, change) => {
		Object.defineProperty(e, "disabled", {
			get: () => off(e),
			set: x => {
				lock(e, !!x);
				if (change) change(!!x);
			},
		});
		lock(e, !!v);
	};
	const real = (nav, ev) => nav || __trusted(ev);
	const press = (e, fn, nav) => {
		e.addEventListener("click", ev => {
			if (active(e) && !off(e) && real(nav, ev)) fn(ev);
		});
		e.addEventListener("keydown", ev => {
			if (ev.target !== e || (ev.key !== "Enter" && ev.key !== " ")) return;
			ev.preventDefault();
			if (!ev.repeat && active(e) && !off(e) && real(nav, ev)) fn(ev);
		});
	};
	const aim = c => (active(c) ? (c._fyFocus ? c._fyFocus() : c.hasAttribute("tabindex") && !off(c) ? c : null) : null);
	const focus = e => {
		const t = e && aim(e);
		if (t && t.focus) t.focus();
	};

	const button = id => o => {
		o = o || {};
		const e = mk(id, "fy-c fy-b" + (o.primary ? " fy-p" : ""), o.css || "", txt(o.label));
		e.setAttribute("role", "button");
		e.setAttribute("aria-label", txt(o.label));
		let disabled = !!o.disabled,
			busy = false;
		const draw = () => {
			e.setAttribute("aria-busy", String(busy));
			lock(e, disabled || busy);
		};
		Object.defineProperty(e, "disabled", {
			get: () => disabled || busy,
			set: x => {
				disabled = !!x;
				draw();
			},
		});
		draw();
		press(
			e,
			ev => {
				if (typeof o.onclick !== "function") return;
				const done = () => {
					busy = false;
					draw();
				};
				const failed = err => {
					done();
					if (callbackFailed(err) && active(e))
						framey.toast("Action failed. Try again or report the problem to Framey.", 4000);
				};
				busy = true;
				draw();
				try {
					const result = o.onclick(ev);
					if (result && typeof result.then === "function") Promise.resolve(result).then(done, failed);
					else done();
				} catch (err) {
					failed(err);
				}
			},
			id === "core" && !!o.nav,
		);
		return e;
	};

	const toggle = id => o => {
		o = o || {};
		const e = mk(id, "fy-c fy-sw", "");
		const track = mk(id, "fy-tr", "");
		track.append(mk(id, "fy-kn", ""));
		e.append(mk(id, "fy-l", "", txt(o.label)), track);
		e.setAttribute("role", "switch");
		e.setAttribute("aria-label", txt(o.label));
		let v = !!o.value;
		const draw = () => e.setAttribute("aria-checked", String(v));
		Object.defineProperty(e, "value", {
			get: () => v,
			set: x => {
				v = !!x;
				draw();
			},
		});
		draw();
		gate(e, o.disabled);
		press(e, () => {
			v = !v;
			draw();
			invoke(o, "onchange", v);
		});
		return e;
	};

	const slider = id => o => {
		o = o || {};
		const min = num(o.min, 0),
			max = Math.max(min, num(o.max, 100));
		const step = num(o.step, 1) > 0 ? num(o.step, 1) : 1;
		const dec = Math.max(dp(step), dp(min));
		const fmt = typeof o.format === "function" ? o.format : x => String(x);
		const snap = x => {
			const bounded = Math.min(max, Math.max(min, min + Math.round((x - min) / step) * step));
			return Math.min(max, Math.max(min, dec <= 100 ? +bounded.toFixed(dec) : +bounded.toPrecision(15)));
		};
		const e = mk(id, "fy-c", "");
		const rw = mk(id, "fy-rw", ""),
			fill = mk(id, "fy-fi", ""),
			thumb = mk(id, "fy-th", ""),
			val = mk(id, "fy-v", "");
		const rail = mk(id, "fy-ra", "");
		rail.append(fill);
		rw.append(rail, thumb);
		e.append(mk(id, "fy-l", "", txt(o.label)), rw, val);
		e.setAttribute("role", "slider");
		e.setAttribute("aria-label", txt(o.label));
		e.setAttribute("aria-valuemin", String(min));
		e.setAttribute("aria-valuemax", String(max));
		let v = snap(num(o.value, min));
		const draw = () => {
			const p = max > min ? ((v - min) / (max - min)) * 100 : 0;
			let t;
			try {
				t = txt(fmt(v));
			} catch (error) {
				callbackFailed(error);
				t = String(v);
			}
			fill.style.width = p + "%";
			thumb.style.left = p + "%";
			val.textContent = t;
			e.setAttribute("aria-valuenow", String(v));
			e.setAttribute("aria-valuetext", t);
		};
		const set = x => {
			x = snap(x);
			if (x === v) return false;
			v = x;
			draw();
			invoke(o, "oninput", v);
			return true;
		};
		Object.defineProperty(e, "value", {
			get: () => v,
			set: x => {
				discard();
				v = snap(num(x, min));
				draw();
			},
		});
		draw();
		let dirty = false,
			from = null,
			pointer = null,
			revision = 0;
		const release = pointerId => {
			if (pointerId == null || !rw.isConnected || typeof rw.releasePointerCapture !== "function") return;
			try {
				if (typeof rw.hasPointerCapture !== "function" || rw.hasPointerCapture(pointerId))
					rw.releasePointerCapture(pointerId);
			} catch (error) {
				if (!namedError(error, "NotFoundError")) diagnostic("ui.pointer_release_failed");
			}
		};
		function discard() {
			const p = pointer;
			revision++;
			dirty = false;
			from = pointer = null;
			release(p);
		}
		gate(e, o.disabled, disabled => {
			if (disabled) discard();
		});
		const commit = () => {
			if (!dirty) return;
			dirty = false;
			if (active(e) && !off(e)) invoke(o, "onchange", v);
		};
		const keys = new Set(["ArrowRight", "ArrowLeft", "Home", "End", "PageUp", "PageDown"]);
		e.addEventListener("keydown", ev => {
			if (!keys.has(ev.key) || ev.target !== e) return;
			ev.preventDefault();
			if (!active(e) || off(e) || !__trusted(ev)) return;
			const value =
				ev.key === "Home"
					? min
					: ev.key === "End"
						? max
						: v + (ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : ev.key === "PageUp" ? 10 : -10) * step;
			const before = revision;
			if (set(value) && revision === before && active(e) && !off(e)) dirty = true;
		});
		e.addEventListener("keyup", ev => {
			if (ev.target === e && keys.has(ev.key)) commit();
		});
		e.addEventListener("blur", commit);
		const at = ev => {
			const r = rw.getBoundingClientRect();
			if (r.width > 0) set(min + ((ev.clientX - r.left) / r.width) * (max - min));
		};
		const matching = ev => from !== null && (pointer == null || ev.pointerId === pointer);
		const end = ev => {
			if (!matching(ev)) return;
			const changed = from !== v,
				p = pointer;
			from = pointer = null;
			release(p);
			if (changed && active(e) && !off(e)) invoke(o, "onchange", v);
		};
		rw.addEventListener("pointerdown", ev => {
			if (!active(e) || off(e) || from !== null || (ev.button != null && ev.button !== 0) || !__trusted(ev)) return;
			ev.preventDefault();
			commit();
			if (!active(e) || off(e)) return;
			e.focus();
			if (!active(e) || off(e)) return;
			from = v;
			pointer = ev.pointerId == null ? null : ev.pointerId;
			if (typeof rw.setPointerCapture === "function" && pointer != null) {
				try {
					rw.setPointerCapture(pointer);
				} catch (error) {
					if (!namedError(error, "NotFoundError")) diagnostic("ui.pointer_capture_failed");
				}
			}
			at(ev);
		});
		rw.addEventListener("pointermove", ev => {
			if (matching(ev) && active(e) && !off(e) && __trusted(ev)) at(ev);
		});
		rw.addEventListener("pointerup", end);
		rw.addEventListener("pointercancel", end);
		rw.addEventListener("lostpointercapture", end);
		return e;
	};

	const list = id => o => {
		o = o || {};
		const e = mk(id, "fy-ls", "");
		e.setAttribute("role", "listbox");
		if (o.multi) e.setAttribute("aria-multiselectable", "true");
		let items = [],
			rows = [],
			cur = -1;
		const sel = new Set(o.selected == null ? [] : o.multi ? [].concat(o.selected) : [o.selected]);
		const value = () => (o.multi ? [...sel] : sel.size ? [...sel][0] : null);
		const mark = () => rows.forEach((r, i) => r.setAttribute("aria-selected", String(sel.has(items[i].id))));
		const rove = i => {
			cur = i;
			rows.forEach((r, j) => (r.tabIndex = j === i ? 0 : -1));
		};
		const pick = it => {
			if (!active(e) || !items.includes(it) || it.disabled) return;
			if (o.multi) sel.has(it.id) ? sel.delete(it.id) : sel.add(it.id);
			else {
				sel.clear();
				sel.add(it.id);
			}
			mark();
			invoke(o, "onselect", value());
		};
		e.setItems = next => {
			const focused = e.contains(document.activeElement),
				previous = cur >= 0 && items[cur] ? items[cur].id : null;
			const seen = new Set();
			items = (Array.isArray(next) ? next : [])
				.filter(it => it && typeof it === "object")
				.slice(0, 500)
				.map(it => ({
					id: it.id == null ? it.label : it.id,
					label: txt(it.label),
					sub: txt(it.sub),
					disabled: !!it.disabled,
				}))
				.filter(it => {
					if ((typeof it.id !== "string" && (typeof it.id !== "number" || !isFinite(it.id))) || seen.has(it.id))
						return false;
					seen.add(it.id);
					return true;
				});
			[...sel].forEach(k => {
				if (!items.some(it => it.id === k)) sel.delete(k);
			});
			rows = items.map(it => {
				const r = mk(id, "fy-c fy-o", "");
				r.append(mk(id, "", "", it.label));
				if (it.sub) r.append(mk(id, "fy-sub", "", it.sub));
				r.setAttribute("role", "option");
				r.setAttribute("aria-label", it.label);
				r.setAttribute("aria-disabled", String(it.disabled));
				Object.defineProperty(r, "_fyAlive", { value: () => active(e) && items.includes(it) });
				press(r, () => pick(it));
				return r;
			});
			e.replaceChildren(...rows);
			if (!items.length && o.empty) e.append(mk(id, "fy-em", "", txt(o.empty)));
			mark();
			const selected = items.findIndex(it => sel.has(it.id) && !it.disabled);
			const keep = focused ? items.findIndex(it => it.id === previous && !it.disabled) : selected;
			rove(keep >= 0 ? keep : selected >= 0 ? selected : items.findIndex(it => !it.disabled));
			if (focused && active(e) && rows[cur]) rows[cur].focus();
		};
		Object.defineProperty(e, "selected", { get: value });
		e._fyFocus = () => rows[cur] || null;
		e.addEventListener("focusin", ev => {
			const i = rows.indexOf(ev.target);
			if (active(e) && i >= 0 && !items[i].disabled) rove(i);
		});
		e.addEventListener("keydown", ev => {
			const d = ev.key === "ArrowDown" ? 1 : ev.key === "ArrowUp" ? -1 : 0;
			const i = rows.indexOf(ev.target);
			if (!active(e) || i < 0) return;
			if (ev.key === "Home" || ev.key === "End") {
				const target =
					ev.key === "Home"
						? items.findIndex(it => !it.disabled)
						: items.length - 1 - [...items].reverse().findIndex(it => !it.disabled);
				if (rows[target]) {
					ev.preventDefault();
					ev.stopPropagation();
					rows[target].focus();
				}
				return;
			}
			if (!d) return;
			for (let j = i + d; j >= 0 && j < rows.length; j += d)
				if (!items[j].disabled) {
					ev.preventDefault();
					ev.stopPropagation();
					rows[j].focus();
					return;
				}
		});
		e.setItems(o.items);
		return e;
	};

	const group =
		id =>
		(...kids) => {
			const g = mk(id, "fy-g", "");
			g.setAttribute("role", "group");
			g.append(...kids);
			let cur = null;
			g._fySync = () => {
				const plain = [...g.children].filter(c => !c._fyFocus && c.hasAttribute("tabindex"));
				cur = plain.includes(cur) && !off(cur) ? cur : plain.find(c => !off(c)) || null;
				plain.forEach(c => (c.tabIndex = c === cur ? 0 : -1));
			};
			g._fyFocus = () => {
				const a = cur && aim(cur);
				if (a) return a;
				for (const c of g.children) {
					const t = aim(c);
					if (t) return t;
				}
				return null;
			};
			g.addEventListener("focusin", ev => {
				const c = [...g.children].find(x => x.contains(ev.target));
				if (c && !c._fyFocus && c.hasAttribute("tabindex") && !off(c)) {
					cur = c;
					g._fySync();
				}
			});
			g.addEventListener("keydown", ev => {
				if (
					ev.target.closest &&
					ev.target.closest(
						"input,textarea,select,[contenteditable]:not([contenteditable='false']),[role='textbox'],[role='combobox']",
					)
				)
					return;
				const side = ev.key === "ArrowLeft" || ev.key === "ArrowRight";
				const d =
					ev.key === "ArrowDown" || ev.key === "ArrowRight"
						? 1
						: ev.key === "ArrowUp" || ev.key === "ArrowLeft"
							? -1
							: 0;
				const c = [...g.children].find(x => x.contains(ev.target));
				if (!active(g) || !d || !c || (side && (c._fyFocus || c.getAttribute("role") === "slider"))) return;
				for (
					let n = d > 0 ? c.nextElementSibling : c.previousElementSibling;
					n;
					n = d > 0 ? n.nextElementSibling : n.previousElementSibling
				) {
					const t = aim(n);
					if (t) {
						ev.preventDefault();
						ev.stopPropagation();
						t.focus();
						return;
					}
				}
			});
			g._fySync();
			return g;
		};

	const kit = (id, alive = () => true) => {
		const own =
			factory =>
			(...args) => {
				const e = factory(id)(...args);
				Object.defineProperty(e, "_fyAlive", { value: alive });
				return e;
			};
		return {
			button: own(button),
			toggle: own(toggle),
			slider: own(slider),
			list: own(list),
			group: own(group),
			focus,
		};
	};
	try {
		document.head.append(style);
		framey.ext.push((api, p) => {
			api.ui = kit(api.id, () => !p || !p.dead);
		});
	} catch (error) {
		style.remove();
		throw error;
	}
	if (privateApi) privateApi.ui = Object.freeze(kit("core"));
	framey.uiReady = true;
})();
