/* Framey-owned external navigation requests. This does not supply a Steam router or browser WindowProxy.
 * Accepts absolute HTTPS URLs of at most 2048 ASCII characters, without credentials or URL repair.
 * Isolated window.open supports only an omitted/empty/_blank target and empty/noopener/noreferrer features.
 * It always returns null synchronously; the native capability reports request acceptance separately. */
function createFrameyDeckyNavigation(api, options = {}) {
	const NativeURL = URL;
	const NativePromise = Promise;
	const installations = new Map();
	let disposed = false;
	function emit(capability, code) {
		const invoke = (callback, argument) => {
			try {
				const result = callback?.(argument);
				if (result?.then) NativePromise.resolve(result).catch(() => {});
			} catch {}
		};
		if (code === "unavailable" || code === "unsupported_shape") invoke(options.onUnsupported, capability);
		invoke(options.onDiagnostic, { capability, code });
	}
	function validURL(url) {
		if (typeof url !== "string" || url.length > 2048 || /[^\x21-\x7e]|\\/.test(url)) return false;
		const authority = /^https:\/\/([^/?#]+)(?:[/?#]|$)/i.exec(url)?.[1];
		if (!authority || authority.includes("@")) return false;
		let port;
		if (authority.startsWith("[")) {
			const match = /^(\[[0-9a-f:.]+\])(?::([0-9]+))?$/i.exec(authority);
			if (!match) return false;
			port = match[2];
		} else {
			const match = /^([^:]+)(?::([0-9]+))?$/.exec(authority);
			if (!match) return false;
			const host = match[1];
			port = match[2];
			if (
				!host ||
				host.length > 253 ||
				!(host.endsWith(".") ? host.slice(0, -1) : host)
					.split(".")
					.every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))
			)
				return false;
		}
		if (port !== undefined && (+port < 1 || +port > 65535)) return false;
		try {
			const parsed = new NativeURL(url);
			return parsed.protocol === "https:" && !!parsed.hostname && !parsed.username && !parsed.password;
		} catch {
			return false;
		}
	}
	function request(url, capability) {
		if (disposed) return emit(capability, "disposed");
		if (!validURL(url)) return emit(capability, "bad_url");
		if (typeof api?.openURL !== "function") return emit(capability, "unavailable");
		try {
			NativePromise.resolve(api.openURL(url))
				.then(reply => {
					if (!reply || reply.requested !== true || "error" in Object(reply)) throw new Error("Request not accepted");
				})
				.catch(() => {
					if (!disposed) emit(capability, "request_failed");
				});
		} catch {
			emit(capability, "request_failed");
		}
	}
	function navigateToExternalWeb(url) {
		request(url, "Navigation.NavigateToExternalWeb");
	}
	function install(realm) {
		if (disposed) return emit("window.open", "disposed");
		if (installations.has(realm)) return;
		const descriptor = Object.getOwnPropertyDescriptor(realm, "open");
		const open = function (url, target, features) {
			if (disposed) {
				emit("window.open", "disposed");
				return null;
			}
			if (
				arguments.length === 0 ||
				arguments.length > 3 ||
				(target !== undefined && target !== "" && target !== "_blank") ||
				(features !== undefined &&
					(typeof features !== "string" ||
						(features !== "" &&
							!features.split(",").every(feature => /^(noopener|noreferrer)$/i.test(feature.trim())))))
			) {
				emit("window.open", "unsupported_shape");
				return null;
			}
			request(url, "window.open");
			return null;
		};
		try {
			Object.defineProperty(realm, "open", {
				configurable: true,
				writable: true,
				enumerable: descriptor?.enumerable ?? true,
				value: open,
			});
			installations.set(realm, { descriptor, open });
		} catch {
			emit("window.open", "unavailable");
		}
	}
	function dispose() {
		if (disposed) return;
		disposed = true;
		for (const [realm, entry] of installations) {
			try {
				if (Object.getOwnPropertyDescriptor(realm, "open")?.value !== entry.open) continue;
				if (entry.descriptor) Object.defineProperty(realm, "open", entry.descriptor);
				else delete realm.open;
			} catch {}
		}
		installations.clear();
	}
	return { navigateToExternalWeb, install, dispose };
}

if (typeof module === "object" && module && module.exports) {
	module.exports = { createFrameyDeckyNavigation };
}
