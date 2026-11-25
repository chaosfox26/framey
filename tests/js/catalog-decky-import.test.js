"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");
const optionalStore = require("./optional-store-fixture.cjs");
const catalogDriver = require("./catalog-fixture-driver.test");
const FILES = ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"];
const DEFER = Symbol("deferred");
const settle = async () => {
	for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
};
const clone = value => JSON.parse(JSON.stringify(value));
const VERSIONS = [
	{ name: "2.0.0", identity: "b".repeat(64), hash: "c".repeat(64), inspectable: true },
	{ name: "1.5.0", identity: "d".repeat(64), hash: "e".repeat(64), inspectable: true },
];
const entry = () => ({
	id: "decky-41",
	name: "Palette helper",
	version: "2.0.0",
	identity: "a".repeat(64),
	format: "decky",
	kind: "decky",
	read_only: true,
	eligible: false,
	installed: false,
	versions: clone(VERSIONS),
	description: "Original Decky package",
	source: { url: "https://plugins.deckbrew.xyz/plugins" },
	compat: { state: "incompatible", msg: "Needs a Framey port" },
});
const source = id => ({
	id,
	label: id === "decky_testing" ? "Decky Store Testing" : id === "stable" ? "Stable" : "Decky Store",
	url: "https://example.test/" + id,
});
const boot = ({
	cap = { profile: 1, import: true },
	importReply = { ok: "decky-41" },
	detailsExtra = {},
	layout = "compact",
} = {}) => {
	const calls = [];
	const replies = {
		decky_runtime_status: optionalStore.status,
		catalog_sources: () => ({
			source: source("stable"),
			sources: [source("stable")],
			decky_sources: [source("decky_stable"), source("decky_testing")],
		}),
		catalog_list: arg => ({
			source: source(arg.source),
			rows: [entry()],
			total: 1,
			offset: 0,
			limit: 50,
			origin: "downloaded",
			stale: false,
		}),
		catalog_details: arg => ({
			source: source(arg.source),
			entry: entry(),
			origin: "downloaded",
			stale: false,
			...detailsExtra,
		}),
		catalog_import: importReply,
	};
	const env = makeDom({
		before(w) {
			w.FY_RUNTIME = {
				panel_layout: layout,
				panel_navigation: "launcher",
				version: "1.1.0",
				api: 1,
				platform: "linux-aarch64",
				lang: "en",
				trusted_input: "off",
			};
			if (cap !== null) w.FY_RUNTIME.decky_compat = cap;
			w.fyCall = raw => {
				const request = JSON.parse(raw);
				calls.push(request);
				if (!request.id) return;
				const reply = replies[request.method];
				const value = typeof reply === "function" ? reply(request.arg) : reply;
				if (value === DEFER) return;
				Promise.resolve(value ?? { error: "Unhandled " + request.method }).then(result =>
					w.Framey._reply(request.gen, request.id, clone(result)),
				);
			};
		},
		loadFiles: FILES,
	});
	optionalStore.install(env);
	const box = env.document.createElement("div");
	env.document.body.append(box);
	const driver = catalogDriver(env, () => box);
	const find = label => {
		const fallback = driver.controls().find(node => (node.getAttribute("aria-label") || node.textContent) === label);
		const node = driver.label(label) || fallback;
		assert.ok(
			node,
			"Missing " +
				label +
				" among " +
				driver
					.controls()
					.map(n => n.getAttribute("aria-label"))
					.join(" | "),
		);
		return node;
	};
	const click = async label => {
		find(label).click();
		await settle();
	};
	const set = async (label, value) => {
		const node = find(label);
		node.value = value;
		node.dispatchEvent(new env.window.Event("change", { bubbles: true }));
		await settle();
	};
	const sent = method => calls.filter(c => c.method === method);
	return {
		env,
		box,
		driver,
		replies,
		find,
		click,
		set,
		sent,
		text: driver.text,
		has: label => driver.controls().some(n => n.getAttribute("aria-label") === label),
		async open() {
			env.window.Framey.pages.decky_store.render(box, {});
			await settle();
		},
		async details() {
			await click("Details: Palette helper");
		},
		async answer(request, value) {
			env.answer(request, clone(value));
			await settle();
		},
	};
};
const tests = [];
const test = (name, run) => tests.push([name, run]);

