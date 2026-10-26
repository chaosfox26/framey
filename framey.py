import asyncio, collections, importlib, io, json, os, pathlib, re, socket, stat, sys, time, urllib.parse

VERSION = "1.0.2"
API_GENERATION = API_VERSION = 1
ROOT = pathlib.Path(__file__).parent
PLUGINS = ROOT / "plugins"
STORE = ROOT / "store"
HOME = pathlib.Path.home()
CONF = HOME / ".config/framey/settings.json"
CDP = ("127.0.0.1", 8080)
NAME = re.compile(r"[a-z0-9_-]{1,32}")
RESERVED_IDS = frozenset(("_core", "core", "modules", "settings", "store", "discover", "developer", "dev", "decky-store"))
ENTRIES, ENTRY_MAX, TOTAL_MAX, DEPTH = 500, 5_000_000, 20_000_000, 8
MAX_MSG = 4 << 20
PAYLOAD_IN = 256 << 10
FETCH_S = 45
REQUIRED = {
    "fy_state": ("load", "save", "PREF_F", "module_state", "set_module_state", "forget_module", "StateError"),
    "fy_supervisor": ("Supervisor", "BackendError"),
    "fy_updates": ("Updates",),
    "fy_catalog": ("METHODS",),
    "fy_iso": ("mode", "Iso", "wanted"),
    "fy_store": ("dirs", "purge", "discard_held", "snapshot_settings", "drop_snapshot", "restore_snapshot", "Log", "read_locale", "clean_tmp"),
    "fy_net": ("hostcall", "sync_disk", "_target", "_resolve", "_connect"),
    "fy_pkg": ("decide", "parse_version", "identity", "write_identity", "replace_guard"),
    "fy_files": ("roots", "hostcall"),
    "fy_media": ("get", "hostcall"),
    "fy_maint": ("background", "check_all"),
    "fy_targets": ("target_add", "target_remove", "get"),
    "fy_dev": ("get", "shutdown"),
}
EDIT = ("install", "add", "enable", "disable", "uninstall", "reload", "settings_repair")
UPDATED = ("update", "rollback", "retry", "ver_install", "repair", "batch_apply")
RELOAD = EDIT + ("finish", "catalog_install")
SCOPED = frozenset(("fetch", "files_list", "resource", "log", "locale", "target_add", "target_remove"))
PAGE_FORBIDDEN = ("file_roots", "safe", "channel")
NOAUTH = {"error": "not authorized"}
CORE_TOK = os.urandom(16).hex()
TOKS = {}
ZERO = (0, 0, 0, 0)
LOADER = "https://github.com/chaosfox26/framey"
SHA = re.compile(r"[0-9a-f]{40}")
SUDO = ["sudo", "-n"]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
ETC = "/etc/"
KEYGEN = "/usr/bin/ssh-keygen"
PRINCIPAL = "framey-release"
HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
NOSETUP = "Protected updater not set up: run Install / Update in the Framey App once (it asks for your password a single time)"
BUSY = {"busy": True, "error": "Busy: another check or update is running"}
MANIFEST = ("framey.py", "inject.js", "bar.js", "icon.svg", "fy_proto.py", "fy_worker.py", "fy_supervisor.py", "fy_store.py", "fy_sdk.py", "fy_net.py", "fy_files.py", "fy_media.py", "fy_targets.py", "fy_state.py", "fy_pkg.py", "fy_updates.py", "fy_dev.py", "fy_diag.py", "fy_maint.py", "fy_iso.py", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-modules.js", "fy-settings.js", "fy-badge.js", "fy-picker.js", "fy-dev.js", "fy-strings-en.js", "fy_catalog.py", "fy_decky_catalog.py", "fy-catalog.js", "catalog/index.json")
LEGACY = MANIFEST[:4]
SMOKE = "import importlib.util as u,sys;s=u.spec_from_file_location('framey_smoke',sys.argv[1]);m=u.module_from_spec(s);s.loader.exec_module(m);print('framey-smoke-ok' if callable(getattr(m,'main',None)) and isinstance(getattr(m,'VERSION',None),str) else '')"
PANEL_JS = ("inject.js", "fy-api.js", "fy-ui.js", "fy-pages.js", "fy-strings-en.js", "fy-modules.js", "fy-catalog.js", "fy-settings.js", "fy-picker.js")
EVENT = "function(p,n,y,d){return this.event(p,n,y,d)}"
REPLY = "function(g,i,v){return this.reply(g,i,v)}"
BIND = "function(i,t,m){return this.bind(i,t,m)}"
RUN = "function(p){return this(p)}"
DISPOSE = "function(p){return this.dispose(p)}"
OWNER = {}
for _mod, _names in (("fy_state", "state_get mod_order mod_sort mod_hide mod_freeze prefs_set"), ("fy_updates", "ver_list ver_install repair notes batch_plan batch_apply loader_check badge autocheck_status companion_fetch"), ("fy_targets", "target_add target_remove"), ("fy_net", "fetch"), ("fy_files", "files_list"), ("fy_media", "resource"), ("fy_dev", "dev_set dev_status devtools_info"), ("fy_diag", "diag"), ("fy_maint", "maint_get maint_refresh maint_prefs"), ("fy_catalog", "catalog_list catalog_details catalog_install catalog_sources catalog_assess")):
    OWNER.update(dict.fromkeys(_names.split(), _mod))
LOCKED = frozenset(("catalog_install", "catalog_sources", "loader_check", "ver_install", "repair", "batch_apply", "companion_fetch"))
DEV_OFF = {"dev_status": {"enabled": False, "hot": [], "exposure": {"state": "unknown", "addresses": []}}}
LOCK = None
LIVE = {}
TASKS = set()
BG = set()
WATCH = {}
MODS = {}
NOTED = set()
TAIL = collections.deque(maxlen=40)
TOGGLES = collections.deque(maxlen=5)
CLICKS = {"untrusted": 0}
FAILS = {}
LOGS = {}
FLUSH = None
supervisor = updates = None
MACHINE = {"amd64": "x86_64", "arm64": "aarch64"}
stopping = False
PANEL = False
RM = "document.querySelectorAll('[data-fy]').forEach(e=>e.remove());"
WIPE = RM + "delete window.Framey"
WIPE_BAR = "window.FYBar&&window.FYBar.dispose&&window.FYBar.dispose();window.FYObs&&window.FYObs.disconnect();" + RM + "delete window.FYBar;delete window.FYObs"
WIPES = {"main": WIPE, "bar": WIPE_BAR}
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
started_at = time.time()
now, mono, loop = time.time, time.monotonic, asyncio.get_running_loop


def rand():
    import random

    return random.random()


class Main(str):
    pass


class Host:
    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name) from None


host = Host()


def log(*a):
    TAIL.append(" ".join(map(str, a))[:300])
    print(*a, file=sys.stderr, flush=True)


def log_tail():
    return list(TAIL)


def rmtree(p):
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def sweep(p):
    if os.path.lexists(p) and p.parent != PLUGINS and not (p / ".version").is_file():
        raise OSError("not a Framey folder: " + p.name)
    rmtree(p)


def tree_copy(a, b):
    import shutil
    shutil.copytree(a, b)


def same(a, b):
    d = len(a) ^ len(b)
    for x, y in zip(a.encode(), b.encode()):
        d |= x ^ y
    return d == 0


def use(name):
    if name not in MODS:
        try:
            m = importlib.import_module(name)
            missing = [n for n in REQUIRED.get(name, ()) if not hasattr(m, n)]
            if missing:
                raise ImportError("missing " + ", ".join(missing))
            MODS[name] = m
        except Exception as e:
            log(name, "unavailable:", e)
            MODS[name] = None
    return MODS[name]


def ok_id(x):
    return isinstance(x, str) and bool(NAME.fullmatch(x)) and x not in RESERVED_IDS


def finite(c):
    raise ValueError(c)


def parse(s):
    try:
        v = json.loads(s, parse_constant=finite)
    except (TypeError, ValueError, RecursionError):
        return {}
    return v if isinstance(v, dict) else {}


def valid(m):
    return isinstance(m, dict) and all(isinstance(m.get(k, ""), str) for k in ("name", "version", "short"))


def manifest(d):
    if not ok_id(d.name):
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
    dv = m.get("data_version")
    m["data_version"] = dv if type(dv) is int and dv >= 0 else 0
    return m


def conf_file(name):
    f = CONF.parent / name
    if not f.is_file():
        return None
    try:
        j = json.loads(read(f, 4097) or "{}", parse_constant=finite) if f.stat().st_size <= 4096 else {}
    except (OSError, ValueError, RecursionError):
        j = {}
    return j if isinstance(j, dict) else {}


def trust():
    st, dev, cfg = use("fy_state"), conf_file("developer"), conf_file("loader.json") or {}
    out = {"safe": (CONF.parent / "safe").is_file(), "dev": dev is not None, "channel": "stable", "file_roots": [], "targets": {}}
    for field, key, src in (("targets", "dev.targets", dev or {}), ("channel", "channel", cfg), ("file_roots", "file_roots", cfg)):
        v, bad = st.PREF_F[key](src[field]) if st and field in src else (None, True)
        if not bad:
            out[field] = v
    return out


def overlay(p):
    t = trust()
    p = p if isinstance(p, dict) else {}
    dev = p.get("dev") if isinstance(p.get("dev"), dict) else {}
    return {**p, "safe": t["safe"], "channel": t["channel"], "file_roots": t["file_roots"], "dev": {**dev, "enabled": t["dev"], "targets": t["targets"]}}


def settings():
    st = use("fy_state")
    if st:
        s = st.load(CONF)
    else:
        try:
            s = json.loads(CONF.read_text())
        except Exception:
            s = None
        s = s if isinstance(s, dict) else {}
        off = s.get("disabled")
        s = {**s, "disabled": [x for x in off if isinstance(x, str)] if isinstance(off, list) else []}
    s["prefs"] = overlay(s.get("prefs"))
    return s


