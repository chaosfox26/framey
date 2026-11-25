"""Service composition tests; only the external CDP/socket boundary is simulated."""

import asyncio
import gc
import importlib
import json
import sys
import unittest
import weakref
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    steam = importlib.import_module("fy_steam")
except ModuleNotFoundError:
    steam = None


FOCUS = {
    "status": "empty",
    "focusedAppId": None,
    "focusedWindowId": 0,
    "focusedOverlayAppId": "0",
    "focusedOverlayPid": "",
}


class Socket:
    """A passive CDP endpoint with manually controlled replies, never a network connection."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.held = []
        self.hold = False
        self.closed = False
        self.readers = 0
        self.writer_closed = False
        self.w = self
        self.on_evaluate = None

    async def send(self, message):
        self.sent.append(message)
        method = message["method"]
        if method == "Runtime.enable":
            self.queue.put_nowait(
                json.dumps(
                    {
                        "method": "Runtime.executionContextCreated",
                        "params": {
                            "context": {
                                "id": 7,
                                "uniqueId": "private-context",
                                "auxData": {"frameId": "main", "isDefault": True},
                            }
                        },
                    }
                )
            )
            self.reply(message, {})
        elif method == "Page.enable":
            self.reply(message, {})
        elif method == "Page.getFrameTree":
            self.reply(message, {"frameTree": {"frame": {"id": "main"}}})
        elif method == "Runtime.evaluate":
            if self.on_evaluate:
                self.on_evaluate(message)
            if self.hold:
                self.held.append(message)
            else:
                self.answer(message)
        else:
            raise AssertionError("A write or unapproved CDP method was attempted")

    def reply(self, message, result):
        self.queue.put_nowait(json.dumps({"id": message["id"], "result": result}))

    def answer(self, message):
        expression = message["params"]["expression"]
        if "framey-steam-probe-v1" in expression:
            value = {"apps": True, "focus": True}
        elif "GetFocused" in expression:
            value = FOCUS
        elif "GetAppOverviewByAppID(" in expression:
            value = {"status": "not_found", "app": None}
        else:
            value = {"status": "empty", "apps": [], "visibleCount": 0, "truncated": False}
        self.reply(message, {"result": {"type": "object", "value": value}})

    def release(self):
        self.hold = False
        for message in self.held:
            self.answer(message)
        self.held.clear()

    async def recv(self):
        self.readers += 1
        try:
            return await self.queue.get()
        finally:
            self.readers -= 1

    def close(self):
        self.closed = True
        self.queue.put_nowait(None)

    async def wait_closed(self):
        self.writer_closed = True


class Host:
    def __init__(self):
        self.socket = Socket()
        self.WS = self
        self.identities = {pid: ("private-token-" + pid, "manifest-v1") for pid in "abcde"}
        self.allowed = True
        self.inventory_calls = 0
        self.opens = 0
        self.inventory_entered = asyncio.Event()
        self.inventory_gate = None
        self.cleanup_gate = None
        self.cleanup_entered = asyncio.Event()
        self.cleanup_interrupted = False

    def steam_caller(self, pid):
        value = self.identities.get(pid)
        return tuple(list(value)) if value is not None else None

    def steam_target_allowed(self, tab):
        return self.allowed

    async def tabs(self):
        self.inventory_calls += 1
        self.inventory_entered.set()
        try:
            if self.inventory_gate:
                await self.inventory_gate.wait()
        finally:
            if self.cleanup_gate:
                self.cleanup_entered.set()
                try:
                    await self.cleanup_gate.wait()
                except asyncio.CancelledError:
                    self.cleanup_interrupted = True
                    raise
        return [
            {
                "id": "private-target",
                "type": "page",
                "url": "https://private.invalid/",
                "webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/private-target",
            }
        ]

    async def open(self, endpoint):
        self.opens += 1
        return self.socket


async def until(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("The offline event sequence did not settle")


class SteamServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(steam, "production caller-scoped Steam service is not implemented")
        self.host = Host()
        self.addAsyncCleanup(steam.shutdown, self.host)

    async def read(self, pid="a", operation="focus", **extra):
        return await steam.METHODS["steam_read"](
            self.host, {"pid": pid, "operation": operation, **extra}
        )

    async def test_authenticated_equal_pid_and_id_returns_real_validated_projection(self):
        self.assertEqual(
            await self.read(id="a"),
            {"ok": True, "available": True, "source": "steam-focus-cache", "data": FOCUS},
        )
        self.assertEqual(self.host.opens, 1)

    async def test_unknown_or_extra_parameters_never_discover_targets(self):
        invalid = [
            None,
            [],
            {},
            {"operation": "focus"},
            {"pid": True, "operation": "focus"},
            {"pid": "a", "id": "b", "operation": "focus"},
            {"pid": "a", "operation": "focus", "target": "private-target"},
            {"pid": "a", "operation": "focus", "args": {"expression": "1"}},
            {"pid": "a", "operation": "app", "args": {"appId": True}},
            {"pid": "a", "operation": "focus", "args": None},
        ]
        for arg in invalid:
            with self.subTest(arg=arg):
                result = await steam.METHODS["steam_read"](self.host, arg)
                self.assertEqual(
                    result, {"ok": False, "available": False, "code": "invalid_arguments"}
                )
        self.assertEqual(
            (await self.read(operation="Runtime.evaluate"))["code"], "unknown_operation"
        )
        self.assertEqual(self.host.inventory_calls, 0)

    async def test_missing_caller_denied_before_inventory(self):
        self.host.identities.clear()
        self.assertEqual((await self.read())["code"], "not_allowed")
        self.assertEqual(self.host.inventory_calls, 0)

    async def test_one_transport_shared_without_sharing_caller_authority(self):
        self.assertTrue((await self.read("a"))["ok"])
        self.assertTrue((await self.read("b", "visibleApps"))["ok"])
        self.assertEqual(self.host.opens, 1)
        self.host.identities["a"] = None
        self.assertEqual((await self.read("a"))["code"], "not_allowed")
        self.assertTrue((await self.read("b", "app", args={"appId": 42}))["ok"])

    async def test_identity_changed_during_inventory_discards_reply(self):
        self.host.inventory_gate = asyncio.Event()
        task = asyncio.create_task(self.read())
        await self.host.inventory_entered.wait()
        self.host.identities["a"] = ("new-private-token", "manifest-v2")
        self.host.inventory_gate.set()
        self.assertEqual((await task)["code"], "stale_caller")
        self.assertTrue((await self.read())["ok"])

    async def test_one_caller_revocation_does_not_cancel_other_pending_read(self):
        self.host.socket.hold = True
        a, b = asyncio.create_task(self.read("a")), asyncio.create_task(self.read("b"))
        try:
            await until(lambda: len(self.host.socket.held) == 2)
            steam.invalidate(self.host, "a")
            self.host.identities["a"] = None
            self.host.socket.release()
            self.assertEqual((await a)["code"], "stale_caller")
            self.assertTrue((await b)["ok"])
            self.assertFalse(self.host.socket.closed)
        finally:
            self.host.socket.release()
            await asyncio.gather(a, b, return_exceptions=True)

    async def test_invalidation_latches_old_identity_until_rotation(self):
        self.assertTrue((await self.read())["ok"])
        steam.invalidate(self.host, "a")
        self.assertEqual((await self.read())["code"], "not_allowed")
        self.host.identities["a"] = ("rotated-private-token", "manifest-v1")
        self.assertTrue((await self.read())["ok"])

    async def test_explicit_invalidation_latches_authorized_identity_before_its_first_read(self):
        self.assertTrue((await self.read("a"))["ok"])
        steam.invalidate(self.host, "b")
        self.assertEqual((await self.read("b")).get("code"), "not_allowed")
        self.host.identities["b"] = ("rotated-b", "manifest-v1")
        self.assertTrue((await self.read("b"))["ok"])

    async def test_repeated_read_cancellation_drains_cleanup_before_releasing_admission(self):
        self.host.inventory_gate = asyncio.Event()
        self.host.cleanup_gate = asyncio.Event()
        task = asyncio.create_task(self.read())
        try:
            await self.host.inventory_entered.wait()
            task.cancel()
            await self.host.cleanup_entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertFalse(self.host.cleanup_interrupted)
            self.assertEqual((await self.read())["code"], "busy")
            self.host.cleanup_gate.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.host.inventory_gate.set()
            self.assertTrue((await self.read())["ok"])
        finally:
            self.host.cleanup_gate.set()
            self.host.inventory_gate.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_shutdown_drains_service_boundary_without_owning_unrelated_caller_work(self):
        self.host.socket.hold = True
        result_returned, unrelated = asyncio.Event(), asyncio.Event()

        async def caller():
            result = await self.read()
            result_returned.set()
            await unrelated.wait()
            return result

        task = asyncio.create_task(caller())
        try:
            await until(lambda: len(self.host.socket.held) == 1)
            await steam.shutdown(self.host)
            self.assertTrue(result_returned.is_set())
            self.assertFalse(task.done(), "Shutdown cancelled or waited for unrelated caller work")
        finally:
            self.host.socket.release()
            unrelated.set()
            await task

    async def test_identity_changed_while_queued_behind_discovery_is_rejected(self):
        self.host.inventory_gate = asyncio.Event()
        a = asyncio.create_task(self.read("a"))
        await self.host.inventory_entered.wait()
        b = asyncio.create_task(self.read("b"))
        await asyncio.sleep(0)
        self.host.identities["b"] = None
        self.host.inventory_gate.set()
        self.assertTrue((await a)["ok"])
        self.assertEqual((await b)["code"], "stale_caller")

    async def test_same_pid_busy_and_total_pending_limit_release_after_cleanup(self):
        self.host.socket.hold = True
        tasks = [asyncio.create_task(self.read(pid)) for pid in "abcd"]
        try:
            await until(lambda: len(self.host.socket.held) == 4)
            self.assertEqual((await self.read("a"))["code"], "busy")
            self.assertEqual((await self.read("e"))["code"], "busy")
            self.host.socket.release()
            self.assertTrue(all(r["ok"] for r in await asyncio.gather(*tasks)))
            self.assertTrue((await self.read("e"))["ok"])
        finally:
            self.host.socket.release()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_per_caller_rate_does_not_block_other_caller(self):
        for _ in range(10):
            self.assertTrue((await self.read())["ok"])
        self.assertEqual((await self.read())["code"], "rate_limited")
        self.assertTrue((await self.read("b"))["ok"])

    async def test_host_rate_limit_bounds_combined_callers(self):
        for pid in "abc":
            for _ in range(10):
                self.assertTrue((await self.read(pid))["ok"])
        self.assertEqual((await self.read("d"))["code"], "rate_limited")

    async def test_target_revocation_keeps_old_leases_revoked_without_revoking_caller(self):
        self.assertTrue((await self.read())["ok"])
        steam.invalidate_targets(self.host)
        self.assertTrue((await self.read())["ok"])
        self.assertEqual(self.host.opens, 1)

    async def test_strict_host_target_policy_rejects_truthy_values(self):
        self.host.allowed = 1
        self.assertEqual((await self.read())["code"], "target_unavailable")
        self.assertEqual(self.host.opens, 0)

    async def test_raw_cdp_error_and_private_values_never_cross_service(self):
        def fail(message):
            raise RuntimeError("private-token ws://private-endpoint/private-target")

        self.host.socket.on_evaluate = fail
        result = await self.read()
        self.assertEqual(result, {"ok": False, "available": False, "code": "context_unavailable"})
        self.assertNotIn("private", json.dumps(result))

    async def test_shutdown_closes_admission_drains_socket_reader_and_stays_closed(self):
        self.assertTrue((await self.read())["ok"])
        await steam.shutdown(self.host)
        self.assertEqual((await self.read())["code"], "service_closed")
        self.assertTrue(self.host.socket.closed)
        self.assertTrue(self.host.socket.writer_closed)
        self.assertEqual(self.host.socket.readers, 0)
        await steam.shutdown(self.host)

    async def test_repeated_shutdown_cancellation_does_not_interrupt_retired_read_cleanup(self):
        self.host.inventory_gate = asyncio.Event()
        self.host.cleanup_gate = asyncio.Event()
        read = asyncio.create_task(self.read())
        shutdown = None
        try:
            await self.host.inventory_entered.wait()
            steam.invalidate(self.host, "a")
            await self.host.cleanup_entered.wait()
            shutdown = asyncio.create_task(steam.shutdown(self.host))
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            self.assertFalse(shutdown.done(), "Shutdown returned while owned cleanup was blocked")
            self.assertFalse(self.host.cleanup_interrupted)
            self.host.cleanup_gate.set()
            result = await read
            self.assertIn(result["code"], ("stale_caller", "service_closed"))
            with self.assertRaises(asyncio.CancelledError):
                await shutdown
            await steam.shutdown(self.host)
        finally:
            self.host.inventory_gate.set()
            self.host.cleanup_gate.set()
            await asyncio.gather(read, *([shutdown] if shutdown else []), return_exceptions=True)

    async def test_completed_reply_cannot_escape_identity_change_before_return(self):
        def rotate(message):
            if "GetFocused" in message["params"]["expression"]:
                self.host.identities["a"] = None

        self.host.socket.on_evaluate = rotate
        self.assertEqual((await self.read())["code"], "stale_caller")

    async def test_closed_host_does_not_survive_in_weak_registry(self):
        host = Host()
        self.assertTrue(
            (await steam.METHODS["steam_read"](host, {"pid": "a", "operation": "focus"}))["ok"]
        )
        await steam.shutdown(host)
        ref = weakref.ref(host)
        del host
        gc.collect()
        self.assertIsNone(ref())


if __name__ == "__main__":
    unittest.main()
