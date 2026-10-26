# Development

Framey runs on the ARM64 Steam Frame. Keep its standard-library Python runtime,
existing Steam browser integration, native plugin contracts, and independent UI.
Development tools run on a contributor's machine or in CI; they are not part of
the installed runtime manifest.

## Code standards

Prefer the smallest implementation that makes ownership and failure behavior
clear. Use names that describe a value's role, early returns for rejected input,
and one responsibility per helper. Keep related state and its cleanup together.
Avoid compressed statements, unexplained aliases, duplicated policy, speculative
abstractions, and new runtime dependencies without a concrete need.

Keep engineering explanations in Markdown. Source code contains executable code,
type declarations, and necessary interpreter directives. Preserve licensing and
attribution in the repository's license and credit documentation. Literal sample
code, regular expressions, URLs, and strings containing comment markers are data;
do not alter them with text-based comment stripping.

Formatting is separate from behavior. Formatting-only changes must preserve
syntax-tree meaning and string values. Renaming must follow bindings, preserve
public property names and keyword parameters, and avoid changing serialized
messages. A short implementation is useful only when its behavior remains clear.

## Ownership and module boundaries

| Area | Owner | Responsibility |
| --- | --- | --- |
| Steam connection and UI routing | `framey.py` | Authenticate callers, reconcile targets, select one frontend owner, dispatch requests, coordinate shutdown |
| Worker protocol | `fy_proto.py` | Bound JSON messages, reject non-finite numbers, normalize protocol errors |
| Backend lifecycle | `fy_supervisor.py`, `fy_worker.py` | Bound queues and calls, start and stop workers, report lost events, release process resources |
| Plugin API | `fy_sdk.py`, `types/` | Define native plugin contracts without exposing private loader operations |
| Settings and packages | `fy_state.py`, `fy_store.py`, `fy_pkg.py` | Validate and persist state, stage changes, preserve recovery data |
| Networking and updates | `fy_net.py`, `fy_updates.py`, `fy_maint.py` | Enforce network limits, server cooldowns, installation and recovery rules |
| Catalogs | `fy_catalog.py`, `fy_decky_catalog.py` | Keep native installation separate from original Decky package inspection |
| Files, media and targets | `fy_files.py`, `fy_media.py`, `fy_targets.py` | Validate access, bound results, revoke resources and target registrations |
| Panel presentation | `fy_panel.py`, `fy_overlay.py`, `fy_overlay_browser.py` | Serialize surface changes, own only Framey's popup and overlay, fall back to embedded presentation |
| Browser lifecycle | `inject.js`, `fy-api.js` | Establish trusted boundaries, bind plugins, reject retired work, dispose subscriptions and resources |
| Interface | `fy-ui.js`, page and feature scripts | Render the existing Framey interface and release handlers when views leave |
| Bottom bar | `bar.js`, `fy-badge.js` | Mount the existing button, reflect state, remove observers and UI on disposal |
| Diagnostics and reporting | `fy_diag.py`, `fy_report.py`, `fy-report.js` | Collect bounded safe information and prepare an explicitly reviewed report |

Retain these boundaries when improving structure. Extract a helper when it owns a
coherent operation or removes real duplication. Do not split modules solely to
meet an arbitrary line count. Avoid turning a readable sequential operation into
a framework of indirect callbacks.

## Local ownership simplifications

Module cards show release notes only through populated update and version results.
They do not reserve an unused notes map, so plugin IDs that coincide with object
prototype properties cannot create inherited note rows. Visibility dispatch owns
subscriber failure isolation, including rejected promises; registration only binds
the callback and its cleanup.

The supervisor retains health checks through its shared task set. Worker state
does not hold a second unused handle. Downloads carry their validated final path;
the save operation owns its unique temporary file and atomic replacement. Target
sends use the internal bounded sender while add, remove and reconciliation retain
their operation-wide sending lock.

Registration keeps its existing source, bind and run stage envelopes. Decode each
stage into manifest, token, source object, API object and redraw roles before
processing acceptance, generation and world checks. Release only the named remote
objects on successful completion, failure or a stale response; tokens, manifests
and redraw flags are not remote object handles. Preserve object groups, session
retirement checks, restore waits and redraw ordering.

Native catalog listing and details share channel and prerelease visibility.
Details selects the requested ID directly from one catalog resolution and then
projects the same row and status fields, independently of browse pagination.
Decky dispatch and native installation identity, eligibility and freshness checks
remain separate. Report-link preparation assigns its status code and explanation
together, preserving missing-repository precedence and the ASCII URL budget.

