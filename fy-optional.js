/* Fixed core broker for the independently installed compatibility runtime. */
(() => {
	const framey = __FY;
	const k = framey?._k?.(typeof __fyt === "string" ? __fyt : "");
	if (!k || k.optionalRuntime) return;
	const P = Promise,
		evaluate = eval;
	const subscribers = new Set();
	let epoch = 0,
		generation = 0,
		provider = null,
		source = "",
		version = "",
		pending = null;
	const error = (code = "runtime_unavailable") =>
		Object.assign(new Error("Compatibility runtime is unavailable"), { code });
	const diagnose = () => {
		try {
			k.diagnostic?.("api.compat_failed");
		} catch {}
	};
	const safeDispose = value => {
		try {
			value?.dispose?.();
		} catch {
			diagnose();
		}
	};
	const notify = replacing => {
		for (const fn of [...subscribers]) {
			try {
				fn(replacing);
			} catch {
				diagnose();
			}
		}
	};
	const clear = (keepPending = false) => {
		epoch++;
		provider = null;
		source = "";
		version = "";
		if (!keepPending) {
			if (pending) pending.cancel(error());
			pending = null;
		}
		notify(keepPending);
	};
	const current = value => !!provider && generation === value;
	const invalidate = status => {
		if (status && Number.isSafeInteger(status.generation) && status.generation < generation) return;
		if (status && Number.isSafeInteger(status.generation)) generation = status.generation;
		clear();
	};
	const ensure = (name, abi) => {
		if (name !== "decky" || abi !== 1) return P.reject(error());
		if (pending) return pending.promise;
		const start = epoch;
		let resolve, reject;
		const promise = new P((yes, no) => {
			resolve = yes;
			reject = no;
		});
		const record = { promise, cancel: reject };
		pending = record;
		P.resolve()
			.then(() => k.call("decky_runtime_frontend", {}))
			.then(reply => {
				if (start !== epoch) throw error();
				if (reply?.error) throw error(typeof reply.code === "string" ? reply.code : "runtime_unavailable");
				if (
					!reply ||
					reply.abi !== 1 ||
					!Number.isSafeInteger(reply.generation) ||
					reply.generation < 1 ||
					typeof reply.version !== "string" ||
					!reply.version ||
					reply.version.length > 100 ||
					typeof reply.source !== "string" ||
					!reply.source ||
					reply.source.length > 2000000 ||
					reply.generation < generation
				)
					throw error();
				if (provider && reply.generation === generation) {
					if (reply.source !== source || reply.version !== version) throw error("runtime_changed");
					return provider;
				}
				if (provider) clear(true);
				// Only the authenticated core RPC may deliver manager-verified factory text.
				const factory = (0, evaluate)("(" + reply.source + "\n)");
				if (typeof factory !== "function") throw error("runtime_abi");
				const loaded = factory();
				if (
					!loaded ||
					loaded.abi !== 1 ||
					typeof loaded.attach !== "function" ||
					(loaded.store !== undefined && typeof loaded.store !== "function")
				)
					throw error("runtime_abi");
				generation = reply.generation;
				source = reply.source;
				version = reply.version;
				provider = Object.freeze({
					abi: 1,
					generation,
					version,
					attach: context => loaded.attach(context),
					...(loaded.store ? { store: context => loaded.store(context) } : {}),
				});
				return provider;
			})
			.then(resolve, reason => {
				if (start === epoch && provider) clear(true);
				reject(reason);
			})
			.finally(() => {
				if (pending === record) pending = null;
			});
		return promise;
	};
	const subscribe = fn => {
		subscribers.add(fn);
		return () => subscribers.delete(fn);
	};
	const store = async context => {
		const loaded = await ensure("decky", 1),
			gen = loaded.generation;
		if (typeof loaded.store !== "function") throw error("runtime_abi");
		let active = true,
			handle,
			rejectPending;
		const cancelled = new P((_, reject) => {
			rejectPending = reject;
		});
		const alive = () => active && current(gen) && (typeof context.alive !== "function" || context.alive());
		const off = subscribe(() => {
			active = false;
			safeDispose(handle);
			rejectPending(error());
		});
		const mounting = P.resolve()
			.then(() => loaded.store({ ...context, generation: gen, current: () => current(gen), alive }))
			.then(value => {
				if (!alive()) {
					safeDispose(value);
					throw error();
				}
				handle = value;
				return value;
			});
		try {
			await P.race([mounting, cancelled]);
			return Object.freeze({
				dispose() {
					if (!active) {
						off();
						return;
					}
					active = false;
					off();
					safeDispose(handle);
				},
				leave() {
					if (active) {
						if (handle?.leave) handle.leave();
						else this.dispose();
					}
				},
			});
		} catch (reason) {
			off();
			throw reason;
		}
	};
	Object.defineProperty(k, "optionalRuntime", {
		value: Object.freeze({
			ensure,
			invalidate,
			current,
			subscribe,
			store,
			state: () => Object.freeze({ generation, version, active: !!provider }),
		}),
		enumerable: true,
	});
})();
