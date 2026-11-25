# Framey Two-panel Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Provide an animated launcher above the existing bottom-bar icon that opens the chosen page in a separate window to the right of Steam or the active 2D app.

**Architecture:** Keep one active frontend, shared page ownership and two presentation sizes. Separate target discovery, validated native geometry, and placement lifecycle. Preserve honest recovery when capabilities are missing.

**Tech Stack:** Existing Python/ctypes/OpenVR, vanilla JavaScript, unittest, JavaScript fixtures and Playwright.

**Spec:** `docs/superpowers/specs/2026-10-27-two-panel-modes-design.md`

## Global Constraints

- Preserve Framey's bottom-bar entry point, plugin capabilities, trusted-input boundary and one-active-frontend ownership.
- No new runtime framework, Decky UI dependency or second frontend process.
- Use the clean experimental checkout; preserve main and all unrelated repositories.
- Do not publish or install to a device as part of local implementation.
- Do not treat a simulated anchor as verified native application tracking.
- Everything must be navigable without scrolling, using bounded screens and visible Previous/Next controls.
- Preserve the magenta, blue, black and white color scheme and existing logo.

## Review Focus

- Long plugin names and many plugins remain navigable without a narrow scrolling rail.
- Legacy direct routes, hidden pages and plugin subpages remain reachable with correct lifecycle callbacks.
- Surface changes retain the selected route and do not duplicate subscriptions or workers.
- Missing, stale or invalid target geometry cannot move another overlay or show Framey at the origin.
- Startup/restart failure preserves the saved requested placement and provides a bounded recovery path.

### Task 1: Shared navigation and two presentation sizes

**Files:** `inject.js`, frontend fixture tests, `tests/browser/panel.spec.mjs`.

**Interfaces:** Consume `window.FY_RUNTIME.panel_layout` (`compact` or `window`, with a conservative default). Keep `Framey.open(route)`, the private `tab()` interface, public plugin APIs and route IDs intact. Expose the current presentation for a runtime update through private `setPanelLayout(layout)` if needed.

- [x] Add failing behavior tests for Plugins/Browse/Settings navigation, Back/Close, direct routes, ordering/hidden pages and compact/window sizing.
- [x] Run the focused fixtures and confirm expected failures before implementation.
- [x] Replace the page rail with header, clear grouped destinations, paginated installed plugin list and nested plugin routes. Preserve keyboard and lifecycle behavior.
- [x] Update browser expectations around intentional layout changes and verify actual focus, overflow and controller-sized controls.
- [x] Run affected frontend fixtures, then all JavaScript fixture files and browser cases.

### Task 2: Anchor capability discovery and geometry

**Files:** `fy_overlay.py`, `fy_overlay_browser.py` only if evidence supports a browser discovery path, `tests/test_fy_overlay.py`, native placement documentation.

**Interfaces:** Known-overlay positioning accepts a validated target key and placement; only Framey's own handle is mutated. Preserve old `position_right()` until controller migration is safe. Active-app identity must come from a verified source; lack of that source is an explicit incomplete capability, not a guessed implementation.

- [ ] Complete arbitrary 2D app-to-overlay discovery. Bottom-bar geometry and the Steam-host path are implemented; the remaining compositor investigation is deferred.
- [x] Add failing geometry/ownership tests for above-bar positioning, non-Steam known anchors, scale, invalid transforms and stale targets.
- [x] Implement minimal verified primitives and run the focused Python tests.
- [x] Document exact native capability and hardware-verification limits. If arbitrary app discovery remains unavailable, preserve that as a concrete outstanding task while completing independent UI work.

### Task 3: Controller and settings integration

**Files:** `fy_panel.py`, `fy_state.py`, `framey.py`, `fy-settings.js`, associated Python and settings fixtures.

**Interfaces:** Publish confirmed layout and placement state. Presentation uses `compact`/`window`; migrate old placement preferences only when replacement native anchors are supported. Keep status error output bounded and free of native diagnostic detail.

- [x] Add failing tests for layout propagation across surface switches, requested-placement preservation on failed startup, status honesty and recovery.
- [x] Integrate working anchors with controller polling, visibility and cleanup; do not present unsupported automatic-app selection as active.
- [x] Keep panel diagnostics under Support, with no ordinary placement chooser; retain explicit legacy recovery internally as required.
- [x] Run affected Python and settings fixtures, then the full Python suite, configured quality gates and all frontend tests.

### Task 4: No-scroll built-in screens

**Files:** `fy-settings.js`, `fy-catalog.js`, `fy-modules.js`, `fy-picker.js`, `fy-report.js`, `fy-dev.js`, relevant JavaScript fixtures.

**Interfaces:** Private shell `setPageNavigation({index,count,onPage,label?,onBack?,title?,onLayout?})`, `clearPageNavigation()` and `panelLayout()` provide fixed header/footer controls. Builtins render bounded semantic screens. Modal flows own equivalent pagination, without stealing navigation behind the dialog.

- [x] Add failing behavior tests for reaching every category/action and later list/text pages without scrolling at compact and window sizes.
- [x] Implement category screens, explicit list pages and bounded long text while preserving async state, transaction checks, disclosure and focus.
- [x] Keep main controls at least 48 pixels high; do not clip content or reduce typography to cram screens.
- [x] Run related fixtures and actual browser checks, including layout changes, long names, empty/error states and cross-page cancellation.
- [x] Identify any shipped plugin surfaces requiring matching navigation changes; preserve third-party plugin ownership and do not mutate arbitrary plugin DOM.

### Task 5: Review and completion evidence

**Files:** Relevant development/panel documentation and private validation receipts.

- [x] Inspect real browser captures of compact and side presentations and fix material defects in a bounded pass.
- [x] Obtain an independent review of the local diff and verify important fixes.
- [x] Scan exact changed content for the retained privacy policy before any commit or publication. Use generic contributor identity if making a local commit.
- [x] Report implemented behavior, verified checks and outstanding native/hardware limitations without claiming the whole goal complete when required capability remains unresolved.

### Task 6: Fan-out launcher and side-page handoff

**Files:** `inject.js`, `bar.js`, `fy_panel.py`, `framey.py`, placement primitives and focused browser/Python fixtures.

**Interfaces:** The compact presentation renders only the launcher. Private frontend launch requests identify an allowed route, not executable code. The bottom-bar toggle supplies a validated normalized horizontal icon anchor. The controller prepares the separate page, replies before changing frontend surfaces, and restores a usable launcher after failed placement without embedding the page inside Steam.

- [x] Add failing tests for extensible plugin tiles, stores/Options destinations, no-scroll batches, accordion opening/selection/collapse, physical press feedback and reduced motion.
- [x] Add failing tests for actual icon anchoring and reply-before-handoff to the selected route, rejecting invalid route/anchor input.
- [x] Implement one-active-frontend launcher/page transitions using the existing owned popup lifecycle; preserve selected page and plugin disposal semantics.
- [ ] Add verified app-to-overlay identity for other 2D apps. The implemented Steam-host path rejects known nonempty app focus rather than substituting Steam silently.
- [x] Remove the superseded user-facing compact/window placement chooser from ordinary Settings. Retain only appropriate internal compatibility/recovery interfaces.
- [x] Run the focused and integrated suites and inspect actual launcher/side-page renders against the supplied layout and palette.

## Remaining device acceptance

Arbitrary 2D app anchoring and physical acceptance of the new launcher/page flow remain open. Diagnostic restarts are deferred. The local source checks and reviews do not establish installed-device behavior, controller comfort, gameplay behavior or automatic restoration after SteamVR restart. No publication, release or device installation was performed.
