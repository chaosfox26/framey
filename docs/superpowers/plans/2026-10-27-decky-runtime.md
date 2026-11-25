# Framey Decky Runtime Implementation Plan

> **For agentic workers:** Use the executing-plans workflow, with independent implementation tasks delegated where appropriate. Steps use checkbox syntax.

**Goal:** Import and exercise original Decky packages through an independent, React-free Framey compatibility profile.

**Architecture:** A native DOM element/hook engine and Decky-facing host map supported UI/API behavior onto Framey. A Python bridge runs unchanged backend classes inside existing per-plugin workers. An offline importer preserves original package bytes and adds explicit wrappers and capability status.

**Tech Stack:** Existing Python standard library and plain JavaScript; current unittest, Node and Playwright tooling. No React/ReactDOM/Preact/Decky implementation dependencies.

**Spec:** docs/superpowers/specs/2026-10-27-decky-runtime-design.md

## Global Constraints

- Existing dirty UI changes remain intact; no commits, publication or SteamVR restarts.
- Original store JavaScript/Python bytes and licenses remain preserved.
- Missing capabilities are explicit; panel support is not equivalent to Steam badge integration.
- Execute third-party code only in explicit compatibility experiments or installed trusted-plugin lifecycle, never during inspection.

## Review Focus

- Cleanup removes effects, listeners, mounts, timers and pending bridge resources on disable/reload.
- State remains with keyed components and refs survive ordinary rerenders.
- Modern and legacy calls cannot invoke private lifecycle methods or mix argument protocols.
- Corrupt or hostile archives cannot escape staging or replace linked user files.
- Unsupported Steam APIs yield truthful capability failures, including when plugin code catches exceptions.

### Current integration slice: cached Steam reads and local sidebar controls

Promote the independently reviewed fixed-read provider and passive main-world transport into native Framey services. Bind reads to an installed, enabled plugin and its current frontend token generation; revoke pending reads on reload/unload and drain the service at shutdown. Only `visibleApps`, `app`, and `focus` operations are admitted. The host selects existing owner-checked Steam page contexts, excluding Framey injection surfaces and configured developer targets. No plugin-supplied target, endpoint, expression or CDP method is accepted. Cached category numbers remain raw Steam metadata; focus snapshots do not imply running-game membership or ProtonDB/Steam Frame verification.

Use a separate passive channel because existing surface sessions boot/inject Framey and may evaluate in an isolated world. Keep missing capabilities explicit, bind default execution contexts to the observed main frame, and invalidate leases on navigation or authority changes. Reuse the reviewed adapter and transport after production-boundary tests. Root owns authentication, target policy, packaging and shutdown; independent workers own service, adapter, transport and public API tests. Local sidebar controls expose only plugin-owned pages through the existing renderer, with state/focus cleanup and no Steam router claim.

Private backend acceptance also advances from source inventories to six exact fixture calls in Storage Cleaner and Steam Status. Use unchanged originals in a validated namespace with entirely synthetic user/config/cache paths, machine assertions and source pins. A constants-only baseline and real Framey bridge dispatch are separate evidence. No actual user-cache deletion, network speed test, headset mutation or broader recipe execution belongs to this slice.

### Task 1: DOM runtime

Create compat/framey-view.js and tests/js/framey-view.test.js. Export createFrameyViewRuntime(document), returning React-shaped element/hook contracts, ReactDOM-shaped createRoot/render/unmount, JSX jsx/jsxs and disposal, implemented independently. RED then GREEN: state updates affect real DOM; effects run/clean once per dependencies; keyed input identity survives reorder; class/ref/context and SVG events work; unmounted setters do nothing. Verify using Node tests with existing jsdom.

### Task 2: Python backend bridge

Create compat/decky_backend.py and tests/test_decky_backend.py. Export DeckyBackend(root, config, data, logs, logger=None, emit=None), async start(), call(method,args,legacy=False), stop(). Honor Plugin instance versus class-self api_version convention, original module-local imports, decky/decky_plugin constants and emit; no root flags, no private RPC, no fake Decky version. One backend per process, with cleanup. RED then GREEN: modern settings persist, legacy kwargs dispatch, private calls fail before invocation, background main is stopped, initialization failures propagate and modules/env restore. Real settings paths are isolated Framey paths; legacy migration of existing external state is unsupported explicitly.

### Task 3: Host and importer

Create compat/decky-host.js, tools/framey_decky.py and focused tests. Host consumes the Task 1 runtime and a plugin-owned Framey API; importer bundles wrappers and Task 2 bridge around original archive entries. Loader handles ESM and legacy IIFE, native components, calls/events and teardown with unsupported capabilities collected, not no-ops. Import verifies expected archive hash and safe extraction, rejects root/native initial profile and unsafe output destinations, records provenance and legal files. Verify original ProtonDB panel/backend first, then additional packages. Update docs with exact evidence and limits.

