"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom, TOK } = require("./harness");

const receipt = token => {
	const root = "http://127.0.0.1:32145/g/" + token + "/";
	return { root, entry: root + "dist/index.js", token, ttl: 600 };
};
const setup = t => {
	const e = makeDom({ loadFiles: ["fy-api.js"] });
	t.after(() => e.window.close());
	return e;
};

test("unresolved module issues reserve late-cleanup capacity without eviction", async t => {
	const e = setup(t),
		api = e.plugin("a");
	const pending = Array.from({ length: 128 }, () =>
		api._rpc("decky_code_issue", { pid: "a" }).catch(error => error.code),
	);
	const requests = e.sent("decky_code_issue").slice();
	assert.equal(requests.length, 128);
	const overflow = api._rpc("decky_code_issue", { pid: "a" });
	if (e.sent("decky_code_issue").length > 128)
		e.answer(e.sent("decky_code_issue").at(-1), { error: "native overload", code: "native_overload" });
	await assert.rejects(overflow, error => error.code === "busy");
	assert.equal(e.sent("decky_code_issue").length, 128);
	e.window.Framey.dispose("a", TOK);
	assert.ok((await Promise.all(pending)).every(code => code === "disposed"));
	const replacement = e.plugin("a");
	await assert.rejects(replacement._rpc("decky_code_issue", { pid: "a" }), error => error.code === "busy");
	const old = requests[0],
		token = "a".repeat(64);
	e.window.Framey._reply("wrong-generation", old.id, receipt(token));
	await assert.rejects(replacement._rpc("decky_code_issue", { pid: "a" }), error => error.code === "busy");
	e.answer(old, receipt(token));
	const cleanup = e.sent("decky_code_revoke").filter(call => call.id === 0);
	assert.equal(cleanup.length, 1);
	assert.equal(cleanup[0].t, "tok-a-0000");
	assert.equal(cleanup[0].arg.pid, "a");
	assert.equal(cleanup[0].arg.token, token);
	const fresh = replacement._rpc("decky_code_issue", { pid: "a" });
	assert.equal(e.sent("decky_code_issue").length, 129);
	e.answer(e.sent("decky_code_issue").at(-1), receipt("b".repeat(64)));
	assert.equal((await fresh).token, "b".repeat(64));
});

test("late cleanup accepts only a matching issue receipt and remains private after full close", async t => {
	const e = setup(t),
		api = e.plugin("a");
	const pending = api._rpc("decky_code_issue", { pid: "a" }).catch(error => error.code);
	const request = e.sent("decky_code_issue")[0];
	e.window.Framey.dispose(undefined, TOK);
	assert.equal(await pending, "disposed");
	e.answer(request, receipt("c".repeat(64)));
	const cleanup = e.sent("decky_code_revoke").filter(call => call.id === 0);
	assert.equal(cleanup.length, 1);
	assert.equal(cleanup[0].arg.token, "c".repeat(64));
	e.answer(request, receipt("d".repeat(64)));
	assert.equal(e.sent("decky_code_revoke").length, 1);
	assert.equal(e.window.__S.K.pending(), 0);
});

test("timed-out issuance still retires its exact late grant", async t => {
	const e = setup(t),
		api = e.plugin("a");
	const pending = api._rpc("decky_code_issue", { pid: "a" });
	const rejected = assert.rejects(pending, /did not reply/);
	const request = e.sent("decky_code_issue")[0];
	e.clock.tick(15001);
	await rejected;
	e.answer(request, receipt("e".repeat(64)));
	assert.equal(e.sent("decky_code_revoke").filter(call => call.id === 0).length, 1);
	assert.equal(e.window.__S.K.pending(), 0);
});

test("reply identifiers cannot coerce into another issue's cleanup reservation", async t => {
	const e = setup(t),
		api = e.plugin("a");
	const pending = api._rpc("decky_code_issue", { pid: "a" }).catch(error => error.code);
	const request = e.sent("decky_code_issue")[0];
	for (const id of [String(request.id), { toString: () => String(request.id) }, "__proto__", true, 0])
		e.window.Framey._reply(request.gen, id, receipt("f".repeat(64)));
	assert.equal(e.window.__S.K.pending(), 1);
	e.window.Framey.dispose("a", TOK);
	assert.equal(await pending, "disposed");
	assert.equal(e.sent("decky_code_revoke").length, 0);
	e.answer(request, receipt("f".repeat(64)));
	assert.equal(e.sent("decky_code_revoke").length, 1);
});
