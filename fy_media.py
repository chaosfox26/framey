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
REASONS = {200: "OK", 206: "Partial Content", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 416: "Range Not Satisfiable", 431: "Request Header Fields Too Large", 503: "Service Unavailable"}
COMMON = "X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nContent-Security-Policy: sandbox\r\nConnection: close\r\n"


class MediaError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message[:300]


def _slice(path, limit):
    with open(path, "rb") as f:
        return f.read(limit)


def _chunk(f, n):
    return f.read(n)


class Media:
    def __init__(self, loop=None, *, max_tokens=16, default_ttl=600, data_max=1048576, clock=time.monotonic):
        self._loop = loop
        self._max = max_tokens
        self._ttl = default_ttl
        self._data_max = data_max
        self._clock = clock
        self._tokens = {}
        self._server = None
        self._port = 0
        self._handle = None
        self._conns = set()
        self._start_lock = None

    def _ttl_for(self, ttl):
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or (isinstance(ttl, float) and not math.isfinite(ttl)) or ttl < 1:
            return self._ttl
        return min(ttl, TTL_MAX)

    async def url_for(self, pid, path, *, ttl=None, data_max=None):
        limit = self._data_max if data_max is None else data_max
        real = os.path.realpath(path)
        mime = TYPES.get(Path(real).suffix.lower().lstrip("."))
        if mime is None:
            raise MediaError("bad_type", "That file type is not allowed")
        try:
            st = os.stat(real)
        except OSError:
            raise MediaError("not_found", "That file was not found")
        if not stat.S_ISREG(st.st_mode):
            raise MediaError("not_found", "That is not a file")
        ttl = self._ttl_for(ttl)
        expires = int(time.time() + ttl)
        if st.st_size <= limit:
            try:
                data = await asyncio.to_thread(_slice, real, limit + 1)
            except OSError:
                raise MediaError("not_found", "That file cannot be read")
            if len(data) <= limit:
                return {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii")), "kind": "data", "expires": expires, "token": None}
        self._sweep()
        now = self._clock()
        for token, e in self._tokens.items():
            if e[0] == pid and e[1] == real:
                e[3] = now + ttl
                self._arm()
                return self._answer(token, expires)
        mine = [t for t, e in self._tokens.items() if e[0] == pid]
        if len(mine) >= PER_PLUGIN:
            del self._tokens[mine[0]]
        elif len(self._tokens) >= self._max:
            del self._tokens[next(iter(self._tokens))]
        await self._start()
        import secrets
        token = secrets.token_hex(16)
        self._tokens[token] = [pid, real, mime, now + ttl]
        self._arm()
        return self._answer(token, expires)

    def _answer(self, token, expires):
        return {"url": "http://127.0.0.1:%d/m/%s" % (self._port, token), "kind": "http", "expires": expires, "token": token}

    async def _start(self):
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._server is None:
                self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=HEAD_MAX)
                self._port = self._server.sockets[0].getsockname()[1]

    def _arm(self):
        if self._handle:
            self._handle.cancel()
            self._handle = None
        if self._tokens:
            loop = self._loop or asyncio.get_running_loop()
            self._handle = loop.call_later(max(0.0, min(e[3] for e in self._tokens.values()) - self._clock()), self._tick)

    def _tick(self):
        self._handle = None
        self._sweep()
        self._arm()

    def _sweep(self):
        now = self._clock()
        for t in [t for t, e in self._tokens.items() if e[3] <= now]:
            del self._tokens[t]
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

    def revoke(self, token, pid=None):
        e = self._tokens.get(token) if isinstance(token, str) else None
        if e is None or (pid is not None and e[0] != pid):
            return False
        del self._tokens[token]
        if not self._tokens:
            self._stop()
        return True

    def revoke_plugin(self, pid):
        gone = [t for t, e in self._tokens.items() if e[0] == pid]
        for t in gone:
            del self._tokens[t]
        if gone and not self._tokens:
            self._stop()
        return len(gone)

    async def close(self):
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
        writer.write(("HTTP/1.1 %d %s\r\nContent-Length: 0\r\n%s%s\r\n" % (code, REASONS[code], extra, COMMON)).encode("latin-1"))
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
            writer.close()

    async def _handle_request(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT)
        except asyncio.LimitOverrunError:
            return await self._reply(writer, 431, reader=reader)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
            return
        lines = head.decode("latin-1").split("\r\n")
        bits = lines[0].split(" ")
        if len(bits) != 3:
            return await self._reply(writer, 400)
        if bits[0] != "GET":
            return await self._reply(writer, 405)
        m = TOKEN.fullmatch(bits[1])
        entry = self._live(m[1]) if m else None
        if entry is None:
            return await self._reply(writer, 404)
        token = m[1]
        want = ""
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "range":
                want = value.strip()
        try:
            f = await asyncio.to_thread(open, entry[1], "rb")
        except OSError:
            return await self._reply(writer, 404)
        with f:
            if os.path.realpath(entry[1]) != entry[1]:
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
            extra = "Content-Type: %s\r\nContent-Length: %d\r\nAccept-Ranges: bytes\r\n" % (entry[2] + ("; charset=utf-8" if entry[2].startswith(("text/", "application/json")) else ""), length)
            if code == 206:
                extra += "Content-Range: bytes %d-%d/%d\r\n" % (start, end, size)
            writer.write(("HTTP/1.1 %d %s\r\n%s%s\r\n" % (code, REASONS[code], extra, COMMON)).encode("latin-1"))
            f.seek(start)
            left = length
            while left > 0 and self._live(token):
                data = await asyncio.to_thread(_chunk, f, min(CHUNK, left))
                if not data:
                    break
                writer.write(data)
                await asyncio.wait_for(writer.drain(), READ_TIMEOUT)
                left -= len(data)


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
        raw = a["revoke"] if isinstance(a["revoke"], str) else ""
        m = TOKEN.search(raw)
        return {"ok": get().revoke(m[1] if m else raw, pid)}
    dirs = fy_files.plugin_dirs(host, pid)
    own = {k: getattr(dirs, k) for k in (("data", "runtime") if page else ("data", "config", "runtime")) if dirs and getattr(dirs, k, None)}
    root = a.get("root")
    if not isinstance(root, str) or root not in own:
        raise MediaError("bad_root", "That location is not available")
    try:
        path = fy_files.safe_join(Path(own[root]), a.get("path", ""))
    except fy_files.FileError as e:
        raise MediaError(e.code, e.message)
    return await get().url_for(pid, path, ttl=a.get("ttl"), data_max=None if page else HOST_DATA_MAX)


async def resource(host, arg):
    try:
        return await hostcall(host, arg.get("pid") if isinstance(arg, dict) else None, arg, True)
    except MediaError as e:
        return {"error": e.message, "code": e.code}


METHODS = {"resource": resource}
