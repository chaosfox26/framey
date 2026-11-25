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
	"fy-modules.js",
	"fy-catalog.js",
];
const script =
	"(function(__fyt){" +
	files.map(file => readFileSync(resolve(root, file), "utf8")).join("\n") +
	`
 const core = __FY._k(__fyt);
${readFileSync(resolve(root, "decky_runtime/frontend/store.js"), "utf8")}

 if(window.fixtureDecky) core.optionalRuntime = { store(context) { return Promise.resolve(createFrameyDeckyStore({...context,generation:1,current:()=>true,alive:context.alive,importEnabled:false})); } };

 window.catalogPages = 0;
 const set = core.setPageNavigation;
 core.setPageNavigation = options => { window.catalogPages++; return set(options); };
 window.catalogBox = document.getElementById('catalog-fixture');
 __FY.pages[window.fixtureDecky ? 'decky_store' : 'store'].render(window.catalogBox);
})('catalog-window-fixture');`;

for (const dimensions of [
	{ width: 746, height: 608 },
	{ width: 488, height: 493 },
]) {
	for (const decky of [false, true]) {
		test(`${decky ? "Decky" : "native"} catalog fits ${dimensions.width} × ${dimensions.height} without window pagination`, async ({
			page,
		}) => {
			const errors = [];
			page.on("pageerror", error => errors.push(error.message));
			await page.setViewportSize({ width: dimensions.width + 40, height: dimensions.height + 40 });
			await page.goto("about:blank");
			await page.setContent(
				`<div id="catalog-fixture" style="width:${dimensions.width}px;height:${dimensions.height}px;box-sizing:border-box"></div>`,
			);
			await page.evaluate(decky => {
				window.fixtureDecky = decky;
				window.FY_RUNTIME = { panel_navigation: "launcher", panel_layout: "window", trusted_input: "off" };
				window.fixtureDescription = "Line\r\n".repeat(50) + "\n".repeat(20) + "界".repeat(1000) + "Final complete line";
				window.fixtureCalls = [];
				const entry = {
					id: "one",
					name: "Catalog module",
					identity: "entry-one",
					version: "1",
					description: window.fixtureDescription,
					eligible: true,
					platform_declared: true,
					source: { repository: "https://example.com/module", commit: "a".repeat(40) },
					compat: { state: "compatible", badge: "ok", msg: "Declared requirements match." },
					permissions: ["filesystem", "network"],
					...(decky
						? {
								format: "decky",
								read_only: true,
								versions: [{ name: "1", identity: "version-one", hash: "b".repeat(64) }],
							}
						: {}),
				};
				window.fyCall = (encoded => {
					const c = JSON.parse(encoded);
					window.fixtureCalls.push(c);
					if (!c.id) return;
					let r = {};
					if (c.method === "decky_runtime_status")
						r = {
							installed: true,
							enabled: true,
							requested_enabled: true,
							version: "fixture",
							previous_version: null,
							generation: 1,
							phase: "enabled",
							error: null,
						};
					if (c.method === "catalog_sources") r = { source: { id: "stable" } };
					if (c.method === "catalog_list")
						r = {
							rows: Array.from({ length: 50 }, (_, index) => ({
								...entry,
								id: "module-" + index,
								name: index ? "Catalog module " + index : entry.name,
							})),
							total: 512,
							origin: "downloaded",
							...window.fixtureListExtra,
						};
					if (c.method === "catalog_details") r = { entry: { ...entry, id: c.arg.id }, origin: "downloaded" };
					queueMicrotask(() => window.Framey._reply(c.gen, c.id, r));
				}).bind(null);
			}, decky);
			await page.addScriptTag({ content: script });
			const box = page.locator("#catalog-fixture");
			await expect(box.locator("[data-catalog-result]")).toHaveCount(dimensions.width < 650 ? 2 : 3);
			await expect(box.locator("select")).toHaveCount(0);
			await expect(box).toContainText((dimensions.width < 650 ? "2" : "3") + " of 512");
			const fits = async () => {
				expect(
					await page.evaluate(() => {
						const box = window.catalogBox,
							bounds = box.getBoundingClientRect();
						return {
							scroll: box.scrollHeight > box.clientHeight + 1 || box.scrollWidth > box.clientWidth + 1,
							outside: [...box.querySelectorAll("[aria-label], [data-catalog-window]")]
								.filter(node => {
									const rect = node.getBoundingClientRect();
									return (
										rect.right > bounds.right + 1 ||
										rect.bottom > bounds.bottom + 1 ||
										rect.left < bounds.left - 1 ||
										rect.top < bounds.top - 1
									);
								})
								.map(node => node.getAttribute("aria-label") || node.dataset.catalogWindow),
							pages: window.catalogPages,
						};
					}),
				).toEqual({ scroll: false, outside: [], pages: 0 });
				await expect(box.getByLabel(/Previous|Next/)).toHaveCount(0);
			};
			await fits();
			await box.getByLabel("Filters", { exact: true }).click();
			await fits();
			if (!decky) {
				await box.getByLabel("Source", { exact: true }).selectOption("custom");
				await fits();
			}
			await box.getByLabel("Done filtering", { exact: true }).click();
			if (!decky) {
				await box.getByLabel("Filters", { exact: true }).click();
				await box.getByLabel("Source", { exact: true }).selectOption("stable");
				await box.getByLabel("Done filtering", { exact: true }).click();
			}
			await box.getByLabel("Details: Catalog module", { exact: true }).click();
			await expect(box.getByLabel("Read details", { exact: true })).toBeVisible();
			await fits();
			await box.getByLabel("Read details", { exact: true }).click();
			await box.getByLabel("Details to read", { exact: true }).selectOption("Description");
			expect(await box.getByLabel("Full details", { exact: true }).textContent()).toEqual(
				await page.evaluate(() => window.fixtureDescription),
			);
			await fits();
			await box.getByLabel("Full details", { exact: true }).focus();
			await page.keyboard.press("ArrowDown");
			await expect(box.getByLabel("Full details", { exact: true })).toBeFocused();
			await page.keyboard.press("End");
			await page.keyboard.press("Control+End");
			await expect(box.getByLabel("Done reading", { exact: true })).toBeVisible();
			await box.getByLabel("Done reading", { exact: true }).click();
			if (!decky) {
				await box.getByLabel("Install Catalog module", { exact: true }).click();
				await fits();
				await box.getByLabel("Cancel", { exact: true }).click();
			}
			expect(
				await page.evaluate(() =>
					window.fixtureCalls.some(c => c.method === "catalog_install" || c.method === "catalog_assess"),
				),
			).toBe(false);
			await box.getByLabel("Back", { exact: true }).click();
			await page.evaluate(() => {
				window.fixtureListExtra = { origin: "cache", stale: true, fetch_error: "Saved catalog problem ".repeat(60) };
			});
			await box.getByLabel("Refresh", { exact: true }).click();
			await expect(box.getByLabel("Report to Framey", { exact: true })).toBeVisible();
			await fits();
			await page.evaluate(() => {
				window.fixtureListExtra = { error: "Catalog error ".repeat(60) };
			});
			await box.getByLabel("Refresh", { exact: true }).click();
			await expect(box.getByLabel("Retry", { exact: true })).toBeVisible();
			await fits();
			await box.getByLabel("Catalog details", { exact: true }).click();
			await box.getByLabel("Details to read", { exact: true }).selectOption("Catalog error");
			await expect(box.getByLabel("Full details", { exact: true })).toContainText("Catalog error");
			await fits();
			expect(errors).toEqual([]);
		});
	}
}
