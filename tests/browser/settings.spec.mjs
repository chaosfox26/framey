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
	"fy-settings.js",
];
const script =
	"(function(__fyt){" +
	files.map(file => readFileSync(resolve(root, file), "utf8")).join("\n") +
	"\nwindow.settingsCore=__FY._k(__fyt);})('settings-window-fixture');";

async function openSettings(page, category = "preferences", width = 746, height = 608) {
	await page.setViewportSize({ width: width + 54, height: height + 112 });
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({
					contentType: "text/html",
					body: "<!doctype html><html><body style='margin:0;background:#080d12'></body></html>",
				})
			: route.abort(),
	);
	await page.goto("https://framey.test/");
	await page.evaluate(() => {
		window.FY_RUNTIME = {
			version: "1.1.0",
			api: 1,
			platform: "linux-aarch64",
			lang: "en",
			trusted_input: "off",
			panel_navigation: "launcher",
			panel_layout: "window",
		};
		window.requests = [];
		window.checkReply = {
			loader: { state: "update", notes: "A verified fixture update." },
			candidate: { tag: "v1.2.0", commit: "a".repeat(40), published: "2026-10-01", prerelease: false },
			installed: { version: "1.1.0" },
		};
		window.replies = {
			loader: { commit: "1234567", kept: true },
			state_get: {
				prefs: {
					channel: "stable",
					autocheck: false,
					notify_loader: true,
					notify_plugins: false,
					workers_max: 2,
					dev: { enabled: false },
				},
				safe: false,
			},
			autocheck_status: { enabled: false, last: 0 },
			maint_prefs: { "maint.enabled_background": true, "maint.window_days": 365 },
			status: {},
			decky_runtime_status: {
				installed: false,
				enabled: false,
				requested_enabled: false,
				version: null,
				previous_version: null,
				generation: 0,
				phase: "absent",
				error: null,
			},
			decky_runtime_enable: {
				installed: false,
				enabled: false,
				requested_enabled: false,
				version: null,
				previous_version: null,
				generation: 0,
				phase: "error",
				error: { code: "release_unavailable", message: "No runtime release has been published" },
			},
			loader_update: { ok: true },
			panel_status: { mode: "window", layout: "window", available: true, active: true },
			notes: { notes: "Fixture release notes\n".repeat(80) + "LAST NOTE MARKER" },
			ver_list: {
				versions: Array.from({ length: 30 }, (_, index) => ({
					version: "Build " + index,
					tag: "v" + index,
					eligible: true,
					published: "2026-10-01",
					compat: { badge: "ok", msg: "Compatible fixture" },
				})),
				more: false,
			},
		};
		window.fyCall = function (payload) {
			const request = JSON.parse(payload);
			window.requests.push(request);
			if (!request.id) return;
			queueMicrotask(() =>
				window.Framey._reply(
					request.gen,
					request.id,
					request.method === "loader_check" ? window.checkReply : (window.replies[request.method] ?? {}),
				),
			);
		}.bind(null);
	});
	await page.addScriptTag({ content: script });
	// An inert, exact-size owned content fixture isolates Options from changes to
	// the shared shell; launcher/native routing has its own integration coverage.
	await page.evaluate(
		({ category, width, height }) => {
			const shell = document.createElement("section");
			shell.className = "fy-panel";
			shell.dataset.fyNavigation = "single";
			shell.dataset.fyLayout = "window";
			shell.style.cssText =
				"display:flex;position:absolute;inset:11px auto auto 11px;padding:16px;background:#0e141b;color:#fff;border-radius:6px;font:18px sans-serif;width:" +
				width +
				"px";
			const box = document.createElement("div");
			box.setAttribute("role", "tabpanel");
			box.id = "settings-fixture";
			box.style.height = height + "px";
			box.style.width = width + "px";
			shell.append(box);
			document.body.append(shell);
			window.openSettingsRoute = category =>
				window.settingsCore.windowPages.find(route => route.id === "#settings:" + category).render(box);
			window.openSettingsRoute(category);
		},
		{ category, width, height },
	);
	await expect(page.locator("#settings-fixture")).not.toContainText("Loading");
}
async function fits(page) {
	const issues = await page.locator("#settings-fixture").evaluate(box => {
		const bounds = box.getBoundingClientRect();
		return [...box.querySelectorAll("[role=button],[role=switch],[role=slider],input")]
			.filter(node => {
				const rect = node.getBoundingClientRect();
				return (
					rect.left < bounds.left - 1 ||
					rect.right > bounds.right + 1 ||
					rect.top < bounds.top - 1 ||
					rect.bottom > bounds.bottom + 1
				);
			})
			.map(node => node.getAttribute("aria-label"));
	});
	expect(issues).toEqual([]);
	const overflow = await page.locator("#settings-fixture").evaluate(box => ({
		vertical: box.scrollHeight > box.clientHeight + 1,
		horizontal: box.scrollWidth > box.clientWidth + 1,
	}));
	expect(overflow).toEqual({ vertical: false, horizontal: false });
	await expect(
		page
			.locator("#settings-fixture")
			.getByRole("button", { name: /^(Previous|Next|Updates|Preferences|Maintenance|Worker|Support)$/ }),
	).toHaveCount(0);
}
for (const [category, control] of [
	["preferences", "Check automatically"],
	["updates", "Check for updates"],
	["maintenance", "Check project maintenance in the background"],
	["worker", "Modules running at once"],
	["decky", "Enable Decky compatibility"],
	["support", "Panel status"],
]) {
	test("focused " + category + " screen fits large and reduced window bounds", async ({ page }) => {
		await openSettings(page, category);
		await expect(page.locator("#settings-fixture").getByLabel(control, { exact: true })).toBeVisible();
		await fits(page);
		await page.screenshot({ path: "/tmp/framey-options-" + category + ".png" });
		await page.locator("#settings-fixture").evaluate(box => {
			box.style.width = "488px";
			box.style.height = "493px";
		});
		await fits(page);
	});
}
test("Update confirmation, result and exact candidate stay inside one window", async ({ page }) => {
	await openSettings(page, "updates");
	const box = page.locator("#settings-fixture");
	await box.getByRole("button", { name: "Update", exact: true }).click();
	await expect(box).toContainText("Available: v1.2.0");
	await fits(page);
	expect(await page.evaluate(() => window.requests.filter(request => request.method === "loader_update").length)).toBe(
		0,
	);
	await box.getByRole("button", { name: "Update now", exact: true }).click();
	await expect(box).toContainText("Updated, Framey is restarting");
	await fits(page);
	await box.getByRole("button", { name: "Back", exact: true }).click();
	await expect(box.getByRole("button", { name: "Check for updates", exact: true })).toBeVisible();
});
test("history filtering reaches the final version and notes expose complete searchable text", async ({ page }) => {
	await openSettings(page, "updates");
	const box = page.locator("#settings-fixture");
	await box.getByRole("button", { name: "History", exact: true }).click();
	await box.getByRole("searchbox", { name: "Find a version" }).fill("Build 29");
	await box.getByRole("button", { name: "Build 29", exact: true }).click();
	await expect(box).toContainText("Compatible fixture");
	await fits(page);
	await box.getByRole("button", { name: "Release notes", exact: true }).click();
	await box.getByRole("searchbox", { name: "Find in text" }).fill("LAST NOTE MARKER");
	await expect(box).toContainText("LAST NOTE MARKER");
	await fits(page);
});

