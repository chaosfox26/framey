import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_state as fs


def rows(*pairs):
    return list(pairs)


def fresh():
    return fs.defaults()


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.dir = Path(self._t.name)
        self.path = self.dir / "cfg" / "settings.json"

    def write_raw(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())

    def stray(self):
        return [p.name for p in self.path.parent.iterdir() if p.name != self.path.name]


class LoadValidation(TmpCase):
    def test_missing_file_gives_defaults_not_repaired(self):
        s = fs.load(self.path)
        self.assertFalse(s["repaired"])
        self.assertEqual({k: v for k, v in s.items() if k != "repaired"}, fs.defaults())

    def test_defaults_shape(self):
        d = fs.defaults()
        self.assertEqual(d["v"], 1)
        self.assertEqual(d["modules"], {"order": [], "sort": "custom", "hidden": [], "frozen": {}, "state": {}})
        self.assertEqual(d["prefs"]["workers_max"], 3)
        self.assertEqual(d["prefs"]["channel"], "stable")
        self.assertIs(d["prefs"]["safe"], False)
        self.assertEqual(d["prefs"]["dev"], {"enabled": False, "hot": [], "hot_until": 0, "targets": {}})
        self.assertEqual(d["updates"]["seen"], {"loader": "", "plugins": {}})

    def test_whole_file_garbage_gives_defaults_and_repaired(self):
        for data in (b"", b"not json", b'{"v":1,"updates":{"last":NaN}}', b'{"prefs":{"workers_max":Infinity}}', b"[]", b"7", b"null", b"\xff\xfe\x00", b"[" * 100000 + b"]" * 100000, b'{"a":'):
            with self.subTest(data=data[:20]):
                self.write_raw(data)
                s = fs.load(self.path)
                self.assertTrue(s["repaired"])
                self.assertEqual(s["modules"]["order"], [])

    def test_oversize_file_is_not_trusted(self):
        self.write_raw(b'{"disabled":[' + b'"a",' * 70000 + b'"a"]}')
        s = fs.load(self.path)
        self.assertTrue(s["repaired"])
        self.assertEqual(s["disabled"], [])

    def test_each_bad_part_is_dropped_alone(self):
        good = {
            "disabled": ["x"],
            "modules": {"order": ["a", "b"], "sort": "name", "hidden": ["a"], "frozen": {"a": {"at": 5, "version": "1.0.0"}}, "state": {"a": {"ver": "1.0.0", "data_v": 2, "ok_at": 9, "fails": 1, "last_error": "boom"}}},
            "prefs": {"autocheck": True, "notify_loader": True, "notify_plugins": True, "channel": "prerelease", "lang": "pt-BR", "safe": True, "workers_max": 3, "file_roots": ["/home/deck/Music"], "dev": {"enabled": True, "hot": ["a"], "hot_until": 77, "targets": {"store": "steamloopback"}}},
            "updates": {"last": 3, "next_ok": 4, "fails": 2, "loader": {"tag": "v1", "state": "update"}, "plugins": {"a": {"latest": "1.1.0", "state": "update"}}, "seen": {"loader": "v1", "plugins": {"a": "1.1.0"}}},
        }
        self.write_raw(good)
        base = fs.load(self.path)
        self.assertFalse(base["repaired"])
        for section in ("disabled", "modules", "prefs", "updates"):
            for junk in ("text", 5, None, [], [1, 2], True):
                with self.subTest(section=section, junk=junk):
                    broken = json.loads(json.dumps(good))
                    broken[section] = junk
                    self.write_raw(broken)
                    s = fs.load(self.path)
                    for other in ("disabled", "modules", "prefs", "updates"):
                        if other != section:
                            self.assertEqual(s[other], base[other])
                    if not (section == "disabled" and junk == []):
                        self.assertTrue(s["repaired"])

    def test_each_field_garbage_keeps_its_siblings(self):
        garbage = ["x", -1, 10**30, 1.5, float("nan"), None, [], {}, "\x00", "é" * 500, [[1]], {"a": 1}]
        for group, key, expect in (
            ("modules", "sort", "custom"),
            ("modules", "order", []),
            ("modules", "hidden", []),
            ("modules", "frozen", {}),
            ("modules", "state", {}),
            ("prefs", "channel", "stable"),
            ("prefs", "lang", ""),
            ("prefs", "workers_max", 3),
            ("prefs", "autocheck", False),
            ("prefs", "safe", False),
            ("prefs", "file_roots", []),
        ):
            for g in garbage:
                with self.subTest(key=key, garbage=repr(g)[:20]):
                    raw = {"modules": {"order": ["keep"]}, "prefs": {"notify_loader": True}}
                    raw[group][key] = g
                    s, bad = fs.validate(raw)
                    self.assertEqual(s[group][key], expect)
                    self.assertTrue(s["prefs"]["notify_loader"])
                    if key != "order":
                        self.assertEqual(s["modules"]["order"], ["keep"])

    def test_workers_max_range(self):
        for v, want, bad in ((1, 1, False), (3, 3, False), (4, 3, True), (0, 3, True), (5, 3, True), (True, 3, True), (2.0, 3, True), ("3", 3, True)):
            with self.subTest(v=v):
                s, b = fs.validate({"prefs": {"workers_max": v}})
                self.assertEqual((s["prefs"]["workers_max"], b), (want, bad))

    def test_channel_lang_and_flags(self):
        s, b = fs.validate({"prefs": {"channel": "beta", "lang": "english-us-long", "autocheck": 1, "notify_loader": "yes"}})
        self.assertTrue(b)
        self.assertEqual((s["prefs"]["channel"], s["prefs"]["lang"], s["prefs"]["autocheck"], s["prefs"]["notify_loader"]), ("stable", "", False, False))
        for lang in ("", "en", "pt-BR", "zh-Hant", "fil"):
            self.assertEqual(fs.validate({"prefs": {"lang": lang}})[0]["prefs"]["lang"], lang)

    def test_ids_lists_filtered_deduped_and_capped(self):
        ids = ["ok", "OK", "has space", "x" * 33, "", 5, None, "ok", "a-b_c", "../etc", "\n"]
        s, b = fs.validate({"modules": {"order": ids}, "disabled": ids})
        self.assertEqual(s["modules"]["order"], ["ok", "a-b_c"])
        self.assertEqual(s["disabled"], ["ok", "a-b_c"])
        self.assertTrue(b)
        many = ["m%d" % i for i in range(300)]
        s, b = fs.validate({"modules": {"order": many}})
        self.assertEqual(s["modules"]["order"], many[:200])
        self.assertTrue(b)

    def test_unicode_digit_ids_rejected(self):
        s, b = fs.validate({"modules": {"order": ["١٢", "a​"]}})
        self.assertEqual(s["modules"]["order"], [])

    def test_frozen_entries(self):
        raw = {"modules": {"frozen": {"a": {"at": 5, "version": "1.0.0"}, "b": {"at": "x", "version": 3}, "c": 7, "BAD": {"at": 1}, "d": {"at": -4, "version": "v" * 41}}}}
        s, b = fs.validate(raw)
        self.assertTrue(b)
        self.assertEqual(s["modules"]["frozen"]["a"], {"at": 5, "version": "1.0.0"})
        self.assertEqual(s["modules"]["frozen"]["b"], {"at": 0, "version": ""})
        self.assertEqual(s["modules"]["frozen"]["d"], {"at": 0, "version": ""})
        self.assertEqual(sorted(s["modules"]["frozen"]), ["a", "b", "d"])

    def test_module_state_records(self):
        raw = {"modules": {"state": {"a": {"ver": "1.0.0", "data_v": 2, "ok_at": 10.9, "fails": 0, "last_error": "line1\nline2\x00" + "z" * 400, "extra": 1}, "b": [], "c": {"data_v": -1, "fails": "x"}}}}
        s, b = fs.validate(raw)
        a = s["modules"]["state"]["a"]
        self.assertEqual(sorted(a), ["data_v", "fails", "last_error", "ok_at", "ver"])
        self.assertEqual(a["ok_at"], 10)
        self.assertEqual(len(a["last_error"]), 198)
        self.assertNotIn("\n", a["last_error"])
        self.assertNotIn("b", s["modules"]["state"])
        self.assertEqual(s["modules"]["state"]["c"]["data_v"], 0)
        self.assertLessEqual(len(json.dumps(a, ensure_ascii=False).encode()), 1024)

    def test_module_state_record_stays_under_one_kib_worst_case(self):
        rec = {"ver": "v" * 40, "data_v": 1_000_000, "ok_at": 4_000_000_000_000, "fails": 1_000_000, "last_error": "\U0001F600" * 500}
        s = fs.defaults()
        fs.set_module_state(s, "a", rec)
        self.assertLessEqual(len(json.dumps(s["modules"]["state"]["a"], ensure_ascii=False).encode()), 1024)

    def test_file_roots_rules(self):
        roots = ["/home/deck/Music", "/run/media/deck/SD", "/mnt/disk", "/etc", "/home", "/home/", "/home/../etc", "/home/a/./b", "relative", "/homes/x", "/home/x\\y", "/home/x//y", "/home/deck/Music", 5, "/home/ok\n"]
        s, b = fs.validate({"prefs": {"file_roots": roots}})
        self.assertEqual(s["prefs"]["file_roots"], ["/home/deck/Music", "/run/media/deck/SD", "/mnt/disk"])
        self.assertTrue(b)
        nine = ["/home/u%d" % i for i in range(9)]
        s, b = fs.validate({"prefs": {"file_roots": nine}})
        self.assertEqual(len(s["prefs"]["file_roots"]), 8)

    def test_dev_targets_rules(self):
        t = {"store": "steamloopback", "BAD": "x", "ok": "", "long": "y" * 121, "n": 5, "a" * 25: "x"}
        t.update({"t%d" % i: "u" for i in range(10)})
        s, b = fs.validate({"prefs": {"dev": {"targets": t, "hot": ["a", "B"], "hot_until": "later", "enabled": "yes"}}})
        d = s["prefs"]["dev"]
        self.assertEqual(len(d["targets"]), 8)
        self.assertEqual(d["targets"]["store"], "steamloopback")
        self.assertNotIn("BAD", d["targets"])
        self.assertEqual((d["hot"], d["hot_until"], d["enabled"]), (["a"], 0, False))
        self.assertTrue(b)

    def test_numbers_reject_bool_nan_inf_negative_and_huge(self):
        for v in (True, -1, float("inf"), float("nan"), 10**400, "5", None, [], 5 * 10**12):
            with self.subTest(v=repr(v)[:15]):
                s, b = fs.validate({"updates": {"last": v}})
                self.assertEqual(s["updates"]["last"], 0)
                self.assertTrue(b)
        self.assertEqual(fs.validate({"updates": {"last": 12.9}})[0]["updates"]["last"], 12)

    def test_updates_section(self):
        raw = {"updates": {"loader": {"tag": "v1.0.2", "state": "x" * 40}, "plugins": {"a": {"latest": "1.0", "state": "update"}, "b": "x", "C": {}}, "seen": {"loader": 5, "plugins": {"a": "1.0", "b": 7, "D": "x"}}, "fails": -2}}
        s, b = fs.validate(raw)
        u = s["updates"]
        self.assertEqual(u["loader"], {"tag": "v1.0.2", "state": ""})
        self.assertEqual(list(u["plugins"]), ["a"])
        self.assertEqual(u["seen"], {"loader": "", "plugins": {"a": "1.0"}})
        self.assertEqual(u["fails"], 0)
        self.assertTrue(b)

    def test_unknown_top_level_keys_survive_and_repaired_is_not_persisted(self):
        self.write_raw({"disabled": ["a"], "legacy": {"x": 1}, "repaired": True})
        s = fs.load(self.path)
        self.assertEqual(s["legacy"], {"x": 1})
        self.assertFalse(s["repaired"])
        fs.save(self.path, s)
        on_disk = json.loads(self.path.read_text())
        self.assertNotIn("repaired", on_disk)
        self.assertEqual(on_disk["legacy"], {"x": 1})
        self.assertEqual(on_disk["disabled"], ["a"])
        self.assertEqual(on_disk["v"], 1)

    def test_roundtrip_is_stable(self):
        s = fs.load(self.path)
        fs.set_pref(s, "workers_max", 2)
        fs.set_hidden(s, "a", True)
        fs.set_order(s, ["b", "a"])
        fs.save(self.path, s)
        first = self.path.read_bytes()
        again = fs.load(self.path)
        self.assertFalse(again["repaired"])
        fs.save(self.path, again)
        self.assertEqual(self.path.read_bytes(), first)


