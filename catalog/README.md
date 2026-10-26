# Framey catalog

`index.json` is the bundled schema 1 discovery catalog. It lists the actual Hello
and Starter examples from Framey commit
`4247757951b8f2aa2f539ada93e416054a2137fc`. Their versions, compatibility,
platforms, and Starter API generation come from the manifests at that commit.
The public source links identify those folders; the manifests' template source
URLs are not download locations. The `stable` channel identifies the example
snapshot, not production readiness or headset verification. `updated` records
the catalog entry date, not an upstream release date.

The catalog in this experimental branch still points at that earlier example
snapshot; it does not automatically track edits to `examples/` or a branch head.
Loader source version 1.0.2 and catalog channel names do not identify a released
installer or certify those examples for physical VR use. See the root README's
[current verification](../README.md#current-verification) for dated checks and
remaining device limits.

Each plugin source names a repository, immutable commit, and optional package
subdirectory. Metadata contains no guessed release assets or download URLs.
Catalog metadata is not package signing: a pinned commit identifies source, but
does not authenticate a release, verify a package signature, or grant permission
to run code. Package validation, compatibility checks, and installation policy
still apply. Framey plugins run trusted code as the current user.

Fan Control is a `setup` entry and is always ineligible for catalog installation.
It requires [Framey App](https://github.com/chaosfox26/framey-app) signed privileged
setup for its root-owned cooling components. The listing intentionally supplies
no version, source commit, package path, or installable artifact. Follow the
[Fan Control project instructions](https://github.com/chaosfox26/frame-fan) for
setup and its signed release process.

The examples remain covered by Framey's [GPL-2.0-only license](../LICENSE).
Keep existing license notices and the [project acknowledgments](../README.md#inspiration-and-acknowledgments)
when redistributing source. Catalog inclusion does not change those terms or
claim affiliation or endorsement.

## Decky Store browsing

The separate Decky Store reads the official
[stable catalog](https://plugins.deckbrew.xyz/plugins) and, with Developer mode
enabled, the [testing catalog](https://testing.deckbrew.xyz/plugins). Search,
categories, details, and version history use read-only catalog requests and a
local cache. Saved results are marked when stale or a refresh fails. Browsing
does not load remote images, execute or install plugins, or POST download or
update statistics. This does not change the native Framey `index.json` catalog.

An optional compatibility assessment requires explicitly selecting and
confirming a version. It binds the selected listing and version to their
identities, downloads that version's ZIP within inspection limits, and requires
its SHA-256 to match the catalog. That checksum is not a publisher signature.
The static inspection reads selected manifests, dependency names, file headers,
and limited frontend/backend text for hardware assumptions. It does not execute
code, extract an installed plugin, check every entry's complete contents or CRC,
or test VR behavior.

Every Decky entry still needs a Framey port and remains ineligible for installation.
ARM64 support is unknown before inspection. An ARM64 binary header is evidence
only; it does not establish operating-system, dependency, hardware, or Framey
compatibility. Detected Intel/AMD code, administrator flags, additional binary
downloads, and Steam Deck hardware paths can require further porting work.

## Publishing a native example or port

Start with the [developer quickstart](../docs/developer-quickstart.md) and
[offline validation and packaging tools](../docs/plugin-tools.md). Give your module
its own identity and real update repository, preserve its license and credits,
and verify the exact archive you intend to publish. Editing this discovery file
alone does not create a release or make an original Decky package installable.
The [development guide](../docs/development.md) describes contributor tools and
source standards; those tools are not installed with the headset runtime.
