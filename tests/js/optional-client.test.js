"use strict";
const { test } = require("node:test"),
	assert = require("node:assert/strict"),
	fs = require("node:fs"),
	path = require("node:path"),
	vm = require("node:vm");
const { makeDom, ROOT } = require("./harness");
function fixture(t) {
	const e = makeDom({ loadFiles: [] });
	t.after(() => e.window.close());
	const records = [];
	let active = true;
	const context = {
		module: { exports: {} },
		Promise,
		setTimeout,
		clearTimeout,
		Date,
		createFrameyDeckyHost(api, options) {
			const r = { api, options, disposed: 0 };
			records.push(r);
			r.host = {
				load(source, format, box) {
					Object.assign(r, { source, format, box });
					return new Promise(resolve => (r.finish = resolve));
				},
				status: () => ({ owned: true }),
				dispose() {
					r.disposed++;
				},
			};
			return r.host;
		},
		createFrameyViewRuntime() {},
		createFrameyDeckyNavigation() {},
		createFrameyDeckyMenus() {},
		createFrameyDeckySidebar() {},
		createFrameyDeckyModules() {},
		createFrameyDeckyAssets: (name, map) => ({ name, map }),
		createFrameyDeckyStore: c => ({ generation: c.generation }),
	};
	vm.runInNewContext(fs.readFileSync(path.join(ROOT, "decky_runtime/client.js"), "utf8"), context, { timeout: 1000 });
	const provider = context.module.exports.createFrameyDeckyProvider(),
		api = { id: "owned" };
	const client = provider.attach({
		api,
		alive: () => active,
		current: () => active,
		request() {
			throw Error("no RPC expected");
		},
		cleanupRequest() {},
		lost() {},
	});
	const box = e.document.createElement("div");
	e.document.body.append(box);
	return {
		e,
		records,
		provider,
		client,
		box,
		api,
		stop() {
			active = false;
			client.dispose();
		},
	};
}
const descriptor = {
	v: 1,
	name: "owned",
	apiVersion: 2,
	rpcResultVersion: 1,
	source: "owned source",
	format: "iife",
	moduleGraph: { required: true },
	assets: { "assets/a.png": "owned image" },
};
test("optional client maps the importer descriptor to the existing helper factories", async t => {
	const f = fixture(t),
		p = f.client.mount(f.box, descriptor),
		r = f.records[0];
	assert.equal(r.api, f.api);
	assert.equal(r.options.nativeRoutes, true);
	assert.equal(r.options.moduleGraph, true);
	assert.equal(r.options.rpcResultVersion, 1);
	assert.equal(typeof r.options.createViewRuntime, "function");
	assert.equal(typeof r.options.createModules, "function");
	assert.equal(r.options.assets.name, "owned");
	assert.equal(r.source, "owned source");
	assert.equal(r.format, "iife");
	assert.notEqual(r.box, f.box);
	r.finish();
	const h = await p;
	assert.equal(h.status().owned, true);
	h.dispose();
	assert.equal(r.disposed, 1);
});
test("replacement retires the earlier host and rejects its late mount", async t => {
	const f = fixture(t),
		a = f.client.mount(f.box, descriptor),
		rejected = assert.rejects(a, x => x.code === "disposed");
	const b = f.client.mount(f.box, descriptor);
	assert.equal(f.records[0].disposed, 1);
	f.records[0].finish();
	await rejected;
	f.records[1].finish();
	const h = await b;
	h.dispose();
	assert.equal(f.records[1].disposed, 1);
});
test("generation loss disposes an attached host and rejects late completion", async t => {
	const f = fixture(t),
		a = f.client.mount(f.box, descriptor),
		rejected = assert.rejects(a, x => x.code === "disposed");
	f.stop();
	assert.equal(f.records[0].disposed, 1);
	f.records[0].finish();
	await rejected;
	await assert.rejects(f.client.mount(f.box, descriptor), x => x.code === "disposed");
});
test("invalid descriptors are rejected before helper creation", async t => {
	const f = fixture(t);
	for (const d of [
		{ ...descriptor, v: 2 },
		{ ...descriptor, apiVersion: 1 },
		{ ...descriptor, rpcResultVersion: 2 },
		{ ...descriptor, format: "unknown" },
	])
		await assert.rejects(f.client.mount(f.box, d), /descriptor/);
	assert.equal(f.records.length, 0);
	assert.equal(f.provider.store({ generation: 9 }).generation, 9);
});
