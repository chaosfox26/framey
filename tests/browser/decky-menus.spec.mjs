import { test, expect } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const helperPath = resolve(root, "decky_runtime/compat/decky-menus.js");
const runtime = readFileSync(resolve(root, "decky_runtime/compat/framey-view.js"), "utf8");
async function prepare(page) {
	expect(existsSync(helperPath), "Framey must provide its independent menu helper").toBe(true);
	await page.route("**/*", route => route.abort());
	await page.setContent(
		'<!doctype html><html><body style="margin:16px;background:#0e141b;color:#edf4fa;font-family:sans-serif"><button id="anchor" style="margin-top:30px">Choose synthetic player</button><button id="outside">Outside action</button><div id="panel"></div></body></html>',
	);
	await page.addScriptTag({
		content:
			runtime +
			"\n" +
			readFileSync(helperPath, "utf8") +
			"\nwindow.makeView=createFrameyViewRuntime;window.makeMenus=createFrameyDeckyMenus;",
	});
	await page.evaluate(() => {
		window.events = [];
		window.failures = [];
		window.missing = [];
		window.snapshots = [];
		window.view = window.makeView(document);
		const R = window.view.React,
			h = R.createElement;
		window.menus = window.makeMenus({
			document,
			React: R,
			ReactDOM: window.view.ReactDOM,
			onUnsupported: name => window.missing.push(name),
			onError: error => window.failures.push(error.message),
			onChange: status => window.snapshots.push(status),
		});
		window.openMenu = (extra = {}) => {
			const { Menu, MenuItem } = window.menus;
			window.handle = window.menus.showContextMenu(
				h(
					Menu,
					{
						label: "Synthetic media players",
						cancelText: "Cancel",
						onCancel: () => window.events.push("cancel"),
						...extra,
					},
					h(MenuItem, { onSelected: () => window.events.push("one") }, "Synthetic player one"),
					h(MenuItem, { disabled: true, onSelected: () => window.events.push("disabled") }, "Disabled player"),
					h(MenuItem, { onSelected: () => window.events.push("two") }, "Synthetic player two"),
				),
				document.getElementById("anchor"),
			);
		};
		document.getElementById("anchor").addEventListener("click", () => window.openMenu());
	});
}

test.afterEach(async ({ page }) => {
	await page
		.evaluate(() => {
			window.menus?.dispose();
			window.view?.dispose();
		})
		.catch(() => {});
});

test("selection closes the anchored menu and stale or disabled items never invoke callbacks", async ({ page }) => {
	await prepare(page);
	await page.getByRole("button", { name: "Choose synthetic player" }).click();
	const menu = page.getByRole("menu", { name: "Synthetic media players" });
	await expect(menu).toBeVisible();
	await page.screenshot({ path: test.info().outputPath("synthetic-menu-desktop.png") });
	await expect(menu.getByRole("menuitem", { name: "Synthetic player one", exact: true })).toBeFocused();
	await expect(menu.getByRole("menuitem", { name: "Disabled player" })).toBeDisabled();
	await menu
		.getByRole("menuitem", { name: "Disabled player" })
		.evaluate(node => node.dispatchEvent(new MouseEvent("click", { bubbles: true })));
	expect(await page.evaluate(() => window.events)).toEqual([]);
	await page.evaluate(() => (window.retained = document.querySelector('[role="menuitem"]')));
	await menu.getByRole("menuitem", { name: "Synthetic player one", exact: true }).click();
	await expect(menu).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Choose synthetic player" })).toBeFocused();
	await page.evaluate(() => {
		window.retained.click();
		window.handle.Hide();
	});
	expect(await page.evaluate(() => window.events)).toEqual(["one"]);
	expect(await page.evaluate(() => window.menus.status())).toMatchObject({
		scope: "framey-transient-only",
		steamSideMenus: false,
		openCount: 0,
	});
});

