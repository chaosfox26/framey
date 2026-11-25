/* Native module graphs remain original HTTP-served bytes. This adapter owns a
 * lease and cancellation of its waits; it cannot interrupt executing native JS. */
const createFrameyDeckyModules = function (options) {
	"use strict";
	const { api, onLost, onError } = options || {};
	const sdk = api?.compat?.modules;
	if (
		!sdk ||
		["issue", "touch", "revoke"].some(key => typeof sdk[key] !== "function") ||
		typeof api?.events?.on !== "function"
	)
		throw new TypeError("Owned module graph API is required");
	const knownCodes = new Set([
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
	const waits = new Set(),
		released = new Set(),
		unavailable = new Set();
	let state = "idle",
		disposed = false,
		lease = null,
		acquisition = null,
		importing = null,
		ownedRealm = null,
		off = null,
		errorCode = null;
	const error = code => Object.assign(new Error("Module graph unavailable: " + code), { code });
	const notify = (callback, value) => {
		try {
			callback?.(value);
		} catch {}
	};
	const release = value => {
		const token = value?.token;
		if (typeof token !== "string" || !/^[a-f0-9]{64}$/.test(token) || released.has(token)) return;
		released.add(token);
		try {
			Promise.resolve(sdk.revoke(token)).catch(() => {});
		} catch {}
	};
	const unsubscribe = () => {
		const remove = off;
		off = null;
		try {
			remove?.();
		} catch {}
	};
	const cancel = reason => {
		for (const reject of waits) reject(reason);
		waits.clear();
	};
	const closedError = () => error(disposed ? "module_graph_disposed" : errorCode || "module_graph_unavailable");
	const active = () => {
		if (disposed || state === "lost" || state === "failed") throw closedError();
	};
	const wait = promise =>
		new Promise((resolve, reject) => {
			if (disposed || state === "lost" || state === "failed") {
				reject(closedError());
				Promise.resolve(promise).catch(() => {});
				return;
			}
			waits.add(reject);
			Promise.resolve(promise).then(
				value => {
					if (waits.delete(reject)) resolve(value);
				},
				reason => {
					if (waits.delete(reject)) reject(reason);
				},
			);
		});
	const fail = (code, capability) => {
		const failure = error(code);
		if (!disposed && state !== "lost" && state !== "failed") {
			errorCode = code;
			state = "failed";
			if (capability) unavailable.add(capability);
			unsubscribe();
			release(lease);
			cancel(failure);
			notify(onError, failure);
		}
		return failure;
	};
	const validate = value => {
		const fields = value && Object.getOwnPropertyDescriptors(value);
		if (!fields || ["root", "entry", "token", "ttl"].some(key => !fields[key] || !("value" in fields[key])))
			throw error("module_graph_invalid");
		value = Object.fromEntries(["root", "entry", "token", "ttl"].map(key => [key, fields[key].value]));
		const match =
			typeof value?.root === "string" &&
			/^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/g\/([a-f0-9]{64})\/$/.exec(value.root);
		if (
			!match ||
			Number(match[1]) > 65535 ||
			value.token !== match[2] ||
			value.entry !== value.root + "dist/index.js" ||
			value.ttl !== 600
		)
			throw error("module_graph_invalid");
		return Object.freeze({ root: value.root, entry: value.entry, token: value.token, ttl: 600 });
	};
	off = api.events.on("compat.modules.lost", payload => {
		// A fresh SDK incarnation can retire its predecessor synchronously. This
		// adapter must not adopt that predecessor's event before knowing its lease.
		if (!lease || disposed || state === "lost" || state === "failed") return;
		errorCode = knownCodes.has(payload?.code) ? payload.code : "code_unavailable";
		state = "lost";
		const failure = error(errorCode);
		unsubscribe();
		release(lease);
		cancel(failure);
		notify(onLost, failure);
	});
	if (typeof off !== "function") throw new TypeError("Module graph event cleanup is required");
	const acquire = () => {
		try {
			active();
		} catch (reason) {
			return Promise.reject(reason);
		}
		if (acquisition) return acquisition;
		state = "acquiring";
		let issued;
		try {
			issued = sdk.issue({ fresh: true });
		} catch (reason) {
			issued = Promise.reject(reason);
		}
		const completion = Promise.resolve(issued).then(
			value => {
				if (disposed || state === "lost" || state === "failed") {
					release(value);
					throw closedError();
				}
				try {
					lease = validate(value);
				} catch {
					release(value);
					throw fail("module_graph_invalid");
				}
				state = "ready";
				return lease;
			},
			reason => {
				if (disposed || state === "lost" || state === "failed") throw closedError();
				throw fail(knownCodes.has(reason?.code) ? reason.code : "code_unavailable");
			},
		);
		acquisition = wait(completion);
		return acquisition;
	};
	return Object.freeze({
		acquire,
		import(realm, suppliedLease) {
			try {
				active();
				if (!lease || suppliedLease !== lease) throw error("module_graph_invalid");
				if (
					!realm ||
					realm.window !== realm ||
					typeof realm.Function !== "function" ||
					(ownedRealm && ownedRealm !== realm)
				)
					throw error("module_graph_realm");
			} catch (reason) {
				return Promise.reject(reason);
			}
			if (importing) return importing;
			ownedRealm = realm;
			state = "importing";
			let nativeImport;
			try {
				nativeImport = realm.Function("url", "return import(url)")(lease.entry);
			} catch {
				nativeImport = Promise.reject(error("module_import_failed"));
			}
			importing = wait(
				Promise.resolve(nativeImport).then(
					namespace => {
						active();
						state = "loaded";
						return namespace;
					},
					() => {
						if (disposed || state === "lost" || state === "failed") throw closedError();
						throw fail("module_import_failed", "modules.import");
					},
				),
			);
			return importing;
		},
		dispose() {
			if (disposed) return;
			disposed = true;
			state = "disposed";
			unsubscribe();
			release(lease);
			cancel(error("module_graph_disposed"));
			ownedRealm = null;
		},
		status: () => ({
			scope: "owned-http-graph",
			state,
			disposed,
			ttlSeconds: 600,
			renewEverySeconds: 240,
			renewal: "sdk-owned",
			longSleepMayExpire: true,
			reopenRequiredAfterLoss: true,
			executingNativeCodeAbortable: false,
			errorCode,
			unsupported: [...unavailable].sort(),
		}),
	});
};
if (typeof module !== "undefined" && module.exports) module.exports = { createFrameyDeckyModules };
