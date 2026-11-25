/* Independent, experimental Decky-facing host. The iframe owns lifecycle and globals;
 * it is an ordinary trusted-plugin realm, not a security sandbox. */
const createFrameyDeckyHost = function (api, options = {}) {
	if (
		!api ||
		typeof api.call !== "function" ||
		typeof api.cleanup !== "function" ||
		typeof api.events?.on !== "function"
	)
		throw new TypeError("A plugin-owned Framey API is required");
	if (typeof options.createViewRuntime !== "function") throw new TypeError("createViewRuntime is required");
	if (typeof options.name !== "string" || !options.name) throw new TypeError("Plugin name is required");
	if (
		options.assets != null &&
		(typeof options.assets !== "object" ||
			typeof options.assets.install !== "function" ||
			typeof options.assets.dispose !== "function")
	)
		throw new TypeError("Owned asset resolver install and dispose methods are required");
	if (options.createNavigation != null && typeof options.createNavigation !== "function")
		throw new TypeError("Navigation factory must be a function");
	if (options.createMenus != null && typeof options.createMenus !== "function")
		throw new TypeError("Menu factory must be a function");
	if (options.createSidebar != null && typeof options.createSidebar !== "function")
		throw new TypeError("Sidebar factory must be a function");
	if (options.moduleGraph === true && typeof options.createModules !== "function")
		throw new TypeError("Owned module graph factory is required");
	if (options.rpcResultVersion !== undefined && options.rpcResultVersion !== 1)
		throw new TypeError("Unsupported Framey RPC result envelope version");
	const unavailable = new Set(),
		errors = [],
		subscriptions = new Map(),
		pending = new Set(),
		resources = new Set();
	let frame,
		realm,
		view,
		root,
		plugin,
		disposed = false,
		dismounted = false,
		unregister;
	const nativeRoutesEnabled = options.nativeRoutes === true && !options.router;
	const routes = new Map();
	const dialogs = [];
	let dialogContext = null,
		dialogSequence = 0,
		dialogBaseTitle = "";
	let routeHistory = [],
		routeModal = null,
		routeRoot = null,
		routeContext = null,
		routeTransition = false;
	let modulesAdapter = null,
		loadStarted = false;
	const graphWaits = new Set();
	let sidebarAdapter = null;
	let menuAdapter = null,
		menuOwner = null,
		menuUnsupportedEpoch = 0;
	let navigationAdapter = null,
		navigationDiagnostic = null,
		navigationReady = false;
	let panel = "idle";
	let layout = { overflow: false, mode: "pending", viewportHeight: 0, contentHeight: 0 };
	const status = () => ({
		experimental: true,
		panel,
		nativeRoutes: nativeRoutesEnabled,
		nativeRouteCount: routes.size,
		nativeRoute: routeHistory.at(-1)?.pathname ?? null,
		moduleGraph:
			typeof modulesAdapter?.status === "function"
				? { ...modulesAdapter.status() }
				: { scope: options.moduleGraph === true ? "owned-http-graph" : "single-bundle", state: "idle" },
		nativeDialogs: true,
		dialogCount: dialogs.length,
		sidebar:
			typeof sidebarAdapter?.status === "function"
				? { ...sidebarAdapter.status() }
				: { scope: "unavailable", routeReporting: false },
		menus:
			typeof menuAdapter?.status === "function"
				? { ...menuAdapter.status() }
				: { scope: "unavailable", steamSideMenus: false, openCount: 0 },
		externalNavigation: {
			scope: navigationAdapter ? "framey-external-links" : "unavailable",
			lastDiagnostic: navigationDiagnostic ? { ...navigationDiagnostic } : null,
		},
		layout: { ...layout },
		steamIntegration: options.router && options.steamClient ? "provided-unverified" : "unavailable",
		unsupported: [...unavailable].sort(),
		errors: [...errors],
	});
	const publish = () => {
		try {
			options.onStatus?.(status());
		} catch {}
	};
	const graphErrorCodes = new Set([
		"invalid_arguments",
		"not_allowed",
		"service_closed",
		"busy",
		"rate_limited",
		"invalid_provenance",
		"unsupported_format",
		"code_limit",
		"code_changed",
		"code_expired",
		"stale_caller",
		"code_unavailable",
		"not_owned",
		"bad_response",
		"module_graph_invalid",
		"module_graph_disposed",
		"module_graph_unavailable",
		"module_graph_realm",
		"module_import_failed",
		"module_execution_failed",
		"module_capability_unsupported",
	]);
	const graphFailure = failure => {
		const code = graphErrorCodes.has(failure?.code) ? failure.code : "module_execution_failed";
		const safe = Object.assign(new Error("Module graph unavailable: " + code), { code });
		// Native stacks can contain capability URLs even when the message is fixed.
		safe.stack = safe.name + ": " + safe.message;
		return safe;
	};
	const errorMessage = error =>
		options.moduleGraph === true ? graphFailure(error).message : String(error?.message ?? error);
	const publicCapability = name =>
		options.moduleGraph === true
			? String(name)
					.replace(/http:\/\/127\.0\.0\.1:[0-9]+\/g\/[a-f0-9]{64}(?:\/[^\s"'<>]*)?/gi, "[module graph]")
					.replace(/[a-f0-9]{64}/gi, "[module graph token]")
			: name;
	const bridgeError = envelope => {
		const error = new Error(errorMessage(envelope.error));
		if (typeof envelope.code === "string") error.code = envelope.code;
		return error;
	};
	const recordError = error => {
		if (errors.length < 50) errors.push(errorMessage(error));
		publish();
	};
	const missing = name => {
		unavailable.add(publicCapability(name));
		publish();
	};
	const unsupported = name => {
		missing(name);
		if (options.moduleGraph === true) throw graphFailure({ code: "module_capability_unsupported" });
		throw new Error("Framey compatibility: unsupported " + name);
	};
	const active = () => {
		if (disposed) throw new Error("Framey Decky host disposed");
	};
	const safely = fn => {
		try {
			const result = fn();
			if (result?.then) result.catch(recordError);
		} catch (error) {
			recordError(error);
		}
	};
	const own = cleanup => {
		resources.add(cleanup);
		return () => {
			if (resources.delete(cleanup)) safely(cleanup);
		};
	};
	const rpcResult = value => {
		if (options.rpcResultVersion === 1) {
			const fields =
				value && typeof value === "object" && !Array.isArray(value) ? Object.getOwnPropertyDescriptors(value) : null;
			if (
				fields &&
				Reflect.ownKeys(fields).length === 2 &&
				Object.prototype.hasOwnProperty.call(fields, "__framey_decky_rpc_result__") &&
				Object.prototype.hasOwnProperty.call(fields, "value") &&
				fields.__framey_decky_rpc_result__ &&
				"value" in fields.__framey_decky_rpc_result__ &&
				fields.__framey_decky_rpc_result__.value === 1 &&
				fields.value &&
				"value" in fields.value
			)
				return fields.value.value;
			if (
				fields &&
				!fields.__framey_decky_rpc_result__ &&
				fields.error &&
				"value" in fields.error &&
				fields.error.value
			)
				throw bridgeError(value);
			throw Object.assign(new Error("Invalid Framey RPC result envelope"), { code: "bad_response" });
		}
		if (value && typeof value === "object" && value.error) throw bridgeError(value);
		return value;
	};
	const request = (method, args, legacy) => {
		active();
		if (typeof method !== "string" || !/^[A-Za-z][A-Za-z0-9_]*$/.test(method))
			return Promise.reject(new TypeError("Only public backend methods are callable"));
		return new Promise((resolve, reject) => {
			const item = { reject };
			pending.add(item);
			Promise.resolve()
				.then(() => {
					active();
					return api.call(method, { args, legacy });
				})
				.then(
					value => {
						if (!pending.delete(item)) return;
						try {
							resolve(rpcResult(value));
						} catch (error) {
							reject(error);
						}
					},
					error => {
						if (pending.delete(item)) reject(error);
					},
				);
		});
	};
	const call = async (method, ...args) => request(method, args, false);
	const compatEventWire = "decky.compat.event";
	const terminalOutputName = /^terminal_output#[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
	const terminalEventArgs = (payload, name) => {
		if (
			!payload ||
			typeof payload !== "object" ||
			Array.isArray(payload) ||
			Object.keys(payload).length !== 3 ||
			!Object.prototype.hasOwnProperty.call(payload, "v") ||
			!Object.prototype.hasOwnProperty.call(payload, "name") ||
			!Object.prototype.hasOwnProperty.call(payload, "args") ||
			payload.v !== 1 ||
			payload.name !== name ||
			!Array.isArray(payload.args)
		)
			return null;
		return payload.args;
	};
	const addEventListener = (name, listener) => {
		active();
		if (typeof name !== "string" || !name || typeof listener !== "function")
			throw new TypeError("Event name and listener required");
		let entries = subscriptions.get(name);
		if (!entries) subscriptions.set(name, (entries = new Map()));
		if (entries.has(listener)) return listener;
		const enveloped = name.length === 52 && terminalOutputName.test(name);
		const off = api.events.on(enveloped ? compatEventWire : name, payload => {
			if (disposed || !entries.has(listener)) return;
			const args = enveloped ? terminalEventArgs(payload, name) : Array.isArray(payload) ? payload : [payload];
			if (args) safely(() => listener(...args));
		});
		if (typeof off !== "function") throw new TypeError("Framey event subscription must provide cleanup");
		entries.set(listener, off);
		return listener;
	};
	const removeEventListener = (name, listener) => {
		const entries = subscriptions.get(name),
			off = entries?.get(listener);
		if (!off) return;
		entries.delete(listener);
		if (!entries.size) subscriptions.delete(name);
		safely(off);
	};
	const bound = new WeakMap();
	const absent = name =>
		new Proxy(function () {}, {
			get(_, key) {
				if (typeof key !== "string" || key === "then") return undefined;
				missing(name + "." + key);
				if (/^(Register|Unregister)/.test(key)) return undefined;
				return absent(name + "." + key);
			},
			apply() {
				return unsupported(name);
			},
		});
	const provider = (name, supplied, query = false, identities = []) =>
		new Proxy(supplied || {}, {
			get(target, key) {
				if (typeof key !== "string" || key === "then") return undefined;
				if (key in target) {
					const value = target[key];
					if (typeof value !== "function" || identities.includes(value)) return value;
					let targetBound = bound.get(target);
					if (!targetBound) bound.set(target, (targetBound = new WeakMap()));
					if (!targetBound.has(value)) targetBound.set(value, Object.assign(value.bind(target), value));
					return targetBound.get(value);
				}
				if (query && /^(find|get)/.test(key))
					return () => {
						missing(name + "." + key);
						return undefined;
					};
				return (..._args) => unsupported(name + "." + key);
			},
		});
	const graphDisposedError = () =>
		Object.assign(new Error("Module graph unavailable: module_graph_disposed"), { code: "module_graph_disposed" });
	const waitForGraph = promise =>
		new Promise((resolve, reject) => {
			if (disposed) {
				reject(graphDisposedError());
				Promise.resolve(promise).catch(() => {});
				return;
			}
			graphWaits.add(reject);
			Promise.resolve(promise).then(
				value => {
					if (graphWaits.delete(reject)) resolve(value);
				},
				error => {
					if (graphWaits.delete(reject)) reject(error);
				},
			);
		});
	const validGraphLease = value => {
		let fields;
		try {
			fields = value && Object.getOwnPropertyDescriptors(value);
		} catch {
			return null;
		}
		if (!fields || ["root", "entry", "token", "ttl"].some(key => !fields[key] || !("value" in fields[key])))
			return null;
		value = Object.fromEntries(["root", "entry", "token", "ttl"].map(key => [key, fields[key].value]));
		const match =
			typeof value?.root === "string" &&
			/^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/g\/([a-f0-9]{64})\/$/.exec(value.root);
		return match &&
			Number(match[1]) <= 65535 &&
			value.token === match[2] &&
			value.entry === value.root + "dist/index.js" &&
			value.ttl === 600
			? Object.freeze(value)
			: null;
	};
	const closeOwnedMenus = owner => {
		if (!menuAdapter || menuAdapter.status().disposed || (owner !== undefined && menuOwner !== owner)) return;
		menuAdapter.closeAll();
	};
	const openContextMenu = (element, anchor, creationOptions) => {
		active();
		const focus = anchor?.ownerDocument === realm.document ? anchor : realm.document.activeElement;
		const owner = focus?.closest?.("dialog") || null;
		const epoch = menuUnsupportedEpoch;
		try {
			const handle = menuAdapter.showContextMenu(element, anchor, creationOptions);
			menuOwner = owner;
			return handle;
		} catch (error) {
			if (epoch === menuUnsupportedEpoch) failRuntime(error);
			else recordError(error);
			throw error;
		}
	};
	// These routes belong only to this plugin's iframe. They never patch or
	// navigate Steam's application router.
	const parseRoute = (value, pattern = false) => {
		if (
			typeof value !== "string" ||
			value.length > (pattern ? 256 : 1024) ||
			!value.startsWith("/") ||
			value.startsWith("//") ||
			/[\\#\s]/.test(value) ||
			Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
		)
			return unsupported("nativeRoutes.path");
		const queryAt = value.indexOf("?");
		if (pattern && queryAt !== -1) return unsupported("nativeRoutes.path");
		const search = queryAt === -1 ? "" : value.slice(queryAt);
		if (search.length > 512) return unsupported("nativeRoutes.path");
		const pathname = queryAt === -1 ? value : value.slice(0, queryAt);
		const parts = pathname.split("/").slice(1);
		if (!parts.length || parts.length > 16 || parts.some(part => !part)) return unsupported("nativeRoutes.path");
		let segments;
		try {
			segments = parts.map(part => decodeURIComponent(part));
		} catch {
			return unsupported("nativeRoutes.path");
		}
		if (
			segments.some(part => {
				if (part === "." || part === ".." || part.length > 128) return true;
				return pattern && part.startsWith(":")
					? !/^:[A-Za-z][A-Za-z0-9_]{0,39}$/.test(part)
					: !/^[\p{L}\p{N}_.~-]+$/u.test(part);
			})
		)
			return unsupported("nativeRoutes.path");
		if (
			[
				"library",
				"store",
				"steam",
				"app",
				"apps",
				"home",
				"settings",
				"downloads",
				"friends",
				"login",
				"collection",
				"collections",
			].includes(segments[0].toLowerCase())
		)
			return unsupported("nativeRoutes.nativePath");
		if ([...new URLSearchParams(search)].length > 32) return unsupported("nativeRoutes.path");
		return { pathname, search, segments };
	};
	const matchRoute = target => {
		let best;
		for (const route of routes.values()) {
			if (route.segments.length !== target.segments.length) continue;
			const params = {},
				names = new Set();
			let matches = true,
				score = 0;
			for (let i = 0; i < route.segments.length; i++) {
				const part = route.segments[i];
				if (part.startsWith(":")) {
					const key = part.slice(1);
					if (names.has(key)) {
						matches = false;
						break;
					}
					names.add(key);
					params[key] = target.segments[i];
				} else if (part !== target.segments[i]) {
					matches = false;
					break;
				} else score++;
			}
			if (matches && (!best || score > best.score)) best = { ...target, route, params, score };
		}
		return best || unsupported("nativeRoutes.unknownRoute");
	};
	const closeNativeRoutes = () => {
		const modal = routeModal,
			ownedRoot = routeRoot;
		routeModal = null;
		routeRoot = null;
		routeHistory = [];
		if (!modal) return;
		routeTransition = true;
		try {
			for (const dialog of [...dialogs].reverse()) if (dialog.routeOwner === modal) finishDialog(dialog);
			safely(() => closeOwnedMenus(modal.dialog));
			safely(() => ownedRoot?.unmount());
			modal.back.onclick = null;
			modal.close.onclick = null;
			modal.dialog.oncancel = null;
			modal.dialog.onclose = null;
			if (modal.dialog.open) modal.dialog.close();
			modal.dialog.remove();
			modal.main.hidden = false;
			modal.main.inert = false;
			if (!disposed && modal.focus?.isConnected) modal.focus.focus({ preventScroll: true });
		} finally {
			routeTransition = false;
		}
		publish();
	};
	const showNativeRoute = () => {
		const current = routeHistory.at(-1);
		if (!current) return closeNativeRoutes();
		const doc = realm.document;
		if (!routeModal) {
			const dialog = doc.createElement("dialog"),
				header = doc.createElement("header"),
				body = doc.createElement("div"),
				title = doc.createElement("h2"),
				back = doc.createElement("button"),
				close = doc.createElement("button"),
				main = doc.querySelector("main");
			dialog.setAttribute("aria-label", options.name + " custom route");
			dialog.style.cssText =
				"box-sizing:border-box;position:fixed;inset:0;margin:0;width:100%;height:100%;max-width:none;max-height:none;border:0;padding:12px;background:#0e141b;color:#e6edf4";
			header.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:12px;flex:none";
			title.style.cssText = "flex:1;min-width:0;font:16px system-ui;overflow-wrap:anywhere;margin:0";
			body.style.cssText = "min-height:0;overflow:auto;flex:1";
			back.type = close.type = "button";
			back.textContent = "Back";
			close.textContent = "Close";
			back.onclick = () => navigateBack();
			close.onclick = () => closeNativeRoutes();
			dialog.oncancel = event => {
				event.preventDefault();
				closeNativeRoutes();
			};
			dialog.onclose = () => closeNativeRoutes();
			header.append(back, title, close);
			dialog.append(header, body);
			routeModal = { dialog, body, title, back, close, main, focus: doc.activeElement };
			main.hidden = true;
			main.inert = true;
			doc.body.append(dialog);
			if (typeof dialog.showModal !== "function") return unsupported("nativeRoutes.dialog");
			dialog.showModal();
			dialog.style.display = "flex";
			dialog.style.flexDirection = "column";
		}
		routeTransition = true;
		try {
			safely(() => closeOwnedMenus(routeModal.dialog));
			safely(() => routeRoot?.unmount());
			routeRoot = null;
		} finally {
			routeTransition = false;
		}
		const modal = routeModal;
		modal.title.textContent = current.pathname;
		const location = { pathname: current.pathname, search: current.search, hash: "" };
		const match = { params: { ...current.params }, path: current.route.path, url: current.pathname, isExact: true };
		const props = { ...current.route.props, location, match, history: { push: navigate, goBack: navigateBack } };
		try {
			routeRoot = view.ReactDOM.createRoot(modal.body);
			routeRoot.render(
				view.React.createElement(
					routeContext.Provider,
					{ value: { params: match.params, location } },
					view.React.createElement(current.route.Component, props),
				),
			);
			active();
			modal.back.focus({ preventScroll: true });
			publish();
		} catch (error) {
			failRuntime(error);
			throw error;
		}
	};
	function navigate(path) {
		active();
		if (routeTransition) return unsupported("nativeRoutes.transition");
		const current = matchRoute(parseRoute(path));
		if (routeHistory.length >= 64) return unsupported("nativeRoutes.historyLimit");
		if (routeHistory.at(-1)?.pathname === current.pathname && routeHistory.at(-1)?.search === current.search) return;
		routeHistory.push(current);
		showNativeRoute();
	}
	function navigateBack() {
		active();
		if (routeTransition) return unsupported("nativeRoutes.transition");
		routeHistory.pop();
		showNativeRoute();
	}
	const nativeNavigation = { Navigate: navigate, NavigateBack: navigateBack };
	const nativeRouter = {
		addRoute(path, Component, props = {}) {
			active();
			const parsed = parseRoute(path, true);
			if (typeof Component !== "function" && !(Component && typeof Component === "object"))
				throw new TypeError("Native route component required");
			// Match property-spread semantics for null and legacy callable bags;
			// callable values are never invoked as route wrappers.
			if ((props !== null && typeof props !== "object" && typeof props !== "function") || Array.isArray(props))
				throw new TypeError("Native route props must be a property bag");
			if (!routes.has(path) && routes.size >= 64) return unsupported("nativeRoutes.registrationLimit");
			if (routes.has(path)) this.removeRoute(path);
			routes.set(path, { ...parsed, path, Component, props: { ...props } });
			publish();
		},
		removeRoute(path) {
			parseRoute(path, true);
			routes.delete(path);
			if (routeHistory.at(-1)?.route.path === path) closeNativeRoutes();
			else routeHistory = routeHistory.filter(entry => entry.route.path !== path);
			publish();
		},
	};
	const routerHook = provider("routerHook", options.router || (nativeRoutesEnabled ? nativeRouter : undefined));
	// Native plugin dialogs have their own roots and close promises. They do not
	// use Steam modal managers, external browser contexts or popout windows.
	function refreshDialog(record) {
		if (record.closed) return;
		const p = record.config || {},
			dialog = record.dialog;
		const busy = record.actionPending || record.closePending;
		dialog.setAttribute("role", p.bAlertDialog ? "alertdialog" : "dialog");
		record.close.hidden = !!p.bHideCloseIcon;
		record.close.disabled = !!(busy || p.bCancelDisabled);
		record.bar.hidden = !!p.bHideCloseIcon;
		dialog.style.width = p.bAllowFullSize ? "calc(100% - 24px)" : "520px";
		dialog.style.height = p.bAllowFullSize ? "calc(100% - 24px)" : "fit-content";
		dialog.style.borderColor = p.bDestructiveWarning ? "#e99b68" : "#526477";
		const heading = record.body.querySelector("[data-framey-dialog-heading]");
		if (heading) {
			if (!heading.id) heading.id = record.id + "-heading";
			dialog.setAttribute("aria-labelledby", heading.id);
		} else {
			dialog.removeAttribute("aria-labelledby");
			dialog.setAttribute("aria-label", record.title);
		}
		for (const button of record.body.querySelectorAll("[data-framey-dialog-action]")) {
			const action = button.dataset.frameyDialogAction;
			button.disabled = !!(
				busy || (action === "ok" ? p.bOKDisabled : action === "middle" ? p.bMiddleDisabled : p.bCancelDisabled)
			);
		}
	}
	function finishOneDialog(record) {
		if (record.closed) return;
		record.closed = true;
		const index = dialogs.indexOf(record);
		if (index !== -1) dialogs.splice(index, 1);
		realm.document.title = dialogs.at(-1)?.title ?? dialogBaseTitle;
		try {
			safely(() => closeOwnedMenus(record.dialog));
			safely(() => record.root?.unmount());
			record.root = null;
			record.close.onclick = null;
			record.dialog.oncancel = null;
			record.dialog.onclick = null;
			record.dialog.onclose = null;
			if (record.dialog.open) record.dialog.close();
			record.dialog.remove();
			const focus = record.focus?.isConnected ? record.focus : record.fallbackFocus;
			if (!disposed && focus?.isConnected && (!dialogs.length || dialogs.at(-1).dialog.contains(focus)))
				focus.focus({ preventScroll: true });
		} finally {
			record.resolve();
			publish();
		}
	}
	function finishDialog(record) {
		const index = dialogs.indexOf(record);
		if (index === -1) return;
		finishOneDialog(record);
	}
	function initialDialogDismiss(record) {
		if (record.closed || !record.chain || record.closePending) return;
		record.closePending = true;
		refreshDialog(record);
		const generation = record.generation;
		const ownsClose = () => !record.closed && record.chain && generation === record.generation;
		const finish = () => {
			if (ownsClose()) finishDialog(record);
		};
		const failed = error => {
			if (!ownsClose()) return;
			recordError(error);
			finish();
		};
		try {
			const result = record.originalClose?.();
			if (result?.then) Promise.resolve(result).then(finish, failed);
			else finish();
		} catch (error) {
			failed(error);
		}
	}
	function closeFromModalRoot(record, props) {
		if (record?.closed) return;
		if (typeof props.closeModal === "function") props.closeModal();
		else if (record?.chain) initialDialogDismiss(record);
	}
	function runDialogAction(record, callback, after) {
		if (record && (record.closed || record.actionPending || record.closePending)) return;
		if (record) {
			record.actionPending = true;
			refreshDialog(record);
		}
		const generation = record?.generation;
		const ownsAction = () => !record || (!record.closed && record.generation === generation);
		const failed = error => {
			if (!ownsAction()) return;
			recordError(error);
			if (record && !record.closed) {
				record.actionPending = false;
				refreshDialog(record);
			}
		};
		const done = () => {
			if (!ownsAction()) return;
			if (record) record.actionPending = false;
			try {
				after();
			} catch (error) {
				recordError(error);
			}
			if (record && !record.closed) refreshDialog(record);
		};
		try {
			const result = callback?.();
			if (result?.then) Promise.resolve(result).then(done, failed);
			else done();
		} catch (error) {
			failed(error);
		}
	}
	function cancelDialog(record, reason, props = record.config || {}) {
		if (record.closed || props.bCancelDisabled || (reason === "backdrop" && props.bDisableBackgroundDismiss)) return;
		const callback = reason === "escape" ? props.onEscKeypress || props.onCancel : props.onCancel;
		runDialogAction(record, callback, () => closeFromModalRoot(record, props));
	}
	function renderDialog(record, element, initial) {
		const R = view.React;
		let content = element;
		if (
			initial &&
			R.isValidElement(element) &&
			(typeof element.type === "function" || typeof element.type === "object")
		)
			content = R.cloneElement(element, { closeModal: () => initialDialogDismiss(record) });
		if (nativeRoutesEnabled) content = R.createElement(routeContext.Provider, { value: record.routeState }, content);
		record.root = R && view.ReactDOM.createRoot(record.body);
		try {
			record.root.render(R.createElement(dialogContext.Provider, { value: record }, content));
			active();
			refreshDialog(record);
			if (dialogs.at(-1) === record && !record.closed) {
				const safe = record.body.querySelector('[data-framey-dialog-action="cancel"]:not(:disabled)') || record.close;
				if (!safe.hidden && !safe.disabled) safe.focus({ preventScroll: true });
			}
		} catch (error) {
			failRuntime(error);
			throw error;
		}
	}
	function showModal(element, parent, props = {}) {
		active();
		const doc = realm.document;
		let owned = parent == null || parent === realm || parent === doc;
		try {
			owned ||= parent?.ownerDocument === doc;
		} catch {
			return unsupported("dialogs.externalParent");
		}
		if (!owned) return unsupported("dialogs.externalParent");
		if (!props || typeof props !== "object" || Array.isArray(props))
			throw new TypeError("Dialog options must be an object");
		if (Object.keys(props).length > 16) return unsupported("dialogs.options");
		for (const key of Object.keys(props)) {
			if (["strTitle", "bNeverPopOut"].includes(key)) continue;
			if (["bForcePopOut", "bHideMainWindowForPopouts", "bHideActionIcons"].includes(key) && !props[key]) continue;
			if (props[key] !== undefined) return unsupported("dialogs." + key);
		}
		if (props.strTitle !== undefined && (typeof props.strTitle !== "string" || props.strTitle.length > 200))
			throw new TypeError("Dialog document title must be bounded text");
		if (dialogs.length >= 8) return unsupported("dialogs.limit");
		const dialog = doc.createElement("dialog"),
			bar = doc.createElement("div"),
			close = doc.createElement("button"),
			body = doc.createElement("div");
		const previousTitle = doc.title,
			title = props.strTitle || options.name + " dialog";
		dialog.dataset.frameyPluginDialog = "true";
		dialog.style.cssText =
			"box-sizing:border-box;position:fixed;inset:0;margin:auto;width:520px;height:fit-content;max-width:calc(100% - 24px);max-height:calc(100% - 24px);border:1px solid #526477;border-radius:8px;padding:0;background:#0e141b;color:#e6edf4;overflow:hidden;flex-direction:column";
		bar.style.cssText = "display:flex;justify-content:flex-end;padding:8px 8px 0;flex:none";
		body.style.cssText =
			"padding:12px 16px 16px;min-width:0;min-height:0;flex:1;display:flex;flex-direction:column;overflow:auto";
		close.type = "button";
		close.textContent = "Close";
		bar.append(close);
		dialog.append(bar, body);
		let resolve;
		const ClosedPromise = new Promise(done => (resolve = done));
		const current = routeHistory.at(-1);
		const record = {
			id: "fy-plugin-dialog-" + ++dialogSequence,
			dialog,
			bar,
			close,
			body,
			resolve,
			ClosedPromise,
			title,
			previousTitle,
			focus: doc.activeElement,
			fallbackFocus: dialogs.at(-1)?.fallbackFocus || dialogs.at(-1)?.focus || null,
			routeOwner: routeModal,
			routeState: {
				params: { ...current?.params },
				location: { pathname: current?.pathname || "", search: current?.search || "", hash: "" },
			},
			root: null,
			closed: false,
			chain: true,
			generation: 0,
			config: null,
			actionPending: false,
			closePending: false,
			originalClose: typeof element?.props?.closeModal === "function" ? element.props.closeModal : null,
		};
		close.onclick = () => cancelDialog(record, "cancel");
		dialog.oncancel = event => {
			event.preventDefault();
			cancelDialog(record, "escape");
		};
		dialog.onclick = event => {
			if (event.target !== dialog) return;
			const bounds = dialog.getBoundingClientRect();
			if (
				event.clientX < bounds.left ||
				event.clientX > bounds.right ||
				event.clientY < bounds.top ||
				event.clientY > bounds.bottom
			)
				cancelDialog(record, "backdrop");
		};
		dialog.onclose = () => finishDialog(record);
		if (!dialogs.length) dialogBaseTitle = doc.title;
		dialogs.push(record);
		doc.body.append(dialog);
		doc.title = title;
		try {
			dialog.showModal();
			dialog.style.display = "flex";
			renderDialog(record, element, true);
		} catch (error) {
			finishDialog(record);
			throw error;
		}
		publish();
		return Object.freeze({
			Close: () => finishDialog(record),
			ClosedPromise,
			Update(next) {
				active();
				if (record.closed) throw new Error("Dialog is closed");
				record.chain = false;
				record.originalClose = null;
				record.closePending = false;
				record.actionPending = false;
				record.generation++;
				safely(() => closeOwnedMenus(record.dialog));
				safely(() => record.root?.unmount());
				record.root = null;
				record.config = null;
				if (!record.closed) renderDialog(record, next, false);
			},
		});
	}
	const fetchNoCors = async (url, init = {}) => {
		active();
		if (typeof api.fetch !== "function") return unsupported("network.fetch");
		const response = await api.fetch(String(url), init);
		active();
		if (response?.error) throw bridgeError(response);
		if (!response || !Number.isInteger(response.status)) throw new TypeError("Framey fetch returned no HTTP status");
		if (response instanceof realm.Response) return response;
		const encoded = response.b64 ?? response.base64;
		const body = encoded ? Uint8Array.from(realm.atob(encoded), char => char.charCodeAt(0)) : (response.text ?? "");
		return new realm.Response([101, 204, 205, 304].includes(response.status) ? null : body, {
			status: response.status,
			headers: response.headers,
		});
	};
	// Legacy callers consume a transport envelope; modern callers keep Response.
	const legacyFetchNoCors = async (url, init = {}) => {
		try {
			active();
			if (!init || typeof init !== "object" || Array.isArray(init))
				throw new TypeError("Legacy fetch options must be an object");
			const request = { ...init };
			if (!request.method) request.method = "POST";
			if (Object.prototype.hasOwnProperty.call(request, "json")) {
				if (Object.prototype.hasOwnProperty.call(request, "body"))
					throw new TypeError("Legacy fetch body is ambiguous");
				request.body = JSON.stringify(request.json);
				if (request.body === undefined) throw new TypeError("Legacy fetch JSON is not serializable");
				const headers = new realm.Headers(request.headers);
				if (!headers.has("content-type")) headers.set("content-type", "application/json");
				request.headers = Object.fromEntries(headers.entries());
				delete request.json;
			}
			const response = await fetchNoCors(url, request);
			const body = await response.text();
			active();
			return {
				success: true,
				result: { body, status: response.status, headers: Object.fromEntries(response.headers.entries()) },
			};
		} catch {
			// Provider URLs may carry credentials; do not echo transport errors.
			return { success: false, result: "Legacy fetch failed" };
		}
	};
	// Translate a bounded host-element body to text and native action buttons.
	// No component, HTML, URL, or callback executes during collection.
	function collectToastBody(value, maxActions) {
		const handlers = new Map(),
			seen = new Set();
		let nodes = 0;
		function text(value, depth = 0, collect = true) {
			if (depth > 12 || ++nodes > 128) throw new TypeError("Toast body limit exceeded");
			if (value == null) return "";
			if (typeof value === "boolean") return depth === 0 ? String(value) : "";
			if (typeof value === "string" || typeof value === "number") return String(value).slice(0, 400);
			if (typeof value !== "object") throw new TypeError("Unsupported toast body");
			if (seen.has(value)) throw new TypeError("Toast body is cyclic");
			seen.add(value);
			try {
				if (Array.isArray(value))
					return value
						.map(child => text(child, depth + 1, collect))
						.join("")
						.slice(0, 400);
				if (!view?.React.isValidElement(value) || value.ref != null || typeof value.type !== "string")
					throw new TypeError("Unsupported toast element");
				if (!["a", "button", "span", "div", "p", "strong", "b", "em", "i", "br"].includes(value.type))
					throw new TypeError("Unsupported toast element tag");
				for (const key of Object.keys(value.props))
					if (!["children", "onClick", "href"].includes(key)) throw new TypeError("Unsupported toast element property");
				if (value.props.href != null && (value.type !== "a" || value.props.href !== "#"))
					throw new TypeError("Unsupported toast navigation");
				if (value.props.onClick != null && typeof value.props.onClick !== "function")
					throw new TypeError("Invalid toast action callback");
				const action = typeof value.props.onClick === "function";
				if (action && !["a", "button"].includes(value.type)) throw new TypeError("Unsupported toast action tag");
				if (value.type === "br") return "\n";
				const label = text(value.props.children, depth + 1, collect && !action);
				if (action) {
					if (!collect) throw new TypeError("Unsupported nested toast action");
					if (handlers.size >= maxActions) throw new TypeError("Native notifications allow three actions");
					if (!label.trim()) throw new TypeError("Toast action label required");
					handlers.set("decky-action-" + handlers.size, { label: label.slice(0, 40), callback: value.props.onClick });
				}
				return label;
			} finally {
				seen.delete(value);
			}
		}
		return { body: text(value), handlers };
	}
	const toastNotices = new Set();
	const retireToast = notice => {
		if (!toastNotices.delete(notice)) return false;
		notice.handlers.clear();
		return true;
	};
	own(() => {
		for (const notice of [...toastNotices]) {
			retireToast(notice);
			safely(() => notice.handle?.dismiss());
		}
	});
	const toaster = {
		toast: message => {
			active();
			if (typeof api.notify !== "function") return unsupported("notifications");
			if (!message || typeof message !== "object" || Array.isArray(message))
				throw new TypeError("Toast message required");
			if (message.onClick != null && typeof message.onClick !== "function")
				throw new TypeError("Invalid toast click callback");
			if (toastNotices.size >= 16) throw new Error("Notification limit reached");
			let collected;
			try {
				collected = collectToastBody(message.body, message.onClick ? 2 : 3);
			} catch (error) {
				missing("notifications.body");
				throw error;
			}
			const { body, handlers } = collected;
			if (message.onClick) handlers.set("decky-open", { label: "Open", callback: message.onClick });
			if (message.logo != null) missing("notifications.logo");
			const notice = { handlers, handle: null };
			const payload = {
				title: String(message.title ?? ""),
				body,
				actions: [...handlers].map(([id, handler]) => ({ id, label: handler.label })),
				onaction: async id => {
					if (disposed || !toastNotices.has(notice) || !handlers.has(id)) return;
					const handler = handlers.get(id);
					retireToast(notice);
					// Native buttons supply an action ID, not a rendered body DOM node.
					// This inert event supports the reviewed persist/target callback shape.
					const target = { outerHTML: "", onClick: handler.callback };
					const event = {
						type: "click",
						target,
						currentTarget: target,
						persist() {},
						preventDefault() {},
						stopPropagation() {},
					};
					try {
						await handler.callback(event);
					} catch (error) {
						recordError(error);
					}
				},
				onclose: () => retireToast(notice),
			};
			if (message.duration != null) payload.duration = message.duration;
			active();
			if (toastNotices.size >= 16) throw new Error("Notification limit reached");
			toastNotices.add(notice);
			try {
				notice.handle = api.notify(payload);
				if (!notice.handle || typeof notice.handle.dismiss !== "function")
					throw new TypeError("Notifier must return dismiss handle");
				if (notice.handle.id === 0) retireToast(notice);
				if (disposed) notice.handle.dismiss();
			} catch (error) {
				retireToast(notice);
				throw error;
			}
			return Object.freeze({
				id: notice.handle.id,
				dismiss() {
					if (retireToast(notice)) notice.handle.dismiss();
				},
			});
		},
	};
	const modern = {
		_version: options.apiVersion ?? 2,
		call,
		callable:
			method =>
			(...args) =>
				call(method, ...args),
		addEventListener,
		removeEventListener,
		routerHook,
		toaster,
		fetchNoCors,
		executeInTab: () => unsupported("executeInTab"),
	};
	const legacy = {
		callPluginMethod: async (method, kwargs = {}) => {
			try {
				if (!kwargs || Array.isArray(kwargs) || typeof kwargs !== "object")
					throw new TypeError("Legacy keyword arguments must be an object");
				return { success: true, result: await request(method, kwargs, true) };
			} catch (error) {
				return { success: false, result: errorMessage(error) };
			}
		},
		toaster,
		routerHook,
		fetchNoCors: legacyFetchNoCors,
		addEventListener,
		removeEventListener,
		callServerMethod: () => unsupported("legacy.callServerMethod"),
	};
	function controls() {
		const R = view.React,
			h = R.createElement;
		const children = p => p.children;
		const box = (tag, className) => p => h(tag, { className, style: p.style }, children(p));
		const label = (p, control) =>
			h(
				"label",
				{ className: "fy-decky-field", style: p.style },
				h("span", null, p.label),
				control,
				p.description ? h("small", null, p.description) : null,
			);
		const button = p =>
			h(
				"button",
				{
					type: "button",
					disabled: p.disabled,
					onClick: p.onClick,
					style: p.style,
					className: p.className,
					title: p.description,
				},
				p.children ?? p.label,
			);
		// Some bundles inspect .render on controls. This is our own component implementation.
		button.render = button;
		const dropdown = p => {
			const values = p.rgOptions || p.options || [];
			const selected = p.selectedOption?.data ?? p.selectedOption;
			const select = h(
				"select",
				{
					disabled: p.disabled,
					value: String(selected ?? ""),
					onChange: event => p.onChange?.(values.find(item => String(item.data) === event.target.value)),
				},
				...values.map(item =>
					h(
						"option",
						{ key: String(item.data), value: String(item.data), disabled: item.disabled },
						typeof item.label === "string" ? item.label : String(item.data),
					),
				),
			);
			return p.label ? label(p, select) : select;
		};
		const localNavigation = {
			...(menuAdapter
				? {
						CloseSideMenus: () => {
							closeOwnedMenus();
						},
					}
				: {}),
			...(nativeRoutesEnabled ? nativeNavigation : {}),
			...(navigationAdapter ? { NavigateToExternalWeb: url => navigationAdapter.navigateToExternalWeb(url) } : {}),
		};
		const classes = new Proxy({}, { get: (_, key) => "fy-decky-" + String(key) });
		const dialogContainer = (name, heading) =>
			R.forwardRef((p, ref) =>
				h(
					"div",
					{
						...p,
						ref,
						className: ["fy-decky-" + name, p.className].filter(Boolean).join(" "),
						...(heading ? { role: "heading", "aria-level": heading, "data-framey-dialog-heading": "true" } : {}),
					},
					p.children,
				),
			);
		const DialogHeader = dialogContainer("DialogHeader", 2);
		const ModalRoot = p => {
			const record = R.useContext(dialogContext);
			R.useLayoutEffect(() => {
				if (!record) return;
				for (const key of Object.keys(p))
					if (
						![
							"children",
							"onCancel",
							"closeModal",
							"onOK",
							"onEscKeypress",
							"className",
							"modalClassName",
							"style",
							"bAllowFullSize",
							"bDestructiveWarning",
							"bDisableBackgroundDismiss",
							"bHideCloseIcon",
							"bOKDisabled",
							"bCancelDisabled",
							"onMiddleButton",
							"strTitle",
							"strDescription",
							"strOKButtonText",
							"strCancelButtonText",
							"strMiddleButtonText",
							"bAlertDialog",
							"bMiddleDisabled",
						].includes(key)
					)
						missing("dialogs.ModalRoot." + key);
				record.config = p;
				refreshDialog(record);
				return () => {
					if (record.config === p) record.config = null;
				};
			}, [record, p]);
			return h(
				"div",
				{ className: ["fy-decky-ModalRoot", p.className, p.modalClassName].filter(Boolean).join(" "), style: p.style },
				p.children,
			);
		};
		const ConfirmModal = p => {
			const record = R.useContext(dialogContext);
			const action = callback => runDialogAction(record, callback, () => closeFromModalRoot(record, p));
			return h(
				ModalRoot,
				p,
				p.strTitle != null ? h(DialogHeader, null, p.strTitle) : null,
				p.strDescription != null ? h("div", { className: "fy-decky-DialogBodyText" }, p.strDescription) : null,
				h("div", { className: "fy-decky-DialogBody" }, p.children),
				h(
					"div",
					{ className: "fy-decky-DialogFooter" },
					h(
						"button",
						{
							type: "button",
							"data-framey-dialog-action": "ok",
							disabled: p.bOKDisabled,
							onClick: () => action(p.onOK),
						},
						p.strOKButtonText ?? "OK",
					),
					!p.bAlertDialog && p.onMiddleButton
						? h(
								"button",
								{
									type: "button",
									"data-framey-dialog-action": "middle",
									disabled: p.bMiddleDisabled,
									onClick: () => action(p.onMiddleButton),
								},
								p.strMiddleButtonText ?? "More",
							)
						: null,
					!p.bAlertDialog
						? h(
								"button",
								{
									type: "button",
									"data-framey-dialog-action": "cancel",
									disabled: p.bCancelDisabled,
									onClick: () => (record ? cancelDialog(record, "cancel", p) : action(p.onCancel)),
								},
								p.strCancelButtonText ?? "Cancel",
							)
						: null,
				),
			);
		};
		const dfl = {
			definePlugin: factory => {
				if (typeof factory !== "function") throw new TypeError("Plugin factory required");
				return factory;
			},
			PanelSection: p =>
				h(
					"section",
					{ className: "fy-decky-section", style: p.style },
					p.title ? h("h2", null, p.title) : null,
					p.children,
				),
			PanelSectionRow: box("div", "fy-decky-row"),
			Toggle: p =>
				h("input", {
					type: "checkbox",
					checked: !!p.value,
					disabled: p.disabled,
					ref: p.navRef,
					"aria-label": p["aria-label"],
					className: p.className,
					style: p.style,
					onChange: event => {
						if (!p.disabled) p.onChange?.(event.target.checked);
					},
				}),
			ToggleField: p =>
				label(
					p,
					h("input", {
						type: "checkbox",
						checked: !!p.checked,
						disabled: p.disabled,
						onChange: event => p.onChange?.(event.target.checked),
					}),
				),
			Dropdown: dropdown,
			DropdownItem: dropdown,
			Button: button,
			ButtonItem: button,
			DialogButton: button,
			DialogButtonPrimary: button,
			DialogButtonSecondary: button,
			...(menuAdapter
				? { Menu: menuAdapter.Menu, MenuItem: menuAdapter.MenuItem, showContextMenu: openContextMenu }
				: {}),
			...(sidebarAdapter ? { SidebarNavigation: sidebarAdapter.SidebarNavigation } : {}),
			showModal,
			ModalRoot,
			ConfirmModal,
			DialogHeader,
			DialogSubHeader: dialogContainer("DialogSubHeader", 3),
			DialogBody: dialogContainer("DialogBody"),
			DialogBodyText: dialogContainer("DialogBodyText"),
			DialogFooter: dialogContainer("DialogFooter"),
			DialogLabel: dialogContainer("DialogLabel"),
			DialogControlsSection: dialogContainer("DialogControlsSection"),
			DialogControlsSectionHeader: dialogContainer("DialogControlsSectionHeader", 3),
			Field: p => label(p, p.children),
			Focusable: box("div", "fy-decky-focusable"),
			Marquee: box("div", "fy-decky-marquee"),
			ScrollPanel: box("div", "fy-decky-scroll"),
			ScrollPanelGroup: box("div", "fy-decky-scroll"),
			TextField: p =>
				label(
					p,
					h(p.multiline ? "textarea" : "input", {
						type: p.type || "text",
						value: p.value ?? "",
						disabled: p.disabled,
						placeholder: p.placeholder,
						onChange: p.onChange,
						onKeyDown: p.onKeyDown,
					}),
				),
			SliderField: p =>
				label(
					p,
					h("input", {
						type: "range",
						min: p.min,
						max: p.max,
						step: p.step,
						value: p.value,
						disabled: p.disabled,
						onChange: event => p.onChange?.(Number(event.target.value)),
					}),
				),
			// Native timer conversion applies unchanged. Owned timer cleanup deliberately
			// leaves outstanding/post-disposal waits pending, without retry-triggering rejections.
			sleep: milliseconds => new realm.Promise(fulfil => realm.setTimeout(fulfil, milliseconds)),
			joinClassNames: (...classes) => classes.join(" "),
			Spinner: p =>
				h(
					"svg",
					{
						...p,
						viewBox: p.viewBox || "0 0 24 24",
						width: p.width ?? 24,
						height: p.height ?? 24,
						role: p.role || "status",
						"aria-label": p["aria-label"] || "Loading",
						className: ["fy-decky-spinner", p.className].filter(Boolean).join(" "),
					},
					h("circle", {
						cx: 12,
						cy: 12,
						r: 9,
						fill: "none",
						stroke: "currentColor",
						strokeWidth: 3,
						strokeLinecap: "round",
						strokeDasharray: "42 18",
					}),
				),
			SteamSpinner: () => h("span", { role: "status", "aria-label": "Loading" }, "Loading…"),
			staticClasses: classes,
			appDetailsClasses: new Proxy(
				{},
				{
					get: (_, key) => {
						missing("Steam.appDetailsClasses." + String(key));
						return undefined;
					},
				},
			),
			appDetailsHeaderClasses: new Proxy(
				{},
				{
					get: (_, key) => {
						missing("Steam.appDetailsHeaderClasses." + String(key));
						return undefined;
					},
				},
			),
			Navigation: provider("Navigation", options.router ? options.router.Navigation : localNavigation),
			Router: new Proxy(options.router || localNavigation, {
				get(target, key) {
					if (key in target) return target[key];
					missing("Router." + String(key));
					return undefined;
				},
			}),
			findModuleExport: predicate => {
				if (options.modules?.findModuleExport) return options.modules.findModuleExport(predicate);
				missing("modules.findModuleExport");
				return undefined;
			},
			findModuleChild: predicate => {
				if (options.modules?.findModuleChild) return options.modules.findModuleChild(predicate);
				missing("modules.findModuleChild");
				return undefined;
			},
			useLocation: () => {
				if (options.router?.useLocation) return options.router.useLocation();
				if (nativeRoutesEnabled) return R.useContext(routeContext).location;
				return unsupported("router.useLocation");
			},
			useParams: () => {
				if (options.router?.useParams) return options.router.useParams();
				if (nativeRoutesEnabled) return R.useContext(routeContext).params;
				missing("router.useParams");
				return {};
			},
		};
		return provider("DFL", dfl, false, [menuAdapter?.Menu, menuAdapter?.MenuItem, sidebarAdapter?.SidebarNavigation]);
	}
	function instrument() {
		const timeoutIntervalTimers = new Map();
		for (const [start, stop, repeat] of [
			["setTimeout", "clearTimeout", false],
			["setInterval", "clearInterval", true],
			["requestAnimationFrame", "cancelAnimationFrame", false],
		]) {
			const schedule = realm[start].bind(realm),
				cancel = realm[stop].bind(realm),
				timers = start === "requestAnimationFrame" ? new Map() : timeoutIntervalTimers;
			realm[start] = (fn, ...args) => {
				if (disposed) return 0;
				if (typeof fn !== "function") throw new TypeError("Timer callback must be a function");
				const id = schedule(
					function () {
						"use strict";
						if (!repeat) {
							resources.delete(timers.get(id));
							timers.delete(id);
						}
						if (!disposed) safely(() => Reflect.apply(fn, this, arguments));
					},
					...args,
				);
				const cleanup = () => cancel(id);
				timers.set(id, cleanup);
				resources.add(cleanup);
				return id;
			};
			realm[stop] = id => {
				resources.delete(timers.get(id));
				timers.delete(id);
				cancel(id);
			};
		}
		for (const name of ["MutationObserver", "ResizeObserver", "IntersectionObserver"]) {
			const Base = realm[name];
			if (Base)
				realm[name] = class extends Base {
					constructor(callback) {
						super((...args) => {
							if (!disposed) safely(() => callback(...args));
						});
						own(() => this.disconnect());
					}
				};
		}
		const target = realm.EventTarget.prototype,
			add = target.addEventListener,
			remove = target.removeEventListener;
		const registrations = new WeakMap();
		const capture = init => (typeof init === "object" && init !== null ? !!init.capture : !!init);
		const release = (owner, entry) => {
			resources.delete(entry.cleanup);
			const entries = registrations.get(owner);
			if (!entries) return;
			const index = entries.indexOf(entry);
			if (index !== -1) entries.splice(index, 1);
			if (!entries.length) registrations.delete(owner);
		};
		target.addEventListener = function (name, listener, init) {
			add.call(this, name, listener, init);
			if (!listener) return;
			const owner = this,
				event = String(name),
				useCapture = capture(init);
			let entries = registrations.get(owner);
			if (!entries) registrations.set(owner, (entries = []));
			if (entries.some(entry => entry.event === event && entry.listener === listener && entry.capture === useCapture))
				return;
			const entry = { event, listener, capture: useCapture, cleanup: null };
			entry.cleanup = () => {
				release(owner, entry);
				remove.call(owner, event, listener, useCapture);
			};
			entries.push(entry);
			resources.add(entry.cleanup);
		};
		target.removeEventListener = function (name, listener, init) {
			remove.call(this, name, listener, init);
			const event = String(name),
				useCapture = capture(init);
			const entry = registrations
				.get(this)
				?.find(entry => entry.event === event && entry.listener === listener && entry.capture === useCapture);
			if (entry) release(this, entry);
		};
		const createURL = realm.URL.createObjectURL.bind(realm.URL),
			revokeURL = realm.URL.revokeObjectURL.bind(realm.URL);
		realm.URL.createObjectURL = blob => {
			const url = createURL(blob);
			own(() => revokeURL(url));
			return url;
		};
		realm.fetch = fetchNoCors;
		for (const name of ["WebSocket", "XMLHttpRequest", "EventSource", "Worker", "SharedWorker"])
			realm[name] = function () {
				return unsupported("network." + name);
			};
		realm.open = () => unsupported("window.open");
		realm.addEventListener("error", event => recordError(event.error || event.message));
		realm.addEventListener("unhandledrejection", event => recordError(event.reason));
	}
	function cleanupHost(nextPanel) {
		if (disposed) return;
		disposed = true;
		for (const reject of graphWaits) reject(graphDisposedError());
		graphWaits.clear();
		if (typeof modulesAdapter?.dispose === "function") safely(() => modulesAdapter.dispose());
		if (plugin && !dismounted) {
			dismounted = true;
			safely(() => plugin.onDismount?.());
		}
		if (typeof menuAdapter?.dispose === "function") safely(() => menuAdapter.dispose());
		menuOwner = null;
		for (const dialog of [...dialogs].reverse()) finishDialog(dialog);
		safely(closeNativeRoutes);
		routes.clear();
		safely(() => root?.unmount());
		safely(() => view?.dispose());
		for (const entries of subscriptions.values()) for (const off of entries.values()) safely(off);
		subscriptions.clear();
		for (const item of pending) item.reject(new Error("Framey Decky host disposed"));
		pending.clear();
		for (const cleanup of resources) safely(cleanup);
		resources.clear();
		navigationReady = false;
		if (typeof navigationAdapter?.dispose === "function") safely(() => navigationAdapter.dispose());
		safely(() => options.assets?.dispose());
		frame?.remove();
		if (typeof unregister === "function") safely(unregister);
		panel = nextPanel;
		publish();
	}
	function dispose() {
		cleanupHost("disposed");
	}
	function failRuntime(error) {
		if (disposed) return;
		if (errors.length < 50) errors.push(errorMessage(error));
		cleanupHost("failed");
	}
	async function load(source, format, container) {
		active();
		if (loadStarted) throw new Error("A host can load one plugin; dispose it before creating another");
		if (typeof source !== "string" || !["esm", "iife", "modern", "legacy"].includes(format))
			throw new TypeError("Expected source text and esm or iife format");
		if (!container?.ownerDocument) throw new TypeError("A panel container is required");
		loadStarted = true;
		panel = "loading";
		publish();
		let graphLease = null,
			graphRoot = "";
		try {
			if (options.moduleGraph === true) {
				if (format !== "esm" && format !== "modern") unsupported("modules.graph.legacy");
				modulesAdapter = options.createModules({
					api,
					onLost: failRuntime,
					onError: error => {
						if (!disposed) recordError(error);
					},
				});
				if (
					!modulesAdapter ||
					["acquire", "import", "dispose", "status"].some(key => typeof modulesAdapter[key] !== "function")
				)
					throw new TypeError("Owned module graph adapter methods are required");
				graphLease = await waitForGraph(
					Promise.resolve(modulesAdapter.acquire()).then(value => {
						if (disposed) {
							if (typeof value?.token === "string" && /^[a-f0-9]{64}$/.test(value.token)) {
								try {
									Promise.resolve(api.compat?.modules?.revoke(value.token)).catch(() => {});
								} catch {}
							}
							throw graphDisposedError();
						}
						return value;
					}),
				);
				active();
				const checked = validGraphLease(graphLease);
				if (!checked)
					throw Object.assign(new Error("Invalid owned module graph lease"), { code: "module_graph_invalid" });
				graphRoot = checked.root;
			}
			frame = container.ownerDocument.createElement("iframe");
			frame.title = options.name + " compatibility panel";
			const ownerWindow = container.ownerDocument.defaultView;
			const sizingAncestors = [];
			for (let node = container.parentElement; node && sizingAncestors.length < 32; node = node.parentElement)
				sizingAncestors.push(node);
			const availableHeight = () => {
				const bounds = container.getBoundingClientRect(),
					css = ownerWindow.getComputedStyle(container);
				const padding = (parseFloat(css.paddingTop) || 0) + (parseFloat(css.paddingBottom) || 0);
				let height =
					Number(css.flexGrow) > 0
						? ownerWindow.innerHeight - bounds.top
						: container.clientHeight || ownerWindow.innerHeight - bounds.top;
				height = Math.min(height, ownerWindow.innerHeight - bounds.top);
				for (const ancestor of sizingAncestors) {
					const style = ownerWindow.getComputedStyle(ancestor);
					if (!/(auto|scroll|hidden|clip)/.test(style.overflowY)) continue;
					const rect = ancestor.getBoundingClientRect();
					height = Math.min(
						height,
						rect.top + ancestor.clientTop + ancestor.clientHeight - bounds.top - (parseFloat(style.paddingBottom) || 0),
					);
				}
				return Math.max(1, height - padding);
			};
			frame.style.cssText =
				"display:block;width:100%;height:" + availableHeight() + "px;min-height:0;border:0;color-scheme:dark";
			container.append(frame);
			realm = frame.contentWindow;
			const doc = realm.document;
			doc.open();
			doc.write(
				"<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-eval' blob:" +
					(graphRoot ? " " + graphRoot : "") +
					"; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'\"><style>html{height:100%;overflow:auto}body{min-height:100%;margin:0;background:#0e141b;color:#e6edf4;font:14px system-ui}button,input,select,textarea{font:inherit;accent-color:#66c0f4}button,select,textarea,input:not([type=checkbox]){padding:8px;border:1px solid #526477;border-radius:4px;background:#1b2838;color:inherit}button{cursor:pointer}.fy-decky-section{padding:12px}.fy-decky-section h2{font-size:16px}.fy-decky-row{margin:8px 0}.fy-decky-field{display:flex;align-items:center;flex-wrap:wrap;justify-content:space-between;gap:10px;margin:10px 0}.fy-decky-field small{flex-basis:100%;color:#b5c0cb}.fy-decky-scroll{overflow:auto}.fy-decky-ModalRoot{display:flex;flex-direction:column;flex:1;min-height:0;max-height:100%}.fy-decky-DialogHeader{font-size:21px;font-weight:bold;margin:0 0 12px;flex:none}.fy-decky-DialogSubHeader{font-size:18px;font-weight:bold;margin:8px 0}.fy-decky-DialogBodyText{white-space:pre-wrap;margin:8px 0;flex:none}.fy-decky-DialogBody{margin:12px 0;min-height:0;overflow:auto;flex:1}.fy-decky-DialogFooter{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px;flex:none}.fy-decky-DialogControlsSection{display:flex;flex-direction:column;gap:8px}.fy-decky-DialogControlsSectionHeader{font-weight:bold}dialog[data-framey-plugin-dialog]::backdrop{background:rgba(0,0,0,.65)}.fy-decky-spinner{animation:fy-decky-spin .9s linear infinite;vertical-align:middle;flex-shrink:0}@keyframes fy-decky-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.fy-decky-spinner{animation:none}}</style></head><body><main></main></body></html>",
			);
			doc.close();
			instrument();
			if (options.createNavigation) {
				navigationAdapter = options.createNavigation(api, {
					onUnsupported: capability => {
						if (!disposed) missing(capability);
					},
					onDiagnostic: diagnostic => {
						if (
							disposed ||
							!["window.open", "Navigation.NavigateToExternalWeb"].includes(diagnostic?.capability) ||
							!["bad_url", "disposed", "unavailable", "unsupported_shape", "request_failed"].includes(diagnostic?.code)
						)
							return;
						navigationDiagnostic = { capability: diagnostic.capability, code: diagnostic.code };
						recordError(diagnostic.capability + ": " + diagnostic.code);
						if (navigationReady && panel === "mounted" && typeof api.notify === "function") {
							try {
								Promise.resolve(
									api.notify({
										title: "Link unavailable",
										body: "Link could not be opened. This action is unavailable here.",
									}),
								).catch(() => {});
							} catch {}
						}
					},
				});
				if (
					!navigationAdapter ||
					typeof navigationAdapter.install !== "function" ||
					typeof navigationAdapter.dispose !== "function" ||
					typeof navigationAdapter.navigateToExternalWeb !== "function"
				)
					throw new TypeError("Native navigation adapter methods are required");
				navigationAdapter.install(realm);
				navigationReady = true;
			}
			options.assets?.install(realm);
			view = options.createViewRuntime(doc, { onError: failRuntime });
			active();
			if (options.createMenus) {
				menuAdapter = options.createMenus({
					document: doc,
					React: view.React,
					ReactDOM: view.ReactDOM,
					onUnsupported: capability => {
						menuUnsupportedEpoch++;
						if (!disposed) missing(capability);
					},
					onError: error => {
						if (!disposed) recordError(error);
					},
					onChange: () => {
						if (!disposed) publish();
					},
				});
				if (
					!menuAdapter ||
					["Menu", "MenuItem", "showContextMenu", "closeAll", "status", "dispose"].some(
						key => typeof menuAdapter[key] !== "function",
					)
				)
					throw new TypeError("Owned menu adapter methods are required");
			}
			if (options.createSidebar) {
				sidebarAdapter = options.createSidebar({
					React: view.React,
					onUnsupported: capability => {
						if (!disposed) missing(capability);
					},
					onError: error => {
						if (!disposed) recordError(error);
					},
				});
				if (
					!sidebarAdapter ||
					typeof sidebarAdapter.SidebarNavigation !== "function" ||
					typeof sidebarAdapter.status !== "function"
				)
					throw new TypeError("Owned sidebar component and status are required");
			}
			dialogContext = view.React.createContext(null);
			if (nativeRoutesEnabled)
				routeContext = view.React.createContext({ params: {}, location: { pathname: "", search: "", hash: "" } });
			// Translate the browser/document locale data contract; no Steam hook is claimed.
			realm.LocalizationManager = {
				m_rgLocalesToUse: [container.ownerDocument.documentElement.lang || realm.navigator.language || "en"],
			};
			realm.SP_REACT = new Proxy(view.React, {
				get(target, key, receiver) {
					if (typeof key === "string" && !(key in target)) missing("SP_REACT." + key);
					return Reflect.get(target, key, receiver);
				},
			});
			realm.SP_REACTDOM = view.ReactDOM;
			realm.SP_JSX = view.JSX;
			realm.DFL = controls();
			realm.SteamClient = options.steamClient || absent("SteamClient");
			realm.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit = {
				connect(version, name) {
					active();
					if (name !== options.name) throw new Error("Plugin identity mismatch");
					if (!Number.isInteger(version) || version < 1 || version > modern._version)
						throw new Error("Unsupported Decky API version");
					return { ...modern, _version: version };
				},
			};
			let factory;
			if (format === "esm" || format === "modern") {
				if (graphLease) factory = (await waitForGraph(modulesAdapter.import(realm, graphLease))).default;
				else {
					const url = realm.URL.createObjectURL(new realm.Blob([source], { type: "text/javascript" }));
					const module = await realm.Function("url", "return import(url)")(url);
					factory = module.default;
				}
			} else factory = realm.eval(source);
			active();
			if (typeof factory !== "function") throw new TypeError("Original bundle must export a plugin factory");
			const produced = Promise.resolve(factory(legacy));
			if (graphLease) {
				plugin = await waitForGraph(
					produced.then(result => {
						if (disposed && result && !dismounted) {
							dismounted = true;
							try {
								Promise.resolve(result.onDismount?.()).catch(() => {});
							} catch {}
						}
						return result;
					}),
				);
			} else plugin = await produced;
			if (disposed && plugin && !dismounted) {
				dismounted = true;
				safely(() => plugin.onDismount?.());
			}
			active();
			if (!plugin || typeof plugin !== "object" || !("content" in plugin))
				throw new TypeError("Plugin factory did not return panel content");
			root = view.ReactDOM.createRoot(doc.querySelector("main"));
			root.render(plugin.content);
			active();
			const measureLayout = () => {
				if (disposed) return;
				const scrolling = doc.scrollingElement || doc.documentElement;
				const viewportHeight = doc.documentElement.clientHeight,
					viewportWidth = doc.documentElement.clientWidth;
				const contentHeight = scrolling.scrollHeight,
					contentWidth = scrolling.scrollWidth;
				const overflow = contentHeight > viewportHeight + 1 || contentWidth > viewportWidth + 1;
				const next = {
					overflow,
					mode: overflow ? "scroll" : "fit",
					viewportHeight,
					contentHeight,
					viewportWidth,
					contentWidth,
				};
				if (JSON.stringify(next) === JSON.stringify(layout)) return;
				layout = next;
				if (overflow) unavailable.add("layout.overflow");
				else unavailable.delete("layout.overflow");
				publish();
			};
			const contentObserver = new realm.MutationObserver(measureLayout);
			contentObserver.observe(doc.body, { subtree: true, childList: true, characterData: true, attributes: true });
			if (ownerWindow.ResizeObserver) {
				const resize = new ownerWindow.ResizeObserver(() => {
					if (disposed) return;
					const height = availableHeight() + "px";
					if (frame.style.height !== height) frame.style.height = height;
					measureLayout();
				});
				resize.observe(container);
				own(() => resize.disconnect());
			}
			if (realm.ResizeObserver) {
				const contentResize = new realm.ResizeObserver(measureLayout);
				contentResize.observe(doc.body);
			}
			measureLayout();
			panel = "mounted";
			publish();
			return status();
		} catch (error) {
			if (disposed && options.moduleGraph === true) throw graphFailure(error);
			if (!(disposed && panel === "failed") && errors.length < 50 && !errors.includes(errorMessage(error)))
				errors.push(errorMessage(error));
			if (!disposed) cleanupHost("failed");
			else publish();
			throw options.moduleGraph === true ? graphFailure(error) : error;
		}
	}
	unregister = api.cleanup(dispose);
	return Object.freeze({ load, status, dispose });
};
if (typeof module === "object" && module && module.exports) module.exports = { createFrameyDeckyHost };
