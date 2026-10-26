import asyncio
import base64
import math
import os
import re
import stat
import time
from pathlib import Path

import fy_proto

TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "json": "application/json",
    "txt": "text/plain",
}
HEAD_MAX = 2048
READ_TIMEOUT = 10
CONN_MAX = 8
PER_PLUGIN = 4
CHUNK = 131072
TTL_MAX = 3600
HOST_DATA_MAX = (fy_proto.PAYLOAD_MAX - 512) * 3 // 4
TOKEN = re.compile(r"/m/([0-9a-f]{32})")
RANGE = re.compile(r"bytes=(\d*)-(\d*)")
REASONS = {
    200: "OK",
    206: "Partial Content",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    416: "Range Not Satisfiable",
    431: "Request Header Fields Too Large",
    503: "Service Unavailable",
}
COMMON = "X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nContent-Security-Policy: sandbox\r\nConnection: close\r\n"


class MediaError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message[:300]


def _open_file(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        parts = Path(path).parts
        parent = os.open(parts[0], flags | os.O_DIRECTORY)
        try:
            for part in parts[1:-1]:
                child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
                os.close(parent)
                parent = child
            fd = os.open(parts[-1], flags, dir_fd=parent)
        finally:
            os.close(parent)
    else:
        if os.path.realpath(path) != path:
            raise OSError("Resource path changed")
        fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.path.realpath(path) != path:
            raise OSError("Resource is not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _inspect(path, limit):
    with _open_file(path) as f:
        size = os.fstat(f.fileno()).st_size
        return size, f.read(limit + 1) if size <= limit else None


def _close_open(task):
    try:
        task.result().close()
    except BaseException:
        pass


def _close_read(task, f):
    try:
        task.result()
    except BaseException:
        pass
    finally:
        f.close()


async def _open(path):
    pending = asyncio.create_task(asyncio.to_thread(_open_file, path))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        pending.add_done_callback(_close_open)
        raise


def _chunk(f, n):
    return f.read(n)


class Media:
    def __init__(
        self, loop=None, *, max_tokens=16, default_ttl=600, data_max=1048576, clock=time.monotonic
    ):
        self._loop = loop
        self._max = max_tokens if type(max_tokens) is int and max_tokens > 0 else 16
        self._ttl = 600
        self._ttl = self._ttl_for(default_ttl)
        self._data_max = data_max
        self._clock = clock
        self._tokens = {}
        self._server = None
        self._port = 0
        self._handle = None
        self._conns = set()
        self._start_lock = None
        self._issue_lock = asyncio.Lock()
        self._generation = 0
        self._owners = {}
        self._streams = {}

    def _ttl_for(self, ttl):
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or (isinstance(ttl, float) and not math.isfinite(ttl))
            or ttl < 1
        ):
            return self._ttl
        return min(ttl, TTL_MAX)

    async def url_for(self, pid, path, *, ttl=None, data_max=None, _root=None):
        generation, owner = self._generation, self._owners.get(pid, 0)
        limit = self._data_max if data_max is None else data_max
        real = os.path.realpath(path)
        if _root is not None:
            base = os.fspath(_root)
            if os.path.commonpath([base, real]) != base:
                raise MediaError("outside_root", "That folder is outside the allowed locations")
        mime = TYPES.get(Path(real).suffix.lower().lstrip("."))
        if mime is None:
            raise MediaError("bad_type", "That file type is not allowed")
        try:
            _, data = await asyncio.to_thread(_inspect, real, limit)
        except OSError:
            raise MediaError("not_found", "That file cannot be read")
        ttl = self._ttl_for(ttl)
        async with self._issue_lock:
            if generation != self._generation or owner != self._owners.get(pid, 0):
                raise MediaError("cancelled", "That resource request was cancelled")
            if data is not None and len(data) <= limit:
                return {
                    "url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii")),
                    "kind": "data",
                    "expires": int(time.time() + ttl),
                    "token": None,
                }
            self._sweep()
            for token, e in self._tokens.items():
                if e[0] == pid and e[1] == real:
                    e[3] = self._clock() + ttl
                    self._arm()
                    return self._answer(token, int(time.time() + ttl))
            try:
                await self._start()
            except BaseException:
                if not self._tokens:
                    self._stop()
                raise
            if generation != self._generation or owner != self._owners.get(pid, 0):
                if not self._tokens:
                    self._stop()
                raise MediaError("cancelled", "That resource request was cancelled")
            mine = [t for t, e in self._tokens.items() if e[0] == pid]
            if len(mine) >= PER_PLUGIN:
                self._drop(mine[0])
            elif len(self._tokens) >= self._max:
                self._drop(next(iter(self._tokens)))
            import secrets

            token = secrets.token_hex(16)
            self._tokens[token] = [pid, real, mime, self._clock() + ttl]
            self._arm()
            return self._answer(token, int(time.time() + ttl))

    def _answer(self, token, expires):
        return {
            "url": "http://127.0.0.1:%d/m/%s" % (self._port, token),
            "kind": "http",
            "expires": expires,
            "token": token,
        }

    async def _start(self):
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._server is None:
                self._server = await asyncio.start_server(
                    self._serve, "127.0.0.1", 0, limit=HEAD_MAX
                )
                self._port = self._server.sockets[0].getsockname()[1]

    def _arm(self):
        if self._handle:
            self._handle.cancel()
            self._handle = None
        if self._tokens:
            loop = self._loop or asyncio.get_running_loop()
            self._handle = loop.call_later(
                max(0.0, min(e[3] for e in self._tokens.values()) - self._clock()), self._tick
            )

    def _tick(self):
        self._handle = None
        self._sweep()
        self._arm()

    def _sweep(self):
        now = self._clock()
        for t in [t for t, e in self._tokens.items() if e[3] <= now]:
            self._drop(t)
        if not self._tokens:
            self._stop()

    def _stop(self):
        if self._handle:
            self._handle.cancel()
            self._handle = None
        s, self._server = self._server, None
        if s:
            s.close()
            for w in list(self._conns):
                w.transport.abort()

    def _drop(self, token):
        self._tokens.pop(token, None)
        for writer, active in list(self._streams.items()):
            if active == token:
                writer.transport.abort()

    def revoke(self, token, pid=None):
        e = self._tokens.get(token) if isinstance(token, str) else None
        if e is None or (pid is not None and e[0] != pid):
            return False
        self._drop(token)
        if not self._tokens:
            self._stop()
        return True

    def revoke_plugin(self, pid):
        self._owners[pid] = self._owners.get(pid, 0) + 1
        gone = [t for t, e in self._tokens.items() if e[0] == pid]
        for t in gone:
            self._drop(t)
        if gone and not self._tokens:
            self._stop()
        return len(gone)

    async def close(self):
        self._generation += 1
        s = self._server
        self._tokens.clear()
        self._stop()
        if s:
            try:
                await asyncio.wait_for(s.wait_closed(), 1)
            except Exception:
                pass

    def _live(self, token):
        e = self._tokens.get(token)
        return e if e and e[3] > self._clock() else None

    async def _reply(self, writer, code, extra="", reader=None):
        writer.write(
            (
                "HTTP/1.1 %d %s\r\nContent-Length: 0\r\n%s%s\r\n"
                % (code, REASONS[code], extra, COMMON)
            ).encode("latin-1")
        )
        await asyncio.wait_for(writer.drain(), READ_TIMEOUT)
        if reader:
            await asyncio.wait_for(reader.read(65536), 1)

    async def _serve(self, reader, writer):
        if len(self._conns) >= CONN_MAX:
            try:
                await self._reply(writer, 503, reader=reader)
            except Exception:
                pass
            writer.close()
            return
        self._conns.add(writer)
        try:
            await self._handle_request(reader, writer)
        except Exception:
            pass
        finally:
            self._conns.discard(writer)
            self._streams.pop(writer, None)
            writer.close()

    async def _handle_request(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT)
        except asyncio.LimitOverrunError:
            return await self._reply(writer, 431, reader=reader)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
            return
        if len(head) > HEAD_MAX:
            return await self._reply(writer, 431)
        lines = head.decode("latin-1").split("\r\n")
        bits = lines[0].split(" ")
        if len(bits) != 3 or bits[2] not in ("HTTP/1.0", "HTTP/1.1"):
            return await self._reply(writer, 400)
        if bits[0] != "GET":
            return await self._reply(writer, 405)
        m = TOKEN.fullmatch(bits[1])
        entry = self._live(m[1]) if m else None
        if entry is None:
            return await self._reply(writer, 404)
        token = m[1]
        self._streams[writer] = token
        want = ""
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "range":
                want = value.strip()
        try:
            f = await _open(entry[1])
        except OSError:
            return await self._reply(writer, 404)
        pending = None
        try:
            if self._live(token) is not entry:
                return await self._reply(writer, 404)
            size = os.fstat(f.fileno()).st_size
            start, end, code = 0, size - 1, 200
            rm = RANGE.fullmatch(want)
            if rm and (rm[1] or rm[2]):
                if rm[1]:
                    start = int(rm[1])
                    end = min(int(rm[2]), size - 1) if rm[2] else size - 1
                else:
                    start = max(0, size - int(rm[2]))
                if start >= size or start > end or (not rm[1] and int(rm[2]) == 0):
                    return await self._reply(writer, 416, "Content-Range: bytes */%d\r\n" % size)
                code = 206
            length = end - start + 1 if size else 0
            extra = "Content-Type: %s\r\nContent-Length: %d\r\nAccept-Ranges: bytes\r\n" % (
                entry[2]
                + ("; charset=utf-8" if entry[2].startswith(("text/", "application/json")) else ""),
                length,
            )
            if code == 206:
                extra += "Content-Range: bytes %d-%d/%d\r\n" % (start, end, size)
            writer.write(
                ("HTTP/1.1 %d %s\r\n%s%s\r\n" % (code, REASONS[code], extra, COMMON)).encode(
                    "latin-1"
                )
            )
            f.seek(start)
            left = length
            while left > 0 and self._live(token):
                pending = asyncio.create_task(asyncio.to_thread(_chunk, f, min(CHUNK, left)))
                data = await asyncio.shield(pending)
                if not data or self._live(token) is not entry:
                    break
                writer.write(data)
                await asyncio.wait_for(writer.drain(), READ_TIMEOUT)
                left -= len(data)
        finally:
            if pending is not None and not pending.done():
                pending.add_done_callback(lambda task: _close_read(task, f))
            else:
                f.close()


