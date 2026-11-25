"""Caller-bound immutable code leases for verified managed Decky ESM imports.

Source files are bounded data. Nothing imports or evaluates original code, and
callers cannot choose paths, origins, listeners, JavaScript or lease lifetimes.
"""

import asyncio
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import weakref

from . import http as fy_code_http
from fy_media import _open_file

ORIGIN = "https://steamloopback.host"
TTL = 600
PROVENANCE_MAX = 512 * 1024
METADATA_MAX = 65536
ORIGINAL_FILES_MAX = 500
GLOBAL_BYTES = 16 * 1024 * 1024
PER_PID_RATE = 10
TOTAL_RATE = 30
RATE_WINDOW = 10
TOMBSTONES_MAX = 64
_PID = re.compile(r"[a-z0-9_-]{1,32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STATES = weakref.WeakKeyDictionary()


class CodeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _failure(code):
    return {"error": "The managed module code request could not be completed", "code": code}


def _same(left, right):
    try:
        return (left == right) is True
    except Exception:
        return False


def _request(arg, token=False):
    allowed = {"pid", "id", "token"} if token else {"pid", "id"}
    if type(arg) is not dict or set(arg) - allowed:
        raise CodeError("invalid_arguments")
    pid = arg.get("pid")
    if type(pid) is not str or not _PID.fullmatch(pid):
        raise CodeError("invalid_arguments")
    if "id" in arg and (type(arg["id"]) is not str or arg["id"] != pid):
        raise CodeError("invalid_arguments")
    value = arg.get("token")
    if token and (type(value) is not str or not _SHA.fullmatch(value)):
        raise CodeError("invalid_arguments")
    return pid, value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("constant")


def _number(text):
    if len(text) > 100:
        raise ValueError("number")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number")
    return value


def _integer(text):
    if len(text) > 20:
        raise ValueError("number")
    return int(text)


def _read_json(path, maximum):
    with _open_file(str(path)) as opened:
        raw = opened.read(maximum + 1)
    if len(raw) > maximum:
        raise CodeError("code_limit")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=_constant,
        parse_float=_number,
        parse_int=_integer,
    )
    if type(value) is not dict:
        raise CodeError("invalid_provenance")
    return value, hashlib.sha256(raw).hexdigest(), raw


def _original_path(value):
    if (
        type(value) is not str
        or not value
        or value.startswith("/")
        or "\\" in value
        or "\0" in value
    ):
        return False
    try:
        if len(value.encode("utf-8")) > 1024:
            return False
    except UnicodeError:
        return False
    return not re.match(r"^[A-Za-z]:", value) and all(
        part not in ("", ".", "..") for part in value.split("/")
    )


def _manifest(host, pid):
    try:
        plugins = Path(host.PLUGINS)
        if not plugins.is_absolute():
            raise ValueError()
        root = plugins / pid
        source = host.manifest(root)
        if type(source) is not dict:
            raise ValueError()
        expected = {key: source.get(key) for key in ("name", "version", "decky_compat")}
        expected = json.loads(json.dumps(expected, allow_nan=False))
        _profile(expected)
        return root, expected
    except CodeError:
        raise
    except Exception:
        raise CodeError("invalid_provenance") from None


def _profile(manifest):
    compat = manifest.get("decky_compat")
    if (
        type(compat) is not dict
        or type(compat.get("profile")) is not int
        or compat["profile"] != 1
        or compat.get("experimental") is not True
        or type(compat.get("sha256")) is not str
        or not _SHA.fullmatch(compat["sha256"])
    ):
        raise CodeError("invalid_provenance")
    if any(
        type(manifest.get(k)) is not str or not 1 <= len(manifest[k]) <= 100
        for k in ("name", "version")
    ):
        raise CodeError("invalid_provenance")


