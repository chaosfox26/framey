# Finding modules and browsing the Decky Store

The experimental branch replaces the Store placeholder with a module catalog. This guide describes that branch; an older installed Framey build may still show "Coming soon". Check the installed commit in Settings. The [user guide](using.md) covers installation, module management and the limits of device validation.

| Tab | What it offers |
|---|---|
| **Store** | Discovery and installation of Framey-native modules, with reviewed catalog snapshot updates. |
| **Decky Store** | Browsing the upstream Decky catalogs and inspecting a selected package for porting evidence. Decky packages cannot be installed here. |

## Framey Store

Open **Framey** from Steam's bottom bar, then **Store**. Enter a name or phrase in **Search modules** and choose **Search**. **Filters** offers category, source and sort controls. Sort by **Name**, **Recently updated** or **Relevance**. **Reset filters** clears the search and category and restores name sorting, keeping the selected source. Use **Load more** for additional results. **Refresh** asks for current catalog data. Loading, empty results and download errors have their own messages, with **Retry** when a request fails.

Choose **Details: module name** to read the description, source, version and compatibility information. Compatibility describes declared Framey, API and platform requirements. Steam Frame uses ARM64 Linux (`linux-aarch64`); an entry marked compatible still needs device testing, especially if it contains native code.

The Store uses **Compatible**, **Not compatible** and **Not checked**, each with a plain reason. **Compatible** means the declared Framey, API and platform requirements match. **Not compatible** means those requirements do not match this device. Missing or unclear platform requirements, or stale catalog data, can produce **Not checked**. These labels do not establish safety, trust or successful VR operation.

## Catalog sources

Choose a source under **Filters**:

