import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const icon = "data:image/svg+xml," + encodeURIComponent(readFileSync(resolve(root, "icon.svg"), "utf8"));
const script =
	"(function(__fyt){" +
	["inject.js", "fy-api.js", "fy-pages.js"].map(file => readFileSync(resolve(root, file), "utf8")).join("\n") +
	"\n})('launcher-fixture-token');";
for (const scenario of [
	{ motion: "no-preference", width: 382, plugins: 1, submenu: false },
	{ motion: "no-preference", width: 542, plugins: 3, submenu: false },
	{ motion: "no-preference", width: 662, plugins: 16, submenu: true },
	{ motion: "reduce", width: 662, plugins: 16, submenu: true },
	{ motion: "no-preference", width: 382, plugins: 16, submenu: true },
]) {
	const { motion, width, plugins, submenu } = scenario;
	const firstRoute = plugins === 1 ? "fan" : "p0";
	const firstName = plugins === 1 ? "Fan Control" : "Sound controls and advanced application preferences";
	test(`launcher ${plugins} plugins with ${motion} at ${width}px`, async ({ page }) => {
		const errors = [];
		page.on("pageerror", error => errors.push(error.message));
		await page.setViewportSize({ width, height: 140 });
		await page.emulateMedia({ reducedMotion: motion });
		await page.route("**/*", route =>
			route.request().url() === "https://framey.test/"
				? route.fulfill({
						contentType: "text/html",
						body: '<!doctype html><html><body style="background:#09090f"><button id="host">Framey bottom-bar icon</button></body></html>',
					})
				: route.abort(),
		);
		await page.goto("https://framey.test/");
		await page.evaluate(icon => {
			window.FY_RUNTIME = { panel_layout: "compact", trusted_input: "require" };
			window.FY_ICON_URL = icon;
			window.requests = [];
			window.launches = [];
			window.pluginRenders = 0;
			window.replyMode = "error";
			document.getElementById("host").focus();
			window.fyCall = function (payload) {
				const request = JSON.parse(payload);
				window.requests.push(request);
				if (request.method === "panel_launch") window.launches.push(request.arg);
				if (request.id)
					(request.method === "panel_launch" ? callback => setTimeout(callback, 100) : queueMicrotask)(() =>
						window.Framey._reply(
							request.gen,
							request.id,
							request.method === "state_get"
								? { order: [], hidden: [] }
								: request.method === "panel_launch"
									? window.replyMode === "error"
										? { error: "private-native-fixture-detail" }
										: { ok: true, pending: true }
									: {},
						),
					);
			}.bind(null);
		}, icon);
		await page.addScriptTag({ content: script });
		await page.evaluate(
			({ plugins, submenu }) => {
				const K = window.Framey._k("launcher-fixture-token");
				for (let i = 0; i < plugins; i++) {
					const api = K.bind(plugins === 1 ? "fan" : "p" + i, "fixture-plugin-token-" + i, {
						name:
							plugins === 1
								? "Fan Control"
								: i === 0
									? "Sound controls and advanced application preferences"
									: "Plugin " + i,
					}).register({
						render() {
							window.pluginRenders++;
						},
					});
					if (i === 0 && submenu)
						api.pages.register({
							id: "advanced",
							title: "Advanced sound menus",
							render() {
								window.pluginRenders++;
							},
						});
				}
				window.Framey.redraw();
				document.getElementById("host").onclick = () => K.openLauncher();
			},
			{ plugins, submenu },
		);
		await page.locator("#host").click();
		const panel = page.getByRole("region", { name: "Framey", exact: true });
		const launcher = page.getByRole("toolbar", { name: "Framey launcher" });
		await expect(launcher).toBeVisible();
		await expect(panel).toHaveCSS("height", "72px");
		await expect(panel).toHaveCSS("background-color", "rgb(14, 20, 27)");
		await expect(panel).toHaveCSS("overflow", "visible");
		await expect(panel.getByRole("tablist")).toBeHidden();
		await expect(panel.getByRole("button", { name: "Close", exact: true })).toBeHidden();
		if (motion === "reduce") await expect(launcher).toHaveCSS("animation-duration", "0s");
		else {
			expect(
				await launcher.evaluate(node =>
					node.getAnimations().some(animation => animation.animationName === "fy-fan-open"),
				),
			).toBe(true);
			await expect(launcher).toHaveAttribute("data-phase", "ready");
		}
		const seen = [];
		for (let batch = 0; batch < 10; batch++) {
			const tiles = launcher.locator("[data-fy-launch]");
			seen.push(...(await tiles.evaluateAll(nodes => nodes.map(node => node.dataset.fyLaunch))));
			expect(
				await launcher.evaluate(node => node.scrollWidth <= node.clientWidth && node.scrollHeight <= node.clientHeight),
			).toBe(true);
			for (const button of await launcher.getByRole("button").all()) {
				const bounds = await button.boundingBox();
				expect(bounds.width).toBeGreaterThanOrEqual(48);
				expect(bounds.height).toBeGreaterThanOrEqual(48);
				expect(bounds.y).toBeGreaterThanOrEqual(0);
				expect(bounds.y + bounds.height).toBeLessThanOrEqual(140);
			}
			const next = launcher.getByRole("button", { name: "Next shortcuts" });
			if (!(await next.count()) || (await next.getAttribute("aria-disabled")) === "true") break;
			await next.click();
		}
		expect(seen).toEqual([
			firstRoute,
			...(submenu ? ["p0:advanced"] : []),
			...Array.from({ length: plugins - 1 }, (_, i) => "p" + (i + 1)),
			"#discover",
			"#decky-store",
			"#settings",
			"#modules",
		]);
		const popupWidth = await page.evaluate(() => window.Framey._k("launcher-fixture-token").launcherWidth());
		expect(popupWidth).toBe(462);
		const boundsOfPanel = await panel.boundingBox();
		expect(Math.abs(boundsOfPanel.x + boundsOfPanel.width / 2 - width / 2)).toBeLessThanOrEqual(1);
		expect(await launcher.locator("[data-fy-launch]").count()).toBeLessThanOrEqual(4);
		await expect(launcher.getByRole("button", { name: "Next shortcuts" })).toHaveCount(1);
		await expect(launcher.getByRole("button", { name: "Previous shortcuts" })).toHaveCount(1);
		while (
			(await launcher.getByRole("button", { name: "Previous shortcuts" }).count()) &&
			(await launcher.getByRole("button", { name: "Previous shortcuts" }).getAttribute("aria-disabled")) !== "true"
		)
			await launcher.getByRole("button", { name: "Previous shortcuts" }).click();
		const first = launcher.locator(`[data-fy-launch="${firstRoute}"]`);
		await expect(first).toHaveAttribute("title", firstName);
		await page.locator("#host").focus();
		await page.mouse.move(0, 0);
		await expect(first.locator(".fy-launcher-tooltip")).toBeHidden();
		await first.hover();
		await expect(first.locator(".fy-launcher-tooltip")).toBeVisible();
		await expect(first.locator(".fy-launcher-tooltip")).toHaveText(firstName);
		await expect(launcher.locator(".fy-launcher-label")).toHaveCount(0);
		await expect(first).toHaveCSS("border-radius", "5px");
		await expect(launcher.locator("img")).toHaveCount(0);
		expect(await first.evaluate(node => getComputedStyle(node).backgroundImage)).toContain("linear-gradient");
		await first.focus();
		await page.keyboard.press("ArrowRight");
		await expect(
			launcher.locator(`[data-fy-launch="${submenu ? "p0:advanced" : plugins === 1 ? "#discover" : "p1"}"]`),
		).toBeFocused();
		await first.focus();
		await page.screenshot({ path: `/tmp/framey-launcher-${plugins}-${width}-${motion}.png` });
		const bounds = await first.boundingBox();
		await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
		await page.mouse.down();
		if (motion !== "reduce") await page.waitForTimeout(100);
		expect(await first.evaluate(node => getComputedStyle(node).boxShadow)).toContain("inset");
		await expect(first).toHaveCSS("background-color", "rgb(14, 20, 27)");
		expect(await first.evaluate(node => getComputedStyle(node).transform)).not.toBe("none");
		await page.mouse.up();
		await page.keyboard.press("Enter");
		if (motion !== "reduce") await expect(launcher).toHaveAttribute("data-phase", "closing");
		await expect(launcher.getByRole("button", { name: "Retry opening page" })).toBeVisible();
		expect(await page.evaluate(() => window.launches)).toEqual([{ route: firstRoute }]);
		expect(await page.evaluate(() => window.pluginRenders)).toBe(0);
		expect(await page.evaluate(() => window.Framey._k("launcher-fixture-token").tab())).toBe("#launcher");
		await expect(launcher).not.toContainText("private-native-fixture-detail");
		let expectedLaunches = 2;
		if (motion === "reduce") {
			await launcher.getByRole("button", { name: "Back to shortcuts" }).focus();
			await page.keyboard.press("Enter");
			await expect(first).toBeFocused({ timeout: 1000 });
			await page.keyboard.press("ArrowRight");
			await expect(launcher.locator('[data-fy-launch="p0:advanced"]')).toBeFocused();
			await first.focus();
			await page.keyboard.press("Enter");
			await expect(launcher.getByRole("button", { name: "Retry opening page" })).toBeVisible();
			expectedLaunches = 3;
		}
		await page.evaluate(() => {
			window.replyMode = "ok";
		});
		await launcher.getByRole("button", { name: "Retry opening page" }).click();
		await expect.poll(() => page.evaluate(() => window.launches.length)).toBe(expectedLaunches);
		expect(await page.evaluate(() => window.pluginRenders)).toBe(0);
		await page.keyboard.press("Escape");
		await expect(panel).toBeHidden();
		await expect(page.locator("#host")).toBeFocused();
		expect(errors).toEqual([]);
	});
}
