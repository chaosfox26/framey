import asyncio, base64, importlib.util, io, json, os, pathlib, re, shutil, signal, sys, urllib.parse, zipfile

ROOT = pathlib.Path(__file__).parent
PLUGINS = ROOT / "plugins"
STORE = ROOT / "store"
CONF = pathlib.Path.home() / ".config/framey/settings.json"
CDP = ("127.0.0.1", 8080)
NAME = re.compile(r"^[a-z0-9_-]{1,32}$")
ENTRIES, ENTRY_MAX, TOTAL_MAX, DEPTH = 500, 5_000_000, 20_000_000, 8
LIVE = {}
mods = {}
RM = "document.querySelectorAll('[data-fy]').forEach(e=>e.remove());"
WIPE = RM + "delete window.Framey"
WIPE_BAR = "window.FYObs&&window.FYObs.disconnect();" + RM + "delete window.FYBar;delete window.FYObs"
WIPES = {"main": WIPE, "bar": WIPE_BAR}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def valid(m):
    return isinstance(m, dict) and all(isinstance(m.get(k, ""), str) for k in ("name", "version", "short"))


def manifest(d):
    if not NAME.match(d.name):
        return None
    try:
        m = json.loads((d / "plugin.json").read_text())
        if not valid(m):
            raise ValueError("plugin.json is not a valid manifest")
    except Exception as e:
        log("skipping plugin", d.name, e)
        return None
    m["id"] = d.name
    m.setdefault("name", d.name)
    return m


def settings():
    try:
        s = json.loads(CONF.read_text())
    except Exception:
        s = None
    s = s if isinstance(s, dict) else {}
    off = s.get("disabled")
    return {**s, "disabled": [x for x in off if isinstance(x, str)] if isinstance(off, list) else []}


def save(s):
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(s))


def manifests():
    return [m for m in map(manifest, sorted(PLUGINS.iterdir()) if PLUGINS.exists() else []) if m]


def catalog():
    return [{"id": m["id"], "name": m["name"], "version": m.get("version", ""), "installed": (PLUGINS / m["id"]).exists(), "linked": (PLUGINS / m["id"]).is_symlink()} for m in map(manifest, sorted(STORE.iterdir()) if STORE.exists() else []) if m]


