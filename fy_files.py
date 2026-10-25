import asyncio
import os
import re
import stat
import types
from pathlib import Path

PATH_MAX = 1024
DEPTH_MAX = 32
LIMIT_MAX = 500
SCAN_MAX = 10000
EXT = re.compile(r"\.?[a-z0-9]{1,12}")
PID = re.compile(r"[a-z0-9_-]{1,32}")
DRIVE = re.compile(r"[A-Za-z]:")


class FileError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message[:300]


DENY_ROOTS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/sbin", "/sys", "/usr", "/var")


def _under(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def allowed(path, home):
    real = os.path.normcase(os.path.realpath(path))
    if os.path.dirname(real) == real or any(_under(real, os.path.normcase(d)) for d in DENY_ROOTS):
        return False
    return not _under(os.path.normcase(os.path.realpath(home)), real)


def roots(home, dirs, extra):
    out = {}
    for key in ("data", "config"):
        path = getattr(dirs, key, None) if dirs else None
        if path and allowed(path, home):
            out[key] = Path(path)
    media = Path("/run/media") / Path(home).name
    if media.is_dir() and allowed(media, home):
        out["media"] = media
    for item in (extra if isinstance(extra, list) else [])[:8]:
        if not isinstance(item, str) or not os.path.isabs(item) or not os.path.isdir(item):
            continue
        if os.path.normcase(os.path.realpath(item)) != os.path.normcase(os.path.normpath(item)):
            continue
        if not allowed(item, home):
            continue
        base = re.sub(r"[^A-Za-z0-9._-]", "_", Path(item).name) or "root"
        name, n = base, 2
        while name in out:
            name = "%s-%d" % (base, n)
            n += 1
        out[name] = Path(item)
    return out


def safe_join(root, rel=""):
    if not isinstance(rel, str) or len(rel) > PATH_MAX or "\0" in rel or "\\" in rel:
        raise FileError("bad_path", "That path is not valid")
    parts = []
    if rel:
        if rel.startswith("/") or DRIVE.match(rel):
            raise FileError("bad_path", "That path is not valid")
        parts = rel.split("/")
        if len(parts) > DEPTH_MAX or any(p in ("", ".", "..") for p in parts):
            raise FileError("bad_path", "That path is not valid")
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, *parts))
    try:
        inside = os.path.commonpath([os.path.normcase(base), os.path.normcase(full)]) == os.path.normcase(base)
    except ValueError:
        inside = False
    if not inside:
        raise FileError("outside_root", "That folder is outside the allowed locations")
    return Path(full)


def _base(rts, root):
    base = rts.get(root) if isinstance(root, str) else None
    if base is None:
        raise FileError("bad_root", "That location is not available")
    return base


def _exts(exts):
    if not exts:
        return ()
    if not isinstance(exts, (list, tuple)) or len(exts) > 16:
        raise FileError("bad_path", "Too many file types")
    out = []
    for e in exts:
        e = e.lower() if isinstance(e, str) else ""
        if not EXT.fullmatch(e):
            raise FileError("bad_path", "That file type is not valid")
        out.append("." + e.lstrip("."))
    return tuple(out)


def root_list(rts):
    return [{"id": k, "label": "Plugin data" if k == "data" else k} for k in rts if k != "media"]


def list_dir(rts, root, rel="", *, hidden=False, dirs_only=False, exts=None, limit=500):
    base = _base(rts, root)
    target = safe_join(base, rel)
    want = _exts(exts)
    limit = limit if isinstance(limit, int) and 1 <= limit <= LIMIT_MAX else LIMIT_MAX
    rows = []
    try:
        with os.scandir(target) as it:
            for n, e in enumerate(it, 1):
                if n > SCAN_MAX:
                    raise FileError("too_many", "That folder has too many items to list")
                name = e.name
                if not hidden and name.startswith("."):
                    continue
                try:
                    link, isdir, isfile = e.is_symlink(), e.is_dir(), e.is_file()
                except OSError:
                    link = isdir = isfile = False
                if dirs_only and not isdir:
                    continue
                if want and not isdir and not name.lower().endswith(want):
                    continue
                kind = "link" if link else "dir" if isdir else "file" if isfile else "other"
                try:
                    st = e.stat(follow_symlinks=False)
                    size = st.st_size if kind in ("file", "other") else None
                    mtime = int(st.st_mtime)
                except OSError:
                    size = mtime = None
                rows.append((0 if isdir else 1, name.casefold(), {"name": name, "type": kind, "size": size, "mtime": mtime}))
    except FileNotFoundError:
        raise FileError("not_found", "That folder was not found")
    except NotADirectoryError:
        raise FileError("not_dir", "That is not a folder")
    except PermissionError:
        raise FileError("denied", "That folder cannot be opened")
    except FileError:
        raise
    except OSError:
        raise FileError("not_found", "That folder cannot be read")
    rows.sort(key=lambda r: (r[0], r[1], r[2]["name"]))
    out = {
        "root": root,
        "path": rel,
        "parent": None if not rel else rel.rpartition("/")[0],
        "entries": [r[2] for r in rows[:limit]],
        "truncated": len(rows) > limit,
    }
    if not rel:
        out["roots"] = root_list(rts)
    return out


def stat_file(rts, root, rel):
    base = _base(rts, root)
    full = safe_join(base, rel)
    try:
        st = os.stat(full)
    except OSError:
        raise FileError("not_found", "That item was not found")
    link = bool(rel) and os.path.islink(os.path.join(str(base), *rel.split("/")))
    kind = "link" if link else "dir" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "other"
    return {
        "root": root,
        "path": rel,
        "name": rel.rpartition("/")[2] or root,
        "type": kind,
        "size": st.st_size if stat.S_ISREG(st.st_mode) else None,
        "mtime": int(st.st_mtime),
    }


def plugin_dirs(host, pid):
    if not isinstance(pid, str) or not PID.fullmatch(pid):
        return None
    f = getattr(host, "dirs", None)
    if f:
        return f(pid)
    try:
        import fy_store

        return fy_store.dirs(pid, home=host.HOME)
    except Exception:
        return None


def _extra(host):
    try:
        prefs = host.settings().get("prefs")
        return prefs.get("file_roots", []) if isinstance(prefs, dict) else []
    except Exception:
        return []


async def hostcall(host, pid, arg, page=False):
    a = arg if isinstance(arg, dict) else {}
    dirs = plugin_dirs(host, pid)
    if page and dirs:
        dirs = types.SimpleNamespace(data=getattr(dirs, "data", None))
    rts = roots(host.HOME, dirs, _extra(host))
    if page and a.get("root") is None:
        return {"root": "", "path": "", "parent": None, "entries": [], "truncated": False, "roots": root_list(rts)}
    return await asyncio.to_thread(
        list_dir,
        rts,
        a.get("root") if isinstance(a.get("root"), str) else next(iter(rts), ""),
        a.get("path", ""),
        hidden=bool(a.get("hidden")),
        dirs_only=bool(a.get("dirs_only")),
        exts=a.get("exts"),
    )


async def files_list(host, arg):
    try:
        return await hostcall(host, arg.get("pid") if isinstance(arg, dict) else None, arg, True)
    except FileError as e:
        return {"error": e.message, "code": e.code}


METHODS = {"files_list": files_list}
