# Steam Frame integration map

This map records provider evidence collected on October 27, 2026 for Framey's experimental compatibility work. It combines a development-source contract with bounded observations from one headset, not a stable Valve API or a promise that a plugin works. Steam browser internals, installed software and context availability can change independently of Framey.

Framey keeps integration adapters in its own code. It does not install Decky Loader, React, ReactDOM, Preact or Decky's UI library to obtain these providers. Original packages retain their authorship and legal files. Framey-specific integration fixes belong in Framey; this work does not require changes or pull requests to upstream plugins. See the [experimental runtime](decky-runtime.md), [plugin API](plugins.md) and [panel placement](panels.md) for their separate contracts.

## Reading the evidence

| Evidence level | Meaning | What remains unproven |
|---|---|---|
| Live read | A bounded getter was read in an existing context without registering a listener. | Future values, transitions, other contexts and complete plugin behavior. |
| Present with static contract | A method or field exists; its installed bundled implementation or usage was inspected. | Successful invocation, callback delivery and runtime cleanup unless separately stated. |
| Header/source contract | A matching declaration or Framey source path was inspected. | Installed interface support and live binding. |
| Packaged source contract | Framey packages an owned API, host policy and lifecycle implementation; scoped host checks exercise their boundaries. | Live installed UI and plugin functions; controlled-browser and private ARM64 service evidence are separate. |
| Private service on ARM64 | The copied production service read real cached Steam data through its actual token/authorization/core path under a private installed-plugin fixture. | Live installed UI, the separately running core's ownership registry, Steam transitions, frontend JavaScript-to-native delivery and full-plugin acceptance. |
| Backend operation | A reviewed operation ran through a real Framey worker on ARM64. | Frontend rendering, browser event delivery, Steam page changes and physical input. |

An unavailable provider is a capability result. Adapters must not manufacture successful values, claim broad support from a mounted panel, or silently initialize a different provider to fill the gap.

