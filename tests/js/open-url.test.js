"use strict";
const assert = require("assert");
const { makeDom, TOK } = require("./harness");

const tests = [];
const t = (name, fn) => tests.push([name, fn]);
const fresh = options => makeDom(Object.assign({ loadFiles: ["fy-api.js"] }, options));
const json = value => JSON.parse(JSON.stringify(value));

t("openURL requests the native core with its plugin token and immutable owner", async () => {
	const e = fresh();
	const api = e.plugin("owner");
	assert.throws(() => {
		api.id = "foreign";
	}, TypeError);
	const pending = api.openURL("https://example.org/path?q=a%20b#section");
	assert.strictEqual(typeof pending.then, "function");
	const request = e.sent("open_url")[0];
	assert.strictEqual(request.plugin, "_core");
	assert.strictEqual(request.t, "tok-owner-0000");
	assert.notStrictEqual(request.t, TOK);
	assert.deepStrictEqual(json(request.arg), {
		url: "https://example.org/path?q=a%20b#section",
		pid: "owner",
	});
	assert.ok(request.id > 0);
	e.answer(request, { requested: true });
	assert.deepStrictEqual(json(await pending), { requested: true });
});

t("openURL cannot navigate the document or call a Steam browser global", async () => {
	const e = fresh({
		before: w => {
			const forbidden = () => {
				throw new Error("Steam or window navigation attempted");
			};
			w.SteamClient = { System: { OpenInSystemBrowser: forbidden }, Browser: { OpenURL: forbidden } };
			w.open = forbidden;
		},
	});
	const pending = e.plugin("owner").openURL("https://example.org/");
	assert.strictEqual(e.window.location.href, "https://steamloopback.host/");
	assert.strictEqual(e.sent("open_url").length, 1);
	e.answer(e.sent("open_url")[0], { requested: true });
	assert.deepStrictEqual(json(await pending), { requested: true });
});

t("openURL keeps different plugins' native requests scoped separately", async () => {
	const e = fresh();
	const first = e.plugin("a").openURL("http://example.org/a");
	const second = e.plugin("b").openURL("https://example.org/b");
	const requests = e.sent("open_url");
	assert.deepStrictEqual(
		requests.map(r => [r.t, r.arg.pid]),
		[
			["tok-a-0000", "a"],
			["tok-b-0000", "b"],
		],
	);
	requests.forEach(r => e.answer(r, { requested: true }));
	await Promise.all([first, second]);
});

t("openURL rejects native errors with their code even alongside a success receipt", async () => {
	const e = fresh();
	const pending = e.plugin("a").openURL("https://example.org/");
	e.answer(e.sent("open_url")[0], {
		requested: true,
		error: "Browser handler unavailable",
		code: "open_url_unavailable",
	});
	await assert.rejects(pending, err => err.code === "open_url_unavailable" && /handler unavailable/.test(err.message));
});

t("openURL rejects every malformed native receipt", async () => {
	for (const receipt of [
		null,
		false,
		true,
		"https://example.org/",
		[],
		{},
		{ requested: false },
		{ requested: 1 },
		{ ok: true },
		{ requested: true, error: "" },
		{ requested: true, error: null },
		{ requested: true, error: false },
	]) {
		const e = fresh();
		const pending = e.plugin("a").openURL("https://example.org/");
		e.answer(e.sent("open_url")[0], receipt);
		await assert.rejects(pending, err => !!err && typeof err.message === "string", JSON.stringify(receipt));
		assert.strictEqual(e.window.Framey.diag().calls, 0);
	}
});

t("openURL rejects transport failure and releases the pending call", async () => {
	const e = fresh();
	e.onCall(() => {
		throw new Error("native transport unavailable");
	});
	await assert.rejects(e.plugin("a").openURL("https://example.org/"), /native transport unavailable/);
	assert.strictEqual(e.window.Framey.diag().calls, 0);
	assert.strictEqual(e.clock.pending(), 0);
});

t("openURL does not retry an unavailable native capability", async () => {
	const e = fresh();
	const pending = e.plugin("a").openURL("https://example.org/");
	e.answer(e.sent("open_url")[0], { error: "unsupported", code: "unsupported" });
	await assert.rejects(pending, err => err.code === "unsupported");
	e.clock.tick(120000);
	assert.strictEqual(e.calls.length, 1);
});

t("openURL called after disposal rejects without sending", async () => {
	const e = fresh();
	const api = e.plugin("a");
	e.window.Framey.dispose("a", TOK);
	await assert.rejects(api.openURL("https://example.org/"), err => err.code === "disposed");
	assert.strictEqual(e.sent("open_url").length, 0);
});

t("openURL pending during disposal rejects and ignores its late reply", async () => {
	const e = fresh();
	const pending = e.plugin("a").openURL("https://example.org/");
	const request = e.sent("open_url")[0];
	e.window.Framey.dispose("a", TOK);
	e.answer(request, { requested: true });
	await assert.rejects(pending, err => err.code === "disposed");
	assert.strictEqual(e.window.Framey.diag().calls, 0);
});

t("openURL observes disposal after a reply but before delivery", async () => {
	const e = fresh();
	const pending = e.plugin("a").openURL("https://example.org/");
	e.answer(e.sent("open_url")[0], { requested: true });
	e.window.Framey.dispose("a", TOK);
	await assert.rejects(pending, err => err.code === "disposed");
});

t("replacing a module rejects delivery to its old openURL handle", async () => {
	const e = fresh();
	const pending = e.plugin("a").openURL("https://example.org/");
	e.answer(e.sent("open_url")[0], { requested: true });
	e.plugin("a");
	await assert.rejects(pending, err => err.code === "disposed");
});

(async () => {
	let failures = 0;
	for (const [name, fn] of tests) {
		try {
			await fn();
			console.log("ok   " + name);
		} catch (err) {
			failures++;
			console.log(
				"FAIL " +
					name +
					"\n     " +
					String((err && err.stack) || err)
						.split("\n")
						.slice(0, 6)
						.join("\n     "),
			);
		}
	}
	console.log("\n" + (tests.length - failures) + " passed, " + failures + " failed, " + tests.length + " total");
	process.exitCode = failures ? 1 : 0;
})();
