/* Framey's independently authored, synchronous DOM compatibility profile.
 * No React implementation is included. This is an experimental subset, not a
 * general React renderer: no hydration, concurrency, Suspense, lazy loading,
 * error boundaries, synthetic event propagation, or React internal APIs.
 * Effects run after each synchronous DOM commit; memo is an identity wrapper.
 */
const createFrameyViewRuntime = function (document, options) {
	"use strict";
	options = options || {};
	const ELEMENT = Symbol.for("react.element");
	const FRAGMENT = Symbol.for("react.fragment");
	const PORTAL = Symbol("framey.portal");
	const TEXT = Symbol("framey.text");
	const EMPTY = Symbol("framey.empty");
	const roots = new Map();
	const idPrefix = Math.random().toString(36).slice(2);
	let current = null,
		disposed = false,
		nextId = 0;
	const unsupported = name => {
		throw new Error("Framey view: unsupported capability " + name);
	};
	const isElement = value => !!value && typeof value === "object" && value.$$typeof === ELEMENT;
	function element(type, config, ...children) {
		const props = { ...((type && type.defaultProps) || {}), ...(config || {}) };
		const key = props.key == null ? null : String(props.key),
			ref = props.ref == null ? null : props.ref;
		delete props.key;
		delete props.ref;
		if (children.length) props.children = children.length === 1 ? children[0] : children;
		return { $$typeof: ELEMENT, type, key, ref, props };
	}
	function flatten(value, keepEmpty = false, out = []) {
		if (Array.isArray(value)) {
			for (const child of value) flatten(child, keepEmpty, out);
		} else if (value == null || typeof value === "boolean") {
			if (keepEmpty) out.push(null);
		} else out.push(value);
		return out;
	}
	const depsEqual = (a, b) =>
		a !== undefined && b !== undefined && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
	const readContext = (context, values) => (values.has(context) ? values.get(context) : context._default);
	function hook(kind, init) {
		if (!current) throw new Error("Framey view: hooks require a function component render");
		const index = current.cursor++;
		if (!current.hooks[index]) current.hooks[index] = Object.assign(init(current), { kind });
		if (current.hooks[index].kind !== kind) throw new Error("Framey view: hook order changed");
		return current.hooks[index];
	}
	function useReducer(reducer, initial, init) {
		const state = hook("state", owner => {
			const slot = { value: init ? init(initial) : initial, reducer, owner };
			slot.dispatch = action => {
				if (!owner.mounted || !owner.root.active) return;
				const value = slot.reducer(slot.value, action);
				if (!Object.is(value, slot.value)) {
					slot.value = value;
					schedule(owner.root);
				}
			};
			return slot;
		});
		state.reducer = reducer;
		return [state.value, state.dispatch];
	}
	function effect(kind, callback, dependencies) {
		const state = hook(kind, () => ({ deps: undefined, cleanup: null }));
		if (!depsEqual(state.deps, dependencies)) {
			state.deps = dependencies && [...dependencies];
			const owner = current;
			owner.root[kind === "layout" ? "layouts" : "effects"].push(() => {
				if (!owner.mounted) return;
				if (state.cleanup) {
					const clean = state.cleanup;
					state.cleanup = null;
					clean();
				}
				const cleanup = callback();
				state.cleanup = typeof cleanup === "function" ? cleanup : null;
			});
		}
	}
	function setRef(ref, value) {
		if (typeof ref === "function") ref(value);
		else if (ref && typeof ref === "object") ref.current = value;
		else if (ref != null) unsupported("string refs");
	}
	function updateRef(inst, ref, value) {
		if (inst.ref === ref) return;
		if (inst.ref) setRef(inst.ref, null);
		inst.ref = ref;
		if (ref)
			inst.root.refs.push(() => {
				if (inst.mounted && inst.ref === ref) setRef(ref, value);
			});
	}
	function Component(props, context) {
		this.props = props;
		this.context = context;
		this.state = this.state || {};
	}
	Component.prototype.isReactComponent = {};
	Component.prototype.setState = function (update, callback) {
		const owner = this._frameyOwner;
		if (!owner || !owner.mounted || !owner.root.active) return;
		owner.previousState = owner.previousState || this.state;
		const patch = typeof update === "function" ? update(this.state, this.props) : update;
		if (patch != null) this.state = { ...this.state, ...patch };
		if (callback) owner.callbacks.push(callback);
		schedule(owner.root);
	};
	Component.prototype.forceUpdate = function (callback) {
		this.setState({}, callback);
	};
	function PureComponent(props, context) {
		Component.call(this, props, context);
	}
	PureComponent.prototype = Object.create(Component.prototype);
	PureComponent.prototype.constructor = PureComponent;
	PureComponent.prototype.isPureReactComponent = true;
	const React = {
		version: "0.1.0-framey-experimental",
		createElement: element,
		cloneElement(value, props, ...children) {
			if (!isElement(value)) throw new Error("Framey view: cloneElement requires an element");
			return element(value.type, { ...value.props, key: value.key, ref: value.ref, ...props }, ...children);
		},
		isValidElement: isElement,
		Fragment: FRAGMENT,
		Children: {
			toArray: value => flatten(value),
			count: value => (value == null ? 0 : flatten(value, true).length),
			map: (value, fn, self) =>
				value == null ? value : flatten(flatten(value, true).map((v, i) => fn.call(self, v, i))),
			forEach: (value, fn, self) => {
				if (value != null) flatten(value, true).forEach((v, i) => fn.call(self, v, i));
			},
			only(value) {
				if (!isElement(value)) throw new Error("Framey view: Children.only requires one element");
				return value;
			},
		},
		Component,
		PureComponent,
		createRef: () => ({ current: null }),
		createContext(defaultValue) {
			const context = { _default: defaultValue };
			context.Provider = { _frameyKind: "provider", context };
			context.Consumer = { _frameyKind: "consumer", context };
			return context;
		},
		useState: initial =>
			useReducer(
				(value, action) => (typeof action === "function" ? action(value) : action),
				initial,
				value => (typeof value === "function" ? value() : value),
			),
		useReducer,
		useRef: initial => hook("ref", () => ({ value: { current: initial } })).value,
		useEffect: (fn, deps) => effect("effect", fn, deps),
		useLayoutEffect: (fn, deps) => effect("layout", fn, deps),
		useMemo(fn, deps) {
			const state = hook("memo", () => ({ deps: undefined }));
			if (!depsEqual(state.deps, deps)) {
				state.value = fn();
				state.deps = deps && [...deps];
			}
			return state.value;
		},
		useCallback: (fn, deps) => React.useMemo(() => fn, deps),
		useContext(context) {
			if (!current) throw new Error("Framey view: useContext requires a component render");
			return readContext(context, current.context);
		},
		useImperativeHandle(ref, create, deps) {
			effect(
				"layout",
				() => {
					setRef(ref, create());
					return () => setRef(ref, null);
				},
				deps === undefined ? undefined : [...deps, ref],
			);
		},
		useId: () => hook("id", () => ({ value: ":framey-" + idPrefix + "-" + ++nextId + ":" })).value,
		forwardRef: render => ({ _frameyKind: "forward", render }),
		memo: type => ({ _frameyKind: "memo", type }),
		useDebugValue() {},
		lazy: () => unsupported("lazy"),
		Suspense: () => unsupported("Suspense"),
		startTransition: () => unsupported("concurrent transitions"),
		useSyncExternalStore(subscribe, getSnapshot) {
			// This DOM-only profile reads the current client snapshot. There is no
			// server rendering or hydration path requiring a server snapshot.
			const snapshot = getSnapshot();
			const state = hook("external-store", owner => ({ owner }));
			state.value = snapshot;
			state.getSnapshot = getSnapshot;
			const check = () => {
				if (!state.owner.mounted || !state.owner.root.active) return;
				let changed;
				try {
					changed = !Object.is(state.value, state.getSnapshot());
				} catch {
					changed = true;
				} // Surface getter failures through rendering and its cleanup.
				if (changed) schedule(state.owner.root);
			};
			// Check each commit, including commits that replace only the getter.
			effect("layout", () => {
				check();
			});
			effect(
				"layout",
				() => {
					let active = true;
					const unsubscribe = subscribe(() => {
						if (active) check();
					});
					check(); // A store may change between rendering and subscribing.
					return () => {
						active = false;
						unsubscribe();
					};
				},
				[subscribe],
			);
			return snapshot;
		},
	};
	const svgNames = {
		className: "class",
		htmlFor: "for",
		tabIndex: "tabindex",
		xlinkHref: "xlink:href",
		xmlnsXlink: "xmlns:xlink",
	};
	const unitless = new Set([
		"opacity",
		"zIndex",
		"fontWeight",
		"lineHeight",
		"flex",
		"flexGrow",
		"flexShrink",
		"order",
		"zoom",
		"strokeWidth",
		"fillOpacity",
		"strokeOpacity",
		"gridRow",
		"gridColumn",
		"scale",
	]);
	const eventName = (name, node) => {
		let event = name
			.slice(2)
			.replace(/Capture$/, "")
			.toLowerCase();
		if (event === "doubleclick") event = "dblclick";
		if (
			event === "change" &&
			(node.tagName === "TEXTAREA" || (node.tagName === "INPUT" && !["checkbox", "radio", "file"].includes(node.type)))
		)
			event = "input";
		return event;
	};
	function prop(inst, name, value, oldValue) {
		const node = inst.dom;
		if (["children", "key", "ref", "suppressHydrationWarning", "suppressContentEditableWarning"].includes(name)) return;
		if (/^on[A-Z]/.test(name)) {
			const old = inst.listeners[name];
			if (old) {
				node.removeEventListener(old.event, old.fn, old.capture);
				delete inst.listeners[name];
			}
			if (typeof value === "function") {
				const capture = name.endsWith("Capture"),
					event = eventName(name, node);
				const fn = ev => {
					if (!ev.nativeEvent) ev.nativeEvent = ev;
					if (!ev.persist) ev.persist = () => {};
					value(ev);
				};
				inst.listeners[name] = { capture, event, fn };
				node.addEventListener(event, fn, capture);
			}
			return;
		}
		if (name === "style") {
			if (typeof value === "string") {
				node.style.cssText = value;
				return;
			}
			if (typeof oldValue === "string") node.style.cssText = "";
			for (const key of Object.keys({ ...(typeof oldValue === "object" ? oldValue : {}), ...value })) {
				const v = value && value[key];
				const text =
					v == null || v === false
						? ""
						: typeof v === "number" && v !== 0 && !unitless.has(key) && !key.startsWith("--")
							? v + "px"
							: String(v);
				if (key.startsWith("--")) node.style.setProperty(key, text);
				else node.style[key] = text;
			}
			return;
		}
		if (name === "dangerouslySetInnerHTML") {
			if (value && value.__html != null && (!oldValue || value.__html !== oldValue.__html))
				node.innerHTML = value.__html;
			else if (!value && oldValue) node.textContent = "";
			return;
		}
		if (name === "value" || name === "checked" || name === "selected" || name === "muted") {
			const next = value == null ? (name === "value" ? "" : false) : value;
			if (node[name] !== next && !(name === "value" && node[name] === String(next))) node[name] = next;
			return;
		}
		if (name === "defaultValue" || name === "defaultChecked") {
			if (oldValue === undefined && value != null) node[name] = value;
			return;
		}
		let attr = svgNames[name] || name;
		if (
			inst.svg &&
			!svgNames[name] &&
			![
				"viewBox",
				"preserveAspectRatio",
				"gradientUnits",
				"gradientTransform",
				"markerWidth",
				"markerHeight",
				"refX",
				"refY",
				"patternUnits",
				"patternContentUnits",
				"patternTransform",
				"textLength",
				"lengthAdjust",
				"attributeName",
				"baseFrequency",
				"keyTimes",
				"keySplines",
				"keyPoints",
			].includes(name)
		)
			attr = name.replace(/[A-Z]/g, c => "-" + c.toLowerCase());
		if (value == null || (value === false && !attr.startsWith("aria-") && !attr.startsWith("data-")))
			node.removeAttribute(attr);
		else if (typeof value !== "function" && typeof value !== "object")
			node.setAttribute(
				attr,
				value === true && !attr.startsWith("aria-") && !attr.startsWith("data-") ? "" : String(value),
			);
	}
	function updateProps(inst, old, props) {
		// Type affects which native event represents onChange.
		if (old.type !== props.type && "type" in props) prop(inst, "type", props.type, old.type);
		for (const name of Object.keys({ ...old, ...props })) {
			if (
				!Object.is(old[name], props[name]) ||
				name === "value" ||
				name === "checked" ||
				((name === "onChange" || name === "onChangeCapture") && old.type !== props.type)
			)
				prop(inst, name, props[name], old[name]);
		}
	}
	function nodes(inst) {
		if (inst.dom) return [inst.dom];
		if (inst.type === PORTAL) return [];
		return inst.children.flatMap(nodes);
	}
	function place(parent, children) {
		let cursor = parent.firstChild;
		for (const node of children.flatMap(nodes)) {
			if (node === cursor) cursor = cursor.nextSibling;
			else parent.insertBefore(node, cursor);
		}
	}
	function unmount(inst) {
		if (!inst.mounted) return;
		inst.mounted = false;
		inst.root.owned.delete(inst);
		const errors = [];
		const attempt = fn => {
			try {
				fn();
			} catch (error) {
				errors.push(error);
			}
		};
		if (inst.ref) attempt(() => setRef(inst.ref, null));
		if (inst.instance && inst.instance.componentWillUnmount) attempt(() => inst.instance.componentWillUnmount());
		for (const state of inst.hooks)
			if (state.cleanup)
				attempt(() => {
					const fn = state.cleanup;
					state.cleanup = null;
					fn();
				});
		for (const child of inst.children) attempt(() => unmount(child));
		if (inst.dom) {
			for (const { event, fn, capture } of Object.values(inst.listeners))
				inst.dom.removeEventListener(event, fn, capture);
			inst.dom.remove();
		}
		if (errors.length) throw errors[0];
	}
	function describe(value) {
		if (value == null) return { type: EMPTY, key: null, props: {}, ref: null };
		if (typeof value === "string" || typeof value === "number" || typeof value === "bigint")
			return { type: TEXT, key: null, props: { value: String(value) }, ref: null };
		if (isElement(value)) return value;
		throw new Error("Framey view: unsupported render value or element type");
	}
	function children(parent, oldChildren, values, context, svg, root) {
		// Empty positions participate in unkeyed identity without producing DOM.
		const next = flatten(values, true).map(describe),
			used = new Set(),
			result = [];
		for (let index = 0; index < next.length; index++) {
			const value = next[index];
			const old =
				value.key !== null
					? oldChildren.find(
							candidate => !used.has(candidate) && candidate.key === value.key && candidate.type === value.type,
						)
					: oldChildren[index] &&
						  oldChildren[index].key === null &&
						  oldChildren[index].type === value.type &&
						  !used.has(oldChildren[index])
						? oldChildren[index]
						: null;
			if (old) used.add(old);
			result.push(patch(parent, old, value, context, svg, root));
		}
		for (const old of oldChildren) if (!used.has(old)) unmount(old);
		return result;
	}
	function patch(parent, old, value, context, svg, root) {
		const inst = old || {
			type: value.type,
			key: value.key,
			props: {},
			children: [],
			hooks: [],
			ref: null,
			listeners: {},
			root,
			mounted: true,
			callbacks: [],
		};
		if (!old) root.owned.add(inst);
		const previousProps = inst.props;
		inst.props = value.props;
		inst.context = context;
		const type = inst.type,
			props = inst.props;
		if (type === EMPTY) return inst;
		if (type === TEXT) {
			if (!inst.dom) inst.dom = document.createTextNode(props.value);
			else if (inst.dom.data !== props.value) inst.dom.data = props.value;
		} else if (typeof type === "string") {
			inst.svg = svg || type === "svg";
			if (!inst.dom)
				inst.dom = inst.svg
					? document.createElementNS("http://www.w3.org/2000/svg", type)
					: document.createElement(type);
			updateProps(inst, previousProps, props);
			if (props.dangerouslySetInnerHTML && props.children != null)
				throw new Error("Framey view: cannot combine innerHTML and children");
			inst.children = children(
				inst.dom,
				inst.children,
				props.dangerouslySetInnerHTML ? [] : props.children,
				context,
				inst.svg && type !== "foreignObject",
				root,
			);
			place(inst.dom, inst.children);
			// Select values apply after option nodes exist.
			if (type === "select" && props.value != null) prop(inst, "value", props.value, previousProps.value);
			updateRef(inst, value.ref, inst.dom);
		} else if (type === FRAGMENT) {
			inst.children = children(parent, inst.children, props.children, context, svg, root);
		} else if (type === PORTAL) {
			if (old && previousProps.container !== props.container) {
				inst.children.forEach(unmount);
				inst.children = [];
			}
			inst.children = children(props.container, inst.children, props.children, context, false, root);
			place(props.container, inst.children);
		} else if (type && type._frameyKind === "provider") {
			const values = new Map(context);
			values.set(type.context, props.value);
			inst.children = children(parent, inst.children, props.children, values, svg, root);
		} else if (type && type._frameyKind === "consumer") {
			if (typeof props.children !== "function") throw new Error("Framey view: Consumer requires a render function");
			inst.children = children(
				parent,
				inst.children,
				props.children(readContext(type.context, context)),
				context,
				svg,
				root,
			);
		} else if (typeof type === "function" && type.prototype && type.prototype.isReactComponent) {
			const initial = !inst.instance;
			if (initial) {
				inst.instance = new type(props, type.contextType ? readContext(type.contextType, context) : undefined);
				inst.instance._frameyOwner = inst;
			}
			const object = inst.instance,
				previousState = inst.previousState || object.state;
			inst.previousState = null;
			object.props = props;
			if (type.contextType) object.context = readContext(type.contextType, context);
			inst.children = children(parent, inst.children, object.render(), context, svg, root);
			updateRef(inst, value.ref, object);
			if (initial && object.componentDidMount)
				root.layouts.push(() => {
					if (inst.mounted) object.componentDidMount();
				});
			if (!initial && object.componentDidUpdate)
				root.layouts.push(() => {
					if (inst.mounted) object.componentDidUpdate(previousProps, previousState);
				});
			root.layouts.push(
				...inst.callbacks.splice(0).map(fn => () => {
					if (inst.mounted) fn.call(object);
				}),
			);
		} else {
			let render = type,
				forwarded = false;
			if (type && type._frameyKind === "memo") render = type.type;
			if (render && render._frameyKind === "forward") {
				render = render.render;
				forwarded = true;
			}
			if (typeof render !== "function") unsupported("element type " + String(type));
			const previous = current;
			inst.cursor = 0;
			current = inst;
			let output;
			try {
				output = render(props, forwarded ? value.ref : undefined);
			} finally {
				current = previous;
			}
			if (inst.cursor !== inst.hooks.length) throw new Error("Framey view: hook order changed");
			inst.children = children(parent, inst.children, output, context, svg, root);
		}
		return inst;
	}
	function schedule(root) {
		if (!root.active) return;
		root.pending = true;
		if (root.rendering) return;
		root.rendering = true;
		let rounds = 0;
		try {
			while (root.pending) {
				if (++rounds > 100) throw new Error("Framey view: excessive synchronous updates");
				root.pending = false;
				root.refs = [];
				root.layouts = [];
				root.effects = [];
				const focused = document.activeElement;
				const selection =
					focused && typeof focused.selectionStart === "number"
						? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection]
						: null;
				root.children = children(root.container, root.children, root.value, new Map(), false, root);
				place(root.container, root.children);
				if (
					focused &&
					focused.isConnected &&
					document.activeElement !== focused &&
					typeof focused.focus === "function"
				) {
					focused.focus({ preventScroll: true });
					if (selection) focused.setSelectionRange(...selection);
				}
				for (const queue of [root.refs, root.layouts, root.effects]) for (const job of queue) job();
			}
			root.committed = true;
		} catch (error) {
			// Rendering failed: release any already registered ownership rather than
			// leaving live setters or effect resources behind an unusable root.
			try {
				drop(root);
			} catch {
				/* Preserve the originating render error. */
			}
			// Initial mounting still throws to the loader's direct try/catch. A
			// later event-driven update may cross realms, so notify its owner
			// directly after cleanup instead of relying on a window error event.
			if (root.committed && typeof options.onError === "function") {
				try {
					options.onError(error);
				} catch {
					throw error;
				} // Preserve the originating error if reporting fails.
			} else throw error;
		} finally {
			root.rendering = false;
		}
	}
	function drop(root) {
		root.active = false;
		roots.delete(root.container);
		const owned = [...root.owned];
		root.children = [];
		const errors = [];
		for (const child of owned) {
			try {
				unmount(child);
			} catch (error) {
				errors.push(error);
			}
		}
		if (errors.length) throw errors[0];
	}
	function createRoot(container) {
		if (disposed) throw new Error("Framey view: runtime disposed");
		if (!container || typeof container.insertBefore !== "function")
			throw new Error("Framey view: invalid root container");
		if (roots.has(container)) throw new Error("Framey view: container already has a root");
		container.textContent = "";
		const root = { container, children: [], owned: new Set(), active: true, rendering: false, pending: false };
		roots.set(container, root);
		return {
			render(value) {
				if (disposed || !root.active) throw new Error("Framey view: root disposed or unmounted");
				root.value = value;
				schedule(root);
			},
			unmount() {
				if (root.active) drop(root);
			},
		};
	}
	const ReactDOM = {
		createRoot,
		render(value, container, callback) {
			if (!roots.has(container)) {
				const handle = createRoot(container);
				roots.get(container).handle = handle;
			}
			const root = roots.get(container);
			root.value = value;
			schedule(root);
			if (callback) callback();
			const first = root.children[0];
			return (first && first.instance) || null;
		},
		unmountComponentAtNode(container) {
			const root = roots.get(container);
			if (!root) return false;
			drop(root);
			return true;
		},
		createPortal: (children, container, key) => element(PORTAL, { children, container, key }),
		findDOMNode(value) {
			if (value == null || value.nodeType) return value;
			if (value._frameyOwner) return nodes(value._frameyOwner)[0] || null;
			unsupported("findDOMNode target");
		},
		hydrate: () => unsupported("hydration"),
		hydrateRoot: () => unsupported("hydration"),
	};
	const JSX = {
		Fragment: FRAGMENT,
		jsx: (type, props, key) => element(type, { ...props, ...(key === undefined ? {} : { key }) }),
	};
	JSX.jsxs = JSX.jsx;
	JSX.jsxDEV = JSX.jsx;
	return {
		React,
		ReactDOM,
		JSX,
		dispose() {
			if (disposed) return;
			disposed = true;
			const errors = [];
			for (const root of [...roots.values()]) {
				try {
					drop(root);
				} catch (error) {
					errors.push(error);
				}
			}
			if (errors.length) throw errors[0];
		},
	};
};
if (typeof module !== "undefined" && module.exports) module.exports = { createFrameyViewRuntime };
