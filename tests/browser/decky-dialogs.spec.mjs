import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const script =
	["decky_runtime/compat/framey-view.js", "decky_runtime/compat/decky-host.js"]
		.map(name => readFileSync(resolve(root, name), "utf8"))
		.join("\n") + "\nwindow.makeHost=createFrameyDeckyHost;window.makeView=createFrameyViewRuntime;";
async function prepare(page, source) {
	await page.route("**/*", route =>
		route.request().url() === "https://framey.test/"
			? route.fulfill({
					contentType: "text/html",
					body: '<!doctype html><div id="panel" style="width:600px;height:600px"></div>',
				})
			: route.abort(),
	);
	await page.goto("https://framey.test/");
	await page.addScriptTag({ content: script });
	await page.evaluate(async source => {
		window.calls = [];
		window.events = [];
		window.dismounts = 0;
		window.closedCount = 0;
		window.host = window.makeHost(
			{
				call: async (method, payload) => {
					window.calls.push({ method, ...payload });
					return true;
				},
				cleanup(fn) {
					window.cleanup = fn;
				},
				events: {
					on() {
						return () => {};
					},
				},
			},
			{ name: "Example", apiVersion: 2, nativeRoutes: true, createViewRuntime: window.makeView },
		);
		await window.host.load(source, "esm", document.getElementById("panel"));
	}, source);
	return page.frameLocator("iframe");
}
const prefix = `const owner=parent,R=SP_REACT,h=R.createElement;const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,'Example');`;
async function backdrop(page) {
	const frame = await page.locator("iframe").boundingBox();
	await page.mouse.click(frame.x + 2, frame.y + 2);
}

// User dismissal chains the original close callback; programmatic Close must skip callbacks and retain panel state.
test("confirmation dismissal callbacks and programmatic Close have distinct lifecycles", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function open(){owner.modal=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Review action',strDescription:'Synthetic confirmation only',strOKButtonText:'Confirm action',closeModal:()=>owner.events.push('close'),onCancel:()=>owner.events.push('cancel'),onOK:()=>api.call('confirm_action')}));owner.modal.ClosedPromise.then(()=>owner.closedCount++);}export default()=>({content:h(function Panel(){const[value,setValue]=R.useState('');return h('section',null,h('label',null,'Panel value',h('input',{value,onChange:e=>setValue(e.target.value)})),h('button',{onClick:open},'Open confirmation'));}),onDismount(){owner.dismounts++;}});`,
	);
	await realm.getByRole("textbox", { name: "Panel value" }).fill("Preserve me");
	await page.evaluate(
		() => (window.retainedInput = document.querySelector("iframe").contentDocument.querySelector("input")),
	);
	await realm.getByRole("button", { name: "Open confirmation" }).click();
	const dialog = realm.getByRole("dialog", { name: "Review action" });
	await expect(dialog).toContainText("Synthetic confirmation only");
	expect(await page.evaluate(() => window.calls)).toEqual([]);
	await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect(dialog).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["cancel", "close"]);
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(1);
	await expect(realm.getByRole("textbox", { name: "Panel value" })).toHaveValue("Preserve me");
	expect(
		await page.evaluate(
			() => window.retainedInput === document.querySelector("iframe").contentDocument.querySelector("input"),
		),
	).toBe(true);
	await expect(realm.getByRole("button", { name: "Open confirmation" })).toBeFocused();
	await realm.getByRole("button", { name: "Open confirmation" }).click();
	await page.evaluate(() => {
		window.modal.Close();
		window.modal.Close();
	});
	expect(await page.evaluate(() => window.events)).toEqual(["cancel", "close"]);
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(2);
	await realm.getByRole("button", { name: "Open confirmation" }).click();
	await dialog.getByRole("button", { name: "Confirm action" }).click();
	await expect(dialog).toHaveCount(0);
	expect(await page.evaluate(() => window.calls)).toEqual([{ method: "confirm_action", args: [], legacy: false }]);
	expect(await page.evaluate(() => window.events)).toEqual(["cancel", "close", "close"]);
	expect((await page.evaluate(() => window.host.status())).dialogCount).toBe(0);
});

