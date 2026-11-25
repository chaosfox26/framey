"""Caller-scoped admission and lifecycle for passive cached Steam reads.

Only the host authenticates callers and selects permitted targets. The data and
transport layers own fixed expressions, main-world leases and bounded projection;
no plugin-supplied target, endpoint or JavaScript enters those layers here.
"""

import asyncio
from collections import deque
import re
import weakref

from fy_steam_data import SteamReadProvider
from fy_steam_transport import SteamCDPTransport, fixed_expression_allowed

_PID = re.compile(r"[a-z0-9_-]{1,32}\Z")
_STATES = weakref.WeakKeyDictionary()


def _failure(code):
    return {"ok": False, "available": False, "code": code}


def _request(arg):
    if type(arg) is not dict or set(arg) - {"pid", "id", "operation", "args"}:
        return "invalid_arguments"
    pid, operation = arg.get("pid"), arg.get("operation")
    if type(pid) is not str or not _PID.fullmatch(pid):
        return "invalid_arguments"
    if "id" in arg and (type(arg["id"]) is not str or arg["id"] != pid):
        return "invalid_arguments"
    if type(operation) is not str:
        return "invalid_arguments"
    if operation not in ("visibleApps", "app", "focus"):
        return "unknown_operation"
    args = arg.get("args", {})
    if type(args) is not dict:
        return "invalid_arguments"
    if operation == "app":
        if (
            set(args) != {"appId"}
            or type(args["appId"]) is not int
            or not 1 <= args["appId"] < 2**32
        ):
            return "invalid_arguments"
    elif args:
        return "invalid_arguments"
    # Own the narrow payload across await boundaries rather than retaining a
    # mutable transport envelope supplied by an upstream caller.
    return pid, operation, dict(args)


def _same(left, right):
    try:
        return (left == right) is True
    except Exception:
        return False