def unreadable():
    st = use("fy_state")
    if st:
        bad = st.broken(CONF)
    else:
        try:
            CONF.read_bytes() if CONF.exists() else None
            bad = False
        except OSError:
            bad = True
    if bad and "settings" not in NOTED:
        NOTED.add("settings")
        log("settings.json could not be read or parsed; no plugins are injected until it is")
    return bad


def settings_state():
    st = use("fy_state")
    if st:
        return st.state(CONF)
    return "unreadable" if unreadable() else "ok"


def save(s):
    st = use("fy_state")
    if st:
        return st.save(CONF, s)
    try:
        CONF.read_bytes() if CONF.exists() else None
    except OSError:
        raise RuntimeError("unreadable: settings could not be read; not overwriting them") from None
    CONF.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONF.with_name(CONF.name + ".tmp")
    tmp.write_text(json.dumps(s))
    os.replace(tmp, CONF)


def pref(key, default=None):
    return (settings().get("prefs") or {}).get(key, default)


def safe():
    return pref("safe") is True


def isolation():
    m = use("fy_iso")
    return m.mode(conf_file("loader.json"), CONF.parent / "isolation") if m else False


def dev_on():
    return (pref("dev") or {}).get("enabled") is True


def entries(p):
    try:
        return sorted(p.iterdir())
    except OSError:
        return []


def manifests():
    return [m for m in map(manifest, entries(PLUGINS)) if m]


def catalog():
    return [{"id": m["id"], "name": m["name"], "version": m.get("version", ""), "installed": os.path.lexists(PLUGINS / m["id"]), "linked": (PLUGINS / m["id"]).is_symlink()} for m in map(manifest, entries(STORE)) if m]


def plugin_dir(pid):
    return PLUGINS / pid


def dirs(pid):
    return use("fy_store").dirs(pid, home=HOME)


def module_state(pid):
    return use("fy_state").module_state(settings(), pid)


def set_module_state(pid, rec):
    s = settings()
    use("fy_state").set_module_state(s, pid, rec)
    save(s)


def snapshot_settings(pid):
    return use("fy_store").snapshot_settings(dirs(pid).config)


def drop_snapshot(pid):
    use("fy_store").drop_snapshot(dirs(pid).config)


def restore_snapshot(pid):
    return use("fy_store").restore_snapshot(dirs(pid).config)


def purge(pid):
    return use("fy_store").purge(dirs(pid))


def forget(pid, keep):
    st = use("fy_state")
    s = settings()
    if st:
        rec = st.module_state(s, pid)
        st.forget_module(s, pid)
        if keep and rec:
            st.set_module_state(s, pid, rec)
    s["disabled"] = [x for x in s["disabled"] if x != pid]
    save(s)


def loader_ok(d):
    try:
        compile((d / "framey.py").read_bytes(), "framey.py", "exec")
    except (OSError, ValueError, SyntaxError):
        return False
    return True


def loader_stage_owned(path):
    try:
        return not _link(path) and (path / ".framey-stage").read_bytes() == b"framey-update-stage-v1\n"
    except OSError:
        return False


def heal_loader():
    L, new, bak = ROOT.resolve(), sib(".new"), sib(".bak")
    if bak.is_dir() and not loader_ok(L) and loader_ok(bak):
        back(L, bak)
    if bak.is_dir() and loader_ok(L):
        for k in ("plugins", "store"):
            if (bak / k).exists() and not (L / k).exists():
                (bak / k).rename(L / k)
        (L / "plugins").mkdir(exist_ok=True)
    if new.exists() and loader_ok(L):
        if loader_stage_owned(new):
            rmtree(new)
        else:
            log("loader recovery kept unrecognised staging folder:", new.name)


def _link(p):
    return p.is_symlink() or getattr(p, "is_junction", lambda: False)()


def recover():
    def usable(path, pid):
        if _link(path) or not path.is_dir():
            return False
        try:
            meta = json.loads((path / "plugin.json").read_text())
            return valid(meta) and meta.get("id", pid) == pid
        except (OSError, ValueError, TypeError):
            return False

    ids = {p.name.partition("-")[2] for p in entries(PLUGINS) if p.name.startswith((".old-", ".hold-"))}
    for pid in sorted(ids):
        if not ok_id(pid):
            continue
        live, old, hold = PLUGINS / pid, PLUGINS / (".old-" + pid), PLUGINS / (".hold-" + pid)
        try:
            if not os.path.lexists(live):
                if usable(old, pid):
                    old.rename(live)
                elif usable(hold, pid) and not os.path.lexists(old):
                    hold.rename(live)
            if live.is_dir() and usable(hold, pid):
                if _link(old):
                    log("recover", old.name, "refused: link")
                    continue
                if os.path.lexists(old):
                    rmtree(old)
                hold.rename(old)
        except OSError as e:
            log("recover", pid, "failed:", e)
    for p in entries(PLUGINS):
        kind, _, pid = p.name.partition("-")
        try:
            if kind in (".old", ".hold") and _link(p):
                log("recover", p.name, "refused: link")
            elif kind == ".old" and ok_id(pid) and not os.path.lexists(PLUGINS / pid) and (p / "plugin.json").is_file():
                p.rename(PLUGINS / pid)
            elif kind == ".hold" and ok_id(pid) and not os.path.lexists(PLUGINS / (".old-" + pid)):
                p.rename(PLUGINS / (".old-" + pid))
            elif kind == ".old" and ok_id(pid) and (pending(pid) or held(pid)):
                continue
            elif kind in (".old", ".stage"):
                rmtree(p)
            elif ok_id(p.name) and p.is_symlink() and real(p.name, False):
                t = real(p.name, False)
                old, stage = t.with_name(".old-" + t.name), t.with_name(".stage-" + t.name)
                sweep(stage)
                if old.exists() and not t.exists():
                    old.rename(t)
                elif old.exists() and not pending(p.name) and not held(p.name):
                    sweep(old)
        except Exception as e:
            log("recover", p.name, "failed:", e)
    try:
        heal_loader()
    except Exception as e:
        log("recover loader failed:", e)


def mark(pid, what, msg, stage=None):
    FAILS[pid] = {"what": what, "msg": str(msg)[:300], "at": time.time(), **({"stage": stage} if stage else {})}


def spawn(coro):
    t = asyncio.create_task(coro)
    BG.add(t)
    t.add_done_callback(BG.discard)
    t.add_done_callback(lambda f: f.cancelled() or f.exception())
    return t


def sup():
    global supervisor
    m = use("fy_supervisor")
    if m and supervisor is None:
        supervisor = m.Supervisor(host)
    return supervisor


def upd():
    global updates
    m = use("fy_updates")
    if m and updates is None:
        updates = m.Updates(host)
        updates.after = push_badge
        updates.cycled = lambda: maint_spawn(True)
    return updates


def hostcall(mod):
    async def run(pid, arg):
        m = use(mod)
        if not m:
            raise RuntimeError(mod + " unavailable")
        return await m.hostcall(host, pid, arg)

    return run


hostcalls = {"fetch": hostcall("fy_net"), "files": hostcall("fy_files"), "resource": hostcall("fy_media")}


async def backend(pid, method, arg):
    s = sup()
    if not s:
        raise RuntimeError("fy_supervisor unavailable")
    try:
        r = await s.call(pid, method, arg)
    except use("fy_supervisor").BackendError as e:
        if e.code == "no_backend":
            raise LookupError("backend not loaded") from None
        mark(pid, "backend", e, e.stage)
        raise
    if FAILS.get(pid, {}).get("what") == "backend":
        del FAILS[pid]
    return r


async def backend_check(pid):
    if not (supervisor and pid in supervisor.status()):
        return ""
    try:
        await supervisor.call(pid, "__ping__", None)
    except use("fy_supervisor").BackendError as e:
        return e.message if e.stage or e.code in ("failed", "start", "spawn", "exit") else ""
    except Exception:
        return ""
    return ""


def load_backends(only=None):
    if safe() or unreadable():
        return
    off = settings()["disabled"]
    for m in manifests():
        if only in (None, m["id"]) and m["id"] not in off and (PLUGINS / m["id"] / "backend.py").exists() and sup():
            supervisor.register(m["id"], PLUGINS / m["id"], m)


async def drop(only):
    t = getattr(host, "targets", None)
    if t:
        await (t.remove_plugin(only) if only else t.remove_all())
        sync_watches()
    media = MODS.get("fy_media")
    if media:
        for pid in [only] if only else [m["id"] for m in manifests()]:
            media.get().revoke_plugin(pid)


async def reload(only=None, reason="reload"):
    if reason in ("disable", "uninstall") and only:
        drop_tok(only)
    if supervisor:
        ids = [k for k in supervisor.status() if only in (None, k)]
        await asyncio.gather(*(supervisor.stop(k, reason) for k in ids))
        store = use("fy_store")
        for k in ids:
            supervisor.forget(k)
            if store:
                store.clean_tmp(dirs(k).runtime)
    await drop(only)
    load_backends(only)


def events_ready():
    global FLUSH
    if FLUSH is None:
        FLUSH = loop().call_later(0.1, flush)


def flush():
    global FLUSH
    FLUSH = None
    if "main" not in LIVE or not supervisor:
        return
    evs = supervisor.events.take(10)
    if evs:
        spawn(emit_all(evs, event_context(evs)))
    if supervisor.events._size:
        events_ready()


def attached(target):
    return target in LIVE


def overlay_key(url, want):
    p = urllib.parse.urlsplit(url)
    return any(urllib.parse.parse_qs(q).get("vrOverlayKey") == [want] for q in (p.query, p.fragment.partition("?")[2]))


async def send(target, expression):
    f = LIVE.get(target)
    if f:
        await f("Runtime.evaluate", expression=expression)


async def send_main(target, expression):
    f = LIVE.get(target)
    if f:
        await (f.main(expression) if hasattr(f, "main") else f("Runtime.evaluate", expression=expression))


async def call_core(f, fn, args):
    if f and getattr(f, "core", None):
        await f("Runtime.callFunctionOn", objectId=f.core, functionDeclaration=fn, arguments=[{"value": a} for a in args], returnByValue=True)


