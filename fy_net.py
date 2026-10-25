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
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
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
KEEP = ("content-type", "content-length", "etag", "last-modified")
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
TEXT_JSON_MAX = 700000
HOPS_MAX = 5
PER_PLUGIN = 2
TOTAL = 4

_lock = threading.Lock()
_live = {}
_pool = _dns = None


class NetError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message[:300]


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
    if _pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _pool = ThreadPoolExecutor(max_workers=TOTAL, thread_name_prefix="fy-net")
    return _pool


def _resolver():
    global _dns
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
    if not host or "@" in p.netloc or not HOST.fullmatch(host):
        raise NetError("bad_url", "That link is not valid")
    path = (p.path or "/") + ("?" + p.query if p.query else "")
    return scheme, host, port or (443 if scheme == "https" else 80), path


def _gai(host, port):
    return [i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _lookup(host, port, resolver, deadline):
    fut = _resolver().submit(resolver or _gai, host, port)
    try:
        return fut.result(DNS_MAX if deadline is None else min(DNS_MAX, _left(deadline)))
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
    for s in list(socks):
        for step in (functools.partial(s.shutdown, socket.SHUT_RDWR), s.close):
            try:
                step()
            except (OSError, ValueError):
                pass


def _connect(scheme, host, port, ips, deadline, opener, socks):
    for ip in ips:
        try:
            s = opener(scheme, host, ip, port, _left(deadline)) if opener else _open(scheme, host, ip, port, _left(deadline), socks.append)
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
            fh.write(chunk)
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


def _used(base, skip):
    total = n = 0
    for root, _, files in os.walk(base):
        for f in files:
            n += 1
            p = os.path.join(root, f)
            if n > QUOTA_FILES:
                raise NetError("quota", "The download folder is full")
            if p not in skip:
                try:
                    total += os.lstat(p).st_size
                except OSError:
                    pass
    return total


def _save(r, sock, method, deadline, dest):
    final, part = dest
    room = QUOTA - _used(final.parent, (str(final), str(part)))
    if room <= 0:
        raise NetError("quota", "The download folder is full")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    done = False
    try:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        with os.fdopen(os.open(part, flags, 0o600), "wb") as fh:
            try:
                _, n, _ = _read(r, sock, method, min(DEST_MAX, room), deadline, fh)
            except NetError as e:
                if e.code == "too_large" and room < DEST_MAX:
                    raise NetError("quota", "The download folder is full")
                raise
        os.replace(part, final)
        done = True
        return n
    finally:
        if not done:
            try:
                os.unlink(part)
            except OSError:
                pass


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


def _run(url, method, headers, body, deadline, limit, dest, allow_http, resolver, opener, socks):
    import http.client
    first = None
    hops = 0
    while True:
        _left(deadline)
        scheme, host, port, path = _target(url, allow_http)
        key = (scheme, host, port)
        first = first or key
        if key != first:
            headers = {k: v for k, v in headers.items() if k.lower() in CROSS_HOST_KEEP}
        sock = _connect(scheme, host, port, _resolve(host, port, resolver, deadline), deadline, opener, socks)
        socks.append(sock)
        conn = http.client.HTTPConnection(host, port)
        conn.sock = sock
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
                    nxt = urljoin(url, loc.strip())
                    nt = _target(nxt, allow_http)
                except NetError:
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
                n = _save(r, sock, method, deadline, dest)
                return _shape(r, b"", n, False, str(dest[0]))
            data, n, cut = _read(r, sock, method, limit, deadline, None)
            return _shape(r, data, n, cut, None)
        except NetError:
            raise
        except (socket.timeout, TimeoutError):
            raise NetError("timeout", "The server took too long")
        except (OSError, http.client.HTTPException):
            raise NetError("network", "The connection failed")
        finally:
            conn.close()
            sock.close()


def _dest(dirs, dest):
    m = DEST.fullmatch(dest) if isinstance(dest, str) else None
    base = getattr(dirs, m[1], None) if m and dirs else None
    if not base or set(m[2]) == {"."}:
        raise NetError("bad_dest", "That download location is not allowed")
    base = Path(base)
    final = base / m[2]
    if final.is_dir():
        raise NetError("bad_dest", "That download location is not allowed")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        body = body.encode("utf-8")
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
    try:
        timeout = float(timeout)
    except (TypeError, ValueError, OverflowError):
        timeout = math.nan
    timeout = min(max(timeout, 1.0), 60.0) if math.isfinite(timeout) else 15.0
    limit = max_bytes if isinstance(max_bytes, int) and not isinstance(max_bytes, bool) and 1 <= max_bytes <= INLINE_MAX else INLINE_MAX
    _acquire(pid)
    socks = []
    deadline = time.monotonic() + timeout
    try:
        loop = asyncio.get_running_loop()
        dst = await loop.run_in_executor(_executor(), _dest, dirs, dest) if dest is not None else None
        return await asyncio.wait_for(
            loop.run_in_executor(_executor(), _run, url, method, headers, body, deadline, limit, dst, allow_http, resolver, opener_factory, socks),
            timeout + 2,
        )
    except asyncio.TimeoutError:
        raise NetError("timeout", "The server took too long")
    finally:
        _abort(socks)
        _release(pid)


def _dev(host):
    try:
        return bool(host.settings()["prefs"]["dev"]["enabled"])
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
