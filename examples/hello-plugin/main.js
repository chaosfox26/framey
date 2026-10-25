let disposed = false;
let renderVersion = 0;

const api = Framey.register({
  render(box, api) {
    const version = ++renderVersion;
    const current = () => !disposed && version === renderVersion;
    const note = Framey.el("font-size:20px;color:#8a8aa0;margin-top:12px;min-height:28px", "");
    let noteVersion = 0;
    const say = text => {
      if (!current()) return;
      const revision = ++noteVersion;
      note.textContent = text;
      api.timeout(() => {
        if (current() && revision === noteVersion) note.textContent = "";
      }, 3000);
    };
    const slider = api.ui.slider({
      label: "Level", min: 0, max: 100, step: 5, value: 50, format: v => v + "%",
      onchange: value => api.call("set", { level: value }).then(r => say(r && r.error ? "Could not save: " + r.error : "Saved")),
    });
    box.append(
      Framey.el("font-size:28px;font-weight:700", "Hello"),
      slider,
      api.ui.button({ label: "Greet", primary: true, onclick: () => api.call("greet", "Frame").then(r => say(r && (r.error || r.text) || "No greeting returned")) }),
      api.ui.button({ label: "Slow call (30 s)", onclick: () => api.call("slow", 30).then(r => say(r && r.error || "Done")) }),
      note,
    );
    api.call("get").then(r => {
      if (!current()) return;
      if (r && r.error) say("Could not load: " + r.error);
      else if (r && Number.isInteger(r.level) && r.level >= 0 && r.level <= 100) slider.value = r.level;
    });
  },
});
api.cleanup(() => {
  disposed = true;
  renderVersion++;
});
