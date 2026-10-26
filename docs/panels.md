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

The owned popup's hidden creation, hide/close lifecycle and native positioning
have been checked on Steam Frame. Automated checks cover the browser controller
and its failure handling. Full mode-switching and live interface validation are
still pending; no 2D or VR game was launched for these checks. Controller aiming,
readability, comfort, game input and recovery during actual gameplay need their
own on-device verification.

The targets remain less than **400 MB total added memory** for Framey, its
workers and its interface in Steam, with minimal CPU use while idle. Reusing
Steam's browser host does not prove those targets: its additional browser/GPU
cost and active-panel behavior still need measurement. These are not guarantees
for arbitrary plugins. See [verification status](using.md#verification-status)
and the [recorded performance evidence](decky-comparison.md#memory-and-cpu-target)
for the scope of earlier measurements.
