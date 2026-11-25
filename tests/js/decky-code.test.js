"use strict";
const assert = require("assert");
const { makeDom, TOK, ROOT } = require("./harness");
const fs = require("node:fs"),
	path = require("node:path");

const tests = [];
const t = (name, fn) => tests.push([name, fn]);
const A = "a".repeat(64),
	B = "b".repeat(64);
const json = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
const receipt = (token = A, port = 32145) => {
	const root = "http://127.0.0.1:" + port + "/g/" + token + "/";
	return { root, entry: root + "dist/index.js", token, ttl: 600 };
};
const fresh = () => {
	let now = 1000000;
	const e = makeDom({
		loadFiles: ["fy-api.js"],
		before: w => {
			w.Date.now = () => now;
		},
	});
	e.window.eval(
		fs.readFileSync(path.join(ROOT, "decky_runtime/client.js"), "utf8") +
			";window.__createDeckyProvider=createFrameyDeckyProvider;",
	);
	e.window.__S.FY.ext.push((api, p, res, io) => {
		const id = api.id;
		const client = e.window.__createDeckyProvider().attach({
			api,
			alive: () => !p.dead,
			current: () => true,
			request: (method, arg) => api._rpc(method, { ...arg, pid: id }),
			cleanupRequest: (method, arg) => io.post(method, { ...arg, pid: id }),
			lost: code => e.event(id, "compat.modules.lost", { code }),
		});
		api.compat = Object.freeze({ modules: client.modules });
		res.push(() => client.dispose());
	});
	e.advance = ms => {
		now += ms;
		e.clock.tick(ms);
	};
	return e;
};
const issued = async (e, api, token = A) => {
	const pending = api.compat.modules.issue();
	e.answer(e.sent("decky_code_issue").at(-1), receipt(token));
	return await pending;
};
const cleanupCalls = e => e.sent("decky_code_revoke").filter(call => call.id === 0);

t("modules.issue uses scoped owner only and returns an immutable copied lease", async () => {
	const e = fresh(),
		api = e.plugin("owner");
	assert.ok(Object.isFrozen(api.compat));
	assert.ok(Object.isFrozen(api.compat.modules));
	const pending = api.compat.modules.issue();
	const request = e.sent("decky_code_issue")[0];
	assert.strictEqual(request.plugin, "_core");
	assert.strictEqual(request.t, "tok-owner-0000");
	assert.notStrictEqual(request.t, TOK);
	assert.deepStrictEqual(json(request.arg), { pid: "owner" });
	const native = receipt();
	e.answer(request, native);
	const lease = await pending;
	assert.deepStrictEqual(json(lease), native);
	assert.notStrictEqual(lease, native);
	assert.ok(Object.isFrozen(lease));
	assert.throws(() => {
		lease.ttl = 999;
	}, TypeError);
	native.entry = "changed";
	assert.strictEqual(lease.entry, receipt().entry);
});

t("module methods reject caller configuration and unowned tokens before dispatch", async () => {
	const e = fresh(),
		modules = e.plugin("a").compat.modules;
	await assert.rejects(modules.issue({ source: "foreign", ttl: 999 }), error => error.code === "invalid_arguments");
	for (const token of [undefined, null, {}, "bad", A]) {
		await assert.rejects(modules.touch(token), error => ["invalid_arguments", "not_owned"].includes(error.code));
		await assert.rejects(modules.revoke(token), error => ["invalid_arguments", "not_owned"].includes(error.code));
	}
	assert.strictEqual(e.calls.length, 0);
});

t("concurrent issue callers share one native request and receive separate frozen snapshots", async () => {
	const e = fresh(),
		modules = e.plugin("a").compat.modules;
	const first = modules.issue(),
		second = modules.issue();
	assert.strictEqual(e.sent("decky_code_issue").length, 1);
	e.answer(e.sent("decky_code_issue")[0], receipt());
	const [a, b] = await Promise.all([first, second]);
	assert.notStrictEqual(a, b);
	assert.deepStrictEqual(json(a), json(b));
	assert.ok(Object.isFrozen(a) && Object.isFrozen(b));
	assert.strictEqual(e.clock.pending(), 1);
});

