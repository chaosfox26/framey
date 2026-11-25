import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const script =
	["framey-view.js", "decky-host.js"]
		.map(name => readFileSync(resolve(root, "decky_runtime/compat", name), "utf8"))
		.join("\n") + "\nwindow.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;";
async function prepare(page, source) {
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({
					contentType: "text/html",
					body: '<!doctype html><div id="panel" style="width:500px;height:500px"></div>',
				})
			: route.abort(),
	);
	await page.goto("https://framey.test/");
	await page.addScriptTag({ content: script });
	await page.evaluate(async source => {
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
			{ name: "Example", apiVersion: 2, createViewRuntime: window.makeView },
		);
		await window.host.load(source, "esm", document.getElementById("panel"));
	}, source);
}
const prefix = `const owner=parent,R=SP_REACT,h=R.createElement;`;

// Replacing sleep with synchronous resolution would run its continuation before the queued microtask.
test("sleep uses an owned-realm promise and resolves undefined after queued microtasks", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`owner.order=[];export default()=>({content:h('button',{onClick:()=>{const wait=DFL.sleep(0);owner.realmPromise=wait instanceof Promise;owner.parentPromise=wait instanceof owner.Promise;owner.order.push('sync');wait.then(value=>{owner.undefinedValue=value===undefined;owner.order.push('timer');});Promise.resolve().then(()=>owner.order.push('microtask'));}},'Wait')});`,
	);
	await page.frameLocator("iframe").getByRole("button", { name: "Wait", exact: true }).click();
	await expect.poll(() => page.evaluate(() => window.order)).toEqual(["sync", "microtask", "timer"]);
	expect(
		await page.evaluate(() => ({
			realm: window.realmPromise,
			parent: window.parentPromise,
			value: window.undefinedValue,
		})),
	).toEqual({ realm: true, parent: false, value: true });
	expect((await page.evaluate(() => window.host.status())).unsupported).not.toContain("DFL.sleep");
});

// Native coercion, zero delays and signed timer overflow must not be replaced with arbitrary compatibility limits.
test("sleep preserves native timer coercion and bounds instead of inventing delay validation", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`owner.run=async()=>{const results=[];for(const delay of [undefined,null,-20,NaN,Infinity,2147483648]){await DFL.sleep(delay);results.push('resolved');}let coercions=0;const start=performance.now();await Promise.all([DFL.sleep('40'),DFL.sleep({valueOf(){coercions++;return 40;}})]);const elapsed=performance.now()-start;const invalid=[];for(const delay of [Symbol('delay'),1n]){try{await DFL.sleep(delay);invalid.push('resolved');}catch(error){invalid.push(error.name);}}let maximumSettled=false;DFL.sleep(2147483647).then(()=>maximumSettled=true,()=>maximumSettled=true);await DFL.sleep(30);return {results,coercions,elapsed,invalid,maximumSettled};};export default()=>({content:h('p',null,'Timer contract')});`,
	);
	const result = await page.evaluate(() => window.run());
	expect(result.results).toEqual(["resolved", "resolved", "resolved", "resolved", "resolved", "resolved"]);
	expect(result.coercions).toBe(1);
	expect(result.elapsed).toBeGreaterThanOrEqual(30);
	expect(result.elapsed).toBeLessThan(2000);
	expect(result.invalid).toEqual(["TypeError", "TypeError"]);
	expect(result.maximumSettled).toBe(false);
	await page.evaluate(() => window.host.dispose());
	await expect(page.locator("#panel")).toBeEmpty();
});

// Rejecting canceled sleeps would let a plugin's catch/retry loop spin after its iframe has been removed.
test("dispose cancels pending sleeps and leaves retained waits inert without retry rejections", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`owner.attempts=0;owner.resumed=0;owner.rejections=0;owner.afterDisposed=0;owner.savedSleep=DFL.sleep;owner.start=()=>{owner.attempts++;DFL.sleep(100).then(()=>{owner.resumed++;owner.start();},()=>{owner.rejections++;owner.start();});};export default()=>({content:h('button',{onClick:()=>owner.start()},'Start retry'),onDismount:()=>owner.dismounted=(owner.dismounted||0)+1});`,
	);
	await page.frameLocator("iframe").getByRole("button", { name: "Start retry" }).click();
	expect(await page.evaluate(() => window.attempts)).toBe(1);
	await page.evaluate(() => {
		window.host.dispose();
		window.savedSleep(0).then(
			() => window.afterDisposed++,
			() => window.afterDisposed++,
		);
		window.host.dispose();
	});
	await expect(page.locator("#panel")).toBeEmpty();
	await page.waitForTimeout(180);
	expect(
		await page.evaluate(() => ({
			attempts: window.attempts,
			resumed: window.resumed,
			rejections: window.rejections,
			afterDisposed: window.afterDisposed,
			dismounted: window.dismounted,
		})),
	).toEqual({ attempts: 1, resumed: 0, rejections: 0, afterDisposed: 0, dismounted: 1 });
	expect(await page.evaluate(() => window.host.status())).toMatchObject({
		panel: "disposed",
		errors: [],
		unsupported: [],
	});
});
