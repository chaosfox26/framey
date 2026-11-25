import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const helpers =
	["framey-view.js", "decky-assets.js", "decky-host.js"]
		.map(name => readFileSync(resolve(root, "decky_runtime/compat", name), "utf8"))
		.join("\n") +
	"\nwindow.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;window.makeAssets=createFrameyDeckyAssets;";
const png =
	"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAYAAAC56t6BAAAAEUlEQVR4nGPQSNnyH4QZMBgArYsNewfxoK0AAAAASUVORK5CYII=";
async function prepare(page, source, { failInstall = false, failView = false } = {}) {
	const requests = [];
	await page.route("**/*", route => {
		const url = route.request().url();
		if (url === "https://framey.test/")
			return route.fulfill({
				contentType: "text/html",
				body: '<!doctype html><div id="panel" style="width:500px;height:500px"></div>',
			});
		requests.push(url);
		return route.abort();
	});
	await page.goto("https://framey.test/");
	await page.addScriptTag({ content: helpers });
	await page.evaluate(
		async ({ source, png, failInstall, failView }) => {
			window.assetInstallCount = 0;
			window.assetDisposeCount = 0;
			window.restored = false;
			const assets = window.makeAssets("Example", { "dist/assets/icon.png": png });
			let imagePrototype, elementPrototype, nativeSetter, nativeAttribute;
			const lifecycle = {
				install(realm) {
					window.assetInstallCount++;
					imagePrototype = realm.HTMLImageElement.prototype;
					elementPrototype = realm.Element.prototype;
					nativeSetter = Object.getOwnPropertyDescriptor(imagePrototype, "src").set;
					nativeAttribute = elementPrototype.setAttribute;
					assets.install(realm);
					if (failInstall) throw new Error("Asset install failed");
				},
				dispose() {
					window.assetDisposeCount++;
					assets.dispose();
					window.restored =
						!imagePrototype ||
						(Object.getOwnPropertyDescriptor(imagePrototype, "src").set === nativeSetter &&
							elementPrototype.setAttribute === nativeAttribute);
				},
			};
			window.host = window.makeHost(
				{
					call: async () => true,
					cleanup() {},
					events: {
						on() {
							return () => {};
						},
					},
				},
				{
					name: "Example",
					apiVersion: 2,
					createViewRuntime: failView
						? () => {
								throw new Error("View construction failed");
							}
						: window.makeView,
					assets: lifecycle,
				},
			);
			try {
				await window.host.load(source, "esm", document.getElementById("panel"));
			} catch (error) {
				window.loadError = error.message;
			}
		},
		{ source, png, failInstall, failView },
	);
	return requests;
}

// The resolver must be installed before any original module statement or renderer creates package image URLs.
test("host installs owned PNG resolution before module evaluation and direct image fallbacks", async ({ page }) => {
	const source = `const owner=parent;const originalURL='http://127.0.0.1:1337/plugins/Example/assets/icon.png';owner.declaredURL=originalURL;window.eager=new Image();window.eager.alt='Eager original image';window.eager.src=originalURL;document.body.append(window.eager);owner.imageErrors=0;export default()=>({content:SP_REACT.createElement('section',null,SP_REACT.createElement('img',{alt:'Rendered original image',src:originalURL}),SP_REACT.createElement('img',{alt:'Direct fallback image',src:'data:image/png;base64,AA==',onError:event=>{owner.imageErrors++;if(owner.imageErrors===1)event.currentTarget.src=originalURL;}}))});`;
	const requests = await prepare(page, source);
	const realm = page.frameLocator("iframe");
	for (const name of ["Eager original image", "Rendered original image", "Direct fallback image"])
		await expect
			.poll(() => realm.getByRole("img", { name }).evaluate(image => [image.naturalWidth, image.naturalHeight]))
			.toEqual([2, 3]);
	expect(await page.evaluate(() => window.declaredURL)).toBe("http://127.0.0.1:1337/plugins/Example/assets/icon.png");
	expect(await page.evaluate(() => window.imageErrors)).toBe(1);
	expect(requests).toEqual([]);
	await page.evaluate(() => {
		window.host.dispose();
		window.host.dispose();
	});
	expect(await page.evaluate(() => ({ disposed: window.assetDisposeCount, restored: window.restored }))).toEqual({
		disposed: 1,
		restored: true,
	});
});

// A fatal scheduled render must restore native image hooks before removing its iframe.
test("fatal host render cleanup disposes its image resolver once", async ({ page }) => {
	await prepare(
		page,
		`const owner=parent;export default()=>({content:SP_REACT.createElement(function Panel(){const[broken,setBroken]=SP_REACT.useState(false);if(broken)throw new Error('Asset panel failed');return SP_REACT.createElement('button',{onClick:()=>setBroken(true)},'Break asset panel');})});`,
	);
	await page.frameLocator("iframe").getByRole("button", { name: "Break asset panel" }).click();
	await expect(page.locator("#panel")).toBeEmpty();
	expect(
		await page.evaluate(() => ({
			installed: window.assetInstallCount,
			disposed: window.assetDisposeCount,
			restored: window.restored,
			panel: window.host.status().panel,
		})),
	).toEqual({ installed: 1, disposed: 1, restored: true, panel: "failed" });
});

for (const failure of ["failInstall", "failView"])
	test("failed " + failure + " setup releases a partially initialized image resolver", async ({ page }) => {
		await prepare(page, "export default()=>({content:null});", { [failure]: true });
		await expect(page.locator("#panel")).toBeEmpty();
		expect(
			await page.evaluate(() => ({
				installed: window.assetInstallCount,
				disposed: window.assetDisposeCount,
				restored: window.restored,
				panel: window.host.status().panel,
			})),
		).toEqual({ installed: 1, disposed: 1, restored: true, panel: "failed" });
	});
