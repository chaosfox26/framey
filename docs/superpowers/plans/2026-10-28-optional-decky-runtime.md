# Optional Decky Runtime Implementation Plan

> For agentic workers: execute coordinated independent file ownership in the current dirty experimental checkout; root integrates and performs one final focused review. Reuse existing workers. No per-file approval/report cascade.

**Goal:** Separate Decky compatibility into an optional downloadable/removable package while native Framey remains independent.
**Architecture:** Fixed core manager/facade, versioned external payload, thin plugin stubs and closed admission generations.
**Tech stack:** Existing Python/JavaScript, standard library, current native SDK and test harnesses.
**Spec:** ../specs/2026-10-28-optional-decky-runtime-design.md

## Global constraints
Preserve all existing work and original licenses/vendor files. No framework dependencies, native UI redesign, protected cooling changes, original plugin tests, device operations, commits/push/publication. Off by default; preserve imported packages/settings on removal. Payload files are independent of core backups. Core API/runtime ABI1, RPC value envelope1.

## Review focus
Physical absence must not be hidden by checkout imports. Disable must fence work before drain, including generic supervisor restarts and deferred frontend source/bind/run. Old embedded wrappers must not bypass the gate. Core/app updates and recovery must not restore the monolith. Failed or unpublished downloads must not be reported as installed/verified.

## Task1 — Core manager and reproducible runtime artifact
Files: new fy_decky_runtime.py, runtime package builder and owned manager tests. Write and run failing tests for default-off/no imports, verified install/failure, stale generation, disable/remove preservation and safe version selection. Implement the fixed manager contract in the spec; reusable transport/staging mechanisms, no new root helper. Build local deterministic archive+metadata and verify exact closure. Do not claim publication.

## Task2 — Runtime extraction and thin imported backend/catalog/code entry
Files: decky_runtime/ Python+compat source, importer/CLI and associated Python tests. Move existing implementation without rewriting it; make imports package-relative, add fixed runtime entry. Thin paired wrappers preserve original bytes and paths. Provide SDK/Worker bridge needs to root as exact narrow interface; root owns generic supervisor integration. Test original-preserving owned imports, package namespace isolation, paired-envelope behavior, physical optional absence. Existing proof epochs remain historical.

## Task3 — Frontend forwarding facade and optional client
Files: fy-api.js, inject.js, optional core loader/client module, runtime frontend client, relevant JS tests. Keep native APIs and abandoned-response cleanup. Extract heavy lease/host logic behind ABI1 provider with per-owner and per-runtime generation. Test native bind before late enable, concurrent activation, disable during mount/issue, stale replies, native cleanup survival. No global registration reopen or core-token exposure.

## Task4 — Settings/Store/Modules integration
Files: fy-settings.js, fy-catalog.js, fy-modules.js, strings/types, optional Store frontend and focused JS/browser tests. Use core-only manager RPC/status; off by default, explicit download action, truthful failure/progress. Native Store must never import optional provider. Preserve window pagination and native input. Owned fixture proves toggle flow without a published release.

## Task5 — Core admission, packaging, SDK integration and app distribution
Root owns framey.py, fy_supervisor.py/fy_worker.py/fy_sdk.py and generic test-harness integration. Gate managed imports on every start/injection/call/reload and stale completion. Distribution owner handles core/app filtering and recovery/layout compatibility separately. Add fresh-interpreter physical-absence owned integration and update/rollback tests. Do not rely on module stubs or source-only deletion tests.

## Task6 — Integration and verification
Resolve shared API contracts once, migrate affected tests/configs/docs and run targeted tests. Run complete Python/JS/browser/configured checks once integrated, inspect results and fix regressions with meaningful tests. Obtain one independent focused final review of changed boundaries. Preserve old evidence/private failures and record actual limitations. Package locally and report any final publication requirement separately; no routine check-in pauses.

## Verification baseline

Recheck the extracted runtime against the native-core and package tests. Track original plug-in compatibility separately in the [readiness assessment](../../decky-status.md).
