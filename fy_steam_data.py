"""Host-owned cached Steam metadata reads.

Only fixed getter expressions are accepted. The host owns caller authorization,
existing-page leases, CDP sessions, and transport cleanup.
"""

import asyncio
import json
import math

MAX_TARGETS = 64
MAX_APPS = 4096
MAX_BYTES = 2 * 1024 * 1024
MAX_APP_ID = 2**32 - 1
FOCUS_FIELDS = ("focusedAppId", "focusedWindowId", "focusedOverlayAppId", "focusedOverlayPid")
CATEGORY_FIELDS = (
    "steamDeckCategory",
    "steamOsCategory",
    "steamMachineCategory",
    "steamFrameCategory",
)

PROBE = """(() => { // framey-steam-probe-v1
  try {
    const a = window.appStore, s = window.FocusedAppWindowStore;
    return {apps: !!(a && typeof a === 'object' && !Array.isArray(a) &&
      window.SteamClient && window.SteamClient.Apps && typeof window.SteamClient.Apps === 'object' &&
      Array.isArray(a.allApps) && typeof a.GetAppOverviewByAppID === 'function'),
      focus: !!(s && typeof s === 'object' && !Array.isArray(s) &&
        ['GetFocusedAppID','GetFocusedWindowID','GetFocusedOverlayAppID','GetFocusedOverlayPID']
        .every(k => typeof s[k] === 'function'))};
  } catch { return {providerError:'context_unavailable'}; }
})()"""

FOCUS = """(() => {
  try {
    const s = window.FocusedAppWindowStore;
    if (!s || typeof s !== 'object' || Array.isArray(s)) return {providerError:'capability_unavailable'};
    const keys = ['GetFocusedAppID','GetFocusedWindowID','GetFocusedOverlayAppID','GetFocusedOverlayPID'];
    if (!keys.every(k => typeof s[k] === 'function')) return {providerError:'capability_unavailable'};
    const v = keys.map(k => s[k]());
    if (!v.every(x => x == null || (typeof x === 'string' && x.length <= 256) ||
      (typeof x === 'number' && Number.isFinite(x)))) return {providerError:'invalid_provider_result'};
    const empty = x => x == null || x === 0 || x === '0' || x === '';
    return {status:v.every(empty) ? 'empty':'present', focusedAppId:v[0] ?? null,
      focusedWindowId:v[1] ?? null, focusedOverlayAppId:v[2] ?? null, focusedOverlayPid:v[3] ?? null};
  } catch { return {providerError:'provider_read_failed'}; }
})()"""

# This projection references only the fields whose cached getter bodies were mapped.
_PROJECT = """const project = a => {
    if (!a || typeof a !== 'object' || !Number.isInteger(a.appid) || a.appid <= 0 ||
        a.appid > 4294967295) throw new Error('shape');
    const category = v => {
      if (v === undefined || v === null) return null;
      if (!Number.isInteger(v) || v < 0 || v > 2147483647) throw new Error('shape');
      return v;
    };
    return {appId:a.appid, steamDeckCategory:category(a.steam_deck_compat_category),
      steamOsCategory:category(a.steam_os_compat_category),
      steamMachineCategory:category(a.steam_machine_compat_category),
      steamFrameCategory:category(a.steam_frame_compat_category)};
  };
  const partial = a => Object.values(a).some(v => v === null);"""

VISIBLE_APPS = """(() => {
  try {
    const store = window.appStore;
    const list = store && store.allApps;
    if (!Array.isArray(list)) return {providerError:'capability_unavailable'};
    PROJECT
    const apps = list.slice(0,4096).map(project);
    return {status:list.length === 0 ? 'empty' : apps.some(partial) ? 'partial':'present',
      apps, visibleCount:list.length, truncated:list.length > 4096};
  } catch { return {providerError:'provider_read_failed'}; }
})()""".replace("PROJECT", _PROJECT)

_LOOKUP = """(() => {
  try {
    const store = window.appStore;
    if (!store || typeof store.GetAppOverviewByAppID !== 'function') return {providerError:'capability_unavailable'};
    const a = store.GetAppOverviewByAppID(APP_ID);
    if (a === null) return {status:'not_found',app:null};
    PROJECT
    const app = project(a);
    return {status:partial(app) ? 'partial':'present',app};
  } catch { return {providerError:'provider_read_failed'}; }
})()""".replace("PROJECT", _PROJECT)


