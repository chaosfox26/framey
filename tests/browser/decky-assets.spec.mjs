import { test, expect } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const path = resolve(import.meta.dirname, "../../decky_runtime/compat/decky-assets.js");
const source = existsSync(path) ? readFileSync(path, "utf8") : "const createFrameyDeckyAssets = undefined;";
// Independently authored 2 × 3 RGBA PNG; no package code or artwork is copied.
const png =
	"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAYAAAC56t6BAAAAEUlEQVR4nGPQSNnyH4QZMBgArYsNewfxoK0AAAAASUVORK5CYII=";

async function prepare(page) {
	const requests = [];
	await page.route("**/*", route => {
		requests.push(route.request().url());
		return route.abort();
	});
	await page.setContent(
		'<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; frame-src about:; img-src data: blob:"><iframe title="Owned plugin"></iframe>',
	);
	await page.addScriptTag({ content: `${source}\nwindow.createAssets = createFrameyDeckyAssets;` });
	expect(await page.evaluate(() => typeof window.createAssets)).toBe("function");
	return requests;
}

// Missing either interception path would leave the real browser unable to decode these package images.
test("package PNGs decode through native src properties and renderer attributes without network", async ({ page }) => {
	const requests = await prepare(page);
	const result = await page.evaluate(async png => {
		const realm = document.querySelector("iframe").contentWindow;
		const parentSetter = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src").set;
		const parentAttribute = Element.prototype.setAttribute;
		const assets = window.createAssets("Music Control", { "dist/assets/cover art.png": png });
		assets.install(realm);
		const load = (url, attribute) =>
			new Promise((resolve, reject) => {
				const image = realm.document.createElement("img");
				image.onload = () => resolve([image.naturalWidth, image.naturalHeight, image.src]);
				image.onerror = () => reject(new Error("Package PNG failed to decode"));
				if (attribute) image.setAttribute("SRC", url);
				else image.src = url;
				realm.document.body.append(image);
			});
		const decoded = await Promise.all([
			load("http://127.0.0.1:1337/plugins/Music Control/assets/cover%20art.png", false),
			load("/plugins/Music%20Control/dist/assets/cover art.png", true),
		]);
		assets.dispose();
		return {
			decoded,
			parentUntouched:
				parentSetter === Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src").set &&
				parentAttribute === Element.prototype.setAttribute,
		};
	}, png);
	expect(result.decoded).toEqual([
		[2, 3, png],
		[2, 3, png],
	]);
	expect(result.parentUntouched).toBe(true);
	expect(requests).toEqual([]);
});

// URL normalization must never turn another package, encoded separators, or traversal into an owned asset.
test("resolver matches exact package paths and preserves rejected URLs verbatim", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(png => {
		const assets = window.createAssets("RadiYo!", { "assets/a b.png": png });
		const accepted = ["http://127.0.0.1:1337/plugins/RadiYo!/assets/a%20b.png", "/plugins/RadiYo%21/assets/a b.png"];
		const rejected = [
			"http://127.0.0.1:1337/plugins/Other/assets/a%20b.png",
			"http://127.0.0.1:1338/plugins/RadiYo!/assets/a%20b.png",
			"https://127.0.0.1:1337/plugins/RadiYo!/assets/a%20b.png",
			"http://localhost:1337/plugins/RadiYo!/assets/a%20b.png",
			"http://u:p@127.0.0.1:1337/plugins/RadiYo!/assets/a%20b.png",
			"//127.0.0.1:1337/plugins/RadiYo!/assets/a%20b.png",
			"/plugins/Other/../RadiYo!/assets/a%20b.png",
			"/plugins/RadiYo!/assets/%2e%2e/assets/a%20b.png",
			"/plugins/RadiYo!%2fassets/a%20b.png",
			"/plugins/RadiYo!/assets/x%2f..%2fa%20b.png",
			"/plugins/RadiYo!/assets/x%5c..%5ca%20b.png",
			"/plugins/RadiYo!/assets/%252e%252e/a%20b.png",
			"/plugins/RadiYo!/assets/%ZZ.png",
			"/plugins/RadiYo!/assets/missing.png",
			"/plugins/RadiYo!/assets/a%20b.png?x=1",
			"/plugins/RadiYo!/assets/a%20b.png#x",
			"data:image/svg+xml,<svg/>",
		];
		return {
			accepted: accepted.map(url => assets.resolve(url)),
			rejected: rejected.map(url => [url, assets.resolve(url)]),
		};
	}, png);
	expect(result.accepted).toEqual([png, png]);
	for (const [url, actual] of result.rejected) expect(actual).toBe(url);
});

// A missing original image must still fire onerror so plugin-authored PNG fallback assignments work.
test("unknown images retain browser error events and package fallback assignments decode", async ({ page }) => {
	const requests = await prepare(page);
	const result = await page.evaluate(async png => {
		const realm = document.querySelector("iframe").contentWindow;
		const assets = window.createAssets("Fallback", { "assets/fallback.png": png });
		assets.install(realm);
		return new Promise((resolve, reject) => {
			const image = realm.document.createElement("img");
			let errors = 0;
			image.onerror = () => {
				if (++errors > 1) return reject(new Error("Fallback failed"));
				image.src = "http://127.0.0.1:1337/plugins/Fallback/assets/fallback.png";
			};
			image.onload = () => {
				assets.dispose();
				resolve([errors, image.naturalWidth, image.naturalHeight]);
			};
			image.src = "http://127.0.0.1:1337/plugins/Other/assets/unknown.png";
		});
	}, png);
	expect(result).toEqual([1, 2, 3]);
	expect(requests).toEqual([]);
});