class Ordering(unittest.TestCase):
    def test_custom_order_then_unseen_by_name_then_id(self):
        s = fresh()
        fs.set_order(s, ["c", "a"])
        got = fs.effective_order(rows(("a", "A"), ("b", "bravo"), ("c", "C"), ("d", "alpha")), s)
        self.assertEqual(got, ["c", "a", "d", "b"])

    def test_removed_ids_do_not_appear_but_keep_their_slot(self):
        s = fresh()
        fs.set_order(s, ["a", "b", "c"])
        self.assertEqual(fs.effective_order(rows(("a", "a"), ("c", "c")), s), ["a", "c"])
        self.assertEqual(s["modules"]["order"], ["a", "b", "c"])
        fs.set_order(s, ["c", "a"])
        self.assertEqual(s["modules"]["order"], ["c", "b", "a"])
        self.assertEqual(fs.effective_order(rows(("a", "a"), ("b", "b"), ("c", "c")), s), ["c", "b", "a"])

    def test_added_ids_in_set_order_are_placed_in_given_order(self):
        s = fresh()
        fs.set_order(s, ["a", "b"])
        fs.set_order(s, ["b", "x", "a"])
        self.assertEqual(s["modules"]["order"], ["b", "x", "a"])
        fs.set_order(s, ["y", "x"])
        self.assertEqual(s["modules"]["order"], ["b", "y", "a", "x"])

    def test_set_order_validation(self):
        s = fresh()
        for bad in ("a", None, ["a", "a"], ["A"], [1], ["a" * 33], ["x%d" % i for i in range(201)], {"a": 1}):
            with self.subTest(bad=repr(bad)[:20]):
                with self.assertRaises(fs.StateError):
                    fs.set_order(s, bad)
        self.assertEqual(s["modules"]["order"], [])

    def test_set_order_cap_with_kept_slots(self):
        s = fresh()
        s["modules"]["order"] = ["m%d" % i for i in range(200)]
        with self.assertRaises(fs.StateError) as cm:
            fs.set_order(s, ["new"])
        self.assertEqual(cm.exception.code, "too_many")
        self.assertEqual(len(s["modules"]["order"]), 200)

    def test_name_sort_casefold_unicode_and_tie_by_id(self):
        s = fresh()
        fs.set_sort(s, "name")
        data = rows(("z1", "zebra"), ("b2", "Apple"), ("a1", "apple"), ("c3", "Äpple"), ("d4", "Zebra"), ("e5", "banana"))
        self.assertEqual(fs.effective_order(data, s), ["a1", "b2", "e5", "d4", "z1", "c3"])

    def test_name_sort_is_independent_of_input_order(self):
        s = fresh()
        fs.set_sort(s, "name")
        base = [("m%d" % i, "Same Name") for i in range(4)]
        base += [("u1", "straße"), ("u2", "STRASSE"), ("u3", "éclair"), ("u4", "éclair")]
        want = ["m0", "m1", "m2", "m3", "u1", "u2", "u3", "u4"]
        for rot in range(len(base)):
            self.assertEqual(fs.effective_order(base[rot:] + base[:rot], s), want)
        self.assertEqual(fs.effective_order(list(reversed(base)), s), want)

    def test_name_sort_ignores_saved_custom_order_but_keeps_it(self):
        s = fresh()
        fs.set_order(s, ["b", "a"])
        fs.set_sort(s, "name")
        self.assertEqual(fs.effective_order(rows(("a", "A"), ("b", "B")), s), ["a", "b"])
        fs.set_sort(s, "custom")
        self.assertEqual(fs.effective_order(rows(("a", "A"), ("b", "B")), s), ["b", "a"])

    def test_non_string_names_fall_back_to_id_and_bad_rows_skipped(self):
        s = fresh()
        fs.set_sort(s, "name")
        self.assertEqual(fs.effective_order([("b", None), ("a", 5), ("BAD", "x"), ("a", "dup")], s), ["a", "b"])

    def test_move(self):
        s = fresh()
        r = rows(("a", "a"), ("b", "b"), ("c", "c"))
        fs.move(s, r, "b", -1)
        self.assertEqual(fs.effective_order(r, s), ["b", "a", "c"])
        fs.move(s, r, "b", -1)
        self.assertEqual(fs.effective_order(r, s), ["b", "a", "c"])
        fs.move(s, r, "a", 1)
        self.assertEqual(fs.effective_order(r, s), ["b", "c", "a"])
        fs.move(s, r, "a", 1)
        self.assertEqual(fs.effective_order(r, s), ["b", "c", "a"])
        for pid, delta in (("zz", 1), ("a", 0), ("a", 2), ("a", True), ("a", "1"), ("a", None)):
            with self.subTest(pid=pid, delta=delta):
                with self.assertRaises(fs.StateError):
                    fs.move(s, r, pid, delta)

    def test_move_works_on_custom_order_even_in_name_mode(self):
        s = fresh()
        fs.set_sort(s, "name")
        r = rows(("b", "x"), ("a", "z"))
        fs.move(s, r, "a", -1)
        self.assertEqual(s["modules"]["order"], ["a", "b"])
        self.assertEqual(s["modules"]["sort"], "name")

    def test_move_keeps_slots_of_missing_modules(self):
        s = fresh()
        fs.set_order(s, ["a", "gone", "b", "c"])
        r = rows(("a", "a"), ("b", "b"), ("c", "c"))
        fs.move(s, r, "c", -1)
        self.assertEqual(s["modules"]["order"], ["a", "gone", "c", "b"])


