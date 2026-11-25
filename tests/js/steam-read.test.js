"use strict";
const assert = require("assert");
const { makeDom, TOK } = require("./harness");

const tests = [];
const t = (name, fn) => tests.push([name, fn]);
const fresh = options => makeDom(Object.assign({ loadFiles: ["fy-api.js"] }, options));
const json = value => JSON.parse(JSON.stringify(value));
const app = () => ({
	appId: 42,
	steamDeckCategory: 3,
	steamOsCategory: 0,
	steamMachineCategory: 1,
	steamFrameCategory: 2,
});
const focus = () => ({
	status: "present",
	focusedAppId: "synthetic-focus",
	focusedWindowId: 27,
	focusedOverlayAppId: null,
	focusedOverlayPid: 0,
});
const success = (operation, data) => ({
	ok: true,
	available: true,
	source: operation === "focus" ? "steam-focus-cache" : "steam-appstore-cache",
	data,
});
const emptyFocus = () => ({
	status: "empty",
	focusedAppId: null,
	focusedWindowId: 0,
	focusedOverlayAppId: "0",
	focusedOverlayPid: "",
});

t("steam.read sends only the scoped operation and copied args with immutable owner", async () => {
	const e = fresh();
	const api = e.plugin("owner");
	const args = { appId: 42 };
	assert.throws(() => {
		api.id = "foreign";
	}, TypeError);
	const pending = api.steam.read("app", args);
	args.appId = 99;
	const request = e.sent("steam_read")[0];
	assert.strictEqual(request.plugin, "_core");
	assert.strictEqual(request.t, "tok-owner-0000");
	assert.notStrictEqual(request.t, TOK);
	assert.deepStrictEqual(json(request.arg), { operation: "app", args: { appId: 42 }, pid: "owner" });
	const reply = success("app", { status: "present", app: app() });
	e.answer(request, reply);
	const result = await pending;
	assert.deepStrictEqual(json(result), reply);
	assert.notStrictEqual(result, reply);
	assert.notStrictEqual(result.data, reply.data);
	assert.notStrictEqual(result.data.app, reply.data.app);
	reply.data.app.steamDeckCategory = 99;
	assert.strictEqual(result.data.app.steamDeckCategory, 3);
});

