import asyncio
import importlib
import importlib.util
import inspect
import itertools
import os
import pathlib
import re
import signal
import sys
import threading
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import fy_proto
except ImportError:
    sys.path.insert(0, HERE)
    import fy_proto

    sys.path.remove(HERE)

PR_SET_PDEATHSIG = 1
PR_SET_NO_NEW_PRIVS = 38
EMIT_RATE = 50
DROP_MAX = (1 << 31) - 1
CALLS_MAX = 32
READ_MAX = fy_proto.LINE_MAX + 2
PLUGIN_ID = re.compile(r"[a-z0-9_-]{1,32}\Z")
HOOKS = ("call", "startup", "unload", "migrate", "uninstall")
OPS = ("startup", "migrate", "call", "uninstall", "unload")
DIR_KEYS = ("config", "data", "runtime", "logs")


class Fail(Exception):
    def __init__(self, err):
        super().__init__(err.get("message"))
        self.err = err


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Box(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def failure(code, message, stage=None, trace=None):
    extra = {}
    if stage is not None:
        extra["stage"] = stage
    if trace is not None:
        extra["trace"] = trace
    return Fail(fy_proto.error(code, message, **extra))


def describe(exc, stage=None):
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-600:]
    return fy_proto.error(
        "exception",
        str(exc) or type(exc).__name__,
        **({"stage": stage} if stage else {}),
        trace=trace,
    )


def takes_arg(func):
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return True
    kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
    )
    return any(p.kind in kinds for p in params)


async def invoke(func, *args):
    if args and not takes_arg(func):
        args = ()
    result = func(*args)
    return await result if inspect.isawaitable(result) else result


def clamp(value, default, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return min(max(value, low), high)


def vmdata_bytes():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmData:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def limit_data(megabytes):
    try:
        import resource
    except ImportError:
        return False, "unsupported platform"
    used = vmdata_bytes()
    if used is None:
        return False, "VmData unreadable"
    limit = used + (int(megabytes) << 20)
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
    except (OSError, ValueError, AttributeError) as e:
        return False, str(e)
    return True, None


def limit_boot():
    try:
        import resource
    except ImportError:
        return
    for name, value in (("RLIMIT_CORE", 0), ("RLIMIT_NOFILE", 256), ("RLIMIT_FSIZE", 64 << 20)):
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
        except (ValueError, OSError, AttributeError):
            pass


def privileges():
    if not sys.platform.startswith("linux"):
        return True, None
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        arg = ctypes.c_ulong
        parent = os.getppid()
        if libc.prctl(PR_SET_PDEATHSIG, arg(int(signal.SIGKILL)), arg(0), arg(0), arg(0)) != 0:
            print(
                "framey worker: parent-death signal unavailable: %s"
                % os.strerror(ctypes.get_errno()),
                file=sys.stderr,
            )
        elif os.getppid() != parent:
            leave(0)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, arg(1), arg(0), arg(0), arg(0)) != 0:
            return False, "no_new_privs unavailable: %s" % os.strerror(ctypes.get_errno())
    except Exception as e:
        return False, "no_new_privs unavailable: %s" % e
    return True, None


def optional(name):
    sys.path.insert(0, HERE)
    try:
        return importlib.import_module(name)
    except Exception:
        return None
    finally:
        if HERE in sys.path:
            sys.path.remove(HERE)


def leave(code):
    os._exit(code)


class Rpc:
    def __init__(self, worker):
        self.worker = worker

    def emit(self, name, payload=None):
        return self.worker.emit(name, payload)

    def event_dropped(self, count=1):
        self.worker.event_dropped(count)

    async def host(self, op, arg=None):
        return await self.worker.host(op, arg)