for (const cap of [null, { profile: 2, import: true }, { profile: 1, import: false }])
	test("unavailable import capability preserves inspection-only behavior " + JSON.stringify(cap), async () => {
		const c = boot({ cap });
		await c.open();
		await c.details();
		assert.ok(!c.has("Review experimental import"));
		assert.match(c.text(), /cannot be installed in Framey/);
		assert.equal(c.sent("catalog_import").length, 0);
	});

// Importing another version, installing before confirmation, or losing the partial-profile disclosure breaks this test.
test("review cancel and confirm bind the exact selected original package", async () => {
	const c = boot();
	await c.open();
	await c.details();
	await c.set("Version", VERSIONS[1].identity);
	await c.click("Review experimental import");
	assert.match(c.text(), /1\.5\.0/);
	assert.ok(c.text().includes(VERSIONS[1].hash));
	assert.match(c.text(), /partial|does not mean.*Steam|not.*full.*Steam/i);
	assert.equal(c.sent("catalog_import").length, 0);
	await c.click("Cancel");
	assert.equal(c.find("Version").value, VERSIONS[1].identity);
	await c.click("Review experimental import");
	await c.click("Import experimental profile");
	assert.deepEqual(
		c.sent("catalog_import").map(c => c.arg),
		[
			{
				source: "decky_stable",
				id: "decky-41",
				identity: "a".repeat(64),
				version_identity: VERSIONS[1].identity,
				profile: 1,
				confirm: true,
			},
		],
	);
	assert.equal(c.sent("catalog_install").length, 0);
	assert.match(c.text(), /Import complete|Imported experimental profile/);
	assert.match(c.text(), /partial|does not mean.*Steam|not.*full.*Steam/i);
});

// Retained controls from a previous review cannot authorize a newly selected archive.
test("changing the selected version invalidates old review and confirm controls", async () => {
	const c = boot();
	await c.open();
	await c.details();
	const oldReview = c.find("Review experimental import");
	await c.click("Review experimental import");
	const oldConfirm = c.find("Import experimental profile");
	await c.click("Cancel");
	await c.set("Version", VERSIONS[1].identity);
	oldReview.click();
	oldConfirm.click();
	await settle();
	assert.equal(c.sent("catalog_import").length, 0);
	assert.equal(c.find("Version").value, VERSIONS[1].identity);
	await c.click("Review experimental import");
	await c.click("Import experimental profile");
	assert.equal(c.sent("catalog_import")[0].arg.version_identity, VERSIONS[1].identity);
});

for (const outcome of [{ ok: "decky-41" }, { error: "Old import failed", code: "import_old" }])
	test("completion for another version cannot replace current selection " + JSON.stringify(outcome), async () => {
		const c = boot({ importReply: DEFER });
		await c.open();
		await c.details();
		await c.click("Review experimental import");
		await c.click("Import experimental profile");
		const request = c.sent("catalog_import")[0];
		await c.set("Version", VERSIONS[1].identity);
		const detailsCalls = c.sent("catalog_details").length;
		await c.answer(request, outcome);
		assert.equal(c.find("Version").value, VERSIONS[1].identity);
		assert.equal(c.sent("catalog_details").length, detailsCalls);
		assert.ok(!/Import complete|Imported experimental profile|Old import failed/.test(c.text()));
		assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "false");
	});

test("source change ignores a pending import failure and invalidates its confirmation", async () => {
	const c = boot({ importReply: DEFER });
	await c.open();
	await c.details();
	await c.click("Review experimental import");
	const oldConfirm = c.find("Import experimental profile");
	await c.click("Import experimental profile");
	const request = c.sent("catalog_import")[0];
	await c.click("Back");
	await c.click("Filters");
	await c.set("Source", "decky_testing");
	await c.answer(request, { error: "Stale source import failure", code: "stale_import" });
	oldConfirm.click();
	await settle();
	assert.equal(c.sent("catalog_import").length, 1);
	assert.ok(!c.text().includes("Stale source import failure"));
	await c.click("Details: Palette helper");
	await c.click("Review experimental import");
	await c.click("Import experimental profile");
	assert.equal(c.sent("catalog_import")[1].arg.source, "decky_testing");
});

