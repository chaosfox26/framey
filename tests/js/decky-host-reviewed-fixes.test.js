"use strict";
// Independently authored synthetic regressions against the full Framey-owned host.
// Closure seams expose lexical APIs/ownership only; no vendor plugin code is loaded.
{
	("use strict");
	const fs = require("node:fs"),
		vm = require("node:vm"),
		path = require("node:path");
	const { createFrameyViewRuntime } = require("../../decky_runtime/compat/framey-view.js");
	const createHarness = (
		notifyOverride,
		hostPath = process.env.FRAMEY_HOST_SOURCE || path.resolve(__dirname, "../../decky_runtime/compat/decky-host.js"),
		hostOptions = {},
	) => {
		const source = fs.readFileSync(hostPath, "utf8");
		const anchor = "\tunregister = api.cleanup(dispose);";
		if (source.split(anchor).length !== 2) throw Error("Exact current host factory capture anchor missing");
		// Capture only lexical APIs and supply the actual Framey view object. Full own
		// factory executes; no plugin load, vendor source, DOM renderer or browser runs.
		const instrumented = source.replace(
			anchor,
			"\tview = options.__testView;\n\toptions.__capture({modern,legacy});\n" + anchor,
		);
		const context = { module: { exports: {} } };
		vm.runInNewContext(instrumented, context, { filename: hostPath, timeout: 1000 });
		const view = createFrameyViewRuntime({}),
			messages = [],
			dismissed = [],
			calls = [];
		let boundaries, cleanup;
		const api = {
			call: async (method, args) => {
				calls.push({ method, args });
				return { ok: true };
			},
			events: {
				on() {
					return () => {};
				},
			},
			cleanup(fn) {
				cleanup = fn;
				return () => {};
			},
			notify(message) {
				messages.push(message);
				if (notifyOverride) return notifyOverride(message, messages.length);
				const id = messages.length;
				return {
					id,
					dismiss() {
						dismissed.push(id);
						message.onclose?.("dismiss");
					},
				};
			},
		};
		const host = context.module.exports.createFrameyDeckyHost(api, {
			...hostOptions,
			name: "Owned toast fixture",
			createViewRuntime() {
				throw Error("A browser view must not run");
			},
			__testView: view,
			__capture(x) {
				boundaries = x;
			},
		});
		return {
			...boundaries,
			host,
			messages,
			dismissed,
			calls,
			view,
			h: view.React.createElement,
			api,
			cleanup: () => cleanup(),
		};
	};

	{
		("use strict");
		const { test } = require("node:test"),
			assert = require("node:assert/strict");

		const settle = () => new Promise(resolve => setImmediate(resolve));
		const plain = x => JSON.parse(JSON.stringify(x));
		test("source79 Framey-created pairing body reaches its own legacy callback", async () => {
			const e = createHarness();
			let target;
			const body = e.h(
				"a",
				{
					href: "#",
					onClick: async event => {
						event.persist();
						event.preventDefault();
						event.stopPropagation();
						assert.equal(event.currentTarget, event.target);
						await e.legacy.callPluginMethod("trust_device", { device_id: "synthetic-own-device" });
						target = event.target;
						target.outerHTML = "Accepted";
						target.onClick = null;
					},
				},
				"Click to accept",
			);
			assert.equal(e.view.React.isValidElement(body), true);
			e.modern.toaster.toast({ title: "Pairing own fixture", body, duration: 15000 });
			const message = e.messages[0];
			assert.equal(message.body, "Click to accept");
			assert.equal(message.duration, 15000);
			assert.deepEqual(plain(message.actions), [{ id: "decky-action-0", label: "Click to accept" }]);
			await message.onaction("decky-action-0");
			assert.equal(target.outerHTML, "Accepted");
			assert.equal(target.onClick, null);
			assert.deepEqual(plain(e.calls), [
				{ method: "trust_device", args: { args: { device_id: "synthetic-own-device" }, legacy: true } },
			]);
			await message.onaction("decky-action-0");
			assert.equal(e.calls.length, 1);
			e.host.dispose();
		});
		test("title conversion cannot exceed the owned notification cap through reentry", () => {
			const e = createHarness();
			for (let i = 0; i < 15; i++) e.modern.toaster.toast({ body: "existing" });
			const title = {
				toString() {
					e.modern.toaster.toast({ body: "inner" });
					return "outer";
				},
			};
			assert.throws(() => e.modern.toaster.toast({ title, body: "outer" }), /limit/);
			assert.equal(e.messages.length, 16);
			e.host.dispose();
		});
		test("duration getter cannot exceed the owned notification cap through reentry", () => {
			const e = createHarness();
			for (let i = 0; i < 15; i++) e.modern.toaster.toast({ body: "existing" });
			let reentered = false;
			const message = {
				body: "outer",
				get duration() {
					if (!reentered) {
						reentered = true;
						e.modern.toaster.toast({ body: "inner" });
					}
					return 5000;
				},
			};
			assert.throws(() => e.modern.toaster.toast(message), /limit/);
			assert.equal(e.messages.length, 16);
			e.host.dispose();
		});
		test("source63 top-level sidebar click is retained without auto navigation", async () => {
			const e = createHarness(),
				steps = [];
			e.modern.toaster.toast({ title: "Free games", body: "New own deals", onClick: () => steps.push("open-sidebar") });
			assert.deepEqual(steps, []);
			assert.equal(e.messages[0].actions[0].id, "decky-open");
			await e.messages[0].onaction("decky-open");
			await e.messages[0].onaction("decky-open");
			assert.deepEqual(steps, ["open-sidebar"]);
			e.host.dispose();
		});
		test("source104 legacy duration and failed app identity reach native action", async () => {
			const e = createHarness(),
				opened = [];
			e.legacy.toaster.toast({
				title: "Sync failed",
				body: "Click to see logs",
				duration: 5000,
				onClick: () => opened.push(42),
			});
			assert.equal(e.messages[0].duration, 5000);
			await e.messages[0].onaction("decky-open");
			assert.deepEqual(opened, [42]);
			e.host.dispose();
		});
		test("one owner combines two body actions and one top action without nesting", async () => {
			const e = createHarness(),
				calls = [];
			e.modern.toaster.toast({
				body: e.h(
					"span",
					null,
					"Choose ",
					e.h("a", { onClick: () => calls.push("a") }, "A"),
					" or ",
					e.h("button", { onClick: () => calls.push("b") }, "B"),
				),
				onClick: () => calls.push("top"),
			});
			assert.equal(e.messages.length, 1);
			assert.equal(e.messages[0].actions.length, 3);
			assert.equal(new Set(e.messages[0].actions.map(a => a.id)).size, 3);
			await e.messages[0].onaction("decky-action-1");
			await e.messages[0].onaction("decky-open");
			assert.deepEqual(calls, ["b"]);
			e.host.dispose();
		});
		test("more than three combined actions rejects before notification creation", () => {
			const e = createHarness(),
				body = e.h("span", null, ...["a", "b", "c"].map(label => e.h("button", { onClick() {} }, label)));
			assert.throws(() => e.modern.toaster.toast({ body, onClick() {} }), /three actions/);
			assert.equal(e.messages.length, 0);
			e.host.dispose();
		});
		test("three body-only actions supported; unknown ID does not consume notification", async () => {
			const e = createHarness(),
				calls = [];
			e.modern.toaster.toast({
				body: ["a", "b", "c"].map(label => e.h("button", { onClick: () => calls.push(label) }, label)),
			});
			assert.equal(e.messages[0].actions.length, 3);
			await e.messages[0].onaction("unknown");
			await e.messages[0].onaction("decky-action-2");
			assert.deepEqual(calls, ["c"]);
			e.host.dispose();
		});
		test("close reasons invalidate every sibling action", async () => {
			for (const reason of ["timeout", "dismiss", "dropped"]) {
				const e = createHarness();
				let called = 0;
				e.modern.toaster.toast({ body: e.h("a", { onClick: () => called++ }, "A"), onClick: () => called++ });
				e.messages[0].onclose(reason);
				for (const action of e.messages[0].actions) await e.messages[0].onaction(action.id);
				assert.equal(called, 0);
				e.host.dispose();
			}
		});
		test("host cleanup dismisses all ordinary/action notices and stops stale callbacks", async () => {
			const e = createHarness();
			let called = 0;
			e.modern.toaster.toast({ body: "ordinary" });
			e.modern.toaster.toast({ body: "action", onClick: () => called++ });
			e.cleanup();
			e.host.dispose();
			assert.deepEqual(e.dismissed, [1, 2]);
			await e.messages[1].onaction("decky-open");
			assert.equal(called, 0);
			assert.throws(() => e.modern.toaster.toast({ body: "late" }), /disposed/);
		});
		test("returned dismiss invalidates action even if transport omits onclose", async () => {
			let closed = 0;
			const e = createHarness(() => ({
				id: 7,
				dismiss() {
					closed++;
				},
			}));
			let called = 0;
			const handle = e.modern.toaster.toast({ body: "x", onClick: () => called++ });
			assert.equal(handle.id, 7);
			handle.dismiss();
			handle.dismiss();
			await e.messages[0].onaction("decky-open");
			assert.equal(called, 0);
			assert.equal(closed, 1);
			e.host.dispose();
		});
		test("id0 rejects action ownership and does not occupy a pending slot", async () => {
			const e = createHarness(() => ({ id: 0, dismiss() {} }));
			let calls = 0;
			for (let n = 0; n < 40; n++) e.modern.toaster.toast({ body: "x", onClick: () => calls++ });
			await e.messages[0].onaction("decky-open");
			assert.equal(calls, 0);
			e.host.dispose();
		});
		test("notify failures roll back pending ownership", () => {
			const e = createHarness(() => {
				throw Error("notify failure");
			});
			for (let n = 0; n < 40; n++)
				assert.throws(() => e.modern.toaster.toast({ body: "x", onClick() {} }), /notify failure/);
			e.host.dispose();
		});
		test("pending notifications bounded at16 and close releases a slot", () => {
			const e = createHarness();
			for (let n = 0; n < 16; n++) e.modern.toaster.toast({ body: "x", onClick() {} });
			assert.throws(() => e.modern.toaster.toast({ body: "extra" }), /limit/);
			e.messages[0].onclose("timeout");
			e.modern.toaster.toast({ body: "replacement" });
			assert.equal(e.messages.length, 17);
			e.host.dispose();
		});
		test("synchronous close and reentrant disposal while notify returns stay closed", async () => {
			let handleDismissed = 0,
				e;
			e = createHarness(message => {
				message.onclose("dropped");
				e.host.dispose();
				return {
					id: 9,
					dismiss() {
						handleDismissed++;
					},
				};
			});
			let calls = 0;
			e.modern.toaster.toast({ body: "x", onClick: () => calls++ });
			await e.messages[0].onaction("decky-open");
			assert.equal(calls, 0);
			assert.equal(handleDismissed, 1);
		});
		test("async callback failures are observed exactly once", async () => {
			const e = createHarness();
			e.modern.toaster.toast({
				body: "x",
				onClick: async () => {
					throw Error("own async failure");
				},
			});
			await e.messages[0].onaction("decky-open");
			await settle();
			assert.equal(e.host.status().errors.filter(x => x === "own async failure").length, 1);
			e.host.dispose();
		});
		test("structured body rejects non-elements, functions, cycles, nested actions and excessive bounds", () => {
			const e = createHarness(),
				cyclic = [];
			cyclic.push(cyclic);
			let deep = "x";
			for (let i = 0; i < 20; i++) deep = e.h("span", null, deep);
			const values = [
				{ type: "span", props: { children: "forged" } },
				e.h(() => {
					throw Error("never executed");
				}),
				cyclic,
				e.h("a", { onClick() {} }, e.h("button", { onClick() {} }, "nested")),
				deep,
				Array(130).fill("x"),
			];
			for (const body of values) assert.throws(() => e.modern.toaster.toast({ body }));
			assert.equal(e.messages.length, 0);
			e.host.dispose();
		});
		test("unsupported navigation/render properties fail instead of dropping callbacks", () => {
			const e = createHarness();
			for (const body of [
				e.h("a", { href: "https://example.invalid" }, "URL"),
				e.h("span", { onMouseOver() {} }, "hover"),
				e.h("button", { ref: {} }, "ref"),
				e.h("span", { dangerouslySetInnerHTML: { __html: "x" } }),
			])
				assert.throws(() => e.modern.toaster.toast({ body }));
			assert.equal(e.messages.length, 0);
			e.host.dispose();
		});
		test("modern and legacy share same toaster; normal strings numbers and empty values remain usable", () => {
			const e = createHarness();
			assert.equal(e.modern.toaster, e.legacy.toaster);
			for (const [body, expected] of [
				["plain", "plain"],
				[42, "42"],
				[null, ""],
				[undefined, ""],
				[false, "false"],
				[true, "true"],
			]) {
				const h = e.modern.toaster.toast({ title: "T", body });
				assert.equal(e.messages.at(-1).title, "T");
				assert.equal(e.messages.at(-1).body, expected);
				h.dismiss();
			}
			e.host.dispose();
		});
		test("normal modern call and legacy envelope unaffected", async () => {
			const e = createHarness();
			assert.deepEqual(plain(await e.modern.call("own_call", 1)), { ok: true });
			assert.deepEqual(plain(await e.legacy.callPluginMethod("own_legacy", { x: 2 })), {
				success: true,
				result: { ok: true },
			});
			assert.deepEqual(plain(e.calls), [
				{ method: "own_call", args: { args: [1], legacy: false } },
				{ method: "own_legacy", args: { args: { x: 2 }, legacy: true } },
			]);
			e.host.dispose();
		});
		test("text/action labels bound and logo remains diagnosed rather than rendered", () => {
			const e = createHarness();
			e.modern.toaster.toast({
				title: "x".repeat(100),
				body: e.h("button", { onClick() {} }, "z".repeat(500)),
				logo: e.h("img", { src: "https://example.invalid" }),
			});
			assert.equal(e.messages[0].body.length, 400);
			assert.equal(e.messages[0].actions[0].label.length, 40);
			assert.equal(e.messages[0].logo, undefined);
			assert.equal(e.host.status().unsupported.includes("notifications.logo"), true);
			e.host.dispose();
		});

		test("diagnostic callback disposal prevents creating a new notice", () => {
			let e;
			e = createHarness(undefined, undefined, {
				onStatus(status) {
					if (status.unsupported.includes("notifications.logo")) e.host.dispose();
				},
			});
			assert.throws(
				() => e.modern.toaster.toast({ body: "x", logo: e.h("img", { src: "https://example.invalid" }), onClick() {} }),
				/disposed/,
			);
			assert.equal(e.messages.length, 0);
		});
	}
}
{
	("use strict");
	// Runs the exact Framey-owned host factory with synthetic in-memory realm/DOM.
	// This is not a browser or React runtime and loads only this test's IIFE factory.
	const { test } = require("node:test");
	const assert = require("node:assert/strict");
	const vm = require("node:vm");
	const path = require("node:path");
	const candidate = process.env.FRAMEY_HOST_SOURCE || path.resolve(__dirname, "../../decky_runtime/compat/decky-host.js");
	const { createFrameyDeckyHost } = require(candidate);
	function harness() {
		const counts = {
			dismount: 0,
			frameRemoved: 0,
			rootUnmount: 0,
			viewDisposed: 0,
			menuDisposed: 0,
			sidebarDisposed: 0,
			observerDisconnected: 0,
			apiUnregister: 0,
		};
		class Target {
			addEventListener() {}
			removeEventListener() {}
		}
		class Observer {
			observe() {}
			disconnect() {
				counts.observerDisconnected++;
			}
		}
		const main = {},
			metrics = { clientHeight: 600, clientWidth: 800, scrollHeight: 600, scrollWidth: 800 };
		const innerDoc = {
			documentElement: { ...metrics },
			body: {},
			scrollingElement: { ...metrics },
			open() {},
			write() {},
			close() {},
			querySelector(q) {
				assert.equal(q, "main");
				return main;
			},
		};
		const realm = new Target();
		Object.assign(realm, {
			document: innerDoc,
			navigator: { language: "en" },
			EventTarget: Target,
			MutationObserver: Observer,
			Promise,
			URL: {
				createObjectURL() {
					throw new Error("No module URL should be requested");
				},
				revokeObjectURL() {},
			},
			setTimeout() {
				throw new Error("No timers expected in this harness");
			},
			clearTimeout() {},
			setInterval() {
				throw new Error("No intervals expected in this harness");
			},
			clearInterval() {},
			requestAnimationFrame() {
				throw new Error("No animation expected in this harness");
			},
			cancelAnimationFrame() {},
			dismount() {
				counts.dismount++;
			},
		});
		realm.window = realm;
		const context = vm.createContext(realm);
		realm.eval = source => vm.runInContext(source, context, { timeout: 1000 });
		const frame = {
			style: {},
			contentWindow: realm,
			remove() {
				counts.frameRemoved++;
			},
		};
		const ownerWindow = {
			innerHeight: 600,
			getComputedStyle() {
				return { flexGrow: "0", paddingTop: "0", paddingBottom: "0" };
			},
		};
		const ownerDoc = {
			defaultView: ownerWindow,
			documentElement: { lang: "en" },
			createElement(tag) {
				assert.equal(tag, "iframe");
				return frame;
			},
		};
		const container = {
			ownerDocument: ownerDoc,
			parentElement: null,
			clientHeight: 600,
			getBoundingClientRect() {
				return { top: 0 };
			},
			append(value) {
				assert.equal(value, frame);
			},
		};
		const React = {
			createElement: (type, props, ...children) => ({ type, props, children }),
			forwardRef: fn => fn,
			createContext: value => ({ value }),
		};
		const view = {
			React,
			ReactDOM: {
				createRoot(node) {
					assert.equal(node, main);
					return {
						render() {},
						unmount() {
							counts.rootUnmount++;
						},
					};
				},
			},
			dispose() {
				counts.viewDisposed++;
			},
		};
		function shared(suffix) {
			return this.tag + suffix;
		}
		shared.custom = { kind: "own-fixture" };
		shared.render = () => "render-marker";
		const nav = { tag: "B", shared };
		const router = { tag: "A", shared, alias: shared, Navigation: nav };
		function Menu() {}
		function MenuItem() {}
		function SidebarNavigation() {}
		const menu = {
			Menu,
			MenuItem,
			showContextMenu() {},
			closeAll() {},
			status() {
				return { disposed: false };
			},
			dispose() {
				counts.menuDisposed++;
			},
		};
		const sidebar = {
			SidebarNavigation,
			status() {
				return {};
			},
			dispose() {
				counts.sidebarDisposed++;
			},
		};
		const api = {
			call() {
				throw new Error("No backend calls authorized by this harness");
			},
			events: {
				on() {
					throw new Error("No subscriptions expected");
				},
			},
			cleanup(fn) {
				return () => {
					counts.apiUnregister++;
				};
			},
		};
		const host = createFrameyDeckyHost(api, {
			name: "OwnFixture",
			router,
			createViewRuntime: () => view,
			createMenus: () => menu,
			createSidebar: () => sidebar,
		});
		return { host, realm, container, router, nav, shared, counts, Menu, MenuItem, SidebarNavigation };
	}
	async function loaded() {
		const state = harness();
		await state.host.load("(()=>({content:null,onDismount(){window.dismount();}}))", "iife", state.container);
		assert.equal(state.host.status().panel, "mounted");
		state.modern = state.realm.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(
			2,
			"OwnFixture",
		);
		return state;
	}
	test("same callable on distinct provider receivers returns each actual receiver", async () => {
		const s = await loaded();
		try {
			const a = s.modern.routerHook.shared,
				b = s.realm.DFL.Navigation.shared;
			assert.equal(a("!"), "A!");
			assert.equal(b("?"), "B?");
			assert.notEqual(a, b);
		} finally {
			s.host.dispose();
		}
	});
	test("same receiver callable remains stable across aliases and repeat access with copied properties", async () => {
		const s = await loaded();
		try {
			const first = s.modern.routerHook.shared;
			assert.equal(first, s.modern.routerHook.shared);
			assert.equal(first, s.modern.routerHook.alias);
			assert.equal(first.custom, s.shared.custom);
			assert.equal(first.render, s.shared.render);
			assert.notEqual(first, s.shared);
			assert.equal(first.call({ tag: "wrong" }, "!"), "A!");
		} finally {
			s.host.dispose();
		}
	});
	test("replacing and restoring a method reuses its original receiver-owned wrapper", async () => {
		const s = await loaded();
		try {
			const a = s.modern.routerHook.shared;
			function replacement(value) {
				return this.tag + " new " + value;
			}
			replacement.custom = 17;
			s.router.shared = replacement;
			const changed = s.modern.routerHook.shared;
			assert.notEqual(changed, a);
			assert.equal(changed("x"), "A new x");
			assert.equal(changed.custom, 17);
			s.router.shared = s.shared;
			assert.equal(s.modern.routerHook.shared, a);
			assert.equal(s.realm.DFL.Navigation.shared("y"), "By");
		} finally {
			s.host.dispose();
		}
	});
	test("identity-sensitive menu and sidebar functions bypass binding unchanged", async () => {
		const s = await loaded();
		try {
			assert.equal(s.realm.DFL.Menu, s.Menu);
			assert.equal(s.realm.DFL.MenuItem, s.MenuItem);
			assert.equal(s.realm.DFL.SidebarNavigation, s.SidebarNavigation);
			assert.equal(s.realm.DFL.Menu, s.realm.DFL.Menu);
		} finally {
			s.host.dispose();
		}
	});
	test("disposal remains idempotent with exact lifecycle cleanup and connection rejection", async () => {
		const s = await loaded();
		const before = s.modern.routerHook.shared;
		s.host.dispose();
		s.host.dispose();
		assert.equal(s.host.status().panel, "disposed");
		assert.deepEqual(s.counts, {
			dismount: 1,
			frameRemoved: 1,
			rootUnmount: 1,
			viewDisposed: 1,
			menuDisposed: 1,
			sidebarDisposed: 0,
			observerDisconnected: 1,
			apiUnregister: 1,
		});
		assert.throws(
			() =>
				s.realm.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2, "OwnFixture"),
			/disposed/,
		);
		// Preserve current host contract: a retained supplied function is not revoked.
		assert.equal(before(" retained"), "A retained");
		await assert.rejects(s.host.load("(()=>({content:null}))", "iife", s.container), /disposed/);
	});
	test("provider binding keeps synchronous throws and asynchronous result identity unchanged", async () => {
		const s = await loaded();
		try {
			const originalFailure = new Error("own provider failure");
			s.router.failure = function () {
				assert.equal(this, s.router);
				throw originalFailure;
			};
			assert.throws(
				() => s.modern.routerHook.failure(),
				error => error === originalFailure,
			);
			const result = Promise.resolve("own provider result");
			s.nav.async = function (value) {
				assert.equal(this, s.nav);
				assert.equal(value, 42);
				return result;
			};
			assert.equal(s.realm.DFL.Navigation.async(42), result);
			assert.equal(await result, "own provider result");
		} finally {
			s.host.dispose();
		}
	});
	test("reverse access order and mutable receiver state remain independent", async () => {
		const s = await loaded();
		try {
			const b = s.realm.DFL.Navigation.shared,
				a = s.modern.routerHook.shared;
			assert.equal(b("!"), "B!");
			assert.equal(a("?"), "A?");
			s.router.tag = "A2";
			s.nav.tag = "B2";
			assert.equal(a("!"), "A2!");
			assert.equal(b("?"), "B2?");
		} finally {
			s.host.dispose();
		}
	});
}
{
	("use strict");
	// Runs Framey-owned source with a private closure seam and entirely synthetic realm.
	// No load(), document, view runtime, browser, vendor code or native timer is used.
	const assert = require("node:assert/strict");
	const test = require("node:test");
	const fs = require("node:fs");
	const vm = require("node:vm");
	const path = require("node:path");
	const SOURCE = process.env.FRAMEY_HOST_SOURCE || path.resolve(__dirname, "../../decky_runtime/compat/decky-host.js");
	const source = fs.readFileSync(SOURCE, "utf8");
	const seam = "return Object.freeze({ load, status, dispose });";
	assert.equal(source.split(seam).length, 2, "exact current-host seam");
	const context = vm.createContext({ module: { exports: {} } });
	vm.runInContext(
		source.replace(
			seam,
			"return Object.freeze({load,status,dispose,__timerTest:{instrument,setRealm(value){realm=value},resourceCount(){return resources.size}}});",
		),
		context,
		{ filename: SOURCE },
	);
	const createHost = context.module.exports.createFrameyDeckyHost;

	function fixture() {
		const timers = new Map(),
			frames = new Map(),
			cancellations = [],
			observerCounts = [],
			urls = new Set();
		let timerId = 0,
			frameId = 0,
			unregistered = 0;
		class Target {
			constructor() {
				this.listeners = [];
			}
			addEventListener(name, listener, init) {
				this.listeners.push({ name, listener, init });
			}
			removeEventListener(name, listener) {
				this.listeners = this.listeners.filter(v => v.name !== name || v.listener !== listener);
			}
		}
		const realm = new Target();
		function add(kind, fn, delay, args) {
			const id = ++timerId;
			timers.set(id, { kind, fn, delay, args });
			return id;
		}
		function cancel(kind, id, pool) {
			assert.equal(this, realm, "native cancel receiver");
			cancellations.push({ kind, id });
			pool.delete(id);
		}
		class Observer {
			constructor(callback) {
				this.callback = callback;
				this.disconnects = 0;
				observerCounts.push(this);
			}
			disconnect() {
				this.disconnects++;
			}
		}
		Object.assign(realm, {
			EventTarget: Target,
			MutationObserver: Observer,
			ResizeObserver: Observer,
			IntersectionObserver: Observer,
			URL: {
				createObjectURL() {
					const url = "owned:" + urls.size;
					urls.add(url);
					return url;
				},
				revokeObjectURL(url) {
					urls.delete(url);
				},
			},
			setTimeout(fn, delay, ...args) {
				assert.equal(this, realm, "native schedule receiver");
				return add("timeout", fn, delay, args);
			},
			setInterval(fn, delay, ...args) {
				assert.equal(this, realm, "native schedule receiver");
				return add("interval", fn, delay, args);
			},
			clearTimeout(id) {
				return cancel.call(this, "timeout", id, timers);
			},
			clearInterval(id) {
				return cancel.call(this, "interval", id, timers);
			},
			requestAnimationFrame(fn) {
				assert.equal(this, realm);
				const id = ++frameId;
				frames.set(id, { kind: "raf", fn, args: [] });
				return id;
			},
			cancelAnimationFrame(id) {
				return cancel.call(this, "raf", id, frames);
			},
		});
		const host = createHost(
			{
				call() {
					throw Error("backend calls forbidden");
				},
				cleanup() {
					return () => unregistered++;
				},
				events: {
					on() {
						throw Error("subscriptions forbidden");
					},
				},
			},
			{
				name: "Own synthetic timer fixture",
				createViewRuntime() {
					throw Error("view/browser execution forbidden");
				},
			},
		);
		host.__timerTest.setRealm(realm);
		host.__timerTest.instrument();
		const baseline = host.__timerTest.resourceCount();
		function fire(kind, id, ...invocation) {
			const receiver = invocation.length ? invocation[0] : realm,
				values = invocation[1];
			const pool = kind === "raf" ? frames : timers,
				record = pool.get(id);
			assert.ok(record, "scheduled callback exists");
			if (record.kind !== "interval") pool.delete(id);
			return Reflect.apply(record.fn, receiver, values ?? record.args);
		}
		return {
			host,
			realm,
			timers,
			frames,
			cancellations,
			observerCounts,
			urls,
			baseline,
			fire,
			resources: () => host.__timerTest.resourceCount() - baseline,
			unregistered: () => unregistered,
		};
	}
	for (const [kind, start] of [
		["timeout", "setTimeout"],
		["interval", "setInterval"],
		["raf", "requestAnimationFrame"],
	]) {
		test(kind + " preserves scheduler-supplied receiver for strict callback", () => {
			const f = fixture(),
				receiver = { label: "scheduler receiver" };
			let seen;
			const id = f.realm[start](function () {
				"use strict";
				seen = this;
			}, 10);
			f.fire(kind, id, receiver, [123]);
			assert.equal(seen, receiver);
			f.host.dispose();
		});
	}
	for (const [kind, start] of [
		["timeout", "setTimeout"],
		["interval", "setInterval"],
		["raf", "requestAnimationFrame"],
	]) {
		test(kind + " preserves undefined null string number and object scheduler receivers without coercion", () => {
			const f = fixture();
			for (const receiver of [undefined, null, "receiver string", 42, { owned: true }]) {
				let seen = "not called";
				const id = f.realm[start](function () {
					"use strict";
					seen = this;
				}, 10);
				f.fire(kind, id, receiver, []);
				assert.equal(seen, receiver);
				if (kind === "interval") f.realm.clearInterval(id);
			}
			f.host.dispose();
		});
	}
	for (const [start, stop] of [
		["setInterval", "clearTimeout"],
		["setTimeout", "clearInterval"],
	]) {
		test(start + "/" + stop + " leaves zero owned records after 100 valid cross-clears", () => {
			const f = fixture();
			for (let i = 0; i < 100; i++) f.realm[stop](f.realm[start](() => {}, 10));
			assert.equal(f.timers.size, 0, "native timer pool is empty");
			assert.equal(f.resources(), 0, "host ownership pool is empty");
			f.host.dispose();
		});
	}
	test("timeout callback arguments and scheduling delay pass through without copying values", () => {
		const f = fixture(),
			object = { owned: true };
		let seen;
		const id = f.realm.setTimeout(
			function (...args) {
				seen = args;
			},
			37,
			object,
			2,
			"three",
		);
		assert.equal(f.timers.get(id).delay, 37);
		f.fire("timeout", id);
		assert.equal(seen[0], object);
		assert.deepEqual(seen.slice(1), [2, "three"]);
		f.host.dispose();
	});
	test("interval repeats retain one record and forward arguments", () => {
		const f = fixture(),
			seen = [];
		const id = f.realm.setInterval(value => seen.push(value), 10, "tick");
		f.fire("interval", id);
		f.fire("interval", id);
		assert.deepEqual(seen, ["tick", "tick"]);
		assert.equal(f.resources(), 1);
		f.realm.clearInterval(id);
		assert.equal(f.resources(), 0);
		f.host.dispose();
	});
	test("timeout completion releases ownership before callback and leaves newly created timer owned", () => {
		const f = fixture();
		let inside, second;
		const first = f.realm.setTimeout(() => {
			inside = f.resources();
			second = f.realm.setTimeout(() => {}, 1);
		}, 1);
		f.fire("timeout", first);
		assert.equal(inside, 0);
		assert.equal(f.resources(), 1);
		assert.ok(f.timers.has(second));
		f.host.dispose();
		assert.equal(f.timers.size, 0);
	});
	test("RAF timestamp is forwarded and RAF completion releases its record", () => {
		const f = fixture();
		let stamp;
		const id = f.realm.requestAnimationFrame(value => (stamp = value));
		f.fire("raf", id, undefined, [90.25]);
		assert.equal(stamp, 90.25);
		assert.equal(f.resources(), 0);
		f.host.dispose();
	});
	test("RAF and timeout identical numeric IDs remain independent when timeout is cleared", () => {
		const f = fixture();
		const timeout = f.realm.setTimeout(() => {}, 1),
			raf = f.realm.requestAnimationFrame(() => {});
		assert.equal(timeout, raf);
		f.realm.clearTimeout(timeout);
		assert.equal(f.resources(), 1);
		assert.equal(f.frames.size, 1);
		f.host.dispose();
		assert.equal(f.frames.size, 0);
	});
	test("RAF and interval identical numeric IDs remain independent when RAF is cancelled", () => {
		const f = fixture();
		const interval = f.realm.setInterval(() => {}, 1),
			raf = f.realm.requestAnimationFrame(() => {});
		assert.equal(interval, raf);
		f.realm.cancelAnimationFrame(raf);
		assert.equal(f.resources(), 1);
		assert.equal(f.timers.size, 1);
		f.host.dispose();
		assert.equal(f.timers.size, 0);
	});
	test("RAF completion cannot release same-ID interval ownership", () => {
		const f = fixture();
		const interval = f.realm.setInterval(() => {}, 1),
			raf = f.realm.requestAnimationFrame(() => {});
		assert.equal(interval, raf);
		f.fire("raf", raf);
		assert.equal(f.resources(), 1);
		f.host.dispose();
		assert.equal(f.timers.size, 0);
	});
	test("timeout completion cannot release same-ID RAF ownership", () => {
		const f = fixture();
		const timeout = f.realm.setTimeout(() => {}, 1),
			raf = f.realm.requestAnimationFrame(() => {});
		assert.equal(timeout, raf);
		f.fire("timeout", timeout);
		assert.equal(f.resources(), 1);
		f.host.dispose();
		assert.equal(f.frames.size, 0);
	});
	test("dispose cancels every remaining timeout interval and RAF exactly once", () => {
		const f = fixture();
		f.realm.setTimeout(() => {}, 1);
		f.realm.setInterval(() => {}, 1);
		f.realm.requestAnimationFrame(() => {});
		f.host.dispose();
		assert.equal(f.timers.size + f.frames.size, 0);
		assert.deepEqual(
			f.cancellations.map(v => v.kind),
			["timeout", "interval", "raf"],
		);
		assert.equal(f.host.__timerTest.resourceCount(), 0);
		f.host.dispose();
		assert.equal(f.cancellations.length, 3);
		assert.equal(f.unregistered(), 1);
	});
	test("completed and explicitly cleared timers are not cancelled again on disposal", () => {
		const f = fixture();
		const done = f.realm.setTimeout(() => {}, 1),
			cleared = f.realm.setInterval(() => {}, 1);
		f.fire("timeout", done);
		f.realm.clearTimeout(cleared);
		assert.equal(f.cancellations.length, 1);
		f.host.dispose();
		assert.equal(f.cancellations.length, 1);
	});
	test("already queued callbacks cannot run after disposal", () => {
		const f = fixture();
		let calls = 0;
		const t = f.realm.setTimeout(() => calls++, 1),
			i = f.realm.setInterval(() => calls++, 1),
			r = f.realm.requestAnimationFrame(() => calls++);
		const queued = [f.timers.get(t).fn, f.timers.get(i).fn, f.frames.get(r).fn];
		f.host.dispose();
		for (const fn of queued) Reflect.apply(fn, f.realm, [10]);
		assert.equal(calls, 0);
	});
	test("scheduling after disposal returns zero without native allocation", () => {
		const f = fixture();
		f.host.dispose();
		for (const name of ["setTimeout", "setInterval", "requestAnimationFrame"])
			assert.equal(
				f.realm[name](() => {}, 1),
				0,
			);
		assert.equal(f.timers.size + f.frames.size, 0);
		assert.equal(f.host.__timerTest.resourceCount(), 0);
	});
	test("throwing timeout callback is contained and completed ownership is released", () => {
		const f = fixture();
		const id = f.realm.setTimeout(() => {
			throw Error("owned timeout failure");
		}, 1);
		assert.doesNotThrow(() => f.fire("timeout", id));
		assert.ok(f.host.status().errors.includes("owned timeout failure"));
		assert.equal(f.resources(), 0);
		f.host.dispose();
	});
	test("throwing interval callback is contained while interval remains owned", () => {
		const f = fixture();
		const id = f.realm.setInterval(() => {
			throw Error("owned interval failure");
		}, 1);
		assert.doesNotThrow(() => f.fire("interval", id));
		assert.equal(f.resources(), 1);
		f.realm.clearTimeout(id);
		assert.equal(f.resources(), 0);
		f.host.dispose();
	});
	test("listener observer and owned object-URL cleanup remains unchanged", () => {
		const f = fixture(),
			target = new f.realm.EventTarget();
		target.addEventListener("owned", () => {});
		for (const name of ["MutationObserver", "ResizeObserver", "IntersectionObserver"]) new f.realm[name](() => {});
		f.realm.URL.createObjectURL({});
		f.realm.setInterval(() => {}, 1);
		f.host.dispose();
		assert.equal(target.listeners.length, 0);
		assert.equal(f.realm.listeners.length, 0);
		assert.equal(f.urls.size, 0);
		assert.deepEqual(
			f.observerCounts.map(v => v.disconnects),
			[1, 1, 1],
		);
		assert.equal(f.timers.size, 0);
		assert.equal(f.host.__timerTest.resourceCount(), 0);
	});
	test("invalid timer callbacks remain rejected without allocating ownership", () => {
		const f = fixture();
		for (const name of ["setTimeout", "setInterval", "requestAnimationFrame"])
			assert.throws(() => f.realm[name]("code string", 1), /Timer callback must be a function/);
		assert.equal(f.resources(), 0);
		assert.equal(f.timers.size + f.frames.size, 0);
		f.host.dispose();
	});
}
