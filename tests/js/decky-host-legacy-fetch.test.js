"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const path = require("node:path");
const hostPath = process.env.FRAMEY_HOST_SOURCE || path.resolve(__dirname, "../../decky_runtime/compat/decky-host.js");
const { createFrameyDeckyHost } = require(hostPath);
const plain = value => JSON.parse(JSON.stringify(value));

// Execute the unchanged owned host and load only this test's synthetic factory.
// The realm/DOM and transport are in memory; no browser, vendor or network runs.
async function setup(t, fetchProvider) {
	class Target {
		addEventListener() {}
		removeEventListener() {}
	}
	class Observer {
		observe() {}
		disconnect() {}
	}
	const main = {};
	const metrics = { clientHeight: 600, clientWidth: 800, scrollHeight: 600, scrollWidth: 800 };
	const document = {
		documentElement: { ...metrics },
		body: {},
		scrollingElement: { ...metrics },
		open() {},
		write() {},
		close() {},
		querySelector(selector) {
			assert.equal(selector, "main");
			return main;
		},
	};
	const unexpected = () => {
		throw new Error("Unexpected native effect in fetch fixture");
	};
	const realm = new Target();
	Object.assign(realm, {
		document,
		navigator: { language: "en" },
		EventTarget: Target,
		MutationObserver: Observer,
		Promise,
		Response,
		Headers,
		atob,
		URL: { createObjectURL: unexpected, revokeObjectURL() {} },
		setTimeout: unexpected,
		clearTimeout() {},
		setInterval: unexpected,
		clearInterval() {},
		requestAnimationFrame: unexpected,
		cancelAnimationFrame() {},
	});
	realm.window = realm;
	const context = vm.createContext(realm);
	realm.eval = source => vm.runInContext(source, context, { timeout: 1000 });
	let framesRemoved = 0;
	const frame = { style: {}, contentWindow: realm, remove: () => framesRemoved++ };
	const ownerDocument = {
		documentElement: { lang: "en" },
		defaultView: {
			innerHeight: 600,
			getComputedStyle: () => ({ flexGrow: "0", paddingTop: "0", paddingBottom: "0" }),
		},
		createElement(tag) {
			assert.equal(tag, "iframe");
			return frame;
		},
	};
	const container = {
		ownerDocument,
		parentElement: null,
		clientHeight: 600,
		getBoundingClientRect: () => ({ top: 0 }),
		append: value => assert.equal(value, frame),
	};
	let unmounted = 0;
	const view = {
		React: {
			createElement: (type, props, ...children) => ({ type, props, children }),
			forwardRef: fn => fn,
			createContext: value => ({ value }),
		},
		ReactDOM: {
			createRoot(node) {
				assert.equal(node, main);
				return { render() {}, unmount: () => unmounted++ };
			},
		},
		dispose() {},
	};
	const calls = [];
	const api = { call: unexpected, events: { on: unexpected }, cleanup: () => () => {} };
	if (fetchProvider !== null)
		api.fetch = (url, init) => {
			calls.push({ url, init });
			return fetchProvider(url, init);
		};
	const host = createFrameyDeckyHost(api, { name: "Owned fetch fixture", createViewRuntime: () => view });
	t.after(() => {
		host.dispose();
		assert.equal(framesRemoved, 1);
		assert.equal(unmounted, 1);
	});
	await host.load("(api => { window.ownLegacy = api; return { content: null }; })", "iife", container);
	assert.equal(host.status().panel, "mounted");
	const modern = realm.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(
		2,
		"Owned fetch fixture",
	);
	return { host, realm, legacy: realm.ownLegacy, modern, calls };
}

test("legacy fetch exposes completed response status, body and native header map", async t => {
	const e = await setup(t, async () => ({
		status: 201,
		text: '{"own":true}',
		headers: { "content-type": "application/json", "x-own": "yes" },
	}));
	assert.deepEqual(plain(await e.legacy.fetchNoCors("https://example.invalid/")), {
		success: true,
		result: { status: 201, body: '{"own":true}', headers: { "content-type": "application/json", "x-own": "yes" } },
	});
});