test("launcher-owned Options routes render directly in the real shared window", async ({ page }) => {
	await openSettings(page, "preferences");
	await page.evaluate(() => {
		document.querySelector("#settings-fixture").parentElement.remove();
		window.Framey.open("#settings:updates");
	});
	const content = page.getByRole("tabpanel");
	await expect(content.getByRole("button", { name: "Check for updates", exact: true })).toBeVisible();
	await expect(content.getByRole("button", { name: "Updates", exact: true })).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Next", exact: true })).toHaveCount(0);
	expect(await page.evaluate(() => window.settingsCore.tab())).toBe("#settings:updates");
	await page.screenshot({ path: "/tmp/framey-options-integrated-updates.png" });
	await page.evaluate(() => window.Framey.open("#settings:preferences"));
	await expect(content.getByRole("switch", { name: "Check automatically", exact: true })).toBeVisible();
	await page.evaluate(() => window.Framey.open("#settings"));
	await expect(content.getByRole("switch", { name: "Check automatically", exact: true })).toBeVisible();
});

test("Decky download failure stays off and the disabled Store points directly to Settings", async ({ page }, testInfo) => {
	await openSettings(page, "decky", 488, 493);
	const box = page.locator("#settings-fixture");
	await box.getByRole("switch", { name: "Enable Decky compatibility" }).click();
	await expect(box).toContainText("No runtime release has been published");
	await expect(box.getByRole("switch", { name: "Enable Decky compatibility" })).toHaveAttribute(
		"aria-checked",
		"false",
	);
	await fits(page);
	await page.screenshot({
		path: testInfo.outputPath("decky-download-unavailable.png"),
	});
	await page.evaluate(() => window.Framey.pages.decky_store.render(document.getElementById("settings-fixture")));
	await expect(box).toContainText("Decky compatibility is off");
	await expect(box.getByRole("button", { name: "Decky compatibility settings" })).toBeVisible();
	await fits(page);
	await expect
		.poll(() => page.evaluate(() => window.requests.filter(request => request.method === "catalog_list").length))
		.toBe(0);
});
