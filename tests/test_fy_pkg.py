import ast
import hashlib
import io
import json
import os
import random
import sys
import tarfile
import tempfile
import tokenize
import unittest
import warnings
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_pkg as pk

POSIX = unittest.skipUnless(os.name == "posix", "POSIX permission bits and special files are not tracked on Windows; runs on Linux")


def tar_bytes(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for e in entries:
            name, kind = e[0], e[1] if len(e) > 1 else "file"
            ti = tarfile.TarInfo(name)
            data = b""
            ti.mode = 0o644
            if kind == "file":
                data = e[2] if len(e) > 2 else b"x"
                ti.size = len(data)
                if len(e) > 3:
                    ti.mode = e[3]
            elif kind == "dir":
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
            elif kind == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, e[2]
            elif kind == "hardlink":
                ti.type, ti.linkname = tarfile.LNKTYPE, e[2]
            elif kind == "chr":
                ti.type, ti.devmajor, ti.devminor = tarfile.CHRTYPE, 1, 3
            elif kind == "blk":
                ti.type, ti.devmajor, ti.devminor = tarfile.BLKTYPE, 8, 0
            elif kind == "fifo":
                ti.type = tarfile.FIFOTYPE
            tf.addfile(ti, io.BytesIO(data) if kind == "file" else None)
    return buf.getvalue()


def zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buf, "w", compression) as z:
            for e in entries:
                zi = zipfile.ZipInfo(e[0])
                zi.compress_type = compression
                if len(e) > 2:
                    zi.create_system = 3
                    zi.external_attr = e[2] << 16
                z.writestr(zi, e[1])
    return buf.getvalue()


class Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.dest = self.root / "out"

    def listing(self):
        if not self.dest.exists():
            return []
        return sorted(str(p.relative_to(self.dest)).replace(os.sep, "/") for p in self.dest.rglob("*"))

    def tree(self):
        return sorted(str(p.relative_to(self.root)).replace(os.sep, "/") for p in self.root.rglob("*"))


class VersionParsing(unittest.TestCase):
    def test_valid_table(self):
        for text, shown, pre in (
            ("1.2.3", "1.2.3", False),
            ("v1.2.3", "1.2.3", False),
            ("1", "1", False),
            ("1.2", "1.2", False),
            ("1.2.3.4", "1.2.3.4", False),
            ("1.2.3-rc.1", "1.2.3-rc.1", True),
            ("v2.0.0-beta", "2.0.0-beta", True),
            ("1.0.0-0.3.7", "1.0.0-0.3.7", True),
            ("1.0.0-x-y-z", "1.0.0-x-y-z", True),
            ("1.0.0+build.5", "1.0.0+build.5", False),
            ("1.0.0-rc.1+build", "1.0.0-rc.1+build", True),
            ("0.0.0", "0.0.0", False),
            ("999999999.0", "999999999.0", False),
        ):
            with self.subTest(text=text):
                v = pk.parse_version(text)
                self.assertIsNotNone(v)
                self.assertEqual(str(v), shown)
                self.assertEqual(v.prerelease, pre)

    def test_invalid_table(self):
        for text in (None, 5, 1.5, [], {}, b"1.0", "", " ", "v", "vv1.0", "V1.0", " 1.0", "1.0 ", "1..2", ".1", "1.", "1.2.3.4.5", "1.0-", "1.0-rc..1", "1.0-rc.", "1.0+", "1.0-rc_1", "a.b.c", "1.x", "-1.0", "1.0\n", "1.0\x00", "1" * 10, "1.0-" + "a" * 33, "1.0-" + ".".join(["a"] * 9), "\u0663.\u0664", "1.0 beta", "latest", "1,0", "1.0;rm", "x" * 1000):
            with self.subTest(text=repr(text)[:30]):
                self.assertIsNone(pk.parse_version(text))

    def test_padding_and_equality(self):
        v = pk.parse_version
        self.assertEqual(v("1.0"), v("1.0.0"))
        self.assertEqual(v("1"), v("1.0.0.0"))
        self.assertEqual(v("v1.2.0"), v("1.2"))
        self.assertEqual(v("1.0.0+a"), v("1.0.0+b"))
        self.assertEqual(hash(v("1.0")), hash(v("1.0.0")))
        self.assertGreater(v("1.2.3.4"), v("1.2.3"))
        self.assertLess(v("1.2.3"), v("1.2.3.1"))
        self.assertGreater(v("1.10"), v("1.9"))
        self.assertGreater(v("2"), v("1.99.99.99"))

    def test_semver_style_ordering(self):
        ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1", "1.10", "2"]
        parsed = [pk.parse_version(t) for t in ordered]
        rng = random.Random(7)
        for _ in range(20):
            shuffled = parsed[:]
            rng.shuffle(shuffled)
            self.assertEqual([str(x) for x in sorted(shuffled)], ordered)

    def test_prerelease_is_below_its_release_and_above_the_previous(self):
        v = pk.parse_version
        self.assertLess(v("1.2.3-rc.1"), v("1.2.3"))
        self.assertGreater(v("1.2.3-rc.1"), v("1.2.2"))
        self.assertLess(v("1.2.3-rc.1"), v("1.2.3-rc.2"))
        self.assertLess(v("1.2.3-rc.2"), v("1.2.3-rc.10"))
        self.assertLess(v("1.2.3-9"), v("1.2.3-a"))
        self.assertLess(v("1.2.3-rc"), v("1.2.3-rc.1"))
        self.assertLess(v("1.2-rc.1"), v("1.2.0"))
        self.assertGreater(v("1.2.0"), v("1.2.0-0"))

    def test_loader_style_tags(self):
        self.assertGreater(pk.parse_version("v1.0.3"), pk.parse_version("1.0.2"))
        self.assertLess(pk.parse_version("1.0.2"), pk.parse_version("v1.1.0-rc.1"))


