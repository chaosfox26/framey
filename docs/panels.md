# Launcher and side-page placement

Click the existing Framey icon on Steam's bottom bar to open the small launcher directly above it. Installed plugin shortcuts, Framey Store, Decky catalog and Options appear as tiles. Selecting a tile collapses and fades the launcher, then opens the chosen page in Framey's separate window to the right of the host window. The page is not embedded in Steam's content.

The icon-only launcher matches Steam’s dark bar, with square framed buttons, blue/magenta icons and tooltips. Its left/right arrows reveal at most four shortcuts at a time; it rises and expands when opened, then collapses inward and slides down after selection. Reduced motion skips the transition.

Each selected tool has one focused window with its own title and Close. Choose another tool or Options category from the accordion rather than Plugins/Browse/Settings tabs. Fan Control places presets, tuning, graph editing, actions and live readings together. Constant, Ramp and Graph change the editor in place. Owned catalog and management collections retain search and direct result selection; complete long details are contextual. Older embedded consumers retain their legacy navigation, and third-party plugin layouts remain their own responsibility.

## Current native scope

Current source supports the Steam host path when the audited focus fields are empty and the Steam target has valid native geometry. Arbitrary 2D app-to-overlay mapping remains unfinished. A focused app without a verified mapping produces an unavailable result rather than silently moving the page beside Steam and claiming app tracking.

The launcher uses the actual bottom-bar icon's normalized horizontal position. It samples the bar's top edge and accounts for physical scale. Native operations change only Framey's owned overlay; the target window is read, not moved. There is one active frontend, transferred between the launcher and page presentations. Recreating the popup temporarily suspends frontend injection so a page does not flash inside Steam during the handoff.

The launcher browser surface is 462 by 140 pixels, with a 72-pixel visible tray. The native full window is 800 by 720 pixels, with a 778 by 698 pixel interface. The native adapter retains bounded launcher-width compatibility for existing callers. Popup dimensions and native geometry are changed together. A surface change reloads plugin frontends; saved settings remain available, while arbitrary in-memory DOM state is recreated.

## Lifecycle and recovery

Use the bottom-bar icon through Steam's System menu when it is available. This does not add a universal controller shortcut or claim that a game pauses. The ordinary flow does not leave a permanently floating page visible after the Steam menu closes.

The source retains legacy placement values internally, but ordinary Options no longer offers the old Inside Steam/Beside Steam/Floating chooser. Support > Panel status contains read-only diagnostics and Retry. A failed move preserves the saved requested placement. Launcher selection errors keep a bounded Retry/Back path; after a native connection failure, use the bottom-bar icon to try opening the launcher again.

Steam's private popup host and the installed OpenVR interfaces can change. The launcher/page flow was installed directly on a headset on October 27, 2026. The launcher and Fan Control route rendered, and native readback verified the side-window position. Closing and recreating the popup can temporarily change Steam's visibility flag; target selection therefore uses audited app focus and native geometry, while dashboard visibility controls display. Physical controller acceptance remains incomplete. Automatic separate-panel restoration after SteamVR restart remains unverified; the earlier manual recovery below applies to the previously deployed layout.

## Earlier device verification and resource limits


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
