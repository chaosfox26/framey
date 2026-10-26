<p align="center"><img src="icon.svg" width="128" height="128" alt="Framey icon"></p>

# Framey

> **Framey is an AI-made project, developed using AI coding tools.**
>
> **Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) and its contributors’ work making Steam Deck customization accessible through plugins. We gratefully acknowledge that inspiration. Framey is independent of Decky Loader and is not affiliated with or endorsed by it.**

Framey is a plugin loader that runs on the Steam Frame. It adds an icon to Steam's bottom bar that opens a VR-friendly panel for installed plugins, updates and loader settings. Framey is unofficial and independent of Valve.

This README describes the **experimental source branch**, currently reporting version 1.0.2. A version number alone does not identify the installed code; check the commit in Settings. The October 26, 2026 checks below cover source commit `0b0b3a3`, not a new release asset or a change to the default update channel. Physical VR and controller validation remains incomplete. The [current Decky comparison](docs/decky-comparison.md) records the remaining compatibility gaps.

## What it does

- Runs as a user service on the headset. It needs Python 3 and uses only the standard library.
- Adds a Framey icon to Steam's bottom bar. The panel has one page per installed plugin, plus Modules, Store, Decky Store and Settings on the experimental branch. [Store](docs/catalog.md) discovers and installs Framey-native modules; the separate [Decky Store](docs/catalog.md#decky-store) browses upstream Decky entries and inspects selected archives without installing or running them. Older installed builds may still show the Store placeholder.
- Offers three [panel modes](docs/panels.md) in Settings: **Inside Steam** (default and fallback), **Beside Steam dashboard**, and **Floating during games**. The separate modes are experimental and reuse Steam's browser host. Gameplay input and physical VR comfort still need validation.
- Plugins (called modules in the panel) can be enabled, disabled, reordered, hidden, frozen, updated, repaired, rolled back and removed from the headset.
- Shows whether a module's upstream project still looks maintained. This is information only.
- Updates Framey and signed Fan Control releases from inside the headset.
- Runs plugin backends in separate worker processes. This is failure containment, not a security sandbox: plugins are trusted code.

**Open issue: separate-panel recovery after a SteamVR restart.** In the latest device check, Framey restarted automatically and its bottom-bar button and embedded panel returned, but the saved separate dashboard panel did not. Open **Settings > Framey panel**, select **Retry**, then select **Beside Steam dashboard** again. This restored the separate panel in that check; automatic restoration remains unresolved. **Inside Steam** remains the fallback. See [panel modes](docs/panels.md) for the controls and other limits.

The full guide is [docs/using.md](docs/using.md). Plugin authors can start with the [developer quickstart](docs/developer-quickstart.md), then use the [plugin API reference](docs/plugins.md). Plain JavaScript and Python plugins need no Node.js build step.

## Install

