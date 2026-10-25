import asyncio
import base64
import importlib
import inspect
import json
import posixpath
import re
import shutil
import socket
import urllib.error
import urllib.parse

def _constant(name):
    raise ValueError(name)


def _loads(data):
    return json.loads(data, parse_constant=_constant)


LOADER = ("chaosfox26", "framey")
NAME = re.compile(r"[a-z0-9_-]{1,32}")
TAG = re.compile(r"[A-Za-z0-9._-]{1,64}")
SHA = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
STAGES = ("check", "download", "verify", "unpack", "swap", "reload")
COMPANION_STAGES = ("check", "download", "verify", "unpack")
LABELS = {"check": "Checking", "download": "Downloading", "verify": "Verifying", "unpack": "Unpacking", "swap": "Installing", "reload": "Reloading", "protected": "Protected update"}
CHANNELS = ("stable", "prerelease")
NOTES_MAX, PAGE, BATCH_MAX, CYCLE_MAX, CACHE_MAX = 8000, 10, 32, 20, 16
DAY, FLOOR, TTL, COMPANION_MAX = 86400, 21600, 300, 20_000_000
CONFIRM = "Going back to %s removes nothing from your data, but data migrations are not undone and the module may not read newer data. The current copy is kept until you press Done."
FORWARD_ONLY = "Protected components only move forward; use Restore previous version after an update"
PROTECTED_UPDATE = "Protected components update through their own Update action"
INTEGRITY = "integrity check, not a signature"


class UpdateError(Exception):
    def __init__(self, code, message, stage=None):
        super().__init__(message)
        self.code, self.message, self.stage = code, str(message)[:300], stage


def parse_version(s):
    try:
        import fy_pkg

        return fy_pkg.parse_version(s)
    except Exception:
        return None


def is_pre(s):
    v = parse_version(s)
    return bool(v and v[1] == 0)


def why(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 429 or (e.code == 403 and e.headers is not None and e.headers.get("X-RateLimit-Remaining") == "0"):
            return "GitHub rate limit reached, try again later"
        return "not found on GitHub (no release yet, private, or renamed)" if e.code == 404 else "GitHub answered HTTP %d" % e.code
    if isinstance(e, urllib.error.URLError):
        return "network error: %s" % e.reason
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "timed out: " + (str(e) or "no answer from GitHub")
    return (str(e) or type(e).__name__)[:300]


def check_digest(data, digest):
    import hashlib
    if not digest:
        return "absent"
    algo, _, want = str(digest).partition(":")
    if algo != "sha256" or not HEX64.fullmatch(want.lower()):
        return "unsupported"
    return "verified" if hashlib.sha256(data).hexdigest() == want.lower() else "mismatch"


def _ok(pid):
    return isinstance(pid, str) and bool(NAME.fullmatch(pid)) and pid != "_core"


def _read(path, n=2048):
    try:
        with open(path, "rb") as h:
            return h.read(n).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _text(s, n):
    s = s if isinstance(s, str) else ""
    return "".join(c for c in s if c in "\n\t" or (c >= " " and c != "\x7f"))[:n]


LINKED = "linked folder: not updated"


def _linked(p):
    return p.is_symlink() or getattr(p, "is_junction", lambda: False)()


def _priv(m):
    p = m.get("privileged") if isinstance(m, dict) else None
    return isinstance(p, dict) and all(isinstance(p.get(k), str) and posixpath.isabs(p[k]) for k in ("installed", "helper"))


def _prefs(s):
    p = s.get("prefs")
    return p if isinstance(p, dict) else {}


def _frozen(s):
    m = s.get("modules")
    f = m.get("frozen") if isinstance(m, dict) else None
    return f if isinstance(f, dict) else {}


def _off(s):
    d = s.get("disabled")
    return {x for x in d if isinstance(x, str)} if isinstance(d, list) else set()


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def _norm(u):
    u = u if isinstance(u, dict) else {}
    seen = u.get("seen") if isinstance(u.get("seen"), dict) else {}
    loader = u.get("loader") if isinstance(u.get("loader"), dict) else {}
    plugins = u.get("plugins") if isinstance(u.get("plugins"), dict) else {}
    sp = seen.get("plugins") if isinstance(seen.get("plugins"), dict) else {}
    return {
        "last": _num(u.get("last")),
        "next_ok": _num(u.get("next_ok")),
        "fails": int(_num(u.get("fails"))),
        "loader": {"tag": loader.get("tag") if isinstance(loader.get("tag"), str) else "", "state": loader.get("state") if isinstance(loader.get("state"), str) else ""},
        "plugins": {k: v for k, v in plugins.items() if _ok(k) and isinstance(v, dict)},
        "seen": {"loader": seen.get("loader") if isinstance(seen.get("loader"), str) else "", "plugins": {k: v for k, v in sp.items() if _ok(k) and isinstance(v, str)}},
    }


def _rel(j):
    if not isinstance(j, dict) or j.get("draft"):
        return None
    tag = j.get("tag_name")
    if not (isinstance(tag, str) and TAG.fullmatch(tag)):
        return None
    url, commit = j.get("html_url"), j.get("target_commitish")
    assets = {}
    for a in (j.get("assets") if isinstance(j.get("assets"), list) else [])[:100]:
        if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str):
            assets[a["name"]] = (a["browser_download_url"], a["digest"] if isinstance(a.get("digest"), str) else "")
    return {
        "tag": tag,
        "version": tag[1:] if tag[:1] == "v" else tag,
        "prerelease": bool(j.get("prerelease")) or is_pre(tag),
        "published": _text(j.get("published_at"), 40),
        "body": _text(j.get("body"), NOTES_MAX),
        "url": url if isinstance(url, str) and url.startswith("https://github.com/") and len(url) <= 300 else "",
        "commit": commit if isinstance(commit, str) and SHA.fullmatch(commit) else "",
        "assets": assets,
    }


