import asyncio
import base64
import json
import os
import re
import time
import urllib.parse

def _constant(name):
    raise ValueError(name)


def _loads(data):
    return json.loads(data, parse_constant=_constant)


API = "https://api.github.com/repos/"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "Framey"}
HOSTS = ("api.github.com",)
NAME = re.compile(r"[a-z0-9_-]{1,32}")
PART = re.compile(r"[A-Za-z0-9._-]{1,100}")
REPO = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
BRANCH = re.compile(r"[A-Za-z0-9._/-]{0,100}")
FILE = re.compile(r"[A-Za-z0-9._/-]{1,220}")
KEY = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}@[A-Za-z0-9._/-]{0,100}#[A-Za-z0-9._/-]{0,200}")
SHA = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"[A-Za-z0-9._-]{1,64}")
DAY_TEXT = re.compile(r"\d{4}-\d\d-\d\d")
STAMP = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)")
SEP = (",", ":")
FILE_MAX, ENTRIES_MAX, PLUGINS_MAX, IDS_MAX = 65536, 200, 200, 64
DAY = 86400
TTL, WEEK = DAY, 7 * DAY
CAP, SECONDS, PARALLEL, REPLY_MAX = 40, 25.0, 2, 500_000
COOLDOWN, WAIT, WAIT_MAX = 900, 900, 21600
WINDOW, WINDOW_MIN, WINDOW_MAX = 365, 30, 1825
CODES = ("rate_limited", "unavailable", "network", "unchecked", "no_source", "unsupported", "invalid_source")
PREF_KEYS = ("maint.enabled_background", "maint.window_days")
TITLES = {
    "archived": "Archived repository",
    "declared": "Maintainer-declared unmaintained",
    "recent": "Recent activity",
    "none": "No recent activity",
    "unknown": "Insufficient evidence",
}
UNKNOWN = {
    "no_source": "Installed from a ZIP with no upstream source, so there is nothing to check. This says nothing about whether the project is maintained.",
    "invalid_source": "The recorded source path is invalid, so its activity could not be checked. This says nothing about whether the project is maintained.",
    "unsupported": "The recorded source is not a GitHub repository, and only GitHub sources can be checked. This says nothing about whether the project is maintained.",
    "unavailable": "Could not check: GitHub did not provide this repository (not found, private or unavailable). That is not proof it was removed or abandoned.",
    "rate_limited": "Could not check: GitHub rate limit reached. Try again later.",
    "network": "Could not check: GitHub could not be reached or gave an unusable answer.",
    "unchecked": "Not checked yet.",
}
BUSY = {"on": False}


class Fail(Exception):
    def __init__(self, code, message, retry=0):
        super().__init__(message)
        self.code, self.text, self.retry = code, message, retry


class Skip(Exception):
    pass


def bad():
    return Fail("network", "Could not check: unexpected reply from GitHub")


