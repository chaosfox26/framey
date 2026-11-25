import { test } from "@playwright/test";
import assert from "node:assert/strict";
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
 const set = core.setPageNavigation;
 core.setPageNavigation = options => { window.catalogNavigation = options; return set(options); };
 __FY.open('#discover');
})('catalog-layout-fixture');`;

for (const layout of ["compact"]) {
	test(`catalog line breaks preserve complete text inside ${layout} panel bounds`, async ({ page }) => {
		await page.setViewportSize(layout === "compact" ? { width: 382, height: 320 } : { width: 542, height: 608 });
		const errors = [];
		page.on("pageerror", error => errors.push(error.message));
		await page.goto("about:blank");
		await page.evaluate(layout => {
			window.FY_RUNTIME = {
				version: "1.1.0",
				api: 1,
				platform: "linux-aarch64",
				lang: "en",
				trusted_input: "off",
				panel_layout: layout,
			};
			const entry = {
				id: "lines",
				name: "Line-rich module",
				version: "1",
				identity: "a".repeat(64),
				eligible: true,
				platform_declared: true,
				source: { repository: "https://example.com/module", commit: "b".repeat(40) },
				description: "Line\n".repeat(12) + "\n".repeat(10) + "界".repeat(70) + "\r\nTail\u2028End\u2029",
				compat: { state: "compatible", badge: "ok", msg: "Compatibility line\n".repeat(12) },
				permissions: "Permission\r\n".repeat(12) + "\n".repeat(10),
			};
			window.fyCall = function (encoded) {
				const call = JSON.parse(encoded);
				if (!call.id) return;
				let reply = {};
				if (call.method === "catalog_sources") reply = { source: { id: "stable" } };
				if (call.method === "catalog_list") reply = { rows: [entry], offset: 0, total: 1, origin: "downloaded" };
				if (call.method === "catalog_details") reply = { entry, origin: "downloaded" };
				queueMicrotask(() => window.Framey._reply(call.gen, call.id, reply));
			}.bind(null);
		}, layout);
		await page.addScriptTag({ content: script });
		await page.waitForFunction(() => window.catalogNavigation && window.catalogNavigation.count > 4);
		await page.evaluate(() => {
			const count = window.catalogNavigation.count;
			for (let index = 0; index < count; index++) {
				window.catalogNavigation.onPage(index);
				const button = document.querySelector('[aria-label="Details: Line-rich module"]');
				if (button) {
					button.click();
					return;
				}
			}
			throw new Error("Details were not reachable");
		});
		await page.waitForFunction(
			() =>
				window.catalogNavigation &&
				window.catalogNavigation.title === "Line-rich module" &&
				window.catalogNavigation.count > 4,
		);
		const result = await page.evaluate(() => {
			const content = document.querySelector('.fy-panel [role="tabpanel"]');
			const joined = { Description: "", Compatibility: "", Permissions: "" };
			const failures = [];
			const count = window.catalogNavigation.count;
			for (let index = 0; index < count; index++) {
				window.catalogNavigation.onPage(index);
				const bounds = content.getBoundingClientRect();
				if (content.scrollHeight > content.clientHeight + 1 || content.scrollWidth > content.clientWidth + 1)
					failures.push({
						index,
						kind: "scroll",
						height: content.clientHeight,
						scrollHeight: content.scrollHeight,
						text: content.textContent,
					});
				for (const node of content.querySelectorAll("[data-catalog-step]")) {
					const rect = node.getBoundingClientRect();
					if (
						rect.bottom > bounds.bottom + 1 ||
						rect.right > bounds.right + 1 ||
						rect.top < bounds.top - 1 ||
						rect.left < bounds.left - 1
					)
						failures.push({ index, kind: "bounds", height: rect.height, text: node.textContent });
					if (Object.hasOwn(joined, node.dataset.catalogStep)) joined[node.dataset.catalogStep] += node.textContent;
				}
			}
			return { joined, failures, count };
		});
		assert.deepEqual(result.failures, [], layout + " text steps must remain inside the visible panel");
		assert.equal(
			result.joined.Description,
			"Line\n".repeat(12) + "\n".repeat(10) + "界".repeat(70) + "\r\nTail\u2028End\u2029",
		);
		assert.equal(
			result.joined.Compatibility,
			"Compatible" +
				"Compatibility line\n".repeat(12) +
				"Meets declared Framey, API and platform requirements. This is not a runtime test.",
		);
		assert.equal(result.joined.Permissions, "Permission\r\n".repeat(12) + "\n".repeat(10));
		assert.deepEqual(errors, []);
		console.log(
			"ok   " +
				layout +
				" newline-rich descriptions, compatibility and permissions preserve exact text within " +
				result.count +
				" visible screens",
		);
	});
}
