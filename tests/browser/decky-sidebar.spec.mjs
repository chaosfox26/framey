import { test, expect } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const helper = resolve(root, "decky_runtime/compat/decky-sidebar.js");
const runtime = readFileSync(resolve(root, "decky_runtime/compat/framey-view.js"), "utf8");
async function prepare(page, props = {}) {
	expect(existsSync(helper), "Framey must provide its independent sidebar helper").toBe(true);
	await page.route("**/*", route => route.abort());
	await page.setContent(
		'<!doctype html><body style="margin:0;background:#0e141b;color:#edf4fa;font:14px system-ui"><div id="panel" style="box-sizing:border-box;width:100%;height:100vh;padding:12px"></div></body>',
	);
	await page.addScriptTag({
		content:
			runtime +
			"\n" +
			readFileSync(helper, "utf8") +
			"\nwindow.makeView=createFrameyViewRuntime;window.makeSidebar=createFrameyDeckySidebar;",
	});
	await page.evaluate(props => {
		window.events = [];
		window.missing = [];
		window.errors = [];
		window.requests = [];
		window.view = window.makeView(document, {
			onError: error => window.errors.push(error.message),
		});
		const R = window.view.React,
			h = R.createElement;
		window.adapter = window.makeSidebar({
			React: R,
			onUnsupported: name => window.missing.push(name),
			onError: error => window.errors.push(error.message),
		});
		function Content({ name }) {
			R.useEffect(() => {
				window.events.push("mount:" + name);
				return () => window.events.push("clean:" + name);
			}, []);
			const [value, set] = R.useState("");
			return h(
				"label",
				null,
				name + " value",
				h("input", {
					"aria-label": name + " value",
					value,
					onChange: event => set(event.target.value),
				}),
			);
		}
		window.defaults = {
			title: "Decky Terminal",
			showTitle: true,
			pages: [
				{
					identifier: "settings",
					title: "Settings",
					content: h(Content, { name: "Settings" }),
				},
				{
					identifier: "ack",
					title: "Acknowledgement",
					content: h(Content, { name: "Acknowledgement" }),
				},
			],
		};
		window.props = { ...window.defaults, ...props };
		window.root = window.view.ReactDOM.createRoot(document.getElementById("panel"));
		window.draw = extra => {
			window.props = { ...window.props, ...extra };
			window.root.render(h(window.adapter.SidebarNavigation, window.props));
		};
		window.draw({});
	}, props);
}
test.afterEach(async ({ page }) => {
	await page.evaluate(() => window.view?.dispose()).catch(() => {});
});

test("Terminal-shaped props render lazy pages with linked accessible tabs", async ({ page }) => {
	await prepare(page);
	await expect(page.getByRole("heading", { name: "Decky Terminal" })).toBeVisible();
	const first = page.getByRole("tab", { name: "Settings", exact: true }),
		second = page.getByRole("tab", { name: "Acknowledgement", exact: true });
	await expect(first).toHaveAttribute("aria-selected", "true");
	await expect(second).toHaveAttribute("aria-selected", "false");
	const panel = page.getByRole("tabpanel", { name: "Settings", exact: true });
	await expect(panel).toBeVisible();
	await expect(first).toHaveAttribute("aria-controls", await panel.getAttribute("id"));
	expect(await page.evaluate(() => window.events)).toEqual(["mount:Settings"]);
	await expect(page.getByRole("textbox", { name: "Acknowledgement value" })).toHaveCount(0);
	expect(await page.evaluate(() => window.adapter.status())).toMatchObject({
		scope: "framey-local-pages-only",
		routeReporting: false,
		pageLifetime: "lazy-retained-until-removal",
	});
});

