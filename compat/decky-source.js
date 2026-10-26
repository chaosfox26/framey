const createDeckySourceAdapter = function (api) {
	"use strict";
	if (
		!api ||
		typeof api.call !== "function" ||
		typeof api.cleanup !== "function" ||
		!api.events ||
		typeof api.events.on !== "function"
	) {
		throw new TypeError("A plugin-owned Framey API with calls, events and cleanup is required");
	}

	const subscriptions = new Map();
	const pendingCalls = new Set();
	let disposed = false;
	let unregisterCleanup = () => {};
	const createDisposedError = () => Object.assign(new Error("Source adapter disposed"), { code: "adapter_disposed" });
	const assertActive = () => {
		if (disposed) throw createDisposedError();
	};
	const validateMethodName = name => {
		if (typeof name !== "string" || !name.length) {
			throw new TypeError("Method must be a nonempty string");
		}
	};
	const validateEventName = name => {
		if (typeof name !== "string" || !/^[a-z0-9_.-]{1,40}$/.test(name)) {
			throw new TypeError("Event name must match [a-z0-9_.-]{1,40}");
		}
	};
	const failureMessages = {
		listener_sync: "listener threw; callback isolated",
		listener_async: "listener promise rejected; callback isolated",
		unsubscribe: "listener removal failed during cleanup",
		cleanup: "cleanup registration removal failed",
	};
	let diagnosticsReported = 0;
	const reportFailure = (context, category) => {
		if (diagnosticsReported >= 5) return;
		diagnosticsReported++;
		const message =
			diagnosticsReported === 5
				? "source adapter: further diagnostics suppressed"
				: "source " + context + ": " + failureMessages[category];
		try {
			if (typeof api.log === "function") {
				Promise.resolve(api.log("error", message)).catch(() => {});
			}
		} catch {}
	};

	const call = async (method, ...args) => {
		assertActive();
		validateMethodName(method);
		return new Promise((resolve, reject) => {
			const request = { reject };
			pendingCalls.add(request);
			const fail = error => {
				if (pendingCalls.delete(request)) reject(error);
			};
			try {
				Promise.resolve(api.call(method, args)).then(value => {
					if (!pendingCalls.delete(request)) return;
					try {
						if (value && value.error) {
							const error = new Error(String(value.error));
							if (typeof value.code === "string") error.code = value.code;
							reject(error);
						} else {
							resolve(value);
						}
					} catch (error) {
						reject(error);
					}
				}, fail);
			} catch (error) {
				fail(error);
			}
		});
	};

	const callable = method => {
		assertActive();
		validateMethodName(method);
		return (...args) => call(method, ...args);
	};

	const addEventListener = (name, listener) => {
		assertActive();
		validateEventName(name);
		if (typeof listener !== "function") throw new TypeError("Event listener must be a function");
		const existing = subscriptions.get(name);
		if (existing && existing.has(listener)) return listener;
		const subscription = { unsubscribe: null };
		subscription.unsubscribe = api.events.on(name, payload => {
			const current = subscriptions.get(name);
			if (disposed || !current || current.get(listener) !== subscription) return;
			try {
				const args = Array.isArray(payload) ? payload : [payload];
				Promise.resolve(listener(...args)).catch(() => reportFailure("event " + name, "listener_async"));
			} catch {
				reportFailure("event " + name, "listener_sync");
			}
		});
		if (typeof subscription.unsubscribe !== "function") {
			throw new TypeError("Framey event registration must return a remover");
		}
		if (!subscriptions.has(name)) subscriptions.set(name, new Map());
		subscriptions.get(name).set(listener, subscription);
		return listener;
	};

	const removeEventListener = (name, listener) => {
		const entries = subscriptions.get(name);
		const subscription = entries && entries.get(listener);
		if (!subscription) return;
		entries.delete(listener);
		if (!entries.size) subscriptions.delete(name);
		subscription.unsubscribe();
	};

	const definePlugin = factory => {
		assertActive();
		if (typeof factory !== "function") throw new TypeError("Plugin factory must be a function");
		return (...args) => {
			assertActive();
			return factory(...args);
		};
	};

	const cleanup = () => {
		if (disposed) return;
		disposed = true;

		const registrations = [...subscriptions.entries()];
		subscriptions.clear();
		registrations.forEach(([name, entries]) =>
			entries.forEach(subscription => {
				try {
					subscription.unsubscribe();
				} catch {
					reportFailure("event " + name, "unsubscribe");
				}
			}),
		);
		pendingCalls.forEach(request => request.reject(createDisposedError()));
		pendingCalls.clear();
		try {
			unregisterCleanup();
		} catch {
			reportFailure("adapter", "cleanup");
		}
	};
	const unregister = api.cleanup(cleanup);
	if (typeof unregister === "function") {
		unregisterCleanup = unregister;

		if (disposed) unregisterCleanup();
	}
	return Object.freeze({
		call,
		callable,
		addEventListener,
		removeEventListener,
		definePlugin,
		cleanup,
	});
};

if (typeof module === "object" && module && module.exports) {
	module.exports = { createDeckySourceAdapter };
}
