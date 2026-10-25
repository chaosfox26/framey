import json
import os
import re
import sys
import threading
import urllib.parse

CAP = 32768
LOG_LINES = 40
WORKERS_MAX = 16
FAILURES_MAX = 16
IDS_MAX = 64
TEXT_MAX = 300
COUNT_MAX = (1 << 63) - 1
PID = re.compile(r"[a-z0-9_-]{1,32}\Z")
HOMES = re.compile(r"/home/[^/\s\"']+")
MAC_HOMES = re.compile(r"/Users/[^/\s\"']+")
WIN_HOMES = re.compile(r"(?i)([a-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+)[^\\/\s\"']+")
MEDIA_HOMES = re.compile(r"/run/media/[^/\s\"']+")
BEARER = re.compile(r"(?i)\bbearer\s+\S+")
GITHUB = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}")
KEYED = re.compile(r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*(?:token|secret|password|passwd|authorization|api[_-]?key|cookie|[_-]key))([\"']?\s*[=:]\s*)[^\r\n]*")
USERINFO = re.compile(r"(://)[^/@\s]+@")
PARAM = re.compile(r"([?&#])([^=&#\s<>\"']{1,100})=([^&#\s<>\"']*)")
QUERY_SECRETS = frozenset(("code", "state", "session", "sessionid", "session_id", "auth", "authorization", "key", "sig", "signature", "credential"))
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
CAPS = {"workers_hard": 3, "worker_idle_s": 120, "calls_queued_per_worker": 16, "event_queue_per_plugin": 64, "events_per_second": 50, "target_items_per_plugin": 8, "media_tokens": 16, "line_bytes": 1048576, "diag_bytes": CAP}


def scrub(text, home=""):
    t = str(text)
    if len(home) > 1:
        t = re.sub(re.escape(home.rstrip("/\\")) + r"(?=$|[\\/\s\"'])", "~", t)
    t = HOMES.sub("/home/<user>", t)
    t = MAC_HOMES.sub("/Users/<user>", t)
    t = WIN_HOMES.sub(lambda m: m.group(1) + "<user>", t)
    t = MEDIA_HOMES.sub("/run/media/<user>", t)
    t = USERINFO.sub(r"\1<hidden>@", t)
    t = PARAM.sub(scrub_param, t)
    t = BEARER.sub("<hidden>", t)
    t = GITHUB.sub("<hidden>", t)
    t = KEYED.sub(lambda m: m.group(1) + m.group(2) + "<hidden>", t)
    return CONTROL.sub(" ", t)[:TEXT_MAX]


def scrub_param(match):
    key = urllib.parse.unquote_plus(match.group(2)).lower().replace("-", "_")
    secret = key in QUERY_SECRETS or any(part in key for part in ("token", "secret", "password", "passwd", "api_key", "credential", "signature"))
    return match.group(1) + match.group(2) + "=<hidden>" if secret else match.group(0)


def proc_stats(base="/proc/self"):
    rss = threads = fds = None
    try:
        with open(base + "/status", "r", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
                elif line.startswith("Threads:"):
                    threads = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        fds = len(os.listdir(base + "/fd"))
    except OSError:
        pass
    return rss, threads, fds


def count(n):
    return min(n, COUNT_MAX) if type(n) is int and n >= 0 else 0


def number(n):
    return type(n) in (int, float) and 0 <= n <= COUNT_MAX


def metric(n, positive=False):
    return n if type(n) is int and (n > 0 if positive else n >= 0) and n <= COUNT_MAX else None


def attempt(fn, default):
    try:
        return fn()
    except Exception:
        return default


def workers_of(sup, home):
    rows = attempt(lambda: sup.status(), {}) if sup is not None else {}
    out = {}
    for pid, r in (rows.items() if isinstance(rows, dict) else []):
        if len(out) >= WORKERS_MAX:
            break
        if not isinstance(pid, str) or not PID.match(pid) or not isinstance(r, dict):
            continue
        err = r.get("last_error")
        out[pid] = {
            "state": scrub(r.get("state"), home)[:20],
            "os_pid": metric(r.get("os_pid"), positive=True),
            "rss_kb": metric(r.get("rss_kb")),
            "calls": count(r.get("calls")),
            "crashes": count(r.get("crashes")),
            "last_used": r.get("last_used") if number(r.get("last_used")) else None,
            "limits_applied": r.get("limits_applied") if isinstance(r.get("limits_applied"), bool) else None,
            "limits_warning": scrub(r["limits_warning"], home) if isinstance(r.get("limits_warning"), str) else None,
            "last_error": {k: scrub(err.get(k), home) for k in ("code", "stage", "message") if err.get(k) is not None} if isinstance(err, dict) else None,
        }
    return out


def failures_of(cfg, home):
    state = (cfg.get("modules") or {}).get("state") if isinstance(cfg.get("modules"), dict) else None
    out = {}
    for pid, rec in (state.items() if isinstance(state, dict) else []):
        if len(out) >= FAILURES_MAX:
            break
        if isinstance(pid, str) and PID.match(pid) and isinstance(rec, dict) and (count(rec.get("fails")) or rec.get("last_error")):
            out[pid] = {"fails": count(rec.get("fails")), "last_error": scrub(rec.get("last_error") or "", home)}
    return out


def updates_of(cfg, disabled, frozen):
    u = cfg.get("updates") if isinstance(cfg.get("updates"), dict) else {}
    plugins = u.get("plugins") if isinstance(u.get("plugins"), dict) else {}
    loader = u.get("loader") if isinstance(u.get("loader"), dict) else {}
    n = sum(1 for pid, v in plugins.items() if isinstance(v, dict) and v.get("state") == "update" and pid not in disabled and pid not in frozen)
    return {"last": count(u.get("last")), "fails": count(u.get("fails")), "badge": {"n": n + (1 if loader.get("state") == "update" else 0), "loader": loader.get("state") == "update"}}


def fit(snap):
    for key, empty in (("logs", {"tail": []}), ("failures", {}), ("workers", {}), ("modules", {"count": 0})):
        if len(json.dumps(snap, separators=(",", ":"))) <= CAP:
            return snap
        snap[key] = empty
        snap["truncated"] = True
    return snap


def snapshot(host, started_at=None):
    home = str(getattr(host, "HOME", "") or "")
    cfg = attempt(lambda: host.settings(), {})
    cfg = cfg if isinstance(cfg, dict) else {}
    prefs = cfg.get("prefs") if isinstance(cfg.get("prefs"), dict) else {}
    mods = cfg.get("modules") if isinstance(cfg.get("modules"), dict) else {}
    disabled = {x for x in cfg.get("disabled", []) if isinstance(x, str)} if isinstance(cfg.get("disabled"), list) else set()
    frozen = set(mods["frozen"]) if isinstance(mods.get("frozen"), dict) else set()
    hidden = {x for x in mods.get("hidden", []) if isinstance(x, str)} if isinstance(mods.get("hidden"), list) else set()
    manifests = attempt(lambda: host.manifests(), [])
    ids = list(dict.fromkeys(m["id"] for m in manifests if isinstance(m, dict) and isinstance(m.get("id"), str) and PID.match(m["id"]))) if isinstance(manifests, (list, tuple)) else []
    sup = getattr(host, "supervisor", None)
    hub = getattr(sup, "events", None)
    targets = getattr(host, "targets", None)
    media = getattr(host, "media", None)
    tail = attempt(lambda: list(host.log_tail()), []) if callable(getattr(host, "log_tail", None)) else []
    rss, threads, fds = proc_stats()
    now = attempt(lambda: host.now(), None)
    settings_state = attempt(lambda: host.settings_state(), "unknown")
    workers_cap = prefs.get("workers_max")
    dropped = getattr(hub, "_dropped", {})
    caps = dict(CAPS, workers_max=workers_cap if type(workers_cap) is int and 1 <= workers_cap <= 3 else 3)
    snap = {
        "settings_state": settings_state if settings_state in ("ok", "corrupt", "unreadable") else "unknown",
        "loader": {
            "version": scrub(getattr(host, "VERSION", ""), home)[:40],
            "api": count(getattr(host, "API_GENERATION", 0)),
            "platform": scrub(getattr(host, "PLATFORM", ""), home)[:40],
            "python": "%d.%d.%d" % sys.version_info[:3],
            "uptime_s": int(now - started_at) if number(now) and number(started_at) and now >= started_at else None,
            "rss_kb": rss,
            "threads": threads,
            "fds": fds,
            "py_threads": threading.active_count(),
        },
        "sessions": {name: bool(attempt(lambda n=name: host.attached(n), False)) for name in ("main", "bar")},
        "modules": {"count": len(ids), "disabled": len(disabled), "hidden": len(hidden), "frozen": len(frozen), "safe": prefs.get("safe") is True, "ids": ids[:IDS_MAX]},
        "workers": workers_of(sup, home),
        "events": {"queued": count(getattr(hub, "_size", 0)), "dropped": min(sum(count(v) for v in dropped.values()), COUNT_MAX) if isinstance(dropped, dict) else 0},
        "caps": caps,
        "failures": failures_of(cfg, home),
        "updates": updates_of(cfg, disabled, frozen),
        "targets": attempt(lambda: len(targets.items), 0) if targets is not None else 0,
        "media": {"tokens": attempt(lambda: len(media._tokens), 0) if media is not None else 0, "server": getattr(media, "_server", None) is not None},
        "logs": {"tail": [scrub(line, home) for line in tail[-LOG_LINES:]]},
    }
    return fit(snap)


def dev_on(host):
    return attempt(lambda: host.settings()["prefs"]["dev"]["enabled"], False) is True


async def diag(host, arg=None):
    if not dev_on(host):
        return {"error": "Developer mode is off", "code": "dev_off"}
    return snapshot(host, getattr(host, "started_at", None))


METHODS = {"diag": diag}
