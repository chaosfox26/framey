const assert = require("assert");
const { makeDom, registerAs, TOK } = require("./harness");

const node = (dirs, files, links) => ({ dirs: dirs || {}, files: files || [], links: links || [] });
const newTree = () => ({
  data: node({ Downloads: node({ old: node() }, ["a.png", "b.txt", "c.PNG"], ["lnk"]), Empty: node(), "<b>x</b>": node() }, ["readme.md"]),
  media: node({ usb: node({}, ["x.mp3"]) }),
});

const boot = (opts = {}) => {
  const spy = { timers: 0, listeners: 0 };
  const sim = { calls: null, tree: newTree(), errors: {}, delay: {}, raw: {}, extra: {} };
  const answer = a => {
    if (a.path in sim.raw) return sim.raw[a.path];
    if (a.path in sim.errors) return { error: sim.errors[a.path], code: "denied" };
    if (a.root === undefined) return { root: "", path: "", parent: null, entries: [], truncated: false, roots: Object.keys(sim.tree).map(id => ({ id, label: id })) };
    const name = a.root;
    const top = sim.tree[name];
    if (!top) return { error: "That location is not available", code: "bad_root" };
    const parts = a.path ? a.path.split("/") : [];
    let cur = top;
    for (const p of parts) {
      cur = cur.dirs[p];
      if (!cur) return { error: "That folder does not exist", code: "not_found" };
    }
    let entries = [
      ...Object.keys(cur.dirs).sort().map(n => ({ name: n, type: "dir", size: null, mtime: 1 })),
      ...cur.files.slice().sort().map(n => ({ name: n, type: "file", size: 5, mtime: 1 })),
      ...cur.links.map(n => ({ name: n, type: "link", size: null, mtime: 1 })),
    ];
    if (a.dirs_only) entries = entries.filter(e => e.type === "dir");
    if (a.exts) entries = entries.filter(e => e.type !== "file" || a.exts.includes(e.name.split(".").pop().toLowerCase()));
    const out = { root: name, path: a.path, parent: parts.length ? parts.slice(0, -1).join("/") : null, entries, truncated: !!sim.extra.truncated };
    if (!a.path) out.roots = Object.keys(sim.tree).map(id => ({ id, label: id }));
    return out;
  };
  const env = makeDom({
    loadFiles: ["fy-api.js"],
    before(w) {
      ["setTimeout", "setInterval", "requestAnimationFrame"].forEach(k => {
        const f = w[k];
        w[k] = (...a) => { spy.timers++; return f.apply(w, a); };
      });
      const log = w.fyCall;
      w.fyCall = s => {
        log(s);
        const m = JSON.parse(s);
        if (m.method !== "files_list") return;
        Promise.resolve(sim.delay[m.arg.path]).then(() => {
          const r = answer(m.arg);
          w.Framey._reply(m.gen, m.id, r === undefined ? undefined : JSON.parse(JSON.stringify(r)));
        });
      };
    },
  });
  sim.calls = env.calls;
  const w = env.window, d = env.document, FY = w.Framey, load = env.load;
  [d, w].forEach(t => {
    const f = t.addEventListener.bind(t);
    t.addEventListener = (...a) => { spy.listeners++; return f(...a); };
  });
  load("fy-ui.js");
  if (!opts.lazy) load("fy-picker.js");
  const api = env.plugin("t");
  if (opts.cleanup) {
    api.cleanups = [];
    api.cleanup = fn => api.cleanups.push(fn);
  }
  const key = (e, k) => {
    const ev = new w.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true });
    e.dispatchEvent(ev);
    return ev;
  };
  const view = () => d.querySelector('[role="dialog"]');
  const buttons = () => [...view().querySelectorAll('[role="button"]')];
  const button = label => buttons().find(b => b.textContent === label);
  const rows = () => [...view().querySelectorAll('[role="option"]')];
  const labels = () => rows().map(r => r.firstChild.textContent);
  const row = n => rows().find(r => r.firstChild.textContent === n);
  const errLine = () => view().querySelector('[role="alert"]');
  return { w, d, FY, api, sim, spy, clock: env.clock, load, key, view, buttons, button, rows, labels, row, errLine };
};