def num(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 4_000_000_000_000 else 0


def text(v, n):
    return "".join(c for c in " ".join(v.split()) if c.isprintable())[:n].strip() if isinstance(v, str) else ""


def digits(v):
    v = v.strip() if isinstance(v, str) else ""
    return int(v) if v.isascii() and v.isdigit() and len(v) <= 12 else None


def window(v):
    if isinstance(v, bool) or not isinstance(v, int):
        return WINDOW
    return min(WINDOW_MAX, max(WINDOW_MIN, v))


def parse_stamp(v):
    import calendar
    import datetime
    m = STAMP.fullmatch(v) if isinstance(v, str) else None
    if not m:
        return None
    try:
        t = calendar.timegm(datetime.datetime(*map(int, m.groups()[:6])).timetuple())
    except ValueError:
        return None
    z = m[7]
    if z != "Z":
        if int(z[1:3]) > 23 or int(z[4:6]) > 59:
            return None
        t -= (1 if z[0] == "+" else -1) * (int(z[1:3]) * 3600 + int(z[4:6]) * 60)
    return t if 0 < t < 32503680000 else None


def fmt(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def stamp(v):
    t = parse_stamp(v)
    return "" if t is None else fmt(t)


def day_ok(v):
    import datetime
    if not (isinstance(v, str) and DAY_TEXT.fullmatch(v)):
        return False
    try:
        datetime.date(*map(int, v.split("-")))
    except ValueError:
        return False
    return True


def moment(v, now):
    t = parse_stamp(v)
    return (None, True) if t is not None and t > now + DAY else (t, False)


def clean_path(v):
    parts = [p for p in v.split("/") if p] if isinstance(v, str) else []
    if not parts or len(v) > 200 or any(p in (".", "..") or not PART.fullmatch(p) for p in parts):
        return None
    return "/".join(parts)


def parse_source(v):
    try:
        u = urllib.parse.urlsplit(v.strip())
    except ValueError:
        return None
    p = [urllib.parse.unquote(x) for x in u.path.strip("/").split("/")]
    if u.scheme != "https" or u.netloc.lower() != "github.com" or len(p) < 2 or any(not x for x in p) or (len(p) > 2 and (p[2] != "tree" or len(p) < 4)):
        return None
    if p[1].endswith(".git"):
        p[1] = p[1][:-4]
    branch = p[3] if len(p) > 3 else ""
    if any(x in (".", "..") or not PART.fullmatch(x) for x in p[:2] + ([branch] if branch else [])):
        return None
    sub = clean_path("/".join(p[4:])) if len(p) > 4 else ""
    return None if sub is None else {"owner": p[0], "repo": p[1], "branch": branch, "path": sub}


def read(path, n=2048):
    try:
        with open(path, "rb") as f:
            return f.read(n).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def valid_id(x):
    return isinstance(x, str) and NAME.fullmatch(x) is not None and x != "_core"


def describe(host, plugin):
    pid, m = None, {}
    if isinstance(plugin, dict):
        m, pid = plugin, plugin.get("id") if valid_id(plugin.get("id")) else None
    elif valid_id(plugin):
        pid, m = plugin, host.manifest(host.plugin_dir(plugin)) or {}
    elif isinstance(plugin, str):
        m = {"source": plugin}
    recorded = read(host.plugin_dir(pid) / ".source") if pid else ""
    found = [x.strip() for x in (recorded, m.get("source")) if isinstance(x, str) and x.strip()]
    for x in found:
        g = parse_source(x)
        if not g and x == recorded:
            try:
                u = urllib.parse.urlsplit(x)
                scoped = u.hostname == "github.com" and len([p for p in u.path.split("/") if p]) > 2
            except ValueError:
                scoped = False
            if scoped:
                return {"id": pid, "source": text(x, 200), "unknown": "invalid_source"}
        if g:
            sub = g["path"]
            if not sub and m.get("source_path") not in (None, ""):
                sub = clean_path(m["source_path"])
                if sub is None:
                    return {"id": pid, "source": text(x, 200), "unknown": "invalid_source"}
            key = "%s/%s@%s#%s" % (g["owner"].lower(), g["repo"].lower(), g["branch"], sub)
            return {"id": pid, "source": text(x, 200), "key": key, **g, "path": sub}
    return {"id": pid, "source": text(found[0], 200) if found else "", "unknown": "unsupported" if found else "no_source"}


def blank():
    return {"v": 1, "prefs": {"enabled_background": False, "window_days": WINDOW}, "bg_next": 0, "limit": None, "entries": {}}


def clean_decl(v):
    if not isinstance(v, dict):
        return None
    by, file, commit = text(v.get("by"), 80), v.get("file"), v.get("commit")
    if not by or not (isinstance(file, str) and FILE.fullmatch(file)):
        return None
    return {"by": by, "note": text(v.get("note"), 300), "date": v["date"] if day_ok(v.get("date")) else "", "file": file, "commit": commit if isinstance(commit, str) and SHA.fullmatch(commit) else ""}


def clean_facts(v):
    if not isinstance(v, dict):
        return None
    repo, branch, path = v.get("repo"), v.get("branch"), v.get("path")
    if not (isinstance(repo, str) and REPO.fullmatch(repo) and isinstance(branch, str) and BRANCH.fullmatch(branch) and isinstance(path, str) and (path == "" or clean_path(path) == path)):
        return None
    fork, tag = v.get("fork_of"), v.get("release_tag")
    return {
        "repo": repo,
        "branch": branch,
        "path": path,
        "archived": v.get("archived") is True,
        "fork_of": fork if isinstance(fork, str) and REPO.fullmatch(fork) else "",
        "commit_at": stamp(v.get("commit_at")),
        "author_at": stamp(v.get("author_at")),
        "release_at": stamp(v.get("release_at")),
        "release_tag": tag if isinstance(tag, str) and TAG.fullmatch(tag) else "",
        "decl": clean_decl(v.get("decl")),
        "decl_note": text(v.get("decl_note"), 160),
    }


def clean_fail(v):
    if not isinstance(v, dict) or v.get("code") not in CODES or not text(v.get("reason"), 300):
        return None
    return {"reason": text(v["reason"], 300), "at": num(v.get("at")), "code": v["code"]}


def clean_entry(k, v):
    if not (isinstance(k, str) and KEY.fullmatch(k) and isinstance(v, dict)):
        return None
    facts, ok_at, fail = clean_facts(v.get("facts")), num(v.get("ok_at")), clean_fail(v.get("fail"))
    if not (facts and ok_at):
        facts, ok_at = None, 0
        if not fail:
            return None
    return {"ok_at": ok_at, "facts": facts, "fail": fail, "retry_after": num(v.get("retry_after"))}


def load(host, now=None):
    s = blank()
    try:
        with open(host.CONF.parent / "maint.json", "rb") as f:
            data = f.read(FILE_MAX + 1)
        raw = _loads(data) if len(data) <= FILE_MAX else None
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        return s
    p = raw.get("prefs") if isinstance(raw.get("prefs"), dict) else {}
    s["prefs"] = {"enabled_background": p.get("enabled_background") is True, "window_days": window(p.get("window_days"))}
    now = int(host.now() if now is None else now)
    s["bg_next"] = min(num(raw.get("bg_next")), now + WEEK + DAY)
    lim = raw.get("limit")
    if isinstance(lim, dict) and num(lim.get("until")) and text(lim.get("reason"), 300):
        s["limit"] = {"until": min(num(lim["until"]), now + WAIT_MAX), "at": min(num(lim.get("at")), now), "reason": text(lim["reason"], 300)}
    ents = raw.get("entries") if isinstance(raw.get("entries"), dict) else {}
    for k, v in list(ents.items())[:ENTRIES_MAX * 2]:
        e = clean_entry(k, v)
        if e:
            if e["ok_at"] > now:
                e["ok_at"], e["facts"] = 0, None
                if not e["fail"]:
                    continue
            if e["fail"]:
                e["fail"]["at"] = min(e["fail"]["at"], now)
            bound = WAIT_MAX if e["fail"] and e["fail"]["code"] == "rate_limited" else COOLDOWN
            e["retry_after"] = min(e["retry_after"], now + bound)
            s["entries"][k] = e
    trim(s)
    return s


def trim(s):
    ents = s["entries"]
    total = len(json.dumps({**s, "entries": {}}, separators=SEP))
    sizes = {k: len(json.dumps(k, separators=SEP)) + len(json.dumps(v, separators=SEP)) + 2 for k, v in ents.items()}
    total += sum(sizes.values())
    for k in list(ents):
        if len(ents) <= ENTRIES_MAX and total <= FILE_MAX:
            break
        total -= sizes[k]
        del ents[k]


def write(path, data):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(host, s):
    trim(s)
    try:
        write(host.CONF.parent / "maint.json", json.dumps(s, separators=SEP).encode("ascii"))
    except OSError as e:
        host.log("maintenance cache not saved:", e)
        return False
    return True


def apply(s, key, outcome, now):
    old = s["entries"].pop(key, None) or {"ok_at": 0, "facts": None}
    kind, val = outcome
    facts = clean_facts(val) if kind == "ok" else None
    if facts:
        s["entries"][key] = {"ok_at": now, "facts": facts, "fail": None, "retry_after": 0}
        return
    f = val if kind == "fail" else bad()
    limited = f.code == "rate_limited"
    s["entries"][key] = {"ok_at": old["ok_at"], "facts": old["facts"], "fail": {"reason": text(f.text, 300), "at": now, "code": f.code}, "retry_after": f.retry if limited else now + COOLDOWN}
    if limited and (not s["limit"] or s["limit"]["until"] < f.retry):
        s["limit"] = {"until": f.retry, "at": now, "reason": text(f.text, 300)}


def failure(e, now):
    h = e.headers or {}
    try:
        body = e.read(2000).decode("utf-8", "replace").lower()
    except Exception:
        body = ""
    left, retry, reset = h.get("X-RateLimit-Remaining"), h.get("Retry-After"), h.get("X-RateLimit-Reset")
    if e.code == 429 or (e.code == 403 and (left == "0" or retry is not None or "rate limit" in body)):
        if digits(retry) is not None:
            until = now + digits(retry)
        elif digits(reset) is not None and left in (None, "0"):
            until = digits(reset)
        else:
            until = now + WAIT
        until = min(max(until, now + 1), now + WAIT_MAX)
        return Fail("rate_limited", "Could not check: GitHub rate limit reached; try again after " + fmt(until), until)
    if e.code in (401, 403, 404, 410, 451):
        return Fail("unavailable", "Could not check: GitHub answered HTTP %d (the repository or branch may be private, renamed or unavailable; this is not proof it was removed or abandoned)" % e.code)
    return Fail("network", "Could not check: GitHub answered HTTP %d" % e.code)


def parse_decl(data, file, commit):
    try:
        doc = _loads(data)
        if isinstance(doc, dict) and "encoding" in doc:
            if doc.get("encoding") != "base64" or not isinstance(doc.get("content"), str):
                raise ValueError
            encoded = re.sub(r"[ \t\r\n]", "", doc["content"])
            doc = _loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, RecursionError):
        return None, "plugin.json could not be parsed; any maintenance declaration was ignored"
    if not isinstance(doc, dict):
        return None, "plugin.json is not an object; any maintenance declaration was ignored"
    if "maintenance" not in doc:
        return None, ""
    m = doc["maintenance"]
    if not isinstance(m, dict):
        return None, "The maintenance field is not an object; ignored"
    if m.get("status") != "unmaintained":
        return None, "maintenance.status is not 'unmaintained'; ignored"
    by = text(m.get("by"), 80)
    if not by:
        return None, "The maintenance declaration names nobody in 'by'; ignored"
    return {"by": by, "note": text(m.get("note"), 300), "date": m["date"] if day_ok(m.get("date")) else "", "file": file, "commit": commit}, ""


class Run:
    def __init__(self, host, now):
        self.host, self.now = host, now
        self.slots, self.keys = asyncio.Semaphore(PARALLEL), asyncio.Semaphore(PARALLEL)
        self.deadline = host.mono() + SECONDS
        self.count, self.limit, self.memo, self.done = 0, None, {}, {}
        self.fetches = set()

    def fetch(self, url):
        try:
            return self.host.fetch(url, HEADERS, HOSTS, REPLY_MAX)
        except Exception as e:
            return e

    async def close(self):
        import urllib.error
        for task in self.memo.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.memo.values(), return_exceptions=True)
        for result in await asyncio.gather(*self.fetches, return_exceptions=True):
            if isinstance(result, urllib.error.HTTPError):
                result.close()

    def once(self, key, make):
        if key not in self.memo:
            self.memo[key] = asyncio.ensure_future(make())
        return self.memo[key]

    async def get(self, url, missing=(), raw=False):
        import urllib.error
        async with self.slots:
            if self.limit or self.count >= CAP or self.host.mono() > self.deadline:
                raise Skip()
            self.count += 1
            try:
                task = asyncio.ensure_future(asyncio.to_thread(self.fetch, url))
                self.fetches.add(task)
                try:
                    data = await asyncio.shield(task)
                    if isinstance(data, Exception):
                        raise data
                finally:
                    if task.done():
                        self.fetches.discard(task)
            except urllib.error.HTTPError as e:
                try:
                    if e.code in missing:
                        return None
                    f = failure(e, self.now)
                finally:
                    e.close()
                if f.code == "rate_limited" and (not self.limit or self.limit.retry < f.retry):
                    self.limit = f
                raise f from None
            except Exception:
                raise Fail("network", UNKNOWN["network"]) from None
        if raw:
            return data
        try:
            return _loads(data)
        except ValueError:
            raise bad() from None

    async def repo(self, base, o, r):
        j = await self.get(base)
        if not isinstance(j, dict) or not isinstance(j.get("archived"), bool):
            raise bad()
        par, db, full = j.get("parent"), j.get("default_branch"), j.get("full_name")
        pf = par.get("full_name") if isinstance(par, dict) else None
        return {
            "full": full if isinstance(full, str) and REPO.fullmatch(full) else o + "/" + r,
            "archived": j["archived"],
            "fork_of": pf if j.get("fork") is True and isinstance(pf, str) and REPO.fullmatch(pf) else "",
            "branch": db if isinstance(db, str) and BRANCH.fullmatch(db) else "",
        }

    async def commit(self, base, branch, path):
        q = ("sha=%s&" % urllib.parse.quote(branch, safe="") if branch else "") + "per_page=1" + ("&path=" + urllib.parse.quote(path, safe="/") if path else "")
        j = await self.get(base + "/commits?" + q, missing=(409,))
        if j is None or j == []:
            return {"at": "", "author": "", "sha": ""}
        c = j[0] if isinstance(j, list) and isinstance(j[0], dict) else {}
        c = c.get("commit") if isinstance(c.get("commit"), dict) else {}
        by = {k: stamp(c[k].get("date")) if isinstance(c.get(k), dict) else "" for k in ("committer", "author")}
        if not (by["committer"] or by["author"]):
            raise bad()
        sha = j[0].get("sha")
        at, au = by["committer"] or by["author"], by["author"]
        return {"at": at, "author": au if au[:10] != at[:10] else "", "sha": sha if isinstance(sha, str) and SHA.fullmatch(sha) else ""}

    async def release(self, base):
        j = await self.get(base + "/releases/latest", missing=(404,))
        if j is None:
            return {"at": "", "tag": ""}
        if not isinstance(j, dict) or any(k in j and j[k] is not False for k in ("draft", "prerelease")):
            raise bad()
        at = stamp(j.get("published_at"))
        if not at:
            raise bad()
        tag = j.get("tag_name")
        return {"at": at, "tag": tag if isinstance(tag, str) and TAG.fullmatch(tag) else ""}

    async def decl(self, base, ref, path):
        file = (path + "/" if path else "") + "plugin.json"
        url = base + "/contents/" + urllib.parse.quote(file, safe="/") + ("?ref=" + urllib.parse.quote(ref, safe="") if ref else "")
        data = await self.get(url, missing=(404,), raw=True)
        return (None, "") if data is None else parse_decl(data, file, ref if SHA.fullmatch(ref) else "")

    async def collect(self, info):
        o, r, path = info["owner"], info["repo"], info["path"]
        base = API + urllib.parse.quote(o, safe="") + "/" + urllib.parse.quote(r, safe="")
        low = (o.lower(), r.lower())
        meta = await self.once(("repo",) + low, lambda: self.repo(base, o, r))
        branch = info["branch"] or meta["branch"]
        got = await asyncio.gather(self.once(("commit",) + low + (branch, path), lambda: self.commit(base, branch, path)), self.once(("release",) + low, lambda: self.release(base)), return_exceptions=True)
        for g in got:
            if isinstance(g, BaseException):
                raise g
        last, rel = got
        facts = {
            "repo": meta["full"],
            "branch": branch,
            "path": path,
            "archived": meta["archived"],
            "fork_of": meta["fork_of"],
            "commit_at": last["at"],
            "author_at": last["author"],
            "release_at": rel["at"],
            "release_tag": rel["tag"],
            "decl": None,
            "decl_note": "",
        }
        if not meta["archived"]:
            ref = last["sha"] or branch
            facts["decl"], facts["decl_note"] = await self.once(("decl",) + low + (ref, path), lambda: self.decl(base, ref, path))
        return facts

    async def one(self, info):
        async with self.keys:
            try:
                out = ("ok", await self.collect(info))
            except Fail as f:
                out = ("fail", f)
            except Skip:
                out = None
            except Exception:
                out = ("fail", Fail("network", UNKNOWN["network"]))
        if out:
            self.done[info["key"]] = out


