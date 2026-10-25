import asyncio
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests" / "fixtures" / "a")]

import harness
from fy_supervisor import Supervisor


class RealSdkEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = harness.make_root()
        for name in ("fy_sdk.py", "fy_store.py"):
            shutil.copy(REPO / name, self.root / name)
        self.addCleanup(shutil.rmtree, self.root, True)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.root / "run")})
        env.start()
        self.addCleanup(env.stop)
        self.host = harness.FakeHost(self.root)
        self.sup = Supervisor(self.host)
        self.addAsyncCleanup(self.sup.shutdown, 1.0)
        plugin, meta = harness.make_plugin(self.root, "p", "emit_flood")
        self.sup.register("p", plugin, meta)

    async def test_rate_drops_reach_delivery_after_next_accepted_event(self):
        accepted = await self.sup.call("p", "flood", 500)
        initial = self.sup.events.take(100)
        self.assertEqual(len(initial), accepted)
        await asyncio.sleep(1.1)
        self.assertEqual(await self.sup.call("p", "flood", 1), 1)
        following = self.sup.events.take(100)
        self.assertEqual(
            sum(e["dropped"] for e in initial + following),
            500 - accepted,
        )
        await asyncio.sleep(1.1)
        self.assertEqual(await self.sup.call("p", "flood", 1), 1)
        self.assertEqual(sum(e["dropped"] for e in self.sup.events.take(100)), 0)

    async def test_sdk_validation_drops_are_reported_once(self):
        self.assertEqual(await self.sup.call("p", "bad", None), [False, False, True])
        delivered = self.sup.events.take(100)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["name"], "fine")
        self.assertEqual(delivered[0]["dropped"], 2)
        self.assertEqual(await self.sup.call("p", "flood", 1), 1)
        self.assertEqual(sum(e["dropped"] for e in self.sup.events.take(100)), 0)


if __name__ == "__main__":
    unittest.main()