t("fresh issue replaces the local graph without sending options to native", async () => {
	const e = fresh(),
		api = e.plugin("a"),
		lost = [];
	api.events.on("compat.modules.lost", payload => lost.push(json(payload)));
	await issued(e, api, A);
	const pending = api.compat.modules.issue({ fresh: true });
	const request = e.sent("decky_code_issue").at(-1);
	assert.deepStrictEqual(json(request.arg), { pid: "a" });
	e.answer(request, receipt(B));
	assert.strictEqual((await pending).token, B);
	assert.deepStrictEqual(
		cleanupCalls(e).map(call => call.arg.token),
		[A],
	);
	assert.deepStrictEqual(lost, [{ code: "stale_caller" }]);
	assert.strictEqual((await api.compat.modules.issue()).token, B);
	assert.strictEqual(e.sent("decky_code_issue").length, 2);
	assert.strictEqual(e.clock.pending(), 1);
});

t("same-API fresh incarnations reject and revoke only the superseded late token", async () => {
	for (const oldFirst of [false, true]) {
		const e = fresh(),
			api = e.plugin("a"),
			modules = api.compat.modules,
			lost = [];
		api.events.on("compat.modules.lost", payload => lost.push(json(payload)));
		const old = modules.issue({ fresh: true });
		old.catch(() => {});
		const replacement = modules.issue({ fresh: true });
		replacement.catch(() => {});
		assert.strictEqual(e.sent("decky_code_issue").length, 2);
		const [oldRequest, newRequest] = e.sent("decky_code_issue");
		if (oldFirst) {
			e.answer(oldRequest, receipt(A));
			await assert.rejects(old, error => error.code === "stale_caller");
			const joined = modules.issue();
			joined.catch(() => {});
			assert.strictEqual(e.sent("decky_code_issue").length, 2);
			e.answer(newRequest, receipt(B));
			assert.strictEqual((await joined).token, B);
		} else {
			e.answer(newRequest, receipt(B));
			assert.strictEqual((await replacement).token, B);
			e.answer(oldRequest, receipt(A));
			await assert.rejects(old, error => error.code === "stale_caller");
		}
		assert.strictEqual((await replacement).token, B);
		assert.strictEqual((await modules.issue()).token, B);
		assert.deepStrictEqual(
			cleanupCalls(e).map(call => call.arg.token),
			[A],
		);
		assert.deepStrictEqual(lost, []);
	}
});

t("a synchronous lost listener cannot make an older fresh call share its newer graph", async () => {
	const e = fresh(),
		api = e.plugin("a"),
		modules = api.compat.modules;
	await issued(e, api);
	let inner;
	api.events.once("compat.modules.lost", () => {
		inner = modules.issue({ fresh: true });
		inner.catch(() => {});
	});
	const outer = modules.issue({ fresh: true });
	outer.catch(() => {});
	assert.strictEqual(e.sent("decky_code_issue").length, 2);
	e.answer(e.sent("decky_code_issue").at(-1), receipt(B));
	await assert.rejects(outer, error => error.code === "stale_caller");
	assert.strictEqual((await inner).token, B);
	assert.strictEqual((await modules.issue()).token, B);
	assert.deepStrictEqual(
		cleanupCalls(e).map(call => call.arg.token),
		[A],
	);
});

t("modules.touch and revoke stay scoped to the issuing API and validate receipts", async () => {
	const e = fresh(),
		a = e.plugin("a"),
		b = e.plugin("b");
	await issued(e, a);
	await assert.rejects(b.compat.modules.touch(A), error => error.code === "not_owned");
	await assert.rejects(b.compat.modules.revoke(A), error => error.code === "not_owned");
	const touch = a.compat.modules.touch(A);
	const touchRequest = e.sent("decky_code_touch")[0];
	assert.deepStrictEqual(json(touchRequest.arg), { pid: "a", token: A });
	assert.strictEqual(touchRequest.t, "tok-a-0000");
	e.answer(touchRequest, { renewed: true, ttl: 600 });
	assert.deepStrictEqual(json(await touch), { renewed: true, ttl: 600 });
	const revoke = a.compat.modules.revoke(A);
	const revokeRequest = e.sent("decky_code_revoke").at(-1);
	assert.deepStrictEqual(json(revokeRequest.arg), { pid: "a", token: A });
	e.answer(revokeRequest, { revoked: true });
	assert.deepStrictEqual(json(await revoke), { revoked: true });
	await assert.rejects(a.compat.modules.touch(A), error => error.code === "not_owned");
	assert.strictEqual(e.clock.pending(), 0);
});

