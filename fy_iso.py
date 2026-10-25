import asyncio
import json


def _constant(name):
    raise ValueError(name)


def _loads(data):
    return json.loads(data, parse_constant=_constant)


WORLD = "framey"
BINDING = "fyCall"
STEP = 5
LOST = 3
STALE = 2
HEAD = '{"method":"'
EVENTS = frozenset(("Runtime.bindingCalled", "Runtime.executionContextCreated", "Runtime.executionContextDestroyed", "Runtime.executionContextsCleared", "Page.frameNavigated"))


def valid(v):
    return v if v is True or v is False or v == "auto" else None


def mode(cfg, path):
    if isinstance(cfg, dict):
        v = valid(cfg.get("isolation"))
    else:
        try:
            with open(path, "rb") as f:
                data = f.read(17).decode()
            v = valid(_loads(data)) if len(data) <= 16 else None
        except (OSError, ValueError, RecursionError):
            v = None
    return False if v is None else v


def wanted(raw):
    if not raw.startswith(HEAD):
        return True
    return raw[len(HEAD):raw.find('"', len(HEAD))] in EVENTS


class Iso:
    def __init__(self, rpc, log):
        self.rpc, self.log = rpc, log
        self.on = self.busy = self.injected = self.held = False
        self.ctx = self.frame = None
        self.stale = self.lost = 0

    async def typeof(self, ctx):
        more = {} if ctx is None else {"contextId": ctx}
        r = await self.rpc("Runtime.evaluate", expression="typeof window.fyCall", returnByValue=True, **more)
        return (r.get("result") or {}).get("value")

    async def make(self):
        r = await self.rpc("Page.createIsolatedWorld", frameId=self.frame, worldName=WORLD)
        ctx = r["executionContextId"]
        if type(ctx) is not int:
            raise ValueError("no execution context id")
        self.ctx, self.injected, self.stale = ctx, False, 0

    async def start(self):
        self.on = self.busy = True
        try:
            await self.rpc("Runtime.enable")
            await self.rpc("Page.enable")
            if await self.typeof(None) != "undefined":
                self.held = True
                raise ValueError("the page already defines fyCall")
            self.frame = (await self.rpc("Page.getFrameTree"))["frameTree"]["frame"]["id"]
            await self.rpc("Runtime.addBinding", name=BINDING, executionContextName=WORLD)
            if await self.typeof(None) != "undefined":
                raise ValueError("the binding is visible to the page")
            await self.make()
            if await self.typeof(self.ctx) != "function":
                raise ValueError("the binding is missing in the isolated world")
        except Exception as e:
            self.log("isolation unavailable:", str(e) or type(e).__name__)
            await self.stop()
            return False
        finally:
            self.busy = False
        return True

    async def stop(self, wipe=None):
        ctx, self.on, self.ctx = self.ctx, False, None
        calls = [("Runtime.removeBinding", {"name": BINDING}), ("Runtime.disable", {}), ("Page.disable", {})]
        if wipe and ctx is not None:
            calls.insert(0, ("Runtime.evaluate", {"expression": wipe, "contextId": ctx}))
        await asyncio.gather(*(self.rpc(method, **p) for method, p in calls), return_exceptions=True)

    def event(self, m):
        k, p = m.get("method"), m.get("params") or {}
        if k == "Runtime.executionContextsCleared":
            self.ctx = None
        elif k == "Runtime.executionContextDestroyed":
            if p.get("executionContextId") == self.ctx:
                self.ctx = None
        elif k == "Runtime.executionContextCreated":
            c = p.get("context") or {}
            if c.get("name") == WORLD and (c.get("auxData") or {}).get("frameId") == self.frame and type(c.get("id")) is int:
                self.ctx, self.injected, self.stale = c["id"], False, 0
        elif k == "Page.frameNavigated":
            f = p.get("frame") or {}
            if "parentId" not in f:
                self.frame, self.ctx = f.get("id") or self.frame, None

    async def renew(self):
        if self.busy or not self.on:
            return False
        self.busy = True
        try:
            try:
                await self.make()
            except Exception:
                self.frame = (await self.rpc("Page.getFrameTree"))["frameTree"]["frame"]["id"]
                await self.make()
            self.lost = 0
            return True
        except Exception as e:
            self.ctx, self.lost = None, self.lost + 1
            self.log("isolated world not renewed:", str(e) or type(e).__name__)
            return False
        finally:
            self.busy = False

    @property
    def dead(self):
        return self.lost >= LOST

    def seen(self, value):
        if value != "undefined":
            self.stale = 0
            return "ok"
        if self.injected:
            self.stale += 1
            if self.stale >= STALE:
                return "fallback"
        self.injected = True
        return "inject"
