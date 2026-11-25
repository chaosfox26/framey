import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const script =
	["framey-view.js", "decky-sidebar.js", "decky-host.js"]
		.map(name => readFileSync(resolve(root, "decky_runtime/compat", name), "utf8"))
		.join("\n") +
	"\nwindow.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;window.makeSidebar=createFrameyDeckySidebar;";
async function prepare(page, source, width = 850, height = 600) {
	await page.setViewportSize({ width: 1000, height: 800 });
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({
					contentType: "text/html",
					body: `<!doctype html><div id="panel" style="width:${width}px;height:${height}px"></div>`,
				})
			: route.abort(),
	);
	await page.goto("https://framey.test/");
	await page.addScriptTag({ content: script });
	await page.evaluate(async source => {
		window.released = 0;
		window.mounted = 0;
		window.dismounted = 0;
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
				nativeRoutes: true,
				createViewRuntime: window.makeView,
				createSidebar(options) {
					const sidebar = window.makeSidebar(options);
					window.sidebarIdentity = sidebar.SidebarNavigation;
					return sidebar;
				},
			},
		);
		await window.host.load(source, "esm", document.getElementById("panel"));
	}, source);
}
const prefix = `const owner=parent,R=SP_REACT,h=R.createElement;`;
const pages = `function Settings(){R.useEffect(()=>{owner.mounted++;return()=>owner.released++;},[]);const[value,setValue]=R.useState('initial');return h('input',{'aria-label':'Settings draft',value,onChange:e=>setValue(e.target.value)});}function Acknowledgements(){R.useEffect(()=>{owner.mounted++;return()=>owner.released++;},[]);return h('p',null,'Original acknowledgements');}function Pages(){return h(DFL.SidebarNavigation,{title:'Terminal settings',showTitle:true,pages:[{title:'Settings',content:h(Settings)},{title:'Acknowledgements',content:h(Acknowledgements)}]});}`;