def load_backends():
    mods.clear()
    off = settings()["disabled"]
    for m in manifests():
        try:
            f = PLUGINS / m["id"] / "backend.py"
            if m["id"] in off or not f.exists():
                continue
            spec = importlib.util.spec_from_file_location("fy_" + m["id"], f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mods[m["id"]] = mod
        except (Exception, SystemExit) as e:
            log("backend", m["id"], "failed:", e)


def prelude():
    url = "data:image/svg+xml," + urllib.parse.quote((ROOT / "icon.svg").read_text())
    return "window.FY_ICON_URL=" + json.dumps(url) + ";\n"


def main_code():
    out = [((ROOT / "inject.js").read_text(), None)]
    off = settings()["disabled"]
    for m in manifests():
        try:
            js = PLUGINS / m["id"] / "main.js"
            if m["id"] not in off and js.exists():
                out.append(("Framey._meta=%s;{%s\n}" % (json.dumps(m), js.read_text()), m["id"]))
        except Exception as e:
            log("main.js", m["id"], "failed:", e)
    return out + [("Framey.restore()", None)]


def bar_code():
    return [(prelude() + (ROOT / "bar.js").read_text(), None)]


async def reload():
    load_backends()
    if "main" in LIVE:
        await LIVE["main"]("Runtime.evaluate", expression=WIPE)


def fetch(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as r:
        if not r.geturl().startswith("https://"):
            raise ValueError("redirected away from https")
        return r.read(5_000_001)


async def install(url):
    if not str(url).startswith("https://"):
        return {"error": "https links only"}
    return await install_zip(await asyncio.to_thread(fetch, url))


def stage_swap(pid, fill):
    dest = PLUGINS / pid
    stage = PLUGINS / (".stage-" + pid)
    old = PLUGINS / (".old-" + pid)
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        fill(stage)
        had = dest.exists()
        if had:
            dest.rename(old)
        try:
            stage.rename(dest)
        except OSError:
            if had:
                old.rename(dest)
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


async def install_zip(data):
    if len(data) > 5_000_000:
        return {"error": "zip over 5 MB"}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"error": "not a zip"}
    infos = {i.filename: i for i in z.infolist() if not i.filename.endswith("/")}
    names = list(infos)
    if len(z.infolist()) > ENTRIES or any(n.startswith("/") or "\\" in n or ".." in n.split("/") or len(n.split("/")) > DEPTH for n in names):
        return {"error": "bad zip"}
    if any(i.file_size > ENTRY_MAX for i in infos.values()) or sum(i.file_size for i in infos.values()) > TOTAL_MAX:
        return {"error": "zip too large unpacked"}
    base = ""
    if "plugin.json" not in names:
        tops = {n.split("/")[0] for n in names}
        if len(tops) != 1 or next(iter(tops)) + "/plugin.json" not in names:
            return {"error": "no plugin.json"}
        base = next(iter(tops)) + "/"
    try:
        meta = json.loads(z.read(base + "plugin.json"))
    except Exception:
        meta = None
    pid = meta.get("id") if valid(meta) else None
    if not (isinstance(pid, str) and NAME.match(pid)):
        return {"error": "bad plugin id"}
    if (PLUGINS / pid).is_symlink():
        return {"error": "linked module"}

    def fill(stage):
        total = 0
        for n, i in infos.items():
            if n.startswith(base):
                f = stage / n[len(base):]
                f.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with z.open(i) as src, f.open("wb") as out:
                    while chunk := src.read(65536):
                        size += len(chunk)
                        total += len(chunk)
                        if size > ENTRY_MAX or total > TOTAL_MAX:
                            raise ValueError("zip too large unpacked")
                        out.write(chunk)

    try:
        stage_swap(pid, fill)
    except Exception as e:
        return {"error": str(e) or "install failed"}
    await reload()
    return {"ok": pid}


async def core(method, arg):
    s = settings()
    if method == "toggle":
        if "main" in LIVE:
            await LIVE["main"]("Runtime.evaluate", expression="Framey.toggle()")
        return None
    if method == "list":
        return [{**m, "enabled": m["id"] not in s["disabled"], "linked": (PLUGINS / m["id"]).is_symlink()} for m in manifests()]
    if method == "store":
        return catalog()
    if method == "install":
        return await install(arg)
    if method == "add":
        if NAME.match(arg or "") and (STORE / arg).is_dir() and not (PLUGINS / arg).is_symlink():
            try:
                stage_swap(arg, lambda stage: shutil.copytree(STORE / arg, stage))
            except Exception as e:
                return {"error": str(e) or "install failed"}
            await reload()
            return {"ok": arg}
        return {"error": "not in store"}
    if method in ("enable", "disable"):
        off = set(s["disabled"])
        off.discard(arg) if method == "enable" else off.add(arg)
        s["disabled"] = sorted(off)
        save(s)
    elif method == "uninstall":
        if NAME.match(arg or ""):
            if (PLUGINS / arg).is_symlink():
                (PLUGINS / arg).unlink()
            else:
                shutil.rmtree(PLUGINS / arg, ignore_errors=True)
    elif method != "reload":
        return {"error": "unknown"}
    await reload()
    return {"ok": True}


class WS:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer

    @classmethod
    async def open(cls, url):
        r, w = await asyncio.open_connection(*CDP)
        key = base64.b64encode(os.urandom(16)).decode()
        w.write(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (urllib.parse.urlsplit(url).path, *CDP, key)).encode())
        if b" 101 " not in (await r.readuntil(b"\r\n\r\n")).split(b"\r\n")[0]:
            raise ConnectionError("handshake refused")
        return cls(r, w)

    def frame(self, op, data):
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + n.to_bytes(2, "big") if n < 65536 else bytes([0xFF]) + n.to_bytes(8, "big"))
        mask = os.urandom(4)
        return head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(data))

    async def send(self, obj):
        self.w.write(self.frame(1, json.dumps(obj).encode()))
        await self.w.drain()

    async def recv(self):
        msg = b""
        try:
            while True:
                b1, b2 = await self.r.readexactly(2)
                n = b2 & 127
                if n > 125:
                    n = int.from_bytes(await self.r.readexactly(2 if n == 126 else 8), "big")
                data = await self.r.readexactly(n)
                op = b1 & 15
                if op == 8:
                    return None
                if op == 9:
                    self.w.write(self.frame(10, data))
                elif op != 10:
                    msg += data
                    if b1 & 0x80:
                        return msg.decode()
        except asyncio.IncompleteReadError:
            return None

    def close(self):
        self.w.close()


