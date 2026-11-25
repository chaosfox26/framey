"use strict";
// Runs the exact Framey-owned host factory with synthetic in-memory realm/DOM.
// This is not a browser or React runtime and loads only this test's IIFE factory.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const path = require("node:path");
const candidate = process.env.FRAMEY_HOST_SOURCE || path.resolve(__dirname, "../../decky_runtime/compat/decky-host.js");
const { createFrameyDeckyHost } = require(candidate);
function harness(extra = {}) {
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
		call:
			extra.call ||
			(() => {
				throw new Error("No backend calls authorized by this harness");
			}),
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
		...extra.hostOptions,
	});
	return { host, realm, container, router, nav, shared, counts, Menu, MenuItem, SidebarNavigation };
}
async function loaded(extra = {}) {
	const state = harness(extra);
	await state.host.load("(()=>({content:null,onDismount(){window.dismount();}}))", "iife", state.container);
	assert.equal(state.host.status().panel, "mounted");
	state.modern = state.realm.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(
		2,
		"OwnFixture",
	);
	return state;
}
module.exports = { harness, loaded };