### Task 4: Integration and review

Run focused and project checks, independently review this feature's files and real-package outcomes, repair important defects with RED/GREEN tests, and report the initial supported profile without overstating full plugin compatibility. Do not commit or deploy incomplete capabilities as stable store support.

### Task 5: Confirmed experimental import from the existing Decky Store UI

Close the UI import gap in the user's original requirement. Factor reusable importer into root fy_decky_import.py; keep tools/framey_decky.py as the same developer CLI facade. Ship the root helper and three compatibility runtime files through MANIFEST. Expose a versioned FY_RUNTIME.decky_compat capability only when those files exist. Add separate core-only catalog_import (profile1, confirm, officialsource, serverderivedid, current listing+selectedversion identities); preserve catalog_install's existing Decky rejection. Fresh download/hash/bounded inspection on each import, no archive/verdict cache relaxation. Only new installs: refuse occupied/symlink/stage/backup/pending paths, use existing Framey transaction/reload/backend_check and rollback only owned new files. Original source/digest/version/profile receipt and catalog-managed marker prevent accidental GitHub updater crossover.

The existing Decky details UI gets Review experimental import only when capability is present. Freeze selected listing+version identity for confirmation; selection/source changes invalidate it. Import jobs use distinct selected-version keys; ignore stale callbacks and retain normal browse/inspect behavior with capability absent. Success means package installed/backend started, not all plugin features supported. Use existing confirmation screens, no new sidebar navigation. RED/GREEN backend tests cover wrong/stale identities, nondefault version, profile/flags/hash/path/collision/rollback, and UI tests cover review/cancel/version change/stale completion. Re-run affected loader/catalog/type tests and final checks. Review this additional integration separately before any deployment.

### Task 6: Native plugin dialogs and confirmation behavior

The broad bootstrap probes do not exercise action-triggered dialogs. Twenty initial-profile-eligible original bundles reference showModal/ConfirmModal/ModalRoot; original Simple Timer, Storage Cleaner and Steam Status provide concrete interaction paths. Add independently authored Framey-owned dialog rendering within the plugin iframe, using the existing renderer and trusted-plugin lifecycle. Do not discover or reuse Steam modal components.

Implement the supported public subset: showModal(element,parent?,props?) with Close/ClosedPromise/Update; ModalRoot and ConfirmModal; basic DialogHeader/SubHeader/Body/BodyText/Footer/Label/ControlsSection containers and DialogButtonSecondary. Initial supplied closeModal callbacks chain through actual dismissal; programmatic Close does not invoke callbacks. Update replaces the element without reconnecting the old close callback chain, matching the public result contract. Confirm buttons respect disabled/alert/middle props. Escape, cancel and backdrop behavior must be explicit and not accidentally confirm an operation. Restore focus, retain panel state, enforce bounded dialog growth, and clean all roots/listeners/promises on disable or fatal render failure. Unsupported external/popout browser contexts fail explicitly; no host or Steam window navigation.

RED/GREEN browser tests must exercise actual DOM controls, callback counts, disabled buttons, Close vs Cancel semantics, ClosedPromise, Update replacement, Escape/backdrop options, nested dialogs/focus, route-dialog coexistence and disposal/throwing render cleanup. Use inert synthetic backend handlers for destructive confirmation tests. Then test one original package's safe dialog path and cancellation with its original frontend. Do not invoke any real suspend, delete-data or hardware action. Re-run focused host/wrapper tests, all browser tests and configured checks. Review this extension separately.

Public behavior references (read for contracts, not copied as implementation): https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/main/src/components/Modal.ts and https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/main/src/components/Dialog.ts.

### Task 7: Package-local PNG delivery (next slice)

Evidence: both eligible Music Control variants reference the same packaged default album-art PNG and RadiYo! references a packaged station logo. Original bytes exist but hardcoded Decky asset URLs are unavailable. Design a bounded exact-package PNG map at import time and resolve only those asset URLs within the owned iframe for rendered image props and direct fallback assignments. Keep external networking, CSS/fonts/SVG/scripts/module chunks outside this slice. Test actual decoded dimensions, fallback, cross-package rejection, untouched originals and resource cleanup. Do not start host edits concurrently with Task 6.

### Later stage: combined code hardening and maintainability

User explicitly placed this AFTER the functionality goal. Once every claimed plugin function has the required evidence, review the combined implementation for readable, professional code, clear module boundaries, duplication, regression fixes, documentation and maintainability. Keep per-feature correctness reviews active during implementation; do not replace the current integration work with an early broad refactor. No upstream pull requests or repository changes; patches remain Framey-owned and original plugin bytes/licenses stay preserved.
