import copy
import json
import os
import re
import time
from pathlib import Path

FILE_MAX = 65536
READ_MAX = 262144
LIST_MAX = 200
ID = re.compile(r"[a-z0-9_-]{1,32}")
RESERVED = frozenset(
    ("_core", "core", "modules", "settings", "store", "discover", "developer", "dev", "decky-store")
)
LOCALE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")
VERSION = re.compile(r"[0-9A-Za-z.+~_-]{0,40}")
TARGET = re.compile(r"[a-z0-9_-]{1,24}")
ROOT_BASES = ("/home/", "/run/media/", "/mnt/")


def _constant(name):
    raise ValueError(name)


class StateError(Exception):
    def __init__(self, message, code="state_error"):
        super().__init__(message[:300])
        self.message = message[:300]
        self.code = code


def _id(v):
    return isinstance(v, str) and ID.fullmatch(v) is not None and v not in RESERVED


def _int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _flag(v):
    return (v, False) if isinstance(v, bool) else (False, True)


def _choice(*opts):
    return lambda v: (v, False) if isinstance(v, str) and v in opts else (opts[0], True)


def _lang(v):
    return (v, False) if isinstance(v, str) and (v == "" or LOCALE.fullmatch(v)) else ("", True)


def _workers(v):
    return (v, False) if _int(v, 1, 3) else (3, True)


def _number(v):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 4_000_000_000_000
    return (int(v), False) if ok else (0, True)


def _count(v):
    return (v, False) if _int(v, 0, 1_000_000) else (0, True)


def _ver(v):
    return (v, False) if isinstance(v, str) and VERSION.fullmatch(v) else ("", True)


def _text(n):
    return lambda v: (
        (v, False) if isinstance(v, str) and len(v) <= n and v.isprintable() else ("", True)
    )


def _msg(v):
    return (
        ("".join(c for c in v[:200] if c.isprintable()), False)
        if isinstance(v, str)
        else ("", True)
    )


def _ids(v):
    if not isinstance(v, list):
        return [], True
    out, seen = [], set()
    for x in v:
        if _id(x) and x not in seen and len(out) < LIST_MAX:
            seen.add(x)
            out.append(x)
    return out, len(out) != len(v)


def _root_ok(x):
    return (
        isinstance(x, str)
        and len(x) <= 1024
        and x.startswith(ROOT_BASES)
        and x.isprintable()
        and "\\" not in x
        and all(p not in ("", ".", "..") for p in x[1:].split("/"))
    )


def _roots(v):
    if not isinstance(v, list):
        return [], True
    out = []
    for x in v:
        if _root_ok(x) and x not in out and len(out) < 8:
            out.append(x)
    return out, len(out) != len(v)


def _targets(v):
    if not isinstance(v, dict):
        return {}, True
    out = {}
    for k, x in v.items():
        if (
            isinstance(k, str)
            and TARGET.fullmatch(k)
            and k != "panel"
            and isinstance(x, str)
            and 1 <= len(x) <= 120
            and x.isprintable()
            and len(out) < 8
        ):
            out[k] = x
    return out, len(out) != len(v)


def _apply(spec, raw):
    if not isinstance(raw, dict):
        return _apply(spec, {})[0], True
    out, bad = {}, False
    for key, (f, default) in spec.items():
        if key in raw:
            out[key], b = f(raw[key])
            bad = bad or b
        else:
            out[key] = f(default)[0]
    return out, bad


def _group(spec):
    return lambda v: _apply(spec, v)


def _entry(spec):
    return lambda x: _apply(spec, x) if isinstance(x, dict) else (None, True)


def _map(rec):
    def run(v):
        if not isinstance(v, dict):
            return {}, True
        out, bad = {}, False
        for k, x in v.items():
            r, b = rec(x) if _id(k) and len(out) < LIST_MAX else (None, True)
            bad = bad or b
            if r is not None:
                out[k] = r
        return out, bad

    return run


def _tag_entry(v):
    return (v, False) if isinstance(v, str) and len(v) <= 64 and v.isprintable() else (None, True)


