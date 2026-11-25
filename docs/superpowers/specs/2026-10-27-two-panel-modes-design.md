# Framey two-panel interface

## Direction

Keep the existing bottom-bar icon. Clicking it opens a small fan-out launcher directly above the icon. Its horizontal tiles provide quick access to installed plugins and their menus, the Framey Store, the Decky catalog and Options. The launcher grows with installed plugins; it is not limited to four fixed buttons. Use explicit page controls when the available width is exhausted, retaining no-scroll navigation.

Selecting a tile collapses the launcher inward like an accordion and fades it away, then opens the respective page in Framey's separate window immediately to the right of Steam's store/library window or the active 2D application window. The page window is outside the host window. This launcher-to-page flow replaces the earlier two competing panel-mode choices. There is no need for a permanently floating gameplay panel: the Steam/System menu provides access to the existing bottom-bar icon during VR.

This revision replaces the previous panel structure while preserving plugin capabilities and Framey's identity.

## Presentation

One active frontend shares navigation, ownership and selected page across launcher and page presentations. The compact presentation is a launcher rather than a miniature copy of the full interface. The roomier side panel keeps Plugins, Browse and Settings navigation, with direct opening to the selected tile's page. Put plugin management within Plugins, and keep the Framey Store and browse-only Decky catalog distinct within Browse. Preserve direct routes, plugin subpages, developer access, hidden/order preferences, keyboard navigation, return focus, trusted-input checks, cleanup and error recovery.

Launcher tiles have a raised, glossy gradient surface in the existing palette. A pointer/controller press visibly depresses the tile with a short inward movement and inset shading. Release activates the selection and starts the launcher collapse. Keep feedback clear without delaying ordinary input, and honor reduced-motion preferences. This explicit surface direction takes precedence over generic flat-design defaults.

Use readable labels, consistent controls, clear Back and Close actions, strong focus indicators, and controller-sized targets. Keep advanced options out of routine navigation. Do not introduce a UI framework, Decky UI dependency, additional runtime process, or duplicate plugin frontend.

## No-scroll navigation and color

The user requires everything to be navigable without scrolling. Use explicit bounded screens and Previous/Next controls for longer lists and text. Settings uses category and detail screens; catalogs, management, file picking, reports and developer tools retain their actions through explicit pages. Keep navigation and pagination controls visible. Do not satisfy this by clipping, hiding actions, shrinking text or forcing focus to scroll an off-screen control. Preserve state and event handlers when changing pages. Arbitrary third-party plugin layouts require their own compatible screen structure; do not silently reparent unknown plugin DOM.

Preserve Framey's magenta, blue, black and white identity and existing logo. Use black surfaces and white text, blue navigation/focus and meaningful magenta accents. Retain established semantic danger treatments where necessary for actual destructive actions.

## Native placement

The launcher must use verified bottom-bar geometry and the actual icon's horizontal position. The side window must use a validated active application overlay identity and geometry; Steam's own window is a valid target when it is the active host. Read target geometry but modify only Framey's own overlay. Do not guess handles, treat pointer hover alone as active application selection, or place Framey at an arbitrary origin. Ordinary launcher/page actions must never silently display the page inside Steam's main content document. Keep legacy recovery internally while replacements are verified and report the confirmed state rather than claiming an unavailable placement.

The current code has a known Steam overlay anchor; arbitrary app-window discovery is not verified. Resolve this with a capability investigation before claiming active-app anchoring works. Keep installed OpenVR ABI signatures correct. Preserve the existing restart-restoration issue until a verified change fixes it.

## Scope and evidence

Implement and validate locally. No public push, main merge, release, channel change, or device installation is authorized by this implementation plan. Existing fixtures and real-browser checks cover UI behavior; native geometry and lifecycle require focused offline tests and subsequent hardware acceptance. Do not claim physical-controller, gameplay, thermal, updater, or whole-device acceptance from browser fixtures.
