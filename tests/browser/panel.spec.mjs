import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
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
	"\n__FY.open('#settings');})('fixture-core-token');";

for (const viewport of [
	{ width: 542, height: 608 },
	{ width: 1280, height: 720 },
]) {
	test(`panel navigation and focus at ${viewport.width} by ${viewport.height}`, async ({ page }) => {
		const errors = [];
		page.on("pageerror", error => errors.push(error.message));
		await page.setViewportSize(viewport);
		await page.route("**/*", route =>
			route.request().url() === "https://framey.test/"
				? route.fulfill({
						contentType: "text/html",
						body: '<!doctype html><html><head><title>Local Framey fixture</title></head><body><button id="host-control">Host application</button></body></html>',
					})
				: route.abort(),
		);
		await page.goto("https://framey.test/");
		await page.evaluate(() => {
			document.getElementById("host-control").focus();
			window.FY_RUNTIME = { version: "1.0.2", api: 1, platform: "linux-aarch64", lang: "en", trusted_input: "require" };
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
		});
		await page.addScriptTag({ content: script });
		const panel = page.getByRole("region", { name: "Framey", exact: true });
		await expect(panel).toBeVisible();
		await expect(page.getByRole("group", { name: "Framey panel", exact: true })).toBeVisible();
		for (const label of ["Inside Steam", "Beside Steam dashboard", "Floating during games"]) {
			await expect(page.getByRole("option", { name: label, exact: true })).toBeVisible();
		}
		await expect(panel).toHaveCSS("width", "520px");
		await expect(page.getByRole("tablist", { name: "Framey pages" })).toHaveCSS("width", "104px");
		await page.getByRole("tab", { name: "Modules", exact: true }).click();
		await expect(page.getByRole("tab", { name: "Modules", exact: true })).toHaveAttribute("aria-selected", "true");
		await page.getByRole("tab", { name: "Settings", exact: true }).click();
		await expect(page.getByRole("group", { name: "Framey panel", exact: true })).toBeVisible();
		await page.keyboard.press("Escape");
		await expect(panel).toBeHidden();
		await expect(page.locator("#host-control")).toBeFocused();
		expect(errors).toEqual([]);
	});
}