test("keyboard navigation skips disabled items and Escape and outside click cancel once", async ({ page }) => {
	await prepare(page);
	await page.getByRole("button", { name: "Choose synthetic player" }).click();
	const menu = page.getByRole("menu");
	await page.keyboard.press("ArrowDown");
	await expect(menu.getByRole("menuitem", { name: "Synthetic player two" })).toBeFocused();
	await page.keyboard.press("End");
	await expect(menu.getByRole("menuitem", { name: "Cancel", exact: true })).toBeFocused();
	await page.keyboard.press("Home");
	await expect(menu.getByRole("menuitem", { name: "Synthetic player one", exact: true })).toBeFocused();
	await page.keyboard.press("Escape");
	await expect(menu).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["cancel"]);
	await page.getByRole("button", { name: "Choose synthetic player" }).click();
	await page.getByRole("button", { name: "Outside action" }).click();
	await expect(menu).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Outside action" })).toBeFocused();
	expect(await page.evaluate(() => window.events)).toEqual(["cancel", "cancel"]);
	await page.getByRole("button", { name: "Choose synthetic player" }).click();
	await page.keyboard.press("Enter");
	expect(await page.evaluate(() => window.events)).toEqual(["cancel", "cancel", "one"]);
});

test("Hide Show closeAll and disposal own only Framey transient menus with generation checks", async ({ page }) => {
	await prepare(page);
	await page.evaluate(() => window.openMenu());
	await page.evaluate(() => window.handle.Hide());
	await expect(page.getByRole("menu")).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual([]);
	await page.evaluate(() => window.handle.Show());
	await expect(page.getByRole("menu")).toBeVisible();
	expect(await page.evaluate(() => window.menus.closeAll())).toBe(1);
	expect(await page.evaluate(() => window.menus.closeAll())).toBe(0);
	await page.evaluate(() => {
		window.oldHandle = window.handle;
		window.handle.Show();
		window.openMenu();
	});
	expect(
		await page.evaluate(() => {
			try {
				window.oldHandle.Show();
				return "accepted";
			} catch (error) {
				return error.message;
			}
		}),
	).toMatch(/stale|generation/i);
	await expect(page.getByRole("menu")).toHaveCount(1);
	await page.evaluate(() => {
		window.menus.dispose();
		window.menus.dispose();
	});
	await expect(page.getByRole("menu")).toHaveCount(0);
	expect(
		await page.evaluate(() => {
			try {
				window.handle.Show();
				return "accepted";
			} catch (error) {
				return error.message;
			}
		}),
	).toMatch(/disposed/i);
	expect(await page.evaluate(() => window.events)).toEqual([]);
	expect(await page.evaluate(() => window.menus.status())).toMatchObject({
		openCount: 0,
		disposed: true,
		steamSideMenus: false,
	});
	expect(await page.locator("[data-framey-menu-style]").count()).toBe(0);
});

test("anchored menus fit a narrow viewport and flip above a low anchor", async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 360 });
	await prepare(page);
	await page.evaluate(() => {
		Object.assign(document.getElementById("anchor").style, {
			position: "fixed",
			right: "5px",
			bottom: "8px",
			margin: "0",
		});
		window.openMenu();
	});
	const menu = page.getByRole("menu"),
		bounds = await menu.boundingBox(),
		anchor = await page.locator("#anchor").boundingBox();
	await page.screenshot({ path: test.info().outputPath("synthetic-menu-narrow.png") });
	expect(bounds.x).toBeGreaterThanOrEqual(7);
	expect(bounds.y).toBeGreaterThanOrEqual(7);
	expect(bounds.x + bounds.width).toBeLessThanOrEqual(313);
	expect(bounds.y + bounds.height).toBeLessThanOrEqual(anchor.y + 1);
	await expect(menu.getByRole("menuitem", { name: "Cancel", exact: true })).toBeVisible();
});

