import ctypes as c
import math
import os
import sys
import threading


LIBRARY = "/opt/steamvr/bin/linuxarm64/libopenvr_api.so"
VERSION = b"IVROverlay_027"
OWN_KEY = b"framey.panel"
STEAM_KEY = b"valve.steam.gamepadui.main"
STANDING = 1
WIDTH_RATIO = 542 / 1280
GAP = 0.04
INTERACTIVE = 65536


class Matrix34(c.Structure):
    _fields_ = [("m", (c.c_float * 4) * 3)]


class Vector2(c.Structure):
    _fields_ = [("v", c.c_float * 2)]


_I = c.c_int32
_H = c.c_uint64
_P = c.POINTER
_SIGNATURES = {
    "find": (0, _I, (c.c_char_p, _P(_H))),
    "key": (3, c.c_uint32, (_H, _P(c.c_char), c.c_uint32, _P(_I))),
    "flag": (10, _I, (_H, _I, c.c_bool)),
    "set_width": (21, _I, (_H, c.c_float)),
    "width": (22, _I, (_H, _P(c.c_float))),
    "transform": (32, _I, (_H, _I, _P(Matrix34))),
    "show": (41, _I, (_H,)),
    "hide": (42, _I, (_H,)),
    "visible": (43, c.c_bool, (_H,)),
    "coordinates": (44, _I, (_H, _I, Vector2, _P(Matrix34))),
    "dashboard": (66, c.c_bool, ()),
}
_SESSION = threading.RLock()
_OWNER = None


class _Failure(Exception):
    pass


def _right_pose(pose, width):
    if not math.isfinite(width) or width <= 0:
        raise _Failure("Steam panel size is unavailable")
    values = [[float(pose.m[r][k]) for k in range(4)] for r in range(3)]
    if not all(math.isfinite(v) for row in values for v in row):
        raise _Failure("Steam panel pose is invalid")
    lengths = [math.sqrt(sum(values[r][k] ** 2 for r in range(3))) for k in range(3)]
    scale = sum(lengths) / 3
    if not math.isfinite(scale) or scale <= 0 or max(lengths) - min(lengths) > scale * 0.001:
        raise _Failure("Steam panel pose is invalid")
    rotation = [[values[r][k] / scale for k in range(3)] for r in range(3)]
    for a in range(3):
        for b in range(a, 3):
            dot = sum(rotation[r][a] * rotation[r][b] for r in range(3))
            if abs(dot - (1 if a == b else 0)) > 0.02:
                raise _Failure("Steam panel pose is invalid")
    a, b, d = rotation
    det = (
        a[0] * (b[1] * d[2] - b[2] * d[1])
        - a[1] * (b[0] * d[2] - b[2] * d[0])
        + a[2] * (b[0] * d[1] - b[1] * d[0])
    )
    if abs(det - 1) > 0.02:
        raise _Failure("Steam panel pose is invalid")
    world_width = width * scale
    own_width = c.c_float(world_width * WIDTH_RATIO).value
    offset = world_width / 2 + own_width / 2 + GAP
    out = Matrix34()
    for r in range(3):
        for k in range(4):
            out.m[r][k] = values[r][3] + offset * rotation[r][0] if k == 3 else rotation[r][k]
    if (
        not math.isfinite(own_width)
        or own_width <= 0
        or not all(math.isfinite(v) for row in out.m for v in row)
    ):
        raise _Failure("Steam panel pose is invalid")
    return own_width, out


