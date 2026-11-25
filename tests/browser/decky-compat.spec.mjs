import { test, expect } from "@playwright/test";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
async function prepare(page, hostOptions = {}) {
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({
					contentType: "text/html",
					body: '<!doctype html><div id="shell">Framey shell</div><div id="panel"></div>',
				})
			: route.abort(),
	);
	await page.goto("https://framey.test/");
	const scripts = ["decky_runtime/compat/framey-view.js", "decky_runtime/compat/decky-host.js"]
		.filter(file => existsSync(resolve(root, file)))
		.map(file => readFileSync(resolve(root, file), "utf8"))
		.join("\n");
	await page.addScriptTag({
		content:
			scripts +
			"\nwindow.makeHost = typeof createFrameyDeckyHost === 'function' ? createFrameyDeckyHost : null;window.makeView = typeof createFrameyViewRuntime === 'function' ? createFrameyViewRuntime : null;",
	});
	expect(await page.evaluate(() => typeof window.makeHost)).toBe("function");
	await page.evaluate(hostOptions => {
		window.calls = [];
		window.listeners = new Map();
		window.cleaned = 0;
		window.failedStatuses = [];
		window.data = { enabled: false };
		window.host = window.makeHost(
			{
				call: async (method, payload) => {
					window.calls.push({ method, ...payload });
					if (method === "fail") throw new Error("backend failed");
					if (method === "wait_forever") return new Promise(() => {});
					if (method === "get") return window.data.enabled;
					if (method === "set") {
						window.data.enabled = payload.legacy ? payload.args.enabled : payload.args[0];
						return window.data.enabled;
					}
					return payload.args;
				},
				events: {
					on(name, fn) {
						window.listeners.set(name, fn);
						return () => window.listeners.delete(name);
					},
				},
				cleanup(fn) {
					window.cleanup = fn;
				},
				notify(message) {
					window.notice = message;
				},
			},
			{
				...hostOptions,
				name: "Example",
				apiVersion: 2,
				createViewRuntime: window.makeView,
				onStatus(state) {
					if (state.panel === "failed")
						window.failedStatuses.push({ panel: state.panel, frames: document.querySelectorAll("iframe").length });
				},
			},
		);
	}, hostOptions);
}

// Removing positional argument translation or replacing the native toggle with inert markup breaks this test.
test("modern original module calls backend and changes native toggle state", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example'); export default DFL.definePlugin(()=>({content:SP_REACT.createElement(function Panel(){const [value,setValue]=SP_REACT.useState(false);SP_REACT.useEffect(()=>{api.call('get').then(setValue);const listener=api.addEventListener('value',(...args)=>setValue(args[0]));return ()=>api.removeEventListener('value',listener);},[]);return SP_REACT.createElement(DFL.ToggleField,{label:'Enabled',checked:value,onChange:async enabled=>{setValue(await api.call('set',enabled));}});}),onDismount(){parent.cleaned++;}}));`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const panel = page.frameLocator("iframe");
	await expect(panel.getByRole("checkbox", { name: "Enabled" })).not.toBeChecked();
	await panel.getByRole("checkbox", { name: "Enabled" }).check();
	await expect.poll(() => page.evaluate(() => window.data.enabled)).toBe(true);
	expect(await page.evaluate(() => window.calls)).toEqual([
		{ method: "get", args: [], legacy: false },
		{ method: "set", args: [true], legacy: false },
	]);
	await page.evaluate(() => window.listeners.get("value")([false, "extra"]));
	await expect(panel.getByRole("checkbox", { name: "Enabled" })).not.toBeChecked();
	const status = await page.evaluate(() => window.host.status());
	expect(status.panel).toBe("mounted");
	expect(status.steamIntegration).toBe("unavailable");
	await page.evaluate(() => {
		window.host.dispose();
		window.host.dispose();
	});
	await expect(page.locator("iframe")).toHaveCount(0);
	expect(await page.evaluate(() => ({ cleaned: window.cleaned, listeners: window.listeners.size }))).toEqual({
		cleaned: 1,
		listeners: 0,
	});
	await expect(page.locator("#shell")).toHaveText("Framey shell");
});

// Mixing kwargs into positional calls or swallowing backend failure breaks this test.
test("legacy factory receives keyword RPC envelopes and backend errors", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		await window.host.load(
			`(function(DFL,R){return DFL.definePlugin(api=>{window.result=Promise.all([api.callPluginMethod('set',{enabled:true}),api.callPluginMethod('fail',{})]);return {content:R.createElement(DFL.PanelSection,{title:'Legacy'},R.createElement(DFL.ButtonItem,{onClick:()=>api.callPluginMethod('set',{enabled:false})},'Disable'))};});})(DFL,SP_REACT);`,
			"iife",
			document.getElementById("panel"),
		);
		return await document.querySelector("iframe").contentWindow.result;
	});
	expect(result).toEqual([
		{ success: true, result: true },
		{ success: false, result: "backend failed" },
	]);
	await page.frameLocator("iframe").getByRole("button", { name: "Disable" }).click();
	await expect.poll(() => page.evaluate(() => window.data.enabled)).toBe(false);
});

