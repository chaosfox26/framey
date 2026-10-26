import ctypes as c
import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fy_overlay as fo


class Export:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


def matrix(rows=None):
    out = fo.Matrix34()
    rows = rows or ((1, 0, 0, 2), (0, 1, 0, 3), (0, 0, 1, 4))
    for r in range(3):
        for k in range(4):
            out.m[r][k] = rows[r][k]
    return out


def rows(pose):
    return [list(row) for row in pose.m]


class Runtime:
    def __init__(self):
        self.calls = []
        self.writes = []
        self.width = 1.28
        self.pose = matrix()
        self.handles = {fo.OWN_KEY: 201, fo.STEAM_KEY: 101}
        self.keys = {201: fo.OWN_KEY, 101: fo.STEAM_KEY}
        self.errors = {}
        self.init_error = 0
        self.token = 17
        self.version = True
        self.table_error = 0
        self.null_table = False
        self.shut = 0
        self.init = 0
        self.dash = True
        self.visible = False
        self.after_width = None
        self.callbacks = []
        self.slots = (c.c_void_p * 80)()
        specs = (
            (0, c.c_int32, (c.c_char_p, c.POINTER(c.c_uint64)), self.find),
            (3, c.c_uint32, (c.c_uint64, c.POINTER(c.c_char), c.c_uint32, c.POINTER(c.c_int32)), self.key),
            (10, c.c_int32, (c.c_uint64, c.c_int32, c.c_bool), self.flag),
            (21, c.c_int32, (c.c_uint64, c.c_float), self.set_width),
            (22, c.c_int32, (c.c_uint64, c.POINTER(c.c_float)), self.get_width),
            (32, c.c_int32, (c.c_uint64, c.c_int32, c.POINTER(fo.Matrix34)), self.transform),
            (41, c.c_int32, (c.c_uint64,), self.show),
            (42, c.c_int32, (c.c_uint64,), self.hide),
            (43, c.c_bool, (c.c_uint64,), self.is_visible),
            (44, c.c_int32, (c.c_uint64, c.c_int32, fo.Vector2, c.POINTER(fo.Matrix34)), self.coordinates),
            (66, c.c_bool, (), self.dashboard),
        )
        for index, result, args, fn in specs:
            callback = c.CFUNCTYPE(result, *args)(fn)
            self.callbacks.append(callback)
            self.slots[index] = c.cast(callback, c.c_void_p).value
        self.VR_InitInternal2 = Export(self.initialize)
        self.VR_ShutdownInternal = Export(self.shutdown)
        self.VR_IsInterfaceVersionValid = Export(self.interface_version)
        self.VR_GetGenericInterface = Export(self.interface)

    def initialize(self, err, app, info):
        self.init += 1
        self.calls.append(("init", app, info))
        c.cast(err, c.POINTER(c.c_int32))[0] = self.init_error
        return self.token

    def shutdown(self):
        self.shut += 1

    def interface_version(self, version):
        self.calls.append(("version", version))
        return self.version

    def interface(self, name, err):
        self.calls.append(("interface", name))
        c.cast(err, c.POINTER(c.c_int32))[0] = self.table_error
        return None if self.null_table else c.addressof(self.slots)

    def find(self, key, handle):
        self.calls.append(("find", key))
        handle[0] = self.handles.get(key, 0)
        return self.errors.get("find", 0)

    def key(self, handle, buf, size, err):
        key = self.keys.get(handle, b"unowned")
        err[0] = self.errors.get("key", 0)
        c.memmove(buf, key + b"\0", min(size, len(key) + 1))
        return self.errors.get("key_length", len(key) + 1)

    def get_width(self, handle, width):
        self.calls.append(("width", handle))
        width[0] = self.width
        return self.errors.get("width", 0)

    def coordinates(self, handle, origin, center, pose):
        self.calls.append(("coordinates", handle, origin, tuple(center.v)))
        c.memmove(pose, c.byref(self.pose), c.sizeof(self.pose))
        return self.errors.get("coordinates", 0)

    def set_width(self, handle, width):
        self.writes.append(("set_width", handle, width))
        if self.after_width:
            self.after_width()
        return self.errors.get("set_width", 0)

    def transform(self, handle, origin, pose):
        self.writes.append(("transform", handle, origin, rows(pose.contents)))
        return self.errors.get("transform", 0)

    def show(self, handle):
        self.writes.append(("show", handle))
        self.visible = True
        return self.errors.get("show", 0)

    def hide(self, handle):
        self.writes.append(("hide", handle))
        self.visible = False
        return self.errors.get("hide", 0)

    def flag(self, handle, flag, enabled):
        self.writes.append(("flag", handle, flag, enabled))
        return self.errors.get("flag", 0)

    def is_visible(self, handle):
        self.calls.append(("visible", handle))
        return self.visible

    def dashboard(self):
        return self.dash


class NativeOverlayTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.loader = patch.object(fo.c, "CDLL", return_value=self.runtime).start()
        self.addCleanup(patch.stopall)
        self.native = fo.NativeOverlay(True)
        self.addCleanup(self.native.close)

    def test_lazy_disabled_and_supported_never_load(self):
        self.loader.assert_not_called()
        off = fo.NativeOverlay()
        self.assertFalse(off.probe()["available"])
        self.assertFalse(off.position_right()["ok"])
        with patch.object(fo.os.path, "isfile", return_value=True):
            self.assertTrue(fo.NativeOverlay.supported())
        self.loader.assert_not_called()
        self.assertEqual(self.runtime.init, 0)

    def test_exact_027_table_and_exports(self):
        self.assertTrue(self.native.probe()["available"])
        self.assertIn(("version", b"IVROverlay_027"), self.runtime.calls)
        self.assertIn(("interface", b"FnTable:IVROverlay_027"), self.runtime.calls)
        self.assertIn(("init", 2, None), self.runtime.calls)
        self.assertIs(self.runtime.VR_GetGenericInterface.restype, c.c_void_p)
        self.assertIs(self.runtime.VR_InitInternal2.restype, c.c_uint32)
        self.assertIs(self.runtime.VR_IsInterfaceVersionValid.restype, c.c_bool)
        self.assertIsNone(self.runtime.VR_ShutdownInternal.restype)
        self.assertEqual(c.sizeof(fo.Matrix34), 48)
        self.assertEqual(c.sizeof(fo.Vector2), 8)
        self.assertEqual(self.native.find_own(), 201)
        self.assertEqual(self.native.find_steam(), 101)

    def test_position_right_uses_center_standing_and_preserves_steam(self):
        before = rows(self.runtime.pose)
        self.assertEqual(self.native.position_right(), {"ok": True, "error": None})
        self.assertIn(("coordinates", 101, 1, (0.5, 0.5)), self.runtime.calls)
        width, transform = self.runtime.writes
        self.assertEqual(width[:2], ("set_width", 201))
        self.assertAlmostEqual(width[2], 0.542, places=6)
        self.assertEqual(transform[:3], ("transform", 201, 1))
        expected = [[1, 0, 0, 2.951], [0, 1, 0, 3], [0, 0, 1, 4]]
        for actual, want in zip(transform[3], expected):
            for a, b in zip(actual, want):
                self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(rows(self.runtime.pose), before)
        self.assertEqual(self.runtime.width, 1.28)
        self.assertTrue(all(item[1] == 201 for item in self.runtime.writes))

    def test_rotated_poses_offset_along_column_not_world_x(self):
        rotations = (
            ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
            ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
            ((0.5, -math.sqrt(0.5), 0.5), (0.5, math.sqrt(0.5), 0.5), (-math.sqrt(0.5), 0, math.sqrt(0.5))),
        )
        for rotation in rotations:
            with self.subTest(rotation=rotation):
                pose = matrix([list(row) + [r + 2] for r, row in enumerate(rotation)])
                before = rows(pose)
                width, got = fo._right_pose(pose, 1.28)
                self.assertAlmostEqual(width, 0.542, places=6)
                for r in range(3):
                    self.assertAlmostEqual(got.m[r][3], before[r][3] + 0.951 * rotation[r][0], places=6)
                    self.assertEqual(list(got.m[r])[:3], before[r][:3])
                self.assertEqual(rows(pose), before)

    def test_actual_steam_dashboard_scale_produces_world_meter_geometry(self):
        self.runtime.width = 2.6670000553131104
        self.runtime.pose = matrix(((0.32258060574531555, 0, -0.17917241156101227, 2),
                                    (0, 0.36899998784065247, 0, 3),
                                    (0.17917241156101227, 0, 0.32258060574531555, 4)))
        before = rows(self.runtime.pose)
        self.assertTrue(self.native.position_right()["ok"])
        width, transform = self.runtime.writes
        self.assertAlmostEqual(width[2], 0.41671458707, places=6)
        positioned = transform[3]
        delta = [positioned[r][3] - before[r][3] for r in range(3)]
        for actual, expected in zip(delta, (0.64727573712, 0, 0.35951930383)):
            self.assertAlmostEqual(actual, expected, places=6)
        for a in range(3):
            for b in range(3):
                dot = sum(positioned[r][a] * positioned[r][b] for r in range(3))
                self.assertAlmostEqual(dot, 1 if a == b else 0, places=6)
        projected_distance = sum(delta[r] * positioned[r][0] for r in range(3))
        self.assertAlmostEqual(projected_distance - 0.98412301006 / 2 - width[2] / 2, 0.04, places=6)
        self.assertEqual(rows(self.runtime.pose), before)
        self.assertTrue(all(write[1] == 201 for write in self.runtime.writes))

    def test_uniform_scale_width_compensation_preserves_physical_panel_geometry(self):
        rotation = ((0, -1, 0), (1, 0, 0), (0, 0, 1))
        baseline = matrix([list(row) + [r + 2] for r, row in enumerate(rotation)])
        expected_width, expected_pose = fo._right_pose(baseline, 1.28)
        for scale in (0.369, 2):
            with self.subTest(scale=scale):
                pose = matrix([[v * scale for v in row] + [r + 2] for r, row in enumerate(rotation)])
                before = rows(pose)
                width, positioned = fo._right_pose(pose, 1.28 / scale)
                self.assertAlmostEqual(width, expected_width, places=6)
                for actual_row, expected_row in zip(rows(positioned), rows(expected_pose)):
                    for actual, expected in zip(actual_row, expected_row):
                        self.assertAlmostEqual(actual, expected, places=6)
                self.assertEqual(rows(pose), before)

    def test_uniform_scale_fixed_raw_width_changes_width_without_scaling_world_gap(self):
        scale = 0.369
        pose = matrix(((0, -scale, 0, 2), (scale, 0, 0, 3), (0, 0, scale, 4)))
        width, positioned = fo._right_pose(pose, 1.28)
        self.assertAlmostEqual(width, 0.199998, places=6)
        self.assertAlmostEqual(positioned.m[1][3] - 3, 0.376159, places=6)
        self.assertAlmostEqual(positioned.m[1][3] - 3 - 1.28 * scale / 2 - width / 2, 0.04, places=6)

    def test_invalid_width_and_nonrigid_pose_never_write(self):
        for value in (0, -1, float("nan"), float("inf")):
            self.runtime.width = value
            self.assertFalse(self.native.position_right()["ok"])
        self.runtime.width = 1.28
        variants = (
            ((2, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            ((0.369, 0, 0, 0), (0, 0.370, 0, 0), (0, 0, 0.369, 0)),
            ((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)),
            ((1, 0.4, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            ((0.000001, 0.0000004, 0, 0), (0, 0.000001, 0, 0), (0, 0, 0.000001, 0)),
            ((0.000001, 0.0000002, 0, 0), (0, math.sqrt(0.96) * 0.000001, 0, 0), (0, 0, 0.000001, 0)),
            ((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            ((-0.369, 0, 0, 0), (0, 0.369, 0, 0), (0, 0, 0.369, 0)),
            ((float("nan"), 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            ((float("inf"), 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
            ((1, 0, 0, float("nan")), (0, 1, 0, 0), (0, 0, 1, 0)),
            ((1, 0, 0, float("inf")), (0, 1, 0, 0), (0, 0, 1, 0)),
        )
        for bad in variants:
            self.runtime.pose = matrix(bad)
            self.assertFalse(self.native.position_right()["ok"])
        self.assertEqual(self.runtime.writes, [])

    def test_float_overflow_in_position_is_rejected(self):
        self.runtime.width = 3e38
        self.runtime.pose = matrix(((1, 0, 0, 3e38), (0, 1, 0, 0), (0, 0, 1, 0)))
        self.assertFalse(self.native.position_right()["ok"])
        self.assertEqual(self.runtime.writes, [])

    def test_show_hide_and_interaction_only_touch_owned_overlay(self):
        self.assertTrue(self.native.show_own()["ok"])
        self.assertTrue(self.native.own_visible())
        self.assertTrue(self.native.hide_own()["ok"])
        self.assertFalse(self.native.own_visible())
        self.assertIsNone(self.native.last_error)
        self.assertTrue(self.native.interactive(True)["ok"])
        self.assertTrue(self.native.interactive(False)["ok"])
        self.assertEqual(self.runtime.writes, [("show", 201), ("hide", 201), ("flag", 201, 65536, True), ("flag", 201, 65536, False)])
        self.assertTrue(self.native.dashboard_visible())
        self.runtime.dash = False
        self.assertFalse(self.native.dashboard_visible())
        self.assertIsNone(self.native.last_error)

    def test_invalid_interaction_does_not_load(self):
        self.assertFalse(self.native.interactive("true")["ok"])
        self.loader.assert_not_called()

    def test_missing_keys_and_wrong_key_length_are_refused(self):
        self.runtime.keys[201] = fo.STEAM_KEY
        self.assertFalse(self.native.show_own()["ok"])
        self.runtime.keys[201] = fo.OWN_KEY
        self.runtime.errors["key_length"] = 200
        self.assertFalse(self.native.position_right()["ok"])
        self.runtime.errors.clear()
        self.runtime.handles[fo.OWN_KEY] = 0
        self.assertFalse(self.native.hide_own()["ok"])
        self.assertEqual(self.runtime.writes, [])

    def test_alias_and_changed_key_cannot_be_written(self):
        self.native.probe()
        with patch.object(self.native, "_find", return_value=101):
            self.assertFalse(self.native.position_right()["ok"])
        self.assertEqual(self.runtime.writes, [])
        self.runtime.after_width = lambda: self.runtime.keys.update({201: fo.STEAM_KEY})
        self.assertFalse(self.native.position_right()["ok"])
        self.assertEqual([entry[0] for entry in self.runtime.writes], ["set_width"])
        self.assertEqual(self.runtime.writes[0][1], 201)

    def test_recreated_owned_overlay_is_resolved_fresh(self):
        self.assertTrue(self.native.show_own()["ok"])
        self.runtime.keys[201] = b"other"
        self.runtime.handles[fo.OWN_KEY] = 202
        self.runtime.keys[202] = fo.OWN_KEY
        self.assertTrue(self.native.hide_own()["ok"])
        self.assertEqual(self.runtime.writes, [("show", 201), ("hide", 202)])

    def test_native_handles_preserve_all_64_bits(self):
        own, steam = (1 << 48) + 201, (1 << 49) + 101
        self.runtime.handles = {fo.OWN_KEY: own, fo.STEAM_KEY: steam}
        self.runtime.keys = {own: fo.OWN_KEY, steam: fo.STEAM_KEY}
        self.assertEqual(self.native.find_own(), own)
        self.assertEqual(self.native.find_steam(), steam)
        self.assertTrue(self.native.position_right()["ok"])
        self.assertTrue(self.native.show_own()["ok"])
        self.assertTrue(self.native.own_visible())
        self.assertTrue(all(write[1] == own for write in self.runtime.writes))
        self.assertIn(("coordinates", steam, 1, (0.5, 0.5)), self.runtime.calls)
        self.assertIn(("visible", own), self.runtime.calls)

    def test_native_errors_stop_positioning(self):
        for name in ("find", "key", "width", "coordinates", "set_width", "transform"):
            with self.subTest(error=name):
                self.runtime.writes.clear()
                self.runtime.errors = {name: 10}
                self.assertFalse(self.native.position_right()["ok"])
                if name in ("find", "key", "width", "coordinates"):
                    self.assertEqual(self.runtime.writes, [])
                if name == "set_width":
                    self.assertEqual(len(self.runtime.writes), 1)

    def test_visibility_failures_are_distinct_from_hidden(self):
        self.native.probe()
        def fail():
            raise OSError("/private/local/path secret")
        self.native._api["dashboard"] = fail
        self.assertFalse(self.native.dashboard_visible())
        self.assertEqual(self.native.last_error, "Native panel visibility is unavailable")
        self.assertNotIn("private", self.native.last_error)

    def test_wrong_abi_missing_slots_and_null_table_release_once(self):
        for invalid in ("version", "null", "slot", "error"):
            with self.subTest(invalid=invalid):
                runtime = Runtime()
                if invalid == "version":
                    runtime.version = False
                elif invalid == "null":
                    runtime.null_table = True
                elif invalid == "slot":
                    runtime.slots[44] = None
                else:
                    runtime.table_error = 105
                with patch.object(fo.c, "CDLL", return_value=runtime):
                    native = fo.NativeOverlay(True)
                    self.assertFalse(native.probe()["available"])
                    self.assertEqual(runtime.shut, 1)
                    native.close()
                    self.assertEqual(runtime.shut, 1)
                    self.assertEqual(native._api, {})
                    self.assertEqual(runtime.writes, [])
                    if invalid == "version":
                        self.assertFalse(any(call[0] == "interface" for call in runtime.calls))

    def test_init_failure_never_shuts_down_an_unowned_session(self):
        self.runtime.init_error = 108
        self.assertFalse(self.native.probe()["available"])
        self.assertEqual(self.runtime.shut, 0)
        self.assertFalse(any(call[0] == "interface" for call in self.runtime.calls))

    def test_zero_token_releases_successful_init(self):
        self.runtime.token = 0
        self.assertFalse(self.native.probe()["available"])
        self.assertEqual(self.runtime.shut, 1)

    def test_missing_export_and_load_error_return_fixed_messages(self):
        for name in ("VR_InitInternal2", "VR_ShutdownInternal", "VR_IsInterfaceVersionValid", "VR_GetGenericInterface"):
            with self.subTest(missing=name):
                runtime = Runtime()
                delattr(runtime, name)
                with patch.object(fo.c, "CDLL", return_value=runtime):
                    native = fo.NativeOverlay(True)
                    self.assertFalse(native.probe()["available"])
                    self.assertEqual(runtime.init, 0)
                    self.assertEqual(native.last_error, "Native panel connection is unavailable")
                    native.close()
        self.loader.side_effect = OSError("/home/private/lib-secret.so")
        self.assertFalse(self.native.probe()["available"])
        self.assertNotIn("private", self.native.last_error)

    def test_disabling_initialized_adapter_blocks_all_native_calls(self):
        self.assertTrue(self.native.probe()["available"])
        self.native.enabled = False
        self.assertFalse(self.native.show_own()["ok"])
        self.assertFalse(self.native.available)
        self.assertEqual(self.runtime.shut, 1)
        self.assertEqual(self.runtime.writes, [])

    def test_failed_probe_can_retry_without_reusing_stale_table(self):
        self.runtime.version = False
        self.assertFalse(self.native.probe()["available"])
        self.assertEqual(self.runtime.shut, 1)
        self.runtime.version = True
        self.assertTrue(self.native.probe()["available"])
        self.assertTrue(self.native.show_own()["ok"])
        self.assertEqual(self.runtime.init, 2)
        self.native.close()
        self.assertEqual(self.runtime.shut, 2)

    def test_duplicate_owner_and_closed_adapter_cannot_release_or_reload(self):
        self.assertTrue(self.native.probe()["available"])
        other = fo.NativeOverlay(True)
        self.assertFalse(other.probe()["available"])
        self.assertEqual(self.loader.call_count, 1)
        other.close()
        self.assertEqual(self.runtime.shut, 0)
        self.native.close()
        self.native.close()
        self.assertEqual(self.runtime.shut, 1)
        calls = list(self.runtime.calls)
        self.assertFalse(self.native.show_own()["ok"])
        self.assertFalse(self.native.dashboard_visible())
        self.assertFalse(self.native.probe()["available"])
        self.assertEqual(self.runtime.calls, calls)
        self.assertEqual(self.loader.call_count, 1)

    def test_platform_guard_precedes_loader(self):
        with patch.object(fo.sys, "platform", "win32"):
            self.assertFalse(self.native.probe()["available"])
        self.loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