def _metadata(root, expected):
    disk, disk_hash, _ = _read_json(root / "plugin.json", METADATA_MAX)
    _profile(disk)
    if any(disk.get(key) != expected[key] for key in expected):
        raise CodeError("invalid_provenance")
    provenance, provenance_hash, _ = _read_json(root / "decky-provenance.json", PROVENANCE_MAX)
    if (
        any(provenance.get(k) != expected[k] for k in ("name", "version"))
        or provenance.get("sha256") != expected["decky_compat"]["sha256"]
        or "eligible" in provenance
        and provenance["eligible"] is not True
        or "state" in provenance
        and provenance["state"] != "experimental"
    ):
        raise CodeError("invalid_provenance")
    if provenance.get("frontend_format") != "esm":
        raise CodeError("unsupported_format")
    originals = provenance.get("original_files")
    if type(originals) is not dict or not originals:
        raise CodeError("invalid_provenance")
    if len(originals) > ORIGINAL_FILES_MAX:
        raise CodeError("code_limit")
    for path, digest in originals.items():
        if not _original_path(path) or type(digest) is not str or not _SHA.fullmatch(digest):
            raise CodeError("invalid_provenance")
    selected = {
        path: digest
        for path, digest in originals.items()
        if path.startswith("dist/") and path.endswith(".js")
    }
    if "dist/index.js" not in selected or any(
        not fy_code_http.canonical(path) for path in selected
    ):
        raise CodeError("invalid_provenance")
    if len(selected) > fy_code_http.MAX_MODULES:
        raise CodeError("code_limit")
    package, package_hash, raw = _read_json(root / "vendor/decky/package.json", METADATA_MAX)
    if hashlib.sha256(raw).hexdigest() != originals.get("package.json"):
        raise CodeError("invalid_provenance")
    if package.get("type") != "module":
        raise CodeError("unsupported_format")
    if package.get("version") != expected["version"]:
        raise CodeError("invalid_provenance")
    return (disk_hash, provenance_hash, package_hash), selected


@dataclass(frozen=True)
class _Plan:
    root: Path
    expected: dict
    digest: tuple
    files: dict
    sizes: dict
    total: int


def _plan(root, expected):
    try:
        digest, files = _metadata(root, expected)
        sizes = {}
        for path in files:
            with _open_file(str(root / "vendor/decky" / path)) as opened:
                opened.seek(0, 2)
                size = opened.tell()
            if size > fy_code_http.MAX_FILE_BYTES:
                raise CodeError("code_limit")
            sizes[path] = size
        total = sum(sizes.values())
        if total > fy_code_http.MAX_PACKAGE_BYTES:
            raise CodeError("code_limit")
        return _Plan(root, expected, digest, files, sizes, total)
    except CodeError:
        raise
    except Exception:
        raise CodeError("invalid_provenance") from None


def _read_graph(plan, capture):
    """All file descriptors are local to the owned worker and close before return."""
    try:
        if _metadata(plan.root, plan.expected)[0] != plan.digest:
            raise CodeError("code_changed")
        files = {}
        for path, digest in plan.files.items():
            with _open_file(str(plan.root / "vendor/decky" / path)) as opened:
                if capture:
                    value = opened.read(plan.sizes[path] + 1)
                    if (
                        len(value) != plan.sizes[path]
                        or hashlib.sha256(value).hexdigest() != digest
                    ):
                        raise CodeError("code_changed")
                    files[path] = value
                else:
                    hashed = hashlib.sha256()
                    total = 0
                    while True:
                        value = opened.read(min(65536, plan.sizes[path] - total + 1))
                        if not value:
                            break
                        total += len(value)
                        if total > plan.sizes[path]:
                            raise CodeError("code_changed")
                        hashed.update(value)
                    if total != plan.sizes[path] or hashed.hexdigest() != digest:
                        raise CodeError("code_changed")
        if _metadata(plan.root, plan.expected)[0] != plan.digest:
            raise CodeError("code_changed")
        return files
    except CodeError as error:
        if error.code == "code_limit":
            raise
        raise CodeError("code_changed") from None
    except Exception:
        raise CodeError("code_changed") from None


