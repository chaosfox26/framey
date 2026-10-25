import asyncio
import collections
import inspect
import itertools
import math
import os
import re
import signal
import sys
import time
from pathlib import Path

import fy_proto
import fy_store

MAX_WORKERS = 3
DEFAULT_WORKERS = 3
QUEUE_MAX = 16
HOST_MAX = 4
SLOT_WAIT = 5.0
PING_GRACE = 2.0
TERM_WAIT = 3.0
KILL_WAIT = 3.0
EXIT_WAIT = 1.0
UNLOAD_WAIT = 3.5
UNINSTALL_WAIT = 10.5
HOOK_GRACE = 0.5
HOST_TIMEOUT = 65.0
CRASH_WINDOW = 300.0
CRASH_MAX = 3
BACKOFF = (1.0, 5.0, 30.0)
ERR_MAX = 65536
EVENT_QUEUE = 64
EVENT_RATE = 50
LIMITS = {"timeout": (10, 1, 60), "idle": (120, 10, 3600), "concurrency": (1, 1, 8), "memory_mb": (32, 16, 64), "startup_timeout": (10, 1, 60)}
MEMORY_SUM = 100
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "XDG_RUNTIME_DIR") + (("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP") if os.name == "nt" else ())
HOST_OPS = ("fetch", "resource", "files", "log")
REASONS = frozenset(("reload", "disable", "update", "idle", "lru", "shutdown", "uninstall"))
META_KEYS = ("id", "name", "version", "api", "data_version")
PLUGIN_ID = re.compile(r"[a-z0-9_-]{1,32}\Z")


class BackendError(RuntimeError):
    def __init__(self, code, message, stage=None, trace=None):
        message = str(message)[:300]
        super().__init__(message)
        self.code, self.message, self.stage, self.trace = code, message, stage, trace


class EventHub:
    def __init__(self, notify, clock=time.monotonic):
        self.notify, self._clock = notify, clock
        self._queues, self._dropped, self._rate = {}, {}, {}
        self._seq = itertools.count()
        self._size = 0

    def put(self, pid, name, payload, dropped=0):
        if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
            self._dropped[pid] = self._dropped.get(pid, 0) + 1
            return False
        if dropped:
            self._dropped[pid] = self._dropped.get(pid, 0) + dropped
        if not fy_proto.event_ok(name, payload):
            self._dropped[pid] = self._dropped.get(pid, 0) + 1
            return False
        now = self._clock()
        window = self._rate.get(pid)
        if window is None or now - window[0] >= 1.0:
            window = self._rate[pid] = [now, 0]
        if window[1] >= EVENT_RATE:
            self._dropped[pid] = self._dropped.get(pid, 0) + 1
            return False
        window[1] += 1
        queue = self._queues.setdefault(pid, collections.deque())
        if len(queue) >= EVENT_QUEUE:
            queue.popleft()
            self._size -= 1
            self._dropped[pid] = self._dropped.get(pid, 0) + 1
        was_empty = self._size == 0
        queue.append((next(self._seq), name, payload))
        self._size += 1
        if was_empty:
            self.notify()
        return True

    def take(self, limit=10):
        out = []
        while len(out) < limit and self._size:
            pid = min((p for p, q in self._queues.items() if q), key=lambda p: self._queues[p][0][0])
            _, name, payload = self._queues[pid].popleft()
            self._size -= 1
            if not self._queues[pid]:
                del self._queues[pid]
            out.append({"plugin": pid, "name": name, "payload": payload, "dropped": self._dropped.pop(pid, 0)})
        return out

    def clear(self, pid=None):
        if pid is None:
            self._queues.clear()
            self._dropped.clear()
            self._rate.clear()
            self._size = 0
            return
        self._size -= len(self._queues.pop(pid, ()))
        self._dropped.pop(pid, None)
        self._rate.pop(pid, None)


Dirs = collections.namedtuple("Dirs", "config data runtime logs")