FROZEN = {"at": (_number, 0), "version": (_ver, "")}
MSTATE = {
    "ver": (_ver, ""),
    "data_v": (_count, 0),
    "ok_at": (_number, 0),
    "fails": (_count, 0),
    "last_error": (_msg, ""),
}
DEV = {
    "enabled": (_flag, False),
    "hot": (_ids, []),
    "hot_until": (_number, 0),
    "targets": (_targets, {}),
}
PREFS = {
    "autocheck": (_flag, False),
    "notify_loader": (_flag, False),
    "notify_plugins": (_flag, False),
    "channel": (_choice("stable", "prerelease"), "stable"),
    "lang": (_lang, ""),
    "safe": (_flag, False),
    "workers_max": (_workers, 3),
    "panel_mode": (_choice("embedded", "dashboard", "floating"), "embedded"),
    "file_roots": (_roots, []),
    "dev": (_group(DEV), {}),
}
MODULES = {
    "order": (_ids, []),
    "sort": (_choice("custom", "name"), "custom"),
    "hidden": (_ids, []),
    "frozen": (_map(_entry(FROZEN)), {}),
    "state": (_map(_entry(MSTATE)), {}),
}
UPDATES = {
    "last": (_number, 0),
    "next_ok": (_number, 0),
    "fails": (_count, 0),
    "loader": (_group({"tag": (_text(64), ""), "state": (_text(32), "")}), {}),
    "plugins": (_map(_entry({"latest": (_text(64), ""), "state": (_text(32), "")})), {}),
    "seen": (_group({"loader": (_text(64), ""), "plugins": (_map(_tag_entry), {})}), {}),
}
TOP = {
    "v": (lambda v: (1, False), 1),
    "disabled": (_ids, []),
    "modules": (_group(MODULES), {}),
    "prefs": (_group(PREFS), {}),
    "updates": (_group(UPDATES), {}),
}
OWN = tuple(TOP) + ("repaired",)
PREF_F = {
    k: PREFS[k][0]
    for k in (
        "autocheck",
        "notify_loader",
        "notify_plugins",
        "channel",
        "lang",
        "safe",
        "workers_max",
        "file_roots",
    )
}
PREF_F.update({"dev." + k: DEV[k][0] for k in ("enabled", "hot", "hot_until", "targets")})


def defaults():
    return _apply(TOP, {})[0]


def validate(raw):
    if not isinstance(raw, dict):
        return defaults(), True
    s, bad = _apply(TOP, raw)
    s.update({k: v for k, v in raw.items() if k not in OWN})
    return s, bad


_UNREADABLE = {}
SAVED_CORRUPT = 3


