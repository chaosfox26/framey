import asyncio
import ipaddress
import json
import os
import re
import struct
import sys

IN_CLOSE_WRITE = 0x8
IN_MOVED_TO = 0x80
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_Q_OVERFLOW = 0x4000
IN_IGNORED = 0x8000
IN_ISDIR = 0x40000000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
WATCH_MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE
HEADER = struct.Struct("iIII")
READ_MAX = 8192
SKIP_PREFIX = (".stage-", ".old-", ".git")
SKIP_SUFFIX = (".swp", "~")
PID = re.compile(r"[a-z0-9_-]{1,32}\Z")
TARGET_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
MAX_ACTIVE = 8
ENTRIES_MAX = 2000
TARGETS_MAX = 32
JSON_MAX = 1048576
PROC_MAX = 1048576
ADDRESSES_MAX = 16
CDP_PORT = 8080
TIMEOUT = 3.0
MAIN_MARK = "vrOverlayKey=valve.steam.gamepadui.main"
BAR_END = "vrOverlayKey=valve.steam.gamepadui.bar"
HINT = "From a PC run: ssh -L {port}:127.0.0.1:{port} steamos@<headset>, then open http://127.0.0.1:{port} in a Chromium based browser. Opening a browser inside the headset is not verified."


class DevError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message[:300]


def _nan(name):
    raise ValueError(name)


def ignored(name):
    return name.startswith(SKIP_PREFIX) or name == "__pycache__" or name.endswith(SKIP_SUFFIX)


def walk(root, max_dirs, max_files=0):
    dirs, files, cut, todo = [], {}, False, [root]
    while todo:
        top = todo.pop()
        dirs.append(top)
        try:
            with os.scandir(top) as it:
                for seen, e in enumerate(it):
                    if seen >= ENTRIES_MAX:
                        cut = True
                        break
                    if ignored(e.name):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if len(dirs) + len(todo) >= max_dirs:
                            cut = True
                        else:
                            todo.append(e.path)
                    elif max_files and e.is_file(follow_symlinks=False):
                        if len(files) >= max_files:
                            cut = True
                        else:
                            st = e.stat(follow_symlinks=False)
                            files[os.path.relpath(e.path, root)] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return dirs, files, cut


def load_libc():
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_add_watch.restype = ctypes.c_int
        libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        libc.inotify_rm_watch.restype = ctypes.c_int
        return libc
    except (ImportError, OSError, AttributeError, TypeError):
        return None


def dev_prefs(host):
    try:
        dev = (host.settings().get("prefs") or {}).get("dev")
    except Exception:
        dev = None
    return dev if isinstance(dev, dict) else {}


