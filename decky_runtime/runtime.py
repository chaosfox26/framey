"""Fixed ABI entry for a verified, generation-owned optional runtime package."""

import asyncio
from contextvars import ContextVar
from pathlib import Path
import weakref

ABI = 1
_SERVICES = weakref.WeakKeyDictionary()
_JOBS = weakref.WeakKeyDictionary()
_CLOSING = weakref.WeakSet()
_LEASE = ContextVar("framey_optional_runtime_catalog_lease", default=None)
_CODE = None


def frontend_bundle():
    """Return the fixed verified factory expression, without loading vendor code."""
    root = Path(__file__).parent
    paths = (
        "compat/framey-view.js",
        "compat/decky-assets.js",
        "compat/decky-navigation.js",
        "compat/decky-menus.js",
        "compat/decky-sidebar.js",
        "compat/decky-modules.js",
        "compat/decky-host.js",
        "frontend/store.js",
        "client.js",
    )
    source = "\n".join((root / name).read_text(encoding="utf-8") for name in paths)
    return "(function(){\n" + source + "\nreturn createFrameyDeckyProvider();\n})"


def _current(host):
    import fy_decky_runtime
    from fy_catalog import CatalogError

    lease = _LEASE.get()
    if not fy_decky_runtime.current(host, lease) or host in _CLOSING:
        raise CatalogError("runtime_unavailable", "Decky compatibility is disabled or unavailable")


async def catalog_dispatch(
    host, operation, arg, lease, *, fetcher=None, archive_fetcher=None, install_lock=None
):
    """Dispatch only a core-admitted catalog generation, with no caller factories."""
    token = _LEASE.set(lease)
    task = asyncio.current_task()
    jobs = None
    try:
        _current(host)
        jobs = _JOBS.setdefault(host, set())
        jobs.add(task)
        from .catalog import CatalogService, SOURCES

        if operation == "sources":
            return [dict(source) for source in SOURCES.values()]
        if operation not in ("list", "details", "assess", "import"):
            raise ValueError("Unknown compatibility catalog operation")
        service = _SERVICES.get(host)
        if service is None:
            service = _SERVICES[host] = CatalogService(
                host,
                fetcher=fetcher,
                archive_fetcher=archive_fetcher,
                install_lock=install_lock,
                current=lambda: _current(host),
            )
        method = "import_decky" if operation == "import" else operation
        value = await getattr(service, method)(arg)
        _current(host)
        return value
    finally:
        if jobs is not None:
            jobs.discard(task)
        _LEASE.reset(token)


class _BackendAdapter:
    def __init__(self, vendor_root, framey_api):
        from .compat import decky_backend, decky_patches

        self._bridge = decky_backend.DeckyBackend(
            vendor_root,
            framey_api.dirs.config,
            framey_api.dirs.data,
            framey_api.dirs.logs,
            logger=framey_api.log,
            emit=framey_api.events.emit,
            patch_registry=decky_patches,
        )

    async def start(self):
        return await self._bridge.start()

    async def call(self, method, envelope):
        if (
            type(envelope) is not dict
            or set(envelope) != {"args", "legacy"}
            or type(envelope["legacy"]) is not bool
        ):
            raise ValueError("A compatibility call envelope is required")
        return await self._bridge.call(
            method,
            envelope["args"],
            legacy=envelope["legacy"],
            result_envelope_version=1,
        )

    async def stop(self):
        await self._bridge.stop()


def backend_adapter(original_root, fy, *, profile=1, rpc_result_version=1):
    """Core supplies an admitted original root; construction imports no vendor code."""
    if type(profile) is not int or profile != 1:
        raise ValueError("Unsupported compatibility profile")
    if type(rpc_result_version) is not int or rpc_result_version != 1:
        raise ValueError("Unsupported compatibility result version")
    return _BackendAdapter(Path(original_root), fy)


async def code_dispatch(host, operation, arg=None):
    global _CODE
    from . import code

    _CODE = code
    methods = {
        "issue": code.decky_code_issue,
        "touch": code.decky_code_touch,
        "revoke": code.decky_code_revoke,
    }
    if operation not in methods:
        raise ValueError("Unknown compatibility code operation")
    return await methods[operation](host, arg)


def invalidate(host, pid=None):
    if _CODE is not None:
        _CODE.invalidate(host, pid)


async def shutdown(host):
    _CLOSING.add(host)
    jobs = tuple(task for task in _JOBS.pop(host, ()) if task is not asyncio.current_task())
    for task in jobs:
        task.cancel()
    service = _SERVICES.pop(host, None)
    if service is not None:
        tasks = list(service.refreshes.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)
    if _CODE is not None and host in _CODE._STATES:
        await _CODE.shutdown(host)
