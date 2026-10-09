(() => {
  if (window.FYBar) return;
  let timer = 0;
  let off = false;
  let moves = 0;
  let since = 0;
  window.FYBar = { dispose() { off = true; clearTimeout(timer); timer = 0; } };
  if (window.FYObs) window.FYObs.disconnect();
  const b = document.createElement("div");
  b.dataset.fy = "";
  b.style.cssText = "flex:none;align-self:center;margin:0 8px 0 4px;width:44px;height:48px;z-index:99999;background:center/24px 24px no-repeat";
  b.style.backgroundImage = 'url("' + FY_ICON_URL + '")';
  b.onclick = () => window.fyCall(JSON.stringify({ id: 0, plugin: "_core", method: "toggle" }));
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
  };
  window.FYObs = new MutationObserver(() => { if (!off && !timer) timer = setTimeout(mount, 300); });
  mount();
  window.FYObs.observe(document.body, { childList: true, subtree: true });
})();