// Accepting the wrong package identity or silently accepting missing Steam hooks breaks this test.
test("validates connection identity and records unsupported Steam capabilities", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		await window.host.load(
			`const init=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;let rejected=0;for(const [v,n] of [[2,'Other'],[999,'Example']]){try{init.connect(v,n);}catch{rejected++;}}const api=init.connect(2,'Example');let error;try{api.routerHook.addPatch('/library',()=>{});}catch(e){error=e.message;}const missing=DFL.findModuleExport(()=>true);export default ()=>({content:SP_REACT.createElement('output',null,JSON.stringify({rejected,error,missing:missing===undefined}))});`,
			"esm",
			document.getElementById("panel"),
		);
		return window.host.status();
	});
	const output = JSON.parse(await page.frameLocator("iframe").locator("output").textContent());
	expect(output.rejected).toBe(2);
	expect(output.error).toMatch(/unsupported/i);
	expect(output.missing).toBe(true);
	expect(result.unsupported).toEqual(expect.arrayContaining(["routerHook.addPatch", "modules.findModuleExport"]));
});

// Failure to cancel realm resources or a throwing dismount preventing frame removal breaks this test.
test("dispose clears realm timers observers and effects even if plugin dismount throws", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		window.ticks = 0;
		window.effectCleanups = 0;
		await window.host.load(
			`setInterval(()=>parent.ticks++,5);const observer=new MutationObserver(()=>parent.ticks++);observer.observe(document.body,{childList:true});export default ()=>({content:SP_REACT.createElement(function Panel(){SP_REACT.useEffect(()=>()=>parent.effectCleanups++,[]);return SP_REACT.createElement('div',null,'Running');}),onDismount(){throw new Error('broken cleanup');}});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await expect(page.frameLocator("iframe").getByText("Running")).toBeVisible();
	await page.waitForTimeout(30);
	await page.evaluate(() => window.host.dispose());
	await expect(page.locator("iframe")).toHaveCount(0);
	const ticks = await page.evaluate(() => window.ticks);
	await page.waitForTimeout(30);
	expect(await page.evaluate(() => window.ticks)).toBe(ticks);
	expect(await page.evaluate(() => window.effectCleanups)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).errors).toContain("broken cleanup");
});

// Losing callable component metadata rejects an original bundle before its factory can run.
test("native button exposes its independently implemented render metadata", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`const Button=DFL.DialogButton.render({}).type;export default()=>({content:SP_REACT.createElement(Button,{onClick:()=>parent.cleaned++},'Native button')});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await page.frameLocator("iframe").getByRole("button", { name: "Native button" }).click();
	expect(await page.evaluate(() => window.cleaned)).toBe(1);
});

// A nested absent Steam API must report its full path instead of an incidental property error.
test("nested Steam calls fail with a recorded capability path", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`let message;try{SteamClient.System.UI.RegisterForFocusChangeEvents(()=>{});}catch(e){message=e.message;}export default()=>({content:SP_REACT.createElement('output',null,message)});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await expect(page.frameLocator("iframe").locator("output")).toContainText("RegisterForFocusChangeEvents");
	expect((await page.evaluate(() => window.host.status())).unsupported).toContain(
		"SteamClient.System.UI.RegisterForFocusChangeEvents",
	);
});

// Missing locale translation makes packages that read host language crash before showing settings.
test("host locale reflects the owning document language", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		document.documentElement.lang = "fr";
		await window.host.load(
			`export default()=>({content:SP_REACT.createElement('output',null,window.LocalizationManager.m_rgLocalesToUse[0])});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await expect(page.frameLocator("iframe").locator("output")).toHaveText("fr");
});

// Accidentally allowing direct sockets or misreading Framey's fetch envelope breaks this boundary test.
test("network uses Framey fetch responses and rejects direct sockets", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		window.host.dispose();
		window.host = window.makeHost(
			{
				call: async () => {},
				cleanup() {},
				events: {
					on() {
						return () => {};
					},
				},
				fetch: async (url, init) => {
					window.fetchRequest = { url, method: init.method };
					return {
						status: 201,
						headers: { "x-framey": "bridge" },
						text: null,
						b64: btoa("bridge body"),
						bytes: 11,
						truncated: false,
						path: null,
					};
				},
			},
			{ name: "Example", apiVersion: 2, createViewRuntime: window.makeView },
		);
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');const r=await api.fetchNoCors('https://example.test/data',{method:'POST'});let socketError;try{new WebSocket('ws://localhost:8080');}catch(e){socketError=e.message;}export default()=>({content:SP_REACT.createElement('output',null,JSON.stringify({status:r.status,header:r.headers.get('x-framey'),body:awaitedBody,socketError}))});const awaitedBody=await r.text();`,
			"esm",
			document.getElementById("panel"),
		);
		return { status: window.host.status(), request: window.fetchRequest };
	});
	const output = JSON.parse(await page.frameLocator("iframe").locator("output").textContent());
	expect(output).toEqual({
		status: 201,
		header: "bridge",
		body: "bridge body",
		socketError: expect.stringMatching(/unsupported network.WebSocket/),
	});
	expect(result.request).toEqual({ url: "https://example.test/data", method: "POST" });
	expect(result.status.unsupported).toContain("network.WebSocket");
});