def event_context(evs):
    sender = LIVE.get("main")
    registrations = getattr(supervisor, "_ws", {})
    owners = {}
    for event in evs:
        pid = event["plugin"]
        worker = registrations.get(pid)
        owners[pid] = (worker, getattr(worker, "epoch", None), tuple(t for t, owner in TOKS.items() if owner == pid))
    return sender, getattr(sender, "core", None), supervisor, owners


async def emit_all(evs, context=None):
    sender, core, active, owners = context if context is not None else event_context(evs)
    for e in evs:
        if LIVE.get("main") is not sender or getattr(sender, "core", None) != core or supervisor is not active:
            break
        worker = getattr(active, "_ws", {}).get(e["plugin"])
        current = (worker, getattr(worker, "epoch", None), tuple(t for t, owner in TOKS.items() if owner == e["plugin"]))
        if current != owners[e["plugin"]]:
            continue
        await call_core(sender, EVENT, [e["plugin"], e["name"], e["payload"], e["dropped"]])


async def notify_page(plugin, name, payload):
    await call_core(LIVE.get("main"), EVENT, [plugin, name, payload, 0])


async def progress(job, event):
    await notify_page("_core", "progress", event)


def badge_n():
    u = settings().get("updates") or {}
    if not (u.get("plugins") or (u.get("loader") or {}).get("state")):
        return 0
    return upd().badge()["n"] if upd() else 0


async def push_badge(b):
    await send("bar", "window.FYBar&&FYBar.badge(%d)" % b["n"])


async def mark_current(pid, latest):
    if upd():
        await upd().record_checked({pid: {"state": "current", "latest": latest}})


def sync_watches():
    t = getattr(host, "targets", None)
    want = t.wanted() if t else []
    for n in [n for n in WATCH if n not in want]:
        WATCH.pop(n).cancel()
    for n in want:
        if n not in WATCH:
            WATCH[n] = spawn(watch(n, lambda u, n=n: bool(t.urlpart(n)) and t.urlpart(n) in u, lambda n=n: t.replay(n), "typeof window.__framey_targets"))


def replay(name):
    t = getattr(host, "targets", None)
    return [(Main(e), p) for e, p in t.replay(name)] if t else []


def js(name):
    try:
        return (ROOT / name).read_text()
    except (OSError, ValueError) as e:
        log(name, "unreadable:", e)
        return ""


def platform_name():
    if hasattr(os, "uname"):
        m = os.uname().machine.lower()
    else:
        m = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
    return "linux-" + MACHINE.get(m, m)


PLATFORM = platform_name()


def trusted_input():
    mode = (conf_file("loader.json") or {}).get("trusted_input")
    return mode if mode in ("off", "auto", "require") else "auto"


ICON_DEFAULT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="28" fill="#0e0e1c" stroke="#5585ff" stroke-width="4"/></svg>'


def prelude():
    url = "data:image/svg+xml," + urllib.parse.quote(js("icon.svg") or ICON_DEFAULT)
    rt = {"version": VERSION, "api": API_GENERATION, "platform": PLATFORM, "lang": pref("lang", ""), "trusted_input": trusted_input()}
    return "window.FY_ICON_URL=%s;window.FY_RUNTIME=%s;\n" % (json.dumps(url), json.dumps(rt))


def drop_tok(pid):
    for t in [t for t, p in TOKS.items() if p == pid]:
        del TOKS[t]


def new_tok(pid):
    drop_tok(pid)
    TOKS[tok := os.urandom(16).hex()] = pid
    return tok


def panel_js(parts):
    return "(function(__fyt){%s\n%s\nreturn __FY&&__FY._k(__fyt);})(%s)" % ("\n".join(parts[:2]), "\n".join("try{%s\n}catch(e){console.error(e)}" % p for p in parts[2:]), json.dumps(CORE_TOK))


def plugin_args(m, keep=False):
    tok = next((t for t, p in TOKS.items() if keep and p == m["id"]), None) or new_tok(m["id"])
    return m, tok, (PLUGINS / m["id"] / "main.js").read_text()


async def load_plugin(m, tok, src, pid=None, redraw=True):
    f = LIVE.get("main")
    if f and getattr(f, "plugin", None):
        await f.plugin(m, tok, src, pid, redraw)


def page_root_ok(a):
    m = use("fy_files")
    root = a.get("root")
    if not m or (root is not None and not isinstance(root, str)):
        return False
    return root is None or root == "data" or root in set(m.roots(HOME, None, pref("file_roots", []))) - {"home", "media"}


def request(payload):
    return parse(payload) if isinstance(payload, str) and len(payload) <= PAYLOAD_IN else {}


def authorize(req):
    method, t = req.get("method"), req.get("t")
    if not isinstance(t, str):
        return method == "toggle", req.get("arg")
    if same(t, CORE_TOK):
        return method not in SCOPED, req.get("arg")
    pid = TOKS.get(t)
    if pid and method in SCOPED:
        a = {**(req.get("arg") if isinstance(req.get("arg"), dict) else {}), "pid": pid, "id": pid}
        return method != "files_list" or page_root_ok(a), a
    return method == "toggle", req.get("arg")


def backend_ok(req):
    t, pid = req.get("t"), req.get("plugin")
    if not isinstance(t, str) or not isinstance(pid, str):
        return False
    return same(t, CORE_TOK) or TOKS.get(t) == pid


def main_code():
    global CORE_TOK
    CORE_TOK = os.urandom(16).hex()
    TOKS.clear()
    built = [prelude()] + [js(f) for f in PANEL_JS if not (safe() and f == "fy-pages.js")]
    if conf_file("developer") is not None:
        built.append(js("fy-dev.js"))
    out = [(panel_js(built), "#core")]
    off = settings()["disabled"]
    for k in [k for k, v in FAILS.items() if v["what"] != "backend"]:
        del FAILS[k]
    for m in [] if safe() or unreadable() else manifests():
        try:
            f = PLUGINS / m["id"] / "main.js"
            if m["id"] not in off and f.exists():
                out.append((plugin_args(m), m["id"]))
        except Exception as e:
            log("main.js", m["id"], "failed:", e)
    return out + replay("main") + [("Framey.restore()", None)]


def bar_code():
    return [(prelude() + js("bar.js"), None), (js("fy-badge.js"), None), ("window.FYBar&&FYBar.set(%s)" % json.dumps(PANEL), None), ("window.FYBar&&FYBar.badge(%d)" % badge_n(), None)] + replay("bar")


async def panel(on):
    global PANEL
    PANEL = on
    await send("bar", "window.FYBar&&FYBar.set(%s)" % json.dumps(on))
    await send("main", "window.Framey&&Framey._panel(%s)" % json.dumps(on))
    if on and pref("autocheck") is True and upd():
        spawn(upd().on_panel_open())


async def refresh():
    send = LIVE.get("main")
    if send:
        await send("Runtime.evaluate", expression=WIPE)
        await send.ping()


PINS = {}


def pinned_https():
    import http.client

    class Conn(http.client.HTTPSConnection):
        def connect(self):
            n = use("fy_net")
            if not n:
                raise OSError("fy_net unavailable")
            end = time.monotonic() + self.timeout
            ips = PINS.pop((self.host, self.port), None) or n._resolve(self.host, self.port, None, end)
            self.sock = n._connect("https", self.host, self.port, ips, end, None, [])

    return Conn


