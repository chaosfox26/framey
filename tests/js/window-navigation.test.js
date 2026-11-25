"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeDom, TOK } = require("./harness");
const boot = t => {
  const e = makeDom({ loadFiles: ["fy-api.js", "fy-ui.js", "fy-pages.js"], before(w) { w.FY_RUNTIME = { panel_navigation: "launcher", panel_layout: "window", trusted_input: "off" }; } });
  t.after(() => e.window.close());
  e.K = e.window.Framey._k(TOK); e.FY = e.window.Framey;
  e.panel = e.document.querySelector('.fy-panel');
  return e;
};
test("selected window has matching surface and no sibling tabs or Back", t => {
  const e = boot(t);
  e.window.__bindFramey('fan','fixture-fan',{name:'Fan Control'}).register({render(box) { box.append(e.FY.button('Fan action','',()=>{})); }});
  e.FY.open('fan');
  const css = node => e.window.getComputedStyle(node);
  assert.equal(css(e.panel).backgroundColor,'rgb(14, 20, 27)');
  assert.equal(css(e.panel.querySelector('[role=tablist]')).display,'none');
  assert.equal(css(e.panel.querySelector('[aria-label=Back]')).display,'none');
  assert.equal(e.document.activeElement.textContent,'Fan action');
  assert.equal(e.panel.style.width,'778px');
  assert.equal(e.panel.style.height,'698px');
});
test("Escape closes selected window instead of navigating to another screen", t => {
  const e=boot(t);let backs=0;
  e.FY.open('#settings');
  e.K.setPageNavigation({index:0,count:2,onPage(){},onBack(){backs++}});
  e.panel.dispatchEvent(new e.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert.equal(backs,0);assert.equal(e.panel.style.display,'none');
});
test("private extra screen shortcuts are selected from the paged accordion", t => {
  const e=boot(t);
  e.K.windowPages=[{id:'#settings:support',name:'Support',render(box){box.textContent='Support screen'}}];
  e.FY.open('#settings:support');assert.equal(e.K.tab(),'#settings:support');
  assert.equal(e.panel.querySelector('[role=tabpanel]').textContent,'Support screen');
  e.K.setPanelLayout('compact');e.K.openLauncher();
  assert.equal(e.K.launcherWidth(),462);
  const toolbar=e.panel.querySelector('.fy-launcher');
  const buttons=()=>[...toolbar.querySelectorAll('[data-fy-launch]')].map(b=>b.dataset.fyLaunch);
  const seen=[];
  for(let i=0;i<10;i++){
    seen.push(...buttons());
    const next=toolbar.querySelector('[aria-label="Next shortcuts"]');assert.ok(next);
    assert.ok(toolbar.querySelector('[aria-label="Previous shortcuts"]'));
    if(next.getAttribute('aria-disabled')==='true')break;
    next.click();
  }
  assert.ok(seen.includes('#modules'));assert.ok(seen.includes('#settings:support'));
});
