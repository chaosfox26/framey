(() => {
  if (window.FYBar) return;
  const fyCall = window.fyCall;
  const stringify = JSON.stringify;
  if (typeof fyCall !== "function" || !/\[native code\]/.test(Function.prototype.toString.call(fyCall))) return;
  try { Object.defineProperty(window, "fyCall", { value: fyCall, writable: false, configurable: true }); } catch (e) {}
  let timer = 0;
  let off = false;
  let moves = 0;
  let since = 0;
  window.FYBar = {
    dispose() { off = true; clearTimeout(timer); timer = 0; if (window.FYObs) window.FYObs.disconnect(); b.remove(); st.remove(); },
    set(on) {
      b.classList.toggle("fy-on", !!on);
      b.setAttribute("aria-pressed", String(!!on));
    },
  };
  if (window.FYObs) window.FYObs.disconnect();
  const old = document.getElementById("fy-bar-style");
  if (old) old.remove();
  const st = document.createElement("style");
  st.id = "fy-bar-style";
  st.dataset.fy = "";
  st.textContent =
    ".fy-bb{position:relative;flex:none;align-self:center;margin:0 6px 0 2px;width:48px;height:48px;z-index:99999;overflow:visible;cursor:pointer;outline:0;user-select:none;-webkit-tap-highlight-color:transparent}" +
    ".fy-bb::before{content:'';position:absolute;left:6px;top:6px;width:36px;height:36px;border-radius:50%;background:#fff;opacity:0;transition:opacity .15s ease}" +
    ".fy-bb>i{position:absolute;left:0;top:0;width:48px;height:48px;background:center/24px 24px no-repeat;transition:filter .15s ease,transform .15s ease}" +
    ".fy-bb>span{position:absolute;left:50%;top:44px;transform:translate(calc(-50% + var(--x,0px)),var(--y,0px));padding:0 6px;border-radius:6px;background:rgba(8,8,16,.92);color:#fff;font:600 14px/18px sans-serif;white-space:nowrap;pointer-events:none;opacity:0;transition:opacity .15s ease}" +
    ".fy-bb.fy-on>i{filter:brightness(1.2)}" +
    ".fy-bb:hover>i,.fy-bb:focus-visible>i{filter:brightness(1.35)}" +
    ".fy-bb:hover::before,.fy-bb:focus-visible::before{opacity:.16}" +
    ".fy-bb:hover>span,.fy-bb:focus-visible>span{opacity:1}" +
    ".fy-bb.fy-on::before{opacity:.24}" +
    ".fy-bb:active::before{opacity:.36}" +
    ".fy-bb:active>i{transform:scale(.92)}" +
    "@keyframes fy-pulse{50%{transform:scale(.92)}}" +
    "@keyframes fy-glow{50%{opacity:.36}}" +
    "@media (prefers-reduced-motion:no-preference){.fy-bb.fy-p>i{animation:fy-pulse .15s ease}.fy-bb.fy-p::before{animation:fy-glow .15s ease}}" +
    "@media (prefers-reduced-motion:reduce){.fy-bb::before,.fy-bb>i,.fy-bb>span{transition:none}.fy-bb:active>i{transform:none}}";
  document.head.append(st);
  const b = document.createElement("div");
  b.dataset.fy = "";
  b.className = "fy-bb";
  b.tabIndex = 0;
  b.setAttribute("role", "button");
  b.setAttribute("aria-label", "Framey");
  b.setAttribute("aria-pressed", "false");
  const icon = document.createElement("i");
  icon.style.backgroundImage = 'url("' + FY_ICON_URL + '")';
  const label = document.createElement("span");
  label.textContent = "Framey";
  label.setAttribute("aria-hidden", "true");
  b.append(icon, label);
  const place = () => {
    const r = b.getBoundingClientRect();
    const w = label.offsetWidth;
    const c = r.left + r.width / 2;
    label.style.setProperty("--x", Math.max(0, 4 - (c - w / 2)) + Math.min(0, innerWidth - 4 - (c + w / 2)) + "px");
    label.style.setProperty("--y", Math.min(0, innerHeight - 2 - (r.top + 44 + label.offsetHeight)) + "px");
  };
  const open = () => fyCall(stringify({ id: 0, plugin: "_core", method: "toggle" }));
  b.addEventListener("mouseenter", place);
  b.addEventListener("focus", place);
  b.addEventListener("click", () => {
    b.classList.remove("fy-p");
    void b.offsetWidth;
    b.classList.add("fy-p");
    open();
  });
  b.addEventListener("animationend", () => b.classList.remove("fy-p"));
  b.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!e.repeat) b.click();
    }
  });
  const mount = () => {
    timer = 0;
    if (off) return;
    let e = document.querySelector("img.avatar");
    while (e && e.getBoundingClientRect().width < 200) e = e.parentElement;
    if (!e || e.children.length < 2 || e.children[1] === b) return;
    const now = Date.now();
    if (now - since > 10000) { since = now; moves = 0; }
    if (++moves > 20) return;
    e.insertBefore(b, e.children[1]);
    window.FYBar.set(b.classList.contains("fy-on"));
  };
  window.FYObs = new MutationObserver(() => { if (!off && !timer) timer = setTimeout(mount, 300); });
  mount();
  window.FYObs.observe(document.body, { childList: true, subtree: true });
})();
