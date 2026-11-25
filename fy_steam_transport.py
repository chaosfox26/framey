"""Passive host-owned CDP transport for fixed cached Steam reads.

The host owns target authorization and the WebSocket network/frame primitive.
Framey's WS caps assembled messages at 4 MiB; this layer separately rejects
returned UTF-8 text above 2 MiB + 64 KiB before JSON parsing. It cannot reduce
allocation already performed inside host.WS.recv(). Connection cancellation
cleanup before a socket handle is returned remains the host primitive's duty;
returned/late sockets and their writers are tracked and drained here.
"""

import asyncio
import inspect
import json
import math
import re
import weakref
from urllib.parse import urlsplit

import fy_steam_data as provider

MAX_TARGETS = 64
MAX_MESSAGE_BYTES = provider.MAX_BYTES + 65536
MAX_ID = 2**53 - 1


class TransportFailure(Exception):
    """Only fixed codes cross this boundary, never CDP/endpoint error text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Lease:
    __slots__ = ("__weakref__",)

    def __repr__(self):
        return "<Steam context lease>"


def fixed_expression_allowed(expression):
    if type(expression) is not str:
        return False
    if expression in (provider.PROBE, provider.FOCUS, provider.VISIBLE_APPS):
        return True
    prefix, suffix = provider._LOOKUP.split("APP_ID")
    if not expression.startswith(prefix) or not expression.endswith(suffix):
        return False
    value = expression[len(prefix) : len(expression) - len(suffix)]
    return bool(re.fullmatch(r"[1-9][0-9]{0,9}", value)) and int(value) <= provider.MAX_APP_ID


def _integer(value):
    return type(value) is int and 0 < value <= MAX_ID


def _identifier(value):
    return (
        type(value) is str
        and 0 < len(value) <= 256
        and value.isascii()
        and not any(ord(char) <= 32 or ord(char) == 127 for char in value)
    )


def _endpoint(value):
    if type(value) is not str or not 0 < len(value) <= 2048 or not value.isascii():
        return False
    if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in ("ws", "wss")
            and bool(parsed.hostname)
            and bool(parsed.path)
            and (parsed.port is None or parsed.port > 0)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
        )
    except ValueError:
        return False


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate")
        value[key] = item
    return value


def _constant(_):
    raise ValueError("constant")


def _json_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number")
    return value


def _json_integer(text):
    if len(text.lstrip("-")) > 309:
        raise ValueError("number")
    value = int(text)
    if not math.isfinite(value):
        raise ValueError("number")
    return value


class _Channel:
    def __init__(self, owner, tab):
        self.owner = owner
        self.tab = tab
        self.target_id = tab["id"]
        self.endpoint = tab["webSocketDebuggerUrl"]
        self.ws = None
        self.open_task = self.reader = self.closing = None
        self.live = True
        self.authorized = True
        self.epoch = 0
        self.main_frame = None
        self.contexts = {}
        self.pending = {}
        self.next_id = 1
        self.write_lock = asyncio.Lock()

    def _revoke(self):
        if self.authorized:
            self.authorized = False
            self.invalidate()

    def _authority(self):
        if not self.live or self.owner._disposed or not self.authorized:
            return False
        if not self.owner._allowed_target(self.tab):
            # An observed denial permanently revokes every existing lease and
            # rejects pending reads before a later policy change can revive it.
            self._revoke()
            return False
        return True

    def grant(self, tab):
        # Only fresh, host-owned discovery can grant authority again. A dynamic
        # policy returning True during is_current/evaluate is insufficient.
        self.tab = tab
        if not self.live or self.owner._disposed or not self.owner._allowed_target(tab):
            self._revoke()
            raise TransportFailure("stale_context")
        if not self.authorized:
            self.invalidate()
            self.authorized = True

    def context(self):
        candidates = [
            (identifier, unique)
            for identifier, (frame, default, unique) in self.contexts.items()
            if frame == self.main_frame and default and self.main_frame is not None
        ]
        return candidates[0] if len(candidates) == 1 else None

    def snapshot(self):
        context = self.context()
        if not self._authority() or context is None:
            raise TransportFailure("context_unavailable")
        return self.epoch, self.main_frame, context

    def current(self, snapshot):
        return self._authority() and snapshot == (self.epoch, self.main_frame, self.context())

    def invalidate(self, *, clear=False):
        self.epoch += 1
        if clear:
            self.contexts.clear()
        for future, snapshot in self.pending.values():
            if snapshot is not None and not future.done():
                future.set_exception(TransportFailure("stale_context"))

    def disconnect(self):
        self.live = False
        self.invalidate(clear=True)
        for future, _ in self.pending.values():
            if not future.done():
                future.set_exception(TransportFailure("connection_closed"))

    async def start(self):
        self.open_task = asyncio.create_task(self.owner.host.WS.open(self.endpoint))
        try:
            self.ws = await asyncio.wait_for(asyncio.shield(self.open_task), self.owner.rpc_timeout)
            if not self._authority():
                raise TransportFailure("stale_context")
            self.reader = asyncio.create_task(self._read())
            await self.rpc("Runtime.enable", {})
            await self.rpc("Page.enable", {})
            tree = await self.rpc("Page.getFrameTree", {})
            frame = tree.get("frameTree", {}).get("frame", {})
            if not _identifier(frame.get("id")):
                raise TransportFailure("invalid_result")
            if self.main_frame is not None and self.main_frame != frame["id"]:
                raise TransportFailure("stale_context")
            self.main_frame = frame["id"]
            self.snapshot()
        except asyncio.CancelledError:
            await self.close()
            raise
        except TransportFailure:
            await self.close()
            raise
        except asyncio.TimeoutError:
            await self.close()
            raise TransportFailure("transport_timeout") from None
        except Exception:
            await self.close()
            raise TransportFailure("connection_closed") from None

    def _event(self, method, params):
        if method == "Runtime.executionContextsCleared":
            self.invalidate(clear=True)
        elif method == "Runtime.executionContextCreated":
            context = params.get("context")
            if type(context) is not dict or not _integer(context.get("id")):
                raise TransportFailure("invalid_message")
            auxiliary = context.get("auxData", {})
            if type(auxiliary) is not dict:
                raise TransportFailure("invalid_message")
            frame, default = auxiliary.get("frameId"), auxiliary.get("isDefault") is True
            unique = context.get("uniqueId")
            if unique is not None and not _identifier(unique):
                raise TransportFailure("invalid_message")
            identifier = context["id"]
            if identifier in self.contexts or default and frame == self.main_frame:
                self.invalidate()
            self.contexts[identifier] = (frame, default, unique)
            if len(self.contexts) > 256:
                raise TransportFailure("context_limit")
        elif method == "Runtime.executionContextDestroyed":
            identifier = params.get("executionContextId")
            if not _integer(identifier):
                raise TransportFailure("invalid_message")
            current = self.context()
            if current and current[0] == identifier:
                self.invalidate()
            self.contexts.pop(identifier, None)
        elif method == "Page.frameNavigated":
            frame = params.get("frame")
            if type(frame) is not dict or not _identifier(frame.get("id")):
                raise TransportFailure("invalid_message")
            if "parentId" not in frame:
                self.invalidate(clear=True)
                self.main_frame = frame["id"]
        elif method == "Page.navigatedWithinDocument":
            if params.get("frameId") == self.main_frame:
                self.invalidate()
        elif method == "Page.frameDetached":
            if params.get("frameId") == self.main_frame:
                self.invalidate(clear=True)
                self.main_frame = None
        elif method in ("Inspector.detached", "Target.detachedFromTarget"):
            self.disconnect()
        elif method == "Target.targetDestroyed" and params.get("targetId") == self.target_id:
            self.disconnect()

    async def _read(self):
        try:
            while self.live:
                raw = await self.ws.recv()
                if raw is None:
                    break
                if (
                    type(raw) is not str
                    or len(raw) > self.owner.max_message_bytes
                    or (len(raw.encode("utf-8")) > self.owner.max_message_bytes)
                ):
                    raise TransportFailure("message_limit")
                message = json.loads(
                    raw,
                    object_pairs_hook=_object,
                    parse_constant=_constant,
                    parse_float=_json_float,
                    parse_int=_json_integer,
                )
                if type(message) is not dict:
                    raise TransportFailure("invalid_message")
                if "id" in message:
                    identifier = message["id"]
                    if (
                        not _integer(identifier)
                        or "method" in message
                        or not any(key in message for key in ("result", "error"))
                    ):
                        raise TransportFailure("invalid_message")
                    entry = self.pending.get(identifier)
                    if entry is None:
                        continue  # cancelled/timed-out replies never get a new owner
                    future, snapshot = entry
                    if future.done():
                        continue
                    if snapshot is not None and not self.current(snapshot):
                        # Authority checks can synchronously revoke and complete
                        # this future along with every other pending lease read.
                        if not future.done():
                            future.set_exception(TransportFailure("stale_context"))
                    elif "error" in message:
                        future.set_exception(TransportFailure("cdp_error"))
                    elif type(message["result"]) is not dict:
                        future.set_exception(TransportFailure("invalid_result"))
                    else:
                        future.set_result(message["result"])
                else:
                    method, params = message.get("method"), message.get("params", {})
                    if (
                        type(method) is not str
                        or not 0 < len(method) <= 128
                        or type(params) is not dict
                    ):
                        raise TransportFailure("invalid_message")
                    self._event(method, params)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # raw JSON/CDP/endpoint errors never enter logs or public errors
        finally:
            self.disconnect()
            if self.ws is not None:
                try:
                    result = self.ws.close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass

    async def _exchange(self, identifier, method, params, future, snapshot):
        async with self.write_lock:
            if not self._authority() or snapshot is not None and not self.current(snapshot):
                raise TransportFailure("stale_context")
            await self.ws.send({"id": identifier, "method": method, "params": params})
            if not self._authority() or snapshot is not None and not self.current(snapshot):
                raise TransportFailure("stale_context")
        result = await future
        if not self._authority() or snapshot is not None and not self.current(snapshot):
            raise TransportFailure("stale_context")
        return result

    async def rpc(self, method, params, snapshot=None):
        if not self._authority():
            raise TransportFailure("stale_context")
        if len(self.pending) >= 8 or self.owner.pending_count >= self.owner.max_pending:
            raise TransportFailure("pending_limit")
        if self.next_id > MAX_ID:
            self.disconnect()
            raise TransportFailure("connection_closed")
        identifier = self.next_id
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future, snapshot
        try:
            return await asyncio.wait_for(
                self._exchange(identifier, method, params, future, snapshot), self.owner.rpc_timeout
            )
        except asyncio.TimeoutError:
            raise TransportFailure("transport_timeout") from None
        except TransportFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise TransportFailure("connection_closed") from None
        finally:
            self.pending.pop(identifier, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _close(self):
        self.disconnect()
        if self.open_task is not None:
            if not self.open_task.done():
                self.open_task.cancel()
            opened = await asyncio.gather(self.open_task, return_exceptions=True)
            if self.ws is None and not isinstance(opened[0], BaseException):
                self.ws = opened[0]
        if self.ws is not None:
            try:
                result = self.ws.close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.ws is not None:
            writer = getattr(self.ws, "w", None)
            wait_closed = getattr(writer, "wait_closed", None)
            if callable(wait_closed):
                try:
                    await asyncio.wait_for(wait_closed(), self.owner.rpc_timeout)
                except Exception:
                    pass

    async def close(self):
        if self.closing is None:
            self.closing = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self.closing)
        except asyncio.CancelledError:
            await asyncio.shield(self.closing)
            raise


class SteamCDPTransport:
    """Inject owner-checked host.tabs/WS and two synchronous host-only policies.

    No default-world fallback is assumed: Page.getFrameTree plus a matching
    auxData.isDefault execution context must establish the main-world context.
    """

    def __init__(
        self,
        host,
        *,
        allow_target,
        allow_expression,
        rpc_timeout=1.0,
        max_message_bytes=MAX_MESSAGE_BYTES,
        max_pending=16,
    ):
        if not callable(allow_target) or not callable(allow_expression):
            raise ValueError("policy")
        if (
            type(rpc_timeout) not in (int, float)
            or not math.isfinite(rpc_timeout)
            or not 0 < rpc_timeout <= 5
        ):
            raise ValueError("timeout")
        if type(max_message_bytes) is not int or not 1024 <= max_message_bytes <= MAX_MESSAGE_BYTES:
            raise ValueError("message_limit")
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError("pending_limit")
        self.host = host
        self.allow_target = allow_target
        self.allow_expression = allow_expression
        self.rpc_timeout = rpc_timeout
        self.max_message_bytes = max_message_bytes
        self.max_pending = max_pending
        self._disposed = False
        self._grant_generation = 0
        self._channels = {}
        self._leases = weakref.WeakKeyDictionary()
        self._discovery_lock = asyncio.Lock()
        self._operations = set()
        self._disposing = None

    @property
    def pending_count(self):
        return sum(len(channel.pending) for channel in self._channels.values())

    def _allowed_target(self, tab):
        try:
            return self.allow_target(tab) is True
        except Exception:
            return False

    def _check(self, generation=None):
        if self._disposed:
            raise TransportFailure("transport_disposed")
        if generation is not None and generation != self._grant_generation:
            raise TransportFailure("stale_context")

    def revoke_all(self):
        """Synchronously revoke grants before host ownership/policy changes.

        Existing leases and pending reads never regain authority. Discovery
        already in flight is stale too; only a newly invoked list_targets call
        may establish grants after the host permits those targets again.
        """
        self._grant_generation += 1
        for channel in self._channels.values():
            channel._revoke()

    def is_current(self, lease):
        if self._disposed or not isinstance(lease, _Lease):
            return False
        entry = self._leases.get(lease)
        return (
            entry is not None
            and self._channels.get(entry[0].target_id) is entry[0]
            and entry[0].current(entry[1])
        )

    async def list_targets(self):
        operation = asyncio.get_running_loop().create_future()
        self._operations.add(operation)
        generation = self._grant_generation
        try:
            self._check(generation)
            async with self._discovery_lock:
                self._check(generation)
                try:
                    inventory = await asyncio.wait_for(self.host.tabs(), self.rpc_timeout)
                except asyncio.TimeoutError:
                    raise TransportFailure("transport_timeout") from None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise TransportFailure("inventory_unavailable") from None
                self._check(generation)
                if type(inventory) not in (list, tuple):
                    raise TransportFailure("invalid_inventory")
                if len(inventory) > MAX_TARGETS:
                    raise TransportFailure("inventory_limit")
                selected = {}
                for tab in inventory:
                    if type(tab) is not dict:
                        raise TransportFailure("invalid_inventory")
                    if tab.get("type") != "page" or not self._allowed_target(tab):
                        continue
                    if not _identifier(tab.get("id")) or not _endpoint(
                        tab.get("webSocketDebuggerUrl")
                    ):
                        continue
                    if tab["id"] in selected:
                        raise TransportFailure("invalid_inventory")
                    selected[tab["id"]] = tab
                for identifier, channel in tuple(self._channels.items()):
                    tab = selected.get(identifier)
                    if (
                        tab is None
                        or not channel.live
                        or tab["webSocketDebuggerUrl"] != channel.endpoint
                    ):
                        await channel.close()
                        self._check(generation)
                        self._channels.pop(identifier, None)
                for identifier, tab in selected.items():
                    if identifier not in self._channels:
                        channel = _Channel(self, tab)
                        self._channels[identifier] = channel
                        try:
                            await channel.start()
                        except BaseException:
                            if self._channels.get(identifier) is channel:
                                self._channels.pop(identifier, None)
                            raise
                        self._check(generation)
                    else:
                        self._channels[identifier].grant(tab)
                leases = []
                for channel in self._channels.values():
                    snapshot = channel.snapshot()
                    lease = _Lease()
                    self._leases[lease] = channel, snapshot
                    leases.append(lease)
                return leases
        finally:
            self._operations.discard(operation)
            if not operation.done():
                operation.set_result(None)

    async def evaluate(self, lease, expression):
        operation = asyncio.get_running_loop().create_future()
        self._operations.add(operation)
        try:
            if not self.is_current(lease):
                raise TransportFailure("stale_context")
            try:
                permitted = self.allow_expression(expression) is True
            except Exception:
                permitted = False
            if not permitted or not fixed_expression_allowed(expression):
                raise TransportFailure("expression_not_allowed")
            channel, snapshot = self._leases[lease]
            result = await channel.rpc(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "contextId": snapshot[2][0],
                    "returnByValue": True,
                    "awaitPromise": False,
                    "userGesture": False,
                },
                snapshot,
            )
            if not self.is_current(lease):
                raise TransportFailure("stale_context")
            remote = result.get("result")
            if type(remote) is not dict or "objectId" in remote or "unserializableValue" in remote:
                raise TransportFailure("invalid_result")
            if "exceptionDetails" not in result and "value" not in remote:
                raise TransportFailure("invalid_result")
            if not self.is_current(lease):
                raise TransportFailure("stale_context")
            return result
        finally:
            self._operations.discard(operation)
            if not operation.done():
                operation.set_result(None)

    async def _dispose(self):
        await asyncio.gather(
            *(channel.close() for channel in tuple(self._channels.values())), return_exceptions=True
        )
        # Completion markers own only each transport call, never the caller's
        # task after it has moved on to unrelated work. Calls already have their
        # own bounded RPC/inventory waits and cancellation cleanup.
        operations = tuple(marker for marker in self._operations if not marker.done())
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)
        self._channels.clear()
        self._leases.clear()

    async def dispose(self):
        self._disposed = True
        for channel in self._channels.values():
            channel.disconnect()
        if self._disposing is None:
            self._disposing = asyncio.create_task(self._dispose())
        try:
            await asyncio.shield(self._disposing)
        except asyncio.CancelledError:
            await asyncio.shield(self._disposing)
            raise

    close = dispose
