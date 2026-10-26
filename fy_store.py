import collections
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import tempfile
import time
from pathlib import Path

PLUGIN_ID = re.compile(r"[a-z0-9_-]{1,32}")
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")
MARKER = re.compile(r"\.?[A-Za-z0-9_-][A-Za-z0-9._-]{0,62}")
ANCHOR = re.compile(r"framey(-[0-9]+)?")
SETTINGS_MAX = 262144
LOG_MAX = 262144
LOCALE_MAX = 65536
LOCALE_KEYS = 2000
LINE_MAX = 2048
KEY_MAX = 64
KEYS_MAX = 200

_LOCK = threading.RLock()


Dirs = collections.namedtuple("Dirs", "config data runtime logs")


def runtime_root(run="/run/user", uid=None):
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base or not hasattr(os, "getuid"):
        return base or None
    uid = os.getuid() if uid is None else uid
    p = os.path.join(run, str(uid))
    try:
        st = os.lstat(p)
    except OSError:
        return None
    return (
        p
        if stat.S_ISDIR(st.st_mode) and st.st_uid == uid and stat.S_IMODE(st.st_mode) == 0o700
        else None
    )


def dirs(pid, home=None, runtime_base=None):
    if not isinstance(pid, str) or not PLUGIN_ID.fullmatch(pid):
        raise ValueError("bad plugin id")
    home = Path(home) if home is not None else Path.home()
    base = runtime_base if runtime_base is not None else runtime_root()
    runtime = Path(base) / "framey" / pid if base else None
    return Dirs(
        home / ".config" / "framey" / "plugins" / pid,
        home / ".local" / "share" / "framey" / pid,
        runtime,
        home / ".local" / "state" / "framey" / pid,
    )


def _anchored(p):
    for q in (p, p.parent, p.parent.parent):
        if ANCHOR.fullmatch(q.name):
            return q
    return None


def _verify(p):
    top = _anchored(p)
    if top is None or os.name != "posix":
        return
    q = p
    while True:
        if os.path.lexists(q):
            st = os.lstat(q)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
                raise PermissionError("unsafe directory")
        if q == top:
            return
        q = q.parent


def _constant(name):
    raise ValueError(name)


def ensure(path):
    if path is None:
        raise RuntimeError("runtime directory unavailable: XDG_RUNTIME_DIR is not set")
    p = Path(path)
    _verify(p)
    missing = []
    q = p
    while not q.exists() and q != q.parent:
        missing.append(q)
        q = q.parent
    for m in reversed(missing):
        _verify(m.parent)
        try:
            m.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _verify(m)
    _verify(p)
    if not p.is_dir():
        raise NotADirectoryError("not a directory")
    return p


def read_json(path, default=None, max_bytes=SETTINGS_MAX):
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return default
        return json.loads(raw.decode("utf-8"), parse_constant=_constant)
    except (OSError, ValueError, RecursionError):
        return default


