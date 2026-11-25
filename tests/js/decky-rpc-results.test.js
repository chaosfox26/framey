"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { makeDom, ROOT, TOK } = require("./harness");
const { loaded, harness } = require("./decky-rpc-fixture.cjs");

// Fresh receipts traverse actual SDK construction, generated OWN source and
// in-process Worker JSON handlers. The subprocess is an owned fixture helper,
// never a Worker/supervisor or original plugin process.
const exportResult = spawnSync(
	process.env.FRAMEY_PYTHON || "python3",
	["-B", path.join(ROOT, "tests/test_decky_rpc_results.py"), "--host-fixtures"],
	{ encoding: "utf8", timeout: 10000, maxBuffer: 8 * 1024 * 1024 },
);
if (exportResult.error || exportResult.status !== 0)
	throw exportResult.error || new Error(exportResult.stderr || "Owned RPC fixture generation failed");
const receipts = JSON.parse(exportResult.stdout);
const plain = value => JSON.parse(JSON.stringify(value));
const tagged = value => ({ __framey_decky_rpc_result__: 1, value });
const bad = error => error.code === "bad_response";
const settle = async () => {
	for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
};

async function generatedPair(t, source) {
	const env = makeDom({ loadFiles: ["fy-api.js"] });
	// JSDOM omits these browser methods. Install explicit denial functions in
	// the owned iframe so the host can bind them; this IIFE fixture must never
	// acquire a blob URL. The generated wrapper/host source stays unchanged.
	let urlEffects = 0;
	const append = env.window.Element.prototype.append;
	env.window.Element.prototype.append = function (...nodes) {
		append.apply(this, nodes);
		for (const node of nodes) {
			if (node.tagName !== "IFRAME") continue;
			const denied = () => {
				urlEffects++;
				throw new Error("Blob URLs are denied in the owned RPC fixture");
			};
			node.contentWindow.URL.createObjectURL = denied;
			node.contentWindow.URL.revokeObjectURL = denied;
		}
	};
	t.after(() => {
		env.window.Framey.dispose(undefined, TOK);
		env.window.close();
		assert.equal(urlEffects, 0);
	});
	env.window.eval(
		"(function(Framey){" + source + "\n})(window.__bindFramey('owned_rpc','tok-owned_rpc-0000',{name:'Owned RPC'}));",
	);
	const plugin = env.record("owned_rpc");
	const api = env.window.Framey._k(TOK).apiOf(plugin);
	const box = env.document.createElement("div");
	env.document.body.append(box);
	plugin.render(box, api);
	await settle();
	const iframe = box.querySelector("iframe");
	assert.ok(iframe, box.textContent);
	const realm = iframe.contentWindow;
	assert.ok(realm.ownLegacy, box.textContent);
	const modern = realm.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(
		2,
		"Owned RPC",
	);
	async function answer(pending, receipt, expected) {
		await settle();
		const call = env.calls.at(-1);
		assert.equal(call.plugin, "owned_rpc");
		assert.equal(call.method, expected.method);
		assert.deepEqual(plain(call.arg), expected.arg);
		// The browser host receives the successful worker value, while an actual
		// worker failure uses Framey's established {error: message} boundary.
		const value = receipt.ok ? receipt.result : { error: receipt.error.message };
		env.answer(call, value);
		return await pending;
	}
	return { env, realm, modern, answer };
}

test("fresh generated modern pair preserves Worker domain values through the actual frontend SDK and host", async t => {
	const pair = await generatedPair(t, receipts.modern.main);
	for (const row of receipts.modern.successes) {
		assert.equal(row.reply.ok, true);
		const result = await pair.answer(pair.modern.call("echo", row.value), row.reply, {
			method: "echo",
			arg: { args: [row.value], legacy: false },
		});
		assert.deepEqual(plain(result), row.value);
	}
});

test("fresh generated legacy pair unwraps once before its success/result envelope", async t => {
	const pair = await generatedPair(t, receipts.legacy.main);
	for (const row of receipts.legacy.successes) {
		const result = await pair.answer(pair.realm.ownLegacy.callPluginMethod("echo", { value: row.value }), row.reply, {
			method: "echo",
			arg: { args: { value: row.value }, legacy: true },
		});
		assert.deepEqual(plain(result), { success: true, result: row.value });
	}
});

