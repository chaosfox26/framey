import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const token = "a".repeat(64),
	otherToken = "b".repeat(64);
let server,
	origin,
	pageServer,
	pageOrigin,
	requests = [],
	revoked = false;
const files = {
	"dist/index.js": `import {count,bump,bValue} from './a.js';import {readA,sharedB} from './b.js';import {shared} from './shared.js';export {count,bump,bValue,readA,shared,sharedB};export const meta=import.meta.url;export const lazy=()=>import('./lazy.js');export default()=>({content:SP_REACT.createElement(function Panel(){const[text,setText]=SP_REACT.useState('Graph panel');return SP_REACT.createElement('button',{onClick:async()=>{const mod=await import('./lazy.js');setText('Lazy '+mod.count());}},text);})});`,
	"dist/a.js": `import {readA} from './b.js';export let count=0;export function bump(){count++;}export function bValue(){return readA();}`,
	"dist/b.js": `import {count} from './a.js';import {shared} from './shared.js';export function readA(){return count;}export const sharedB=shared;`,
	"dist/shared.js": `globalThis.sharedLoads=(globalThis.sharedLoads||0)+1;export const shared={marker:'shared'};`,
	"dist/lazy.js": `import {shared} from './shared.js';import {count as current} from './a.js';globalThis.lazyLoads=(globalThis.lazyLoads||0)+1;export {shared};export function count(){return current;}`,
	"dist/tla.js": `await new Promise(()=>{});export default()=>({content:'Never mounted'});`,
	"dist/bare.js": `import 'react';export default()=>({content:'Must not mount'});`,
	"dist/probes.js": `export const missing=()=>import('./missing.js');export const foreign=()=>import('../../${otherToken}/dist/index.js');export const external=()=>import('https://example.invalid/module.js');export const bare=()=>import('react');`,
};
test.beforeAll(async () => {
	server = createServer((req, res) => {
		requests.push(req.url);
		res.setHeader("Access-Control-Allow-Origin", "*");
		res.setHeader("Cache-Control", "no-store");
		res.setHeader("Content-Type", "text/javascript");
		const prefix = "/g/" + token + "/";
		if (!req.url.startsWith(prefix)) {
			res.writeHead(404).end();
			return;
		}
		if (revoked) {
			res.writeHead(410).end();
			return;
		}
		const body = files[req.url.slice(prefix.length)];
		if (body === undefined) res.writeHead(404).end();
		else res.end(body);
	});
	await new Promise(done => server.listen(0, "127.0.0.1", done));
	origin = "http://127.0.0.1:" + server.address().port;
	pageServer = createServer((_req, res) => {
		res.setHeader("Content-Type", "text/html");
		res.end('<!doctype html><div id="panel" style="width:500px;height:500px"></div>');
	});
	await new Promise(done => pageServer.listen(0, "127.0.0.1", done));
	pageOrigin = "http://127.0.0.1:" + pageServer.address().port;
});
test.afterAll(async () => {
	await Promise.all([new Promise(done => server.close(done)), new Promise(done => pageServer.close(done))]);
});
test.beforeEach(() => {
	requests = [];
	revoked = false;
});
const source = () =>
	["framey-view.js", "decky-modules.js", "decky-host.js"]
		.map(name =>
			existsSync(resolve(root, "decky_runtime/compat", name))
				? readFileSync(resolve(root, "decky_runtime/compat", name), "utf8")
				: "",
		)
		.join("\n") +
	"\nwindow.makeModules=typeof createFrameyDeckyModules==='function'?createFrameyDeckyModules:undefined;window.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;";