class Setters(unittest.TestCase):
    def test_sort(self):
        s = fresh()
        fs.set_sort(s, "name")
        self.assertEqual(s["modules"]["sort"], "name")
        for bad in ("Name", "", None, 1, ["name"]):
            with self.assertRaises(fs.StateError):
                fs.set_sort(s, bad)
        self.assertEqual(s["modules"]["sort"], "name")

    def test_hidden_independent_of_disabled(self):
        s = fresh()
        s["disabled"] = ["a"]
        fs.set_hidden(s, "a", True)
        fs.set_hidden(s, "a", True)
        self.assertEqual(s["modules"]["hidden"], ["a"])
        self.assertEqual(s["disabled"], ["a"])
        fs.set_hidden(s, "a", False)
        fs.set_hidden(s, "a", False)
        self.assertEqual(s["modules"]["hidden"], [])
        self.assertEqual(s["disabled"], ["a"])
        for pid, flag in (("A", True), (None, True), ("a", 1), ("a", None), ("a", "true")):
            with self.assertRaises(fs.StateError):
                fs.set_hidden(s, pid, flag)

    def test_hidden_cap(self):
        s = fresh()
        for i in range(200):
            fs.set_hidden(s, "m%d" % i, True)
        with self.assertRaises(fs.StateError):
            fs.set_hidden(s, "extra", True)

    def test_frozen_records_version_and_time_and_is_idempotent(self):
        s = fresh()
        fs.set_frozen(s, "a", True, "1.2.3", at=100)
        fs.set_frozen(s, "a", True, "9.9.9", at=200)
        self.assertEqual(s["modules"]["frozen"], {"a": {"at": 100, "version": "1.2.3"}})
        fs.set_frozen(s, "a", False, "")
        self.assertEqual(s["modules"]["frozen"], {})
        fs.set_frozen(s, "a", False, "")
        before = time.time()
        fs.set_frozen(s, "b", True, "bad version!")
        self.assertEqual(s["modules"]["frozen"]["b"]["version"], "")
        self.assertGreaterEqual(s["modules"]["frozen"]["b"]["at"], int(before))
        with self.assertRaises(fs.StateError):
            fs.set_frozen(s, "c", 1, "1.0")
        with self.assertRaises(fs.StateError):
            fs.set_frozen(s, "c", True, "1.0", at=-5)
        with self.assertRaises(fs.StateError):
            fs.set_frozen(s, "BAD", True, "1.0")

    def test_set_pref_whitelist_and_types(self):
        s = fresh()
        good = {
            "autocheck": True,
            "notify_loader": True,
            "notify_plugins": True,
            "channel": "prerelease",
            "lang": "fr",
            "safe": True,
            "workers_max": 2,
            "file_roots": ["/mnt/x"],
            "dev.enabled": True,
            "dev.hot": ["a", "b"],
            "dev.targets": {"store": "abc"},
        }
        for k, v in good.items():
            fs.set_pref(s, k, v)
        self.assertEqual(s["prefs"]["autocheck"], True)
        self.assertEqual(s["prefs"]["dev"]["hot"], ["a", "b"])
        self.assertEqual(s["prefs"]["dev"]["targets"], {"store": "abc"})
        self.assertEqual(s["prefs"]["workers_max"], 2)
        snapshot = json.dumps(s, sort_keys=True)
        bad = {
            "autocheck": 1,
            "notify_loader": "true",
            "channel": "nightly",
            "lang": "x",
            "safe": None,
            "workers_max": 9,
            "file_roots": ["/etc"],
            "dev.enabled": "on",
            "dev.hot": ["A"],
            "dev.targets": {"BAD": "x"},
            "dev.hot_until": "soon",
            "disabled": [],
            "v": 2,
            "modules.sort": "name",
            "prefs": {},
            "dev": {},
            "": 1,
            None: 1,
            5: 1,
            "__proto__": 1,
        }
        for k, v in bad.items():
            with self.subTest(key=k):
                with self.assertRaises(fs.StateError):
                    fs.set_pref(s, k, v)
        self.assertEqual(json.dumps(s, sort_keys=True), snapshot)

    def test_hot_until_is_a_whitelisted_bounded_timestamp(self):
        s = fresh()
        fs.set_pref(s, "dev.hot_until", 1_800_000_000)
        self.assertEqual(s["prefs"]["dev"]["hot_until"], 1_800_000_000)
        for bad in (-1, 4_000_000_000_001, True, "soon", None, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(fs.StateError):
                    fs.set_pref(s, "dev.hot_until", bad)
        self.assertEqual(s["prefs"]["dev"]["hot_until"], 1_800_000_000)

    def test_forget_module_prunes_everywhere(self):
        s = fresh()
        fs.set_order(s, ["a", "b"])
        fs.set_hidden(s, "a", True)
        fs.set_frozen(s, "a", True, "1.0")
        fs.set_module_state(s, "a", {"ver": "1.0", "data_v": 1})
        fs.set_pref(s, "dev.hot", ["a", "b"])
        s["updates"]["plugins"]["a"] = {"latest": "2", "state": "update"}
        s["updates"]["seen"]["plugins"]["a"] = "2"
        fs.forget_module(s, "a")
        fs.forget_module(s, "never-there")
        self.assertEqual(s["modules"]["order"], ["b"])
        self.assertEqual(s["modules"]["hidden"], [])
        self.assertEqual(s["modules"]["frozen"], {})
        self.assertEqual(s["modules"]["state"], {})
        self.assertEqual(s["prefs"]["dev"]["hot"], ["b"])
        self.assertEqual(s["updates"]["plugins"], {})
        self.assertEqual(s["updates"]["seen"]["plugins"], {})

    def test_module_state_helpers(self):
        s = fresh()
        self.assertEqual(fs.module_state(s, "a"), {})
        fs.set_module_state(s, "a", {"ver": "1.0.0", "data_v": 1})
        got = fs.module_state(s, "a")
        self.assertEqual((got["ver"], got["data_v"], got["fails"], got["last_error"]), ("1.0.0", 1, 0, ""))
        got["ver"] = "changed"
        self.assertEqual(fs.module_state(s, "a")["ver"], "1.0.0")
        for pid, rec in (("A", {}), ("a", []), ("a", {"data_v": "x"}), ("a", {"ver": "bad ver"})):
            with self.assertRaises(fs.StateError):
                fs.set_module_state(s, pid, rec)
        for i in range(199):
            fs.set_module_state(s, "m%d" % i, {})
        with self.assertRaises(fs.StateError):
            fs.set_module_state(s, "one-too-many", {})
        fs.set_module_state(s, "a", {"ver": "2.0.0", "data_v": 2})


class Persistence(TmpCase):
    def test_save_and_load_roundtrip_with_fsync_and_no_strays(self):
        s = fs.load(self.path)
        fs.set_pref(s, "safe", True)
        fs.save(self.path, s)
        self.assertTrue(fs.load(self.path)["prefs"]["safe"])
        self.assertEqual(self.stray(), [])

    def test_save_creates_missing_directory(self):
        deep = self.dir / "a" / "b" / "settings.json"
        fs.save(deep, fresh())
        self.assertTrue(deep.exists())

    def test_size_cap_keeps_previous_file(self):
        s = fresh()
        fs.set_hidden(s, "keepme", True)
        fs.save(self.path, s)
        old = self.path.read_bytes()
        big = fresh()
        for i in range(200):
            big["modules"]["state"]["m%d" % i] = {"ver": "1", "data_v": 1, "ok_at": 1, "fails": 1, "last_error": "\U0001F600" * 200}
        with self.assertRaises(fs.StateError) as cm:
            fs.save(self.path, big)
        self.assertEqual(cm.exception.code, "too_large")
        self.assertIn("state too large", str(cm.exception))
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(self.stray(), [])

    def test_size_boundary(self):
        s = fresh()
        s["pad"] = "x" * 10
        base = len(json.dumps(fs.validate(s)[0], separators=(",", ":"), ensure_ascii=False).encode())
        s["pad"] = "x" * (10 + fs.FILE_MAX - base)
        fs.save(self.path, s)
        self.assertEqual(self.path.stat().st_size, fs.FILE_MAX)
        s["pad"] += "x"
        with self.assertRaises(fs.StateError):
            fs.save(self.path, s)
        self.assertEqual(self.path.stat().st_size, fs.FILE_MAX)

    def test_replace_failure_keeps_old_and_cleans_temp(self):
        s = fresh()
        fs.set_hidden(s, "old", True)
        fs.save(self.path, s)
        old = self.path.read_bytes()
        s2 = fresh()
        fs.set_hidden(s2, "new", True)
        with mock.patch("fy_state.os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(fs.StateError) as cm:
                fs.save(self.path, s2)
        self.assertEqual(cm.exception.code, "write_failed")
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(self.stray(), [])

    def test_fsync_failure_keeps_old(self):
        fs.save(self.path, fresh())
        old = self.path.read_bytes()
        s2 = fresh()
        fs.set_hidden(s2, "new", True)
        with mock.patch("fy_state.os.fsync", side_effect=OSError("io")):
            with self.assertRaises(fs.StateError):
                fs.save(self.path, s2)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(self.stray(), [])

    def test_unserialisable_and_non_dict_state(self):
        fs.save(self.path, fresh())
        old = self.path.read_bytes()
        s = fresh()
        s["extra"] = object()
        for bad in (s, [], None, "x"):
            with self.assertRaises(fs.StateError):
                fs.save(self.path, bad)
        self.assertEqual(self.path.read_bytes(), old)

    def test_save_drops_invalid_parts_instead_of_persisting_them(self):
        s = fresh()
        s["prefs"]["workers_max"] = 99
        s["modules"]["order"] = ["ok", "NOT OK"]
        fs.save(self.path, s)
        got = json.loads(self.path.read_text())
        self.assertEqual(got["prefs"]["workers_max"], 3)
        self.assertEqual(got["modules"]["order"], ["ok"])

    def test_non_ascii_state_is_stored_as_utf8(self):
        s = fresh()
        fs.set_module_state(s, "a", {"last_error": "é中"})
        fs.save(self.path, s)
        self.assertIn("中".encode(), self.path.read_bytes())
        self.assertEqual(fs.load(self.path)["modules"]["state"]["a"]["last_error"], "é中")

    def test_load_after_unreadable_path_is_repaired_defaults(self):
        self.path.mkdir(parents=True)
        s = fs.load(self.path)
        self.assertTrue(s["repaired"])
        self.assertEqual(s["modules"]["order"], [])

    def test_older_loader_style_file_is_upgraded(self):
        self.write_raw({"disabled": ["a", 5, "b"]})
        s = fs.load(self.path)
        self.assertEqual(s["disabled"], ["a", "b"])
        self.assertTrue(s["repaired"])
        self.assertEqual(s["modules"]["sort"], "custom")

    def test_survives_process_restart(self):
        s = fs.load(self.path)
        fs.set_order(s, ["b", "a"])
        fs.set_sort(s, "name")
        fs.set_frozen(s, "a", True, "1.0.0", at=9)
        fs.set_pref(s, "safe", True)
        fs.save(self.path, s)
        again = fs.load(self.path)
        self.assertEqual(again["modules"]["order"], ["b", "a"])
        self.assertEqual(again["modules"]["sort"], "name")
        self.assertEqual(again["modules"]["frozen"], {"a": {"at": 9, "version": "1.0.0"}})
        self.assertTrue(again["prefs"]["safe"])


def fake_host(path, manifests):
    return types.SimpleNamespace(CONF=Path(path), manifests=lambda: list(manifests), now=lambda: 1234.7)


class Methods(TmpCase):
    def call(self, host, name, arg=None):
        return asyncio.run(fs.METHODS[name](host, arg))

    def setUp(self):
        super().setUp()
        self.host = fake_host(self.path, [{"id": "a", "name": "Zeta", "version": "1.0.0"}, {"id": "b", "name": "Alpha", "version": "2.0.0"}, {"id": "c", "version": "3.0.0"}])

    def test_method_names_match_contract(self):
        self.assertEqual(sorted(fs.METHODS), ["mod_freeze", "mod_hide", "mod_order", "mod_sort", "prefs_set", "state_get"])
        for fn in fs.METHODS.values():
            self.assertTrue(inspect.iscoroutinefunction(fn))

    def test_state_get_shape_and_no_write(self):
        r = self.call(self.host, "state_get")
        self.assertEqual(sorted(r), ["frozen", "hidden", "order", "prefs", "safe", "sort"])
        self.assertEqual(r["order"], ["b", "c", "a"])
        self.assertEqual(r["sort"], "custom")
        self.assertIs(r["safe"], False)
        self.assertFalse(self.path.exists())

    def test_order_sort_hide_freeze_prefs_persist(self):
        r = self.call(self.host, "mod_order", {"ids": ["c", "b", "a"]})
        self.assertEqual(r["order"], ["c", "b", "a"])
        r = self.call(self.host, "mod_order", {"id": "a", "delta": -1})
        self.assertEqual(r["order"], ["c", "a", "b"])
        r = self.call(self.host, "mod_sort", "name")
        self.assertEqual(r["sort"], "name")
        self.assertEqual(r["order"], ["b", "c", "a"])
        r = self.call(self.host, "mod_hide", {"id": "a", "hidden": True})
        self.assertEqual(r["hidden"], ["a"])
        r = self.call(self.host, "mod_freeze", {"id": "b", "frozen": True})
        self.assertEqual(r["frozen"], {"b": {"at": 1234, "version": "2.0.0"}})
        r = self.call(self.host, "prefs_set", {"safe": True, "channel": "prerelease", "dev.enabled": True})
        self.assertIs(r["safe"], True)
        self.assertEqual(r["prefs"]["channel"], "prerelease")
        self.assertIs(r["prefs"]["dev"]["enabled"], True)
        fresh_process = self.call(self.host, "state_get")
        self.assertEqual(fresh_process, r)
        self.assertEqual(fresh_process["hidden"], ["a"])
        self.assertEqual(fresh_process["sort"], "name")
        self.assertTrue(fresh_process["safe"])
        r = self.call(self.host, "mod_freeze", {"id": "b", "frozen": False})
        self.assertEqual(r["frozen"], {})
        r = self.call(self.host, "mod_hide", {"id": "a", "hidden": False})
        self.assertEqual(r["hidden"], [])

    def test_errors_are_error_dicts_and_do_not_save(self):
        self.call(self.host, "mod_hide", {"id": "a", "hidden": True})
        old = self.path.read_bytes()
        cases = [
            ("mod_order", None),
            ("mod_order", {"ids": "nope"}),
            ("mod_order", {"id": "zz", "delta": 1}),
            ("mod_order", {"id": "a", "delta": 5}),
            ("mod_sort", "alphabet"),
            ("mod_sort", {"sort": "name"}),
            ("mod_hide", {"id": "A", "hidden": True}),
            ("mod_hide", {"id": "a", "hidden": "yes"}),
            ("mod_hide", "a"),
            ("mod_freeze", {"id": "ghost", "frozen": True}),
            ("mod_freeze", {"id": "a", "frozen": 1}),
            ("prefs_set", {}),
            ("prefs_set", {"nope": 1}),
            ("prefs_set", {"safe": True, "workers_max": 99}),
            ("prefs_set", [1]),
            ("prefs_set", {("k%d" % i): 1 for i in range(17)}),
        ]
        for name, arg in cases:
            with self.subTest(name=name, arg=repr(arg)[:30]):
                r = self.call(self.host, name, arg)
                self.assertIn("error", r)
                self.assertIn("code", r)
                self.assertLessEqual(len(r["error"]), 300)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertFalse(fs.load(self.path)["prefs"]["safe"])

    def test_unfreeze_of_removed_module_is_allowed(self):
        self.call(self.host, "mod_freeze", {"id": "a", "frozen": True})
        host2 = fake_host(self.path, [])
        r = self.call(host2, "mod_freeze", {"id": "a", "frozen": False})
        self.assertEqual(r["frozen"], {})

    def test_methods_preserve_foreign_keys_and_disabled(self):
        self.write_raw({"disabled": ["b"], "future": {"k": 1}})
        self.call(self.host, "mod_hide", {"id": "a", "hidden": True})
        on_disk = json.loads(self.path.read_text())
        self.assertEqual(on_disk["disabled"], ["b"])
        self.assertEqual(on_disk["future"], {"k": 1})
        self.assertEqual(on_disk["modules"]["hidden"], ["a"])

    def test_reply_is_a_copy_not_the_live_state(self):
        r = self.call(self.host, "mod_hide", {"id": "a", "hidden": True})
        r["hidden"].append("zzz")
        r["prefs"]["safe"] = True
        self.assertEqual(self.call(self.host, "state_get")["hidden"], ["a"])
        self.assertFalse(self.call(self.host, "state_get")["safe"])

    def test_corrupt_part_is_repaired_on_next_write_without_losing_the_rest(self):
        self.write_raw({"disabled": ["b"], "modules": {"order": ["a"], "sort": "bogus"}, "prefs": {"safe": True, "workers_max": 77}})
        r = self.call(self.host, "mod_hide", {"id": "a", "hidden": True})
        on_disk = json.loads(self.path.read_text())
        self.assertEqual(on_disk["modules"]["sort"], "custom")
        self.assertEqual(on_disk["modules"]["order"], ["a"])
        self.assertTrue(on_disk["prefs"]["safe"])
        self.assertEqual(on_disk["prefs"]["workers_max"], 3)
        self.assertEqual(on_disk["disabled"], ["b"])

    def test_save_failure_is_reported_not_raised(self):
        with mock.patch("fy_state.os.replace", side_effect=OSError("denied")):
            r = self.call(self.host, "mod_hide", {"id": "a", "hidden": True})
        self.assertEqual(r["code"], "write_failed")


class NoComments(unittest.TestCase):
    def test_module_has_no_comments_or_docstrings(self):
        import ast
        import tokenize

        src = Path(fs.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.assertIsNone(ast.get_docstring(node))
        toks = tokenize.generate_tokens(iter(src.splitlines(True)).__next__)
        self.assertEqual([t.string for t in toks if t.type == tokenize.COMMENT], [])


class UnreadableSettingsTests(unittest.TestCase):
    def test_a_read_error_is_not_an_empty_file_and_is_never_overwritten(self):
        import errno
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text(json.dumps({"disabled": ["b"]}))
            with mock.patch("builtins.open", side_effect=OSError(errno.EMFILE, "too many open files")):
                s = fs.load(p)
            with self.assertRaises(fs.StateError) as c:
                fs.save(p, s)
            self.assertEqual(c.exception.code, "unreadable")
            self.assertEqual(json.loads(p.read_text()), {"disabled": ["b"]})
            self.assertEqual(fs.load(p)["disabled"], ["b"])
            fs.save(p, fs.load(p))

    def test_a_corrupt_file_is_kept_as_a_bad_copy(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_bytes(b"{not json")
            self.assertEqual(fs.load(p)["repaired"], True)
            self.assertEqual((Path(d) / "settings.json.bad").read_bytes(), b"{not json")
            with self.assertRaises(fs.StateError):
                fs.save(p, fs.load(p))
            self.assertEqual(p.read_bytes(), b"{not json")


if __name__ == "__main__":
    unittest.main()
