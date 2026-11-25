"use strict";
// Existing catalog safety fixtures read the complete semantic document. This
// driver visits the real pager, retaining original nodes and restoring focus.
// Dedicated catalog-navigation fixtures assert each actually visible screen.
module.exports = (env, getBox) => {
	const K = env.window.__S.K;
	let navigation = null,
		owner = null;
	const set = K.setPageNavigation;
	K.setPageNavigation = options => {
		navigation = options;
		owner = getBox();
		return set(options);
	};
	const clear = K.clearPageNavigation;
	K.clearPageNavigation = () => {
		navigation = null;
		owner = null;
		return clear();
	};
	const visit = (read, seek = false) => {
		const box = getBox(),
			nav = navigation;
		if (!nav || owner !== box) return read(box);
		const original = nav.index,
			focused = env.document.activeElement;
		const values = [];
		for (let index = 0; index < nav.count; index++) {
			nav.onPage(index);
			const value = read(box);
			if (seek && value) {
				if (focused && focused.isConnected && focused !== env.document.activeElement)
					focused.focus({ preventScroll: true });
				return value;
			}
			values.push(value);
		}
		nav.onPage(original);
		if (focused && focused.isConnected && focused !== env.document.activeElement)
			focused.focus({ preventScroll: true });
		return seek ? null : values;
	};
	const all = selector => {
		const found = visit(box => [...box.querySelectorAll(selector)]);
		return [...new Set(found.flat())];
	};
	const find = selector => visit(box => box.querySelector(selector), true);
	const text = () => {
		const title = (navigation && navigation.title) || "";
		const chunks = visit(box => box.textContent);
		return title + (Array.isArray(chunks) ? chunks.join("") : chunks);
	};
	const controls = () => {
		const nodes = all("[aria-label]");
		if (navigation && navigation.onBack) {
			const node = [...env.document.querySelectorAll('[role="button"]')].find(n => n.textContent === "Back");
			if (node) nodes.push(node);
		}
		return nodes;
	};
	const label = name => {
		const found = controls().find(n => (n.getAttribute("aria-label") || n.textContent) === name);
		if (!found) return null;
		if (found.textContent === "Back" && !found.getAttribute("aria-label")) return found;
		if (env.document.querySelector('[role="dialog"]')) return found;
		return visit(
			box => [...box.querySelectorAll("[aria-label]")].find(n => n.getAttribute("aria-label") === name),
			true,
		);
	};
	const proxies = new WeakMap();
	const proxy = box => {
		if (proxies.has(box)) return proxies.get(box);
		const value = new Proxy(box, {
			set(target, key, value) {
				Reflect.set(target, key, value, target);
				return true;
			},
			get(target, key) {
				if (key === "__catalogReal") return target;
				if (key === "querySelector")
					return selector =>
						owner === target
							? env.document.querySelector('[role="dialog"]')
								? all(selector)[0] || null
								: find(selector)
							: target.querySelector(selector);
				if (key === "querySelectorAll")
					return selector => (owner === target ? all(selector) : target.querySelectorAll(selector));
				if (key === "textContent" && owner === target) return text();
				const result = Reflect.get(target, key, target);
				return typeof result === "function" ? result.bind(target) : result;
			},
		});
		proxies.set(box, value);
		return value;
	};
	return {
		controls,
		label,
		text,
		all,
		find,
		proxy,
		get navigation() {
			return navigation;
		},
	};
};