// Disabled actions and Escape must never cause a confirmation; middle and alert modes are explicit native controls.
test("confirmation disabled middle and alert modes never invent OK actions", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function Locked(props){const[locked,setLocked]=R.useState(true);return h(DFL.ConfirmModal,{...props,strTitle:'Locked actions',strOKButtonText:'Accept',strMiddleButtonText:'Alternative',bOKDisabled:locked,bCancelDisabled:locked,bMiddleDisabled:locked,onOK:()=>owner.events.push('ok'),onCancel:()=>owner.events.push('cancel'),onMiddleButton:()=>owner.events.push('middle')},h('button',{onClick:()=>setLocked(false)},'Enable actions'));}export default()=>({content:h('section',null,h('button',{onClick:()=>DFL.showModal(h(Locked))},'Open locked'),h('button',{onClick:()=>DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Alert mode',bAlertDialog:true,onOK:()=>owner.events.push('ok'),onCancel:()=>owner.events.push('alert-cancel'),onMiddleButton:()=>owner.events.push('middle')}))},'Open alert'))});`,
	);
	await realm.getByRole("button", { name: "Open locked" }).click();
	const locked = realm.getByRole("dialog", { name: "Locked actions" });
	for (const name of ["Accept", "Cancel", "Alternative", "Close"])
		await expect(locked.getByRole("button", { name, exact: true })).toBeDisabled();
	await page.keyboard.press("Escape");
	await expect(locked).toBeVisible();
	expect(await page.evaluate(() => window.events)).toEqual([]);
	await locked.getByRole("button", { name: "Enable actions" }).click();
	await locked.getByRole("button", { name: "Alternative" }).click();
	await expect(locked).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["middle"]);
	await realm.getByRole("button", { name: "Open alert" }).click();
	const alert = realm.getByRole("alertdialog", { name: "Alert mode" });
	await expect(alert.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
	await expect(alert.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
	await expect(alert.getByRole("button", { name: "Alternative" })).toHaveCount(0);
	await page.keyboard.press("Escape");
	await expect(alert).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["middle", "alert-cancel"]);
});

// Update drops the old element and never connects its replacement to the original automatic close chain.
test("Update replaces elements without reconnecting stale or new close callbacks", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function Initial(props){owner.oldClose=props.closeModal;R.useEffect(()=>()=>owner.events.push('initial-cleanup'),[]);return h(DFL.ModalRoot,{...props},h(DFL.DialogHeader,null,'Initial modal'),h(DFL.DialogBody,null,'Old body'));}function Replacement(props){R.useEffect(()=>()=>owner.events.push('replacement-cleanup'),[]);return h(DFL.ModalRoot,{...props},h(DFL.DialogHeader,null,'Updated modal'),h(DFL.DialogButtonSecondary,{onClick:props.closeModal},'Replacement close'));}owner.replace=()=>owner.modal.Update(h(Replacement,{closeModal:()=>owner.events.push('new-close')}));owner.replaceConfirm=()=>owner.modal.Update(h(DFL.ConfirmModal,{strTitle:'Updated confirm',onCancel:()=>owner.events.push('new-cancel')}));export default()=>({content:h('button',{onClick:()=>{owner.modal=DFL.showModal(h(Initial,{closeModal:()=>owner.events.push('original-close')}));owner.modal.ClosedPromise.then(()=>owner.closedCount++);}},'Open update')});`,
	);
	await realm.getByRole("button", { name: "Open update" }).click();
	await page.evaluate(() => {
		window.replace();
		window.oldClose();
	});
	const updated = realm.getByRole("dialog", { name: "Updated modal" });
	await expect(updated).toContainText("Replacement close");
	await updated.getByRole("button", { name: "Replacement close" }).click();
	await expect(updated).toBeVisible();
	expect(await page.evaluate(() => window.events)).toEqual(["initial-cleanup", "new-close"]);
	expect(await page.evaluate(() => window.closedCount)).toBe(0);
	await page.evaluate(() => window.replaceConfirm());
	const confirm = realm.getByRole("dialog", { name: "Updated confirm" });
	await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect(confirm).toBeVisible();
	expect(await page.evaluate(() => window.events)).toEqual([
		"initial-cleanup",
		"new-close",
		"replacement-cleanup",
		"new-cancel",
	]);
	await page.evaluate(() => window.modal.Close());
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).dialogCount).toBe(0);
});

// Background dismissal and Escape are cancel paths, while disabled background dismissal leaves the view intact.
test("Escape and backdrop use cancel policies without confirming", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function open(locked){DFL.showModal(h(DFL.ConfirmModal,{strTitle:locked?'Locked backdrop':'Open backdrop',bDisableBackgroundDismiss:locked,onEscKeypress:locked?()=>owner.events.push('escape'):undefined,onCancel:()=>owner.events.push('cancel'),onOK:()=>owner.events.push('ok'),closeModal:()=>owner.events.push('close')}));}export default()=>({content:h('section',null,h('button',{onClick:()=>open(true)},'Open locked backdrop'),h('button',{onClick:()=>open(false)},'Open dismissible backdrop'))});`,
	);
	await realm.getByRole("button", { name: "Open locked backdrop" }).click();
	const locked = realm.getByRole("dialog", { name: "Locked backdrop" });
	await backdrop(page);
	await expect(locked).toBeVisible();
	expect(await page.evaluate(() => window.events)).toEqual([]);
	await page.keyboard.press("Escape");
	await expect(locked).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["escape", "close"]);
	await realm.getByRole("button", { name: "Open dismissible backdrop" }).click();
	await backdrop(page);
	await expect(realm.getByRole("dialog", { name: "Open backdrop" })).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual(["escape", "close", "cancel", "close"]);
});

