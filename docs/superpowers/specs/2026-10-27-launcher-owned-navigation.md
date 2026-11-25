# Launcher-owned navigation

The user's current direction supersedes the previous shared side-window navigation and paging design.

- The original Framey icon remains on Steam's bottom bar and opens the accordion.
- The accordion has Previous/Next arrows revealing different shortcut buttons. Extra screens belong here, not in the side window's tabs or Previous/Next carousel.
- A chosen shortcut opens its own respective screen in the right-hand window. Closing it and selecting another accordion shortcut changes the screen.
- The window matches the shipped launcher: Steam charcoal #0e141b, 6px window corners, square 5px raised controls with clear borders that depress into the surface, white text and Framey blue/magenta accents.
- Preserve no-scroll access. All main controls for an individual screen fit together. Use search/filtering for large collections and contextual dialogs or in-place editing for a selected item; do not remove functionality or silently truncate data.
- Preserve arbitrary 2D-app refusal; no new device diagnostics or SteamVR restart. Existing installation authorization remains in effect. No root/cooling-backend changes, publication, commits or pushes.
- Third-party plugin content is outside our control; theme the shared shell without claiming to rewrite arbitrary plugin internals.