t("modules.issue rejects malformed roots, entry paths, tokens and lifetimes and retires safe tokens", async () => {
	for (const native of [
		null,
		[],
		{},
		{ ...receipt(), ttl: 599 },
		{ ...receipt(), ttl: "600" },
		{ ...receipt(), root: "https://127.0.0.1:32145/g/" + A + "/" },
		{ ...receipt(), root: "http://localhost:32145/g/" + A + "/" },
		{ ...receipt(), root: "http://127.0.0.2:32145/g/" + A + "/" },
		receipt(A, 0),
		receipt(A, 65536),
		receipt(A + "\n"),
		{ ...receipt(), root: receipt().root + "\n", entry: receipt().root + "\ndist/index.js" },
		{ ...receipt(), entry: receipt().root + "../dist/index.js" },
		{ ...receipt(), entry: receipt().entry + "?x=1" },
		{ ...receipt(), token: B },
		{ ...receipt(), token: "bad" },
		{ ...receipt(), extra: "PRIVATE_NATIVE_DETAIL" },
		{ ...receipt(), error: "" },
	]) {
		const e = fresh(),
			api = e.plugin("a");
		const pending = api.compat.modules.issue();
		e.answer(e.sent("decky_code_issue")[0], native);
		await assert.rejects(pending, error => error.code === "bad_response");
		if (
			native &&
			typeof native.token === "string" &&
			native.token.length === 64 &&
			/^[a-f0-9]{64}$/.test(native.token)
		) {
			assert.ok(cleanupCalls(e).some(call => call.arg.token === native.token));
		}
		assert.strictEqual(e.clock.pending(), 0);
	}
});

t("module failures preserve bounded codes and never expose native or transport text", async () => {
	for (const code of ["not_allowed", "not_owned", "code_expired", "busy", "PRIVATE_UNBOUNDED_CODE"]) {
		const e = fresh();
		const pending = e.plugin("a").compat.modules.issue();
		e.answer(e.sent("decky_code_issue")[0], { error: "PRIVATE_NATIVE_DETAIL", code });
		await assert.rejects(
			pending,
			error =>
				error.code === (code === "PRIVATE_UNBOUNDED_CODE" ? "code_unavailable" : code) &&
				!error.message.includes("PRIVATE_NATIVE_DETAIL"),
		);
	}
	const e = fresh();
	e.onCall(() => {
		throw new Error("PRIVATE_TRANSPORT_DETAIL");
	});
	await assert.rejects(
		e.plugin("a").compat.modules.issue(),
		error => !error.message.includes("PRIVATE_TRANSPORT_DETAIL") && /^[a-z_]+$/.test(error.code),
	);
});

t("repeated issue caches its live lease and keeps one private heartbeat", async () => {
	const e = fresh(),
		api = e.plugin("a");
	const first = await issued(e, api, A);
	const second = await api.compat.modules.issue();
	assert.notStrictEqual(first, second);
	assert.deepStrictEqual(json(first), json(second));
	assert.strictEqual(e.sent("decky_code_issue").length, 1);
	assert.strictEqual(e.clock.pending(), 1);
	e.advance(239999);
	assert.strictEqual(e.sent("decky_code_touch").length, 0);
	e.advance(1);
	const touches = e.sent("decky_code_touch");
	assert.deepStrictEqual(
		touches.map(call => call.arg.token),
		[A],
	);
	touches.forEach(call => e.answer(call, { renewed: true, ttl: 600 }));
	await flush();
	assert.strictEqual(e.clock.pending(), 1);
	e.advance(240000);
	assert.strictEqual(e.sent("decky_code_touch").length, 2);
	e.sent("decky_code_touch")
		.slice(1)
		.forEach(call => e.answer(call, { renewed: true, ttl: 600 }));
	await flush();
	assert.strictEqual(e.clock.pending(), 1);
});