async function prepare(page, { entry = "dist/index.js", deferIssue = false } = {}) {
	await page.route("**/*", route => {
		const url = route.request().url();
		return url.startsWith(pageOrigin + "/") || url.startsWith(origin + "/") ? route.continue() : route.abort();
	});
	await page.goto(pageOrigin + "/");
	await page.addScriptTag({ content: source() });
	expect(await page.evaluate(() => typeof window.makeModules)).toBe("function");
	await page.exposeFunction("serverRevoke", () => {
		revoked = true;
	});
	await page.evaluate(
		({ origin, token, entry, deferIssue }) => {
			window.lease = {
				root: origin + "/g/" + token + "/",
				entry: origin + "/g/" + token + "/" + entry,
				token,
				ttl: 600,
			};
			window.issueCalls = 0;
			window.revocations = [];
			window.errors = [];
			window.losses = [];
			window.listeners = new Set();
			window.statuses = [];
			window.api = {
				call: async () => true,
				cleanup() {},
				events: {
					on(name, fn) {
						if (name !== "compat.modules.lost") throw Error("Wrong local event");
						window.listeners.add(fn);
						return () => window.listeners.delete(fn);
					},
				},
				compat: {
					modules: {
						issue() {
							window.issueCalls++;
							return deferIssue
								? new Promise(resolve => (window.finishIssue = () => resolve(window.lease)))
								: Promise.resolve(window.lease);
						},
						touch: async () => ({ renewed: true, ttl: 600 }),
						revoke: async token => {
							window.revocations.push(token);
							await window.serverRevoke();
							return { revoked: true };
						},
					},
				},
			};
			window.emitLoss = code => {
				for (const fn of [...window.listeners]) fn({ code });
			};
			window.graph = window.makeModules({
				api: window.api,
				onError: error => window.errors.push(error.code),
				onLost: error => window.losses.push(error.code),
			});
			window.makeRealm = () => {
				const frame = document.createElement("iframe");
				document.getElementById("panel").append(frame);
				const realm = frame.contentWindow;
				realm.document.open();
				realm.document.write(
					"<!doctype html><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none';script-src 'unsafe-eval' blob: " +
						window.lease.root +
						";connect-src 'none';base-uri 'none'\"><body></body>",
				);
				realm.document.close();
				return realm;
			};
		},
		{ origin, token, entry, deferIssue },
	);
}

// Native module cache, cycles and live exports must survive unchanged source and lazy imports.
test("HTTP graph keeps native cycles live bindings lazy identity and import.meta", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		const lease = await window.graph.acquire(),
			realm = window.makeRealm(),
			ns = await window.graph.import(realm, lease);
		const before = realm.lazyLoads || 0;
		ns.bump();
		const lazy = await ns.lazy(),
			again = await ns.lazy();
		return {
			before,
			count: ns.count,
			cycle: ns.bValue(),
			other: ns.readA(),
			shared: ns.shared === ns.sharedB && ns.shared === lazy.shared,
			lazy: lazy === again,
			sharedLoads: realm.sharedLoads,
			lazyLoads: realm.lazyLoads,
			meta: ns.meta,
			status: window.graph.status(),
		};
	});
	expect(result).toMatchObject({
		before: 0,
		count: 1,
		cycle: 1,
		other: 1,
		shared: true,
		lazy: true,
		sharedLoads: 1,
		lazyLoads: 1,
		meta: origin + "/g/" + token + "/dist/index.js",
		status: {
			scope: "owned-http-graph",
			state: "loaded",
			ttlSeconds: 600,
			renewEverySeconds: 240,
			longSleepMayExpire: true,
			reopenRequiredAfterLoss: true,
		},
	});
	expect(requests.filter(path => path.endsWith("/lazy.js"))).toHaveLength(1);
	await page.evaluate(() => {
		window.graph.dispose();
		document.querySelector("iframe").remove();
	});
});

// Paths outside the exact capability root must be rejected by CSP before their HTTP request.
test("exact root CSP and CORS allow owned imports but no foreign external or bare modules", async ({ page }) => {
	await prepare(page);
	await page.evaluate(() => {
		window.lease.entry = window.lease.root + "dist/probes.js";
	});
	// The only public entry is dist/index.js: the fixture switches its bytes, not the descriptor path.
	const original = files["dist/index.js"];
	files["dist/index.js"] = files["dist/probes.js"];
	try {
		await page.evaluate(() => {
			window.lease.entry = window.lease.root + "dist/index.js";
		});
		const result = await page.evaluate(async () => {
			const lease = await window.graph.acquire(),
				ns = await window.graph.import(window.makeRealm(), lease);
			const errors = [];
			for (const name of ["missing", "foreign", "external", "bare"])
				try {
					await ns[name]();
					errors.push("resolved");
				} catch (error) {
					errors.push(error.name);
				}
			return errors;
		});
		expect(result).toEqual(["TypeError", "TypeError", "TypeError", "TypeError"]);
		expect(requests).toContain("/g/" + token + "/dist/missing.js");
		expect(requests.some(path => path.includes(otherToken))).toBe(false);
	} finally {
		files["dist/index.js"] = original;
	}
});