t("steam.read preserves visible-library, lookup and window-focus success snapshots", async () => {
	const partial = app();
	partial.steamOsCategory = null;
	for (const [operation, args, data] of [
		["visibleApps", undefined, { status: "present", apps: [app()], visibleCount: 1, truncated: false }],
		["visibleApps", {}, { status: "empty", apps: [], visibleCount: 0, truncated: false }],
		["visibleApps", {}, { status: "partial", apps: [partial], visibleCount: 1, truncated: false }],
		["app", { appId: 42 }, { status: "partial", app: partial }],
		["app", { appId: 42 }, { status: "not_found", app: null }],
		["focus", undefined, focus()],
		["focus", {}, emptyFocus()],
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read(operation, args);
		const request = e.sent("steam_read")[0];
		assert.deepStrictEqual(json(request.arg.args), args || {});
		const reply = success(operation, data);
		e.answer(request, reply);
		assert.deepStrictEqual(json(await pending), reply);
	}
});

t("steam.read rejects unknown operations and malformed args before dispatch", async () => {
	for (const [operation, args, code] of [
		["evaluate", {}, "unknown_operation"],
		["runningApps", {}, "unknown_operation"],
		["__proto__", {}, "unknown_operation"],
		[null, {}, "unknown_operation"],
		[42, {}, "unknown_operation"],
		["focus", null, "invalid_arguments"],
		["focus", [], "invalid_arguments"],
		["focus", new Date(0), "invalid_arguments"],
		["focus", { pid: "foreign" }, "invalid_arguments"],
		["visibleApps", { expression: "window.appStore" }, "invalid_arguments"],
		["app", {}, "invalid_arguments"],
		["app", { appId: 42, target: "foreign" }, "invalid_arguments"],
		...[true, 0, -1, 1.5, "42", null, 4294967296].map(appId => ["app", { appId }, "invalid_arguments"]),
	]) {
		const e = fresh();
		await assert.rejects(e.plugin("a").steam.read(operation, args), error => error.code === code);
		assert.strictEqual(e.sent("steam_read").length, 0);
	}
});

t("steam.read preserves the largest validated lookup identity", async () => {
	const e = fresh();
	const pending = e.plugin("a").steam.read("app", { appId: 4294967295 });
	const record = app();
	record.appId = 4294967295;
	e.answer(e.sent("steam_read")[0], success("app", { status: "present", app: record }));
	assert.strictEqual((await pending).data.app.appId, 4294967295);
});

t("steam.read preserves a bounded truncated library snapshot by value", async () => {
	const e = fresh();
	const pending = e.plugin("a").steam.read("visibleApps");
	const records = Array.from({ length: 4096 }, (_, index) => ({ ...app(), appId: index + 1 }));
	e.answer(
		e.sent("steam_read")[0],
		success("visibleApps", {
			status: "present",
			apps: records,
			visibleCount: 4097,
			truncated: true,
		}),
	);
	const result = await pending;
	assert.strictEqual(result.data.apps.length, 4096);
	assert.strictEqual(result.data.visibleCount, 4097);
	assert.strictEqual(result.data.truncated, true);
	assert.notStrictEqual(result.data.apps, records);
	assert.notStrictEqual(result.data.apps[0], records[0]);
});

t("steam.read rejects failure envelopes with sanitized provider or host codes", async () => {
	for (const code of [
		"not_allowed",
		"stale_caller",
		"busy",
		"rate_limited",
		"service_closed",
		"target_unavailable",
		"capability_unavailable",
		"provider_timeout",
		"stale_context",
		"provider_disposed",
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("focus");
		e.answer(e.sent("steam_read")[0], { ok: false, available: false, code, detail: "PRIVATE_ENDPOINT" });
		await assert.rejects(pending, error => error.code === code && !error.message.includes("PRIVATE_ENDPOINT"));
		e.clock.tick(120000);
		assert.strictEqual(e.sent("steam_read").length, 1);
	}
});

t("steam.read never exposes unknown native or transport error text", async () => {
	for (const reply of [
		{ ok: false, available: false, code: "PRIVATE_URL_OR_ID" },
		{ error: "PRIVATE_URL_OR_ID", code: "not_allowed" },
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("focus");
		e.answer(e.sent("steam_read")[0], reply);
		await assert.rejects(
			pending,
			error => /^[a-z_]+$/.test(error.code) && !error.message.includes("PRIVATE_URL_OR_ID"),
		);
	}
	const e = fresh();
	e.onCall(() => {
		throw new Error("PRIVATE_TRANSPORT_TEXT");
	});
	await assert.rejects(
		e.plugin("a").steam.read("focus"),
		error => error.code === "provider_read_failed" && !error.message.includes("PRIVATE_TRANSPORT_TEXT"),
	);
	assert.strictEqual(e.window.Framey.diag().calls, 0);
});

t("steam.read rejects an unanswered native request without retry or leaked pending call", async () => {
	const e = fresh();
	const pending = e.plugin("a").steam.read("focus");
	e.clock.tick(120000);
	await assert.rejects(pending, error => error.code === "provider_read_failed");
	assert.strictEqual(e.sent("steam_read").length, 1);
	assert.strictEqual(e.window.Framey.diag().calls, 0);
});

t("steam.read rejects malformed or inconsistent success envelopes", async () => {
	for (const reply of [
		null,
		[],
		{},
		{ ok: true },
		{ ...success("focus", focus()), ok: 1 },
		{ ...success("focus", focus()), available: false },
		{ ...success("focus", focus()), source: "steam-appstore-cache" },
		{ ...success("focus", focus()), source: "unverified" },
		{ ...success("focus", focus()), code: "not_allowed" },
		{ ...success("focus", focus()), error: "" },
		{ ...success("focus", focus()), extra: "PRIVATE_METADATA" },
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("focus");
		e.answer(e.sent("steam_read")[0], reply);
		await assert.rejects(pending, error => error.code === "invalid_provider_result");
	}
});

t("steam.read validates raw app records, lookup identity and partial status", async () => {
	const partial = app();
	partial.steamDeckCategory = null;
	for (const data of [
		{ status: "present", app: { ...app(), appId: 43 } },
		{ status: "present", app: { ...app(), name: "PRIVATE_NAME" } },
		{ status: "present", app: { ...app(), steamDeckCategory: -1 } },
		{ status: "present", app: { ...app(), steamDeckCategory: 1.5 } },
		{ status: "present", app: { ...app(), steamDeckCategory: 2147483648 } },
		{ status: "present", app: { ...app(), steamDeckCategory: true } },
		{ status: "present", app: partial },
		{ status: "partial", app: app() },
		{ status: "present", app: null },
		{ status: "not_found", app: app() },
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("app", { appId: 42 });
		e.answer(e.sent("steam_read")[0], success("app", data));
		await assert.rejects(pending, error => error.code === "invalid_provider_result");
	}
});

t("steam.read rejects duplicate, mismatched or oversized visible snapshots", async () => {
	for (const data of [
		{ status: "present", apps: [app(), app()], visibleCount: 2, truncated: false },
		{ status: "present", apps: Array(1), visibleCount: 1, truncated: false },
		{ status: "present", apps: [app()], visibleCount: 2, truncated: false },
		{ status: "empty", apps: [app()], visibleCount: 1, truncated: false },
		{ status: "present", apps: [app()], visibleCount: 1, truncated: true },
		{ status: "empty", apps: [], visibleCount: -1, truncated: false },
		{ status: "empty", apps: [], visibleCount: false, truncated: false },
		{ status: "present", apps: [app()], visibleCount: 4097, truncated: true },
		{ status: "present", apps: Array(4097).fill(app()), visibleCount: 4097, truncated: true },
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("visibleApps");
		e.answer(e.sent("steam_read")[0], success("visibleApps", data));
		await assert.rejects(pending, error => error.code === "invalid_provider_result");
	}
});

t("steam.read validates focus scalars and derives no running-app or scene identity", async () => {
	for (const data of [
		{ ...focus(), focusedAppId: true },
		{ ...focus(), focusedAppId: {} },
		{ ...focus(), focusedAppId: [] },
		{ ...focus(), focusedAppId: Infinity },
		{ ...focus(), focusedAppId: "x".repeat(257) },
		{ ...focus(), running: true },
		{ ...focus(), status: "empty" },
		{ ...emptyFocus(), status: "present" },
	]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("focus");
		e.answer(e.sent("steam_read")[0], success("focus", data));
		await assert.rejects(pending, error => error.code === "invalid_provider_result");
	}
});

t("steam.read never reads Steam/page globals and keeps requests separate by plugin", async () => {
	const e = fresh({
		before: w => {
			for (const name of ["SteamClient", "appStore", "FocusedAppWindowStore"]) {
				Object.defineProperty(w, name, {
					get() {
						throw new Error("page global read attempted");
					},
				});
			}
		},
	});
	const first = e.plugin("a").steam.read("focus");
	const second = e.plugin("b").steam.read("focus");
	const requests = e.sent("steam_read");
	assert.deepStrictEqual(
		requests.map(r => [r.t, r.arg.pid]),
		[
			["tok-a-0000", "a"],
			["tok-b-0000", "b"],
		],
	);
	requests.forEach(r => e.answer(r, success("focus", emptyFocus())));
	await Promise.all([first, second]);
	assert.ok(e.calls.every(r => r.method === "steam_read"));
});

t("steam.read rejects disposed handles before sending and pending late replies", async () => {
	const e = fresh();
	const api = e.plugin("a");
	const pending = api.steam.read("focus");
	const request = e.sent("steam_read")[0];
	e.window.Framey.dispose("a", TOK);
	e.answer(request, success("focus", focus()));
	await assert.rejects(pending, error => error.code === "disposed");
	await assert.rejects(api.steam.read("focus"), error => error.code === "disposed");
	assert.strictEqual(e.sent("steam_read").length, 1);
	assert.strictEqual(e.window.Framey.diag().calls, 0);
});

t("steam.read rejects disposal or replacement after answer before delivery", async () => {
	for (const replace of [false, true]) {
		const e = fresh();
		const pending = e.plugin("a").steam.read("focus");
		e.answer(e.sent("steam_read")[0], success("focus", focus()));
		if (replace) e.plugin("a");
		else e.window.Framey.dispose("a", TOK);
		await assert.rejects(pending, error => error.code === "disposed");
	}
});

(async () => {
	let failures = 0;
	for (const [name, fn] of tests) {
		let deadline;
		try {
			await Promise.race([
				fn(),
				new Promise((_, reject) => {
					deadline = setTimeout(() => reject(new Error("test did not settle")), 1000);
				}),
			]);
			console.log("ok   " + name);
		} catch (error) {
			failures++;
			console.log(
				"FAIL " +
					name +
					"\n     " +
					String(error.stack || error)
						.split("\n")
						.slice(0, 6)
						.join("\n     "),
			);
		} finally {
			clearTimeout(deadline);
		}
	}
	console.log("\n" + (tests.length - failures) + " passed, " + failures + " failed, " + tests.length + " total");
	process.exitCode = failures ? 1 : 0;
})();
