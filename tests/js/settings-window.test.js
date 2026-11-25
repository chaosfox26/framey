"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom } = require("./harness");
const files = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-settings.js"];
const settle = async () => {
	for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
};
const candidate = { tag: "v2.0.0", commit: "a".repeat(40), published: "2026-10-01" };
function boot(over = {}) {
	const requests = [];
	const replies = {
		loader: { commit: "1234567", kept: true },
		state_get: {
			prefs: {
				autocheck: false,
				notify_loader: true,
				notify_plugins: false,
				workers_max: 2,
				channel: "stable",
				dev: { enabled: false },
			},
			safe: true,
		},
		autocheck_status: { enabled: false, last: 0 },
		maint_prefs: { "maint.enabled_background": true, "maint.window_days": 365 },
		status: {},
		panel_status: {
			mode: "window",
			layout: "window",
			placements: { compact: true, window: true },
			active: true,
			available: true,
		},
		loader_check: { loader: { state: "update", notes: "fixture notes" }, candidate, installed: { version: "1.0.0" } },
		...over,
	};
	const env = makeDom({
		before(w) {
			w.FY_RUNTIME = {
				version: "1.0.0",
				api: 1,
				lang: "en",
				platform: "linux",
				panel_navigation: "launcher",
				panel_layout: "window",
			};
			w.fyCall = function (payload) {
				const request = JSON.parse(payload);
				requests.push(request);
				if (!request.id) return;
				const value = replies[request.method] ?? { error: "unhandled " + request.method };
				Promise.resolve(typeof value === "function" ? value(request.arg) : value).then(reply =>
					w.Framey._reply(request.gen, request.id, reply),
				);
			}.bind(null);
		},
		loadFiles: files,
	});
	const K = env.window.__S.K;
	const box = env.document.querySelector("[role=tabpanel]");
	const control = name =>
		[...box.querySelectorAll("[aria-label]")].find(node => node.getAttribute("aria-label") === name);
	const click = async name => {
		assert.ok(control(name), "control " + name);
		control(name).click();
		await settle();
	};
	const open = async (name = "preferences") => {
		const route = K.windowPages?.find(page => page.id === "#settings:" + name);
		assert.ok(route, "private settings window route " + name);
		route.render(box);
		await settle();
	};
	return { env, box, K, open, control, click, requests, replies, text: () => box.textContent };
}
test("private window routes open focused screens and #settings defaults to Preferences", async () => {
	const c = boot();
	assert.deepEqual(
		Array.from(c.K.windowPages, page => page.id),
		[
			"#settings:preferences",
			"#settings:updates",
			"#settings:decky",
			"#settings:maintenance",
			"#settings:worker",
			"#settings:support",
		],
	);
	await c.open();
	for (const label of ["Check automatically", "Tell me about Framey updates", "Tell me about module updates"])
		assert.ok(c.control(label), label);
	assert.ok(c.text().includes("Safe mode is on"));
	assert.equal(c.control("Updates"), undefined);
	assert.equal(c.K.pageNavigation?.()?.count > 1, false);
	c.env.window.Framey.pages.settings.render(c.box);
	await settle();
	assert.ok(c.control("Check automatically"));
	c.env.window.close();
});
test("Updates shows all actions together and confirms exact candidate before writing", async () => {
	const c = boot({ loader_update: { ok: true } });
	await c.open("updates");
	for (const label of ["Check for updates", "Update", "Release notes", "History", "Done", "Roll back"])
		assert.ok(c.control(label), label);
	await c.click("Update");
	assert.ok(c.text().includes("Available: v2.0.0"));
	assert.equal(c.requests.filter(request => request.method === "loader_update").length, 0);
	await c.click("Update now");
	assert.deepEqual(c.requests.find(request => request.method === "loader_update").arg, {
		confirm: false,
		candidate: { tag: candidate.tag, commit: candidate.commit },
	});
	assert.ok(c.text().includes("Updated, Framey is restarting"));
	assert.ok(c.control("Back"));
	c.env.window.close();
});
test("leaving a window route ignores its pending loader check", async () => {
	let release;
	const c = boot({
		loader_check: () =>
			new Promise(resolve => {
				release = resolve;
			}),
	});
	await c.open("updates");
	await c.click("Update");
	await c.open("preferences");
	const text = c.text();
	release({ loader: { state: "update" }, candidate });
	await settle();
	assert.equal(c.text(), text);
	assert.equal(c.control("Update now"), undefined);
	c.env.window.close();
});
test("window history keeps later server pages and filters every loaded version without carousel", async () => {
	const versions = Array.from({ length: 20 }, (_, i) => ({
		tag: "v" + i,
		version: "build " + i,
		eligible: true,
		published: "2026-10-01",
	}));
	const c = boot({
		ver_list: arg => ({ versions: arg.page === 1 ? versions.slice(0, 10) : versions.slice(10), more: arg.page === 1 }),
	});
	await c.open("updates");
	await c.click("History");
	assert.ok(c.control("Find a version"));
	await c.click("Show more versions");
	const input = c.control("Find a version");
	input.value = "build 19";
	input.dispatchEvent(new c.env.window.Event("input", { bubbles: true }));
	assert.ok(c.control("build 19"));
	await c.click("build 19");
	assert.ok(c.text().includes("2026-10-01"));
	assert.ok(c.control("Install this version"));
	assert.equal(c.control("Previous"), undefined);
	assert.equal(c.control("Next"), undefined);
	c.env.window.close();
});
test("Panel diagnostics are contextual and stale replies never replace Support", async () => {
	let release;
	const c = boot({
		panel_status: () =>
			new Promise(resolve => {
				release = resolve;
			}),
	});
	await c.open("support");
	await c.click("Panel status");
	assert.ok(c.text().includes("Loading"));
	await c.click("Back");
	const text = c.text();
	release({ mode: "window", layout: "window", available: true, active: true });
	await settle();
	assert.equal(c.text(), text);
	assert.ok(c.control("Panel status"));
	c.env.window.close();
});
test("window switches retain pending saves and unrelated preference values", async () => {
	let release;
	const c = boot({
		prefs_set: () =>
			new Promise(resolve => {
				release = resolve;
			}),
	});
	await c.open();
	await c.click("Tell me about Framey updates");
	const toggle = c.control("Tell me about Framey updates");
	assert.equal(toggle.getAttribute("aria-busy"), "true");
	toggle.click();
	await settle();
	assert.equal(c.requests.filter(request => request.method === "prefs_set").length, 1);
	release({ prefs: { notify_loader: false } });
	await settle();
	assert.equal(toggle.value, false);
	assert.equal(c.control("Tell me about module updates").value, false);
	c.env.window.close();
});
test("contextual notes retain every character, including newline-heavy text and long tails", async () => {
	const notes = "Header\n" + "\n".repeat(80) + "W".repeat(1000) + " FINAL NOTE";
	const c = boot({ notes: { notes } });
	await c.open("updates");
	await c.click("Check for updates");
	await c.click("Release notes");
	const position = c.control("Text position");
	const excerpt = () => [...c.box.querySelectorAll("*")].find(node => node.style.whiteSpace === "pre-wrap").textContent;
	let complete = "";
	for (let index = 1; index <= Number(position.max); index++) {
		position.value = String(index);
		position.dispatchEvent(new c.env.window.Event("input"));
		complete += excerpt();
	}
	assert.equal(complete, notes);
	await c.click("Back");
	assert.ok(c.control("History"));
	c.env.window.close();
});
test("a failed loader transport releases busy state and leaves check/report actions available", async () => {
	const c = boot({
		loader_check: () => {
			throw new Error("fixture check failed");
		},
	});
	await c.open("updates");
	await c.click("Check for updates");
	assert.ok(c.text().includes("fixture check failed"));
	assert.ok(c.control("Report to Framey"));
	c.replies.loader_check = { loader: { state: "current" } };
	await c.click("Check for updates");
	assert.ok(c.text().includes("Up to date"));
	c.env.window.close();
});
test("version history downgrade confirmation pins the selected tag before installation", async () => {
	const c = boot({
		ver_list: { versions: [{ version: "Old build", tag: "v0.9", eligible: true, downgrade: true }], more: false },
		ver_install: { ok: true },
	});
	await c.open("updates");
	await c.click("History");
	await c.click("Old build");
	await c.click("Go back to this version");
	assert.equal(c.requests.filter(request => request.method === "ver_install").length, 0);
	await c.click("Go back to this version");
	assert.deepEqual(c.requests.find(request => request.method === "ver_install").arg, {
		id: "_loader",
		tag: "v0.9",
		confirm: true,
	});
	c.env.window.close();
});
test("operation completion survives opening contextual progress details", async () => {
	let release;
	const c = boot({
		loader_update: () =>
			new Promise(resolve => {
				release = resolve;
			}),
	});
	await c.open("updates");
	await c.click("Update");
	await c.click("Update now");
	await c.click("Progress details");
	release({ ok: true });
	await settle();
	assert.ok(c.text().includes("Updated, Framey is restarting"));
	c.env.window.close();
});

