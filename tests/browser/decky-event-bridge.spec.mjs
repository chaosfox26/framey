import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const root = resolve(import.meta.dirname, "../..");
const external = "terminal_output#12345678-1234-4234-8234-123456789abc";
const token = "event-bridge-core-token";

// Removing either compatibility hunk drops generated Terminal events. The SDK,
// protocol, EventHub, browser core and host below are actual Framey-owned code.
// The synthetic producer never starts a worker, terminal, PTY or vendor module.
test("generated-session output crosses the real SDK, protocol and isolated browser host", async ({ page }) => {
	const result = spawnSync("python3", ["-I", resolve(root, "tests/test_decky_event_bridge.py"), "--packets"], {
		encoding: "utf8",
		timeout: 10000,
	});
	expect(result.status, result.stderr).toBe(0);
	const production = JSON.parse(result.stdout);
	const packets = production.packets;
	await page.route("**/*", route => route.abort());
	const checks = [];
	const check = (name, pass, detail) => checks.push({ name, pass, detail });
	try {
		await page.setContent('<!doctype html><body><div id="a"></div><div id="b"></div></body>');
		const read = rel =>
			readFileSync(
				rel === "decky_runtime/compat/decky-host.js" && process.env.FRAMEY_HOST_SOURCE
					? process.env.FRAMEY_HOST_SOURCE
					: resolve(root, rel),
				"utf8",
			);
		await page.addScriptTag({
			content:
				"window.fyCall=(function(){}).bind(null);(function(__fyt){" +
				read("inject.js") +
				"\n;window.__S={FY:__FY,trusted:__trusted,K:__FY&&__FY._k(__fyt)};window.__bindFramey=(...args)=>window.__S.K.bind(...args);})(" +
				JSON.stringify(token) +
				");",
		});
		await page.addScriptTag({
			content:
				"(function(__fyt,__FY,__trusted){" +
				read("fy-api.js") +
				"\n})(" +
				JSON.stringify(token) +
				",window.__S.FY,window.__S.trusted);",
		});
		await page.addScriptTag({
			content:
				read("decky_runtime/compat/framey-view.js") +
				"\n" +
				read("decky_runtime/compat/decky-host.js") +
				"\nwindow.makeView=createFrameyViewRuntime;window.makeHost=createFrameyDeckyHost;",
		});
		const ready = await page.evaluate(async external => {
			window.sinks = { terminal65: [], other65: [] };
			window.hosts = {};
			window.apis = {};
			window.frames = {};
			window.dismounts = 0;
			for (const [id, target] of [
				["terminal65", "a"],
				["other65", "b"],
			]) {
				const name = "Synthetic " + id;
				const api = window
					.__bindFramey(id, "tok-" + id + "-0000", { name, short: id, version: "1.0.0" })
					.register({ render() {} });
				window.apis[id] = api;
				const host = window.makeHost(api, { name, apiVersion: 2, createViewRuntime: window.makeView });
				window.hosts[id] = host;
				const source =
					"(function(api){const handler=function(data){parent.sinks[" +
					JSON.stringify(id) +
					"].push(data)};const listener=api.addEventListener(" +
					JSON.stringify(external) +
					',handler);api.addEventListener("server_event",function(data){parent.sinks[' +
					JSON.stringify(id) +
					'].push("strict:"+data)});window.syntheticApi=api;window.syntheticHandler=handler;window.syntheticToken=listener;return {content:null,onDismount(){api.removeEventListener(' +
					JSON.stringify(external) +
					",listener);parent.dismounts++;}};})";
				await host.load(source, "legacy", document.getElementById(target));
				window.frames[id] = document.getElementById(target).querySelector("iframe").contentWindow;
			}
			return { apiReady: window.__S.FY.apiReady, hostStates: Object.values(window.hosts).map(h => h.status().panel) };
		}, external);
		check(
			"real Framey browser core and own view/hosts loaded",
			ready.apiReady && ready.hostStates.every(s => s === "mounted"),
			ready,
		);
		const delivered = await page.evaluate(packets => {
			for (const p of packets) window.__S.K.event(p.plugin, p.name, p.payload, p.dropped);
			return window.sinks;
		}, packets);
		check(
			"SDK+JSON+EventHub Terminal output reaches matching real browser API listener",
			JSON.stringify(delivered.terminal65) === JSON.stringify(["synthetic A", "strict:existing"]),
			delivered,
		);
		check("other plugin cannot observe same UUID output", delivered.other65.length === 0, delivered.other65);
		const details = await page.evaluate(
			({ external, packet }) => {
				const frame = window.frames.terminal65;
				const api = frame.syntheticApi;
				const originalCount = window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions;
				const token = api.addEventListener(external, frame.syntheticHandler);
				const duplicateCount = window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions;
				const beforeInvalid = window.sinks.terminal65.length;
				for (const payload of [
					null,
					[],
					{},
					{ v: 2, name: external, args: ["bad"] },
					{ v: 1, name: external, args: "bad" },
					{ v: 1, name: external, args: ["bad"], extra: true },
					{ v: 1, name: external + "\n", args: ["bad"] },
				])
					window.__S.K.event("terminal65", "decky.compat.event", payload, 0);
				for (const name of [external + "\n", external + "x", "stateUpdate", "bad#id", "x".repeat(41), "é"]) {
					const handler = () => window.sinks.terminal65.push("invalid");
					api.addEventListener(name, handler);
					window.__S.K.event("terminal65", name, ["invalid"], 0);
					api.removeEventListener(name, handler);
				}
				window.__S.K.event("other65", packet.name, { ...packet.payload, args: ["other owner"] }, 3);
				return {
					tokenSame: token === frame.syntheticToken,
					originalCount,
					duplicateCount,
					invalidIgnored: beforeInvalid === window.sinks.terminal65.length,
					sinks: window.sinks,
				};
			},
			{ external, packet: packets[0] },
		);
		check(
			"duplicate add preserves callback token without extra subscription",
			details.tokenSame && details.originalCount === details.duplicateCount,
			details,
		);
		check("malformed envelopes and invalid names do not invoke listeners", details.invalidIgnored, details);
		check(
			"per-plugin owner routing remains isolated",
			JSON.stringify(details.sinks.other65) === JSON.stringify(["other owner"]),
			details.sinks,
		);
		const maxcount = await page.evaluate(() => {
			const api = window.frames.terminal65.syntheticApi;
			const handlers = [];
			let overflow = false;
			const initial = window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions;
			for (let i = 0; i < 64 - initial; i++) {
				const name = "terminal_output#00000000-0000-4000-8000-" + i.toString(16).padStart(12, "0");
				const fn = () => {};
				api.addEventListener(name, fn);
				handlers.push([name, fn]);
			}
			try {
				api.addEventListener("terminal_output#ffffffff-ffff-4fff-8fff-ffffffffffff", () => {});
			} catch (e) {
				overflow = e instanceof RangeError || e.name === "RangeError";
			}
			const atCap = window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions;
			for (const [name, fn] of handlers) api.removeEventListener(name, fn);
			const after = window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions;
			return { initial, atCap, overflow, after };
		});
		check(
			"existing 64-subscription cap and cleanup remain enforced",
			maxcount.atCap === 64 && maxcount.overflow && maxcount.after === maxcount.initial,
			maxcount,
		);
		const callbackErrors = await page.evaluate(
			({ external, packet }) => {
				const api = window.frames.terminal65.syntheticApi;
				let peer = 0;
				const throwing = () => {
					throw new Error("synthetic-listener-failure");
				};
				const surviving = () => peer++;
				api.addEventListener(external, throwing);
				api.addEventListener(external, surviving);
				window.__S.K.event("terminal65", packet.name, { ...packet.payload, args: ["throw-check"] }, 0);
				const errors = window.hosts.terminal65.status().errors;
				api.removeEventListener(external, throwing);
				api.removeEventListener(external, surviving);
				return { peer, errors, privateNameAbsent: errors.every(error => !error.includes(external)) };
			},
			{ external, packet: packets[0] },
		);
		check(
			"listener exception is isolated and diagnostics omit dynamic event name",
			callbackErrors.peer === 1 &&
				callbackErrors.errors.includes("synthetic-listener-failure") &&
				callbackErrors.privateNameAbsent,
			callbackErrors,
		);
		const rebound = await page.evaluate(
			({ external, packet }) => {
				const frame = window.frames.terminal65;
				const api = frame.syntheticApi;
				const handler = frame.syntheticHandler;
				const before = window.sinks.terminal65.length;
				api.removeEventListener(external, handler);
				window.__S.K.event("terminal65", packet.name, { ...packet.payload, args: ["removed"] }, 0);
				const removedInert = window.sinks.terminal65.length === before;
				const token = api.addEventListener(external, handler);
				api.addEventListener(external, handler);
				window.__S.K.event("terminal65", packet.name, { ...packet.payload, args: ["rebound"] }, 0);
				return {
					removedInert,
					tokenSame: token === handler,
					exactlyOnce: window.sinks.terminal65.length === before + 1,
					last: window.sinks.terminal65.at(-1),
					subscriptions: window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions,
				};
			},
			{ external, packet: packets[0] },
		);
		check(
			"unsubscribe then re-add delivers exactly once and releases old subscription",
			rebound.removedInert &&
				rebound.tokenSame &&
				rebound.exactlyOnce &&
				rebound.last === "rebound" &&
				rebound.subscriptions === 2,
			rebound,
		);
		const disposal = await page.evaluate(
			({ packet, external }) => {
				const old = window.frames.terminal65.syntheticApi;
				window.hosts.terminal65.dispose();
				window.hosts.terminal65.dispose();
				const before = window.sinks.terminal65.length;
				window.__S.K.event("terminal65", packet.name, packet.payload, 0);
				let disposedReject = false;
				try {
					old.addEventListener(external, () => {});
				} catch {
					disposedReject = true;
				}
				return {
					oldInert: before === window.sinks.terminal65.length,
					disposedReject,
					remaining: window.__S.FY.diag().plugins.find(p => p.id === "terminal65").subscriptions,
					frames: document.querySelectorAll("iframe").length,
					dismounts: window.dismounts,
				};
			},
			{ packet: packets[0], external },
		);
		check(
			"host disposal removes callbacks/subscriptions once",
			disposal.oldInert &&
				disposal.disposedReject &&
				disposal.remaining === 0 &&
				disposal.frames === 1 &&
				disposal.dismounts === 1,
			disposal,
		);
		// Re-evaluate the real core scripts. Initialization guards must retain old disposal.
		await page.addScriptTag({
			content: "(function(__fyt){" + read("inject.js") + "\n})(" + JSON.stringify(token) + ");",
		});
		await page.addScriptTag({
			content:
				"(function(__fyt,__FY,__trusted){" +
				read("fy-api.js") +
				"\n})(" +
				JSON.stringify(token) +
				",window.__S.FY,window.__S.trusted);",
		});
		const reinjection = await page.evaluate(packet => {
			const before = window.sinks.terminal65.length;
			window.__S.K.event("terminal65", packet.name, packet.payload, 0);
			window.hosts.other65.dispose();
			window.__S.K.dispose();
			return {
				oldInert: before === window.sinks.terminal65.length,
				frames: document.querySelectorAll("iframe").length,
				subs: window.__S.FY.diag().subs,
				dismounts: window.dismounts,
			};
		}, packets[0]);
		check(
			"core reinjection cannot revive old listeners and full teardown drains frames",
			reinjection.oldInert && reinjection.frames === 0 && reinjection.subs === 0 && reinjection.dismounts === 2,
			reinjection,
		);
	} catch (error) {
		check("browser setup/execution", false, { name: error.name, message: error.message, stack: error.stack });
	}

	try {
		await page.evaluate(() => window.__S?.K?.dispose());
	} catch {}
	expect(checks.filter(item => !item.pass)).toEqual([]);
	expect(production.adapter_accepted, "real compatibility backend should envelope the generated event").toBe(true);
});
