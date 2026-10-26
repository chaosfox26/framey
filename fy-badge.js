(() => {
	const bar = window.FYBar;
	if (!bar || bar.badge) return;
	let count = 0;
	let disposed = false;
	const previousStyle = document.getElementById("fy-badge-style");
	if (previousStyle) previousStyle.remove();
	const style = document.createElement("style");
	style.id = "fy-badge-style";
	style.dataset.fy = "";
	style.textContent =
		".fy-bb>.fy-bdg{position:absolute;right:8px;top:8px;width:10px;height:10px;border-radius:50%;background:#ffb347;box-shadow:0 0 0 2px rgba(8,8,16,.92);pointer-events:none}";
	document.head.append(style);
	const renderBadge = () => {
		if (disposed) return;
		const button = bar.button;
		if (!button) return;
		let dot = button.querySelector(":scope>.fy-bdg");
		if (count > 0 && !dot) {
			const badge = document.createElement("b");
			badge.dataset.fy = "";
			badge.className = "fy-bdg";
			badge.setAttribute("aria-hidden", "true");
			button.append(badge);
			dot = badge;
		}
		if (count <= 0 && dot) dot.remove();
		button.setAttribute(
			"aria-label",
			count > 0 ? "Framey, " + count + (count === 1 ? " update" : " updates") : "Framey",
		);
	};
	const originalSet = bar.set;
	bar.set = on => {
		if (disposed) return;
		originalSet.call(bar, on);
		renderBadge();
	};
	const originalDispose = bar.dispose;
	bar.dispose = () => {
		if (disposed) return;
		disposed = true;
		style.remove();
		if (typeof originalDispose === "function") originalDispose.call(bar);
	};
	bar.badge = value => {
		if (disposed) return;
		let updateCount;
		try {
			updateCount = Number(value);
		} catch (error) {
			updateCount = 0;
		}
		count = isFinite(updateCount) ? Math.max(0, Math.min(99, Math.floor(updateCount))) : 0;
		renderBadge();
	};
})();
