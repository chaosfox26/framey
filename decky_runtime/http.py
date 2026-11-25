"""Serve immutable, hash-verified JavaScript snapshots on owned loopback URLs.

Issuance is core-only; HTTP requests cannot select filesystem paths or create resources.
"""

import asyncio
import hashlib
import math
import re
import secrets
import time
from types import MappingProxyType
from pathlib import Path

MAX_MODULES = 64
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_RESIDENT_BYTES = 16 * 1024 * 1024
MAX_PACKAGES = 16
CONNECTIONS = 8
HEADER_BYTES = 2048
READ_TIMEOUT = 0.5
TTL_MAX = 600
PATH = re.compile(r"dist/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.js\Z")
TARGET = re.compile(r"/g/([0-9a-f]{64})/(dist/[A-Za-z0-9_./-]+)\Z")
OWNER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    431: "Request Header Fields Too Large",
    503: "Service Unavailable",
}


class ModuleError(Exception):
    def __init__(self, code):
        super().__init__("Code module resource rejected")
        self.code = code


def canonical(path):
    return (
        isinstance(path, str)
        and len(path) <= 512
        and bool(PATH.fullmatch(path))
        and all(p not in ("", ".", "..") for p in path.split("/"))
    )


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or not 1 <= len(manifest) <= MAX_MODULES:
        raise ModuleError("bounds")
    for path, digest in manifest.items():
        if not canonical(path):
            raise ModuleError("path")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ModuleError("hash")


def _validate_ttl(ttl):
    if (
        isinstance(ttl, bool)
        or not isinstance(ttl, (int, float))
        or not 1 <= ttl <= TTL_MAX
        or not math.isfinite(ttl)
    ):
        raise ModuleError("ttl")


def ingest_verified(root, manifest):
    """Trusted caller supplies derived plugin root + provenance hashes, never HTTP input."""
    validate_manifest(manifest)
    if not isinstance(root, (str, Path)) or not Path(root).is_absolute():
        raise ModuleError("path")
    # The caller derives the root from verified provenance, never HTTP input.
    from fy_media import _open_file

    files = {}
    total = 0
    for path, digest in manifest.items():
        try:
            with _open_file(str(Path(root) / path)) as opened:
                value = opened.read(MAX_FILE_BYTES + 1)
        except OSError:
            raise ModuleError("file") from None
        if len(value) > MAX_FILE_BYTES:
            raise ModuleError("bounds")
        total += len(value)
        if total > MAX_PACKAGE_BYTES:
            raise ModuleError("bounds")
        if hashlib.sha256(value).hexdigest() != digest:
            raise ModuleError("hash")
        files[path] = value
    return files


