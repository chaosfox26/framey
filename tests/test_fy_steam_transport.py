"""Offline transport contract tests. Never connect to a CDP endpoint."""

import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    provider = importlib.import_module("fy_steam_data")
except ModuleNotFoundError:
    provider = None

try:
    transport_module = importlib.import_module("fy_steam_transport")
except ModuleNotFoundError:
    transport_module = None


class Writer:
    def __init__(self):
        self.waited = False

    async def wait_closed(self):
        self.waited = True


class WebSocket:
    def __init__(self, contexts=None):
        self.queue = asyncio.Queue()
        self.w = Writer()
        self.closed = False
        self.sent = []
        self.readers = self.max_readers = 0
        self.defer = False
        self.on_evaluate = None
        self.contexts = (
            contexts
            if contexts is not None
            else [
                {"id": 7, "uniqueId": "main-v1", "auxData": {"frameId": "main", "isDefault": True}},
                {"id": 8, "auxData": {"frameId": "child", "isDefault": True}},
                {"id": 9, "auxData": {"frameId": "main", "isDefault": False}},
            ]
        )

    def emit(self, method, params=None):
        self.queue.put_nowait(json.dumps({"method": method, "params": params or {}}))

    def reply(self, identifier, result=None, **extra):
        self.queue.put_nowait(json.dumps({"id": identifier, "result": result or {}, **extra}))

    async def send(self, message):
        self.sent.append(message)
        method = message["method"]
        if method == "Runtime.enable":
            for context in self.contexts:
                self.emit("Runtime.executionContextCreated", {"context": context})
            self.reply(message["id"])
        elif method == "Page.enable":
            self.reply(message["id"])
        elif method == "Page.getFrameTree":
            self.reply(message["id"], {"frameTree": {"frame": {"id": "main"}}})
        elif method == "Runtime.evaluate":
            if self.on_evaluate:
                self.on_evaluate(message)
            elif not self.defer:
                self.reply(
                    message["id"],
                    {"result": {"type": "object", "value": {"apps": True, "focus": False}}},
                )
        else:
            raise AssertionError("unapproved CDP method")

    async def recv(self):
        self.readers += 1
        self.max_readers = max(self.max_readers, self.readers)
        try:
            return await self.queue.get()
        finally:
            self.readers -= 1

    def close(self):
        self.closed = True
        self.queue.put_nowait(None)


class Host:
    def __init__(self, sockets=None):
        self.inventory = [
            {
                "id": "private-target",
                "type": "page",
                "title": "private title",
                "url": "https://private.invalid/",
                "owned": True,
                "webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/private-target",
            }
        ]
        self.sockets = list(sockets or [WebSocket()])
        self.opened = []
        self.WS = self

    async def tabs(self):
        return self.inventory

    async def open(self, endpoint):
        self.opened.append(endpoint)
        return self.sockets.pop(0)


