import importlib.util
import inspect
import json
import os
import re
import sys
import threading
import time
import types
from collections.abc import Mapping
from pathlib import Path

import fy_store

EVENT_NAME = re.compile(r"[a-z0-9_.-]{1,40}")
COMPANION_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
EVENT_MAX = 16384
TOKENS_MAX = 32
API_GENERATION = 1


class FrameyApiError(Exception):
    def __init__(self, code: object, message: object = ""):
        message = str(message)[:300]
        super().__init__(message)
        self.code = str(code)
        self.message = message


_CORE_RUNTIME_MANAGER = None


def _runtime_manager():
    global _CORE_RUNTIME_MANAGER
    if _CORE_RUNTIME_MANAGER is None:
        name = "_framey_core_decky_runtime_worker"
        path = Path(__file__).with_name("fy_decky_runtime.py")
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise FrameyApiError("runtime_unavailable", "Core runtime validator is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        _CORE_RUNTIME_MANAGER = module
    return _CORE_RUNTIME_MANAGER


def validate_decky_runtime_descriptor(descriptor):
    try:
        return _runtime_manager().validate_worker_descriptor(descriptor)
    except Exception as error:
        raise FrameyApiError(getattr(error, "code", "runtime_unavailable"), str(error)) from error


def _forget_runtime(namespace):
    for name in tuple(sys.modules):
        if name == namespace or name.startswith(namespace + "."):
            sys.modules.pop(name, None)


class _CompatBackend:
    def __init__(self, descriptor, adapter, namespace, vendor, plugin_dir):
        self._descriptor = descriptor
        self._adapter = adapter
        self._namespace = namespace
        self._vendor = vendor
        self._plugin_dir = plugin_dir
        self._closed = False

    def _check(self):
        if self._closed:
            raise FrameyApiError("runtime_retired", "Compatibility backend has stopped")
        _vendor_root(self._vendor, self._plugin_dir)
        validate_decky_runtime_descriptor(self._descriptor)

    async def _run(self, method, *args):
        self._check()
        result = getattr(self._adapter, method)(*args)
        if inspect.isawaitable(result):
            result = await result
        self._check()
        return result

    async def start(self):
        return await self._run("start")

    async def call(self, method, arg):
        return await self._run("call", method, arg)

    async def stop(self):
        if self._closed:
            return None
        try:
            result = self._adapter.stop()
            return await result if inspect.isawaitable(result) else result
        finally:
            self._closed = True
            _forget_runtime(self._namespace)


def _vendor_root(original_root, plugin_dir):
    if plugin_dir is None:
        raise FrameyApiError("runtime_vendor", "Plugin directory is unavailable")
    expected = Path(plugin_dir).absolute() / "vendor" / "decky"
    try:
        vendor = Path(original_root)
        if (
            not vendor.is_absolute()
            or vendor != expected
            or any(p.is_symlink() for p in (vendor, *vendor.parents))
            or not vendor.is_dir()
        ):
            raise ValueError("Original backend must stay in the managed vendor directory")
    except (TypeError, ValueError, OSError) as error:
        raise FrameyApiError("runtime_vendor", str(error)) from error
    return vendor


def _compat_backend(descriptor, original_root, plugin_dir, api, profile, rpc_result_version):
    if (
        type(profile) is not int
        or profile != 1
        or type(rpc_result_version) is not int
        or rpc_result_version != 1
    ):
        raise FrameyApiError("runtime_abi", "Unsupported compatibility backend ABI")
    vendor = _vendor_root(original_root, plugin_dir)
    validate_decky_runtime_descriptor(descriptor)
    root = Path(descriptor["root"])
    namespace = "_framey_worker_decky_" + os.urandom(16).hex()
    if (vendor / (namespace + ".py")).exists() or (vendor / namespace).exists():
        raise FrameyApiError("runtime_collision", "Reserved runtime module name")
    try:
        spec = importlib.util.spec_from_file_location(
            namespace, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        if spec is None or spec.loader is None:
            raise FrameyApiError("runtime_unavailable", "Optional runtime package is unavailable")
        package = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = package
        spec.loader.exec_module(package)
        spec = importlib.util.spec_from_file_location(namespace + ".runtime", root / "runtime.py")
        if spec is None or spec.loader is None:
            raise FrameyApiError("runtime_unavailable", "Optional backend entry is unavailable")
        entry = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = entry
        spec.loader.exec_module(entry)
        validate_decky_runtime_descriptor(descriptor)
        if (
            type(getattr(entry, "ABI", None)) is not int
            or entry.ABI != 1
            or not callable(getattr(entry, "backend_adapter", None))
        ):
            raise FrameyApiError("runtime_abi", "Optional runtime has no compatible backend entry")
        adapter = entry.backend_adapter(
            vendor, api, profile=profile, rpc_result_version=rpc_result_version
        )
        if not all(callable(getattr(adapter, name, None)) for name in ("start", "call", "stop")):
            raise FrameyApiError("runtime_abi", "Invalid optional backend adapter")
        return _CompatBackend(descriptor, adapter, namespace, vendor, plugin_dir)
    except BaseException:
        _forget_runtime(namespace)
        raise


class _LazyDirs:
    __slots__ = ("_paths",)

    def __init__(self, paths):
        self._paths = paths

    config = property(lambda self: fy_store.ensure(self._paths.config))
    data = property(lambda self: fy_store.ensure(self._paths.data))
    runtime = property(lambda self: fy_store.ensure(self._paths.runtime))
    logs = property(lambda self: fy_store.ensure(self._paths.logs))


def build(pid, meta, dirs, runtime, rpc, max_events=50):
    meta = meta if isinstance(meta, dict) else {}
    runtime_metadata = runtime if isinstance(runtime, dict) else {}
    raw_paths = (
        dirs[k] if isinstance(dirs, dict) else getattr(dirs, k) for k in fy_store.Dirs._fields
    )
    paths = fy_store.Dirs(*(Path(p) if p is not None else None for p in raw_paths))
    plugin_dir = runtime_metadata.get("plugin_dir") or meta.get("plugin_dir")
    api_generation = (
        runtime_metadata["api"] if type(runtime_metadata.get("api")) is int else API_GENERATION
    )

    runtime_info = types.SimpleNamespace(
        framey=str(runtime_metadata.get("framey", "")),
        api=api_generation,
        platform=str(runtime_metadata.get("platform", "")),
        python=str(runtime_metadata.get("python") or "%d.%d.%d" % sys.version_info[:3]),
        plugin=pid,
        version=str(meta.get("version") or ""),
        plugin_dir=Path(plugin_dir) if plugin_dir else None,
    )
    settings = fy_store.Settings(paths.config / "settings.json")
    log = fy_store.Log(pid, paths.logs, sys.stderr)

    event_window = [0.0, 0]
    window_lock = threading.Lock()

    def event_dropped():
        try:
            hook = getattr(rpc, "event_dropped", None)
            if callable(hook):
                hook(1)
        except Exception:
            pass
        return False

    def emit(name, payload=None):
        if not isinstance(name, str) or not EVENT_NAME.fullmatch(name):
            return event_dropped()
        try:
            size = len(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            return event_dropped()
        if size > EVENT_MAX:
            return event_dropped()
        now = time.monotonic()
        with window_lock:
            if not 0 <= now - event_window[0] < 1.0:
                event_window[0], event_window[1] = now, 0
            accepted = event_window[1] < max_events
            if accepted:
                event_window[1] += 1
        if not accepted:
            return event_dropped()
        return bool(rpc.emit(name, payload))

    async def host(op, arg):
        try:
            r = await rpc.host(op, arg)
        except FrameyApiError:
            raise
        except Exception as e:
            raise FrameyApiError(
                getattr(e, "code", None) or "host_error", getattr(e, "message", None) or e
            ) from None
        if isinstance(r, dict) and r.get("error"):
            e = r["error"]
            if isinstance(e, dict):
                raise FrameyApiError(e.get("code") or "host_error", e.get("message") or "")
            raise FrameyApiError(r.get("code") or "host_error", e)
        return r

    async def fetch(
        url, *, method="GET", headers=None, body=None, timeout=15, max_bytes=524288, dest=None
    ):
        if not isinstance(url, str):
            raise FrameyApiError("bad_argument", "url must be a string")
        for name, value, kind in (
            ("method", method, str),
            ("headers", headers, Mapping),
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
                arg[name] = dict(value) if name == "headers" else value
        r = await host("fetch", arg)
        if not isinstance(r, dict):
            raise FrameyApiError("bad_result", "unexpected fetch reply")
        return r

    resource_tokens = {}

    async def resource_url(root, path, ttl=600):
        r = await host("resource", {"root": root, "path": path, "ttl": ttl})
        url = r.get("url") if isinstance(r, dict) else None
        if not isinstance(url, str):
            raise FrameyApiError("bad_result", "unexpected resource reply")
        if isinstance(r.get("token"), str):
            resource_tokens[url] = r["token"]
            while len(resource_tokens) > TOKENS_MAX:
                del resource_tokens[next(iter(resource_tokens))]
        return url

    async def resource_revoke(url):
        token = resource_tokens.get(url)
        if token is None:
            return False
        r = await host("resource", {"revoke": token})
        revoked = (
            r.get("revoked", r.get("ok", True)) is not False if isinstance(r, dict) else bool(r)
        )
        if revoked and resource_tokens.get(url) == token:
            resource_tokens.pop(url, None)
        return revoked

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
            if not pattern.fullmatch(e.name):
                continue
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
                key = (e.stat(follow_symlinks=False).st_mtime_ns, e.name)
            except OSError:
                continue
            if best is None or key > best[0]:
                best = (key, Path(e.path))
        if best is None:
            raise FrameyApiError("not_installed", "companion %s is not installed" % name)
        exe = best[1] / name
        return exe if exe.is_file() and not exe.is_symlink() else best[1]

    descriptor = runtime_metadata.get("decky_runtime")
    descriptor = dict(descriptor) if type(descriptor) is dict else None
    compat_adapter = None

    def backend(*, original_root, profile=1, rpc_result_version=1):
        nonlocal compat_adapter
        if compat_adapter is None:
            compat_adapter = _compat_backend(
                descriptor, original_root, plugin_dir, api_module, profile, rpc_result_version
            )
        else:
            if (
                type(profile) is not int
                or profile != 1
                or type(rpc_result_version) is not int
                or rpc_result_version != 1
                or _vendor_root(original_root, plugin_dir) != compat_adapter._vendor
            ):
                raise FrameyApiError("runtime_abi", "Compatibility backend arguments changed")
            compat_adapter._check()
        return compat_adapter

    api_module = types.ModuleType("framey_api")
    api_module.__dict__.update(
        PLUGIN=pid,
        API=api_generation,
        runtime=runtime_info,
        dirs=_LazyDirs(paths),
        settings=settings,
        log=log,
        events=types.SimpleNamespace(emit=emit),
        fetch=fetch,
        resource_url=resource_url,
        resource_revoke=resource_revoke,
        list_dir=list_dir,
        migrate=types.SimpleNamespace(
            move=fy_store.migrate_move,
            keys=keys,
            copy_once=copy_once,
            snapshot=lambda: fy_store.snapshot_settings(paths.config),
        ),
        companion=companion,
        compat=types.SimpleNamespace(backend=backend),
        FrameyApiError=FrameyApiError,
    )
    return api_module
