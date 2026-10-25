import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import fy_proto


def make_root():
    root = Path(tempfile.mkdtemp(prefix="fya-"))
    for name in ("fy_worker.py", "fy_proto.py"):
        shutil.copy(REPO / name, root / name)
    shutil.copy(HERE / "fy_sdk_stub.py", root / "fy_sdk.py")
    return root


def make_plugin(root, pid, fixture, manifest=None):
    d = root / "plugins" / pid
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / (fixture + ".py"), d / "backend.py")
    m = {"id": pid, "name": pid, "version": "1.0.0"}
    m.update(manifest or {})
    (d / "plugin.json").write_text(json.dumps(m))
    return d, m


class FakeHost:
    def __init__(self, root):
        self.ROOT = root
        self.HOME = root / "home"
        self.VERSION = "1.1.0"
        self.PLATFORM = "linux-aarch64"
        self.API_GENERATION = 1
        self.clock = 1000.0
        self.logged = []
        self.states = {}
        self.hostcalls = {}
        self.prefs = {}
        self.snapshots = []
        self.dropped = []
        self.restored = []
        self.purged = []
        self.ready_signals = 0

    def log(self, *a):
        self.logged.append(" ".join(str(x) for x in a))

    def now(self):
        return self.clock

    def mono(self):
        return self.clock

    def loop(self):
        return asyncio.get_running_loop()

    def settings(self):
        return {"prefs": dict(self.prefs)}

    def manifest(self, d):
        try:
            return json.loads((Path(d) / "plugin.json").read_text())
        except (OSError, ValueError):
            return None

    def module_state(self, pid):
        return dict(self.states.get(pid, {}))

    def set_module_state(self, pid, rec):
        self.states[pid] = dict(rec)

    def snapshot_settings(self, pid):
        self.snapshots.append(pid)

    def drop_snapshot(self, pid):
        self.dropped.append(pid)

    def restore_snapshot(self, pid):
        self.restored.append(pid)

    def purge(self, pid):
        self.purged.append(pid)
        return {"removed": ["data"], "failed": []}

    def events_ready(self):
        self.ready_signals += 1


class Wire:
    def __init__(self, root, timeout=15):
        self.proc = subprocess.Popen([sys.executable, "-I", str(root / "fy_worker.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(root))
        self.timer = None
        self.err = b""

    def send(self, obj):
        self.proc.stdin.write(fy_proto.encode(obj))
        self.proc.stdin.flush()

    def send_raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def recv(self, limit=15):
        import threading
        t = threading.Timer(limit, self.proc.kill)
        t.start()
        try:
            line = self.proc.stdout.readline()
        finally:
            t.cancel()
        return fy_proto.decode(line) if line else None

    def request(self, obj):
        self.send(obj)
        while True:
            msg = self.recv()
            if msg is None or msg.get("re") == obj["id"]:
                return msg

    def close(self):
        try:
            self.proc.kill()
        except OSError:
            pass
        self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


def startup_message(rid, plugin, file, cwd, **extra):
    base = {"op": "startup", "id": rid, "plugin": plugin, "file": str(file), "dirs": {k: str(Path(cwd) / k) for k in ("config", "data", "runtime", "logs")}, "meta": {"id": plugin, "version": "1.0.0"}, "runtime": {"framey": "1.1.0", "api": 1, "platform": "linux-aarch64", "python": "3.11.2"}, "limits": {"startup_timeout": 5, "timeout": 5, "concurrency": 1, "memory_mb": 64}}
    base.update(extra)
    return base