test("Decky support remains off until the explicit download and enable succeeds", async () => {
	let release;
	const off = {
		installed: false,
		enabled: false,
		requested_enabled: false,
		version: null,
		previous_version: null,
		generation: 0,
		phase: "off",
		error: null,
	};
	const c = boot({
		decky_runtime_status: off,
		decky_runtime_enable: () =>
			new Promise(resolve => {
				release = resolve;
			}),
	});
	await c.open("decky");
	assert.equal(c.control("Enable Decky compatibility").value, false);
	assert.match(c.text(), /download/i);
	assert.equal(c.requests.filter(r => r.method === "decky_runtime_enable").length, 0);
	await c.click("Enable Decky compatibility");
	assert.deepEqual(c.requests.find(r => r.method === "decky_runtime_enable").arg, { enabled: true });
	const pending = c.control("Enable Decky compatibility");
	assert.equal(pending.value, false);
	assert.equal(pending.getAttribute("aria-busy"), "true");
	pending.click();
	await settle();
	assert.equal(c.requests.filter(r => r.method === "decky_runtime_enable").length, 1);
	release({
		...off,
		installed: true,
		enabled: true,
		requested_enabled: true,
		generation: 1,
		version: "1.0.0",
		phase: "enabled",
	});
	await settle();
	assert.equal(c.control("Enable Decky compatibility").value, true);
	assert.ok(c.text().includes("1.0.0"));
	assert.equal(c.requests.filter(r => r.method === "prefs_set").length, 0);
	c.env.window.close();
});
test("unpublished Decky download is an honest failure with the switch still off", async () => {
	const off = {
		installed: false,
		enabled: false,
		requested_enabled: false,
		version: null,
		previous_version: null,
		generation: 0,
		phase: "off",
		error: null,
	};
	const c = boot({
		decky_runtime_status: off,
		decky_runtime_enable: { ...off, phase: "error", error: "No runtime release has been published" },
	});
	await c.open("decky");
	await c.click("Enable Decky compatibility");
	assert.equal(c.control("Enable Decky compatibility").value, false);
	assert.match(c.text(), /No runtime release has been published/);
	assert.ok(c.control("Refresh status"));
	c.env.window.close();
});
test("Decky disable and removal use manager RPCs while retained plugin preferences are untouched", async () => {
	const on = {
		installed: true,
		enabled: true,
		requested_enabled: true,
		version: "1.0.0",
		previous_version: null,
		generation: 2,
		phase: "enabled",
		error: null,
	};
	const off = { ...on, enabled: false, requested_enabled: false, generation: 3, phase: "off" };
	const c = boot({
		decky_runtime_status: on,
		decky_runtime_enable: off,
		decky_runtime_remove: { ...off, installed: false, version: null, generation: 4 },
	});
	await c.open("decky");
	await c.click("Enable Decky compatibility");
	assert.deepEqual(c.requests.find(r => r.method === "decky_runtime_enable").arg, { enabled: false });
	assert.equal(c.control("Enable Decky compatibility").value, false);
	await c.click("Remove compatibility package");
	assert.deepEqual(c.requests.find(r => r.method === "decky_runtime_remove").arg, {});
	assert.equal(c.control("Remove compatibility package"), undefined);
	assert.equal(
		c.requests.some(r => ["prefs_set", "enable", "disable", "remove", "purge"].includes(r.method)),
		false,
	);
	c.env.window.close();
});
test("a late Decky enable reply cannot replace a different Settings window", async () => {
	let release;
	const off = {
		installed: false,
		enabled: false,
		requested_enabled: false,
		version: null,
		previous_version: null,
		generation: 0,
		phase: "off",
		error: null,
	};
	const c = boot({
		decky_runtime_status: off,
		decky_runtime_enable: () =>
			new Promise(resolve => {
				release = resolve;
			}),
	});
	await c.open("decky");
	await c.click("Enable Decky compatibility");
	await c.open("preferences");
	const text = c.text();
	release({ ...off, installed: true, enabled: true, requested_enabled: true, generation: 1, phase: "enabled" });
	await settle();
	assert.equal(c.text(), text);
	assert.ok(c.control("Check automatically"));
	c.env.window.close();
});

