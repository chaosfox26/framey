import asyncio
import base64
import ipaddress
import functools
import json
import math
import os
import re
import socket
import ssl
import tempfile
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

METHODS_OK = frozenset(("GET", "POST", "PUT", "DELETE", "HEAD"))
HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,40}")
HOST = re.compile(r"[A-Za-z0-9.:-]{1,253}")
DEST = re.compile(r"(runtime|data):([A-Za-z0-9._-]{1,64})")
PID = re.compile(r"[a-z0-9_-]{1,32}")
BAD_URL = re.compile(r"[\x00-\x20\x7f\\]")
BAD_VALUE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
TEXTY = re.compile(r"text/|json|xml|javascript|x-www-form-urlencoded", re.I)
DROP = frozenset(("host", "content-length", "connection", "transfer-encoding", "accept-encoding", "expect", "upgrade", "te", "keep-alive", "trailer"))
CROSS_HOST_KEEP = frozenset(("accept", "accept-language", "content-type", "user-agent", "range", "if-none-match", "if-modified-since"))
KEEP = ("content-type", "content-length", "etag", "last-modified", "retry-after")
REDIRECTS = frozenset((301, 302, 303, 307, 308))
CGNAT = ipaddress.ip_network("100.64.0.0/10")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
URL_MAX = 2048
BODY_MAX = 262144
INLINE_MAX = 524288
DEST_MAX = 20 * 1024 * 1024
QUOTA = 64 * 1024 * 1024
QUOTA_FILES = 20000
DNS_MAX = 5.0
RETRY_AFTER_MAX = 7 * 86400
ADDRESSES_MAX = 64
TEXT_JSON_MAX = 700000
HOPS_MAX = 5
PER_PLUGIN = 2
TOTAL = 4

_lock = threading.Lock()
_live = {}
_pool = _dns = None
_dns_slots = threading.BoundedSemaphore(TOTAL)
_saving = set()
_save_condition = threading.Condition()


class NetError(Exception):
    def __init__(self, code, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message[:300]
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.retry_after = min(RETRY_AFTER_MAX, max(0, retry_after)) if type(retry_after) is int else None


def retry_after_seconds(value, now=None):
    if not isinstance(value, str) or len(value) > 256 or not value.isascii() or BAD_VALUE.search(value):
        return 0
    value = value.strip(" \t")
    if value.isdecimal():
        return min(RETRY_AFTER_MAX, int(value))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return 0
        now = time.time() if now is None else now
        if type(now) not in (int, float) or not math.isfinite(now):
            return 0
        seconds = date.timestamp() - now
        return min(RETRY_AFTER_MAX, max(0, math.ceil(seconds)))
    except (TypeError, ValueError, OverflowError):
        return 0


class _Transfer:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancelled = False
        self.socks = []

    def check(self):
        if self.cancelled:
            raise NetError("cancelled", "The request was cancelled")

    def append(self, sock):
        with self.lock:
            if not self.cancelled:
                self.socks.append(sock)
                return
        _abort([sock])
        self.check()

    def abort(self):
        with self.lock:
            self.cancelled = True
            socks = self.socks[:]
            self.socks.clear()
        _abort(socks)

    def publish(self, part, final, deadline):
        with self.lock:
            self.check()
            _left(deadline)
            os.replace(part, final)


def _check(socks):
    if isinstance(socks, _Transfer):
        socks.check()


def is_public(ip):
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if a.version == 6:
        v4 = a.ipv4_mapped or a.sixtofour
        if v4 is None and a in NAT64:
            v4 = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
        if v4 is not None:
            return is_public(str(v4))
    if (a.version == 6 and a.is_site_local) or a.is_multicast or a.is_reserved or a.is_loopback or a.is_link_local or a.is_private or a.is_unspecified or a in CGNAT:
        return False
    return a.is_global


def _executor():
    global _pool
    with _lock:
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=TOTAL, thread_name_prefix="fy-net")
    return _pool


def _resolver():
    global _dns
    with _lock:
        if _dns is None:
            from concurrent.futures import ThreadPoolExecutor
            _dns = ThreadPoolExecutor(max_workers=TOTAL, thread_name_prefix="fy-dns")
    return _dns


