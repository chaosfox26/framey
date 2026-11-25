"use strict";
const assert = require("assert");
const { test } = require("node:test");
const { makeDom } = require("./harness");

const boot = (width = 1200) => {
  let host;
  const env = makeDom({
    before(window) {
      window.FY_ICON_URL = "icon.svg";
      Object.defineProperty(window, "innerWidth", { value: width, writable: true });
      window.Element.prototype.getBoundingClientRect = function () {
        const width = this.tagName === "IMG" ? 48 : 1200;
        return { left: 0, top: 0, right: width, bottom: 48, width, height: 48 };
      };
      host = window.document.createElement("div");
      const avatar = window.document.createElement("img");
      avatar.className = "avatar";
      host.append(avatar, window.document.createElement("span"));
      window.document.body.append(host);
    },
    loadFiles: ["bar.js"],
  });
  env.button = env.window.FYBar.button;
  env.host = host;
  env.close = () => {
    env.window.FYBar.dispose();
    env.window.close();
  };
  return env;
};

const setBounds = (button, left, width) => {
  button.getBoundingClientRect = () => ({
    left, top: 0, right: left + width, bottom: 48, width, height: 48,
  });
};

const toggleRequest = anchor => ({
  id: 0,
  plugin: "_core",
  method: "toggle",
  arg: { anchor_x: anchor },
});

test("bar clicks send the current icon center in bar viewport coordinates", () => {
  const env = boot();
  try {
    assert.equal(env.host.children[1], env.button);
    setBounds(env.button, 276, 48);
    env.button.click();
    assert.deepEqual(env.sent("toggle"), [toggleRequest(0.25)]);
    assert.equal(env.button.classList.contains("fy-p"), true);

    env.window.innerWidth = 800;
    setBounds(env.button, 576, 48);
    env.button.dispatchEvent(new env.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    assert.deepEqual(env.sent("toggle"), [toggleRequest(0.25), toggleRequest(0.75)]);
    assert.equal(env.host.children[1], env.button);
  } finally { env.close(); }
});

test("bar anchor positions outside the viewport are clamped", () => {
  const env = boot(800);
  try {
    setBounds(env.button, -100, 48);
    env.button.click();
    setBounds(env.button, 900, 48);
    env.button.click();
    assert.deepEqual(env.sent("toggle"), [toggleRequest(0), toggleRequest(1)]);
  } finally { env.close(); }
});

test("invalid bar geometry sends a finite centered launcher anchor", () => {
  const cases = [
    { viewport: 0, left: 100, width: 48 },
    { viewport: -800, left: 100, width: 48 },
    { viewport: Infinity, left: 100, width: 48 },
    { viewport: NaN, left: 100, width: 48 },
    { viewport: 800, left: NaN, width: 48 },
    { viewport: 800, left: Infinity, width: 48 },
    { viewport: 800, left: 100, width: Infinity },
    { viewport: 800, left: 100, width: 0 },
    { viewport: 800, left: 100, width: -48 },
  ];
  for (const geometry of cases) {
    const env = boot(geometry.viewport);
    try {
      setBounds(env.button, geometry.left, geometry.width);
      env.button.click();
      assert.deepEqual(env.sent("toggle"), [toggleRequest(0.5)]);
    } finally { env.close(); }
  }
});
