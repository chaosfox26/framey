# Offline plugin tools

`tools/framey_plugin.py` validates a plugin folder and builds reproducible release ZIPs using Python's standard library. Run it from this checkout; it needs the adjacent `fy_pkg.py`. It does not fetch dependencies, contact services, execute plugin code, install a plugin, or change runtime settings. It implements Framey's own format; no Decky source or runtime is needed.

```sh
python3 tools/framey_plugin.py --help
python3 tools/framey_plugin.py validate examples/starter
python3 tools/framey_plugin.py validate examples/starter --framey-version 1.0.2 --platform linux-aarch64
python3 tools/framey_plugin.py pack examples/starter --output /tmp/starter-release.zip
```

Choose a new output filename. `pack` requires an explicit `.zip` path outside the source folder. Its parent directory must already exist. Existing files, directories and links are refused, including dangling links. No overwrite option exists. Failed writes are cleaned up. Validation writes no output files. Both commands print a JSON report to standard output; errors go to standard error. Exit codes are `0` for success, `1` for invalid sources or incompatible targets, `2` for command usage errors, and `3` for filesystem failures.

Validation requires a UTF-8 `plugin.json` object with a valid, non-reserved id and a parseable version. Duplicate JSON fields and non-finite numbers are rejected. Manifest fields, backend declarations, companion declarations and compatibility syntax reuse `fy_pkg.normalize_manifest`. A source folder's name need not match the id: ZIP installation uses the manifest id. At least one nonempty UTF-8 `main.js` or `backend.py` must exist. Python syntax is parsed without execution. JavaScript is checked for encoding, empty content and NUL characters; use your normal JavaScript tooling for syntax and behavior tests.

An unsupported Decky layout is refused with an `unsupported_format` reason when `plugin.json` declares `api_version`, a regular `dist/index.js` or `main.py` exists, and Framey's `main.js` and `backend.py` are both missing. Detection checks names and types without reading omitted code or following links. An unknown `api_version` field alone does not identify a Framey plugin as Decky. Port the source to Framey's entry points and API before using this tool: the validator is not a Decky converter or runtime bridge. The explicit [source-helper example](../examples/decky-style) is already a Framey plugin and validates normally.

API and platform declarations are recommended; missing declarations produce report warnings for older plugins. Supplying both `--framey-version` and `--platform` checks target compatibility using `fy_pkg.decide`, including architecture aliases. `--api` changes the target API generation (default `1`); it only affects this optional target check. Without a target, valid future compatibility declarations can be packaged on any development machine.

Compatibility checks compare manifest declarations; they do not inspect native machine code, prove ARM binary compatibility, or test device hardware. A declared ARM platform is not evidence that a helper executable can run there. Native companions need their own architecture checks and runtime testing.

The package uses a deliberately narrow allowlist:

- Root `plugin.json`, `main.js`, `backend.py`, `README`, `README.md` and `README.txt`.
- Root or included nested license and notice files named `LICENSE`, `LICENCE`, `COPYING`, `NOTICE` or `AUTHORS`, optionally followed by `.` or `-` and a suffix.
- Runtime/static files inside `assets`, `locales`, `lib`, `vendor`, `licenses` and `docs`, with suffixes `.js`, `.mjs`, `.cjs`, `.py`, `.json`, `.css`, `.html`, `.htm`, `.svg`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.ico`, `.woff`, `.woff2`, `.ttf`, `.otf`, `.txt`, `.md`, `.wasm` or `.csv`.

Hidden paths, `__pycache__`, `node_modules`, `cache` and `caches` are always omitted. Other root files and folders are omitted, including local settings, data, repository metadata and installer identity files. `omitted` lists skipped paths; omitted directories are listed once without reading their contents. Keep private information out of allowed source/assets too: an allowlist cannot inspect whether public asset contents are suitable for release. Move required runtime resources into an allowed directory; review the report before publishing. Unsupported binary executables should use Framey's declared companion mechanism. License and notice files belonging to vendored content must live in included directories.

Included symlinks and special files are refused. Paths and collisions follow the real installer rules: no traversal, backslashes, unsafe characters, case/Unicode collisions or paths deeper than eight components. File count (500), per-file size (5,000,000 bytes) and unpacked size (20,000,000 bytes) include the generated metadata. The final ZIP is capped at 5,000,000 bytes to fit the plugin installer. Empty directories are omitted.

ZIP entries have sorted paths, a fixed 1980 timestamp and regular-file mode `0644`, independent of source timestamps and permissions. Entries are stored without compression for byte stability across zlib versions. The original manifest and source bytes are retained. A generated `framey-package.json` contains format version `1`, plugin id/name/version, normalized Framey/API/platform declarations and a sorted inventory with each source file's byte size and SHA-256. This filename is reserved; a source copy is omitted and regenerated. The inventory excludes itself. The JSON command report includes the final archive size and SHA-256 for release/catalog tooling. This hash proves byte identity, not publisher authenticity.

The ZIP has `plugin.json` at its root and is accepted by `fy_pkg.safe_extract_zip` with its default automatic layout handling. The tool provides release preparation; use the [plugin author guide](plugins.md) for runtime behavior and installation instructions.

```sh
python3 -m unittest discover -s tests -p 'test_fy_plugin_tools.py'
```
