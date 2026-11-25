#!/usr/bin/env python3
"""Build Framey's optional Decky ZIP and metadata offline; never import payload code."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile


PAYLOAD_FILES = (
    "__init__.py",
    "runtime.py",
    "catalog.py",
    "importer.py",
    "code.py",
    "http.py",
    "compat/framey-view.js",
    "compat/decky-assets.js",
    "compat/decky-navigation.js",
    "compat/decky-menus.js",
    "compat/decky-sidebar.js",
    "compat/decky-modules.js",
    "compat/decky-host.js",
    "compat/decky_backend.py",
    "compat/decky_patches.py",
    "client.js",
    "frontend/store.js",
)
VERSION_FILE = "runtime-version.json"
PLATFORMS = frozenset(("linux-aarch64", "linux-x86_64"))
FILE_MAX = 5_000_000
EXPANDED_MAX = 64_000_000
ARCHIVE_MAX = 20_000_000
METADATA_MAX = 16_384
MANIFEST_MAX = 131_072
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
REPOSITORY = "https://github.com/chaosfox26/framey"


def _json(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _read_regular(path, maximum):
    """Bound reads and reject links/special files before any potentially blocking IO."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        original = path.lstat()
        if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
            raise ValueError("Runtime input must be a single-link regular file")
        if original.st_size > maximum:
            raise ValueError("Runtime input exceeds its size limit")
        fd = os.open(path, flags)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (original.st_dev, original.st_ino)
            or before.st_size > maximum
        ):
            raise ValueError("Runtime input changed or is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        if len(raw) > maximum or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Runtime input changed during the bounded read")
        return raw
    except OSError as error:
        raise ValueError("A required runtime input is missing or unreadable") from error
    finally:
        if fd is not None:
            os.close(fd)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate runtime metadata field")
        result[key] = value
    return result


def _metadata(source):
    try:
        value = json.loads(
            _read_regular(source / VERSION_FILE, METADATA_MAX), object_pairs_hook=_pairs
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Runtime version metadata must be bounded UTF-8 JSON") from error
    keys = {"schema", "version", "manager_api", "core_api", "compat_profile", "platform"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Runtime version metadata fields do not match schema1")
    for field in ("schema", "manager_api", "core_api", "compat_profile"):
        if type(value[field]) is not int or value[field] != 1:
            raise ValueError("Unsupported runtime schema, API or compatibility profile")
    version = value["version"]
    if not isinstance(version, str) or len(version) > 40 or not VERSION.fullmatch(version):
        raise ValueError("A bounded runtime version is required")
    platforms = value["platform"]
    if (
        not isinstance(platforms, list)
        or not platforms
        or any(not isinstance(item, str) or item not in PLATFORMS for item in platforms)
        or len(set(platforms)) != len(platforms)
    ):
        raise ValueError("Runtime platforms must be a unique supported nonempty list")
    return {**value, "platform": sorted(platforms)}


def _source_closure(source):
    allowed = frozenset(PAYLOAD_FILES) | {VERSION_FILE}
    seen = set()

    def visit(directory):
        for child in directory.iterdir():
            info = child.lstat()
            relative = child.relative_to(source).as_posix()
            if stat.S_ISDIR(info.st_mode):
                # Imported source tests may produce bytecode; never distribute it.
                if child.name == "__pycache__":
                    continue
                if not any(name.startswith(relative + "/") for name in allowed):
                    raise ValueError("An unlisted runtime source directory is present")
                visit(child)
            elif stat.S_ISREG(info.st_mode):
                if relative not in allowed:
                    raise ValueError("An unlisted runtime source file is present")
                seen.add(relative)
            else:
                raise ValueError("Runtime source contains a link or special file")

    try:
        visit(source)
    except OSError as error:
        raise ValueError("Runtime source could not be inspected") from error
    if seen != allowed:
        raise ValueError("The runtime source closure is incomplete")


def _zip(files):
    if sum(map(len, files.values())) > EXPANDED_MAX:
        raise ValueError("Expanded runtime exceeds64MB")
    stream = io.BytesIO()
    # Stored bytes avoid compressor-version differences and remain below the
    # bounded archive limit. No source bytes or line endings are transformed.
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, raw in sorted(files.items()):
            entry = zipfile.ZipInfo("decky_runtime/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, raw)
    raw = stream.getvalue()
    if len(raw) > ARCHIVE_MAX:
        raise ValueError("Runtime ZIP exceeds20MB")
    return raw


def build_runtime(source, output, *, tag):
    """Build a complete local release; reject bad inputs before writing outputs."""
    source, output = Path(source).absolute(), Path(output).absolute()
    if source.resolve() != source or not source.is_dir():
        raise ValueError("Runtime source must be a canonical regular directory")
    if output.resolve() == source or source in output.resolve().parents:
        raise ValueError("Release output must be outside runtime source")
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or ".." in tag:
        raise ValueError("A safe explicit first-party release tag is required")
    _source_closure(source)
    metadata = _metadata(source)
    files = {name: _read_regular(source / name, FILE_MAX) for name in PAYLOAD_FILES}
    files["LICENSE"] = _read_regular(source.parent / "LICENSE", FILE_MAX)
    if sum(map(len, files.values())) > EXPANDED_MAX:
        raise ValueError("Expanded runtime exceeds64MB")
    manifest_raw = _json(
        {**metadata, "files": {name: _sha(raw) for name, raw in sorted(files.items())}}
    )
    if len(manifest_raw) > MANIFEST_MAX:
        raise ValueError("Runtime manifest is too large")
    # A concurrent author edit must not silently produce a mixed revision.
    _source_closure(source)
    if _metadata(source) != metadata:
        raise ValueError("Runtime version metadata changed during the build")
    for name, raw in files.items():
        path = source.parent / "LICENSE" if name == "LICENSE" else source / name
        if _read_regular(path, FILE_MAX) != raw:
            raise ValueError("Runtime source changed during the build")
    archive_raw = _zip({**files, "manifest.json": manifest_raw})
    archive_name = "decky-runtime-" + metadata["version"] + ".zip"
    release = {
        "schema": 1,
        "version": metadata["version"],
        "archive": {
            "url": REPOSITORY + "/releases/download/" + tag + "/" + archive_name,
            "sha256": _sha(archive_raw),
            "size": len(archive_raw),
        },
        "manifest_sha256": _sha(manifest_raw),
    }
    artifacts = {
        archive_name: archive_raw,
        "manifest.json": manifest_raw,
        "decky-runtime.json": _json(release),
    }
    if any(os.path.lexists(output / name) for name in artifacts):
        raise ValueError("Refusing to overwrite existing local release outputs")
    output.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for name, raw in artifacts.items():
            target = output / name
            with target.open("xb") as stream:
                written.append(target)
                stream.write(raw)
    except BaseException:
        for target in written:
            target.unlink(missing_ok=True)
        raise
    return release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[1] / "decky_runtime"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Private or ignored local output directory"
    )
    parser.add_argument(
        "--tag", required=True, help="Explicit first-party release tag; does not publish"
    )
    arguments = parser.parse_args(argv)
    try:
        release = build_runtime(arguments.source, arguments.output, tag=arguments.tag)
    except (ValueError, OSError) as error:
        parser.exit(1, "Runtime build failed: " + str(error) + "\n")
    print(_json(release).decode(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