// Allowing private methods or leaving bridge promises live after teardown breaks this test.
test("rejects private backend methods and pending requests settle on dispose", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		window.host.dispose();
		const calls = [];
		window.host = window.makeHost(
			{
				call: async method => {
					calls.push(method);
					return new Promise(() => {});
				},
				cleanup() {},
				events: {
					on() {
						return () => {};
					},
				},
			},
			{ name: "Example", apiVersion: 2, createViewRuntime: window.makeView },
		);
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');window.callAfter=api.call;window.blocked=api.call('_unload').catch(e=>e.message);window.pending=api.call('slow').catch(e=>e.message);export default()=>({content:SP_REACT.createElement('div',null,'Waiting')});`,
			"esm",
			document.getElementById("panel"),
		);
		const realm = document.querySelector("iframe").contentWindow;
		const blocked = await realm.blocked,
			pending = realm.pending;
		window.host.dispose();
		let synchronous = false,
			after;
		try {
			after = await realm.callAfter("slow").catch(error => error.message);
		} catch {
			synchronous = true;
		}
		return { blocked, pending: await pending, calls, synchronous, after };
	});
	expect(result.blocked).toMatch(/public backend methods/);
	expect(result.pending).toMatch(/disposed/);
	expect(result.calls).toEqual(["slow"]);
	expect(result.synchronous).toBe(false);
	expect(result.after).toMatch(/disposed/);
});

// A factory that resolves after disable still owns its dismount callback; dropping it leaks resources.
test("disposes a plugin factory that finishes after the host was disabled", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		window.factoryReady = new Promise(resolve => (window.markFactoryReady = resolve));
		window.factoryWait = new Promise(resolve => (window.releaseFactory = resolve));
		const loading = window.host
			.load(
				`export default async()=>{const owner=parent;owner.markFactoryReady();await owner.factoryWait;return {content:SP_REACT.createElement('div',null,'Late'),onDismount(){owner.cleaned++;}};};`,
				"esm",
				document.getElementById("panel"),
			)
			.catch(e => e.message);
		await window.factoryReady;
		window.host.dispose();
		window.releaseFactory();
		return { error: await loading, cleaned: window.cleaned, panel: window.host.status().panel };
	});
	expect(result.error).toMatch(/disposed/);
	expect(result.cleaned).toBe(1);
	expect(result.panel).toBe("disposed");
	await expect(page.locator("iframe")).toHaveCount(0);
});

// A fixed iframe or height-auto main hides tall original plugin controls and falsely reports a fitting layout.
test("tall original panels fill their container and expose a truthful accessible scroll fallback", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		const container = document.getElementById("panel");
		container.style.cssText = "height:280px;width:360px";
		await window.host.load(
			`export default()=>({content:SP_REACT.createElement(DFL.PanelSection,{title:'Original long panel'},...Array.from({length:30},(_,i)=>SP_REACT.createElement(DFL.PanelSectionRow,{key:i},SP_REACT.createElement(DFL.ButtonItem,{onClick:()=>parent.cleaned++},'Original action '+i))))});`,
			"esm",
			container,
		);
	});
	await expect(page.locator("iframe")).toHaveCSS("height", "280px");
	await expect.poll(() => page.evaluate(() => window.host.status().layout?.overflow)).toBe(true);
	const status = await page.evaluate(() => window.host.status());
	expect(status.layout.mode).toBe("scroll");
	expect(status.layout.contentHeight).toBeGreaterThan(status.layout.viewportHeight);
	expect(status.unsupported).toContain("layout.overflow");
	const finalControl = page.frameLocator("iframe").getByRole("button", { name: "Original action 29", exact: true });
	await finalControl.scrollIntoViewIfNeeded();
	await finalControl.click();
	expect(await page.evaluate(() => window.cleaned)).toBe(1);
	expect(
		await page.evaluate(() => document.querySelector("iframe").contentDocument.scrollingElement.scrollTop),
	).toBeGreaterThan(0);
	await expect(page.locator("#shell")).toHaveText("Framey shell");
});

