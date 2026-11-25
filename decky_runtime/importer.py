#!/usr/bin/env python3
"""Offline, opt-in importer for Framey's experimental Decky compatibility profile."""

import argparse
import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import struct
import tempfile
import zipfile
import zlib

import fy_pkg
from fy_catalog import CatalogError
from .catalog import ARCHIVE_MAX, inspect_archive

ROOT = Path(__file__).resolve().parent


class ImportError(ValueError):
    def __init__(self, message, code="decky_import"):
        super().__init__(message)
        self.code = code


def _contents(data, digest):
    try:
        assessment = inspect_archive(data, digest)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            items = [
                (fy_pkg._parts(info.filename), info.is_dir(), info.file_size, info)
                for info in archive.infolist()
            ]
            files = {
                "/".join(parts): archive.read(info)
                for parts, directory, _, info in fy_pkg._layout(items, "auto", None)
                if not directory
            }
        manifest = fy_pkg._loads(files["plugin.json"])
        package = fy_pkg._loads(files.get("package.json", b"{}"))
    except (CatalogError, fy_pkg.PkgError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise ImportError(
            "The selected Decky package could not be verified", "bad_package"
        ) from error
    if not isinstance(manifest, dict) or not isinstance(package, dict):
        raise ImportError("Package metadata must be objects", "bad_metadata")
    return assessment, files, manifest, package


def inspect_package(data, expected_hash):
    """Read complete verified archive entries without importing or evaluating code."""
    assessment, files, manifest, package = _contents(data, expected_hash)
    reasons = []
    name, version = manifest.get("name"), package.get("version")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        reasons.append("A bounded plugin name is required")
    if fy_pkg.parse_version(version) is None:
        reasons.append("A valid package version is required")
    api = manifest.get("api_version", 0)
    if type(api) is not int or api not in (0, 1, 2):
        reasons.append("This backend API version is not supported by the initial profile")
    if assessment["flags"]:
        reasons.append("Privilege flags are not supported by the initial profile")
    if assessment["native_binary_count"]:
        reasons.append("Native binaries need a separately verified compatibility profile")
    if package.get("remote_binary"):
        reasons.append("Remote binary downloads are not supported by the initial profile")
    if not assessment["has_frontend"]:
        reasons.append("An original dist/index.js frontend is required")
    frontend_format = "esm" if package.get("type") == "module" else "iife"
    module_graph = build_module_graph_profile(files, frontend_format)
    if module_graph["state"] == "unsupported":
        reasons.append(
            "The native module graph is unsupported: " + ", ".join(module_graph["reasons"])
        )
    return {
        "name": name,
        "version": version,
        "sha256": expected_hash,
        "frontend_format": frontend_format,
        "module_graph": module_graph,
        "backend_api": api,
        "has_backend": assessment["has_backend"],
        "eligible": not reasons,
        "state": "experimental" if not reasons else "unsupported",
        "reasons": reasons,
        "runtime_verified": False,
        "native_binary_count": assessment["native_binary_count"],
        "architecture": assessment["architecture"],
        "unverified": ["Plugin panel behavior", "Steam page integration", "Headset interaction"],
        "not_executed": True,
    }


def build_module_graph_profile(files, frontend_format):
    """Describe the bounded native ESM subset without parsing or changing source bytes."""
    limits = {"files": 64, "file_bytes": 2 * 1024 * 1024, "total_bytes": 8 * 1024 * 1024}
    candidates = {
        path: value
        for path, value in files.items()
        if path.startswith("dist/") and re.search(r"\.[cm]?js(?:[?#].*)?$", path, re.IGNORECASE)
    }
    required = frontend_format == "esm" and len(candidates) > 1
    profile = {
        "profile": 1,
        "required": required,
        "state": "not_required",
        "entry": "dist/index.js",
        "files": {},
        "file_count": 0,
        "total_bytes": 0,
        "limits": limits,
        "reasons": [],
        "unsupported_paths": [],
    }
    if not required:
        return profile
    path_pattern = re.compile(
        r"dist/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.js\Z"
    )
    for path, value in sorted(candidates.items()):
        if (
            len(path) > 512
            or not path_pattern.fullmatch(path)
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            profile["unsupported_paths"].append(path)
            continue
        profile["files"][path] = hashlib.sha256(value).hexdigest()
        profile["total_bytes"] += len(value)
        if len(value) > limits["file_bytes"] and "module_bytes_limit" not in profile["reasons"]:
            profile["reasons"].append("module_bytes_limit")
    profile["file_count"] = len(profile["files"])
    if profile["unsupported_paths"]:
        profile["reasons"].append("unsupported_module_path")
    if profile["entry"] not in profile["files"]:
        profile["reasons"].append("missing_entry")
    if len(candidates) > limits["files"]:
        profile["reasons"].append("module_count_limit")
    if profile["total_bytes"] > limits["total_bytes"]:
        profile["reasons"].append("total_bytes_limit")
    profile["state"] = "unsupported" if profile["reasons"] else "experimental"
    return profile


def build_png_asset_map(files, package_name=None):
    """Map a bounded PNG subset from verified original package entries, without decoding images."""
    limits = {
        "images": 64,
        "image_bytes": 262144,
        "total_bytes": 524288,
        "max_dimension": 4096,
        "max_pixels": 4000000,
    }
    assets = {}
    profile = {
        "profile": 1,
        "media_type": "image/png",
        "verification": "png_signature_ihdr",
        "limits": limits,
        "included": [],
        "omitted": [],
        "total_bytes": 0,
    }
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    binding_supported = package_name is None or (
        isinstance(package_name, str)
        and 0 < len(package_name) <= 100
        and package_name not in (".", "..")
        and not any(character in package_name for character in "\\/%?#")
        and not any(ord(character) < 32 or ord(character) == 127 for character in package_name)
    )
    for path, value in sorted(files.items()):
        in_assets = path.startswith(("assets/", "dist/assets/"))
        if not in_assets and not path.lower().endswith(".png"):
            continue
        parts = path.split("/")
        reason = ""
        if (
            any(part in ("", ".", "..") for part in parts)
            or any(character in path for character in "\\%?#:")
            or not all(character.isprintable() for character in path)
        ):
            reason = "invalid_path"
        elif not in_assets:
            reason = "unsupported_path"
        elif not path.lower().endswith(".png"):
            reason = "unsupported_format"
        elif not isinstance(value, bytes):
            reason = "invalid_png_header"
        elif len(value) > limits["image_bytes"]:
            reason = "image_bytes_limit"
        elif (
            len(value) < 33
            or value[:8] != b"\x89PNG\r\n\x1a\n"
            or value[8:16] != b"\x00\x00\x00\rIHDR"
            or value[29:33] != struct.pack(">I", zlib.crc32(value[12:29]))
        ):
            reason = "invalid_png_header"
        else:
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", value[16:29]
            )
            if (
                depth not in depths.get(color, ())
                or compression != 0
                or filtering != 0
                or interlace not in (0, 1)
            ):
                reason = "invalid_png_header"
            elif (
                not 1 <= width <= limits["max_dimension"]
                or not 1 <= height <= limits["max_dimension"]
                or width * height > limits["max_pixels"]
            ):
                reason = "dimensions_limit"
            elif not binding_supported:
                reason = "unsupported_package_name"
            elif len(assets) >= limits["images"]:
                reason = "image_count_limit"
            elif profile["total_bytes"] + len(value) > limits["total_bytes"]:
                reason = "total_bytes_limit"
            else:
                assets[path] = "data:image/png;base64," + base64.b64encode(value).decode("ascii")
                profile["total_bytes"] += len(value)
                profile["included"].append(
                    {"path": path, "bytes": len(value), "width": width, "height": height}
                )
        if reason:
            profile["omitted"].append({"path": path, "reason": reason})
    return assets, profile


def _main_source(files, report, assets):
    try:
        source = files["dist/index.js"].decode("utf-8")
    except UnicodeError as error:
        raise ImportError("The original frontend must be UTF-8", "bad_frontend") from error
    descriptor = {
        "v": 1,
        "name": report["name"],
        "apiVersion": 2,
        "source": source,
        "format": report["frontend_format"],
        "moduleGraph": report["module_graph"],
        "assets": assets,
    }
    if report["has_backend"]:
        descriptor["rpcResultVersion"] = 1
    encoded = json.dumps(descriptor, ensure_ascii=True)
    return """// Framey optional runtime wrapper ABI 1, generation 2.
let mounted;
let renderEpoch = 0;
const frameyApi = Framey.register({
    async render(container) {
        const epoch = ++renderEpoch;
        mounted?.dispose();
        mounted = undefined;
        const handle = await frameyApi.compat.mount(container, __DESCRIPTOR__);
        if (epoch !== renderEpoch) handle?.dispose();
        else mounted = handle;
    }
});
frameyApi.cleanup(() => { ++renderEpoch; mounted?.dispose(); mounted = undefined; });
""".replace("__DESCRIPTOR__", encoded)


def _backend_source():
    return """# Framey optional runtime wrapper ABI 1, generation 2.
from pathlib import Path
import framey_api as fy

_framey_bridge = fy.compat.backend(
    original_root=Path(__file__).parent / "vendor" / "decky",
    profile=1, rpc_result_version=1,
)

async def startup(ctx):
    await _framey_bridge.start()

async def call(method, arg):
    return await _framey_bridge.call(method, arg)

async def unload(reason):
    await _framey_bridge.stop()
"""


def import_plugin(data, expected_hash, destination, pid):
    """Wrap a verified original archive as an explicit experimental Framey module."""
    if (
        not isinstance(pid, str)
        or not fy_pkg.ID.fullmatch(pid)
        or pid in fy_pkg.RESERVED_PLUGIN_IDS
    ):
        raise ImportError("Choose a valid, non-reserved Framey plugin id", "bad_id")
    dest = Path(destination).absolute()
    if any(path.is_symlink() for path in (dest, *dest.parents)):
        raise ImportError("The destination cannot contain symbolic links", "linked_output")
    if os.path.lexists(dest) or not dest.parent.is_dir():
        raise ImportError(
            "The destination must be new and its parent must exist", "existing_output"
        )
    report = inspect_package(data, expected_hash)
    if not report["eligible"]:
        raise ImportError("; ".join(report["reasons"]), "unsupported_profile")
    _, files, _, _ = _contents(data, expected_hash)
    assets, asset_profile = build_png_asset_map(files, report["name"])
    # Build wrappers before allocating the destination, so unsupported source leaves no output.
    main = _main_source(files, report, assets)
    backend = _backend_source() if report["has_backend"] else None
    provenance = {
        **report,
        "asset_profile": asset_profile,
        "original_files": {
            name: hashlib.sha256(value).hexdigest() for name, value in files.items()
        },
        "implementation": "Framey independently authored compatibility profile 1",
        "react_dependency": False,
        "decky_loader_installed": False,
    }
    provenance["runtime"] = {"abi": 1, "wrapper_version": 2, "profile": 1}
    if report["has_backend"]:
        provenance["framey_features"] = {"rpc_envelope": "framey-rpc-value-v1"}
    manifest = {
        "id": pid,
        "name": report["name"],
        "short": report["name"][:24],
        "version": report["version"],
        "api": 1,
        "platform": ["linux-aarch64", "linux-x86_64"],
        "decky_compat": {
            "profile": 1,
            "sha256": expected_hash,
            "experimental": True,
            "runtime_abi": 1,
            "wrapper_version": 2,
        },
        "backend": {
            "memory_mb": 64,
            "startup_timeout": 10,
            "timeout": 15,
            "keepalive": report["has_backend"],
        },
    }
    with tempfile.TemporaryDirectory(prefix=".framey-decky-stage-", dir=dest.parent) as temporary:
        stage = Path(temporary)
        original = stage / "vendor" / "decky"
        original.mkdir(parents=True)
        for name, value in files.items():
            output = original.joinpath(*name.split("/"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(value)
        (stage / "main.js").write_text(main, encoding="utf-8")
        if backend is not None:
            (stage / "backend.py").write_text(backend, encoding="utf-8")
        for name, value in (("plugin.json", manifest), ("decky-provenance.json", provenance)):
            (stage / name).write_text(
                json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
            )
        # Reserve rather than replacing any preexisting destination, including empty directories.
        try:
            dest.mkdir()
        except FileExistsError as error:
            raise ImportError("The destination already exists", "existing_output") from error
        try:
            for child in stage.iterdir():
                shutil.move(str(child), dest / child.name)
        except BaseException:
            shutil.rmtree(dest)
            raise
    return {**report, "id": pid, "destination": str(dest)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "import"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.archive.open("rb") as stream:
            data = stream.read(ARCHIVE_MAX + 1)
        if args.command == "inspect":
            result = inspect_package(data, args.sha256)
        elif not args.id or args.output is None:
            parser.error("import requires --id and --output")
        else:
            result = import_plugin(data, args.sha256, args.output, args.id)
    except (ImportError, OSError, UnicodeError) as error:
        print(
            json.dumps(
                {"ok": False, "code": getattr(error, "code", "import_failed"), "error": str(error)}
            )
        )
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