class ModuleServer:
    def __init__(self, origin, clock=time.monotonic):
        if not isinstance(origin, str) or not re.fullmatch(
            r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", origin
        ):
            raise ModuleError("origin")
        self.origin = origin
        self.clock = clock
        self.port = 0
        self.closed = False
        self.server = None
        self._start_task = None
        self.packages = {}
        self.connections = set()
        self._handlers = set()
        self.streams = {}
        self.expiry = None
        self.read_timeout = READ_TIMEOUT
        self.request_count = 0
        self.error_count = 0

    async def start(self):
        if self.closed:
            raise ModuleError("closed")
        if self.server is not None:
            return
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._create_listener())
        pending = self._start_task
        try:
            await asyncio.shield(pending)
        finally:
            if pending.done() and self._start_task is pending:
                self._start_task = None
        if self.closed:
            raise ModuleError("closed")

    async def _create_listener(self):
        try:
            candidate = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=HEADER_BYTES)
        except asyncio.CancelledError:
            if self.closed:
                raise ModuleError("closed") from None
            raise
        # Cancellation may be delayed/suppressed by a resource factory; never publish
        # a listener after the owning service has closed.
        if self.closed:
            candidate.close()
            await candidate.wait_closed()
            raise ModuleError("closed")
        self.server = candidate
        self.port = candidate.sockets[0].getsockname()[1]

    def issue_bytes(self, owner, files, manifest, ttl=120):
        if self.closed:
            raise ModuleError("closed")
        if not isinstance(owner, str) or not OWNER.fullmatch(owner):
            raise ModuleError("owner")
        validate_manifest(manifest)
        if not isinstance(files, dict) or set(files) != set(manifest):
            raise ModuleError("manifest")
        _validate_ttl(ttl)
        verified = {}
        total = 0
        for path, digest in manifest.items():
            value = files[path]
            if type(value) is not bytes:
                raise ModuleError("bytes")
            total += len(value)
            if len(value) > MAX_FILE_BYTES or total > MAX_PACKAGE_BYTES:
                raise ModuleError("bounds")
            if hashlib.sha256(value).hexdigest() != digest:
                raise ModuleError("hash")
            verified[path] = value
        self._sweep()
        old = [token for token, p in self.packages.items() if p["owner"] == owner]
        resident = sum(p["size"] for token, p in self.packages.items() if token not in old)
        if len(self.packages) - len(old) >= MAX_PACKAGES or resident + total > MAX_RESIDENT_BYTES:
            raise ModuleError("bounds")
        for token in old:
            self._drop(token)
        token = secrets.token_hex(32)
        self.packages[token] = {
            "owner": owner,
            "files": MappingProxyType(verified),
            "hashes": MappingProxyType(dict(manifest)),
            "size": total,
            "expires": self.clock() + ttl,
            "served": {},
        }
        self._arm()
        return {
            "token": token,
            "prefix": "/g/" + token + "/",
            "root": "http://127.0.0.1:" + str(self.port) + "/g/" + token + "/",
        }

    def _live(self, token):
        p = self.packages.get(token)
        return p if p and p["expires"] > self.clock() else None

    def _drop(self, token):
        self.packages.pop(token, None)
        for writer, active in list(self.streams.items()):
            if active == token:
                writer.transport.abort()

    def _sweep(self):
        for token in [token for token, p in self.packages.items() if p["expires"] <= self.clock()]:
            self._drop(token)

    def _arm(self):
        if self.expiry:
            self.expiry.cancel()
            self.expiry = None
        if self.packages:
            self.expiry = asyncio.get_running_loop().call_later(
                max(0, min(p["expires"] for p in self.packages.values()) - self.clock()), self._tick
            )

    def _tick(self):
        self.expiry = None
        self._sweep()
        self._arm()

    def revoke(self, token, owner):
        p = self.packages.get(token)
        if not p or p["owner"] != owner:
            return False
        self._drop(token)
        self._arm()
        return True

    def renew(self, owner, token, ttl=TTL_MAX):
        """Extend only a live owned lease, retaining its token, URL and immutable bytes."""
        if self.closed:
            return False
        _validate_ttl(ttl)
        if (
            not isinstance(owner, str)
            or not OWNER.fullmatch(owner)
            or not isinstance(token, str)
            or not re.fullmatch("[0-9a-f]{64}", token)
        ):
            return False
        self._sweep()
        package = self._live(token)
        if package is None or package["owner"] != owner:
            self._arm()
            return False
        package["expires"] = self.clock() + ttl
        self._arm()
        return True

    def revoke_owner(self, owner):
        tokens = [token for token, p in self.packages.items() if p["owner"] == owner]
        for token in tokens:
            self._drop(token)
        self._arm()
        return len(tokens)

    def served_hashes(self, owner):
        return {
            path: digest
            for package in self.packages.values()
            if package["owner"] == owner
            for path, digest in package["served"].items()
        }

    def snapshot(self):
        return {
            "packages": len(self.packages),
            "residentBytes": sum(p["size"] for p in self.packages.values()),
            "connections": len(self.connections),
            "requests": self.request_count,
            "errors": self.error_count,
        }

    async def close(self):
        self.closed = True
        if self.expiry:
            self.expiry.cancel()
            self.expiry = None
        for token in list(self.packages):
            self._drop(token)
        for writer in list(self.connections):
            writer.transport.abort()
        pending = self._start_task
        if pending is not None:
            if not pending.done():
                pending.cancel()
            try:
                await asyncio.shield(pending)
            except (Exception, asyncio.CancelledError):
                pass
            if pending.done() and self._start_task is pending:
                self._start_task = None
        listener, self.server = self.server, None
        if listener:
            listener.close()
            await listener.wait_closed()
        handlers = [task for task in self._handlers if task is not asyncio.current_task()]
        for task in handlers:
            task.cancel()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)

    async def _reply(self, writer, code, body=b"", *, head=False, origin=False, digest=None):
        common = {
            "Content-Length": str(len(body)),
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
        }
        if code == 200:
            common["Content-Type"] = "text/javascript; charset=utf-8"
        if origin:
            common.update({"Access-Control-Allow-Origin": self.origin, "Vary": "Origin"})
        if digest:
            common["ETag"] = '"sha256-' + digest + '"'
        writer.write(
            (
                "HTTP/1.1 "
                + str(code)
                + " "
                + REASONS[code]
                + "\r\n"
                + "".join(k + ": " + v + "\r\n" for k, v in common.items())
                + "\r\n"
            ).encode("ascii")
        )
        if not head:
            for i in range(0, len(body), 65536):
                writer.write(body[i : i + 65536])
                await asyncio.wait_for(writer.drain(), self.read_timeout)
        await asyncio.wait_for(writer.drain(), self.read_timeout)

    async def _serve(self, reader, writer):
        if self.closed or len(self.connections) >= CONNECTIONS:
            # No reply/drain/read await or reject pool: overflow owns no live handler.
            writer.transport.abort()
            return
        self.connections.add(writer)
        handler = asyncio.current_task()
        if handler is not None:
            self._handlers.add(handler)
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.read_timeout)
            except asyncio.LimitOverrunError:
                return await self._reply(writer, 431)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return
            if len(head) > HEADER_BYTES:
                return await self._reply(writer, 431)
            lines = head.decode("latin1").split("\r\n")
            request = lines[0].split(" ")
            if len(request) != 3 or request[2] not in ("HTTP/1.0", "HTTP/1.1"):
                return await self._reply(writer, 400)
            method, target, _ = request
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                name, sep, value = line.partition(":")
                name = name.lower()
                if not sep or name in headers:
                    return await self._reply(writer, 400)
                headers[name] = value.strip()
            if headers.get("host") != "127.0.0.1:" + str(self.port):
                return await self._reply(writer, 400)
            if headers.get("origin") != self.origin:
                return await self._reply(writer, 403)
            if method not in ("GET", "HEAD"):
                return await self._reply(writer, 405, origin=True)
            match = TARGET.fullmatch(target)
            if not match or not canonical(match[2]):
                return await self._reply(writer, 404, origin=True)
            token, path = match.groups()
            package = self._live(token)
            if package is None or path not in package["files"]:
                return await self._reply(writer, 404, origin=True)
            self.request_count += 1
            package["served"][path] = package["hashes"][path]
            self.streams[writer] = token
            await self._reply(
                writer,
                200,
                package["files"][path],
                head=method == "HEAD",
                origin=True,
                digest=package["hashes"][path],
            )
        except Exception:
            self.error_count += 1
        finally:
            self.connections.discard(writer)
            self.streams.pop(writer, None)
            self._handlers.discard(handler)
            writer.close()