def _safe(fn):
    async def run(self, *a, **k):
        try:
            return await fn(self, *a, **k)
        except UpdateError as e:
            return {"error": e.message, "code": e.code, **({"stage": e.stage} if e.stage else {})}
        except Exception as e:
            return {"error": why(e), "code": "failed"}

    run.__name__ = fn.__name__
    return run


class Job:
    def __init__(self, host, n, pid, names):
        self.host, self.n, self.pid, self.names = host, n, pid, names
        self.loop = host.loop()
        self.counted, self.done, self.total, self.last = False, 0, 0, -1e9

    def measured(self):
        if self.counted and self.total and self.total > 0:
            return {"done": min(self.done, self.total), "total": self.total}
        return None

    async def emit(self, stage, state, error=None, measured=None):
        ev = {"job": self.n, "id": self.pid, "stage": stage, "index": self.names.index(stage) + 1, "total": len(self.names), "label": LABELS[stage], "state": state, "measured": measured, "error": error}
        try:
            await self.host.progress(self.n, ev)
        except Exception as e:
            self.host.log("progress event failed:", e)

    def emit_sync(self, stage, state, error=None):
        try:
            asyncio.run_coroutine_threadsafe(self.emit(stage, state, error), self.loop).result(5)
        except Exception:
            pass

    def progress(self, done, total=None):
        self.counted, self.done, self.total = True, done, total if isinstance(total, int) else 0
        now = self.host.mono()
        if self.total and now - self.last >= 0.5:
            self.last = now
            try:
                asyncio.run_coroutine_threadsafe(self.emit("download", "running", None, self.measured()), self.loop).result(5)
            except Exception:
                pass


