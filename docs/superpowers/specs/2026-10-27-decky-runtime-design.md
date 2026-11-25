# Framey-owned Decky compatibility runtime

The user authorized an independently implemented runtime compatibility layer, analogous in purpose to application API translation. Import original Decky store packages without installing Decky Loader or depending on React, ReactDOM, Preact, or Decky's UI library. Keep Framey's existing native DOM shell, accordion and separate right-hand window.

## Decisions and evidence

Three exact stable-store packages are downloaded privately and SHA-256 verified: ProtonDB Badges 1.3.4, HLTB for Deck 2.0.12, Notebook 0.1.5. Their distributed JavaScript must remain unchanged. Modern bundles use ESM/default factories, SP_REACT/SP_REACTDOM/SP_JSX/DFL and the public Decky API connection contract; Notebook is an IIFE with legacy serverAPI. Backends use main.py/Plugin, decky_plugin imports, lifecycle hooks, and positional or keyword RPC. No native executable was detected in these three packages. This is evidence about these packages only.

Alternatives considered: installing Decky violates the requested independent loader; shipping React for plugin panels violates the requested implementation constraint; per-plugin rewritten frontends do not provide the requested original-bundle compatibility. Implement a documented, tested subset of the frontend/runtime contracts with Framey's own DOM renderer and worker bridge. Never represent a missing Steam patch API as implemented or silently discard it.

## Initial deliverable

- Independently authored element/state/effect/DOM compatibility runtime, with no React implementation dependency.
- Native Framey controls implementing selected Decky component contracts, modern/legacy backend translation, lifecycle cleanup and explicit unsupported capability reporting.
- Original archives remain immutable; an experimental importer creates an adapted Framey package around their original files, preserves licenses/notices and records original hash/version. Do not execute a package merely to inspect it.
- Backend execution is per-plugin, as the current Framey worker model requires; ordinary trusted plugin code, not a security sandbox. No root plugins or native payloads in the first supported profile.
- Importer and runtime are explicitly experimental and opt-in. Existing store browsing remains functional. A plugin may have a verified panel/backend and still lack badges or other Steam page integration; show those as distinct facts.
- Prove real distributed bundles can evaluate and render/interact where supported, and prove real backend settings persistence. Use original ProtonDB first, then Notebook and HLTB as compatibility probes. Record limitations rather than fabricating host facilities.
- No SteamVR restart, diagnostics changes, commits, push, publication or unrelated upscaling modifications. Preserve the dirty UI work and all source attribution.

## Acceptance and limits

Automated tests exercise renderer state/effect/unmount and keyed identity, native control events, modern/legacy RPC, import boundaries/hash/path checks, unsupported APIs and teardown. Real-archive integration is kept outside committed third-party fixtures and records hashes and results. Support claims name the features tested. General React compatibility, arbitrary plugins, Steam React-tree patching and native/hardware compatibility are not implied by this first profile.
