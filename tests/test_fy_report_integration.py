import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "fixtures" / "j"), str(HERE.parent)]

import jharness as H

REPORT_METHODS = ("report_draft", "report_link", "report_open")


class ReportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loader = H.Loader()
        self.addAsyncCleanup(self.loader.close)
        self.fy = self.loader.fy
        self.loader.plugin("sample", {"api": 1}, backend="raise AssertionError('reporting started plugin code')\n")
        self.fy.load_backends()
        self.fy.FAILS["sample"] = {"what": "backend", "msg": "existing failure", "at": 1}
        self.initial_failures = copy.deepcopy(self.fy.FAILS)
        self.initial_status = self.fy.supervisor.status()
        self.preview_arg = {"plugin": {"name": "Sample", "version": "1.2.3", "source": "local"}, "error": "The panel failed"}
        self.no_process = mock.patch("asyncio.create_subprocess_exec", new_callable=mock.AsyncMock)
        self.process = self.no_process.start()
        self.process.side_effect = AssertionError("unexpected worker/browser launch")
        self.addCleanup(self.no_process.stop)
        self.no_network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network"))
        self.network = self.no_network.start()
        self.addCleanup(self.no_network.stop)

    async def attach(self):
        page, task = await H.attach(self.loader)
        self.addAsyncCleanup(self.finish_session, task)
        return page

    async def finish_session(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        requests = list(self.fy.TASKS)
        for request in requests:
            request.cancel()
        if requests:
            await asyncio.gather(*requests, return_exceptions=True)

    async def settled(self, predicate):
        self.assertTrue(await H.settle(predicate, limit=2))

    def assert_no_runtime_effects(self):
        self.assertEqual(self.fy.FAILS, self.initial_failures)
        self.assertEqual(self.fy.supervisor.status(), self.initial_status)
        self.process.assert_not_awaited()
        self.network.assert_not_called()
        self.assertEqual(self.loader.net.calls, [])

    async def test_report_methods_are_core_only_and_outside_mutation_routes(self):
        for method in REPORT_METHODS:
            with self.subTest(method=method):
                self.assertEqual(self.fy.OWNER[method], "fy_report")
                for routes in (self.fy.SCOPED, self.fy.JOBS, self.fy.EDIT, self.fy.LOCKED, self.fy.RELOAD, self.fy.UPDATED):
                    self.assertNotIn(method, routes)
        page = await self.attach()
        plugin_token = next(token for token, pid in self.fy.TOKS.items() if pid == "sample")
        module = self.fy.use("fy_report")
        handlers = {method: mock.AsyncMock(return_value={"fixture": method}) for method in REPORT_METHODS}
        with mock.patch.dict(module.METHODS, handlers):
            request_id = 0
            for method in REPORT_METHODS:
                for token in (plugin_token, "forged-token", None):
                    request_id += 1
                    page.bind(id=request_id, gen="unauthorized", plugin="_core", method=method, arg={}, t=token)
            await self.settled(lambda: len(page.replies()) == 9)
            self.assertTrue(all(reply[2] == self.fy.NOAUTH for reply in page.replies()))
            for handler in handlers.values():
                handler.assert_not_awaited()
            for method in REPORT_METHODS:
                request_id += 1
                page.bind(id=request_id, gen="authorized", plugin="_core", method=method, arg={"fixture": method})
            await self.settled(lambda: len(page.replies()) == 12)
            self.assertEqual([reply[2] for reply in page.replies()[9:]], [{"fixture": method} for method in REPORT_METHODS])
            for method, handler in handlers.items():
                handler.assert_awaited_once_with(self.fy.host, {"fixture": method})
        self.assert_no_runtime_effects()

    async def test_actual_preview_and_link_ignore_busy_mutation_lock(self):
        lock = asyncio.Lock()
        await lock.acquire()
        self.fy.LOCK = lock
        try:
            draft = await asyncio.wait_for(self.fy.core("report_draft", self.preview_arg), 1)
            linked = await asyncio.wait_for(self.fy.core("report_link", {"title": draft["title"], "body": draft["body"]}), 1)
        finally:
            lock.release()
        self.assertIn("Sample", draft["body"])
        self.assertIn("1.2.3", draft["body"])
        self.assertIn(self.fy.VERSION, draft["body"])
        self.assertTrue(linked["url"].startswith(self.fy.LOADER + "/issues/new?"))
        self.assertNotIn("created", linked)
        self.assertNotIn("submitted", linked)
        self.assert_no_runtime_effects()

    async def test_actual_session_preview_and_link_preserve_generation_and_existing_failure(self):
        page = await self.attach()
        page.bind(id=1, gen="details-old", plugin="_core", method="report_draft", arg=self.preview_arg)
        await self.settled(lambda: len(page.replies()) == 1)
        generation, request_id, draft = page.replies()[0]
        self.assertEqual((generation, request_id), ("details-old", 1))
        self.assertIn("Sample", draft["body"])
        page.bind(id=1, gen="report-current", plugin="_core", method="report_link", arg={"title": draft["title"], "body": draft["body"]})
        await self.settled(lambda: len(page.replies()) == 2)
        self.assertEqual(page.replies()[1][:2], ["report-current", 1])
        self.assertTrue(page.replies()[1][2]["url"].startswith(self.fy.LOADER + "/issues/new?"))
        self.assertEqual(page.events(), [])
        self.assert_no_runtime_effects()

    async def test_failed_status_and_log_collectors_are_not_needed_for_preview(self):
        private_failure = OSError("/home/alice/private token=unshared")
        with mock.patch.object(self.fy.supervisor, "status", side_effect=private_failure) as status, mock.patch.object(self.fy, "log_tail", side_effect=private_failure) as logs, mock.patch.object(self.fy, "manifests", side_effect=private_failure) as manifests:
            draft = await self.fy.core("report_draft", self.preview_arg)
            self.assertIn("Sample", draft["body"])
            self.assertIn("Steps to reproduce", draft["body"])
            status.assert_not_called()
            logs.assert_not_called()
            manifests.assert_not_called()
        self.assert_no_runtime_effects()

    async def test_shipping_and_panel_injection_include_report_helper_before_its_consumers(self):
        for filename in ("fy_report.py", "fy-report.js"):
            self.assertIn(filename, self.fy.MANIFEST)
            self.assertTrue((self.fy.ROOT / filename).is_file(), filename + " must ship in a fresh loader")
        self.assertIn("fy-report.js", self.fy.PANEL_JS)
        for consumer in ("fy-modules.js", "fy-catalog.js"):
            self.assertLess(self.fy.PANEL_JS.index("fy-report.js"), self.fy.PANEL_JS.index(consumer))
        prelude = self.fy.prelude()
        runtime, _ = json.JSONDecoder().raw_decode(prelude.split("window.FY_RUNTIME=", 1)[1])
        self.assertEqual(runtime["report_repository"], self.fy.LOADER)
        helper = self.fy.js("fy-report.js")
        self.assertTrue(helper.strip())
        panel = self.fy.main_code()[0][0]
        self.assertIn(helper, panel)
        self.assertLess(panel.index(helper), panel.index(self.fy.js("fy-modules.js")))
        self.assertLess(panel.index(helper), panel.index(self.fy.js("fy-catalog.js")))
        self.assert_no_runtime_effects()


if __name__ == "__main__":
    unittest.main()
