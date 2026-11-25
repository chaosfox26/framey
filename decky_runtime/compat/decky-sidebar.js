/* Independently authored local SidebarNavigation compatibility profile.
 * Contract inspected: SteamDeckHomebrew/decky-frontend-lib/src/components/SidebarNavigation.ts.
 * Page lifetime is a Framey choice: lazily mount on first selection, retain visited
 * content while hidden, unmount on page removal/visibility removal/root disposal.
 * There is no Steam router, route reporting, class discovery, or external link handling.
 */
const createFrameyDeckySidebar = function (options) {
	"use strict";
	const { React: R, onUnsupported, onError } = options || {};
	if (typeof R?.createElement !== "function" || typeof R?.useState !== "function")
		throw new TypeError("Independent Framey view runtime required");
	const h = R.createElement,
		unavailable = new Set();
	function unsupported(name) {
		const key = "SidebarNavigation." + name;
		unavailable.add(key);
		onUnsupported?.(key);
		throw new Error("Framey sidebar: unsupported " + key);
	}
	function allowed(props, keys, name) {
		for (const key of Object.keys(props)) if (!keys.includes(key)) unsupported(name + key);
	}
	function normalize(props) {
		allowed(props, ["title", "showTitle", "pages", "page", "onPageRequested"], "props.");
		if (!Array.isArray(props.pages)) unsupported("pages");
		if (props.showTitle !== undefined && typeof props.showTitle !== "boolean") unsupported("showTitle");
		if (props.page !== undefined && typeof props.page !== "string") unsupported("page");
		if (props.onPageRequested !== undefined && typeof props.onPageRequested !== "function")
			unsupported("onPageRequested");
		const ids = new Set();
		return props.pages
			.map((page, index) => {
				if (page === "separator") return { separator: true, id: "separator:" + index };
				if (!page || typeof page !== "object" || Array.isArray(page)) return unsupported("pages.item");
				allowed(page, ["title", "content", "icon", "visible", "hideTitle", "identifier", "padding"], "pages.");
				if (page.title == null || typeof page.title === "boolean") unsupported("pages.title");
				if (page.visible !== undefined && typeof page.visible !== "boolean") unsupported("pages.visible");
				if (page.hideTitle !== undefined && typeof page.hideTitle !== "boolean") unsupported("pages.hideTitle");
				if (page.identifier !== undefined && (typeof page.identifier !== "string" || !page.identifier))
					unsupported("pages.identifier");
				if (page.padding !== undefined && !["none", "compact"].includes(page.padding)) unsupported("pages.padding");
				const id = page.identifier ?? String(index);
				if (ids.has(id)) unsupported("pages.identifier");
				ids.add(id);
				return { ...page, id };
			})
			.filter(page => page.separator || page.visible !== false);
	}
	function report(error) {
		if (typeof onError === "function") onError(error);
		else throw error;
	}
	const css = `
.fy-decky-sidebar{display:flex;flex-direction:column;box-sizing:border-box;width:100%;height:100%;max-width:100%;min-width:0;min-height:0;overflow:hidden;color:#edf4fa;font:400 14px/1.45 system-ui,sans-serif;color-scheme:dark}
.fy-decky-sidebar-title{margin:0 0 16px;font-size:20px;line-height:1.25;overflow-wrap:anywhere}
.fy-decky-sidebar-body{display:grid;grid-template-columns:minmax(140px,28%) minmax(0,1fr);gap:16px;flex:1;min-height:0;min-width:0;overflow:hidden}
.fy-decky-sidebar-tabs{display:flex;flex-direction:column;gap:4px;overflow:auto;min-width:0;align-self:stretch;scrollbar-color:#738b9e #0e141b}
.fy-decky-sidebar-tab{display:flex;align-items:center;gap:8px;width:100%;min-width:0;min-height:44px;box-sizing:border-box;padding:10px 12px;text-align:left;font:inherit;color:#dce8f2;background:transparent;border:0;border-radius:6px;overflow-wrap:anywhere;cursor:pointer}
.fy-decky-sidebar-tab[aria-selected=true]{background:#253e52;color:#fff;font-weight:600}
.fy-decky-sidebar-tab:hover{background:#213243}
.fy-decky-sidebar-tab:focus-visible{outline:2px solid #89caff;outline-offset:-2px}
.fy-decky-sidebar-tab-label{min-width:0;overflow-wrap:anywhere}
.fy-decky-sidebar-panels{min-width:0;min-height:0;overflow:auto;scrollbar-color:#738b9e #0e141b}
.fy-decky-sidebar-panel{box-sizing:border-box;min-width:0;max-width:100%;padding:12px;overflow-wrap:anywhere}
.fy-decky-sidebar-panel[hidden]{display:none}
.fy-decky-sidebar-panel h2{font-size:17px;line-height:1.3;margin:0 0 14px;overflow-wrap:anywhere}
.fy-decky-sidebar-panel input{box-sizing:border-box;max-width:100%;font:inherit}
.fy-decky-sidebar-separator{height:1px;background:#42586b;flex-shrink:0;margin:8px 12px}
.fy-decky-sidebar-empty{margin:0;color:#b8c9d7}
.fy-decky-sidebar ::selection{background:#376a91;color:#fff}
@media(max-width:520px){.fy-decky-sidebar-body{display:flex;flex-direction:column;gap:12px}.fy-decky-sidebar-tabs{flex-shrink:0;max-height:180px}.fy-decky-sidebar-panels{flex:1}.fy-decky-sidebar-title{font-size:18px;margin-bottom:12px}}
`;
	function SidebarNavigation(props) {
		const entries = normalize(props),
			pages = entries.filter(page => !page.separator);
		const [localPage, setLocalPage] = R.useState(() => pages[0]?.id);
		const visited = R.useRef(new Set()),
			refs = R.useRef(new Map()),
			prefix = R.useId();
		const selected =
			props.page !== undefined ? props.page : pages.some(page => page.id === localPage) ? localPage : pages[0]?.id;
		if (props.page !== undefined && !pages.some(page => page.id === props.page)) unsupported("page.unavailable");
		const ids = new Set(pages.map(page => page.id));
		for (const id of visited.current) if (!ids.has(id)) visited.current.delete(id);
		if (selected !== undefined) visited.current.add(selected);
		const tabId = id => prefix + "-tab-" + encodeURIComponent(id),
			panelId = id => prefix + "-panel-" + encodeURIComponent(id);
		function select(id) {
			if (id === selected) return;
			if (props.page === undefined) setLocalPage(id);
			if (props.onPageRequested) {
				try {
					const result = props.onPageRequested(id);
					if (result?.then) Promise.resolve(result).catch(report);
				} catch (error) {
					report(error);
				}
			}
		}
		function keyboard(event, id) {
			if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
			event.preventDefault();
			const index = pages.findIndex(page => page.id === id);
			const next =
				event.key === "Home"
					? 0
					: event.key === "End"
						? pages.length - 1
						: (index + (event.key === "ArrowDown" ? 1 : -1) + pages.length) % pages.length;
			if (pages[next]) {
				select(pages[next].id);
				refs.current.get(pages[next].id)?.focus();
			}
		}
		return h(
			"section",
			{
				className: "fy-decky-sidebar",
				"aria-label": typeof props.title === "string" ? props.title : "Local pages",
			},
			h("style", null, css),
			props.showTitle && props.title != null ? h("h1", { className: "fy-decky-sidebar-title" }, props.title) : null,
			h(
				"div",
				{ className: "fy-decky-sidebar-body" },
				h(
					"div",
					{
						role: "tablist",
						"aria-orientation": "vertical",
						"aria-label": "Page navigation",
						className: "fy-decky-sidebar-tabs",
					},
					entries.map(page =>
						page.separator
							? h("div", {
									key: page.id,
									role: "separator",
									"aria-orientation": "horizontal",
									className: "fy-decky-sidebar-separator",
								})
							: h(
									"button",
									{
										type: "button",
										key: page.id,
										id: tabId(page.id),
										role: "tab",
										className: "fy-decky-sidebar-tab",
										"aria-selected": page.id === selected,
										"aria-controls": panelId(page.id),
										tabIndex: page.id === selected ? 0 : -1,
										ref: node => {
											if (node) refs.current.set(page.id, node);
											else refs.current.delete(page.id);
										},
										onClick: () => select(page.id),
										onKeyDown: event => keyboard(event, page.id),
									},
									page.icon == null ? null : h("span", { "aria-hidden": true }, page.icon),
									h("span", { className: "fy-decky-sidebar-tab-label" }, page.title),
								),
					),
				),
				h(
					"div",
					{ className: "fy-decky-sidebar-panels" },
					pages.length
						? pages.map(page =>
								h(
									"section",
									{
										key: page.id,
										id: panelId(page.id),
										role: "tabpanel",
										"aria-labelledby": tabId(page.id),
										className: "fy-decky-sidebar-panel",
										hidden: page.id !== selected,
										inert: page.id !== selected,
										"aria-hidden": page.id !== selected,
										style: page.padding === "none" ? { padding: 0 } : undefined,
									},
									!visited.current.has(page.id) || page.hideTitle ? null : h("h2", null, page.title),
									visited.current.has(page.id) ? page.content : null,
								),
							)
						: h("p", { className: "fy-decky-sidebar-empty", role: "status" }, "No pages available"),
				),
			),
		);
	}
	return {
		SidebarNavigation,
		status: () => ({
			scope: "framey-local-pages-only",
			routeReporting: false,
			pageLifetime: "lazy-retained-until-removal",
			unsupported: [...unavailable].sort(),
		}),
	};
};
if (typeof module !== "undefined" && module.exports) module.exports = { createFrameyDeckySidebar };
