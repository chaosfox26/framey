"""Fresh generated pairs through the real SDK and in-process Worker JSON handlers.

All plugin source is created by this test. No worker process, limits, network,
downloaded plugin or device operation is used. --host-fixtures supplies fresh
JSON receipts and generated frontend source to the matching owned JS tests.
"""

import asyncio
import hashlib
import io
import importlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from optional_backend_fixture import install_runtime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

fy_decky_import = importlib.import_module("decky_runtime.importer")
fy_proto = importlib.import_module("fy_proto")
fy_sdk = importlib.import_module("fy_sdk")
fy_store = importlib.import_module("fy_store")
fy_worker = importlib.import_module("fy_worker")

VALUES = [
    {"error": "domain", "code": "quota"},
    {"error": False, "code": 0},
    {"__framey_decky_rpc_result__": 1, "value": "domain marker"},
    {"__framey_decky_rpc_result__": 2, "value": {"error": "nested"}},
    {"success": False, "result": {"error": "domain"}},
    None,
    [],
    [None, {"error": "list item"}],
    "text",
    0,
    False,
]
FEATURE = "framey-rpc-value-v1"
FRONTEND = b"(api=>{window.ownLegacy=api;return {content:null};})"
BACKEND = b"""import asyncio
class Plugin:
    calls = 0
    async def echo(self, value):
        self.calls += 1
        return value
    def immediate(self, value):
        self.calls += 1
        return value
    async def crash(self):
        raise ValueError('own RPC fixture exception')
    async def non_json(self):
        return object()
    async def cancelled(self):
        raise asyncio.CancelledError()
"""


def own_archive(api=2, backend=True):
    files = {
        "plugin.json": json.dumps(
            {"name": "Owned RPC", "author": "Own fixture", "api_version": api, "flags": []}
        ).encode(),
        "package.json": b'{"version":"1.0.0"}',
        "dist/index.js": FRONTEND,
        "LICENSE": b"Newly owned synthetic source",
    }
    if backend:
        files["main.py"] = BACKEND
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items():
            archive.writestr("own/" + name, data)
    return stream.getvalue(), files


class OwnedPair:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.sequence = 0
        self.workers = []
        self.home = self.folder / "home"
        package = ROOT / "decky_runtime"
        payload = {path.relative_to(package).as_posix(): path.read_bytes()
                   for path in package.rglob("*") if path.is_file()
                   and "__pycache__" not in path.parts and path.name != "runtime-version.json"}
        self.runtime_descriptor = install_runtime(self.home, payload)
        self.home_context = patch.object(Path, "home", return_value=self.home)
        self.home_context.start()
        self.module_context = patch.dict(sys.modules)
        self.module_context.__enter__()

    def generate(self, api=2, backend=True):
        self.sequence += 1
        raw, files = own_archive(api, backend)
        destination = self.folder / ("pair-%d" % self.sequence)
        fy_decky_import.import_plugin(
            raw, hashlib.sha256(raw).hexdigest(), destination, "owned-rpc"
        )
        for name, data in files.items():
            if (destination / "vendor/decky" / name).read_bytes() != data:
                raise AssertionError("Importer changed owned archive source")
        return destination

    async def settle(self, worker):
        tasks = tuple(worker.tasks)
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        await asyncio.sleep(0)

    async def boot(self, api=2):
        destination = self.generate(api)
        worker = fy_worker.Worker(
            asyncio.get_running_loop(), -1, sdk_module=fy_sdk, store_module=fy_store
        )
        # Process resource-limit installation is outside an in-process test.
        # Real JSON dispatch, SDK construction and generated startup still run.
        worker.limits_configured = True
        worker.limits_reason = "not exercised by owned in-process regression"
        packets = []
        worker.write_data = lambda data: packets.append(bytes(data)) or True
        roots = {
            name: str(self.folder / (name + str(self.sequence))) for name in fy_worker.DIR_KEYS
        }
        worker.on_line(
            fy_proto.encode(
                {
                    "op": "startup",
                    "id": 1,
                    "plugin": "owned_rpc",
                    "file": str(destination / "backend.py"),
                    "dirs": roots,
                    "meta": {"name": "Owned RPC", "version": "1.0.0",
                             "decky_compat": json.loads((destination / "plugin.json").read_text())["decky_compat"]},
                    "runtime": {"decky_runtime": self.runtime_descriptor},
                }
            )
        )
        await self.settle(worker)
        self.workers.append(worker)
        reply = fy_proto.decode(packets.pop())
        if not reply["ok"]:
            raise AssertionError(reply)
        if worker.sdk.API != fy_sdk.API_GENERATION or worker.sdk.PLUGIN != "owned_rpc":
            raise AssertionError("Real SDK was not constructed for owned worker")
        return worker, destination, packets

    async def call(self, worker, packets, value=None, *, legacy=False, method="echo"):
        packets.clear()
        worker.on_line(
            fy_proto.encode(
                {
                    "op": "call",
                    "id": 2,
                    "method": method,
                    "arg": {
                        "args": ({"value": value} if legacy else [value])
                        if method in ("echo", "immediate")
                        else ({} if legacy else []),
                        "legacy": legacy,
                    },
                }
            )
        )
        await self.settle(worker)
        return fy_proto.decode(packets[-1]) if packets else None

    async def close(self):
        try:
            for worker in reversed(self.workers):
                await worker.mod.unload("owned-test-finished")
                worker.sdk.log.close()
                if worker.tasks or worker.calls:
                    raise AssertionError("Owned Worker did not drain")
        finally:
            self.module_context.__exit__(None, None, None)
            self.home_context.stop()