const tick = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 4; i++) await tick(); };
const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("folder picker: dialog, request shape, folders only, roots row, Select", async () => {
  const { api, sim, view, d, button, labels, errLine, rows } = boot();
  const p = api.pickFolder({ title: "Pick one" });
  await settle();
  assert.equal(view().getAttribute("aria-label"), "Pick one");
  assert.equal(view().dataset.fy, "t");
  assert.equal(sim.calls.length, 2);
  assert.equal(sim.calls[0].plugin, "_core");
  assert.equal(sim.calls[0].method, "files_list");
  assert.deepEqual(sim.calls[0].arg, { path: "", dirs_only: true, pid: "t" });
  assert.deepEqual(labels(), ["<b>x</b>", "Downloads", "Empty"].sort());
  assert.equal(view().querySelectorAll("b").length, 0);
  assert.equal(errLine().style.display, "none");
  assert.ok(view().textContent.includes("Pick one"));
  assert.equal(button("Up").getAttribute("aria-disabled"), "true");
  assert.equal(button("Select").getAttribute("aria-disabled"), "false");
  assert.ok(button("data") && button("media"));
  assert.equal(d.activeElement, rows()[0]);
  button("Select").click();
  assert.deepEqual(await p, { root: "data", path: "" });
  assert.equal(d.querySelector('[role="dialog"]'), null);
});

t("navigation: Enter descends, breadcrumb follows, Up climbs, Up disabled at the top", async () => {
  const { api, sim, view, d, key, button, row, labels, rows } = boot();
  const p = api.pickFolder({});
  await settle();
  key(row("Downloads"), "Enter");
  await settle();
  assert.equal(sim.calls[2].arg.path, "Downloads");
  assert.ok(view().textContent.includes("data / Downloads"));
  assert.deepEqual(labels(), ["old"]);
  assert.equal(button("Up").getAttribute("aria-disabled"), "false");
  assert.equal(d.activeElement, rows()[0]);
  rows()[0].click();
  await settle();
  assert.equal(sim.calls[3].arg.path, "Downloads/old");
  assert.ok(view().textContent.includes("data / Downloads / old"));
  assert.equal(labels().length, 0);
  assert.ok(view().textContent.includes("Nothing here"));
  button("Up").click();
  await settle();
  assert.equal(sim.calls[4].arg.path, "Downloads");
  button("Up").click();
  await settle();
  assert.equal(sim.calls[5].arg.path, "");
  assert.equal(button("Up").getAttribute("aria-disabled"), "true");
  key(row("Downloads"), "Enter");
  await settle();
  button("Select").click();
  assert.deepEqual(await p, { root: "data", path: "Downloads" });
});

t("start path: first request uses it and Up is available", async () => {
  const { api, sim, button } = boot();
  const p = api.pickFolder({ root: "data", path: "Downloads" });
  await settle();
  assert.deepEqual(sim.calls[1].arg, { root: "data", path: "Downloads", dirs_only: true, pid: "t" });
  assert.equal(button("Up").getAttribute("aria-disabled"), "false");
  button("Cancel").click();
  assert.equal(await p, null);
});

t("start: data root wins even when another root is listed first", async () => {
  const { api, sim, labels, button } = boot();
  sim.tree = { media: newTree().media, data: newTree().data };
  const p = api.pickFolder();
  await settle();
  assert.equal(sim.calls.length, 2);
  assert.equal(sim.calls[1].arg.root, "data");
  assert.deepEqual(labels(), ["<b>x</b>", "Downloads", "Empty"].sort());
  button("Cancel").click();
  assert.equal(await p, null);
});

