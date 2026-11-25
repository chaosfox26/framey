"""Bounded, plugin-scoped requests to the native HTTPS browser handler."""

import asyncio
import json
import subprocess
import weakref
from collections import Counter, deque
from time import monotonic

from fy_net import NetError, PID, _target

PER_PLUGIN = 1
TOTAL = 4
PER_PLUGIN_RATE = 4
TOTAL_RATE = 12
RATE_WINDOW = 10.0
REQUEST_TIMEOUT = 3.0
SHUTDOWN_GRACE = 1.0

_states = weakref.WeakKeyDictionary()


def _error(code):
    return {
        "error": {
            "not_allowed": "This module cannot open links right now",
            "busy": "A browser request is already pending",
            "rate_limited": "Wait a moment before opening another link",
            "browser_unavailable": "The browser request could not be completed",
            "timeout": "The browser request took too long",
        }[code],
        "code": code,
    }


def _identity(host, pid):
    """Fail closed unless the current installed manifest and enabled state agree."""
    try:
        if not isinstance(pid, str) or not PID.fullmatch(pid) or host.enabled(pid) is not True:
            return None
        found = [m for m in host.manifests() if isinstance(m, dict) and m.get("id") == pid]
        if len(found) == 1:
            return json.dumps(found[0], sort_keys=True, separators=(",", ":"))
    except Exception:
        pass
    return None


class _State:
    def __init__(self):
        self.active = Counter()
        self.recent = deque()
        self.requests = {}
        self.spawns = set()
        self.closed = False
        self.draining = None

    def acquire(self, pid):
        if self.closed:
            return "not_allowed"
        now = monotonic()
        while self.recent and self.recent[0][0] <= now - RATE_WINDOW:
            self.recent.popleft()
        if self.active[pid] >= PER_PLUGIN or sum(self.active.values()) >= TOTAL:
            return "busy"
        if (
            len(self.recent) >= TOTAL_RATE
            or sum(p == pid for _, p in self.recent) >= PER_PLUGIN_RATE
        ):
            return "rate_limited"
        self.active[pid] += 1
        self.recent.append((now, pid))
        return None

    def finished(self, pid, task):
        self.requests.pop(task, None)
        self.active[pid] -= 1
        if not self.active[pid]:
            del self.active[pid]
        # A timed-out/cancelled caller no longer consumes the task's result.
        if not task.cancelled():
            task.exception()


async def _spawn(host, pid, identity, url, abandoned):
    # This runs in its own task: recheck after that scheduling boundary.
    if abandoned.is_set() or _identity(host, pid) != identity:
        return None
    return await asyncio.create_subprocess_exec(
        "/usr/bin/xdg-open",
        url,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _request(host, pid, identity, url, abandoned, state):
    process = None
    waiter = abandoner = None
    spawn = asyncio.create_task(_spawn(host, pid, identity, url, abandoned))
    state.spawns.add(spawn)
    spawn.add_done_callback(state.spawns.discard)
    try:
        # Caller cancellation signals abandonment rather than cancelling a spawn
        # which may already have created a child but not returned its handle.
        process = await asyncio.shield(spawn)
        if process is None:
            return _error("not_allowed")
        waiter = asyncio.create_task(process.wait())
        if abandoned.is_set() or _identity(host, pid) != identity:
            return _error("not_allowed")
        abandoner = asyncio.create_task(abandoned.wait())
        await asyncio.wait((waiter, abandoner), return_when=asyncio.FIRST_COMPLETED)
        if abandoned.is_set():
            return _error("timeout")
        if waiter.result() != 0:
            return _error("browser_unavailable")
        # This acknowledges the OS handler's request, never browser visibility.
        return {"requested": True}
    except asyncio.CancelledError:
        # Preserve process ownership if the host itself is shutting down.
        if process is None:
            # asyncio's subprocess transport closes and reaps a child when its
            # creation coroutine is cancelled before returning the handle.
            spawn.cancel()
            try:
                process = await spawn
            except (asyncio.CancelledError, Exception):
                pass
        raise
    except Exception:
        return _error("browser_unavailable")
    finally:
        if abandoner is not None:
            abandoner.cancel()
            await asyncio.gather(abandoner, return_exceptions=True)
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            if waiter is None:
                waiter = asyncio.create_task(process.wait())
            await asyncio.shield(waiter)


async def open_url(host, arg=None):
    """Return an acceptance receipt or a sanitized error; never fetch the URL."""
    a = arg if isinstance(arg, dict) else {}
    pid = a.get("pid")
    identity = _identity(host, pid)
    if identity is None:
        return _error("not_allowed")
    url = a.get("url")
    try:
        _target(url, False)
    except NetError as error:
        return {"error": error.message, "code": error.code}
    try:
        state = _states.setdefault(host, _State())
    except TypeError:
        return _error("not_allowed")
    limited = state.acquire(pid)
    if limited:
        return _error(limited)
    abandoned = asyncio.Event()
    task = asyncio.create_task(_request(host, pid, identity, url, abandoned, state))
    state.requests[task] = abandoned
    task.add_done_callback(lambda done: state.finished(pid, done))
    try:
        return await asyncio.wait_for(asyncio.shield(task), REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        abandoned.set()
        return _error("timeout")
    except asyncio.CancelledError:
        abandoned.set()
        raise


async def _drain(state):
    requests = tuple(state.requests)
    for abandoned in state.requests.values():
        abandoned.set()
    if requests:
        await asyncio.wait(requests, timeout=SHUTDOWN_GRACE)
        # Only cancel stalled creation coroutines. Cancelling a request already
        # reaping its child would interrupt its finally block and lose ownership.
        # asyncio's subprocess creation cancellation closes and reaps a child
        # whose process handle has not yet been returned.
        for spawn in tuple(state.spawns):
            if not spawn.done():
                spawn.cancel()
        await asyncio.gather(*requests, return_exceptions=True)


async def shutdown(host):
    """Close admission and drain native children before the host closes its loop.

    Allow one second for late spawns, then cancel them and await subprocess
    transport cleanup. If the caller is cancelled, finish reaping before
    propagating cancellation. Repeated calls share the same drain.
    """
    try:
        state = _states.setdefault(host, _State())
    except TypeError:
        return
    state.closed = True
    for abandoned in state.requests.values():
        abandoned.set()
    if state.draining is None:
        state.draining = asyncio.create_task(_drain(state))
    try:
        await asyncio.shield(state.draining)
    except asyncio.CancelledError:
        await asyncio.shield(state.draining)
        raise


METHODS = {"open_url": open_url}
