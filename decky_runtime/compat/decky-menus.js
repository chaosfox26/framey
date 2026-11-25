/* Framey-owned context menus for the explicit Menu/MenuItem compatibility profile.
 * This helper never discovers or closes Steam UI. Native menu ownership is local
 * to this factory. Unsupported component shapes and creation options fail openly.
 */
const createFrameyDeckyMenus = function (options) {
	"use strict";
	const { document, React: R, ReactDOM, onUnsupported, onError, onChange } = options || {};
	if (!document?.body || typeof R?.createElement !== "function" || typeof ReactDOM?.createRoot !== "function")
		throw new TypeError("Framey menus require a document and the independent view runtime");
	const realm = document.defaultView,
		h = R.createElement,
		Context = R.createContext(null),
		unavailable = new Set();
	let current = null,
		generation = 0,
		disposed = false,
		style = null;
	const status = () => ({
		scope: "framey-transient-only",
		steamSideMenus: false,
		openCount: current?.open ? 1 : 0,
		disposed,
		unsupported: [...unavailable].sort(),
	});
	const report = error => {
		if (typeof onError === "function") onError(error);
		else throw error;
	};
	const publish = () => {
		if (typeof onChange === "function") onChange(status());
	};
	const unsupported = name => {
		const capability = "menus." + name;
		unavailable.add(capability);
		if (typeof onUnsupported === "function") onUnsupported(capability);
		throw new Error("Framey menus: unsupported " + capability);
	};
	const active = () => {
		if (disposed) throw new Error("Framey menus disposed");
	};
	const invoke = (fn, event) => {
		if (!fn) return;
		try {
			const result = fn(event);
			if (result && typeof result.then === "function") Promise.resolve(result).catch(report);
		} catch (error) {
			report(error);
		}
	};
	function validateProps(props, allowed, name) {
		for (const key of Object.keys(props)) if (!allowed.includes(key)) unsupported(name + "." + key);
	}
	function validate(element) {
		if (!R.isValidElement(element) || element.type !== Menu || element.ref != null) unsupported("element");
		const props = element.props;
		validateProps(props, ["label", "cancelText", "onCancel", "children"], "Menu");
		if (typeof props.label !== "string" || !props.label.trim()) unsupported("Menu.label");
		if (props.cancelText !== undefined && typeof props.cancelText !== "string") unsupported("Menu.cancelText");
		if (props.onCancel !== undefined && typeof props.onCancel !== "function") unsupported("Menu.onCancel");
		for (const item of R.Children.toArray(props.children)) {
			if (!R.isValidElement(item) || item.type !== MenuItem || item.ref != null) unsupported("Menu.children");
			validateProps(item.props, ["onSelected", "disabled", "children"], "MenuItem");
			if (item.props.onSelected !== undefined && typeof item.props.onSelected !== "function")
				unsupported("MenuItem.onSelected");
			if (item.props.disabled !== undefined && typeof item.props.disabled !== "boolean")
				unsupported("MenuItem.disabled");
		}
	}
	function anchorNode(parent) {
		if (parent == null || parent === realm) return null;
		if (
			parent.ownerDocument !== document ||
			parent.nodeType !== 1 ||
			!parent.isConnected ||
			typeof parent.getBoundingClientRect !== "function"
		)
			unsupported("anchor");
		return parent;
	}
	function close(record, restore = true) {
		if (!record?.open) return false;
		record.open = false;
		for (const off of record.listeners.splice(0)) off();
		let failure;
		try {
			record.root?.unmount();
		} catch (error) {
			failure = error;
		}
		record.root = null;
		record.container?.remove();
		record.container = null;
		if (record.anchorAttributes) {
			for (const [name, value] of record.anchorAttributes) {
				if (value === null) record.anchor.removeAttribute(name);
				else record.anchor.setAttribute(name, value);
			}
			record.anchorAttributes = null;
		}
		if (restore && record.returnFocus?.isConnected && typeof record.returnFocus.focus === "function")
			record.returnFocus.focus({ preventScroll: true });
		publish();
		if (failure) report(failure);
		return true;
	}
	function expire(record) {
		record.expired = true;
		close(record, false);
	}
	function usable(record) {
		if (!record?.open) return false;
		if (
			!record.container?.isConnected ||
			(record.anchor && !record.anchor.isConnected) ||
			(record.owner && (!record.owner.isConnected || !record.owner.open)) ||
			(record.returnFocus && !record.returnFocus.isConnected)
		) {
			expire(record);
			return false;
		}
		return true;
	}
	function cancel(record, event, restore = true) {
		if (usable(record) && close(record, restore)) invoke(record.element.props.onCancel, event);
	}
	function Menu(props) {
		const record = R.useContext(Context);
		if (!record) return unsupported("Menu.outsideContext");
		return h(
			"div",
			{ role: "menu", id: record.id, "aria-label": props.label, tabIndex: -1, className: "fy-decky-menu" },
			h("div", { className: "fy-decky-menu-label" }, props.label),
			props.children,
			h(
				"button",
				{
					type: "button",
					role: "menuitem",
					className: "fy-decky-menu-cancel",
					onClick: event => cancel(record, event),
				},
				props.cancelText ?? "Cancel",
			),
		);
	}
	function MenuItem(props) {
		const record = R.useContext(Context);
		if (!record) return unsupported("MenuItem.outsideContext");
		return h(
			"button",
			{
				type: "button",
				role: "menuitem",
				disabled: props.disabled,
				"aria-disabled": !!props.disabled,
				onClick: event => {
					if (!props.disabled && usable(record) && close(record)) invoke(props.onSelected, event);
				},
			},
			props.children,
		);
	}
	function installStyle() {
		if (style) return;
		style = document.createElement("style");
		style.setAttribute("data-framey-menu-style", "");
		style.textContent = `
[data-framey-menu-root]{position:fixed;z-index:2147483000;width:max-content;max-width:calc(100vw - 16px);color:#edf4fa;font:400 14px/1.4 system-ui,sans-serif;color-scheme:dark}
.fy-decky-menu{box-sizing:border-box;min-width:min(220px,calc(100vw - 16px));max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);overflow:auto;overflow-wrap:anywhere;padding:6px;background:#182330;border-radius:8px;box-shadow:0 8px 28px rgb(0 0 0 / .5);scrollbar-color:#738b9e #182330}
.fy-decky-menu-label{padding:8px 10px;color:#b8c9d7;font-weight:600}
.fy-decky-menu [role=menuitem]{box-sizing:border-box;display:block;width:100%;min-height:40px;padding:8px 10px;border:0;border-radius:4px;background:transparent;color:inherit;font:inherit;text-align:left;white-space:normal;overflow-wrap:anywhere;cursor:pointer}
.fy-decky-menu [role=menuitem]:hover:enabled{background:#2a3d4f}
.fy-decky-menu [role=menuitem]:focus-visible{outline:2px solid #89caff;outline-offset:-2px;background:#2a3d4f}
.fy-decky-menu [role=menuitem]:disabled{color:#90a5b7;cursor:default}
.fy-decky-menu .fy-decky-menu-cancel{margin-top:6px;color:#b8c9d7}
.fy-decky-menu ::selection{background:#376a91;color:#fff}
`;
		document.head.append(style);
	}
	function position(record) {
		if (!record.open) return;
		const width = realm.innerWidth,
			height = realm.innerHeight,
			margin = 8;
		const anchor = record.anchor?.getBoundingClientRect();
		const bounds = record.container.getBoundingClientRect();
		let left = anchor ? anchor.left : margin,
			top = anchor ? anchor.bottom + 4 : margin;
		if (anchor && top + bounds.height > height - margin) top = anchor.top - bounds.height - 4;
		left = Math.max(margin, Math.min(left, width - bounds.width - margin));
		top = Math.max(margin, Math.min(top, height - bounds.height - margin));
		record.container.style.left = left + "px";
		record.container.style.top = top + "px";
	}
	function show(record) {
		active();
		if (current !== record || record.generation !== generation || record.expired)
			throw new Error("Framey menus: stale handle generation");
		if (
			(record.owner && (!record.owner.isConnected || !record.owner.open)) ||
			(record.returnFocus && !record.returnFocus.isConnected)
		) {
			expire(record);
			throw new Error("Framey menus: stale menu owner");
		}
		if (record.open) {
			usable(record);
			return;
		}
		if (record.anchor) anchorNode(record.anchor);
		installStyle();
		const container = document.createElement("div");
		container.setAttribute("data-framey-menu-root", "");
		container.style.left = "8px";
		container.style.top = "8px";
		// A body child is inert under a modal top layer. Keep the menu in its
		// native dialog owner while retaining viewport-based fixed positioning.
		(record.owner || document.body).append(container);
		record.container = container;
		record.open = true;
		record.listeners = [];
		try {
			if (record.anchor) {
				record.anchorAttributes = ["aria-haspopup", "aria-expanded", "aria-controls"].map(name => [
					name,
					record.anchor.getAttribute(name),
				]);
				record.anchor.setAttribute("aria-haspopup", "menu");
				record.anchor.setAttribute("aria-expanded", "true");
				record.anchor.setAttribute("aria-controls", record.id);
			}
			record.root = ReactDOM.createRoot(container);
			record.root.render(h(Context.Provider, { value: record }, record.element));
			position(record);
			const listen = (target, name, callback, capture = false) => {
				target.addEventListener(name, callback, capture);
				record.listeners.push(() => target.removeEventListener(name, callback, capture));
			};
			const enabled = () => [...container.querySelectorAll('[role="menuitem"]')].filter(item => !item.disabled);
			listen(
				document,
				"pointerdown",
				event => {
					if (!container.contains(event.target)) cancel(record, event, false);
				},
				true,
			);
			listen(
				document,
				"focusin",
				event => {
					if (!container.contains(event.target)) cancel(record, event, false);
				},
				true,
			);
			listen(
				document,
				"keydown",
				event => {
					if (!usable(record)) return;
					if (event.key === "Escape") {
						event.preventDefault();
						event.stopPropagation();
						cancel(record, event);
					} else if (event.key === "Tab") cancel(record, event);
					else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
						event.preventDefault();
						event.stopPropagation();
						const items = enabled(),
							index = items.indexOf(document.activeElement);
						const next =
							event.key === "Home"
								? 0
								: event.key === "End"
									? items.length - 1
									: (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
						items[next]?.focus();
					}
				},
				true,
			);
			if (record.owner) listen(record.owner, "close", () => expire(record));
			const observer = new realm.MutationObserver(() => {
				usable(record);
			});
			observer.observe(document.documentElement, {
				childList: true,
				subtree: true,
				attributes: true,
				attributeFilter: ["open"],
			});
			record.listeners.push(() => observer.disconnect());
			listen(realm, "resize", () => position(record));
			listen(realm, "scroll", () => position(record), true);
			(enabled()[0] || container.querySelector('[role="menu"]')).focus({ preventScroll: true });
			publish();
		} catch (error) {
			try {
				close(record, false);
			} catch {
				/* Preserve initial rendering errors for the host load path. */
			}
			throw error;
		}
	}
	function showContextMenu(element, parent, creationOptions = {}) {
		active();
		validate(element);
		if (!creationOptions || typeof creationOptions !== "object" || Array.isArray(creationOptions))
			unsupported("options");
		for (const key of Object.keys(creationOptions)) unsupported("options." + key);
		const anchor = anchorNode(parent),
			returnFocus = anchor || document.activeElement,
			owner = returnFocus?.closest?.("dialog") || null;
		if (owner && !owner.open) unsupported("anchor.closedDialog");
		close(current, false);
		const record = {
			element,
			anchor,
			returnFocus,
			owner,
			generation: ++generation,
			id: "framey-menu-" + Math.random().toString(36).slice(2),
			open: false,
			listeners: [],
		};
		current = record;
		show(record);
		return {
			Hide() {
				active();
				if (current !== record || record.generation !== generation)
					throw new Error("Framey menus: stale handle generation");
				close(record);
			},
			Show() {
				show(record);
			},
		};
	}
	return {
		Menu,
		MenuItem,
		showContextMenu,
		status,
		closeAll() {
			active();
			return close(current) ? 1 : 0;
		},
		dispose() {
			if (disposed) return;
			disposed = true;
			generation++;
			try {
				close(current, false);
			} finally {
				current = null;
				style?.remove();
				style = null;
				publish();
			}
		},
	};
};
if (typeof module !== "undefined" && module.exports) module.exports = { createFrameyDeckyMenus };
