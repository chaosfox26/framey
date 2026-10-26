(() => {
	if (window.FYBar) return;
	const fyCall = window.fyCall;
	const stringify = JSON.stringify;
	if (typeof fyCall !== "function" || !/\[native code\]/.test(Function.prototype.toString.call(fyCall))) return;
	try {
		Object.defineProperty(window, "fyCall", { value: fyCall, writable: false, configurable: true });
	} catch (error) {}
	let mountTimer = 0;
	let disposed = false;
	let mountAttempts = 0;
	let mountWindowStartedAt = 0;
	let observer = null;
	const bar = {
		get button() {
			return disposed ? null : button;
		},
		dispose() {
			if (disposed) return;
			disposed = true;
			clearTimeout(mountTimer);
			mountTimer = 0;
			if (observer) observer.disconnect();
			if (window.FYObs === observer) delete window.FYObs;
			button.remove();
			style.remove();
			if (window.FYBar === bar) delete window.FYBar;
		},
		set(on) {
			if (disposed) return;
			button.classList.toggle("fy-on", !!on);
			button.setAttribute("aria-pressed", String(!!on));
		},
	};
	window.FYBar = bar;
	if (window.FYObs) window.FYObs.disconnect();
	const old = document.getElementById("fy-bar-style");
	if (old) old.remove();
	const style = document.createElement("style");
	style.id = "fy-bar-style";
	style.dataset.fy = "";
	style.textContent =
		".fy-bb{position:relative;flex:none;align-self:center;margin:0 6px 0 2px;width:48px;height:48px;z-index:99999;overflow:visible;cursor:pointer;outline:0;user-select:none;-webkit-tap-highlight-color:transparent}" +
		".fy-bb::before{content:'';position:absolute;left:6px;top:6px;width:36px;height:36px;border-radius:50%;background:#fff;opacity:0;transition:opacity .15s ease}" +
		".fy-bb>i{position:absolute;left:0;top:0;width:48px;height:48px;background:center/24px 24px no-repeat;transition:filter .15s ease,transform .15s ease}" +
		".fy-bb>span{position:absolute;left:50%;top:44px;transform:translate(calc(-50% + var(--x,0px)),var(--y,0px));padding:0 6px;border-radius:6px;background:rgba(8,8,16,.92);color:#fff;font:600 14px/18px sans-serif;white-space:nowrap;pointer-events:none;opacity:0;transition:opacity .15s ease}" +
		".fy-bb.fy-on>i{filter:brightness(1.2)}" +
		".fy-bb:hover>i,.fy-bb:focus-visible>i{filter:brightness(1.35)}" +
		".fy-bb:focus-visible{outline:3px solid #8fb0ff;outline-offset:3px;border-radius:8px}" +
		".fy-bb:hover::before,.fy-bb:focus-visible::before{opacity:.16}" +
		".fy-bb:hover>span,.fy-bb:focus-visible>span{opacity:1}" +
		".fy-bb.fy-on::before{opacity:.24}" +
		".fy-bb:active::before{opacity:.36}" +
		".fy-bb:active>i{transform:scale(.92)}" +
		"@keyframes fy-pulse{50%{transform:scale(.92)}}" +
		"@keyframes fy-glow{50%{opacity:.36}}" +
		"@media (prefers-reduced-motion:no-preference){.fy-bb.fy-p>i{animation:fy-pulse .15s ease}.fy-bb.fy-p::before{animation:fy-glow .15s ease}}" +
		"@media (prefers-reduced-motion:reduce){.fy-bb::before,.fy-bb>i,.fy-bb>span{transition:none}.fy-bb:active>i{transform:none}}";
	document.head.append(style);
	const button = document.createElement("div");
	button.dataset.fy = "";
	button.className = "fy-bb";
	button.tabIndex = 0;
	button.setAttribute("role", "button");
	button.setAttribute("aria-label", "Framey");
	button.setAttribute("aria-pressed", "false");
	const icon = document.createElement("i");
	icon.style.backgroundImage = 'url("' + FY_ICON_URL + '")';
	const label = document.createElement("span");
	label.textContent = "Framey";
	label.setAttribute("aria-hidden", "true");
	button.append(icon, label);
	const positionLabel = () => {
		const buttonBounds = button.getBoundingClientRect();
		const labelWidth = label.offsetWidth;
		const buttonCenter = buttonBounds.left + buttonBounds.width / 2;
		label.style.setProperty(
			"--x",
			Math.max(0, 4 - (buttonCenter - labelWidth / 2)) +
				Math.min(0, innerWidth - 4 - (buttonCenter + labelWidth / 2)) +
				"px",
		);
		label.style.setProperty("--y", Math.min(0, innerHeight - 2 - (buttonBounds.top + 44 + label.offsetHeight)) + "px");
	};
	const togglePanel = () => {
		if (!disposed) fyCall(stringify({ id: 0, plugin: "_core", method: "toggle" }));
	};
	button.addEventListener("mouseenter", positionLabel);
	button.addEventListener("focus", positionLabel);
	button.addEventListener("click", () => {
		button.classList.remove("fy-p");
		void button.offsetWidth;
		button.classList.add("fy-p");
		togglePanel();
	});
	button.addEventListener("animationend", () => button.classList.remove("fy-p"));
	button.addEventListener("keydown", event => {
		if (event.key === "Enter" || event.key === " ") {
			event.preventDefault();
			if (!event.repeat) button.click();
		}
	});
	const mountButton = () => {
		mountTimer = 0;
		if (disposed) return;
		let steamBar = document.querySelector("img.avatar");
		while (steamBar && steamBar.getBoundingClientRect().width < 200) steamBar = steamBar.parentElement;
		if (!steamBar || steamBar.children.length < 2 || steamBar.children[1] === button) return;
		const now = Date.now();
		if (now - mountWindowStartedAt > 10000) {
			mountWindowStartedAt = now;
			mountAttempts = 0;
		}
		if (++mountAttempts > 20) {
			mountTimer = setTimeout(mountButton, Math.max(1, 10001 - (now - mountWindowStartedAt)));
			return;
		}
		steamBar.insertBefore(button, steamBar.children[1]);
		bar.set(button.classList.contains("fy-on"));
	};
	observer = new MutationObserver(() => {
		if (!disposed && !mountTimer) mountTimer = setTimeout(mountButton, 300);
	});
	window.FYObs = observer;
	mountButton();
	observer.observe(document.body, { childList: true, subtree: true });
})();
