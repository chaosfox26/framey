# Choosing where Framey appears

Open **Settings > Framey panel** to choose where the panel appears. **Inside
Steam** is the default and the fallback when a separate panel is unavailable.
When Framey is open:

| Mode | With the Steam menu open | After the Steam menu closes |
| --- | --- | --- |
| **Inside Steam** | Framey opens inside Steam's interface. | Framey is visible only with Steam's interface. |
| **Beside Steam dashboard** | Framey opens as its own panel to the right of Steam. | The separate panel is hidden. |
| **Floating during games** | Framey opens to the right of Steam and follows its position. | An open Framey panel remains at its last position in the VR world. |

The separate modes keep Framey's existing 520-pixel interface and navigation.
They use an owned popup in Steam's existing browser host. Framey transfers its
single active frontend between surfaces. A surface change reloads plugin
frontends; their saved settings remain available, while arbitrary in-memory
view state is recreated.

## Open and close the panel

If Steam's System menu is available while a 2D or VR game is running, open it
and use the existing **Framey** button on its bottom bar. That button opens or closes Framey in the
selected mode. In **Floating during games**, close the Steam menu to leave the
open Framey panel visible. To dismiss it, return to the Steam menu and use the
Framey button again, or return to **Inside Steam** in Framey's Settings.

Framey does not take over the Steam/System button or provide a guaranteed global
controller shortcut. **Controller pointers may interrupt game controls while
the floating panel is visible.** A visible panel does not guarantee that a game
pauses or stops receiving input. Close Framey when you want to resume ordinary
game controls.

While Steam's menu is open, the separate panel follows Steam's position on its
right. Framey accounts for Steam's physical scale when positioning it. In
floating mode, closing the menu holds the last world position; this is not an
always-following head-locked panel.

## Availability and recovery

The separate modes are experimental. They depend on Steam's private popup API
and the OpenVR features available in the installed Steam Frame runtime. A Steam
update, missing interface or failed native operation can make them unavailable.
**Inside Steam** remains the recovery path; **Retry** in **Settings > Framey
panel** refreshes the reported state and lets you try a separate mode again.
Selecting the mode performs its availability checks. After a failed move, use
the last confirmed choice shown in Settings rather than assuming the requested
mode took effect.

Returning to **Inside Steam** closes Framey's own popup and releases its native
overlay connection. It does not close Steam's dashboard or another application's
windows. Mode changes retain Framey's selected page and open state when the
interface can be transferred successfully.

For plugin authors, there is one active Framey frontend surface. The separate
panel is internal to the loader, not a new public injection target or developer
target name. Existing `main` and `bar` injections still refer to Steam's original
surfaces; they do not become aliases for the separate panel.

## Verification and resource limits

Commit `4641a14` was deployed on an ARM64 Steam Frame running SteamOS VR 0.5.6.
Trusted browser-debugger clicks on the actual Settings controls verified **Inside
Steam → Beside Steam dashboard → Floating during games → Inside Steam → Beside
Steam dashboard**. There was one active frontend and no service restart.
Dashboard/floating transitions reused the popup; returning inside Steam restored
Settings in the main interface and removed the popup target and its owned
reference. Fan Control telemetry rendered in the separate panel without changing
fan profiles, presets or its privileged service. The bottom-bar button hid the
Framey overlay while Steam's dashboard stayed visible, then reopened the same
separate target and Fan page; Settings remained usable.

Native checks confirmed that Framey's overlay was visible with Steam's dashboard
open and that extra gameplay pointer interaction was off in that state. Hidden
creation, hide/close cleanup and scaled native positioning also passed. **The
Steam-menu-closed floating state and its gameplay pointer activation remain
unverified.** No 2D or VR game was launched. Physical controller aiming,
readability, comfort, game input and recovery during gameplay still need their
own validation. See the
[automated-check receipts](using.md#verification-status) for their separate scope.

The targets remain less than **400 MB total added memory** for Framey, its
workers and its interface in Steam, with minimal CPU use while idle. Reusing
Steam's browser host does not prove those targets. In a 21.3-second sample with
floating mode selected and Steam's dashboard visible, the service cgroup used
about **32.58 MiB**, with average CPU about **0.486% of one core**. A separate
38.08-second closed-panel observation, with the Fan worker already loaded,
measured **47.18 MiB** and **0.176% of one core**. A warm comparison of whole-Steam
PSS between dashboard and embedded modes differed by about
**9.6 MiB**; that includes other Steam activity and cannot be attributed exactly
to Framey or its GPU allocations.

These short observations do not establish sustained idle cost, peak memory,
gameplay performance or total added memory. The targets are not guarantees for
arbitrary plugins. See [verification status](using.md#verification-status) and the
[recorded performance evidence](decky-comparison.md#memory-and-cpu-target) for the
scope of earlier measurements.
