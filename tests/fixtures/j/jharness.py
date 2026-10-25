import asyncio
import email.message
import importlib.util
import io
import itertools
import json
import os
import pathlib
import shutil
import sys
import tarfile
import tempfile
import types
import urllib.error
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[3]
COUNT = itertools.count(1)
SHA = "a" * 40


def repo_files():
    return [f for f in REPO.iterdir() if f.is_file() and (f.suffix in (".py", ".js", ".svg"))]


def mkzip(files, top=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr((top + "/" if top else "") + k, v)
    return b.getvalue()


def mktar(files, top="relpkg"):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        for k, v in files.items():
            data = v if isinstance(v, bytes) else v.encode()
            i = tarfile.TarInfo((top + "/" if top else "") + k)
            i.size = len(data)
            i.mode = 0o755 if k.endswith(".sh") else 0o644
            t.addfile(i, io.BytesIO(data))
    return b.getvalue()


def http_error(url, code, **headers):
    m = email.message.Message()
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    return urllib.error.HTTPError(url, code, "x", m, None)


class Net:
    def __init__(self):
        self.routes = {}
        self.calls = []
        self.report = True

    def __call__(self, url, headers=None, hosts=None, limit=5_000_000, progress=None):
        self.calls.append(url)
        v = self.routes.get(url)
        if v is None:
            raise http_error(url, 404)
        if isinstance(v, BaseException):
            raise v
        data = v() if callable(v) else v
        if progress and self.report:
            progress(len(data), len(data))
        return data

    def json(self, path, value):
        self.routes["https://api.github.com/repos/" + path] = json.dumps(value).encode()

    def commit(self, owner, repo, ref, sha):
        self.routes["https://api.github.com/repos/%s/%s/commits/%s" % (owner, repo, ref)] = sha.encode()

    def archive(self, owner, repo, sha, data):
        self.routes["https://github.com/%s/%s/archive/%s.zip" % (owner, repo, sha)] = data


class Loader:
    def __init__(self, prefs=None, files=None):
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="fyj-")).resolve()
        self.home = self.base / "home"
        self.root = self.home / "framey"
        (self.root / "plugins").mkdir(parents=True)
        (self.root / "store").mkdir()
        (self.home / ".config" / "framey").mkdir(parents=True)
        (self.base / "run").mkdir()
        for f in files if files is not None else repo_files():
            shutil.copy(f, self.root / f.name)
        (self.root / ".version").write_text(SHA)
        self.env = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR",)}
        os.environ["XDG_RUNTIME_DIR"] = str(self.base / "run")
        self.old_home = pathlib.Path.__dict__["home"]
        pathlib.Path.home = classmethod(lambda cls: self.home)
        self.net = Net()
        self.errors = io.StringIO()
        spec = importlib.util.spec_from_file_location("framey_j%d" % next(COUNT), self.root / "framey.py")
        self.fy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fy)
        self.fy.fetch = self.net
        self.fy.log = lambda *a: self.errors.write(" ".join(map(str, a)) + "\n")
        if prefs:
            self.prefs(**prefs)

    @property
    def plugins(self):
        return self.root / "plugins"

    def trust(self, **kw):
        d = self.home / ".config" / "framey"
        d.mkdir(parents=True, exist_ok=True)
        dev, cfg = d / "developer", d / "loader.json"
        j = json.loads(cfg.read_text()) if cfg.exists() else {}
        for k, v in kw.items():
            if k == "safe":
                (d / "safe").write_text("") if v else (d / "safe").unlink(missing_ok=True)
            elif k == "dev.enabled":
                dev.write_text(dev.read_text() if dev.exists() else "{}") if v else dev.unlink(missing_ok=True)
            elif k == "dev.targets":
                dev.write_text(json.dumps({"targets": v}))
            else:
                j[k] = v
        if j:
            cfg.write_text(json.dumps(j))

    def prefs(self, **kw):
        self.trust(**{k: kw.pop(k) for k in list(kw) if k in ("safe", "channel", "file_roots", "dev.enabled", "dev.targets")})
        s = self.fy.settings()
        for k, v in kw.items():
            if "." in k:
                a, b = k.split(".")
                s["prefs"][a][b] = v
            else:
                s["prefs"][k] = v
        self.fy.save(s)

    def plugin(self, pid, manifest=None, main="1", backend=None, extra=None):
        d = self.plugins / pid
        d.mkdir(parents=True, exist_ok=True)
        m = {"id": pid, "name": pid, "version": "1.0.0"}
        m.update(manifest or {})
        (d / "plugin.json").write_text(json.dumps(m))
        if main is not None:
            (d / "main.js").write_text(main)
        if backend is not None:
            (d / "backend.py").write_text(backend)
        for k, v in (extra or {}).items():
            (d / k).write_text(v)
        return d

    async def core(self, method, arg=None):
        self.fy.LOCK = None
        return await self.fy.core(method, arg)

    async def close(self):
        fy = self.fy
        fy.stopping = True
        for t in list(fy.WATCH.values()) + list(fy.BG):
            t.cancel()
        try:
            await asyncio.wait_for(fy.down(), 8)
        except Exception:
            pass
        pathlib.Path.home = self.old_home
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.base, ignore_errors=True)


