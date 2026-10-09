# Framey

A small plugin loader for the Steam Frame. It adds a button to Steam's bottom bar that opens a VR-friendly panel with a side menu of modules, a Settings tab and a Store tab.

Framey attaches to Steam's own UI over the local browser debug port (`127.0.0.1:8080`), injects its panel, and runs each module's optional Python backend. Unofficial. Not affiliated with Valve.

## Modules

A module is a folder in `plugins/` (a real folder or a symlink):

| File | Purpose |
|---|---|
| `plugin.json` | `name`, optional `version` and `short` (side menu label) |
| `main.js` | calls `Framey.register({render(box){...}})` |
| `backend.py` | optional, `async def call(method, arg)` |

Modules reach the backend with `Framey.call(moduleId, method, arg)`. `Framey.el` and `Framey.button` build panel UI, and the `fy-r` class styles range sliders. The module id is its folder name.

The Settings tab turns modules on or off, removes them, reloads, and installs a module from an HTTPS link to a zip containing `plugin.json` with an `id`.

## Run

Needs Python 3.9 or newer and no packages. Place the folder at `~/framey` on the Frame and run it as a user service:

```ini
[Service]
ExecStart=/usr/bin/python3 %h/framey/framey.py
Restart=always
```

Footprint: about 21 MB of memory and near zero CPU when idle.

## License

GPL-2.0, see `LICENSE`.
