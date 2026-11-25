/* Optional ABI 1 provider. Loaded only from the verified runtime package. */
const createFrameyDeckyProvider = function () {
	"use strict";
	const attach = context => {
		if (
			!context ||
			!context.api ||
			["alive", "current", "request", "cleanupRequest", "lost"].some(k => typeof context[k] !== "function")
		)
			throw new TypeError("Plugin-owned runtime context required");
		const api = context.api,
			key = api.id;
		const NativePromise = Promise,
			scheduleTimeout = setTimeout,
			cancelTimeout = clearTimeout;
		let dead = false,
			host = null,
			mountEpoch = 0;
		const alive = () => !dead && context.alive() && context.current();
		const disposed = () => Object.assign(new Error("Plugin disposed"), { code: "disposed" });
		const rpc = (method, arg) => context.request(method, arg);
		const invokeSafely = (_code, fn) => {
			try {
				return fn();
			} catch {}
		};
		const steamShape = (value, keys) =>
			value !== null &&
			typeof value === "object" &&
			!Array.isArray(value) &&
			Object.prototype.toString.call(value) === "[object Object]" &&
			Object.keys(value).length === keys.length &&
			keys.every(key => Object.prototype.hasOwnProperty.call(value, key));
		const CODE_TOKEN = /^[a-f0-9]{64}$/;
		const CODE_FAILURES = new Set([
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
		]);
		const codeError = code => Object.assign(new Error(code), { code });
		const codeFailure = error => codeError(error && CODE_FAILURES.has(error.code) ? error.code : "code_unavailable");
		const codeSnapshot = lease => Object.freeze({ root: lease.root, entry: lease.entry, token: lease.token, ttl: 600 });
		const validCodeLease = lease => {
			if (
				!steamShape(lease, ["root", "entry", "token", "ttl"]) ||
				typeof lease.token !== "string" ||
				!CODE_TOKEN.test(lease.token) ||
				lease.ttl !== 600 ||
				typeof lease.root !== "string"
			)
				return false;
			const root = lease.root.match(new RegExp("^http://127\\.0\\.0\\.1:([1-9][0-9]{0,4})/g/" + lease.token + "/$"));
			return !!root && Number(root[1]) <= 65535 && lease.entry === lease.root + "dist/index.js";
		};
		const codeLeases = new Map();
		let codeCurrent = null,
			codePending = null,
			codeTimer = 0,
			codeGeneration = 0;
		const codeRetire = token =>
			invokeSafely("api.compat_failed", () => context.cleanupRequest("decky_code_revoke", { token }));
		const codeDrop = (entry, code, notify) => {
			if (codeLeases.get(entry.lease.token) !== entry) return;
			const current = codeCurrent === entry;
			codeRetire(entry.lease.token);
			codeLeases.delete(entry.lease.token);
			if (codeCurrent === entry) codeCurrent = null;
			if (!codeLeases.size && codeTimer) {
				cancelTimeout(codeTimer);
				codeTimer = 0;
			}
			if (notify && current && alive()) context.lost(code);
		};
		const codeOwned = token => {
			if (typeof token !== "string" || !CODE_TOKEN.test(token)) throw codeError("invalid_arguments");
			const entry = codeLeases.get(token);
			if (!entry) throw codeError("not_owned");
			return entry;
		};
		const codeTouch = token => {
			if (!alive()) return NativePromise.reject(disposed());
			let entry;
			try {
				entry = codeOwned(token);
				if (entry.revoking) throw codeError("not_owned");
				if (Date.now() >= entry.expires) {
					codeDrop(entry, "code_expired", true);
					throw codeError("code_expired");
				}
				if (entry.touching) return entry.touching;
				const deadline = entry.expires;
				entry.touching = rpc("decky_code_touch", { pid: key, token })
					.then(reply => {
						if (!alive()) throw disposed();
						if (codeLeases.get(token) !== entry || entry.revoking) throw codeError("not_owned");
						if (Date.now() >= deadline) throw codeError("code_expired");
						if (!steamShape(reply, ["renewed", "ttl"]) || reply.renewed !== true || reply.ttl !== 600)
							throw codeError("bad_response");
						entry.expires = Date.now() + 600000;
						return Object.freeze({ renewed: true, ttl: 600 });
					})
					.catch(error => {
						if (!alive()) throw disposed();
						const failure = codeFailure(error);
						if (!entry.revoking) codeDrop(entry, failure.code, true);
						throw failure;
					})
					.finally(() => {
						entry.touching = null;
					});
				return entry.touching;
			} catch (error) {
				return NativePromise.reject(codeFailure(error));
			}
		};
		const codeArm = () => {
			if (codeTimer || !codeLeases.size || !alive()) return;
			codeTimer = scheduleTimeout(() => {
				codeTimer = 0;
				if (!alive()) return;
				for (const entry of codeLeases.values()) {
					if (entry.revoking) continue;
					if (Date.now() >= entry.expires) codeDrop(entry, "code_expired", true);
					else codeTouch(entry.lease.token).catch(() => {});
				}
				codeArm();
			}, 240000);
		};
		const compat = Object.freeze({
			modules: Object.freeze({
				issue(...args) {
					if (!alive()) return NativePromise.reject(disposed());
					const options = args[0];
					if (args.length > 1 || (options !== undefined && (!steamShape(options, ["fresh"]) || options.fresh !== true)))
						return NativePromise.reject(codeError("invalid_arguments"));
					let generation = codeGeneration;
					if (options) {
						generation = ++codeGeneration;
						codePending = null;
						if (codeCurrent) codeDrop(codeCurrent, "stale_caller", true);
					}
					if (codeCurrent && Date.now() >= codeCurrent.expires) codeDrop(codeCurrent, "code_expired", true);
					if (generation !== codeGeneration) return NativePromise.reject(codeError("stale_caller"));
					if (codeCurrent && !codeCurrent.revoking) return NativePromise.resolve(codeSnapshot(codeCurrent.lease));
					if (!codePending) {
						const pending = rpc("decky_code_issue", { pid: key })
							.then(reply => {
								const token =
									reply && typeof reply.token === "string" && CODE_TOKEN.test(reply.token) ? reply.token : null;
								if (!alive()) {
									if (token) codeRetire(token);
									throw disposed();
								}
								if (generation !== codeGeneration) {
									if (token) codeRetire(token);
									throw codeError("stale_caller");
								}
								if (!validCodeLease(reply)) {
									if (token) codeRetire(token);
									throw codeError("bad_response");
								}
								const entry = {
									lease: codeSnapshot(reply),
									expires: Date.now() + 600000,
									touching: null,
									revoking: null,
								};
								codeLeases.set(token, entry);
								codeCurrent = entry;
								codeArm();
								return entry.lease;
							})
							.catch(error => {
								if (!alive()) throw disposed();
								if (generation !== codeGeneration) throw codeError("stale_caller");
								throw codeFailure(error);
							})
							.finally(() => {
								if (codePending === pending) codePending = null;
							});
						codePending = pending;
					}
					return codePending.then(codeSnapshot);
				},
				touch(token, ...extra) {
					if (!alive()) return NativePromise.reject(disposed());
					return extra.length ? NativePromise.reject(codeError("invalid_arguments")) : codeTouch(token);
				},
				revoke(token, ...extra) {
					if (!alive()) return NativePromise.reject(disposed());
					let entry;
					try {
						if (extra.length) throw codeError("invalid_arguments");
						entry = codeOwned(token);
						if (entry.revoking) return entry.revoking;
						if (Date.now() >= entry.expires) {
							codeDrop(entry, "code_expired", true);
							return NativePromise.resolve(Object.freeze({ revoked: true }));
						}
						entry.revoking = rpc("decky_code_revoke", { pid: key, token })
							.then(reply => {
								if (!alive()) throw disposed();
								if (!steamShape(reply, ["revoked"]) || reply.revoked !== true) throw codeError("bad_response");
								if (codeLeases.get(token) === entry) {
									codeLeases.delete(token);
									if (codeCurrent === entry) codeCurrent = null;
									if (!codeLeases.size && codeTimer) {
										cancelTimeout(codeTimer);
										codeTimer = 0;
									}
								}
								return Object.freeze({ revoked: true });
							})
							.catch(error => {
								if (!alive()) throw disposed();
								const failure = codeFailure(error);
								codeDrop(entry, failure.code, true);
								throw failure;
							});
						return entry.revoking;
					} catch (error) {
						return NativePromise.reject(codeFailure(error));
					}
				},
			}),
		});

		const dispose = () => {
			if (dead) return;
			dead = true;
			++mountEpoch;
			if (codeTimer) {
				cancelTimeout(codeTimer);
				codeTimer = 0;
			}
			for (const entry of [...codeLeases.values()]) codeDrop(entry, "disposed", false);
			const old = host;
			host = null;
			old?.dispose();
		};
		const mount = async (container, descriptor) => {
			if (!alive()) throw disposed();
			if (
				!descriptor ||
				descriptor.v !== 1 ||
				descriptor.apiVersion !== 2 ||
				typeof descriptor.name !== "string" ||
				!descriptor.name ||
				typeof descriptor.source !== "string" ||
				!["esm", "iife", "modern", "legacy"].includes(descriptor.format) ||
				(descriptor.rpcResultVersion !== undefined && descriptor.rpcResultVersion !== 1) ||
				!container?.ownerDocument
			)
				throw new TypeError("Unsupported compatibility mount descriptor");
			const epoch = ++mountEpoch;
			host?.dispose();
			host = null;
			const document = container.ownerDocument;
			const notice = document.createElement("div"),
				surface = document.createElement("div");
			container.style.cssText += ";display:flex;flex-direction:column;min-height:0;gap:8px";
			notice.style.cssText = "font:14px sans-serif;color:#ff7fdc;flex:none";
			surface.style.cssText = "flex:1;min-height:0";
			container.replaceChildren(notice, surface);
			const owned = createFrameyDeckyHost(api, {
				name: descriptor.name,
				apiVersion: descriptor.apiVersion,
				nativeRoutes: true,
				createViewRuntime: createFrameyViewRuntime,
				createNavigation: createFrameyDeckyNavigation,
				createMenus: createFrameyDeckyMenus,
				createSidebar: createFrameyDeckySidebar,
				...(descriptor.rpcResultVersion === 1 ? { rpcResultVersion: 1 } : {}),
				...(descriptor.moduleGraph === true || descriptor.moduleGraph?.required === true
					? { moduleGraph: true, createModules: createFrameyDeckyModules }
					: {}),
				...(descriptor.assets && Object.keys(descriptor.assets).length
					? { assets: createFrameyDeckyAssets(descriptor.name, descriptor.assets) }
					: {}),
				onStatus(state) {
					if (!alive() || epoch !== mountEpoch) return;
					const unavailable = state.unsupported.some(name => name !== "layout.overflow"),
						overflow = state.unsupported.includes("layout.overflow");
					notice.textContent =
						"Experimental compatibility · " +
						(state.panel === "failed"
							? "Panel could not be loaded"
							: (unavailable ? "Some plugin features are unavailable. " : "") +
								(overflow ? "Scroll to see all controls. " : "") +
								"Steam page integration has not been verified");
				},
			});
			host = owned;
			try {
				await owned.load(descriptor.source, descriptor.format, surface);
				if (!alive() || epoch !== mountEpoch) {
					owned.dispose();
					throw disposed();
				}
				return Object.freeze({
					status: () => owned.status(),
					dispose() {
						owned.dispose();
						if (host === owned) host = null;
					},
				});
			} catch (error) {
				owned.dispose();
				if (host === owned) host = null;
				throw error;
			}
		};
		return Object.freeze({ modules: compat.modules, mount, dispose });
	};
	return Object.freeze({
		abi: 1,
		attach,
		store(context) {
			if (typeof createFrameyDeckyStore !== "function") throw new Error("Decky Store unavailable");
			return createFrameyDeckyStore(context);
		},
	});
};
if (typeof module !== "undefined" && module.exports) module.exports = { createFrameyDeckyProvider };