class Page:
    def __init__(self, probe="undefined"):
        self.q = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.probe = probe
        self.tok = None
        self.toks = {}

    async def send(self, obj):
        self.sent.append(obj)
        p = obj["params"]
        if obj["method"] == "Runtime.evaluate" and p.get("returnByValue"):
            self.q.put_nowait(json.dumps({"id": obj["id"], "result": {"result": {"value": self.probe}}}))
        elif obj["method"] == "Runtime.evaluate" and p["expression"].startswith("(function(__fyt){"):
            self.q.put_nowait(json.dumps({"id": obj["id"], "result": {"result": {"objectId": "core"}}}))

    async def recv(self):
        return await self.q.get()

    def close(self):
        self.closed = True

    def exprs(self):
        return [o["params"]["expression"] for o in self.sent if o["method"] == "Runtime.evaluate"]

    def live(self):
        fy = getattr(self, "fy", None)
        return (fy.CORE_TOK, fy.TOKS) if fy else (self.tok, self.toks)

    def bind(self, **req):
        core, toks = self.live()
        if req.get("plugin") == "_core":
            req.setdefault("t", core)
        elif "t" in req and req["t"] is None:
            del req["t"]
        elif req.get("plugin") and "t" not in req:
            req["t"] = next((t for t, p in toks.items() if p == req["plugin"]), None)
        self.q.put_nowait(json.dumps({"method": "Runtime.bindingCalled", "params": {"payload": json.dumps(req)}}))

    def trace(self, since=0):
        out = []
        for o in self.sent[since:]:
            p = o["params"]
            if o["method"] == "Runtime.evaluate":
                out.append(p["expression"])
            elif o["method"] == "Runtime.callFunctionOn":
                decl = p.get("functionDeclaration", "")
                kind = next((k for k in ("reply", "event", "bind", "dispose") if ("this.%s(" % k) in decl), "run" if decl == "function(p){return this(p)}" else "call")
                out.append((kind, [a.get("value", "<obj>") for a in p.get("arguments", [])]))
        return out

    def replies(self):
        return [x[1] for x in self.trace() if isinstance(x, tuple) and x[0] == "reply"]

    def events(self):
        return [x[1] for x in self.trace() if isinstance(x, tuple) and x[0] == "event"]


class Fail(Exception):
    pass