// Nested plugin dialogs restore their actual opener, and closing them preserves the independent native route view.
test("nested dialogs coexist with native routes and restore focus and route state", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function Route(){const[value,setValue]=R.useState('');return h('section',null,h('label',null,'Route draft',h('input',{value,onChange:e=>setValue(e.target.value)})),h('button',{onClick:()=>DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Outer confirm'},h('button',{onClick:()=>{owner.inner=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Inner confirm'}));owner.inner.ClosedPromise.then(()=>owner.closedCount++);}},'Open nested')))},'Open outer'));}api.routerHook.addRoute('/example/dialogs',Route);export default()=>({content:h('button',{onClick:()=>DFL.Navigation.Navigate('/example/dialogs')},'Open route')});`,
	);
	await realm.getByRole("button", { name: "Open route" }).click();
	await realm.getByRole("textbox", { name: "Route draft" }).fill("Route retained");
	await realm.getByRole("button", { name: "Open outer" }).click();
	const outer = realm.getByRole("dialog", { name: "Outer confirm" });
	await outer.getByRole("button", { name: "Open nested" }).click();
	await expect(realm.getByRole("dialog", { name: "Inner confirm" })).toBeVisible();
	await page.evaluate(() => window.inner.Close());
	await expect(outer.getByRole("button", { name: "Open nested" })).toBeFocused();
	await outer.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect(realm.getByRole("textbox", { name: "Route draft" })).toHaveValue("Route retained");
	await expect(realm.getByRole("button", { name: "Open outer" })).toBeFocused();
	expect((await page.evaluate(() => window.host.status())).nativeRoute).toBe("/example/dialogs");
	expect((await page.evaluate(() => window.host.status())).dialogCount).toBe(0);
});

// Fatal dialog renders must settle handles and release both dialog and panel resources through host teardown.
test("fatal dialog rerenders close pending handles and release effects timers and the panel", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`owner.ticks=0;owner.effects=0;function Broken(props){const[broken,setBroken]=R.useState(false);R.useEffect(()=>{const id=setInterval(()=>owner.ticks++,5);return()=>{clearInterval(id);owner.effects++;};},[]);if(broken)throw new Error('Dialog render failed');return h(DFL.ModalRoot,{...props},h(DFL.DialogHeader,null,'Fatal modal'),h(DFL.DialogButton,{onClick:()=>setBroken(true)},'Break dialog'));}export default()=>({content:h('button',{onClick:()=>{owner.modal=DFL.showModal(h(Broken));owner.modal.ClosedPromise.then(()=>owner.closedCount++);}},'Open fatal'),onDismount(){owner.dismounts++;}});`,
	);
	await realm.getByRole("button", { name: "Open fatal" }).click();
	await expect.poll(() => page.evaluate(() => window.ticks)).toBeGreaterThan(0);
	await realm.getByRole("dialog", { name: "Fatal modal" }).getByRole("button", { name: "Break dialog" }).click();
	await expect(page.locator("#panel")).toBeEmpty();
	const ticks = await page.evaluate(() => window.ticks);
	await page.waitForTimeout(30);
	expect(await page.evaluate(() => window.ticks)).toBe(ticks);
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(1);
	expect(
		await page.evaluate(() => ({ effects: window.effects, dismounts: window.dismounts, status: window.host.status() })),
	).toMatchObject({
		effects: 1,
		dismounts: 1,
		status: { panel: "failed", dialogCount: 0, errors: ["Dialog render failed"] },
	});
});

// Container contracts must retain children, styles and native refs rather than masking an unsupported Steam component.
test("dialog containers preserve native refs styles and disabled secondary buttons", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function Content(props){const ref=R.useRef(null);R.useEffect(()=>{owner.bodyIsNative=ref.current instanceof HTMLElement;},[]);return h(DFL.ModalRoot,{...props,bAllowFullSize:true,bDestructiveWarning:true},h(DFL.DialogHeader,null,'Container dialog'),h(DFL.DialogSubHeader,null,'Subheading'),h(DFL.DialogBody,{ref,style:{color:'rgb(12, 34, 56)'},className:'custom-body'},h(DFL.DialogLabel,null,'A label'),h(DFL.DialogBodyText,null,'Body copy'),h(DFL.DialogControlsSection,null,h(DFL.DialogControlsSectionHeader,null,'Controls'))),h(DFL.DialogFooter,null,h(DFL.DialogButtonSecondary,{disabled:true,onClick:()=>owner.events.push('disabled')},'Disabled secondary'),h(DFL.DialogButtonSecondary,{onClick:props.closeModal},'Done')));}export default()=>({content:h('button',{onClick:()=>DFL.showModal(h(Content))},'Open containers')});`,
	);
	await realm.getByRole("button", { name: "Open containers" }).click();
	const dialog = realm.getByRole("dialog", { name: "Container dialog" });
	await expect(dialog.getByRole("heading", { name: "Subheading" })).toBeVisible();
	await expect(dialog.locator(".custom-body")).toHaveCSS("color", "rgb(12, 34, 56)");
	expect(await page.evaluate(() => window.bodyIsNative)).toBe(true);
	await expect(dialog.getByRole("button", { name: "Disabled secondary" })).toBeDisabled();
	await dialog.getByRole("button", { name: "Done", exact: true }).click();
	await expect(dialog).toHaveCount(0);
	expect(await page.evaluate(() => window.events)).toEqual([]);
});