// An injected descriptor cannot enlarge the host's script policy or change its fixed entry.
test("malformed lease descriptors are refused before allocating a realm", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		const bad = [
			{ root: "https://127.0.0.1:123/g/" + window.lease.token + "/" },
			{ root: window.lease.root + '\"; script-src *' },
			{ entry: window.lease.root + "dist/other.js" },
			{ token: "B".repeat(64) },
			{ ttl: 601 },
			{ root: "http://127.0.0.1:65536/g/" + window.lease.token + "/" },
		];
		const codes = [];
		window.graph.dispose();
		for (const patch of bad) {
			const api = {
				...window.api,
				compat: { modules: { ...window.api.compat.modules, issue: async () => ({ ...window.lease, ...patch }) } },
			};
			const graph = window.makeModules({ api, onError() {} });
			try {
				await graph.acquire();
				codes.push("resolved");
			} catch (error) {
				codes.push(error.code);
			}
			graph.dispose();
		}
		return codes;
	});
	expect(result).toEqual(Array(6).fill("module_graph_invalid"));
	expect(requests).toEqual([]);
});

// Disposal must reject the owned wait immediately and release a token returned after closure.
test("pending acquisition shares one issue and revokes late lease without callbacks", async ({ page }) => {
	await prepare(page, { deferIssue: true });
	const first = await page.evaluate(async () => {
		window.first = window.graph.acquire().then(
			() => "resolved",
			error => error.code,
		);
		window.second = window.graph.acquire().then(
			() => "resolved",
			error => error.code,
		);
		window.graph.dispose();
		return Promise.all([window.first, window.second]);
	});
	expect(first).toEqual(["module_graph_disposed", "module_graph_disposed"]);
	await page.evaluate(() => window.finishIssue());
	await expect.poll(() => page.evaluate(() => window.revocations.length)).toBe(1);
	expect(
		await page.evaluate(() => ({
			issues: window.issueCalls,
			listeners: window.listeners.size,
			errors: window.errors,
			losses: window.losses,
		})),
	).toEqual({ issues: 1, listeners: 0, errors: [], losses: [] });
});

// Native TLA can remain pending; the independently owned import wait still has to settle on close.
test("dispose rejects a pending native import and late module loss is inert", async ({ page }) => {
	await prepare(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] = files["dist/tla.js"];
	try {
		await page.evaluate(async () => {
			const lease = await window.graph.acquire();
			window.pendingImport = window.graph.import(window.makeRealm(), lease).then(
				() => "resolved",
				error => error.code,
			);
		});
		await expect.poll(() => requests.some(path => path.endsWith("/index.js"))).toBe(true);
		const result = await page.evaluate(async () => {
			window.graph.dispose();
			document.querySelector("iframe").remove();
			window.emitLoss("code_expired");
			return window.pendingImport;
		});
		expect(result).toBe("module_graph_disposed");
		expect(await page.evaluate(() => window.losses)).toEqual([]);
	} finally {
		files["dist/index.js"] = original;
	}
});

// SDK expiry is a lost lease, not a promise that the graph remains available forever.
test("lease loss cancels pending import once and reports expiration without token data", async ({ page }) => {
	await prepare(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] = files["dist/tla.js"];
	try {
		const result = await page.evaluate(async () => {
			const lease = await window.graph.acquire();
			const pending = window.graph.import(window.makeRealm(), lease).catch(error => error.code);
			setTimeout(() => {
				window.emitLoss("code_expired");
				window.emitLoss("code_expired");
			}, 25);
			return {
				code: await pending,
				status: window.graph.status(),
				losses: window.losses,
				listeners: window.listeners.size,
			};
		});
		expect(result).toMatchObject({
			code: "code_expired",
			losses: ["code_expired"],
			listeners: 0,
			status: { state: "lost", errorCode: "code_expired", reopenRequiredAfterLoss: true },
		});
		expect(JSON.stringify(result)).not.toContain(token);
	} finally {
		files["dist/index.js"] = original;
		await page.evaluate(() => {
			window.graph.dispose();
			document.querySelector("iframe")?.remove();
		});
	}
});

