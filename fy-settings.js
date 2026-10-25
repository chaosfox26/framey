(() => {
  const FY = __FY;
  const K = FY && FY._k && FY._k(typeof __fyt === "string" ? __fyt : "");
  if (!K || !K.kit || K.settingsReady) return;
  K.settingsReady = true;
  const { T, call, tx, put, col, row, say, warn, obj, day, make, dmg, ui, GREY, GOOD, BAD, UP, WARN, TITLE, DANGER } = K.kit;

  const settings = {
    render(box) {
      const k = make(box);
      const { btn, sw } = k;
      let ld = {}, st = { prefs: {} }, auto = {}, mp = {}, ss = null, lres = null, cand = null, info = null, line = null;
      k.names = id => (id === "_loader" ? T("s.framey") : "");
      const draw = () =>
        Promise.all([call("loader"), call("state_get"), call("autocheck_status"), call("maint_prefs"), call("status")]).then(([l, s, a, m, x]) => {
          ld = l;
          st = s;
          auto = a;
          mp = m;
          ss = obj(x).settings_state;
          home();
        }).catch(k.bad);
      const back = () => draw();
      k.bad = e => k.show(tx(TITLE, T("s.title")), col(BAD, T("c.failed", { msg: String((e && e.message) || T("c.noanswer")).slice(0, 200) })), row(btn(T("c.retry"), draw)));
      const pref = (label, key, value, after) =>
        sw(label, value, (v, el) =>
          call("prefs_set", { [key]: v }).then(r => {
            if (r.error) {
              el.value = !v;
              warn(r.error);
              return;
            }
            if (r.prefs) st = r;
            if (after) after();
          }),
        );
      const check = el => {
        k.hold(true);
        el.textContent = T("c.checking");
        el.style.color = GREY;
        call("loader_check").then(r => {
          k.hold(false);
          lres = r.loader || null;
          cand = r.candidate || (lres && lres.candidate) || null;
          info = r.installed || null;
          line = lres ? null : T("c.failed", { msg: r.error || T("c.noanswer") });
          home();
        });
      };
      const loaderUpdate = confirm =>
        k.work(T("s.updating"), () => call("loader_update", { confirm }), (r, out) => {
          if (r.confirm) return k.ask(T("s.downgrade_title"), r.confirm, [[T("s.update_now"), () => loaderUpdate(true), DANGER]], back);
          if (r.current) {
            lres = { state: "current" };
            return k.result(out, {}, T("s.already"), back);
          }
          if (r.ok) {
            try { sessionStorage.fyMsg = T("s.updated"); } catch (e) {}
            const b = k.btn(T("c.back"), back);
            put(out, col(GOOD, T("s.restarting"), 21, true), row(b));
            b.focus();
            return out;
          }
          k.result(out, r, "", back);
        });
      const identity = c => [T("s.candidate", { tag: c.tag, c: String(c.commit || "").slice(0, 7) }), day(c.published) && T("s.published", { d: day(c.published) })].filter(Boolean).join(", ");
      const updateAsk = () =>
        k.ask(
          T("s.update_title"),
          [cand ? identity(cand) : T("s.update_nocheck"), lres && lres.downgrade && T("v.limits"), lres && lres.notes && T("s.notes_head") + "\n" + lres.notes].filter(Boolean).join("\n\n"),
          [[T("s.update_now"), () => loaderUpdate(false), { primary: true }]],
          back,
        );
      const lazyDev = () => {
        k.hold(true);
        FY.lazy("dev").then(
          () => {
            k.hold(false);
            if (FY.dev && FY.open) FY.open("#developer");
            else warn("Restart Framey to open Developer tools.");
          },
          e => {
            k.hold(false);
            warn(e.message);
          },
        );
      };
      const maintPref = key => (v, el) =>
        call("maint_prefs", { [key]: v }).then(r => {
          if (r.error) {
            if (el) el.value = !v;
            warn(r.error);
          } else mp = r;
        });
      const maintBox = () =>
        mp.error || typeof mp["maint.window_days"] !== "number"
          ? [col(GREY, T("mt.unavailable"), 16)]
          : [
              sw(T("mt.bg"), mp["maint.enabled_background"] === true, maintPref("maint.enabled_background")),
              col(GREY, T("mt.bg_text"), 16),
              ui().slider({ label: T("mt.window_label"), min: 30, max: 1825, step: 5, value: mp["maint.window_days"], format: v => T("mt.days", { count: v }), onchange: v => maintPref("maint.window_days")(v) }),
            ];
      const finish = (method, title, okKey) => k.work(title, () => call(method), (r, out) => k.result(out, r, T(okKey), back));
      const home = () => {
        const p = obj(st.prefs);
        const dev = p.dev && p.dev.enabled === true;
        const rv = (window.FY_RUNTIME || {}).version;
        const iv = (info && info.version) || rv;
        const c = tx("background:#181826;border-radius:14px;padding:12px;margin-bottom:12px");
        const sum = col(GREY, "", 19);
        const state = lres && lres.state;
        const v = line ? [line, BAD]
          : state === "current" ? [T("s.current"), GOOD]
          : state === "update" ? (lres.downgrade ? [T("s.older"), WARN] : [T("s.update_avail"), UP])
          : state === "error" ? [T("s.check_failed", { msg: lres.msg }), BAD]
          : ["", GREY];
        sum.textContent = v[0];
        sum.style.color = v[1];
        put(
          c,
          tx("font-size:22px;font-weight:700", T("s.framey")),
          col(GREY, iv ? T("s.installed", { v: iv, c: ld.commit || T("c.unknown") }) : T("s.installed_c", { c: ld.commit || T("c.unknown") })),
          col("#fff", cand ? identity(cand) : T("s.not_checked"), 18),
          cand && cand.prerelease && col(WARN, cand.label || T("s.prerelease"), 18),
          sum,
          col(GREY, T("s.ch_ro", { channel: p.channel === "prerelease" ? "prerelease" : "stable" }), 16),
          row(btn(T("s.check"), () => check(sum), { primary: true }), btn(T("s.update"), updateAsk, { primary: state === "update" })),
          row(
            btn(T("s.notes"), () => k.notes("_loader", cand.tag, T("s.notes_title", { tag: cand.tag }), home), { disabled: !cand }),
            btn(T("s.history"), () => k.versions("_loader", T("s.history"), back)),
          ),
          ld.kept && col("#fff", T("s.kept"), 19, true),
          ld.kept && row(btn(T("s.done"), () => finish("loader_done", T("s.working"), "s.done_ok")), btn(T("s.rollback"), () => k.ask(T("s.rollback_title"), T("s.rollback_text"), [[T("s.rollback"), () => finish("loader_roll", T("s.rolling"), "s.rolled"), DANGER]], back))),
        );
        k.show(
          tx(TITLE, T("s.title")),
          dmg(k, ss, back),
          c,
          pref(T("s.auto"), "autocheck", p.autocheck === true, draw),
          col(GREY, auto.enabled ? (auto.last ? T("s.auto_last", { d: new Date(auto.last * 1000).toLocaleString() }) : T("s.auto_never")) : T("s.auto_off"), 16),
          pref(T("s.notify_loader"), "notify_loader", p.notify_loader === true),
          pref(T("s.notify_plugins"), "notify_plugins", p.notify_plugins === true),
          ...maintBox(),
          col(GREY, T("s.lang"), 16),
          col(GREY, T("s.safe_ro", { state: T(st.safe === true ? "s.on" : "s.off") }), 16),
          col(GREY, T("s.safe_text"), 16),
          col(GREY, T("s.dev_ro", { state: T(dev ? "s.on" : "s.off") }), 16),
          dev && row(btn(T("s.dev_open"), lazyDev), btn(T("s.diag"), lazyDev)),
          row(btn(T("s.reload"), () => { k.hold(true); call("reload").then(r => { k.hold(false); if (r.error) warn(r.error); else say(T("s.reloaded")); }); })),
          col(GREY, T("s.trust"), 18),
        );
      };
      draw();
    },
  };
  Object.defineProperty(FY.pages, "settings", { value: Object.freeze({ render: settings.render }), enumerable: true });
  Object.freeze(FY.pages);
})();