test("page visits retain input DOM and state while inactive content stays unfocusable", async ({ page }) => {
	await prepare(page);
	await page.getByRole("textbox", { name: "Settings value" }).fill("Unsaved settings");
	await page.evaluate(() => (window.savedInput = document.querySelector("input")));
	await page.getByRole("tab", { name: "Acknowledgement", exact: true }).click();
	await expect(page.getByRole("tabpanel", { name: "Acknowledgement", exact: true })).toBeVisible();
	await expect(page.getByRole("textbox", { name: "Settings value" })).toHaveCount(0);
	expect(
		await page.evaluate(() => {
			window.savedInput.focus();
			return document.activeElement === window.savedInput;
		}),
	).toBe(false);
	await page.getByRole("textbox", { name: "Acknowledgement value" }).fill("Unsaved acknowledgement");
	await page.getByRole("tab", { name: "Settings", exact: true }).click();
	await expect(page.getByRole("textbox", { name: "Settings value" })).toHaveValue("Unsaved settings");
	expect(await page.evaluate(() => document.querySelector("input") === window.savedInput)).toBe(true);
	expect(await page.evaluate(() => window.events)).toEqual(["mount:Settings", "mount:Acknowledgement"]);
	await page.evaluate(() => window.draw({ showTitle: false }));
	await expect(page.getByRole("heading", { name: "Decky Terminal" })).toHaveCount(0);
	await expect(page.getByRole("textbox", { name: "Settings value" })).toHaveValue("Unsaved settings");
});

