"use strict";
const assert = require("node:assert/strict"),
	fs = require("node:fs"),
	path = require("node:path");
const { test } = require("node:test");
const { makeDom, ROOT, TOK } = require("./harness");
const tick = () => new Promise(r => setImmediate(r));
function setup(t) {
	const e = makeDom({ loadFiles: [] });
	if (fs.existsSync(path.join(ROOT, "fy-optional.js"))) e.load("fy-optional.js");
	e.load("fy-api.js");
	t.after(() => e.window.close());
	return e;
}
const source = `function(){window.factoryRuns=(window.factoryRuns||0)+1;return {abi:1,attach(c){window.clientContexts=(window.clientContexts||[]);window.clientContexts.push(c);return {modules:{issue(){return c.request('decky_code_issue',{pid:'foreign'});},touch(){return Promise.resolve();},revoke(){return Promise.resolve();}},mount(){return new Promise(resolve=>{window.finishMount=resolve;});},dispose(){window.clientDisposals=(window.clientDisposals||0)+1;}};},store(c){return {dispose(){window.storeDisposals=(window.storeDisposals||0)+1;}};}}}`;
const reply = (e, c, g = 1, s = source) => e.answer(c, { abi: 1, generation: g, version: "1.0.0", source: s });
test("native registration remains usable and does not activate optional code after extension freeze", t => {
	const e = setup(t),
		a = e.plugin("native");
	assert.equal(typeof a.call, "function");
	assert.equal(typeof a.cleanup, "function");
	assert.ok(Object.isFrozen(e.window.Framey.ext));
	assert.equal(typeof e.window.__S.K.optionalRuntime?.ensure, "function");
	assert.equal(e.sent("decky_runtime_frontend").length, 0);
	assert.equal(e.window.factoryRuns, undefined);
});
test("concurrent late enable resolves one verified provider without reopening extensions", async t => {
	const e = setup(t);
	e.plugin("native");
	const k = e.window.__S.K.optionalRuntime;
	assert.ok(k);
	const a = k.ensure("decky", 1),
		b = k.ensure("decky", 1);
	await tick();
	assert.equal(e.sent("decky_runtime_frontend").length, 1);
	reply(e, e.sent("decky_runtime_frontend")[0]);
	const [x, y] = await Promise.all([a, b]);
	assert.equal(x, y);
	assert.equal(e.window.factoryRuns, 1);
	assert.ok(Object.isFrozen(e.window.Framey.ext));
});
test("optional client requests retain plugin token and replace caller-selected owner", async t => {
	const e = setup(t),
		a = e.plugin("owner");
	const pending = a.compat.modules.issue();
	await tick();
	const r = e.sent("decky_runtime_frontend")[0];
	assert.ok(r, "facade must request the admitted runtime");
	reply(e, r);
	await tick();
	const call = e.sent("decky_code_issue")[0];
	assert.ok(call);
	assert.equal(call.t, "tok-owner-0000");
	assert.notEqual(call.t, TOK);
	assert.deepEqual(call.arg, { pid: "owner" });
	e.answer(call, { own: true });
	assert.deepEqual(JSON.parse(JSON.stringify(await pending)), { own: true });
	assert.equal(Object.values(e.window.clientContexts[0]).includes(TOK), false);
});
test("invalidation rejects pending activation and never evaluates its late source", async t => {
	const e = setup(t),
		k = e.window.__S.K.optionalRuntime;
	assert.ok(k);
	const pending = k.ensure("decky", 1);
	const rejected = assert.rejects(pending, x => x.code === "runtime_unavailable");
	await tick();
	const r = e.sent("decky_runtime_frontend")[0];
	k.invalidate({ generation: 2, enabled: false });
	await rejected;
	reply(e, r, 1);
	await tick();
	assert.equal(e.window.factoryRuns, undefined);
});
test("runtime invalidation rejects pending mounts immediately and disposes late returned hosts", async t => {
	const e = setup(t),
		a = e.plugin("owner"),
		box = e.document.createElement("div");
	e.document.body.append(box);
	const pending = a.compat.mount(box, { v: 1 });
	const rejected = assert.rejects(pending, x => x.code === "runtime_unavailable");
	await tick();
	reply(e, e.sent("decky_runtime_frontend")[0]);
	await tick();
	assert.equal(typeof e.window.finishMount, "function");
	e.window.__S.K.optionalRuntime.invalidate({ generation: 2, enabled: false });
	await rejected;
	let disposed = 0;
	e.window.finishMount({
		status() {
			return {};
		},
		dispose() {
			disposed++;
		},
	});
	await tick();
	assert.equal(disposed, 1);
	assert.equal(e.window.clientDisposals, 1);
});
test("plugin disposal cannot attach a late runtime client while native sibling survives", async t => {
	const e = setup(t),
		a = e.plugin("decky"),
		n = e.plugin("native");
	let nativeCleaned = 0;
	n.cleanup(() => nativeCleaned++);
	const pending = a.compat.modules.issue(),
		rejected = assert.rejects(pending, x => x.code === "disposed");
	await tick();
	const r = e.sent("decky_runtime_frontend")[0];
	e.window.Framey.dispose("decky", TOK);
	await rejected;
	reply(e, r);
	await tick();
	assert.equal(e.window.clientContexts?.length || 0, 0);
	assert.equal(nativeCleaned, 0);
	assert.equal(typeof n.call, "function");
});
test("disabled delivery never evaluates code and unknown factories cannot be supplied by caller", async t => {
	const e = setup(t),
		k = e.window.__S.K.optionalRuntime;
	assert.ok(k);
	await assert.rejects(k.ensure("foreign", 1), x => x.code === "runtime_unavailable");
	assert.equal(e.calls.length, 0);
	const pending = k.ensure("decky", 1),
		rejected = assert.rejects(pending, x => x.code === "runtime_disabled");
	await tick();
	e.answer(e.sent("decky_runtime_frontend")[0], { error: "off", code: "runtime_disabled" });
	await rejected;
	assert.equal(e.window.factoryRuns, undefined);
});
test("new runtime generation replaces cached provider without cancelling its own admission", async t => {
	const e = setup(t),
		k = e.window.__S.K.optionalRuntime;
	const first = k.ensure("decky", 1);
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 1);
	const old = await first;
	const next = k.ensure("decky", 1);
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 2);
	const fresh = await next;
	assert.equal(fresh.generation, 2);
	assert.equal(k.current(old.generation), false);
	assert.equal(e.window.factoryRuns, 2);
});
test("runtime invalidation rejects pending Store attachment and disposes late Store handles", async t => {
	const e = setup(t),
		k = e.window.__S.K.optionalRuntime;
	const s = `function(){return {abi:1,attach(){},store(){return new Promise(resolve=>window.finishStore=resolve);}}}`;
	const pending = k.store({ alive: () => true });
	const rejected = assert.rejects(pending, x => x.code === "runtime_unavailable");
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 1, s);
	await tick();
	assert.equal(typeof e.window.finishStore, "function");
	k.invalidate({ generation: 2 });
	await rejected;
	let disposed = 0;
	e.window.finishStore({
		dispose() {
			disposed++;
		},
	});
	await tick();
	assert.equal(disposed, 1);
});
test("a failed fresh admission retires already attached optional clients", async t => {
	const e = setup(t),
		a = e.plugin("owner");
	const first = a.compat.modules.issue();
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1));
	await tick();
	e.answer(e.sent("decky_code_issue").at(-1), { own: true });
	await first;
	const next = a.compat.modules.issue();
	const rejected = assert.rejects(next, x => ["runtime_disabled", "runtime_unavailable"].includes(x.code));
	await tick();
	e.answer(e.sent("decky_runtime_frontend").at(-1), { error: "off", code: "runtime_disabled" });
	await rejected;
	assert.equal(e.window.clientDisposals, 1);
	assert.equal(e.window.__S.K.optionalRuntime.current(1), false);
});
test("the mount that discovers a new generation survives replacement of an older cached provider", async t => {
	const e = setup(t),
		a = e.plugin("owner");
	const first = a.compat.modules.issue();
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 1);
	await tick();
	e.answer(e.sent("decky_code_issue").at(-1), { own: true });
	await first;
	const mounted = a.compat.mount(e.document.createElement("div"), { v: 1 });
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 2);
	await tick();
	let disposed = 0;
	e.window.finishMount({
		dispose() {
			disposed++;
		},
	});
	await mounted;
	assert.equal(disposed, 0);
});
test("actual extracted client retires an issue delivered after runtime invalidation using its owner token", async t => {
	const e = setup(t),
		a = e.plugin("owner");
	const actual =
		"function(){" +
		fs.readFileSync(path.join(ROOT, "decky_runtime/client.js"), "utf8") +
		";return createFrameyDeckyProvider();}";
	const pending = a.compat.modules.issue(),
		rejected = assert.rejects(pending, x => x.code === "runtime_unavailable");
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 1, actual);
	await tick();
	const issued = e.sent("decky_code_issue").at(-1);
	assert.ok(issued);
	e.window.__S.K.optionalRuntime.invalidate({ generation: 2, enabled: false });
	await rejected;
	const token = "a".repeat(64),
		root = "http://127.0.0.1:32145/g/" + token + "/";
	e.answer(issued, { token, root, entry: root + "dist/index.js", ttl: 600 });
	await tick();
	const cleanup = e.sent("decky_code_revoke").at(-1);
	assert.equal(cleanup.id, 0);
	assert.equal(cleanup.t, "tok-owner-0000");
	assert.deepEqual(cleanup.arg, { pid: "owner", token });
	assert.equal(e.clock.pending(), 0);
});
test("throwing Store disposers and diagnostics cannot interrupt invalidation", async t => {
	const e = setup(t),
		k = e.window.__S.K.optionalRuntime;
	const s =
		'function(){return {abi:1,attach(){},store(){return {dispose(){throw Error("owned disposer failure");}};}}}';
	const pending = k.store({ alive: () => true });
	await tick();
	reply(e, e.sent("decky_runtime_frontend").at(-1), 1, s);
	await pending;
	let notified = 0;
	k.subscribe(() => {
		notified++;
	});
	e.window.__S.K.diagnostic = () => {
		throw Error("owned recorder failure");
	};
	assert.doesNotThrow(() => k.invalidate({ generation: 2, enabled: false }));
	assert.equal(notified, 1);
	assert.equal(k.current(1), false);
});