class _W:
    def __init__(self, pid):
        self.pid = pid
        self.dir = self.meta = self.lim = None
        self.state = "stopped"
        self.proc = self.os_pid = self.exited = self.timer = self.health = self.stopper = None
        self.ids = itertools.count(1)
        self.pending, self.waiters = {}, collections.deque()
        self.host_tasks = set()
        self.inflight = 0
        self.starting = self.stopping = self.snap = False
        self.uninstalling = False
        self.epoch = 0
        self.boot = None
        self.crashes = collections.deque()
        self.crash_total = self.calls = self.used = 0
        self.last_error = self.last_used = self.limits_applied = self.limits_reason = self.why = None


def _number(value, key):
    default, low, high = LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return min(max(value, low), high)


def _limits(meta):
    b = meta.get("backend") if isinstance(meta.get("backend"), dict) else {}
    return {
        "timeout": _number(b.get("timeout", meta.get("timeout")), "timeout"),
        "idle": _number(b.get("idle"), "idle"),
        "keepalive": b.get("keepalive") is True,
        "concurrency": int(_number(b.get("concurrency"), "concurrency")),
        "memory_mb": int(_number(b.get("memory_mb"), "memory_mb")),
        "startup_timeout": _number(b.get("startup_timeout"), "startup_timeout"),
    }


def _meta(meta):
    out = {}
    for key in META_KEYS:
        value = meta.get(key)
        if isinstance(value, str):
            out[key] = value[:200]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
    return out


async def _aw(value):
    return await value if inspect.isawaitable(value) else value


def _rss(os_pid):
    if not os_pid or not sys.platform.startswith("linux"):
        return None
    try:
        with open("/proc/%d/status" % os_pid) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