// Unsupported host contexts fail before opening anything, and repeated modal creation has a fixed ownership bound.
test("dialog contexts fail explicitly and disposal closes bounded nested handles without callbacks", async ({
	page,
}) => {
	const realm = await prepare(
		page,
		prefix +
			`owner.failures=[];function open(){for(const attempt of [()=>DFL.showModal(h(DFL.ConfirmModal),window.parent),()=>DFL.showModal(h(DFL.ConfirmModal),undefined,{browserContext:{}}),()=>DFL.showModal(h(DFL.ConfirmModal),undefined,{bForcePopOut:true}),()=>DFL.showModal(h(DFL.ConfirmModal),undefined,{fnOnClose:()=>{}})]){try{attempt();}catch(e){owner.failures.push(e.message);}}for(let i=0;i<9;i++){try{const result=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Bounded '+i,onCancel:()=>owner.events.push('cancel'),closeModal:()=>owner.events.push('close')}));result.ClosedPromise.then(()=>owner.closedCount++);}catch(e){owner.failures.push(e.message);}}}export default()=>({content:h('button',{onClick:open},'Open bounded'),onDismount(){owner.dismounts++;}});`,
	);
	await realm.getByRole("button", { name: "Open bounded" }).click();
	expect((await page.evaluate(() => window.host.status())).dialogCount).toBe(8);
	expect(await page.evaluate(() => window.failures.length)).toBe(5);
	expect((await page.evaluate(() => window.host.status())).unsupported).toEqual(
		expect.arrayContaining([
			"dialogs.externalParent",
			"dialogs.browserContext",
			"dialogs.bForcePopOut",
			"dialogs.fnOnClose",
			"dialogs.limit",
		]),
	);
	await page.evaluate(() => {
		window.host.dispose();
		window.host.dispose();
	});
	await expect(page.locator("#panel")).toBeEmpty();
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(8);
	expect(await page.evaluate(() => window.events)).toEqual([]);
	expect(await page.evaluate(() => window.dismounts)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).dialogCount).toBe(0);
});