class WorldPage(Page):
    def __init__(self, **opt):
        super().__init__()
        self.opt = opt
        self.ids = itertools.count(10)
        self.frame = opt.get("frame", "F1")
        self.contexts = {}
        self.plain = {"fyCall"} if opt.get("page_defines") else set()
        self.scoped = {}
        self.calls = []
        self.world_failures = 0
        self.main = self.context("")

    def context(self, name):
        c = types.SimpleNamespace(id=next(self.ids), name=name, globals=set(), tokens=[], evals=[])
        self.contexts[c.id] = c
        return c

    def emit(self, method, **params):
        self.q.put_nowait(json.dumps({"method": method, "params": params}))

    def created(self, c):
        self.emit("Runtime.executionContextCreated", context={"id": c.id, "origin": "https://steam", "name": c.name, "auxData": {"frameId": self.frame, "isDefault": not c.name}})

    def sees(self, c, name):
        if name == "fyCall":
            return name in self.plain or c.name in self.scoped.get(name, ()) and not self.opt.get("hide")
        return name in c.globals

    def worlds(self):
        return [c for c in self.contexts.values() if c.name == "framey"]

    def iso(self):
        w = self.worlds()
        return w[-1] if w else None

    def has(self, name):
        return any(name in c.globals for c in self.contexts.values())

    def evaluates(self):
        return [o["params"] for o in self.sent if o["method"] == "Runtime.evaluate"]

    def methods(self):
        return [m for m, _ in self.calls]

    def call(self, ctx, **req):
        if not self.sees(ctx, "fyCall"):
            return False
        core, toks = self.live()
        if req.get("plugin") == "_core":
            req.setdefault("t", core)
        elif req.get("plugin") and "t" not in req:
            req["t"] = next((t for t, p in toks.items() if p == req["plugin"]), None)
        self.emit("Runtime.bindingCalled", name="fyCall", payload=json.dumps(req), executionContextId=ctx.id)
        return True

    def navigate(self, frame=None, announce=True):
        self.contexts.clear()
        self.emit("Runtime.executionContextsCleared")
        self.frame = frame or self.frame
        if announce:
            self.emit("Page.frameNavigated", frame={"id": self.frame, "url": "https://steam/next"})
        self.main = self.context("")
        self.created(self.main)

    def destroy(self, c):
        del self.contexts[c.id]
        self.emit("Runtime.executionContextDestroyed", executionContextId=c.id)

    async def send(self, obj):
        self.sent.append(obj)
        method, p = obj["method"], obj["params"]
        self.calls.append((method, p))
        if method in self.opt.get("silent", ()):
            return
        try:
            out = {"id": obj["id"], "result": getattr(self, "m_" + method.replace(".", "_"), lambda p: {})(p)}
        except Fail as e:
            out = {"id": obj["id"], "error": {"code": -32000, "message": str(e)}}
        self.q.put_nowait(json.dumps(out))

    def m_Runtime_enable(self, p):
        for c in list(self.contexts.values()):
            self.created(c)
        return {}

    def m_Page_getFrameTree(self, p):
        return {} if self.opt.get("no_frame") else {"frameTree": {"frame": {"id": self.frame}}}

    def m_Page_createIsolatedWorld(self, p):
        if self.opt.get("world_error") or self.world_failures:
            self.world_failures = max(0, self.world_failures - 1)
            raise Fail("Cannot create world")
        if p.get("frameId") != self.frame:
            raise Fail("No frame for given id found")
        c = self.context(p.get("worldName", ""))
        self.created(c)
        return {"executionContextId": c.id}

    def m_Runtime_addBinding(self, p):
        if "executionContextName" in p:
            if self.opt.get("reject_name"):
                raise Fail("Invalid parameters")
            if not self.opt.get("ignore_name"):
                self.scoped.setdefault(p["name"], set()).add(p["executionContextName"])
                return {}
        self.plain.add(p["name"])
        return {}

    def m_Runtime_removeBinding(self, p):
        self.plain.discard(p["name"])
        self.scoped.pop(p["name"], None)
        return {}

    def m_Runtime_evaluate(self, p):
        c = self.contexts.get(p["contextId"]) if "contextId" in p else self.main
        if c is None:
            raise Fail("Cannot find context with specified id")
        e = p["expression"]
        if p.get("returnByValue"):
            name = e.rpartition(".")[2]
            return {"result": {"type": "string", "value": "function" if name == "fyCall" and self.sees(c, name) else "object" if name in c.globals else "undefined"}}
        c.evals.append(e)
        if "THROW_ME" in e:
            return {"result": {}, "exceptionDetails": {"text": "Uncaught", "exception": {"description": "Error: boom"}}}
        if self.opt.get("blind") and c.name:
            return {"result": {}}
        if e.startswith("(function(__fyt){"):
            c.tokens.append(e.rstrip()[-34:-2])
            if e.startswith("(function(__fyt){window.FY_ICON_URL"):
                c.globals.add("Framey")
            if not p.get("returnByValue"):
                return {"result": {"objectId": "core"}}
        elif e.startswith("window.FY_ICON_URL="):
            c.globals.add("FYBar")
        elif e.endswith("delete window.Framey"):
            c.globals.discard("Framey")
        elif e.startswith("window.FYBar&&window.FYBar.dispose"):
            c.globals.discard("FYBar")
        return {"result": {}}


async def attach(loader, name="main", page=None):
    fy = loader.fy
    page = page or Page()
    pages = getattr(loader, "pages", None)
    if pages is None:
        pages = loader.pages = {}
        async def fake_open(cls, url):
            return pages[url.rsplit("/", 1)[1]]
        fy.WS.open = classmethod(fake_open)
    pages[name] = page
    page.tok = fy.CORE_TOK
    page.toks = fy.TOKS
    page.fy = fy
    code, probe = (fy.main_code, "typeof window.Framey") if name == "main" else (fy.bar_code, "typeof window.FYBar")
    task = asyncio.create_task(fy.session(name, {"webSocketDebuggerUrl": "ws://x/" + name}, code, probe))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if name in fy.LIVE and len(page.exprs()) > 2:
            break
    return page, task


async def settle(cond, limit=10.0, step=0.02):
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return cond()