async def _drain(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            break
    if not task.cancelled():
        task.exception()


class _Call:
    def __init__(self, state, pid, identity, operation, token):
        self.state, self.pid, self.identity = state, pid, identity
        self.operation, self.token = operation, token
        self.revoked = self.cancelled = False
        self.locked = False
        self.published_token = None
        self.reserved = 0
        self.io = set()
        self.done = state.loop.create_future()
        self.task = state.loop.create_task(_perform(self))
        self.task.add_done_callback(lambda _: state.finish(self))


class _State:
    def __init__(self, host):
        self.loop = asyncio.get_running_loop()
        self.host = weakref.ref(host, self.orphaned)
        self.server = fy_code_http.ModuleServer(ORIGIN)
        self.leases, self.revoked, self.tombstones = {}, {}, {}
        self.calls, self.retired = set(), set()
        self.by_pid, self.locks, self.pid_rate = {}, {}, {}
        self.total_rate = deque()
        self.reserved = 0
        self.closed = False
        self.closing = None

    def orphaned(self, _):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.begin_shutdown)

    def caller(self, pid):
        host = self.host()
        if host is None or self.closed:
            return None
        try:
            return host.caller_identity(pid)
        except Exception:
            return None

    def tombstone(self, pid, lease):
        self.tombstones[lease["token"]] = pid, lease["identity"], self.loop.time() + TTL
        while len(self.tombstones) > TOMBSTONES_MAX:
            self.tombstones.pop(next(iter(self.tombstones)))

    def retire(self, pid, *, token=None, identity=None):
        lease = self.leases.get(pid)
        if (
            lease is None
            or token is not None
            and lease["token"] != token
            or identity is not None
            and not _same(lease["identity"], identity)
        ):
            return
        self.leases.pop(pid, None)
        if lease is not None:
            self.server.revoke(lease["token"], pid)
            self.tombstone(pid, lease)

    def invalidate(self, pid=None):
        keys = {pid} if pid is not None else set(self.leases) | set(self.by_pid)
        for key in keys:
            lease = self.leases.get(key)
            calls = tuple(self.by_pid.get(key, ()))
            # The root rotates its caller generation before this notification.
            # Retire only identities we actually granted/admitted, never the
            # newly current identity returned by a post-rotation host callback.
            identities = [lease["identity"]] if lease is not None else []
            for call in calls:
                if not any(_same(call.identity, old) for old in identities):
                    identities.append(call.identity)
            if identities:
                self.revoked[key] = tuple(identities)
            self.retire(key)
            for call in calls:
                call.revoked = True
                self.retired.add(call)
                if not call.task.done() and not call.task.cancelling():
                    call.task.cancel()

    def check(self, call):
        identity = self.caller(call.pid)
        if self.closed or call.revoked or call.cancelled or not _same(identity, call.identity):
            if call.published_token is not None:
                self.retire(call.pid, token=call.published_token)
            elif not _same(identity, call.identity):
                self.retire(call.pid, identity=call.identity)
            raise CodeError("stale_caller")

    def admit(self, pid, identity, operation, token):
        if self.closed:
            raise CodeError("service_closed")
        if any(_same(identity, old) for old in self.revoked.get(pid, ())):
            raise CodeError("not_allowed")
        existing = self.by_pid.get(pid, ())
        if len(self.calls) >= 4 or len(existing) >= 2 or existing and operation != "issue":
            raise CodeError("busy")
        now = self.loop.time()
        for key, history in tuple(self.pid_rate.items()):
            while history and history[0] <= now - RATE_WINDOW:
                history.popleft()
            if not history:
                self.pid_rate.pop(key, None)
        while self.total_rate and self.total_rate[0] <= now - RATE_WINDOW:
            self.total_rate.popleft()
        history = self.pid_rate.setdefault(pid, deque())
        if len(history) >= PER_PID_RATE or len(self.total_rate) >= TOTAL_RATE:
            raise CodeError("rate_limited")
        if pid in self.leases and not _same(self.leases[pid]["identity"], identity):
            self.retire(pid)
        call = _Call(self, pid, identity, operation, token)
        self.calls.add(call)
        self.by_pid.setdefault(pid, []).append(call)
        self.locks.setdefault(pid, asyncio.Lock())
        history.append(now)
        self.total_rate.append(now)
        return call

    def reserve(self, call, count):
        if self.server.snapshot()["residentBytes"] + self.reserved + count > GLOBAL_BYTES:
            raise CodeError("code_limit")
        call.reserved = count
        self.reserved += count

    def finish(self, call):
        self.reserved -= call.reserved
        call.reserved = 0
        self.calls.discard(call)
        self.retired.discard(call)
        calls = self.by_pid.get(call.pid, [])
        if call in calls:
            calls.remove(call)
        if not calls:
            self.by_pid.pop(call.pid, None)
            self.locks.pop(call.pid, None)
        if not call.done.done():
            call.done.set_result(None)
        if not call.task.cancelled():
            call.task.exception()

    async def close(self):
        close = self.loop.create_task(self.server.close())
        await asyncio.gather(close, *(call.done for call in tuple(self.calls)))
        self.leases.clear()
        self.tombstones.clear()
        self.revoked.clear()
        self.pid_rate.clear()
        self.total_rate.clear()

    def begin_shutdown(self):
        if self.closing is None:
            self.closed = True
            self.invalidate()
            self.closing = self.loop.create_task(self.close())
        return self.closing


def _state(host):
    state = _STATES.get(host)
    if state is None:
        state = _STATES[host] = _State(host)
    return state


async def _owned(call, coroutine):
    try:
        call.state.check(call)
    except BaseException:
        coroutine.close()
        raise
    task = asyncio.create_task(coroutine)
    call.io.add(task)
    try:
        result = await asyncio.shield(task)
        call.state.check(call)
        return result
    except asyncio.CancelledError:
        await _drain(task)
        raise
    finally:
        call.io.discard(task)


