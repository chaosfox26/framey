#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

DESCRIPTION = "Offline validation and reproducible packaging for Framey plugins."


def _load_rules():

    path = Path(__file__).resolve().parents[1] / "fy_pkg.py"
    spec = importlib.util.spec_from_file_location("_framey_plugin_rules", path)
    module = importlib.util.module_from_spec(spec)

    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


pk = _load_rules()
METADATA = "framey-package.json"
STAMP = (1980, 1, 1, 0, 0, 0)
PACKAGE_MAX = min(pk.ARCHIVE_MAX, 5_000_000)
ROOT_FILES = {"plugin.json", "main.js", "backend.py", "README", "README.md", "README.txt"}
ASSET_DIRS = {"assets", "locales", "lib", "vendor", "licenses", "docs"}
SUFFIXES = {
    ".js",
    ".mjs",
    ".cjs",
    ".py",
    ".json",
    ".css",
    ".html",
    ".htm",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".txt",
    ".md",
    ".wasm",
    ".csv",
}
CACHE_DIRS = {"__pycache__", "node_modules", "cache", "caches"}


class ToolError(Exception):
    def __init__(self, message, code="invalid_plugin"):
        super().__init__(message)
        self.code = code


def _legal(name):
    upper = name.upper()
    return any(
        upper == word or upper.startswith(word + "-") or upper.startswith(word + ".")
        for word in ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
    )


def _allowed(parts, is_dir):
    if any(p.startswith(".") or p in CACHE_DIRS for p in parts):
        return False
    if is_dir:
        return parts[0] in ASSET_DIRS
    if len(parts) == 1:
        return parts[0] in ROOT_FILES or _legal(parts[0])
    return parts[0] in ASSET_DIRS and (
        _legal(parts[-1]) or Path(parts[-1]).suffix.lower() in SUFFIXES
    )


def _read(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ToolError("included files must be regular files", "unsafe_type")
    if before.st_size > pk.ENTRY_MAX:
        raise ToolError("included file exceeds the per-file size limit", "too_large")
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(
            opened.st_mode
        ):
            raise ToolError("source changed while reading", "source_changed")
        data = stream.read(pk.ENTRY_MAX + 1)
        after = os.fstat(stream.fileno())
    if len(data) > pk.ENTRY_MAX:
        raise ToolError("included file exceeds the per-file size limit", "too_large")
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    ):
        raise ToolError("source changed while reading", "source_changed")
    return data


def _json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON field")
            out[key] = value
        return out

    def constant(value):
        raise ValueError("non-finite JSON number")

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def _collect(source):
    source = Path(source).absolute()
    if source.is_symlink() or not source.is_dir():
        raise ToolError("source must be an existing directory, not a symlink", "bad_source")
    source = source.resolve()
    files, omitted, budget, layout = {}, [], pk._Budget(), []

    def visit(folder, prefix=()):
        for path in sorted(folder.iterdir(), key=lambda p: p.name):
            parts = prefix + (path.name,)
            mode = path.lstat().st_mode
            is_dir = stat.S_ISDIR(mode)

            allowed = _allowed(parts, is_dir) or (stat.S_ISLNK(mode) and _allowed(parts, True))
            if not allowed:
                omitted.append("/".join(parts) + ("/" if is_dir else ""))
                continue
            pk._parts("/".join(parts))
            if is_dir:
                visit(path, parts)
                continue
            if not stat.S_ISREG(mode):
                raise ToolError("included paths cannot be symlinks or special files", "unsafe_type")
            data = _read(path)
            budget.add(len(data))
            name = "/".join(parts)
            layout.append((list(parts), False, len(data), None))
            files[name] = data

    visit(source)
    pk._layout(layout, 0, None)
    return source, files, omitted


def _decky_layout(source, manifest, files):
    if not isinstance(manifest, dict) or "api_version" not in manifest:
        return []
    if any(name in files for name in ("main.js", "backend.py")):
        return []

    def regular(name):
        path = source
        parts = name.split("/")
        try:
            for part in parts[:-1]:
                path = path / part
                if not stat.S_ISDIR(path.lstat().st_mode):
                    return False
            return stat.S_ISREG((path / parts[-1]).lstat().st_mode)
        except FileNotFoundError:
            return False

    return [name for name in ("dist/index.js", "main.py") if regular(name)]


