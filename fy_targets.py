import json
import re

MAX_ITEMS = 8
MAX_ITEM = 65536
MAX_PLUGIN = 262144
MAX_PLUGINS = 64
KNOWN = ("main", "bar")
PID = re.compile(r"[a-z0-9_-]{1,32}\Z")
KEY = re.compile(r"[A-Za-z0-9_.-]{1,40}\Z")
NAME = re.compile(r"[a-z0-9_-]{1,24}\Z")

CSS_ADD = (
    '(function(o,k,t){var n=document.querySelectorAll("[data-fy-target]"),s=null,i;'
    "for(i=0;i<n.length;i++)if(n[i].dataset.fyOwner===o&&n[i].dataset.fyKey===k)s=n[i];"
    'if(!s){s=document.createElement("style");s.setAttribute("data-fy",o);'
    's.setAttribute("data-fy-target","css");s.setAttribute("data-fy-owner",o);'
    's.setAttribute("data-fy-key",k);(document.head||document.documentElement).appendChild(s)}'
    "s.textContent=t})"
)
CSS_DEL = (
    '(function(o,k){var n=document.querySelectorAll("[data-fy-target]"),i;'
    "for(i=0;i<n.length;i++)if(n[i].dataset.fyOwner===o&&n[i].dataset.fyKey===k)n[i].remove()})"
)
JS_ADD = (
    "(function(o,k,c,d){var T=window.__framey_targets||(window.__framey_targets=Object.create(null)),i=o+\"|\"+k,e;"
    "if(i in T){e=T[i];delete T[i];try{(0,eval)(e)}catch(x){}}T[i]=d;(0,eval)(c)})"
)
JS_DEL = (
    '(function(o,k){var T=window.__framey_targets,i=o+"|"+k,e;'
    "if(T&&i in T){e=T[i];delete T[i];try{(0,eval)(e)}catch(x){}}})"
)


class TargetError(Exception):
    def __init__(self, code, message):
        message = str(message)[:300]
        super().__init__(message)
        self.code = code
        self.message = message


def call(fn, *args):
    return fn + "(" + ",".join(json.dumps(a) for a in args) + ")"


def add_expr(kind, pid, key, code, teardown):
    if kind == "css":
        return call(CSS_ADD, pid, key, code)
    return call(JS_ADD, pid, key, code, teardown or "")


def remove_expr(kind, pid, key):
    return call(CSS_DEL if kind == "css" else JS_DEL, pid, key)


def size(*texts):
    return sum(len(t.encode("utf-8", "surrogatepass")) for t in texts if t)


class Targets:
    def __init__(self, host):
        self.host = host
        self.items = {}

    def dev_table(self):
        dev = (self.host.settings().get("prefs") or {}).get("dev") or {}
        table = dev.get("targets") if dev.get("enabled") is True else None
        if not isinstance(table, dict):
            return {}
        out = {}
        for name, part in list(table.items())[:MAX_ITEMS]:
            if isinstance(name, str) and NAME.match(name) and name not in KNOWN and isinstance(part, str) and part:
                out[name] = part[:120]
        return out

    def names(self):
        return KNOWN + tuple(self.dev_table())

    def urlpart(self, name):
        return self.dev_table().get(name)

    def wanted(self):
        names = self.dev_table()
        return sorted({k[1] for k in self.items if k[1] in names})

    def check(self, pid, target, kind, key, code, teardown):
        if not isinstance(pid, str) or not PID.match(pid):
            raise TargetError("bad_plugin", "Missing or invalid plugin id")
        if not isinstance(target, str) or target not in self.names():
            raise TargetError("unknown_target", "Unknown target: " + str(target)[:40])
        if kind not in ("css", "js"):
            raise TargetError("bad_kind", "kind must be css or js")
        if not isinstance(key, str) or not KEY.match(key):
            raise TargetError("bad_key", "key must be 1-40 characters of letters, digits, dot, dash or underscore")
        if not isinstance(code, str):
            raise TargetError("bad_code", "code must be a string")
        if kind == "css" or teardown is None:
            teardown = None
        elif not isinstance(teardown, str):
            raise TargetError("bad_code", "teardown must be a string")
        total = size(code, teardown)
        if total > MAX_ITEM:
            raise TargetError("too_large", "Item is larger than %d bytes" % MAX_ITEM)
        slot = (pid, target, kind, key)
        mine = [k for k in self.items if k[0] == pid and k != slot]
        if len(mine) + 1 > MAX_ITEMS:
            raise TargetError("too_many", "At most %d injected items per plugin" % MAX_ITEMS)
        if sum(self.items[k][2] for k in mine) + total > MAX_PLUGIN:
            raise TargetError("too_large", "Injected items of one plugin are limited to %d bytes" % MAX_PLUGIN)
        pids = {k[0] for k in self.items}
        if pid not in pids and len(pids) >= MAX_PLUGINS:
            raise TargetError("too_many", "Too many plugins with injected items")
        return slot, teardown, total

    async def add(self, pid, target, kind, key, code, teardown=None):
        slot, teardown, total = self.check(pid, target, kind, key, code, teardown)
        self.items[slot] = (code, teardown, total)
        await self.push(target, add_expr(kind, pid, key, code, teardown))

    async def add_css(self, pid, target, key, text):
        await self.add(pid, target, "css", key, text)

    async def add_js(self, pid, target, key, code, teardown=None):
        await self.add(pid, target, "js", key, code, teardown)

    async def push(self, target, *expressions):
        if not self.host.attached(target):
            return
        try:
            await getattr(self.host, "send_main", self.host.send)(target, ";".join(expressions))
        except Exception as e:
            self.host.log("target", target, "send failed:", e)

    async def remove(self, pid, target=None, key=None):
        gone = [k for k in self.items if k[0] == pid and (target is None or k[1] == target) and (key is None or k[3] == key)]
        by = {}
        for k in gone:
            del self.items[k]
            by.setdefault(k[1], []).append(remove_expr(k[2], k[0], k[3]))
        for name, expressions in by.items():
            await self.push(name, *expressions)
        return len(gone)

    async def remove_plugin(self, pid):
        return await self.remove(pid)

    async def remove_all(self):
        for pid in sorted({k[0] for k in self.items}):
            await self.remove(pid)

    def replay(self, target):
        if target not in self.names():
            return []
        return [(add_expr(k[2], k[0], k[3], v[0], v[1]), k[0]) for k, v in self.items.items() if k[1] == target]


def get(host):
    targets = getattr(host, "targets", None)
    if targets is None:
        targets = host.targets = Targets(host)
    return targets


async def target_add(host, arg):
    a = arg if isinstance(arg, dict) else {}
    try:
        await get(host).add(a.get("pid"), a.get("target"), a.get("kind"), a.get("key"), a.get("code"), a.get("teardown"))
    except TargetError as e:
        return {"error": e.message, "code": e.code}
    return {"ok": True}


async def target_remove(host, arg):
    a = arg if isinstance(arg, dict) else {}
    pid = a.get("pid")
    if not isinstance(pid, str) or not PID.match(pid):
        return {"error": "Missing or invalid plugin id", "code": "bad_plugin"}
    await get(host).remove(pid, a.get("target"), a.get("key"))
    return {"ok": True}


METHODS = {"target_add": target_add, "target_remove": target_remove}
