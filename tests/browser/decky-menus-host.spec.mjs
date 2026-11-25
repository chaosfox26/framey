import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const script =
	["framey-view.js", "decky-menus.js", "decky-host.js"]
		.map(name => readFileSync(resolve(root, "decky_runtime/compat", name), "utf8"))
		.join("\n") +
	"\nwindow.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;window.makeMenus=createFrameyDeckyMenus;";
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
		window.selected = 0;
		window.cancelled = 0;
		window.released = 0;
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
				createMenus: window.makeMenus,
			},
		);
		await window.host.load(source, "esm", document.getElementById("panel"));
	}, source);
}
const prefix = `const owner=parent,R=SP_REACT,h=R.createElement;const menu=(selected)=>h(DFL.Menu,{label:'Actions',onCancel:()=>owner.cancelled++},h(DFL.MenuItem,{onSelected:selected},'Settings'),h(DFL.MenuItem,{disabled:true,onSelected:()=>owner.selected+=100},'Unavailable'));`;

// Binding exported component functions would fail the menu helper's exact identity contract.
test("original-shaped menu selection opens a local route and preserves the panel input", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`export default api=>{api.routerHook.addRoute('/example/settings',()=>h('p',null,'Settings route'));return{content:h('section',null,h('input',{'aria-label':'Draft',defaultValue:'original'}),h('button',{onClick:e=>DFL.showContextMenu(menu(()=>{owner.selected++;DFL.Router.CloseSideMenus();DFL.Navigation.Navigate('/example/settings');}),e.currentTarget)},'Actions'))};};`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("textbox", { name: "Draft" }).fill("kept draft");
	await frame.getByRole("button", { name: "Actions", exact: true }).click();
	await expect(frame.getByRole("menu", { name: "Actions" })).toBeVisible();
	await expect(frame.getByRole("menuitem", { name: "Unavailable" })).toBeDisabled();
	await frame.getByRole("menuitem", { name: "Settings", exact: true }).click();
	await expect(frame.getByText("Settings route")).toBeVisible();
	await expect(frame.getByRole("menu")).toHaveCount(0);
	await frame.getByRole("button", { name: "Close", exact: true }).click();
	await expect(frame.getByRole("textbox", { name: "Draft" })).toHaveValue("kept draft");
	expect(
		await page.evaluate(() => ({
			selected: window.selected,
			cancelled: window.cancelled,
			status: window.host.status(),
		})),
	).toMatchObject({
		selected: 1,
		cancelled: 0,
		status: {
			steamIntegration: "unavailable",
			menus: { scope: "framey-transient-only", steamSideMenus: false, openCount: 0 },
		},
	});
});

// A dialog replacement must retire its old menu before removing its anchor/root.
test("dialog Update and Close retire only their owned menu without callbacks", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`function Dialog(){return h(DFL.ConfirmModal,{strTitle:'Owner dialog'},h('button',{onClick:e=>{owner.retained=DFL.showContextMenu(menu(()=>owner.selected++),e.currentTarget);}},'Dialog actions'));}export default()=>{owner.replace=()=>owner.dialog.Update(h(Dialog));return{content:h('button',{onClick:()=>owner.dialog=DFL.showModal(h(Dialog))},'Open dialog')};};`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Open dialog" }).click();
	await frame.getByRole("button", { name: "Dialog actions" }).click();
	await expect(frame.getByRole("menuitem", { name: "Settings", exact: true })).toBeVisible();
	await page.evaluate(() => {
		const doc = document.querySelector("iframe").contentDocument;
		window.oldItem = doc.querySelector("[role=menuitem]");
		window.replace();
		window.oldItem.click();
	});
	await expect(frame.getByRole("menu")).toHaveCount(0);
	await frame.getByRole("button", { name: "Dialog actions" }).click();
	await expect(frame.getByRole("menuitem", { name: "Settings", exact: true })).toBeVisible();
	await page.evaluate(() => window.dialog.Close());
	await expect(frame.getByRole("dialog")).toHaveCount(0);
	expect(await page.evaluate(() => ({ selected: window.selected, cancelled: window.cancelled }))).toEqual({
		selected: 0,
		cancelled: 0,
	});
});