class Updates:
    SPACING = 2.0

    def __init__(self, host):
        self.host = host
        self.after = None
        self.cycled = None
        self._cache = {}
        self._jobs = 0
        self._timer = None
        self._task = None
        self._running = False
        table = {
            "ver_list": lambda a: self.versions(a.get("id"), a.get("page", 1)),
            "ver_install": lambda a: self.install_version(a.get("id"), a.get("tag"), a.get("confirm") is True),
            "repair": lambda a: self.repair(a.get("id"), a.get("reset") is True, a.get("confirm") is True),
            "notes": lambda a: self.notes(a.get("id"), a.get("tag")),
            "batch_plan": lambda a: self.batch_plan(a.get("ids")),
            "batch_apply": lambda a: self.batch_apply(a.get("ids")),
            "loader_check": lambda a: self.loader_check(),
            "badge": lambda a: self.badge(),
            "autocheck_status": lambda a: self.autocheck_status(),
            "companion_fetch": lambda a: self.companion_fetch(a.get("id"), a.get("name")),
        }
        self.METHODS = {k: self._entry(f) for k, f in table.items()}

    def _entry(self, f):
        async def call(host, arg):
            r = f(arg if isinstance(arg, dict) else {})
            return await r if inspect.isawaitable(r) else r

        return call

    def _pkg(self):
        if hasattr(self.host, "pkg"):
            return self.host.pkg
        try:
            return importlib.import_module("fy_pkg")
        except ImportError:
            return None

    def _pref(self, key, default=None):
        return _prefs(self.host.settings()).get(key, default)

    def _channel(self, c=None):
        c = c if c in CHANNELS else self._pref("channel", "stable")
        return "prerelease" if c == "prerelease" and self._dev() else "stable"

    def _dev(self):
        d = self._pref("dev")
        return isinstance(d, dict) and d.get("enabled") is True

    def _label(self, prerelease):
        return ("Testing build" if self._dev() else "Prerelease") if prerelease else ""

    def _job(self, pid, names):
        self._jobs += 1
        return Job(self.host, self._jobs, pid, names)

    def _sha(self, pid):
        v = _read(self.host.plugin_dir(pid) / ".version", 64)
        return v if SHA.fullmatch(v) else ""

    def _loader_sha(self):
        v = _read(self.host.ROOT / ".version", 64)
        return v if SHA.fullmatch(v) else ""

    def _plugin(self, pid):
        host = self.host
        m = host.manifest(host.plugin_dir(pid)) if _ok(pid) else None
        if not m:
            raise UpdateError("unknown_plugin", "unknown plugin")
        dest = host.real(pid)
        if not dest:
            raise UpdateError("bad_link", "the link must point to a real folder inside your home, outside the loader folder")
        g, msg = host.source(m)
        if not g:
            raise UpdateError("no_source", msg or "no update source recorded")
        return m, dest, g

    def _pending(self, m, dest):
        fn = getattr(self.host, "pending", None)
        if fn:
            return bool(fn(m["id"]))
        return _priv(m) and dest.with_name(".old-" + dest.name).is_dir()

    def _store(self, mutate):
        host = self.host
        s = host.settings()
        u = _norm(s.get("updates"))
        mutate(u)
        s["updates"] = u
        host.save(s)
        return s

    def _mark(self, pid, latest, state="current"):
        try:
            self._store(lambda u: u["plugins"].__setitem__(pid, {"latest": latest, "state": state}))
        except Exception as e:
            self.host.log("could not store update state:", e)

    async def _run(self, job, stage, fn, *a):
        await job.emit(stage, "running")
        try:
            r = await fn(*a) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, *a)
        except Exception as e:
            err = e if isinstance(e, UpdateError) else UpdateError("failed", why(e))
            err.stage = stage
            await job.emit(stage, "fail", err.message)
            raise err
        await job.emit(stage, "ok", None, job.measured() if stage == "download" else None)
        return r

    async def _releases(self, g, page):
        key = (g[0], g[1], page)
        now = self.host.mono()
        hit = self._cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1], hit[2]
        raw = await asyncio.to_thread(self.host.api_json, "%s/%s/releases?per_page=%d&page=%d" % (g[0], g[1], PAGE, page))
        if not isinstance(raw, list):
            raise ValueError("unexpected reply from GitHub")
        rows = [r for r in map(_rel, raw[:PAGE]) if r]
        more = len(raw) >= PAGE
        while len(self._cache) >= CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (now, rows, more)
        return rows, more

    def _source_of(self, pid):
        if pid == "_loader":
            return None, LOADER, self.host.VERSION
        m, dest, g = self._plugin(pid)
        return m, g, m.get("version", "")

    @_safe
    async def versions(self, pid, page=1):
        host = self.host
        m, g, curver = self._source_of(pid)
        page = page if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= 100 else 1
        prot = _priv(m)
        installed = self._loader_sha() if pid == "_loader" else self._sha(pid)
        cur = parse_version(curver)
        stable = self._channel() != "prerelease"
        try:
            rows, more = await self._releases(g, page)
            missing = page == 1 and not rows
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            missing = True
        if missing:
            sha = await asyncio.to_thread(host.latest, g[0], g[1], g[2] if len(g) > 2 else "HEAD")
            row = {"tag": sha, "version": sha[:7], "commit": sha, "prerelease": False, "published": "", "label": "", "current": sha == installed, "compat": {"badge": "unknown", "msg": ""}, "downgrade": False, "eligible": not prot and sha != installed and pid != "_loader", "reason": PROTECTED_UPDATE if prot else ("Installed" if sha == installed else "")}
            return {"versions": [row], "more": False, "note": "No releases are published: showing the latest commit only; version history is unavailable"}
        out = []
        for r in rows:
            v = parse_version(r["tag"])
            down = bool(v and cur and v < cur)
            now_ = bool(v and cur and v == cur)
            reason = ""
            if prot:
                reason = FORWARD_ONLY if down else PROTECTED_UPDATE
            elif v is None:
                reason = "Unrecognised version tag"
            elif now_:
                reason = "Installed: use Repair to reinstall it"
            elif r["prerelease"] and stable:
                reason = "Prerelease: switch the channel to Prerelease first"
            compat = {"badge": "unknown", "msg": "Checked before install"}
            if now_ and m is not None:
                bad = host.gate(m)
                compat = {"badge": "unknown" if bad else "ok", "msg": bad}
            out.append({"tag": r["tag"], "version": r["version"], "commit": r["commit"], "prerelease": r["prerelease"], "published": r["published"], "label": self._label(r["prerelease"]), "current": now_, "compat": compat, "downgrade": down, "eligible": not reason, "reason": reason})
        return {"versions": out, "more": more}

    @_safe
    async def notes(self, pid, tag):
        if not (isinstance(tag, str) and TAG.fullmatch(tag)):
            raise UpdateError("bad_tag", "unrecognised version tag")
        m, g, curver = self._source_of(pid)
        for (o, r, _), (_, rows, _) in list(self._cache.items()):
            if (o, r) == (g[0], g[1]):
                for x in rows:
                    if x["tag"] == tag:
                        return {"notes": x["body"], "url": x["url"]}
        try:
            r = _rel(await asyncio.to_thread(self.host.api_json, "%s/%s/releases/tags/%s" % (g[0], g[1], tag)))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            r = None
        return {"notes": r["body"] if r else "", "url": r["url"] if r else ""}

    async def _remote_meta(self, g, sha):
        try:
            j = await asyncio.to_thread(self.host.api_json, "%s/%s/contents/plugin.json?ref=%s" % (g[0], g[1], sha))
            meta = _loads(base64.b64decode(j["content"]).decode("utf-8"))
            return meta if isinstance(meta, dict) and isinstance(meta.get("version", ""), str) else None
        except Exception:
            return None

    def _skip(self, m, dest, s):
        pid = m["id"]
        g, msg = self.host.source(m)
        if not g or not dest:
            return "no source"
        if _priv(m):
            return "protected: updates from its own card"
        if _linked(self.host.plugin_dir(pid)):
            return "linked folder"
        if pid in _off(s):
            return "disabled"
        if pid in _frozen(s):
            return "frozen"
        if self._pending(m, dest):
            return "pending update"
        return ""

    @_safe
    async def batch_plan(self, ids=None):
        host = self.host
        s = host.settings()
        ms = {m["id"]: m for m in host.manifests()}
        if ids is None:
            order = sorted(ms)
        elif isinstance(ids, list) and len(ids) <= BATCH_MAX and all(_ok(x) for x in ids):
            order = list(dict.fromkeys(ids))
        else:
            raise UpdateError("bad_batch", "at most %d valid module ids per batch" % BATCH_MAX)
        rows, budget = [], BATCH_MAX
        for pid in order:
            m = ms.get(pid)
            if not m:
                rows.append({"id": pid, "name": pid, "from": "", "to": "", "eligible": False, "reason": "not installed"})
                continue
            row = {"id": pid, "name": m.get("name", pid), "from": m.get("version", ""), "to": "", "eligible": False, "reason": ""}
            rows.append(row)
            row["reason"] = self._skip(m, host.real(pid), s)
            if row["reason"]:
                continue
            if budget <= 0:
                row["reason"] = "not checked: limit of %d reached" % BATCH_MAX
                continue
            budget -= 1
            try:
                await self._probe_row(m, row)
            except Exception as e:
                row["reason"] = why(e)
        return {"items": rows}

    async def _probe_row(self, m, row):
        host = self.host
        pid = m["id"]
        g, _ = host.source(m)
        sha = await asyncio.to_thread(host.latest, *g)
        if sha == self._sha(pid):
            row["to"], row["reason"] = row["from"], "current"
            return
        meta = await self._remote_meta(g, sha)
        row["to"] = meta.get("version") or sha[:7] if meta else sha[:7]
        bad = host.gate(meta) if meta else ""
        if bad:
            row["reason"] = "incompatible: " + bad
        else:
            row["eligible"] = True

    @_safe
    async def batch_apply(self, ids):
        host = self.host
        if not (isinstance(ids, list) and 0 < len(ids) <= BATCH_MAX and all(_ok(x) for x in ids)):
            raise UpdateError("bad_batch", "between 1 and %d valid module ids per batch" % BATCH_MAX)
        s = host.settings()
        ms = {m["id"]: m for m in host.manifests()}
        results = []
        for pid in dict.fromkeys(ids):
            m = ms.get(pid)
            row = {"id": pid, "name": m.get("name", pid) if m else pid, "state": "failed", "from": m.get("version", "") if m else "", "to": ""}
            results.append(row)
            if not m:
                row.update(state="skipped", reason="not installed")
                continue
            try:
                reason = self._skip(m, host.real(pid), s)
                if reason:
                    row.update(state="skipped", reason=reason)
                else:
                    row.update(await self._update_one(m))
            except UpdateError as e:
                if e.code == "incompatible":
                    row.update(state="skipped", reason=e.message)
                else:
                    row.update(state="failed", error=e.message)
            except Exception as e:
                row.update(state="failed", error=why(e))
        await self._after(host.settings())
        return {"results": results, "ok": sum(r["state"] == "ok" for r in results), "failed": sum(r["state"] == "failed" for r in results)}

    async def _update_one(self, m):
        host = self.host
        pid = m["id"]
        _, dest, g = self._plugin(pid)
        job = self._job(pid, STAGES)
        sha = await self._run(job, "check", host.latest, *g)
        if sha == self._sha(pid):
            return {"state": "current", "to": m.get("version", "")}
        r = await self._replace(job, pid, m, dest, g, sha, kind="commit", confirm=False)
        if "confirm" in r:
            return {"state": "skipped", "reason": "the newest commit holds an older version than the installed one"}
        return {"state": "ok", "to": r["version"]}

    @staticmethod
    def _nothing():
        return None

    @_safe
    async def install_version(self, pid, tag, confirm=False):
        if pid == "_loader":
            return await self._loader_version(tag, confirm)
        host = self.host
        m, dest, g = self._plugin(pid)
        tag = tag if isinstance(tag, str) else ""
        if not (TAG.fullmatch(tag) or SHA.fullmatch(tag)):
            raise UpdateError("bad_tag", "unrecognised version tag")
        want = parse_version(tag)
        if _priv(m):
            cur = parse_version(m.get("version", ""))
            raise UpdateError("protected", FORWARD_ONLY if want and cur and want < cur else PROTECTED_UPDATE)
        if pid in _frozen(host.settings()):
            raise UpdateError("frozen", "This module is frozen at %s: unfreeze it first" % (m.get("version") or "its current version"))
        if not SHA.fullmatch(tag) and want is None:
            raise UpdateError("bad_tag", "unrecognised version tag")
        if is_pre(tag) and self._channel() != "prerelease":
            raise UpdateError("prerelease", "Prerelease versions need the Prerelease channel (Settings)")
        job = self._job(pid, STAGES)
        sha = await self._run(job, "check", self._resolve, g, tag)
        if sha == self._sha(pid):
            return {"current": True}
        have = parse_version(m.get("version", ""))
        return await self._pushed(self._replace(job, pid, m, dest, g, sha, kind="commit" if SHA.fullmatch(tag) else "release", tag="" if SHA.fullmatch(tag) else tag, confirm=confirm, lower=bool(want and have and want < have)))

    def _resolve(self, g, tag):
        return tag if SHA.fullmatch(tag) else self.host.latest(g[0], g[1], tag)

    def _identity(self, pid):
        d = self.host.plugin_dir(pid)
        raw = _read(d / ".identity.json", 4096)
        if raw:
            try:
                j = _loads(raw)
            except ValueError:
                j = None
            if isinstance(j, dict):
                if j.get("kind") in ("commit", "release") and isinstance(j.get("commit"), str) and SHA.fullmatch(j["commit"]):
                    return {"kind": j["kind"], "commit": j["commit"], "tag": j["tag"] if isinstance(j.get("tag"), str) and TAG.fullmatch(j["tag"]) else ""}
                raise UpdateError("no_identity", "cannot repair: no immutable source recorded")
        v = self._sha(pid)
        if v:
            return {"kind": "commit", "commit": v, "tag": ""}
        raise UpdateError("no_identity", "cannot repair: no immutable source recorded")

    @_safe
    async def repair(self, pid, reset=False, confirm=False):
        m, dest, g = self._plugin(pid)
        if _priv(m):
            raise UpdateError("protected", "Protected components cannot be repaired here: run Install / Update in the Framey App")
        ident = self._identity(pid)
        if reset and not confirm:
            return {"confirm": "Reset also deletes this module's saved settings and cannot be undone. Everything else is kept."}
        job = self._job(pid, STAGES)
        await self._run(job, "check", self._nothing)
        return await self._pushed(self._replace(job, pid, m, dest, g, ident["commit"], kind=ident["kind"], tag=ident["tag"], confirm=True, reset=reset))

    def _archive(self, g, sha, job):
        fn = self.host.archive
        try:
            takes = "progress" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            takes = False
        return fn(g[0], g[1], sha, progress=job.progress) if takes else fn(g[0], g[1], sha)

    def _vet(self, data, pid):
        v = self.host.vet(data)
        if isinstance(v, dict):
            raise UpdateError("bad_package", v.get("error") or "the package is not valid")
        z, infos, base, vid, meta = v
        if vid != pid:
            raise UpdateError("wrong_plugin", "rejected: the package is plugin %s, not %s" % (vid, pid))
        bad = self.host.gate(meta)
        if bad:
            raise UpdateError("incompatible", bad)
        return z, infos, base, meta

    async def _replace(self, job, pid, m, dest, g, sha, *, kind, tag="", confirm=False, reset=False, lower=False):
        host = self.host
        if _linked(host.plugin_dir(pid)):
            raise UpdateError("bad_link", LINKED)
        data =await self._run(job, "download", self._archive, g, sha, job)
        z, infos, base, meta = await self._run(job, "verify", self._vet, data, pid)
        new, cur = parse_version(meta.get("version", "")), parse_version(m.get("version", ""))
        down = lower or bool(new and cur and new < cur) or (new is None) != (cur is None)
        if down and not confirm:
            return {"confirm": CONFIRM % (meta.get("version") or "the older version"), "downgrade": True}
        d = host.plugin_dir(pid)
        src = _read(d / ".source")
        ident = {"kind": kind, "source": "%s/%s" % (g[0], g[1]), "commit": sha}
        if tag:
            ident["tag"] = tag
        where = ["unpack"]

        def fill(stage):
            job.emit_sync("unpack", "running")
            host.unpack(z, infos, base)(stage)
            (stage / ".source").unlink(missing_ok=True)
            if src:
                (stage / ".source").write_text(src)
            (stage / ".version").write_text(sha)
            (stage / ".identity.json").write_text(json.dumps(ident))
            job.emit_sync("unpack", "ok")
            where[0] = "swap"
            job.emit_sync("swap", "running")

        old, hold = dest.with_name(".old-" + dest.name), dest.with_name(".hold-" + dest.name)
        if new == cur and old.is_dir():
            old.rename(hold)
        try:
            try:
                await asyncio.to_thread(host.stage_swap, pid, fill, dest, True)
            except Exception as e:
                err = UpdateError("failed", why(e), where[0])
                await job.emit(where[0], "fail", err.message)
                raise err
            await job.emit("swap", "ok")
            try:
                await self._run(job, "reload", self._reload, pid, reset)
            except UpdateError as err:
                back = False
                try:
                    host.restore(dest)
                    await host.reload(pid)
                    back = True
                except Exception as e:
                    host.log("restore after failed reload:", e)
                if err.code == "start_failed":
                    err.message = (err.message + ("; the previous version was restored" if back else "; restoring the previous version failed"))[:300]
                raise
        finally:
            if hold.is_dir():
                shutil.rmtree(old, ignore_errors=True)
                hold.rename(old)
        self._mark(pid, sha[:7])
        version = (host.manifest(d) or {}).get("version", "")
        return {"ok": pid, "version": version, **({"kept_previous": True} if down else {})}

    @_safe
    async def update(self, pid):
        m, dest, g = self._plugin(pid)
        if self._pending(m, dest):
            raise UpdateError("pending", "finish or roll back the pending update first")
        job = self._job(pid, STAGES)
        sha = await self._run(job, "check", self.host.latest, *g)
        if sha == self._sha(pid):
            return {"current": True}
        return await self._pushed(self._replace(job, pid, m, dest, g, sha, kind="commit", confirm=True))

    async def _reload(self, pid, reset):
        c = self.host.HOME / ".config" / "framey" / "plugins" / pid
        if reset and c.is_symlink():
            raise UpdateError("bad_link", "the settings folder is a link: refusing to delete it")
        await self.host.reload(pid)
        if reset:
            shutil.rmtree(c, ignore_errors=True)
        check = getattr(self.host, "backend_check", None)
        bad = await check(pid) if check else ""
        if bad:
            raise UpdateError("start_failed", "The new version could not start (%s)" % bad)

    async def loader_candidate(self, channel=None):
        host = self.host
        ch = self._channel(channel)
        try:
            if ch == "stable":
                r = _rel(await asyncio.to_thread(host.api_json, "%s/%s/releases/latest" % LOADER))
            else:
                raw = await asyncio.to_thread(host.api_json, "%s/%s/releases?per_page=%d" % (LOADER + (PAGE,)))
                r = next((x for x in map(_rel, raw if isinstance(raw, list) else []) if x), None)
            if not r:
                raise ValueError("unexpected reply from GitHub")
            sha = await asyncio.to_thread(host.latest, LOADER[0], LOADER[1], r["tag"])
        except Exception as e:
            return {"error": why(e), "channel": ch}
        return {"tag": r["tag"], "commit": sha, "prerelease": r["prerelease"], "published": r["published"], "label": self._label(r["prerelease"]), "channel": ch, "notes": r["body"][:600]}

    def loader_allowed(self, cand, confirm=False):
        if not isinstance(cand, dict) or not cand.get("tag"):
            return {"error": "no loader version selected", "code": "no_candidate"}
        if cand.get("prerelease") and self._channel() != "prerelease":
            return {"error": "Prerelease versions need the Prerelease channel (Settings)", "code": "prerelease"}
        new, cur = parse_version(cand["tag"]), parse_version(self.host.VERSION)
        if (new is None or (cur and new < cur)) and not confirm:
            return {"confirm": CONFIRM % cand["tag"]}
        return None

    async def _loader_version(self, tag, confirm):
        host = self.host
        if not (isinstance(tag, str) and TAG.fullmatch(tag)) or SHA.fullmatch(tag):
            raise UpdateError("bad_tag", "unrecognised version tag")
        fn = getattr(host, "loader_install", None)
        if not fn:
            raise UpdateError("unavailable", "Installing a chosen Framey version is not available")
        sha = await asyncio.to_thread(host.latest, LOADER[0], LOADER[1], tag)
        cand = {"tag": tag, "commit": sha, "prerelease": is_pre(tag)}
        verdict = self.loader_allowed(cand, confirm)
        if verdict:
            return verdict
        return await fn(tag, sha)

    @_safe
    async def loader_check(self):
        cand = await self.loader_candidate()
        if cand.get("error"):
            return {"loader": {"state": "error", "msg": cand["error"]}}
        cur = self._loader_sha()
        same = cand["commit"] == cur
        new, old = parse_version(cand["tag"]), parse_version(self.host.VERSION)
        down = not same and bool(new and old and new < old)
        state = "current" if same else "update"
        installed = {"version": self.host.VERSION, "commit": cur[:7], "tag": cand["tag"] if same else ""}
        loader = {"state": state, "latest": cand["tag"], "installed": cand["tag"] if same else cur[:7], "candidate": {k: cand[k] for k in ("tag", "commit", "prerelease", "published", "label", "channel")}, "downgrade": down, "channel_fallback": self._pref("channel") == "prerelease" and self._channel() != "prerelease"}
        if state == "update":
            loader["notes"] = cand["notes"]
        stored = "current" if same else ("downgrade" if down else "update")
        try:
            s = self._store(lambda u: u["loader"].update(tag=cand["tag"], state=stored))
            await self._notify(s)
            await self._after(s)
        except Exception as e:
            self.host.log("loader check bookkeeping failed:", e)
        return {"loader": loader, "candidate": loader["candidate"], "installed": installed}

    def badge(self, state=None):
        host = self.host
        s = state if isinstance(state, dict) else host.settings()
        u = _norm(s.get("updates"))
        off, frozen = _off(s), _frozen(s)
        ids = {m["id"] for m in host.manifests()}
        n = sum(1 for pid, r in u["plugins"].items() if pid in ids and pid not in off and pid not in frozen and r.get("state") == "update")
        loader = u["loader"]["state"] == "update"
        return {"n": n + int(loader), "loader": loader}

    def autocheck_status(self):
        s = self.host.settings()
        u = _norm(s.get("updates"))
        return {"enabled": _prefs(s).get("autocheck") is True, "last": u["last"], "next_ok": u["next_ok"], "fails": u["fails"]}

    def due(self, now, floor=DAY, state=None):
        s = state if isinstance(state, dict) else self.host.settings()
        if _prefs(s).get("autocheck") is not True:
            return False
        u = _norm(s.get("updates"))
        last = u["last"] if u["last"] <= now else 0
        wait = u["next_ok"] if u["next_ok"] <= now + FLOOR else 0
        return now >= wait and now - last >= floor

    def arm_autocheck(self, delay=None):
        if self._timer is not None or self._pref("autocheck") is not True:
            return
        self._timer = self.host.loop().call_later(delay or 90 + self.host.rand() * 300, self._fire)

    def disarm(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _fire(self):
        self._timer = None
        self._task = self.host.loop().create_task(self._cycle(FLOOR))
        self.arm_autocheck(DAY)

    async def on_panel_open(self):
        await self._cycle(DAY)

    async def _cycle(self, floor):
        host = self.host
        if self._running or not self.due(host.now(), floor):
            return
        self._running = True
        try:
            await self._check_all(host.now())
        except Exception as e:
            host.log("automatic check failed:", e)
        finally:
            self._running = False

    async def _check_all(self, now):
        host = self.host
        s = host.settings()
        off, frozen = _off(s), _frozen(s)
        prior = _norm(s.get("updates"))
        ms = [m for m in host.manifests() if m["id"] not in off and m["id"] not in frozen]
        ms.sort(key=lambda m: (m["id"] in prior["plugins"], m["id"]))
        errors, good, limited, recs = [], 0, False, {}
        cand = await self.loader_candidate()
        loader = None
        if cand.get("error"):
            errors.append(cand["error"])
            limited = "rate limit" in cand["error"]
        else:
            good += 1
            cur = self._loader_sha()
            new, old = parse_version(cand["tag"]), parse_version(host.VERSION)
            loader = "current" if cand["commit"] == cur else ("downgrade" if new and old and new < old else "update")
        first = True
        for m in ms[:CYCLE_MAX]:
            if limited:
                break
            if not first and self.SPACING:
                await asyncio.sleep(self.SPACING)
            first = False
            try:
                r = await asyncio.to_thread(self._probe, m)
            except Exception as e:
                errors.append(why(e))
                limited = "rate limit" in errors[-1]
                continue
            good += 1
            if r["state"] in ("update", "current"):
                recs[m["id"]] = {"latest": r["latest"], "state": r["state"]}
        fails = prior["fails"]

        def mutate(u):
            ids = {m["id"] for m in host.manifests()}
            u["plugins"] = {k: v for k, v in u["plugins"].items() if k in ids}
            u["plugins"].update(recs)
            if loader:
                u["loader"].update(tag=cand["tag"], state=loader)
            if errors and not good:
                u["fails"] = fails + 1
                u["next_ok"] = now + (FLOOR if limited else min(FLOOR, 900 * 2 ** min(fails, 10)))
            elif limited:
                u["last"], u["fails"], u["next_ok"] = now, fails + 1, now + FLOOR
            else:
                u["last"], u["fails"], u["next_ok"] = now, 0, 0

        s = self._store(mutate)
        await self._notify(s)
        await self._after(s)
        if self.cycled:
            try:
                self.cycled()
            except Exception as e:
                host.log("maintenance hook failed:", e)

    def _probe(self, m):
        host = self.host
        g, _ = host.source(m)
        if not g:
            return {"state": "none", "latest": ""}
        if _priv(m):
            tag = host.release(g[0], g[1])["tag"]
            new, cur = parse_version(tag), parse_version(m.get("version", ""))
            if not new:
                raise ValueError("unrecognised release tag " + tag)
            return {"state": "update" if cur is None or new > cur else "current", "latest": tag}
        sha = host.latest(*g)
        return {"state": "current" if sha == self._sha(m["id"]) else "update", "latest": sha[:7]}

    async def record_checked(self, checked):
        recs = {k: {"latest": v.get("latest", ""), "state": v["state"]} for k, v in (checked or {}).items() if _ok(k) and isinstance(v, dict) and v.get("state") in ("update", "current") and isinstance(v.get("latest", ""), str)}
        s = self._store(lambda u: u["plugins"].update(recs))
        await self._notify(s)
        await self._after(s)

    async def _pushed(self, pending):
        r = await pending
        await self._after(self.host.settings())
        return r

    async def _after(self, s):
        if self.after:
            try:
                await self.after(self.badge(s))
            except Exception as e:
                self.host.log("badge push failed:", e)

    async def _notify(self, s):
        host = self.host
        prefs, u = _prefs(s), _norm(s.get("updates"))
        if not host.attached("main"):
            return
        off, frozen = _off(s), _frozen(s)
        ms = {m["id"]: m for m in host.manifests()}
        sent = {}
        tag = u["loader"]["tag"]
        if prefs.get("notify_loader") is True and u["loader"]["state"] == "update" and tag and tag != u["seen"]["loader"]:
            try:
                await host.notify_page("_core", "notice", {"title": "Framey update available", "body": _text("Framey %s is ready. Open Settings to review and update." % tag, 400), "actions": [{"id": "open:settings", "label": "Open Settings"}]})
                sent["loader"] = tag
            except Exception as e:
                host.log("notice failed:", e)
        if prefs.get("notify_plugins") is True:
            fresh = [(pid, r["latest"]) for pid, r in sorted(u["plugins"].items()) if pid in ms and pid not in off and pid not in frozen and r.get("state") == "update" and isinstance(r.get("latest"), str) and r["latest"] and u["seen"]["plugins"].get(pid) != r["latest"]]
            if fresh:
                names = ", ".join(ms[p].get("name", p) for p, _ in fresh[:5]) + (" and more" if len(fresh) > 5 else "")
                try:
                    await host.notify_page("_core", "notice", {"title": _text("Module updates available (%d)" % len(fresh), 80), "body": _text("Updates are ready for: " + names, 400), "actions": [{"id": "open:modules", "label": "Open Modules"}]})
                    sent["plugins"] = dict(fresh)
                except Exception as e:
                    host.log("notice failed:", e)
        if sent:
            def mark(x):
                if "loader" in sent:
                    x["seen"]["loader"] = sent["loader"]
                x["seen"]["plugins"].update(sent.get("plugins", {}))
                x["seen"]["plugins"] = {k: v for k, v in x["seen"]["plugins"].items() if k in ms}

            self._store(mark)

    @_safe
    async def companion_fetch(self, pid, name):
        host = self.host
        if _linked(host.plugin_dir(pid)):
            raise UpdateError("bad_link", LINKED)
        m, dest, g = self._plugin(pid)
        pkg = self._pkg()
        if pkg is None:
            raise UpdateError("unavailable", "Companion support is not available")
        spec = next((c for c in pkg.companions(m) if c.get("name") == name), None) if isinstance(name, str) else None
        if not spec:
            raise UpdateError("unknown_companion", "this module declares no companion with that name for this headset")
        job = self._job(pid, COMPANION_STAGES)
        asset = await self._run(job, "check", self._find_asset, g, m, spec)
        data = await self._run(job, "download", self._download, asset[0], job)
        state = await self._run(job, "verify", self._verify_asset, data, asset[1], spec)
        out = host.HOME / ".local" / "share" / "framey" / pid / "bin"
        res = await self._run(job, "unpack", pkg.install_companion, data, spec, out)
        folder = out / ("%s-%s" % (spec["name"], spec.get("version", "")))
        ex = (res or {}).get("exec") or []
        path = folder / ex[0] if ex else folder
        return {"ok": name, "path": str(path), "digest": state if state == "verified" else "no digest published", "note": INTEGRITY}

    def _find_asset(self, g, m, spec):
        host = self.host
        tags = []
        ident = _read(host.plugin_dir(m["id"]) / ".identity.json", 4096)
        try:
            t = _loads(ident).get("tag") if ident else None
        except ValueError:
            t = None
        for x in (t, "v" + m.get("version", ""), m.get("version", "")):
            if isinstance(x, str) and TAG.fullmatch(x) and x not in tags:
                tags.append(x)
        for tag in tags:
            try:
                r = _rel(host.api_json("%s/%s/releases/tags/%s" % (g[0], g[1], tag)))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    continue
                raise
            hit = r["assets"].get(spec["asset"]) if r else None
            if hit:
                parts = urllib.parse.urlsplit(hit[0])
                if parts.scheme == "https" and parts.hostname == "github.com" and parts.path.startswith("/%s/%s/releases/download/" % (g[0], g[1])):
                    return hit
                raise UpdateError("bad_asset", "the companion asset is not hosted by this module's own GitHub repository")
        raise UpdateError("no_asset", "the release has no companion file named " + str(spec["asset"]))

    def _download(self, url, job):
        fn = self.host.fetch
        try:
            takes = "progress" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            takes = False
        data = fn(url, {"User-Agent": "Framey"}, HOSTS, COMPANION_MAX, progress=job.progress) if takes else fn(url, {"User-Agent": "Framey"}, HOSTS, COMPANION_MAX)
        if len(data) > COMPANION_MAX:
            raise UpdateError("too_large", "the companion download is over 20 MB")
        return data

    def _verify_asset(self, data, digest, spec):
        state = check_digest(data, digest)
        if state == "mismatch":
            raise UpdateError("digest_mismatch", "the download does not match the digest GitHub published for it")
        want = str(digest).partition(":")[2].lower()
        if state == "verified" and isinstance(spec.get("sha256"), str) and spec["sha256"].lower() != want:
            raise UpdateError("digest_mismatch", "the digest GitHub published differs from the one the module declares")
        return state
