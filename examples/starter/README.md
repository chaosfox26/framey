# Starter plugin

A small Framey plugin that demonstrates the main API generation 1 workflows: registration, backend calls and events, settings, logging, controller-friendly controls, notifications with actions, visibility, translations, the file picker, pages, a global component and cleanup. It uses only the platform APIs; it needs Framey 1.0.2 or newer, as `framey.min` in `plugin.json` says.

| File | What it shows |
|---|---|
| `plugin.json` | `api: 1`, backend limits, `data_version`, default language |
| `main.js` | `Framey.register`, `api.ui.*`, `api.events`, `api.notify`, `api.onVisible`, `api.t`, `api.pickFile`, `api.pages`, `api.global`, `api.cleanup` |
| `backend.py` | `call`, `startup`, `migrate`, `unload`, `uninstall`, `framey_api.settings`, `.log`, `.events`, `.fetch`, `.migrate` |
| `locales/en.json` | strings for `api.t`, including plural keys |

## Make your own

1. Copy this folder to `~/framey/plugins/<your-id>` on the headset.
2. In `plugin.json` change `id` (lowercase letters, digits, `-`, `_`), `name`, `short` and `version`. Keep the folder and manifest id equal. Add `source` only when you have an actual GitHub update repository with this plugin at its root.
3. Rename `starter-details` in `main.js` to a page id of your own.
4. Replace the strings in `locales/en.json`; every `api.t("key")` in `main.js` needs a key there.
5. Do setup that must happen once (event subscriptions, pages) outside `render`: `render` runs again each time the tab is shown.
6. Create controls with `api.ui`, not plain `div`s, so they take focus and respond to Enter, Space and the arrow keys.
7. Register timers, listeners and anything else you create through `api` (`api.timeout`, `api.on`, `api.cleanup`) so Framey removes them when the module reloads or is disabled. Guard delayed UI work with a disposed flag and render generation, as this example does.
8. In `backend.py` return `{"error": "..."}` for problems you expect and validate every argument; the page is untrusted input.
9. Keep user data in `framey_api.settings` and `framey_api.dirs`, not inside the plugin folder (updates replace the folder). Change `data_version` and handle it in `migrate(ctx)` when the stored data changes shape.
10. Press Reload modules in Settings after each edit. With Developer mode on you can switch on hot reload for your module in the Developer page instead.

## Notes

- The backend runs in its own process and starts on the first call. That is failure containment, not a security sandbox: it runs as the same user and can read that user's files.
- `fetch_demo` fetches `https://example.com/`; change `URL` to your own address. Plain `http` is allowed only in developer mode; private and loopback addresses are still refused.
- `api.pickFile` is available to every module without setup; `choose` calls it directly. The chooser offers only the plugin's data folder and the `file_roots` in `~/.config/framey/loader.json`.
- Whether the Steam controller moves focus to these controls is not verified on a headset; keyboard arrows, Enter and Space are what is tested.

See the [developer quickstart](../../docs/developer-quickstart.md) for test commands, archive packaging, and Decky source-port guidance. The example runs as plain JavaScript and Python; Node.js is optional browser-test tooling. `api.visible` follows panel/document visibility and does not identify the selected plugin tab.