test("empty menus remain cancellable and lifecycle cleanup preserves the panel input identity", async ({ page }) => {
	await prepare(page);
	await page.evaluate(() => {
		const R = window.view.React,
			h = R.createElement,
			{ Menu, MenuItem } = window.menus;
		window.panelRoot = window.view.ReactDOM.createRoot(document.getElementById("panel"));
		function Panel() {
			const [text, set] = R.useState("");
			return h("input", { "aria-label": "Panel value", value: text, onChange: event => set(event.target.value) });
		}
		window.panelRoot.render(h(Panel));
		window.input = document.querySelector("input");
		function ItemContent() {
			R.useEffect(() => () => window.events.push("item cleanup"), []);
			return h("span", null, "Lifecycle item");
		}
		window.menus.showContextMenu(
			h(
				Menu,
				{ label: "Lifecycle menu" },
				h(MenuItem, { onSelected: () => window.events.push("selected") }, h(ItemContent)),
			),
			document.getElementById("anchor"),
		);
	});
	await page.getByRole("menuitem", { name: "Lifecycle item" }).click();
	expect(await page.evaluate(() => window.events)).toEqual(["item cleanup", "selected"]);
	await page.getByRole("textbox", { name: "Panel value" }).fill("Keep this value");
	await page.evaluate(() => {
		const R = window.view.React;
		window.menus.showContextMenu(
			R.createElement(window.menus.Menu, {
				label: "Empty providers",
				cancelText: "Cancel",
				onCancel: () => window.events.push("empty cancel"),
			}),
			window,
		);
	});
	await page.getByRole("menuitem", { name: "Cancel", exact: true }).click();
	expect(await page.evaluate(() => document.querySelector("input") === window.input)).toBe(true);
	await expect(page.getByRole("textbox", { name: "Panel value" })).toHaveValue("Keep this value");
	expect(await page.evaluate(() => window.events)).toEqual(["item cleanup", "selected", "empty cancel"]);
});

test("unsupported menu shapes options and foreign anchors fail explicitly without live menus", async ({ page }) => {
	await prepare(page);
	const result = await page.evaluate(() => {
		const h = window.view.React.createElement,
			{ Menu, MenuItem } = window.menus,
			anchor = document.getElementById("anchor");
		const cases = [
			() => window.menus.showContextMenu(h("div", null, "Unexpected wrapper"), anchor),
			() => window.menus.showContextMenu(h(Menu, { label: "Unknown options" }), anchor, { bForcePopup: true }),
			() => window.menus.showContextMenu(h(Menu, { label: "Unknown item" }, h("span", null, "Item")), anchor),
			() =>
				window.menus.showContextMenu(
					h(Menu, { label: "Unsupported item props" }, h(MenuItem, { onMoveRight() {} }, "Submenu")),
					anchor,
				),
			() =>
				window.menus.showContextMenu(
					h(Menu, { label: "Foreign anchor" }),
					document.implementation.createHTMLDocument().body,
				),
		];
		return cases.map(run => {
			try {
				run();
				return "accepted";
			} catch (error) {
				return error.message;
			}
		});
	});
	expect(result).toHaveLength(5);
	for (const error of result) expect(error).toMatch(/unsupported/i);
	await expect(page.getByRole("menu")).toHaveCount(0);
	expect(await page.evaluate(() => window.missing.length)).toBe(5);
});