class Decide(unittest.TestCase):
    LOADER = "1.0.2"
    PLAT = "linux-aarch64"

    def d(self, m, loader=None, plat=None, api=1):
        return pk.decide(m, loader or self.LOADER, plat or self.PLAT, api)

    def test_shape(self):
        r = self.d({})
        self.assertEqual(sorted(r), ["badge", "msg", "reasons", "state"])
        self.assertIsInstance(r["reasons"], list)

    def test_unknown_when_nothing_declared(self):
        for m in ({}, {"name": "x", "version": "1.0"}, {"framey": {}}, {"framey": None}, {"platform": []}, {"framey": {}, "platform": [], "api": None}):
            with self.subTest(m=m):
                r = self.d(m)
                self.assertEqual((r["state"], r["badge"], r["msg"], r["reasons"]), ("unknown", "unknown", "Compatibility not declared", []))

    def test_ok_when_every_declared_constraint_holds(self):
        for m in (
            {"framey": {"min": "1.0.2"}},
            {"framey": {"min": "1.0.0", "max": "1.0.2"}},
            {"framey": {"max": "2"}},
            {"platform": ["linux-aarch64"]},
            {"platform": ["linux-x86_64", "linux-aarch64"]},
            {"api": 1},
            {"api": 1, "framey": {"min": "1.0.2"}, "platform": ["linux-aarch64"]},
        ):
            with self.subTest(m=m):
                r = self.d(m)
                self.assertEqual((r["state"], r["badge"], r["msg"], r["reasons"]), ("ok", "ok", "", []))

    def test_framey_older_than_needed(self):
        r = self.d({"framey": {"min": "1.1.0"}})
        self.assertEqual((r["state"], r["badge"]), ("incompatible", "newer"))
        self.assertEqual(r["reasons"], ["needs Framey 1.1.0 or newer; you have 1.0.2"])
        self.assertEqual(r["msg"], "Needs Framey 1.1.0 or newer; you have 1.0.2")
        self.assertEqual(self.d({"framey": {"min": "v1.0.3"}})["badge"], "newer")
        self.assertEqual(self.d({"framey": {"min": "1.0.2"}}, loader="1.0.2-rc.1")["badge"], "newer")

    def test_framey_newer_than_supported(self):
        r = self.d({"framey": {"max": "1.0.1"}})
        self.assertEqual((r["state"], r["badge"]), ("incompatible", "older"))
        self.assertEqual(r["reasons"], ["supports Framey up to 1.0.1; you have 1.0.2"])

    def test_max_with_fewer_than_three_parts_admits_the_whole_series(self):
        self.assertEqual(self.d({"framey": {"max": "1.9"}}, loader="1.9")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9"}}, loader="1.9.0")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9"}}, loader="1.9.1")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9"}}, loader="1.9.99.1")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "v1.9"}}, loader="1.9.3-rc.1")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9"}}, loader="1.10.0")["badge"], "older")
        self.assertEqual(self.d({"framey": {"max": "1.9"}}, loader="2.0")["badge"], "older")
        self.assertEqual(self.d({"framey": {"max": "1"}}, loader="1.99.9")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1"}}, loader="2.0.0")["badge"], "older")

    def test_min_may_sit_inside_a_series_named_by_a_short_max(self):
        self.assertEqual(self.d({"framey": {"min": "1.9.2", "max": "1.9"}}, loader="1.9.4")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"min": "1.0.2", "max": "1.0"}})["badge"], "ok")
        self.assertEqual(pk.normalize_manifest({"id": "a", "framey": {"min": "1.9.2", "max": "1.9"}})["ok"], True)
        self.assertEqual(pk.normalize_manifest({"id": "a", "framey": {"min": "1.10", "max": "1.9"}})["ok"], False)
        self.assertEqual(pk.normalize_manifest({"id": "a", "framey": {"min": "1.9.3", "max": "1.9.2"}})["ok"], False)

    def test_max_with_three_or_more_parts_is_exact_and_inclusive(self):
        self.assertEqual(self.d({"framey": {"max": "1.9.2"}}, loader="1.9.2")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9.2"}}, loader="1.9.1")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9.2"}}, loader="1.9.3")["badge"], "older")
        self.assertEqual(self.d({"framey": {"max": "1.9.2"}}, loader="1.9.2.1")["badge"], "older")
        self.assertEqual(self.d({"framey": {"max": "1.9.2.0"}}, loader="1.9.2")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9.5"}}, loader="1.9.5")["badge"], "ok")
        self.assertEqual(self.d({"framey": {"max": "1.9.0"}}, loader="1.9.1")["badge"], "older")

    def test_wrong_platform(self):
        r = self.d({"platform": ["linux-aarch64"]}, plat="linux-x86_64")
        self.assertEqual((r["state"], r["badge"]), ("incompatible", "platform"))
        self.assertEqual(r["reasons"], ["built for linux-aarch64; this headset is linux-x86_64"])
        r = self.d({"platform": ["linux-aarch64", "linux-riscv64"]}, plat="linux-x86_64")
        self.assertIn("linux-aarch64 or linux-riscv64", r["reasons"][0])

    def test_api_generation(self):
        r = self.d({"api": 2})
        self.assertEqual((r["state"], r["badge"]), ("incompatible", "api"))
        self.assertEqual(r["reasons"], ["needs API generation 2; this Framey provides 1"])
        self.assertEqual(self.d({"api": 2}, api=2)["badge"], "ok")
        self.assertEqual(self.d({"api": 1}, api=2)["badge"], "ok")

    def test_default_api_generation_is_one(self):
        self.assertEqual(pk.API_GENERATION, 1)
        self.assertEqual(pk.decide({"api": 2}, "1.0.2", "linux-aarch64")["badge"], "api")
        self.assertEqual(pk.decide({"api": 1}, "1.0.2", "linux-aarch64")["badge"], "ok")

    def test_several_reasons_are_all_listed_and_first_wins_badge(self):
        r = self.d({"framey": {"min": "2.0"}, "api": 2, "platform": ["linux-riscv64"]})
        self.assertEqual(len(r["reasons"]), 3)
        self.assertEqual(r["badge"], "newer")
        self.assertEqual(r["state"], "incompatible")
        r = self.d({"api": 2, "platform": ["linux-riscv64"]})
        self.assertEqual(r["badge"], "api")
        self.assertEqual(len(r["reasons"]), 2)

    def test_invalid_declarations_are_not_ok(self):
        for m in (
            {"api": 0},
            {"api": -1},
            {"api": "1"},
            {"api": True},
            {"api": 1.0},
            {"api": 10**9},
            {"framey": "1.0"},
            {"framey": []},
            {"framey": {"min": "banana"}},
            {"framey": {"min": 1}},
            {"framey": {"max": ["1"]}},
            {"framey": {"min": "2.0", "max": "1.0"}},
            {"platform": "linux-aarch64"},
            {"platform": [1]},
            {"platform": ["Linux-ARM"]},
            {"platform": ["windows"]},
            {"platform": ["linux-aarch64"] * 9},
        ):
            with self.subTest(m=m):
                r = self.d(m)
                self.assertEqual((r["state"], r["badge"]), ("incompatible", "unknown"))
                self.assertTrue(r["reasons"])

    def test_non_dict_manifest(self):
        for m in (None, [], "x", 5):
            r = pk.decide(m, "1.0.2", "linux-aarch64")
            self.assertEqual(r["state"], "incompatible")

    def test_unparsable_loader_version_with_framey_constraint(self):
        r = pk.decide({"framey": {"min": "1.0"}}, "garbage", "linux-aarch64")
        self.assertNotEqual(r["state"], "ok")
        self.assertEqual(pk.decide({"platform": ["linux-aarch64"]}, "garbage", "linux-aarch64")["state"], "ok")

    def test_existing_fan_manifest(self):
        fan = {"id": "fan", "name": "Fan Control", "version": "1.0.1", "framey": {"min": "1.0.2"}, "platform": ["linux-aarch64"], "privileged": {"installed": "/etc/frame-fan/.version", "helper": "/etc/frame-fan/update-helper"}}
        self.assertEqual(self.d(fan)["badge"], "ok")
        self.assertEqual(self.d(fan, plat="linux-x86_64")["badge"], "platform")
        self.assertEqual(self.d(fan, loader="1.0.1")["badge"], "newer")

    def test_hostile_manifest_does_not_raise(self):
        cyc = {}
        cyc["framey"] = cyc
        cyc["platform"] = [cyc]
        for m in (cyc, {"framey": {"min": "9" * 100000}}, {"platform": ["linux-x"] * 100000}, {"api": 5}):
            pk.decide(m, "1.0.2", "linux-aarch64")


class Normalize(unittest.TestCase):
    def test_minimal_manifest_defaults(self):
        r = pk.normalize_manifest({"id": "hello"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["errors"], [])
        v = r["value"]
        self.assertEqual((v["id"], v["name"], v["version"], v["short"], v["source"]), ("hello", "hello", "", "", ""))
        self.assertEqual(v["backend"], {"timeout": 10, "idle": 120, "keepalive": False, "concurrency": 1, "memory_mb": 32, "startup_timeout": 10})
        self.assertEqual(v["timeout"], 10)
        self.assertIsNone(v["api"])
        self.assertEqual(v["framey"], {"min": None, "max": None})
        self.assertIsNone(v["platform"])
        self.assertEqual((v["data_version"], v["i18n"], v["companions"], v["privileged"]), (0, {"default": "en"}, [], None))

    def test_full_manifest(self):
        m = {
            "id": "tool",
            "name": "Tool",
            "version": "1.2.0",
            "short": "does things",
            "source": "https://github.com/chaosfox26/tool",
            "api": 1,
            "framey": {"min": "1.0.2", "max": "1.9"},
            "platform": ["linux-aarch64"],
            "backend": {"timeout": 15, "idle": 300, "keepalive": True, "concurrency": 3, "memory_mb": 48, "startup_timeout": 20},
            "data_version": 2,
            "i18n": {"default": "pt-BR"},
            "privileged": {"installed": "/etc/x/.version", "helper": "/etc/x/update-helper"},
            "unknown_field": {"a": 1},
        }
        r = pk.normalize_manifest(m)
        self.assertTrue(r["ok"], r["errors"])
        v = r["value"]
        self.assertEqual(v["api"], 1)
        self.assertEqual(v["framey"], {"min": "1.0.2", "max": "1.9"})
        self.assertEqual(v["backend"], {"timeout": 15, "idle": 300, "keepalive": True, "concurrency": 3, "memory_mb": 48, "startup_timeout": 20})
        self.assertEqual(v["data_version"], 2)
        self.assertEqual(v["i18n"], {"default": "pt-BR"})
        self.assertEqual(v["privileged"], m["privileged"])
        self.assertNotIn("unknown_field", v)

    def test_top_level_timeout_is_alias_and_backend_wins(self):
        self.assertEqual(pk.normalize_manifest({"id": "a", "timeout": 25})["value"]["backend"]["timeout"], 25)
        r = pk.normalize_manifest({"id": "a", "timeout": 25, "backend": {"timeout": 7}})["value"]
        self.assertEqual((r["backend"]["timeout"], r["timeout"]), (7, 7))
        r = pk.normalize_manifest({"id": "a", "timeout": 25, "backend": {"idle": 50}})["value"]
        self.assertEqual(r["backend"]["timeout"], 25)
        self.assertEqual(pk.normalize_manifest({"id": "a", "timeout": 1.5})["value"]["timeout"], 1.5)

    def test_clamps(self):
        for key, low, high, lo_v, hi_v in (
            ("timeout", 0, 500, 1, 60),
            ("idle", 1, 99999, 10, 3600),
            ("concurrency", 0, 50, 1, 8),
            ("startup_timeout", 0, 600, 1, 60),
        ):
            for given, want in ((low, lo_v), (high, hi_v)):
                with self.subTest(key=key, given=given):
                    r = pk.normalize_manifest({"id": "a", "backend": {key: given}})
                    self.assertTrue(r["ok"], r["errors"])
                    self.assertEqual(r["value"]["backend"][key], want)
        r = pk.normalize_manifest({"id": "a", "backend": {"timeout": -5}})
        self.assertEqual(r["value"]["backend"]["timeout"], 1)

    def test_memory_limit_rejects_above_64_and_accepts_64(self):
        r = pk.normalize_manifest({"id": "a", "backend": {"memory_mb": 65}})
        self.assertFalse(r["ok"])
        self.assertIn("backend.memory_mb must be 64 or less", r["errors"])
        r = pk.normalize_manifest({"id": "a", "backend": {"memory_mb": 1e9}})
        self.assertFalse(r["ok"])
        r = pk.normalize_manifest({"id": "a", "backend": {"memory_mb": 64}})
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["value"]["backend"]["memory_mb"], 64)
        self.assertEqual(pk.normalize_manifest({"id": "a", "backend": {"memory_mb": 1}})["value"]["backend"]["memory_mb"], 16)
        self.assertEqual(pk.normalize_manifest({"id": "a"})["value"]["backend"]["memory_mb"], 32)

    def test_wrong_types_are_errors_with_safe_defaults(self):
        for field, bad in (
            ("backend", "x"),
            ("backend", {"timeout": "5"}),
            ("backend", {"timeout": True}),
            ("backend", {"timeout": float("nan")}),
            ("backend", {"timeout": float("inf")}),
            ("backend", {"concurrency": 2.5}),
            ("backend", {"concurrency": True}),
            ("backend", {"memory_mb": "64"}),
            ("backend", {"keepalive": 1}),
            ("backend", {"idle": [120]}),
            ("timeout", "10"),
            ("data_version", "1"),
            ("data_version", -1),
            ("data_version", True),
            ("data_version", 1.5),
            ("i18n", "en"),
            ("i18n", {"default": 5}),
            ("i18n", {"default": "not a locale"}),
            ("name", 5),
            ("version", ["1"]),
            ("short", {}),
            ("source", 5),
            ("companions", "x"),
            ("companions", [5]),
            ("companions", [{}]),
            ("privileged", "x"),
            ("privileged", {"helper": "relative/path"}),
            ("privileged", {"helper": 5}),
            ("id", None),
            ("id", "Bad Id"),
            ("id", "_core"),
            ("id", "x" * 33),
            ("api", 0),
            ("api", "1"),
            ("framey", {"min": "x"}),
            ("platform", ["x"]),
        ):
            with self.subTest(field=field, bad=repr(bad)[:40]):
                m = {"id": "a"}
                m[field] = bad
                r = pk.normalize_manifest(m)
                self.assertFalse(r["ok"])
                self.assertTrue(r["errors"])
                self.assertTrue(all(len(e) <= 300 for e in r["errors"]))
                self.assertIn("backend", r["value"])
                self.assertGreaterEqual(r["value"]["backend"]["timeout"], 1)
                self.assertLessEqual(r["value"]["backend"]["timeout"], 60)

    def test_non_dict_and_garbage_never_raise(self):
        for m in (None, [], "x", 5, b"x", object()):
            r = pk.normalize_manifest(m)
            self.assertEqual((r["ok"], r["value"]), (False, {}))
            self.assertTrue(r["errors"])
        cyc = {"id": "a"}
        cyc["backend"] = cyc
        cyc["companions"] = [cyc]
        cyc["framey"] = cyc
        cyc["privileged"] = cyc
        cyc["i18n"] = cyc
        self.assertFalse(pk.normalize_manifest(cyc)["ok"])
        nasty = [None, True, 0, -1, 10**100, 1.5, float("nan"), "", "x" * 10000, "\x00", [], {}, [[]], {"a": {"b": {}}}, [None] * 1000]
        for key in ("id", "name", "version", "short", "source", "api", "framey", "platform", "timeout", "backend", "data_version", "i18n", "companions", "privileged"):
            for n in nasty:
                r = pk.normalize_manifest({"id": "a", key: n} if key != "id" else {key: n})
                self.assertIn("ok", r)
                self.assertIsInstance(r["errors"], list)
                self.assertLessEqual(len(r["errors"]), 20)

    def test_text_fields_are_cleaned_and_capped(self):
        r = pk.normalize_manifest({"id": "a", "name": "N\x00a\nme" + "x" * 200, "short": "s" * 500, "version": "1.0\x07", "source": "u" * 900})["value"]
        self.assertTrue(r["name"].startswith("Name"))
        self.assertLessEqual(len(r["name"]), 80)
        self.assertEqual(len(r["short"]), 200)
        self.assertEqual(r["version"], "1.0")
        self.assertEqual(len(r["source"]), 300)

    def test_legacy_manifest_still_ok(self):
        r = pk.normalize_manifest({"id": "hello", "name": "Hello", "version": "1.0.0", "short": "hi", "timeout": 5, "main": "main.js"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["value"]["timeout"], 5)

    def test_manifest_with_unparsable_version_text_is_not_rejected(self):
        self.assertTrue(pk.normalize_manifest({"id": "a", "version": "banana"})["ok"])

    def test_companion_declarations_in_manifest(self):
        good = {"name": "tool", "version": "1.2.0", "platform": "linux-aarch64", "asset": "tool-linux-aarch64.tar.gz", "sha256": "a" * 64, "size": 100, "exec": ["tool"]}
        r = pk.normalize_manifest({"id": "a", "companions": [good]})
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["value"]["companions"], [good])
        twice = pk.normalize_manifest({"id": "a", "companions": [good, dict(good)]})
        self.assertFalse(twice["ok"])
        other_platform = dict(good, platform="linux-x86_64", asset="tool-linux-x86_64.tar.gz")
        self.assertTrue(pk.normalize_manifest({"id": "a", "companions": [good, other_platform]})["ok"])
        self.assertFalse(pk.normalize_manifest({"id": "a", "companions": [good] * 17})["ok"])


def good_spec(archive, **over):
    spec = {"name": "tool", "version": "1.2.0", "platform": "linux-aarch64", "asset": "tool-linux-aarch64.tar.gz", "sha256": hashlib.sha256(archive).hexdigest(), "size": len(archive), "exec": ["tool"]}
    spec.update(over)
    return spec


class Companions(Tmp):
    def decl(self, **over):
        d = {"name": "tool", "version": "1.2.0", "platform": "linux-aarch64", "asset": "tool.tar.gz", "sha256": "b" * 64, "size": 10, "exec": ["tool"]}
        d.update(over)
        return d

    def test_companions_filter_by_platform_and_validity(self):
        m = {"companions": [self.decl(), self.decl(name="other", platform="linux-x86_64"), self.decl(name="bad", sha256="zz"), "junk", self.decl(name="tool", version="2.0.0")]}
        got = pk.companions(m, "linux-aarch64")
        self.assertEqual([c["name"] for c in got], ["tool"])
        self.assertEqual(got[0]["version"], "1.2.0")
        self.assertEqual([c["name"] for c in pk.companions(m, "linux-x86_64")], ["other"])
        self.assertEqual(pk.companions(m, "linux-riscv64"), [])
        for junk in (None, {}, {"companions": "x"}, {"companions": None}, [], "x", 5):
            self.assertEqual(pk.companions(junk, "linux-aarch64"), [])

    def test_companions_default_platform_is_this_machine(self):
        self.assertTrue(pk.PLATFORM.startswith("linux-"))
        got = pk.companions({"companions": [self.decl(platform=pk.PLATFORM)]})
        self.assertEqual(len(got), 1)

    def test_declaration_validation_table(self):
        bad = [
            {"name": "Tool"},
            {"name": ""},
            {"name": 5},
            {"name": "x" * 33},
            {"version": "banana"},
            {"version": None},
            {"platform": "windows-x86"},
            {"platform": "linux-"},
            {"platform": 5},
            {"asset": "../tool.tar.gz"},
            {"asset": "dir/tool.tar.gz"},
            {"asset": "tool.exe"},
            {"asset": "tool"},
            {"asset": ".hidden.tar.gz"},
            {"asset": "a b.tar.gz"},
            {"asset": 5},
            {"asset": "x" * 200 + ".tar.gz"},
            {"sha256": "a" * 63},
            {"sha256": "a" * 65},
            {"sha256": "g" * 64},
            {"sha256": "sha256:" + "a" * 64},
            {"sha256": None},
            {"size": 0},
            {"size": -1},
            {"size": pk.ARCHIVE_MAX + 1},
            {"size": "10"},
            {"size": True},
            {"size": 1.5},
            {"exec": "tool"},
            {"exec": [5]},
            {"exec": ["../tool"]},
            {"exec": ["/bin/sh"]},
            {"exec": ["a\\b"]},
            {"exec": ["a%d" % i for i in range(9)]},
        ]
        for over in bad:
            with self.subTest(over=repr(over)[:50]):
                self.assertEqual(pk.companions({"companions": [self.decl(**over)]}, "linux-aarch64"), [])
                r = pk.normalize_manifest({"id": "a", "companions": [self.decl(**over)]})
                self.assertFalse(r["ok"])

    def test_declaration_normalisation(self):
        d = pk.companions({"companions": [self.decl(sha256="AB" * 32, version="v1.2.0", exec=["bin/tool", "bin/tool", "x"])]}, "linux-aarch64")[0]
        self.assertEqual(d["sha256"], "ab" * 32)
        self.assertEqual(d["version"], "1.2.0")
        self.assertEqual(d["exec"], ["bin/tool", "x"])
        d = pk.companions({"companions": [self.decl(exec=[])]}, "linux-aarch64")[0]
        self.assertEqual(d["exec"], [])
        d = pk.companions({"companions": [{k: v for k, v in self.decl().items() if k != "exec"}]}, "linux-aarch64")[0]
        self.assertEqual(d["exec"], [])

    def test_sha256_and_verify_file(self):
        f = self.root / "f.bin"
        f.write_bytes(b"abc")
        digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.assertEqual(pk.sha256_file(f), digest)
        self.assertTrue(pk.verify_file(f, digest))
        self.assertTrue(pk.verify_file(f, digest.upper()))
        self.assertTrue(pk.verify_file(f, digest, 3))
        self.assertTrue(pk.verify_file(f, "sha256:" + digest, 3))
        self.assertFalse(pk.verify_file(f, digest, 4))
        self.assertFalse(pk.verify_file(f, "0" * 64))
        self.assertFalse(pk.verify_file(f, "short"))
        self.assertFalse(pk.verify_file(f, None))
        self.assertFalse(pk.verify_file(self.root / "missing", digest))
        self.assertFalse(pk.verify_file(self.root, digest))

    def test_url_allowed(self):
        for u in ("https://github.com/o/r/releases/download/v1/a.tar.gz", "https://objects.githubusercontent.com/x", "https://release-assets.githubusercontent.com/x?y=1", "https://GitHub.com/a", "https://github.com:443/a"):
            self.assertTrue(pk.url_allowed(u), u)
        for u in ("http://github.com/a", "https://evil.com/github.com", "https://github.com.evil.com/a", "https://user@github.com/a", "https://github.com:8443/a", "ftp://github.com/a", "https://githubusercontent.com/a", "", None, 5, "https://[::1/", "//github.com/a", "file:///etc/passwd"):
            self.assertFalse(pk.url_allowed(u), u)

    def test_digest_agrees(self):
        h = "a" * 64
        self.assertTrue(pk.digest_agrees(h, "sha256:" + h))
        self.assertTrue(pk.digest_agrees(h, "sha256:" + h.upper()))
        self.assertTrue(pk.digest_agrees(h, None))
        self.assertTrue(pk.digest_agrees(h, ""))
        self.assertFalse(pk.digest_agrees(h, "sha256:" + "b" * 64))
        self.assertFalse(pk.digest_agrees(h, "sha512:" + "a" * 128))
        self.assertFalse(pk.digest_agrees(h, "garbage"))
        self.assertFalse(pk.digest_agrees(h, 5))
        self.assertFalse(pk.digest_agrees(None, "sha256:" + h))

    def test_install_tar_with_top_folder(self):
        arc = tar_bytes([("tool-1.2.0/", "dir"), ("tool-1.2.0/tool", "file", b"#!bin"), ("tool-1.2.0/lib/x.so", "file", b"lib"), ("tool-1.2.0/README", "file", b"hi")])
        r = pk.install_companion(arc, good_spec(arc), self.dest, platform_id="linux-aarch64")
        self.assertEqual(r["files"], ["README", "lib/x.so", "tool"])
        self.assertEqual(r["exec"], ["tool"])
        final = self.dest / "tool-1.2.0"
        self.assertEqual((final / "tool").read_bytes(), b"#!bin")
        self.assertEqual((final / "lib" / "x.so").read_bytes(), b"lib")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["tool-1.2.0"])

    def test_install_zip_flat(self):
        arc = zip_bytes([("tool", b"bin"), ("data/a.txt", b"a")])
        spec = good_spec(arc, asset="tool-linux-aarch64.zip")
        r = pk.install_companion(arc, spec, self.dest, platform_id="linux-aarch64")
        self.assertEqual(r["files"], ["data/a.txt", "tool"])
        self.assertTrue((self.dest / "tool-1.2.0" / "tool").is_file())

    def test_install_replaces_previous_copy_atomically(self):
        a1 = tar_bytes([("tool", "file", b"v1")])
        a2 = tar_bytes([("tool", "file", b"v2"), ("extra", "file", b"e")])
        pk.install_companion(a1, good_spec(a1), self.dest, platform_id="linux-aarch64")
        pk.install_companion(a2, good_spec(a2), self.dest, platform_id="linux-aarch64")
        final = self.dest / "tool-1.2.0"
        self.assertEqual((final / "tool").read_bytes(), b"v2")
        self.assertTrue((final / "extra").exists())
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["tool-1.2.0"])

    def test_wrong_digest_extracts_nothing(self):
        arc = tar_bytes([("tool", "file", b"x")])
        for sha in ("0" * 64, hashlib.sha256(arc + b"x").hexdigest()):
            with self.assertRaises(pk.PkgError) as cm:
                pk.install_companion(arc, good_spec(arc, sha256=sha), self.dest, platform_id="linux-aarch64")
            self.assertEqual(cm.exception.code, "digest")
        self.assertEqual(self.tree(), ["out"] if self.dest.exists() else [])

    def test_wrong_size_and_oversize(self):
        arc = tar_bytes([("tool", "file", b"x")])
        for size in (len(arc) + 1, len(arc) - 1):
            with self.assertRaises(pk.PkgError) as cm:
                pk.install_companion(arc, good_spec(arc, size=size), self.dest, platform_id="linux-aarch64")
            self.assertEqual(cm.exception.code, "size")
        big = b"\x00" * (pk.ARCHIVE_MAX + 1)
        with self.assertRaises(pk.PkgError) as cm:
            pk.install_companion(big, good_spec(big, size=pk.ARCHIVE_MAX), self.dest, platform_id="linux-aarch64")
        self.assertEqual(cm.exception.code, "too_large")

    def test_wrong_platform(self):
        arc = tar_bytes([("tool", "file", b"x")])
        with self.assertRaises(pk.PkgError) as cm:
            pk.install_companion(arc, good_spec(arc), self.dest, platform_id="linux-x86_64")
        self.assertEqual(cm.exception.code, "platform")
        self.assertIn("linux-aarch64", cm.exception.message)
        self.assertEqual(self.tree(), [])

    def test_invalid_spec_and_missing_exec(self):
        arc = tar_bytes([("tool", "file", b"x")])
        with self.assertRaises(pk.PkgError) as cm:
            pk.install_companion(arc, good_spec(arc, sha256="zz"), self.dest, platform_id="linux-aarch64")
        self.assertEqual(cm.exception.code, "bad_spec")
        with self.assertRaises(pk.PkgError) as cm:
            pk.install_companion(arc, "not a dict", self.dest, platform_id="linux-aarch64")
        self.assertEqual(cm.exception.code, "bad_spec")
        with self.assertRaises(pk.PkgError) as cm:
            pk.install_companion(arc, good_spec(arc, exec=["nothere"]), self.dest, platform_id="linux-aarch64")
        self.assertEqual(cm.exception.code, "missing_exec")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [])

    def test_hostile_archives_are_refused_after_digest_check(self):
        for entries in (
            [("tool", "file", b"x"), ("link", "symlink", "/etc/passwd")],
            [("../escape", "file", b"x"), ("tool", "file", b"x")],
            [("tool", "file", b"x"), ("tool", "file", b"y")],
            [("tool", "file", b"x"), ("TOOL", "file", b"y")],
        ):
            arc = tar_bytes(entries)
            with self.assertRaises(pk.PkgError):
                pk.install_companion(arc, good_spec(arc), self.dest, platform_id="linux-aarch64")
            self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [])
        self.assertFalse((self.root / "escape").exists())

    def test_failed_install_keeps_previous_copy(self):
        a1 = tar_bytes([("tool", "file", b"v1")])
        pk.install_companion(a1, good_spec(a1), self.dest, platform_id="linux-aarch64")
        bad = tar_bytes([("tool", "file", b"v2"), ("evil", "symlink", "x")])
        with self.assertRaises(pk.PkgError):
            pk.install_companion(bad, good_spec(bad), self.dest, platform_id="linux-aarch64")
        self.assertEqual((self.dest / "tool-1.2.0" / "tool").read_bytes(), b"v1")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["tool-1.2.0"])

    @POSIX
    def test_exec_bit_only_for_declared_names_and_never_setuid(self):
        arc = tar_bytes([("tool", "file", b"bin", 0o4755), ("other", "file", b"o", 0o4777), ("bin/helper", "file", b"h", 0o755)])
        r = pk.install_companion(arc, good_spec(arc, exec=["tool", "bin/helper"]), self.dest, platform_id="linux-aarch64")
        final = self.dest / "tool-1.2.0"
        self.assertEqual((final / "tool").stat().st_mode & 0o7777, 0o755)
        self.assertEqual((final / "bin" / "helper").stat().st_mode & 0o7777, 0o755)
        self.assertEqual((final / "other").stat().st_mode & 0o7111, 0)
        self.assertEqual(sorted(r["exec"]), ["bin/helper", "tool"])
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o700)

    @POSIX
    def test_zip_exec_bit_and_symlink_member(self):
        arc = zip_bytes([("tool", b"bin", 0o100755), ("other", b"o", 0o104777)])
        pk.install_companion(arc, good_spec(arc, asset="t.zip"), self.dest, platform_id="linux-aarch64")
        final = self.dest / "tool-1.2.0"
        self.assertEqual((final / "tool").stat().st_mode & 0o7777, 0o755)
        self.assertEqual((final / "other").stat().st_mode & 0o7111, 0)