def fetch(url, headers=None, hosts=None, limit=5_000_000, progress=None):
    import urllib.request

    class Https(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.lower().startswith("https://"):
                raise ValueError("redirected away from https")
            if hosts and urllib.parse.urlsplit(newurl).hostname not in hosts:
                raise ValueError("redirected to a host that is not allowed")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class Pinned(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(pinned_https(), req)

    end = time.monotonic() + FETCH_S
    parts, n = [], 0
    with urllib.request.build_opener(Https, Pinned).open(urllib.request.Request(url, headers=headers or {}), timeout=FETCH_S) as r:
        total = r.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None
        sock = getattr(getattr(r.fp, "raw", None), "_sock", None)
        while (left := end - time.monotonic()) > 0:
            if r.isclosed():
                break
            if sock:
                sock.settimeout(left)
            try:
                chunk = r.read1(65536)
            except (TimeoutError, socket.timeout):
                raise TimeoutError("download too slow") from None
            if not chunk:
                break
            n += len(chunk)
            if n > limit:
                raise ValueError("download too large")
            parts.append(chunk)
            if progress:
                progress(n, total)
        else:
            raise TimeoutError("download too slow")
        if r.length:
            raise ValueError("download incomplete: the connection closed early")
    return b"".join(parts)


def addr_check(url):
    n = use("fy_net")
    if not n:
        raise ValueError("fy_net unavailable")
    _, h, port, _ = n._target(url, False)
    n._resolve(h, port, None)


def pin(url):
    n = use("fy_net")
    if not n:
        raise ValueError("fy_net unavailable")
    _, h, port, _ = n._target(url, False)
    PINS[(h, port)] = n._resolve(h, port, None)


async def install(url):
    if not str(url).startswith("https://"):
        return {"error": "https links only"}
    try:
        await asyncio.to_thread(pin, url)
        data = await asyncio.to_thread(fetch, url)
    except Exception as e:
        return {"error": getattr(e, "message", None) or str(e)}
    finally:
        PINS.clear()
    return await install_zip(data)


def fix_identity(stage):
    f, p = stage / ".identity.json", use("fy_pkg")
    try:
        j = json.loads(f.read_text())
        if p and isinstance(j, dict) and j.get("v") != 1:
            p.write_identity(stage, p.identity(j.get("kind"), j.get("source"), j.get("tag"), j.get("commit")))
    except FileNotFoundError:
        pass
    except Exception:
        f.unlink(missing_ok=True)


def stage_swap(pid, fill, dest=None, keep=False):
    dest = dest or PLUGINS / pid
    stage = dest.with_name(".stage-" + dest.name)
    old = dest.with_name(".old-" + dest.name)
    sweep(stage)
    sweep(old)
    try:
        fill(stage)
        fix_identity(stage)
        had = dest.exists()
        if hasattr(os, "sync"):
            os.sync()
        if had:
            dest.rename(old)
        try:
            stage.rename(dest)
        except OSError:
            if had:
                old.rename(dest)
            raise
        if not keep:
            rmtree(old)
    finally:
        rmtree(stage)


async def sync_disk():
    n = use("fy_net")
    if n:
        await n.sync_disk()


def guard(path):
    p = use("fy_pkg")
    if p:
        p.replace_guard(path)


def restore_ready(dest):
    old, bad = dest.with_name(".old-" + dest.name), dest.with_name(".stage-" + dest.name)
    if not dest.is_dir() or _link(dest) or not old.is_dir():
        raise OSError("no previous version to restore")
    if _link(old):
        raise OSError("previous copy is a link")
    try:
        meta = json.loads((old / "plugin.json").read_text())
    except (OSError, ValueError, TypeError):
        raise OSError("previous copy has no valid manifest") from None
    if not valid(meta):
        raise OSError("previous copy has no valid manifest")
    if os.path.lexists(bad) and (_link(bad) or bad.parent != PLUGINS and not (bad / ".version").is_file()):
        raise OSError("unrecognised staging folder: " + bad.name)
    return old, bad, meta


def restore(dest):
    old, bad, _ = restore_ready(dest)
    sweep(bad)
    dest.rename(bad)
    try:
        old.rename(dest)
    except OSError:
        bad.rename(dest)
        raise
    rmtree(bad)


def vet(data, loader=False):
    import zipfile

    if len(data) > 5_000_000:
        return {"error": "zip over 5 MB"}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"error": "not a zip"}
    if any(i.orig_filename != i.filename for i in z.infolist()):
        return {"error": "zip has ambiguous entry names"}
    infos = {i.filename: i for i in z.infolist() if not i.filename.endswith("/")}
    names = list(infos)
    if len(z.infolist()) > ENTRIES or any(n.startswith("/") or "\\" in n or len(n.split("/")) > DEPTH or any(x in ("", ".", "..") for x in n.split("/")) for n in names):
        return {"error": "zip has unsafe paths or too many files"}
    if any(i.file_size > ENTRY_MAX for i in infos.values()) or sum(i.file_size for i in infos.values()) > TOTAL_MAX:
        return {"error": "zip too large unpacked"}
    key = "framey.py" if loader else "plugin.json"
    base = ""
    if key not in names:
        tops = {n.split("/")[0] for n in names}
        if len(tops) != 1 or next(iter(tops)) + "/" + key not in names:
            return {"error": "no " + key}
        base = next(iter(tops)) + "/"
    if loader:
        return z, infos, base, None, None
    try:
        with z.open(base + "plugin.json") as f:
            raw = f.read(65537)
    except Exception:
        raw = b""
    if len(raw) > 65536:
        return {"error": "plugin.json over 64 KB"}
    try:
        meta = json.loads(raw)
    except Exception:
        meta = None
    pid = meta.get("id") if valid(meta) else None
    if not ok_id(pid):
        return {"error": "bad plugin id"}
    native_entry = any(base + f in infos for f in ("main.js", "backend.py"))
    decky_layout = "api_version" in meta or any(base + f in infos for f in ("main.py", "dist/index.js"))
    if decky_layout and (not native_entry or "api_version" in meta and (type(meta.get("api")) is not int or meta["api"] < 1)):
        return {"error": "This is a Decky plugin. It needs a Framey port before it can run on this headset", "code": "unsupported_format"}
    return z, infos, base, pid, meta


def unpack(z, infos, base):
    def fill(stage):
        total = 0
        for n, i in infos.items():
            if n.startswith(base):
                f = stage / n[len(base):]
                if stage.resolve() not in f.resolve().parents:
                    raise ValueError("zip has unsafe paths")
                f.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with z.open(i) as src, f.open("wb") as out:
                    while chunk := src.read(65536):
                        size += len(chunk)
                        total += len(chunk)
                        if size > ENTRY_MAX or total > TOTAL_MAX:
                            raise ValueError("zip too large unpacked")
                        out.write(chunk)
                if i.external_attr >> 16 & 0o100:
                    f.chmod(0o755)

    return fill


def untar(data, top):
    import tarfile

    def fill(stage):
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        except tarfile.TarError:
            raise ValueError("not a tar.gz")
        n = total = 0
        with tf:
            for m in tf:
                n += 1
                parts = m.name.split("/")
                if n > ENTRIES or m.name.startswith("/") or "\\" in m.name or len(parts) > DEPTH or parts[0] != top or any(x in ("", ".", "..") for x in parts) or not (m.isreg() or m.isdir()) or m.issparse() or m.size > ENTRY_MAX or (m.isreg() and len(parts) < 2):
                    raise ValueError("package has unsafe or oversized entries")
                if m.isdir():
                    continue
                f = stage.joinpath(*parts[1:])
                f.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with tf.extractfile(m) as src, f.open("wb") as out:
                    while chunk := src.read(65536):
                        size += len(chunk)
                        total += len(chunk)
                        if size > ENTRY_MAX or total > TOTAL_MAX:
                            raise ValueError("package too large unpacked")
                        out.write(chunk)
                if m.mode & 0o100:
                    f.chmod(0o755)

    return fill


async def install_zip(data):
    v = vet(data)
    if isinstance(v, dict):
        return v
    z, infos, base, pid, meta = v
    bad = gate(meta)
    if bad:
        return {"error": bad}
    if (PLUGINS / pid).is_symlink():
        return {"error": "that plugin is linked, remove the link first"}
    if pending(pid):
        return {"error": "finish or roll back the pending update first"}
    try:
        await asyncio.to_thread(stage_swap, pid, unpack(z, infos, base))
    except Exception as e:
        return {"error": str(e) or "install failed"}
    await reload(pid)
    return {"ok": pid}


def read(f, n=2048):
    try:
        with open(f, "rb") as h:
            return h.read(n).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def real(pid, need=True):
    p = PLUGINS / pid
    if not p.is_symlink():
        return p
    t = pathlib.Path(os.path.realpath(p))
    h, r = HOME.resolve(), ROOT.resolve()
    if h in t.parents and t != r and r not in t.parents and t not in r.parents and (t.is_dir() or not need):
        return t


def installed(pid):
    v = read(PLUGINS / pid / ".version", 64)
    return v if SHA.fullmatch(v) else ""


def gh(src):
    u = urllib.parse.urlsplit(src.strip())
    p = [urllib.parse.unquote(x) for x in u.path.split("/") if x]
    if u.scheme != "https" or u.netloc.lower() != "github.com" or len(p) < 2 or (len(p) > 2 and p[2] != "tree"):
        return None
    if p[1].endswith(".git"):
        p[1] = p[1][:-4]
    ref = p[3:]
    if all(re.fullmatch(r"[A-Za-z0-9._-]+", x) and x not in (".", "..") for x in p[:2] + ref):
        return p[0], p[1], "/".join(ref) or "HEAD"


def source(m):
    receipt = PLUGINS / m["id"] / ".catalog.json"
    if os.path.lexists(receipt):
        return None, "Pinned catalog package: manage updates and repairs in Store"
    found = [x for x in (read(PLUGINS / m["id"] / ".source"), m.get("source")) if isinstance(x, str) and x.strip()]
    for x in found:
        g = gh(x)
        if g:
            return g, ""
    return None, "Automatic checking unavailable: " + ("the recorded source is not a GitHub repository link" if found else "no update source recorded")


def api(path):
    return fetch("https://api.github.com/repos/" + path, {"Accept": "application/vnd.github+json", "User-Agent": "Framey"})


def api_json(path):
    return json.loads(api(path))


def latest(owner, repo, ref):
    t = fetch("https://api.github.com/repos/%s/%s/commits/%s" % (owner, repo, urllib.parse.quote(ref, safe="/")), {"Accept": "application/vnd.github.sha", "User-Agent": "Framey"}).decode("ascii", "replace").strip()
    if not SHA.fullmatch(t):
        raise ValueError("unexpected reply from GitHub")
    return t


def release(owner, repo):
    try:
        j = json.loads(api("%s/%s/releases/latest" % (owner, repo)))
        tag = j["tag_name"]
        if not (isinstance(tag, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tag)):
            raise ValueError
        ok = [a for a in j.get("assets", []) if isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str)]
        assets = {a["name"]: a["browser_download_url"] for a in ok}
        digests = {a["name"]: a["digest"] for a in ok if isinstance(a.get("digest"), str)}
        body = j.get("body") if isinstance(j.get("body"), str) else ""
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("unexpected reply from GitHub")
    return {"tag": tag, "assets": assets, "digests": digests, "notes": body[:600]}


def archive(owner, repo, sha, progress=None):
    url, head = "https://github.com/%s/%s/archive/%s.zip" % (owner, repo, sha), {"User-Agent": "Framey"}
    return fetch(url, head, None, 5_000_000, progress) if progress else fetch(url, head)


def compat(m):
    p = use("fy_pkg")
    return p.decide(m, VERSION, PLATFORM) if p and isinstance(m, dict) else None


def gate(m):
    p = use("fy_pkg")
    if not p:
        return "fy_pkg unavailable"
    d = p.decide(m, VERSION, PLATFORM)
    return d["msg"] if d["state"] == "incompatible" else ""


def vkey(s):
    p = use("fy_pkg")
    v = p.parse_version(s) if p else None
    return v[0] if v is not None and not v.prerelease else None