test("selection and cancel callback failures are reported after closure without repeated invocation", async ({
	page,
}) => {
	await prepare(page);
	await page.evaluate(() => {
		const h = window.view.React.createElement,
			{ Menu, MenuItem } = window.menus;
		window.menus.showContextMenu(
			h(
				Menu,
				{ label: "Rejected selection" },
				h(
					MenuItem,
					{
						onSelected: async () => {
							window.events.push("selection attempted");
							throw new Error("Synthetic selection failure");
						},
					},
					"Reject selection",
				),
			),
			document.getElementById("anchor"),
		);
	});
	await page.getByRole("menuitem", { name: "Reject selection" }).click();
	await expect(page.getByRole("menu")).toHaveCount(0);
	await expect.poll(() => page.evaluate(() => window.failures)).toEqual(["Synthetic selection failure"]);
	await page.evaluate(() =>
		window.openMenu({
			onCancel: () => {
				window.events.push("cancel attempted");
				throw new Error("Synthetic cancel failure");
			},
		}),
	);
	await page.keyboard.press("Escape");
	expect(await page.evaluate(() => window.failures)).toEqual([
		"Synthetic selection failure",
		"Synthetic cancel failure",
	]);
	expect(await page.evaluate(() => window.events)).toEqual(["selection attempted", "cancel attempted"]);
});

test("trigger accessibility is linked while open and restored on close", async ({ page }) => {
	await prepare(page);
	await page.locator("#anchor").evaluate(node => {
		node.setAttribute("aria-haspopup", "listbox");
		node.setAttribute("aria-expanded", "false");
		node.setAttribute("aria-controls", "prior-control");
	});
	await page.locator("#anchor").click();
	await expect(page.locator("#anchor")).toHaveAttribute("aria-haspopup", "menu");
	await expect(page.locator("#anchor")).toHaveAttribute("aria-expanded", "true");
	const menuId = await page.getByRole("menu").getAttribute("id");
	expect(menuId).toBeTruthy();
	await expect(page.locator("#anchor")).toHaveAttribute("aria-controls", menuId);
	await page.getByRole("menuitem", { name: "Cancel", exact: true }).click();
	await expect(page.locator("#anchor")).toHaveAttribute("aria-haspopup", "listbox");
	await expect(page.locator("#anchor")).toHaveAttribute("aria-expanded", "false");
	await expect(page.locator("#anchor")).toHaveAttribute("aria-controls", "prior-control");
});

test("Tab cancellation invokes onCancel once", async ({ page }) => {
	await prepare(page);
	await page.evaluate(() => window.openMenu());
	await page.keyboard.press("Tab");
	await expect(page.getByRole("menu")).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["cancel"]);
});

test("both operations on an outdated menu handle reject explicitly", async ({ page }) => {
	await prepare(page);
	await page.evaluate(() => window.openMenu());
	const results = await page.evaluate(() => {
		const old = window.handle;
		window.openMenu();
		return ["Hide", "Show"].map(name => {
			try {
				old[name]();
				return "accepted";
			} catch (error) {
				return error.message;
			}
		});
	});
	for (const result of results) expect(result).toMatch(/stale|generation/i);
	await expect(page.getByRole("menu")).toHaveCount(1);
});

async function modalOwner(page, fullSize = false) {
	await page.evaluate(fullSize => {
		const owner = document.createElement("dialog");
		owner.id = "menu-owner";
		owner.setAttribute("aria-label", "Synthetic owning modal");
		owner.style.cssText = fullSize
			? "box-sizing:border-box;position:fixed;inset:0;margin:0;width:100%;height:100%;max-width:none;max-height:none;border:0;padding:12px;background:#0e141b;color:#e6edf4"
			: "box-sizing:border-box;width:520px;max-width:calc(100% - 24px);height:400px;padding:20px;background:#0e141b;color:#e6edf4";
		owner.append(document.getElementById("anchor"));
		document.body.append(owner);
		owner.showModal();
	}, fullSize);
}