test("legacy json body preserves ordinary values, content type and caller input", async t => {
	const e = await setup(t, async () => ({ status: 200, text: "ok" }));
	for (const json of [null, false, 0, ["own-id"], { values: [null, false, 0, "\u20ac"] }]) {
		const headers = new Headers({ "Content-Type": "application/custom+json", "X-Own": "yes" });
		const init = { method: "POST", json, headers };
		const before = JSON.stringify(init);
		const response = await e.legacy.fetchNoCors("https://example.invalid/", init);
		const sent = e.calls.at(-1).init;
		assert.deepEqual(JSON.parse(sent.body), json);
		assert.equal(response.success, true);
		assert.equal(sent.headers["content-type"], "application/custom+json");
		assert.equal(sent.headers["x-own"], "yes");
		assert.equal(Object.hasOwn(sent, "json"), false);
		assert.equal(JSON.stringify(init), before);
		assert.equal(Object.hasOwn(init, "body"), false);
		assert.equal(headers.get("content-type"), "application/custom+json");
	}
	await e.legacy.fetchNoCors("https://example.invalid/", { json: ["own-id"] });
	assert.equal(e.calls.at(-1).init.body, '["own-id"]');
	assert.equal(e.calls.at(-1).init.headers["content-type"], "application/json");
});

test("legacy omitted and falsy methods default POST without changing modern defaults", async t => {
	const e = await setup(t, async () => ({ status: 200, text: "ok" }));
	for (const method of [undefined, "", null, false, 0]) {
		const init = method === undefined ? {} : { method };
		const before = JSON.stringify(init);
		const response = await e.legacy.fetchNoCors("https://example.invalid/", init);
		assert.equal(e.calls.at(-1).init.method, "POST");
		assert.equal(response.success, true);
		assert.equal(JSON.stringify(init), before);
	}
	await e.modern.fetchNoCors("https://example.invalid/");
	assert.equal(Object.hasOwn(e.calls.at(-1).init, "method"), false);
});

test("completed404 and503 remain legacy transport successes and204 has no body", async t => {
	let status = 404;
	const e = await setup(t, async () => ({ status, text: "own response" }));
	for (const code of [404, 503, 204]) {
		status = code;
		const result = await e.legacy.fetchNoCors("https://example.invalid/");
		assert.equal(result.success, true);
		assert.equal(result.result.status, code);
		assert.equal(result.result.body, code === 204 ? "" : "own response");
	}
});

test("modern and realm fetch remain Response functions with unchanged options", async t => {
	const e = await setup(t, async () => ({ status: 202, text: "modern", headers: { "x-own": "yes" } }));
	assert.equal(e.modern.fetchNoCors, e.realm.fetch);
	assert.notEqual(e.legacy.fetchNoCors, e.realm.fetch);
	const init = { method: "POST", json: ["unchanged"] };
	const response = await e.modern.fetchNoCors("https://example.invalid/", init);
	assert.ok(response instanceof Response);
	assert.equal(response.success, undefined);
	assert.equal(response.status, 202);
	assert.equal(await response.text(), "modern");
	assert.equal(e.calls.at(-1).init, init);
	assert.equal(Object.hasOwn(init, "body"), false);
	const direct = await e.realm.fetch("https://example.invalid/");
	assert.ok(direct instanceof Response);
	assert.notEqual(direct, response);
	assert.equal(await direct.text(), "modern");
});

test("transport denial and rejection sanitize legacy failure and preserve modern rejection", async t => {
	let reject = false;
	const e = await setup(t, async () => {
		if (reject) throw Error("own confidential fixture");
		return { error: "Owned address denied", code: "blocked_address" };
	});
	assert.deepEqual(plain(await e.legacy.fetchNoCors("https://example.invalid/")), {
		success: false,
		result: "Legacy fetch failed",
	});
	await assert.rejects(e.modern.fetchNoCors("https://example.invalid/"), {
		message: "Owned address denied",
		code: "blocked_address",
	});
	reject = true;
	assert.deepEqual(plain(await e.legacy.fetchNoCors("https://example.invalid/")), {
		success: false,
		result: "Legacy fetch failed",
	});
	await assert.rejects(e.realm.fetch("https://example.invalid/"), /own confidential fixture/);
});

test("invalid legacy JSON options fail before transport", async t => {
	const e = await setup(t, async () => ({ status: 200 }));
	const circular = {};
	circular.self = circular;
	for (const init of [null, [], 3, { json: undefined }, { json: circular }, { json: [], body: "conflict" }])
		assert.deepEqual(plain(await e.legacy.fetchNoCors("https://example.invalid/", init)), {
			success: false,
			result: "Legacy fetch failed",
		});
	assert.equal(e.calls.length, 0);
});

test("disposed legacy request cannot reach transport or accept a late response", async t => {
	let release;
	const e = await setup(t, () => new Promise(resolve => (release = resolve)));
	const pending = e.legacy.fetchNoCors("https://example.invalid/");
	e.host.dispose();
	release({ status: 200, text: "late" });
	assert.equal((await pending).success, false);
	assert.equal((await e.legacy.fetchNoCors("https://example.invalid/")).success, false);
	await assert.rejects(e.modern.fetchNoCors("https://example.invalid/"), /disposed/);
	assert.equal(e.calls.length, 1);
});
