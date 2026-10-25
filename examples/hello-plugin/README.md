# Hello plugin

A small, build-free Framey plugin: an accessible level slider saved with `framey_api.settings`, a greeting call, and a deliberately slow call that exercises timeout handling. It declares API generation 1, Framey 1.0.2 or newer, and both Linux ARM64 and x86-64 because it has no native dependencies.

Copy this directory to `~/framey/plugins/hello` and choose **Reload modules** in Framey Settings. Rename the folder and manifest `id` together when making your own plugin. Change `name`, `short`, and `version` too. Add `source` only after you have a real GitHub repository with this plugin at its root; local development needs no update source.

The backend rejects malformed arguments and keeps settings in the loader's per-plugin config directory. It does not hardcode the `hello` identity or store user data in the replaceable plugin directory. The page uses `api.ui` controls and checks a render generation before applying delayed results. Cleanup invalidates pending UI updates when the plugin is disabled or reloaded.

The 30-second call intentionally exceeds the manifest's 15-second timeout. Try it to see an error reply; use `await` for waits so the worker can cancel them. `api.call` resolves with the result or `{error}`. Backend arguments must still be validated even when the page normally sends a valid value.

Follow the [developer quickstart](../../docs/developer-quickstart.md) for local checks and packaging, and the [plugin reference](../../docs/plugins.md) for lifecycle and compatibility details. No Node.js build or dependency installation is required to run this example.