def classify(facts, now, window_days):
    w = window(window_days)
    f = facts if isinstance(facts, dict) else {}
    if f.get("unknown") or not f.get("repo"):
        code = f.get("unknown") if f.get("unknown") in UNKNOWN else "unchecked"
        src = text(f.get("source"), 200)
        return {"label": "unknown", "title": TITLES["unknown"], "reason": UNKNOWN[code], "evidence": ["Recorded source: " + src] if src else []}
    path, fork, decl = f.get("path") or "", f.get("fork_of") or "", f.get("decl")
    ct, cf = moment(f.get("commit_at"), now)
    rt, rf = moment(f.get("release_at"), now)
    ev = ["Repository: " + f["repo"] + (" (fork of %s)" % fork if fork else "")]
    if fork:
        ev.append("This fork was assessed itself, not the project it was forked from")
    ev.append("Branch checked: " + (f.get("branch") or "default branch"))
    ev.append("Describes only changes under " + path if path else "Describes the whole repository, not only this plugin")
    if f.get("archived") is True:
        ev.append("GitHub reports the repository as archived")
    ev.append("Latest commit: " + (day(ct) if ct is not None else "none found") + (" (authored %s)" % f["author_at"][:10] if f.get("author_at") and ct is not None else ""))
    ev.append("Latest release: " + ((text(f.get("release_tag"), 64) + " on " if f.get("release_tag") else "") + day(rt) if rt is not None else "none found") + (" (repository-wide, not used for this label)" if path and rt is not None else ""))
    if cf or rf:
        ev.append("Ignored a date in the future")
    if decl:
        ev.append("Declaration: unmaintained, by %s%s%s [%s@%s]" % (decl["by"], " on " + decl["date"] if decl["date"] else "", ": " + decl["note"] if decl["note"] else "", decl["file"], decl["commit"][:7] or "unknown commit"))
    if f.get("decl_note"):
        ev.append(f["decl_note"])
    if f.get("archived") is True:
        label, reason = "archived", "GitHub reports this repository as archived, which means its owner has made it read-only."
    elif decl:
        label, reason = "declared", "The maintainer declares this project unmaintained (%s)." % decl["by"]
    else:
        times = [t for t in (ct, None if path else rt) if t is not None]
        newest = max(times) if times else None
        what = "change under " + path if path else "commit or release"
        if newest is not None and now - newest <= w * DAY:
            label, reason = "recent", "The latest %s (%s) is within the last %d days. This describes upstream activity only." % (what, day(newest), w)
        else:
            label = "none"
            reason = "No %s within the last %d days%s. This does not establish abandonment: stable software may need few changes." % (what, w, " (latest %s)" % day(newest) if newest is not None else "")
    return {"label": label, "title": TITLES[label], "reason": reason, "evidence": [text(x, 300) for x in ev][:12]}


