# Writing a Framey plugin

This page is for plugin authors. For what Framey is, see the [README](../README.md). The panel's own wording calls plugins "modules".

## Layout

A plugin is a folder inside the loader's `plugins/` directory (create it if it does not exist). The folder name is the plugin id. A symlinked folder works too, which is how Fan Control is linked in.

```
plugins/<id>/
  plugin.json   required
  main.js       the panel page (JavaScript)
  backend.py    optional Python backend
```

An id uses lowercase letters, digits, `-` and `_`, 1 to 32 characters. A folder with any other name is skipped without a log line, and so are the hidden `.stage-` and `.old-` folders used during installs. `_core` is reserved: calls to that name go to the loader itself, so a zip, a Store entry or a folder with that id is refused or skipped. Ids are matched in full, so an id with a trailing newline is invalid. The loader does not require `main.js` or `backend.py`, but a plugin without `main.js` has no tab. The Framey App refuses a package that has neither.

## plugin.json

```json
{"id": "hello", "name": "Hello", "version": "0.1.0", "short": "Hi"}
```

| Field | Use |
|---|---|
| `id` | Required in a zip, where it becomes the folder name and must match the id rule above. For a folder you place yourself, the folder name is the id and any `id` in the file is ignored. |
| `name` | Title shown in lists. Defaults to the id. |
| `version` | Optional, shown in Settings and Store. |
| `short` | Optional label for the side menu. Defaults to the first word of `name`. |

The file must be a JSON object. If `name`, `version` or `short` is present it must be a string. Anything else (missing file, invalid JSON, not an object, a non-string field) makes the loader skip the plugin and log the reason. Other fields are ignored by the loader but are passed to the page as `Framey._meta`.

## main.js

`main.js` runs inside Steam's interface when the panel is loaded. Register a page:

```js
Framey.register({
  render(box) {
    box.textContent = "Hello from a plugin";
  },
});
```

`render(box)` is called each time the tab is opened. `box` is an empty element to fill. A plugin can also set `name` or `short` in the object to override `plugin.json`. Call `Framey.register` synchronously, at the top level of the file: it reads the plugin's manifest values from `Framey._meta`, which is only correct while that file is running. Calling it more than once adds more than one tab. Plugin tabs appear in folder-name order, followed by Settings and Store.

Available helpers:

| Helper | Use |
|---|---|
| `Framey.call(id, method, arg)` | Calls the `call(method, arg)` function of the backend with that id and returns a Promise with the JSON result. There is no timeout. A reply from before the panel was reloaded is discarded, so a call still pending during a reload is never resolved. |
| `Framey.el(css, text)` | Creates a `div` with inline CSS and optional text, marked with `data-fy`. |
| `Framey.button(text, css, onclick)` | Creates a centered, tappable button (a `data-fy` div). |
| `Framey.toast(text, ms)` | Shows a short message over the page, 2500 ms by default. |

Range inputs with the class `fy-r` get Framey's large slider styling. Keep controls large: the panel is used with a laser pointer. Keyboard shortcuts did not reach the page when tested on the headset, so do not rely on them. Framey gives plugins no storage API of its own.

### Isolation and cleanup

- Each `main.js` is evaluated as its own script, wrapped in a block, so top-level `let` and `const` do not clash between plugins. `var` and function declarations are not block-scoped and can still become globals.
- A syntax error means nothing from that file runs. An exception thrown at the top level stops the rest of that file, but anything it had already registered stays. In both cases the loader shows "Module <id> failed: <message>" for a few seconds and logs it, and other plugins are not affected. If `render` throws, the tab shows "Module error: <message>". Errors in asynchronous code (timers, rejected promises) are not caught by the loader.
- Elements made with `Framey.el` or `Framey.button` carry a `data-fy` attribute. When the loader stops, or reloads plugins, it removes every element with `data-fy` and deletes `window.Framey`, and nothing else. Elements you create yourself, event listeners, timers, styles and globals you add are not removed, so mark your own elements with `data-fy` (or remove them yourself) if you want them cleaned up.

## backend.py

An optional module with one coroutine:

```python
async def call(method, arg):
    return {"ok": True}
```

It must be `async def`. The result must be JSON-serializable: a result that cannot be serialized is returned to the page as `{"error": "..."}`. Exceptions raised in `call` are returned the same way, as is calling a plugin that has no loaded backend (`backend not loaded`). If 32 or more calls are already running when new ones arrive, the new ones are answered with `{"error": "busy"}`. A backend that fails to import (including a `sys.exit()` while importing) is skipped and logged. The file is loaded by path on its own, so sibling files are not on the import path.