t("first request has no root and gets roots only, then the chosen root is listed", async () => {
  const { api, sim, labels, button } = boot();
  const p = api.pickFile();
  await settle();
  assert.equal(sim.calls.length, 2);
  assert.equal(sim.calls[0].arg.root, undefined);
  assert.equal(sim.calls[1].arg.root, "data");
  assert.deepEqual(labels(), ["<b>x</b>", "Downloads", "Empty", "readme.md"].sort());
  button("Cancel").click();
  assert.equal(await p, null);
});

t("roots reply with data only: starts there, no roots row", async () => {
  const { api, sim, button, labels, view } = boot();
  sim.tree = { data: newTree().data };
  const p = api.pickFolder();
  await settle();
  assert.equal(sim.calls.length, 2);
  assert.equal(view().querySelectorAll('[role="button"]').length, 3);
  assert.equal(button("data"), undefined);
  assert.ok(view().textContent.includes("data"));
  assert.deepEqual(labels(), ["<b>x</b>", "Downloads", "Empty"].sort());
  button("Cancel").click();
  await p;
});

t("empty roots: readable message, nothing to pick, Select and Up off", async () => {
  const { api, sim, view, button, errLine, rows } = boot();
  sim.tree = {};
  const p = api.pickFolder();
  await settle();
  assert.equal(errLine().textContent, "No folders are available to this module. Add one in loader.json (file_roots).");
  assert.equal(errLine().style.display, "");
  assert.equal(rows().length, 0);
  assert.equal(button("Select").getAttribute("aria-disabled"), "true");
  assert.equal(button("Up").getAttribute("aria-disabled"), "true");
  assert.ok(view().textContent.includes("Nothing here"));
  button("Cancel").click();
  assert.equal(await p, null);
});

t("empty roots: a successful reply with no roots gets the same message", async () => {
  const { api, sim, errLine, button } = boot();
  sim.raw[""] = { root: "data", path: "", parent: null, entries: [], roots: [] };
  const p = api.pickFile();
  await settle();
  assert.equal(errLine().textContent, "No folders are available to this module. Add one in loader.json (file_roots).");
  assert.equal(button("Select").getAttribute("aria-disabled"), "true");
  button("Cancel").click();
  await p;
});

t("error reply to the first request shows its text, not the empty message", async () => {
  const { api, sim, errLine, button, rows } = boot();
  sim.raw[""] = { error: "You do not have permission to open the folders", code: "denied" };
  const p = api.pickFolder();
  await settle();
  assert.equal(errLine().textContent, "You do not have permission to open the folders");
  assert.equal(rows().length, 0);
  button("Cancel").click();
  await p;
});

t("hostile root names and labels render as plain text", async () => {
  const { api, sim, view, button } = boot();
  sim.tree = { data: newTree().data, "<i>evil</i>": node() };
  const p = api.pickFolder();
  await settle();
  assert.ok(button("<i>evil</i>"));
  assert.equal(view().querySelectorAll("i").length, 0);
  button("<i>evil</i>").click();
  await settle();
  assert.equal(view().querySelectorAll("i").length, 0);
  button("Cancel").click();
  await p;
});

t("file picker: ext filter, files choosable, links disabled, Select needs a file", async () => {
  const { api, sim, view, key, button, row, labels } = boot();
  const p = api.pickFile({ exts: ["PNG", "txt"] });
  await settle();
  assert.deepEqual(sim.calls[0].arg, { path: "", dirs_only: false, exts: ["png", "txt"], pid: "t" });
  assert.equal(button("Select").getAttribute("aria-disabled"), "true");
  assert.ok(view().textContent.includes("Choose a file"));
  assert.equal(labels().includes("readme.md"), false);
  key(row("Downloads"), "Enter");
  await settle();
  assert.deepEqual(labels(), ["old", "a.png", "b.txt", "c.PNG", "lnk"]);
  assert.equal(row("lnk").getAttribute("aria-disabled"), "true");
  row("lnk").click();
  assert.equal(button("Select").getAttribute("aria-disabled"), "true");
  assert.equal(sim.calls.length, 3);
  button("Select").click();
  assert.ok(view());
  row("a.png").click();
  assert.equal(row("a.png").getAttribute("aria-selected"), "true");
  assert.equal(button("Select").getAttribute("aria-disabled"), "false");
  assert.equal(sim.calls.length, 3);
  button("Up").click();
  await settle();
  assert.equal(button("Select").getAttribute("aria-disabled"), "true");
  key(row("Downloads"), "Enter");
  await settle();
  key(row("b.txt"), " ");
  button("Select").click();
  assert.deepEqual(await p, { root: "data", path: "Downloads/b.txt", name: "b.txt" });
});

