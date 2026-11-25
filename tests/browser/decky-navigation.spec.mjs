import { test, expect } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const path = resolve(import.meta.dirname, "../../decky_runtime/compat/decky-navigation.js");
const source = existsSync(path) ? readFileSync(path, "utf8") : "const createFrameyDeckyNavigation = undefined;";

async function prepare(page) {
	const requests = [];
	await page.route("**/*", route => {
		requests.push(route.request().url());
		return route.abort();
	});
	await page.setContent('<!doctype html><iframe title="Owned plugin"></iframe>');
	await page.addScriptTag({ content: `${source}\nwindow.makeNavigation = createFrameyDeckyNavigation;` });
	expect(await page.evaluate(() => typeof window.makeNavigation)).toBe("function");
	return requests;
}

// Losing the URL query/fragment or returning an invented WindowProxy would break real search actions.
test("external navigation preserves HTTPS destinations and window.open returns null synchronously", async ({
	page,
}) => {
	const requests = await prepare(page);
	const result = await page.evaluate(async () => {
		const urls = [];
		const adapter = window.makeNavigation({ openURL: url => (urls.push(url), Promise.resolve({ requested: true })) });
		const realm = document.querySelector("iframe").contentWindow;
		adapter.install(realm);
		const returns = [
			adapter.navigateToExternalWeb("https://www.protondb.com"),
			realm.open("https://www.google.com/search?q=Game%20Name#results", "_blank"),
			realm.open("https://www.pcgamingwiki.com", "", "noopener,noreferrer"),
			realm.open("https://howlongtobeat.com"),
		];
		await Promise.resolve();
		adapter.dispose();
		return { urls, undefinedNavigation: returns[0] === undefined, windowReturns: returns.slice(1) };
	});
	expect(result).toEqual({
		urls: [
			"https://www.protondb.com",
			"https://www.google.com/search?q=Game%20Name#results",
			"https://www.pcgamingwiki.com",
			"https://howlongtobeat.com",
		],
		undefinedNavigation: true,
		windowReturns: [null, null, null],
	});
	expect(requests).toEqual([]);
});

// WHATWG URL normalization must not silently repair inputs the native validator rejects.
test("unsafe and normalized-away URL shapes never reach the native capability", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(() => {
		const calls = [],
			diagnostics = [];
		const adapter = window.makeNavigation(
			{ openURL: url => calls.push(url) },
			{ onDiagnostic: d => diagnostics.push(d) },
		);
		const invalid = [
			"http://example.com",
			"javascript:alert(1)",
			"data:text/html,x",
			"file:///etc/hosts",
			"steam://open/main",
			"//example.com",
			"/relative",
			"https:example.com",
			"https:///example.com",
			"https://user:pass@example.com",
			"https://@example.com",
			"https://example.com:",
			"https://example.com:0",
			"https://example.com:65536",
			"https://example.com/\nsecret",
			"https://example.com\\path",
			" https://example.com",
			"https://éxample.com",
			"https://-bad.example",
			"https://example..com",
			"https://example.com/" + "x".repeat(2048),
			{},
			null,
		];
		for (const url of invalid) adapter.navigateToExternalWeb(url);
		adapter.dispose();
		return {
			calls,
			count: diagnostics.length,
			sanitized: diagnostics.every(
				d => d.code === "bad_url" && d.capability === "Navigation.NavigateToExternalWeb" && Object.keys(d).length === 2,
			),
		};
	});
	expect(result).toEqual({ calls: [], count: 23, sanitized: true });
});

// Other browsing contexts or window-management features cannot be represented by a native open request.
test("window targets and feature requests fail closed without invoking native open", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(() => {
		const calls = [],
			diagnostics = [];
		const adapter = window.makeNavigation(
			{ openURL: url => calls.push(url) },
			{ onDiagnostic: d => diagnostics.push(d) },
		);
		const realm = document.querySelector("iframe").contentWindow;
		adapter.install(realm);
		const rejected = [
			["https://example.com", "_self"],
			["https://example.com", "_top"],
			["https://example.com", "_parent"],
			["https://example.com", "named"],
			["https://example.com", "_blank", "width=600"],
			["https://example.com", "_blank", "noopener=false"],
			["https://example.com", {}, ""],
			["https://example.com", "_blank", {}],
			["https://example.com", "_blank", "", true],
			[],
		];
		const results = rejected.map(args => realm.open(...args));
		adapter.dispose();
		return { calls, allNull: results.every(v => v === null), diagnostics };
	});
	expect(result.calls).toEqual([]);
	expect(result.allNull).toBe(true);
	expect(result.diagnostics).toHaveLength(10);
	for (const d of result.diagnostics) expect(d).toEqual({ capability: "window.open", code: "unsupported_shape" });
});

