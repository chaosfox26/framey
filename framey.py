import asyncio, base64, importlib.util, io, json, os, pathlib, re, shutil, signal, urllib.parse, zipfile

ROOT = pathlib.Path(__file__).parent
PLUGINS = ROOT / "plugins"
STORE = ROOT / "store"
CONF = pathlib.Path.home() / ".config/framey/settings.json"
CDP = ("127.0.0.1", 8080)
NAME = re.compile(r"^[a-z0-9_-]{1,32}$")
LIVE = {}
mods = {}
WIPE = "document.querySelectorAll('div').forEach(d=>{if(d.style.zIndex==='99999')d.remove()});document.querySelectorAll('[data-fy]').forEach(e=>e.remove());delete window.Framey"
WIPE_BAR = "window.FYObs&&window.FYObs.disconnect();document.querySelectorAll('div').forEach(d=>{if(d.style.zIndex==='99999')d.remove()});delete window.FYBar;delete window.FYObs"
WIPES = {"main": WIPE, "bar": WIPE_BAR}


def settings():
    try:
        return {"disabled": [], **json.loads(CONF.read_text())}
    except (OSError, ValueError, TypeError):
        return {"disabled": []}


def save(s):
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(s))


def manifests():
    out = []
    for d in sorted(PLUGINS.iterdir()) if PLUGINS.exists() else []:
        try:
            m = json.loads((d / "plugin.json").read_text())
        except (OSError, ValueError):
            continue
        m["id"] = d.name
        out.append(m)
    return out


def catalog():
    out = []
    for d in sorted(STORE.iterdir()) if STORE.exists() else []:
        try:
            m = json.loads((d / "plugin.json").read_text())
        except (OSError, ValueError):
            continue
        out.append({"id": d.name, "name": m.get("name", d.name), "version": m.get("version", ""), "installed": (PLUGINS / d.name).exists()})
    return out


def load_backends():
    mods.clear()
    off = settings()["disabled"]
    for m in manifests():
        f = PLUGINS / m["id"] / "backend.py"
        if m["id"] in off or not f.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("fy_" + m["id"], f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mods[m["id"]] = mod
        except Exception:
            pass


def prelude():
    url = "data:image/svg+xml," + urllib.parse.quote((ROOT / "icon.svg").read_text())
    return "window.FY_ICON_URL=" + json.dumps(url) + ";\n"


def main_code():
    code = (ROOT / "inject.js").read_text()
    off = settings()["disabled"]
    for m in manifests():
        js = PLUGINS / m["id"] / "main.js"
        if m["id"] not in off and js.exists():
            code += "\nFramey._meta=%s;try{%s\n}catch(e){Framey.fail(%s,e)}" % (json.dumps(m), js.read_text(), json.dumps(m["id"]))
    return code + "\nFramey.restore()"


def bar_code():
    return prelude() + (ROOT / "bar.js").read_text()


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


async def install_zip(data):
    if len(data) > 5_000_000:
        return {"error": "zip over 5 MB"}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"error": "not a zip"}
    names = [n for n in z.namelist() if not n.endswith("/")]
    if len(names) > 200 or any(n.startswith("/") or ".." in n.split("/") for n in names):
        return {"error": "bad zip"}
    base = ""
    if "plugin.json" not in names:
        tops = {n.split("/")[0] for n in names}
        if len(tops) != 1 or next(iter(tops)) + "/plugin.json" not in names:
            return {"error": "no plugin.json"}
        base = next(iter(tops)) + "/"
    pid = json.loads(z.read(base + "plugin.json")).get("id", "")
    if not NAME.match(pid):
        return {"error": "bad plugin id"}
    dest = PLUGINS / pid
    if dest.is_symlink():
        return {"error": "linked module"}
    shutil.rmtree(dest, ignore_errors=True)
    for n in names:
        if n.startswith(base):
            f = dest / n[len(base):]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(z.read(n))
    await reload()
    return {"ok": pid}


async def core(method, arg):
    s = settings()
    if method == "toggle":
        if "main" in LIVE:
            await LIVE["main"]("Runtime.evaluate", expression="Framey.toggle()")
        return None
    if method == "list":
        return [{**m, "enabled": m["id"] not in s["disabled"]} for m in manifests()]
    if method == "store":
        return catalog()
    if method == "install":
        return await install(arg)
    if method == "add":
        if NAME.match(arg or "") and (STORE / arg).is_dir() and not (PLUGINS / arg).is_symlink():
            shutil.copytree(STORE / arg, PLUGINS / arg, dirs_exist_ok=True)
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
                await send("Runtime.evaluate", expression=code())
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