t("file picker: a file at the top level has no path prefix", async () => {
  const { api, row, button } = boot();
  const p = api.pickFile();
  await settle();
  row("readme.md").click();
  button("Select").click();
  assert.deepEqual(await p, { root: "data", path: "readme.md", name: "readme.md" });
});

t("cancel button and Escape resolve null, remove the modal and restore focus; picker reusable", async () => {
  const { api, d, view, button, key } = boot();
  const opener = d.body.appendChild(d.createElement("button"));
  opener.focus();
  let p = api.pickFolder();
  await settle();
  assert.notEqual(d.activeElement, opener);
  button("Cancel").click();
  assert.equal(await p, null);
  assert.equal(d.querySelector('[role="dialog"]'), null);
  assert.equal(d.activeElement, opener);
  p = api.pickFile();
  await settle();
  assert.ok(key(view(), "Escape"));
  assert.equal(await p, null);
  assert.equal(d.querySelector('[role="dialog"]'), null);
  p = api.pickFolder();
  await settle();
  assert.ok(view());
  button("Cancel").click();
  assert.equal(await p, null);
});

t("error line: readable text, list kept, cleared by the next success", async () => {
  const { api, sim, view, row, button, labels, errLine, d, key } = boot();
  sim.errors.Empty = "You do not have permission to open that folder";
  const p = api.pickFolder();
  await settle();
  row("Empty").focus();
  key(row("Empty"), "Enter");
  await settle();
  assert.equal(errLine().textContent, "You do not have permission to open that folder");
  assert.equal(errLine().style.display, "");
  assert.equal(errLine().getAttribute("role"), "alert");
  assert.deepEqual(labels(), ["<b>x</b>", "Downloads", "Empty"].sort());
  assert.ok(view().textContent.includes("data"));
  assert.equal(d.activeElement, row("Empty"));
  row("Downloads").click();
  await settle();
  assert.equal(errLine().textContent, "");
  assert.equal(errLine().style.display, "none");
  assert.deepEqual(labels(), ["old"]);
  button("Cancel").click();
  await p;
});

t("error line: first listing fails, malformed replies, focus stays inside", async () => {
  const { api, sim, view, button, errLine, rows, d } = boot();
  let p = api.pickFolder({ path: "nope" });
  await settle();
  assert.equal(errLine().textContent, "That folder does not exist");
  assert.equal(rows().length, 0);
  assert.ok(view().contains(d.activeElement));
  assert.equal(button("Up").getAttribute("aria-disabled"), "true");
  button("Cancel").click();
  await p;
  sim.raw["junk"] = null;
  p = api.pickFolder({ path: "junk" });
  await settle();
  assert.equal(errLine().textContent, "Could not read this folder.");
  button("Cancel").click();
  await p;
  sim.raw["odd"] = { entries: "no" };
  p = api.pickFolder({ path: "odd" });
  await settle();
  assert.equal(errLine().textContent, "Could not read this folder.");
  button("Cancel").click();
  await p;
  sim.raw["long"] = { error: "x".repeat(900) };
  p = api.pickFolder({ path: "long" });
  await settle();
  assert.equal(errLine().textContent.length, 300);
  button("Cancel").click();
  await p;
});

t("dispose resolves null and removes the modal (real api.cleanup)", async () => {
  const { api, FY, d } = boot();
  const p = api.pickFolder();
  await settle();
  assert.ok(d.querySelector('[role="dialog"]'));
  FY.dispose("t", TOK);
  assert.equal(await p, null);
  assert.equal(d.querySelector('[role="dialog"]'), null);
  FY.dispose("t", TOK);
});