def why(e):
    import urllib.error

    if isinstance(e, urllib.error.HTTPError):
        if e.code == 429 or (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0"):
            return "GitHub rate limit reached, try again later"
        return "not found on GitHub (no release yet, private, or renamed)" if e.code == 404 else "GitHub answered HTTP %d" % e.code
    if isinstance(e, urllib.error.URLError):
        return "network error: %s" % e.reason
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "timed out: " + (str(e) or "no answer from GitHub")
    return str(e) or type(e).__name__


def priv(m):
    p = m.get("privileged") if m else None
    if isinstance(p, dict) and all(isinstance(p.get(k), str) and os.path.isabs(p[k]) for k in ("installed", "helper")):
        return p


def root_ok(st, kind):
    return kind(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022


def trusted(path):
    try:
        return root_ok(os.lstat(path), stat.S_ISREG) and root_ok(os.lstat(os.path.dirname(path)), stat.S_ISDIR)
    except OSError:
        return False


def sane(h):
    return h.startswith(ETC) and os.path.normpath(h) == h and trusted(h)


def marker(p):
    return read(p["installed"], 64) if trusted(p["installed"]) else ""


def pending(pid):
    d = real(pid)
    m = manifest(PLUGINS / pid) if d else None
    p = priv(m)
    if not p or not d.with_name(".old-" + d.name).is_dir():
        return None
    v = m.get("version", "")
    return None if v and marker(p) == v else {"installed": p["installed"]}


def held(pid):
    d = real(pid)
    return bool(d and (d.with_name(".old-" + d.name) / "plugin.json").is_file())


def helper(p, arg):
    import subprocess

    h = p["helper"]
    if not sane(h):
        return NOSETUP
    try:
        r = subprocess.run(SUDO + [h, arg], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, env=ENV)
    except subprocess.TimeoutExpired:
        return "The protected updater took longer than 3 minutes"
    except OSError:
        return NOSETUP
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode == 0:
        return ""
    if any(x in out for x in ("password is required", "not allowed", "may not run sudo")):
        return NOSETUP
    return "The protected updater failed (exit %d): %s" % (r.returncode, out[-200:])


def verify(data, sig, signers):
    import subprocess
    import tempfile

    ns = re.search(r'namespaces="([^",]+)', read(signers, 4096))
    if not ns or not sig or len(sig) > 8192:
        return "Release signature check failed: no usable signature or signing key"
    with tempfile.TemporaryDirectory() as t:
        f = pathlib.Path(t) / "sig"
        f.write_bytes(sig)
        try:
            r = subprocess.run([KEYGEN, "-Y", "verify", "-f", signers, "-I", PRINCIPAL, "-n", ns[1], "-s", str(f)], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, env=ENV)
        except (OSError, subprocess.TimeoutExpired):
            return "Could not run the signature check"
    return "" if r.returncode == 0 else "Refused: the package is not signed by the Framey publisher"


def digest_bad(data, digest):
    import hashlib

    algo, _, want = (digest or "").partition(":")
    return algo == "sha256" and hashlib.sha256(data).hexdigest() != want.lower()


def probe(g, cur):
    try:
        sha = latest(*g)
    except Exception as e:
        return {"state": "error", "msg": why(e)}
    return {"state": "current" if sha == cur else "update", "latest": sha[:7], "known": bool(cur)}


def rel_probe(m, g):
    try:
        rel = release(g[0], g[1])
        tag = rel["tag"]
    except Exception as e:
        return {"state": "error", "msg": why(e)}
    new = vkey(tag)
    if not new:
        return {"state": "error", "msg": "unrecognised release tag " + tag}
    r = {"state": "update" if new > (vkey(m.get("version", "")) or ZERO) else "current", "latest": tag, "known": True}
    return {**r, "notes": rel["notes"]} if r["state"] == "update" else r


def look(m):
    g, msg = source(m)
    if not g:
        return {"state": "none", "msg": msg}
    return rel_probe(m, g) if priv(m) else probe(g, installed(m["id"]))


def loader_sha():
    v = read(ROOT / ".version", 64)
    return v if SHA.fullmatch(v) else ""


def sib(sfx):
    L = ROOT.resolve()
    return L.with_name(L.name + sfx)


def info(m):
    pid = m["id"]
    return {"commit": installed(pid)[:7], "nosource": source(m)[1], "pending": pending(pid), "held": held(pid), "compat": compat(m), "fail": FAILS.get(pid), "stuck": bool(supervisor and supervisor.stuck(pid))}


async def page_reload(pid, redraw=True):
    send, m = LIVE.get("main"), manifest(PLUGINS / pid) if ok_id(pid) else None
    if send and m and pid not in settings()["disabled"] and not safe() and (PLUGINS / pid / "main.js").exists():
        await call_core(send, DISPOSE, [pid])
        await load_plugin(*plugin_args(m, True), pid=pid, redraw=redraw)


async def reload_one(pid):
    m = manifest(PLUGINS / pid) if ok_id(pid) else None
    if not m:
        return {"error": "unknown plugin"}
    FAILS.pop(pid, None)
    await reload(pid)
    await page_reload(pid)
    return {"ok": True}


def update_event(method, arg, out):
    pid = arg.get("id") if isinstance(arg, dict) else arg
    pid = pid if isinstance(pid, str) else None
    rows = out["results"] if isinstance(out.get("results"), list) else [{"id": pid, "state": "failed" if out.get("error") else ("current" if out.get("current") else "ok"), "to": out.get("version", ""), **({"error": out["error"]} if out.get("error") else {})}]
    return {"method": method, "id": pid, "ok": sum(r.get("state") == "ok" for r in rows if isinstance(r, dict)), "failed": sum(r.get("state") == "failed" for r in rows if isinstance(r, dict)), "results": rows}


async def after_update(method, arg, out):
    if "ok" in out:
        pid = arg.get("id") if isinstance(arg, dict) else arg
        for x in [r.get("id") for r in out["results"] if isinstance(r, dict) and r.get("state") == "ok"] if isinstance(out.get("results"), list) else [pid]:
            await page_reload(x, False)
    if not (out.get("confirm") or out.get("busy")):
        await notify_page("_core", "update_result", update_event(method, arg, out))


async def maint_run(m, plugins, background):
    try:
        out = await (m.background(host, plugins) if background else m.check_all(host, plugins))
        if out:
            await notify_page("_core", "maint", {"ids": sorted(out)[:64]})
    except Exception as e:
        log("maintenance check failed:", e)


def maint_spawn(background=False):
    m = use("fy_maint")
    ps = [x for x in manifests() if source(x)[0]] if m else []
    if ps:
        spawn(maint_run(m, ps, background))


async def do_check(_):
    ms = manifests()
    checked = await asyncio.to_thread(lambda: {m["id"]: look(m) for m in ms})
    if upd():
        await upd().record_checked(checked)
    maint_spawn()
    return {"checked": checked}


async def rel_update(pid, m, p, g, dest):
    signers = os.path.join(os.path.dirname(p["installed"]), "allowed_signers")
    if not sane(p["helper"]):
        return {"error": NOSETUP}
    if not trusted(signers):
        return {"error": "Release signing key not installed by root: run Install / Update in the Framey App once"}
    cur = m.get("version", "")
    name = g[1] + ".tar.gz"
    try:
        rel = await asyncio.to_thread(release, g[0], g[1])
        new = vkey(rel["tag"])
        if not new:
            return {"error": "unrecognised release tag " + rel["tag"]}
        if new <= (vkey(cur) or ZERO):
            return {"current": True}
        urls = [rel["assets"].get(x) for x in (name, name + ".sig")]
        pre = "/%s/%s/releases/download/" % (g[0], g[1])
        if not all(u and urllib.parse.urlsplit(u).scheme == "https" and urllib.parse.urlsplit(u).hostname == "github.com" and urllib.parse.urlsplit(u).path.startswith(pre) for u in urls):
            return {"error": "the release has no signed package from this repository"}
        data, sig = [await asyncio.to_thread(fetch, u, {"User-Agent": "Framey"}, HOSTS) for u in urls]
        sha = await asyncio.to_thread(latest, g[0], g[1], rel["tag"])
    except Exception as e:
        return {"error": why(e)}
    if len(data) > 5_000_000:
        return {"error": "package over 5 MB"}
    if digest_bad(data, rel.get("digests", {}).get(name)):
        return {"error": "Refused: the package does not match the digest GitHub published for it"}
    bad = await asyncio.to_thread(verify, data, sig, signers)
    if bad:
        return {"error": bad}
    src = read(PLUGINS / pid / ".source")

    def fill(stage):
        untar(data, g[1])(stage)
        meta = json.loads((stage / "plugin.json").read_text())
        if not valid(meta) or meta.get("id") != pid:
            raise ValueError("rejected: the package is a different plugin")
        if not vkey(meta.get("version")) or vkey(meta["version"]) != new:
            raise ValueError("rejected: the package version does not match the selected release")
        if vkey(meta["version"]) <= (vkey(cur) or ZERO):
            raise ValueError("rejected: the package is not newer than the installed version")
        if not priv(meta):
            raise ValueError("rejected: the package has no protected-component declaration")
        bad = gate(meta)
        if bad:
            raise ValueError(bad)
        (stage / ".source").unlink(missing_ok=True)
        if src:
            (stage / ".source").write_text(src)
        (stage / ".version").write_text(rel["tag"])
        (stage / ".identity.json").write_text(json.dumps({"kind": "release", "source": "%s/%s" % g[:2], "tag": rel["tag"], "commit": sha}))

    try:
        guard(PLUGINS / pid)
        await asyncio.to_thread(stage_swap, pid, fill, dest, True)
    except Exception as e:
        return {"error": str(e) or "update failed"}
    return await settle(pid, "update")


async def settle(pid, arg):
    try:
        m = manifest(PLUGINS / pid)
        p = priv(m)
        if not p:
            raise ValueError("the updated plugin.json is not valid")
    except Exception as e:
        try:
            restore(real(pid))
        except OSError:
            pass
        await reload(pid)
        return {"error": "update undone: " + (str(e) or "failed")}
    err = await asyncio.to_thread(helper, p, arg)
    if not err and marker(p) != m.get("version"):
        err = "Partial update: the protected part reports %s, expected %s" % (marker(p) or "nothing", m.get("version"))
    if err:
        return {"error": err if err.startswith("Partial") else "Partial update: " + err}
    await reload(pid, "update")
    await mark_current(pid, m.get("version", ""))
    return {"ok": pid, "version": m.get("version", "")}


async def do_update(pid):
    m = manifest(PLUGINS / pid) if ok_id(pid) else None
    if not m:
        return {"error": "unknown plugin"}
    dest = real(pid)
    if not dest:
        return {"error": "the link must point to a real folder inside your home, outside the loader folder"}
    if pending(pid):
        return {"error": "finish or roll back the pending update first"}
    g, msg = source(m)
    if not g:
        return {"error": msg}
    p = priv(m)
    if p:
        return await rel_update(pid, m, p, g, dest)
    return await upd().update(pid)


async def do_finish(pid):
    d = real(pid) if ok_id(pid) else None
    if not d or not d.with_name(".old-" + d.name).is_dir():
        return {"error": "no pending update"}
    if pending(pid):
        return {"pending": pending(pid)}
    rmtree(d.with_name(".old-" + d.name))
    await reload(pid)
    return {"ok": pid}


async def do_retry(pid):
    if not (ok_id(pid) and pending(pid)):
        return {"error": "nothing is waiting for the protected step"}
    return await settle(pid, "update")


async def do_rollback(pid):
    d = real(pid) if ok_id(pid) else None
    if not d or not (pending(pid) or held(pid)):
        return {"error": "nothing to roll back"}
    p = priv(manifest(PLUGINS / pid))
    try:
        _, _, oldmeta = restore_ready(d)
        oldv = oldmeta.get("version", "")
        if p and not vkey(oldv):
            raise OSError("previous protected copy has no valid version")
    except OSError as e:
        return {"error": "roll back failed: " + str(e)}
    cur = marker(p) if p else ""
    if p and cur != oldv:
        err = await asyncio.to_thread(helper, p, "rollback")
        if err:
            return {"error": "Roll back stopped: " + err}
        if marker(p) != oldv:
            return {"error": "Roll back stopped: the protected part reports %s, expected %s; user files were kept" % (marker(p) or "nothing", oldv)}
    try:
        restore(d)
    except OSError as e:
        return {"error": "roll back failed: " + (str(e) or "error")}
    await reload(pid)
    return {"ok": pid}


def back(L, bak):
    if any((L / k).exists() and (bak / k).exists() for k in ("plugins", "store")):
        raise OSError("loader and backup both hold plugins or store; not rolling back")
    for k in ("plugins", "store"):
        if (L / k).exists():
            (L / k).rename(bak / k)
    rmtree(L)
    bak.rename(L)


def needs(src):
    import ast

    try:
        for n in ast.parse(src).body:
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "MANIFEST" for t in n.targets):
                v = ast.literal_eval(n.value)
                if isinstance(v, (tuple, list)) and all(isinstance(x, str) for x in v):
                    return v
    except (SyntaxError, ValueError):
        pass
    return LEGACY


def bak_busy():
    if sib(".bak").exists():
        return {"error": "Finish the previous Framey update first (Done or Roll back)"}


async def loader_step(job, stage, state, error=None, measured=None):
    if job:
        await job.emit(stage, state, error, measured)


async def install_loader(tag, sha):
    import subprocess

    L, new, bak = ROOT.resolve(), sib(".new"), sib(".bak")
    if bak_busy():
        return bak_busy()
    if os.path.lexists(new) and not loader_stage_owned(new):
        return {"error": "Framey staging folder is occupied; existing files were kept"}
    u = upd()
    job = u._job("_loader", use("fy_updates").STAGES) if u else None
    await loader_step(job, "check", "ok")
    try:
        if sha == loader_sha():
            return {"current": True}
        await loader_step(job, "download", "running")
        data = await asyncio.to_thread(archive, *gh(LOADER)[:2], sha, job.progress if job else None)
    except Exception as e:
        await loader_step(job, "download", "fail", why(e))
        return {"error": why(e)}
    await loader_step(job, "download", "ok", job.measured() if job else None)
    await loader_step(job, "verify", "running")
    v = vet(data, True)
    if isinstance(v, dict):
        await loader_step(job, "verify", "fail", v.get("error"))
        return v
    await loader_step(job, "verify", "ok")
    z, infos, base = v[:3]

    def fill(stage):
        if base + ".framey-stage" in infos:
            raise ValueError("package uses a reserved staging marker")
        unpack(z, infos, base)(stage)
        for k in ("plugins", "store"):
            rmtree(stage / k)
            if (stage / k).exists():
                raise ValueError("unexpected package layout")
        (stage / ".version").write_text(sha)
        src = (stage / "framey.py").read_text()
        compile(src, "framey.py", "exec")
        for f in needs(src):
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}(?:/[A-Za-z0-9_.-]{1,64})*", f) or any(x in (".", "..") for x in f.split("/")) or not (stage / f).is_file():
                raise ValueError("the package has no " + f)
            if f.endswith(".py"):
                compile((stage / f).read_text(), f, "exec")
        try:
            out = subprocess.run([sys.executable, "-I", "-B", "-c", SMOKE, str(stage / "framey.py")], stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=True)
        except (subprocess.SubprocessError, OSError):
            out = None
        if not (out and out.stdout.rstrip().endswith(b"framey-smoke-ok")):
            raise ValueError("the new loader does not start")

    if loader_stage_owned(new):
        rmtree(new)
    new.mkdir(mode=0o700)
    (new / ".framey-stage").write_bytes(b"framey-update-stage-v1\n")
    at = "unpack"
    try:
        await loader_step(job, "unpack", "running")
        await asyncio.to_thread(fill, new)
        await sync_disk()
        await loader_step(job, "unpack", "ok")
        at = "swap"
        await loader_step(job, "swap", "running")
        L.rename(bak)
        try:
            new.rename(L)
            for k in ("plugins", "store"):
                if (bak / k).exists() and not (L / k).exists():
                    (bak / k).rename(L / k)
            (L / "plugins").mkdir(exist_ok=True)
        except Exception:
            back(L, bak)
            raise
        await loader_step(job, "swap", "ok")
    except Exception as e:
        await loader_step(job, at, "fail", str(e) or type(e).__name__)
        return {"error": "Framey update not applied: " + (str(e) or type(e).__name__)}
    finally:
        if loader_stage_owned(new):
            rmtree(new)
    await loader_step(job, "reload", "ok")
    return {"ok": "framey", "tag": tag, "restart": True}


