"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom } = require("./harness");
const files = [
	"fy-api.js",
	"fy-ui.js",
	"fy-pages.js",
	"fy-strings-en.js",
	"fy-modules.js",
	"fy-catalog.js",
	"fy-settings.js",
];
const settle = async () => {
	for (let n = 0; n < 10; n++) await new Promise(resolve => setImmediate(resolve));
};
function boot(runtime = {}) {
	const calls = [];
	const replies = {
		decky_runtime_status: {
			installed: false,
			enabled: false,
			requested_enabled: false,
			version: null,
			previous_version: null,
			generation: 0,
			phase: "off",
			error: null,
		},
		catalog_sources: {
			sources: [
				{ id: "stable", label: "Stable" },
				{ id: "testing", label: "Testing" },
				{ id: "custom", label: "Custom" },
			],
			source: { id: "stable" },
		},
		catalog_list: { entries: [], total: 0, more: false, source: { id: "stable" } },
		...runtime,
	};
	const env = makeDom({
		before(w) {
			w.FY_RUNTIME = { api: 1, lang: "en", platform: "linux", panel_navigation: "launcher", panel_layout: "window" };
			w.fyCall = raw => {
				const r = JSON.parse(raw);
				calls.push(r);
				if (r.id) Promise.resolve(replies[r.method] || {}).then(reply => w.Framey._reply(r.gen, r.id, reply));
			};
		},
		loadFiles: files,
	});
	const box = env.document.querySelector('[role="tabpanel"]');
	return { env, box, K: env.window.__S.K, calls, replies };
}
test("native Store never resolves the optional Store provider or runtime status", async () => {
	const c = boot();
	let providers = 0;
	c.K.optionalRuntime = {
		store() {
			providers++;
			throw Error("optional provider must stay unused");
		},
	};
	c.env.window.Framey.pages.store.render(c.box);
	await settle();
	assert.equal(providers, 0);
	assert.equal(
		c.calls.some(r => r.method === "decky_runtime_status"),
		false,
	);
	assert.equal(c.calls.find(r => r.method === "catalog_list").arg.source, "stable");
	c.env.window.close();
});
test("disabled Decky Store explains the setting without catalog calls or provider execution", async () => {
	const c = boot();
	let providers = 0;
	c.K.optionalRuntime = {
		store() {
			providers++;
			throw Error("disabled provider");
		},
		invalidate() {},
	};
	c.env.window.Framey.pages.decky_store.render(c.box);
	await settle();
	assert.equal(providers, 0);
	assert.equal(
		c.calls.some(r => r.method === "catalog_list" || r.method === "catalog_sources"),
		false,
	);
	assert.match(c.box.textContent, /Decky compatibility is off/);
	const settings = [...c.box.querySelectorAll('[role="button"]')].find(
		n => n.getAttribute("aria-label") === "Decky compatibility settings",
	);
	assert.ok(settings);
	c.env.window.close();
});
test("enabled Decky Store resolves its scoped factory and disposes stale page completion", async () => {
	const c = boot({
		decky_runtime_status: {
			installed: true,
			enabled: true,
			requested_enabled: true,
			version: "1",
			previous_version: null,
			generation: 4,
			phase: "enabled",
			error: null,
		},
	});
	let release,
		context,
		disposed = 0,
		invalidated = 0;
	c.K.optionalRuntime = {
		invalidate() {
			invalidated++;
		},
		store(value) {
			context = value;
			return new Promise(resolve => {
				release = resolve;
			});
		},
	};
	c.env.window.Framey.pages.decky_store.render(c.box);
	await settle();
	assert.ok(context && context.box === c.box);
	assert.equal(invalidated, 0, "opening Store must not invalidate an enabled runtime");
	assert.equal("CORE_TOK" in context, false);
	c.env.window.Framey.pages.decky_store.leave();
	release({
		dispose() {
			disposed++;
		},
	});
	await settle();
	assert.equal(disposed, 1);
	assert.equal(context.alive(), false);
	c.env.window.close();
});
test("an enabled runtime provider failure is unavailable rather than falsely switched off", async () => {
	const c = boot({
		decky_runtime_status: {
			installed: true,
			enabled: true,
			requested_enabled: true,
			version: "1",
			previous_version: null,
			generation: 4,
			phase: "enabled",
			error: null,
		},
	});
	c.K.optionalRuntime = {
		store() {
			return Promise.reject(new Error("Runtime source could not be verified"));
		},
	};
	c.env.window.Framey.pages.decky_store.render(c.box);
	await settle();
	assert.match(c.box.textContent, /Decky Store is unavailable/);
	assert.match(c.box.textContent, /Runtime source could not be verified/);
	assert.equal(c.box.textContent.includes("Decky compatibility is off"), false);
	c.env.window.close();
});
