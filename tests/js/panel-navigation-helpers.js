"use strict";
// Exercise the same explicit page controls as users; do not inspect hidden route data.
const pageButton = (panel, text) => [...panel.querySelectorAll('[role="navigation"][aria-label="Page navigation"] [role="button"]')].find(node => node.textContent === text);
const firstPage = panel => {
  const previous = pageButton(panel, "Previous");
  for (let turns = 0; previous && previous.getAttribute("aria-disabled") === "false" && turns < 1024; turns++) previous.click();
};
const findShortcut = (panel, label) => {
  const content = panel.querySelector('[role="tabpanel"]');
  if (!content.querySelector("[data-fy-route]")) return undefined;
  const initial = Number(panel.querySelector('[aria-label="Page navigation"]').dataset.fyPage || 0);
  firstPage(panel);
  for (let turns = 0; turns < 1024; turns++) {
    const found = [...content.querySelectorAll('[data-fy-route]')].find(node => node.textContent === label);
    if (found) return found;
    const next = pageButton(panel, "Next");
    if (!next || next.getAttribute("aria-disabled") !== "false") break;
    next.click();
  }
  firstPage(panel);
  for (let turn = 0; turn < initial; turn++) pageButton(panel, "Next").click();
  return undefined;
};
const shortcutLabels = panel => {
  const content = panel.querySelector('[role="tabpanel"]');
  if (!content.querySelector("[data-fy-route]")) return [];
  const initial = Number(panel.querySelector('[aria-label="Page navigation"]').dataset.fyPage || 0), labels = [];
  firstPage(panel);
  for (let turns = 0; turns < 1024; turns++) {
    labels.push(...[...content.querySelectorAll('[data-fy-route]')].map(node => node.textContent));
    const next = pageButton(panel, "Next");
    if (!next || next.getAttribute("aria-disabled") !== "false") break;
    next.click();
  }
  firstPage(panel);
  for (let turn = 0; turn < initial; turn++) pageButton(panel, "Next").click();
  return labels;
};
module.exports = { findShortcut, shortcutLabels };

const pagedFind = (panel, predicate) => {
  const initial = Number(panel.querySelector('[aria-label="Page navigation"]').dataset.fyPage || 0);
  firstPage(panel);
  for (let turns = 0; turns < 1024; turns++) {
    const hit = predicate(panel);
    if (hit) return hit;
    const next = pageButton(panel, "Next");
    if (!next || next.getAttribute("aria-disabled") !== "false") break;
    next.click();
  }
  firstPage(panel);
  for (let turn = 0; turn < initial; turn++) pageButton(panel, "Next").click();
  return undefined;
};
const pagedControl = (panel, label) => pagedFind(panel, surface => [...surface.querySelectorAll('[aria-label]')].find(node => node.getAttribute("aria-label") === label && node.style.display !== "none"));
const pagedText = panel => {
  const initial = Number(panel.querySelector('[aria-label="Page navigation"]').dataset.fyPage || 0), text = [];
  firstPage(panel);
  for (let turns = 0; turns < 1024; turns++) {
    text.push(panel.querySelector('[role="tabpanel"]').textContent);
    const next = pageButton(panel, "Next");
    if (!next || next.getAttribute("aria-disabled") !== "false") break;
    next.click();
  }
  firstPage(panel);
  for (let turn = 0; turn < initial; turn++) pageButton(panel, "Next").click();
  return panel.firstElementChild.textContent + text.join("");
};
module.exports.pagedControl = pagedControl;
module.exports.pagedText = pagedText;