loader_install = install_loader


async def loader_update(arg):
    candidate = arg.get("candidate") if isinstance(arg, dict) else None
    if candidate is not None and (not isinstance(candidate, dict) or not isinstance(candidate.get("tag"), str) or not isinstance(candidate.get("commit"), str) or not SHA.fullmatch(candidate["commit"])):
        return {"error": "Invalid Framey release selection; check for updates again"}
    g, u = gh(LOADER), upd()
    if bak_busy():
        return bak_busy()
    try:
        if u:
            c = await u.loader_candidate()
            if c.get("error"):
                return {"error": c["error"]}
            bad = u.loader_allowed(c, isinstance(arg, dict) and arg.get("confirm") is True)
            if bad:
                return bad
            tag, sha = c["tag"], c["commit"]
        else:
            tag = (await asyncio.to_thread(release, g[0], g[1]))["tag"]
            sha = await asyncio.to_thread(latest, g[0], g[1], tag)
    except Exception as e:
        return {"error": why(e)}
    if candidate is not None and (candidate["tag"] != tag or candidate["commit"] != sha):
        return {"error": "Framey release changed; check for updates again"}
    return await install_loader(tag, sha)


async def loader_done(_):
    bak = sib(".bak")
    if not bak.is_dir():
        return {"error": "nothing to finish"}
    if any(os.path.lexists(bak / name) for name in ("plugins", "store")):
        return {"error": "your plugins or store are still in the backup folder; not finishing"}
    rmtree(bak)
    return {"ok": True}


async def loader_roll(_):
    bak = sib(".bak")
    if not bak.is_dir():
        return {"error": "nothing to roll back"}
    try:
        back(ROOT.resolve(), bak)
    except OSError as e:
        return {"error": "roll back failed: " + (str(e) or "error")}
    return {"ok": "framey", "restart": True}


async def report(arg):
    if isinstance(arg, dict) and ok_id(arg.get("id")) and arg.get("what") in ("load", "render") and isinstance(arg.get("msg"), str):
        mark(arg["id"], arg["what"], arg["msg"])
    return None


JOBS = {"check": do_check, "update": do_update, "finish": do_finish, "retry": do_retry, "rollback": do_rollback, "loader_update": loader_update, "loader_done": loader_done, "loader_roll": loader_roll}