test("vertical keyboard navigation activates pages with one roving tab stop", async ({ page }) => {
	await prepare(page);
	await page.getByRole("tab", { name: "Settings", exact: true }).focus();
	await page.keyboard.press("ArrowDown");
	await expect(page.getByRole("tab", { name: "Acknowledgement", exact: true })).toBeFocused();
	await expect(page.getByRole("tab", { name: "Acknowledgement", exact: true })).toHaveAttribute(
		"aria-selected",
		"true",
	);
	await expect(page.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute("tabindex", "-1");
	await page.keyboard.press("Home");
	await expect(page.getByRole("tab", { name: "Settings", exact: true })).toBeFocused();
	await page.keyboard.press("End");
	await expect(page.getByRole("tab", { name: "Acknowledgement", exact: true })).toBeFocused();
	await page.keyboard.press("ArrowDown");
	await expect(page.getByRole("tab", { name: "Settings", exact: true })).toBeFocused();
});

test("controlled page requests wait for props and unavailable controlled pages fail explicitly", async ({ page }) => {
	await prepare(page, { page: "settings" });
	await page.evaluate(() => window.draw({ onPageRequested: id => window.requests.push(id) }));
	await page.getByRole("tab", { name: "Acknowledgement", exact: true }).click();
	expect(await page.evaluate(() => window.requests)).toEqual(["ack"]);
	await expect(page.getByRole("tabpanel", { name: "Settings", exact: true })).toBeVisible();
	await page.evaluate(() => window.draw({ page: "ack" }));
	await expect(page.getByRole("tabpanel", { name: "Acknowledgement", exact: true })).toBeVisible();
	await page.evaluate(() => window.draw({ page: "unavailable" }));
	expect(await page.evaluate(() => window.missing)).toEqual(["SidebarNavigation.page.unavailable"]);
	expect(await page.evaluate(() => window.errors)).toHaveLength(1);
	await expect(page.getByRole("tablist")).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual([
		"mount:Settings",
		"mount:Acknowledgement",
		"clean:Settings",
		"clean:Acknowledgement",
	]);
});

test("page identifiers retain state across reorder while removed pages clean exactly once", async ({ page }) => {
	await prepare(page);
	await page.getByRole("textbox", { name: "Settings value" }).fill("Retain across reorder");
	await page.getByRole("tab", { name: "Acknowledgement", exact: true }).click();
	await page.evaluate(() =>
		window.draw({
			pages: [window.defaults.pages[1], window.defaults.pages[0]],
		}),
	);
	await page.getByRole("tab", { name: "Settings", exact: true }).click();
	await expect(page.getByRole("textbox", { name: "Settings value" })).toHaveValue("Retain across reorder");
	await page.evaluate(() => window.draw({ pages: [window.defaults.pages[0]] }));
	expect(await page.evaluate(() => window.events)).toEqual([
		"mount:Settings",
		"mount:Acknowledgement",
		"clean:Acknowledgement",
	]);
	await page.evaluate(() => {
		window.root.unmount();
		window.root.unmount();
	});
	expect(await page.evaluate(() => window.events)).toEqual([
		"mount:Settings",
		"mount:Acknowledgement",
		"clean:Acknowledgement",
		"clean:Settings",
	]);
});

test("hidden pages and separators never eagerly mount and empty lists remain accessible", async ({ page }) => {
	await prepare(page);
	await page.evaluate(() =>
		window.draw({
			pages: [window.defaults.pages[0], "separator", { ...window.defaults.pages[1], visible: false }],
		}),
	);
	await expect(page.getByRole("tab")).toHaveCount(1);
	await expect(page.getByRole("separator")).toHaveCount(1);
	expect(await page.evaluate(() => window.events)).toEqual(["mount:Settings"]);
	await page.evaluate(() => window.draw({ pages: [] }));
	await expect(page.getByText("No pages available")).toBeVisible();
	expect(await page.evaluate(() => window.events)).toEqual(["mount:Settings", "clean:Settings"]);
});

for (const width of [850, 320])
	test("sidebar bounds remain within " + width + "px viewport", async ({ page }) => {
		await page.setViewportSize({ width, height: 560 });
		await prepare(page);
		await page.evaluate(() =>
			window.draw({
				title: "Long title with a deliberatelyunbrokenstringthatmustwrapwithintheavailablewidth",
				pages: window.defaults.pages.map(p => ({
					...p,
					title: p.title + " with long words deliberatelyunbrokenstringthatmustwrap",
				})),
			}),
		);
		const bounds = await page.getByRole("tablist").boundingBox();
		expect(bounds.x).toBeGreaterThanOrEqual(0);
		expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		await page.screenshot({
			path: test.info().outputPath("synthetic-sidebar-" + width + ".png"),
		});
	});

test("unimplemented routes and unknown shapes reject instead of simulating Steam behavior", async ({ page }) => {
	await prepare(page);
	const results = await page.evaluate(() => {
		const R = window.view.React;
		window.root.unmount();
		const cases = [
			{ pages: [{ title: "Routed", content: null, route: "/steam/page" }] },
			{
				pages: [{ title: "Linked", content: null, link: "https://example.com" }],
			},
			{
				pages: [
					{ title: "One", content: null, identifier: "duplicate" },
					{ title: "Two", content: null, identifier: "duplicate" },
				],
			},
			{ pages: "invalid" },
			{ pages: [], disableRouteReporting: false },
		];
		return cases.map(extra => {
			const target = document.createElement("div");
			document.body.append(target);
			const root = window.view.ReactDOM.createRoot(target);
			try {
				root.render(
					R.createElement(window.adapter.SidebarNavigation, {
						...window.defaults,
						...extra,
					}),
				);
				return "accepted";
			} catch (error) {
				return error.message;
			} finally {
				root.unmount();
				target.remove();
			}
		});
	});
	for (const value of results) expect(value).toMatch(/unsupported/);
	expect(await page.evaluate(() => window.missing.length)).toBe(5);
});

test("unvisited tabs reference empty hidden panels without eagerly mounting their content", async ({ page }) => {
	await prepare(page);
	expect(
		await page.getByRole("tab", { name: "Acknowledgement", exact: true }).evaluate(node => {
			const panel = document.getElementById(node.getAttribute("aria-controls"));
			return !!panel && panel.hidden && panel.inert && panel.childElementCount === 0;
		}),
	).toBe(true);
	expect(await page.evaluate(() => window.events)).toEqual(["mount:Settings"]);
});