class NativeOverlay:
    def __init__(self, enabled=False):
        self.enabled = enabled is True
        self.available = False
        self.error = (
            "Native panel is disabled" if not self.enabled else "Native panel has not been checked"
        )
        self._closed = False
        self._initialized = False
        self._lib = None
        self._api = {}
        self._shutdown = None

    @staticmethod
    def supported():
        return sys.platform == "linux" and c.sizeof(c.c_void_p) == 8 and os.path.isfile(LIBRARY)

    @property
    def last_error(self):
        return self.error

    def _release(self):
        global _OWNER
        self.available = False
        self._api.clear()
        try:
            if self._initialized and self._shutdown is not None:
                self._shutdown()
        except Exception:
            pass
        finally:
            self._initialized = False
            self._shutdown = None
            self._lib = None
            if _OWNER is self:
                _OWNER = None

    def probe(self):
        global _OWNER
        with _SESSION:
            if self._closed:
                self.error = "Native panel is closed"
            elif not self.enabled:
                self._release()
                self.error = "Native panel is disabled"
            elif self.available:
                return {"available": True, "error": None}
            else:
                try:
                    if (
                        sys.platform != "linux"
                        or c.sizeof(c.c_void_p) != 8
                        or c.sizeof(Matrix34) != 48
                        or c.sizeof(Vector2) != 8
                    ):
                        raise _Failure("Native panel ABI is unavailable")
                    if _OWNER is not None and _OWNER is not self:
                        raise _Failure("Native panel connection is already in use")
                    lib = c.CDLL(LIBRARY, mode=os.RTLD_LOCAL | os.RTLD_NOW)
                    exports = (
                        ("VR_InitInternal2", c.c_uint32, [_P(_I), _I, c.c_char_p]),
                        ("VR_ShutdownInternal", None, []),
                        ("VR_IsInterfaceVersionValid", c.c_bool, [c.c_char_p]),
                        ("VR_GetGenericInterface", c.c_void_p, [c.c_char_p, _P(_I)]),
                    )
                    for name, restype, argtypes in exports:
                        fn = getattr(lib, name)
                        fn.restype, fn.argtypes = restype, argtypes
                    err = _I(0)
                    token = lib.VR_InitInternal2(c.byref(err), 2, None)
                    if err.value:
                        raise _Failure("SteamVR connection is unavailable")
                    self._initialized = True
                    self._lib, self._shutdown, _OWNER = lib, lib.VR_ShutdownInternal, self
                    if not token:
                        raise _Failure("SteamVR connection is unavailable")
                    if not lib.VR_IsInterfaceVersionValid(VERSION):
                        raise _Failure("Native panel ABI is unavailable")
                    table = lib.VR_GetGenericInterface(b"FnTable:" + VERSION, c.byref(err))
                    if err.value or not table:
                        raise _Failure("Native panel ABI is unavailable")
                    slots = c.cast(table, _P(c.c_void_p))
                    for name, (index, restype, argtypes) in _SIGNATURES.items():
                        if not slots[index]:
                            raise _Failure("Native panel ABI is unavailable")
                        self._api[name] = c.CFUNCTYPE(restype, *argtypes)(slots[index])
                    self.available, self.error = True, None
                except _Failure as e:
                    self._release()
                    self.error = str(e)
                except Exception:
                    self._release()
                    self.error = "Native panel connection is unavailable"
            return {"available": self.available, "error": self.error}

    def close(self):
        with _SESSION:
            self._closed = True
            self._release()
            self.error = "Native panel is closed"

    def _ready(self):
        if not self.probe()["available"]:
            raise _Failure(self.error)

    def _key_is(self, handle, key):
        buf, err = c.create_string_buffer(128), _I(0)
        n = self._api["key"](handle, buf, len(buf), c.byref(err))
        return not err.value and n == len(key) + 1 and buf.value == key

    def _find(self, key):
        handle = _H(0)
        if (
            self._api["find"](key, c.byref(handle))
            or not handle.value
            or not self._key_is(handle.value, key)
        ):
            raise _Failure(
                "Framey panel is unavailable" if key == OWN_KEY else "Steam panel is unavailable"
            )
        return handle.value

    def _lookup(self, key):
        with _SESSION:
            try:
                self._ready()
                handle = self._find(key)
                self.error = None
                return handle
            except _Failure as e:
                self.error = str(e)
            except Exception:
                self.error = "Native panel lookup failed"
            return None

    def find_own(self):
        return self._lookup(OWN_KEY)

    def find_steam(self):
        return self._lookup(STEAM_KEY)

    def _own(self, handle):
        if not self._key_is(handle, OWN_KEY):
            raise _Failure("Framey panel changed before the operation")

    def _result(self, operation):
        with _SESSION:
            try:
                self._ready()
                operation()
                self.error = None
                return {"ok": True, "error": None}
            except _Failure as e:
                self.error = str(e)
            except Exception:
                self.error = "Native panel operation failed"
            return {"ok": False, "error": self.error}

    def position_right(self):
        def operation():
            own, steam = self._find(OWN_KEY), self._find(STEAM_KEY)
            if own == steam:
                raise _Failure("Framey panel ownership is invalid")
            width, pose = c.c_float(), Matrix34()
            if self._api["width"](steam, c.byref(width)):
                raise _Failure("Steam panel size is unavailable")
            center = Vector2((c.c_float * 2)(0.5, 0.5))
            if self._api["coordinates"](steam, STANDING, center, c.byref(pose)):
                raise _Failure("Steam panel pose is unavailable")
            own_width, positioned = _right_pose(pose, width.value)
            self._own(own)
            if self._api["set_width"](own, own_width):
                raise _Failure("Framey panel size could not be set")
            self._own(own)
            if self._api["transform"](own, STANDING, c.byref(positioned)):
                raise _Failure("Framey panel position could not be set")

        return self._result(operation)

    def _action(self, name, *args):
        def operation():
            own = self._find(OWN_KEY)
            self._own(own)
            if self._api[name](own, *args):
                raise _Failure("Framey panel operation was refused")

        return self._result(operation)

    def show_own(self):
        return self._action("show")

    def hide_own(self):
        return self._action("hide")

    def interactive(self, enabled):
        if type(enabled) is not bool:
            return {"ok": False, "error": "Native panel interaction setting is invalid"}
        return self._action("flag", INTERACTIVE, enabled)

    def _visible(self, own):
        with _SESSION:
            try:
                self._ready()
                visible = bool(
                    self._api["visible"](self._find(OWN_KEY)) if own else self._api["dashboard"]()
                )
                self.error = None
                return visible
            except _Failure as e:
                self.error = str(e)
            except Exception:
                self.error = "Native panel visibility is unavailable"
            return False

    def dashboard_visible(self):
        return self._visible(False)

    def own_visible(self):
        return self._visible(True)