def restart():
    import subprocess

    subprocess.Popen(["systemctl", "--user", "restart", "framey.service"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


async def delegate(method, arg):
    name = OWNER[method]
    if name == "fy_dev" and not dev_on():
        return DEV_OFF.get(method) or {"error": "Developer mode is off", "code": "dev_off"}
    mod = use(name)
    if not mod:
        return {"error": name + " unavailable"}
    if method == "prefs_set" and isinstance(arg, dict) and any(k in PAGE_FORBIDDEN or str(k).startswith("dev.") for k in arg):
        return {"error": "That setting is changed in a file in ~/.config/framey, not from the page", "code": "not_allowed"}
    if name == "fy_diag":
        media = MODS.get("fy_media")
        host.media = media.get() if media else None
    r = await (upd().METHODS if name == "fy_updates" else mod.METHODS)[method](host, arg)
    if name == "fy_diag" and isinstance(r, dict) and "error" not in r:
        r["untrusted_clicks"] = CLICKS["untrusted"]
    if isinstance(r, dict) and "error" not in r:
        if name == "fy_state" and isinstance(r.get("prefs"), dict):
            r["prefs"] = overlay(r["prefs"])
            r["safe"] = r["prefs"]["safe"]
        await settled(method, arg)
    return r


async def resync():
    if not dev_on() and MODS.get("fy_dev"):
        MODS["fy_dev"].shutdown(host)
    sync_watches()


async def settled(method, arg):
    if method == "prefs_set" and isinstance(arg, dict):
        if "autocheck" in arg and upd():
            upd().arm_autocheck() if arg["autocheck"] is True else upd().disarm()
    if method in ("prefs_set", "target_add", "target_remove"):
        sync_watches()


async def core(method, arg):
    global LOCK
    if method in JOBS or method in LOCKED or method in EDIT:
        LOCK = LOCK or asyncio.Lock()
        if LOCK.locked():
            return BUSY
        async with LOCK:
            return await run(method, arg)
    return await run(method, arg)


async def run(method, arg):
    if method in JOBS:
        return await JOBS[method](arg)
    if method in OWNER:
        return await delegate(method, arg)
    if method == "toggle":
        t = mono()
        if len(TOGGLES) == 5 and t - TOGGLES[0] < 1:
            return None
        TOGGLES.append(t)
        if "main" in LIVE:
            await LIVE["main"]("Runtime.evaluate", expression="Framey.toggle()")
        return None
    if method == "panel":
        a = arg if isinstance(arg, dict) else {"open": arg is True}
        if type(a.get("untrusted_clicks")) is int:
            CLICKS["untrusted"] = a["untrusted_clicks"]
        await panel(a.get("open") is True)
        return None
    if method == "fail":
        return await report(arg)
    if method == "reload_one":
        return await reload_one(arg)
    if method == "list":
        off = settings()["disabled"]
        return [{**m, "enabled": m["id"] not in off, "linked": (PLUGINS / m["id"]).is_symlink(), **info(m)} for m in manifests()]
    if method == "loader":
        return {"commit": loader_sha()[:7], "kept": sib(".bak").is_dir()}
    if method == "store":
        return catalog()
    if method == "asset":
        return {"ok": True}
    if method == "status":
        return {"settings_state": settings_state()}
    if method == "settings_repair":
        st = use("fy_state")
        if not st:
            return {"ok": False, "error": "settings module unavailable"}
        r = st.repair(CONF)
        if r["ok"]:
            await reload()
        return r
    if method in ("locale", "log"):
        a, st = arg if isinstance(arg, dict) else {}, use("fy_store")
        pid = a.get("id" if method == "locale" else "pid")
        if not st:
            return {"error": "fy_store unavailable"}
        if not (ok_id(pid) and manifest(PLUGINS / pid)):
            return {"error": "unknown plugin"}
        if method == "locale":
            return {"strings": st.read_locale(PLUGINS / pid, a.get("lang"))}
        lg = LOGS.get(pid) or LOGS.setdefault(pid, st.Log(pid, dirs(pid).logs))
        getattr(lg, a.get("level") if a.get("level") in ("debug", "info", "warning", "error") else "info")(str(a.get("msg"))[:2048])
        return None
    if method == "install":
        return await install(arg)
    if method == "add":
        if not (ok_id(arg) and (STORE / arg).is_dir() and manifest(STORE / arg)):
            return {"error": "not in store"}
        if gate(manifest(STORE / arg)):
            return {"error": gate(manifest(STORE / arg))}
        if (PLUGINS / arg).is_symlink():
            return {"error": "that plugin is linked, remove the link first"}
        if pending(arg):
            return {"error": "finish or roll back the pending update first"}
        try:
            await asyncio.to_thread(stage_swap, arg, lambda stage: tree_copy(STORE / arg, stage))
        except Exception as e:
            return {"error": str(e) or "install failed"}
        await reload(arg)
        return {"ok": arg}
    reason = "reload"
    if method in ("enable", "disable"):
        if not ok_id(arg) or (method == "disable" and not manifest(PLUGINS / arg)):
            return {"error": "unknown plugin"}
        s = settings()
        off = set(s["disabled"])
        off.discard(arg) if method == "enable" else off.add(arg)
        s["disabled"] = sorted(off)
        save(s)
        reason = method
    elif method == "uninstall":
        pid, wipe = (arg.get("id"), arg.get("purge") is True) if isinstance(arg, dict) else (arg, False)
        if not ok_id(pid):
            return {"error": "unknown plugin"}
        if supervisor:
            await supervisor.uninstall(pid, wipe, do_purge=False)
        store = use("fy_store")
        if store:
            store.discard_held(PLUGINS, pid)
        p = PLUGINS / pid
        if p.is_symlink():
            p.unlink()
        else:
            rmtree(p)
        if os.path.lexists(p):
            return {"error": "could not remove " + pid}
        left = purge(pid)["failed"] if wipe else []
        forget(pid, not wipe)
        arg, reason = pid, "uninstall"
    elif method != "reload":
        return {"error": "unknown"}
    FAILS.pop(arg, None) if method != "reload" else FAILS.clear()
    await reload(arg if method != "reload" else None, reason)
    if method == "reload":
        await resync()
    return {"ok": True, "purge_failed": left} if method == "uninstall" and left else {"ok": True}


class WS:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer

    @classmethod
    async def open(cls, url):
        import base64

        if not cdp_owner_ok():
            raise OSError("the port on 127.0.0.1:8080 is not owned by this user")
        r, w = await asyncio.wait_for(asyncio.open_connection(*CDP), 5)
        key = base64.b64encode(os.urandom(16)).decode()
        try:
            w.write(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (urllib.parse.urlsplit(url).path, *CDP, key)).encode())
            head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
            if b" 101 " not in head.split(b"\r\n")[0]:
                raise ConnectionError("handshake refused")
        except BaseException:
            w.close()
            raise
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
                if len(msg) + n > MAX_MSG:
                    return None
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


def cdp_owner_ok(path="/proc/net/tcp"):
    if not sys.platform.startswith("linux"):
        return True
    try:
        with open(path) as f:
            rows = [r.split() for r in f.readlines()[1:]]
    except OSError:
        return False
    owners = [int(r[7]) for r in rows if len(r) > 7 and r[1] == "0100007F:1F90" and r[3] == "0A"]
    return bool(owners) and all(u == os.getuid() for u in owners)


async def tabs():
    if not cdp_owner_ok():
        raise OSError("the port on 127.0.0.1:8080 is not owned by this user")
    r, w = await asyncio.wait_for(asyncio.open_connection(*CDP), 3)
    try:
        w.write(("GET /json HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % CDP).encode())
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 3)
        body = await asyncio.wait_for(r.readexactly(int(re.search(rb"Content-Length:\s*(\d+)", head, re.I)[1])), 3)
    finally:
        w.close()
    return json.loads(body)


async def session(name, tab, code, probe_expr, marker=None):
    ws = await WS.open(tab["webSocketDebuggerUrl"])
    ids = iter(range(1, 1 << 30))
    pending = {}
    waiters = {}
    probe = missed = 0
    probe_world = None
    load_epoch = 0
    held = False
    todo = []
    iso = boot = mod = mode = None
    object_group = "framey-" + os.urandom(8).hex()

    async def release(*objects):
        for oid in set(x for x in objects if isinstance(x, str)):
            try:
                await send("Runtime.releaseObject", objectId=oid)
            except Exception:
                pass

    async def matches():
        if marker is None:
            return True
        current = next((x for x in await tabs() if (x.get("id") == tab["id"] if isinstance(tab.get("id"), str) else x.get("webSocketDebuggerUrl") == tab.get("webSocketDebuggerUrl"))), None)
        return current is not None and marker(current.get("url", ""))

    async def send(method, _pid=None, _main=False, **p):
        nonlocal load_epoch
        if method in ("Runtime.evaluate", "Runtime.callFunctionOn"):
            if LIVE.get(name) is not send:
                raise ConnectionError("debugger session was replaced")
            if (name not in WIPES or _main or _pid == "#core" or isinstance(_pid, tuple)) and not await matches():
                ws.close()
                raise ConnectionError("debugger target no longer matches")
        if p.get("expression") in WIPES.values():
            load_epoch += 1
            await call_core(send, DISPOSE, [None])
            if getattr(send, "core", None):
                await send("Runtime.releaseObjectGroup", objectGroup=object_group)
            send.core = None
        if method in ("Runtime.evaluate", "Runtime.callFunctionOn") and (_pid == "#core" or isinstance(_pid, tuple)):
            p.setdefault("objectGroup", object_group)
        if iso and iso.on and method == "Runtime.evaluate" and not _main:
            if iso.ctx is None:
                return 0
            p["contextId"] = iso.ctx
        i = next(ids)
        if _pid:
            world = (iso.ctx, getattr(iso, "epoch", None)) if iso and iso.on else None
            pending[i] = (_pid, load_epoch, world)
        await ws.send({"id": i, "method": method, "params": p})
        return i

    async def rpc(method, **p):
        i = next(ids)
        waiters[i] = asyncio.get_running_loop().create_future()
        try:
            await ws.send({"id": i, "method": method, "params": p})
            m = await asyncio.wait_for(waiters[i], mod.STEP)
        finally:
            waiters.pop(i, None)
        if "error" in m:
            raise RuntimeError((m["error"] or {}).get("message") or "cdp error")
        return m.get("result") or {}

    async def ping():
        nonlocal probe, probe_world
        if held:
            return
        if not await matches():
            ws.close()
            return
        if iso and iso.on and iso.ctx is None and not await iso.renew():
            if iso.dead:
                await fallback("the isolated world could not be renewed")
            return
        probe_world = (iso.ctx, getattr(iso, "epoch", None)) if iso and iso.on else None
        probe = await send("Runtime.evaluate", expression=probe_expr, returnByValue=True)

    async def legacy():
        if not await matches():
            ws.close()
            return
        await send("Runtime.addBinding", name="fyCall")
        if name in WIPES:
            await send("Runtime.evaluate", expression=WIPES[name])
        else:
            target = getattr(host, "targets", None)
            if target and hasattr(target, "reconcile"):
                await target.reconcile(name)
            else:
                for expression, pid in code():
                    await send("Runtime.evaluate", _pid=pid, expression=expression)
        if name == "main" and supervisor:
            supervisor.events.clear()
        await ping()

    async def fallback(why):
        nonlocal held
        if iso.on:
            if mode is True:
                log(name, "isolation true: page not injected:", why)
                held = True
                ws.close()
                return
            log(name, "isolation off, using the page context:", why)
            await iso.stop(WIPES[name])
            await legacy()

    async def setup():
        nonlocal held
        if not await matches():
            held = True
            ws.close()
            return
        if iso and await iso.start():
            await send("Runtime.evaluate", expression=WIPES[name], _main=True)
            await send("Runtime.evaluate", expression=WIPES[name])
            if name == "main" and supervisor:
                supervisor.events.clear()
            await ping()
        elif mode is True or iso and iso.held:
            if mode is True:
                log(name, "isolation true: page not injected")
            held = True
            ws.close()
        else:
            await legacy()

    async def fail_load(pid, d):
        msg = ((d.get("exception", {}).get("description") or d.get("text") or "error") if d else "not loaded").split("\n")[0]
        log("plugin", pid, "failed:", msg)
        mark(pid, "load", msg)
        await send("Runtime.evaluate", expression="Framey.fail(%s,{message:%s})" % (json.dumps(pid), json.dumps(msg)))

    async def plug(m, tok, src, pid, redraw=True):
        await send("Runtime.evaluate", _pid=("#src", pid, tok, m, redraw), expression="(function(Framey){" + src + "\n})")

    async def step():
        while todo:
            if todo[0][0] == "Framey.restore()":
                world = (iso.ctx, getattr(iso, "epoch", None)) if iso and iso.on else None
                if any(isinstance(p[0], tuple) and p[0][0] in ("#src", "#bind", "#run") and p[1] == load_epoch and p[2] == world for p in pending.values()):
                    return
            expr, pid = todo.pop(0)
            if isinstance(expr, tuple):
                await plug(*expr, pid)
                continue
            await send("Runtime.evaluate", _pid=pid, expression=expr, _main=isinstance(expr, Main))
            if pid == "#core":
                return
        target = getattr(host, "targets", None)
        if name in WIPES and target and hasattr(target, "reconcile"):
            await target.reconcile(name)

    async def registered(generation, world, who=None):
        current = (iso.ctx, getattr(iso, "epoch", None)) if iso and iso.on else None
        if generation != load_epoch or world != current:
            return
        if todo:
            if todo[0][0] == "Framey.restore()":
                await step()
        elif who:
            await send("Runtime.evaluate", expression="window.Framey&&Framey.redraw(%s)" % json.dumps(who))

    send.ping = ping
    send.main = lambda expression: send("Runtime.evaluate", expression=expression, _main=True)
    send.plugin = plug
    send.core = None
    LIVE[name] = send

    async def handle(req, full):
        arg = None
        try:
            if full:
                raise RuntimeError("busy")
            if req.get("plugin") == "_core":
                allowed, arg = authorize(req)
                out = await core(req.get("method"), arg) if allowed else NOAUTH
            else:
                out = await backend(req.get("plugin"), req.get("method"), req.get("arg")) if backend_ok(req) else NOAUTH
            json.dumps(out)
        except Exception as e:
            out = {"error": str(e) or "failed"}
        i, gen = req.get("id"), req.get("gen")
        if i and isinstance(i, int) and isinstance(gen, str):
            try:
                await call_core(send, REPLY, [gen, i, out])
            except Exception as e:
                log("reply failed:", str(e)[:200])
        if req.get("plugin") == "_core" and isinstance(out, dict):
            method = req.get("method")
            if out.get("restart"):
                await asyncio.sleep(1)
                try:
                    restart()
                except OSError as e:
                    log("restart failed:", e)
            elif method in RELOAD and ("ok" in out or "staged" in out):
                await refresh()
            elif method in UPDATED:
                await after_update(method, arg, out)

    async def ticker():
        nonlocal missed
        while True:
            await asyncio.sleep(5)
            missed += 1
            if missed > 6:
                ws.close()
                return
            await ping()

    t = None
    try:
        mode = name in WIPES and isolation()
        mod = mode and use("fy_iso")
        iso = mod.Iso(rpc, log) if mod else None
        if iso:
            boot = spawn(setup())
        else:
            await setup()
        t = asyncio.create_task(ticker())
        while (raw := await ws.recv()) is not None:
            if iso and iso.on and not mod.wanted(raw):
                continue
            m = parse(raw)
            if m.get("id") in waiters:
                waiters[m["id"]].set_result(m)
                continue
            if iso and iso.on:
                iso.event(m)
            if m.get("id") == probe:
                if probe_world is not None and iso and iso.on and probe_world != (iso.ctx, getattr(iso, "epoch", None)):
                    continue
                missed = 0
                v = m.get("result", {}).get("result", {}).get("value")
                if iso and iso.on:
                    if "error" in m:
                        iso.ctx = None
                        if hasattr(iso, "epoch"):
                            iso.epoch += 1
                        continue
                    act = iso.seen(v)
                    if act == "fallback":
                        spawn(fallback("the Framey handle was not found in the isolated world"))
                        continue
                    v = "undefined" if act == "inject" else None
                if v == "undefined" and not stopping:
                    target = getattr(host, "targets", None)
                    if name not in WIPES and target and hasattr(target, "reconcile"):
                        await target.reconcile(name)
                    else:
                        load_epoch += 1
                        todo[:] = [(expression, pid) for expression, pid in code() if name not in WIPES or not isinstance(expression, Main)]
                        send.core = None
                        await step()
            elif m.get("id") in pending:
                pid, generation, world = pending.pop(m["id"])
                res = m.get("result") or {}
                current_world = (iso.ctx, getattr(iso, "epoch", None)) if iso and iso.on else None
                if generation != load_epoch or world != current_world:
                    await release((res.get("result") or {}).get("objectId"), *(pid[2:] if isinstance(pid, tuple) and pid[0] in ("#bind", "#run") else []))
                    continue
                if pid == "#core":
                    send.core = (res.get("result") or {}).get("objectId")
                    if send.core:
                        await step()
                    else:
                        todo.clear()
                    continue
                if isinstance(pid, tuple):
                    stage, who, *rest = pid
                    d = res.get("exceptionDetails")
                    oid = (res.get("result") or {}).get("objectId")
                    if d or not send.core or (stage != "#run" and not oid):
                        await fail_load(who, d)
                        await release(oid, *(rest if stage in ("#bind", "#run") else []))
                        await registered(generation, world)
                    elif stage == "#src":
                        tok, mf, redraw = rest
                        await send("Runtime.callFunctionOn", _pid=("#bind", who, oid, redraw), objectId=send.core, functionDeclaration=BIND, arguments=[{"value": mf["id"]}, {"value": tok}, {"value": mf}])
                    elif stage == "#bind":
                        await send("Runtime.callFunctionOn", _pid=("#run", who, rest[0], oid, rest[1]), objectId=rest[0], functionDeclaration=RUN, arguments=[{"objectId": oid}])
                    elif stage == "#run":
                        await release(*rest, oid)
                        await registered(generation, world, who if rest[2] else None)
                    continue
                d = res.get("exceptionDetails")
                if d:
                    await fail_load(pid, d)
            elif m.get("method") == "Runtime.bindingCalled":
                p = m.get("params") or {}
                if p.get("name") != "fyCall":
                    continue
                if iso and iso.on and (iso.ctx is None or type(p.get("executionContextId")) is not int or p.get("executionContextId") != iso.ctx):
                    continue
                payload = p.get("payload")
                if isinstance(payload, str) and len(payload) > PAYLOAD_IN:
                    head = re.match(r'\{\s*"gen"\s*:\s*("(?:[^"\\]|\\.){1,200}")\s*,\s*"id"\s*:\s*(\d{1,12})', payload[:512])
                    if head:
                        await call_core(send, REPLY, [json.loads(head[1]), int(head[2]), {"error": "too large"}])
                    continue
                req = request(payload)
                if req:
                    task = asyncio.create_task(handle(req, len(TASKS) >= 32))
                    TASKS.add(task)
                    task.add_done_callback(TASKS.discard)
    finally:
        if t:
            t.cancel()
        if boot:
            boot.cancel()
        owns_session = LIVE.get(name) is send
        if owns_session:
            LIVE.pop(name, None)
        try:
            await asyncio.wait_for(send("Runtime.releaseObjectGroup", objectGroup=object_group), 1)
        except Exception:
            pass
        ws.close()
        if owns_session and name == "main" and not stopping:
            await panel(False)


async def watch(name, marker, code, probe_expr):
    last = None
    while True:
        try:
            tab = next((t for t in await tabs() if marker(t["url"])), None)
            if tab:
                await session(name, tab, code, probe_expr, marker)
        except Exception as e:
            if repr(e) != last:
                last = repr(e)
                log(name, "attach failed:", last)
        await asyncio.sleep(5)


async def down():
    t, media = getattr(host, "targets", None), MODS.get("fy_media")
    if updates:
        updates.disarm()
    if MODS.get("fy_dev"):
        MODS["fy_dev"].shutdown(host)
    steps = [x for x in (t and t.remove_all(), supervisor and supervisor.shutdown(1.5), media and media.close()) if x]
    await asyncio.wait_for(asyncio.gather(*steps, return_exceptions=True), 6)


def start_optional():
    if pref("autocheck") is True and upd():
        upd().arm_autocheck()
    if dev_on() and use("fy_dev"):
        MODS["fy_dev"].get(host).resume()


async def main():
    import signal

    global stopping
    for step in (recover, load_backends, start_optional):
        try:
            step()
        except Exception as e:
            log("startup", step.__name__, "failed:", e)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    work = asyncio.gather(
        watch("main", lambda u: overlay_key(u, "valve.steam.gamepadui.main"), main_code, "typeof window.Framey"),
        watch("bar", lambda u: overlay_key(u, "valve.steam.gamepadui.bar"), bar_code, "typeof window.FYBar"),
    )
    try:
        await stop.wait()
    finally:
        stopping = True
        await asyncio.gather(*(asyncio.wait_for(send("Runtime.evaluate", expression=WIPES[name]), 2) for name, send in list(LIVE.items()) if name in WIPES), return_exceptions=True)
        try:
            await down()
        except Exception as e:
            log("shutdown failed:", str(e)[:200])
        tasks = list(TASKS) + list(BG)
        for task in tasks:
            task.cancel()
        work.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(work, *tasks, return_exceptions=True), 3)
        except asyncio.TimeoutError:
            log("shutdown task cleanup timed out")



def main_entry():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
    loop.close()
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    os._exit(0)


if __name__ == "__main__":
    main_entry()