// Throwing effect cleanup cannot strand a dialog handle, and an initial render exception still retires the host.
test("throwing cleanup settles its handle while an initial modal render failure retires the host", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function Cleanup(props){R.useEffect(()=>()=>{owner.events.push('cleanup');throw new Error('Dialog cleanup threw');},[]);return h(DFL.ModalRoot,{...props},h(DFL.DialogHeader,null,'Cleanup dialog'));}function Broken(){throw new Error('Initial dialog render failed');}export default()=>({content:h('section',null,h('button',{onClick:()=>{owner.modal=DFL.showModal(h(Cleanup));owner.modal.ClosedPromise.then(()=>owner.closedCount++);}},'Open cleanup'),h('button',{onClick:()=>{try{DFL.showModal(h(Broken));}catch(error){owner.renderError=error.message;}}},'Open broken')),onDismount(){owner.dismounts++;}});`,
	);
	await realm.getByRole("button", { name: "Open cleanup" }).click();
	await page.evaluate(() => window.modal.Close());
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).panel).toBe("mounted");
	expect(await page.evaluate(() => window.events)).toEqual(["cleanup"]);
	await realm.getByRole("button", { name: "Open broken" }).click();
	await expect(page.locator("#panel")).toBeEmpty();
	expect(await page.evaluate(() => window.renderError)).toBe("Initial dialog render failed");
	expect(await page.evaluate(() => window.dismounts)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).errors).toEqual([
		"Dialog cleanup threw",
		"Initial dialog render failed",
	]);
});

// Auto-height plugin surfaces must respect the visible Framey content area, not the larger host application's viewport.
test("long help dialogs keep footer controls inside a clipped Framey surface and scroll only their body", async ({
	page,
}) => {
	const realm = await prepare(
		page,
		prefix +
			`const panel=owner.document.getElementById('panel');panel.style.cssText='width:380px;height:360px;overflow:auto';const tool=owner.document.createElement('div');tool.style.cssText='display:flex;flex-direction:column;min-height:0;gap:8px';const note=owner.document.createElement('div');note.style.cssText='height:40px;flex:none';note.textContent='Experimental compatibility';const surface=owner.document.createElement('div');surface.style.cssText='flex:1;min-height:0';owner.longSurface=surface;tool.append(note,surface);panel.append(tool);export default()=>({content:h('button',{onClick:()=>DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Long original help',strOKButtonText:'Close help'},...Array.from({length:70},(_,i)=>h('p',{key:i,style:{lineHeight:'22px'}},'Original instruction paragraph '+i))))},'Open long help')});`,
	);
	// Recreate the same host inside an unconstrained importer-style flex surface.
	await page.evaluate(async () => {
		const source = `const R=SP_REACT,h=R.createElement;export default()=>({content:h('button',{onClick:()=>DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Long original help',strOKButtonText:'Close help'},...Array.from({length:70},(_,i)=>h('p',{key:i,style:{lineHeight:'22px'}},'Original instruction paragraph '+i))))},'Open long help')});`;
		window.host.dispose();
		window.host = window.makeHost(
			{
				call: async () => true,
				cleanup() {},
				events: {
					on() {
						return () => {};
					},
				},
			},
			{ name: "Example", apiVersion: 2, nativeRoutes: true, createViewRuntime: window.makeView },
		);
		await window.host.load(source, "esm", window.longSurface);
	});
	await realm.getByRole("button", { name: "Open long help" }).click();
	const dialog = realm.getByRole("dialog", { name: "Long original help" });
	const geometry = await page.evaluate(() => {
		const clip = document.getElementById("panel").getBoundingClientRect(),
			frame = document.querySelector("iframe").getBoundingClientRect(),
			win = document.querySelector("iframe").contentWindow,
			dialog = win.document.querySelector("dialog"),
			footer = dialog.querySelector(".fy-decky-DialogFooter").getBoundingClientRect(),
			body = dialog.querySelector(".fy-decky-DialogBody");
		return {
			frameBottom: frame.bottom,
			clipBottom: clip.bottom,
			footerBottom: frame.top + footer.bottom,
			bodyOverflow: body.scrollHeight > body.clientHeight,
			dialogScrollTop: dialog.scrollTop,
		};
	});
	expect(geometry.frameBottom).toBeLessThanOrEqual(geometry.clipBottom + 1);
	expect(geometry.footerBottom).toBeLessThanOrEqual(geometry.clipBottom + 1);
	expect(geometry.bodyOverflow).toBe(true);
	expect(geometry.dialogScrollTop).toBe(0);
	await dialog.locator(".fy-decky-DialogBody").evaluate(body => (body.scrollTop = body.scrollHeight));
	await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect(dialog).toHaveCount(0);
	await expect(realm.getByRole("button", { name: "Open long help" })).toBeFocused();
});

// Opening a follow-up dialog from OK must not cause the first dialog's normal dismissal to destroy the new handle.
test("a follow-up dialog remains usable when its opener dialog completes", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`function open(){owner.outer=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'First step',onOK:()=>{owner.inner=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Follow-up',onCancel:()=>owner.events.push('follow-up-cancel')}));owner.inner.ClosedPromise.then(()=>owner.closedCount++);}}));owner.outer.ClosedPromise.then(()=>owner.closedCount++);}export default()=>({content:h('button',{onClick:open},'Open steps')});`,
	);
	await realm.getByRole("button", { name: "Open steps" }).click();
	await realm.getByRole("dialog", { name: "First step" }).getByRole("button", { name: "OK", exact: true }).click();
	const child = realm.getByRole("dialog", { name: "Follow-up" });
	await expect(child).toBeVisible();
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(1);
	expect((await page.evaluate(() => window.host.status())).dialogCount).toBe(1);
	await child.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(2);
	await expect(realm.getByRole("button", { name: "Open steps" })).toBeFocused();
	expect(await page.evaluate(() => window.events)).toEqual(["follow-up-cancel"]);
});