class HotReload:
    def __init__(self, host, reload_one, *, debounce=0.4, ttl=3600, inotify=True, max_dirs=32, max_files=200, interval=2.0, libc=None):
        self.host = host
        self.reload_one = reload_one
        self.debounce = debounce
        self.ttl = ttl
        self.interval = interval
        self.max_dirs = max_dirs
        self.max_files = max_files
        self._want_inotify = inotify
        self._libc = libc
        self._fd = None
        self._w = {}
        self._wds = {}

    def active(self):
        return sorted(self._w)

    def status(self):
        now = self.host.mono()
        return [{"id": p, "mode": w["mode"], "truncated": w["cut"], "remaining_s": max(0, int(w["until"] - now))} for p, w in sorted(self._w.items())]

    def enable(self, pid, ttl=None):
        if not isinstance(pid, str) or not PID.match(pid):
            raise DevError("bad_plugin", "Missing or invalid plugin id")
        if dev_prefs(self.host).get("enabled") is not True:
            raise DevError("dev_off", "Developer mode is off")
        root = self.host.real(pid)
        if root is None or not os.path.isdir(root):
            raise DevError("not_found", "Module folder not found: " + pid)
        self._drop(pid)
        if len(self._w) >= MAX_ACTIVE:
            raise DevError("too_many", "Hot reload is limited to %d modules at once" % MAX_ACTIVE)
        w = {"root": str(root), "mode": "", "cut": False, "wds": [], "ttl": None, "bounce": None, "tick": None, "task": None, "snap": None, "until": 0.0}
        self._w[pid] = w
        if not (self._want_inotify and self._watch(pid, w)):
            self._poll(pid, w)
        self._arm_ttl(pid, w, ttl)
        self._persist()
        return {"mode": w["mode"], "truncated": w["cut"]}

    def disable(self, pid):
        if pid in self._w:
            self._drop(pid)
            self._persist()

    def disable_all(self):
        for pid in list(self._w):
            self._drop(pid)

    def resume(self):
        dev = dev_prefs(self.host)
        until = dev.get("hot_until")
        hot = dev.get("hot")
        if dev.get("enabled") is not True or not isinstance(hot, list) or isinstance(until, bool) or not isinstance(until, (int, float)):
            return []
        left = until - self.host.now()
        if left <= 0:
            return []
        out = []
        for pid in list(hot)[:MAX_ACTIVE]:
            try:
                self.enable(pid, min(left, self.ttl))
                out.append(pid)
            except DevError:
                pass
        return out

    def _persist(self):
        try:
            s = self.host.settings()
            dev = s.setdefault("prefs", {}).setdefault("dev", {})
            dev["hot"] = sorted(self._w)
            dev["hot_until"] = int(self.host.now() + max(w["until"] for w in self._w.values()) - self.host.mono()) if self._w else 0
            self.host.save(s)
        except Exception as e:
            self.host.log("hot reload", "could not save state:", e)

    def _arm_ttl(self, pid, w, ttl=None):
        ttl = self.ttl if ttl is None else ttl
        if w["ttl"] is not None:
            w["ttl"].cancel()
        w["until"] = self.host.mono() + ttl
        w["ttl"] = self.host.loop().call_later(ttl, self._expire, pid)

    def _expire(self, pid):
        w = self._w.get(pid)
        if w is not None:
            w["ttl"] = None
            self.disable(pid)

    def _drop(self, pid):
        w = self._w.pop(pid, None)
        if w is None:
            return
        for key in ("ttl", "bounce", "tick"):
            if w[key] is not None:
                w[key].cancel()
        for wd in w["wds"]:
            self._wds.pop(wd, None)
            try:
                self._libc.inotify_rm_watch(self._fd, wd)
            except Exception:
                pass
        if self._fd is not None and not self._wds:
            self._close_fd()

    def _close_fd(self):
        fd, self._fd = self._fd, None
        try:
            self.host.loop().remove_reader(fd)
        except Exception:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _open_fd(self):
        if self._fd is not None:
            return True
        libc = self._libc or load_libc()
        if libc is None:
            return False
        try:
            fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
            if fd < 0:
                return False
        except Exception:
            return False
        try:
            self.host.loop().add_reader(fd, self._read)
        except Exception:
            os.close(fd)
            return False
        self._libc, self._fd = libc, fd
        return True

    def _add_dir(self, pid, w, path):
        try:
            wd = self._libc.inotify_add_watch(self._fd, os.fsencode(path), WATCH_MASK)
        except Exception:
            return False
        if wd < 0:
            return False
        if wd not in self._wds:
            self._wds[wd] = (pid, path)
            w["wds"].append(wd)
        return True

    def _watch(self, pid, w):
        if not self._open_fd():
            return False
        dirs, _, cut = walk(w["root"], self.max_dirs)
        if not all(self._add_dir(pid, w, d) for d in dirs):
            for wd in w["wds"]:
                self._wds.pop(wd, None)
                try:
                    self._libc.inotify_rm_watch(self._fd, wd)
                except Exception:
                    pass
            w["wds"] = []
            if not self._wds:
                self._close_fd()
            return False
        w["mode"], w["cut"] = "inotify", cut
        return True

    def _read(self):
        try:
            buf = os.read(self._fd, READ_MAX)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            self.host.log("hot reload", "read failed:", e)
            self.disable_all()
            return
        off, hit = 0, set()
        while off + HEADER.size <= len(buf):
            wd, mask, _, n = HEADER.unpack_from(buf, off)
            raw = buf[off + HEADER.size:off + HEADER.size + n]
            off += HEADER.size + n
            if mask & IN_Q_OVERFLOW:
                hit.update(self._w)
                continue
            owner = self._wds.get(wd)
            if owner is None:
                continue
            if mask & IN_IGNORED:
                del self._wds[wd]
                continue
            pid, base = owner
            name = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            if not name or ignored(name):
                continue
            w = self._w.get(pid)
            if w is None:
                continue
            if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO) and len(w["wds"]) < self.max_dirs:
                self._add_dir(pid, w, os.path.join(base, name))
            hit.add(pid)
        for pid in hit:
            self._changed(pid)

    def _poll(self, pid, w):
        w["mode"] = "mtime"
        _, w["snap"], w["cut"] = walk(w["root"], self.max_dirs, self.max_files)
        w["tick"] = self.host.loop().call_later(self.interval, self._tick, pid)

    def _tick(self, pid):
        w = self._w.get(pid)
        if w is None:
            return
        _, snap, w["cut"] = walk(w["root"], self.max_dirs, self.max_files)
        if snap != w["snap"]:
            w["snap"] = snap
            self._changed(pid)
        w["tick"] = self.host.loop().call_later(self.interval, self._tick, pid)

    def _changed(self, pid):
        w = self._w.get(pid)
        if w is None:
            return
        self._arm_ttl(pid, w)
        if w["bounce"] is not None:
            w["bounce"].cancel()
        w["bounce"] = self.host.loop().call_later(self.debounce, self._fire, pid)

    def _fire(self, pid):
        w = self._w.get(pid)
        if w is None:
            return
        loop = self.host.loop()
        if w["task"] is not None:
            w["bounce"] = loop.call_later(self.debounce, self._fire, pid)
            return
        w["bounce"] = None
        w["task"] = loop.create_task(self._reload(pid))

    async def _reload(self, pid):
        try:
            await self.reload_one(pid)
        except Exception as e:
            self.host.log("hot reload", pid, "failed:", e)
        finally:
            w = self._w.get(pid)
            if w is not None:
                w["task"] = None


