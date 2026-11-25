"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");
const optionalStore = require("./optional-store-fixture.cjs");
const settle = async () => {
	for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));
};
const description = "A complete description\r\n".repeat(80) + "Final line";
const entry = (id, extra = {}) => ({
	id,
	name: "Module " + id,
	identity: id,
	version: "1",
	description,
	eligible: true,
	platform_declared: true,
	compatibility: { status: "compatible", basis: "declared_requirements", reason: "Declared requirements match." },
	source: { repository: "https://example.com/" + id, commit: "a".repeat(40) },
	permissions: ["filesystem", "network"],
	...extra,
});
const boot = (decky = false, replies = {}) => {
	const calls = [],
		reports = [];
	const env = makeDom({
		before(w) {
			w.FY_RUNTIME = { panel_navigation: "launcher", panel_layout: "window", trusted_input: "off" };
			w.fyCall = encoded => {
				const c = JSON.parse(encoded);
				calls.push(c);
				if (!c.id) return;
				const chosen = entry(
					c.arg.id || "first",
					decky
						? {
								format: "decky",
								read_only: true,
								versions: [{ name: "1", identity: "version-one", hash: "b".repeat(64) }],
							}
						: {},
				);
				let r = {};
				if (c.method === "decky_runtime_status") r = optionalStore.status;
				if (c.method === "catalog_sources") r = { source: { id: "stable" } };
				if (c.method === "catalog_list") r = { rows: [chosen], total: c.arg.query ? 1 : 75, origin: "downloaded" };
				if (c.method === "catalog_details") r = { entry: chosen, origin: "downloaded" };
				if (c.method === "catalog_install") r = { ok: c.arg.id };
				if (c.method === "catalog_assess")
					r = {
						id: c.arg.id,
						format: "decky",
						read_only: true,
						version: "1",
						hash: "b".repeat(64),
						assessment: { not_executed: true, verified_sha256: true, architecture: "x86_64", reasons: [description] },
					};
				if (replies[c.method]) r = replies[c.method](c.arg);
				Promise.resolve(r).then(result => w.Framey._reply(c.gen, c.id, result));
			};
		},
		loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"],
	});
	if (decky) optionalStore.install(env);
	const w = env.window,
		box = env.document.createElement("div");
	env.document.body.append(box);
	const K = w.__S.K;
	let navCalls = 0;
	const set = K.setPageNavigation;
	K.setPageNavigation = o => {
		navCalls++;
		return set(o);
	};
	K.report = r => {
		reports.push(r);
		return replies.report ? replies.report(r) : Promise.resolve();
	};
	const find = label => [...box.querySelectorAll("[aria-label]")].find(n => n.getAttribute("aria-label") === label);
	const click = async label => {
		assert.ok(find(label), "Missing " + label);
		find(label).click();
		await settle();
	};
	return {
		w,
		box,
		calls,
		reports,
		find,
		click,
		get navCalls() {
			return navCalls;
		},
		async open() {
			w.Framey.pages[decky ? "decky_store" : "store"].render(box);
			await settle();
		},
	};
};
(async () => {
	const c = boot();
	await c.open();
	assert.equal(c.navCalls, 0, "window catalog must not publish paginated page navigation");
	assert.ok(c.find("Search modules") && c.find("Search") && c.find("Filters") && c.find("Refresh"));
	assert.ok(c.find("Details: Module first"), "matches open directly from the search screen");
	assert.equal(c.box.querySelectorAll("select").length, 0, "main results must not use a scrolling native chooser");
	assert.match(c.box.textContent, /75/);
	assert.match(c.box.textContent, /narrow|refine/i);
	assert.ok(!c.find("Next results") && !c.find("Previous results"));
	c.find("Search modules").value = "last-module";
	c.find("Search modules").dispatchEvent(new c.w.Event("input", { bubbles: true }));
	await c.click("Search");
	assert.equal(c.calls.filter(x => x.method === "catalog_list").at(-1).arg.query, "last-module");
	assert.equal(c.calls.filter(x => x.method === "catalog_list").at(-1).arg.limit, 50);
	await c.click("Details: Module first");
	assert.ok(c.find("Install Module first") && c.find("Read details") && c.find("Back"));
	await c.click("Read details");
	assert.ok(c.find("Details to read"));
	c.find("Details to read").value = "Description";
	c.find("Details to read").dispatchEvent(new c.w.Event("change", { bubbles: true }));
	assert.equal(c.find("Full details").textContent, description, "contextual reader preserves complete long text");
	await c.click("Done reading");
	await c.click("Report to Framey");
	assert.equal(c.reports[0].plugin.name, "Module first");
	await c.click("Install Module first");
	assert.equal(c.calls.filter(x => x.method === "catalog_install").length, 0);
	assert.ok(c.find("Install") && c.find("Cancel"));
	await c.click("Install");
	assert.deepEqual(c.calls.filter(x => x.method === "catalog_install").at(-1).arg, {
		id: "first",
		identity: "first",
		source: "stable",
		confirm: true,
	});
	assert.equal(c.navCalls, 0);
	c.w.close();
	const d = boot(true);
	await d.open();
	await d.click("Details: Module first");
	assert.ok(d.find("Version") && d.find("Inspect compatibility"));
	await d.click("Inspect compatibility");
	assert.equal(d.calls.filter(x => x.method === "catalog_install").length, 0);
	await d.click("Read details");
	assert.ok([...d.find("Details to read").options].some(o => o.value === "Archive inspection"));
	assert.equal(d.navCalls, 0);
	d.w.close();
	let firstSearch, secondSearch;
	const late = boot(false, {
		catalog_list: arg =>
			arg.query === "older"
				? new Promise(resolve => {
						firstSearch = resolve;
					})
				: arg.query === "newer"
					? new Promise(resolve => {
							secondSearch = resolve;
						})
					: { rows: [entry("first")], total: 1, origin: "downloaded" },
	});
	await late.open();
	const search = async query => {
		late.find("Search modules").value = query;
		late.find("Search modules").dispatchEvent(new late.w.Event("input", { bubbles: true }));
		await late.click("Search");
	};
	await search("older");
	await search("newer");
	secondSearch({ rows: [entry("newer")], total: 1, origin: "downloaded" });
	await settle();
	firstSearch({ rows: [entry("older")], total: 1, origin: "downloaded" });
	await settle();
	assert.ok(late.find("Details: Module newer"), "stale search replies cannot replace newer results");
	assert.ok(!late.find("Details: Module older"));
	late.w.close();
	let finishFirst;
	const selection = boot(false, {
		catalog_list: () => ({ rows: [entry("first"), entry("second")], total: 2, origin: "downloaded" }),
		catalog_details: arg =>
			arg.id === "first"
				? new Promise(resolve => {
						finishFirst = resolve;
					})
				: { entry: entry(arg.id), origin: "downloaded" },
	});
	await selection.open();
	await selection.click("Details: Module first");
	await selection.click("Back");
	await selection.click("Details: Module second");
	finishFirst({ entry: entry("first"), origin: "downloaded" });
	await settle();
	assert.ok(selection.find("Install Module second"));
	assert.ok(!selection.find("Install Module first"));
	await selection.click("Report to Framey");
	assert.equal(selection.reports[0].plugin.name, "Module second");
	selection.w.close();
	const ineligible = boot(false, {
		catalog_details: () => ({
			entry: entry("first", { eligible: false, reason: "Unsupported architecture" }),
			origin: "downloaded",
		}),
	});
	await ineligible.open();
	await ineligible.click("Details: Module first");
	assert.equal(ineligible.find("Install Module first").getAttribute("aria-disabled"), "true");
	await ineligible.click("Install Module first");
	assert.ok(!ineligible.find("Install"));
	assert.equal(ineligible.calls.filter(x => x.method === "catalog_install").length, 0);
	ineligible.w.close();
	const failedReport = boot(false, { report: () => Promise.reject(new Error("preview failed")) });
	await failedReport.open();
	await failedReport.click("Details: Module first");
	await failedReport.click("Report to Framey");
	assert.match(failedReport.box.textContent, /attention/);
	await failedReport.click("Read details");
	assert.ok([...failedReport.find("Details to read").options].some(option => option.value === "Report unavailable"));
	failedReport.w.close();
	let savedSource;
	const draft = boot(false, {
		catalog_sources: arg =>
			arg.source === "custom"
				? new Promise(resolve => {
						savedSource = resolve;
					})
				: { source: { id: "stable" } },
	});
	await draft.open();
	await draft.click("Filters");
	draft.find("Source").value = "custom";
	draft.find("Source").dispatchEvent(new draft.w.Event("change", { bubbles: true }));
	await settle();
	const editURL = value => {
		draft.find("Custom catalog URL").value = value;
		draft.find("Custom catalog URL").dispatchEvent(new draft.w.Event("input", { bubbles: true }));
	};
	editURL("https://example.com/older.json");
	await draft.click("Apply source");
	assert.match(draft.box.textContent, /Saving catalog source/);
	editURL("https://example.com/newer.json");
	assert.ok(!/Saving catalog source/.test(draft.box.textContent), "editing the URL ends the pending source notice");
	savedSource({ source: { id: "custom" } });
	await settle();
	assert.equal(
		draft.calls.filter(c => c.method === "catalog_list").length,
		1,
		"a stale source save must not load the edited URL",
	);
	draft.w.close();
	const many = boot(false, {
		catalog_list: arg => ({
			rows: arg.query ? [entry("last")] : Array.from({ length: 50 }, (_, index) => entry(String(index))),
			total: arg.query ? 1 : 512,
			origin: "downloaded",
		}),
	});
	await many.open();
	assert.equal(
		many.box.querySelectorAll("[data-catalog-result]").length,
		3,
		"only three direct matches can appear at once",
	);
	assert.match(many.box.textContent, /3 of 512/);
	assert.match(many.box.textContent, /narrow|refine/i);
	assert.equal(many.box.querySelectorAll("select").length, 0);
	many.find("Search modules").value = "last";
	many.find("Search modules").dispatchEvent(new many.w.Event("input", { bubbles: true }));
	await many.click("Search");
	assert.equal(
		many.calls.filter(call => call.method === "catalog_list").at(-1).arg.limit,
		50,
		"visible result bounds do not alter server request semantics",
	);
	await many.click("Details: Module last");
	assert.equal(
		many.calls.filter(call => call.method === "catalog_details").at(-1).arg.id,
		"last",
		"refining a query can reach entries beyond the initial returned matches",
	);
	many.w.close();
	const entries = Array.from({ length: 58 }, (_, i) =>
		entry("candidate-" + i, {
			name: i === 3 ? "Fan Control" : i === 54 ? "Target after first batch" : "A Fan Control " + i,
		}),
	);
	const collision = boot(true, {
		catalog_list: arg => ({
			rows: entries.slice(arg.offset, arg.offset + 50),
			total: entries.length,
			origin: "downloaded",
		}),
	});
	await collision.open();
	const jump = async n => {
		const input = collision.find("First match");
		assert.ok(input, "all matching entries have a direct position selector");
		input.focus();
		input.value = String(n);
		input.dispatchEvent(new collision.w.Event("change", { bubbles: true }));
		await settle();
		assert.equal(collision.w.document.activeElement.getAttribute("aria-label"), "First match");
	};
	await jump(4);
	assert.ok(collision.find("Details: Fan Control"), "an exact-name collision cannot hide the fourth matching entry");
	await jump(55);
	assert.ok(
		collision.find("Details: Target after first batch"),
		"all server batches remain reachable without window page arrows",
	);
	assert.equal(collision.calls.filter(c => c.method === "catalog_list").at(-1).arg.offset, 50);
	collision.w.close();
	console.log(
		"ok   catalog window keeps search, details, review, reports and inspection in one destination without pagination",
	);
})().catch(error => {
	console.error(error);
	process.exitCode = 1;
});
