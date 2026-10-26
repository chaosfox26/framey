import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "a"))

import fy_pkg
import harness

EXAMPLE = ROOT / "examples" / "decky-style"


class AuthoredSourcePortRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-authored-sourceport-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("fy_worker.py", "fy_proto.py", "fy_sdk.py", "fy_store.py"):
            shutil.copy(ROOT / name, self.root / name)
        self.plugin = self.root / "plugins" / "decky-style"
        self.plugin.mkdir(parents=True)
        for name in ("plugin.json", "main.js", "backend.py"):
            shutil.copy(EXAMPLE / name, self.plugin / name)
        self.manifest = json.loads((self.plugin / "plugin.json").read_text())
        self.ids = iter(range(1, 100))

    def request(self, wire, **message):
        rid = next(self.ids)
        wire.send(dict(message, id=rid))
        events = []
        while True:
            reply = wire.recv(limit=3)
            self.assertIsNotNone(reply, "authored source-port worker ended before replying")
            if reply.get("re") == rid:
                return reply, events
            self.assertEqual(reply.get("ev"), "emit", reply)
            events.append(reply)

    def boot(self):
        policy = fy_pkg.decide(self.manifest, "1.1.0", fy_pkg.PLATFORM)
        if policy["state"] != "ok":
            self.skipTest("source-port example does not declare this test host")
        wire = harness.Wire(self.root)
        self.addCleanup(wire.close)
        message = harness.startup_message(0, "decky-style", self.plugin / "backend.py", self.root,
                                          meta=self.manifest, limits=self.manifest["backend"])
        message["runtime"]["platform"] = fy_pkg.PLATFORM
        message.pop("id")
        reply, events = self.request(wire, **message)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(set(reply["result"]["hooks"]), {"call", "startup", "unload"})
        self.assertEqual(events, [])
        return wire

    def call(self, wire, method, args):
        reply, events = self.request(wire, op="call", method=method, arg=args)
        self.assertTrue(reply["ok"], reply)
        return reply["result"], events

    def unload(self, wire, reason):
        reply, events = self.request(wire, op="unload", reason=reason)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(events, [])
        self.assertEqual(wire.proc.wait(timeout=3), 0)

    def test_settings_survive_full_worker_teardown_and_fresh_sdk(self):
        first = self.boot()
        self.assertEqual(self.call(first, "save_goal", ["Read"]), ({"goal": "Read", "count": 0}, []))
        snapshot, events = self.call(first, "check_in", [])
        self.assertEqual(snapshot, {"goal": "Read", "count": 1})
        self.assertEqual([(event["name"], event["payload"]) for event in events],
                         [("check_in", [snapshot])])
        saved = json.loads((self.root / "config" / "settings.json").read_text())
        self.assertEqual(saved, snapshot)
        self.unload(first, "reload")

        second = self.boot()
        self.assertNotEqual(first.proc.pid, second.proc.pid)
        self.assertEqual(self.call(second, "read_settings", []), (saved, []))
        snapshot, events = self.call(second, "check_in", [])
        self.assertEqual(snapshot, {"goal": "Read", "count": 2})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"], [snapshot])
        self.unload(second, "disable")
        log = (self.root / "logs" / "plugin.log").read_text()
        self.assertEqual(log.count("session check-ins started"), 2)
        self.assertIn("session check-ins unloaded (reload)", log)
        self.assertIn("session check-ins unloaded (disable)", log)

    def test_foreign_call_shapes_do_not_bypass_explicit_positional_mapping(self):
        wire = self.boot()
        before, events = self.call(wire, "save_goal", ["Plan"])
        self.assertEqual(events, [])
        settings = self.root / "config" / "settings.json"
        saved_before = settings.read_bytes()
        for method, args in (("save_goal", {"goal": "Read"}),
                             ("check_in", None),
                             ("read_settings", ["unexpected"]),
                             ("_main", [])):
            with self.subTest(method=method, args=args):
                result, events = self.call(wire, method, args)
                self.assertEqual(set(result), {"error"})
                self.assertEqual(events, [])
        self.assertEqual(self.call(wire, "read_settings", []), (before, []))
        self.assertEqual(settings.read_bytes(), saved_before)
        self.unload(wire, "uninstall")


if __name__ == "__main__":
    unittest.main()