The recommended way is the [Framey App](https://github.com/chaosfox26/framey-app/releases). It installs Framey, and optionally Fan Control, over SSH. The App currently fetches Framey's `main` source and the latest signed Fan Control release. Building the App's experimental branch does not select experimental Framey or Fan Control sources; its existing manual build workflow also bundles those repositories' `main` snapshots. Do not assume a released installer contains the changes described for this branch.

Manual installation is possible but unsupported. Steps are in [docs/using.md](docs/using.md#manual-install).

Framey needs Steam to be running with its local debug port open (`127.0.0.1:8080`). Without that port Framey cannot attach.

## Current verification

The October 26, 2026 checks cover Framey `0b0b3a3` and, for Fan Control integration, Fan source `5429624`. These are development-snapshot results, separate from the earlier release and panel-mode observations in the [user guide](docs/using.md#verification-status).

GitHub's [source-quality and x64/ARM64 fixture checks](https://github.com/chaosfox26/framey/actions/runs/40346665179) also passed for Framey `0b0b3a3`.

| Scope | Result and limits |
|---|---|
| Development machine | 2,134 Python cases completed with one expected platform skip; all 31 JavaScript fixture files, 248 offline checks and two Playwright cases passed. Configured Ruff, Mypy, Pyright, ESLint, Prettier and TypeScript checks passed. Type checking covers the [configured scope](docs/development.md#development-tools), not every runtime file. |
| Actual ARM64 Steam Frame | On SteamOS VR 0.5.6/Python 3.12.3, 2,107 Python cases passed and 27 skipped: 25 needed absent Node/browser-expression tooling, one the absent TypeScript compiler, and one an off-Linux platform. All 248 offline checks and nine Fan regressions passed. No development tools were installed on the headset. |
| Live Steam browser and Fan Control | Fan telemetry, navigation and installed version metadata rendered. Duplicate custom requests were skipped while active; custom → Stock → same custom reapplied within the previously affected five-second window, with the original saved curve restored. This was a direct local installation, not Framey App end-to-end acceptance. |
| SteamVR restart | Fan service and saved/active profile held. Framey automatically restarted once; embedded UI returned, while the separate dashboard panel required the manual recovery above. Native overlay presence/visibility and one minute of stability were verified after recovery. Automatic separate-panel restoration remains **OPEN**. |

Automated fixtures and Steam browser checks do not establish physical controller input, VR comfort, extended gameplay, thermal stress/failure behavior, or native Windows/macOS App acceptance. The runtime source changes have no new performance claim.

## Projects

Three separate repositories, each under GPL-2.0:

| Project | What it is |
|---|---|
| [framey](https://github.com/chaosfox26/framey) (this repo) | The plugin loader that runs on the headset. Version 1.0.2. |
| [frame-fan](https://github.com/chaosfox26/frame-fan) | Fan Control, a plugin with privileged cooling components. It has its own signed releases. |
| [framey-app](https://github.com/chaosfox26/framey-app) | A desktop app that installs and updates Framey and Fan Control over SSH. |

## Develop a plugin

Copy [Hello](examples/hello-plugin) for a small settings and backend example, or [Starter](examples/starter) for events, translations, notifications, extra pages and cleanup. Both declare API generation 1, minimum loader version and supported platforms. The [quickstart](docs/developer-quickstart.md) covers local edits, checks, packaging and source ports from Decky. Decky plugin bundles do not load directly in Framey. The [limited source-port helper and example](docs/decky-compatibility.md) adapt a few call and event patterns; they do not supply Decky React/UI, backend runtime or native binary compatibility. A platform declaration or an archive labeled ARM64 does not verify SteamOS device behavior or a usable VR interface.

Local Python and simulated browser tests provide development evidence. Changes after a tested snapshot still need verification of the installed revision and relevant device behavior; use the [current checks above](#current-verification) and the [user guide's verification limits](docs/using.md#verification-status) when assessing support.

Loader contributors can use the [development guide](docs/development.md) for
module responsibilities, code standards, pinned tools, source quality checks and
scoped type coverage. The [testing guide](docs/testing.md) covers fixture and
browser setup. Ruff/Mypy, ESLint/Prettier, TypeScript/Pyright and Playwright are
contributor tools; they are not headset runtime dependencies.

## Inspiration and acknowledgments

Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader). Its plugin-loader concept, and the way it made Steam Deck customization accessible through plugins, shaped Framey's idea of a small loader with plugins, a Settings page and a Store page. Thank you to the Decky Loader maintainers and contributors for the work and the example. Framey's aim is to bring that kind of convenience to the Steam Frame while respecting the work that inspired it.

Framey is independently implemented for Steam Frame. It is not a port of Decky Loader and does not run original Decky plugin bundles. Decky Loader's public source was read for reference during development, so Framey is not a clean-room implementation. The limited source-port helper and demonstration do not bundle Decky Loader, `@decky/api` or `@decky/ui`. Preserve the licenses and credits of any third-party plugin source you adapt.

Decky-related compatibility work is experimental. Browsing a Decky Store listing or inspecting its archive does not install or run it in Framey. Report Framey compatibility problems to [Framey](https://github.com/chaosfox26/framey/issues); Decky maintainers and plugin authors are not expected to support Framey-specific failures. Review and remove private details before submitting a public issue.

Framey is unofficial and independent of Valve.

## License

Framey is licensed under the GNU General Public License, version 2 only (GPL-2.0-only). The full text is in [LICENSE](LICENSE). The source files do not carry their own license notices.