def owned(tab):
    return tab.get("owned") is True


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("offline event sequence did not settle")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(
            transport_module, "production passive CDP transport is not implemented"
        )
        self.assertIsNotNone(provider, "production fixed-read provider is not implemented")
        self.socket = WebSocket()
        self.host = Host([self.socket])
        self.transport = transport_module.SteamCDPTransport(
            self.host,
            allow_target=owned,
            allow_expression=lambda expression: True,
            rpc_timeout=0.05,
        )
        self.addAsyncCleanup(self.transport.dispose)

    async def lease(self):
        return (await self.transport.list_targets())[0]

    async def assert_failure(self, operation, code):
        with self.assertRaises(transport_module.TransportFailure) as caught:
            await operation
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    async def evaluations(self, count):
        await until(
            lambda: len([m for m in self.socket.sent if m["method"] == "Runtime.evaluate"]) == count
        )
        return [m for m in self.socket.sent if m["method"] == "Runtime.evaluate"]

    async def test_passive_domains_one_reader_and_explicit_main_default_context(self):
        lease = await self.lease()
        result = await self.transport.evaluate(lease, provider.PROBE)
        self.assertEqual(
            result, {"result": {"type": "object", "value": {"apps": True, "focus": False}}}
        )
        self.assertEqual(
            [m["method"] for m in self.socket.sent],
            ["Runtime.enable", "Page.enable", "Page.getFrameTree", "Runtime.evaluate"],
        )
        params = self.socket.sent[-1]["params"]
        self.assertEqual(
            params,
            {
                "expression": provider.PROBE,
                "contextId": 7,
                "returnByValue": True,
                "awaitPromise": False,
                "userGesture": False,
            },
        )
        self.assertEqual(self.socket.max_readers, 1)
        self.assertNotIn("private", repr(lease))
        self.assertFalse(hasattr(lease, "target_id"))

    async def test_ignores_unowned_nonpage_and_missing_endpoint_targets(self):
        base = self.host.inventory[0]
        self.host.inventory = [
            dict(base, id="unowned", owned=False),
            dict(base, id="worker", type="worker"),
            {"id": "no-endpoint", "type": "page", "owned": True},
        ]
        self.assertEqual(await self.transport.list_targets(), [])
        self.assertEqual(self.host.opened, [])

    async def test_rejects_excess_inventory_and_duplicate_identity_before_open(self):
        base = self.host.inventory[0]
        self.host.inventory = [dict(base, id=str(i)) for i in range(65)]
        await self.assert_failure(self.transport.list_targets(), "inventory_limit")
        self.host.inventory = [base, dict(base)]
        await self.assert_failure(self.transport.list_targets(), "invalid_inventory")
        self.assertEqual(self.host.opened, [])

    async def test_missing_or_ambiguous_main_default_context_fails_closed(self):
        for contexts in [
            [{"id": 8, "auxData": {"frameId": "child", "isDefault": True}}],
            [
                {"id": 7, "auxData": {"frameId": "main", "isDefault": True}},
                {"id": 10, "auxData": {"frameId": "main", "isDefault": True}},
            ],
        ]:
            socket = WebSocket(contexts)
            host = Host([socket])
            transport = transport_module.SteamCDPTransport(
                host, allow_target=owned, allow_expression=lambda expression: True
            )
            try:
                await self.assert_failure(transport.list_targets(), "context_unavailable")
            finally:
                await transport.dispose()

    async def test_exact_fixed_expressions_and_lookup_bounds_only(self):
        lease = await self.lease()
        for expression in [
            provider.PROBE,
            provider.FOCUS,
            provider.VISIBLE_APPS,
            provider._LOOKUP.replace("APP_ID", "1"),
            provider._LOOKUP.replace("APP_ID", "4294967295"),
        ]:
            await self.transport.evaluate(lease, expression)
        initial = len(self.socket.sent)
        for expression in [
            "window.appStore",
            provider.PROBE + "\n",
            provider._LOOKUP,
            provider._LOOKUP.replace("APP_ID", "0"),
            provider._LOOKUP.replace("APP_ID", "01"),
            provider._LOOKUP.replace("APP_ID", "4294967296"),
            provider._LOOKUP.replace("APP_ID", "1);alert(1)//"),
        ]:
            await self.assert_failure(
                self.transport.evaluate(lease, expression), "expression_not_allowed"
            )
        self.assertEqual(len(self.socket.sent), initial)

    async def test_host_expression_and_target_authority_fail_closed(self):
        lease = await self.lease()
        self.transport.allow_expression = lambda expression: False
        await self.assert_failure(
            self.transport.evaluate(lease, provider.PROBE), "expression_not_allowed"
        )
        self.host.inventory[0]["owned"] = False
        self.assertFalse(self.transport.is_current(lease))
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")

    async def test_explicit_revoke_catches_transient_attach_policy_loss_between_checks(self):
        permitted = {"value": True}
        self.transport.allow_target = lambda tab: permitted["value"]
        lease = await self.lease()
        revoke = getattr(self.transport, "revoke_all", None)
        self.assertTrue(callable(revoke), "synchronous ownership revocation hook missing")
        permitted["value"] = False
        self.assertIsNone(revoke())
        permitted["value"] = True
        self.assertFalse(self.transport.is_current(lease))
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")
        fresh = await self.lease()
        self.assertTrue(self.transport.is_current(fresh))
        self.assertFalse(self.transport.is_current(lease))

    async def test_explicit_revoke_rejects_pending_read_without_waiting_for_late_reply(self):
        lease = await self.lease()
        self.socket.defer = True
        pending = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        message = (await self.evaluations(1))[0]
        revoke = getattr(self.transport, "revoke_all", None)
        self.assertTrue(callable(revoke), "synchronous ownership revocation hook missing")
        revoke()
        await self.assert_failure(pending, "stale_context")
        self.assertEqual(self.transport.pending_count, 0)
        self.socket.reply(message["id"], {"result": {"type": "string", "value": "old grant"}})
        self.assertFalse(self.transport.is_current(lease))
        fresh = await self.lease()
        self.socket.defer = False
        self.assertTrue(self.transport.is_current(fresh))
        await self.transport.evaluate(fresh, provider.PROBE)
        self.assertFalse(self.transport.is_current(lease))

    async def test_explicit_revoke_invalidates_discovery_already_waiting_on_inventory(self):
        started, gate = asyncio.Event(), asyncio.Event()

        async def inventory():
            started.set()
            await gate.wait()
            return self.host.inventory

        self.host.tabs = inventory
        pending = asyncio.create_task(self.transport.list_targets())
        await started.wait()
        revoke = getattr(self.transport, "revoke_all", None)
        self.assertTrue(callable(revoke), "synchronous ownership revocation hook missing")
        revoke()
        gate.set()
        await self.assert_failure(pending, "stale_context")
        self.assertEqual(self.host.opened, [])
        fresh = await self.lease()
        self.assertTrue(self.transport.is_current(fresh))

    async def test_observed_policy_revocation_cannot_revive_old_lease_on_regrant(self):
        permitted = {"value": True}
        self.transport.allow_target = lambda tab: permitted["value"]
        lease = await self.lease()
        permitted["value"] = False
        self.assertFalse(self.transport.is_current(lease))
        permitted["value"] = True
        self.assertFalse(self.transport.is_current(lease))
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")
        fresh = await self.lease()
        self.assertTrue(self.transport.is_current(fresh))
        self.assertFalse(self.transport.is_current(lease))
        await self.transport.evaluate(fresh, provider.PROBE)
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")

    async def test_policy_revocation_rejects_pending_before_reply_and_never_revives_it(self):
        permitted = {"value": True}
        self.transport.allow_target = lambda tab: permitted["value"]
        lease = await self.lease()
        self.socket.defer = True
        pending = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        message = (await self.evaluations(1))[0]
        permitted["value"] = False
        self.assertFalse(self.transport.is_current(lease))
        permitted["value"] = True
        await self.assert_failure(pending, "stale_context")
        self.assertEqual(self.transport.pending_count, 0)
        self.socket.reply(message["id"], {"result": {"type": "string", "value": "late old grant"}})
        fresh = await self.lease()
        self.socket.defer = False
        self.assertTrue(self.transport.is_current(fresh))
        self.assertFalse(self.transport.is_current(lease))
        self.assertEqual(
            (await self.transport.evaluate(fresh, provider.PROBE))["result"]["type"], "object"
        )

    async def test_reader_observed_revocation_keeps_single_completion_and_can_regrant(self):
        permitted = {"value": True}
        self.transport.allow_target = lambda tab: permitted["value"]
        lease = await self.lease()
        self.socket.defer = True
        pending = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        message = (await self.evaluations(1))[0]
        permitted["value"] = False
        self.socket.reply(message["id"], {"result": {"type": "object", "value": {}}})
        await self.assert_failure(pending, "stale_context")
        self.assertFalse(self.socket.closed)
        permitted["value"] = True
        fresh = await self.lease()
        self.assertTrue(self.transport.is_current(fresh))
        self.assertFalse(self.transport.is_current(lease))

    async def test_policy_loss_after_write_await_is_latched(self):
        permitted = {"value": True}
        self.transport.allow_target = lambda tab: permitted["value"]
        lease = await self.lease()
        original_send = self.socket.send

        async def send(message):
            await original_send(message)
            if message["method"] == "Runtime.evaluate":
                permitted["value"] = False

        self.socket.send = send
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")
        permitted["value"] = True
        self.assertFalse(self.transport.is_current(lease))

    async def test_context_clear_invalidates_before_reply_completion(self):
        lease = await self.lease()

        def emit(message):
            self.socket.emit("Runtime.executionContextsCleared")
            self.socket.reply(message["id"], {"result": {"type": "object", "value": {}}})

        self.socket.on_evaluate = emit
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")
        self.assertFalse(self.transport.is_current(lease))

    async def test_navigation_during_read_discards_reply(self):
        lease = await self.lease()

        def emit(message):
            self.socket.emit("Page.frameNavigated", {"frame": {"id": "main", "url": "private"}})
            self.socket.reply(message["id"], {"result": {"type": "object", "value": {}}})

        self.socket.on_evaluate = emit
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")
        self.assertFalse(self.transport.is_current(lease))

    async def test_iframe_navigation_does_not_replace_main_context(self):
        lease = await self.lease()
        self.socket.emit("Page.frameNavigated", {"frame": {"id": "child", "parentId": "main"}})
        await self.transport.evaluate(lease, provider.PROBE)
        self.assertTrue(self.transport.is_current(lease))
        self.assertEqual(self.socket.sent[-1]["params"]["contextId"], 7)

    async def test_destroyed_and_reused_context_id_never_revives_old_lease(self):
        lease = await self.lease()
        self.socket.emit("Runtime.executionContextDestroyed", {"executionContextId": 7})
        self.socket.emit(
            "Runtime.executionContextCreated",
            {
                "context": {
                    "id": 7,
                    "uniqueId": "main-v2",
                    "auxData": {"frameId": "main", "isDefault": True},
                }
            },
        )
        await until(lambda: not self.transport.is_current(lease))
        new_lease = await self.lease()
        self.assertTrue(self.transport.is_current(new_lease))
        self.assertFalse(self.transport.is_current(lease))

    async def test_target_close_and_reconnect_reused_id_get_fresh_lease(self):
        lease = await self.lease()
        self.socket.queue.put_nowait(None)
        await until(lambda: not self.transport.is_current(lease))
        replacement = WebSocket()
        self.host.sockets.append(replacement)
        new_lease = await self.lease()
        self.assertTrue(self.transport.is_current(new_lease))
        self.assertFalse(self.transport.is_current(lease))
        self.assertTrue(self.socket.w.waited)

    async def test_removed_inventory_target_is_invalidated_and_closed(self):
        lease = await self.lease()
        self.host.inventory = []
        self.assertEqual(await self.transport.list_targets(), [])
        self.assertFalse(self.transport.is_current(lease))
        self.assertTrue(self.socket.closed)
        self.assertTrue(self.socket.w.waited)

    async def test_out_of_order_replies_match_unique_ids(self):
        lease = await self.lease()
        self.socket.defer = True
        first = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        second = asyncio.create_task(self.transport.evaluate(lease, provider.FOCUS))
        messages = await self.evaluations(2)
        self.socket.reply(messages[1]["id"], {"result": {"type": "string", "value": "second"}})
        self.socket.reply(messages[0]["id"], {"result": {"type": "string", "value": "first"}})
        self.assertEqual((await first)["result"]["value"], "first")
        self.assertEqual((await second)["result"]["value"], "second")
        self.assertNotEqual(messages[0]["id"], messages[1]["id"])

    async def test_cancel_removes_pending_and_late_reply_is_drained(self):
        lease = await self.lease()
        self.socket.defer = True
        task = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        message = (await self.evaluations(1))[0]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.transport.pending_count, 0)
        self.socket.reply(message["id"], {"result": {"type": "string", "value": "late"}})
        self.socket.defer = False
        self.assertEqual(
            (await self.transport.evaluate(lease, provider.PROBE))["result"]["type"], "object"
        )
        self.assertTrue(self.transport.is_current(lease))

    async def test_timeout_removes_pending_and_late_reply_cannot_satisfy_future_call(self):
        lease = await self.lease()
        self.socket.defer = True
        await self.assert_failure(
            self.transport.evaluate(lease, provider.PROBE), "transport_timeout"
        )
        message = (await self.evaluations(1))[0]
        self.assertEqual(self.transport.pending_count, 0)
        self.socket.reply(message["id"], {"result": {"type": "string", "value": "old"}})
        self.socket.defer = False
        result = await self.transport.evaluate(lease, provider.PROBE)
        self.assertIsInstance(result["result"]["value"], dict)

    async def test_default_message_limit_rejects_valid_json_below_host_allocation_cap(self):
        lease = await self.lease()
        empty = json.dumps({"id": 9999, "result": {"value": ""}})
        raw = json.dumps(
            {"id": 9999, "result": {"value": "a" * (2 * 1024 * 1024 + 64 * 1024 + 1 - len(empty))}}
        )
        self.assertLess(len(raw), 4 * 1024 * 1024)
        self.socket.queue.put_nowait(raw)
        await until(lambda: not self.transport.is_current(lease))
        self.assertTrue(self.socket.closed)

    async def test_pending_limit_rejects_without_writing(self):
        lease = await self.lease()
        self.socket.defer = True
        self.transport.max_pending = 1
        task = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        await self.evaluations(1)
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "pending_limit")
        self.assertEqual(len([m for m in self.socket.sent if m["method"] == "Runtime.evaluate"]), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_revalidates_identity_after_waiting_for_write_lock(self):
        lease = await self.lease()
        channel = next(iter(self.transport._channels.values()))
        await channel.write_lock.acquire()
        task = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        await asyncio.sleep(0)
        self.socket.emit("Runtime.executionContextsCleared")
        await until(lambda: not self.transport.is_current(lease))
        channel.write_lock.release()
        await self.assert_failure(task, "stale_context")
        self.assertEqual([m for m in self.socket.sent if m["method"] == "Runtime.evaluate"], [])

    async def test_revalidates_identity_after_write_await(self):
        lease = await self.lease()
        original = self.socket.send

        async def send(message):
            await original(message)
            if message["method"] == "Runtime.evaluate":
                self.host.inventory[0]["owned"] = False

        self.socket.send = send
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")

    async def test_preserves_exception_details_and_rejects_remote_object_refs(self):
        lease = await self.lease()
        self.socket.on_evaluate = lambda m: self.socket.reply(
            m["id"],
            {"result": {"type": "undefined"}, "exceptionDetails": {"text": "private exception"}},
        )
        result = await self.transport.evaluate(lease, provider.PROBE)
        self.assertEqual(result["exceptionDetails"], {"text": "private exception"})
        self.socket.on_evaluate = lambda m: self.socket.reply(
            m["id"], {"result": {"type": "object", "objectId": "private remote reference"}}
        )
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "invalid_result")

    async def test_cdp_errors_are_sanitized(self):
        lease = await self.lease()
        self.socket.on_evaluate = lambda m: self.socket.reply(
            m["id"], error={"message": "private URL and endpoint"}
        )
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "cdp_error")

    async def test_oversize_malformed_and_nonfinite_json_close_connection(self):
        for raw in [
            "x" * 4097,
            '{"id": NaN}',
            '{"id":1,"result":{"value":1e309}}',
            '{"id":1,"id":2}',
            '{"id":true,"result":{}}',
            '{"id":1,"result":{},"method":"Runtime.executionContextsCleared"}',
            "[]",
        ]:
            socket = WebSocket()
            transport = transport_module.SteamCDPTransport(
                Host([socket]),
                allow_target=owned,
                allow_expression=lambda expression: True,
                max_message_bytes=4096,
            )
            try:
                lease = (await transport.list_targets())[0]
                socket.queue.put_nowait(raw)
                await until(lambda: not transport.is_current(lease))
                self.assertTrue(socket.closed)
            finally:
                await transport.dispose()

    async def test_target_close_event_invalidates_pending_read(self):
        lease = await self.lease()
        self.socket.on_evaluate = lambda m: self.socket.emit(
            "Inspector.detached", {"reason": "private"}
        )
        await self.assert_failure(self.transport.evaluate(lease, provider.PROBE), "stale_context")
        self.assertFalse(self.transport.is_current(lease))

    async def test_dispose_is_idempotent_drains_reader_writer_and_pending(self):
        lease = await self.lease()
        self.socket.defer = True
        task = asyncio.create_task(self.transport.evaluate(lease, provider.PROBE))
        await self.evaluations(1)
        await self.transport.dispose()
        await self.transport.dispose()
        await self.assert_failure(task, "stale_context")
        self.assertEqual(self.transport.pending_count, 0)
        self.assertFalse(self.transport.is_current(lease))
        self.assertTrue(self.socket.closed)
        self.assertTrue(self.socket.w.waited)
        self.assertEqual(self.socket.readers, 0)
        self.assertEqual(self.transport._channels, {})
        await self.assert_failure(self.transport.list_targets(), "transport_disposed")

    async def test_fixed_provider_consumes_transport_result_without_metadata_leak(self):
        data = {
            "status": "empty",
            "focusedAppId": None,
            "focusedWindowId": None,
            "focusedOverlayAppId": None,
            "focusedOverlayPid": None,
        }

        def reply(message):
            value = (
                {"apps": False, "focus": True}
                if message["params"]["expression"] == provider.PROBE
                else data
            )
            self.socket.reply(message["id"], {"result": {"type": "object", "value": value}})

        self.socket.on_evaluate = reply
        adapter = provider.SteamReadProvider(self.transport)
        result = await adapter.read("focus")
        self.assertEqual(
            result, {"ok": True, "available": True, "source": "steam-focus-cache", "data": data}
        )
        self.assertNotIn("private", json.dumps(result))

    async def test_dispose_does_not_own_unrelated_work_after_transport_call_finishes(self):
        lease = await self.lease()
        started, release_caller = asyncio.Event(), asyncio.Event()

        async def blocked_send(message):
            started.set()
            await asyncio.Event().wait()

        self.socket.send = blocked_send

        async def caller():
            try:
                await self.transport.evaluate(lease, provider.PROBE)
            except transport_module.TransportFailure:
                pass
            await release_caller.wait()

        task = asyncio.create_task(caller())
        await started.wait()
        await self.transport.dispose()
        still_owned_by_caller = not task.done()
        release_caller.set()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(still_owned_by_caller)


class LoopCloseTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            transport_module, "production passive CDP transport is not implemented"
        )
        self.assertIsNotNone(provider, "production fixed-read provider is not implemented")

    def run_lifecycle(self, scenario):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scenario())
            self.assertEqual(asyncio.all_tasks(loop), set())
        finally:
            remaining = asyncio.all_tasks(loop)
            for task in remaining:
                task.cancel()
            if remaining:
                loop.run_until_complete(asyncio.gather(*remaining, return_exceptions=True))
            loop.close()

    def test_dispose_waits_for_late_open_and_closes_returned_writer(self):
        async def scenario():
            socket = WebSocket()
            host = Host([])
            started, cancelled, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def slow_open(endpoint):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await gate.wait()
                    return socket

            host.open = slow_open
            transport = transport_module.SteamCDPTransport(
                host, allow_target=owned, allow_expression=lambda expression: True, rpc_timeout=0.1
            )
            discovery = asyncio.create_task(transport.list_targets())
            await started.wait()
            dispose = asyncio.create_task(transport.dispose())
            await cancelled.wait()
            self.assertFalse(dispose.done())
            gate.set()
            await dispose
            await asyncio.gather(discovery, return_exceptions=True)
            self.assertTrue(socket.closed)
            self.assertTrue(socket.w.waited)
            self.assertEqual(transport.pending_count, 0)

        self.run_lifecycle(scenario)

    def test_dispose_drains_blocked_write_even_when_dispose_caller_cancelled(self):
        async def scenario():
            socket = WebSocket()
            host = Host([socket])
            transport = transport_module.SteamCDPTransport(
                host, allow_target=owned, allow_expression=lambda expression: True, rpc_timeout=0.01
            )
            lease = (await transport.list_targets())[0]
            started = asyncio.Event()
            original_send = socket.send

            async def blocked_send(message):
                if message["method"] == "Runtime.evaluate":
                    started.set()
                    await asyncio.Event().wait()
                await original_send(message)

            socket.send = blocked_send
            read = asyncio.create_task(transport.evaluate(lease, provider.PROBE))
            await started.wait()
            dispose = asyncio.create_task(transport.dispose())
            await asyncio.sleep(0)
            dispose.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await dispose
            await asyncio.gather(read, return_exceptions=True)
            self.assertEqual(transport.pending_count, 0)
            self.assertTrue(socket.closed)
            self.assertTrue(socket.w.waited)

        self.run_lifecycle(scenario)

    def test_dispose_cancels_pending_inventory_and_leaves_no_owned_tasks(self):
        async def scenario():
            host = Host()
            started = asyncio.Event()

            async def slow_inventory():
                started.set()
                await asyncio.Event().wait()

            host.tabs = slow_inventory
            transport = transport_module.SteamCDPTransport(
                host, allow_target=owned, allow_expression=lambda expression: True, rpc_timeout=0.01
            )
            discovery = asyncio.create_task(transport.list_targets())
            await started.wait()
            await transport.dispose()
            await asyncio.gather(discovery, return_exceptions=True)
            self.assertEqual(transport.pending_count, 0)
            self.assertEqual(host.opened, [])

        self.run_lifecycle(scenario)


if __name__ == "__main__":
    unittest.main()