class Identity(Tmp):
    FULL = {"source": "https://github.com/chaosfox26/frame-fan", "tag": "v1.0.1", "commit": "a" * 40, "digest": "sha256:" + "b" * 64}

    def test_kinds_and_required_fields(self):
        r = pk.identity("release", **self.FULL)
        self.assertEqual(r, {"v": 1, "kind": "release", **self.FULL})
        self.assertEqual(pk.identity("commit", source=self.FULL["source"], commit="abc1234")["kind"], "commit")
        self.assertEqual(pk.identity("branch", source=self.FULL["source"], tag="main")["tag"], "main")
        self.assertEqual(pk.identity("url", url="https://example.com/p.zip")["url"], "https://example.com/p.zip")
        self.assertEqual(pk.identity("local"), {"v": 1, "kind": "local"})
        for kind, kw in (("release", {"source": "s"}), ("release", {"source": "s", "tag": "t"}), ("commit", {"source": "s"}), ("commit", {"commit": "abc1234"}), ("branch", {"source": "s"}), ("url", {}), ("url", {"source": "s"})):
            with self.subTest(kind=kind, kw=kw):
                with self.assertRaises(pk.PkgError) as cm:
                    pk.identity(kind, **kw)
                self.assertEqual(cm.exception.code, "bad_identity")

    def test_invalid_fields(self):
        base = dict(self.FULL)
        for key, bad in (
            ("commit", "ABCDEF1"),
            ("commit", "abc12"),
            ("commit", "g" * 40),
            ("commit", "a" * 65),
            ("commit", 5),
            ("digest", "sha256:" + "z" * 64),
            ("digest", "md5:abc"),
            ("digest", "b" * 63),
            ("digest", 5),
            ("tag", "has space"),
            ("tag", "x" * 101),
            ("tag", "new\nline"),
            ("tag", 5),
            ("source", "a b"),
            ("source", "x" * 301),
            ("source", ["x"]),
        ):
            with self.subTest(key=key, bad=repr(bad)[:20]):
                with self.assertRaises(pk.PkgError):
                    pk.identity("release", **{**base, key: bad})
        for u in ("http://example.com/p.zip", "ftp://x/y", "https://x y", "x" * 600, 5):
            with self.assertRaises(pk.PkgError):
                pk.identity("url", url=u)
        for kind in ("Release", "", None, 5, ["release"], "zip"):
            with self.assertRaises(pk.PkgError):
                pk.identity(kind)

    def test_digest_is_normalised(self):
        h = "AB" * 32
        self.assertEqual(pk.identity("release", **{**self.FULL, "digest": h})["digest"], "sha256:" + h.lower())
        self.assertEqual(pk.identity("release", **{**self.FULL, "digest": "sha256:" + h})["digest"], "sha256:" + h.lower())

    def test_empty_strings_count_as_absent(self):
        r = pk.identity("local", source="", tag="", commit="", digest="", url="")
        self.assertEqual(r, {"v": 1, "kind": "local"})

    def test_write_read_round_trip_without_touching_source_and_version(self):
        stage = self.root / "stage"
        stage.mkdir()
        (stage / ".source").write_bytes(b"https://github.com/chaosfox26/frame-fan\n")
        (stage / ".version").write_bytes(b"0123456789abcdef0123456789abcdef01234567")
        before = {n: ((stage / n).read_bytes(), (stage / n).stat().st_mtime_ns) for n in (".source", ".version")}
        ident = pk.identity("release", **self.FULL)
        pk.write_identity(stage, ident)
        self.assertEqual(pk.read_identity(stage), ident)
        after = {n: ((stage / n).read_bytes(), (stage / n).stat().st_mtime_ns) for n in (".source", ".version")}
        self.assertEqual(before, after)
        self.assertEqual(sorted(p.name for p in stage.iterdir()), [".identity.json", ".source", ".version"])
        self.assertLessEqual((stage / ".identity.json").stat().st_size, 4096)

    def test_write_is_deterministic_and_overwrites(self):
        pk.write_identity(self.root, pk.identity("local"))
        first = (self.root / ".identity.json").read_bytes()
        pk.write_identity(self.root, pk.identity("local"))
        self.assertEqual((self.root / ".identity.json").read_bytes(), first)
        pk.write_identity(self.root, pk.identity("commit", source="s", commit="abcdef1"))
        self.assertEqual(pk.read_identity(self.root)["commit"], "abcdef1")

    def test_write_validates_and_fails_cleanly(self):
        for bad in (None, [], "x", {}, {"kind": "release"}, {"kind": "local", "commit": "NOPE"}):
            with self.assertRaises(pk.PkgError):
                pk.write_identity(self.root, bad)
        self.assertEqual(self.tree(), [])
        with self.assertRaises(pk.PkgError):
            pk.write_identity(self.root / "missing", pk.identity("local"))

    def test_write_ignores_unknown_keys_in_given_dict(self):
        pk.write_identity(self.root, {**pk.identity("local"), "evil": "x" * 10000, "v": 99})
        self.assertEqual(json.loads((self.root / ".identity.json").read_text()), {"kind": "local", "v": 1})

    def test_read_rejects_bad_files(self):
        p = self.root / ".identity.json"
        self.assertIsNone(pk.read_identity(self.root))
        for data in (b"", b"[]", b"7", b"nope", b'{"v":1}', b'{"v":2,"kind":"local"}', b'{"v":1,"kind":"zip"}', b'{"v":1,"kind":"commit","source":"s","commit":"NOPE"}', b'{"v":1,"kind":"local","pad":"' + b"x" * 5000 + b'"}', b"[" * 50000):
            p.write_bytes(data)
            self.assertIsNone(pk.read_identity(self.root), data[:30])
        p.unlink()
        p.mkdir()
        self.assertIsNone(pk.read_identity(self.root))

    def test_read_ignores_unknown_keys(self):
        (self.root / ".identity.json").write_text('{"v":1,"kind":"local","extra":1}')
        self.assertEqual(pk.read_identity(self.root), {"v": 1, "kind": "local"})

    def test_read_refuses_symlink(self):
        real = self.root / "real.json"
        real.write_text('{"v":1,"kind":"local"}')
        try:
            os.symlink(real, self.root / ".identity.json")
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks; runs on Linux")
        self.assertIsNone(pk.read_identity(self.root))


