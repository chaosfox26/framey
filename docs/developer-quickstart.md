# Developer quickstart

Framey loads a JavaScript page and an optional Python backend from a plugin folder. You can write and run a plugin without Node.js, npm, React, or a bundling step. Framey supplies the page API and injects `framey_api` into the backend worker; do not install an unrelated package with that name.

Start with [Hello](../examples/hello-plugin) for saved settings and backend calls, or [Starter](../examples/starter) for events, translations, pages, notifications, and cleanup. The complete reference is [Writing a Framey plugin](plugins.md).

## Make the first plugin

1. Copy `examples/hello-plugin` to `~/framey/plugins/hello` on your development headset. The folder name is the installed identity. Use 1–32 lowercase letters, digits, hyphens, or underscores; `_core`, `core`, `modules`, `settings`, `store`, `discover`, `developer`, `dev`, and `decky-store` are reserved.
2. Open `plugin.json`. Keep `id` equal to the folder name. Set a descriptive `name`, short tab label, and your own dotted `version`. Keep `api: 1`, declare your minimum Framey version, and list the platforms you actually support. The examples declare `linux-aarch64` and `linux-x86_64` and use no native binaries; these declarations do not establish headset testing.
3. Leave `source` absent during local development. Add your actual public GitHub repository URL when publishing; a placeholder is not a working update source. A source URL refers to a repository whose root contains `plugin.json`, not to this repository's `examples` directory.
4. In Framey Settings, choose **Reload modules**, then open **Hello**. Move the level slider, leave and reopen the page, and confirm the backend returns the saved value. **Greet** sends a string to `call("greet", arg)`.
5. Choose **Slow call (30 s)** to exercise error handling. The example declares a 15-second worker timeout, so this deliberately slow request should report a timeout. The delay uses `await asyncio.sleep`, which allows cancellation.
6. Edit the greeting in `backend.py`, reload modules, and try it again. Reload both page and backend state; reopening a tab alone is not a code reload.

A page-only plugin needs just `plugin.json` and `main.js`. Add `backend.py` for persistent settings or device work. Changes to Python code require a worker reload, just like changes to page code require a page reload.

## Identity, versions, and files

```text
hello/
  plugin.json        identity, API generation, compatibility, worker limits
  main.js            plain JavaScript evaluated with a scoped Framey object
  backend.py         optional Python module, loaded by Framey's worker
  locales/en.json    optional translation dictionary
```

These versions have different jobs:

| Value | Purpose |
|---|---|
| `version: "1.0.0"` | Your plugin's release version. |
| `api: 1` | The Framey API generation your plugin needs. |
| `framey.min: "1.0.2"` | The oldest loader version your code supports. Raise it if you use newer functionality. |
| `platform` | Compatible operating system and architecture combinations. Ship architecture-specific companions when needed. |
| `data_version: 1` | Your settings/data schema version. Increase it and provide an idempotent migration when stored data changes shape. |

`api.runtime.framey` and `fy.runtime.framey` identify the loader; `.version` identifies the plugin. `api.id` and `fy.PLUGIN` identify the installed plugin. Do not derive identities or data paths from a display name.

Use `fy.settings` for JSON settings and `fy.dirs` for data and logs. Updates replace the plugin folder, so do not save user data there. Production backend startup requires a secure runtime root (`$XDG_RUNTIME_DIR` or the validated `/run/user/<uid>` fallback). If none is available, the supervisor refuses startup. Isolated SDK tests can construct a missing runtime path and exercise other SDK helpers; that does not establish production startup without runtime storage.

## Check ARM64, SteamOS, and VR support

A `platform` declaration is a requirement check, not evidence that the contents or interface work on the target device. Review each layer before calling a port ready:

| Layer | Evidence to check |
|---|---|
| ARM64 native code | Every executable, shared library, Python extension, helper and downloaded dependency needs an ARM64 build or a supported replacement. One ARM64 file, or no recognized binaries in an archive, does not settle the rest. |
| SteamOS and device behavior | Review required services, permissions, library versions, filesystem paths and hardware interfaces. Linux support alone does not prove Steam Deck-specific device code works on Steam Frame. |
| Framey lifecycle and APIs | Provide Framey entry points, dispatch, storage and cleanup. The source helper does not load Decky packages, supply React or emulate a Decky backend runtime. |
| VR interface | Keep controls at least 48 px high, provide readable labels and controller/keyboard focus, and test navigation, scrolling, close/back behavior and error states in the headset. Simulated-browser input tests do not prove VR usability. |