def build(info, s, now):
    w = s["prefs"]["window_days"]
    if "key" not in info:
        facts = {"unknown": info["unknown"], "source": info["source"]}
        ok_at, fail, fresh, retry = 0, None, True, 0
    else:
        e = s["entries"].get(info["key"])
        lim = s["limit"] if s["limit"] and s["limit"]["until"] > now else None
        ok_at, facts, fail = (e["ok_at"], e["facts"], e["fail"]) if e else (0, None, None)
        fresh = bool(facts and not fail and now - ok_at <= TTL)
        if not fresh and lim and not fail:
            fail = {"reason": lim["reason"], "at": lim["at"], "code": "rate_limited"}
        retry = max(e["retry_after"] if e else 0, lim["until"] if lim else 0)
        retry = retry if retry > now and not fresh else 0
        facts = facts or {"unknown": fail["code"] if fail else "unchecked", "source": info["source"]}
    c = classify(facts, now, w)
    repo = facts.get("repo") or ""
    path = facts.get("path") or ""
    return {
        "id": info["id"],
        "label": c["label"],
        "title": c["title"],
        "reason": c["reason"],
        "evidence": c["evidence"],
        "repo": repo,
        "branch": facts.get("branch") or "",
        "path": path,
        "scope": ("plugin path" if path else "whole repository") if repo else "",
        "fork_of": facts.get("fork_of") or "",
        "archived": facts.get("archived") is True,
        "commit_at": facts.get("commit_at") or "",
        "release_at": facts.get("release_at") or "",
        "release_tag": facts.get("release_tag") or "",
        "declaration": facts.get("decl"),
        "declaration_note": facts.get("decl_note") or "",
        "checked_at": ok_at,
        "stale": not fresh,
        "failure": {"reason": fail["reason"], "at": fail["at"]} if fail else None,
        "retry_after": retry,
        "window_days": w,
        "source": info["source"],
    }


