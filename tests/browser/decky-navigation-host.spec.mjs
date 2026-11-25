import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const script =
	["framey-view.js", "decky-navigation.js", "decky-host.js"]
		.map(name => readFileSync(resolve(root, "decky_runtime/compat", name), "utf8"))
		.join("\n") +
	"\nwindow.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;window.makeNavigation=createFrameyDeckyNavigation;";
async function prepare(page, source, { available = true, reject = false } = {}) {
	const network = [];
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({
					contentType: "text/html",
					body: '<!doctype html><div id="panel" style="width:500px;height:500px"></div>',
				})
			: (network.push(route.request().url()), route.abort()),
	);
	await page.goto("https://framey.test/");
	await page.addScriptTag({ content: script });
	await page.evaluate(
		async ({ source, available, reject }) => {
			window.urls = [];
			window.notices = [];
			window.navigationDisposed = 0;
			window.navigationRestored = false;
			const api = {
				call: async () => true,
				cleanup() {},
				events: {
					on() {
						return () => {};
					},
				},
				notify: notice => window.notices.push(notice),
			};
			if (available)
				api.openURL = async url => {
					window.urls.push(url);
					if (reject) throw new Error("PRIVATE_NATIVE_FAILURE");
					return { requested: true };
				};
			window.host = window.makeHost(api, {
				name: "Example",
				apiVersion: 2,
				nativeRoutes: true,
				createViewRuntime: window.makeView,
				createNavigation(api, reporters) {
					const navigation = window.makeNavigation(api, reporters);
					let realm, nativeOpen;
					return {
						...navigation,
						install(win) {
							realm = win;
							nativeOpen = win.open;
							navigation.install(win);
						},
						dispose() {
							navigation.dispose();
							window.navigationDisposed++;
							window.navigationRestored = !realm || realm.open === nativeOpen;
						},
					};
				},
			});
			await window.host.load(source, "esm", document.getElementById("panel"));
		},
		{ source, available, reject },
	);
	return network;
}
const prefix = `const owner=parent,R=SP_REACT,h=R.createElement;`;

// Only real link actions may cross the scoped native boundary; iframe window.open cannot fabricate a window handle.
test("host connects external link controls and window.open to the owned native capability", async ({ page }) => {
	const network = await prepare(
		page,
		prefix +
			`export default()=>({content:h('section',null,h('button',{onClick:()=>DFL.Navigation.NavigateToExternalWeb('https://example.test/help?a=1')},'Open help'),h('button',{onClick:()=>owner.openResult=window.open('https://example.test/docs','_blank','noopener,noreferrer')},'Open docs'))});`,
	);
	expect(await page.evaluate(() => window.urls)).toEqual([]);
	const realm = page.frameLocator("iframe");
	await realm.getByRole("button", { name: "Open help" }).click();
	await realm.getByRole("button", { name: "Open docs" }).click();
	await expect.poll(() => page.evaluate(() => window.urls.length)).toBe(2);
	expect(await page.evaluate(() => window.urls)).toEqual([
		"https://example.test/help?a=1",
		"https://example.test/docs",
	]);
	expect(await page.evaluate(() => window.openResult)).toBe(null);
	expect(network).toEqual([]);
	expect((await page.evaluate(() => window.host.status())).steamIntegration).toBe("unavailable");
	await page.evaluate(() => {
		window.host.dispose();
		window.host.dispose();
	});
	expect(
		await page.evaluate(() => ({ disposed: window.navigationDisposed, restored: window.navigationRestored })),
	).toEqual({ disposed: 1, restored: true });
});

// Denied shapes and unavailable native operations must produce fixed safe diagnostics and a visible local notice.
test("host reports unsupported link requests without exposing their private URL or native failure", async ({
	page,
}) => {
	await prepare(
		page,
		prefix +
			`export default()=>({content:h('section',null,h('button',{onClick:()=>DFL.Navigation.NavigateToExternalWeb('https://private.example/private-token')},'Unavailable help'),h('button',{onClick:()=>window.open('https://private.example/private-token','_self')},'Unsupported shape'))});`,
		{ available: false },
	);
	const realm = page.frameLocator("iframe");
	await realm.getByRole("button", { name: "Unavailable help" }).click();
	await realm.getByRole("button", { name: "Unsupported shape" }).click();
	const result = await page.evaluate(() => ({
		urls: window.urls,
		notices: window.notices,
		status: window.host.status(),
	}));
	expect(result.urls).toEqual([]);
	expect(result.status.unsupported).toEqual(
		expect.arrayContaining(["Navigation.NavigateToExternalWeb", "window.open"]),
	);
	expect(result.status.externalNavigation.lastDiagnostic).toEqual({
		capability: "window.open",
		code: "unsupported_shape",
	});
	expect(result.notices.length).toBe(2);
	expect(
		result.notices.every(
			notice =>
				notice.title === "Link unavailable" &&
				notice.body === "Link could not be opened. This action is unavailable here.",
		),
	).toBe(true);
	expect(JSON.stringify(result)).not.toContain("private-token");
});

// Native rejections must remain failures, and fatal view teardown must retire the adapter and restore the realm hook.
test("native rejection is truthful and fatal cleanup retires navigation", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`export default()=>({content:h(function Panel(){const[broken,setBroken]=R.useState(false);if(broken)throw new Error('Link panel failed');return h('section',null,h('button',{onClick:()=>DFL.Navigation.NavigateToExternalWeb('https://example.test/fail')},'Reject link'),h('button',{onClick:()=>setBroken(true)},'Break link panel'));})});`,
		{ reject: true },
	);
	const realm = page.frameLocator("iframe");
	await realm.getByRole("button", { name: "Reject link" }).click();
	await expect.poll(() => page.evaluate(() => window.notices.length)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).externalNavigation.lastDiagnostic).toEqual({
		capability: "Navigation.NavigateToExternalWeb",
		code: "request_failed",
	});
	expect(JSON.stringify(await page.evaluate(() => window.host.status()))).not.toContain("PRIVATE_NATIVE_FAILURE");
	await realm.getByRole("button", { name: "Break link panel" }).click();
	await expect(page.locator("#panel")).toBeEmpty();
	expect(
		await page.evaluate(() => ({
			disposed: window.navigationDisposed,
			restored: window.navigationRestored,
			panel: window.host.status().panel,
		})),
	).toEqual({ disposed: 1, restored: true, panel: "failed" });
});