class ProviderFailure(Exception):
    def __init__(self, code):
        self.code = code


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _app(value):
    if type(value) is not dict or set(value) != {"appId", *CATEGORY_FIELDS}:
        return False
    return _integer(value["appId"], 1, MAX_APP_ID) and all(
        value[k] is None or _integer(value[k], 0, 2**31 - 1) for k in CATEGORY_FIELDS
    )


def _partial(value):
    return any(value[k] is None for k in CATEGORY_FIELDS)


def _empty(value):
    return value is None or value == 0 or value == "0" or value == ""


def _validate(operation, data, app_id):
    if type(data) is not dict:
        return False
    if operation == "focus":
        if set(data) != {"status", *FOCUS_FIELDS}:
            return False
        values = [data[k] for k in FOCUS_FIELDS]
        if not all(
            v is None
            or (type(v) is str and len(v) <= 256)
            or (type(v) in (int, float) and math.isfinite(v))
            for v in values
        ):
            return False
        return data["status"] == ("empty" if all(_empty(v) for v in values) else "present")
    if operation == "app":
        if set(data) != {"status", "app"}:
            return False
        app = data["app"]
        if app is None:
            return data["status"] == "not_found"
        return (
            _app(app)
            and app["appId"] == app_id
            and data["status"] == ("partial" if _partial(app) else "present")
        )
    if set(data) != {"status", "apps", "visibleCount", "truncated"}:
        return False
    apps, count, truncated = data["apps"], data["visibleCount"], data["truncated"]
    if (
        type(apps) is not list
        or not _integer(count, 0, 2**31 - 1)
        or type(truncated) is not bool
        or len(apps) != min(count, MAX_APPS)
        or truncated != (count > MAX_APPS)
        or not all(_app(a) for a in apps)
    ):
        return False
    if len({a["appId"] for a in apps}) != len(apps):
        return False
    return data["status"] == (
        "empty" if count == 0 else "partial" if any(_partial(a) for a in apps) else "present"
    )


