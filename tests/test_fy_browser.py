import asyncio
import gc
import importlib
import subprocess
import sys
import unittest
import weakref
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    browser = importlib.import_module("fy_browser")
except ModuleNotFoundError:
    browser = None


class Host:
    def __init__(self):
        self.plugins = {
            "lookup": {"id": "lookup", "version": "1"},
            "proton": {"id": "proton", "version": "1"},
            "third": {"id": "third", "version": "1"},
        }
        self.disabled = set()

    def enabled(self, pid):
        return pid in self.plugins and pid not in self.disabled

    def manifests(self):
        return list(self.plugins.values())


class Process:
    def __init__(self, code=0):
        self.returncode = code
        self.exited = asyncio.Event()
        self.reaped = asyncio.Event()
        self.waiting = asyncio.Event()
        self.killed = False
        if code is not None:
            self.exited.set()

    async def wait(self):
        self.waiting.set()
        await self.exited.wait()
        self.reaped.set()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.exited.set()


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("expected asynchronous operation did not settle")


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(browser, "native browser opener has not been implemented")
        self.host = Host()
        self.now = 100.0
        self.processes = []
        self.argv = []

        async def launch(*args, **kwargs):
            self.argv.append((args, kwargs))
            process = Process()
            self.processes.append(process)
            return process

        self.launch = mock.patch.object(
            browser.asyncio, "create_subprocess_exec", side_effect=launch
        )
        self.launch.start()
        self.clock = mock.patch.object(browser, "monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.launch.stop)
        self.addCleanup(self.clock.stop)

    async def open(self, url="https://example.com/", pid="lookup", host=None):
        return await browser.open_url(host or self.host, {"pid": pid, "url": url})

    async def test_preserves_original_url_as_one_argument_without_shell_or_output(self):
        url = "https://EXAMPLE.com:443/a?secret=%26&literal=$HOME;echo#frag?x=y"
        self.assertEqual(await self.open(url), {"requested": True})
        self.assertEqual(
            self.argv,
            [
                (
                    ("/usr/bin/xdg-open", url),
                    {
                        "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                    },
                )
            ],
        )
        self.assertTrue(self.processes[0].reaped.is_set())

    async def test_rejects_unsafe_urls_before_launch_or_network(self):
        urls = [
            None,
            3,
            "",
            "/relative",
            "//example.com/a",
            "http://example.com/",
            "file:///tmp/a",
            "javascript:alert(1)",
            "steam://openurl/x",
            "https://user:secret@example.com/",
            "https://example.com\\@other.com/",
            "https://example.com/\nsecret",
            "https://exämple.com/",
            "https://-bad.com/",
            "https://example..com/",
            "https://example.com:/",
            "https://example.com:0/",
            "https://example.com:65536/",
            "https://[garbage]/",
            "https://example.com/" + "a" * 2048,
        ]
        with (
            mock.patch("socket.getaddrinfo", side_effect=AssertionError("no DNS")),
            mock.patch("socket.create_connection", side_effect=AssertionError("no HTTP")),
        ):
            for url in urls:
                with self.subTest(url=url):
                    result = await self.open(url)
                    self.assertIn(result["code"], ("bad_url", "scheme"))
                    self.assertNotIn("requested", result)
        self.assertEqual(self.argv, [])

    async def test_valid_maximum_ascii_url_ipv6_and_fragments_need_no_resolution(self):
        urls = [
            "https://[::1]:443/a#frag",
            "https://localhost/?q=ok#part",
            "https://example.com/" + "a" * (2048 - len("https://example.com/")),
        ]
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("no DNS")):
            for url in urls:
                self.assertEqual(await self.open(url), {"requested": True})
        self.assertEqual([item[0][1] for item in self.argv], urls)

    async def test_unknown_disabled_and_malformed_identity_fail_closed(self):
        self.host.disabled.add("lookup")
        for pid in ["lookup", "missing", "../lookup", "", None, 1, True]:
            with self.subTest(pid=pid):
                self.assertEqual((await self.open(pid=pid))["code"], "not_allowed")
        del self.host.plugins["lookup"]
        self.host.disabled.clear()
        self.host.enabled = lambda pid: True
        self.assertEqual((await self.open())["code"], "not_allowed")
        self.assertEqual(self.argv, [])

    async def test_missing_broken_or_non_boolean_authorization_fails_closed(self):
        for enabled in [None, lambda pid: 1, mock.Mock(side_effect=RuntimeError("private"))]:
            host = Host()
            host.enabled = enabled
            self.assertEqual((await self.open(host=host))["code"], "not_allowed")
        self.assertEqual(self.argv, [])

    async def test_invalid_argument_shape_fails_closed(self):
        for arg in [None, "https://example.com/", [], {"url": "https://example.com/"}]:
            self.assertEqual((await browser.open_url(self.host, arg))["code"], "not_allowed")
        self.assertEqual(self.argv, [])

    async def test_per_plugin_and_total_running_limits_release_after_exit(self):
        processes = [Process(None) for _ in range(3)]

        async def launch(*args, **kwargs):
            return processes.pop(0)

        tasks = []
        with (
            mock.patch.object(browser.asyncio, "create_subprocess_exec", side_effect=launch),
            mock.patch.object(browser, "TOTAL", 2),
        ):
            tasks.append(asyncio.create_task(self.open()))
            await until(lambda: len(processes) == 2)
            self.assertEqual((await self.open())["code"], "busy")
            tasks.append(asyncio.create_task(self.open(pid="proton")))
            await until(lambda: len(processes) == 1)
            self.assertEqual((await self.open(pid="third"))["code"], "busy")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await until(lambda: not browser._states[self.host].active)
        self.assertEqual(await self.open(pid="third"), {"requested": True})

    async def test_sliding_rate_limits_are_per_plugin_and_total_with_expiry(self):
        with (
            mock.patch.object(browser, "PER_PLUGIN_RATE", 2),
            mock.patch.object(browser, "TOTAL_RATE", 3),
            mock.patch.object(browser, "RATE_WINDOW", 10),
        ):
            self.assertEqual(await self.open(), {"requested": True})
            self.assertEqual(await self.open(), {"requested": True})
            self.assertEqual((await self.open())["code"], "rate_limited")
            self.assertEqual(await self.open(pid="proton"), {"requested": True})
            self.assertEqual((await self.open(pid="third"))["code"], "rate_limited")
            self.now += 10
            self.assertEqual(await self.open(), {"requested": True})

    async def test_hosts_have_independent_limits_and_do_not_leak_after_collection(self):
        other = Host()
        with mock.patch.object(browser, "PER_PLUGIN_RATE", 1):
            self.assertEqual(await self.open(), {"requested": True})
            self.assertEqual((await self.open())["code"], "rate_limited")
            self.assertEqual(await self.open(host=other), {"requested": True})
        ref = weakref.ref(other)
        del other
        gc.collect()
        self.assertIsNone(ref())

    async def test_disable_uninstall_or_replacement_before_spawn_prevents_launch(self):
        for change in [
            lambda: self.host.disabled.add("lookup"),
            lambda: self.host.plugins.pop("lookup"),
            lambda: self.host.plugins["lookup"].update(version="2"),
        ]:
            self.host = Host()
            task = asyncio.create_task(self.open())
            await asyncio.sleep(0)
            change()
            self.assertEqual((await task)["code"], "not_allowed")
        self.assertEqual(self.argv, [])

    async def test_identity_change_during_spawn_kills_and_reaps_before_acceptance(self):
        gate = asyncio.Event()
        started = asyncio.Event()
        process = Process(None)

        async def launch(*args, **kwargs):
            started.set()
            await gate.wait()
            return process

        with mock.patch.object(browser.asyncio, "create_subprocess_exec", side_effect=launch):
            task = asyncio.create_task(self.open())
            await started.wait()
            self.host.disabled.add("lookup")
            gate.set()
            self.assertEqual((await task)["code"], "not_allowed")
        self.assertTrue(process.killed)
        self.assertTrue(process.reaped.is_set())

    async def test_spawn_and_nonzero_exit_failures_are_sanitized(self):
        with mock.patch.object(
            browser.asyncio, "create_subprocess_exec", side_effect=OSError("secret URL or path")
        ):
            result = await self.open()
        self.assertEqual(result["code"], "browser_unavailable")
        self.assertNotIn("secret", str(result))
        process = Process(3)
        with mock.patch.object(browser.asyncio, "create_subprocess_exec", return_value=process):
            result = await self.open()
        self.assertEqual(result["code"], "browser_unavailable")
        self.assertTrue(process.reaped.is_set())

    async def test_timeout_kills_and_reaps_child_and_releases_slot(self):
        process = Process(None)
        with (
            mock.patch.object(browser, "REQUEST_TIMEOUT", 0.01),
            mock.patch.object(browser.asyncio, "create_subprocess_exec", return_value=process),
        ):
            self.assertEqual((await self.open())["code"], "timeout")
        await until(lambda: process.reaped.is_set())
        await until(lambda: not browser._states[self.host].active)
        self.assertTrue(process.killed)
        self.assertEqual(await self.open(), {"requested": True})

    async def test_cancellation_during_wait_kills_and_reaps_child(self):
        process = Process(None)
        with mock.patch.object(browser.asyncio, "create_subprocess_exec", return_value=process):
            task = asyncio.create_task(self.open())
            await process.waiting.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await until(lambda: process.reaped.is_set())
        self.assertTrue(process.killed)

    async def test_cancellation_during_spawn_reaps_late_child_and_holds_slot(self):
        gate = asyncio.Event()
        started = asyncio.Event()
        process = Process(None)

        async def launch(*args, **kwargs):
            started.set()
            await gate.wait()
            return process

        with mock.patch.object(browser.asyncio, "create_subprocess_exec", side_effect=launch):
            task = asyncio.create_task(self.open())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((await self.open())["code"], "busy")
            gate.set()
            await until(lambda: process.reaped.is_set())
            await until(lambda: not browser._states[self.host].active)
        self.assertTrue(process.killed)


class ShutdownTests(unittest.TestCase):
    def run_lifecycle(self, scenario):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scenario())
            self.assertEqual(asyncio.all_tasks(loop), set())
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def test_shutdown_reaps_cancelled_request_before_immediate_loop_close(self):
        async def scenario():
            host = Host()
            process = Process(None)
            with mock.patch.object(browser.asyncio, "create_subprocess_exec", return_value=process):
                task = asyncio.create_task(
                    browser.open_url(host, {"pid": "lookup", "url": "https://example.com/"})
                )
                await process.waiting.wait()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                shutdown = getattr(browser, "shutdown", None)
                if shutdown is not None:
                    await shutdown(host)
                self.assertTrue(process.reaped.is_set(), "shutdown must await native reaping")
                self.assertTrue(process.killed)
                self.assertEqual(
                    (
                        await browser.open_url(
                            host, {"pid": "lookup", "url": "https://example.com/"}
                        )
                    )["code"],
                    "not_allowed",
                )

        self.run_lifecycle(scenario)

    def test_shutdown_drains_late_spawn_and_is_idempotent(self):
        async def scenario():
            host = Host()
            process = Process(None)
            started = asyncio.Event()
            gate = asyncio.Event()

            async def launch(*args, **kwargs):
                started.set()
                await gate.wait()
                return process

            with mock.patch.object(browser.asyncio, "create_subprocess_exec", side_effect=launch):
                task = asyncio.create_task(
                    browser.open_url(host, {"pid": "lookup", "url": "https://example.com/"})
                )
                await started.wait()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(
                    callable(getattr(browser, "shutdown", None)), "shutdown hook missing"
                )
                drain = asyncio.create_task(browser.shutdown(host))
                await asyncio.sleep(0)
                self.assertFalse(drain.done())
                gate.set()
                await drain
                await browser.shutdown(host)
                self.assertTrue(process.reaped.is_set())
                self.assertTrue(process.killed)

        self.run_lifecycle(scenario)

    def test_shutdown_cancels_stalled_spawn_after_bounded_grace(self):
        async def scenario():
            host = Host()
            started = asyncio.Event()
            spawn_cancelled = asyncio.Event()

            async def launch(*args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    spawn_cancelled.set()

            with mock.patch.object(browser.asyncio, "create_subprocess_exec", side_effect=launch):
                task = asyncio.create_task(
                    browser.open_url(host, {"pid": "lookup", "url": "https://example.com/"})
                )
                await started.wait()
                self.assertTrue(
                    callable(getattr(browser, "shutdown", None)), "shutdown hook missing"
                )
                with mock.patch.object(browser, "SHUTDOWN_GRACE", 0.01):
                    await browser.shutdown(host)
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(spawn_cancelled.is_set())

        self.run_lifecycle(scenario)

    def test_shutdown_closes_admission_before_queued_spawn_can_run(self):
        async def scenario():
            host = Host()
            with mock.patch.object(
                browser.asyncio, "create_subprocess_exec", return_value=Process()
            ) as launch:
                task = asyncio.create_task(
                    browser.open_url(host, {"pid": "lookup", "url": "https://example.com/"})
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await browser.shutdown(host)
                await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(launch.call_count, 0)

        self.run_lifecycle(scenario)

    def test_cancelled_shutdown_still_drains_before_propagating_cancellation(self):
        async def scenario():
            host = Host()
            process = Process(None)
            with mock.patch.object(browser.asyncio, "create_subprocess_exec", return_value=process):
                task = asyncio.create_task(
                    browser.open_url(host, {"pid": "lookup", "url": "https://example.com/"})
                )
                await process.waiting.wait()
                drain = asyncio.create_task(browser.shutdown(host))
                await asyncio.sleep(0)
                drain.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await drain
                self.assertTrue(process.reaped.is_set())
                await asyncio.gather(task, return_exceptions=True)

        self.run_lifecycle(scenario)

    def test_shutdown_never_cancels_reaping_when_spawn_grace_expires(self):
        async def scenario():
            host = Host()
            process = Process(None)
            reap_gate = asyncio.Event()

            async def slow_reap():
                process.waiting.set()
                await process.exited.wait()
                await reap_gate.wait()
                process.reaped.set()
                return process.returncode

            process.wait = slow_reap
            with mock.patch.object(browser.asyncio, "create_subprocess_exec", return_value=process):
                task = asyncio.create_task(
                    browser.open_url(host, {"pid": "lookup", "url": "https://example.com/"})
                )
                await process.waiting.wait()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                with mock.patch.object(browser, "SHUTDOWN_GRACE", 0.001):
                    drain = asyncio.create_task(browser.shutdown(host))
                    await asyncio.sleep(0.01)
                    returned_before_reaping = drain.done()
                    reap_gate.set()
                    await drain
                self.assertFalse(returned_before_reaping)
                self.assertTrue(process.reaped.is_set())

        self.run_lifecycle(scenario)


if __name__ == "__main__":
    unittest.main()