t("an explicit touch coalesces with heartbeat for the same lease", async () => {
	const e = fresh(),
		api = e.plugin("a");
	await issued(e, api);
	e.advance(239999);
	const explicit = api.compat.modules.touch(A);
	e.advance(1);
	assert.strictEqual(e.sent("decky_code_touch").length, 1);
	e.answer(e.sent("decky_code_touch")[0], { renewed: true, ttl: 600 });
	await explicit;
	await flush();
	assert.strictEqual(e.clock.pending(), 1);
});

t("a long-sleep heartbeat retires expired leases without attempting renewal", async () => {
	const e = fresh(),
		api = e.plugin("a"),
		lost = [];
	api.events.on("compat.modules.lost", payload => lost.push(json(payload)));
	await issued(e, api);
	e.advance(600000);
	await flush();
	assert.strictEqual(e.sent("decky_code_touch").length, 0);
	assert.deepStrictEqual(lost, [{ code: "code_expired" }]);
	assert.deepStrictEqual(json(cleanupCalls(e).map(call => call.arg)), [{ pid: "a", token: A }]);
	await assert.rejects(api.compat.modules.touch(A), error => error.code === "not_owned");
	assert.strictEqual(e.clock.pending(), 0);
});

t("a renewal reply after the original deadline never revives its lease", async () => {
	const e = fresh(),
		api = e.plugin("a"),
		lost = [];
	api.events.on("compat.modules.lost", payload => lost.push(json(payload)));
	await issued(e, api);
	const pending = api.compat.modules.touch(A);
	// Move wall time without firing the transport timeout or heartbeat.
	e.window.Date.now = () => 1600000;
	e.answer(e.sent("decky_code_touch")[0], { renewed: true, ttl: 600 });
	await assert.rejects(pending, error => error.code === "code_expired");
	assert.deepStrictEqual(lost, [{ code: "code_expired" }]);
	assert.ok(cleanupCalls(e).some(call => call.arg.token === A));
	await assert.rejects(api.compat.modules.touch(A), error => error.code === "not_owned");
});

t("expired cache requires a fresh native issue and does not reuse its old token", async () => {
	const e = fresh(),
		api = e.plugin("a");
	await issued(e, api, A);
	e.advance(600000);
	await flush();
	const pending = api.compat.modules.issue();
	assert.strictEqual(e.sent("decky_code_issue").length, 2);
	e.answer(e.sent("decky_code_issue").at(-1), receipt(B));
	assert.strictEqual((await pending).token, B);
	assert.deepStrictEqual(
		cleanupCalls(e).map(call => call.arg.token),
		[A],
	);
});

t("public revoke can retire its still-known expired token without renewing it", async () => {
	const e = fresh(),
		api = e.plugin("a");
	await issued(e, api);
	e.window.Date.now = () => 1600000;
	assert.deepStrictEqual(json(await api.compat.modules.revoke(A)), { revoked: true });
	assert.deepStrictEqual(
		cleanupCalls(e).map(call => call.arg.token),
		[A],
	);
	assert.strictEqual(e.sent("decky_code_touch").length, 0);
	assert.strictEqual(e.clock.pending(), 0);
});

t("heartbeat loss retires only its owned entry and emits sanitized local code", async () => {
	const e = fresh(),
		a = e.plugin("a"),
		b = e.plugin("b"),
		own = [],
		foreign = [];
	a.events.on("compat.modules.lost", payload => own.push(json(payload)));
	b.events.on("compat.modules.lost", payload => foreign.push(json(payload)));
	await issued(e, a, A);
	await issued(e, b, B);
	e.advance(240000);
	const touches = e.sent("decky_code_touch");
	e.answer(
		touches.find(call => call.arg.token === A),
		{ error: "PRIVATE_ROOT", code: "code_expired" },
	);
	e.answer(
		touches.find(call => call.arg.token === B),
		{ renewed: true, ttl: 600 },
	);
	await flush();
	assert.deepStrictEqual(own, [{ code: "code_expired" }]);
	assert.deepStrictEqual(foreign, []);
	assert.deepStrictEqual(
		cleanupCalls(e).map(call => call.arg.token),
		[A],
	);
	e.advance(240000);
	assert.deepStrictEqual(
		e
			.sent("decky_code_touch")
			.slice(2)
			.map(call => call.arg.token),
		[B],
	);
	e.answer(e.sent("decky_code_touch").at(-1), { renewed: true, ttl: 600 });
	await flush();
});

