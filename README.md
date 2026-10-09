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
| [framey](https://github.com/chaosfox26/framey) (this repo) | The plugin loader that runs on the headset. |
| [frame-fan](https://github.com/chaosfox26/frame-fan) | Fan Control, a plugin that installs on its own and includes privileged cooling components. |
| [framey-app](https://github.com/chaosfox26/framey-app) | A portable desktop app that installs and updates the other two over SSH. |

## What it does today

- Runs as a user service on the headset, in Python 3.9 or newer with no third-party Python packages, at low CPU priority.
- Attaches to Steam's own web interface through its local debug port (`127.0.0.1:8080`) and adds a small icon to the bottom bar. Tapping it opens a panel with a side menu, large tap targets and a dark look, sized for a laser pointer. It remembers the last tab and whether the panel was open.
- Each plugin gets its own tab. A plugin is a folder with metadata (`plugin.json`), a JavaScript interface (`main.js`) and an optional Python backend (`backend.py`). See [docs/plugins.md](docs/plugins.md).
- **Settings tab:** turn plugins on or off, remove them (two taps; this only removes the plugin from Framey and does not undo anything it installed on the system, so for Fan Control use the Framey App's **Remove** to restore stock cooling), reload them, and install one from an HTTPS link to a zip file. Zip installs check the link, size (including unpacked size), paths and plugin id, and only replace an installed plugin once the new one has unpacked cleanly.
- **Store tab:** lists plugins found in a local `store` folder next to the loader and installs or removes them. The folder is empty by default. There is no online catalog.
- A plugin with a broken manifest, a JavaScript error or a backend that fails to load is skipped and reported instead of taking the panel down. Plugins are not sandboxed: each plugin's page script is evaluated separately, but backends run in the loader's own Python process and event loop with your permissions, so a backend that blocks or crashes can stall or stop the loader.
- Reconnects when Steam's interface reloads, refreshes its pages when it starts, and removes its additions when it stops.

## Install

The easiest way is the [Framey App](https://github.com/chaosfox26/framey-app), which installs and updates Framey over SSH.

Manually, on a Steam Frame with Developer Mode enabled (Steam Settings > System > Enable Developer Mode):

1. Copy this repository to `~/framey` on the headset.
2. Create `~/.config/systemd/user/framey.service`:

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

Framey needs Steam to be running with its local debug port open. That port was available on the author's headset. If it is not available on yours, Framey cannot attach.

## Use, update and remove

- Tap the Framey icon in Steam's bottom bar to open or close the panel, and use the side menu to switch tabs.
- Update by running the Framey App's **Install / Update**, or replace the files in `~/framey` and run `systemctl --user restart framey.service`.
- Remove with the Framey App's **Remove**, or manually: `systemctl --user disable --now framey.service`, then delete `~/.config/systemd/user/framey.service`, `~/framey` and `~/.config/framey`. Stopping the service removes the icon and panel from Steam's interface.

## Status and verification

Framey has been developed and tested on one Steam Frame running SteamOS 0.4.5. Because it works through Steam's own interface, a Steam update could change or break it. It has not been tested on other headsets or SteamOS versions. AI authorship and a working build are not proof that something works; only what was run on the headset is claimed here.

## Roadmap (not implemented)

- A temperature and fan readout shown over flat-screen games. Nothing like this exists yet.

## Inspiration and acknowledgments

Framey was inspired by [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader). Its plugin-loader concept, and the way it made Steam Deck customization accessible through plugins, shaped Framey's idea of a small loader with plugins, a Settings page and a Store page. Thank you to the Decky Loader maintainers and contributors for the work and the example. Framey's aim is to bring that kind of convenience to the Steam Frame while respecting the work that inspired it.

What Framey is not: it is not a port of Decky Loader, it is not compatible with Decky plugins, and it has no official relationship with the Decky Loader project. It is also not a clean-room implementation. While Framey was being designed, Decky Loader's public source was read for reference. No Decky Loader code, assets or documentation were copied. A line-by-line comparison of this repository against Decky Loader's source found one identical line, the generic browser call `document.head.append(style);`, and no other matches.

Framey is unofficial and independent of Valve.

## License

Framey is licensed under the GNU General Public License, version 2 only. The full text is in [LICENSE](LICENSE), and GitHub identifies it as GPL-2.0. The source files do not carry their own license notices.
