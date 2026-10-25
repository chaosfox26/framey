# Writing a Framey plugin

This is the plugin author guide for API generation 1, as implemented in Framey 1.0.2. For what Framey is and how to install it, see the [README](../README.md). Two working examples are in the repository: [examples/hello-plugin](../examples/hello-plugin) (small) and [examples/starter](../examples/starter) (most of the API).

Plugins are trusted code. The page part runs inside Steam's interface, and the backend part runs as a separate process under the same user. Framey contains failures, so one broken plugin does not take the panel down. It is not a security sandbox (see [Security model](#security-model)).

## Quick start

1. Copy `examples/starter` to `~/framey/plugins/<id>` on the headset. The folder name is the plugin id: 1 to 32 characters from `a-z`, `0-9`, `-` and `_`.
2. Change `id`, `name`, `short`, `version` and `source` in `plugin.json`.
3. Open Settings and tap **Reload modules**. (Restarting `framey.service` also works.)
4. If the plugin fails, its row on the Modules page shows the stage (load, render or backend), the message and the time, with **Disable** and **Reload**.

## Layout

```
plugins/<id>/
  plugin.json       the manifest; must be a JSON object
  main.js           page script; without it the plugin has no tab
  backend.py        optional backend; its process starts on the first call
  locales/<lang>.json   optional strings for api.i18n.load
  .source           GitHub link the plugin was installed from (written by the installer)
  .version          commit installed by an update (written by the updater)
  .identity.json    how the version was obtained: release, commit, branch, url or local
```

- A folder whose name is not a valid id is skipped silently. So are the `.stage-<id>` and `.old-<id>` folders used during installs and updates.
- `_core` is reserved and cannot be a plugin id.
- A symlink to a folder works. The Modules page marks it as linked.
- The loader does not read a plugin `icon.svg`. The side-menu tab uses `short`.
- Plugins are listed in folder-name order until the user moves them on the Modules page.

## plugin.json

```json
{"id": "starter", "name": "Starter", "version": "1.0.0", "short": "Starter", "source": "https://github.com/you/framey-starter", "api": 1, "framey": {"min": "1.0.2"}, "platform": ["linux-aarch64", "linux-x86_64"], "backend": {"timeout": 15, "idle": 120, "keepalive": false, "concurrency": 1, "memory_mb": 64, "startup_timeout": 10}, "data_version": 1, "i18n": {"default": "en"}}
```