// Old async actions belong to the old Update generation and cannot disable, release or report errors into its replacement.
for (const outcome of ["resolve", "reject"])
	test(
		"Update invalidates pending actions and ignores old " + outcome + " after a replacement action starts",
		async ({ page }) => {
			const realm = await prepare(
				page,
				prefix +
					`owner.oldGate=new Promise((resolve,reject)=>{owner.resolveOld=resolve;owner.rejectOld=reject;});owner.newGate=new Promise(resolve=>owner.resolveNew=resolve);owner.replace=()=>owner.modal.Update(h(DFL.ConfirmModal,{strTitle:'Replacement action',strOKButtonText:'Run replacement',onOK:()=>{owner.events.push('new-ok');return owner.newGate;},closeModal:()=>owner.modal.Close()}));export default()=>({content:h('button',{onClick:()=>{owner.modal=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Old action',onOK:()=>owner.oldGate,closeModal:()=>owner.events.push('old-close')}));owner.modal.ClosedPromise.then(()=>owner.closedCount++);}},'Open pending')});`,
			);
			await realm.getByRole("button", { name: "Open pending" }).click();
			await realm.getByRole("dialog", { name: "Old action" }).getByRole("button", { name: "OK", exact: true }).click();
			await page.evaluate(() => window.replace());
			const replacement = realm.getByRole("dialog", { name: "Replacement action" }),
				run = replacement.getByRole("button", { name: "Run replacement" });
			await expect(run).toBeEnabled();
			await expect(replacement.getByRole("button", { name: "Cancel", exact: true })).toBeEnabled();
			await expect(replacement.getByRole("button", { name: "Close", exact: true })).toBeEnabled();
			await run.click();
			await expect(run).toBeDisabled();
			await page.evaluate(outcome => {
				if (outcome === "resolve") window.resolveOld();
				else window.rejectOld(new Error("Stale action failure"));
			}, outcome);
			await page.waitForTimeout(20);
			await expect(run).toBeDisabled();
			expect((await page.evaluate(() => window.host.status())).errors).toEqual([]);
			expect(await page.evaluate(() => window.closedCount)).toBe(0);
			await page.evaluate(() => window.resolveNew());
			await expect(replacement).toHaveCount(0);
			await expect.poll(() => page.evaluate(() => window.closedCount)).toBe(1);
			expect(await page.evaluate(() => window.events)).toEqual(["new-ok"]);
		},
	);