// Native resolution errors must not publish capability URLs or become a successful empty module.
test("bare static imports fail explicitly with sanitized graph status", async ({ page }) => {
	await prepare(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] = files["dist/bare.js"];
	try {
		const result = await page.evaluate(async () => {
			const lease = await window.graph.acquire();
			try {
				await window.graph.import(window.makeRealm(), lease);
			} catch (error) {
				return { code: error.code, message: error.message, status: window.graph.status(), errors: window.errors };
			}
		});
		expect(result).toMatchObject({
			code: "module_import_failed",
			message: "Module graph unavailable: module_import_failed",
			errors: ["module_import_failed"],
			status: { state: "failed", unsupported: ["modules.import"] },
		});
		expect(JSON.stringify(result)).not.toContain(token);
	} finally {
		files["dist/index.js"] = original;
		await page.evaluate(() => {
			window.graph.dispose();
			document.querySelector("iframe")?.remove();
		});
	}
});

async function startHost(page, { deferIssue = false, factoryMode = "normal" } = {}) {
	await prepare(page, { deferIssue });
	await page.evaluate(factoryMode => {
		window.graph.dispose();
		window.revocations = [];
		window.statuses = [];
		window.hostDisposed = 0;
		window.host = window.makeHost(window.api, {
			name: "Example",
			apiVersion: 2,
			moduleGraph: true,
			createModules: window.makeModules,
			createViewRuntime: window.makeView,
			onStatus: status => window.statuses.push(status),
		});
		if (factoryMode === "malformed")
			window.host = window.makeHost(window.api, {
				name: "Example",
				apiVersion: 2,
				moduleGraph: true,
				createViewRuntime: window.makeView,
				createModules: () => ({
					acquire: async () => ({
						...window.lease,
						root: "http://127.0.0.1:1/g/" + window.lease.token + '/"; script-src *',
					}),
					import: async () => {
						window.badImported = true;
					},
					dispose() {
						window.hostDisposed++;
					},
					status: () => ({ scope: "fixture" }),
				}),
			});
	}, factoryMode);
}
// The HTTP namespace must execute in the owned runtime, including unchanged lazy UI code.
test("host installs its globals before original HTTP graph execution and native lazy controls", async ({ page }) => {
	await startHost(page);
	await page.evaluate(() =>
		window.host.load("throw new Error('Blob path must not run')", "esm", document.getElementById("panel")),
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Graph panel", exact: true }).click();
	await expect(frame.getByRole("button", { name: "Lazy 0", exact: true })).toBeVisible();
	const status = await page.evaluate(() => window.host.status());
	expect(status).toMatchObject({
		panel: "mounted",
		steamIntegration: "unavailable",
		moduleGraph: { scope: "owned-http-graph", state: "loaded", ttlSeconds: 600 },
	});
	expect(JSON.stringify(status)).not.toContain(token);
	await page.evaluate(() => window.host.dispose());
	await expect(page.locator("#panel")).toBeEmpty();
	expect(await page.evaluate(() => window.revocations.length)).toBe(1);
});

// Host validation is separate from the adapter: an injected factory cannot widen the CSP.
test("host rejects unsafe graph leases before iframe CSP even from an injected factory", async ({ page }) => {
	await startHost(page, { factoryMode: "malformed" });
	const result = await page.evaluate(async () => {
		try {
			await window.host.load("", "esm", document.getElementById("panel"));
		} catch (error) {
			return {
				message: error.message,
				status: window.host.status(),
				disposed: window.hostDisposed,
				imported: !!window.badImported,
			};
		}
	});
	expect(result).toMatchObject({
		message: "Module graph unavailable: module_graph_invalid",
		status: { panel: "failed" },
		disposed: 1,
		imported: false,
	});
	await expect(page.locator("iframe")).toHaveCount(0);
	expect(requests).toEqual([]);
});

// A first await must not allow a second load, nor let late issue completion republish stale state.
test("host guards pending acquisition and disposal releases late lease with no stale publication", async ({ page }) => {
	await startHost(page, { deferIssue: true });
	const result = await page.evaluate(async () => {
		window.loading = window.host.load("", "esm", document.getElementById("panel")).then(
			() => "resolved",
			error => error.code || error.message,
		);
		let second;
		try {
			await window.host.load("", "esm", document.getElementById("panel"));
			second = "resolved";
		} catch (error) {
			second = error.message;
		}
		window.host.dispose();
		window.statusCount = window.statuses.length;
		return { first: await window.loading, second };
	});
	expect(result.first).toBe("module_graph_disposed");
	expect(result.second).toContain("one plugin");
	await expect(page.locator("iframe")).toHaveCount(0);
	await page.evaluate(() => window.finishIssue());
	await expect.poll(() => page.evaluate(() => window.revocations.length)).toBe(1);
	expect(
		await page.evaluate(() => ({
			issues: window.issueCalls,
			updates: window.statuses.length - window.statusCount,
			status: window.host.status().panel,
		})),
	).toEqual({ issues: 1, updates: 0, status: "disposed" });
});

// Closing while native TLA is pending must settle the owned host load without claiming JS can be aborted.
test("host close settles native TLA load and retires its realm lease and event listener", async ({ page }) => {
	await startHost(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] = files["dist/tla.js"];
	try {
		await page.evaluate(() => {
			window.loading = window.host.load("", "esm", document.getElementById("panel")).then(
				() => "resolved",
				error => error.code,
			);
		});
		await expect.poll(() => requests.some(path => path.endsWith("/index.js"))).toBe(true);
		const result = await page.evaluate(async () => {
			window.host.dispose();
			return { code: await window.loading, status: window.host.status(), listeners: window.listeners.size };
		});
		expect(result).toMatchObject({
			code: "module_graph_disposed",
			listeners: 0,
			status: { panel: "disposed", moduleGraph: { disposed: true, executingNativeCodeAbortable: false } },
		});
		await expect(page.locator("iframe")).toHaveCount(0);
	} finally {
		files["dist/index.js"] = original;
	}
});

// Lease loss closes an already mounted plugin instead of promising future module availability.
test("host lease expiration fails and cleans the mounted graph exactly once", async ({ page }) => {
	await startHost(page);
	await page.evaluate(() => window.host.load("", "esm", document.getElementById("panel")));
	await page.evaluate(() => {
		window.emitLoss("code_expired");
		window.emitLoss("code_expired");
	});
	await expect(page.locator("iframe")).toHaveCount(0);
	expect(await page.evaluate(() => window.host.status())).toMatchObject({
		panel: "failed",
		errors: ["Module graph unavailable: code_expired"],
		moduleGraph: { disposed: true, errorCode: "code_expired" },
	});
	expect(
		await page.evaluate(() => ({ revocations: window.revocations.length, listeners: window.listeners.size })),
	).toEqual({ revocations: 1, listeners: 0 });
});

// An asynchronous original factory can finish late; its result must be dismounted without rendering.
test("host cancels a pending graph factory and dismounts its late result once", async ({ page }) => {
	await startHost(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] =
		`const owner=parent;export default()=>new Promise(resolve=>owner.finishFactory=()=>resolve({content:'Late panel',onDismount:()=>owner.hostDisposed++}));`;
	try {
		await page.evaluate(() => {
			window.loading = window.host.load("", "esm", document.getElementById("panel")).then(
				() => "resolved",
				error => error.code,
			);
		});
		await expect.poll(() => page.evaluate(() => typeof window.finishFactory)).toBe("function");
		expect(
			await page.evaluate(async () => {
				window.host.dispose();
				return window.loading;
			}),
		).toBe("module_graph_disposed");
		await page.evaluate(() => window.finishFactory());
		await expect.poll(() => page.evaluate(() => window.hostDisposed)).toBe(1);
		await expect(page.locator("#panel")).toBeEmpty();
	} finally {
		files["dist/index.js"] = original;
	}
});

// Graph opt-in must not alter the original single-bundle Blob or legacy eval behavior.
test("single modern Blob and legacy IIFE keep their original load paths without a graph lease", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		window.graph.dispose();
		const results = [];
		for (const [source, format] of [
			["export default()=>({content:'Blob unchanged'});", "esm"],
			["(()=>()=>({content:'IIFE unchanged'}))()", "iife"],
		]) {
			const host = window.makeHost(window.api, { name: "Example", createViewRuntime: window.makeView });
			await host.load(source, format, document.getElementById("panel"));
			results.push(document.querySelector("iframe").contentDocument.querySelector("main").textContent);
			host.dispose();
		}
		return { results, issues: window.issueCalls };
	});
	expect(result).toEqual({ results: ["Blob unchanged", "IIFE unchanged"], issues: 0 });
	expect(requests).toEqual([]);
});