// Turning bridge failures into Response(undefined) presents errors as successful 200 responses.
test("fetch and backend failure envelopes reject while preserving their codes", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(async () => {
		window.host.dispose();
		window.host = window.makeHost(
			{
				call: async () => ({ error: "backend denied", code: "backend_denied" }),
				cleanup() {},
				events: {
					on() {
						return () => {};
					},
				},
				fetch: async () => ({ error: "network denied", code: "network_denied" }),
			},
			{ name: "Example", apiVersion: 2, createViewRuntime: window.makeView },
		);
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');window.failures=await Promise.all([api.call('read'),api.fetchNoCors('https://example.test/')].map(p=>p.then(()=>({resolved:true}),error=>({message:error.message,code:error.code}))));export default()=>({content:SP_REACT.createElement('div',null,'Failure contracts')});`,
			"esm",
			document.getElementById("panel"),
		);
		return document.querySelector("iframe").contentWindow.failures;
	});
	expect(result).toEqual([
		{ message: "backend denied", code: "backend_denied" },
		{ message: "network denied", code: "network_denied" },
	]);
});

// Original bundles use Button as the native control contract as well as ButtonItem.
test("Button alias renders an actionable native button", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`export default()=>({content:SP_REACT.createElement(DFL.Button,{onClick:()=>parent.cleaned++},'Start timer')});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await page.frameLocator("iframe").getByRole("button", { name: "Start timer" }).click();
	expect(await page.evaluate(() => window.cleaned)).toBe(1);
});

// Missing optional native hooks must remain absent so a package's feature detection can choose its fallback.
test("optional native hook detection chooses fallback and a required missing hook still fails", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`const hook=SteamClient.Input.RegisterForControllerStateChanges;let error;try{SteamClient.System.UI.RegisterForFocusChangeEvents(()=>{});}catch(e){error=e.message;}export default()=>({content:SP_REACT.createElement('output',null,JSON.stringify({fallback:!hook,error}))});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const output = JSON.parse(await page.frameLocator("iframe").locator("output").textContent());
	expect(output.fallback).toBe(true);
	expect(output.error).toMatch(/RegisterForFocusChangeEvents/);
	expect((await page.evaluate(() => window.host.status())).unsupported).toEqual(
		expect.arrayContaining([
			"SteamClient.Input.RegisterForControllerStateChanges",
			"SteamClient.System.UI.RegisterForFocusChangeEvents",
		]),
	);
});

// A missing view API should support feature detection and report the capability even when plugin code catches its error.
test("missing element runtime APIs remain absent and are recorded", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`let error;const available=typeof SP_REACT.useFrameyMissingAPI==='function';try{SP_REACT.useFrameyMissingAPI();}catch(e){error=e.message;}export default()=>({content:SP_REACT.createElement('output',null,JSON.stringify({available,error}))});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const output = JSON.parse(await page.frameLocator("iframe").locator("output").textContent());
	expect(output.available).toBe(false);
	expect(output.error).toMatch(/useFrameyMissingAPI/);
	expect((await page.evaluate(() => window.host.status())).unsupported).toContain("SP_REACT.useFrameyMissingAPI");
});

// A fatal scheduled render must retire the whole plugin, including resources outside its view root.
test("fatal scheduled renders fail the host and stop every plugin resource", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		window.ticks = 0;
		window.effectCleanups = 0;
		await window.host.load(
			`const owner=parent;const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.call('wait_forever').catch(error=>owner.pendingFailure=error.message);setInterval(()=>owner.ticks++,5);export default()=>({content:SP_REACT.createElement(function Panel(){const [broken,setBroken]=SP_REACT.useState(false);SP_REACT.useEffect(()=>{const listener=()=>{};window.addEventListener('framey-resource',listener);const backendListener=api.addEventListener('fatal_event',listener);return()=>{window.removeEventListener('framey-resource',listener);api.removeEventListener('fatal_event',backendListener);owner.effectCleanups++;};},[]);if(broken)throw new Error('fatal rerender');return SP_REACT.createElement(DFL.Button,{onClick:()=>setBroken(true)},'Break panel');}),onDismount(){owner.cleaned++;}});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await expect.poll(() => page.evaluate(() => window.ticks)).toBeGreaterThan(0);
	await page.frameLocator("iframe").getByRole("button", { name: "Break panel" }).click();
	await expect.poll(() => page.evaluate(() => window.host.status().panel)).toBe("failed");
	await expect(page.locator("#panel")).toBeEmpty();
	const ticks = await page.evaluate(() => window.ticks);
	await page.waitForTimeout(40);
	expect(await page.evaluate(() => window.ticks)).toBe(ticks);
	await expect.poll(() => page.evaluate(() => window.pendingFailure)).toMatch(/disposed/);
	expect(await page.evaluate(() => window.listeners.size)).toBe(0);
	expect(await page.evaluate(() => window.failedStatuses)).toEqual([{ panel: "failed", frames: 0 }]);
	expect(
		await page.evaluate(() => ({
			dismounts: window.cleaned,
			effectCleanups: window.effectCleanups,
			errors: window.host.status().errors,
		})),
	).toEqual({ dismounts: 1, effectCleanups: 1, errors: ["fatal rerender"] });
	await page.evaluate(() => {
		window.host.dispose();
		window.cleanup();
	});
	expect((await page.evaluate(() => window.host.status())).panel).toBe("failed");
	expect(await page.evaluate(() => window.cleaned)).toBe(1);
});