// Disposal must neither call a stale API nor overwrite a later owner of realm.open.
test("installation restores only its own open function and disposed references stay inert", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(() => {
		const calls = [],
			diagnostics = [];
		const realm = document.querySelector("iframe").contentWindow;
		const original = realm.open;
		const adapter = window.makeNavigation(
			{ openURL: url => calls.push(url) },
			{ onDiagnostic: d => diagnostics.push(d) },
		);
		adapter.install(realm);
		adapter.install(realm);
		const stale = realm.open;
		adapter.dispose();
		const restored = realm.open === original;
		const staleReturn = stale("https://example.com", "_blank");
		adapter.navigateToExternalWeb("https://example.com");
		adapter.install(realm);
		const stayedRestored = realm.open === original;
		const second = window.makeNavigation({ openURL: url => calls.push(url) });
		second.install(realm);
		const laterOwner = () => null;
		realm.open = laterOwner;
		second.dispose();
		return {
			calls,
			restored,
			stayedRestored,
			staleReturn,
			laterOwnerKept: realm.open === laterOwner,
			codes: diagnostics.map(d => d.code),
		};
	});
	expect(result).toEqual({
		calls: [],
		restored: true,
		stayedRestored: true,
		staleReturn: null,
		laterOwnerKept: true,
		codes: ["disposed", "disposed", "disposed"],
	});
});

// Provider errors can include credentials or response content; diagnostics must contain neither.
test("missing or rejecting providers produce sanitized diagnostics without uncaught errors", async ({ page }) => {
	const errors = [];
	page.on("pageerror", error => errors.push(error.name));
	await prepare(page);
	const result = await page.evaluate(async () => {
		const diagnostics = [],
			unsupported = [];
		const options = { onDiagnostic: d => diagnostics.push(d), onUnsupported: name => unsupported.push(name) };
		const absent = window.makeNavigation({}, options);
		absent.navigateToExternalWeb("https://example.com");
		const rejecting = window.makeNavigation(
			{ openURL: () => Promise.reject(new Error("private response text")) },
			options,
		);
		rejecting.navigateToExternalWeb("https://example.com");
		const throwing = window.makeNavigation(
			{
				openURL() {
					throw new Error("private response text");
				},
			},
			options,
		);
		throwing.navigateToExternalWeb("https://example.com");
		await new Promise(resolve => setTimeout(resolve, 0));
		return { diagnostics, unsupported };
	});
	expect(result).toEqual({
		diagnostics: [
			{ capability: "Navigation.NavigateToExternalWeb", code: "unavailable" },
			{ capability: "Navigation.NavigateToExternalWeb", code: "request_failed" },
			{ capability: "Navigation.NavigateToExternalWeb", code: "request_failed" },
		],
		unsupported: ["Navigation.NavigateToExternalWeb"],
	});
	expect(errors).toEqual([]);
});

// A resolved denial envelope is still a failure, not evidence that a browser request was accepted.
test("resolved denial and malformed native replies are reported as sanitized request failures", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		const diagnostics = [];
		for (const reply of [{ error: "private response text", code: "denied" }, { requested: false }, undefined]) {
			const adapter = window.makeNavigation(
				{ openURL: () => Promise.resolve(reply) },
				{ onDiagnostic: d => diagnostics.push(d) },
			);
			adapter.navigateToExternalWeb("https://example.com");
		}
		await new Promise(resolve => setTimeout(resolve, 0));
		return diagnostics;
	});
	expect(result).toEqual([
		{ capability: "Navigation.NavigateToExternalWeb", code: "request_failed" },
		{ capability: "Navigation.NavigateToExternalWeb", code: "request_failed" },
		{ capability: "Navigation.NavigateToExternalWeb", code: "request_failed" },
	]);
});
