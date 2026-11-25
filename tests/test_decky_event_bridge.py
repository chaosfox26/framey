"""Execute only private current Framey copies and synthetic producers. No plugin imports."""

from pathlib import Path
import asyncio
import importlib
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
fy_sdk = importlib.import_module("fy_sdk")
fy_proto = importlib.import_module("fy_proto")
fy_store = importlib.import_module("fy_store")
fy_supervisor = importlib.import_module("fy_supervisor")
backend = importlib.import_module("decky_runtime.compat.decky_backend")

ID = "12345678-1234-4234-8234-123456789abc"
NAME = "terminal_output#" + ID
WIRE = "decky.compat.event"


class Transport:
    def __init__(self):
        self.dropped = 0
        self.pending = 0
        self.packets = []
        self.hub = fy_supervisor.EventHub(lambda: None, clock=lambda: 1.0)

    def event_dropped(self, n=1):
        self.dropped += n
        self.pending += n

    def emit(self, name, payload):
        if not fy_proto.event_ok(name, payload):
            self.event_dropped()
            return False
        # Exact Framey encode/decode, EventHub owner admission, and take shape.
        msg = fy_proto.decode(
            fy_proto.encode(
                {"ev": "emit", "name": name, "payload": payload, "dropped": self.pending}
            )
        )
        self.pending = 0
        accepted = self.hub.put("terminal65", msg["name"], msg["payload"], msg["dropped"])
        self.packets.extend(self.hub.take(100))
        return accepted


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framey-event-bridge-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.transport = Transport()
        self.api = fy_sdk.build(
            "terminal65",
            {"name": "Synthetic Terminal"},
            fy_store.dirs(
                "terminal65", home=self.root / "home", runtime_base=self.root / "run"
            )._asdict(),
            {},
            self.transport,
            max_events=50,
        )
        self.adapter = backend.DeckyBackend.__new__(backend.DeckyBackend)
        self.adapter.emit = self.api.events.emit

    def emit(self, name, *args):
        return asyncio.run(self.adapter._emit(name, *args))

    def test_terminal_family_crosses_real_sdk_json_and_hub(self):
        self.assertTrue(self.emit(NAME, "synthetic stdout\n"))
        packet = self.transport.packets[-1]
        self.assertEqual(packet["plugin"], "terminal65")
        self.assertEqual(packet["name"], WIRE)
        self.assertEqual(packet["payload"], {"v": 1, "name": NAME, "args": ["synthetic stdout\n"]})

    def test_raw_sdk_and_protocol_admission_remain_strict(self):
        for name in [NAME, "a" * 41, "stateUpdate", "bad#id", "line\n", "x\x00y", "é", False, None]:
            self.assertFalse(self.api.events.emit(name, "text"))
            self.assertFalse(fy_proto.event_ok(name, "text"))
        self.assertEqual(len(self.transport.packets), 0)
        self.assertEqual(self.transport.dropped, 9)

    def test_existing_strict_name_is_not_rewritten(self):
        self.assertTrue(self.emit("server_event", {"type": "update_status", "data": "synthetic"}))
        self.assertEqual(self.transport.packets[-1]["name"], "server_event")
        self.assertEqual(
            self.transport.packets[-1]["payload"], [{"type": "update_status", "data": "synthetic"}]
        )

    def test_noncanonical_ids_and_arbitrary_names_stay_rejected(self):
        for name in [
            NAME + "\n",
            NAME + "x",
            NAME.upper(),
            "terminal_output#1",
            "terminal_output#" + ID.replace("-4234-", "-1234-"),
            "terminal_output#" + ID.replace("-8234-", "-7234-"),
            "terminal_output#" + "a" * 36,
        ]:
            self.assertFalse(self.emit(name, "x"))
        self.assertEqual(self.transport.dropped, 7)
        self.assertFalse(self.transport.packets)

    def test_full_envelope_size_budget_and_drop_report(self):
        overhead = len(fy_proto.dumps({"v": 1, "name": NAME, "args": [""]}))
        size = fy_proto.EVENT_MAX - overhead
        self.assertTrue(self.emit(NAME, "x" * size))
        self.assertFalse(self.emit(NAME, "x" * (size + 1)))
        self.assertTrue(self.emit(NAME, "after drop"))
        self.assertEqual(self.transport.packets[-1]["dropped"], 1)
        self.assertEqual(self.transport.dropped, 1)

    def test_sdk_rate_cap_unchanged_and_nonfinite_payload_drops(self):
        self.api = fy_sdk.build(
            "terminal65",
            {},
            fy_store.dirs(
                "terminal65", home=self.root / "other", runtime_base=self.root / "other-run"
            )._asdict(),
            {},
            self.transport,
            max_events=2,
        )
        self.adapter.emit = self.api.events.emit
        self.assertFalse(self.emit(NAME, float("nan")))
        self.assertTrue(self.emit(NAME, "one"))
        self.assertTrue(self.emit(NAME, "two"))
        self.assertFalse(self.emit(NAME, "three"))
        self.assertEqual(self.transport.dropped, 2)

    def test_two_plugin_queues_keep_owner_identity(self):
        payload = {"v": 1, "name": NAME, "args": ["owner A"]}
        self.assertTrue(self.transport.hub.put("a", WIRE, payload))
        self.assertTrue(
            self.transport.hub.put("b", WIRE, {"v": 1, "name": NAME, "args": ["owner B"]})
        )
        self.assertEqual([p["plugin"] for p in self.transport.hub.take(10)], ["a", "b"])


def synthetic_packets():
    """Actual SDK/protocol/EventHub path; no worker, PTY or vendor producer starts."""
    with tempfile.TemporaryDirectory(prefix="framey-event-packets-") as tmp:
        transport = Transport()
        root = Path(tmp)
        sdk = fy_sdk.build(
            "terminal65",
            {},
            fy_store.dirs("terminal65", home=root / "home", runtime_base=root / "run")._asdict(),
            {},
            transport,
        )
        adapter = backend.DeckyBackend.__new__(backend.DeckyBackend)
        adapter.emit = sdk.events.emit
        accepted = asyncio.run(adapter._emit(NAME, "synthetic A"))
        # Baseline-only fallback isolates frontend admission as well as backend failure.
        # Candidate packets always come from the actual adapter, with no fallback.
        if not accepted:
            sdk.events.emit(WIRE, {"v": 1, "name": NAME, "args": ["synthetic A"]})
        asyncio.run(
            adapter._emit(
                "terminal_output#87654321-4321-4321-9321-cba987654321", "synthetic other session"
            )
        )
        asyncio.run(adapter._emit("server_event", "existing"))
        return {"adapter_accepted": bool(accepted), "packets": transport.packets}


if __name__ == "__main__":
    if "--packets" in sys.argv:
        print(json.dumps(synthetic_packets()))
    else:
        unittest.main()