class TarHostile(Tmp):
    def extract(self, entries, **kw):
        return pk.safe_extract_tar(tar_bytes(entries), self.dest, **kw)

    def refused(self, entries, code=None, **kw):
        with self.assertRaises(pk.PkgError) as cm:
            self.extract(entries, **kw)
        if code:
            self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.listing(), [])
        self.assertEqual([p for p in self.tree() if not p.startswith("out")], [])

    def test_plain_extraction(self):
        files = self.extract([("a.txt", "file", b"A"), ("sub/", "dir"), ("sub/b.txt", "file", b"B"), ("sub/deeper/c.txt", "file", b"C")])
        self.assertEqual(files, ["a.txt", "sub/b.txt", "sub/deeper/c.txt"])
        self.assertEqual((self.dest / "sub" / "deeper" / "c.txt").read_bytes(), b"C")

    def test_dot_slash_prefix_is_accepted(self):
        files = self.extract([("./", "dir"), ("./tool", "file", b"t"), ("./lib/", "dir"), ("./lib/x", "file", b"x")])
        self.assertEqual(files, ["lib/x", "tool"])

    def test_top_and_strip(self):
        entries = [("repo-1/", "dir"), ("repo-1/a.txt", "file", b"A"), ("repo-1/sub/b.txt", "file", b"B")]
        self.assertEqual(self.extract(entries, top="repo-1"), ["a.txt", "sub/b.txt"])
        second = self.root / "out2"
        self.assertEqual(pk.safe_extract_tar(tar_bytes(entries), second, strip=1), ["a.txt", "sub/b.txt"])
        self.assertEqual(pk.safe_extract_tar(tar_bytes(entries), self.root / "out3", strip="auto"), ["a.txt", "sub/b.txt"])
        self.assertEqual(pk.safe_extract_tar(tar_bytes(entries), self.root / "out4"), ["repo-1/a.txt", "repo-1/sub/b.txt"])

    def test_auto_strip_leaves_mixed_roots_alone(self):
        files = pk.safe_extract_tar(tar_bytes([("a/x", "file", b"1"), ("b/y", "file", b"2")]), self.dest, strip="auto")
        self.assertEqual(files, ["a/x", "b/y"])
        files = pk.safe_extract_tar(tar_bytes([("top", "file", b"1"), ("d/y", "file", b"2")]), self.root / "o2", strip="auto")
        self.assertEqual(files, ["d/y", "top"])

    def test_top_mismatch_refused(self):
        self.refused([("other/a", "file", b"x")], "unsafe_path", top="repo-1")
        self.refused([("repo-1", "file", b"x")], "unsafe_path", top="repo-1")

    def test_strip_validation(self):
        for bad in (-1, 5, "2", None, 1.0, True):
            with self.subTest(strip=bad):
                with self.assertRaises(pk.PkgError):
                    self.extract([("a/b", "file", b"x")], strip=bad)

    def test_traversal_and_absolute_paths(self):
        for name in ("../evil", "a/../../evil", "/etc/passwd", "a/../b", "..", "a/./b", "a//b", "..\\evil", "a\\b", "C:\\x", "C:/x", "a/b:stream", "a/b*", 'a/"b', "a/<b", "a/b|c", "a/b?", "con./x", "trail.", "trail ", "x" * 256, "a\x01b", "a\x7fb"):
            with self.subTest(name=name):
                self.refused([("ok.txt", "file", b"x"), (name, "file", b"x")], "unsafe_path")
        self.assertFalse((self.root / "evil").exists())

    def test_too_deep(self):
        self.extract([("/".join("abcdefgh"), "file", b"x")])
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_tar(tar_bytes([("/".join("abcdefghi"), "file", b"x")]), self.root / "deep")
        self.assertEqual(cm.exception.code, "unsafe_path")

    def test_symlink_hardlink_device_fifo_refused(self):
        self.refused([("ok", "file", b"x"), ("l", "symlink", "/etc/passwd")], "unsafe_type")
        self.refused([("ok", "file", b"x"), ("l", "symlink", "ok")], "unsafe_type")
        self.refused([("ok", "file", b"x"), ("h", "hardlink", "ok")], "unsafe_type")
        self.refused([("ok", "file", b"x"), ("c", "chr")], "unsafe_type")
        self.refused([("ok", "file", b"x"), ("b", "blk")], "unsafe_type")
        self.refused([("ok", "file", b"x"), ("f", "fifo")], "unsafe_type")

    def test_symlink_dir_trick_cannot_write_outside(self):
        self.refused([("link", "symlink", ".."), ("link/evil", "file", b"x")], "unsafe_type")
        self.assertFalse((self.root / "evil").exists())

    def test_duplicate_names(self):
        self.refused([("a", "file", b"1"), ("a", "file", b"2")], "duplicate")
        self.refused([("d/a", "file", b"1"), ("d/a", "file", b"2")], "duplicate")

    def test_case_collisions(self):
        self.refused([("a.txt", "file", b"1"), ("A.TXT", "file", b"2")], "case_collision")
        self.refused([("Dir/a", "file", b"1"), ("dir/b", "file", b"2")], "case_collision")
        self.refused([("dir/", "dir"), ("DIR/x", "file", b"2")], "case_collision")
        self.refused([("caf\u00e9", "file", b"1"), ("cafe\u0301", "file", b"2")], "case_collision")
        self.refused([("stra\u00dfe", "file", b"1"), ("STRASSE", "file", b"2")], "case_collision")

    def test_file_folder_conflicts(self):
        self.refused([("a", "file", b"1"), ("a/b", "file", b"2")], "duplicate")
        self.refused([("a/b", "file", b"2"), ("a", "file", b"1")], "duplicate")
        self.refused([("a/", "dir"), ("a", "file", b"1")], "duplicate")

    def test_same_folder_listed_twice_is_fine(self):
        self.assertEqual(self.extract([("a/", "dir"), ("a/", "dir"), ("a/x", "file", b"1")]), ["a/x"])

    def test_entry_count_cap(self):
        ok = [("f%d" % i, "file", b"") for i in range(pk.ENTRIES)]
        self.assertEqual(len(self.extract(ok)), pk.ENTRIES)
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_tar(tar_bytes([("g%d" % i, "file", b"") for i in range(pk.ENTRIES + 1)]), self.root / "many")
        self.assertEqual(cm.exception.code, "too_many")
        self.assertFalse((self.root / "many").exists())

    def test_dirs_count_toward_entry_cap(self):
        entries = [("d%d/" % i, "dir") for i in range(pk.ENTRIES + 1)]
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_tar(tar_bytes(entries), self.root / "dirs")
        self.assertEqual(cm.exception.code, "too_many")

    def test_entry_size_cap(self):
        edge = b"\x00" * pk.ENTRY_MAX
        self.assertEqual(self.extract([("edge", "file", edge)]), ["edge"])
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_tar(tar_bytes([("big", "file", edge + b"\x00")]), self.root / "big")
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse((self.root / "big").exists())

    def test_total_unpacked_cap(self):
        chunk = b"\x00" * (pk.TOTAL_MAX // 4 + 1)
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_tar(tar_bytes([("f%d" % i, "file", chunk) for i in range(4)] + [("g", "file", b"x")]), self.root / "total")
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse((self.root / "total").exists())

    def test_archive_size_cap_and_wrong_types(self):
        for data in (b"\x00" * (pk.ARCHIVE_MAX + 1), None, "text", 5, []):
            with self.assertRaises(pk.PkgError):
                pk.safe_extract_tar(data, self.dest)
        self.assertEqual(self.tree(), [])

    def test_not_a_gzip_tar(self):
        for data in (b"", b"hello", b"\x1f\x8b" + b"\x00" * 20, tar_bytes([("a", "file", b"x")])[:-10], b"PK\x03\x04" + b"\x00" * 30):
            with self.assertRaises(pk.PkgError) as cm:
                pk.safe_extract_tar(data, self.dest)
            self.assertEqual(cm.exception.code, "bad_archive")

    def test_uncompressed_tar_is_not_accepted(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            ti = tarfile.TarInfo("a")
            ti.size = 1
            tf.addfile(ti, io.BytesIO(b"x"))
        with self.assertRaises(pk.PkgError):
            pk.safe_extract_tar(buf.getvalue(), self.dest)

    def test_decompression_cap_wrapper(self):
        cap = pk._Cap(io.BytesIO(b"x" * 100), 50)
        self.assertEqual(len(cap.read(10)), 10)
        with self.assertRaises(pk.PkgError) as cm:
            while cap.read(64):
                pass
        self.assertEqual(cm.exception.code, "too_large")

    def test_long_names_within_limits_work(self):
        name = "d/" + "n" * 200
        self.assertEqual(self.extract([(name, "file", b"x")]), [name])

    def test_existing_file_is_never_overwritten(self):
        self.dest.mkdir()
        (self.dest / "a").write_bytes(b"mine")
        with self.assertRaises(pk.PkgError):
            self.extract([("a", "file", b"theirs")])
        self.assertEqual((self.dest / "a").read_bytes(), b"mine")

    def test_empty_archive_extracts_nothing(self):
        self.assertEqual(self.extract([]), [])

    @POSIX
    def test_symlink_in_destination_is_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.dest.mkdir()
        os.symlink(outside, self.dest / "sub")
        with self.assertRaises(pk.PkgError):
            self.extract([("sub/evil", "file", b"x")])
        self.assertEqual(list(outside.iterdir()), [])

    @POSIX
    def test_modes_ignore_archive_bits(self):
        self.extract([("a", "file", b"x", 0o4777), ("b", "file", b"x", 0o2755), ("c", "file", b"x", 0o1777), ("run", "file", b"x", 0o600)], exec_names=("run",))
        for n in "abc":
            self.assertEqual((self.dest / n).stat().st_mode & 0o7111, 0, n)
        self.assertEqual((self.dest / "run").stat().st_mode & 0o7777, 0o755)

    @POSIX
    def test_exec_names_match_relative_path_after_strip(self):
        files = self.extract([("top/bin/tool", "file", b"x"), ("top/tool", "file", b"y")], top="top", exec_names=("bin/tool",))
        self.assertEqual(files, ["bin/tool", "tool"])
        self.assertEqual((self.dest / "bin" / "tool").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.dest / "tool").stat().st_mode & 0o111, 0)


def patch_flags(data, bit):
    i = data.find(b"PK\x01\x02")
    flags = int.from_bytes(data[i + 8:i + 10], "little") | bit
    return data[:i + 8] + flags.to_bytes(2, "little") + data[i + 10:]


def patch_eocd(data, offset, value, width):
    i = data.rfind(b"PK\x05\x06")
    return data[:i + offset] + value.to_bytes(width, "little") + data[i + offset + width:]


class ZipHostile(Tmp):
    def extract(self, entries, **kw):
        return pk.safe_extract_zip(zip_bytes(entries), self.dest, **kw)

    def refused(self, data_or_entries, code=None, **kw):
        data = data_or_entries if isinstance(data_or_entries, bytes) else zip_bytes(data_or_entries)
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_zip(data, self.dest, **kw)
        if code:
            self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.listing(), [])
        self.assertEqual([p for p in self.tree() if not p.startswith("out")], [])

    def test_plain_and_auto_strip(self):
        self.assertEqual(self.extract([("repo-1/", b""), ("repo-1/a.txt", b"A"), ("repo-1/sub/b.txt", b"B")]), ["a.txt", "sub/b.txt"])
        out = self.root / "o2"
        self.assertEqual(pk.safe_extract_zip(zip_bytes([("repo-1/a.txt", b"A")]), out, strip=0), ["repo-1/a.txt"])
        out = self.root / "o3"
        self.assertEqual(pk.safe_extract_zip(zip_bytes([("a.txt", b"A"), ("d/b", b"B")]), out), ["a.txt", "d/b"])

    def test_traversal_and_absolute(self):
        for name in ("../evil", "a/../../evil", "/etc/passwd", "a/./b", "a//b", "..\\evil", "C:/x", "a/b:s", "a/b|c", "x" * 256, "trail."):
            with self.subTest(name=name):
                self.refused([("ok", b"x"), (name, b"x")], "unsafe_path")
        self.assertFalse((self.root / "evil").exists())

    def test_symlink_and_special_modes_refused(self):
        self.refused([("ok", b"x"), ("l", b"/etc/passwd", 0o120777)], "unsafe_type")
        self.refused([("ok", b"x"), ("c", b"", 0o020666)], "unsafe_type")
        self.refused([("ok", b"x"), ("f", b"", 0o010644)], "unsafe_type")
        self.refused([("ok", b"x"), ("s", b"", 0o140755)], "unsafe_type")
        self.refused([("d/", b"", 0o100644)], "unsafe_type")
        self.refused([("notdir", b"", 0o040755)], "unsafe_type")

    def test_encrypted_and_exotic_compression_refused(self):
        self.refused(patch_flags(zip_bytes([("a", b"x")]), 1), "unsafe_type")
        self.refused(zip_bytes([("a", b"x")], zipfile.ZIP_BZIP2), "unsafe_type")
        self.refused(zip_bytes([("a", b"x")], zipfile.ZIP_LZMA), "unsafe_type")

    def test_duplicates_and_case_collisions(self):
        self.refused([("a", b"1"), ("a", b"2")], "duplicate")
        self.refused([("a.txt", b"1"), ("A.TXT", b"2")], "case_collision")
        self.refused([("Dir/a", b"1"), ("dir/b", b"2")], "case_collision")
        self.refused([("a", b"1"), ("a/b", b"2")], "duplicate")
        self.refused([("a/b", b"2"), ("a", b"1")], "duplicate")

    def test_entry_and_total_caps(self):
        self.assertEqual(self.extract([("edge", b"\x00" * pk.ENTRY_MAX)]), ["edge"])
        out = self.root / "big"
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_zip(zip_bytes([("big", b"\x00" * (pk.ENTRY_MAX + 1))]), out)
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse(out.exists())
        chunk = b"\x00" * (pk.TOTAL_MAX // 4 + 1)
        out = self.root / "total"
        with self.assertRaises(pk.PkgError) as cm:
            pk.safe_extract_zip(zip_bytes([("f%d" % i, chunk) for i in range(4)] + [("g", b"x")]), out)
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse(out.exists())

    def test_entry_count_cap_and_central_directory_lies(self):
        out = self.root / "ok"
        self.assertEqual(len(pk.safe_extract_zip(zip_bytes([("f%d" % i, b"") for i in range(pk.ENTRIES)]), out)), pk.ENTRIES)
        many = zip_bytes([("g%d" % i, b"") for i in range(pk.ENTRIES + 1)])
        for data in (many, patch_eocd(zip_bytes([("a", b"x")]), 10, 0xFFFF, 2), patch_eocd(zip_bytes([("a", b"x")]), 12, 2_000_000, 4)):
            with self.assertRaises(pk.PkgError) as cm:
                pk.safe_extract_zip(data, self.root / "many")
            self.assertEqual(cm.exception.code, "too_many")
            self.assertFalse((self.root / "many").exists())

    def test_not_a_zip_and_oversize(self):
        for data in (b"", b"hello", b"PK\x05\x06" + b"\x00" * 5, tar_bytes([("a", "file", b"x")]), zip_bytes([("a", b"x" * 100)])[:-30], b"\x00" * (pk.ARCHIVE_MAX + 1), None, "x"):
            with self.assertRaises(pk.PkgError):
                pk.safe_extract_zip(data, self.dest)
        self.assertEqual(self.tree(), [])

    def test_damaged_member_data_is_refused(self):
        data = bytearray(zip_bytes([("a", b"hello world" * 50)]))
        data[40] ^= 0xFF
        with self.assertRaises(pk.PkgError):
            pk.safe_extract_zip(bytes(data), self.dest)

    def test_lying_size_in_header_cannot_exceed_declared(self):
        data = zip_bytes([("a", b"x" * 1000)])
        i = data.find(b"PK\x01\x02")
        lie = data[:i + 24] + (10).to_bytes(4, "little") + data[i + 28:]
        j = lie.find(b"PK\x03\x04")
        lie = lie[:j + 22] + (10).to_bytes(4, "little") + lie[j + 26:]
        with self.assertRaises(pk.PkgError):
            pk.safe_extract_zip(lie, self.dest)

    def test_existing_file_is_never_overwritten(self):
        self.dest.mkdir()
        (self.dest / "a").write_bytes(b"mine")
        with self.assertRaises(pk.PkgError):
            self.extract([("a", b"theirs")])
        self.assertEqual((self.dest / "a").read_bytes(), b"mine")

    @POSIX
    def test_modes_ignore_archive_bits(self):
        self.extract([("a", b"x", 0o104777), ("run", b"x", 0o100600)], exec_names=["run"], strip=0)
        self.assertEqual((self.dest / "a").stat().st_mode & 0o7111, 0)
        self.assertEqual((self.dest / "run").stat().st_mode & 0o7777, 0o755)


class Hygiene(unittest.TestCase):
    def test_modules_have_no_comments_or_docstrings_and_use_stdlib_only(self):
        stdlib = {"ast", "asyncio", "copy", "gzip", "hashlib", "hmac", "io", "json", "math", "os", "platform", "re", "shutil", "stat", "tarfile", "tempfile", "time", "unicodedata", "urllib", "zipfile", "zlib", "pathlib"}
        for mod in (pk, __import__("fy_state")):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.assertIsNone(ast.get_docstring(node), mod.__name__)
                if isinstance(node, ast.Import):
                    self.assertTrue({a.name.split(".")[0] for a in node.names} <= stdlib, mod.__name__)
                if isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module.split(".")[0], stdlib, mod.__name__)
            toks = tokenize.generate_tokens(iter(src.splitlines(True)).__next__)
            self.assertEqual([t.string for t in toks if t.type == tokenize.COMMENT], [], mod.__name__)

    def test_import_has_no_side_effects(self):
        import subprocess

        code = "import sys; sys.path.insert(0, %r); import fy_pkg, fy_state; print(sorted(m for m in sys.modules if m in ('ssl','asyncio','urllib.request','http.client','subprocess')))" % str(Path(pk.__file__).parent)
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30, cwd=tempfile.gettempdir())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


class LinkedTreeTests(unittest.TestCase):
    def test_a_linked_git_checkout_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "dev"
            (real / ".git").mkdir(parents=True)
            link = Path(d) / "fan"
            try:
                os.symlink(real, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not permitted here")
            with self.assertRaises(pk.PkgError) as c:
                pk.replace_guard(link)
            self.assertEqual(c.exception.code, "linked_tree")
            pk.replace_guard(Path(d) / "absent")
            self.assertTrue((real / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