_media = None


def get():
    global _media
    if _media is None:
        _media = Media()
    return _media


async def close():
    global _media
    m, _media = _media, None
    if m:
        await m.close()


async def hostcall(host, pid, arg, page=False):
    import fy_files

    a = arg if isinstance(arg, dict) else {}
    if a.get("revoke") is not None:
        if not isinstance(pid, str) or not fy_files.PID.fullmatch(pid):
            return {"ok": False}
        raw = a["revoke"] if isinstance(a["revoke"], str) else ""
        media = get()
        prefix = "http://127.0.0.1:%d" % media._port
        m = TOKEN.fullmatch(raw[len(prefix) :]) if raw.startswith(prefix + "/") else None
        return {"ok": media.revoke(m[1] if m else raw, pid)}
    dirs = fy_files.plugin_dirs(host, pid)
    own = {
        k: getattr(dirs, k)
        for k in (("data", "runtime") if page else ("data", "config", "runtime"))
        if dirs and getattr(dirs, k, None)
    }
    root = a.get("root")
    if not isinstance(root, str) or root not in own:
        raise MediaError("bad_root", "That location is not available")
    base = Path(os.path.realpath(own[root]))
    try:
        path = fy_files.safe_join(base, a.get("path", ""))
    except fy_files.FileError as e:
        raise MediaError(e.code, e.message)
    return await get().url_for(
        pid, path, ttl=a.get("ttl"), data_max=None if page else HOST_DATA_MAX, _root=base
    )


async def resource(host, arg):
    try:
        return await hostcall(host, arg.get("pid") if isinstance(arg, dict) else None, arg, True)
    except MediaError as e:
        return {"error": e.message, "code": e.code}


METHODS = {"resource": resource}