// Genuine plugin-local routes must show original components while leaving the panel's DOM and state mounted.
test("native custom routes render forms params and query with Back and Close preserving the panel", async ({
	page,
}) => {
	await prepare(page, { nativeRoutes: true });
	await page.evaluate(async () => {
		window.panelMounts = 0;
		window.panelCleanups = 0;
		window.routeMounts = 0;
		window.routeCleanups = 0;
		await window.host.load(
			`const owner=parent,R=SP_REACT,h=R.createElement;const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');function Edit(props){const params=DFL.useParams();const [value,setValue]=R.useState('');R.useEffect(()=>{owner.routeMounts++;return()=>owner.routeCleanups++;},[]);return h('form',{onSubmit:event=>{event.preventDefault();owner.routeSaved=value;}},h('output',null,props.greeting+params.id+'|'+new URLSearchParams(props.location.search).get('tab')),h('label',null,'Route value',h('input',{value,onChange:event=>setValue(event.target.value)})),h('button',{type:'submit'},'Save route'),h(DFL.Button,{onClick:()=>DFL.Navigation.Navigate('/example/details/8?tab=history')},'Open route details'));}function Details(){const params=DFL.useParams();R.useEffect(()=>{owner.routeMounts++;return()=>owner.routeCleanups++;},[]);return h('output',null,'Details '+params.id);}api.routerHook.addRoute('/example/edit/:id',Edit,{greeting:'Edit '});api.routerHook.addRoute('/example/details/:id',Details);export default()=>({content:h(function Panel(){const[value,setValue]=R.useState('');R.useEffect(()=>{owner.panelMounts++;return()=>owner.panelCleanups++;},[]);return h('section',null,h('label',null,'Panel value',h('input',{value,onChange:event=>setValue(event.target.value)})),h(DFL.Button,{onClick:()=>DFL.Navigation.Navigate('/example/edit/7?tab=notes')},'Edit locally'));}),onDismount(){owner.cleaned++;}});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const realm = page.frameLocator("iframe");
	await realm.getByRole("textbox", { name: "Panel value" }).fill("Keep me");
	await page.evaluate(
		() => (window.retainedPanelInput = document.querySelector("iframe").contentDocument.querySelector("input")),
	);
	await realm.getByRole("button", { name: "Edit locally" }).click();
	const dialog = realm.getByRole("dialog");
	await expect(dialog).toBeVisible();
	await expect(dialog.locator("output")).toHaveText("Edit 7|notes");
	await dialog.getByRole("textbox", { name: "Route value" }).fill("Saved in route");
	await dialog.getByRole("button", { name: "Save route" }).click();
	expect(await page.evaluate(() => window.routeSaved)).toBe("Saved in route");
	await dialog.getByRole("button", { name: "Open route details" }).click();
	await expect(dialog.locator("output")).toHaveText("Details 8");
	await dialog.getByRole("button", { name: "Back", exact: true }).click();
	await expect(dialog.locator("output")).toHaveText("Edit 7|notes");
	await dialog.getByRole("button", { name: "Close", exact: true }).click();
	await expect(realm.getByRole("dialog")).toHaveCount(0);
	await expect(realm.getByRole("textbox", { name: "Panel value" })).toHaveValue("Keep me");
	expect(
		await page.evaluate(
			() => window.retainedPanelInput === document.querySelector("iframe").contentDocument.querySelector("input"),
		),
	).toBe(true);
	expect(
		await page.evaluate(() => ({
			mounts: window.panelMounts,
			cleanups: window.panelCleanups,
			routeMounts: window.routeMounts,
			routeCleanups: window.routeCleanups,
		})),
	).toEqual({ mounts: 1, cleanups: 0, routeMounts: 3, routeCleanups: 3 });
	expect((await page.evaluate(() => window.host.status())).steamIntegration).toBe("unavailable");
	expect((await page.evaluate(() => window.host.status())).nativeRouteCount).toBe(2);
	await page.evaluate(() => {
		window.host.dispose();
		window.host.dispose();
	});
	expect(
		await page.evaluate(() => ({
			panel: window.panelCleanups,
			dismounts: window.cleaned,
			routes: window.host.status().nativeRouteCount,
		})),
	).toEqual({ panel: 1, dismounts: 1, routes: 0 });
});

// Local routing never becomes a substitute for native Steam paths, missing routes or patch APIs.
test("native routes reject Steam paths unknown destinations and unsupported Steam patches", async ({ page }) => {
	await prepare(page, { nativeRoutes: true });
	await page.evaluate(async () => {
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');const failures=[];for(const invoke of [()=>api.routerHook.addRoute('/library/app/:id',()=>null),()=>DFL.Navigation.Navigate('/store/app/1'),()=>DFL.Navigation.Navigate('/example/missing'),()=>api.routerHook.addRoute('https://example.test',()=>null),()=>api.routerHook.addPatch('/example',()=>{})]){try{invoke();failures.push('accepted');}catch(e){failures.push(e.message);}}export default()=>({content:SP_REACT.createElement('output',null,JSON.stringify(failures))});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const failures = JSON.parse(await page.frameLocator("iframe").locator("output").textContent());
	expect(failures).toHaveLength(5);
	expect(failures.every(message => /unsupported|invalid/i.test(message))).toBe(true);
	const status = await page.evaluate(() => window.host.status());
	expect(status.unsupported).toEqual(
		expect.arrayContaining([
			"nativeRoutes.nativePath",
			"nativeRoutes.unknownRoute",
			"nativeRoutes.path",
			"routerHook.addPatch",
		]),
	);
	expect(status.steamIntegration).toBe("unavailable");
});

// Removing the active route must unmount its effects and event handlers, then restore the existing panel.
test("removing an active native route closes it and releases route-owned effects and listeners", async ({ page }) => {
	await prepare(page, { nativeRoutes: true });
	await page.evaluate(async () => {
		window.routeCleanups = 0;
		window.routeClicks = 0;
		await window.host.load(
			`const owner=parent,api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.routerHook.addRoute('/example/owned',()=>{SP_REACT.useEffect(()=>{const listener=()=>owner.routeClicks++;window.addEventListener('owned-route-event',listener);return()=>{window.removeEventListener('owned-route-event',listener);owner.routeCleanups++;};},[]);return SP_REACT.createElement('button',{onClick:()=>owner.routeClicks++},'Owned route action');});window.removeOwned=()=>api.routerHook.removeRoute('/example/owned');export default()=>({content:SP_REACT.createElement(DFL.Button,{onClick:()=>DFL.Navigation.Navigate('/example/owned')},'Open owned route')});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const realm = page.frameLocator("iframe");
	await realm.getByRole("button", { name: "Open owned route" }).click();
	await page.evaluate(() => {
		const win = document.querySelector("iframe").contentWindow;
		window.retainedRouteButton = [...win.document.querySelectorAll("dialog button")].find(
			button => button.textContent === "Owned route action",
		);
		win.removeOwned();
		win.dispatchEvent(new win.Event("owned-route-event"));
		window.retainedRouteButton.click();
	});
	await expect(realm.getByRole("dialog")).toHaveCount(0);
	await expect(realm.getByRole("button", { name: "Open owned route" })).toBeVisible();
	expect(
		await page.evaluate(() => ({
			cleanups: window.routeCleanups,
			clicks: window.routeClicks,
			routes: window.host.status().nativeRouteCount,
		})),
	).toEqual({ cleanups: 1, clicks: 0, routes: 0 });
	await page.evaluate(() => window.host.dispose());
	expect(await page.evaluate(() => window.routeCleanups)).toBe(1);
});

// The opt-in profile cannot silently replace either the default missing-router contract or an explicit provider.
test("native routing stays opt-in and an explicit router provider remains authoritative", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`let message;try{const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.routerHook.addRoute('/example',()=>null);}catch(e){message=e.message;}export default()=>({content:SP_REACT.createElement('output',null,message)});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await expect(page.frameLocator("iframe").locator("output")).toContainText("unsupported routerHook.addRoute");
	expect((await page.evaluate(() => window.host.status())).nativeRoutes).toBe(false);
	await page.evaluate(async () => {
		window.host.dispose();
		const router = {
			addRoute(path) {
				this.path = path;
			},
			Navigation: {
				Navigate(path) {
					document.querySelector("#shell").textContent = "Provider navigated " + path;
				},
			},
		};
		window.host = window.makeHost(
			{
				call: async () => {},
				cleanup() {},
				events: {
					on() {
						return () => {};
					},
				},
			},
			{ name: "Example", apiVersion: 2, createViewRuntime: window.makeView, nativeRoutes: true, router },
		);
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.routerHook.addRoute('/library/provider',()=>null);export default()=>({content:SP_REACT.createElement(DFL.Button,{onClick:()=>DFL.Navigation.Navigate('/library/provider')},'Use provider')});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await page.frameLocator("iframe").getByRole("button", { name: "Use provider" }).click();
	await expect(page.locator("#shell")).toHaveText("Provider navigated /library/provider");
	expect((await page.evaluate(() => window.host.status())).nativeRoutes).toBe(false);
	await expect(page.frameLocator("iframe").getByRole("dialog")).toHaveCount(0);
});

// Bounded registration must reject growth without turning missing navigation into Steam navigation.
test("native route registration is bounded and rejects encoded Steam paths", async ({ page }) => {
	await prepare(page, { nativeRoutes: true });
	const result = await page.evaluate(async () => {
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');let count=0,error;for(let i=0;i<65;i++){try{api.routerHook.addRoute('/example/route-'+i,()=>null);count++;}catch(e){error=e.message;}}let encoded;try{api.routerHook.addRoute('/%6cibrary/app/:id',()=>null);}catch(e){encoded=e.message;}export default()=>({content:SP_REACT.createElement('output',null,JSON.stringify({count,error,encoded}))});`,
			"esm",
			document.getElementById("panel"),
		);
		return window.host.status();
	});
	const output = JSON.parse(await page.frameLocator("iframe").locator("output").textContent());
	expect(output.count).toBe(64);
	expect(output.error).toMatch(/registrationLimit/);
	expect(output.encoded).toMatch(/nativePath/);
	expect(result.nativeRouteCount).toBe(64);
});

