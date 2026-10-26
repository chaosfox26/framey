import asyncio
import json
import re
import urllib.parse


KEY = "framey.panel"
OWNER = "FrameyOverlayBrowser/1"
DEADLINE = 8
MESSAGE_MAX = 65536
EVENT_MAX = 64
TARGET_MAX = 256
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class _Failure(Exception):
    def __init__(self, code):
        self.code = code


def _shared_target(target):
    if (
        not isinstance(target, dict)
        or target.get("title") != "SharedJSContext"
        or target.get("type") != "page"
    ):
        return False
    raw = target.get("url")
    if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 32 for c in raw):
        return False
    try:
        url = urllib.parse.urlsplit(raw)
        return (
            url.scheme == "https"
            and url.netloc == "steamloopback.host"
            and url.hostname == "steamloopback.host"
            and not url.username
            and not url.password
            and url.port is None
        )
    except ValueError:
        return False


def _websocket(target):
    identifier = target.get("id")
    raw = target.get("webSocketDebuggerUrl")
    if (
        not isinstance(identifier, str)
        or not _IDENTIFIER.fullmatch(identifier)
        or not isinstance(raw, str)
        or len(raw) > 512
        or any(ord(c) < 33 for c in raw)
    ):
        raise _Failure("endpoint_unavailable")
    try:
        url = urllib.parse.urlsplit(raw)
        if (
            url.scheme != "ws"
            or url.netloc != "127.0.0.1:8080"
            or url.path != "/devtools/page/" + identifier
            or url.query
            or url.fragment
        ):
            raise _Failure("endpoint_unavailable")
    except ValueError:
        raise _Failure("endpoint_unavailable") from None
    return raw


def _expression(action):

    config = json.dumps(
        {"owner": OWNER, "key": KEY, "action": action}, ensure_ascii=True, allow_nan=False
    )
    return """(async () => {
  const config = CONFIG;
  const failure = code => ({ok: false, code});
  if (location.origin !== 'https://steamloopback.host') return failure('context_changed');
  const owned = value => value && typeof value === 'object' &&
    value.owner === config.owner && value.key === config.key && value.popup !== window;
  let record = window.__frameyPanelHost;
  if (record !== undefined && !owned(record)) return failure('ownership_conflict');
  const manager = window.g_PopupManager;
  const factory = manager && manager.constructor && manager.constructor.CreatePopup;
  if (record && record.popup && record.popup.closed === true && window.__frameyPanelHost === record) {
    delete window.__frameyPanelHost;
    record = undefined;
  }
  if (config.action === 'probe') return {ok: true, available: typeof factory === 'function'};
  let created = false;
  if (!record && config.action !== 'ensure') {
    return config.action === 'show' ? failure('popup_missing') : {ok: true, absent: true};
  }
  if (!record) {
    if (typeof factory !== 'function') return failure('factory_unavailable');
    record = {owner: config.owner, key: config.key, popup: null, element: null,
              state: 'creating', pending: null, busy: false};
    window.__frameyPanelHost = record;
    created = true;
    record.pending = (async () => {
      try {
        const result = await factory.call(manager.constructor, 'Framey_Panel', {
          browserType: 1, eCreationFlags: 2, strVROverlayKey: config.key,
          title: 'Framey', dimensions: {width: 542, height: 607, left: 0, top: 0}
        });
        if (!result || !result.popup || result.popup === window) { record.state = 'failed'; return; }
        record.popup = result.popup;
        record.element = result.element;
        const native = record.popup.SteamClient && record.popup.SteamClient.Window;
        if (window.__frameyPanelHost !== record) {
          // Only this just-created reference is ours; never touch its replacement.
          if (native && typeof native.Close === 'function') await native.Close();
          record.state = 'failed'; return;
        }
        if (!native || typeof native.HideWindow !== 'function') {
          record.state = 'failed'; return;
        }
        await native.HideWindow();
        record.state = 'ready';
      } catch {
        record.state = 'failed';
      }
    })();
  }
  try {
    if (record.pending) await record.pending;
    if (window.__frameyPanelHost !== record || !owned(record)) return failure('ownership_conflict');
    if (config.action === 'close' && !record.popup) {
      delete window.__frameyPanelHost;
      return {ok: true, absent: true};
    }
    if (record.state === 'closing') return failure('popup_closing');
    if (record.state !== 'ready' && config.action !== 'close') return failure('creation_failed');
    if (created && config.action === 'ensure') return {ok: true, created: true, requested: 'hide'};
    if (record.busy) return failure('popup_busy');
    const native = record.popup && record.popup.SteamClient && record.popup.SteamClient.Window;
    const method = config.action === 'show' ? 'ShowWindow' : config.action === 'close' ? 'Close' : 'HideWindow';
    if (!native || typeof native[method] !== 'function') return failure('window_api_unavailable');
    record.busy = true;
    try {
      // Retain the native receiver. Completion acknowledges the command only;
      // visibility is verified separately through the compositor adapter.
      await native[method]();
      if (config.action === 'close' && window.__frameyPanelHost === record) {
        // Give asynchronous window destruction a bounded chance to complete.
        // A still-open popup keeps its ownership guard; no idle timer remains.
        record.state = 'closing';
        for (let attempt = 0; attempt < 10 && window.__frameyPanelHost === record &&
             record.popup.closed !== true; attempt++) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        if (record.popup.closed === true && window.__frameyPanelHost === record) {
          delete window.__frameyPanelHost;
        }
      }
      return {ok: true, created, requested: config.action === 'ensure' ? 'hide' : config.action};
    } finally {
      record.busy = false;
    }
  } catch {
    return failure('window_command_failed');
  }
})()""".replace("CONFIG", config, 1)