def _acquire(pid):
    with _lock:
        if _live.get(pid, 0) >= PER_PLUGIN or sum(_live.values()) >= TOTAL:
            raise NetError("busy", "Too many downloads are running")
        _live[pid] = _live.get(pid, 0) + 1


def _release(pid):
    with _lock:
        n = _live.get(pid, 0) - 1
        if n > 0:
            _live[pid] = n
        else:
            _live.pop(pid, None)


def _left(deadline):
    t = deadline - time.monotonic()
    if t <= 0:
        raise NetError("timeout", "The server took too long")
    return t


def _target(url, allow_http):
    if not isinstance(url, str) or len(url) > URL_MAX or not url.isascii() or BAD_URL.search(url):
        raise NetError("bad_url", "That link is not valid")
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        raise NetError("bad_url", "That link is not valid")
    scheme = p.scheme.lower()
    if scheme != "https" and not (allow_http and scheme == "http"):
        raise NetError("scheme", "Only https links are allowed")
    host = p.hostname
    if not host or "@" in p.netloc or not HOST.fullmatch(host) or port == 0 or p.netloc.endswith(":"):
        raise NetError("bad_url", "That link is not valid")
    if ":" in host or p.netloc.startswith("["):
        try:
            if ipaddress.ip_address(host).version != 6:
                raise ValueError()
        except ValueError:
            raise NetError("bad_url", "That link is not valid")
    elif not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in (host[:-1] if host.endswith(".") else host).split(".")):
        raise NetError("bad_url", "That link is not valid")
    path = (p.path or "/") + ("?" + p.query if p.query else "")
    return scheme, host, port if port is not None else (443 if scheme == "https" else 80), path


