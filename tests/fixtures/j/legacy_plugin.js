Framey.register({
  render(box) {
    box.append(Framey.el("", "Temperature sensor unavailable"));
    Framey.call("fan", "get");
  },
});