for (const detailsExtra of [
	{ stale: true },
	{ entry: { ...entry(), identity: "" } },
	{ entry: { ...entry(), versions: [{ ...VERSIONS[0], inspectable: false }] } },
])
	test("unreviewable source or version prevents import " + JSON.stringify(detailsExtra).slice(0, 60), async () => {
		const c = boot({ detailsExtra });
		await c.open();
		await c.details();
		if (c.has("Review experimental import")) {
			assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "true");
			await c.click("Review experimental import");
		}
		assert.equal(c.sent("catalog_import").length, 0);
	});

test("window confirmation visibly identifies the chosen version rather than the latest listing", async () => {
	const c = boot({ layout: "window" });
	await c.open();
	await c.details();
	await c.set("Version", VERSIONS[1].identity);
	await c.click("Review experimental import");
	assert.match(c.box.textContent, /Selected version 1\.5\.0/);
	assert.ok(!c.box.textContent.includes("v2.0.0"));
	await c.click("Read details");
	assert.ok(c.text().includes(VERSIONS[1].hash));
});

for (const reply of [{ error: "Hash changed", code: "hash_mismatch" }, { ok: "another-package" }])
	test("failed or unconfirmed imports never show successful installation " + JSON.stringify(reply), async () => {
		const c = boot({ importReply: reply });
		await c.open();
		await c.details();
		await c.click("Review experimental import");
		await c.click("Import experimental profile");
		assert.match(c.text(), /Import failed/);
		assert.ok(!c.text().includes("Import complete"));
		assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "false");
	});

test("capable hosts explain static Decky inspection without contradicting experimental import", async () => {
	const c = boot();
	c.replies.catalog_assess = arg => ({
		id: arg.id,
		format: "decky",
		read_only: true,
		version: VERSIONS[0].name,
		hash: VERSIONS[0].hash,
		assessment: {
			state: "port_required",
			label: "Needs a Framey port",
			has_frontend: true,
			has_backend: true,
			verified_sha256: true,
			not_executed: true,
			reasons: ["Built for Decky’s interface; it needs a Framey version before it can run here."],
			warnings: [],
			native_binaries: [],
			architecture: "unknown",
		},
	});
	await c.open();
	await c.details();
	await c.click("Inspect compatibility");
	assert.ok(c.has("Review experimental import"));
	assert.match(c.text(), /experimental profile support.*unverified/i);
	assert.ok(
		!/needs a Framey version before it can run|cannot be installed in Framey|cannot install or run/.test(c.text()),
	);
});

// A background import outlives a render; the reopened exact selection must share its pending state and receipt.
for (const outcome of [{ error: "Backend start failed", code: "load_failed" }, { ok: "decky-41" }])
	test(
		"reopened same-version details retain a pending import and receive completion " + JSON.stringify(outcome),
		async () => {
			const c = boot({ importReply: DEFER });
			await c.open();
			await c.details();
			await c.click("Review experimental import");
			await c.click("Import experimental profile");
			const request = c.sent("catalog_import")[0];
			await c.open();
			await c.details();
			assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "true");
			assert.match(c.text(), /Importing/);
			await c.click("Review experimental import");
			assert.equal(c.sent("catalog_import").length, 1);
			const sentinel = c.env.document.createElement("button");
			sentinel.textContent = "Other task";
			c.env.document.body.append(sentinel);
			sentinel.focus();
			const detailCalls = c.sent("catalog_details").length;
			await c.answer(request, outcome);
			assert.equal(c.env.document.activeElement, sentinel, "background completion must not steal external focus");
			assert.equal(c.sent("catalog_details").length, detailCalls, "completion must not reopen or refetch details");
			if (outcome.error) {
				assert.match(c.text(), /Import failed/);
				assert.ok(c.text().includes("Backend start failed"));
				assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "false");
			} else {
				assert.match(c.text(), /Import complete/);
				assert.match(c.text(), /partial/i);
				assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "true");
			}
			assert.equal(c.sent("catalog_import").length, 1);
		},
	);