def keep_bad(path, raw):
    bad = Path(str(path) + ".bad")
    try:
        fd = os.open(str(bad), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    except OSError:
        pass


def load(path):
    key = os.path.abspath(str(path))
    try:
        with open(path, "rb") as f:
            data = f.read(READ_MAX + 1)
    except FileNotFoundError:
        _UNREADABLE.pop(key, None)
        s = defaults()
        s["repaired"] = False
        return s
    except OSError:
        _UNREADABLE[key] = "unreadable"
        s = defaults()
        s["repaired"] = True
        return s
    _UNREADABLE.pop(key, None)
    try:
        raw = json.loads(data, parse_constant=_constant) if len(data) <= READ_MAX else None
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        keep_bad(path, data)
        _UNREADABLE[key] = "corrupt"
    s, bad = validate(raw)
    s["repaired"] = bad
    return s


def state(path):
    load(path)
    return _UNREADABLE.get(os.path.abspath(str(path)), "ok")


def broken(path):
    return state(path) != "ok"


def repair(path):
    path = Path(path)
    key = os.path.abspath(str(path))
    kind = state(path)
    if kind == "ok":
        return {"ok": True}
    if kind == "unreadable":
        return {
            "ok": False,
            "error": "settings.json could not be read (permissions or I/O); nothing was changed",
        }
    stamp = int(time.time())
    dst = path.with_name("%s.corrupt-%d" % (path.name, stamp))
    while dst.exists():
        stamp += 1
        dst = path.with_name("%s.corrupt-%d" % (path.name, stamp))
    try:
        os.replace(path, dst)
    except OSError as e:
        return {
            "ok": False,
            "error": "could not move settings.json aside: " + (e.strerror or "i/o error"),
        }
    _UNREADABLE.pop(key, None)
    pat = re.compile(re.escape(path.name) + r"\.corrupt-(\d+)")
    kept = []
    try:
        for p in path.parent.iterdir():
            m = pat.fullmatch(p.name)
            if m and p.is_file() and not p.is_symlink():
                kept.append((int(m.group(1)), p))
    except OSError:
        return {"ok": True}
    for _, p in sorted(kept, key=lambda t: t[0])[:-SAVED_CORRUPT]:
        try:
            p.unlink()
        except OSError:
            pass
    return {"ok": True}


def save(path, state):
    if not isinstance(state, dict):
        raise StateError("state must be an object", "bad_state")
    if os.path.abspath(str(path)) in _UNREADABLE:
        raise StateError("settings could not be read; not overwriting them", "unreadable")
    load(path)
    if os.path.abspath(str(path)) in _UNREADABLE:
        raise StateError("settings could not be read; not overwriting them", "unreadable")
    clean = validate(state)[0]
    try:
        data = json.dumps(clean, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise StateError("state is not serialisable", "bad_state") from None
    if len(data) > FILE_MAX:
        raise StateError("state too large", "too_large")
    path = Path(path)
    tmp = None
    import tempfile

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise StateError(
            "could not save state: " + (e.strerror or "i/o error"), "write_failed"
        ) from None
    if hasattr(os, "O_DIRECTORY"):
        try:
            d = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(d)
            finally:
                os.close(d)
        except OSError:
            pass


def _names(rows):
    out = {}
    for pid, name in rows:
        if _id(pid) and pid not in out:
            out[pid] = name if isinstance(name, str) else pid
    return out


def _sort_key(names):
    import unicodedata

    return lambda pid: (unicodedata.normalize("NFC", names[pid]).casefold(), pid)


def _custom(names, state):
    saved = [p for p in state["modules"]["order"] if p in names]
    have = set(saved)
    return saved + sorted((p for p in names if p not in have), key=_sort_key(names))


def effective_order(rows, state):
    names = _names(rows)
    if state["modules"]["sort"] == "name":
        return sorted(names, key=_sort_key(names))
    return _custom(names, state)


def _need_id(pid):
    if not _id(pid):
        raise StateError("invalid module id", "bad_id")
    return pid


def set_order(state, ids):
    if not isinstance(ids, list) or len(ids) > LIST_MAX:
        raise StateError("ids must be a list of at most %d" % LIST_MAX, "bad_value")
    for p in ids:
        _need_id(p)
    if len(set(ids)) != len(ids):
        raise StateError("duplicate ids", "bad_value")
    known = set(ids)
    fill = iter(ids)
    out = [next(fill, p) if p in known else p for p in state["modules"]["order"]]
    out.extend(fill)
    if len(out) > LIST_MAX:
        raise StateError("too many modules", "too_many")
    state["modules"]["order"] = out


def move(state, rows, pid, delta):
    _need_id(pid)
    names = _names(rows)
    if pid not in names:
        raise StateError("module not found", "not_found")
    if not _int(delta, -1, 1) or delta == 0:
        raise StateError("delta must be 1 or -1", "bad_value")
    cur = _custom(names, state)
    i = cur.index(pid)
    j = min(max(i + delta, 0), len(cur) - 1)
    cur[i], cur[j] = cur[j], cur[i]
    set_order(state, cur)


def set_sort(state, mode):
    if mode not in ("custom", "name") or not isinstance(mode, str):
        raise StateError("sort must be custom or name", "bad_value")
    state["modules"]["sort"] = mode


def _need_flag(flag):
    if not isinstance(flag, bool):
        raise StateError("flag must be true or false", "bad_value")
    return flag


def set_hidden(state, pid, flag):
    _need_id(pid)
    hidden = state["modules"]["hidden"]
    if _need_flag(flag) and pid not in hidden:
        if len(hidden) >= LIST_MAX:
            raise StateError("too many hidden modules", "too_many")
        hidden.append(pid)
    elif not flag and pid in hidden:
        hidden.remove(pid)


def set_frozen(state, pid, flag, version="", at=None):
    _need_id(pid)
    frozen = state["modules"]["frozen"]
    if _need_flag(flag):
        if pid in frozen:
            return
        if len(frozen) >= LIST_MAX:
            raise StateError("too many frozen modules", "too_many")
        stamp, bad = _number(time.time() if at is None else at)
        if bad:
            raise StateError("invalid time", "bad_value")
        frozen[pid] = {"at": stamp, "version": _ver(version)[0]}
    else:
        frozen.pop(pid, None)


def set_pref(state, key, value):
    f = PREF_F.get(key) if isinstance(key, str) else None
    if f is None:
        raise StateError("unknown preference", "bad_key")
    v, bad = f(value)
    if bad:
        raise StateError("invalid value for " + key, "bad_value")
    group, _, name = key.rpartition(".")
    (state["prefs"][group] if group else state["prefs"])[name] = v


def forget_module(state, pid):
    m, u = state["modules"], state["updates"]
    for lst in (m["order"], m["hidden"], state["prefs"]["dev"]["hot"]):
        while pid in lst:
            lst.remove(pid)
    for d in (m["frozen"], m["state"], u["plugins"], u["seen"]["plugins"]):
        d.pop(pid, None)


def module_state(state, pid):
    r = state["modules"]["state"].get(pid)
    return dict(r) if r else {}


def set_module_state(state, pid, rec):
    _need_id(pid)
    if not isinstance(rec, dict):
        raise StateError("record must be an object", "bad_value")
    v, bad = _apply(MSTATE, rec)
    if bad:
        raise StateError("invalid module record", "bad_value")
    store = state["modules"]["state"]
    if pid not in store and len(store) >= LIST_MAX:
        raise StateError("too many module records", "too_many")
    store[pid] = v


def _rows(host):
    return [(m["id"], m.get("name")) for m in host.manifests() if isinstance(m, dict) and "id" in m]


def view(host, state):
    p = state["prefs"]
    return {
        "order": effective_order(_rows(host), state),
        "sort": state["modules"]["sort"],
        "hidden": list(state["modules"]["hidden"]),
        "frozen": copy.deepcopy(state["modules"]["frozen"]),
        "prefs": copy.deepcopy(p),
        "safe": p["safe"],
    }


def _obj(a):
    if not isinstance(a, dict):
        raise StateError("expected an object", "bad_arg")
    return a


def _m_order(host, s, a):
    a = _obj(a)
    if "ids" in a:
        set_order(s, a["ids"])
    else:
        move(s, _rows(host), a.get("id"), a.get("delta"))


def _m_sort(host, s, a):
    set_sort(s, a)


def _m_hide(host, s, a):
    a = _obj(a)
    set_hidden(s, a.get("id"), a.get("hidden"))


def _m_freeze(host, s, a):
    a = _obj(a)
    found = [m for m in host.manifests() if isinstance(m, dict) and m.get("id") == a.get("id")]
    if a.get("frozen") is True and not found:
        raise StateError("module not found", "not_found")
    version = found[0].get("version") if found else ""
    set_frozen(
        s,
        a.get("id"),
        a.get("frozen"),
        version if isinstance(version, str) else "",
        int(host.now()),
    )


def _m_prefs(host, s, a):
    a = _obj(a)
    if not a or len(a) > 16:
        raise StateError("expected 1 to 16 preferences", "bad_arg")
    for k, v in a.items():
        set_pref(s, k, v)


def _method(fn, write=True):
    async def run(host, arg=None):
        try:
            state = load(host.CONF)
            fn(host, state, arg)
            if write:
                save(host.CONF, state)
            return view(host, state)
        except StateError as e:
            return {"error": e.message, "code": e.code}

    return run


METHODS = {
    "state_get": _method(lambda host, s, a: None, False),
    "mod_order": _method(_m_order),
    "mod_sort": _method(_m_sort),
    "mod_hide": _method(_m_hide),
    "mod_freeze": _method(_m_freeze),
    "prefs_set": _method(_m_prefs),
}
