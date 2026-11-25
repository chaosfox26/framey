import asyncio
import copy
import inspect
import math

import fy_overlay
import fy_overlay_browser


MODES = ("embedded", "dashboard", "floating", "compact", "window")
POLL_INTERVAL = 0.5
ALIGN_INTERVAL = 1.0
FIND_TIMEOUT = 1.0
NATIVE_TIMEOUT = 1.0
SWITCH_TIMEOUT = 15.0
CLOSE_TIMEOUT = 1.0
VISIBILITY_ATTEMPTS = 5
VISIBILITY_INTERVAL = 0.05
ERROR = "Framey's separate panel is unavailable. Use the Steam panel or retry."
TARGET_ERROR = "Framey cannot place a page beside this app yet. Return to the launcher and retry with Steam open."
INPUT_NOTICE = (
    "Controller pointers may interrupt game controls while the floating panel is visible."
)


class _Failure(Exception):
    pass


class _Superseded(Exception):
    pass


class _TargetUnavailable(_Failure):
    pass


async def _await(value):
    return await value if inspect.isawaitable(value) else value


class Controller:
    def __init__(self, host, native_factory=None, browser_factory=None, native_runner=None):
        self.host = host
        self._native_factory = native_factory or fy_overlay.NativeOverlay
        self._browser_factory = browser_factory or fy_overlay_browser.OverlayBrowser
        self._runner = native_runner or asyncio.to_thread
        self.supported = (
            True if native_factory is not None else fy_overlay.NativeOverlay.supported()
        )
        self._open = getattr(host, "PANEL", False) is True
        self.mode, self.available, self.visible, self.error = (
            "embedded",
            self.supported,
            self._open,
            None,
        )
        self._native = self._browser = None
        self._native_task = self._cleanup = self._monitor = None
        self._browser_closing = None
        self._native_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._revision = 0
        self._pending = None
        self._requested = None
        self._busy = self._applying = self._closed = False
        self._wake = asyncio.Event()
        self._shown = self._interactive = self._ui_visible = None
        self._last_align = 0.0
        self._last_poll = 0.0
        self._anchor_x = 0.5
        self._launcher_width = 382
        self._launcher_flow = False
        self._launcher_task = None
        self._prepared_width = None

    def status(self):
        pending = not self._closed and (
            self._pending is not None
            or self._busy
            or self._applying
            or self._launcher_task is not None
        )
        result = {
            "mode": self.mode,
            "layout": "compact" if self.mode == "compact" else "window",
            "placements": {"compact": bool(self.supported), "window": bool(self.supported)},
            "target_scope": "steam",
            "available": bool(self.available),
            "active": bool(self.visible),
            "visible": bool(self.visible),
            "open": self._open,
            "pending": pending,
            "requested_mode": (
                self._pending["mode"]
                if self._pending
                else self._requested or ("compact" if self._launcher_task is not None else None)
            )
            if pending
            else None,
            "supported": bool(self.supported),
        }
        if self.error:
            result["error"] = self.error
        if self.mode == "floating":
            result["input_notice"] = INPUT_NOTICE
        return result

    def refresh_offer(self):

        if not self._closed and self.mode == "embedded":
            self.available = bool(self.supported)
        return self.status()

    async def _call(self, name, *args):
        fn = getattr(self.host, name, None)
        if not callable(fn):
            raise _Failure()
        result = await _await(fn(*args))
        if result is False or isinstance(result, dict) and result.get("ok") is False:
            raise _Failure()
        return result

    async def _native_call(self, name, *args, drain=False):
        async with self._native_lock:
            if self._closed and not drain:
                raise _Failure()
            if self._native_task is not None and not self._native_task.done():
                raise _Failure()
            native = self._native
            if native is None:
                raise _Failure()
            task = self._native_task = asyncio.create_task(
                self._runner(getattr(native, name), *args)
            )
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            if drain:
                result = await asyncio.shield(task)
            else:
                done, _ = await asyncio.wait((task,), timeout=NATIVE_TIMEOUT)
                if not done:
                    raise _Failure()
                result = task.result()
            if isinstance(result, dict) and (
                result.get("ok") is False or result.get("available") is False
            ):
                raise _Failure()
            if name in ("dashboard_visible", "own_visible", "steam_visible") and (
                type(result) is not bool or getattr(native, "last_error", None)
            ):
                raise _Failure()
            return result

    def _retire(self):
        if self._cleanup is None or self._cleanup.done():
            self._cleanup = asyncio.create_task(self._cleanup_native())
            self._cleanup.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        return self._cleanup

    async def _cleanup_native(self):

        task = self._native_task
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except Exception:
                pass
        for name, args in (("interactive", (False,)), ("hide_own", ()), ("close", ())):
            if self._native is not None:
                try:
                    await self._native_call(name, *args, drain=True)
                except Exception:
                    pass
        self._native = None
        self._prepared_width = None
        self._shown = self._interactive = None

    async def _browser_call(self, name, *args):
        if self._closed or self._browser is None:
            raise _Failure()
        result = await getattr(self._browser, name)(*args)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise _Failure()
        return result

    async def _close_browser(self):
        browser, self._browser = self._browser, None
        if browser is not None:
            self._browser_closing = asyncio.create_task(browser.close())
            self._browser_closing.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        if self._browser_closing is not None:
            await asyncio.wait((self._browser_closing,), timeout=CLOSE_TIMEOUT)

    async def _prepare_native(self, mode):
        if self._closed or not self.supported:
            raise _Failure()
        self._native = self._native_factory(enabled=True)
        await self._native_call("probe")
        if self._closed:
            raise _Failure()
        self._browser = self._browser_factory(self.host)
        target = await self._resolve_target() if mode == "window" else None
        if mode == "compact":
            await self._browser_call("ensure", "compact", self._launcher_width)
            self._prepared_width = self._launcher_width
        else:
            await self._browser_call("ensure")
            self._prepared_width = None
        if self._closed:
            raise _Failure()
        async with asyncio.timeout(FIND_TIMEOUT):
            while not await self._native_call("find_own"):
                await asyncio.sleep(0.1)
        if target is not None:
            await self._native_call("position_right", target)
        else:
            await self._position(mode)
        self._last_align = asyncio.get_running_loop().time()
        self.available = True

    async def _position(self, mode):
        if mode == "compact":
            await self._native_call("position_above_bar", self._anchor_x, self._launcher_width)
        elif mode == "window":
            await self._native_call("position_right", await self._resolve_target())
        else:
            await self._native_call("position_right")

    async def _resolve_target(self):
        if self._browser is None or not callable(getattr(self._browser, "active_target", None)):
            raise _TargetUnavailable()
        target = await self._browser.active_target()
        if (
            not isinstance(target, dict)
            or target.get("ok") is not True
            or target.get("target") != "steam"
        ):
            raise _TargetUnavailable()
        # Steam can hide this overlay while its popup windows change. App focus
        # identifies the target; positioning validates its geometry, and the
        # dashboard visibility check controls when Framey's window is shown.
        return fy_overlay.STEAM_KEY

    async def toggle_launcher(self, anchor_x=0.5):
        if (
            isinstance(anchor_x, bool)
            or not isinstance(anchor_x, (int, float))
            or not math.isfinite(anchor_x)
            or not 0 <= anchor_x <= 1
        ):
            return dict(self.status(), error="Launcher position is invalid.", code="bad_anchor")
        if self._closed:
            return dict(self.status(), error="Framey's panel is shutting down.", code="closed")
        if self._transition_busy():
            return dict(self.status(), error="A panel change is already pending.", code="busy")
        self._launcher_task = asyncio.current_task()
        try:
            result = await self._toggle_launcher(anchor_x)
        finally:
            self._launcher_task = None
        return dict(result, pending=self.status()["pending"])

    async def _toggle_launcher(self, anchor_x):
        self._launcher_flow = True
        self._anchor_x = anchor_x
        if self.mode == "compact" and self._open:
            await self._call("ui_panel_route", None)
            await self._call("panel", False)
            return self.status()
        width_reader = getattr(self.host, "launcher_width", None)
        try:
            width = await _await(width_reader()) if callable(width_reader) else 382
        except Exception:
            width = 382
        if self._closed:
            return dict(self.status(), error="Framey's panel is shutting down.", code="closed")
        self._launcher_width = width if type(width) is int and 382 <= width <= 662 else 382
        await self._call("panel", True)
        prepared = await self.request("compact")
        if prepared.get("error"):
            if not self._closed and prepared.get("code") != "busy":
                await self._call("panel", False)
            return prepared
        if self._pending is not None:
            self._pending["state"].update(tab="#launcher", open=True)
            applied = await self.apply_pending()
            if applied.get("error"):
                return applied
        await self._call("ui_panel_route", "#launcher")
        return self.status()

    async def launch(self, route):
        allowed = getattr(self.host, "panel_route_allowed", None)
        if not callable(allowed) or not await _await(allowed(route)):
            return dict(self.status(), error="This launcher page is unavailable.", code="bad_route")
        self._launcher_flow = True
        prepared = await self.request("window")
        if prepared.get("error"):
            return prepared
        if self._pending is not None:
            self._pending["state"].update(tab=route, open=True)
        else:
            await self._call("ui_panel_route", route)
        return dict(self.status(), ok=True)

    def _transition_busy(self):
        return bool(
            self._busy
            or self._applying
            or self._pending
            or self._launcher_task is not None
            and self._launcher_task is not asyncio.current_task()
            or self._cleanup is not None
            and not self._cleanup.done()
            or self._browser_closing is not None
            and not self._browser_closing.done()
        )

    async def request(self, arg=None):
        mode = arg.get("mode") if isinstance(arg, dict) else arg
        if mode not in MODES:
            return dict(
                self.status(),
                error="Choose Above bottom bar or Beside active app.",
                code="bad_mode",
            )
        if self._closed:
            return dict(self.status(), error="Framey's panel is shutting down.", code="closed")
        if self._transition_busy():
            return dict(self.status(), error="A panel change is already pending.", code="busy")
        resize_launcher = mode == "compact" and self._prepared_width != self._launcher_width
        if mode == self.mode and not resize_launcher:
            return self.status()
        self._busy, self._requested = True, mode
        try:
            state = await self._call("capture_panel_state")
            if self._closed:
                raise _Failure()
            state = dict(state) if isinstance(state, dict) else {}
            self._open = getattr(self.host, "PANEL", False) is True
            state["open"] = self._open
            if mode == "window" and self.mode != "embedded":
                await self._resolve_target()
            if mode != "embedded" and self.mode == "embedded":
                await self._prepare_native(mode)
            if self._closed:
                raise _Failure()
            self._pending = {"mode": mode, "state": state}
            self.error = None
            return self.status()
        except asyncio.CancelledError:
            if self.mode == "embedded":
                self._retire()
                await self._close_browser()
            raise
        except Exception as error:
            target_failed = isinstance(error, _TargetUnavailable)
            self.error, self.available = TARGET_ERROR if target_failed else ERROR, False
            if self.mode == "embedded":
                self._retire()
                await self._close_browser()
            return dict(
                self.status(),
                pending=False,
                requested_mode=None,
                code="target_unavailable" if target_failed else "unavailable",
            )
        finally:
            self._busy = False
            self._requested = None

    async def _publish_visibility(self, visible):
        if self._closed:
            return
        self.visible = bool(visible)
        if self._ui_visible != self.visible:
            await self._call("ui_panel_visibility", self.visible)
            self._ui_visible = self.visible

    async def _sync_native(self):
        async with self._sync_lock:
            while not self._closed and self.mode != "embedded":
                if await self._sync_pass():
                    return

    async def _sync_pass(self):
        revision, mode, opened = self._revision, self.mode, self._open
        self._last_poll = asyncio.get_running_loop().time()

        def check():
            if self._closed or revision != self._revision:
                raise _Superseded()

        async def native(name, *args):
            check()
            result = await self._native_call(name, *args)
            check()
            return result

        async def browser(name):
            check()
            await self._browser_call(name)
            check()

        try:
            dashboard = await native("dashboard_visible") if opened else False
            wanted = opened and (mode == "floating" or dashboard)
            now = asyncio.get_running_loop().time()
            if (
                wanted
                and dashboard
                and (self._shown is not True or now - self._last_align >= ALIGN_INTERVAL)
            ):
                if mode == "compact":
                    await native("position_above_bar", self._anchor_x, self._launcher_width)
                elif mode == "window":
                    target = await self._resolve_target()
                    check()
                    await native("position_right", target)
                else:
                    await native("position_right")
                self._last_align = now
            if not wanted and self._interactive is not False:
                await native("interactive", False)
                self._interactive = False
            if self._shown != wanted:
                await browser("show" if wanted else "hide")
                await native("show_own" if wanted else "hide_own")
                self._shown = wanted
            visible = await native("own_visible")
            if visible != wanted:
                async with asyncio.timeout(VISIBILITY_ATTEMPTS * VISIBILITY_INTERVAL):
                    for _ in range(VISIBILITY_ATTEMPTS):
                        await asyncio.sleep(VISIBILITY_INTERVAL)
                        check()
                        visible = await native("own_visible")
                        if visible == wanted:
                            break
                if visible != wanted:
                    raise _Failure()
            interactive = mode == "floating" and visible and not dashboard and opened
            if self._interactive != interactive:
                await native("interactive", interactive)
                self._interactive = interactive
            await self._publish_visibility(visible)
            check()
            return True
        except _Superseded:
            self._shown = self._interactive = None
            return False

    async def _notify(self):
        if self._closed:
            return
        try:
            await self._call("notify_page", "_core", "panel_mode", self.status())
        except Exception:
            pass

    async def _fallback(self, state):
        self.error, self.available = ERROR, False
        self.mode = "embedded"
        if self._launcher_flow:
            self._open = False
        state = dict(state, layout="window", open=self._open)
        self._revision += 1
        try:
            if not self._closed:
                await asyncio.wait_for(
                    self._call(
                        "switch_panel_surface", "none" if self._launcher_flow else "main", state
                    ),
                    SWITCH_TIMEOUT,
                )
                if not self._closed:
                    await self._publish_visibility(self._open)
        except Exception:
            self.visible = False
        self._retire()
        await self._close_browser()

    async def _save_mode(self, mode):
        if self._closed:
            raise _Failure()
        cfg = copy.deepcopy(self.host.settings())
        cfg.setdefault("prefs", {})["panel_mode"] = mode
        await _await(self.host.save(cfg))

    async def apply_pending(self):
        if self._pending is None or self._applying or self._closed:
            return self.status()
        pending = self._pending
        self._applying = True
        self._revision += 1
        self._open = getattr(self.host, "PANEL", False) is True
        state: dict[str, object] = dict(pending["state"], open=self._open)
        mode = pending["mode"]
        try:
            resize_launcher = (
                mode == self.mode == "compact" and self._prepared_width != self._launcher_width
            )
            if (self.mode == "compact") != (mode == "compact") or resize_launcher:
                state["layout"] = "compact" if mode == "compact" else "window"
                if self.mode != "embedded" and mode != "embedded":
                    await self._call(
                        "switch_panel_surface", "none" if self._launcher_flow else "main", state
                    )
                    if self._closed:
                        raise _Failure()
                    self.mode = "embedded"
                    await asyncio.wait_for(asyncio.shield(self._retire()), NATIVE_TIMEOUT)
                    await self._close_browser()
                    if self._closed:
                        raise _Failure()
                    if self._browser_closing is not None and not self._browser_closing.done():
                        raise _Failure()
                    await self._prepare_native(mode)
            if (self.mode == "embedded") != (mode == "embedded"):
                await asyncio.wait_for(
                    self._call(
                        "switch_panel_surface", "main" if mode == "embedded" else "panel", state
                    ),
                    SWITCH_TIMEOUT,
                )
            if self._closed:
                raise _Failure()
            if self.mode == "embedded" and mode != "embedded":
                self.visible = False
            self.mode = mode
            if mode == "embedded":
                self._retire()
                await self._close_browser()
                if self._closed:
                    raise _Failure()
                await self._publish_visibility(self._open)
            else:
                await self._sync_native()
            if self._closed:
                raise _Failure()
            await self._save_mode(mode)
            self.error = None
        except asyncio.CancelledError:
            await self._fallback(state)
            raise
        except Exception:
            await self._fallback(state)
        finally:
            self._pending = None
            self._applying = False
            if not self._closed:
                await self._notify()
            self._ensure_monitor()
        return self.status()

    def _ensure_monitor(self):
        if (
            not self._closed
            and self.mode != "embedded"
            and (self._monitor is None or self._monitor.done())
        ):
            self._monitor = asyncio.create_task(self._watch())
            self._monitor.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        self._wake.set()

    async def _watch(self):
        while not self._closed and self.mode != "embedded":
            self._wake.clear()
            if not self._open:
                await self._wake.wait()
                continue
            left = POLL_INTERVAL - (asyncio.get_running_loop().time() - self._last_poll)
            if left > 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), left)
                except asyncio.TimeoutError:
                    pass
                continue
            if not self._transition_busy():
                try:
                    await self._sync_native()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._fallback({"open": self._open})
                    await self._notify()
                    return
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def panel_changed(self, opened):
        self._open = opened is True
        self._revision += 1
        if self._pending is not None:
            self._pending["state"]["open"] = self._open
        if self._closed:
            return self.status()
        try:
            if self.mode == "embedded":
                await self._publish_visibility(self._open)
            elif not self._busy and not self._applying:
                await self._sync_native()
        except Exception:
            await self._fallback({"open": self._open})
            await self._notify()
        self._ensure_monitor()
        return self.status()

    set_open = panel_changed

    async def start(self):
        try:
            cfg = self.host.settings()
            mode = cfg.get("prefs", {}).get("panel_mode", "embedded")
        except Exception:
            mode = "embedded"
        if mode in MODES and mode != "embedded" and not self._closed:
            requested = await self.request(mode)
            if requested.get("error"):
                self.error = (
                    TARGET_ERROR if requested.get("code") == "target_unavailable" else ERROR
                )
                return self.status()
            return await self.apply_pending()
        return self.status()

    async def close(self):
        self._closed = True
        self._revision += 1
        self._pending = None
        self._requested = None
        self._wake.set()
        if self._monitor is not None:
            self._monitor.cancel()
            await asyncio.wait((self._monitor,), timeout=CLOSE_TIMEOUT)
        cleanup = self._retire()
        await self._close_browser()
        done, _ = await asyncio.wait((cleanup,), timeout=CLOSE_TIMEOUT)
        if not done:
            self.error = ERROR
        self.visible = False
        return self.status()


def get(host):
    controller = getattr(host, "panel_controller", None)
    if controller is None:
        controller = host.panel_controller = Controller(host)
    return controller


async def panel_status(host, arg=None):
    return get(host).refresh_offer()


async def panel_mode(host, arg=None):
    return await get(host).request(arg)


async def panel_launch(host, arg=None):
    route = arg.get("route") if isinstance(arg, dict) and set(arg) == {"route"} else None
    return await get(host).launch(route)


METHODS = {"panel_status": panel_status, "panel_mode": panel_mode, "panel_launch": panel_launch}