// Replacing a route root must release transient menu actions before unmounting that route.
test("route changes and host disposal invalidate retained menus and effects", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`export default api=>{function First(){R.useEffect(()=>()=>owner.released++,[]);return h('button',{onClick:e=>owner.retained=DFL.showContextMenu(menu(()=>owner.selected++),e.currentTarget)},'Route actions');}api.routerHook.addRoute('/example/first',First);api.routerHook.addRoute('/example/second',()=>h('p',null,'Second route'));owner.go=()=>DFL.Navigation.Navigate('/example/second');return{content:h('button',{onClick:()=>DFL.Navigation.Navigate('/example/first')},'Open route')};};`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Open route" }).click();
	await frame.getByRole("button", { name: "Route actions" }).click();
	await expect(frame.getByRole("menu", { name: "Actions" })).toBeVisible();
	await page.evaluate(() => window.go());
	await expect(frame.getByText("Second route")).toBeVisible();
	await expect(frame.getByRole("menu")).toHaveCount(0);
	expect(
		await page.evaluate(() => ({ released: window.released, selected: window.selected, cancelled: window.cancelled })),
	).toEqual({ released: 1, selected: 0, cancelled: 0 });
	await page.evaluate(() => window.host.dispose());
	await expect(page.locator("#panel")).toBeEmpty();
	expect((await page.evaluate(() => window.host.status())).menus).toMatchObject({
		openCount: 0,
		disposed: true,
		steamSideMenus: false,
	});
});

// Unsupported menu contracts remain explicit while ordinary panel controls keep working.
test("unsupported menu options report a boundary without faking a menu", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`export default()=>({content:h('button',{onClick:e=>{try{DFL.showContextMenu(menu(()=>{}),e.currentTarget,{fake:true});}catch{owner.rejected=true;}}},'Unsupported actions')});`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Unsupported actions" }).click();
	expect(await page.evaluate(() => window.rejected)).toBe(true);
	await expect(frame.getByRole("menu")).toHaveCount(0);
	expect(await page.evaluate(() => window.host.status())).toMatchObject({
		panel: "mounted",
		unsupported: expect.arrayContaining(["menus.options.fake"]),
	});
});

// A fatal main render must retire menu listeners and handles with the whole iframe lifecycle.
test("fatal panel rendering disposes an open menu without selection or cancellation", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`export default()=>({content:h(function Panel(){const[broken,setBroken]=R.useState(false);owner.breakPanel=()=>setBroken(true);if(broken)throw new Error('Menu panel failed');return h('button',{onClick:e=>owner.retained=DFL.showContextMenu(menu(()=>owner.selected++),e.currentTarget)},'Open menu');})});`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Open menu" }).click();
	await expect(frame.getByRole("menu", { name: "Actions" })).toBeVisible();
	await page.evaluate(() => window.breakPanel());
	await expect(page.locator("#panel")).toBeEmpty();
	expect(
		await page.evaluate(() => ({
			selected: window.selected,
			cancelled: window.cancelled,
			status: window.host.status(),
		})),
	).toMatchObject({
		selected: 0,
		cancelled: 0,
		status: {
			panel: "failed",
			errors: expect.arrayContaining(["Menu panel failed"]),
			menus: { openCount: 0, disposed: true },
		},
	});
	expect(
		await page.evaluate(() => {
			try {
				window.retained.Show();
				return false;
			} catch {
				return true;
			}
		}),
	).toBe(true);
});

// Closing a lower dialog must not steal an independent child dialog's current menu.
test("window-anchored menus belong to the focused dialog rather than every dialog", async ({ page }) => {
	await prepare(
		page,
		prefix +
			`function Child(){return h(DFL.ConfirmModal,{strTitle:'Child dialog'},h('button',{onClick:()=>DFL.showContextMenu(menu(()=>owner.selected++),window)},'Child actions'));}function Parent(){return h(DFL.ConfirmModal,{strTitle:'Parent dialog'},h('button',{onClick:()=>owner.childDialog=DFL.showModal(h(Child))},'Open child'));}export default()=>({content:h('button',{onClick:()=>owner.parentDialog=DFL.showModal(h(Parent))},'Open parent')});`,
	);
	const frame = page.frameLocator("iframe");
	await frame.getByRole("button", { name: "Open parent" }).click();
	await frame.getByRole("button", { name: "Open child" }).click();
	await frame.getByRole("button", { name: "Child actions" }).click();
	await expect(frame.getByRole("menu", { name: "Actions" })).toBeVisible();
	await page.evaluate(() => window.parentDialog.Close());
	await expect(frame.getByRole("menu", { name: "Actions" })).toBeVisible();
	await frame.getByRole("menuitem", { name: "Settings", exact: true }).click();
	expect(await page.evaluate(() => ({ selected: window.selected, cancelled: window.cancelled }))).toEqual({
		selected: 1,
		cancelled: 0,
	});
	await page.evaluate(() => window.childDialog.Close());
	await expect(frame.getByRole("dialog")).toHaveCount(0);
});