def wanted(s, key, now, force):
    if s["limit"] and s["limit"]["until"] > now:
        return False
    e = s["entries"].get(key)
    if e is None:
        return True
    if e["retry_after"] > now and (not force or (e["fail"] and e["fail"]["code"] == "rate_limited")):
        return False
    return force or e["fail"] is not None or now - e["ok_at"] > TTL


async def run_checks(host, infos, now, force):
    s = load(host, now)
    todo = {}
    for i in infos:
        if "key" in i and i["key"] not in todo and wanted(s, i["key"], now, force):
            todo[i["key"]] = i
    if todo and not BUSY["on"]:
        run = Run(host, now)
        BUSY["on"] = True
        try:
            order = sorted(todo, key=lambda k: s["entries"][k]["ok_at"] if k in s["entries"] else 0)
            await asyncio.gather(*(run.one(todo[k]) for k in order))
            s = load(host, now)
            for k in order:
                if k in run.done:
                    apply(s, k, run.done[k], now)
            if run.limit and (not s["limit"] or s["limit"]["until"] < run.limit.retry):
                s["limit"] = {"until": run.limit.retry, "at": now, "reason": text(run.limit.text, 300)}
            for i in infos:
                if i.get("key") in s["entries"]:
                    s["entries"][i["key"]] = s["entries"].pop(i["key"])
            save(host, s)
        finally:
            cleanup = asyncio.ensure_future(run.close())
            cleanup.add_done_callback(lambda task: BUSY.update(on=False))
            await asyncio.shield(cleanup)
    s = load(host, now)
    return [build(i, s, now) for i in infos]