t("malformed touch and revoke receipts fail closed and retire their capability", async () => {
	for (const method of ["touch", "revoke"]) {
		const e = fresh(),
			api = e.plugin("a");
		await issued(e, api);
		const pending = api.compat.modules[method](A);
		e.answer(
			e.sent("decky_code_" + method).at(-1),
			method === "touch" ? { renewed: true, ttl: 601 } : { revoked: false },
		);
		await assert.rejects(pending, error => error.code === "bad_response");
		assert.ok(cleanupCalls(e).some(call => call.arg.token === A));
		await assert.rejects(api.compat.modules.touch(A), error => error.code === "not_owned");
	}
});

t("disposal revokes its owned lease through the private cleanup route and stops heartbeat", async () => {
	const e = fresh(),
		api = e.plugin("a");
	await issued(e, api, A);
	e.window.Framey.dispose("a", TOK);
	const revokes = cleanupCalls(e);
	assert.deepStrictEqual(
		revokes.map(call => call.arg.token),
		[A],
	);
	assert.ok(revokes.every(call => call.t === "tok-a-0000" && call.arg.pid === "a"));
	assert.strictEqual(e.clock.pending(), 0);
	await assert.rejects(api.compat.modules.issue(), error => error.code === "disposed");
	await assert.rejects(api.compat.modules.touch(A), error => error.code === "disposed");
	await assert.rejects(api.compat.modules.revoke(A), error => error.code === "disposed");
	e.advance(1000000);
	assert.strictEqual(e.sent("decky_code_touch").length, 0);
});

t("an issue answer awaiting delivery is revoked when disposal wins", async () => {
	const e = fresh(),
		api = e.plugin("a");
	const pending = api.compat.modules.issue();
	e.answer(e.sent("decky_code_issue")[0], receipt());
	e.window.Framey.dispose("a", TOK);
	await assert.rejects(pending, error => error.code === "disposed");
	assert.deepStrictEqual(json(cleanupCalls(e).map(call => call.arg)), [{ pid: "a", token: A }]);
});

t("a genuinely late native issue answer is retired after disposal by transport cleanup", async () => {
	const e = fresh(),
		api = e.plugin("a");
	const pending = api.compat.modules.issue();
	const request = e.sent("decky_code_issue")[0];
	e.window.Framey.dispose("a", TOK);
	e.answer(request, receipt());
	await assert.rejects(pending, error => error.code === "disposed");
	assert.ok(cleanupCalls(e).some(call => call.arg.token === A));
});

t("replacement rejects old renewal replies and does not notify the replacement graph", async () => {
	const e = fresh(),
		old = e.plugin("a"),
		lost = [];
	await issued(e, old);
	const pending = old.compat.modules.touch(A);
	e.answer(e.sent("decky_code_touch")[0], { renewed: true, ttl: 600 });
	const replacement = e.plugin("a");
	replacement.events.on("compat.modules.lost", payload => lost.push(json(payload)));
	await assert.rejects(pending, error => error.code === "disposed");
	assert.deepStrictEqual(lost, []);
	assert.strictEqual(e.clock.pending(), 0);
});

t("a late old issue cleanup never retires the replacement API's fresh token", async () => {
	const e = fresh(),
		old = e.plugin("a");
	const oldPending = old.compat.modules.issue();
	const oldRequest = e.sent("decky_code_issue")[0];
	e.window.Framey.dispose("a", TOK);
	const replacement = e.plugin("a");
	const freshPending = replacement.compat.modules.issue();
	e.answer(e.sent("decky_code_issue").at(-1), receipt(B));
	assert.strictEqual((await freshPending).token, B);
	e.answer(oldRequest, receipt(A));
	await assert.rejects(oldPending, error => error.code === "disposed");
	assert.deepStrictEqual(
		cleanupCalls(e).map(call => call.arg.token),
		[A],
	);
	const touch = replacement.compat.modules.touch(B);
	e.answer(e.sent("decky_code_touch").at(-1), { renewed: true, ttl: 600 });
	await touch;
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