def keep_bad(path, raw):
    bad = Path(str(path) + ".bad")
    try:
        fd = os.open(bad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        if not bad.is_file() or bad.is_symlink():
            raise OSError(errno.EEXIST, "invalid settings recovery copy")
        fd = os.open(bad, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise OSError(errno.EINVAL, "invalid settings recovery copy")
            if f.read(len(raw) + 1) != raw:
                raise OSError(errno.EEXIST, "settings recovery copy already exists")
            os.fsync(f.fileno())
    _sync_dir(bad.parent, strict=True)


def _sync_dir(d, strict=False):
    if os.name != "posix":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        if strict:
            raise


def write_json_atomic(path, obj, max_bytes=SETTINGS_MAX):
    try:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, RecursionError) as e:
        raise ValueError("value is not serialisable") from e
    if len(data) > max_bytes:
        raise ValueError("data too large")
    path = Path(path)
    ensure(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _sync_dir(path.parent)


def _key(k):
    if not isinstance(k, str) or not 1 <= len(k) <= KEY_MAX:
        raise ValueError("bad key")
    return k


class Settings:
    def __init__(self, path, max_bytes=SETTINGS_MAX):
        self.path = Path(path)
        self.max_bytes = max_bytes

    def _load(self):
        d = read_json(self.path, {}, self.max_bytes)
        return d if isinstance(d, dict) else {}

    def _strict(self):
        try:
            with open(self.path, "rb") as f:
                raw = f.read(self.max_bytes + 1)
        except FileNotFoundError:
            return {}
        if len(raw) > self.max_bytes:
            raise ValueError("settings file too large")
        try:
            d = json.loads(raw.decode("utf-8"), parse_constant=_constant)
        except (ValueError, RecursionError):
            d = None
        if not isinstance(d, dict):
            keep_bad(self.path, raw)
            return {}
        return d

    def _edit(self, fn):
        with _LOCK:
            data = self._strict()
            if fn(data) is False:
                return
            if len(data) > KEYS_MAX:
                raise ValueError("too many keys")
            write_json_atomic(self.path, data, self.max_bytes)

    def get(self, key, default=None):
        with _LOCK:
            return self._load().get(key, default)

    def set(self, key, value):
        _key(key)
        self._edit(lambda d: d.__setitem__(key, value))

    def update(self, mapping):
        items = dict(mapping)
        for k in items:
            _key(k)
        self._edit(lambda d: d.update(items))

    def delete(self, key):
        def fn(d):
            if key not in d:
                return False
            del d[key]

        self._edit(fn)

    def all(self):
        with _LOCK:
            return self._load()

    def reset(self):
        self._edit(lambda d: d.clear())


class Log:
    def __init__(self, pid, logs_dir, stream=None, max_bytes=LOG_MAX, keep=3, per_second=20):
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("bad log size")
        self.pid = pid
        self.dir = Path(logs_dir)
        self.stream = stream
        self.max_bytes = max_bytes
        self.keep = max(1, keep)
        self.per_second = per_second
        self.dropped = 0
        self._lock = threading.Lock()
        self._clock = time.monotonic
        self._start = None
        self._count = 0
        self._closed = False

    def debug(self, msg, *args):
        self._emit("DEBUG", msg, args)

    def info(self, msg, *args):
        self._emit("INFO", msg, args)

    def warning(self, msg, *args):
        self._emit("WARNING", msg, args)

    def error(self, msg, *args):
        self._emit("ERROR", msg, args)

    def close(self):
        with self._lock:
            self._closed = True

    def _emit(self, level, msg, args):
        try:
            text = str(msg) % args if args else str(msg)
        except Exception:
            text = repr(msg) + " " + repr(args)
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            if self._start is None or not 0 <= now - self._start < 1.0:
                self._start = now
                self._count = 0
            if self._count >= self.per_second:
                self.dropped += 1
                return
            self._count += 1
            from datetime import datetime, timezone

            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write(("%s %s %s" % (stamp, level, text))[:LINE_MAX])
            if self.stream is not None and level in ("WARNING", "ERROR"):
                try:
                    self.stream.write(("[%s] %s" % (self.pid, text))[:LINE_MAX] + "\n")
                    self.stream.flush()
                except Exception:
                    pass

    def _write(self, line):
        data = (line + "\n").encode("utf-8", "replace")
        if len(data) > self.max_bytes:
            data = data[: self.max_bytes - 1].decode("utf-8", "ignore").encode("utf-8") + b"\n"
        with _LOCK:
            self._write_locked(data)

    def _write_locked(self, data):
        try:
            ensure(self.dir)
            path = self.dir / "plugin.log"
            if _link(path):
                return
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size and size + len(data) > self.max_bytes:
                self._rotate(path)
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                return
            with os.fdopen(fd, "ab") as f:
                f.write(data)
        except OSError:
            pass

    def _rotate(self, base):
        names = [base] + [Path("%s.%d" % (base, i)) for i in range(1, self.keep)]
        names[-1].unlink(missing_ok=True)
        for i in range(len(names) - 1, 0, -1):
            if names[i - 1].exists():
                os.replace(names[i - 1], names[i])


def snapshot_settings(config_dir):
    src = Path(config_dir) / "settings.json"
    dst = Path(config_dir) / "settings.json.premigrate"
    with _LOCK:
        _verify(Path(config_dir))
        if os.path.lexists(dst):
            if not dst.is_file() or _link(dst):
                raise OSError(errno.EEXIST, "invalid settings snapshot")
            return dst
        if not src.is_file():
            return None
        _copy(src, dst)
    return dst


def drop_snapshot(config_dir):
    with _LOCK:
        _verify(Path(config_dir))
        Path(config_dir, "settings.json.premigrate").unlink(missing_ok=True)


def restore_snapshot(config_dir):
    src = Path(config_dir) / "settings.json.premigrate"
    with _LOCK:
        _verify(Path(config_dir))
        if not src.is_file():
            return False
        _copy(src, Path(config_dir) / "settings.json")
    return True


def _link(p):
    return p.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(p)


def _refuse(p, name):
    if not PLUGIN_ID.fullmatch(p.name):
        return "bad_path"
    chain = [p, p.parent]
    if name == "config":
        if p.parent.name != "plugins":
            return "bad_path"
        chain.append(p.parent.parent)
    if not ANCHOR.fullmatch(chain[-1].name):
        return "bad_path"
    if any(_link(q) for q in chain):
        return "symlink"
    return None


def purge(d, which=("config", "data", "runtime", "logs")):
    removed, failed = [], []
    for name in which:
        if name not in Dirs._fields:
            failed.append({"name": str(name), "reason": "unknown"})
            continue
        if getattr(d, name) is None:
            failed.append({"name": name, "reason": "unavailable"})
            continue
        p = Path(getattr(d, name))
        reason = _refuse(p, name)
        if reason is None and os.path.lexists(p):
            if not p.is_dir():
                reason = "not_directory"
            else:
                try:
                    _verify(p)
                except OSError:
                    reason = "unsafe_directory"
                else:
                    try:
                        shutil.rmtree(p)
                        removed.append(name)
                    except OSError:
                        reason = "error"
        if reason:
            failed.append({"name": name, "reason": reason})
    return {"removed": removed, "failed": failed}


def discard_held(root, pid):
    if not isinstance(pid, str) or not PLUGIN_ID.fullmatch(pid):
        raise ValueError("bad plugin id")
    root = Path(root)
    try:
        _verify(root)
    except OSError:
        return
    if _link(root):
        return
    for name in (".old-", ".stage-"):
        shutil.rmtree(root / (name + pid), ignore_errors=True)


def clean_tmp(runtime_dir):
    if runtime_dir is None:
        return 0
    d = Path(runtime_dir)
    if _link(d) or not d.is_dir():
        return 0
    try:
        _verify(d)
        entries = list(os.scandir(d))
    except OSError:
        return 0
    n = 0
    for e in entries:
        if not e.name.startswith("tmp-"):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.path)
            else:
                os.unlink(e.path)
            n += 1
        except OSError:
            pass
    return n


def _rm(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif os.path.lexists(p):
        os.unlink(p)


def _tree(p):
    mode = p.lstat().st_mode
    if stat.S_ISLNK(mode):
        return ("link", os.readlink(p))
    if stat.S_ISREG(mode):
        digest = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return ("file", digest.digest())
    if not stat.S_ISDIR(mode):
        raise OSError(errno.ENOTSUP, "unsupported migration source")
    out = []
    for root, dirnames, filenames in os.walk(p):
        for n in dirnames + filenames:
            q = Path(root) / n
            is_dir = q.is_dir() and not q.is_symlink()
            out.append((str(q.relative_to(p)), ("directory",) if is_dir else _tree(q)))
    return ("directory", sorted(out))


def _copy_file(src, dst):
    with open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b)
        b.flush()
        os.fsync(b.fileno())
    shutil.copystat(src, dst)
    return dst


def _copy(src, dst):
    src, dst = Path(src), Path(dst)
    if _link(src):
        raise OSError(errno.ELOOP, "linked migration source")
    if src.is_dir() and src.resolve() in (dst.resolve(), *dst.resolve().parents):
        raise OSError(errno.EINVAL, "migration destination is inside source")
    before = _tree(src)
    if src.is_dir():
        tmp = Path(tempfile.mkdtemp(dir=dst.parent, prefix="." + dst.name + ".", suffix=".fy-part"))
    else:
        fd, name = tempfile.mkstemp(dir=dst.parent, prefix="." + dst.name + ".", suffix=".fy-part")
        os.close(fd)
        tmp = Path(name)
    try:
        if src.is_dir():
            shutil.copytree(src, tmp, symlinks=True, dirs_exist_ok=True, copy_function=_copy_file)
            for root, _, _ in os.walk(tmp, topdown=False):
                _sync_dir(root, strict=True)
        else:
            _copy_file(src, tmp)
        if before != _tree(src) or before != _tree(tmp):
            raise OSError(errno.EIO, "copy verification failed")
        os.replace(tmp, dst)
        _sync_dir(dst.parent, strict=True)
    except BaseException:
        _rm(tmp)
        raise


def migrate_move(src, dst, overwrite=False):
    with _LOCK:
        return _migrate_move(src, dst, overwrite)


def _migrate_move(src, dst, overwrite):
    src, dst = Path(src), Path(dst)
    if not os.path.lexists(src):
        return {"moved": False, "reason": "missing_source"}
    if src.is_symlink():
        return {"moved": False, "reason": "link"}
    if os.path.lexists(dst):
        if os.path.exists(dst) and os.path.samefile(src, dst):
            return {"moved": False, "reason": "same"}
        if not overwrite or dst.is_dir():
            return {"moved": False, "reason": "exists"}
    try:
        if src.is_dir() and src.resolve() in (dst.resolve(), *dst.resolve().parents):
            raise OSError(errno.EINVAL, "migration destination is inside source")
        ensure(dst.parent)
        try:
            os.replace(src, dst)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            _copy(src, dst)
            _rm(src)
            _sync_dir(src.parent)
    except OSError as e:
        return {
            "moved": False,
            "reason": "error:" + (errno.errorcode.get(e.errno) or type(e).__name__),
        }
    return {"moved": True, "reason": "ok"}


def migrate_keys(settings, mapping):
    if not isinstance(mapping, dict) or len(mapping) > KEYS_MAX:
        raise ValueError("bad mapping")
    for old, new in mapping.items():
        _key(old)
        _key(new)
    renamed = []
    for old, new in mapping.items():
        if old != new and old in settings and new not in settings:
            settings[new] = settings.pop(old)
            renamed.append([old, new])
    return {"renamed": renamed}


def copy_once(src, dst, marker_name):
    if not isinstance(marker_name, str) or not MARKER.fullmatch(marker_name):
        raise ValueError("bad marker name")
    src, dst = Path(src), Path(dst)
    marker = dst.parent / marker_name
    with _LOCK:
        if os.path.lexists(marker) or not os.path.lexists(src) or _link(src):
            return False
        if src.is_dir() and src.resolve() in (dst.resolve(), *dst.resolve().parents):
            raise OSError(errno.EINVAL, "migration destination is inside source")
        ensure(dst.parent)
        copied = not os.path.lexists(dst)
        if copied:
            _copy(src, dst)
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return copied
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_dir(dst.parent)
        return copied


def read_locale(plugin_dir, lang):
    if not isinstance(lang, str) or not LANG.fullmatch(lang):
        return {}
    p = Path(plugin_dir) / "locales" / (lang + ".json")
    if p.is_symlink():
        return {}
    d = read_json(p, None, LOCALE_MAX)
    if not isinstance(d, dict) or len(d) > LOCALE_KEYS:
        return {}
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in d.items()):
        return {}
    return d
