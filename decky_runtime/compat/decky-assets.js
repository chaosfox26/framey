const createFrameyDeckyAssets = function (name, assetMap) {
	"use strict";
	const maximumImageBytes = 256 * 1024;
	const maximumTotalBytes = 512 * 1024;
	const maximumEntries = 64;
	const images = new Map();
	const installations = new Map();
	let disposed = false;
	const hasControl = value =>
		Array.from(value).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
	const safeSegment = segment =>
		typeof segment === "string" &&
		segment.length > 0 &&
		segment !== "." &&
		segment !== ".." &&
		!/[\\/%?#]/.test(segment) &&
		!hasControl(segment);
	if (!safeSegment(name) || name.length > 256) throw new TypeError("A package manifest name is required");
	const safeKey = key => {
		if (typeof key !== "string" || key.length > 1024) return false;
		const segments = key.split("/");
		return (
			segments.every(safeSegment) &&
			(segments[0] === "assets" || (segments[0] === "dist" && segments[1] === "assets")) &&
			/\.png$/i.test(key)
		);
	};
	const pngSize = value => {
		const prefix = "data:image/png;base64,";
		if (
			typeof value !== "string" ||
			!value.startsWith(prefix) ||
			value.length > prefix.length + 4 * Math.ceil(maximumImageBytes / 3)
		)
			return 0;
		const encoded = value.slice(prefix.length);
		if (encoded.length % 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) return 0;
		let bytes;
		try {
			bytes = atob(encoded);
		} catch {
			return 0;
		}
		if (
			bytes.length < 45 ||
			bytes.length > maximumImageBytes ||
			bytes.slice(0, 16) !== "\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
		)
			return 0;
		const uint32 = offset =>
			bytes.charCodeAt(offset) * 16777216 +
			bytes.charCodeAt(offset + 1) * 65536 +
			bytes.charCodeAt(offset + 2) * 256 +
			bytes.charCodeAt(offset + 3);
		const width = uint32(16);
		const height = uint32(20);
		if (!width || !height || width > 4096 || height > 4096 || width * height > 4000000) return 0;
		return bytes.length;
	};
	if (assetMap && typeof assetMap === "object") {
		const descriptors = Object.getOwnPropertyDescriptors(assetMap);
		const entries = Object.entries(descriptors);
		if (entries.length <= maximumEntries) {
			let totalBytes = 0;
			for (const [key, descriptor] of entries) {
				if (!safeKey(key) || !Object.hasOwn(descriptor, "value")) continue;
				const bytes = pngSize(descriptor.value);
				if (!bytes || totalBytes + bytes > maximumTotalBytes) continue;
				images.set(key, descriptor.value);
				totalBytes += bytes;
			}
		}
	}
	const resolve = url => {
		if (disposed || typeof url !== "string" || url.length > 2048 || /[\\?#]/.test(url) || hasControl(url)) return url;
		const localOrigin = "http://127.0.0.1:1337";
		const path = url.startsWith(localOrigin + "/") ? url.slice(localOrigin.length) : url;
		if (!path.startsWith("/plugins/")) return url;
		let segments;
		try {
			segments = path
				.slice(1)
				.split("/")
				.map(segment => decodeURIComponent(segment));
		} catch {
			return url;
		}
		if (!segments.every(safeSegment) || segments[1] !== name) return url;
		const key = segments.slice(2).join("/");
		if (!safeKey(key)) return url;
		return images.get(key) || (key.startsWith("assets/") && images.get("dist/" + key)) || url;
	};
	const install = realm => {
		if (disposed) throw new Error("Package image resolver disposed");
		if (installations.has(realm)) return;
		if (!realm || !realm.HTMLImageElement || !realm.Element)
			throw new TypeError("An image-capable plugin realm is required");
		const imagePrototype = realm.HTMLImageElement.prototype;
		const elementPrototype = realm.Element.prototype;
		const srcDescriptor = Object.getOwnPropertyDescriptor(imagePrototype, "src");
		const attributeDescriptor = Object.getOwnPropertyDescriptor(elementPrototype, "setAttribute");
		if (
			!srcDescriptor ||
			!srcDescriptor.set ||
			!srcDescriptor.configurable ||
			!attributeDescriptor ||
			typeof attributeDescriptor.value !== "function" ||
			!attributeDescriptor.configurable
		)
			throw new TypeError("Plugin image properties cannot be instrumented");
		const setSrc = function (value) {
			return srcDescriptor.set.call(this, resolve(value));
		};
		const setAttribute = function (attribute, value) {
			const rewritten =
				this instanceof realm.HTMLImageElement && typeof attribute === "string" && attribute.toLowerCase() === "src"
					? resolve(value)
					: value;
			return attributeDescriptor.value.call(this, attribute, rewritten);
		};
		Object.defineProperty(imagePrototype, "src", { ...srcDescriptor, set: setSrc });
		try {
			Object.defineProperty(elementPrototype, "setAttribute", { ...attributeDescriptor, value: setAttribute });
		} catch (error) {
			Object.defineProperty(imagePrototype, "src", srcDescriptor);
			throw error;
		}
		installations.set(realm, {
			imagePrototype,
			elementPrototype,
			srcDescriptor,
			attributeDescriptor,
			setSrc,
			setAttribute,
		});
	};
	const dispose = () => {
		if (disposed) return;
		disposed = true;
		images.clear();
		for (const entry of installations.values()) {
			const currentSrc = Object.getOwnPropertyDescriptor(entry.imagePrototype, "src");
			const currentAttribute = Object.getOwnPropertyDescriptor(entry.elementPrototype, "setAttribute");
			if (currentSrc && currentSrc.configurable && currentSrc.set === entry.setSrc)
				Object.defineProperty(entry.imagePrototype, "src", { ...currentSrc, set: entry.srcDescriptor.set });
			if (currentAttribute && currentAttribute.configurable && currentAttribute.value === entry.setAttribute)
				Object.defineProperty(entry.elementPrototype, "setAttribute", {
					...currentAttribute,
					value: entry.attributeDescriptor.value,
				});
		}
		installations.clear();
	};
	return Object.freeze({ resolve, install, dispose });
};

if (typeof module === "object" && module && module.exports) {
	module.exports = { createFrameyDeckyAssets };
}
