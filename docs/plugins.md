# Writing a Framy plugin

This page is for plugin authors. For what Framy is, see the [README](../README.md). Identifiers keep the spelling `framey` (the JavaScript object is `Framey`).

## Layout

A plugin is a folder inside the loader's `plugins/` directory. The folder name is the plugin id. A symlinked folder works too, which is how Fan Control is linked in.

```
plugins/<id>/
  plugin.json   required
  main.js       the panel page (JavaScript)
  backend.py    optional Python backend
```

An id uses lowercase letters, digits, `-` and `_`, up to 32 characters. The Framy App refuses a package that has neither `main.js` nor `backend.py`.

## plugin.json

```json
{"id": "hello", "name": "Hello", "version": "0.1.0", "short": "Hi"}
```

| Field | Use |
|---|---|
| `id` | Required for zip installs, where it becomes the folder name. For a folder you place yourself, the folder name is the id. |
| `name` | Title shown in lists. |
| `version` | Optional, shown in Settings and Store. |
| `short` | Optional label for the side menu. Defaults to the first word of `name`. |

## main.js

`main.js` runs inside Steam's interface when the panel is loaded. Register a page:

```js
Framey.register({
  render(box) {
    box.textContent = "Hello from a plugin";
  },
});
```

`render(box)` is called each time the tab is opened. `box` is an empty element to fill. A plugin can also set `name` or `short` in the object to override `plugin.json`.

Available helpers:

| Helper | Use |
|---|---|
| `Framey.call(id, method, arg)` | Calls your backend and returns a Promise with the JSON result. |
| `Framey.el(css, text)` | Creates a `div` with inline CSS and optional text. |
| `Framey.button(text, css, onclick)` | Creates a centered, tappable button. |
| `Framey.toast(text, ms)` | Shows a short message over the page. |

Range inputs with the class `fy-r` get Framy's large slider styling. Keep controls large: the panel is used with a laser pointer. Keyboard shortcuts did not reach the page when tested on the headset, so do not rely on them.

If `main.js` throws while loading, the loader shows "Module <id> failed" and carries on. If `render` throws, the tab shows the error.

## backend.py

An optional module with one function:

```python
async def call(method, arg):
    return {"ok": True}
```

The result must be JSON-serializable. Exceptions are returned to the page as `{"error": "..."}`. The backend runs inside the loader's Python process with the user's permissions, so keep it small and do not block. Backends load when the loader starts or reloads, and are skipped for disabled plugins.

## Installing a plugin

- **Linked or copied folder:** place or link the folder in `plugins/`, then reload from the Settings tab or restart `framey.service`.
- **Zip:** an HTTPS link to a `.zip` with `plugin.json` at its top level (or inside a single top folder). `plugin.json` needs an `id`. The Framy App can also install a zip file or a GitHub link and does this for you.
- **Store:** put a folder with `plugin.json` into the loader's `store/<id>/` directory and install it from the Store tab.

The Settings tab stores which plugins you turned off in `~/.config/framey/settings.json`.

## Security

Plugins run code on the headset, in Steam's interface and as the user. Only install plugins you trust. A plugin that needs root access has to ship and explain its own installation step, as Fan Control does.