class Supervisor:
    def __init__(self, host, spawn=None):
        self.host, self._spawn = host, spawn
        self._ws, self._tasks, self._freed, self._old = {}, set(), [], set()
        self._seq = itertools.count(1)
        self._closed = self._runtime_warned = False
        self.events = EventHub(getattr(host, "events_ready", None) or (lambda: None))

    def register(self, pid, plugin_dir, meta):
        if not isinstance(pid, str) or not PLUGIN_ID.match(pid):
            raise ValueError("bad plugin id")
        meta = meta if isinstance(meta, dict) else {}
        w = self._ws.get(pid) or self._ws.setdefault(pid, _W(pid))
        w.dir, w.meta, w.lim = Path(plugin_dir), _meta(meta), _limits(meta)
        w.meta.setdefault("id", pid)
        backend = meta.get("backend")
        declared = backend.get("memory_mb") if isinstance(backend, dict) else None
        if isinstance(declared, (int, float)) and not isinstance(declared, bool) and declared > LIMITS["memory_mb"][2]:
            self.host.log("[%s] memory_mb %s clamped to %d" % (pid, declared, w.lim["memory_mb"]))

    def forget(self, pid):
        w = self._ws.pop(pid, None)
        self.events.clear(pid)
        if w is not None:
            w.epoch += 1
            if w.proc is not None or w.starting:
                self._old.add(w)
            self._cancel_timer(w)
            self._fail_waiters(w, BackendError("forgotten", "module removed"))
            self._stopper(w, "uninstall")

    def stuck(self, pid):
        w = self._ws.get(pid)
        return w is not None and w.state == "hung"

    def status(self):
        out = {}
        for pid, w in self._ws.items():
            out[pid] = {
                "state": w.state,
                "os_pid": w.os_pid,
                "rss_kb": _rss(w.os_pid) if self._spawn is None else None,
                "calls": w.calls,
                "crashes": w.crash_total,
                "last_error": dict(w.last_error) if w.last_error else None,
                "last_used": w.last_used,
                "limits_applied": w.limits_applied,
                "limits_warning": "memory cap not applied: %s" % (w.limits_reason or "unknown") if w.limits_applied is False else None,
            }
        return out

    async def call(self, pid, method, arg):
        w = self._get(pid)
        if self._closed:
            raise BackendError("shutdown", "loader is shutting down")
        if not isinstance(method, str) or not 0 < len(method) <= 128:
            raise BackendError("bad_method", "bad method name")
        try:
            size = len(fy_proto.dumps(arg))
        except fy_proto.ProtocolError:
            raise BackendError("bad_arg", "argument is not JSON")
        if size > fy_proto.PAYLOAD_MAX:
            raise BackendError("too_large", "argument is %d bytes; limit is %d" % (size, fy_proto.PAYLOAD_MAX))
        await self._acquire(w)
        w.calls += 1
        try:
            return await self._run(w, method, arg)
        finally:
            self._release(w)

    async def stop(self, pid, reason="reload"):
        w = self._ws.get(pid)
        if w is not None:
            w.epoch += 1
            self._fail_waiters(w, self._stopped())
            self._wake()
            await self._join(self._stopper(w, reason))

    async def restart(self, pid):
        w = self._get(pid)
        await self.stop(pid, "reload")
        w.crashes.clear()
        w.last_error = None
        if w.state in ("failed", "crashed"):
            w.state = "stopped"
        meta = self.host.manifest(w.dir)
        if isinstance(meta, dict):
            self.register(pid, w.dir, meta)

    async def uninstall(self, pid, purge=False, do_purge=True):
        w = self._ws.get(pid)
        if w is not None:
            if w.uninstalling:
                raise BackendError("busy", "module is being uninstalled")
            w.uninstalling = True
        try:
            return await self._uninstall(pid, w, purge, do_purge)
        finally:
            if w is not None:
                w.uninstalling = False

    async def _uninstall(self, pid, w, purge, do_purge):
        hook, error = "skipped", None
        if w is not None:
            self._fail_waiters(w, BackendError("stopped", "module is being uninstalled"))
            try:
                try:
                    await self._acquire(w, uninstall=True)
                except BackendError as e:
                    error = e.message
                else:
                    try:
                        rep = await self._request(w, {"op": "uninstall", "purge": bool(purge)}, UNINSTALL_WAIT)
                        if rep.get("ok"):
                            hook = "ran" if (rep.get("result") or {}).get("ran") else "none"
                        else:
                            hook, error = "failed", (rep.get("error") or {}).get("message")
                    except asyncio.TimeoutError:
                        hook, error = "failed", "uninstall hook timed out"
                        await self._terminate(w)
                    except BackendError as e:
                        hook, error = "failed", e.message
                    finally:
                        self._release(w)
            finally:
                w.epoch += 1
                self._fail_waiters(w, self._stopped())
                self._wake()
                await self._join(self._stopper(w, "uninstall"))
        purged = False
        if purge and do_purge:
            try:
                result = await _aw(self.host.purge(pid))
                purged = not (isinstance(result, dict) and result.get("failed"))
            except Exception as e:
                error = error or "purge failed: %s" % e
        out = {"ok": True, "purged": purged, "hook": hook}
        if error:
            out["error"] = str(error)[:300]
        return out

    async def shutdown(self, total=4.0):
        self._closed = True
        every = list(self._ws.values()) + list(self._old)
        for w in every:
            w.epoch += 1
            self._cancel_timer(w)
            self._fail_waiters(w, BackendError("shutdown", "loader is shutting down"))
        self._wake()
        booting = [w.boot for w in every if w.boot is not None and not w.boot.done() and w.proc is None]
        if booting:
            await asyncio.wait(booting, timeout=total)
        live = [w for w in every if w.proc is not None]
        for w in live:
            w.stopping, w.state = True, "stopping"
            self._send(w, {"op": "unload", "id": next(w.ids), "reason": "shutdown"})
        await self._until_exit(live, total)
        left = [w for w in live if w.proc is not None]
        for w in left:
            self._signal(w, w.proc, False)
        await self._until_exit(left, 1.0)
        left = [w for w in left if w.proc is not None]
        for w in left:
            self._signal(w, w.proc, True)
        await self._until_exit(left, 1.0)

    def _get(self, pid):
        w = self._ws.get(pid)
        if w is None or w.dir is None:
            raise BackendError("no_backend", "module has no backend")
        return w

    def _task(self, coro):
        task = self.host.loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return task

    async def _join(self, task):
        if task is not None:
            await asyncio.wait({task})

    async def _until_exit(self, ws, timeout):
        futs = [w.exited for w in ws if w.exited is not None and not w.exited.done()]
        if futs:
            await asyncio.wait(futs, timeout=timeout)

    def _paths(self, pid):
        home = Path(self.host.HOME)
        xdg = fy_store.runtime_root()
        if not xdg:
            if not self._runtime_warned:
                self._runtime_warned = True
                self.host.log("XDG_RUNTIME_DIR is not set and /run/user/<uid> is not a private directory; backends will not start")
            raise BackendError("runtime", "XDG_RUNTIME_DIR is not set")
        runtime = Path(xdg) / "framey" / pid
        return Dirs(home / ".config" / "framey" / "plugins" / pid, home / ".local" / "share" / "framey" / pid, runtime, home / ".local" / "state" / "framey" / pid)

    def _cap(self):
        try:
            value = self.host.settings()["prefs"]["workers_max"]
        except (KeyError, TypeError, AttributeError):
            return DEFAULT_WORKERS
        return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_WORKERS else DEFAULT_WORKERS

    def _prune(self, w, now):
        while w.crashes and now - w.crashes[0] > CRASH_WINDOW:
            w.crashes.popleft()

    def _gone(self, w, epoch):
        return self._closed or w.epoch != epoch or self._ws.get(w.pid) is not w

    def _stopped(self):
        if self._closed:
            return BackendError("shutdown", "loader is shutting down")
        return BackendError("stopped", "module was stopped while starting")

    def _gate(self, w):
        if w.state == "failed":
            e = w.last_error or {}
            raise BackendError("failed", e.get("message") or "module failed; reload it", stage=e.get("stage"))
        now = self.host.mono()
        self._prune(w, now)
        if w.state == "crashed" and w.crashes:
            wait = BACKOFF[min(len(w.crashes), len(BACKOFF)) - 1] - (now - w.crashes[-1])
            if wait > 0:
                raise BackendError("backoff", "module crashed; try again in %d s" % math.ceil(wait))

    async def _acquire(self, w, uninstall=False):
        if self._closed or self._ws.get(w.pid) is not w:
            raise self._stopped()
        if w.stopping or (w.uninstalling and not uninstall):
            raise BackendError("stopped", "module is being stopped")
        if w.state in ("failed", "crashed"):
            self._gate(w)
        if len(w.waiters) >= QUEUE_MAX:
            raise BackendError("busy", "module busy; too many pending calls")
        fut = asyncio.get_running_loop().create_future()
        w.waiters.append(fut)
        self._pump(w)
        try:
            await fut
        except BaseException:
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                self._release(w)
            elif fut in w.waiters:
                w.waiters.remove(fut)
            raise

    def _release(self, w):
        w.inflight -= 1
        w.used, w.last_used = next(self._seq), self.host.now()
        if w.inflight == 0:
            self._arm(w)
            self._wake()
        self._pump(w)

    def _pump(self, w):
        if self._closed or self._ws.get(w.pid) is not w:
            return
        while w.waiters:
            fut = w.waiters[0]
            if fut.done():
                w.waiters.popleft()
            elif w.state == "ready":
                if w.inflight >= (1 if w.uninstalling else w.lim["concurrency"]):
                    return
                w.waiters.popleft()
                w.inflight += 1
                self._cancel_timer(w)
                fut.set_result(None)
            elif w.state in ("stopped", "crashed", "failed") and not w.starting:
                w.starting = True
                w.boot = self._task(self._boot_task(w))
                return
            else:
                return

    def _fail_waiters(self, w, err):
        while w.waiters:
            fut = w.waiters.popleft()
            if not fut.done():
                fut.set_exception(BackendError(err.code, err.message, stage=err.stage))

    def _wake(self):
        freed, self._freed = self._freed, []
        for fut in freed:
            if not fut.done():
                fut.set_result(None)

    def _cancel_timer(self, w):
        if w.timer is not None:
            w.timer.cancel()
            w.timer = None

    def _arm(self, w):
        self._cancel_timer(w)
        if w.state == "ready" and w.inflight == 0 and not w.waiters and not w.lim["keepalive"]:
            w.timer = self.host.loop().call_later(w.lim["idle"], self._idle, w)

    def _idle(self, w):
        w.timer = None
        if w.state == "ready" and w.inflight == 0 and not w.waiters:
            self._stopper(w, "idle")

    async def _boot_task(self, w):
        try:
            await self._boot(w)
        except asyncio.CancelledError:
            self._fail_waiters(w, self._stopped())
            await self._terminate(w)
            raise
        except BackendError as e:
            self._fail_waiters(w, e)
        except Exception as e:
            self._fail_waiters(w, BackendError("start", "backend could not start: %s" % e))
            await self._terminate(w)
        finally:
            w.starting, w.boot = False, None
            if w.proc is None:
                if w.state == "starting":
                    w.state = "stopped"
                self._old.discard(w)
                self._wake()
        self._pump(w)

    def _running(self):
        return [x for x in list(self._ws.values()) + list(self._old) if x.proc is not None or x.state in ("starting", "ready", "hung", "stopping")]

    def _live(self):
        return len(self._running())

    def _memory(self):
        return sum(x.lim["memory_mb"] for x in self._running())

    def _victim(self, w):
        idle = [x for x in self._ws.values() if x is not w and x.state == "ready" and x.inflight == 0 and not x.waiters]
        pool = [x for x in idle if not x.lim["keepalive"]] or idle
        return min(pool, key=lambda x: x.used) if pool else None

    async def _room(self, w, epoch):
        loop = asyncio.get_running_loop()
        end = loop.time() + SLOT_WAIT
        while self._live() >= self._cap() or self._memory() + w.lim["memory_mb"] > MEMORY_SUM:
            if self._gone(w, epoch):
                raise self._stopped()
            victim = self._victim(w)
            if victim is not None:
                await self._join(self._stopper(victim, "lru"))
                continue
            if self._memory() + w.lim["memory_mb"] > MEMORY_SUM:
                raise BackendError("memory", "memory limit reached: running modules use %d MB of %d MB" % (self._memory(), MEMORY_SUM))
            left = end - loop.time()
            fut = loop.create_future()
            self._freed.append(fut)
            try:
                if left <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait_for(fut, left)
            except asyncio.TimeoutError:
                raise BackendError("busy_workers", "too many modules running; try again shortly")
            finally:
                if fut in self._freed:
                    self._freed.remove(fut)
        w.state = "starting"

    def _errlog(self, logs, chunk):
        logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = logs / "worker.err"
        try:
            if path.stat().st_size + len(chunk) > ERR_MAX:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass
        with open(path, "ab") as f:
            f.write(chunk)

    async def _drain(self, proc, logs):
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            try:
                self._errlog(logs, chunk)
            except OSError:
                pass

    async def _launch(self, w, dirs):
        argv = [sys.executable, "-I", "-B", str(Path(self.host.ROOT) / "fy_worker.py")]
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env.update(FRAMEY_PLUGIN=w.pid, PYTHONDONTWRITEBYTECODE="1")
        dirs.data.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._spawn is not None:
            return await _aw(self._spawn(argv, env, str(dirs.data)))
        options = dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=str(dirs.data), close_fds=True, limit=fy_proto.LINE_MAX + 2)
        if os.name != "nt":
            options["start_new_session"] = True
        return await asyncio.create_subprocess_exec(*argv, **options)

    def _init_message(self, w, dirs):
        return {
            "plugin": w.pid,
            "file": str(w.dir / "backend.py"),
            "dirs": {k: str(v) if v is not None else None for k, v in dirs._asdict().items()},
            "meta": dict(w.meta),
            "runtime": {"framey": self.host.VERSION, "api": self.host.API_GENERATION, "platform": self.host.PLATFORM, "python": "%d.%d.%d" % sys.version_info[:3]},
            "limits": {"startup_timeout": w.lim["startup_timeout"], "timeout": w.lim["timeout"], "concurrency": w.lim["concurrency"], "memory_mb": w.lim["memory_mb"]},
        }

    async def _abort(self, w, proc=None):
        if proc is not None:
            self._signal(w, proc, True)
            try:
                await asyncio.wait_for(proc.wait(), KILL_WAIT)
            except asyncio.TimeoutError:
                pass
        if w.state == "starting":
            w.state = "stopped"
        self._wake()
        raise self._stopped()

    async def _boot(self, w):
        epoch = w.epoch
        self._gate(w)
        if not (w.dir / "backend.py").is_file():
            raise BackendError("no_backend", "module has no backend")
        dirs = self._paths(w.pid)
        await self._room(w, epoch)
        if self._gone(w, epoch):
            await self._abort(w)
        try:
            proc = await self._launch(w, dirs)
        except OSError as e:
            self._crashed(w, "could not start backend: %s" % e)
            raise BackendError("spawn", "could not start backend: %s" % e)
        if self._gone(w, epoch):
            await self._abort(w, proc)
        w.proc, w.os_pid, w.stopping, w.why = proc, proc.pid, False, None
        w.exited = asyncio.get_running_loop().create_future()
        reader = self._task(self._read(w, proc))
        if getattr(proc, "stderr", None) is not None:
            self._task(self._drain(proc, dirs.logs))
        self._task(self._reap(w, proc, reader))
        try:
            await self._start_hooks(w, dirs)
        except BackendError as e:
            if w.proc is proc and not w.stopping:
                w.last_error = {"code": e.code, "message": e.message, "stage": e.stage}
                w.state = "failed"
                await self._terminate(w)
            if w.snap:
                await self._restore(w)
            raise
        if self._gone(w, epoch) or w.proc is not proc or w.stopping:
            raise self._stopped()
        w.state = "ready"
        self._arm(w)

    async def _start_hooks(self, w, dirs):
        host, meta = self.host, w.meta
        try:
            rec = host.module_state(w.pid)
        except Exception:
            rec = None
        rec = rec if isinstance(rec, dict) else {}
        ver, data_v = meta.get("version"), meta.get("data_version")
        data_v = data_v if isinstance(data_v, int) else None
        due = bool(rec.get("ver")) and (rec.get("ver") != ver or rec.get("data_v") != data_v)
        base = self._init_message(w, dirs)
        w.snap = False
        if due:
            try:
                await _aw(host.snapshot_settings(w.pid))
            except Exception as e:
                raise BackendError("stage", "could not back up settings: %s" % e, stage="migrate")
            w.snap = True
            await self._hook(w, dict(base, op="migrate", from_version=rec.get("ver"), to_version=ver, data_from=rec.get("data_v"), data_to=data_v), "migrate")
        result = await self._hook(w, dict(base, op="startup"), "startup")
        w.limits_applied, w.limits_reason = result.get("limits_applied"), result.get("limits_reason")
        if due or (ver and not rec.get("ver")):
            try:
                host.set_module_state(w.pid, dict(rec, ver=ver, data_v=data_v, ok_at=int(host.now()), fails=0))
            except Exception as e:
                host.log("[%s] could not save module state: %s" % (w.pid, e))
        if due and getattr(host, "drop_snapshot", None):
            try:
                await _aw(host.drop_snapshot(w.pid))
            except Exception:
                pass

    async def _restore(self, w):
        restore = getattr(self.host, "restore_snapshot", None)
        if restore is None:
            return
        try:
            await _aw(restore(w.pid))
        except Exception as e:
            self.host.log("[%s] could not restore settings: %s" % (w.pid, e))

    async def _hook(self, w, msg, stage):
        limit = w.lim["startup_timeout"]
        try:
            rep = await self._request(w, msg, limit + HOOK_GRACE)
        except asyncio.TimeoutError:
            raise BackendError("timeout", "%s hook timed out after %g s" % (stage, limit), stage=stage)
        if not rep.get("ok"):
            e = rep.get("error") if isinstance(rep.get("error"), dict) else {}
            raise BackendError(str(e.get("code") or "stage"), e.get("message") or "%s failed" % stage, stage=e.get("stage") or stage, trace=e.get("trace"))
        return rep.get("result") if isinstance(rep.get("result"), dict) else {}

    async def _request(self, w, msg, timeout):
        if w.proc is None:
            raise BackendError("exit", "backend is not running")
        rid = msg["id"] = next(w.ids)
        fut = asyncio.get_running_loop().create_future()
        w.pending[rid] = fut
        try:
            if not self._send(w, msg):
                raise BackendError("exit", "backend is not running")
            done, _ = await asyncio.wait({fut}, timeout=timeout)
            if not done:
                raise asyncio.TimeoutError
            return fut.result()
        finally:
            w.pending.pop(rid, None)
            if not fut.done():
                fut.cancel()

    def _send(self, w, msg):
        if w.proc is None:
            return False
        try:
            w.proc.stdin.write(fy_proto.encode(msg))
        except fy_proto.ProtocolError as e:
            raise BackendError("too_large", str(e))
        except Exception:
            return False
        return True

    async def _run(self, w, method, arg):
        limit, proc = w.lim["timeout"], w.proc
        msg = {"op": "call", "method": method, "arg": arg}
        try:
            rep = await self._request(w, msg, limit)
        except asyncio.CancelledError:
            if "id" in msg:
                self._hung(w, proc, msg["id"])
            raise
        except asyncio.TimeoutError:
            self._hung(w, proc, msg["id"])
            raise BackendError("timeout", "backend call timed out after %g s; module not responding" % limit)
        if rep.get("ok"):
            return rep.get("result")
        e = rep.get("error") if isinstance(rep.get("error"), dict) else {}
        raise BackendError(str(e.get("code") or "exception"), e.get("message") or "backend error", stage=e.get("stage"), trace=e.get("trace"))

    def _hung(self, w, proc, rid):
        if w.proc is not proc or w.stopping:
            return
        self._send(w, {"op": "cancel", "id": next(w.ids), "target": rid})
        if w.state == "ready":
            w.state = "hung"
            w.health = self._task(self._health(w, proc))

    async def _health(self, w, proc):
        try:
            await self._request(w, {"op": "ping"}, PING_GRACE)
            alive = True
        except (asyncio.TimeoutError, BackendError):
            alive = False
        if w.proc is not proc or w.stopping:
            return
        w.health = None
        if alive:
            w.state = "ready"
            self._arm(w)
            self._pump(w)
        else:
            w.why = "backend stopped responding and was killed"
            await self._kill(w, proc)

    async def _read(self, w, proc):
        why = None
        while True:
            try:
                line = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                why = "protocol violation: oversize line"
                break
            if not line.endswith(b"\n"):
                break
            try:
                ok = self._dispatch(w, fy_proto.decode(line))
            except fy_proto.ProtocolError as e:
                ok, why = False, "protocol violation: %s" % e
            if not ok:
                why = why or "protocol violation: unknown message"
                break
        w.why = w.why or why
        if why is None:
            try:
                await asyncio.wait_for(proc.wait(), EXIT_WAIT)
            except asyncio.TimeoutError:
                w.why = w.why or "backend closed its output"
        if proc.returncode is None:
            await self._kill(w, proc)
        self._down(w, proc)

    def _dispatch(self, w, m):
        rid = m.get("re")
        if isinstance(rid, int) and not isinstance(rid, bool):
            fut = w.pending.get(rid)
            if fut is not None and not fut.done():
                fut.set_result(m)
            elif rid == 0 and isinstance(m.get("error"), dict):
                w.why = str(m["error"].get("message"))[:300]
            return True
        if m.get("ev") == "emit":
            if self._ws.get(w.pid) is w and not self._closed:
                self.events.put(w.pid, m.get("name"), m.get("payload"), m.get("dropped", 0))
            return True
        if "host" in m:
            self._host(w, m)
            return True
        return False

    def _host(self, w, m):
        op, rid = m.get("host"), m.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool):
            return
        if self._ws.get(w.pid) is not w:
            self._host_reply(w, rid, error=fy_proto.error("unavailable", "module removed"))
        elif op not in HOST_OPS:
            self._host_reply(w, rid, error=fy_proto.error("bad_op", "unknown host call"))
        elif len(w.host_tasks) >= HOST_MAX:
            self._host_reply(w, rid, error=fy_proto.error("busy", "too many host calls running"))
        else:
            task = self._task(self._host_run(w, w.proc, op, rid, m.get("arg")))
            w.host_tasks.add(task)
            task.add_done_callback(w.host_tasks.discard)

    def _host_reply(self, w, rid, result=None, error=None):
        msg = {"op": "host_reply", "id": rid, "ok": error is None}
        msg["error" if error else "result"] = error or result
        self._send(w, msg)

    async def _host_run(self, w, proc, op, rid, arg):
        try:
            fn = self.host.hostcalls[op]
        except (KeyError, AttributeError, TypeError):
            return self._host_reply(w, rid, error=fy_proto.error("unavailable", "%s is not available" % op))
        try:
            result = await asyncio.wait_for(_aw(fn(w.pid, arg)), HOST_TIMEOUT)
            if len(fy_proto.dumps(result)) > fy_proto.PAYLOAD_MAX:
                raise BackendError("too_large", "host result too large")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            error = fy_proto.error("timeout", "host call timed out")
        except fy_proto.ProtocolError:
            error = fy_proto.error("bad_result", "host result is not JSON")
        except Exception as e:
            error = fy_proto.error(str(getattr(e, "code", "error")), getattr(e, "message", None) or str(e) or type(e).__name__)
        else:
            error = None
        if w.proc is proc:
            self._host_reply(w, rid, result if error is None else None, error)

    def _signal(self, w, proc, hard):
        try:
            if self._spawn is None and hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGTERM)
            elif proc.returncode is None:
                (proc.kill if hard else proc.terminate)()
        except OSError:
            pass

    def _sweep(self, w, proc):
        if self._spawn is None and hasattr(os, "killpg"):
            self._signal(w, proc, True)

    async def _exited(self, proc):
        fd = None
        if hasattr(os, "pidfd_open"):
            try:
                fd = os.pidfd_open(proc.pid)
            except OSError:
                pass
        if fd is None:
            await proc.wait()
            return
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        loop.add_reader(fd, lambda: fut.done() or fut.set_result(None))
        try:
            await fut
        finally:
            loop.remove_reader(fd)
            os.close(fd)

    async def _reap(self, w, proc, reader):
        await self._exited(proc)
        self._sweep(w, proc)
        await asyncio.wait({reader}, timeout=EXIT_WAIT)
        if not reader.done():
            proc.stdout.feed_eof()

    async def _kill(self, w, proc):
        self._signal(w, proc, False)
        try:
            await asyncio.wait_for(proc.wait(), TERM_WAIT)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                self._signal(w, proc, True)
            try:
                await asyncio.wait_for(proc.wait(), KILL_WAIT)
            except asyncio.TimeoutError:
                pass

    async def _terminate(self, w):
        proc = w.proc
        if proc is None:
            return
        w.stopping = True
        await self._kill(w, proc)
        await self._until_exit([w], EXIT_WAIT)

    def _stopper(self, w, reason):
        if w.stopper is None and w.proc is not None:
            w.stopping, w.state = True, "stopping"
            self._cancel_timer(w)
            w.stopper = self._task(self._stop_run(w, reason if reason in REASONS else "reload"))
        return w.stopper

    async def _stop_run(self, w, reason):
        proc = w.proc
        try:
            try:
                await self._request(w, {"op": "unload", "reason": reason}, UNLOAD_WAIT)
            except (asyncio.TimeoutError, BackendError):
                pass
            await self._until_exit([w], EXIT_WAIT)
            if w.proc is proc:
                await self._kill(w, proc)
                await self._until_exit([w], EXIT_WAIT)
        finally:
            w.stopper = None

    def _crashed(self, w, message):
        now = self.host.mono()
        self._prune(w, now)
        w.crashes.append(now)
        w.crash_total += 1
        w.last_error = {"code": "crash", "message": message, "stage": None}
        w.state = "failed" if len(w.crashes) >= CRASH_MAX else "crashed"
        self._wake()
        self.host.log("[%s] %s" % (w.pid, message))

    def _down(self, w, proc):
        if w.proc is not proc:
            return
        self._sweep(w, proc)
        self._old.discard(w)
        w.proc, w.os_pid = None, None
        message = w.why or "backend exited unexpectedly (code %s)" % proc.returncode
        crash = not w.stopping
        for fut in list(w.pending.values()):
            if not fut.done():
                fut.set_exception(BackendError("exit", message))
        w.pending.clear()
        for task in list(w.host_tasks):
            task.cancel()
        self._cancel_timer(w)
        w.health = None
        if crash:
            self._crashed(w, message)
            self._fail_waiters(w, BackendError("exit", message))
        elif w.state != "failed":
            w.state = "stopped"
        w.stopping, w.why = False, None
        if w.exited is not None and not w.exited.done():
            w.exited.set_result(None)
        self._wake()
        self._pump(w)
