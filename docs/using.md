# Using Framey

This guide describes the experimental branch of Framey, currently reporting version 1.0.2. An installed stable build can have different features; the version number alone does not identify its source. Check the installed commit in Settings before comparing its panel with this guide. Editing or checking out this branch does not update the running service. See [Verification status](#verification-status) for the limits of device testing.

Contents: [Install](#install) · [The icon and panel](#the-icon-and-panel) · [Catalog](catalog.md) · [Managing modules](#managing-modules) · [Updates](#updates) · [Upstream project status](#upstream-project-status) · [Configuration files](#configuration-files) · [Developer mode](#developer-mode-and-diagnostics) · [Backend workers](#backend-workers-and-failure-containment) · [Resource limits](#resource-limits) · [Recovery](#recovery) · [Fan Control](#fan-control) · [Known limitations](#known-limitations) · [Verification status](#verification-status)

## Install

Framey's target is Steam Frame running ARM64 SteamOS, with module platform identifier `linux-aarch64`. Equivalent architecture spellings such as `linux-arm64` are accepted; they do not make an x86-64 package compatible with ARM64. A module with native binaries needs an ARM64 build. The VR panel runs inside Steam's interface, so module compatibility also depends on the installed Framey API and Steam UI. Compatibility metadata is a check of declared requirements, not proof that a module has been tested on the device.

### Framey App (recommended)

Download the app from its [releases page](https://github.com/chaosfox26/framey-app/releases). It connects to the headset over SSH and installs Framey, and optionally Fan Control. The first Install or Update asks for the headset password once, to set up the protected updater. Later Fan Control updates from the panel do not need the password. (This is App behaviour, not checked in this repository.)

### Manual install

Manual installation is unsupported. On a Steam Frame with Developer Mode enabled (Steam Settings > System > Enable Developer Mode):

1. Copy this repository to `~/framey` on the headset. When updating later, leave its `plugins` and `store` folders alone.
2. Create `~/.config/systemd/user/framey.service`:

```ini
[Unit]
Description=Framey

[Service]
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /home/steamos/framey/framey.py
Restart=always
RestartSec=5
Nice=19
CPUSchedulingPolicy=idle
CPUWeight=1
MemoryHigh=128M
MemoryMax=150M
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=default.target
```

3. Run `systemctl --user daemon-reload && systemctl --user enable --now framey.service`.

The app's unit also has a recovery step before start (see [Recovery](#recovery)). The manual unit above does not, so a broken `~/framey` is not restored automatically.

If the icon does not appear, check the service log with `journalctl --user -u framey.service`.

## The icon and panel

- **Icon.** Framey's icon is added to Steam's bottom bar, labelled "Framey". Tap it to open or close the panel. If `icon.svg` cannot be read, a built-in circle icon is used. The icon shows a small badge with the number of updates available, counting enabled, unfrozen modules and Framey itself, from the last recorded check (`fy_updates.py` `badge`). The badge is refreshed after a batch update, a version install, a repair, a single-module Update and a Framey update check.
- **Side menu.** One entry per module that is not hidden, then Modules, Store, Decky Store and Settings. The panel's own wording calls Framey plugins "modules".
- **Modules.** Every installed module as a card, described in [Managing modules](#managing-modules) and [Updates](#updates).
- **Store.** Browse and search the experimental Framey-native module catalog, inspect details and review an installation before starting it. See [Finding modules and browsing the Decky Store](catalog.md) for sources, compatibility and saved catalog data. Older installed builds may still show "Coming soon".
- **Decky Store.** A separate tab for upstream Decky listings and version details, with an explicit **Inspect compatibility** action that downloads a selected archive for read-only checks. It does not install Decky packages. **Not compatible** identifies the need for a Framey port; **ARM64: Not checked** identifies missing processor evidence. Even an archive containing ARM64 code still needs its interface, backend and hardware behavior ported and tested for Steam Frame's VR environment. See [Decky Store](catalog.md#decky-store).
- **Settings.** The Framey version and its update, the automatic-check and notification switches, the activity window, the worker limit, the read-only update channel, safe mode and developer mode, and Reload modules.
- The panel's last page and whether it was open are kept in the page's session storage. They can reset when Steam's interface restarts.
- Framey checks every 5 seconds whether Steam's interface is still attached. When Steam reloads its interface, Framey reattaches and injects its pages again.

## Managing modules

Each card on the Modules page shows the name, version, installed commit, an Enabled switch, a compatibility line and the upstream status chip. Less common actions are under **More**.

- **Enabled.** Turning a module off stops its page and backend. The state is kept in `settings.json`. Turning it on starts it again.
- **Order and sort.** **Move up** and **Move down** set the order. **Sort** switches between Custom and Name (display name, then id).
- **Hide.** Removes the module's tab from the side menu. The module keeps running. **Show** brings the tab back.
- **Freeze.** Records the installed version ("Frozen at v..."). Batch updates and automatic checks skip frozen modules. A manual update asks you to unfreeze first.
- **Remove.** Two taps. The module's folder is deleted. Its settings and data are kept. **Remove and delete data** also deletes its settings, data, runtime and log folders (see [Configuration files](#configuration-files)); a partial deletion is reported. A linked module (a symlink in `plugins/`) is only unlinked. For a module with protected system components, use its dedicated uninstaller first. Removing its Framey interface does not restore system settings or undo changes elsewhere on the system.
- **Reload.** Reloads only that module. A module that failed to load, render or answer shows a red row with the message, the time, **Disable** and **Reload**.
- **Compatibility line.** One of "Compatible", "Needs a newer Framey", "Built for an older Framey", "Not for this platform", "Needs a different API generation" or "Compatibility not declared". It is based on `framey.min`, `framey.max`, `platform` and `api` in `plugin.json`. Installs and updates refuse a module that is clearly incompatible before any file is replaced.
- **Zip install.** The Modules page has a box for an `https://` link to a module `.zip`. Downloads must stay on `https` at every redirect and are abandoned after 45 seconds. Limits: the zip itself 5 MB, 500 entries, 5 MB per entry, 20 MB unpacked, 8 folder levels. Installing from a link asks you to confirm first; the confirm step names the host and says the plugin and its backend run code with your user's permissions.
- **Local store folder.** Modules in `~/framey/store` are listed on the Modules page and can be installed from there. The folder does not exist by default.
- **Catalog-installed modules.** The Store records the reviewed source commit and package folder. When that catalog entry changes, the Store can offer **Update module name**. A pinned catalog snapshot does not start following a repository branch; see [Saved data and later updates](catalog.md#saved-data-and-later-updates).

### Leaving a running operation

Progress screens offer **Back** and say "You can go back while this operation continues." Back returns to the page; it does not cancel an install, update, repair or rollback already accepted by the loader. **Cancel** on a confirmation screen returns without starting that action. After leaving a running operation, return to Modules or Settings to check the installed version and result. A slow response or page timeout does not mean that the loader stopped the work.

## Updates

### Modules

The generic update, **Versions** and **Repair** paths below apply to modules with a supported recorded source. Catalog-installed modules retain a pinned package receipt and refuse these generic paths. Use the Store for eligible catalog snapshot updates; it currently has no separate repair or downgrade action. Modules still provides enable/disable, freeze, restore and removal for them. See [Saved data and later updates](catalog.md#saved-data-and-later-updates).

- **Check for updates** only looks. It records the result and never installs. It also asks GitHub for upstream status of modules whose last result is older than 24 hours or failed.
- **Update** is two taps. The release notes are shown at the confirm step. The row shows the steps check, download, verify, unpack, swap and reload as they run. Framey stages the new files, checks them and swaps them in. If the swap fails, the old copy is restored. After a successful update the previous version is kept for **Restore previous version**.
- **Update all** shows a plan first: each module with its current and target version, and any module skipped with the reason. **Update N** applies the plan. One failure does not stop or hide the others. The result is listed under "Last update result". Protected components (Fan Control) are always skipped with the reason `protected: updates from its own card`; they update from their own card through the signed flow. A linked folder is skipped with the reason `linked folder`.
- **Partial update.** If the protected part of a module (see [Fan Control](#fan-control)) did not reach the new version, the row shows **Retry protected step**, **Check again** and **Restore previous version**.
- **Repair** reinstalls the installed version from its recorded identity and keeps settings and data. A successful repair also keeps a previous copy: a repair of the same version keeps the older kept copy, so **Restore previous version** still goes back to it. **Repair and reset settings** also deletes the module's settings folder, after a second confirmation.
- **Versions** lists releases ten at a time, with **Show more versions**. **Install this version** installs one. Going back to an older version asks for confirmation: data migrations made by newer versions are not undone, and the module may not read newer data. After every successful update, repair or version install, one previous version is kept. **Restore previous version** is offered whenever a kept copy exists, and brings it back. **Keep this version** deletes the kept copy. A later update replaces it with the version that was installed before that update.
- Modules with a protected part (Fan Control) only move to a newer signed release through their own action. The version list does not install an older protected version.

### Framey

Settings > **Framey** shows the installed version (the release tag when the installed commit is that release, otherwise the short commit). Then:

- **Check for updates** looks only.
- **Update** checks first when no candidate has been checked, then asks you to confirm the displayed release and commit. Framey validates that selection before replacing files and shows the steps check, download, verify, unpack, swap and reload as they run. It downloads the loader archive for that commit from GitHub over HTTPS and stages it next to its folder (`~/framey.new`). It checks that `framey.py` compiles, starts it once in a smoke test, keeps the old folder as `~/framey.bak` and restarts its service. The swap is two renames, not one atomic step: a crash between them leaves no `framey.py` at the service path (see [Known limitations](#known-limitations)).
- After the restart, **Done** deletes the backup. **Roll back** swaps the backup back and restarts. Going back to an older Framey asks for confirmation.
- **Reload modules** reloads all modules without restarting the service.

### Channels

The update channel is set only in `~/.config/framey/loader.json` (`channel`). There is no control in Settings; Settings shows the channel read-only and names the file. The value is `stable` (default) or `prerelease`. `prerelease` also needs developer mode (the `developer` file, see [Developer mode](#developer-mode-and-diagnostics)). Without developer mode the stable channel is used, and the Framey check result reports `channel_fallback`. Prerelease tags are only offered on the prerelease channel, both for Framey and in the version lists of modules. Changing the channel never installs anything.

The stable channel also rejects prerelease versions declared inside a module's package, even if its release tag looks stable. Repair may reinstall the identical prerelease already installed when its immutable identity is recorded.

### Automatic checks

"Check automatically" is off by default (`prefs.autocheck`). When it is on:

- One check runs 90 to 390 seconds after the loader starts. After that the timer fires again every 24 hours while the switch is on.
- A check runs when the panel opens, if the last check is at least 24 hours old.
- After a failure, the next attempt waits 15 minutes, doubling up to 6 hours.
- A cycle checks at most 20 modules, two seconds apart.

Automatic checks never install anything. "Tell me about Framey updates" and "Tell me about module updates" (both off by default) show a notice with an action that opens Settings or Modules.

### Digest and integrity

When GitHub publishes a SHA-256 digest for a release asset, the download must match it, or it is refused. When GitHub publishes no digest, the result says "no digest published". This is an integrity check, not a signature. Fan Control's releases also carry a signature, checked as described in [Fan Control](#fan-control).

## Upstream project status

Modules installed from a GitHub repository get an **Upstream project** chip on their card. It is information only: Framey never disables, removes, blocks or warns before an update because of it, and it gives no score. An old commit is not treated as abandonment.

Chip labels (exact text):

| Label | Meaning |
|---|---|
| Archived | GitHub reports the repository as archived (read-only). |
| Unmaintained (declared) | The upstream `plugin.json` has `"maintenance": {"status": "unmaintained", "by": "...", ...}`. Framey shows the file and commit it read this from. |
| Recent activity | The commit at the recorded branch or pinned revision, or for a whole repository the latest release, is inside the activity window. |
| No recent activity | Nothing inside the window. This does not establish abandonment. |
| Unknown | Nothing could be read: a ZIP install with no upstream source, a non-GitHub source, or GitHub could not be reached, answered "not found" or private, or rate-limited. |

- **Activity window.** 365 days by default. Settings sets 30 to 1825 days. Changing it relabels cached results without a network request.
- **Pinned sources.** Commit evidence follows the recorded revision. Whole-repository checks can still show recent activity because of a newer repository release. For a plugin subfolder, repository releases are shown as evidence but do not determine its activity label.
- **Stale marker.** Shown next to the chip when the stored result is older than 24 hours, was never read, or the last refresh failed.
- **Details.** Tapping the chip opens the repository and branch read, the scope (the module's own folder, or the whole repository), commit and release dates, the archived flag, any declaration, fork information, the evidence list and the reason a refresh failed. **Refresh** and **Refresh all** force a new check.
- **Checks** use GitHub's public API without a token. GitHub sees your IP address and the repository names of your modules. Results are cached in `~/.config/framey/maint.json` (at most 64 KB and 200 entries).
- **Background pass.** "Check project maintenance in the background" is off by default. It needs "Check automatically" to be on, because it runs only after an automatic check cycle. Those cycles run daily while the switch is on, and when the panel opens if the last check is a day old. The pass itself runs at most once every 7 to 8 days, at the first cycle after that interval.

## Configuration files

Loader configuration is under `~/.config/framey/`. The page cannot change the files marked "you edit". Framey refuses page requests to change safe mode, the channel and file roots.

| File | Who writes it | Purpose |
|---|---|---|
| `loader.json` | You (over SSH) | Loader keys, below. |
| `developer` | You | Its existence turns developer mode on. Its JSON object may hold a `targets` key (extra injection targets, developer mode only). A file that cannot be parsed, or is over 4 KB, still turns developer mode on, and its `targets` are ignored. |
| `safe` | You | Its existence turns safe mode on. |
| `isolation` | You | Fallback value for `isolation`, read only when `loader.json` does not exist. |
| `settings.json` | Framey | Disabled modules, order, hidden, frozen, preferences, last update results. Written to a temporary file and then renamed. Invalid parts are dropped one at a time. |
| `maint.json` | Framey | Upstream status cache. |
| `catalog.json` | Framey | Selected catalog source, custom URL and up to two saved catalog snapshots. |
| `decky-catalog.json` | Framey | Saved upstream Decky catalog metadata. The Decky source choice itself is held in the page's session storage. |
| `plugins/<id>/` | The module | Its settings (config). |

Per module, Framey also creates `~/.local/share/framey/<id>` (data), `<runtime root>/framey/<id>` (runtime, cleared at reboot) and `~/.local/state/framey/<id>` (logs). The runtime root is `$XDG_RUNTIME_DIR` when it is set. Otherwise it is `/run/user/<uid>`, but only if that is a real directory (not a symlink) owned by your user with mode 0700. If neither applies, there is no runtime folder: every backend fails to start with a runtime error, and the loader writes one log line. There is no `/tmp` fallback. Removal with "Remove and delete data" deletes all of them.

Module settings and data live outside the loader and module code folders. Disabling, hiding, reloading, freezing, updating or repairing a module keeps them. **Remove** keeps them too, so reinstalling the same module id can reuse them. **Repair and reset settings** resets configuration only; **Remove and delete data** removes all of the module's Framey-managed folders. Restoring older code does not reverse data migrations made by newer code. Back up persistent configuration and data before trying versions that may change their format.

### loader.json keys

| Key | Values (default) | Effect |
|---|---|---|
| `isolation` | `false`, `true`, `"auto"` (`false`) | Runs Framey's scripts on the main and bar pages in a separate browser world, so Steam's page scripts cannot see the `fyCall` binding. Any other value counts as `false`. With `true`, if the world cannot be set up, Framey does not inject the page and logs why. With `"auto"`, it logs and falls back to the page context, unless the page already defines `fyCall`; then the page is not injected, as with `true`. Module code in this mode cannot see Steam's own page globals. Not verified on a headset. |
| `trusted_input` | `"off"`, `"auto"`, `"require"` (`"auto"`; any other value counts as `"auto"`) | Framey's controls (its own pages, and plugin controls from `api.ui`, `Framey.button` and notification actions) act on a click or key press only when the browser marks it as user input (`isTrusted`). `"off"`: no check. `"auto"`: no check until the page has seen one genuine trusted click, key press or pointer press; from then on the check applies. So in `auto` a script can click Framey's controls until the first real input. `"require"`: the check applies from the start. Refused clicks are counted as `untrusted_clicks` in diagnostics. The Back and Cancel buttons of core pages are not gated. **Unverified on a headset:** whether Steam Frame controller input produces trusted events. If it never does, `auto` never starts enforcing, and `require` would refuse controller input. |
| `file_roots` | List of up to 8 absolute folder paths (`[]`) | Folders the in-headset file picker may browse, in addition to the data folder. Accepted paths are under `/home/`, `/run/media/` or `/mnt/`. The home folder itself cannot be a root, and the built-in media folder is not offered to pages. Folders are checked again when used. |
| `channel` | `"stable"`, `"prerelease"` (`"stable"`) | See [Channels](#channels). |

## Developer mode and diagnostics

Create the empty file `~/.config/framey/developer` to turn developer mode on, and delete it to turn it off. Settings then shows **Developer tools**. The page is for plugin authors and does nothing by itself:

- **Hot reload** per module (On/Off). When a module's files change, it is reloaded. Hot reload turns itself off after 60 minutes without changes. It uses inotify where available, and otherwise checks file times every 2 seconds, only while it is on. If saving the preference fails, a warning explains that the change is for this session only.
- **Debug targets.** Lists Steam's debug targets from `127.0.0.1:8080` with their inspector addresses, which you can copy. The panel shows an SSH tunnel command for opening them from a PC. Opening a browser inside the headset is not verified.
- **Exposure.** Warns when port 8080 is reachable from addresses other than loopback. Framey does not listen on port 8080. Its only listener is the media server on `127.0.0.1` (random port), used for large module files.
- **Diagnostics.** **Run diagnostics** produces JSON with versions, status, counts and recent loader/module log text. Recognized home paths and sensitive values are hidden. Review the report before sharing: log messages can contain information Framey does not recognize as sensitive.

Framey does not use React, so React DevTools does not apply. Use Diagnostics and Debug targets instead.

## Backend workers and failure containment

A module's `backend.py` runs in its own worker process. Its interface (`main.js`) runs in Steam's page.

- **Lazy start.** A worker starts on the module's first call. Each worker runs with `nice 19`, at most 256 open files, a 64 MB file-size limit, no core dumps and `no_new_privs` (so `sudo` and setuid helpers fail inside a backend). Its environment is an allowlist.
- **Limits.** At most 3 workers run at once. Settings > **Modules running at once** sets the limit from 1 to 3 (`prefs.workers_max` in `settings.json`). When a worker is needed and the limit is reached, the least recently used idle worker is stopped. Idle modules can restart when needed. A worker stops after 120 seconds without a call (`backend.idle`, 10 to 3600).
- **Memory.** Each worker's memory limit is `backend.memory_mb` (default 32, 16 to 64). On Linux it is `RLIMIT_DATA`, set to the worker's data size at start plus this many MB, so it is headroom for growth, not a total size. If it cannot be applied, the worker runs without it, the loader's status shows `limits_applied: false`, and Diagnostics shows `limits_warning`. The limits of running workers are capped at 100 MB in total. A new worker that would take the total past that first stops the least recently used idle worker. If none can be stopped, the call fails at once with a memory-limit message.
- **Timeouts.** A call times out after 10 seconds (`backend.timeout`, up to 60 if the manifest asks). The caller gets "module not responding". Framey pings the worker. A worker that answers keeps running. One that does not is terminated, then killed. Three crashes within 300 seconds stop the module until it is reloaded. Respawns after a crash wait 1, 5 and then 30 seconds.
- **Waiting for replies.** The page stops waiting for a loader reply after 600 seconds for long-running actions, including installs, updates, downloads, checks, repairs, rollback and batch operations; 55 seconds for catalog browsing/source requests, archive assessment and resource URLs; 60 seconds for a module's backend call, which normally gets the worker's own timeout reply first; and 15 seconds for other loader calls, such as the file pickers. The notice is `Framey core did not reply in N s` or `Framey module did not reply in N s`. The loader does not cancel the work, so check the Modules page for its result.
- **Stop.** Disabling, removing, updating, reloading, or stopping the service sends the module `unload` and then terminates its worker.
- **Limit of containment.** This is failure containment, not a security sandbox. Every installed plugin is trusted code. A backend runs as your user (same user ID), with no seccomp, landlock or bwrap restriction, so it can read any file you can. The process-group kill does not catch children that call `setsid` or double-fork. Plugin page code runs in the same JavaScript realm as Steam's page and the loader's page code, so a plugin can read or change anything on that page, including other plugins' DOM. Framey freezes module objects and keeps tokens in closures, which limits accidents, not a hostile plugin. Any script in Steam's page can call the `fyCall` binding unless `isolation` is on. A backend that spins or allocates heavily can still slow the service.

## Resource limits

The Steam Frame performance target is **less than 400 MB total added memory** for Framey, its backend workers and its interface in Steam, with CPU use near zero while idle. This is a target to measure on the headset, not an allowance to consume. The service limit below covers the loader and its child processes; Steam's browser is a separate process, so the interface's additional memory must be assessed separately. Development review workers are not part of the installed application.

- **Service ceiling.** The app's unit sets `MemoryHigh=128M` and `MemoryMax=150M`. The loader and its workers share this cgroup. These are ceilings, not a budget. Going past `MemoryMax` can stop the service. The manual unit uses the same numbers.
- **Measured (indicative).** Measured on one Windows 11 PC with Python 3.14, not on a headset and not under systemd:
  - Idle, 120 seconds, no modules: about 34 MB working set, about 21.7 MB private, CPU 0%.
  - Panel open, 100 backend calls over 10 seconds: median 0.68 ms, p95 1.06 ms, no errors. Two workers used about 51 MB working set in total. One worker used about 25.6 MB.
  - Windows numbers are not comparable with Linux RSS. The service-ceiling scenario was not run because systemd is not available on Windows.
  - The measurements came from a tree that changed slightly afterwards.

## Recovery

- **Safe mode.** Create the empty file `~/.config/framey/safe`, then reload. No module pages load and no backends start. The panel still opens. Delete the file and reload to leave safe mode. Safe mode is a recovery aid, not protection: module code that already runs in Steam's UI is not contained.
- **Startup repair.** At each start, Framey finishes or undoes interrupted changes. It restores a module's previous folder if the module folder is missing and deletes leftover `.stage-` and `.old-` folders, except an `.old-` folder kept for a pending or held restore (see [Updates](#updates)).
- **Damaged settings.** The state of `~/.config/framey/settings.json` is `ok`, `corrupt` or `unreadable`. `corrupt` means it is over 256 KB, is not a JSON object or does not parse. `unreadable` means it cannot be read (permissions or disk error). While the state is not `ok`, no plugin pages load and no backends start, and the Modules and Settings pages show a banner. For `corrupt`, the banner has **Repair settings**. After a confirmation, the damaged file is renamed to `settings.json.corrupt-<unix time>`, settings go back to defaults (the disabled list and order are reset), and the modules reload. The three newest such copies are kept. The first damaged copy is also kept as `settings.json.bad`. For `unreadable` there is no repair button and the file is left untouched; Framey refuses to overwrite it. Plugins stay off until the file is repaired or can be read.
- **Loader restore.** When the loader starts and `framey.py` does not compile but `~/framey.bak` does, it restores the backup. This runs only inside a running loader, so when `framey.py` is missing the App's unit step below is what restores it.
- **App unit recovery.** The unit written by the Framey App has an `ExecStartPre` step that runs before each start. If `~/framey/framey.py` is missing or does not parse, it tries `~/framey.next` (the previous tree, moved aside by an interrupted App install), then `~/framey.bak` and `~/framey.bak.prev`, and uses the first one that parses. It moves the broken tree to `~/framey.broken`, carries `plugins` and `store` over, and moves the chosen tree into place. It does not restore a loader that crashes after it has started. The step is in the App's source (`framey-app`, `src/core.cpp`, not part of this repository) and is not verified on a headset.
- **Backups.** A Framey update keeps `~/framey.bak` until you press Done. The app keeps the previous install as `.bak` and the one before as `.bak.prev`.
- **Rollback.** Settings > Framey > **Roll back** (while a backup exists), or **Restore previous version** on a module.
- **Stopping.** With `KillMode=mixed`, systemd sends SIGTERM only to the loader. The loader removes its page parts (at most 2 s), then stops its workers: an `unload` request with 1.5 s to exit, then TERM with 1 s and KILL with 1 s, at most 5 s in all. The stop of the worker, media and target parts is capped at 6 s. The process then exits without waiting for background threads. The worst case adds up to about 8.3 s, inside `TimeoutStopSec=15`. This is computed from the timeouts, not measured. Remaining processes in the service's cgroup are killed when the stop finishes.
- **Removing Framey by hand.** Run `systemctl --user disable --now framey.service`, then delete `~/.config/systemd/user/framey.service`, `~/framey`, `~/framey.bak`, `~/framey.bak.prev`, `~/framey.broken`, `~/framey.new` (if present) and `~/.config/framey`. (`framey.bak.prev` and `framey.broken` are created by the App's recovery step, which is not in this repository.) This does not undo system changes made by Fan Control; use the app's Remove for that.

## Fan Control

Fan Control is a separate project: [chaosfox26/frame-fan](https://github.com/chaosfox26/frame-fan). Its privileged part is installed by the Framey App, which asks for the headset password once.

If GitHub cannot be reached, rate-limits the request, or the release cannot be verified, Fan Control is not installed or updated and the notice says `Fan Control was not installed: could not reach or verify the signed release`. Framey still updates, and the installed Fan Control is left alone. There is no bundled copy of Fan Control to fall back to.

Its updates are signed. Framey downloads the release and its `.sig`, checks the GitHub digest, verifies the signature with `ssh-keygen` against a root-owned `allowed_signers` file next to the component's installed marker (the path comes from the component's manifest, not from this repository), and only then runs the root helper through a fixed sudo rule. A release that is not newer than the installed one is not installed. Framey does not run the helper without the signature check.

The signed update path has not been run on a headset (see [Verification status](#verification-status)).

## Known limitations

These are limits of the experimental implementation. Code review and isolated tests do not establish how often they occur on a headset.

- **Loader self-update is not signature-verified.** Framey fetches the loader archive for a commit over HTTPS from GitHub, checks its structure, runs it once and swaps it in. It does not check a signature. Fan Control's root parts and signed plugin updates are verified (see [Fan Control](#fan-control)). A compromise of the GitHub repository or its release path would reach headsets at the next Framey update.
- **Self-update is not atomic.** It is two renames (see [Updates](#framey)). A crash between them leaves no `framey.py` at the service path. The App's unit restores `framey.bak` before start; the manual unit does not. `Restart=always` has no start limit.
- **Trusted input is not enforced before the first real input.** The default `auto` lets scripts click Framey's controls until the page sees one trusted event. Whether controller input is trusted is unverified.
- **Plugins are not isolated from each other or from Steam's page.** Frozen module objects and token closures limit accidents only (see [Backend workers](#backend-workers-and-failure-containment)).
- **Backends are not confined.** Children that call `setsid` escape the process-group kill.
- **Native functions are captured after page scripts run.** `inject.js` captures `fyCall` and `Function.prototype.toString` after Steam's scripts start, so a same-page script that runs first can hook them.
- **The `bindingCalled` context filter is open.** Without isolation, `Runtime.bindingCalled` is accepted from any execution context. Without a token only `toggle` is allowed, rate-limited to 5 per second. Filtering by the main context needs `Runtime.enable`, which the non-isolated mode does not use.
- **Corrupt settings of one plugin are replaced on its next write.** Only the first corrupt copy is kept as `.bad`.
- **A late reply from Steam's debug port can drop the page session.** Framey reconnects after 5 seconds.
- **Companion installs** swap two folders with two renames, which the startup repair does not scan. A crash between them can leave an orphan `.old-` folder.
- **Settings writes block the loader** while the disk syncs, so slow storage can delay shutdown.
- **Some removals of `framey.bak` and `.old-` folders do not check for Framey markers.** Only the staging and linked-plugin paths are guarded.
- **The memory cap is not measured on Linux.** Its effect and the `limits_warning` path are checked in code only.
- **The Developer, Store and Decky Store pages are English only.**
- **The update channel has no Settings control.** Change `channel` in `loader.json` over SSH. Settings shows it read-only.
- **Linked folders are never updated.** A single-module Update, a version install and a repair refuse a linked folder (`linked folder: not updated`), and batch update skips it (`linked folder`). Framey does not write through the link.

## Verification status

- **Existing installed build.** Framey 1.0.1 ran on one Steam Frame with SteamOS 0.4.5, with the panel working. The bottom-bar button has also been confirmed working in the installed build. This does not verify the experimental catalog or management changes.
- **ARM64 baseline.** A selected set of 415 isolated tests passed on ARM64. They used fixtures and temporary test state; this was not a full run of the new service inside Steam's VR interface. Earlier Python and jsdom suites were also run offline on Windows, where Linux-only tests were skipped.
- **Experimental device validation.** Full service and VR validation of the new experimental build is not established by that baseline. In particular, the following need separate device evidence:
  - `trusted_input` `"auto"` and `"require"` with real controller input.
  - The isolated world (`isolation`), including whether the browser accepts the world and binding, and controller focus in it.
  - Catalog browsing, source/category filters, details, installation and offline recovery in the Steam VR panel.
  - Decky Store browsing, version selection and read-only archive inspection in that panel. Static metadata or native binary headers do not verify plugin execution or VR behavior.
  - Navigation during a running install, update or repair, and the final result after returning to the page.
  - The Fan Control signed update end to end: the signers file, the helper, the digest, rollback and the partial-update path.
  - Live GitHub downloads, renamed repositories, rate limits and maintenance endpoints; fixture responses do not test the network service.
  - Backend worker limits under the running service, including RLIMIT and `no_new_privs` behavior with real modules.
  - The service memory ceiling under systemd.
  - The upstream chips, details screen and bar badge on the real Steam UI.
  - Safe mode, startup repair, the app's recovery step and the service restart after a Framey update.
- Also not verified: other headsets, other SteamOS versions, and Steam updates. Framey works through Steam's own interface, so a Steam update could break it.