def get(host):
    hot = getattr(host, "hot", None)
    if hot is None:
        hot = host.hot = HotReload(host, host.reload_one)
    return hot


def shutdown(host):
    hot = getattr(host, "hot", None)
    if hot is not None:
        hot.disable_all()


def decode_address(text):
    raw = bytes.fromhex(text)
    if len(raw) == 4:
        return ipaddress.IPv4Address(raw[::-1])
    if len(raw) == 16:
        return ipaddress.IPv6Address(b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))
    raise ValueError("address length")


def is_loopback(ip):
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def listeners(text, port):
    found = []
    for line in str(text).splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":
            continue
        try:
            host_hex, port_hex = parts[1].rsplit(":", 1)
            if int(port_hex, 16) == port:
                found.append(decode_address(host_hex))
        except ValueError:
            continue
    return found


def remote_exposure(text_tcp, text_tcp6, port=CDP_PORT):
    found = listeners(text_tcp, port) + listeners(text_tcp6, port)
    names = sorted({"[%s]:%d" % (ip, port) if ip.version == 6 else "%s:%d" % (ip, port) for ip in found})
    if not found:
        state = "closed"
    elif all(is_loopback(ip) for ip in found):
        state = "loopback"
    else:
        state = "exposed"
    return {"state": state, "addresses": names[:ADDRESSES_MAX]}


def exposure_now(base="/proc/net", port=CDP_PORT):
    texts = []
    for name in ("tcp", "tcp6"):
        try:
            with open(base + "/" + name, "r", encoding="ascii", errors="replace") as f:
                texts.append(f.read(PROC_MAX))
        except OSError:
            texts.append(None)
    if texts == [None, None]:
        return {"state": "unknown", "addresses": []}
    return remote_exposure(texts[0] or "", texts[1] or "", port)


async def fetch_json(port):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    try:
        w.write(("GET /json HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % port).encode())
        await w.drain()
        raw = b""
        while len(raw) <= JSON_MAX:
            chunk = await r.read(65536)
            if not chunk:
                break
            raw += chunk
    finally:
        w.close()
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep or len(raw) > JSON_MAX or b" 200" not in head.split(b"\r\n", 1)[0]:
        raise ValueError("reply")
    return json.loads(body.decode("utf-8"), parse_constant=_nan)


def target_name(url, table):
    if MAIN_MARK in url:
        return "main"
    if url.endswith(BAR_END):
        return "bar"
    for name, part in table.items():
        if isinstance(part, str) and part and part in url:
            return str(name)[:24]
    return ""


def list_targets(items, port, table):
    out = []
    for t in items[:TARGETS_MAX] if isinstance(items, list) else []:
        tid = t.get("id") if isinstance(t, dict) else None
        if not isinstance(tid, str) or not TARGET_ID.match(tid):
            continue
        url = str(t.get("url") or "")
        out.append({
            "name": target_name(url, table),
            "title": str(t.get("title") or "")[:120],
            "url": url[:300],
            "inspector": "http://127.0.0.1:%d/devtools/inspector.html?ws=127.0.0.1:%d/devtools/page/%s" % (port, port, tid),
        })
    return out


async def devtools_info(host, arg=None, *, port=CDP_PORT):
    out = {"available": False, "port": port, "targets": [], "error": None, "hint": HINT.format(port=port)}
    table = dev_prefs(host).get("targets")
    try:
        items = await asyncio.wait_for(fetch_json(port), TIMEOUT)
        out["targets"] = list_targets(items, port, table if isinstance(table, dict) else {})
        out["available"] = True
    except asyncio.TimeoutError:
        out["error"] = "Port %d did not answer within %g s." % (port, TIMEOUT)
    except ConnectionRefusedError:
        out["error"] = "Nothing is listening on port %d. Steam's debugger is closed or Steam is not running." % port
    except ValueError:
        out["error"] = "Port %d answered, but not with a list of debug targets." % port
    except OSError as e:
        out["error"] = ("Could not reach port %d: %s" % (port, e.strerror or type(e).__name__))[:300]
    return out


def status(host):
    hot = getattr(host, "hot", None)
    return {
        "enabled": dev_prefs(host).get("enabled") is True,
        "hot": hot.status() if hot is not None else [],
        "exposure": exposure_now(),
    }


async def dev_status(host, arg=None):
    return status(host)


async def dev_set(host, arg):
    a = arg if isinstance(arg, dict) else {}
    pid, on = a.get("id"), a.get("hot")
    if not isinstance(on, bool) or not (pid is None or isinstance(pid, str)) or (on and pid is None):
        return {"error": "Expected {id, hot: true|false}", "code": "bad_arg"}
    try:
        if on:
            get(host).enable(pid)
        else:
            hot = getattr(host, "hot", None)
            if hot is not None:
                for p in [pid] if pid is not None else hot.active():
                    hot.disable(p)
    except DevError as e:
        return {"error": e.message, "code": e.code}
    return status(host)


METHODS = {"dev_set": dev_set, "dev_status": dev_status, "devtools_info": devtools_info}