| Source | Behavior |
|---|---|
| **Stable** | Shows stable entries and excludes prerelease versions. This is the default. |
| **Testing** | Also shows testing entries and prerelease versions. Requires [developer mode](using.md#developer-mode-and-diagnostics). |
| **Custom** | Enter a **Custom catalog URL**, then choose **Apply source**. Use a public HTTPS catalog URL without credentials, query parameters or a fragment. |

Stable and Testing currently read the same catalog from this repository's experimental branch; they filter its entries differently. Those source labels do not select which Framey loader build runs on the headset. The loader's stable/prerelease update channel is a separate, file-controlled setting described under [Channels](using.md#channels).

The selected source and custom URL persist in `~/.config/framey/catalog.json`, alongside saved catalog data. If developer mode is turned off after selecting Testing, the effective source returns to Stable. Switching sources does not install or update modules. Review the source shown in an entry's details before installing from a custom catalog.

The default metadata request goes to the [experimental Framey catalog](https://raw.githubusercontent.com/chaosfox26/framey/experimental/catalog/index.json); Custom requests go to the URL you select. Installing a module downloads its pinned source archive from GitHub. Those servers receive normal request information, including the requester's IP address and requested URL. Catalog metadata does not grant the module permissions: installing it permits trusted code to run as your user, as explained below.

## Install a module

Framey is independent of Decky Loader and is not affiliated with or endorsed by it. A Framey-native port of a Decky plugin is experimental; compatibility varies. Decky and plugin authors are not expected to support failures specific to Framey.

1. Open its details and review its source and compatibility.
2. Choose **Install module name** to open **Review installation**.
3. Read the trust notice, then choose **Install** to start, or **Cancel** to return without installing.
4. Wait for the result, then open **Modules** to manage the installed module.

Modules and their backends run code with your user permissions. Catalog inclusion, a stable label or an immutable source commit does not prove safety, provide package signing or establish headset testing. The installation checks the package and compatibility before replacing module files. An ineligible entry cannot be installed. An already installed entry normally directs you to Modules; catalog-managed entries can offer an update as described below.

This catalog installs Framey modules. It does not establish compatibility with packages written for another plugin loader.

Installation can run module code: after replacing files, Framey reloads the module and may start an enabled backend for a health check. An enabled module's frontend can be loaded into Steam's interface. Review the source before confirming installation, rather than treating installation as a download-only step.

The progress page says "Installation continues when you leave this page." Leaving the page does not cancel a request that the loader has accepted. If an operation is slow or the page times out, return to Modules to check what is installed before trying again.

## Saved catalog data and offline use

The Store identifies when it is showing saved catalog data or the bundled catalog, with saved and last-check times when available. A catalog request refreshes data older than 24 hours; an ordinary failed refresh waits 15 minutes before another request retries automatically. **Refresh** can retry an ordinary failure sooner. It cannot override a server denial or rate-limit cooldown, described below. There is no separate catalog background timer. Up to two source snapshots are kept in `catalog.json`.

A bundled snapshot can help you browse when a download fails; it is not evidence that the online catalog is current. When the page says **Showing saved catalog data that may be out of date.**, or is using the bundled fallback, installation is unavailable. Stale data can mean it is old or its last refresh failed; it does not by itself prove that the headset is offline. Check the connection and choose **Refresh** before trying to install. A fresh saved catalog can be used for installation, but only catalog metadata is cached: the package still downloads from GitHub and needs a network connection.

A failed refresh does not replace the last good catalog with an error response. If the catalog or an entry changed after you opened its details, return to the list, refresh and review the entry again. Installation requires the identity of the entry you reviewed, so confirming an old screen cannot silently select a different package.

## What is included

The bundled catalog lists **Hello** and **Starter**, the plugin development examples from this repository, at a pinned source snapshot. These are examples for exploring Framey's APIs. Their catalog `stable` channel describes the snapshot, not production readiness.

**Fan Control** is an informational setup entry. It cannot be installed through the catalog because its cooling components require the separate signed privileged setup in the [Framey App](https://github.com/chaosfox26/framey-app). See [Fan Control](using.md#fan-control) for its installation and update behavior. The catalog does not provide an alternate unsigned route to those components.

## Saved data and later updates

The Store can offer **Update module name** when a module installed from the catalog has the same id, repository and package folder but the catalog points to a different commit. Review that snapshot before confirming it. Catalog installations record an immutable source commit; a pinned example does not automatically start following the repository's main branch.

The Store refuses updates for frozen, linked or protected modules, a catalog version older than the installed version, and modules with a pending or retained previous version. Use Modules to unfreeze the module or finish the previous operation first. The Store has no downgrade action.

Use **Modules** for enable/disable, hide/show, ordering, freeze, restore and removal. Its generic **Versions** and **Repair** paths refuse catalog-installed modules, preserving the catalog's pinned package identity and folder. Other modules can use their recorded source for updates, versions and repair. Module checks and automatic checks do not install updates; an update requires an explicit action. See [Updates](using.md#updates).

Settings and module data are stored outside the installed code. Updates and ordinary removal keep them; **Remove and delete data** deletes the module's Framey-managed settings, data, runtime and logs. Returning to older code does not undo a newer module's data migrations. See [Configuration files](using.md#configuration-files) for the persistent locations.

The local `store/` folder and the **Modules** page's HTTPS ZIP install remain separate ways to add modules. Their presence does not make an archive a trusted catalog entry.

## Decky Store

Open the separate **Decky Store** tab to browse the public upstream listings. Search, category filters, sorting, details and **Load more** work as in the Framey Store. Its source choices are **Decky Store** and **Decky Store Testing**; the testing source requires developer mode. This choice is remembered in the Steam page's session storage and does not change the native Store's selected source.

Framey is independent of Decky Loader and is not affiliated with or endorsed by it. Experimental compatibility varies, and Decky or plugin authors are not expected to support Framey-specific failures. Listing a plugin here preserves its upstream attribution; it does not imply endorsement or official Framey support.

Use **Report to Framey** in native or Decky catalog details and relevant error views for a Framey-specific problem. The editable preview targets the Framey runtime repository and lets you change the draft or remove optional diagnostics before opening GitHub. Opening a draft does not create an issue; you submit it yourself. See [Report to Framey](using.md#report-to-framey) for the fields, copy fallback and browser-opening limits.

Decky entries show **Not compatible** with the reason "Built for Decky’s interface; it needs a Framey version before it can run here." Processor support is separate: **ARM64: Not checked** means that support has not been established, rather than that the package contains no native code. There is no Install or Update action for a Decky entry.

### Inspect a selected version

1. Open the entry's details and choose a **Version**. The page shows the version name, SHA-256 and published/download information supplied by the catalog.
2. Choose **Inspect compatibility**. The notice explains that this downloads only the selected archive for a bounded, read-only inspection and runs no plugin code.
3. Read the **Archive inspection** result, or the failure message and retry when appropriate. A stale catalog must be refreshed before inspection.

The inspection checks the archive's SHA-256 against the selected catalog version and reads selected metadata and native file headers. It reports detected frontend/backend files, native binary architectures, declared root access, package dependency names and limited hardware hints. Long result lists and paths can be shortened. This is not an exhaustive dependency or hardware review. It does not install, import or execute the plugin. It does not read every file fully or validate every file's CRC. A matching SHA-256 is an integrity check against the catalog, not a publisher signature.

An x86-only package needs an ARM64 build for Steam Frame. Finding ARM64 binaries provides evidence about those files only; missing, mixed or unrecognized binaries leave further work to do. Every Decky result still requires a Framey port. Steam Frame's ARM64 SteamOS and VR interface differ from the Steam Deck environment assumed by many Decky plugins, including its hardware paths and privileged helpers.

The optional [Decky-style source-port helper](decky-compatibility.md) helps authors adapt a small set of source API patterns. It is not binary emulation or a complete Decky/React runtime. An author must port the frontend, backend, manifest and platform-specific behavior before distributing a Framey-native module. Archive inspection does not establish that this port exists or works in VR.

Browsing, installing, frontend loading and backend execution are separate capabilities. This Decky Store tab currently browses metadata and inspects archives only. It neither loads a Decky frontend nor executes a Decky backend. A separately adapted Framey-native port follows Framey's normal module lifecycle: its frontend runs in Steam's page, and its backend runs in a worker process when called, including an installation health check. Such a port remains experimental and needs its own runtime and VR evidence.

### Decky cache and network requests

Opening this tab reads public metadata from the [Decky Store feed](https://plugins.deckbrew.xyz/plugins) or [Decky Store Testing feed](https://testing.deckbrew.xyz/plugins). Browsing and opening details do not download plugin archives. **Inspect compatibility** downloads the chosen version from its public HTTPS artifact URL; when no artifact URL is supplied, the feed's SHA-256 selects an archive on the [default archive host](https://cdn.tzatzikiweeb.moe/file/steam-deck-homebrew/versions/). Remote servers receive normal request information, including the requester's IP address and requested URL.

Catalog requests and Decky archive inspection use Framey's backend networking. They identify the application as `Framey/<version>` with its project URL in the User-Agent; they do not identify themselves as Decky Loader. A denied or failed request produces an error or a saved-catalog warning rather than an alternate route around the server's decision.

Metadata is limited to 524,288 bytes per feed request and saved in `~/.config/framey/decky-catalog.json`. Requests refresh old data after 24 hours; ordinary failed refreshes retain usable saved data and wait 15 minutes before an automatic retry. **Refresh** can retry an ordinary failure sooner. There is no bundled Decky catalog. If no saved data exists and the feed cannot be reached, the tab reports that the catalog is unavailable.

For catalog and Decky archive requests, an access denial or rate limit produces a clear Framey-facing message. HTTP 401/403 means access was denied; HTTP 429 means the server asked Framey to slow down. HTTP 503 with a positive, supported `Retry-After` delay also pauses requests and shows a temporarily-unavailable message. The cooldown is at least 15 minutes and honors a longer supported delay, up to seven days. **Refresh** and repeated inspection cannot bypass it. Cooldowns are saved with the catalog cache and restored after a normal restart; a cache-save failure is reported. Other ordinary transient failures, including HTTP 503 without that delay, can be retried with **Refresh**. Framey does not switch identities or endpoints to evade the response.

Package inspection is limited to a 5,000,000-byte download and a 50-second assessment deadline. Archives are also subject to bounded entry sizes, file counts and paths. Unsupported or oversized archives fail inspection; their compatibility remains unverified. Only metadata is cached for later browsing, so inspection still needs a network connection.