class DeckyRPCResults(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="framey-owned-rpc-")
        self.fixture = OwnedPair(self.temporary.name)

    async def asyncTearDown(self):
        await self.fixture.close()
        self.temporary.cleanup()

    async def test_every_json_success_survives_generated_sdk_worker_json_roundtrip(self):
        for api, legacy in [(2, False), (0, True)]:
            worker, _, packets = await self.fixture.boot(api)
            try:
                for value in VALUES:
                    for method in ["echo", "immediate"]:
                        reply = await self.fixture.call(
                            worker, packets, value, legacy=legacy, method=method
                        )
                        self.assertTrue(reply["ok"])
                        self.assertEqual(
                            reply["result"], {"__framey_decky_rpc_result__": 1, "value": value}
                        )
            finally:
                await worker.mod.unload("own-pair-complete")

    async def test_real_exception_and_nonjson_results_remain_failure_receipts(self):
        worker, _, packets = await self.fixture.boot()
        for method, code in [("crash", "exception"), ("non_json", "bad_result")]:
            reply = await self.fixture.call(worker, packets, method=method)
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["error"]["code"], code)
            self.assertNotIn("result", reply)

    async def test_cancellation_has_no_success_receipt(self):
        worker, _, packets = await self.fixture.boot()
        self.assertIsNone(await self.fixture.call(worker, packets, method="cancelled"))

    async def test_strict_backend_versions_fail_before_invocation_and_default_stays_unpaired(self):
        worker, _, _ = await self.fixture.boot()
        bridge = worker.mod._framey_bridge._adapter._bridge
        for version in [True, False, 0, 2, 1.0, "1", {}, []]:
            with self.assertRaises(ValueError):
                await bridge.call("echo", ["value"], result_envelope_version=version)
        self.assertEqual(bridge._plugin.calls, 0)
        self.assertEqual(
            await bridge.call("echo", [{"error": "old domain"}]), {"error": "old domain"}
        )

    async def test_matching_generated_pair_feature_is_conditional_and_original_bytes_are_preserved(
        self,
    ):
        for api in [0, 1, 2]:
            for backend in [False, True]:
                destination = self.fixture.generate(api, backend)
                provenance = json.loads((destination / "decky-provenance.json").read_text())
                if backend:
                    self.assertEqual(provenance["framey_features"]["rpc_envelope"], FEATURE)
                    self.assertIn(
                        "rpc_result_version=1", (destination / "backend.py").read_text()
                    )
                else:
                    self.assertNotIn("rpc_envelope", provenance.get("framey_features", {}))
                    self.assertFalse((destination / "backend.py").exists())
                frontend = (destination / "main.js").read_text()
                descriptor = json.JSONDecoder().raw_decode(frontend.split(".compat.mount(container, ", 1)[1])[0]
                self.assertEqual(descriptor.get("rpcResultVersion"), 1 if backend else None)


    async def test_exact_existing_worker_payload_budget_includes_42_bytes_of_framing(self):
        worker, _, packets = await self.fixture.boot()
        fitting = "x" * (fy_proto.PAYLOAD_MAX - 44)
        reply = await self.fixture.call(worker, packets, fitting)
        framed = reply["result"]
        self.assertTrue(reply["ok"])
        self.assertEqual(len(fy_proto.dumps(framed)), fy_proto.PAYLOAD_MAX)
        self.assertEqual(len(fy_proto.dumps(framed)) - len(fy_proto.dumps(fitting)), 42)
        rejected = await self.fixture.call(worker, packets, fitting + "x")
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"]["code"], "too_large")
        self.assertNotIn("result", rejected)


async def host_fixtures():
    with tempfile.TemporaryDirectory(prefix="framey-own-rpc-export-") as folder:
        fixture = OwnedPair(folder)
        result = {}
        try:
            for name, api, legacy in [("modern", 2, False), ("legacy", 0, True)]:
                worker, destination, packets = await fixture.boot(api)
                rows = []
                for value in VALUES:
                    rows.append(
                        {
                            "value": value,
                            "reply": await fixture.call(worker, packets, value, legacy=legacy),
                        }
                    )
                failure = await fixture.call(worker, packets, method="crash", legacy=legacy)
                result[name] = {
                    "successes": rows,
                    "failure": failure,
                    "main": (destination / "main.js").read_text(),
                }
                await worker.mod.unload("own-export-complete")
            return result
        finally:
            await fixture.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--host-fixtures"]:
        print(json.dumps(asyncio.run(host_fixtures())))
    else:
        unittest.main()
