# Hello plugin

A tiny Framey plugin: a slider whose value is saved by the backend, a greeting call, and a deliberately slow call that shows the backend timeout. It needs Framey 1.0.2 or newer.

Install it by copying this folder to `~/framey/plugins/hello` on the headset and tapping Reload modules in Settings. The backend keeps the slider value in `~/.config/hello/settings.json`, a fixed path that does not follow the id; change it too if you rename the plugin, or use `framey_api.settings` as the starter does. Change `id`, `name` and `source` in `plugin.json` for your own plugin. See `docs/plugins.md` for the manifest fields, cleanup, updates and compatibility.
