import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fy_browser  # noqa: E402


class BrowserShutdownIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_down_reaps_native_child_before_outer_request_cancellation(self):
        spec = importlib.util.spec_from_file_location("framey_browser_shutdown", ROOT / "framey.py")
        fy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fy)

        class Process:
            def __init__(self):
                self.returncode = None
                self.waiting = asyncio.Event()
                self.exited = asyncio.Event()
                self.reaped = False

            async def wait(self):
                self.waiting.set()
                await self.exited.wait()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.reaped = True
                return self.returncode

            def kill(self):
                self.returncode = -9
                self.exited.set()

        process = Process()

        async def launch(*args, **kwargs):
            return process

        with (
            mock.patch.object(fy, "manifests", return_value=[{"id": "owner", "version": "1"}]),
            mock.patch.object(fy, "settings", return_value={"disabled": []}),
            mock.patch.dict(fy.MODS, {"fy_browser": fy_browser}, clear=True),
            mock.patch.object(fy_browser.asyncio, "create_subprocess_exec", launch),
        ):
            request = asyncio.create_task(
                fy_browser.open_url(fy.host, {"pid": "owner", "url": "https://example.org/"})
            )
            try:
                await asyncio.wait_for(process.waiting.wait(), 1)
                await fy.down()
                self.assertTrue(
                    process.reaped, "Framey shutdown returned before native child reaping"
                )
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                self.assertEqual(
                    (
                        await fy_browser.open_url(
                            fy.host, {"pid": "owner", "url": "https://example.org/"}
                        )
                    )["code"],
                    "not_allowed",
                )
            finally:
                await fy_browser.shutdown(fy.host)
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
