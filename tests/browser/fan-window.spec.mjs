import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const bundle = ["inject.js", "fy-api.js", "fy-ui.js", "fy-pages.js"]
	.map(f => readFileSync(resolve(root, f), "utf8"))
	.join("\n");
const fanSource = readFileSync(resolve(root, "../frame-fan/main.js"), "utf8");
test("Fan Control fills one enlarged real window with no scrolling or sibling navigation", async ({ page }) => {
	const errors = [];
	page.on("pageerror", error => errors.push(error.message));
	await page.setViewportSize({ width: 800, height: 720 });
	await page.setContent('<body style="margin:0;background:#080c11"></body>');
	await page.evaluate(() => {
		window.FY_RUNTIME = { panel_navigation: "launcher", panel_layout: "window", trusted_input: "require" };
		window.fanWrites = [];
		const state = {
			applied: { stock: true },
			profiles: [
				{ name: "Stock", stock: true },
				{ name: "Quiet", floor: 40, ceil: 98, t0: 30, t1: 75, s: 0 },
				{ name: "Balanced", floor: 60, ceil: 98, t0: 30, t1: 75, s: 0 },
				{ name: "Performance", floor: 75, ceil: 98, t0: 30, t1: 75, s: 0 },
			],
			temp: 54.5,
			rpm: 5160,
			pwm: 60,
			zone: "cpu_thermal",
		};
		window.fyCall = function (raw) {
			const req = JSON.parse(raw);
			if (["set", "save", "delete"].includes(req.method)) window.fanWrites.push(req.method);
			if (req.id)
				queueMicrotask(() =>
					window.Framey._reply(
						req.gen,
						req.id,
						req.method === "state_get" ? { order: [], hidden: [] } : req.method === "get" ? state : {},
					),
				);
		}.bind(null);
	});
	await page.addScriptTag({ content: "(function(__fyt){" + bundle + '\n})("fan-window-fixture");' });
	await page.addScriptTag({
		content:
			"(function(Framey){" +
			fanSource +
			'\n})(window.Framey._k("fan-window-fixture").bind("fan","fixture-fan",{name:"Fan Control"}));',
	});
	await page.evaluate(() => window.Framey.open("fan"));
	const panel = page.getByRole("region", { name: "Framey", exact: true });
	const content = panel.getByRole("tabpanel");
	await expect(content.getByRole("button", { name: "Stock", exact: true }).first()).toBeVisible();
	await expect(panel.getByRole("tablist")).toBeHidden();
	await expect(panel.getByRole("button", { name: "Back", exact: true })).toBeHidden();
	await expect(content.getByRole("button", { name: /^(Previous|Next)$/ })).toHaveCount(0);
	const fits = async () => {
		expect(
			await content.evaluate(node => ({
				horizontal: node.scrollWidth > node.clientWidth + 1,
				vertical: node.scrollHeight > node.clientHeight + 1,
			})),
		).toEqual({ horizontal: false, vertical: false });
		const outside = await content.evaluate(node => {
			const b = node.getBoundingClientRect();
			return [...node.querySelectorAll('[role="button"],input,svg')]
				.filter(el => {
					const r = el.getBoundingClientRect();
					return (
						r.width &&
						r.height &&
						(r.left < b.left - 1 || r.right > b.right + 1 || r.top < b.top - 1 || r.bottom > b.bottom + 1)
					);
				})
				.map(el => el.getAttribute("aria-label") || el.textContent);
		});
		expect(outside).toEqual([]);
	};
	await fits();
	await panel.screenshot({
		path: "/tmp/framey-fan-large.png",
	});
	await content.getByRole("button", { name: "Constant", exact: true }).click();
	await fits();
	await content.getByRole("button", { name: "Graph", exact: true }).click();
	await fits();
	await panel.screenshot({
		path: "/tmp/framey-fan-graph-large.png",
	});
	expect(await page.evaluate(() => window.fanWrites)).toEqual([]);
	expect(errors).toEqual([]);
});
