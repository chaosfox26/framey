let note;
Framey.register({
  render(box, api) {
    note = Framey.el("font-size:20px;color:#8a8aa0;margin-top:12px", "");
    const say = text => {
      note.textContent = text;
      api.timeout(() => (note.textContent = ""), 3000);
    };
    const level = Framey.el("font-size:22px;margin-top:12px", "Level: ...");
    const slider = document.createElement("input");
    slider.type = "range";
    slider.className = "fy-r";
    slider.min = 0;
    slider.max = 100;
    slider.style.cssText = "width:100%;margin:16px 0";
    api.on(slider, "input", () => (level.textContent = "Level: " + slider.value));
    api.on(slider, "change", () => api.call("set", { level: Number(slider.value) }).then(r => say(r.error ? "Could not save: " + r.error : "Saved")));
    box.append(
      Framey.el("font-size:28px;font-weight:700", "Hello"),
      level,
      slider,
      Framey.button("Greet", "height:60px;background:#5585ff;margin-top:8px", () => api.call("greet", "Frame").then(r => say(r.error || r.text))),
      Framey.button("Slow call (30 s)", "height:60px;margin-top:10px", () => api.call("slow", 30).then(r => say(r.error || "Done"))),
      note,
    );
    api.call("get").then(r => {
      const v = r && typeof r.level === "number" ? r.level : 50;
      slider.value = v;
      level.textContent = "Level: " + v;
    });
  },
  dispose() {
    note = null;
  },
});
