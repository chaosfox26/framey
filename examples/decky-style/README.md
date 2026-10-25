# Session Check-ins: a Decky-pattern source port

This independently authored example tracks completed practice, reading and planning
sessions. Its goal and check-in count survive plugin reloads. It uses a small shared
source adapter to illustrate familiar Decky API patterns while registering and
rendering through Framey's native API.

This is a **source port**, not Decky bundle compatibility. Existing Decky bundles,
React UI components, Decky Python dispatchers and Steam Deck hardware integrations
do not automatically run here. No Decky plugin code is included. The example does
not control hardware, invoke sudo, start subprocesses or require npm/React.

## What “ported” means here

“Ported” means the author has adapted the source and produced a new **Framey-native
package**. This example demonstrates that process with original example code; it
is not a converted release of an existing published Decky plugin.

- **Manifest:** `plugin.json` uses Framey's `api`, `framey`, `platform` and backend
  limits. A Decky manifest is not copied unchanged or renamed and assumed to work.
- **Backend:** `backend.py` implements Framey's `call(method, args)` dispatcher and
  native settings/events. The positional arrays are an explicit contract with
  the helper, not automatic support for arbitrary Decky backend methods.
- **UI:** `source.js` uses one `Framey.register`, native controls and native
  cleanup. The limited helper adapts call/event syntax; it does not provide React,
  Decky components, Steam UI bindings or hardware functionality.
- **Package:** generated `main.js` includes the exact helper plus adapted source.
  The runtime package contains `plugin.json`, `main.js` and `backend.py`; it does
  not require the original Decky package alongside it.

An unmodified Decky package still needs a source review and an actual port. This
example is not evidence that another plugin works merely because it uses similar
API names or lists ARM64 in its metadata.

## Run it

1. Copy this directory into your Framey installation's `plugins/decky-style`
   directory. Keep that folder name so it matches `plugin.json`.
2. In Framey Settings, select **Reload modules**, then open **Check-ins**.
3. Choose **Practice**, **Read** or **Plan**, select **Save goal**, and select
   **Check in** after a session. **Reset count** clears the total. **Refresh**
   reads the saved snapshot again.
4. Reload the plugin to confirm the goal and count remain saved. Disabling or
   removing the plugin keeps its saved data unless you also choose to delete data.

`main.js`, `backend.py` and `plugin.json` are the runtime files. `main.js` is already
self-contained and loads through Framey's ordinary per-plugin wrapper. No import
loader or build step runs on the headset. The manifest explicitly supports
`linux-aarch64` (ARM64) and `linux-x86_64`; no native binary is shipped. Those
platform entries are **metadata evidence only**, not a hardware test result.
The automated tests exercise manifest policy, ZIP extraction, native JavaScript
loading, calls/events and Python settings through the real worker on the test
host. They do not establish ARM64 execution, controller input, Steam UI behavior
or compatibility of an original Decky release. Device UI behavior still needs
headset testing. This local example intentionally has no
update-source URL; give your own port its own repository identity before publishing.

Settings use `framey_api.settings` in the plugin's config directory. The Python
backend validates arguments and writes settings before emitting an event. Events
can be dropped, so the call response also carries the complete saved snapshot and
**Refresh** can recover it. The count is a total across goals, not a separate count
for each goal. Calls are short and serialized; there is no periodic polling or
background job.

## Source mapping

| Familiar source pattern | This port |
| --- | --- |
| `call("save_goal", goal)` | Adapter sends native `api.call("save_goal", [goal])`; `backend.py` explicitly validates and unpacks the positional argument array. |
| `callable("check_in")()` | Returns the raw backend snapshot; a truthy `{error}` response becomes a rejected promise handled by the UI. |
| `addEventListener("check_in", handler)` | Own-backend `api.events.on`; Python emits `[snapshot]`, so the adapter invokes `handler(snapshot)`. |
| `removeEventListener(name, handler)` | Removes the exact listener installed earlier; the return from `addEventListener` is the handler, not an unsubscribe function. |
| Saved plugin setting | Native backend `fy.settings.set/get`, read and written through calls. |
| Plugin dismount | Native `api.cleanup` explicitly detaches the event handler and calls the adapter's idempotent `cleanup`; in-flight UI work is ignored after disposal. |
| Plugin UI/registration | One native `Framey.register({render})`, `Framey.el`, and `api.ui` controls. `definePlugin` does not mount a Decky UI or translate lifecycle hooks. |

The adapter is [compat/decky-source.js](../../compat/decky-source.js). It does not
replace the Framey API or add globals. Its cleanup does not cancel work already
running in Python; this example's backend has no ongoing resources to release.

## Edit and verify

Edit `source.js`, then run this from the repository root:

```sh
python3 examples/decky-style/build.py
python3 -m unittest discover -s tests -p 'test_fy_decky_example.py'
node tests/js/decky-example.test.js
```

`build.py` prepends the **exact** repository adapter to `source.js` and writes
`main.js`. Do not maintain a second helper implementation inside the example.
The checked-in generated file lets normal installations run without a build tool.
The JavaScript test uses the repository's existing jsdom API harness; use its
`FRAMEY_JSDOM` or `NODE_PATH` setup if jsdom is not on the default module path.
Python tests use temporary storage and the real Framey SDK and worker protocol.
The package fixture is generated only from this example's three runtime files,
round-trips through Framey's safe ZIP extraction, and starts the extracted backend
in the real worker. A JavaScript test copies only those runtime files into a fresh
directory and loads `main.js` with the normal native plugin wrapper. These are
host-side checks of this port; no original Decky archive or publisher feed is used.