async def tabs():
    r, w = await asyncio.open_connection(*CDP)
    w.write(("GET /json HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % CDP).encode())
    head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 3)
    body = await asyncio.wait_for(r.readexactly(int(re.search(rb"Content-Length:\s*(\d+)", head, re.I)[1])), 3)
    w.close()
    return json.loads(body)


async def session(name, tab, code, probe_expr):
    ws = await WS.open(tab["webSocketDebuggerUrl"])
    ids = iter(range(1, 1 << 30))
    pending = {}

    async def send(method, **p):
        i = next(ids)
        await ws.send({"id": i, "method": method, "params": p})
        return i

    LIVE[name] = send

    async def handle(req):
        try:
            if req["plugin"] == "_core":
                out = await core(req["method"], req.get("arg"))
            else:
                out = await mods[req["plugin"]].call(req["method"], req.get("arg"))
        except Exception as e:
            out = {"error": str(e)}
        if req["id"]:
            await send("Runtime.evaluate", expression="window.Framey&&Framey._reply(%d,%s)" % (req["id"], json.dumps(out)))

    async def ticker():
        nonlocal probe
        while True:
            await asyncio.sleep(5)
            probe = await send("Runtime.evaluate", expression=probe_expr, returnByValue=True)

    t = None
    try:
        await send("Runtime.addBinding", name="fyCall")
        await send("Runtime.evaluate", expression=WIPES[name])
        probe = await send("Runtime.evaluate", expression=probe_expr, returnByValue=True)
        t = asyncio.create_task(ticker())
        while (raw := await ws.recv()) is not None:
            m = json.loads(raw)
            if m.get("id") == probe and m.get("result", {}).get("result", {}).get("value") == "undefined":
                for expr, pid in code():
                    i = await send("Runtime.evaluate", expression=expr)
                    if pid:
                        pending[i] = pid
            elif m.get("id") in pending:
                pid = pending.pop(m["id"])
                d = m.get("result", {}).get("exceptionDetails")
                if d:
                    msg = (d.get("exception", {}).get("description") or d.get("text") or "error").split("\n")[0]
                    log("plugin", pid, "failed:", msg)
                    await send("Runtime.evaluate", expression="Framey.fail(%s,{message:%s})" % (json.dumps(pid), json.dumps(msg)))
            elif m.get("method") == "Runtime.bindingCalled":
                asyncio.create_task(handle(json.loads(m["params"]["payload"])))
    finally:
        if t:
            t.cancel()
        LIVE.pop(name, None)
        ws.close()


async def watch(name, marker, code, probe_expr):
    while True:
        try:
            tab = next((t for t in await tabs() if marker(t["url"])), None)
            if tab:
                await session(name, tab, code, probe_expr)
        except Exception:
            pass
        await asyncio.sleep(5)


async def main():
    load_backends()
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    work = asyncio.gather(
        watch("main", lambda u: "vrOverlayKey=valve.steam.gamepadui.main" in u, main_code, "typeof window.Framey"),
        watch("bar", lambda u: u.endswith("vrOverlayKey=valve.steam.gamepadui.bar"), bar_code, "typeof window.FYBar"),
    )
    await stop.wait()
    for name, send in list(LIVE.items()):
        await send("Runtime.evaluate", expression=WIPES[name])
    await asyncio.sleep(0.3)
    work.cancel()


asyncio.run(main())