def clock(host, now):
    return int(host.now() if now is None else now)


async def check_all(host, plugins, now=None, force=False):
    now = clock(host, now)
    out = await run_checks(host, [describe(host, p) for p in list(plugins)[:PLUGINS_MAX]], now, force)
    return {r["id"]: r for r in out if r["id"]}


async def check_one(host, plugin, now=None, force=True):
    return (await run_checks(host, [describe(host, plugin)], clock(host, now), force))[0]


def cached(host, plugin_id_or_source, now=None):
    now = clock(host, now)
    return build(describe(host, plugin_id_or_source), load(host, now), now)


async def assess_url(host, github_url, now=None):
    return await check_one(host, {"source": github_url}, now, False)


async def background(host, plugins, now=None):
    now = clock(host, now)
    s = load(host, now)
    if not s["prefs"]["enabled_background"] or now < s["bg_next"]:
        return None
    out = await check_all(host, plugins, now)
    s = load(host, now)
    s["bg_next"] = int(now + WEEK + host.rand() * DAY)
    save(host, s)
    return out


def err(message, code):
    return {"error": message, "code": code}


def safe(fn):
    async def run(host, arg=None):
        try:
            return await fn(host, arg)
        except Exception as e:
            return err(text(str(e) or type(e).__name__, 200), "failed")

    return run


