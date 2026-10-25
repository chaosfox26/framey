<p align="center"><img src="icon.svg" width="128" height="128" alt="Framey icon"></p>

# Framey

> **Framey is an AI-made project, developed using AI coding tools.**
>
> **Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) and its contributors’ work making Steam Deck customization accessible through plugins. We gratefully acknowledge that inspiration. Framey is an independent project for Steam Frame, with no claimed affiliation or endorsement.**

Framey is a plugin loader that runs on the Steam Frame. It adds an icon to Steam's bottom bar that opens a VR-friendly panel for installed plugins, updates and loader settings. Framey is unofficial and independent of Valve.

Version 1.0.2. Most of what is new in 1.0.2 has not been run on a headset yet. See [Verification status](docs/using.md#verification-status).

## What it does

- Runs as a user service on the headset. It needs Python 3 and uses only the standard library.
- Adds a Framey icon to Steam's bottom bar. The panel has one page per installed plugin, plus Modules, Store (coming soon) and Settings.
- Plugins (called modules in the panel) can be enabled, disabled, reordered, hidden, frozen, updated, repaired, rolled back and removed from the headset.
- Shows whether a module's upstream project still looks maintained. This is information only.
- Updates Framey and signed Fan Control releases from inside the headset.
- Runs plugin backends in separate worker processes. This is failure containment, not a security sandbox: plugins are trusted code.

The full guide is [docs/using.md](docs/using.md). Plugin authors should read [docs/plugins.md](docs/plugins.md).

## Install

The recommended way is the [Framey App](https://github.com/chaosfox26/framey-app/releases). It installs Framey, and optionally Fan Control, over SSH.

Manual installation is possible but unsupported. Steps are in [docs/using.md](docs/using.md#manual-install).

Framey needs Steam to be running with its local debug port open (`127.0.0.1:8080`). Without that port Framey cannot attach.

## Projects

Three separate repositories, each under GPL-2.0:

| Project | What it is |
|---|---|
| [framey](https://github.com/chaosfox26/framey) (this repo) | The plugin loader that runs on the headset. Version 1.0.2. |
| [frame-fan](https://github.com/chaosfox26/frame-fan) | Fan Control, a plugin with privileged cooling components. It has its own signed releases. |
| [framey-app](https://github.com/chaosfox26/framey-app) | A desktop app that installs and updates Framey and Fan Control over SSH. |

## Inspiration and acknowledgments

Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader). Its plugin-loader concept, and the way it made Steam Deck customization accessible through plugins, shaped Framey's idea of a small loader with plugins, a Settings page and a Store page. Thank you to the Decky Loader maintainers and contributors for the work and the example. Framey's aim is to bring that kind of convenience to the Steam Frame while respecting the work that inspired it.

What Framey is not: it is not a port of Decky Loader, it is not compatible with Decky plugins, and it has no official relationship with the Decky Loader project. It is also not a clean-room implementation. While Framey was being designed, Decky Loader's public source was read for reference. No Decky Loader code, assets or documentation were copied. An earlier comparison against Decky Loader's source reported one identical line, the generic browser call `document.head.append(style);`. That comparison was not repeated for this verification.

Framey is unofficial and independent of Valve.

## License

Framey is licensed under the GNU General Public License, version 2 only (GPL-2.0-only). The full text is in [LICENSE](LICENSE). The source files do not carry their own license notices.