The separately reviewed [Stage 7 host and event corrections](decky-runtime.md#reviewed-host-and-event-corrections) have local synthetic adapter evidence for notification actions, provider method receivers, timer ownership and a narrow Terminal event envelope. Those checks supply no new Steam provider and establish no original Terminal producer/output, PTY, native notification click or headset behavior. They apply to fresh imports/regenerated wrappers, with existing installed-wrapper refresh pending. The previously measured provider, backend and module-delivery results below retain their original scopes; no additional original-method or device credit follows from this host composition.

## Packaged cached-read API

Current development source packages `api.steam.read` through Framey's owned [caller service](../fy_steam.py), [data projection](../fy_steam_data.py) and [browser transport](../fy_steam_transport.py). The [API types](../types/framey.d.ts) describe three operations. These return copied snapshots; plugins do not receive a Steam object, target lease or transport handle.

| Operation | Input | Successful snapshot |
|---|---|---|
| `visibleApps` | No arguments or an empty argument object. | `apps`, `visibleCount`, `truncated` and `status` (`empty`, `present` or `partial`). At most 4,096 visible-library records are projected; this is not the full owned or running-app inventory. |
| `app` | An argument object containing only a positive 32-bit `appId`. | `app` plus `status`: `not_found` with null, or `present`/`partial` with a matching projected record. |
| `focus` | No arguments or an empty argument object. | `focusedAppId`, `focusedWindowId`, `focusedOverlayAppId`, `focusedOverlayPid` and `status` (`empty` or `present`). Fields are bounded strings, finite numbers or null. This remains window-focus data. |

Every projected app record contains `appId` and four separate raw fields: `steamDeckCategory`, `steamOsCategory`, `steamMachineCategory` and `steamFrameCategory`. Missing categories are null and make the record partial. Numbers are not converted into compatibility labels or ProtonDB ratings. A successful reply has `ok` and `available` set to true, a `data` snapshot and the source tag `steam-appstore-cache` or `steam-focus-cache`.

Invalid, unavailable, ambiguous, stale, unauthorized, timed-out and malformed reads reject with a bounded error code. Admission also bounds concurrency and request rate. An unavailable read is not a successful empty result. The API accepts only these operations and their narrow arguments; plugins cannot supply a debugger endpoint, target, method or JavaScript expression. Reads use fixed getter expressions and register no Steam subscriptions.

### Source policy and lifetime

The production host policy selects existing page targets from one exact HTTPS Steam loopback origin and requires the local debugger listener to be owned by the current user. It excludes Framey surfaces, main/bar injection surfaces and configured developer targets. A target reserved for Framey or developer injection stays excluded for the rest of the host process, even after its injection session ends; a later capability match cannot clear that exclusion.

Caller admission requires a currently installed, enabled plugin, its current token identity and unchanged manifest/source filesystem identity. Reload, token replacement or removal, unload/disable and settings saves invalidate the corresponding grants. A result is checked again after asynchronous work, so an old caller cannot obtain a late reply under a replacement identity.

The transport binds each read to the observed main-frame default context, session and generation. Context destruction/clear, navigation, detach, disconnect and host-policy revocation invalidate leases and pending reads. Reauthorization requires fresh host discovery. Shutdown closes admission, retires active reads and awaits their cleanup and socket closure; the host connection/listing primitives drain owned writers even under repeated cancellation.

A controlled Chromium fixture additionally exercised the actual Framey host, target listing, WebSocket primitive, user-owned-listener check and production service/data/transport stack. It used a locally fulfilled HTTPS page containing synthetic Steam-shaped data, temporary private installed-plugin fixtures and isolated debugger configuration. Real navigation invalidated the old lease and a fresh read recovered; revoking one caller left another caller able to read. Shutdown drained owned readers, connection tasks and writers, with no remaining owned asynchronous tasks.

The controlled-browser result verifies transport and lifecycle behavior over synthetic data. A separate private ARM64 headset experiment then ran the copied production service on normal-user CPython 3.12.3, using a private installed-plugin fixture and the actual token, authorization, core-dispatch and service path. `visibleApps` returned a valid, untruncated visible-library projection with raw numeric categories; `focus` returned four empty numeric scalars. No app identities were retained. The single-app `app` lookup was not exercised in that device proof.

The headset experiment preserved the production host, target-listing, WebSocket, ownership and policy guards. It used its own private settings and registries; it did not reuse or reproduce the ownership knowledge of the separately running live core. Caller invalidation and actual service shutdown completed, with no additional pending tasks, unchanged socket totals and unchanged SteamVR process identities. Original/copied source and private configuration bytes stayed unchanged, and the private remote bundle was removed. No Steam transition was induced.

Source contracts, controlled-browser evidence, private ARM64 service evidence and the earlier pure-getter observations below remain separate. The private service proof does not establish a live installed interface, the frontend JavaScript-to-native path, imported-plugin functions, badge attachment, physical controller behavior or an installed release. Refer to the [runtime evidence](decky-runtime.md#evidence-and-what-it-does-not-establish) for the separate combined local checks.

## Native JavaScript delivery evidence

Current development source packages an owned native issuer, immutable loopback JavaScript server, caller-scoped SDK and iframe graph host for supported managed multi-file ESM imports. Original JavaScript bytes and hashes are preserved. The limits are 64 files, 2 MiB per file, 8 MiB per package and a 16 MiB aggregate resident/capture budget. Leases last 600 seconds and the SDK attempts renewal every 240 seconds. A long sleep or lost lease requires explicitly reopening the plugin; this is not an indefinite availability promise. See the [managed module contract](decky-runtime.md#managed-multi-file-javascript) for path, provenance, lifecycle and unsupported-resource limits. This delivery mechanism supplies no Steam provider by itself.

A separate private ARM64 browser feasibility probe imported two tiny Framey-owned modules with relative/lazy imports and stable module identity, using the actual headset browser, owned iframe and immutable HTTP server. Owned iframe/server resources were removed, socket totals and SteamVR process identities stayed unchanged, and the private bundle was removed. The probe used its own private registries, and did not certify the live core's ownership state. It did not execute an original plugin, deploy an imported interface or verify the packaged issuer/SDK/host path end to end. The new Phase 5 combined local Python, JavaScript, browser and configured checks passed. Separate desktop production-path acceptance loaded all nine original Deck Shelves 2.0.0 modules and mounted its panel at two widths, with late-issuance and pending-import disposal checks. That disposable context explicitly allowed local-network access after default policy blocked loopback loading; permission portability is unproven. No original controls, backend, Steam provider or headset interface ran, and Steam patching remained explicitly unsupported. See the [runtime evidence](decky-runtime.md#evidence-and-what-it-does-not-establish) for the exact scope. Neither native module delivery nor this feasibility result establishes Steam page attachment, controller input or full-plugin functionality.

## Existing Steam browser contexts

The full Steam frontend, main/bar overlays and shared JavaScript context are different provider locations. The sampled full frontend exposed `window.appStore` and `SteamClient.Apps`; the sampled main/bar overlays did not expose the same full Apps surface. `FocusedAppWindowStore` was available in `SharedJSContext` and absent from the sampled main overlay.

Select an existing context by the exact capabilities needed for the operation. Do not assume a page title, target order or a context chosen for the bottom-bar button identifies the app-library provider. A successful read in one context does not expose that provider to an imported plugin automatically.

A later private prototype read bounded app-projection and focus shapes in an existing main-frame default context. It observed numeric Steam categories and four empty numeric focus scalars, without retaining app identities. The temporary session closed; no native Steam methods, callbacks or UI changes were involved. Raw category numbers did not establish enum labels, default semantics or ProtonDB tier equivalence.

That earlier prototype established provider availability only: product ownership and exclusion of product-owned targets were unverified, and no navigation, context destruction or invalidation transition was observed. The packaged source now implements the ownership and lease boundaries described above. The controlled-browser fixture supplies synthetic-data lifecycle acceptance, and the later private ARM64 service proof adds real cached-read evidence under a private fixture. Neither retroactively expands the earlier prototype's ownership or transition evidence.

### App metadata and compatibility categories

| Provider | Observed contract | Limits |
|---|---|---|
| `window.appStore.allApps` | Synchronous cached list of visible app-overview records; live list read and installed getter inspected. | This is the visible library list, not a complete owned/hidden-app inventory. |
| `window.appStore.GetAppOverviewByAppID` | Cached lookup returning an overview or null; descriptor and installed body inspected. | Individual lookups were not used to establish plugin support. |
| `AppOverview.appid` and `AppOverview.gameid` | Numeric app ID and string GameID shapes were observed. | They are distinct identifiers; shortcut/mod GameIDs must not be treated as interchangeable store app IDs. Keep values internal. |
| `steam_deck_compat_category`, `steam_os_compat_category`, `steam_machine_compat_category`, `steam_frame_compat_category` | Separate cached category getters backed by packed Steam hardware-category data. | These describe different Steam compatibility categories. Preserve category identity and unknown states. |
| `BIsSteamFrameCompatible` | Function descriptor and comparison body inspected. | The function was not called during this inventory. |
| `AppOverview.installed` | Installed getter selects the most available per-client record. | It does not by itself prove installation on the local headset. |
| `AppOverview.local_per_client_data` | A separate local-client cached getter was inspected. | Missing local data needs an explicit unavailable/unknown result. |

Steam's Deck, OS, Machine and Frame categories are separate from ProtonDB ratings. No ProtonDB global was established by the known-global inventory. A ratings integration needs its own reviewed provider, app-ID mapping, network/cache behavior and UI attachment contract; reading Steam categories cannot substitute for that work.

Installed source also declares `size_on_disk` and `rt_purchased_time`; the followup did not sample their live types, units or values. A consuming plugin's interpretation of these fields is not additional host verification.

No synchronous running-state getter was established directly on `appStore` or `AppOverview`. Installed source contains a separate running-apps provider, but that source observation does not establish its runtime resolution or update semantics.

### Subscription candidates

These methods were present, with installed bundled usage inspected. **None was registered by the inventory.** Function presence or a native function's reported argument count does not prove the complete call contract.

| Candidate | Static evidence | Cleanup and validation limit |
|---|---|---|
| `SteamClient.Apps.RegisterForAppOverviewChanges` | Existing AppStore initialization consumes overview updates. | Returned cleanup contract was not verified; delivery was not exercised. |
| `SteamClient.Apps.RegisterForAppDetails` | Existing AppDetailsStore receives per-app details. | Installed usage calls the returned handle's `unregister` method; this cleanup shape is static evidence only. Registration, delivery and runtime disposal remain untested. |
| `SteamClient.GameSessions.RegisterForAppLifetimeNotifications` | Existing bundled callback consumes app identity and running-state fields. | Cleanup was not verified; delivery was not exercised. |
| `SteamClient.Overlay.RegisterForOverlayActivated` | Function present; installed usage stores its returned handle and calls `unregister`. A bundled consumer accepts four positional callback arguments. | Argument meanings remain incomplete; registration, delivery and runtime cleanup were not exercised. |
| `SteamClient.OpenVR.RegisterForVRSceneAppChange` | Method present in the shared context. | Signature, returned disposer and event delivery remain unknown. |

Any future subscription adapter must first establish its exact callback shape, registration ownership, disposer and unload behavior. A read-only getter adapter must not register subscriptions implicitly.

### Further Steam contracts

These followups inspected native method descriptors and installed source or original consumer requirements. No listed native getter, subscription or mutator was called.

| Surface | Established evidence | Remaining limits |
|---|---|---|
| Launch options | App-details consumers read `strLaunchOptions`; installed usage passes app identity and an edited string to `SteamClient.Apps.SetAppLaunchOptions`. | No actual launch options, write, return/failure behavior or timing were sampled. Timeout and synchronous-delivery cleanup need explicit treatment. |
| Steam audio | `SteamClient.System.Audio.GetDevices`, `RegisterForDeviceVolumeChanged` and `SetDeviceVolume` are present. Installed usage consumes device collections and passes device identity, audio type and volume in that order. | `GetDevices` was never called. Runtime shapes, ranges, numeric selector meanings and the volume subscription's native disposer remain unverified. Consumer-only default-device flags are not host-verified. |
| Downloads | `SteamClient.Downloads.RegisterForDownloadItems` is present. Installed usage receives an initial-state flag and client-grouped item collections. `QueueAppUpdate`, `SetQueueIndex` and `ResumeAppUpdate` take client identity as the final argument in installed usage. | Native registration cleanup and callback delivery remain unverified. Legacy flat callbacks and omitted-client calls appear in original consumers, not verified current host behavior. An empty grouped callback cannot safely select a legacy mode. |
| Resume from suspend | Direct `SteamClient.System.RegisterForOnResumeFromSuspend` is absent. Bundled code uses an internal SleepManager subscription with static `unregister` cleanup. | That internal module was not resolved as an exposed global, initialized or registered. It is not a verified drop-in fallback. |
| Client restart | `SteamClient.User.StartRestart` is present and installed UI has a boolean-argument call site. | Full parameter meaning and return/failure behavior remain unknown; no restart was performed. |

These Steam audio candidates are distinct from the read-only OS audio list results below. Neither source inspection nor a consumer awaiting a call proves its native Promise/result contract. Download queue changes, launch-option edits and client restart remain mutating operations requiring their own consuming-behavior validation.

## Focus, scene identity and overlay state

The existing shared-context `FocusedAppWindowStore` exposes four pure getters: `GetFocusedAppID`, `GetFocusedWindowID`, `GetFocusedOverlayAppID` and `GetFocusedOverlayPID`. All four were read successfully and returned valid empty values in the idle sample. Installed function bodies were simple field reads. No focus change was induced and no listener was registered.

Framey's existing browser adapter requires all four getters and validates bounded scalar results. It accepts the Steam placement target only when all four are empty. A nonempty result does not establish support for placing a panel beside that app. Missing getters, exceptions and invalid result shapes must remain unavailable results.

An empty focus sample does not prove that no VR scene exists. Steam window focus, current scene, scene-focus process, last-frame renderer and overlay rendering ownership describe different states. Keep those states separate in adapter results.

Native OpenVR headers offer candidate getters for scene, compositor, input-availability and known-overlay metadata. The installed native library and exports were discovered, but this inventory did not load it, initialize a native client, bind a function table or invoke these getters. Library presence does not prove acceptance of an interface version.

Framey's existing native overlay adapter uses **`IVROverlay_027`**. The captured header used for the candidate inventory declares **`IVROverlay_028`**, whose table slots differ. Match the exact interface version, ABI, signatures and table layout before binding; never reuse slots across those versions. The captured application, compositor and system interfaces likewise need their own version checks.

Native session ownership is also part of the contract. Existing Framey overlay getter paths can lazily initialize an Overlay client when no session exists; calling them is not necessarily a passive availability probe. A future native provider needs a reviewed session strategy and matching release behavior. No restart requirement was established by the read-only inventory.

Browser document readiness, popup construction capability and cached panel status do not independently measure compositor visibility. Overlay visibility/input-method flags do not prove successful controller input routing. Physical VR visibility, placement and controller acceptance need their own validation.

## Controller and input candidates

The full Steam frontend exposed `SteamClient.Input` and several input-message registration methods. No controller method was invoked, no input values were collected and no callback was registered. Equivalent availability in main/bar overlays or Framey-owned windows was not established.

| Candidate | Observed contract | Remaining limits |
|---|---|---|
| `RegisterForControllerInputMessages` | Function present. Installed usage passes one callback; a shared handler demonstrates five positional arguments for controller index, input enum, digital pressed truthiness and relevant analog coordinates. Installed callers retain a handle and call lowercase `unregister`. | Callback scalar types, ranges, frequency, initial delivery, listener interactions and VR-overlay delivery were not observed. Cleanup is static evidence only. This does not establish controller-state or button-mask snapshot semantics. |
| `RegisterForControllerAnalogInputMessages` | Function present; installed source uses the same shared input handler. | Analog-specific disposer and activation requirements remain unresolved. A digital listener's disposer must not be assumed to release analog registration. |
| `RegisterForUnboundControllerListChanges`, `RegisterForControllerCommandMessages`, `RegisterForControllerConfigInfoMessages` | Functions and bundled consumers observed. | Complete payloads, runtime delivery and cleanup remain unverified; these serve distinct list, command and configuration needs. |
| `GetControllerPreviouslySeen` | Function present; bundled usage expects an asynchronous collection of historical controller types. | Not invoked; historical types are not connected-controller state. |
| `RegisterForUIVisualization` | Function and static registration/disposal usage present. | Paired start/stop operations are stateful; this is not a passive controller-state provider and was not activated. |

`RegisterForControllerStateChanges`, `GetControllerState`, `GetControllerList` and `GetConnectedControllers` were absent from the inspected native namespace. Do not implement a supposed verified state API under those names or substitute input messages without an explicit semantic contract. Separate internal controller-service messages were found in bundled source, but their module, transport and initialization lifetime were not established as a global API. Initializing an internal controller manager can have synchronization effects and was not part of this inventory.

`SteamClient.Input.ControllerKeyboardSetKeyState` is present. Installed usage demonstrates two positional arguments for key enum and boolean press/release, but it was never called. Original numeric key mappings, native return/failure behavior and focused-browser delivery remain unverified. A future owned injector would need to release every key it pressed on failure or unload.

Haptics, rumble, binding changes, analog enablement and visualization control were not exercised. Method presence does not establish controller operation or physical input acceptance.

## Steam page attachment candidates

Read-only inspection found `SteamUIStore`, window-store accessor descriptors and bundled CSS class-map factories in the full frontend. The factories were not executed. No public `Router`, module-export cache, React bridge or Decky global was established. A class token or factory in installed source is build-specific evidence, not a stable page/component API.

All tested grid/details/header/asset selectors had no matches in the sampled host document. The actual gamepad-window documents behind window-store accessors were not inspected. This establishes no usable current badge anchor; it also does not prove those surfaces are absent from every Steam window. Host-document location patterns and generic history property names are insufficient to establish Steam navigation or app identity.

A future Framey-owned passive DOM badge could use standard DOM APIs and a separately reviewed app-data/ratings provider. It still needs an authoritative target document and route, validated app-to-anchor identity, coherent layout, and behavior across navigation and recycled library tiles. App names, broad path matching and a CSS cover token alone cannot establish genuine Steam app identity. Details, library-grid and store attachment are separate integration requests. No badges, observers, timers, listeners or DOM changes were created by this mapping.

Any attachment adapter must own its nodes/styles, cancel pending work and requests, ignore stale navigation results, and disconnect observers on disable, unload or document replacement. It must invalidate bindings when app identity or a recycled tile changes and remove only its own changes. Missing or ambiguous anchors remain unavailable results. Native focus, click, modal and controller behavior need additional validation beyond passive display.

### Browser views and collections

`SteamClient.BrowserView` exposes creation/destruction function descriptors. Installed source describes a window-instance `CreateBrowserView` wrapper, `GetBrowser`, navigation and destruction, plus a three-argument `HeaderStore.SetCurrentBrowserAndBackstack` mutator. The actual window instance and native view were not resolved. Native per-view visibility/bounds methods are consumer requirements only; presence on a created view, geometry, focus, events and cleanup were not verified. No view was created, navigated, resized or destroyed. A future adapter must own every view, listener and delayed action it creates and restore any header, focus, input or style state it changes.

`window.collectionStore` and selected descriptors were present; no collection getter or operation was invoked. Installed source shows that user collections are computed rather than a plain stored array, drag/drop conversion can return null, and add/remove methods start asynchronous saves without returning those saves. Saving a newly created collection can delete existing deletable collections sharing its display name. Membership alone does not preserve dynamic filters and manual inclusion/exclusion state. These are persistence and deletion contracts, not harmless discovery getters; future edits need explicit ownership, full state preservation and completion tracking.

## OS and Python providers

The sampled headset ran Linux with an AArch64 CPython 3.12 interpreter. Architecture checks covered the interpreter and selected installed native extensions. This supports architecture-aware dependency planning, not a guarantee that arbitrary Python packages, bundled executables or native plugins will load.

Installed discovery found `aiohttp`, `psutil`, D-Bus and GObject bindings, plus standard-library facilities. Several optional plugin dependencies were absent from the sampled environment. Discovery did not import third-party modules or establish their complete runtime contracts. A separate static map matched direct backend imports to this discovery; it did not resolve transitive or dynamic imports. Missing dependencies in a minimal test container do not establish absence on the headset. Resolve dependencies per plugin and preserve a clear unavailable result instead of assuming a desktop or Steam Deck environment.

The runtime's [legacy fetch and TLS helper profile](decky-runtime.md#legacy-fetch-and-tls-helper-profile) is separate from provider discovery. Its legacy response envelope uses Framey's existing filtered, bounded network transport, while its optional Python TLS helper creates a fresh verified context from platform trust defaults. It preserves network permissions and provides no insecure fallback. Offline adapter and TLS-context checks do not establish a real service request, authenticated peer handshake, installed dependency compatibility or headset-provider operation. The additions apply to fresh imports/regenerated wrappers; existing installed-wrapper refresh remains pending and original-method coverage is unchanged.

| Provider | Read-only evidence | Limits |
|---|---|---|
| Pulse-compatible session audio | JSON sink and sink-input list getters completed; the sink sample had volume/mute field shapes. | Empty stream results are valid. Device/stream identity, writes, volume changes and mute changes were not validated. |
| Session D-Bus | Name enumeration succeeded in the existing user session. | Service presence does not establish every method, permission or hardware capability. |
| `org.freedesktop.Notifications.GetCapabilities` | Returned a capability-name array. | Creating a notification was not exercised by this provider inventory. |
| MPRIS player properties | A future `org.freedesktop.DBus.Properties.GetAll` contract was identified. | No current player provider was available; property reads, playback and subscriptions were not exercised. |
| Desktop focus tools | Relevant tools/libraries were discoverable. | The probing session did not establish a usable display environment or query an active window. Desktop focus would still differ from VR scene identity. |

Audio setters, playback actions, notification creation, D-Bus subscriptions, service activation and hardware controls require separate reviewed contracts. Read-only discovery is not evidence that those controls work.

## Backend evidence stays separate from headset UI evidence

Three reviewed original Python backends completed bounded operations through current Framey workers on ARM64. Evidence covered settings or synthetic page persistence across worker replacement and a real short-timer completion payload. Original package files were unchanged and owned workers were unloaded and exited.

Separate combined local Python, JavaScript, browser and configured quality checks passed for the reviewed snapshot. They used inert fixtures and mocks for native boundaries; they do not add headset/provider ownership, native behavior or full-plugin function proof. See the [runtime evidence](decky-runtime.md#evidence-and-what-it-does-not-establish) for those local results.

Those ARM64 results establish only the listed backend operations and transport path. No frontend received the ARM64 timer events in that experiment. They do not establish compatibility badges, library/store page modifications, controller operation, native binary support or a live imported-plugin interface on the headset. The [runtime evidence and limits](decky-runtime.md#evidence-and-what-it-does-not-establish) describes the separate local-shell checks.

A separate **Stage 9 local normal-user proof, 27 October 2026**, accepted eight bounded original metadata/logging/settings/counter entries in ScreenshotUploader 0.0.3 and MagicBlack 2.0.2 through actual `DeckyBackend.start/call/stop`. Its 18 public calls and four separate source-audited lifecycle paths add no ARM64, Worker JSON/Supervisor, frontend, overlay/input or device proof. See the [dated Stage 9 method evidence](decky-runtime.md#stage-9-bounded-method-evidence) for its recipe-argument, filesystem-digest, cleanup and authorization-record qualifications, and [current plugin readiness](decky-status.md) for the latest statuses and computed evidence counts. The earlier ARM64 backend results remain separate evidence.

## Requesting an integration

Record one exact consuming function before adding a provider. The request should connect these steps:

1. **Plugin function:** Identify the original package/version, the consuming call and the user-visible behavior it needs. Preserve original package bytes and attribution.
2. **Needed provider:** Name the Steam browser context, OS service or native interface that can supply that behavior. Distinguish required metadata from UI attachment, input and mutating controls.
3. **Contract and platform:** Classify the operation as a cached getter, live getter, subscription or mutation. Record arguments, return/error shapes, unknown states, availability checks, architecture and interface-version requirements. For persistent resources, specify the actual cleanup contract and owning lifetime.
4. **Framey adapter:** Implement the smallest owned adapter at the existing Framey boundary. Keep private identities internal; do not add React-tree traversal, Decky runtime dependencies or silent privileged/native fallbacks. Report unsupported operations explicitly.
5. **Validation:** Test the consuming behavior, not just method presence. Cover unavailable contexts, empty results, malformed values, errors and cleanup. Keep offline fixtures, actual worker operations, browser event delivery, live Steam UI attachment and physical controller/VR checks as separate evidence.

A request is ready to implement when its provider and cleanup requirements are concrete. It is ready to describe as supported only when the relevant consuming behavior has been verified. New evidence should update the dated contract and its remaining limits rather than widening a capability claim by implication.