def known(host, pid):
    return valid_id(pid) and host.manifest(host.plugin_dir(pid)) is not None


@safe
async def maint_get(host, arg):
    a = arg if isinstance(arg, dict) else {}
    if "ids" in a:
        ids = a["ids"]
        if not isinstance(ids, list) or len(ids) > IDS_MAX or not all(known(host, x) for x in ids):
            return err("ids must be at most %d installed module ids" % IDS_MAX, "bad_arg")
        s, now = load(host), clock(host, None)
        return {"records": {x: build(describe(host, x), s, now) for x in ids}}
    if "id" in a:
        return cached(host, a["id"]) if known(host, a["id"]) else err("unknown module", "unknown_plugin")
    if isinstance(a.get("source"), str):
        return cached(host, {"source": a["source"]})
    return err("id, ids or source is required", "bad_arg")


@safe
async def maint_refresh(host, arg):
    a = arg if isinstance(arg, dict) else {}
    if a.get("id") != "all" and not known(host, a.get("id")):
        return err("unknown module", "unknown_plugin")
    if BUSY["on"]:
        return err("A maintenance check is already running", "busy")
    if a["id"] == "all":
        return {"records": await check_all(host, host.manifests(), force=True)}
    return await check_one(host, a["id"])


def pref_view(s):
    return {"maint.enabled_background": s["prefs"]["enabled_background"], "maint.window_days": s["prefs"]["window_days"]}


@safe
async def maint_prefs(host, arg):
    s = load(host)
    if isinstance(arg, dict) and arg:
        if any(k not in PREF_KEYS for k in arg):
            return err("unknown preference", "bad_key")
        if "maint.enabled_background" in arg:
            if not isinstance(arg["maint.enabled_background"], bool):
                return err("maint.enabled_background must be true or false", "bad_value")
            s["prefs"]["enabled_background"] = arg["maint.enabled_background"]
        if "maint.window_days" in arg:
            v = arg["maint.window_days"]
            if isinstance(v, bool) or not isinstance(v, int) or not WINDOW_MIN <= v <= WINDOW_MAX:
                return err("maint.window_days must be a whole number from %d to %d" % (WINDOW_MIN, WINDOW_MAX), "bad_value")
            s["prefs"]["window_days"] = v
        if not save(host, s):
            return err("could not save the maintenance settings", "write_failed")
    return pref_view(s)


METHODS = {"maint_get": maint_get, "maint_refresh": maint_refresh, "maint_prefs": maint_prefs}
