# Porting Decky-style source to Framey

Framey does not run arbitrary compiled Decky plugins. The optional
[`compat/decky-source.js`](../compat/decky-source.js) helper offers a small,
independently authored source-port surface. An author must adapt the frontend,
backend and manifest, then rebuild a Framey plugin. It is not the `@decky/api`
package and does not impersonate a Decky loader connection.

The familiar call, callable, listener and factory shapes are informed by the
public [Decky loader API](https://github.com/SteamDeckHomebrew/loader-api) and
[loader event implementation](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/plugin-loader.tsx).
No upstream implementation is bundled or copied by this helper. Preserve all
original licenses and credits when porting any third-party plugin source.

## Package assessments and source ports

A Store compatibility assessment describes evidence from a particular package;
it does not perform a source port. A catalog listing, a matching download hash,
a reference to this helper, or the absence of detected native binaries does not
establish that a plugin can run in Framey.

| Package evidence | What it establishes |
| --- | --- |
| A Framey source-port package | The author has provided a Framey manifest and appropriate native entry points: `main.js` for an adapted frontend and `backend.py` for an adapted backend. Check its Framey/API/platform requirements and dependencies as a Framey package. Including this helper alone is insufficient. |
| An unadapted Decky package | Decky entry points, API imports, React UI or backend conventions still need adaptation. JavaScript-only contents or an ARM64 binary do not resolve that loader mismatch. |
| An x86 native program required on ARM64 | That component needs an ARM64 build or a supported replacement. This helper does not translate native code. Finding one file's architecture does not establish the architecture of every other dependency. |
| An unknown or incompletely inspected payload | Compatibility has not been established. Do not treat missing evidence as either a successful port or proof of an architecture mismatch. |

Read **Compatible** as a finding limited to the checks and evidence shown;
it is not a promise of correct hardware behavior. **Not compatible** needs a
specific reason, such as an unadapted loader format or a demonstrated platform
mismatch. **Not checked** means the available evidence does not settle the
question. A matching checksum identifies the inspected download; it is not a
publisher signature, a code review or a device test. See the
[Store guide](catalog.md) for the inspection flow and its limits.

The adapter does not assess archives or expose a Store inspection API. It only
uses the plugin-owned calls, subscriptions and cleanup described below.

## Loading and registration

Prepend the helper's exact source to your plugin source when producing `main.js`,
or bundle its CommonJS export. No package installation is necessary. Framey
executes `main.js` inside a per-plugin function, so the helper declaration stays
local. Do not load it as a shared page script or assign it to `window`. Each
plugin creates its own adapter from the API returned by `Framey.register`.
The [independently authored example](../examples/decky-style) includes a
repeatable, dependency-free build recipe.

```js
// compat/decky-source.js is prepended before this plugin source.
const api = Framey.register({
  render(box) {
    box.textContent = "A source port using native Framey rendering";
  },
});
const decky = createDeckySourceAdapter(api);
const read = decky.callable("read");
read().then(value => api.log("info", JSON.stringify(value))).catch(error => {
  api.log("error", error.message);
});
const listener = decky.addEventListener("changed", value => {
  api.log("info", JSON.stringify(value));
});
// For early removal: decky.removeEventListener("changed", listener).
```

For tests or a bundler, use
`const { createDeckySourceAdapter } = require("./compat/decky-source.js")`.
Framey's page loader does not provide CommonJS `require`; bundle or prepend
before installation. Register subscriptions once during startup, not every time
Framey calls `render`. Native [plugin registration and rendering](plugins.md)
remain explicit.

## Supported contract

| Operation | Source-port behavior |
| --- | --- |
| `createDeckySourceAdapter(api)` | Requires a plugin-owned API with `call`, `events.on` and `cleanup`. Returns an independent frozen adapter; optional `api.log` reports listener failures. |
| `call(method, ...args)` | Calls `api.call(method, args)`: the backend receives one positional argument array, including `[]` for no arguments. Returns a promise for the raw result. Method must be a nonempty string. |
| `callable(method)` | Returns a reusable function that forwards positional arguments to `call`. |
| `addEventListener(name, listener)` | Registers a listener and returns that same listener. Repeating the same name and function has no additional effect. |
| `removeEventListener(name, listener)` | Removes the matching registration. Missing or already removed listeners are harmless. The return value of `addEventListener` is a listener, not a disposer. |
| `definePlugin(factory)` | Returns a function forwarding invocation arguments to the factory. It does not call the factory, register a plugin, mount returned content, or wire `onDismount`. |
| `cleanup()` | Idempotently removes this adapter's listeners and rejects its pending calls. Automatically registered with `api.cleanup`. |

Event names must match Framey's `[a-z0-9_.-]{1,40}` grammar. Emit an array from
the backend to represent positional event arguments: `fy.events.emit("changed",
[value, revision])` calls the frontend listener as `listener(value, revision)`.
A non-array payload is delivered as one argument. To deliver an array as one
argument, emit `[the_array]`. An empty payload array means zero arguments.
Framey's event metadata, including dropped-event counts, is not forwarded; use
`api.events.on` directly when the port needs that information. Subscriptions
remain subject to Framey's per-plugin limit and best-effort delivery.

The backend needs an explicit Framey dispatcher; existing Decky backend classes
are not discovered automatically:

```python
async def call(method, arg):
    if method == "read" and arg == []:
        return {"value": 1}
    return {"error": "Unsupported operation", "code": "unsupported_method"}
```

Validate arguments before dispatch. Only expose the methods the port needs;
never turn this dispatcher into arbitrary attribute or command execution.

## Errors and lifecycle

A truthy top-level `error` in a backend reply becomes a rejected `Error`; a
string `code` is preserved. Other values pass through, including any
`success`/`result` fields, which are not unwrapped. Reserve top-level `error`
for failures. Transport rejections and synchronous call errors also reject.
Invalid listener names or factories throw `TypeError`; invalid calls reject.
Listener throws and rejected listener promises are contained and reported via
`api.log("error", ...)` when available; other listeners can still run.

Disabling, reloading or unloading the Framey plugin runs adapter cleanup.
Manual `cleanup()` releases its cleanup registration and is safe to repeat.
Both future calls and pending adapter calls reject with code
`adapter_disposed`; late backend replies are ignored by the adapter. Calls
already sent to the backend are not canceled, and pending backend effects may
still complete. Adding a listener, making a callable or invoking a factory
after cleanup throws with that code. Removing a listener remains harmless.
Cleanup removes only registrations owned by this adapter. Native timers,
resources, pages and other listeners should use Framey's own tracking helpers.

## Explicit limits

This surface covers source porting of a few API idioms only. It does not load
Decky bundles, resolve `@decky/api` or `@decky/ui` imports, implement React hooks
or components, reproduce Steam's internal UI tree, emulate Decky manifests,
legacy `serverAPI`, routing, toaster, file picker, fetch proxy, tab injection,
or Decky backend lifecycle. Use native Framey APIs for those features and
replace unsupported behavior deliberately.

On ARM64, JavaScript glue does not make x86_64 native executables, Python
extensions, shared libraries or dependencies run. Obtain or build ARM64
versions and declare the correct manifest platforms. Steam Deck-specific
hardware paths, fan controls, power settings and devices need a supported
platform-specific implementation; this helper provides no hardware emulation.
Desktop tests establish the adapter contract, not headset hardware or full
Decky runtime compatibility.
