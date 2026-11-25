"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { makeDom } = require("./harness");
const settle = async () => {
	for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
};
function boot(over = {}) {
	const calls = [],
		replies = Object.assign(
			{
				list: Array.from({ length: 80 }, (_, i) => ({
					id: "p" + i,
					name: "Plugin " + i,
					version: "1.0",
					enabled: true,
					compat: { badge: "ok" },
					commit: "a".repeat(40),
				})),
				store: [],
				state_get: { order: [], sort: "custom", hidden: [], frozen: {} },
				status: {},
				maint_prefs: {},
				maint_get: { records: {} },
			},
			over,
		);
	const e = makeDom({
		before(w) {
			w.FY_RUNTIME = { trusted_input: "off", lang: "en", panel_navigation: "launcher", panel_layout: "window" };
			w.fyCall = function (raw) {
				const c = JSON.parse(raw);
				calls.push(c);
				if (c.id)
					Promise.resolve(
						typeof replies[c.method] === "function" ? replies[c.method](c.arg) : replies[c.method] || {},
					).then(v => w.Framey._reply(c.gen, c.id, v));
			};
		},
		loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js"],
	});
	const K = e.window.__S.K;
	K.panelLayout = () => "window";
	let navigations = 0;
	K.setPageNavigation = () => navigations++;
	const panel = e.document.createElement("div");
	panel.className = "fy-panel";
	const box = e.document.createElement("div");
	panel.append(box);
	e.document.body.append(panel);
	const find = label => box.querySelector(`[aria-label="${label}"]`);
	const click = async label => {
		const n = find(label);
		assert.ok(n, "Missing " + label + " in " + box.textContent);
		n.click();
		await settle();
	};
	return {
		e,
		K,
		box,
		calls,
		replies,
		find,
		click,
		navigations: () => navigations,
		async open() {
			e.window.Framey.pages.modules.render(box);
			await settle();
		},
		async input(label, value) {
			const n = find(label);
			assert.ok(n, "Missing " + label);
			n.value = value;
			n.dispatchEvent(new e.window.Event("input", { bubbles: true }));
			await settle();
		},
		async choose(label, value) {
			const n = find(label);
			assert.ok(n, "Missing " + label);
			n.value = value;
			n.dispatchEvent(new e.window.Event("change", { bubbles: true }));
			await settle();
		},
	};
}
test("window collection searches every installed plugin without shell pagination", async () => {
	const c = boot();
	await c.open();
	assert.ok(c.find("Search installed plugins"));
	assert.equal(c.navigations(), 0);
	await c.input("Search installed plugins", "p79");
	await c.click("Plugin 79");
	assert.ok(c.find("Actions"));
	assert.equal(c.navigations(), 0);
	assert.ok(!c.box.textContent.includes("Previous"));
	c.e.window.close();
});
test("contextual actions retain confirmation and exact uninstall arguments", async () => {
	const c = boot();
	await c.open();
	await c.input("Search installed plugins", "p79");
	await c.click("Plugin 79");
	await c.choose("Actions", "Remove and delete data");
	await c.click("Run action");
	assert.equal(c.calls.filter(x => x.method === "uninstall").length, 0);
	await c.click("Delete everything");
	assert.deepEqual(JSON.parse(JSON.stringify(c.calls.find(x => x.method === "uninstall").arg)), {
		id: "p79",
		purge: true,
	});
	c.e.window.close();
});
test("complete maintenance and metadata values remain searchable without clipping", async () => {
	const long = "start " + "detail ".repeat(400) + " FINAL EVIDENCE";
	const c = boot({
		maint_get: {
			records: {
				p79: { label: "recent", reason: long, evidence: Array.from({ length: 25 }, (_, i) => "evidence " + i) },
			},
		},
	});
	await c.open();
	await c.input("Search installed plugins", "p79");
	await c.click("Plugin 79");
	await c.click("Details");
	await c.input("Find a detail", "FINAL EVIDENCE");
	assert.ok(c.box.querySelector('select[aria-label="Detail"]'));
	await c.choose("Text segment", "last");
	assert.ok(c.box.textContent.includes("FINAL EVIDENCE"));
	assert.equal(c.navigations(), 0);
	c.e.window.close();
});
test("all maintenance batches and all platform companions remain reachable", async () => {
	const c = boot({
		list: [
			{
				id: "p79",
				name: "Plugin 79",
				enabled: true,
				companions: Array.from({ length: 8 }, (_, i) => ({ name: "Helper " + i })),
			},
		],
	});
	await c.open();
	await c.click("Plugin 79");
	const options = [...c.find("Actions").options].map(n => n.textContent);
	assert.ok(options.includes("Download Helper 7"));
	c.e.window.close();
	const many = boot();
	await many.open();
	assert.deepEqual(
		many.calls.filter(c => c.method === "maint_get").map(c => c.arg.ids.length),
		[64, 16],
	);
	many.e.window.close();
});
test("progress detail review and Back preserve an in-flight update completion", async () => {
	let release;
	const c = boot({
		update: () =>
			new Promise(r => {
				release = r;
			}),
	});
	await c.open();
	await c.input("Search installed plugins", "p79");
	await c.click("Plugin 79");
	await c.click("Update");
	await c.click("Install");
	c.e.event("_core", "progress", {
		job: "one",
		stage: "download",
		label: "Full download stage evidence",
		state: "running",
		index: 1,
		total: 2,
	});
	await c.click("Progress details");
	await c.click("Back");
	release({ ok: "p79", version: "2.0" });
	await settle();
	assert.ok(c.box.textContent.includes("Updated Plugin 79"));
	assert.ok(c.find("Back"));
	assert.equal(c.navigations(), 0);
	c.e.window.close();
});
test("navigating back ignores late lifecycle replies and retains the search", async () => {
	let release;
	const c = boot({
		reload_one: () =>
			new Promise(r => {
				release = r;
			}),
	});
	await c.open();
	await c.input("Search installed plugins", "p79");
	await c.click("Plugin 79");
	await c.choose("Actions", "Reload");
	await c.click("Run action");
	await c.click("Back");
	assert.equal(c.find("Search installed plugins").value, "p79");
	const before = c.box.textContent;
	release({ ok: true });
	await settle();
	assert.equal(c.box.textContent, before);
	c.e.window.close();
});
test("complete batch results survive reopening in the same frontend session", async () => {
	const c = boot({
		batch_plan: {
			items: Array.from({ length: 70 }, (_, i) => ({
				id: "p" + i,
				name: "Plugin " + i,
				eligible: true,
				from: "1",
				to: "2",
			})),
		},
		batch_apply: {
			results: Array.from({ length: 70 }, (_, i) => ({ id: "p" + i, name: "Plugin " + i, state: "ok", to: "2" })),
		},
	});
	await c.open();
	await c.click("Update all");
	await c.click("Update 70");
	assert.ok(c.find("Text segment"));
	await c.choose("Text segment", "last");
	assert.ok(c.box.textContent.includes("Plugin 69"));
	await c.open();
	await c.choose("Text segment", "last");
	assert.ok(c.box.textContent.includes("Plugin 69"));
	assert.ok(JSON.parse(c.e.window.sessionStorage.fyRes).rows.length <= 32);
	c.e.window.close();
});
test("lifecycle failure keeps its complete error and stage in contextual Details", async () => {
	const error = "Lifecycle error " + "evidence ".repeat(100) + " FINAL FAILURE";
	const c = boot({ reload_one: { error, stage: "verify-package" } });
	await c.open();
	await c.input("Search installed plugins", "p79");
	await c.click("Plugin 79");
	await c.choose("Actions", "Reload");
	await c.click("Run action");
	await c.click("Details");
	await c.input("Find a detail", "FINAL FAILURE");
	await c.choose("Text segment", "last");
	assert.ok(c.box.textContent.includes("FINAL FAILURE"));
	await c.input("Find a detail", "verify-package");
	assert.ok(c.box.textContent.includes("verify-package"));
	c.e.window.close();
});
