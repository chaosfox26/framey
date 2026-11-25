(() => {
	const framey = __FY;
	const privateApi = framey && framey._k && framey._k(typeof __fyt === "string" ? __fyt : "");
	if (!framey || !privateApi || framey.pickerReady) return;
	const recordFailure = code => {
		try {
			if (typeof privateApi.diagnostic === "function") privateApi.diagnostic(code);
		} catch {}
	};
	const isolate = (callback, code) => {
		try {
			callback();
			return true;
		} catch {
			recordFailure(code);
			return false;
		}
	};
	const focusSafely = callback => isolate(callback, "picker.focus_failed");
	let busy = false;
	const track = (api, fn) =>
		api.cleanup ? api.cleanup(fn) : api.on({ addEventListener() {}, removeEventListener: fn }, "fy-picker", fn);
	const join = (p, n) => (p ? p + "/" + n : n);
	const rootsOf = r =>
		(Array.isArray(r) ? r : [])
			.slice(0, 16)
			.map(x =>
				typeof x === "string"
					? { id: x, label: x }
					: x && { id: x.root || x.id || x.name, label: x.label || x.name || x.root || x.id },
			)
			.filter(x => x && x.id)
			.map(x => ({ id: String(x.id), label: String(x.label).slice(0, 60) }));

	const concealed = e => {
		if (!e || !e.isConnected) return true;
		for (let at = e; at; at = at.parentElement) {
			if (at.hidden || at.getAttribute("aria-hidden") === "true") return true;
			const css = getComputedStyle(at);
			if (
				css.display === "none" ||
				css.visibility === "hidden" ||
				css.visibility === "collapse" ||
				css.contentVisibility === "hidden" ||
				css.opacity === "0"
			)
				return true;
		}
		return false;
	};
	const focusable = e => {
		if (!e || e.tabIndex < 0 || e.disabled || concealed(e)) return false;
		for (let at = e; at; at = at.parentElement) {
			if (
				at.inert ||
				at.hasAttribute("inert") ||
				at.getAttribute("aria-disabled") === "true" ||
				at.matches("fieldset[disabled]")
			)
				return false;
		}
		return true;
	};

	const pick = (api, o, folder) =>
		new Promise(resolve => {
			if (busy) return resolve(null);
			o = o || {};
			const ui = api.ui,
				back = document.activeElement;
			const origin = back && back !== document.body && back !== document.documentElement ? back : null;
			const exts =
				!folder && Array.isArray(o.exts) && o.exts.length
					? o.exts.slice(0, 16).map(x => String(x).toLowerCase())
					: undefined;
			const name = String(o.title || "").slice(0, 80) || (folder ? "Choose a folder" : "Choose a file");
			let root = "",
				path = "",
				parent = null,
				seq = 0,
				done = false,
				loading = false,
				ready = false,
				entries = new Map(),
				chosen = null,
				known = [],
				untrack = null,
				last = null,
				observer = null,
				pageIndex = 0,
				pageRows = [],
				mode = "entries",
				detailText = "",
				detailEntry = null,
				unpanel = null;
			const box = (css, text) => {
				const e = framey.el(css, text);
				e.dataset.fy = api.id;
				return e;
			};
			const view = box(
				"position:fixed;top:0;left:0;right:0;bottom:0;z-index:100000;box-sizing:border-box;padding:8px;background:#000;color:#fff;font:20px sans-serif;overflow:hidden;display:flex;flex-direction:column",
			);
			view.setAttribute("role", "dialog");
			view.setAttribute("aria-modal", "true");
			view.setAttribute("aria-label", name);
			const crumb = box("font-size:18px;color:#fff;overflow-wrap:anywhere");
			const err = box("font-size:19px;color:#ff35c8;overflow-wrap:anywhere;display:none");
			err.setAttribute("role", "alert");
			const status = box("font-size:19px;color:#fff");
			status.setAttribute("role", "status");
			status.setAttribute("aria-live", "polite");
			const errorActions = ui.group();
			errorActions.style.display = "none";
			const rootRow = ui.group();
			const lst = ui.list({ empty: "Nothing here", onselect: n => open(n) });
			lst.style.overflow = "hidden";
			lst.style.minHeight = "0";
			lst.style.flex = "1";
			const detail = box("font-size:18px;line-height:1.3;white-space:pre-wrap;overflow-wrap:anywhere;flex:1");
			detail.dataset.pickerDetails = "true";
			detail.style.display = "none";
			const up = ui.button({
				label: "Up",
				disabled: true,
				onclick: () => {
					if (mode !== "entries") {
						mode = "entries";
						pageIndex = 0;
						paintRows();
					} else load(root, parent);
				},
			});
			const ok = ui.button({
				label: "Select",
				primary: true,
				disabled: true,
				onclick: () => {
					if (!alive() || !ready || loading || !root) return;
					if (mode === "name") {
						const entry = detailEntry;
						mode = "entries";
						pageIndex = 0;
						paintRows();
						open(entry, true);
						return;
					}
					if (folder) finish({ root, path });
					else if (chosen != null && entries.get(chosen)?.type === "file")
						finish({ root, path: join(path, chosen), name: chosen });
				},
			});
			const no = ui.button({ label: "Cancel", onclick: () => finish(null) });
			const pageSize = () =>
				Math.max(1, Math.floor(((view.clientHeight || document.documentElement.clientHeight || 320) - 280) / 60));
			const pageLabel = box("font-size:16px;color:#ff70d8;text-align:center;flex:1");
			const previous = ui.button({ label: "Previous", onclick: () => turn(-1) });
			const next = ui.button({ label: "Next", onclick: () => turn(1) });
			const pager = ui.group(previous, pageLabel, next);
			pager.setAttribute("aria-label", "Folder entries pages");
			const textParts = text => {
				const parts = [];
				let part = "",
					lines = 1,
					columns = 0;
				for (const c of String(text || "")) {
					if ((c === "\n" || columns >= 14) && lines >= 3) {
						parts.push(part);
						part = "";
						lines = 1;
						columns = 0;
					}
					if (columns >= 14) {
						columns = 0;
						lines++;
					}
					part += c;
					if (c === "\n") {
						lines++;
						columns = 0;
					} else columns++;
				}
				parts.push(part);
				return parts;
			};
			const pageCount = () =>
				mode === "entries"
					? Math.max(1, Math.ceil(pageRows.length / pageSize()))
					: mode === "roots"
						? Math.max(1, known.length)
						: textParts(detailText).length;
			const paintRows = () => {
				const size = pageSize(),
					count = pageCount();
				pageIndex = Math.max(0, Math.min(pageIndex, count - 1));
				const isDetail = mode !== "entries" && mode !== "roots";
				lst.style.display = isDetail ? "none" : "flex";
				detail.style.display = isDetail ? "" : "none";
				rootRow.style.display = mode === "entries" ? "flex" : "none";
				up.textContent = mode === "entries" ? "Up" : "Back";
				up.disabled = mode === "entries" && parent === null;
				ok.textContent =
					mode === "name" ? (entries.get(detailEntry)?.type === "dir" ? "Open folder" : "Choose file") : "Select";
				ok.disabled = !ready || loading || (mode !== "name" && !folder && chosen === null);
				if (isDetail) detail.textContent = textParts(detailText)[pageIndex];
				else if (mode === "roots") {
					const at = known[pageIndex];
					lst.setItems(at ? [{ id: at.id, label: at.label.length > 35 ? at.label.slice(0, 32) + "…" : at.label }] : []);
				} else
					lst.setItems(
						pageRows
							.slice(pageIndex * size, (pageIndex + 1) * size)
							.map(row => ({ ...row, label: row.label.length > 35 ? row.label.slice(0, 32) + "…" : row.label })),
					);
				previous.disabled = pageIndex === 0;
				next.disabled = pageIndex === count - 1;
				pageLabel.textContent = pageIndex + 1 + " of " + count;
			};
			const showDetails = (text, entry) => {
				mode = entry == null ? "details" : "name";
				detailText = text;
				detailEntry = entry;
				pageIndex = 0;
				paintRows();
				focusSafely(() => ui.focus(body));
			};
			const turn = delta => {
				if (!alive() || loading || pageIndex + delta < 0 || pageIndex + delta >= pageCount()) return;
				pageIndex += delta;
				paintRows();
				focusSafely(() => ui.focus(mode === "entries" || mode === "roots" ? lst : pager));
			};
			const body = ui.group(
				box("font-size:20px;font-weight:700", name.length <= 35 ? name : folder ? "Choose a folder" : "Choose a file"),
				crumb,
				status,
				err,
				errorActions,
				rootRow,
				lst,
				detail,
				ui.group(up, ok, no),
				pager,
			);
			body.style.flexDirection = "column";
			body.style.height = "100%";
			body.style.minHeight = "0";
			body.style.gap = "4px";
			view.append(body);

			const finish = v => {
				if (done) return;
				done = true;
				++seq;
				busy = false;
				if (observer) isolate(() => observer.disconnect(), "picker.cleanup_failed");
				if (typeof unpanel === "function") isolate(unpanel, "picker.cleanup_failed");
				isolate(() => view.remove(), "picker.cleanup_failed");
				if (typeof untrack === "function") isolate(untrack, "picker.cleanup_failed");
				focusSafely(() => {
					const active = document.activeElement;
					if (!(active && active !== document.body && focusable(active))) {
						if (back && focusable(back)) back.focus();
						else {
							const panel = document.querySelector('[role="region"][aria-label="Framey"]');
							const target =
								panel &&
								[
									...panel.querySelectorAll(
										'[role="tabpanel"] [tabindex], [role="tabpanel"] button, [role="tabpanel"] input, [role="tabpanel"] select, [role="tabpanel"] textarea',
									),
									...panel.querySelectorAll('[role="tab"][aria-selected="true"]'),
								].find(focusable);
							if (target) target.focus();
						}
					}
				});
				resolve(v);
			};
			const alive = () => {
				if (done) return false;
				if (origin && concealed(origin)) {
					finish(null);
					return false;
				}
				return true;
			};
			const open = (n, confirmed = false) => {
				if (mode === "roots") {
					mode = "entries";
					pageIndex = 0;
					return load(n, "");
				}
				const x = entries.get(n);
				if (!alive() || !x || loading) return;
				if (!confirmed && n.length > 35) return showDetails(n, n);
				if (x.type === "dir") load(root, join(path, n));
				else if (!folder && ready && x.type === "file") {
					chosen = n;
					ok.disabled = false;
					const active = document.activeElement;
					focusSafely(() => {
						ok.focus();
						if (active && view.contains(active)) active.focus();
					});
				}
			};
			const settle = () => {
				const a = document.activeElement;
				if (!view.contains(a) || a.getAttribute("aria-disabled") === "true") focusSafely(() => ui.focus(body));
			};
			const failure = message => {
				recordFailure("picker.request_failed");
				loading = false;
				ready = false;
				chosen = null;
				view.setAttribute("aria-busy", "false");
				lst.setAttribute("aria-busy", "false");
				status.textContent = "";
				ok.disabled = true;
				up.disabled = parent === null;
				const errorText = String(message || "Could not read this folder.").slice(0, 300);
				err.textContent = errorText.length > 80 ? "Could not read this folder." : errorText;
				if (errorText.length > 80) {
					mode = "error";
					detailText = errorText;
					pageIndex = 0;
					paintRows();
				}
				err.style.display = "";
				errorActions.replaceChildren(
					ui.button({
						label: "Retry",
						onclick: () => {
							if (last) load(last.root, last.path);
						},
					}),
				);
				errorActions.style.display = "";
				errorActions._fySync();
				settle();
			};
			const none = () => {
				err.textContent = "No folders are available to this module. Add one in loader.json (file_roots).";
				err.style.display = "";
				lst.setItems([]);
				rootRow.replaceChildren();
				crumb.textContent = "";
				entries = new Map();
				ready = false;
				up.disabled = true;
				ok.disabled = true;
				settle();
			};
			const load = (r, p) => {
				if (!alive()) return;
				const n = ++seq;
				mode = "entries";
				detail.style.display = "none";
				lst.style.display = "flex";
				up.textContent = "Up";
				ok.textContent = "Select";
				last = { root: r, path: p };
				loading = true;
				ready = false;
				chosen = null;
				ok.disabled = true;
				up.disabled = true;
				view.setAttribute("aria-busy", "true");
				lst.setAttribute("aria-busy", "true");
				status.textContent = "Loading folders…";
				err.textContent = "";
				err.style.display = "none";
				errorActions.replaceChildren();
				errorActions.style.display = "none";
				Promise.resolve()
					.then(() =>
						!alive() || n !== seq
							? null
							: api._rpc("files_list", { root: r, path: p, dirs_only: folder, exts, pid: api.id }),
					)
					.then(
						res => res,
						e => ({ error: e && e.message, code: e && e.code }),
					)
					.then(res => {
						if (!alive() || n !== seq) return;
						if (res && res.code === "disposed") return finish(null);
						loading = false;
						view.setAttribute("aria-busy", "false");
						lst.setAttribute("aria-busy", "false");
						status.textContent = "";
						const good = !!res && typeof res === "object";
						if (good && Array.isArray(res.roots)) known = rootsOf(res.roots);
						if (r == null && !known.length && (!good || !res.error || res.code === "bad_root")) return none();
						if (!good || res.error || !Array.isArray(res.entries)) {
							return failure(good && res.error);
						}
						if (r == null) {
							const pref = String(o.root || "");
							const at = known.some(x => x.id === pref) ? pref : (known.find(x => x.id === "data") || known[0]).id;
							const sp = String(o.path || "");
							if (at !== res.root || sp) return load(at, sp);
						}
						err.textContent = "";
						err.style.display = "none";
						root = String(res.root || r || "");
						if (!root) return failure("That location is not available.");
						path = String(res.path == null ? p || "" : res.path);
						parent = typeof res.parent === "string" ? res.parent : null;
						chosen = null;
						ready = true;
						const rows = res.entries.slice(0, 500).filter(x => x && typeof x.name === "string" && x.name);
						entries = new Map(rows.map(x => [x.name, x]));
						pageIndex = 0;
						pageRows = rows.map(x => ({
							id: x.name,
							label: x.name,
							sub: x.type === "dir" ? "Folder" : "",
							disabled: !(x.type === "dir" || (x.type === "file" && !folder)),
						}));
						paintRows();
						if (known.length > 1) {
							rootRow.replaceChildren(
								...(known.length > 2 || known.some(x => x.label.length > 20)
									? [
											ui.button({
												label: "Locations",
												onclick: () => {
													mode = "roots";
													pageIndex = 0;
													paintRows();
													focusSafely(() => ui.focus(lst));
												},
											}),
										]
									: known.map(x =>
											ui.button({
												label: x.label,
												primary: x.id === root,
												onclick: () => load(x.id, ""),
											}),
										)),
							);
							rootRow._fySync();
						}
						up.disabled = parent === null;
						ok.disabled = !folder;
						const locationText =
							[root].concat(path.split("/").filter(Boolean)).join(" / ") + (res.truncated ? "  (list shortened)" : "");
						if (locationText.length > 35)
							crumb.replaceChildren(ui.button({ label: "Location details", onclick: () => showDetails(locationText) }));
						else crumb.textContent = locationText;
						if (folder) focusSafely(() => ok.focus());
						focusSafely(() => ui.focus(lst));
						settle();
					})
					.catch(e => {
						if (alive() && n === seq) failure(e && e.message);
					});
			};
			view.addEventListener("keydown", ev => {
				if (ev.key === "Escape") {
					ev.preventDefault();
					ev.stopPropagation();
					finish(null);
				} else if (ev.key === "Tab") {
					const targets = [...view.querySelectorAll("[tabindex],button,input,select,textarea,a[href]")].filter(
						focusable,
					);
					const first = targets[0],
						last = targets[targets.length - 1],
						active = document.activeElement;
					if (
						!first ||
						(ev.shiftKey ? active === first || !targets.includes(active) : active === last || !targets.includes(active))
					) {
						ev.preventDefault();
						focusSafely(() => {
							if (first) (ev.shiftKey ? last : first).focus();
							else ui.focus(body);
						});
					}
				}
			});
			let armed = false,
				early = false;
			try {
				untrack = track(api, () => (armed ? finish(null) : (early = true)));
			} catch {
				recordFailure("picker.cleanup_failed");
				return resolve(null);
			}
			if (early) return resolve(null);
			armed = true;
			busy = true;
			if (!alive()) return;
			if (origin && typeof MutationObserver === "function") {
				const observed = isolate(() => {
					observer = new MutationObserver(() => {
						alive();
					});
					for (let at = origin; at; at = at.parentElement) {
						observer.observe(at, {
							childList: true,
							attributes: true,
							attributeFilter: ["hidden", "aria-hidden", "style", "class"],
						});
					}
				}, "picker.cleanup_failed");
				if (!observed) return finish(null);
			}
			if (typeof framey.onPanel === "function") {
				if (
					!isolate(() => {
						unpanel = framey.onPanel(open => {
							if (!open) finish(null);
						});
					}, "picker.cleanup_failed")
				)
					return finish(null);
			}
			document.body.append(view);
			focusSafely(() => ui.focus(body));
			load(undefined, "");
		});

	const attach = api => {
		api.pickFile = o => pick(api, o, false);
		api.pickFolder = o => pick(api, o, true);
	};
	framey.plugins.forEach(p => {
		const a = privateApi && privateApi.apiOf(p);
		if (a) attach(a);
	});
	if (Object.isExtensible(framey.ext)) framey.ext.push(attach);
	framey.pickerReady = true;
})();
