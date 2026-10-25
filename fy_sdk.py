import json
import os
import re
import sys
import threading
import time
import types
from pathlib import Path

import fy_store

EVENT_NAME = re.compile(r"[a-z0-9_.-]{1,40}")
COMPANION_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
EVENT_MAX = 16384
TOKENS_MAX = 32
API_GENERATION = 1


class FrameyApiError(Exception):
    def __init__(self, code, message=""):
        message = str(message)[:300]
        super().__init__(message)
        self.code = str(code)
        self.message = message


class _LazyDirs:
    __slots__ = ("_d",)

    def __init__(self, d):
        self._d = d

    config = property(lambda s: fy_store.ensure(s._d.config))
    data = property(lambda s: fy_store.ensure(s._d.data))
    runtime = property(lambda s: fy_store.ensure(s._d.runtime))
    logs = property(lambda s: fy_store.ensure(s._d.logs))


def build(pid, meta, dirs, runtime, rpc, max_events=50):
    meta = meta if isinstance(meta, dict) else {}
    rt = runtime if isinstance(runtime, dict) else {}
    paths = fy_store.Dirs(*(Path(dirs[k] if isinstance(dirs, dict) else getattr(dirs, k)) for k in fy_store.Dirs._fields))
    plugin_dir = rt.get("plugin_dir") or meta.get("plugin_dir")
    api = rt["api"] if type(rt.get("api")) is int else API_GENERATION

    info = types.SimpleNamespace(
        framey=str(rt.get("framey", "")),
        api=api,
        platform=str(rt.get("platform", "")),
        python=str(rt.get("python") or "%d.%d.%d" % sys.version_info[:3]),
        plugin=pid,
        version=str(meta.get("version") or ""),
        plugin_dir=Path(plugin_dir) if plugin_dir else None,
    )
    settings = fy_store.Settings(paths.config / "settings.json")
    log = fy_store.Log(pid, paths.logs, sys.stderr)

    window = [0.0, 0]
    window_lock = threading.Lock()

    def emit(name, payload=None):
        if not isinstance(name, str) or not EVENT_NAME.fullmatch(name):
            return False
        try:
            size = len(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            return False
        if size > EVENT_MAX:
            return False
        now = time.monotonic()
        with window_lock:
            if not 0 <= now - window[0] < 1.0:
                window[0], window[1] = now, 0
            if window[1] >= max_events:
                return False
            window[1] += 1
        return bool(rpc.emit(name, payload))

    async def host(op, arg):
        try:
            r = await rpc.host(op, arg)
        except FrameyApiError:
            raise
        except Exception as e:
            raise FrameyApiError(getattr(e, "code", None) or "host_error", getattr(e, "message", None) or e) from None
        if isinstance(r, dict) and r.get("error"):
            e = r["error"]
            if isinstance(e, dict):
                raise FrameyApiError(e.get("code") or "host_error", e.get("message") or "")
            raise FrameyApiError(r.get("code") or "host_error", e)
        return r

    async def fetch(url, *, method="GET", headers=None, body=None, timeout=15, max_bytes=524288, dest=None):
        if not isinstance(url, str):
            raise FrameyApiError("bad_argument", "url must be a string")
        for name, value, kind in (
            ("method", method, str),
            ("headers", headers, dict),
            ("body", body, str),
            ("dest", dest, str),
            ("timeout", timeout, (int, float)),
            ("max_bytes", max_bytes, int),
        ):
            if value is not None and not isinstance(value, kind):
                raise FrameyApiError("bad_argument", "bad type for " + name)
        arg = {"url": url, "method": method, "timeout": timeout, "max_bytes": max_bytes}
        for name, value in (("headers", headers), ("body", body), ("dest", dest)):
            if value is not None:
                arg[name] = value
        r = await host("fetch", arg)
        if not isinstance(r, dict):
            raise FrameyApiError("bad_result", "unexpected fetch reply")
        return r

    tokens = {}

    async def resource_url(root, path, ttl=600):
        r = await host("resource", {"root": root, "path": path, "ttl": ttl})
        url = r.get("url") if isinstance(r, dict) else None
        if not isinstance(url, str):
            raise FrameyApiError("bad_result", "unexpected resource reply")
        if isinstance(r.get("token"), str):
            tokens[url] = r["token"]
            while len(tokens) > TOKENS_MAX:
                del tokens[next(iter(tokens))]
        return url

    async def resource_revoke(url):
        token = tokens.get(url)
        if token is None:
            return False
        r = await host("resource", {"revoke": token})
        tokens.pop(url, None)
        if isinstance(r, dict):
            return r.get("revoked", r.get("ok", True)) is not False
        return bool(r)

    async def list_dir(root, path="", *, hidden=None, dirs_only=None, exts=None):
        arg = {"root": root, "path": path}
        for name, value in (("hidden", hidden), ("dirs_only", dirs_only), ("exts", exts)):
            if value is not None:
                arg[name] = list(value) if name == "exts" else value
        r = await host("files", arg)
        if not isinstance(r, dict):
            raise FrameyApiError("bad_result", "unexpected files reply")
        return r

    def keys(mapping):
        out = {}

        def fn(d):
            out.update(fy_store.migrate_keys(d, mapping))
            if not out["renamed"]:
                return False

        settings._edit(fn)
        return out

    def copy_once(src, dst, marker):
        return fy_store.copy_once(src, dst, marker)

    def companion(name):
        if not isinstance(name, str) or not COMPANION_NAME.fullmatch(name):
            raise FrameyApiError("bad_argument", "bad companion name")
        pattern = re.compile(re.escape(name) + r"-[0-9][0-9A-Za-z.+_-]*")
        best = None
        try:
            entries = list(os.scandir(paths.data / "bin"))
        except OSError:
            entries = []
        for e in entries:
            if pattern.fullmatch(e.name) and e.is_dir(follow_symlinks=False):
                key = (e.stat(follow_symlinks=False).st_mtime_ns, e.name)
                if best is None or key > best[0]:
                    best = (key, Path(e.path))
        if best is None:
            raise FrameyApiError("not_installed", "companion %s is not installed" % name)
        exe = best[1] / name
        return exe if exe.is_file() and not exe.is_symlink() else best[1]

    mod = types.ModuleType("framey_api")
    mod.PLUGIN = pid
    mod.API = api
    mod.runtime = info
    mod.dirs = _LazyDirs(paths)
    mod.settings = settings
    mod.log = log
    mod.events = types.SimpleNamespace(emit=emit)
    mod.fetch = fetch
    mod.resource_url = resource_url
    mod.resource_revoke = resource_revoke
    mod.list_dir = list_dir
    mod.migrate = types.SimpleNamespace(
        move=fy_store.migrate_move,
        keys=keys,
        copy_once=copy_once,
        snapshot=lambda: fy_store.snapshot_settings(paths.config),
    )
    mod.companion = companion
    mod.FrameyApiError = FrameyApiError
    return mod