// Source, listing identity, entry and version all participate in the persisted job key across renders.
for (const change of ["source", "listing", "entry", "version"])
	test("a completion from a prior render cannot repaint a different " + change, async () => {
		const c = boot({ importReply: DEFER });
		await c.open();
		await c.details();
		await c.click("Review experimental import");
		await c.click("Import experimental profile");
		const request = c.sent("catalog_import")[0];
		if (change === "listing" || change === "entry") {
			const current = {
				...entry(),
				identity: "f".repeat(64),
				...(change === "entry" ? { id: "decky-42", name: "Other helper" } : {}),
			};
			c.replies.catalog_list = arg => ({
				source: source(arg.source),
				rows: [current],
				total: 1,
				origin: "downloaded",
				stale: false,
			});
			c.replies.catalog_details = arg => ({
				source: source(arg.source),
				entry: current,
				origin: "downloaded",
				stale: false,
			});
		}
		await c.open();
		if (change === "source") {
			await c.click("Filters");
			await c.set("Source", "decky_testing");
		}
		await c.click(change === "entry" ? "Details: Other helper" : "Details: Palette helper");
		if (change === "version") await c.set("Version", VERSIONS[1].identity);
		const focused = c.find("Version");
		focused.focus();
		const html = c.box.innerHTML,
			detailCalls = c.sent("catalog_details").length;
		await c.answer(request, { error: "Prior render import failed", code: "load_failed" });
		assert.equal(c.box.innerHTML, html);
		assert.equal(c.env.document.activeElement, focused);
		assert.equal(c.sent("catalog_details").length, detailCalls);
		assert.ok(!c.text().includes("Prior render import failed"));
		assert.equal(c.find("Review experimental import").getAttribute("aria-disabled"), "false");
	});

// Completed receipts may be evicted to bound module memory; live imports must survive receipt churn and rerenders.
test("persisted import receipts stay bounded without evicting pending jobs or accumulating render listeners", async () => {
	const c = boot({ layout: "window", importReply: arg => (arg.identity === "a".repeat(64) ? DEFER : { ok: arg.id }) });
	const baseline = c.env.window.Framey.diag().subs;
	let current = entry();
	c.replies.catalog_list = arg => ({
		source: source(arg.source),
		rows: [current],
		total: 1,
		origin: "downloaded",
		stale: false,
	});
	c.replies.catalog_details = arg => ({
		source: source(arg.source),
		entry: current,
		origin: "downloaded",
		stale: false,
	});
	await c.open();
	await c.details();
	await c.click("Review experimental import");
	await c.click("Import experimental profile");
	const pending = c.sent("catalog_import")[0];
	for (let index = 1; index <= 129; index++) {
		current = { ...entry(), identity: index.toString(16).padStart(64, "0") };
		await c.open();
		await c.details();
		await c.click("Review experimental import");
		await c.click("Import experimental profile");
	}
	current = { ...entry(), identity: "1".padStart(64, "0") };
	await c.open();
	await c.details();
	assert.equal(
		c.find("Review experimental import").getAttribute("aria-disabled"),
		"false",
		"old completed receipt should be evicted",
	);
	current = { ...entry(), identity: (129).toString(16).padStart(64, "0") };
	await c.open();
	await c.details();
	assert.equal(
		c.find("Review experimental import").getAttribute("aria-disabled"),
		"true",
		"latest receipt should survive rerender",
	);
	current = entry();
	await c.open();
	await c.details();
	assert.equal(
		c.find("Review experimental import").getAttribute("aria-disabled"),
		"true",
		"pending import must not be evicted",
	);
	assert.equal(c.env.window.Framey.diag().subs, baseline);
	await c.answer(pending, { error: "Pending job finished", code: "load_failed" });
	await c.click("Read details");
	await c.set("Details to read", "Experimental import");
	assert.ok(c.text().includes("Pending job finished"));
	await c.click("Done reading");
	c.env.window.Framey.pages.decky_store.leave();
	assert.equal(c.box.onkeydown, null);
	assert.equal(c.env.window.Framey.diag().subs, baseline);
});

(async () => {
	let failed = 0;
	for (const [name, run] of tests) {
		try {
			await run();
			console.log("ok   " + name);
		} catch (error) {
			failed++;
			console.error("FAIL " + name + "\n" + error.stack);
		}
	}
	console.log(tests.length - failed + " passed, " + failed + " failed, " + tests.length + " total");
	process.exit(failed ? 1 : 0);
})();