// An initial asynchronous close callback is also detached by Update, including any late rejection diagnostic.
test("Update detaches an old pending close callback and its rejection", async ({ page }) => {
	const realm = await prepare(
		page,
		prefix +
			`owner.oldCloseGate=new Promise((resolve,reject)=>owner.rejectOldClose=reject);owner.replace=()=>owner.modal.Update(h(DFL.ConfirmModal,{strTitle:'Fresh close generation',closeModal:()=>owner.modal.Close()}));export default()=>({content:h('button',{onClick:()=>owner.modal=DFL.showModal(h(DFL.ConfirmModal,{strTitle:'Old pending close',closeModal:()=>owner.oldCloseGate}))},'Open pending close')});`,
	);
	await realm.getByRole("button", { name: "Open pending close" }).click();
	await realm
		.getByRole("dialog", { name: "Old pending close" })
		.getByRole("button", { name: "Cancel", exact: true })
		.click();
	await page.evaluate(() => {
		window.replace();
		window.rejectOldClose(new Error("Detached close failure"));
	});
	await page.waitForTimeout(20);
	const current = realm.getByRole("dialog", { name: "Fresh close generation" });
	await expect(current.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
	expect((await page.evaluate(() => window.host.status())).errors).toEqual([]);
	await current.getByRole("button", { name: "Cancel", exact: true }).click();
	await expect(current).toHaveCount(0);
});
