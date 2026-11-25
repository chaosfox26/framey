# Launcher-Owned Navigation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for coordination and superpowers:dispatching-parallel-agents for independent frontend domains. Steps use checkbox syntax for tracking.

**Goal:** Put screen selection in the accordion and give each side window one focused screen matching Steam's bar.
**Architecture:** Core owns the route-only shell and paged accordion. Core-owned extra screens can be exposed as private `K.windowPages` objects with `{id,name,short?,render,leave?}`; plugin screens use existing `Framey.pages.register` (at most eight per plugin). Old compact non-launcher layouts remain compatible. Each owned window screen has no section tabs or Previous/Next controls.
**Tech Stack:** Existing dependency-free JS, Python native panel bridge, inert fixtures and Playwright.
**Spec:** ../specs/2026-10-27-launcher-owned-navigation.md

## Global Constraints
- No scrolling for main screens; preserve complete data and all actions via filtering and in-place contextual editors.
- Existing original bottom-bar icon and side placement remain intact.
- Keep SteamVR running and cooling components/profile unchanged.
- No commits, pushes or publication; reuse current experimental checkouts.

## Review Focus
- Closing and choosing another shortcut must start the chosen route, never a remembered sibling section.
- Large/long collections must remain reachable and data must not be discarded merely to fit.
- No actual fan change is made during UI verification; cooling request shapes remain intact.
- Hidden section tabs must not remain in the keyboard path or take focus.
- Every extra shortcut must be accepted by the native route validator and rendered correctly after recreation.

### Task 1: Route-only shell, theme, accordion navigation
Files: inject.js, fy-ui.js, framey.py, focused JS/browser/Python route tests.
- [x] Add failing window-shell tests: selected title and Close, no section tabs or sibling Back, Steam styling and focus inside the current screen.
- [x] Make accordion arrows explicit and cap visible shortcuts at four; add private extra builtin shortcuts and preserve plugin pages.
- [x] Add exact builtin settings routes to native validation; keep unknown routes rejected.
- [x] Apply matching shell/control styling in fy-ui.js, keeping inject.js within its size cap.
- [x] Verify changed core tests, launcher browser cases and route tests.

### Task 2: Fan Control focused screens (independent)
Files: frame-fan/main.js and its frontend tests.
- [x] Use existing plugin pages for distinct preset/custom/graph screens, which appear as accordion shortcuts.
- [x] Keep each selected window screen self-contained; remove window Previous/Next carousel, preserve legacy compact behavior.
- [x] Verify profile/config/graph access and unchanged backend request shapes using inert state.

### Task 3: Options focused screens (independent)
Files: fy-settings.js and its settings tests.
- [x] Expose private builtin routes `#settings:preferences`, `#settings:updates`, `#settings:maintenance`, `#settings:worker`, `#settings:support` through K.windowPages.
- [x] Render each chosen category as a single bounded window screen with no Previous/Next carousel; preserve existing compact path.
- [x] Verify asynchronous actions, busy/error state and settings preservation.

### Task 4: Catalog and management window access
Files: fy-catalog.js, fy-modules.js, related catalog/management browser fixtures.
- [x] Keep collection access through bounded search/filter results rather than side-window page carousels; preserve all actions and complete metadata.
- [x] Keep detail/edit actions contextual within the selected destination, without switching sibling destinations.
- [x] Verify collection reachability and action state.

### Task 5: Integrate and install
- [x] Run appropriate suites and one fresh scoped review. Fix important findings with regressions.
- [x] Render representative routes together at real window bounds and inspect once, then confirm actual defects once.
- [x] Update documentation; deploy with existing backup/hash/health checks and no SteamVR restart.
- [x] Inspect live selected route and return a truthful preview/capture.

## Final execution notes
The user enlarged the native side window to 800×720, enabling a single combined Fan Control screen instead of the originally planned Fan subpage shortcuts. The shell uses 778×698 with approximately754×609 usable content. Separate Options category shortcuts stay in the accordion; compatibility consumers retain legacy embedded navigation. Two fresh-review findings were fixed with regressions before the direct local device update.