test("refreshing the same enabled runtime status never tears down its provider", async () => {
	const on = {
		installed: true,
		enabled: true,
		requested_enabled: true,
		version: "1.0.0",
		previous_version: null,
		generation: 4,
		phase: "enabled",
		error: null,
	};
	const c = boot({ decky_runtime_status: on });
	let invalidations = 0;
	c.K.optionalRuntime = {
		state: () => ({ generation: 4, active: true }),
		invalidate() {
			invalidations++;
		},
	};
	await c.open("decky");
	await c.click("Refresh status");
	assert.equal(invalidations, 0);
	c.env.window.close();
});
test("cancelling an in-flight download stays off when the old enable response arrives", async () => {
	let finishEnable;
	const off = {
		installed: false,
		enabled: false,
		requested_enabled: false,
		version: null,
		previous_version: null,
		generation: 0,
		phase: "absent",
		error: null,
	};
	const c = boot({
		decky_runtime_status: off,
		decky_runtime_enable: arg =>
			arg.enabled
				? new Promise(resolve => {
						finishEnable = resolve;
					})
				: { ...off, generation: 2 },
	});
	await c.open("decky");
	await c.click("Enable Decky compatibility");
	await c.click("Cancel download");
	assert.deepEqual(
		c.requests.filter(r => r.method === "decky_runtime_enable").map(r => r.arg),
		[{ enabled: true }, { enabled: false }],
	);
	assert.equal(c.control("Enable Decky compatibility").value, false);
	finishEnable({
		...off,
		installed: true,
		enabled: true,
		requested_enabled: true,
		version: "late",
		generation: 1,
		phase: "enabled",
	});
	await settle();
	assert.equal(c.control("Enable Decky compatibility").value, false);
	assert.equal(c.text().includes("late"), false);
	c.env.window.close();
});
test("unknown refresh results preserve last known runtime state and report uncertainty", async () => {
	const on = {
		installed: true,
		enabled: true,
		requested_enabled: true,
		version: "1",
		previous_version: null,
		generation: 4,
		phase: "enabled",
		error: null,
	};
	const c = boot({ decky_runtime_status: on });
	await c.open("decky");
	c.replies.decky_runtime_status = { error: "Status could not be read" };
	await c.click("Refresh status");
	assert.equal(c.control("Enable Decky compatibility").value, true);
	assert.match(c.text(), /Status could not be read/);
	c.env.window.close();
});
test("the direct Decky Settings route also opens the bounded compact controls", async () => {
	const off = {
		installed: false,
		enabled: false,
		requested_enabled: false,
		version: null,
		previous_version: null,
		generation: 0,
		phase: "absent",
		error: null,
	};
	const c = boot({ decky_runtime_status: off });
	c.K.singleWindow = () => false;
	c.K.panelLayout = () => "compact";
	await c.open("decky");
	assert.ok(c.control("Enable Decky compatibility"));
	assert.equal(c.requests.filter(r => r.method === "decky_runtime_status").length, 1);
	c.env.window.close();
});