def inspect_plugin(source, *, framey_version=None, platform=None, api=pk.API_GENERATION):
    if not pk._int(api, 1, 1000):
        raise ToolError("target API must be an integer from 1 to 1000", "bad_target")
    source, files, omitted = _collect(source)
    if "plugin.json" not in files:
        raise ToolError("plugin.json is required")
    try:
        manifest = _json(files["plugin.json"])
    except (UnicodeError, ValueError, RecursionError):
        raise ToolError(
            "plugin.json must be UTF-8 JSON with unique fields and finite numbers"
        ) from None
    decky = _decky_layout(source, manifest, files)
    if decky:
        raise ToolError(
            "Decky format is unsupported: plugin.json declares api_version and "
            + " and ".join(decky)
            + " exist, but Framey main.js/backend.py entry points are missing. "
            + "Port the source to Framey's entry points and API before packaging; this tool does not convert Decky plugins.",
            "unsupported_format",
        )
    normalized = pk.normalize_manifest(manifest)
    if not normalized["ok"]:
        raise ToolError("; ".join(normalized["errors"]))
    if pk.parse_version(manifest.get("version")) is None:
        raise ToolError("version must be a valid Framey version")
    if not any(name in files for name in ("main.js", "backend.py")):
        raise ToolError("main.js or backend.py is required")
    for name in ("main.js", "backend.py"):
        if name not in files:
            continue
        try:
            text = files[name].decode("utf-8")
        except UnicodeError:
            raise ToolError(name + " must be UTF-8") from None
        if not text.strip() or "\x00" in text:
            raise ToolError(name + " must contain nonempty source without NUL characters")
        if name == "backend.py":
            try:
                compile(text, "backend.py", "exec", dont_inherit=True)
            except (SyntaxError, ValueError, RecursionError):
                raise ToolError("backend.py has invalid Python syntax") from None
    if bool(framey_version) != bool(platform):
        raise ToolError("--framey-version and --platform must be supplied together", "bad_target")
    compatibility = None
    if framey_version is not None:
        if pk.parse_version(framey_version) is None or not pk.PLAT.fullmatch(platform):
            raise ToolError("target version or platform is invalid", "bad_target")
        compatibility = pk.decide(manifest, framey_version, platform, api)
        if compatibility["state"] == "incompatible":
            raise ToolError(compatibility["msg"], "incompatible")
    warnings = []
    if "api" not in manifest:
        warnings.append("API generation is not declared")
    if not manifest.get("platform"):
        warnings.append("platform compatibility is not declared")
    report = {
        "id": normalized["value"]["id"],
        "name": normalized["value"]["name"],
        "version": normalized["value"]["version"],
        "framey": normalized["value"]["framey"],
        "api": normalized["value"]["api"],
        "platform": normalized["value"]["platform"],
        "files": sorted(files),
        "omitted": sorted(omitted),
        "warnings": warnings,
        "compatibility": compatibility,
    }
    return source, files, report


def validate(source, **target):
    return inspect_plugin(source, **target)[2]


def _archive(files, report):
    metadata = {
        "format": "framey-plugin-package",
        "format_version": 1,
        "id": report["id"],
        "name": report["name"],
        "version": report["version"],
        "framey": report["framey"],
        "api": report["api"],
        "platform": report["platform"],
        "files": [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())
        ],
    }
    files = dict(files)
    files[METADATA] = (
        json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    budget = pk._Budget()
    payload = io.BytesIO()

    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, data in sorted(files.items()):
            budget.add(len(data))
            info = zipfile.ZipInfo(name, STAMP)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    data = payload.getvalue()
    if len(data) > PACKAGE_MAX:
        raise ToolError("package exceeds the archive size limit", "too_large")
    return data


def pack(source, output, **target):
    source, files, report = inspect_plugin(source, **target)
    output = Path(output).absolute()
    parent = output.parent.resolve(strict=True)
    destination = parent / output.name
    if destination == source or source in destination.parents:
        raise ToolError("output must be outside the source directory", "bad_output")
    if output.suffix.lower() != ".zip":
        raise ToolError("output must have a .zip suffix", "bad_output")
    data = _archive(files, report)

    created = False
    try:
        with open(destination, "xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            destination.unlink(missing_ok=True)
        raise
    report.update(
        {
            "archive_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "package_files": sorted([*files, METADATA]),
        }
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "pack"):
        command = commands.add_parser(
            name,
            help="validate a source tree" if name == "validate" else "write a deterministic ZIP",
        )
        command.add_argument("source", help="plugin source directory")
        if name == "pack":
            command.add_argument(
                "--output",
                required=True,
                help="new .zip path outside the source; never overwritten",
            )
        command.add_argument("--framey-version", help="target Framey version (requires --platform)")
        command.add_argument("--platform", help="target platform, for example linux-aarch64")
        command.add_argument(
            "--api",
            type=int,
            default=pk.API_GENERATION,
            help="target API generation (default: %(default)s)",
        )
    args = parser.parse_args(argv)
    if not 1 <= args.api <= 1000:
        parser.error("--api must be from 1 to 1000")
    try:
        target = {"framey_version": args.framey_version, "platform": args.platform, "api": args.api}
        result = (
            pack(args.source, args.output, **target)
            if args.command == "pack"
            else validate(args.source, **target)
        )
    except (ToolError, pk.PkgError) as error:
        print("framey-plugin: " + error.code + ": " + str(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(
            "framey-plugin: io_error: " + (error.strerror or "file operation failed"),
            file=sys.stderr,
        )
        return 3
    except (ValueError, OverflowError, RuntimeError):
        print("framey-plugin: invalid_plugin: source could not be processed", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
