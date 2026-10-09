(() => {
  if (window.FYBar) return;
  window.FYBar = true;
  if (window.FYObs) window.FYObs.disconnect();
  const b = document.createElement("div");
  b.dataset.fy = "";
  b.style.cssText = "flex:none;align-self:center;margin:0 8px 0 4px;width:44px;height:48px;z-index:99999;background:center/24px 24px no-repeat";
  b.style.backgroundImage = 'url("' + FY_ICON_URL + '")';
  b.onclick = () => window.fyCall(JSON.stringify({ id: 0, plugin: "_core", method: "toggle" }));
  let timer = 0;
  let moves = 0;
  const mount = () => {
    timer = 0;
    let e = document.querySelector("img.avatar");
    while (e && e.getBoundingClientRect().width < 200) e = e.parentElement;
    if (!e || e.children.length < 2 || e.children[1] === b) return;
    if (++moves > 20) return window.FYObs.disconnect();
    e.insertBefore(b, e.children[1]);
  };
  window.FYObs = new MutationObserver(() => { if (!timer) timer = setTimeout(mount, 300); });
  mount();
  window.FYObs.observe(document.body, { childList: true, subtree: true });
})();
