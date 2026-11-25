import { test, expect } from "@playwright/test";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const root = resolve(import.meta.dirname, "../..");

test("the optional runtime mounts an imported fixture and opens its native route", async ({ page }) => {
	const temporary = mkdtempSync(join(tmpdir(), "framey-decky-wrapper-"));
	try {
		const source = `
const api = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2, 'Route Probe');
api.routerHook.addRoute('/route-probe/:value', () => SP_REACT.createElement('p', null, 'Route value: '+DFL.useParams().value));
export default () => ({content: SP_REACT.createElement('div', null,
 SP_REACT.createElement(DFL.Button, {onClick:()=>DFL.Navigation.Navigate('/route-probe/42')}, 'Open plugin view'),
 SP_REACT.createElement(DFL.Button, {onClick:()=>{try{DFL.findModuleChild(()=>false)}catch{}}}, 'Check unavailable feature'))});
`;
		const builder = spawnSync(
			"python3",
			[
				"-I",
				"-B",
				"-c",
				`import sys,json,io,zipfile,hashlib
sys.path.insert(0,sys.argv[1])
from decky_runtime.importer import import_plugin
from decky_runtime.runtime import frontend_bundle
source=sys.stdin.read();out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
 z.writestr('probe/plugin.json',json.dumps({'name':'Route Probe','api_version':2,'flags':[]}))
 z.writestr('probe/package.json',json.dumps({'version':'1.0.0','type':'module'}))
 z.writestr('probe/dist/index.js',source)
 z.writestr('probe/LICENSE','Independently authored test fixture')
raw=out.getvalue();import_plugin(raw,hashlib.sha256(raw).hexdigest(),sys.argv[2],'decky-route-probe')
print(frontend_bundle())`,
				root,
				join(temporary, "plugin"),
			],
			{ input: source, encoding: "utf8", timeout: 10000 },
		);
		expect(builder.status, builder.stderr).toBe(0);
		await page.setViewportSize({ width: 800, height: 720 });
		await page.route("**/*", route =>
			route.request().url() === "https://framey.test/"
				? route.fulfill({ contentType: "text/html", body: '<!doctype html><body style="margin:0"></body>' })
				: route.abort(),
		);
		await page.goto("https://framey.test/");
		await page.evaluate(provider => {
			window.FY_RUNTIME = { panel_layout: "window", panel_navigation: "launcher", trusted_input: "require" };
			window.fyCall = function (raw) {
				const request = JSON.parse(raw);
				if (!request.id) return;
				const response =
					request.method === "decky_runtime_frontend"
						? { abi: 1, generation: 1, version: "0.1.0", source: provider }
						: request.method === "state_get"
							? { order: [], hidden: [] }
							: {};
				queueMicrotask(() => Framey._reply(request.gen, request.id, response));
			}.bind(null);
		}, builder.stdout);
		const core = ["inject.js", "fy-optional.js", "fy-api.js", "fy-ui.js", "fy-pages.js"]
			.map(file => readFileSync(join(root, file), "utf8"))
			.join("\n");
		await page.addScriptTag({ content: `(function(__fyt){${core}\n})('wrapper-core-token');` });
		const original = readFileSync(join(temporary, "plugin/vendor/decky/dist/index.js"), "utf8");
		expect(original).toBe(source);
		const generated = readFileSync(join(temporary, "plugin/main.js"), "utf8");
		await page.addScriptTag({
			content: `(function(Framey){${generated}\n})(window.Framey._k('wrapper-core-token').bind('decky-route-probe','wrapper-plugin-token',{name:'Route Probe'}));`,
		});
		await page.evaluate(() => Framey.open("decky-route-probe"));
		const frame = page.frameLocator('iframe[title="Route Probe compatibility panel"]');
		await expect(frame.getByRole("button", { name: "Open plugin view" })).toBeVisible({ timeout: 3000 });
		await frame.getByRole("button", { name: "Open plugin view" }).click();
		const dialog = frame.getByRole("dialog", { name: "Route Probe custom route" });
		await expect(dialog).toBeVisible();
		await expect(dialog).toContainText("Route value: 42");
		await dialog.getByRole("button", { name: "Close", exact: true }).click();
		await expect(dialog).toHaveCount(0);
		await expect(frame.getByRole("button", { name: "Open plugin view" })).toBeVisible();
		await frame.getByRole("button", { name: "Check unavailable feature" }).click();
		await expect(page.locator(".fy-panel")).toContainText(
			"Some plugin features are unavailable. Steam page integration has not been verified",
		);
		await expect(page.locator(".fy-panel")).not.toContainText("modules.findModuleChild");
	} finally {
		rmSync(temporary, { recursive: true, force: true });
	}
});