class SteamReadProvider:
    """Transport implements async list_targets/evaluate and sync is_current.

    Target refs are opaque host leases carrying session/context epochs. This class
    never opens or owns target sessions and never exposes its transport to plugins.
    """

    def __init__(self, transport, *, timeout=2.0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError("timeout")
        self._transport = transport
        self._timeout = timeout
        self._epoch = 0
        self._disposed = False
        self._pending = set()
        self._cancel_requested = set()

    def _cancel_once(self, task):
        if not task.done() and task not in self._cancel_requested:
            self._cancel_requested.add(task)
            if not task.cancelling():
                task.cancel()

    async def _drain(self, task):
        # Shield only the owned child's cleanup, never the caller's entire read.
        # Further caller cancellation remains visible in cancelling() and is
        # propagated after cleanup. It must not inject another cancel into the
        # child's asynchronous transport finally block.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                break
        if not task.cancelled():
            task.exception()

    def invalidate(self):
        self._epoch += 1
        # Only cancel child tasks created by read(), never its caller's task or
        # shared transport session. Awaiters drain these children before return.
        for task in tuple(self._pending):
            self._cancel_once(task)

    def dispose(self):
        if not self._disposed:
            self._disposed = True
            self.invalidate()

    def _check(self, epoch, ref=None):
        if self._disposed:
            raise ProviderFailure("provider_disposed")
        if epoch != self._epoch or (ref is not None and not self._transport.is_current(ref)):
            raise ProviderFailure("stale_context")

    async def _evaluate(self, ref, expression, epoch):
        self._check(epoch, ref)
        try:
            result = await self._transport.evaluate(ref, expression)
        except Exception:
            # A transport failure may be the first indication of a revoked
            # lease. Preserve lifecycle reasons via host state, never raw text.
            self._check(epoch, ref)
            raise
        self._check(epoch, ref)
        if type(result) is not dict or "exceptionDetails" in result:
            raise ProviderFailure("provider_read_failed")
        remote = result.get("result")
        if type(remote) is not dict or "objectId" in remote or "value" not in remote:
            raise ProviderFailure("invalid_provider_result")
        value = remote["value"]
        try:
            if len(json.dumps(value, allow_nan=False).encode("utf-8")) > MAX_BYTES:
                raise ProviderFailure("result_limit")
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise ProviderFailure("invalid_provider_result") from None
        if type(value) is dict and set(value) == {"providerError"}:
            code = value["providerError"]
            if code in (
                "context_unavailable",
                "capability_unavailable",
                "invalid_provider_result",
                "provider_read_failed",
            ):
                raise ProviderFailure(code)
            raise ProviderFailure("invalid_provider_result")
        return value

    async def _perform(self, operation, app_id, epoch):
        self._check(epoch)
        refs = await self._transport.list_targets()
        self._check(epoch)
        if type(refs) not in (list, tuple):
            raise ProviderFailure("context_unavailable")
        if len(refs) > MAX_TARGETS:
            raise ProviderFailure("inventory_limit")
        if not refs:
            raise ProviderFailure("target_unavailable")
        selected, failures = [], 0
        capability = "focus" if operation == "focus" else "apps"
        for ref in refs:
            try:
                caps = await self._evaluate(ref, PROBE, epoch)
                if (
                    type(caps) is not dict
                    or set(caps) != {"apps", "focus"}
                    or not all(type(v) is bool for v in caps.values())
                ):
                    raise ProviderFailure("invalid_provider_result")
                if caps[capability]:
                    selected.append(ref)
            except ProviderFailure as error:
                if error.code in ("stale_context", "provider_disposed"):
                    raise
                failures += 1
            except Exception:
                failures += 1
        if not selected:
            raise ProviderFailure("context_unavailable" if failures else "capability_unavailable")
        if failures:
            raise ProviderFailure("context_unavailable")
        if len(selected) > 1:
            raise ProviderFailure("ambiguous_context")
        expression = (
            FOCUS
            if operation == "focus"
            else VISIBLE_APPS
            if operation == "visibleApps"
            else _LOOKUP.replace("APP_ID", str(app_id))
        )
        data = await self._evaluate(selected[0], expression, epoch)
        if not _validate(operation, data, app_id):
            raise ProviderFailure("invalid_provider_result")
        self._check(epoch, selected[0])
        return {
            "ok": True,
            "available": True,
            "source": "steam-focus-cache" if operation == "focus" else "steam-appstore-cache",
            "data": data,
        }

    async def read(self, operation, args=None):
        task = None
        epoch = self._epoch
        try:
            if type(operation) is not str or operation not in ("visibleApps", "app", "focus"):
                raise ProviderFailure("unknown_operation")
            args = {} if args is None else args
            if type(args) is not dict or set(args) != ({"appId"} if operation == "app" else set()):
                raise ProviderFailure("invalid_arguments")
            app_id = args.get("appId")
            if operation == "app" and not _integer(app_id, 1, MAX_APP_ID):
                raise ProviderFailure("invalid_arguments")
            self._check(epoch)
            task = asyncio.create_task(self._perform(operation, app_id, epoch))
            self._pending.add(task)
            result = await asyncio.wait_for(asyncio.shield(task), self._timeout)
            self._check(epoch)
            return result
        except asyncio.CancelledError:
            # External caller cancellation retains its normal semantics even if
            # it races with invalidation. Lifecycle cancellation affects only our
            # owned child and returns a specific sanitized failure to its caller.
            if asyncio.current_task().cancelling():
                raise
            if self._disposed:
                code = "provider_disposed"
            elif epoch != self._epoch:
                code = "stale_context"
            else:
                raise
        except asyncio.TimeoutError:
            code = "provider_timeout"
        except ProviderFailure as error:
            code = error.code
        except Exception:
            code = "provider_read_failed"
        finally:
            if task is not None:
                self._cancel_once(task)
                try:
                    await self._drain(task)
                finally:
                    self._pending.discard(task)
                    self._cancel_requested.discard(task)
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
        # Lifecycle changes may arrive while a timed-out read is draining.
        if task is not None:
            if self._disposed:
                code = "provider_disposed"
            elif epoch != self._epoch:
                code = "stale_context"
        return {"ok": False, "available": False, "code": code}