for (const fullSize of [false, true]) {
	test(`modal-owned menus receive actual pointer and keyboard input in ${fullSize ? "route-style" : "ordinary"} native dialogs`, async ({
		page,
	}) => {
		await prepare(page);
		await modalOwner(page, fullSize);
		await page.getByRole("button", { name: "Choose synthetic player" }).click();
		const item = page.getByRole("menuitem", { name: "Synthetic player one", exact: true });
		expect(
			await item.evaluate(node => {
				const r = node.getBoundingClientRect(),
					hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
				return node === hit || node.contains(hit);
			}),
		).toBe(true);
		await expect(item).toBeFocused();
		await page.keyboard.press("ArrowDown");
		await expect(page.getByRole("menuitem", { name: "Synthetic player two" })).toBeFocused();
		await page.keyboard.press("Enter");
		expect(await page.evaluate(() => window.events)).toEqual(["two"]);
		await page.getByRole("button", { name: "Choose synthetic player" }).click();
		await page.getByRole("menuitem", { name: "Synthetic player one", exact: true }).click({ timeout: 1800 });
		expect(await page.evaluate(() => window.events)).toEqual(["two", "one"]);
		await page.getByRole("button", { name: "Choose synthetic player" }).click();
		expect(await page.getByRole("menu").evaluate(node => node.closest("dialog")?.id)).toBe("menu-owner");
		await page.keyboard.press("Escape");
		await expect(page.getByRole("menu")).toHaveCount(0);
		await expect(page.getByRole("dialog", { name: "Synthetic owning modal" })).toBeVisible();
		await expect(page.getByRole("button", { name: "Choose synthetic player" })).toBeFocused();
		expect(await page.evaluate(() => window.events)).toEqual(["two", "one", "cancel"]);
	});
}

for (const teardown of ["close", "remove", "replaceChildren"]) {
	test(`owner teardown ${teardown} expires menus without cancellation or stale selections`, async ({ page }) => {
		await prepare(page);
		await modalOwner(page);
		await page.getByRole("button", { name: "Choose synthetic player" }).click();
		await page.evaluate(teardown => {
			const owner = document.getElementById("menu-owner");
			window.retained = document.querySelector('[role="menuitem"]');
			owner[teardown]();
			// Exercise the same turn before the observer/close-event cleanup can run.
			window.retained.click();
		}, teardown);
		await expect.poll(() => page.evaluate(() => window.menus.status().openCount)).toBe(0);
		await expect(page.locator("[data-framey-menu-root]")).toHaveCount(0);
		await page.evaluate(() => window.retained.click());
		expect(await page.evaluate(() => window.events)).toEqual([]);
		expect(
			await page.evaluate(() => {
				try {
					window.handle.Show();
					return "accepted";
				} catch (error) {
					return error.message;
				}
			}),
		).toMatch(/stale|owner|generation/i);
	});
}

test("window fallback keeps a menu inside the focused native modal owner", async ({ page }) => {
	await prepare(page);
	await modalOwner(page);
	await page.evaluate(() => {
		document.getElementById("anchor").focus();
		window.handle = window.menus.showContextMenu(
			window.view.React.createElement(window.menus.Menu, { label: "Modal window fallback" }),
			window,
		);
	});
	const menu = page.getByRole("menu", { name: "Modal window fallback" });
	expect(await menu.evaluate(node => node.closest("dialog")?.id)).toBe("menu-owner");
	await expect(menu.getByRole("menuitem", { name: "Cancel", exact: true })).toBeFocused();
	await menu.getByRole("menuitem", { name: "Cancel", exact: true }).click({ timeout: 1800 });
	await expect(menu).toHaveCount(0);
});

test("a hidden window-anchored handle cannot reopen after its focused owner content is replaced", async ({ page }) => {
	await prepare(page);
	await modalOwner(page);
	await page.evaluate(() => {
		const R = window.view.React;
		document.getElementById("anchor").focus();
		window.handle = window.menus.showContextMenu(
			R.createElement(window.menus.Menu, { label: "Owned fallback" }),
			window,
		);
		window.menus.closeAll();
		document.getElementById("menu-owner").replaceChildren(document.createElement("p"));
	});
	expect(
		await page.evaluate(() => {
			try {
				window.handle.Show();
				return "accepted";
			} catch (error) {
				return error.message;
			}
		}),
	).toMatch(/stale|owner|anchor/i);
	await expect(page.getByRole("menu")).toHaveCount(0);
});