## Development tools

Install Python checks into a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Install the locked JavaScript development dependencies:

```bash
npm ci --ignore-scripts --no-audit --no-fund
```

Run the checks from the repository root:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
npm run lint
npm run format:check
npm run types:check
npm run javascript:types
npm run python:types
python3 examples/decky-style/build.py
```

JavaScript uses tabs for indentation and a 120-column formatting target; Python
uses four spaces and a 100-column target. Public TypeScript declarations retain
their two-space indentation and 100-column target. The core injection script must
stay below its existing 24 KiB source limit; do not relax that gate to accommodate
a formatting change.

Ruff checks Python syntax and common correctness errors and supplies one
formatter. ESLint checks the authored browser scripts against their real injected
globals. Prettier supplies JavaScript and TypeScript declaration formatting.
Mypy starts with strict checks of `fy_proto.py`; this does not claim type-checking
coverage of the remaining Python runtime. Expand its scope as modules gain useful
annotations. Pyright checks eleven runtime modules, including worker supervision,
the SDK, reporting, panel/overlay handling, files, targets and state, plus Python
stubs, with Python 3.12/Linux as its target. TypeScript checks the public frontend declarations and checks
`fy-badge.js` strictly against the development-only bar declaration. This does
not claim complete static type coverage of the remaining runtime.

`jsconfig.json` gives the JavaScript language service a project for navigation and
symbol discovery across authored scripts. The separate `tsconfig.checks.json`
enables strict implementation checking for the currently supported source scope.
Automatic type acquisition is disabled; the project uses its pinned dependencies
and explicit declarations. Expand this checking without adding unchecked casts or
suppressing real errors merely to obtain a passing result.

`inject.js` intentionally publishes `__FY` into the enclosing loader expression.
Other frontend scripts receive `__FY`, `__fyt`, and `__trusted` from that expression.
The source-port helper also supports a CommonJS export for its offline tests.
These narrow bindings are declared explicitly in the ESLint configuration.

The source-port example's `main.js` is generated. Edit `compat/decky-source.js` and
`examples/decky-style/source.js`, format those inputs, then run the builder. Do not
format or edit the generated bundle independently. Its deterministic-build test
must continue to compare it with the builder's output.

## Verification and resource claims

Use [the testing guide](testing.md) for the complete Python, process, browser,
offline assessment, and public API checks. Keep their failures visible. Source
checks complement executable tests and cannot establish physical VR behavior.

For a behavior-preserving cleanup, compare against the previous source, run the
existing suites, and investigate every difference. Keep formatting separate from
behavioral fixes where possible. Do not remove lifecycle guards, cancellation,
limits, error handling, or recovery paths to shorten code.

Measure memory and CPU on the actual ARM64 device before claiming a performance
improvement. Total added memory includes workers, browser interface, and GPU
cost; the service's memory is only one component. Formatting, shorter names, and
removing comments do not establish lower runtime memory or idle CPU.

See [panels](panels.md) for presentation modes and physical acceptance limits,
and [the Decky comparison](decky-comparison.md) for the distinction between
inspection, source ports, and original plugin execution.

The implementation and test notes below retain the explanations moved from source.

## Ready-to-use checks and browser tools

After the virtual-environment and npm setup above, run the complete source-quality
checks with:

```bash
npm run check
```

Install Playwright's Chromium browser once, then run the real-browser fixtures:

```bash
npm run browser:install
npm run browser:test
```

Playwright and Pyright are pinned npm development dependencies. Ruff's linter and
formatter and Mypy are pinned Python development dependencies in `.venv`. The
TypeScript package also supplies its language server. A language-server client
can launch the local `tsserver` or `pyright-langserver` executables from
`node_modules/.bin`; installed executables do not by themselves establish a live
connection to an editor.

The browser fixtures load the actual Framey frontend with synthetic local backend
replies. They block external requests and verify Settings, panel-mode choices,
Modules navigation, fixed panel/rail widths, Escape dismissal, and restoration of
focus to a host control. They cover the 542-by-608 panel viewport and a 1280-by-720
dashboard viewport. Chromium is a local test browser; it is not Steam CEF, and
passing these fixtures does not establish compositor alignment or physical VR
controller and gameplay acceptance.

Failure screenshots stay under ignored `test-results/`. Do not publish screenshots
or traces from a real account or device without reviewing them for private data.

## Codex capabilities

Superpowers supplies planning, debugging, testing, and review workflows. Context7
supplies current library documentation; GitHub supplies repository and check
access; Figma supplies design inspection and authoring. These are contributor-side
capabilities, not Framey runtime dependencies. Follow the existing publication and
privacy boundaries when using them. A connected account does not authorize public
issues, comments, messages, deployment, or changes to unrelated designs.

## Simplifying existing runtime code

Raise the domain exception directly when a helper only forwards its arguments to
that exception. Keep helpers that enforce policy, normalize data, or implement a
shared operation. Catalog validation still uses the same bounded error codes and
messages; direct raises make rejected input and subsequent narrowing explicit.

Build the dynamic native SDK module through one explicit export mapping. Keep its
public names and the standard Python module identity intact. Capture nested input
before validating it so readers and analyzers can follow the same value through
its validation and use.

Keep subprocess launch options explicit rather than passing a heterogeneous
configuration dictionary through unrestricted keyword expansion. Preserve the
same stream pipes, environment, working directory, line limits, and process-session
behavior. Worker host-call results have explicit initial state for failure paths.

Discard unused unpacked results without removing the operation that validates or
produces them. Self-comparison in numeric limit checks is intentional NaN rejection;
it is not dead code. Preserve the exact native visibility checks and never shorten
a timeout or ownership guard merely to make a linter quiet.

Core UI state names describe the current tab, page state, toast container, request
counter, and session-value helper. Renaming must leave serialized field names and
public properties unchanged. The payload limit still applies after formatting.

## Implementation notes

These explanations accompany the source without inline prose comments. Paths
refer to their current modules; notes are grouped by their original owning file.

The source-port helper is independently authored. Its inclusion does not provide
the original Decky runtime or UI libraries. Keep the existing project credits and
license files when redistributing source.

### .github/ci/offline_assessment.py

Run Decky fixtures with a Python audit guard; child processes are not isolated.

### examples/decky-style/backend.py

A Framey backend for an independently authored Decky-pattern source port.

The source adapter sends a JSON array containing the positional arguments.

Arrays are spread into positional handler arguments by the adapter. Save first: the next read recovers even if this event is dropped.

This backend owns no tasks, threads, child processes or external resources.

### examples/decky-style/build.py

Package the shared source adapter with this example; no npm is required.

### fy_overlay_browser.py

Own one hidden SteamCEF popup; compositor placement belongs to the caller.

Steam's popup factory is a private, version-dependent interface. Every operation
rediscovers its SharedJSContext and checks both the debugger endpoint and the
page origin. This module never adopts windows by title or closes Steam's host.

Only fixed application-owned configuration enters this expression. A retained pending creation prevents a lost CDP reply causing a second popup.

**OverlayBrowser**

Serialized popup commands; no background task or idle polling loop.

The host supplies its fixed local `tabs`, `WS` and `cdp_owner_ok`
implementations. A timed-out command has an uncertain native outcome; the
next command rediscovers the page and consults the retained owned record.
`ok` means a native command returned, not that pixels are visible.

This is the final transport boundary; never log CDP data.

### fy_panel.py

Own panel presentation without changing Steam target identities.

`request` prepares a mode change; the loader calls `apply_pending` only after
replying to its originating RPC. Native calls have one retained thread slot,
including after a timeout. A late call must drain before cleanup or retry.

**Controller**

Host callbacks: capture state, await surface readiness, visibility, notify.

Factories receive `enabled=True` and `host` respectively. Tests can
inject an async `native_runner(function, *args)` instead of worker threads.
Public `mode` is committed presentation; `status` separates pending mode
and logical open state from actual compositor visibility (`active`).

**refresh_offer**

An explicit status retry reoffers support without invoking native code.

A timeout is not permission to overlap a new native thread. This task can remain blocked on the original call while close returns bounded.

### tools/framey_plugin.py

Offline validation and reproducible packaging for Framey plugins.

Load only the sibling repository module, never a plugin or the current directory.

Compile the known file directly so validation does not create a bytecode cache.

A directory symlink in an allowed tree is refused, not followed.

Inspect names and file types only; excluded Decky code remains unread.

Stored entries avoid dependence on a particular zlib version.

Exclusive creation refuses existing files, links, and directories, even dangling links.

### compat/decky-source.js

Independently authored source-port helpers. Prepend inside a Framey plugin.

Exception contents can contain credentials or private paths. Report only a fixed category and validated event name, at most four times plus one notice per adapter lifetime. Native logging applies its own rate limit as well.

The optional logger is the final fault boundary; do not log recursively.

The same final fault boundary applies to a synchronously failing logger.

Native dispatch may already hold this callback when it is removed and registered again. Only the current registration may invoke the listener.

Clear identity and mark disposed before any remover can reenter dispatch.

Framey immediately invokes a cleanup registered after native disposal.

### examples/decky-style/source.js

Render can run repeatedly; subscriptions belong to the plugin lifetime.

### fy-api.js

Diagnostics must not create a second callback failure.

Cleanup uses the same scheduling APIs that created the pending work.

### fy-pages.js

onclose can dispose the owner or publish a newer registration for this key.

### fy-report.js

Teardown must finish even when the bounded diagnostics sink is unavailable.

### inject.js

Capture call boundaries before same-realm plugins run.

Diagnostics must not create another failure.

### types/framey.d.ts

Backend calls and target operations resolve with errors; fetch/resource reject.

The code can be absent for transport failures.

Loader catalog UI terminology: unverified is displayed as "Not checked".

Internal loader inspection data; binary evidence does not verify runtime support.

Core-only catalog_assess success; plugins have no catalog inspection method.

Lowercase SHA-256 identities validated by the loader.

Promise returns are observed for busy state and failure reporting.

May be async; rejected promises are reported by the runtime.

May be async; rejected promises are reported by the runtime.

Returns a function that unregisters this cleanup without running it.

T describes your backend's success payload; errors still resolve.

Available once fy-picker.js is loaded. Cancellation resolves to null.

May be async; only failures of the current live render are reported.

The lexical Framey object supplied to plugin main.js.

The loader object on window.Framey is separate from the plugin binding.

## Test implementation notes

### tests/test_fy_catalog_validation.py

Adversarial catalog boundaries, using only in-memory catalog downloads.

### tests/test_fy_decky_catalog_validation.py

Untrusted Decky feed and ZIP inspection boundaries; no external downloads.

### tests/test_fy_decky_example.py

Exercise the source-port example with real SDK storage and worker dispatch.

**runtime_package**

Build a fixture from owned runtime files, never an external Decky release.

### tests/test_fy_decky_platform_files.py

Static package inspection must keep hostile files bounded and inert.

### tests/test_fy_decky_platform_isolation.py

Denied plugin capabilities can receive an error, but cannot enter core.

Store RPC ownership and inert metadata boundaries, without live CDP or archives.

### tests/test_fy_decky_platform_media.py

Offline media/privacy boundaries for Decky catalog browsing and assessment.

### tests/test_fy_decky_platform_network.py

Controlled Decky transport checks; no public network or plugin execution.

### tests/test_fy_decky_runtime_cancel.py

Selected archive inspection stays outside the plugin worker lifecycle.

### tests/test_fy_decky_runtime_events.py

Decky inspection boundaries alongside live runtime requests and events.

Fixture I/O is injected into the production catalog service. Core routing,
session authorization, request scheduling, reply generations, archive inspection
and event delivery are production code. No browser, network, device, or service
is contacted.

### tests/test_fy_decky_runtime_sourceport.py

Declare the test host honestly: ARM64 manifest support is metadata,

while this subprocess exercises only the current machine.

Exercise the authored Framey source port across real worker lifetimes.

Only the repository example is executed. No Decky Store package is imported.

### tests/test_fy_frontend_types.py

This guards against erasing error handling or silently escaping to any.

The wire value "unverified" maps to the UI's "Not checked" label.

CI and developers can opt into a local compiler without fetching packages.

Frontend declaration contracts; no dependency installation is performed.

### tests/test_fy_iso_ownership.py

Frame identifiers are opaque strings.

Context ownership and delayed CDP response regressions, without a browser.

### tests/test_fy_panel.py

Panel controller behavior with in-memory adapters; no native or browser I/O.

### tests/test_fy_panel_integration.py

Panel/main session integration with fixture debugger pages only.

### tests/test_fy_report_integration.py

Report routing/packaging integration; no browser, network or worker launch.

### tests/test_fy_supervisor_parity.py

Queue boot first, then mark its admission future cancelled before boot runs.

Regressions for supervisor lifecycle transitions using isolated workers.

### tests/test_fy_worker_parity.py

Regressions for worker lifecycle and ownership of in-flight requests.