// Actual SDK generations must isolate two graph adapters sharing one plugin API.
for (const activeOld of [false, true])
	test(`same API fresh graph replacement cannot revoke its new lease (${activeOld ? "active" : "pending"} predecessor)`, async ({
		page,
	}) => {
		await prepare(page);
		const inject = readFileSync(resolve(root, "inject.js"), "utf8"),
			apiSource = readFileSync(resolve(root, "fy-api.js"), "utf8");
		await page.evaluate(
			({ inject, apiSource }) => {
				window.graph.dispose();
				window.nativeCalls = [];
				window.fyCall = (text => window.nativeCalls.push(JSON.parse(text))).bind(null);
				window.eval(
					"(function(__fyt){" +
						inject +
						"\nwindow.testFY=__FY;window.testTrusted=__trusted;window.testPrivate=__FY._k(__fyt);})(" +
						JSON.stringify("module-test-core") +
						");",
				);
				window.eval(
					"(function(__fyt,__FY,__trusted){" +
						apiSource +
						"\n})(" +
						JSON.stringify("module-test-core") +
						",window.testFY,window.testTrusted);",
				);
				const handle = window.testPrivate.bind("example", "tok-example-0000", { name: "Example" });
				window.realApi = handle.register({ render() {} });
				window.actualLosses = [];
				window.issueReplies = () => window.nativeCalls.filter(call => call.method === "decky_code_issue");
				window.answerIssue = (index, token) => {
					const call = window.issueReplies()[index],
						root = window.lease.root.replace(window.lease.token, token);
					window.Framey._reply(call.gen, call.id, { root, entry: root + "dist/index.js", token, ttl: 600 });
				};
			},
			{ inject, apiSource },
		);
		const result = await page.evaluate(
			async ({ activeOld, token, otherToken }) => {
				const old = window.makeModules({
					api: window.realApi,
					onLost: error => window.actualLosses.push("old:" + error.code),
				});
				const oldWait = old.acquire().then(
					value => value.token,
					error => error.code,
				);
				if (activeOld) {
					window.answerIssue(0, token);
					await oldWait;
				} else old.dispose();
				const newer = window.makeModules({
					api: window.realApi,
					onLost: error => window.actualLosses.push("new:" + error.code),
				});
				const newWait = newer.acquire();
				if (window.issueReplies().length !== 2) return { issues: window.issueReplies().length };
				window.answerIssue(1, otherToken);
				const lease = await newWait;
				if (!activeOld) window.answerIssue(0, token);
				await oldWait;
				await Promise.resolve();
				await Promise.resolve();
				const current = await window.realApi.compat.modules.issue();
				const result = {
					issues: window.issueReplies().length,
					newToken: lease.token,
					cached: current.token,
					newState: newer.status().state,
					losses: window.actualLosses,
					revoked: window.nativeCalls.filter(call => call.method === "decky_code_revoke").map(call => call.arg.token),
				};
				old.dispose();
				newer.dispose();
				window.Framey.dispose("example", "module-test-core");
				return result;
			},
			{ activeOld, token, otherToken },
		);
		expect(result).toMatchObject({
			issues: 2,
			newToken: otherToken,
			cached: otherToken,
			newState: "ready",
			losses: activeOld ? ["old:stale_caller"] : [],
		});
		expect(result.revoked).toContain(token);
		expect(result.revoked).not.toContain(otherToken);
	});

