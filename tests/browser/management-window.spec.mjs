import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
const src =
	"(function(__fyt){" +
	["inject.js", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js"]
		.map(f => readFileSync(resolve(root, f), "utf8"))
		.join("\n") +
	"\nwindow.managementFY=__FY;})('test-token')";
for (const size of [
	{ width: 488, height: 493 },
	{ width: 746, height: 608 },
]) {
	test(`management focused flows fit ${size.width}×${size.height}`, async ({ page }) => {
		await page.setViewportSize({ width: 900, height: 800 });
		await page.route("**/*", route =>
			route.request().url() === "https://framey.test/"
				? route.fulfill({ contentType: "text/html", body: "<!doctype html><html><head></head><body></body></html>" })
				: route.abort(),
		);
		await page.goto("https://framey.test/");
		await page.evaluate(() => {
			window.FY_RUNTIME = {
				version: "1.2.0",
				api: 1,
				lang: "en",
				platform: "linux-aarch64",
				trusted_input: "off",
				panel_navigation: "launcher",
				panel_layout: "window",
			};
			window.managementCalls = [];
			window.managementReplies = {
				list: Array.from({ length: 100 }, (_, i) => ({
					id: "p" + i,
					name: "Plugin " + i,
					version: "1.0",
					enabled: true,
					commit: "a".repeat(40),
					compat: { badge: "ok", msg: "Compatible" },
					pending: i === 99,
					fail: i === 99 ? { what: "backend", msg: "Failure evidence ".repeat(40), stage: "download" } : null,
					companions: Array.from({ length: 6 }, (_, j) => ({ name: "Helper " + j })),
				})),
				store: Array.from({ length: 50 }, (_, i) => ({ id: "store" + i, name: "Available " + i })),
				state_get: { sort: "custom", order: [], hidden: [], frozen: {}, safe: true },
				status: { settings_state: "corrupt" },
				maint_prefs: { "maint.window_days": 365 },
				maint_get: {
					records: {
						p99: {
							label: "recent",
							reason: "Maintenance evidence ".repeat(80) + " END EVIDENCE",
							evidence: Array.from({ length: 30 }, (_, i) => "Evidence " + i),
						},
					},
				},
				ver_list: {
					versions: Array.from({ length: 30 }, (_, i) => ({
						tag: "v" + i,
						version: "2.0." + i,
						eligible: true,
						compat: { badge: "ok" },
					})),
				},
				notes: { notes: "line\n".repeat(20) + "Release notes ".repeat(200) },
				ver_install: { error: "Version install failed. ".repeat(30), stage: "download" },
				batch_plan: {
					items: Array.from({ length: 70 }, (_, i) => ({
						id: "p" + i,
						name: "Plugin " + i,
						from: "1.0",
						to: "2.0",
						eligible: i < 65,
						reason: "Not eligible",
					})),
				},
				batch_apply: {
					results: Array.from({ length: 70 }, (_, i) => ({
						id: "p" + i,
						name: "Plugin " + i,
						state: i === 69 ? "failed" : "ok",
						error: "FINAL RESULT EVIDENCE ".repeat(30),
						to: "2.0",
					})),
				},
			};
			window.fyCall = function (raw) {
				const c = JSON.parse(raw);
				window.managementCalls.push(c);
				if (c.id) queueMicrotask(() => Framey._reply(c.gen, c.id, window.managementReplies[c.method] || {}));
			}.bind(null);
		});
		await page.addScriptTag({ content: src });
		await page.evaluate(size => {
			const p = document.createElement("div");
			p.className = "fy-panel";
			p.style.cssText = `position:relative;display:block;left:0;top:0;width:${size.width}px;height:${size.height}px;padding:0;border:0;background:#0e141b;color:#fff;font:20px sans-serif`;
			const b = document.createElement("div");
			b.id = "management-fixture";
			b.style.cssText = "height:100%;width:100%;box-sizing:border-box";
			p.append(b);
			document.body.append(p);
			window.managementFY.pages.modules.render(b);
		}, size);
		const content = page.locator("#management-fixture");
		const bounds = async () => {
			const result = await content.evaluate(box => {
				const rect = box.getBoundingClientRect();
				return {
					w: box.clientWidth,
					h: box.clientHeight,
					sw: box.scrollWidth,
					sh: box.scrollHeight,
					out: [...box.querySelectorAll("input,select,[role=button],[role=switch]")]
						.filter(n => {
							const r = n.getBoundingClientRect();
							return (
								r.width &&
								r.height &&
								(r.bottom > rect.bottom + 1 ||
									r.right > rect.right + 1 ||
									r.left < rect.left - 1 ||
									r.top < rect.top - 1)
							);
						})
						.map(n => n.getAttribute("aria-label")),
				};
			});
			expect(result.sw).toBeLessThanOrEqual(result.w + 1);
			expect(result.sh).toBeLessThanOrEqual(result.h + 1);
			expect(result.out).toEqual([]);
			expect(await page.getByRole("button", { name: "Previous", exact: true }).count()).toBe(0);
			expect(await page.getByRole("button", { name: "Next", exact: true }).count()).toBe(0);
		};
		const run = async label => {
			await content.getByRole("combobox", { name: "Actions", exact: true }).selectOption({ label });
			await content.getByRole("button", { name: "Run action", exact: true }).click();
		};
		await expect(content.getByRole("textbox", { name: "Search installed plugins" })).toBeVisible();
		await bounds();
		await content.getByRole("textbox", { name: "Search installed plugins" }).fill("p99");
		await content.getByRole("button", { name: "Plugin 99", exact: true }).click();
		await bounds();
		await content.getByRole("button", { name: "Details", exact: true }).click();
		await content.getByRole("textbox", { name: "Find a detail" }).fill("END EVIDENCE");
		await content.getByRole("combobox", { name: "Text segment" }).selectOption("last");
		await expect(content).toContainText("END EVIDENCE");
		await bounds();
		await content.getByRole("button", { name: "Back", exact: true }).click();
		await run("Remove and delete data");
		await expect(content).toContainText("cannot be undone");
		await bounds();
		expect(await page.evaluate(() => window.managementCalls.filter(c => c.method === "uninstall").length)).toBe(0);
		await content.getByRole("button", { name: "Cancel", exact: true }).click();
		await run("Versions");
		await expect(content.getByRole("combobox", { name: "Actions", exact: true })).toBeVisible();
		await bounds();
		await run("2.0.29");
		await bounds();
		await content.getByRole("button", { name: "Release notes", exact: true }).click();
		await bounds();
		await content.getByRole("combobox", { name: "Text segment" }).selectOption("last");
		await bounds();
		await content.getByRole("button", { name: "Back", exact: true }).click();
		await content.getByRole("button", { name: "Install this version", exact: true }).click();
		await expect(content).toContainText("Version install failed");
		await bounds();
		await page.screenshot({ path: `/tmp/framey-management-window-${size.width}.png` });
	});
}

test("actual window shell keeps management focus and all controls inside the enlarged content", async ({ page }) => {
	await page.setViewportSize({ width: 800, height: 720 });
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({ contentType: "text/html", body: "<!doctype html><html><head></head><body></body></html>" })
			: route.abort(),
	);
	await page.goto("https://framey.test/");
	await page.evaluate(() => {
		window.FY_RUNTIME = {
			version: "1.2.0",
			api: 1,
			lang: "en",
			trusted_input: "off",
			panel_navigation: "launcher",
			panel_layout: "window",
		};
		window.fyCall = function (raw) {
			const c = JSON.parse(raw);
			if (c.id)
				queueMicrotask(() =>
					Framey._reply(
						c.gen,
						c.id,
						{
							list: Array.from({ length: 30 }, (_, i) => ({
								id: "p" + i,
								name: "Plugin " + i,
								enabled: true,
								version: "1.0",
								commit: "a".repeat(40),
								compat: { badge: "ok" },
							})),
							store: [],
							state_get: { sort: "custom", order: [], hidden: [], frozen: {} },
							maint_get: { records: {} },
							status: {},
							maint_prefs: {},
						}[c.method] || {},
					),
				);
		}.bind(null);
	});
	await page.addScriptTag({ content: src });
	await page.evaluate(() => window.managementFY.open("#modules"));
	const content = page.getByRole("tabpanel");
	const panel = page.locator(".fy-panel");
	await expect(content.getByRole("textbox", { name: "Search installed plugins" })).toBeVisible();
	await content.getByRole("textbox", { name: "Search installed plugins" }).fill("p29");
	await content.getByRole("button", { name: "Plugin 29", exact: true }).click();
	await expect(content.getByRole("combobox", { name: "Actions", exact: true })).toBeVisible();
	await content.getByRole("button", { name: "Details", exact: true }).click();
	await expect(content.getByRole("textbox", { name: "Find a detail" })).toBeFocused();
	const geometry = await content.evaluate(n => ({
		w: n.clientWidth,
		h: n.clientHeight,
		sw: n.scrollWidth,
		sh: n.scrollHeight,
	}));
	expect(geometry.w).toBeGreaterThan(700);
	expect(geometry.h).toBeGreaterThan(550);
	expect(geometry.sw).toBeLessThanOrEqual(geometry.w + 1);
	expect(geometry.sh).toBeLessThanOrEqual(geometry.h + 1);
	await expect(panel.getByRole("tab")).toHaveCount(0);
	await expect(panel.getByRole("button", { name: "Previous", exact: true })).toHaveCount(0);
	await expect(panel.getByRole("button", { name: "Next", exact: true })).toHaveCount(0);
	await page.screenshot({ path: "/tmp/framey-management-shell.png" });
});