test("actual generated Worker exception stays modern rejection and legacy failure through the frontend SDK", async t => {
	const modern = await generatedPair(t, receipts.modern.main);
	const rejection = assert.rejects(modern.modern.call("crash"), /own RPC fixture exception/);
	await modern.answer(rejection, receipts.modern.failure, { method: "crash", arg: { args: [], legacy: false } });
	const legacy = await generatedPair(t, receipts.legacy.main);
	const result = await legacy.answer(legacy.realm.ownLegacy.callPluginMethod("crash", {}), receipts.legacy.failure, {
		method: "crash",
		arg: { args: {}, legacy: true },
	});
	assert.deepEqual(plain(result), { success: false, result: "own RPC fixture exception" });
});

test("paired host preserves nested error/code/marker lookalikes without a domain heuristic", async () => {
	for (const row of receipts.modern.successes) {
		const state = await loaded({ call: () => row.reply.result, hostOptions: { rpcResultVersion: 1 } });
		try {
			assert.deepEqual(await state.modern.call("echo"), row.value);
		} finally {
			state.host.dispose();
		}
	}
});

test("malformed or unknown-version success framing rejects, including mixed raw old-backend values", async () => {
	const values = [
		undefined,
		null,
		[],
		0,
		false,
		{},
		{ value: 1 },
		{ __framey_decky_rpc_result__: 1 },
		{ __framey_decky_rpc_result__: true, value: 1 },
		{ __framey_decky_rpc_result__: 2, value: 1 },
		{ __framey_decky_rpc_result__: 1, value: 1, extra: 0 },
		{ __framey_decky_rpc_result__: 2, value: 1, error: "must not bypass bad version" },
	];
	for (const value of values) {
		const state = await loaded({ call: () => value, hostOptions: { rpcResultVersion: 1 } });
		try {
			await assert.rejects(state.modern.call("echo"), bad);
		} finally {
			state.host.dispose();
		}
	}
});

test("outer framing requires exactly two own data fields without evaluating accessors", async () => {
	let reads = 0;
	const markerGetter = Object.defineProperty({ value: 1 }, "__framey_decky_rpc_result__", {
		get() {
			reads++;
			return 1;
		},
		enumerable: true,
	});
	const valueGetter = Object.defineProperty({ __framey_decky_rpc_result__: 1 }, "value", {
		get() {
			reads++;
			return 1;
		},
		enumerable: true,
	});
	const inherited = Object.assign(Object.create({ __framey_decky_rpc_result__: 1 }), { value: 1 });
	const symbolExtra = Object.assign(tagged(1), { [Symbol("extra")]: true });
	for (const value of [markerGetter, valueGetter, inherited, symbolExtra]) {
		const state = await loaded({ call: () => value, hostOptions: { rpcResultVersion: 1 } });
		try {
			await assert.rejects(state.modern.call("echo"), bad);
		} finally {
			state.host.dispose();
		}
	}
	assert.equal(reads, 0);
});

test("strict host version admission and absent option preserve the old unpaired contract", async () => {
	for (const version of [null, 0, 2, true, false, "1", {}])
		assert.throws(() => harness({ hostOptions: { rpcResultVersion: version } }), /RPC result envelope version/);
	const old = await loaded({ call: () => ({ old: true }) });
	try {
		assert.deepEqual(await old.modern.call("echo"), { old: true });
	} finally {
		old.host.dispose();
	}
	const mismatched = await loaded({ call: () => tagged({ error: "new backend data" }) });
	try {
		assert.deepEqual(await mismatched.modern.call("echo"), tagged({ error: "new backend data" }));
	} finally {
		mismatched.host.dispose();
	}
});

test("real transport rejection and synchronous throw never become framed success", async () => {
	for (const call of [
		() => {
			throw new Error("own transport");
		},
		() => Promise.reject(new Error("own transport")),
	]) {
		const state = await loaded({ call, hostOptions: { rpcResultVersion: 1 } });
		try {
			await assert.rejects(state.modern.call("echo"), /own transport/);
		} finally {
			state.host.dispose();
		}
	}
	const state = await loaded({ call: () => Promise.reject(undefined), hostOptions: { rpcResultVersion: 1 } });
	try {
		let succeeded = false;
		await state.modern.call("echo").then(
			() => {
				succeeded = true;
			},
			() => {},
		);
		assert.equal(succeeded, false);
	} finally {
		state.host.dispose();
	}
});

test("disposal rejects pending paired calls and ignores late framed replies", async () => {
	let resolve;
	const state = await loaded({
		call: () =>
			new Promise(done => {
				resolve = done;
			}),
		hostOptions: { rpcResultVersion: 1 },
	});
	const pending = state.modern.call("wait");
	await Promise.resolve();
	await Promise.resolve();
	state.host.dispose();
	await assert.rejects(pending, /disposed/);
	resolve(tagged({ error: "late domain" }));
	await Promise.resolve();
	assert.equal(state.host.status().panel, "disposed");
});
