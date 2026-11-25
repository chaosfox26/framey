import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";
const root = resolve(import.meta.dirname, "../..");
const files = ["inject.js", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js"];
const src =
	"(function(__fyt){" +
	files.map(f => readFileSync(root + "/" + f, "utf8")).join("\n") +
	"\n__FY.open('#modules');})('test-token')";
for (const layout of ["compact"]) {
	test(`management workflows fit every ${layout} screen`, async ({ page }) => {
		test.setTimeout(120000);
		await page.setViewportSize(layout === "compact" ? { width: 382, height: 320 } : { width: 542, height: 608 });
		await page.route("**/*", r =>
			r.request().url() === "https://framey.test/"
				? r.fulfill({ contentType: "text/html", body: "<!doctype html><html><head></head><body></body></html>" })
				: r.abort(),
		);
		await page.goto("https://framey.test/");
		await page.evaluate(layout => {
			window.FY_RUNTIME = {
				version: "1.2.0",
				api: 1,
				platform: "linux-aarch64",
				lang: "en",
				trusted_input: "off",
				panel_layout: layout,
			};
			window.replies = {
				list: [
					{
						id: "a",
						name: "Alpha",
						version: "1.0.0",
						commit: "abc123",
						enabled: true,
						compat: { badge: "ok", msg: "" },
						fail: { what: "backend", msg: "Failure evidence ".repeat(24), stage: "download" },
						pending: true,
						held: false,
						companions: [{ name: "Helper", platform: "linux-aarch64" }],
					},
				],
				store: [{ id: "test", name: "Store plugin", version: "2.0" }],
				state_get: { order: ["a"], sort: "custom", hidden: [], frozen: {}, prefs: {}, safe: true },
				status: { settings_state: "corrupt" },
				maint_prefs: { "maint.window_days": 365 },
				maint_get: {
					records: {
						a: {
							label: "recent",
							repo: "acme/widget",
							reason: "Upstream status reason ".repeat(30),
							scope: "whole repository",
							source: "https://github.com/acme/widget",
							branch: "main",
							commit_at: "2026-10-01",
							checked_at: 1790000000,
							evidence: ["Evidence ".repeat(30), "More evidence"],
							window_days: 365,
						},
					},
				},
				ver_list: {
					versions: Array.from({ length: 12 }, (_, i) => ({
						tag: "v" + i,
						version: "1.0." + i,
						published: "2026-10-01",
						eligible: true,
						compat: { badge: "ok", msg: "Compatible" },
					})),
					more: false,
				},
				notes: { notes: Array.from({ length: 16 }, () => "- ok").join("\n") + "\n" + "Release notes ".repeat(160) },
				ver_install: { error: "Could not install version. ".repeat(24), stage: "download" },
				batch_apply: {
					results: [
						{ id: "p0", name: "Plugin 0", state: "ok", to: "2.0.0" },
						{ id: "p1", name: "Plugin 1", state: "failed", error: "Download failed. ".repeat(24) },
					],
				},
				batch_plan: {
					items: Array.from({ length: 12 }, (_, i) => ({
						id: "p" + i,
						name: "Plugin " + i,
						from: "1.0.0",
						to: "2.0.0",
						eligible: i < 8,
						reason: "Unavailable",
					})),
				},
			};
			window.fyCall = function (raw) {
				const c = JSON.parse(raw);
				if (c.id) queueMicrotask(() => Framey._reply(c.gen, c.id, window.replies[c.method] ?? {}));
			}.bind(null);
		}, layout);
		await page.addScriptTag({ content: src });
		const content = page.locator("[role=tabpanel]"),
			panel = page.locator(".fy-panel");
		await page.waitForFunction(() => document.querySelector("[role=tabpanel] [role=button]"));
		const bounds = async () => {
			const value = await content.evaluate(n => ({
				w: n.clientWidth,
				h: n.clientHeight,
				sw: n.scrollWidth,
				sh: n.scrollHeight,
				text: n.textContent,
			}));
			expect(value.sw, value.text).toBeLessThanOrEqual(value.w + 1);
			expect(value.sh, value.text).toBeLessThanOrEqual(value.h + 1);
		};
		const seek = async label => {
			const previous = panel.getByRole("button", { name: "Previous", exact: true });
			if (await previous.isVisible())
				while ((await previous.getAttribute("aria-disabled")) !== "true") await previous.click();
			for (let i = 0; i < 200; i++) {
				await bounds();
				const hit = content.getByRole("button", { name: label, exact: true });
				if (await hit.count()) return hit;
				const next = panel.getByRole("button", { name: "Next", exact: true });
				if (!(await next.isVisible()) || (await next.getAttribute("aria-disabled")) === "true")
					throw Error("Missing " + label);
				await next.click();
			}
		};
		const scan = async () => {
			const previous = panel.getByRole("button", { name: "Previous", exact: true });
			if (await previous.isVisible())
				while ((await previous.getAttribute("aria-disabled")) !== "true") await previous.click();
			for (let i = 0; i < 250; i++) {
				await bounds();
				const next = panel.getByRole("button", { name: "Next", exact: true });
				if (!(await next.isVisible()) || (await next.getAttribute("aria-disabled")) === "true") return;
				await next.click();
			}
			throw Error("Too many pages");
		};
		const back = () => panel.getByRole("button", { name: "Back", exact: true }).click();
		await (await seek("Alpha")).click();
		await scan();
		await (await seek("More")).click();
		await scan();
		await (await seek("Versions")).click();
		await scan();
		await (await seek("1.0.0")).click();
		await scan();
		await (await seek("Release notes")).click();
		await scan();
		await back();
		await (await seek("Install this version")).click();
		await page.waitForFunction(() =>
			document.querySelector('[role="tabpanel"]').textContent.includes("Could not install"),
		);
		await scan();
		await back();
		await back();
		await (await seek("Alpha")).click();
		await (await seek("Upstream project status of Alpha: Recent activity")).click();
		await scan();
		await back();
		await back();
		await (await seek("Alpha")).click();
		await (await seek("Remove")).click();
		await scan();
		await back();
		await back();
		await (await seek("Manage plugins")).click();
		await scan();
		await (await seek("Update all")).click();
		await scan();
		await (await seek("Update 8")).click();
		await page.waitForFunction(() => document.querySelector('[role="tabpanel"]').textContent.includes("updated"));
		await scan();
	});
}