class OverlayBrowser:
    def __init__(self, host):
        self.host = host
        self._lock = asyncio.Lock()
        self._serial = 0

    async def _run(self, action):
        ws = None
        try:
            async with asyncio.timeout(DEADLINE):
                async with self._lock:
                    if self.host.cdp_owner_ok() is not True:
                        raise _Failure("debugger_unavailable")
                    targets = await self.host.tabs()
                    if not isinstance(targets, list) or len(targets) > TARGET_MAX:
                        raise _Failure("context_unavailable")
                    matches = [target for target in targets if _shared_target(target)]
                    if len(matches) != 1:
                        raise _Failure("context_unavailable")
                    endpoint = _websocket(matches[0])
                    ws = await self.host.WS.open(endpoint)
                    self._serial += 1
                    request_id = self._serial
                    await ws.send(
                        {
                            "id": request_id,
                            "method": "Runtime.evaluate",
                            "params": {
                                "expression": _expression(action),
                                "returnByValue": True,
                                "awaitPromise": True,
                                "userGesture": True,
                            },
                        }
                    )
                    for _ in range(EVENT_MAX):
                        raw = await ws.recv()
                        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MESSAGE_MAX:
                            raise _Failure("debugger_reply_failed")
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            raise _Failure("debugger_reply_failed")
                        if type(message.get("id")) is not int or message["id"] != request_id:
                            continue
                        result = message.get("result")
                        if (
                            "error" in message
                            or not isinstance(result, dict)
                            or "exceptionDetails" in result
                        ):
                            raise _Failure("window_command_failed")
                        remote = result.get("result")
                        value = remote.get("value") if isinstance(remote, dict) else None
                        if not isinstance(value, dict) or type(value.get("ok")) is not bool:
                            raise _Failure("debugger_reply_failed")
                        if value["ok"] is not True:
                            code = value.get("code")
                            allowed = {
                                "context_changed",
                                "ownership_conflict",
                                "popup_missing",
                                "factory_unavailable",
                                "creation_failed",
                                "popup_busy",
                                "window_api_unavailable",
                                "window_command_failed",
                                "popup_closing",
                            }
                            raise _Failure(
                                code
                                if isinstance(code, str) and code in allowed
                                else "window_command_failed"
                            )
                        answer = {"ok": True, "key": KEY}
                        if action == "probe":
                            if type(value.get("available")) is not bool:
                                raise _Failure("debugger_reply_failed")
                            answer["available"] = value["available"]
                        elif value.get("absent") is True:
                            if action not in ("hide", "close"):
                                raise _Failure("debugger_reply_failed")
                            answer["absent"] = True
                        else:
                            expected = "hide" if action == "ensure" else action
                            if (
                                value.get("requested") != expected
                                or type(value.get("created")) is not bool
                            ):
                                raise _Failure("debugger_reply_failed")
                            answer["created"] = value["created"]
                            answer["requested"] = value["requested"]
                        return answer
                    raise _Failure("debugger_reply_failed")
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return self._failure("timeout")
        except _Failure as error:
            return self._failure(error.code)
        except Exception:
            return self._failure("debugger_unavailable")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    @staticmethod
    def _failure(code):
        return {
            "ok": False,
            "error": "Framey's separate panel is unavailable; use the Steam panel.",
            "code": code,
        }

    async def ensure(self):
        return await self._run("ensure")

    async def probe(self):
        result = await self._run("probe")
        if not result.get("ok"):
            result["available"] = False
        return result

    async def show(self):
        return await self._run("show")

    async def hide(self):
        return await self._run("hide")

    async def close(self):
        return await self._run("close")