// Native route root ownership must retain visited page state and release it on route exit.
test("original-shaped local settings pages preserve drafts and release visited effects on Back", async ({ page }) => {
	await prepare(
		page,
		prefix +
			pages +
			`export default api=>{owner.sameIdentity=DFL.SidebarNavigation===owner.sidebarIdentity;api.routerHook.addRoute('/example/settings',Pages);return{content:h('section',null,h('input',{'aria-label':'Panel draft',defaultValue:'panel'}),h('button',{onClick:()=>DFL.Navigation.Navigate('/example/settings')},'Open settings')),onDismount:()=>owner.dismounted++};};`,
	);
	const frame = page.frameLocator("iframe");
	expect(await page.evaluate(() => window.sameIdentity)).toBe(true);
	await frame.getByRole("textbox", { name: "Panel draft" }).fill("kept panel");
	await frame.getByRole("button", { name: "Open settings" }).click();
	await expect(frame.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute("aria-selected", "true");
	await frame.getByRole("textbox", { name: "Settings draft" }).fill("kept settings");
	expect(await page.evaluate(() => window.mounted)).toBe(1);
	await frame.getByRole("tab", { name: "Acknowledgements", exact: true }).click();
	await expect(frame.getByText("Original acknowledgements")).toBeVisible();
	await expect(frame.getByRole("textbox", { name: "Settings draft" })).toBeHidden();
	await frame.getByRole("tab", { name: "Acknowledgements", exact: true }).press("Home");
	await expect(frame.getByRole("tab", { name: "Settings", exact: true })).toBeFocused();
	await expect(frame.getByRole("textbox", { name: "Settings draft" })).toHaveValue("kept settings");
	await frame.getByRole("button", { name: "Back", exact: true }).click();
	await expect(frame.getByRole("textbox", { name: "Panel draft" })).toHaveValue("kept panel");
	expect(await page.evaluate(() => ({ released: window.released, mounted: window.mounted }))).toEqual({
		released: 2,
		mounted: 2,
	});
	expect((await page.evaluate(() => window.host.status())).sidebar).toMatchObject({
		scope: "framey-local-pages-only",
		routeReporting: false,
		pageLifetime: "lazy-retained-until-removal",
	});
	await page.evaluate(() => {
		window.host.dispose();
		window.host.dispose();
	});
	expect(await page.evaluate(() => window.dismounted)).toBe(1);
	await expect(page.locator("#panel")).toBeEmpty();
});

for (const width of [320, 850])
	test(`local pages remain within the owned route at ${width}px and expose long content`, async ({ page }) => {
		await prepare(
			page,
			prefix +
				`function Pages(){return h(DFL.SidebarNavigation,{title:'Local pages',showTitle:true,pages:[{title:'Long acknowledgement page',content:h('div',null,...Array.from({length:40},(_,i)=>h('p',{key:i},'Line '+i)),h('button',{onClick:()=>owner.reached=true},'Last acknowledgement'))},{title:'Settings',content:h('p',null,'Settings content')}]});}export default api=>{api.routerHook.addRoute('/example/pages',Pages);return{content:h('button',{onClick:()=>DFL.Navigation.Navigate('/example/pages')},'Open pages')};};`,
			width,
			420,
		);
		const frame = page.frameLocator("iframe");
		await frame.getByRole("button", { name: "Open pages" }).click();
		await expect(frame.getByRole("tab", { name: "Long acknowledgement page" })).toBeVisible();
		await frame.getByRole("button", { name: "Last acknowledgement" }).click();
		expect(await page.evaluate(() => window.reached)).toBe(true);
		const bounds = await page.evaluate(() => {
			const realm = document.querySelector("iframe").contentWindow;
			const doc = realm.document;
			const dialog = doc.querySelector("dialog").getBoundingClientRect();
			const sidebar = doc.querySelector('section[aria-label="Local pages"]').getBoundingClientRect();
			return {
				height: sidebar.height,
				dialogHeight: dialog.height,
				right: sidebar.right,
				bottom: sidebar.bottom,
				dialogRight: dialog.right,
				dialogBottom: dialog.bottom,
				scrollWidth: doc.documentElement.scrollWidth,
				width: realm.innerWidth,
			};
		});
		expect(bounds.height).toBeLessThanOrEqual(bounds.dialogHeight - 50);
		expect(bounds.right).toBeLessThanOrEqual(bounds.dialogRight);
		expect(bounds.bottom).toBeLessThanOrEqual(bounds.dialogBottom);
		expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.width);
		await frame.getByRole("tab", { name: "Settings", exact: true }).click();
		await expect(frame.getByText("Settings content")).toBeVisible();
		await frame.getByRole("button", { name: "Close", exact: true }).click();
		await expect(frame.getByRole("button", { name: "Open pages" })).toBeFocused();
	});

// A hidden controlled page must report its unsupported state instead of quietly choosing another page.
test("unsupported controlled local pages fail truthfully and clean the host", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`function Pages(){const[hide,setHide]=R.useState(false);owner.hidePage=()=>setHide(true);return h(DFL.SidebarNavigation,{pages:[{identifier:'settings',title:'Settings',visible:!hide,content:h('p',null,'Settings active')},{identifier:'other',title:'Other',content:h('p',null,'Other content')}],page:'settings',onPageRequested:()=>{}});}export default api=>{api.routerHook.addRoute('/example/controlled',Pages);return{content:h('button',{onClick:()=>DFL.Navigation.Navigate('/example/controlled')},'Open controlled'),onDismount:()=>owner.dismounted++};};`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Open controlled" }).click();
	await expect(frame.getByText("Settings active")).toBeVisible();
	await page.evaluate(() => window.hidePage());
	await expect(page.locator("#panel")).toBeEmpty();
	expect(await page.evaluate(() => window.host.status())).toMatchObject({
		panel: "failed",
		nativeRouteCount: 0,
		steamIntegration: "unavailable",
		unsupported: expect.arrayContaining(["SidebarNavigation.page.unavailable"]),
	});
	expect(await page.evaluate(() => window.dismounted)).toBe(1);
});
