"use strict";
const assert = require("assert");
const { makeDom } = require("./harness");
const settle = async () => {
	for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
};
const row = (id, extra = {}) => ({
	id,
	name: id,
	description: "Description ".repeat(60),
	version: "1",
	identity: id,
	source: { repository: "https://example.com/" + id, commit: "a".repeat(40) },
	eligible: true,
	platform_declared: true,
	compat: { state: "compatible", badge: "ok", msg: "Compatible" },
	...extra,
});
const boot = (layout = "compact", extra = {}) => {
	const calls = [];
	const env = makeDom({
		before(w) {
			w.FY_RUNTIME = { panel_layout: layout, trusted_input: "off" };
			w.fyCall = encoded => {
				const c = JSON.parse(encoded);
				calls.push(c);
				if (!c.id) return;
				let r = { error: "Unhandled" };
				if (c.method === "catalog_sources") r = { source: { id: "stable" } };
				if (c.method === "catalog_list")
					r = {
						rows: c.arg.offset ? [row("last")] : [row("first", extra), row("second")],
						total: 3,
						offset: c.arg.offset,
						source: { id: "stable" },
						origin: "downloaded",
					};
				if (c.method === "catalog_details")
					r = { entry: row(c.arg.id, { permissions: ["filesystem", "network"], ...extra }), origin: "downloaded" };
				Promise.resolve().then(() => w.Framey._reply(c.gen, c.id, r));
			};
		},
		loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js"],
	});
	const { window: w, document: d } = env;
	const K = w.__S.K,
		box = d.createElement("div");
	d.body.append(box);
	let page;
	const real = K.setPageNavigation;
	K.setPageNavigation = o => {
		page = o;
		real(o);
	};
	const labels = () => [...box.querySelectorAll("[aria-label]")].map(n => n.getAttribute("aria-label"));
	const find = label => box.querySelector('[aria-label="' + label + '"]');
	const seek = label => {
		for (let i = 0; i < page.count; i++) {
			page.onPage(i);
			if (find(label)) return find(label);
		}
		throw new Error("Missing " + label);
	};
	return {
		w,
		d,
		K,
		box,
		calls,
		labels,
		find,
		seek,
		get page() {
			return page;
		},
		open: async () => {
			w.Framey.pages.store.render(box);
			await settle();
		},
	};
};
(async () => {
	const c = boot();
	await c.open();
	assert.ok(c.page, "catalog registers explicit page navigation");
	assert.ok(c.page.count > 1, "catalog controls and entries occupy separate bounded pages");
	for (let i = 0; i < c.page.count; i++) {
		c.page.onPage(i);
		assert.ok(
			c.box.querySelectorAll("button,[role=button],input,select").length <= 1,
			"compact page has one readable control",
		);
		assert.ok(c.box.textContent.length <= 150, "compact copy has a bounded reading step");
	}
	assert.ok(!c.labels().includes("Load more"));
	c.seek("Details: first").click();
	await settle();
	let description = "";
	for (let i = 0; i < c.page.count; i++) {
		c.page.onPage(i);
		assert.ok(c.box.textContent.length <= 150);
		if (c.page.label === "Description") description += c.box.textContent;
	}
	assert.equal(description, "Description ".repeat(60), "long descriptions remain fully readable across explicit pages");
	assert.ok(c.page.onBack, "details expose a fixed Back action");
	c.page.onBack();
	await settle();
	c.seek("Next results").click();
	await settle();
	assert.equal(c.calls.filter(x => x.method === "catalog_list").at(-1).arg.offset, 2);
	assert.ok(c.seek("Details: last"));
	assert.throws(
		() => c.seek("Details: first"),
		/Missing/,
		"server pages replace prior results instead of accumulating",
	);
	c.seek("Previous results").click();
	await settle();
	assert.equal(c.calls.filter(x => x.method === "catalog_list").at(-1).arg.offset, 0);
	assert.ok(c.seek("Details: first"));
	c.w.close();
	const name = "Long catalog name " + "界".repeat(180);
	const long = boot("compact", { name });
	await long.open();
	const details = long.seek("Details: " + name);
	assert.ok(details.textContent.length <= 40, "long catalog names use concise action labels");
	details.click();
	await settle();
	assert.ok(long.page.title.length <= 40, "the fixed header cannot expand around oversized metadata");
	let fullName = "";
	for (let i = 0; i < long.page.count; i++) {
		long.page.onPage(i);
		if (long.page.label === "Name") fullName += long.box.textContent;
	}
	assert.equal(fullName, name.slice(0, 200), "full normalized names remain reachable as text pages");
	long.K.report = () => Promise.reject(new Error("unavailable"));
	long.seek("Report to Framey").click();
	await settle();
	assert.equal(long.page.label, "Report unavailable", "preview errors have a dedicated readable step");
	assert.equal(long.box.querySelectorAll("[role=button],button,input,select").length, 0);
	assert.ok(long.box.textContent.length <= 64);
	long.w.close();
	for (const layout of ["compact"]) {
		const description = "Line\n".repeat(12) + "\n".repeat(10) + "界".repeat(70) + "\r\nTail\u2028End\u2029";
		const permissions = "Permission\r\n".repeat(12) + "\n".repeat(10);
		const reason = "Compatibility line\n".repeat(12);
		const multiline = boot(layout, {
			description,
			permissions,
			compat: { state: "compatible", badge: "ok", msg: reason },
		});
		await multiline.open();
		multiline.seek("Details: first").click();
		await settle();
		const joined = { Description: "", Compatibility: "", Permissions: "" };
		for (let index = 0; index < multiline.page.count; index++) {
			multiline.page.onPage(index);
			for (const node of multiline.box.querySelectorAll("[data-catalog-step]")) {
				if (Object.hasOwn(joined, node.dataset.catalogStep)) joined[node.dataset.catalogStep] += node.textContent;
			}
		}
		assert.equal(joined.Description, description, layout + " preserves all description line breaks and blank lines");
		assert.equal(joined.Permissions, permissions, layout + " preserves all permissions and CRLF pairs");
		assert.equal(
			joined.Compatibility,
			"Compatible" + reason + "Meets declared Framey, API and platform requirements. This is not a runtime test.",
		);
		multiline.w.close();
	}
	console.log("ok   compact catalog uses bounded screens, complete text pages, and replacement result navigation");
})().catch(e => {
	console.error(e);
	process.exitCode = 1;
});