// Unsafe maps must not execute getters or turn SVG, invalid PNG headers, or oversized data into image sources.
test("map validation copies bounded PNG data without invoking accessors", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(png => {
		let getters = 0;
		const map = {
			"assets/good.png": png,
			"assets/svg.png": "data:image/svg+xml,<svg/>",
			"assets/fake.png": "data:image/png;base64,AAAA",
		};
		Object.defineProperty(map, "assets/getter.png", {
			enumerable: true,
			get() {
				getters++;
				return png;
			},
		});
		map["assets/big.png"] = "data:image/png;base64," + "A".repeat(350000);
		map["assets/../escape.png"] = png;
		const assets = window.createAssets("Safe", map);
		map["assets/good.png"] = "changed";
		const names = ["good", "svg", "fake", "getter", "big"];
		return { getters, values: names.map(name => assets.resolve(`/plugins/Safe/assets/${name}.png`)) };
	}, png);
	expect(result.getters).toBe(0);
	expect(result.values).toEqual([
		png,
		"/plugins/Safe/assets/svg.png",
		"/plugins/Safe/assets/fake.png",
		"/plugins/Safe/assets/getter.png",
		"/plugins/Safe/assets/big.png",
	]);
});

// Cleanup must restore native methods while preserving replacements installed later by another owner.
test("installation is idempotent and disposal restores only still-owned wrappers", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(png => {
		const realm = document.querySelector("iframe").contentWindow;
		const imagePrototype = realm.HTMLImageElement.prototype;
		const elementPrototype = realm.Element.prototype;
		const before = Object.getOwnPropertyDescriptor(imagePrototype, "src");
		const beforeAttribute = Object.getOwnPropertyDescriptor(elementPrototype, "setAttribute");
		const assets = window.createAssets("Cleanup", { "assets/a.png": png });
		assets.install(realm);
		const first = Object.getOwnPropertyDescriptor(imagePrototype, "src").set;
		assets.install(realm);
		const idempotent = first === Object.getOwnPropertyDescriptor(imagePrototype, "src").set;
		assets.dispose();
		assets.dispose();
		const restored =
			before.set === Object.getOwnPropertyDescriptor(imagePrototype, "src").set &&
			beforeAttribute.value === elementPrototype.setAttribute;
		const second = window.createAssets("Cleanup", { "assets/a.png": png });
		second.install(realm);
		const laterSetter = function (value) {
			return before.set.call(this, value);
		};
		const laterAttribute = function (...args) {
			return beforeAttribute.value.apply(this, args);
		};
		Object.defineProperty(imagePrototype, "src", { ...before, set: laterSetter });
		elementPrototype.setAttribute = laterAttribute;
		second.dispose();
		return {
			idempotent,
			restored,
			laterKept:
				imagePrototype.__lookupSetter__("src") === laterSetter && elementPrototype.setAttribute === laterAttribute,
			inactive: assets.resolve("/plugins/Cleanup/assets/a.png"),
		};
	}, png);
	expect(result).toEqual({
		idempotent: true,
		restored: true,
		laterKept: true,
		inactive: "/plugins/Cleanup/assets/a.png",
	});
});

test("disposal preserves a later src getter replacement and disables its retained wrapper", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(png => {
		const realm = document.querySelector("iframe").contentWindow;
		const prototype = realm.HTMLImageElement.prototype;
		const assets = window.createAssets("Later", { "assets/a.png": png });
		assets.install(realm);
		const installed = Object.getOwnPropertyDescriptor(prototype, "src");
		const getter = function () {
			return "owned by later caller";
		};
		Object.defineProperty(prototype, "src", { ...installed, get: getter });
		assets.dispose();
		const image = realm.document.createElement("img");
		image.src = "/plugins/Later/assets/a.png";
		return {
			laterKept: Object.getOwnPropertyDescriptor(prototype, "src").get === getter,
			raw: image.getAttribute("src"),
		};
	}, png);
	expect(result).toEqual({ laterKept: true, raw: "/plugins/Later/assets/a.png" });
});

test("entry, image, dimension, and aggregate byte limits bound admitted PNG maps", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(png => {
		const prefix = "data:image/png;base64,";
		const bytes = atob(png.slice(prefix.length));
		const padded = prefix + btoa(bytes + "\0".repeat(200000 - bytes.length));
		const oversized = prefix + btoa(bytes + "\0".repeat(256 * 1024 + 1 - bytes.length));
		const wide = prefix + btoa(bytes.slice(0, 16) + "\0\0\x10\x01" + bytes.slice(20));
		const pixels = prefix + btoa(bytes.slice(0, 16) + "\0\0\x07\xd1\0\0\x07\xd0" + bytes.slice(24));
		const assets = window.createAssets("Bounds", {
			"assets/a.png": padded,
			"assets/b.png": padded,
			"assets/c.png": padded,
			"assets/large.png": oversized,
			"assets/wide.png": wide,
			"assets/pixels.png": pixels,
		});
		const many = {};
		for (let index = 0; index < 65; index++) many[`assets/${index}.png`] = png;
		const rejected = window.createAssets("Bounds", many);
		return {
			accepted: [
				assets.resolve("/plugins/Bounds/assets/a.png") === padded,
				assets.resolve("/plugins/Bounds/assets/b.png") === padded,
			],
			others: ["c", "large", "wide", "pixels"].map(name => assets.resolve(`/plugins/Bounds/assets/${name}.png`)),
			count: rejected.resolve("/plugins/Bounds/assets/0.png"),
		};
	}, png);
	expect(result).toEqual({
		accepted: [true, true],
		others: [
			"/plugins/Bounds/assets/c.png",
			"/plugins/Bounds/assets/large.png",
			"/plugins/Bounds/assets/wide.png",
			"/plugins/Bounds/assets/pixels.png",
		],
		count: "/plugins/Bounds/assets/0.png",
	});
});