// A changing getter must never become an unvalidated CSP string after descriptor validation.
test("accessor lease roots are refused by helper and independently by host before CSP allocation", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		window.graph.dispose();
		let reads = 0;
		const unstable = { ...window.lease };
		Object.defineProperty(unstable, "root", {
			get() {
				return ++reads <= 3 ? window.lease.root : window.lease.root + '"; script-src *';
			},
			enumerable: true,
		});
		const badApi = {
			...window.api,
			compat: { modules: { ...window.api.compat.modules, issue: async () => unstable } },
		};
		let helper;
		const graph = window.makeModules({ api: badApi });
		try {
			await graph.acquire();
			helper = "resolved";
		} catch (error) {
			helper = error.code;
		}
		graph.dispose();
		reads = 0;
		const host = window.makeHost(window.api, {
			name: "Example",
			createViewRuntime: window.makeView,
			moduleGraph: true,
			createModules: () => ({
				acquire: async () => unstable,
				import: async () => ({ default: () => ({ content: "Unsafe accepted" }) }),
				dispose() {},
				status: () => ({ scope: "fixture" }),
			}),
		});
		let hostError;
		try {
			await host.load("", "esm", document.getElementById("panel"));
			hostError = "resolved";
		} catch (error) {
			hostError = error.message;
		}
		host.dispose();
		return { helper, hostError };
	});
	expect(result).toEqual({
		helper: "module_graph_invalid",
		hostError: "Module graph unavailable: module_graph_invalid",
	});
	await expect(page.locator("iframe")).toHaveCount(0);
});