class Worker:
    def __init__(self, loop, out, sdk_module=None, store_module=None):
        self.loop, self.out, self.sdk_module, self.store_module = (
            loop,
            out,
            sdk_module,
            store_module,
        )
        self.lock = threading.Lock()
        self.window, self.count, self.dropped = 0.0, 0, 0
        self.tasks, self.calls, self.waiting = set(), {}, {}
        self.cancelled, self.hook_tasks = set(), set()
        self.lifecycle = None
        self.closing = False
        self.limits_configured = False
        self.host_ids = itertools.count(1)
        self.inited = self.ready = self.migrated = self.limits_applied = False
        self.limits_reason = "startup incomplete"
        self.mod = self.sdk = self.plugin = self.key = None
        self.startup_timeout = 10.0
        self.runtime = self.dirs = None

    def write(self, obj):
        data = fy_proto.encode(obj)
        with self.lock:
            return self.write_data(data)

    def write_data(self, data):
        try:
            view = memoryview(data)
            while view:
                written = os.write(self.out, view)
                if written <= 0:
                    leave(0)
                    return False
                view = view[written:]
        except OSError:
            leave(0)
            return False
        return True

    def reply(self, rid, result=None, err=None):
        msg = {"re": rid, "ok": err is None}
        msg["error" if err else "result"] = err or result
        try:
            self.write(msg)
        except fy_proto.ProtocolError:
            self.write(
                {"re": rid, "ok": False, "error": fy_proto.error("too_large", "reply too large")}
            )

    def term(self):
        if self.closing:
            return
        self.on_line(b'{"op":"unload","reason":"shutdown","id":0}\n')

    def die(self, code, message):
        try:
            self.write({"re": 0, "ok": False, "error": fy_proto.error(code, message)})
        finally:
            leave(3)

    def emit(self, name, payload=None):
        if not fy_proto.event_ok(name, payload):
            self.event_dropped()
            return False
        now = time.monotonic()
        with self.lock:
            if now - self.window >= 1.0:
                self.window, self.count = now, 0
            if self.count >= EMIT_RATE:
                self.dropped = min(DROP_MAX, self.dropped + 1)
                return False
            self.count += 1
            msg = {"ev": "emit", "name": name, "payload": payload}
            if self.dropped:
                msg["dropped"] = self.dropped
            if not self.write_data(fy_proto.encode(msg)):
                return False
            self.dropped = 0
            return True

    def event_dropped(self, count=1):
        if type(count) is int and count > 0:
            with self.lock:
                self.dropped = min(DROP_MAX, self.dropped + count)

    async def host(self, op, arg):
        hid = next(self.host_ids)
        fut = self.loop.create_future()
        self.waiting[hid] = fut
        try:
            try:
                self.write({"host": op, "id": hid, "arg": arg})
            except fy_proto.ProtocolError as e:
                raise RpcError("too_large", str(e))
            return await fut
        finally:
            self.waiting.pop(hid, None)

    def host_reply(self, m):
        hid = m.get("id")
        fut = self.waiting.get(hid) if type(hid) is int else None
        if fut is None or fut.done():
            return
        if m.get("ok"):
            fut.set_result(m.get("result"))
        else:
            e = m.get("error") if isinstance(m.get("error"), dict) else {}
            fut.set_exception(
                RpcError(
                    str(e.get("code") or "error"), str(e.get("message") or "host call failed")[:300]
                )
            )

    def on_line(self, line):
        try:
            m = fy_proto.decode(line)
        except fy_proto.ProtocolError as e:
            return self.die("too_large" if "large" in str(e) else "exception", str(e))
        op, rid = m.get("op"), m.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool):
            rid = 0
        if op == "host_reply":
            return self.host_reply(m)
        if op == "ping":
            return self.reply(rid, {"pong": True})
        if op == "cancel":
            target = m.get("target")
            task = self.calls.get(target) if type(target) is int else None
            cancelled = bool(task and task not in self.cancelled and task.cancel())
            if cancelled:
                self.cancelled.add(task)
            return self.reply(rid, {"cancelled": cancelled})
        if op not in OPS:
            return self.reply(rid, err=fy_proto.error("exception", "unknown op"))
        if self.closing:
            return self.reply(rid, err=fy_proto.error("not_ready", "backend is stopping"))
        if op == "call":
            if rid in self.calls or len(self.calls) >= CALLS_MAX:
                return self.reply(
                    rid,
                    err=fy_proto.error(
                        "busy",
                        "too many calls running"
                        if rid not in self.calls
                        else "call id already running",
                    ),
                )
        elif op == "unload":
            self.closing = True
            self.ready = False
        elif self.lifecycle is not None or self.hook_tasks:
            return self.reply(
                rid, err=fy_proto.error("busy", "backend lifecycle operation is running", stage=op)
            )
        task = self.loop.create_task(self.run(rid, getattr(self, "op_" + op), m))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        if op == "call":
            self.calls[rid] = task

            def call_done(done, r=rid):
                if self.calls.get(r) is done:
                    self.calls.pop(r, None)
                self.cancelled.discard(done)

            task.add_done_callback(call_done)
        elif op != "unload":
            self.lifecycle = task

            def lifecycle_done(done):
                if self.lifecycle is done:
                    self.lifecycle = None

            task.add_done_callback(lifecycle_done)

    async def run(self, rid, operation, message):
        err = None
        result = None
        try:
            result = await operation(rid, message)
        except asyncio.CancelledError:
            return
        except Fail as f:
            err = f.err
        except Exception as e:
            err = describe(e)
        if asyncio.current_task() not in self.cancelled:
            self.reply(rid, result, err)

    def init(self, m):
        if self.inited:
            return
        try:
            plugin, file, d = m["plugin"], m["file"], m["dirs"]
            if (
                not isinstance(plugin, str)
                or not PLUGIN_ID.match(plugin)
                or not isinstance(file, str)
            ):
                raise ValueError("bad startup message")
            limits = m.get("limits") if isinstance(m.get("limits"), dict) else {}
            self.startup_timeout = clamp(limits.get("startup_timeout"), 10.0, 1.0, 60.0)
            paths = {
                k: None if k == "runtime" and d[k] is None else pathlib.Path(d[k]) for k in DIR_KEYS
            }
            if not self.limits_configured:
                self.limits_applied, self.limits_reason = limit_data(
                    clamp(limits.get("memory_mb"), 32, 16, 64)
                )
                self.limits_configured = True
            store = self.store_module
            self.dirs = (
                store.Dirs(**paths) if store is not None and hasattr(store, "Dirs") else Box(paths)
            )
            meta = Box(m.get("meta") if isinstance(m.get("meta"), dict) else {})
            self.runtime = Box(m.get("runtime") if isinstance(m.get("runtime"), dict) else {})
            self.runtime.update(
                plugin=plugin, version=meta.get("version"), plugin_dir=pathlib.Path(file).parent
            )
            self.plugin, self.key = plugin, "fy_" + plugin
            if self.sdk_module is not None:
                self.sdk = self.sdk_module.build(plugin, meta, self.dirs, self.runtime, Rpc(self))
                sys.modules["framey_api"] = self.sdk
            self.mod = self.load(file)
        except (Exception, SystemExit) as e:
            raise Fail(describe(e, "import"))
        self.inited = True

    def load(self, file):
        spec = importlib.util.spec_from_file_location(self.key, file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[self.key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(self.key, None)
            raise
        return mod

    def context(self, **extra):
        sdk = self.sdk
        values = dict(
            plugin=self.plugin,
            runtime=getattr(sdk, "runtime", self.runtime),
            dirs=getattr(sdk, "dirs", self.dirs),
            settings=getattr(sdk, "settings", None),
            log=getattr(sdk, "log", None),
            from_version=None,
            to_version=None,
            data_from=None,
            data_to=None,
            purge=False,
            reason=None,
        )
        values.update(extra)
        return types.SimpleNamespace(**values)

    async def hook(self, name, arg, limit, stage):
        func = getattr(self.mod, name, None)
        if not callable(func):
            return False

        async def checked():
            try:
                return await invoke(func, arg)
            except (Exception, SystemExit) as e:
                raise Fail(describe(e, stage))

        task = self.loop.create_task(checked())
        self.tasks.add(task)
        self.hook_tasks.add(task)

        def hook_done(done):
            self.tasks.discard(done)
            self.hook_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(hook_done)
        try:
            done, _ = await asyncio.wait((task,), timeout=limit)
            if not done:
                task.cancel()
                raise failure("timeout", "%s hook timed out after %g s" % (name, limit), stage)
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise
        return True

    async def op_startup(self, rid, m):
        if self.ready:
            raise failure("stage", "already started", "startup")
        self.init(m)
        await self.hook("startup", self.context(), self.startup_timeout, "startup")
        self.ready = True
        hooks = [n for n in HOOKS if callable(getattr(self.mod, n, None))]
        return {
            "hooks": hooks,
            "migrated": self.migrated,
            "limits_applied": self.limits_applied,
            "limits_reason": self.limits_reason,
        }

    async def op_migrate(self, rid, m):
        if self.ready:
            raise failure("stage", "backend already started", "migrate")
        self.init(m)
        ctx = self.context(
            from_version=m.get("from_version"),
            to_version=m.get("to_version"),
            data_from=m.get("data_from"),
            data_to=m.get("data_to"),
        )
        ran = await self.hook("migrate", ctx, self.startup_timeout, "migrate")
        self.migrated = self.migrated or ran
        return {"migrated": ran}

    async def op_uninstall(self, rid, m):
        if not self.inited:
            raise failure("not_ready", "backend is not started")
        ran = await self.hook(
            "uninstall", self.context(purge=bool(m.get("purge"))), 10.0, "uninstall"
        )
        return {"ran": ran}

    async def op_call(self, rid, m):
        if not self.ready:
            raise failure("not_ready", "backend is not started")
        func = getattr(self.mod, "call", None)
        if not callable(func):
            raise failure("exception", "backend has no call()")
        try:
            result = func(m.get("method"), m.get("arg"))
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except SystemExit:
            raise failure("exit", "backend called exit")
        except Exception as e:
            raise Fail(describe(e))
        try:
            size = len(fy_proto.dumps(result))
        except fy_proto.ProtocolError:
            raise failure(
                "bad_result", "result of type %s is not JSON serialisable" % type(result).__name__
            )
        if size > fy_proto.PAYLOAD_MAX:
            raise failure(
                "too_large", "result is %d bytes; limit is %d" % (size, fy_proto.PAYLOAD_MAX)
            )
        return result

    async def op_unload(self, rid, m):
        self.closing = True
        self.ready = False
        reason = m.get("reason") if isinstance(m.get("reason"), str) else "reload"
        err = None
        others = [t for t in self.tasks if t is not asyncio.current_task()]
        self.cancelled.update(self.calls.values())
        for task in others:
            task.cancel()
        for fut in tuple(self.waiting.values()):
            fut.cancel()
        self.waiting.clear()
        if self.inited:
            try:
                await self.hook("unload", reason, 3.0, "unload")
            except Fail as f:
                err = f.err
        others = [t for t in self.tasks if t is not asyncio.current_task() and not t.done()]
        for task in others:
            task.cancel()
        if others:
            await asyncio.wait(others, timeout=1.0)
        for fut in tuple(self.waiting.values()):
            fut.cancel()
        self.waiting.clear()
        close = getattr(getattr(self.sdk, "log", None), "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self.reply(rid, {"unloaded": True}, err)
        leave(0)

    def read(self, fd):
        stream = os.fdopen(fd, "rb")
        try:
            while True:
                line = stream.readline(READ_MAX)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if len(line) >= READ_MAX:
                        self.die("too_large", "line too large")
                    break
                try:
                    self.loop.call_soon_threadsafe(self.on_line, line)
                except RuntimeError:
                    return
        except OSError:
            pass
        finally:
            stream.close()
        try:
            self.loop.call_soon_threadsafe(self.term)
        except RuntimeError:
            leave(0)


def main():
    rfd, wfd = os.dup(0), os.dup(1)
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)
    os.close(nul)
    os.dup2(2, 1)
    if os.name == "nt":
        import msvcrt

        for fd in (rfd, wfd):
            msvcrt.setmode(fd, os.O_BINARY)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:
            pass
    if hasattr(os, "nice"):
        try:
            os.nice(max(0, 19 - os.nice(0)))
        except OSError:
            pass
    limit_boot()
    safe, reason = privileges()
    if not safe:
        worker = Worker(None, wfd)
        worker.reply(0, err=fy_proto.error("exception", reason, stage="import"))
        leave(3)
        return
    sdk_module, store_module = optional("fy_sdk"), optional("fy_store")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    worker = Worker(loop, wfd, sdk_module, store_module)
    try:
        loop.add_signal_handler(signal.SIGTERM, worker.term)
    except (NotImplementedError, RuntimeError, ValueError):
        pass
    try:
        threading.stack_size(262144)
    except (ValueError, RuntimeError):
        pass
    reader = threading.Thread(target=worker.read, args=(rfd,), daemon=True)
    reader.start()
    try:
        threading.stack_size(0)
    except (ValueError, RuntimeError):
        pass
    loop.run_forever()


if __name__ == "__main__":
    main()
