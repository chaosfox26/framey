# Framey experimental and Decky Loader

Checked October 25, 2026. **Framey is not yet one-to-one with Decky Loader.**
The core plugin-host and management features overlap substantially. The largest
remaining gap is running existing Decky plugins unchanged, including their React
interfaces, shared Steam UI components and Python plugin contracts.

This compares Framey's tested runtime commit
[`678c0fd`](https://github.com/chaosfox26/framey/commit/678c0fd1580c0d316a3909754d53cd980e6870e6)
with Decky's current main
[`00cd202`](https://github.com/SteamDeckHomebrew/decky-loader/commit/00cd202872b85868071b041c8f1b34e734147195)
and latest stable release
[`v3.3.0`](https://github.com/SteamDeckHomebrew/decky-loader/releases/tag/v3.3.0),
resolved to
[`67196ad`](https://github.com/SteamDeckHomebrew/decky-loader/commit/67196adfa9b299f20dfb0cc932e88a169417b7d5).
Later Framey documentation and CI-only commits do not change that runtime snapshot.
“Comparable” below means a capability exists on both sides; it does not mean the
packages, APIs or user experience are interchangeable.

## Capability comparison

| Area | Decky | Framey on Steam Frame | Assessment |
| --- | --- | --- | --- |
| Plugin processes and lifecycle | Separate Python plugin processes, lifecycle hooks and call routing | Supervised Python workers, lifecycle hooks, bounded calls, crash handling and idle cleanup | Comparable foundation; different plugin contracts |
| Calls, events and persistence | Decky frontend/backend APIs, events and settings | Native calls, events, scoped settings and storage; a small source-port helper | Partial API coverage; source adaptation required |
| Shared interface tools | React mounting, Steam routing, `@decky/api` and `@decky/ui` | Native DOM controls, pages, notifications, pickers, resource helpers and cleanup | Partial capability coverage; no drop-in Decky React/SDK support |
| Store browsing | Native Decky catalog and package actions | Native Framey Store plus a clearly separate Decky Store with read-only inspection | Browsing overlap; upstream listings are not runnable Framey plugins |
| Installation and updates | Installs and manages Decky packages and published versions | Installs Framey-native packages; staged replacement, update planning and recovery controls | Comparable native operations; original Decky installation remains unsupported |
| Module management | Enable, disable, reload, remove, hide, reorder, freeze and update | Comparable controls plus retained-copy recovery | Similar capabilities with different policies; no overall reliability ranking |
| Developer workflow | React/TypeScript template and build/deploy CLI | Plain JavaScript/Python examples, types, validation, deterministic packaging, reload and diagnostics | Useful native workflow; no equivalent complete build/deploy CLI |
| Architecture and hardware | Official ARM64 loader asset is published | Running and tested on ARM64 SteamOS VR with the Steam Frame panel integration | Neither an ARM64 asset nor an ARM64 header establishes individual plugin or VR compatibility |
| Performance and acceptance | No equivalent Decky-on-Frame benchmark was run for this comparison | Bounded live samples and automated checks; physical controller/gameplay acceptance remains incomplete | No defensible faster/lighter or full-parity claim |

Upstream evidence: Decky's
[plugin loader](https://github.com/SteamDeckHomebrew/decky-loader/blob/00cd202872b85868071b041c8f1b34e734147195/frontend/src/plugin-loader.tsx),
[backend worker](https://github.com/SteamDeckHomebrew/decky-loader/blob/00cd202872b85868071b041c8f1b34e734147195/backend/decky_loader/plugin/sandboxed_plugin.py),
[Store](https://github.com/SteamDeckHomebrew/decky-loader/blob/00cd202872b85868071b041c8f1b34e734147195/frontend/src/components/store/Store.tsx),
[installer](https://github.com/SteamDeckHomebrew/decky-loader/blob/67196adfa9b299f20dfb0cc932e88a169417b7d5/backend/decky_loader/browser.py),
[API exports](https://github.com/SteamDeckHomebrew/loader-api/blob/d1b7f16070777a0ada939cbacd3606eeba0574f5/src/index.ts),
[shared UI exports](https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/5419bcbbc8ab77fecd28f563e9a8c19fd8e5e5d7/src/index.ts)
and [development CLI](https://github.com/SteamDeckHomebrew/cli/tree/6c32da7396942a2362f63a02bfa6c68242f0ab07/src/cli/plugin).
Framey's corresponding contracts are documented in the
[plugin API](plugins.md), [Store guide](catalog.md),
[source-port guide](decky-compatibility.md), [plugin tools](plugin-tools.md) and
[user guide](using.md).

Framey remains its own implementation, informed by public interfaces and credited
inspiration. The source-port helper is not a bundled Decky loader, `@decky/api`
or `@decky/ui`. Preserve licenses and credits when adapting third-party source.
Framey's user-service and separately installed privileged helpers also differ
from Decky's inspected root loader service; privilege behavior should be adapted
deliberately rather than copied to achieve nominal parity.

## Verification evidence

The frozen runtime completed 1,984 Python tests on the development Linux PC
(1,983 passed, one expected off-Linux skip) and on the actual ARM64 headset
(1,969 passed, 15 Node/TypeScript-dependent skips). All 29 browser test files
passed on the PC; strict TypeScript compilation also passed there. These are
fixture and process checks, not physical VR acceptance. See [Testing](testing.md).

Live Steam Frame checks established the bottom-bar/panel path, the original
520-pixel panel and 104-pixel navigation rail, three native catalog listings and
116 upstream Decky listings at the time checked. A native Hello install and
backend greeting succeeded; the temporary example was removed afterwards, and
the Fan and built-in navigation tabs remained present after the live reload. Notebook 0.1.5's selected 134,532-byte archive was
successfully inspected with its catalog SHA-256 checked, without installing,
importing or executing its plugin code. That inspection still correctly requires
a Framey port and leaves ARM64 runtime compatibility unverified.

Hosted CI is recorded separately from those local/device receipts. The corrected
workflow [40245115255](https://github.com/chaosfox26/framey/actions/runs/40245115255)
passed both native ARM64 and x64 jobs at commit `31ff533`, whose runtime files
match `678c0fd`. This is hosted fixture/process/browser evidence, not physical
VR acceptance.

## Memory and CPU target

The target is less than **400 MB total added memory** for Framey, workers and the
interface in Steam, with CPU near zero while idle. It is a target, not a guarantee
for arbitrary trusted plugin code.

A short initial idle sample measured about 27 MiB for the service, approximately
0.115% of one CPU core and an approximately 2.6 MiB browser PSS increase versus
Steam without Framey. Browsing measured about 30 MiB service memory and 0.097%
of one core. In a subsequent 141-second observation after a native backend call,
one worker exited while idle; service memory peaked near 54 MiB and average CPU
was approximately 0.102% of one core. The service and workers have a shared
150 MiB systemd ceiling; Steam's browser is outside it.

These samples precede or accompany the final runtime corrections and are not a
controlled peak-memory, gameplay or all-plugin test. Browser PSS changes include
Steam activity and cannot be attributed exactly to Framey. The whole Steam
browser process is not Framey's added memory. No equivalent Decky measurement
was performed. Decky's inspected
[Rollup configuration](https://github.com/SteamDeckHomebrew/decky-loader/blob/00cd202872b85868071b041c8f1b34e734147195/frontend/rollup.config.js)
uses Steam's existing React globals, so assuming it loads a second complete
React runtime would be incorrect.

## Remaining proof needed

1. Port a small existing, hardware-independent Decky plugin with its credits
   intact, then compare actual behavior on Steam Frame. The independently authored
   source-port example demonstrates the helper; it is not a port of a published
   Decky release.
2. Decide which original frontend/backend contracts and shared components merit
   compatibility support, with explicit CPU, graphics-driver, operating-system
   and privileged-helper requirements.
3. Validate controller aiming, readability, focus, full update/recovery workflows,
   gameplay impact and representative whole-stack memory over longer sessions.

Main remains separate. This comparison establishes current capabilities and gaps;
it does not establish one-to-one compatibility or readiness to merge.
