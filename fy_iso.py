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
EVENTS = frozenset(
    (
        "Runtime.bindingCalled",
        "Runtime.executionContextCreated",
        "Runtime.executionContextDestroyed",
        "Runtime.executionContextsCleared",
        "Page.frameNavigated",
    )
)


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
    return raw[len(HEAD) : raw.find('"', len(HEAD))] in EVENTS


class Iso:
    def __init__(self, rpc, log):
        self.rpc, self.log = rpc, log
        self.on = self.busy = self.injected = self.held = False
        self.ctx = self.frame = None
        self.stale = self.lost = 0
        self.epoch = 0
        self.creating = None

    async def typeof(self, ctx):
        more = {} if ctx is None else {"contextId": ctx}
        r = await self.rpc(
            "Runtime.evaluate", expression="typeof window.fyCall", returnByValue=True, **more
        )
        return (r.get("result") or {}).get("value")

    async def locate(self):
        epoch = self.epoch
        r = await self.rpc("Page.getFrameTree")
        frame = r["frameTree"]["frame"]["id"]
        if not isinstance(frame, str) or not frame:
            raise ValueError("no main frame id")
        if not self.on or epoch != self.epoch:
            raise ValueError("main frame changed while being located")
        self.frame = frame

    async def make(self):
        frame, epoch = self.frame, self.epoch
        destroyed = self.creating = set()
        try:
            r = await self.rpc("Page.createIsolatedWorld", frameId=frame, worldName=WORLD)
        finally:
            self.creating = None
        ctx = r["executionContextId"]
        if type(ctx) is not int or ctx <= 0:
            raise ValueError("no execution context id")
        if not self.on or epoch != self.epoch or frame != self.frame or ctx in destroyed:
            raise ValueError("isolated world changed while being created")
        self.ctx, self.injected, self.stale = ctx, False, 0

    async def verify(self):
        ctx, epoch = self.ctx, self.epoch
        if type(ctx) is not int or ctx <= 0:
            raise ValueError("no execution context id")
        if await self.typeof(ctx) != "function":
            raise ValueError("the binding is missing in the isolated world")
        if not self.on or ctx != self.ctx or epoch != self.epoch:
            raise ValueError("isolated world changed while being verified")

    async def start(self):
        self.on = self.busy = True
        try:
            await self.rpc("Runtime.enable")
            await self.rpc("Page.enable")
            if await self.typeof(None) != "undefined":
                self.held = True
                raise ValueError("the page already defines fyCall")
            await self.locate()
            await self.rpc("Runtime.addBinding", name=BINDING, executionContextName=WORLD)
            if await self.typeof(None) != "undefined":
                raise ValueError("the binding is visible to the page")
            await self.make()
            await self.verify()
        except Exception as e:
            self.log("isolation unavailable:", str(e) or type(e).__name__)
            await self.stop()
            return False
        finally:
            self.busy = False
        return True

    async def stop(self, wipe=None):
        ctx, self.on, self.ctx = self.ctx, False, None
        self.epoch += 1
        calls = [
            ("Runtime.removeBinding", {"name": BINDING}),
            ("Runtime.disable", {}),
            ("Page.disable", {}),
        ]
        if wipe and ctx is not None:
            calls.insert(0, ("Runtime.evaluate", {"expression": wipe, "contextId": ctx}))
        await asyncio.gather(
            *(self.rpc(method, **p) for method, p in calls), return_exceptions=True
        )

    def event(self, m):
        if not self.on or not isinstance(m, dict):
            return
        k, p = m.get("method"), m.get("params", {})
        if not isinstance(p, dict):
            return
        if k == "Runtime.executionContextsCleared":
            self.epoch += 1
            self.ctx = None
        elif k == "Runtime.executionContextDestroyed":
            ctx = p.get("executionContextId")
            if type(ctx) is not int:
                return
            if self.creating is not None:
                self.creating.add(ctx)
            if ctx == self.ctx:
                self.epoch += 1
                self.ctx = None
        elif k == "Page.frameNavigated":
            f = p.get("frame")
            if (
                isinstance(f, dict)
                and "parentId" not in f
                and isinstance(f.get("id"), str)
                and f["id"]
            ):
                self.epoch += 1
                self.frame, self.ctx = f["id"], None

    async def renew(self):
        if self.busy or not self.on:
            return False
        self.busy = True
        try:
            try:
                await self.make()
            except Exception:
                if not self.on:
                    return False
                await self.locate()
                await self.make()
            await self.verify()
            self.lost = 0
            return True
        except Exception as e:
            if not self.on:
                return False
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