The experimental **Store** installs Framey-native modules. The separate **Decky Store** browses upstream packages and offers explicit, read-only archive inspection; it does not install or execute those packages. A **Compatible** label on a native entry means its declared requirements match, **Not compatible** gives a specific mismatch or source-port requirement, and **Not checked** leaves an unanswered question. An unadapted Decky package still needs a Framey port even when its archive contains ARM64 code. See [Decky Store browsing](catalog.md#decky-store) and [selected-version inspection](catalog.md#inspect-a-selected-version) for the current flow and evidence limits.

## A lifecycle that survives reloads

`Framey.register({render, dispose})` registers one page. `render(box, api)` runs again when the page is opened; avoid registering another event subscription or page on every render. Starter demonstrates a shared initialization promise and a render generation check, so a delayed initialization cannot populate an obsolete page or recreate UI after disposal.

Use `api.ui` controls for focus, keyboard operation, and Framey's large touch targets. Use `api.timeout`, `api.interval`, `api.on`, `api.events.on`, and `api.cleanup` to give Framey ownership of timers, listeners, subscriptions, and custom teardown. Release your own observers, sockets, and other resources too. Treat visibility as a hint to reduce work: current `api.visible` reflects panel and document visibility, not a guarantee that your tab is selected.

`api.call` resolves to the backend result or an object with `error`; check that field before using the result. Host helpers such as `api.fetch` reject, so catch their errors. Validate Python arguments even when a control normally sends the right shape: booleans are integers in Python, and an unchecked `int(arg["level"])` silently accepts input your contract may intend to reject.

Backends start on demand and can stop when idle. Keep durable state in settings; memory is only a cache. Optional hooks are `migrate(ctx)`, `startup(ctx)`, `unload(reason)`, and `uninstall(ctx)`. Migration precedes startup when required. Cancel and await background tasks in `unload`, as Starter does. A worker process isolates failures; it runs as the same user and is not a security sandbox.

## Check changes locally

From the Framey repository root, the example backend checks use Python's standard library:

```sh
python3 -m unittest discover -s tests -p test_fy_starter.py
```

They exercise argument validation, saved settings, migrations, task cancellation, events, and the worker protocol. Browser checks are optional development tooling; they require Node.js and the `jsdom` version specified in [the test harness](../tests/js/harness.js):

```sh
node tests/js/starter.test.js
```

If dependencies live outside the checkout, set `FRAMEY_JSDOM` to the folder containing their `node_modules`. This test dependency is not needed to install or run a plugin on the headset.

These checks use temporary folders, worker processes, and a simulated browser. They do not establish that a newly edited loader service works on a headset. Verify the installed revision separately: startup, open/reopen, input, saved values after worker restart, backend errors, disable/reload, and removal. See the maintained [verification status](using.md#verification-status); do not infer headset validation from a passing fixture suite.

For development, an empty JSON object in `~/.config/framey/developer` enables the Developer page. Enable hot reload there for your plugin, or keep using Reload modules. Read loader logs with:

```sh
journalctl --user -u framey.service -n 100 --no-pager
```

Plugin logs are under `~/.local/state/framey/<id>/`. A syntax or render failure appears on the Modules page. If plugin UI prevents recovery, create `~/.config/framey/safe` and restart the loader to start without plugins; remove the file after fixing the plugin.

## Package a release

Keep `plugin.json` at the archive root or inside one top-level folder. The repository includes [offline plugin tools](plugin-tools.md) for validation and reproducible packaging without running plugin code:

```sh
python3 tools/framey_plugin.py validate examples/hello-plugin --framey-version 1.0.2 --platform linux-aarch64
python3 tools/framey_plugin.py pack examples/hello-plugin --output /tmp/hello-release.zip
```

Choose a new output path; the packager refuses to overwrite existing files. Review its included/omitted file report. For a minimal archive, Python can also create a zip from inside your plugin directory without a JavaScript build:

```sh
python3 -m zipfile -c ../hello.zip plugin.json main.js backend.py
```

Include your `locales` directory and other required assets if present. Inspect the zip before release, omit credentials and generated caches, and test installing that exact archive. See [Installing](plugins.md#installing) for archive limits and [Updates and versions](plugins.md#updates-and-versions) for repository sources and release behavior.

## Porting source from Decky

Framey is an independent GPL-2.0-only project inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader). A Decky plugin bundle is not a Framey plugin package. A source port requires adapting entry points, UI, backend calls, storage paths, lifecycle hooks, and any device-specific code.

| Existing plugin concern | Framey equivalent to implement |
|---|---|
| Frontend entry point and framework integration | Plain `main.js` calling `Framey.register` once; use `api.ui` or ship your own compatible output. |
| Backend RPC bridge | `api.call(method, arg)` to your own `backend.py` `call(method, arg)`. |
| Settings and device paths | `framey_api.settings`, `framey_api.dirs`, and Frame-compatible device logic. |
| Events and teardown | `fy.events.emit`, `api.events.on`, scoped cleanup, and `unload(reason)`. |
| Package metadata and native helpers | Framey `plugin.json`, explicit API/platform/version fields, and declared companions where applicable. |

An optional [Decky-style source helper](decky-compatibility.md) supports a few call and event idioms, with a [dependency-free example](../examples/decky-style). It does not load compiled Decky bundles, resolve Decky packages, or provide React, backend lifecycle, or hardware compatibility.

Retain the original source's license notices and attribution when adapting it. Do not describe a source port as bundle compatibility or promise that Steam Deck hardware features exist on Steam Frame.