async def _perform(call):
    state, pid = call.state, call.pid
    try:
        async with state.locks[pid]:
            call.locked = True
            state.check(call)
            host = state.host()
            root, expected = _manifest(host, pid)
            del host
            if call.operation == "issue":
                state.retire(pid)
            lease = state.leases.get(pid)
            if call.operation == "touch":
                if lease is None:
                    known = state.tombstones.get(call.token)
                    if (
                        known is not None
                        and known[0] == pid
                        and _same(known[1], call.identity)
                        and known[2] > state.loop.time()
                    ):
                        raise CodeError("code_expired")
                    raise CodeError("not_allowed")
                if not _same(lease["identity"], call.identity):
                    state.retire(pid)
                    raise CodeError("stale_caller")
                if call.token != lease["token"]:
                    raise CodeError("not_allowed")
            plan = await _owned(call, asyncio.to_thread(_plan, root, expected))
            if call.operation == "touch":
                if plan.digest != lease["digest"]:
                    state.retire(pid)
                    raise CodeError("code_changed")
                await _owned(call, asyncio.to_thread(_read_graph, plan, False))
                state.check(call)
                if not state.server.renew(pid, call.token, ttl=TTL):
                    state.retire(pid)
                    raise CodeError("code_expired")
                return {"renewed": True, "ttl": TTL}
            state.reserve(call, plan.total)
            files = await _owned(call, asyncio.to_thread(_read_graph, plan, True))
            await _owned(call, state.server.start())
            state.check(call)
            resource = state.server.issue_bytes(pid, files, plan.files, ttl=TTL)
            call.published_token = resource["token"]
            state.leases[pid] = {
                "identity": call.identity,
                "digest": plan.digest,
                "token": resource["token"],
            }
            state.check(call)
            return {
                "root": resource["root"],
                "entry": resource["root"] + "dist/index.js",
                "token": resource["token"],
                "ttl": TTL,
            }
    except CodeError as error:
        if call.locked and error.code not in ("not_allowed", "busy", "rate_limited"):
            state.retire(pid)
        return _failure(error.code)
    except fy_code_http.ModuleError:
        state.retire(pid)
        return _failure("code_unavailable")
    except asyncio.CancelledError:
        if call.locked:
            state.retire(pid)
        raise
    except Exception:
        if call.locked:
            state.retire(pid)
        return _failure("code_unavailable")


async def _dispatch(host, arg, operation):
    try:
        pid, token = _request(arg, operation != "issue")
        state = _STATES.get(host)
        if state is not None and state.closed:
            raise CodeError("service_closed")
        try:
            identity = host.caller_identity(pid)
        except Exception:
            identity = None
        if identity is None:
            if state is not None:
                state.invalidate(pid)
            raise CodeError("not_allowed")
        state = state or _state(host)
        if (
            operation == "touch"
            and pid in state.leases
            and not _same(state.leases[pid]["identity"], identity)
        ):
            state.retire(pid)
            raise CodeError("stale_caller")
        call = state.admit(pid, identity, operation, token)
    except CodeError as error:
        return _failure(error.code)
    except Exception:
        return _failure("not_allowed")
    try:
        result = await asyncio.shield(call.task)
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            call.cancelled = True
            if not call.task.done() and not call.task.cancelling():
                call.task.cancel()
            await _drain(call.task)
            if call.published_token is not None:
                state.retire(pid, token=call.published_token)
            raise
        await _drain(call.task)
        return _failure("stale_caller")
    finally:
        await _drain(call.task)
    try:
        state.check(call)
    except CodeError as error:
        return _failure(error.code)
    return result


async def decky_code_issue(host, arg=None):
    return await _dispatch(host, arg, "issue")


async def decky_code_touch(host, arg=None):
    return await _dispatch(host, arg, "touch")


async def decky_code_revoke(host, arg=None):
    try:
        pid, token = _request(arg, True)
        state = _STATES.get(host)
        if state is None or state.closed:
            raise CodeError("service_closed" if state is not None else "not_allowed")
        identity = state.caller(pid)
        if identity is None:
            state.invalidate(pid)
            raise CodeError("not_allowed")
        lease = state.leases.get(pid)
        if lease is not None and token == lease["token"]:
            if not _same(identity, lease["identity"]):
                state.retire(pid)
                raise CodeError("stale_caller")
            state.retire(pid)
            return {"revoked": True}
        tombstone = state.tombstones.get(token)
        if (
            tombstone is not None
            and tombstone[0] == pid
            and _same(tombstone[1], identity)
            and tombstone[2] > state.loop.time()
        ):
            return {"revoked": True}
        raise CodeError("not_allowed")
    except CodeError as error:
        return _failure(error.code)
    except Exception:
        return _failure("not_allowed")


def invalidate(host, pid=None):
    state = _STATES.get(host)
    if state is not None:
        state.invalidate(pid)


async def shutdown(host):
    state = _state(host)
    task = state.begin_shutdown()
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


METHODS = {
    "decky_code_issue": decky_code_issue,
    "decky_code_touch": decky_code_touch,
    "decky_code_revoke": decky_code_revoke,
}