def _gai(host, port):
    return [i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _lookup(host, port, resolver, deadline):
    deadline = min(deadline, time.monotonic() + DNS_MAX) if deadline is not None else time.monotonic() + DNS_MAX
    if not _dns_slots.acquire(timeout=_left(deadline)):
        raise NetError("timeout", "The server took too long")

    def lookup():
        ips = []
        for i, ip in enumerate((resolver or _gai)(host, port)):
            if i >= ADDRESSES_MAX:
                raise NetError("network", "Too many server addresses")
            if not isinstance(ip, str) or "%" in ip or not is_public(ip):
                raise NetError("blocked_address", "That address is not allowed")
            ip = str(ipaddress.ip_address(ip))
            if ip not in ips:
                ips.append(ip)
        return ips

    try:
        fut = _resolver().submit(lookup)
    except BaseException:
        _dns_slots.release()
        raise
    fut.add_done_callback(lambda _: _dns_slots.release())
    try:
        return fut.result(_left(deadline))
    except FutureTimeout:
        fut.cancel()
        raise NetError("timeout", "The server took too long")


def _resolve(host, port, resolver, deadline=None):
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            ips = list(dict.fromkeys(_lookup(host, port, resolver, deadline)))
        except NetError:
            raise
        except Exception:
            raise NetError("network", "Could not find that server")
    if not ips or not all(is_public(i) for i in ips):
        raise NetError("blocked_address", "That address is not allowed")
    return ips


def _open(scheme, host, ip, port, timeout, track=None):
    s = socket.create_connection((ip, port), timeout)
    if track:
        track(s)
    if scheme == "https":
        try:
            s = ssl.create_default_context().wrap_socket(s, server_hostname=host, do_handshake_on_connect=False)
            if track:
                track(s)
            s.do_handshake()
        except BaseException:
            s.close()
            raise
    return s


def _abort(socks):
    if isinstance(socks, _Transfer):
        socks.abort()
        return
    for s in list(socks):
        for step in (functools.partial(s.shutdown, socket.SHUT_RDWR), s.close):
            try:
                step()
            except (OSError, ValueError):
                pass


def _connect(scheme, host, port, ips, deadline, opener, socks):
    for ip in ips:
        _check(socks)
        try:
            s = opener(scheme, host, ip, port, _left(deadline)) if opener else _open(scheme, host, ip, port, _left(deadline), socks.append)
            if opener:
                socks.append(s)
            _left(deadline)
        except (socket.timeout, TimeoutError):
            raise NetError("timeout", "The server took too long")
        except ssl.SSLError:
            raise NetError("network", "The secure connection failed")
        except OSError:
            continue
        try:
            ok = is_public(s.getpeername()[0])
        except OSError:
            ok = False
        if not ok:
            s.close()
            raise NetError("blocked_address", "That address is not allowed")
        return s
    raise NetError("network", "Could not connect to that server")


def _read(r, sock, method, limit, deadline, fh):
    if method == "HEAD" or r.status in (204, 304) or r.status < 200:
        return b"", 0, False
    parts = []
    n = 0
    cut = False
    while not r.isclosed():
        sock.settimeout(_left(deadline))
        chunk = r.read1(min(65536, limit + 1 - n))
        if not chunk:
            break
        n += len(chunk)
        if n > limit:
            if fh is not None:
                raise NetError("too_large", "The download is larger than allowed")
            chunk = chunk[: len(chunk) - (n - limit)]
            n = limit
            cut = True
        if fh is not None:
            try:
                fh.write(chunk)
            except OSError:
                raise NetError("bad_dest", "Could not save the download")
        else:
            parts.append(chunk)
        if cut:
            break
    if not cut and r.length:
        raise NetError("network", "The connection closed early")
    return b"".join(parts), n, cut


async def sync_disk():
    if hasattr(os, "sync"):
        await asyncio.get_running_loop().run_in_executor(_executor(), os.sync)


def _used(base, skip, deadline=None, socks=None):
    total = 0
    n = 1
    pending = [base]
    while pending:
        _check(socks)
        if deadline is not None:
            _left(deadline)
        try:
            entries = os.scandir(pending.pop())
        except OSError:
            raise NetError("bad_dest", "Could not inspect the download folder")
        with entries:
            for entry in entries:
                _check(socks)
                if deadline is not None:
                    _left(deadline)
                if entry.path in skip:
                    continue
                n += 1
                if n > QUOTA_FILES:
                    raise NetError("quota", "The download folder is full")
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                except FileNotFoundError:
                    pass
    return total


def _save(r, sock, method, deadline, dest, socks=None):
    final = dest[0]
    key = str(final.parent.resolve())
    with _save_condition:
        while key in _saving:
            _check(socks)
            _save_condition.wait(min(0.1, _left(deadline)))
        _saving.add(key)
    part = None
    try:
        _check(socks)
        room = QUOTA - _used(final.parent, (str(final),), deadline, socks)
        if room <= 0:
            raise NetError("quota", "The download folder is full")
        fd, part = tempfile.mkstemp(prefix=".fy-download-", suffix=".part", dir=final.parent)
        with os.fdopen(fd, "wb") as fh:
            try:
                _, n, _ = _read(r, sock, method, min(DEST_MAX, room), deadline, fh)
            except (socket.timeout, TimeoutError):
                raise NetError("timeout", "The server took too long")
            except OSError:
                raise NetError("network", "The connection failed")
            except NetError as e:
                if e.code == "too_large" and room < DEST_MAX:
                    raise NetError("quota", "The download folder is full")
                raise
        if isinstance(socks, _Transfer):
            socks.publish(part, final, deadline)
        else:
            _left(deadline)
            os.replace(part, final)
        return n
    except OSError:
        raise NetError("bad_dest", "Could not save the download")
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass
        with _save_condition:
            _saving.discard(key)
            _save_condition.notify_all()


def _decode(data, cut):
    for k in range(4 if cut else 1):
        try:
            return data[: len(data) - k].decode("utf-8")
        except UnicodeDecodeError:
            pass
    return None


def _shape(r, data, n, cut, path):
    text = b64 = None
    if path is None:
        if not data:
            text = ""
        elif TEXTY.search(r.getheader("content-type") or ""):
            text = _decode(data, cut)
            if text is not None and len(json.dumps(text)) > TEXT_JSON_MAX:
                text = None
        if text is None:
            b64 = base64.b64encode(data).decode("ascii")
    headers = {}
    for k in KEEP:
        v = r.getheader(k)
        if v:
            headers[k] = v[:256]
    return {"status": r.status, "headers": headers, "text": text, "b64": b64, "bytes": n, "truncated": cut, "path": path}


def _run(url, method, headers, body, deadline, limit, dest, allow_http, resolver, opener, socks, binary=False):
    import http.client
    first = None
    hops = 0
    while True:
        _check(socks)
        _left(deadline)
        scheme, host, port, path = _target(url, allow_http)
        key = (scheme, host, port)
        first = first or key
        if key != first:
            headers = {k: v for k, v in headers.items() if k.lower() in CROSS_HOST_KEEP}
        sock = _connect(scheme, host, port, _resolve(host, port, resolver, deadline), deadline, opener, socks)
        conn = http.client.HTTPConnection(host, port)
        conn.sock = sock
        r = None
        try:
            sock.settimeout(_left(deadline))
            hostline = ("[%s]" % host if ":" in host else host) + ("" if port == (443 if scheme == "https" else 80) else ":%d" % port)
            conn.request(method, path, body=body, headers=dict(headers, Host=hostline))
            r = conn.getresponse()
            loc = r.getheader("location")
            if r.status in REDIRECTS and loc:
                hops += 1
                if hops > HOPS_MAX:
                    raise NetError("redirect_blocked", "Too many redirects")
                try:
                    loc = loc.strip(" \t")
                    if not loc or len(loc) > URL_MAX or not loc.isascii() or BAD_URL.search(loc):
                        raise NetError("bad_url", "That link is not valid")
                    nxt = urljoin(url, loc)
                    nt = _target(nxt, allow_http)
                except (NetError, ValueError):
                    raise NetError("redirect_blocked", "The redirect was not allowed")
                if r.status in (301, 302, 303):
                    if method != "HEAD":
                        method = "GET"
                    body = None
                    headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
                elif body is not None and nt[:3] != first:
                    raise NetError("redirect_blocked", "The redirect was not allowed")
                url = nxt
                continue
            if dest:
                n = _save(r, sock, method, deadline, dest, socks)
                return _shape(r, b"", n, False, str(dest[0]))
            if binary and r.status != 200:
                if r.status in (401, 403):
                    code, message = "access_denied", "The server denied access to the download"
                elif r.status == 429:
                    code, message = "rate_limited", "The server asked Framey to wait before downloading"
                else:
                    code, message = "network", "The server did not return a complete download"
                raise NetError(code, message, status=r.status, retry_after=retry_after_seconds(r.getheader("retry-after")))
            data, n, cut = _read(r, sock, method, limit, deadline, None)
            if binary:
                if cut:
                    raise NetError("too_large", "The download is larger than allowed")
                return data
            return _shape(r, data, n, cut, None)
        except NetError:
            raise
        except (socket.timeout, TimeoutError):
            raise NetError("timeout", "The server took too long")
        except (OSError, http.client.HTTPException):
            raise NetError("network", "The connection failed")
        finally:
            if r is not None:
                r.close()
            conn.close()
            sock.close()


def _dest(dirs, dest):
    m = DEST.fullmatch(dest) if isinstance(dest, str) else None
    base = getattr(dirs, m[1], None) if m and dirs else None
    if not base or set(m[2]) == {"."}:
        raise NetError("bad_dest", "That download location is not allowed")
    try:
        base = Path(base)
        final = base / m[2]
        if final.is_dir():
            raise NetError("bad_dest", "That download location is not allowed")
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (OSError, TypeError, ValueError):
        raise NetError("bad_dest", "That download location is not allowed")
    return final, base / (m[2] + ".part")


def _headers(headers):
    if headers is None:
        return {}
    if not isinstance(headers, dict) or len(headers) > 16:
        raise NetError("bad_request", "Too many or invalid headers")
    out = {}
    for k, v in headers.items():
        if not isinstance(k, str) or not HEADER_NAME.fullmatch(k) or not isinstance(v, str) or len(v) > 1024 or not v.isascii() or BAD_VALUE.search(v):
            raise NetError("bad_request", "A header is not valid")
        if k.lower() not in DROP:
            out[k] = v
    return out


def _body(body):
    if body is None:
        return None
    if isinstance(body, str):
        try:
            body = body.encode("utf-8")
        except UnicodeEncodeError:
            raise NetError("bad_request", "The request body is not valid")
    if not isinstance(body, (bytes, bytearray)):
        raise NetError("bad_request", "The request body is not valid")
    if len(body) > BODY_MAX:
        raise NetError("too_large", "The request body is too large")
    return bytes(body)


async def fetch_plugin(pid, url, *, method="GET", headers=None, body=None, timeout=15, max_bytes=INLINE_MAX, dest=None, dirs=None, allow_http=False, resolver=None, opener_factory=None):
    if not isinstance(pid, str) or not PID.fullmatch(pid):
        raise NetError("bad_request", "Unknown module")
    method = method.upper() if isinstance(method, str) else ""
    if method not in METHODS_OK:
        raise NetError("bad_request", "That request method is not allowed")
    _target(url, allow_http)
    headers = _headers(headers)
    body = _body(body)
    limit = max_bytes if isinstance(max_bytes, int) and not isinstance(max_bytes, bool) and 1 <= max_bytes <= INLINE_MAX else INLINE_MAX
    return await _perform(pid, url, method, headers, body, timeout, limit, dest, dirs, allow_http, resolver, opener_factory)


async def fetch_bytes(pid, url, *, headers=None, timeout=30, max_bytes=5_000_000):
    if not isinstance(pid, str) or not PID.fullmatch(pid):
        raise NetError("bad_request", "Unknown module")
    _target(url, False)
    if type(max_bytes) is not int or not 1 <= max_bytes <= DEST_MAX:
        raise NetError("bad_request", "The download size limit is not valid")
    return await _perform(pid, url, "GET", _headers(headers), None, timeout, max_bytes, None, None, False, None, None, binary=True)


async def _perform(pid, url, method, headers, body, timeout, limit, dest, dirs, allow_http, resolver, opener_factory, binary=False):
    try:
        timeout = float(timeout)
    except (TypeError, ValueError, OverflowError):
        timeout = math.nan
    timeout = min(max(timeout, 1.0), 60.0) if math.isfinite(timeout) else 15.0
    _acquire(pid)
    socks = _Transfer()
    deadline = time.monotonic() + timeout

    def work():
        socks.check()
        _left(deadline)
        dst = _dest(dirs, dest) if dest is not None else None
        return _run(url, method, headers, body, deadline, limit, dst, allow_http, resolver, opener_factory, socks, binary=binary)

    try:
        future = _executor().submit(work)
    except BaseException:
        _release(pid)
        raise
    future.add_done_callback(lambda _: _release(pid))
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda f: None if f.cancelled() else f.exception())

    async def result():
        await asyncio.wait((wrapped,))
        return wrapped.result()

    try:
        return await asyncio.wait_for(result(), timeout + 2)
    except asyncio.TimeoutError:
        raise NetError("timeout", "The server took too long")
    finally:
        _abort(socks)
        future.cancel()
        if not wrapped.done():
            try:
                await asyncio.wait((wrapped,), timeout=0.1)
            except asyncio.CancelledError:
                pass


def _dev(host):
    try:
        return host.settings()["prefs"]["dev"]["enabled"] is True
    except Exception:
        return False


async def hostcall(host, pid, arg):
    import fy_files

    a = arg if isinstance(arg, dict) else {}
    return await fetch_plugin(
        pid,
        a.get("url"),
        method=a.get("method", "GET"),
        headers=a.get("headers"),
        body=a.get("body"),
        timeout=a.get("timeout", 15),
        max_bytes=a.get("max_bytes", INLINE_MAX),
        dest=a.get("dest"),
        dirs=fy_files.plugin_dirs(host, pid),
        allow_http=_dev(host),
    )


async def fetch(host, arg):
    a = dict(arg) if isinstance(arg, dict) else {}
    a.pop("dest", None)
    try:
        return await hostcall(host, a.get("pid"), a)
    except NetError as e:
        return {"error": e.message, "code": e.code}


METHODS = {"fetch": fetch}
