import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const iconData = "data:image/svg+xml," + encodeURIComponent(readFileSync(resolve(root, "icon.svg"), "utf8"));
const files = [
	"inject.js",
	"fy-api.js",
	"fy-ui.js",
	"fy-pages.js",
	"fy-strings-en.js",
	"fy-report.js",
	"fy-modules.js",
	"fy-catalog.js",
	"fy-settings.js",
	"fy-picker.js",
];
const script =
	"(function(__fyt){" +
	files.map(file => readFileSync(resolve(root, file), "utf8")).join("\n") +
	"\n__FY.open('#plugins');})('fixture-core-token');";

for (const presentation of [
	{ layout: "compact", width: 382, height: 320, panelWidth: "360px" },
	{ layout: "window", width: 800, height: 720, panelWidth: "778px" },
	{ layout: "window", width: 1280, height: 720, panelWidth: "778px" },
]) {
	test(`panel navigation and focus in ${presentation.layout} at ${presentation.width} by ${presentation.height}`, async ({
		page,
	}) => {
		const errors = [];
		page.on("pageerror", error => errors.push(error.message));
		await page.setViewportSize({ width: presentation.width, height: presentation.height });
		await page.route("**/*", route =>
			route.request().url() === "https://framey.test/"
				? route.fulfill({
						contentType: "text/html",
						body: '<!doctype html><html><head><title>Local Framey fixture</title></head><body><button id="host-control">Host application</button></body></html>',
					})
				: route.abort(),
		);
		await page.goto("https://framey.test/");
		await page.evaluate(
			({ layout, icon }) => {
				window.FY_ICON_URL = icon;
				document.getElementById("host-control").focus();
				window.FY_RUNTIME = {
					version: "1.0.2",
					api: 1,
					platform: "linux-aarch64",
					lang: "en",
					trusted_input: "require",
					panel_layout: layout,
					panel_navigation: "launcher",
				};
				const replies = {
					state_get: {
						order: [],
						hidden: [],
						prefs: {
							channel: "stable",
							autocheck: false,
							notify_loader: false,
							notify_plugins: false,
							dev: { enabled: false },
						},
					},
					panel_status: {
						mode: "embedded",
						supported: true,
						available: true,
						active: true,
						visible: true,
						open: true,
						pending: false,
					},
					list: [],
					loader: { commit: "a".repeat(40), kept: false },
					maint_get: { records: {}, prefs: {} },
				};
				window.fyCall = function (payload) {
					const request = JSON.parse(payload);
					if (request.id)
						queueMicrotask(() => window.Framey._reply(request.gen, request.id, replies[request.method] ?? {}));
				}.bind(null);
			},
			{ layout: presentation.layout, icon: iconData },
		);
		await page.addScriptTag({ content: script });
		await page.evaluate(() => {
			const core = window.Framey._k("fixture-core-token");
			for (let i = 0; i < 16; i++) {
				const id = "plugin" + i;
				const api = core
					.bind(id, "fixture-token-" + id, {
						name: i === 0 ? "Sound controls and advanced application preferences" : "Installed plugin " + i,
					})
					.register({
						render(box) {
							const button = document.createElement("button");
							button.textContent = "Plugin action";
							box.append(button);
						},
					});
				if (i === 0)
					api.pages.register({
						id: "detail",
						title: "Advanced sound settings",
						render(box) {
							box.textContent = "Sound detail content";
						},
					});
			}
			window.Framey.redraw();
		});
		const panel = page.getByRole("region", { name: "Framey", exact: true });
		const destinations = page.getByRole("tablist", { name: "Framey destinations" });
		await expect(panel).toBeVisible();
		await expect(panel).toHaveCSS("width", presentation.panelWidth);

		if (presentation.layout === "window") {
			await expect(destinations).toBeHidden();
			await page.evaluate(() => window.Framey.open("plugin0"));
			await expect(panel.getByRole("button", { name: "Back", exact: true })).toBeHidden();
			await expect(panel.getByRole("button", { name: "Plugin action", exact: true })).toBeVisible();
			await expect(panel.getByRole("navigation", { name: "Page navigation" })).toBeHidden();
			await expect(panel).toHaveCSS("background-color", "rgb(14, 20, 27)");
			await panel.getByRole("button", { name: "Close", exact: true }).click();
			await expect(panel).toBeHidden();
			await expect(page.locator("#host-control")).toBeFocused();
			await page.evaluate(() => window.Framey.open("plugin0:detail"));
			await expect(panel).toContainText("Sound detail content");
			await expect(destinations).toBeHidden();
			await expect(panel.getByRole("button", { name: "Back", exact: true })).toBeHidden();
			await page.keyboard.press("Escape");
			await expect(panel).toBeHidden();
			expect(errors).toEqual([]);
			return;
		}
		await expect(destinations.getByRole("tab")).toHaveText(["Plugins", "Browse", "Settings"]);
		const content = panel.getByRole("tabpanel");
		const assertBounded = async () => {
			expect(
				await content.evaluate(
					node => node.scrollHeight <= node.clientHeight + 1 && node.scrollWidth <= node.clientWidth + 1,
				),
			).toBe(true);
			for (const control of await panel.locator('[role="button"]:visible,[role="tab"]:visible').all()) {
				const bounds = await control.boundingBox(),
					surface = await panel.boundingBox();
				expect(bounds.y).toBeGreaterThanOrEqual(surface.y);
				expect(bounds.y + bounds.height).toBeLessThanOrEqual(surface.y + surface.height + 1);
			}
		};
		const seen = [];
		for (let screen = 0; screen < 18; screen++) {
			await assertBounded();
			seen.push(
				...(await content.locator("[data-fy-route]").evaluateAll(nodes => nodes.map(node => node.dataset.fyRoute))),
			);
			const next = panel
				.getByRole("navigation", { name: "Page navigation" })
				.getByRole("button", { name: "Next", exact: true });
			if ((await next.getAttribute("aria-disabled")) === "true") break;
			await next.click();
		}
		expect(seen).toEqual([
			"plugin0",
			"plugin0:detail",
			...Array.from({ length: 15 }, (_, i) => "plugin" + (i + 1)),
			"#modules",
		]);
		const previous = panel
			.getByRole("navigation", { name: "Page navigation" })
			.getByRole("button", { name: "Previous", exact: true });
		while ((await previous.getAttribute("aria-disabled")) !== "true") await previous.click();
		const first = page.getByRole("button", {
			name: "Sound controls and advanced application preferences",
			exact: true,
		});
		await expect(first).toBeVisible();
		await expect(panel.locator("img")).toHaveAttribute("src", iconData);
		await first.focus();
		await page.setViewportSize({ width: 542, height: 608 });
		await page.evaluate(() => window.Framey._k("fixture-core-token").setPanelLayout("window"));
		await expect(first).toBeFocused();
		await assertBounded();
		await page.setViewportSize({ width: presentation.width, height: presentation.height });
		await page.evaluate(layout => window.Framey._k("fixture-core-token").setPanelLayout(layout), presentation.layout);
		await assertBounded();
		expect(await first.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
		for (const target of await destinations.getByRole("tab").all()) {
			const rect = await target.boundingBox();
			expect(rect.width).toBeGreaterThanOrEqual(48);
			expect(rect.height).toBeGreaterThanOrEqual(48);
		}
		await destinations.getByRole("tab", { name: "Plugins", exact: true }).focus();
		await page.keyboard.press("Shift+Tab");
		await expect(page.getByRole("button", { name: "Close", exact: true })).toBeFocused({ timeout: 1000 });
		await first.click();
		await expect(content).toContainText("Plugin action");
		await page.getByRole("button", { name: "Back", exact: true }).click();
		await expect(first).toBeFocused();
		await page.screenshot({ path: `/tmp/framey-panel-${presentation.layout}-${presentation.width}.png` });
		const plugins = destinations.getByRole("tab", { name: "Plugins", exact: true });
		await plugins.focus();
		await page.keyboard.press("ArrowRight");
		await expect(destinations.getByRole("tab", { name: "Browse", exact: true })).toBeFocused();
		await page.keyboard.press("Enter");
		if (presentation.layout === "compact")
			await panel
				.getByRole("navigation", { name: "Page navigation" })
				.getByRole("button", { name: "Next", exact: true })
				.click();
		await assertBounded();
		await page.getByRole("button", { name: "Decky catalog", exact: true }).click();
		await expect(destinations.getByRole("tab", { name: "Browse", exact: true })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		await page.getByRole("button", { name: "Back", exact: true }).click();
		await expect(page.getByRole("button", { name: "Decky catalog", exact: true })).toBeFocused();
		await destinations.getByRole("tab", { name: "Settings", exact: true }).click();
		await expect(destinations.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
		await page.getByRole("button", { name: "Close", exact: true }).click();
		await expect(panel).toBeHidden();
		await expect(page.locator("#host-control")).toBeFocused();
		await page.evaluate(() => window.Framey.open("#plugins"));
		await page.keyboard.press("Escape");
		await expect(panel).toBeHidden();
		expect(errors).toEqual([]);
	});
}
