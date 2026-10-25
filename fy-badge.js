(() => {
  const bar = window.FYBar;
  if (!bar || bar.badge) return;
  let count = 0;
  const old = document.getElementById("fy-badge-style");
  if (old) old.remove();
  const st = document.createElement("style");
  st.id = "fy-badge-style";
  st.dataset.fy = "";
  st.textContent = ".fy-bb>.fy-bdg{position:absolute;right:8px;top:8px;width:10px;height:10px;border-radius:50%;background:#ffb347;box-shadow:0 0 0 2px rgba(8,8,16,.92);pointer-events:none}";
  document.head.append(st);
  const paint = () => {
    const b = document.querySelector(".fy-bb");
    if (!b) return;
    let dot = b.querySelector(":scope>.fy-bdg");
    if (count > 0 && !dot) {
      dot = document.createElement("b");
      dot.dataset.fy = "";
      dot.className = "fy-bdg";
      dot.setAttribute("aria-hidden", "true");
      b.append(dot);
    }
    if (count <= 0 && dot) dot.remove();
    b.setAttribute("aria-label", count > 0 ? "Framey, " + count + (count === 1 ? " update" : " updates") : "Framey");
  };
  const set = bar.set;
  bar.set = on => {
    set(on);
    paint();
  };
  bar.badge = n => {
    n = Number(n);
    count = isFinite(n) ? Math.max(0, Math.min(99, Math.floor(n))) : 0;
    paint();
  };
})();