There is no process isolation. Every backend runs inside the loader's single Python process and event loop, with the user's permissions. A backend that blocks, spins or exits stalls or stops the loader and every other plugin, so keep it small and never block. With the service unit the Framey App installs, that process also runs at idle CPU priority and is held to `MemoryHigh=40M` and `MemoryMax=60M`.

Backends load when the loader starts and on every reload, and are skipped for disabled plugins. A reload re-imports every enabled backend, not just the one that changed. There is no unload hook: Framey calls nothing in a backend when its plugin is disabled, removed or reloaded. A reload drops the old module reference, but threads, tasks, sockets and subprocesses the old module started keep running until the loader exits. A backend that starts any of these has to manage them itself, and a restart of `framey.service` is the only full reset.

## Installing a plugin

- **Linked or copied folder:** place or link the folder in `plugins/`, then reload from the Settings tab or restart `framey.service`.
- **Zip:** an HTTPS link to a `.zip` with `plugin.json` at its top level (or inside a single top folder). `plugin.json` needs a valid `id`. The link must be `https://`, and every redirect hop must be `https://` too: a hop to another scheme is refused before it is contacted. The download is abandoned after 45 seconds. Limits: the zip itself at most 5 MB, at most 500 entries, 5 MB per file and 20 MB unpacked in total, and paths of at most 8 components (the top folder, if any, and the file name included). Paths that are absolute, contain `..` or contain a backslash are refused. The Framey App can also install a zip file or a GitHub link and does this for you.
- **Store:** put a folder with a valid `plugin.json` into the loader's `store/<id>/` directory and install it from the Store tab.

A zip is checked first (paths, entry count, sizes, manifest, id), then unpacked into a hidden `.stage-<id>` folder inside `plugins/`. Only when that succeeds does the old copy move aside to `.old-<id>` and the new one take its place; if the swap fails the old copy is moved back, and a failed unpack leaves the working plugin untouched. A Store install is copied through the same staging and swap, and is refused unless the store entry has a valid `plugin.json`. An install is refused if the plugin folder is a link. If the loader is killed in the middle of a swap, the next start moves a left-behind `.old-<id>` back when the plugin folder is missing, and deletes any other `.stage-` and `.old-` folders. After a successful install, enable, disable, remove or reload, the loader reloads its backends and re-injects the panel immediately; the panel reopens on the same tab and shows a short result message. A failed action shows its error and leaves the panel in place. Re-injection also happens on the loader's 5-second check if the immediate one did not take.

The Settings tab stores which plugins you turned off in `~/.config/framey/settings.json`, which is read each time it is needed and written to a temporary file that then replaces it. A missing, unreadable or malformed file is treated as empty, a `disabled` entry that is not a list becomes an empty list, and entries that are not strings are ignored. Turning a plugin off needs a valid id of an installed plugin. Removing a plugin from the Settings or Store tab clears its off flag; removing the folder by any other means leaves the flag in the file.

Remove in Settings only deletes the plugin folder, or unlinks it if it is a link, and reports an error if the folder could not be deleted. In the Store tab, Remove is one tap for a copied plugin and two for a linked one. It never undoes anything a plugin installed elsewhere on the system, and for a linked plugin the loader says so before removing it. For Fan Control, Remove in the loader does not restore stock cooling: use the Framey App's **Remove** for that.

## Known limits

These were found by reading the code and have not been checked on a headset, so they are stated as limits rather than fixed:

- The `fyCall` binding is not restricted to Framey's own frame. Any script running in the same Steam page can call the loader and every backend. Whether foreign frames exist in those pages was not checked.
- Whether the panel needs re-injecting is decided by checking that `window.Framey` exists. A panel that is half built or detached is not repaired until Steam's page reloads.
- The bar icon is moved back at most 20 times per 10 seconds. The cost of watching the bar page for changes was not measured on a headset.
- If the CPU is starved so that a 5-second check is answered late, re-injection is delayed by one check. If no check is answered for about 30 seconds the loader drops the connection and reconnects.
- Plugin files already on disk are not size-checked, only zips. A very large `main.js` costs memory and time (a 5 MB one measured about 0.25 s of blocking work on a PC, not on a headset).
- Unpacking a zip and copying a Store entry run on the loader's event loop, so a large install briefly stalls it.
- A backend has no unload hook (see above).

## Security

Plugins run code on the headset, in Steam's interface and as the user. Only install plugins you trust. A plugin that needs root access has to ship and explain its own installation step, as Fan Control does.