t("no api.cleanup: dispose still resolves null through api.on", async () => {
  const { api, FY, d } = boot();
  api.cleanup = null;
  const p = api.pickFolder();
  await settle();
  FY.dispose("t", TOK);
  assert.equal(await p, null);
  assert.equal(d.querySelector('[role="dialog"]'), null);
});

t("pick on a disposed module rejects with a clear error and opens no dialog", async () => {
  const { api, FY, d } = boot();
  FY.dispose("t", TOK);
  await assert.rejects(api.pickFile(), /module disposed/);
  assert.equal(d.querySelector('[role="dialog"]'), null);
});

t("dispose resolves null through api.cleanup when present, once", async () => {
  const { api, d, view } = boot({ cleanup: true });
  const p = api.pickFile();
  await settle();
  assert.equal(api.cleanups.length, 1);
  api.cleanups.forEach(f => f());
  assert.equal(await p, null);
  assert.equal(d.querySelector('[role="dialog"]'), null);
  api.cleanups.forEach(f => f());
  assert.equal(view(), null);
});

t("dispose while a listing is pending: no throw, nothing redrawn, picker free again", async () => {
  const { api, FY, sim, d } = boot();
  let go;
  sim.delay[""] = new Promise(r => (go = r));
  const p = api.pickFolder();
  await settle();
  FY.dispose("t", TOK);
  assert.equal(await p, null);
  go();
  await settle();
  assert.equal(d.querySelector('[role="dialog"]'), null);
  const b = registerAs(FY, { id: "b" });
  sim.delay = {};
  const q = b.pickFolder();
  await settle();
  assert.ok(d.querySelector('[role="dialog"]'));
  d.querySelector('[role="dialog"]').dispatchEvent(new d.defaultView.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(await q, null);
});

t("one picker at a time: a second request resolves null without a second dialog", async () => {
  const { api, FY, d, button } = boot();
  const p = api.pickFolder();
  await settle();
  const other = registerAs(FY, { id: "o" });
  assert.equal(await other.pickFile(), null);
  assert.equal(await api.pickFolder(), null);
  assert.equal(d.querySelectorAll('[role="dialog"]').length, 1);
  button("Cancel").click();
  assert.equal(await p, null);
});

t("roots: buttons switch location, current one highlighted", async () => {
  const { api, sim, button, labels, view } = boot();
  const p = api.pickFolder();
  await settle();
  assert.ok(button("data").className.includes("fy-p"));
  assert.ok(!button("media").className.includes("fy-p"));
  button("media").click();
  await settle();
  assert.deepEqual(sim.calls[2].arg, { root: "media", path: "", dirs_only: true, pid: "t" });
  assert.ok(button("media").className.includes("fy-p"));
  assert.ok(!button("data").className.includes("fy-p"));
  assert.deepEqual(labels(), ["usb"]);
  assert.ok(view().textContent.includes("media"));
  const usb = [...view().querySelectorAll('[role="option"]')][0];
  usb.click();
  await settle();
  button("Select").click();
  assert.deepEqual(await p, { root: "media", path: "usb" });
});

t("roots: object-shaped roots and a single root are handled", async () => {
  const { api, sim, button, view } = boot();
  sim.raw[""] = { root: "data", path: "", parent: null, entries: [], roots: [{ root: "data", label: "Home" }, { id: "media", name: "USB" }] };
  let p = api.pickFolder();
  await settle();
  assert.ok(button("Home") && button("USB"));
  button("Cancel").click();
  await p;
  sim.raw[""] = { root: "data", path: "", parent: null, entries: [], roots: ["data"] };
  p = api.pickFolder();
  await settle();
  assert.equal(button("data"), undefined);
  assert.equal(view().querySelectorAll('[role="button"]').length, 3);
  button("Cancel").click();
  await p;
});

t("stale reply is ignored", async () => {
  const { api, sim, button, labels, row, errLine } = boot();
  const p = api.pickFolder();
  await settle();
  let go;
  sim.delay.Downloads = new Promise(r => (go = r));
  row("Downloads").click();
  await settle();
  button("media").click();
  await settle();
  assert.deepEqual(labels(), ["usb"]);
  go();
  await settle();
  assert.deepEqual(labels(), ["usb"]);
  assert.equal(errLine().style.display, "none");
  button("Select").click();
  assert.deepEqual(await p, { root: "media", path: "" });
});

t("keyboard chain: list top to roots, list bottom to footer, footer sideways", async () => {
  const { api, d, key, rows, button } = boot();
  const p = api.pickFolder();
  await settle();
  const r = rows();
  assert.equal(d.activeElement, r[0]);
  key(r[0], "ArrowDown");
  key(r[1], "ArrowDown");
  assert.equal(d.activeElement, r[2]);
  key(r[2], "ArrowDown");
  assert.equal(d.activeElement, button("Select"));
  key(button("Select"), "ArrowRight");
  assert.equal(d.activeElement, button("Cancel"));
  key(button("Cancel"), "ArrowLeft");
  assert.equal(d.activeElement, button("Select"));
  key(button("Select"), "ArrowUp");
  assert.equal(d.activeElement, r[2]);
  key(r[2], "ArrowUp");
  key(r[1], "ArrowUp");
  key(r[0], "ArrowUp");
  assert.equal(d.activeElement, button("data"));
  key(button("data"), "ArrowRight");
  assert.equal(d.activeElement, button("media"));
  key(button("media"), "ArrowDown");
  assert.equal(d.activeElement, r[0]);
  button("Cancel").click();
  await p;
});

t("list cap and truncation note", async () => {
  const { api, sim, rows, view, button } = boot();
  sim.extra.truncated = true;
  sim.tree.data.dirs = {};
  for (let i = 0; i < 600; i++) sim.tree.data.dirs["d" + i] = node();
  const p = api.pickFolder();
  await settle();
  assert.equal(rows().length, 500);
  assert.ok(view().textContent.includes("(list shortened)"));
  button("Cancel").click();
  await p;
});

t("no timers, no document or window listeners across a full flow", async () => {
  const { api, spy, clock, key, row, button } = boot();
  const base = { listeners: spy.listeners };
  const p = api.pickFile({ exts: ["png"] });
  await settle();
  key(row("Downloads"), "Enter");
  await settle();
  row("a.png").click();
  button("Up").click();
  await settle();
  button("Cancel").click();
  await p;
  assert.equal(clock.pending(), 0);
  assert.equal(spy.listeners, base.listeners);
});

t("lazy load: a plugin bound before the picker gets pickFile and pickFolder, second load is a no-op", async () => {
  const { w, api, load } = boot({ lazy: true });
  assert.equal(api.pickFile, undefined);
  load("fy-picker.js");
  load("fy-picker.js");
  assert.equal(typeof api.pickFile, "function");
  assert.equal(typeof api.pickFolder, "function");
  assert.equal(w.Framey.pickerReady, true);
});

t("each dismissed pick releases its cleanup registration: 300 picks in a row all resolve null", async () => {
  const { api, button } = boot();
  assert.equal(typeof api.cleanup, "function", "the real plugin api, not the fallback");
  for (let i = 0; i < 300; i++) {
    const p = api.pickFile({ exts: ["png"] });
    await settle();
    button("Cancel").click();
    assert.strictEqual(await p, null, "pick " + (i + 1));
  }
});

t("no K.ready hook call is left in fy-picker.js", () => {
  const fs = require("fs"), path = require("path");
  assert.ok(!/K\.ready/.test(fs.readFileSync(path.join(require("./harness").ROOT, "fy-picker.js"), "utf8")));
});

(async () => {
  let pass = 0, fail = 0;
  for (const [name, fn] of tests) {
    try { await fn(); pass++; console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n" + (e.stack || e)); }
  }
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