| Field | Meaning |
|---|---|
| `id` | In a folder install the folder name is the id and any `id` in the file is replaced by it. In a zip it must match `[a-z0-9_-]{1,32}`. |
| `name` | Title in lists. Default: the id. |
| `short` | Side-menu label. Default: the first word of `name`. |
| `version` | Shown on the Modules page and compared by updates. Dotted numbers with an optional leading `v` are ordered (`1.2.0`, `v1.2.0`). |
| `source` | GitHub link used for updates: `https://github.com/<owner>/<repo>`, optionally ending in `.git` or followed by `/tree/<branch>`. A `.source` file in the folder is used first if it holds a GitHub link. |
| `api` | Integer API generation the plugin needs. This Framey provides 1. A higher value is reported as incompatible. |
| `framey` | `{"min": "X.Y.Z", "max": "X.Y.Z"}`. Lowest and highest Framey version supported; `max` is inclusive. |
| `platform` | List of `linux-aarch64` and/or `linux-x86_64`. Absent means any. |
| `backend` | Backend limits, see [Worker limits](#worker-limits). |
| `timeout` | Top-level alias of `backend.timeout`. If both are set, `backend` wins. |
| `data_version` | Integer from 0. Increase it when the stored data changes shape; the loader then runs `migrate` (see [Backend hooks](#backend-hooks)). |
| `i18n` | `{"default": "en"}`: the language your own strings are in, used as fallback. |
| `companions` | Optional helper tools that can be downloaded from your repository, see [Companions](#companions). |
| `privileged` | `{"installed": "<absolute path>", "helper": "<absolute path under /etc/>"}` for a protected component, see [Updates](#updates-and-versions). Ordinary plugins do not use it. |

The loader checks only that `name`, `version` and `short` are strings when present, and that the file is a JSON object. Out-of-range backend values are clamped to the limits in the table below, not rejected. An unknown field is ignored.

### Compatibility

`framey.min`, `framey.max`, `api` and `platform` are checked before anything is installed: for a zip, a local store entry, a URL install, and a new package during an update (checked after the archive is verified and before anything is swapped). A mismatch is refused with a message naming both sides, for example `Needs Framey 1.1.0 or newer; you have 1.0.2` or `Built for linux-x86_64; this headset is linux-aarch64`.

- A plugin that declares none of these is "Compatibility not declared" and nothing is blocked.
- A field of the wrong type, or a version that cannot be read, makes the plugin incompatible with the message `plugin.json compatibility fields are not valid`.
- An installed plugin that becomes incompatible (after a Framey update, for example) still loads and is only flagged.

## Page: main.js

`main.js` is evaluated as the body of a function that receives your `Framey` object. Top-level `let`, `const` and `var` stay local to your file.

```js
const api = Framey.register({
  render(box, api) {
    box.append(Framey.el("font-size:28px;font-weight:700", "Hello"));
  },
  dispose() {
    // optional: your own cleanup (see Cleanup)
  },
});
```

- `Framey.register(object)` must be called once per file. A second call throws `this module is already registered`. It returns the `api` object.
- `render(box, api)` runs each time the tab is opened, into an empty `box`. Put one-time setup (event subscriptions, pages) outside `render`.
- `name` and `short` in the object override `plugin.json`.
- `Framey._meta` is no longer on the global `Framey`. The object you receive still has a `_meta` copy of the manifest, but do not use it. Use `api.id`, `api.runtime.version` (manifest version) and `api.runtime.plugin`.
- `Framey.call(id, method, arg)` ignores its first argument and always calls your own backend. Use `api.call(method, arg)`.
- The global `window.Framey` is the loader's object, not your API. Do not add to it. Its `pages` and `plugins` registries are read-only to plugins: `plugins` is a frozen array of frozen module objects, and the global `dispose` needs the loader's core token, which plugins do not have. Your own `Framey.dispose()` on your proxy disposes only your module.

### Helpers on Framey

| Helper | Use |
|---|---|
| `Framey.el(css, text?)` | `div` with inline CSS and optional text. While `render` runs it is marked with your id and removed on cleanup. Nodes created later are not marked: set `node.dataset.fy = api.id` yourself (the starter does). |
| `Framey.button(text, css, onclick)` | Centered tappable button. Gated by trusted input (see below). |
| `Framey.toast(text, ms?)` | Short message over the page; 200 characters, 2500 ms by default, at most 60000 ms. |
| `Framey.fail(id, error)` | Shows `Module <id> failed: <message>` for 4 seconds. |
| `Framey.panelOpen` | Whether the panel is open. Prefer `api.visible`. |

### The api object

| Member | Use |
|---|---|
| `api.id` | Your plugin id. |
| `api.runtime` | Frozen `{framey, api, platform, lang, plugin, version}`. |
| `api.visible` | True while the panel is open and the page is visible. `api.onVisible(fn)` reports changes. |
| `api.lang` | Current language. |
| `api.t(key, vars?)`, `api.i18n` | Translations, see [Translations](#translations). |
| `api.call(method, arg)` | Backend call. Resolves to the backend result or `{error}`. Never rejects. |
| `api.fetch(url, opts?)` | HTTPS request through the loader. Rejects with an `Error` whose `code` is set. |
| `api.resource(root, path, ttl?)`, `api.revoke(url)` | URL for a file in your data or runtime folder. |
| `api.events.on/once/off` | Backend events. |
| `api.notify(opts)` | Notification with optional action buttons. |
| `api.timeout`, `api.interval`, `api.clear`, `api.raf` | Timers and frame callbacks that the loader cancels on cleanup. |
| `api.on(target, type, fn, opts?)` | Listener removed on cleanup. |
| `api.cleanup(fn)` | Extra teardown function, run on cleanup. |
| `api.onVisible(fn)` | Visibility callback, removed on cleanup. |
| `api.ui` | Controls, see [Controls](#controls-and-trusted-input). |
| `api.pages` | Panel tabs and full-screen pages. |
| `api.global.mount(id, opts)` | Elements in the corner, overlay or top bar zones. |
| `api.target(name)` | Injected CSS or JavaScript, see [Injection targets](#injection-targets). |
| `api.pickFile(opts?)`, `api.pickFolder(opts?)` | File and folder chooser. |
| `api.log(level, msg)` | Writes to the loader log (`debug`, `info`, `warning`, `error`). At most 5 lines per second. |

### Backend calls

`api.call(method, arg)` sends `call(method, arg)` to your backend and resolves with what it returns. The result must be JSON and at most 768 KiB. The promise resolves to `{error: "<message>"}` when the backend raised an exception, timed out, crashed, is busy, failed to start, or has no backend. The page waits up to 60 seconds for the reply (`Framey module did not reply in 60 s`); the worker's own limit (`backend.timeout`, 10 s by default) normally answers first. Typical messages: `backend call timed out after 15 s; module not responding`, `module busy; too many pending calls`, `module crashed; try again in 5 s`. After the plugin is disposed it resolves to `{error: "module disposed"}`.

Validate every argument in the backend. The page is untrusted input.

### Storage

The page has no storage API. Keep saved state in the backend (`framey_api.settings`) and read and write it with `api.call`. The page can keep a short-lived value in memory, but it is lost on reload.

### Events

The backend calls `framey_api.events.emit(name, payload)`. The page receives it with `api.events.on(name, (payload, info) => ...)`. `info` is `{name, dropped, open, tab}`; `dropped` counts events lost since the last delivery. `on` and `once` return a function that unsubscribes.

- Names match `[a-z0-9_.-]{1,40}`. Payloads are JSON of at most 16 KiB.
- The backend may emit 50 events per second. Each module queues up to 64 events for delivery. Events beyond that are dropped and counted.
- A page holds at most 64 subscriptions.
- Only events from your own backend reach `api.events`. Loader events (update results, progress) are never delivered to plugins.

### Notifications

`api.notify({title, body, icon, duration, actions, onaction, onclose})` returns `{id, dismiss()}`.

- The title is shown as `[<id>] <title>`, so the user can see which module sent it. `title` up to 80 characters, `body` up to 400, `icon` up to 8; nothing is shown if both are empty. `duration` in milliseconds, default 4000, at most 60000.
- Up to 3 actions `{id, label}` (label up to 40 characters). An id of `open:modules` or `open:settings` opens that panel page. `onaction(id)` runs on a tap; `onclose(reason)` gets `timeout`, `dismiss`, `action` or `dropped`.
- At most two notifications per module on screen; the rest wait in a queue.

### Translations

Strings live in `locales/<lang>.json` as a flat object of string values (at most 64 KiB and 2000 keys; keys at most 128 characters, values at most 1024). Load them with `await api.i18n.load("de")`, or add them directly with `api.i18n.add(lang, dict)`.

The lookup order is the user's language, its base language (`de` for `de-AT`), the `i18n.default` from `plugin.json`, then `en`. A missing key returns the key itself.

- Placeholders: `api.t("hello", {name: "Frame"})` fills `{name}`.
- Plurals: pass `count`. The loader tries `key_zero` (only when count is 0), then `key_<plural category>`, then `key_other`, then `key`.

### Pages and mounts

`api.pages.register({id, title, mode, render(box, ctx), onclose})` returns `{open(), close(), remove()}` or `{error}`.

- `id` matches `[A-Za-z0-9_.-]{1,40}`. The ids `modules`, `settings`, `discover` and `developer` are reserved.
- `mode: "panel"` (default) adds a tab after your module's tab. `mode: "full"` shows a full-screen page with a Back button.
- `ctx` is `{id, plugin, mode, api, close()}`. At most 8 pages per module.

`api.global.mount(id, {zone, render(el)})` returns `{el, unmount()}` or `{error}`. Zones are `corner` (bottom right), `overlay` (full screen; only your element takes pointer input) and `bar` (a strip across the top). At most 8 mounts per module.

### Pickers

`api.pickFile({title, root, path, exts})` and `api.pickFolder({title, root, path})` open a chooser and resolve to `{root, path}` (a file also has `name`), or `null` when cancelled. Only one chooser is open at a time.

The roots offered to a page are your plugin's data folder (named `data`) and the folders listed in `file_roots` in `~/.config/framey/loader.json`. The home folder, `/run/media` and the config folder are never offered from the page. The chooser works in three steps:

1. **Roots.** The first request has no `root`. The reply lists only the roots, as `{id, label}` entries; `entries` is empty. The chooser opens the root you passed in `root` if it is offered, otherwise the data folder.
2. **Browse.** A request with a named root lists that folder as before. The top-level listing of a root also carries `roots`, so the labels stay the same.
3. **Pick.** A folder resolves to `{root, path}`; a file resolves to `{root, path, name}`.

If no root is available the chooser says so. `exts` limits files to the listed extensions (up to 16). `file_roots` takes up to 8 absolute folders under `/home/`, `/run/media/` or `/mnt/`; the home folder itself cannot be a root, but folders inside it can.

### Media

`api.resource("data" | "runtime", "sub/file.png", ttl?)` resolves to a URL for `<img>`, `<audio>` or `<video>`. Only these types are served: png, jpg, jpeg, webp, gif, svg, mp3, ogg, wav, mp4, webm, json and txt. The path is relative and may not contain `..`.

- Page calls return files up to 1 MiB (1,048,576 bytes) inline as a `data:` URL. A backend's `fy.resource_url` returns the inline form only up to 589,440 bytes, because its reply is capped at 768 KiB (786,432 bytes) and base64 adds a third; larger files get a token URL.
- Larger files are served by the loader on `127.0.0.1` under a token. The token lasts `ttl` seconds (default 600, at most 3600); call `api.revoke(url)` to end it early. At most 16 tokens at a time.

### Fetch

`api.fetch(url, {method, headers, body, timeout, max_bytes})` resolves to `{status, headers, text, b64, bytes, truncated, path}`.

- Only `https` links to public addresses. Loopback, private, link-local and CGNAT addresses are refused. Redirects are followed for up to 5 hops and must stay on `https`. Developer mode also allows `http`.
- `method` is GET, POST, PUT, DELETE or HEAD. The request body is at most 256 KiB. `max_bytes` defaults to and is capped at 512 KiB; `truncated` says when it was cut. `timeout` is 1 to 60 seconds (default 15).
- `text` is returned for text, JSON, XML and JavaScript responses, otherwise `b64`. `path` is always `null`: the page cannot write files. The `dest` option is backend only.
- At most 2 downloads per module and 4 in total run at once. Errors reject with `code` set to values such as `scheme`, `blocked_address`, `timeout`, `network`, `too_large` or `busy`.

### Controls and trusted input

`api.ui` builds controls that take focus and respond to Enter and Space; sliders and lists also respond to the arrow keys. Each returns an element you append to `box`.

| Control | Options |
|---|---|
| `api.ui.button({label, onclick, disabled, primary, css})` | `primary` styles it as the main action. |
| `api.ui.toggle({label, value, onchange, disabled})` | `onchange(boolean)`. |
| `api.ui.slider({label, min, max, step, value, oninput, onchange, disabled, format})` | `format(value)` returns the shown text. |
| `api.ui.list({items, selected, onselect, multi, empty})` | `items` are `{id, label, sub, disabled}`. |
| `api.ui.group(...elements)`, `api.ui.focus(element)` | Row layout and focus helper. |

The panel is used with a laser pointer and a controller, so keep targets large (48 px or more). A plain range input gets the panel's large styling when it has the class `fy-r`. Whether the Steam controller moves focus into these controls was not verified on a headset.

Trusted input: `~/.config/framey/loader.json` may set `"trusted_input"` to `"off"`, `"auto"` or `"require"` (default `"auto"`). With `"auto"`, nothing is refused until the page has seen one real user input event (a click, key press or pointer press); after that, clicks and key presses that are not user input (synthetic events from a script) are ignored for `Framey.button`, `api.ui` buttons, toggles, sliders and lists, and notification action buttons, and counted as `untrusted_clicks` in diagnostics. `"require"` refuses them from the start. Whether controller input is a trusted event on the Steam Frame is unverified. The gate is not a security boundary: plugin code runs in the same page as the controls (see [Security model](#security-model)).

### Injection targets

`api.target(name)` returns `{css(key, text), js(key, code, teardown?), remove(key)}`. Items are added to the Steam page under your plugin's ownership and replaced when you call again with the same key. Keys match `[A-Za-z0-9_.-]{1,40}`. A module can hold at most 8 items, 64 KiB each and 256 KiB in total. `js` runs with `eval` in the page; `teardown` is evaluated when the item is removed or replaced.

The targets `main` (Steam's main page) and `bar` (the bottom bar) are available to every plugin in the current code. Other target names come from the developer table `dev.targets` in `~/.config/framey/developer` and exist only in developer mode. Injected code has full access to Steam's page: install only plugins you trust.

### Cleanup

Framey removes what it can track; the rest is yours.

- **Page:** the loader calls `dispose()` once when the module is disabled, removed, reloaded, updated, or when the panel is re-injected. After `dispose()` it removes the timers, intervals, listeners, raf callbacks, cleanup functions, notifications, injected items and page subscriptions made through `api`, removes the `Framey.el` and `Framey.button` nodes marked with your id, and ignores late replies. An exception in `dispose` shows a toast and does not stop other modules.
- Anything created in other ways (`setInterval`, `addEventListener`, `MutationObserver`, WebSockets, globals) is not tracked: clean it up in `dispose()`.

### Failures

- A syntax error or a top-level exception stops that file and shows `Module <id> failed`. The Modules page lists it as a **load** failure.
- An exception in `render` shows `Module error: <message>` in the tab and is listed as **render**.
- Errors in asynchronous code (timers, promises) are not caught by the loader. Catch them yourself.
- A backend failure is listed as **backend** and is forgotten after the next successful call.
- Requests from the page to the loader core stop waiting after 55 seconds for `api.fetch` and `api.resource`, 15 seconds for other core calls such as the pickers, and 600 seconds for install, update, check and similar actions. The notice is `Framey core did not reply in N s`. So `api.fetch`'s `timeout` (1 to 60 seconds) is cut at 55 seconds on the page side. The loader does not cancel the request, so the download may still finish.

## Backend: backend.py

An optional module. `call(method, arg)` handles every request; it may be `def` or `async def`.

```python
import framey_api as fy

def call(method, arg):
    count = fy.settings.get("count", 0)
    if method == "add":
        if isinstance(arg, bool) or not isinstance(arg, int) or not 1 <= arg <= 10:
            return {"error": "add takes a whole number from 1 to 10"}
        count += arg
        fy.settings.set("count", count)
    elif method != "get":
        return {"error": "unknown method"}
    return {"count": count}
```

A plain `def` call runs on the worker's event loop, so a slow synchronous call delays everything else in that worker, including `cancel` and `ping`. Use `async def` and `await` for waits and I/O.

### Backend hooks

Optional functions in `backend.py`. A hook receives its argument only if it declares a parameter.

| Hook | When | Limit |
|---|---|---|
| `startup(ctx)` | When the worker starts (first call, or first use after an idle stop or reload). | `startup_timeout` (default 10 s) |
| `migrate(ctx)` | Before `startup`, when the installed `version` or `data_version` differs from the last version that started successfully (not on the first start). The settings file is copied to `settings.json.premigrate` first and restored if the migration fails. | `startup_timeout` |
| `unload(reason)` | Before the worker stops. `reason` is `reload`, `disable`, `update`, `idle`, `lru` (evicted for another worker), `shutdown` (loader stopping) or `uninstall`. | 3.5 s (1.5 s on loader shutdown) |
| `uninstall(ctx)` | When the plugin is removed. `ctx.purge` is true for "Remove and delete data". | 10.5 s |

`ctx` has `plugin`, `runtime`, `dirs`, `settings`, `log`, `from_version`, `to_version`, `data_from`, `data_to`, `purge` and `reason`. Stop the threads, sockets and subprocesses you started inside `unload`. When the worker exits, the loader also kills its process group on Linux, so child processes in that group are killed too.

### framey_api

`import framey_api as fy` gives the following. The types are in [types/framey_api.pyi](../types/framey_api.pyi).

| Name | Use |
|---|---|
| `fy.PLUGIN`, `fy.API` | Plugin id and API generation (1). |
| `fy.runtime` | `framey`, `api`, `platform`, `python`, `plugin`, `version` (manifest), `plugin_dir`. No environment, tokens or other plugins. |
| `fy.dirs.config` / `.data` / `.runtime` / `.logs` | Your folders, created on first access. |
| `fy.settings.get/set/update/delete/all/reset` | JSON settings in `dirs.config/settings.json`. Atomic writes; at most 256 KiB and 200 keys; key names up to 64 characters. |
| `fy.log.debug/info/warning/error(msg, *args)` | Writes `plugin.log` in `dirs.logs` (256 KiB, 3 files kept, 2048 characters per line, 20 lines per second; excess lines are counted and dropped). Warnings and errors are also echoed to stderr. |
| `fy.events.emit(name, payload)` | Sends an event to the page (see [Events](#events)). Returns `False` when dropped. |
| `await fy.fetch(url, method=, headers=, body=, timeout=, max_bytes=, dest=)` | Same rules as the page, plus `dest="data:<name>"` or `dest="runtime:<name>"` to save the body to a file (up to 20 MB per file, 64 MB per destination folder). Raises `FrameyApiError(code, message)`. |
| `await fy.resource_url(root, path, ttl=)`, `await fy.resource_revoke(url)` | URLs for files under `data`, `config` or `runtime`. |
| `await fy.list_dir(root, path="", hidden=, dirs_only=, exts=)` | Lists a folder under `data`, `config`, `media` (`/run/media/<user>`) or a `file_roots` folder. The reply's top-level `roots` is a list of `{id, label}` and does not show `media`; `media` can still be listed by name. |
| `fy.companion(name)` | Path of an installed companion, see [Companions](#companions). |
| `fy.migrate.keys(mapping)`, `.move(src, dst, overwrite=)`, `.copy_once(src, dst, marker)`, `.snapshot()` | Idempotent helpers for migrations. `keys` renames settings keys that do not exist yet. |
| `fy.FrameyApiError` | Exception with `.code` and `.message`. |

The helpers are conveniences, not a sandbox: a backend can read any file its user can read.

### Worker limits

Each module with a `backend.py` runs in its own worker process. Limits, with defaults:

| Limit | Value |
|---|---|
| Worker start | On the first call. Stopped after `backend.idle` seconds without calls (default 120, 10 to 3600), unless `keepalive` is true. |
| Call timeout | `backend.timeout`: 10 s by default, 1 to 60 s. |
| Startup timeout | `backend.startup_timeout`: 10 s, 1 to 60 s. |
| Concurrency | `backend.concurrency`: 1 call at a time by default, 1 to 8. Further calls wait in a queue of 16; then `busy`. |
| Memory | `backend.memory_mb`: 32 MB by default, clamped to 16 to 64. On Linux it sets `RLIMIT_DATA` to the worker's data size at start plus this many MB, so it is headroom for growth. If the limit cannot be applied, the worker runs without it: `limits_applied` is false in the loader's status and Diagnostics shows `limits_warning`. |
| Workers at once | At most 3. The preference `workers_max` (1 to 3, default 3) in the `prefs` section of `~/.config/framey/settings.json` can lower it. |
| Memory across workers | The declared `memory_mb` of running workers adds up to at most 100 MB. When a new worker needs room, the least recently used idle worker is stopped (reason `lru`); `keepalive` workers are stopped only when no other idle worker exists. If nothing can be stopped, a memory shortage fails at once with code `memory`. When the worker count (3) is the limit, the call waits 5 s and fails with `busy_workers`. |
| Host calls | At most 4 outstanding per worker (fetch, resource, files). |
| Protocol lines | 1 MiB per line. Arguments and results at most 768 KiB. |
| Stderr | Captured to `worker.err` in `dirs.logs`; rotated to `worker.err.1` after 64 KiB. |

The worker runs at low priority, may open at most 256 files, cannot write files over 64 MB, produces no core dumps and cannot gain privileges, so `sudo` fails inside a backend. Its environment is limited to `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ` and `XDG_RUNTIME_DIR`. Its working directory is your data folder. Output printed to stdout goes to stderr.

When a call times out, the caller gets `backend call timed out after N s; module not responding`. The loader sends `cancel` and then a `ping` with 2 seconds to answer. If the worker answers, only that call is abandoned. If it does not, the worker is killed. Three crashes within 5 minutes mark the module **failed** until it is reloaded; restarts after crashes wait 1, 5 and then 30 seconds.

Protocol, for tool authors only: the loader and the worker exchange one JSON object per line on stdin and stdout. The loader sends `startup`, `migrate`, `call`, `uninstall`, `unload`, `ping`, `cancel` and `host_reply` with an integer `id`; the worker answers with `{"re": id, "ok": true, "result": ...}` or `{"re": id, "ok": false, "error": {...}}`, and sends `emit` and `host` messages of its own.

## Companions

A companion is a helper program that the module needs, downloaded on request. Declare it in `plugin.json`:

```json
"companions": [{"name": "tool", "version": "1.2.0", "platform": "linux-aarch64", "asset": "tool-linux-aarch64.tar.gz", "sha256": "<64 hex digits>", "size": 123456, "exec": ["tool"]}]
```

- `asset` ends in `.tar.gz`, `.tgz` or `.zip`. `size` is 1 to 20,000,000 bytes. `exec` lists up to 8 paths inside the archive.
- The user starts the download with **Download <name>** on the Modules page. The loader takes the asset from a GitHub release of your own repository (tag `v<version>`, `<version>` or the installed tag). Other hosts are refused.
- The archive's size and SHA-256 must match the manifest. If GitHub publishes a digest for the asset, it must agree too. This is an integrity check, not a signature.
- The archive is unpacked into `~/.local/share/framey/<id>/bin/<name>-<version>/`. Companions never run as root.
- `framey_api.companion(name)` returns `<folder>/<name>` if that file exists, otherwise the folder. It does not read the `exec` list. Raises `FrameyApiError("not_installed")` when none is installed.

## Installing

- **Folder:** copy or link the folder into `plugins/`, then reload from Settings or restart `framey.service`.
- **Zip:** an `https://` link to a `.zip` entered on the Modules page, or a zip file installed with the Framey App. `plugin.json` must be at the top of the zip or inside one top folder. Limits: the zip at most 5 MB; 500 entries; each file at most 5 MB; 20 MB unpacked in total; paths at most 8 components, with no absolute path, no `..` and no backslash. Files marked executable stay executable.
- **URL:** the zip link must be `https://` and resolve to a public address; each redirect must also be `https://`. The download is abandoned after 45 seconds. The Modules page asks for confirmation first, naming the host and saying the plugin runs with your permissions.
- **Store folder:** a folder with a valid `plugin.json` in `store/<id>/` can be installed from the Modules page. The Store page itself is a placeholder.

An install is staged in `.stage-<id>` and swapped in; the previous copy is kept as `.old-<id>` until the swap succeeds. A failed unpack leaves the working plugin untouched. An install is refused when the target folder is a link or an update is waiting to be finished. A zip install writes regular files only; it does not create links.

## Updates and versions

Updates run only when the user taps. Checking never installs.

- **Source.** The update source is the `.source` file or else `source` in `plugin.json`. Only GitHub is supported.
- **Check.** *Check for updates* on the Modules page asks GitHub for the head commit of the source branch and reports an update when it differs from the installed commit. A protected component is compared by its newest release tag instead. Automatic checks run only when the Settings preference `autocheck` is on (off by default). The Versions list shows releases.
- **Channel.** Prereleases are offered only on the `prerelease` channel, which is set only in `~/.config/framey/loader.json` (`channel`); there is no Settings control. The prerelease channel also needs developer mode (`~/.config/framey/developer`); without it the stable channel is used. The stable channel ignores prereleases.
- **Install.** The archive of the chosen release or commit is downloaded, verified against the GitHub digest when one is published, checked like a zip, and then swapped in. After every successful update, repair or version install, the previous copy is kept as `.old-<id>` for **Restore previous version**, until **Keep this version** deletes it or a later update replaces it. A repair of the same version keeps the older kept copy. If the new version does not start, the previous copy is restored and the update reports why.
- **Kept and replaced.** The plugin folder is replaced as a whole, so store data outside it. Settings, data, logs and the enabled state are not touched. The update keeps `.source` and writes `.version` and `.identity.json`.
- **Downgrades** need a confirmation. A frozen module is not updated until it is unfrozen.
- **Linked plugins.** A single Update, a version install and a repair refuse a linked folder (`linked folder: not updated`), and batch update skips it (`linked folder`). Framey does not write through the link.
- **Protected components** (`privileged`) move only forward. They are installed from a signed GitHub release (a `.tar.gz` and a `.sig` asset checked with `ssh-keygen` against the root-owned `allowed_signers` file next to `installed`). The loader then runs exactly `sudo -n <helper> update` (or `rollback`) with no shell and no password. An update counts as finished only when the root-owned `installed` file reports the new version; otherwise it is a **partial update** with Retry, Check again and Restore previous version. Batch update skips protected components with the reason `protected: updates from its own card`.

The Framey App sets up the protected updater once, asking for the password once. The loader never stores a password.

## Maintenance status

The Modules page can show how a project is maintained. It is based on GitHub's data and on one declaration you can make.

| Label | Shown when |
|---|---|
| Archived repository | GitHub reports the repository as archived (GitHub's flag; a plugin cannot declare it). |
| Maintainer-declared unmaintained | The upstream `plugin.json` contains the declaration below. |
| Recent activity / No recent activity | The latest commit or release is inside / outside the window. |
| Unknown | Not enough data, no GitHub source, or the check failed. |

Declare it in the `plugin.json` of your repository (at the source path, not the installed copy):

```json
"maintenance": {"status": "unmaintained", "by": "maintainer name or handle", "note": "optional, up to 300 characters", "date": "2026-01-31"}
```

`status` must be exactly `unmaintained`, `by` is required, `date` (`YYYY-MM-DD`) is optional. Any other value is ignored. The check runs on demand and, if the Settings preference `maint.enabled_background` is on (off by default), in the background. The activity window is `maint.window_days`, 30 to 1825, default 365.

## Lifecycle

- **Load.** At startup each enabled module with a `main.js` is evaluated once, and its tab appears. The backend does not start until it is first called.
- **Disable / enable.** The page is disposed and the backend stops (reason `disable`). Data is kept. The disabled list is in `~/.config/framey/settings.json`.
- **Reload.** A module's **Reload** (or **Reload modules** in Settings) disposes the page, stops the backend (reason `reload`) and evaluates `main.js` again. The backend starts again on its next call.
- **Hide and freeze.** Hide removes the tab only; the module keeps running. Freeze blocks updates until it is unfrozen.
- **Update.** Described above. The new files are loaded only after the swap.
- **Rollback.** Whenever a kept `.old-<id>` copy exists (after an update, repair, version install, downgrade or partial protected update), **Restore previous version** swaps it back. **Keep this version** deletes it.
- **Uninstall (Remove).** Runs `uninstall(ctx)`, then deletes the folder (or removes the link, never its target). **Remove and delete data** also deletes the four standard folders (config, data, runtime, logs). Plain removal keeps them.
- **Standard folders.** Created on first use with mode 0700, never inside `plugins/`: config `~/.config/framey/plugins/<id>/`, data `~/.local/share/framey/<id>/`, runtime `<runtime root>/framey/<id>/`, logs `~/.local/state/framey/<id>/`. The runtime root is `$XDG_RUNTIME_DIR` when it is set, otherwise `/run/user/<uid>` if that is a real directory owned by your user with mode 0700. If neither applies there is no runtime folder: every backend fails to start with a runtime error. There is no `/tmp` fallback.

## Security model

Framey is failure containment, not a security sandbox. Treat any installed plugin as trusted code. Ordinary plugins run as the same user as the loader; their backends can read and write any file that user can, and their pages run in Steam's page with full access to it.

- Backends get no privileges (`no_new_privs`) and a limited environment, but are not isolated from the user's files. They run with the same user ID and have no seccomp, landlock or bwrap restriction. A child process that calls `setsid` escapes the process-group kill.
- Page code of all modules shares one JavaScript realm with the loader's page code. Framey keeps tokens in closures and freezes module objects, which stops accidents, not a hostile plugin. The `trusted_input` gate is not a security boundary.
- The loader runs `sudo` only for a `privileged.helper` under `/etc/` of a package that was verified against the root-owned `allowed_signers` file. Ordinary plugins never get `sudo`.
- Page fetches and downloads refuse non-public addresses and non-HTTPS links. File access from pages is limited to the plugin's data folder and `file_roots`.
- Plugin injection into `main` and `bar` (see [Injection targets](#injection-targets)) can change Steam's own page.
- If the panel misbehaves, create the empty file `~/.config/framey/safe`: Framey then starts without plugins (safe mode) so you can disable or remove them.

## Developer tools

Developer mode is switched on by the file `~/.config/framey/developer` (a JSON object, may be `{}`). A file that cannot be parsed, or is over 4 KB, still switches developer mode on, and its `targets` are then ignored. It adds the **Developer** page and enables:

- **Hot reload** (per module, Developer page): reloads a module when its files change, watching with inotify where available and polling otherwise. Each switch turns itself off after an hour without changes; at most 8 modules at a time. Files named `.stage-*`, `.old-*`, `.git`, `__pycache__`, `*.swp` and `*~` are ignored.
- **Debug targets** (Developer page): lists the pages of Steam's Chromium debugger on port 8080. The page reports whether the port listens beyond loopback.
- **Diagnostics** (Developer page): a JSON snapshot with the loader version, worker states, per-module counters and recent loader log lines. Home paths and tokens are removed. Plugin code cannot call it directly.
- **Plain `http` links** are allowed in `api.fetch` and in backend `fetch`.
- The developer table `dev.targets` (extra injection targets).

Reload your module after each edit if hot reload is off.

## Minimal example

`plugins/counter/plugin.json`:

```json
{"id": "counter", "name": "Counter", "version": "1.0.0", "short": "Count", "framey": {"min": "1.0.2"}, "data_version": 1}
```

`plugins/counter/main.js`:

```js
Framey.register({
  render(box, api) {
    const out = Framey.el("font-size:24px;margin-top:12px", "Loading...");
    const show = r => (out.textContent = r.error ? "Error: " + r.error : "Count: " + r.count);
    box.append(
      Framey.el("font-size:28px;font-weight:700", "Counter"),
      out,
      Framey.button("Add one", "height:60px;margin-top:16px", () => api.call("add", 1).then(show)),
    );
    api.call("get").then(show);
  },
});
```

`plugins/counter/backend.py`:

```python
import framey_api as fy

def call(method, arg):
    count = fy.settings.get("count", 0)
    if method == "add":
        if isinstance(arg, bool) or not isinstance(arg, int) or not 1 <= arg <= 10:
            return {"error": "add takes a whole number from 1 to 10"}
        count += arg
        fy.settings.set("count", count)
    elif method != "get":
        return {"error": "unknown method"}
    return {"count": count}
```

Copy it to `~/framey/plugins/counter`, then reload modules. The count is kept in `~/.config/framey/plugins/counter/settings.json`. For translations, pages, events and file pickers, read `examples/starter`.

## Not supported

- **Store and discovery.** The Store page is a "Coming soon" placeholder: no catalog, no search.
- **Page storage, page file writes and calls to other plugins.** Keep state in the backend; only the backend saves downloads (`dest`); `api.call` reaches your own backend only.
- **Starting companion or protected updates from a plugin.** Only the loader's own UI does.
- **React DevTools.** The loader has no React integration; use the Developer page's debug targets and the Chromium inspector.
- **Backends in other languages, `icon.svg`, and plugin-declared archive flags.** Backends are Python; the loader does not read a plugin icon; "archived" is GitHub's flag.