// Disabling an open route must release both view roots and cannot leave route event handlers callable.
test("disposing while a custom route is open releases its root effects and route registry", async ({ page }) => {
	await prepare(page, { nativeRoutes: true });
	await page.evaluate(async () => {
		window.routeCleanups = 0;
		window.routeClicks = 0;
		window.panelCleanups = 0;
		await window.host.load(
			`const owner=parent,api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.routerHook.addRoute('/example/active',()=>{SP_REACT.useEffect(()=>{const listener=()=>owner.routeClicks++;window.addEventListener('active-route-event',listener);return()=>{window.removeEventListener('active-route-event',listener);owner.routeCleanups++;};},[]);return SP_REACT.createElement('button',{onClick:()=>owner.routeClicks++},'Active route action');});export default()=>({content:SP_REACT.createElement(function Panel(){SP_REACT.useEffect(()=>()=>owner.panelCleanups++,[]);return SP_REACT.createElement(DFL.Button,{onClick:()=>DFL.Navigation.Navigate('/example/active')},'Open active route');}),onDismount(){api.routerHook.removeRoute('/example/active');owner.cleaned++;}});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	await page.frameLocator("iframe").getByRole("button", { name: "Open active route" }).click();
	await page.evaluate(() => {
		const win = document.querySelector("iframe").contentWindow;
		const button = [...win.document.querySelectorAll("dialog button")].find(
			button => button.textContent === "Active route action",
		);
		const dispatch = win.dispatchEvent.bind(win),
			EventType = win.Event;
		window.host.dispose();
		dispatch(new EventType("active-route-event"));
		button.click();
		window.host.dispose();
	});
	await expect(page.locator("#panel")).toBeEmpty();
	expect(
		await page.evaluate(() => ({
			route: window.routeCleanups,
			panel: window.panelCleanups,
			clicks: window.routeClicks,
			dismounts: window.cleaned,
			status: window.host.status(),
		})),
	).toMatchObject({
		route: 1,
		panel: 1,
		clicks: 0,
		dismounts: 1,
		status: { panel: "disposed", nativeRouteCount: 0, nativeRoute: null, errors: [] },
	});
});

// Closing route effects must retire the host's listener ownership too, rather than replaying it on disable.
test("route listener cleanup is released once rather than retained until host disposal", async ({ page }) => {
	await prepare(page, { nativeRoutes: true });
	await page.evaluate(async () => {
		window.listenerRemovals = 0;
		const box = document.getElementById("panel"),
			append = box.append.bind(box);
		box.append = frame => {
			append(frame);
			const target = frame.contentWindow.EventTarget.prototype,
				remove = target.removeEventListener;
			target.removeEventListener = function (name, ...args) {
				if (name === "route-cleanup-once") window.listenerRemovals++;
				return remove.call(this, name, ...args);
			};
		};
		await window.host.load(
			`const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.routerHook.addRoute('/example/cleanup',()=>{SP_REACT.useEffect(()=>{const listener=()=>{};window.addEventListener('route-cleanup-once',listener);return()=>window.removeEventListener('route-cleanup-once',listener);},[]);return SP_REACT.createElement('div',null,'Route cleanup');});export default()=>({content:SP_REACT.createElement(DFL.Button,{onClick:()=>DFL.Navigation.Navigate('/example/cleanup')},'Open cleanup route')});`,
			"esm",
			box,
		);
	});
	const realm = page.frameLocator("iframe");
	await realm.getByRole("button", { name: "Open cleanup route" }).click();
	await realm.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
	expect(await page.evaluate(() => window.listenerRemovals)).toBe(1);
	await page.evaluate(() => window.host.dispose());
	expect(await page.evaluate(() => window.listenerRemovals)).toBe(1);
});

// Toggle's public value/onChange(boolean) contract differs from ToggleField's checked prop.
test("standalone Toggle uses value boolean changes disabled state and its native focus ref", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		window.toggleChanges = [];
		await window.host.load(
			`const owner=parent,R=SP_REACT,h=R.createElement;export default()=>({content:h(function Panel(){const[value,setValue]=R.useState(true),ref=R.useRef(null);return h('section',null,h(DFL.Field,{label:'Standalone enabled'},h(DFL.Toggle,{value,onChange:checked=>{owner.toggleChanges.push(checked);setValue(checked);},navRef:ref})),h(DFL.Field,{label:'Standalone disabled'},h(DFL.Toggle,{value:true,disabled:true,onChange:checked=>owner.toggleChanges.push(checked)})),h('button',{onClick:()=>ref.current.focus()},'Focus toggle'));})});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const realm = page.frameLocator("iframe"),
		toggle = realm.getByRole("checkbox", { name: "Standalone enabled" });
	await expect(toggle).toBeChecked();
	await toggle.uncheck();
	await expect(toggle).not.toBeChecked();
	await realm.getByRole("button", { name: "Focus toggle" }).click();
	await expect(toggle).toBeFocused();
	await toggle.press("Space");
	await expect(toggle).toBeChecked();
	await expect(realm.getByRole("checkbox", { name: "Standalone disabled" })).toBeDisabled();
	expect(await page.evaluate(() => window.toggleChanges)).toEqual([false, true]);
});

// Spinner is a styled SVG loading indicator, and disappears when the original component leaves its busy state.
test("Spinner displays a styled accessible SVG while the component is busy", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`const R=SP_REACT,h=R.createElement;export default()=>({content:h(function Panel(){const[busy,setBusy]=R.useState(true);return h('section',null,busy?h(DFL.Spinner,{'aria-label':'Loading plugin',className:'custom-spinner',style:{width:36,height:36,color:'rgb(100, 150, 200)'}}):h('output',null,'Ready'),h('button',{onClick:()=>setBusy(false)},'Finish loading'));})});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const realm = page.frameLocator("iframe"),
		spinner = realm.getByRole("status", { name: "Loading plugin" });
	await expect(spinner).toBeVisible();
	expect(await spinner.evaluate(node => node.tagName.toLowerCase())).toBe("svg");
	await expect(spinner).toHaveCSS("width", "36px");
	await expect(spinner).toHaveCSS("height", "36px");
	await expect(spinner).toHaveCSS("color", "rgb(100, 150, 200)");
	await expect(spinner).toHaveClass(/custom-spinner/);
	await realm.getByRole("button", { name: "Finish loading" }).click();
	await expect(spinner).toHaveCount(0);
	await expect(realm.locator("output")).toHaveText("Ready");
});

// Joining public positional class strings must affect real elements across rerenders, including empty strings.
test("joinClassNames preserves positional strings for native element styling", async ({ page }) => {
	await prepare(page);
	await page.evaluate(async () => {
		await window.host.load(
			`const R=SP_REACT,h=R.createElement;export default()=>({content:h(function Panel(){const[active,setActive]=R.useState(false);return h('section',null,h('style',null,'.framey-join-active{color:rgb(12, 34, 56)}'),h('output',{className:DFL.joinClassNames('framey-join-base','',active?'framey-join-active':'framey-join-idle')},'Classes applied'),h('button',{onClick:()=>setActive(true)},'Activate class'));})});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const realm = page.frameLocator("iframe"),
		output = realm.locator("output");
	await expect(output).toHaveAttribute("class", "framey-join-base  framey-join-idle");
	await realm.getByRole("button", { name: "Activate class" }).click();
	await expect(output).toHaveAttribute("class", "framey-join-base  framey-join-active");
	await expect(output).toHaveCSS("color", "rgb(12, 34, 56)");
});

// Upstream route props are object-spread: null and old function values neither reject registration nor execute callbacks.
test("native routes accept null and legacy callable props as enumerable property bags", async ({ page }) => {
	await prepare(page, { nativeRoutes: true });
	await page.evaluate(async () => {
		window.routeSaved = 0;
		window.routeWrapperCalls = 0;
		await window.host.load(
			`const owner=parent,R=SP_REACT,h=R.createElement,api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');api.routerHook.addRoute('/example/null',()=>h('button',{onClick:()=>owner.routeSaved++},'Save null route'),null);const oldProps=()=>{owner.routeWrapperCalls++;throw new Error('Old wrapper must not be called');};oldProps.note='Callable own property';api.routerHook.addRoute('/example/callable',props=>h('output',null,props.note),oldProps);export default()=>({content:h('section',null,h('button',{onClick:()=>DFL.Navigation.Navigate('/example/null')},'Open null route'),h('button',{onClick:()=>DFL.Navigation.Navigate('/example/callable')},'Open callable route'))});`,
			"esm",
			document.getElementById("panel"),
		);
	});
	const realm = page.frameLocator("iframe");
	await realm.getByRole("button", { name: "Open null route" }).click();
	await realm.getByRole("dialog").getByRole("button", { name: "Save null route" }).click();
	expect(await page.evaluate(() => window.routeSaved)).toBe(1);
	await realm.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
	await realm.getByRole("button", { name: "Open callable route" }).click();
	await expect(realm.getByRole("dialog").locator("output")).toHaveText("Callable own property");
	expect(await page.evaluate(() => window.routeWrapperCalls)).toBe(0);
});
