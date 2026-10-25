(() => {
  const MODES = ["eco", "normal", "boost"];
  const state = { enabled: true, level: 50, mode: "normal", job: null, file: null, note: "" };
  let api;
  let view = null;
  let badge = null;
  let details = null;
  let ready = null;

  const ask = (method, arg) => api.call(method, arg).then(r => r || {});
  const el = (css, text) => {
    const e = Framey.el(css, text);
    e.dataset.fy = api.id;
    return e;
  };
  const gap = e => {
    e.style.marginTop = "12px";
    return e;
  };

  const paint = force => {
    if (!view || !(force || api.visible)) return;
    view.note.textContent = state.note;
    view.job.textContent = state.job ? api.t("job.progress", state.job) : api.t("job.idle");
    view.file.textContent = state.file ? api.t("file.picked", { name: state.file }) : api.t("file.none");
    view.seen.textContent = api.visible ? api.t("visible.yes") : api.t("visible.no");
  };

  const say = text => {
    state.note = text;
    paint(true);
    api.timeout(() => {
      if (state.note !== text) return;
      state.note = "";
      paint(true);
    }, 3000);
  };

  const save = () =>
    ask("set", { enabled: state.enabled, level: state.level, mode: state.mode }).then(r => {
      if (!r.error) api.log("info", "saved level " + state.level);
      say(r.error ? api.t("save.failed", { reason: r.error }) : api.t("saved"));
    });

  const start = () =>
    ask("start").then(r => {
      if (r.error) say(api.t("job.failed", { reason: r.error }));
    });

  const fetchDemo = () =>
    ask("fetch_demo").then(r => say(r.error ? api.t("fetch.failed", { reason: r.error }) : api.t("fetch.result", r)));

  const choose = () =>
    api.pickFile({ title: api.t("file.choose") }).then(r => {
      state.file = r ? r.name : null;
      paint(true);
    });

  const showBadge = on => {
    if (badge) badge.unmount();
    badge = null;
    if (!on) return;
    const m = api.global.mount("badge", {
      zone: "corner",
      render: slot => slot.append(el("background:#101018;color:#fff;border-radius:12px;padding:8px 14px;font:18px sans-serif", api.t("badge.text"))),
    });
    if (!m.error) badge = m;
  };

  const apply = r => {
    if (r.error) return;
    if (typeof r.enabled === "boolean") state.enabled = r.enabled;
    if (typeof r.level === "number") state.level = r.level;
    if (MODES.includes(r.mode)) state.mode = r.mode;
  };

  const setup = () => {
    api.events.on("progress", p => {
      state.job = { step: (p && p.step) | 0, total: (p && p.total) | 0 };
      paint();
    });
    api.events.on("done", p => {
      state.job = null;
      paint();
      api.notify({
        title: api.t("done.title"),
        body: api.t("done.body", { count: (p && p.steps) | 0 }),
        actions: [{ id: "again", label: api.t("done.again") }, { id: "open:modules", label: api.t("done.modules") }],
        onaction: id => {
          if (id === "again") start();
        },
      });
    });
    api.onVisible(() => paint(true));
    details = api.pages.register({
      id: "starter-details",
      title: api.t("page.title"),
      mode: "full",
      render: box => box.append(el("font-size:26px;font-weight:700", api.t("page.title")), el("font-size:20px;margin-top:10px", api.t("page.body"))),
    });
  };

  const boot = () => {
    if (!ready) {
      const langs = api.lang === "en" ? ["en"] : ["en", api.lang];
      ready = Promise.all(langs.map(l => api.i18n.load(l)).concat(ask("get"))).then(done => {
        apply(done[done.length - 1]);
        setup();
      });
    }
    return ready;
  };

  const draw = box => {
    const ui = api.ui;
    view = {
      note: el("font-size:19px;color:#8a8aa0;min-height:26px;margin-top:12px", ""),
      job: el("font-size:20px;margin-top:12px", ""),
      file: el("font-size:18px;color:#8a8aa0;margin-top:6px;overflow-wrap:anywhere", ""),
      seen: el("font-size:16px;color:#8a8aa0;margin-top:6px", ""),
    };
    box.append(
      el("font-size:28px;font-weight:700", api.t("title")),
      gap(ui.toggle({ label: api.t("enabled"), value: state.enabled, onchange: v => (state.enabled = v) })),
      gap(ui.slider({ label: api.t("level"), min: 0, max: 100, step: 5, value: state.level, oninput: v => (state.level = v), format: v => v + "%" })),
      gap(ui.list({ items: MODES.map(id => ({ id, label: api.t("mode." + id) })), selected: state.mode, onselect: v => (state.mode = v) })),
      gap(ui.toggle({ label: api.t("badge.show"), value: !!badge, onchange: showBadge })),
      gap(ui.group(ui.button({ label: api.t("save"), primary: true, onclick: save }), ui.button({ label: api.t("job.start"), onclick: start }), ui.button({ label: api.t("fetch.run"), onclick: fetchDemo }))),
      gap(ui.group(ui.button({ label: api.t("file.choose"), onclick: choose }), ui.button({ label: api.t("page.open"), onclick: () => details && details.open && details.open() }))),
      view.note,
      view.job,
      view.file,
      view.seen,
    );
    paint(true);
  };

  api = Framey.register({ render: box => boot().then(() => draw(box)) });
  api.cleanup(() => {
    view = badge = details = null;
  });
})();