async def _drain(task):
    """Drain an owned task without forwarding further caller cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            break
    if not task.cancelled():
        task.exception()


class _CallerTransport:
    def __init__(self, entry):
        self.entry = entry

    def _check(self):
        if not self.entry.current():
            raise RuntimeError("stale_caller")

    async def list_targets(self):
        self._check()
        try:
            refs = await self.entry.state.transport.list_targets()
        except Exception:
            self._check()
            raise
        self._check()
        return refs

    def is_current(self, lease):
        if not self.entry.current():
            return False
        current = self.entry.state.transport.is_current(lease)
        return current is True and self.entry.current()

    async def evaluate(self, lease, expression):
        self._check()
        try:
            result = await self.entry.state.transport.evaluate(lease, expression)
        except Exception:
            self._check()
            raise
        self._check()
        return result


class _Entry:
    def __init__(self, state, pid, identity):
        self.state, self.pid, self.identity = state, pid, identity
        self.revoked = False
        self.provider = SteamReadProvider(_CallerTransport(self))

    def current(self):
        if self.revoked:
            return False
        identity = self.state.caller(self.pid)
        if identity is None or not _same(identity, self.identity):
            self.revoked = True
            return False
        return True

    def dispose(self):
        self.revoked = True
        self.provider.dispose()


class _Call:
    def __init__(self, entry, operation, args):
        loop = asyncio.get_running_loop()
        self.entry = entry
        self.done = loop.create_future()
        self.task = loop.create_task(entry.provider.read(operation, args))


class _State:
    def __init__(self, host):
        self.host = weakref.ref(host)
        # A weak registry must not retain its key through transport.host or a
        # bound host callback. Pending calls themselves naturally own their host.
        self.transport = SteamCDPTransport(
            weakref.proxy(host),
            allow_target=self.allowed,
            allow_expression=fixed_expression_allowed,
        )
        self.entries, self.revoked, self.by_pid = {}, {}, {}
        self.retired, self.calls = set(), set()
        self.total_rate, self.pid_rate = deque(), {}
        self.closed = False
        self.closing = None

    def caller(self, pid):
        host = self.host()
        if host is None or self.closed:
            return None
        try:
            return host.steam_caller(pid)
        except Exception:
            return None

    def allowed(self, tab):
        host = self.host()
        if host is None or self.closed:
            return False
        try:
            return host.steam_target_allowed(tab) is True
        except Exception:
            return False

    def retire(self, entry):
        self.revoked[entry.pid] = entry.identity
        entry.dispose()
        if self.entries.get(entry.pid) is entry:
            del self.entries[entry.pid]
        if any(call.entry is entry for call in self.calls):
            self.retired.add(entry)

    def invalidate(self, pid=None):
        identity = self.caller(pid) if pid is not None else None
        for key, entry in tuple(self.entries.items()):
            if pid is None or key == pid:
                self.retire(entry)
        if identity is not None:
            self.revoked[pid] = identity
        if pid is None:
            self.transport.revoke_all()

    def admit(self, pid, identity, operation, args):
        if self.closed:
            return _failure("service_closed")
        if pid in self.revoked and _same(identity, self.revoked[pid]):
            return _failure("not_allowed")
        if pid in self.by_pid or len(self.calls) >= 4:
            return _failure("busy")
        now = asyncio.get_running_loop().time()
        rate = self.pid_rate.setdefault(pid, deque())
        for history in (self.total_rate, rate):
            while history and history[0] <= now - 10:
                history.popleft()
        if len(self.total_rate) >= 30 or len(rate) >= 10:
            return _failure("rate_limited")
        entry = self.entries.get(pid)
        if entry is not None and not entry.current():
            self.retire(entry)
            entry = None
        if entry is None:
            entry = self.entries[pid] = _Entry(self, pid, identity)
        # No await occurs between checking admission and reserving the slot.
        call = _Call(entry, operation, args)
        self.calls.add(call)
        self.by_pid[pid] = call
        self.total_rate.append(now)
        rate.append(now)
        return call

    def finish(self, call):
        self.calls.discard(call)
        if self.by_pid.get(call.entry.pid) is call:
            del self.by_pid[call.entry.pid]
        if not any(other.entry is call.entry for other in self.calls):
            self.retired.discard(call.entry)
        if not call.done.done():
            call.done.set_result(None)

    async def close(self):
        # Completion markers end at the service boundary, not when its caller's
        # unrelated task finishes. Retired reads remain owned until their drain.
        if self.calls:
            await asyncio.gather(*(call.done for call in tuple(self.calls)))
        await self.transport.dispose()
        self.entries.clear()
        self.retired.clear()
        self.revoked.clear()
        self.pid_rate.clear()
        self.total_rate.clear()


def _state(host):
    state = _STATES.get(host)
    if state is None:
        state = _STATES[host] = _State(host)
    return state


async def steam_read(host, arg=None):
    request = _request(arg)
    if type(request) is str:
        return _failure(request)
    pid, operation, args = request
    state = _state(host)
    if state.closed:
        return _failure("service_closed")
    identity = state.caller(pid)
    if identity is None:
        return _failure("not_allowed")
    call = state.admit(pid, identity, operation, args)
    if type(call) is dict:
        return call
    try:
        try:
            result = await asyncio.shield(call.task)
        except asyncio.CancelledError:
            if not call.task.done() and not call.task.cancelling():
                call.task.cancel()
            raise
        except Exception:
            result = _failure("provider_read_failed")
    finally:
        if not call.task.done() and not call.task.cancelling():
            call.task.cancel()
        await _drain(call.task)
        state.finish(call)
    if state.closed:
        return _failure("service_closed")
    if not call.entry.current():
        return _failure("stale_caller")
    return result


def invalidate(host, pid=None):
    """Synchronously revoke caller grants; retired reads keep their drain owner."""
    state = _state(host) if pid is not None else _STATES.get(host)
    if state is not None:
        state.invalidate(pid)


def invalidate_targets(host):
    """Revoke target leases without revoking otherwise current plugin identities."""
    state = _STATES.get(host)
    if state is not None:
        state.transport.revoke_all()
        for entry in tuple(state.entries.values()):
            entry.provider.invalidate()


async def shutdown(host):
    """Close admission and await shared owned cleanup despite repeated cancellation."""
    state = _state(host)
    if state.closing is None:
        state.closed = True
        state.invalidate()
        state.closing = asyncio.create_task(state.close())
    cancelled = False
    while not state.closing.done():
        try:
            await asyncio.shield(state.closing)
        except asyncio.CancelledError:
            cancelled = True
    state.closing.result()
    if cancelled:
        raise asyncio.CancelledError


METHODS = {"steam_read": steam_read}