// A native lazy rejection after entry import must not escape the host's graph error boundary.
test("async original factory chunk failures publish and reject only fixed graph diagnostics", async ({ page }) => {
	await startHost(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] = `export default async()=>{await import('./missing.js');return{content:'Unreachable'};};`;
	try {
		const result = await page.evaluate(async () => {
			try {
				await window.host.load("", "esm", document.getElementById("panel"));
			} catch (error) {
				return { message: error.message, code: error.code, stack: error.stack, status: window.host.status() };
			}
		});
		expect(result).toMatchObject({
			message: "Module graph unavailable: module_execution_failed",
			code: "module_execution_failed",
			status: { panel: "failed", errors: ["Module graph unavailable: module_execution_failed"] },
		});
		expect(JSON.stringify(result)).not.toContain(token);
		expect(JSON.stringify(result)).not.toContain(origin);
		await expect(page.locator("iframe")).toHaveCount(0);
	} finally {
		files["dist/index.js"] = original;
	}
});

// A live panel's event and cleanup paths share the same fixed diagnostic boundary.
test("mounted lazy rejection and cleanup failures never publish an owned URL or token", async ({ page }) => {
	await startHost(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] =
		`export default()=>({content:SP_REACT.createElement('button',{onClick:()=>{import('./missing.js');}},'Missing lazy chunk'),onDismount:()=>{throw new Error(import.meta.url);}});`;
	try {
		await page.evaluate(() => window.host.load("", "esm", document.getElementById("panel")));
		await page.frameLocator("iframe").getByRole("button", { name: "Missing lazy chunk" }).click();
		await expect.poll(() => page.evaluate(() => window.host.status().errors.length)).toBeGreaterThan(0);
		const mounted = await page.evaluate(() => window.host.status());
		expect(mounted).toMatchObject({ panel: "mounted", errors: ["Module graph unavailable: module_execution_failed"] });
		await page.evaluate(() => window.host.dispose());
		const statuses = await page.evaluate(() => window.statuses);
		expect(statuses.at(-1)).toMatchObject({ panel: "disposed" });
		expect(
			statuses.every(status =>
				status.errors.every(message => message === "Module graph unavailable: module_execution_failed"),
			),
		).toBe(true);
		expect(JSON.stringify(statuses)).not.toContain(token);
		expect(JSON.stringify(statuses)).not.toContain(origin);
	} finally {
		files["dist/index.js"] = original;
	}
});

// Even a compatibility failure thrown back into graph code must have no native module stack.
test("graph capability errors expose fixed codes and sanitized capability names without native stacks", async ({
	page,
}) => {
	await startHost(page);
	const original = files["dist/index.js"];
	files["dist/index.js"] =
		`const owner=parent;export default()=>{try{DFL[import.meta.url]();}catch(error){owner.caughtGraphError={message:error.message,code:error.code,stack:error.stack};}return{content:'Caught unsupported capability'};};`;
	try {
		await page.evaluate(() => window.host.load("", "esm", document.getElementById("panel")));
		const result = await page.evaluate(() => ({ error: window.caughtGraphError, status: window.host.status() }));
		expect(result.error).toMatchObject({
			message: "Module graph unavailable: module_capability_unsupported",
			code: "module_capability_unsupported",
		});
		expect(result.status.unsupported).toContain("DFL.[module graph]");
		expect(JSON.stringify(result)).not.toContain(token);
		expect(JSON.stringify(result)).not.toContain(origin);
		await page.evaluate(() => window.host.dispose());
	} finally {
		files["dist/index.js"] = original;
	}
});
