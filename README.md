<p align="center"><img src="icon.svg" width="128" height="128" alt="Framey icon"></p>

# Framey

> **Framey is an AI-made project, developed by ChaosFox using AI coding tools.**
>
> **Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) and its contributors’ work making Steam Deck customization accessible through plugins. We gratefully acknowledge that inspiration. Framey is an independent project for Steam Frame, with no claimed affiliation or endorsement.**

Framey is a lightweight plugin loader that runs on the Steam Frame itself. It adds a button to Steam's bottom bar that opens a VR-friendly panel where plugins live, starting with fan control. Unofficial and independent of Valve.

## Origins and purpose

Framey grew from a wish to make the Steam Frame easier to customize through a lightweight, VR-first plugin interface, beginning with convenient fan controls. Plugins are small folders, so new features can be added without changing the loader.

## The Framey projects

Three separate repositories, each under GPL-2.0:

| Project | What it is |
|---|---|
| [framey](https://github.com/chaosfox26/framey) (this repo) | The plugin loader that runs on the headset. Version 1.0. |
| [frame-fan](https://github.com/chaosfox26/frame-fan) | Fan Control, a plugin that installs on its own and includes privileged cooling components. |
| [framey-app](https://github.com/chaosfox26/framey-app) | A portable desktop app that installs and updates the other two over SSH. This is the officially recommended and supported way to install Framey. |

## What it does

- Runs as a user service on the headset, in Python 3.9 or newer with no third-party Python packages, at low CPU priority.
- Attaches to Steam's own web interface through its local debug port (`127.0.0.1:8080`) and adds a small icon to the bottom bar. Tapping it opens a panel with a side menu, large tap targets and a dark look, sized for a laser pointer. The last tab and whether the panel was open are kept in the page's session storage, so they may reset when Steam's interface restarts.
- Each plugin gets its own tab. A plugin is a folder with metadata (`plugin.json`), a JavaScript interface (`main.js`) and an optional Python backend (`backend.py`). See [docs/plugins.md](docs/plugins.md).
- **Settings tab:** turn plugins on or off, remove them (two taps; this only removes the plugin from Framey and does not undo anything it installed on the system, so for Fan Control use the Framey App's **Remove** to restore stock cooling), reload them, and install one from an HTTPS link to a zip file. Every action shows a short result message: on success the loader re-injects its panel straight away and the panel reopens on the same tab; on failure the error is shown and the panel stays as it is. Taps are ignored while an action is still running. The panel's own wording calls plugins "modules".
- **Store tab:** lists plugins found in a local `store` folder next to the loader and installs or removes them. The folder does not exist by default, so the tab is empty until you create it. There is no online catalog.
- Reconnects when Steam's interface reloads (it checks every 5 seconds), injects its pages when it starts, and removes the elements it added when it stops.

### Plugin isolation

- Each plugin's `main.js` is evaluated as its own unit. A syntax error or exception affects only that plugin and shows a "Module ... failed" message over the page for a few seconds.
- A plugin folder whose `plugin.json` is missing or invalid is skipped and logged. A folder whose name is not a valid plugin id is skipped without a log line. A backend that fails to import is skipped and logged. Logs go to the service journal (`journalctl --user -u framey.service`).
- `settings.json` is shape-validated: anything that is not the expected shape is treated as empty. It is written to a temporary file and then renamed over the old one.
- Backends are not isolated. They run in the loader's own Python process and event loop with your permissions, so a backend that blocks, spins or exits can stall or stop the loader and every other plugin. There is no backend unload hook, so threads, tasks and subprocesses a backend starts keep running across a reload.
- Everything the loader adds to the page carries a `data-fy` attribute, and cleanup removes only elements with it. Anything else a plugin adds to the page is the plugin's to clean up.
- Zip installs are checked, unpacked into a staging folder and swapped in only when that succeeds, with the old copy restored if the swap fails. If the loader is killed in the middle of a swap, the next start restores the old copy when the plugin folder is missing and deletes leftover `.stage-` and `.old-` folders. Download links must be `https://` at every redirect hop, and a download is abandoned after 45 seconds. Limits: the zip itself 5 MB, 500 entries, 5 MB per entry, 20 MB unpacked in total, paths at most 8 levels deep.
- A linked plugin (a symlink in `plugins/`) cannot be overwritten by an install. Remove in the loader only unlinks it and does not undo changes the plugin made elsewhere on the system.

## Install

The recommended and supported way is the [Framey App](https://github.com/chaosfox26/framey-app), which installs and updates Framey (and optionally Fan Control) over SSH. Download it from the [releases page](https://github.com/chaosfox26/framey-app/releases). After installing, the app restarts SteamVR so the loader and panel load cleanly. A running VR session ends when that happens.

Manual installation is possible but unsupported. On a Steam Frame with Developer Mode enabled (Steam Settings > System > Enable Developer Mode):

1. Copy this repository to `~/framey` on the headset.
2. Run `mkdir -p ~/.config/systemd/user`, then create `~/.config/systemd/user/framey.service` (this is the same unit the Framey App writes):

```ini
[Unit]
Description=Framey

[Service]
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /home/steamos/framey/framey.py
Restart=always
RestartSec=3
Nice=19
CPUSchedulingPolicy=idle
CPUWeight=1
MemoryHigh=40M
MemoryMax=60M

[Install]
WantedBy=default.target
```

3. Run `systemctl --user daemon-reload && systemctl --user enable --now framey.service`.

If the icon does not appear after a manual install, restart SteamVR from the headset.

Framey needs Steam to be running with its local debug port open. That port was available on the author's headset. If it is not available on yours, Framey cannot attach.

## Use, update and remove

- Tap the Framey icon in Steam's bottom bar to open or close the panel, and use the side menu to switch tabs.
- Update by running the Framey App's **Install / Update**, or by replacing the loader files in `~/framey` (leave its `plugins` and `store` folders alone) and running `systemctl --user restart framey.service`.
- Remove with the Framey App's **Remove**, or manually: `systemctl --user disable --now framey.service`, then delete `~/.config/systemd/user/framey.service`, `~/framey` and `~/.config/framey`. Stopping the service removes the icon and panel from Steam's interface. Manual removal does not undo any system changes a plugin made; for Fan Control use the app's **Remove**.

## Fixed in 1.0

Problems in the earlier public version that are fixed in 1.0:

- A plugin with a malformed manifest or a JavaScript syntax error could stop the whole loader or panel.
- A failed plugin update could leave a half-replaced plugin.
- Zip limits applied only to the compressed size.
- A bad `settings.json` could stop startup.
- Cleanup could remove elements that were not the loader's own.

The Framey App's earlier 1.0.x releases were withdrawn and replaced. See its [release history](https://github.com/chaosfox26/framey-app#release-history).

## Status and verification

Version 1.0. Framey was built and run by the author earlier on one Steam Frame running SteamOS 0.4.5 beta. The latest changes (plugin isolation, staged swaps, archive limits, settings validation, call-reply matching, atomic settings writes, startup recovery and the Settings feedback) were tested offline only, with simulated pages, sockets and interrupted swaps and have not yet been tested on a headset. It has not been tested on other headsets or SteamOS versions, and because it works through Steam's own interface, a Steam update could change or break it. AI authorship and a working build are not proof that something works; only what was run is claimed here.

## Roadmap (not implemented)

- A temperature and fan readout shown over flat-screen games. Nothing like this exists yet.

## Inspiration and acknowledgments

Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader). Its plugin-loader concept, and the way it made Steam Deck customization accessible through plugins, shaped Framey's idea of a small loader with plugins, a Settings page and a Store page. Thank you to the Decky Loader maintainers and contributors for the work and the example. Framey's aim is to bring that kind of convenience to the Steam Frame while respecting the work that inspired it.

What Framey is not: it is not a port of Decky Loader, it is not compatible with Decky plugins, and it has no official relationship with the Decky Loader project. It is also not a clean-room implementation. While Framey was being designed, Decky Loader's public source was read for reference. No Decky Loader code, assets or documentation were copied. A line-by-line comparison of this repository against Decky Loader's source found one identical line, the generic browser call `document.head.append(style);`, and no other matches.

Framey is unofficial and independent of Valve.

## License

Framey is licensed under the GNU General Public License, version 2 only. The full text is in [LICENSE](LICENSE), and GitHub identifies it as GPL-2.0. The source files do not carry their own license notices.
