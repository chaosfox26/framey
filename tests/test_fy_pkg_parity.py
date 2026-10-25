import hashlib
import io
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_pkg as pk


class ZipExecutableParityTests(unittest.TestCase):
    def archive(self, entries):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for name, mode, system in entries:
                info = zipfile.ZipInfo(name)
                info.create_system = system
                info.external_attr = mode << 16
                archive.writestr(info, "fixture")
        return out.getvalue()

    @unittest.skipUnless(os.name == "posix", "requires POSIX executable permission bits")
    def test_default_strips_archive_executable_bits(self):
        data = self.archive([("pkg/tool", stat.S_IFREG | 0o755, 3)])
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(pk.safe_extract_zip(data, root), ["tool"])
            self.assertEqual((Path(root) / "tool").stat().st_mode & 0o111, 0)

    @unittest.skipUnless(os.name == "posix", "requires POSIX executable permission bits")
    def test_preserve_exec_uses_the_normalized_target_path(self):
        data = self.archive([("pkg/bin/tool", stat.S_IFREG | 0o751, 3)])
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(pk.safe_extract_zip(data, root, preserve_exec=True), ["bin/tool"])
            self.assertEqual(stat.S_IMODE((Path(root) / "bin/tool").stat().st_mode), 0o755)

    @unittest.skipUnless(os.name == "posix", "requires POSIX executable permission bits")
    def test_preserved_executables_do_not_retain_special_or_writable_bits(self):
        data = self.archive([("tool", stat.S_IFREG | 0o7777, 3)])
        with tempfile.TemporaryDirectory() as root:
            pk.safe_extract_zip(data, root, preserve_exec=True)
            self.assertEqual(stat.S_IMODE((Path(root) / "tool").stat().st_mode), 0o755)

    @unittest.skipUnless(os.name == "posix", "requires POSIX executable permission bits")
    def test_non_unix_or_group_only_execute_bits_are_not_preserved(self):
        data = self.archive([("dos", stat.S_IFREG | 0o755, 0), ("group", stat.S_IFREG | 0o654, 3)])
        with tempfile.TemporaryDirectory() as root:
            pk.safe_extract_zip(data, root, preserve_exec=True)
            for name in ("dos", "group"):
                self.assertEqual((Path(root) / name).stat().st_mode & 0o111, 0)

    @unittest.skipUnless(os.name == "posix", "requires POSIX executable permission bits")
    def test_explicit_executable_names_still_work(self):
        data = self.archive([("pkg/tool", stat.S_IFREG | 0o644, 3)])
        with tempfile.TemporaryDirectory() as root:
            pk.safe_extract_zip(data, root, exec_names=["tool"])
            self.assertEqual(stat.S_IMODE((Path(root) / "tool").stat().st_mode), 0o755)

    def test_preserve_exec_does_not_relax_archive_path_checks(self):
        data = self.archive([("../tool", stat.S_IFREG | 0o755, 3)])
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(pk.PkgError) as caught:
                pk.safe_extract_zip(data, root, preserve_exec=True)
            self.assertEqual(caught.exception.code, "unsafe_path")
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_preserve_exec_does_not_relax_special_file_checks(self):
        data = self.archive([("tool", stat.S_IFLNK | 0o755, 3)])
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(pk.PkgError) as caught:
                pk.safe_extract_zip(data, root, preserve_exec=True)
            self.assertEqual(caught.exception.code, "unsafe_type")
            self.assertEqual(list(Path(root).iterdir()), [])


class ReservedPluginIdTests(unittest.TestCase):
    def test_reserved_ids_are_rejected_with_a_specific_error(self):
        reserved = {"_core", "core", "modules", "settings", "store", "discover", "developer", "dev", "decky-store"}
        self.assertEqual(pk.RESERVED_PLUGIN_IDS, reserved)
        for plugin_id in reserved:
            with self.subTest(plugin_id=plugin_id):
                result = pk.normalize_manifest({"id": plugin_id})
                self.assertFalse(result["ok"])
                self.assertEqual(result["value"]["id"], "")
                self.assertIn("id is reserved for Framey", result["errors"])

    def test_ids_containing_reserved_words_are_still_valid(self):
        for plugin_id in ("dev-tools", "developer_tools", "storefront", "settings2", "_core_plugin", "my-modules"):
            with self.subTest(plugin_id=plugin_id):
                result = pk.normalize_manifest({"id": plugin_id})
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["value"]["id"], plugin_id)

    def test_other_manifest_fields_may_use_reserved_words(self):
        result = pk.normalize_manifest({"id": "example", "name": "Store", "short": "dev", "source": "core"})
        self.assertTrue(result["ok"], result)


class MaximumVersionParityTests(unittest.TestCase):
    def decision(self, maximum, loader):
        return pk.decide({"framey": {"max": maximum}}, loader, "linux-aarch64")

    def test_build_metadata_does_not_change_short_maximum_series(self):
        for maximum, loader in (
            ("1+build.5", "1.99.99"),
            ("1.9+build.5", "1.9.99"),
            ("v1.9+build.5.6", "1.9.99.1"),
            ("1.9+build.5", "1.9.3-rc.1"),
        ):
            with self.subTest(maximum=maximum, loader=loader):
                self.assertEqual(self.decision(maximum, loader)["state"], "ok")

    def test_short_maximum_series_still_rejects_the_next_series(self):
        for maximum, loader in (("1+build.5", "2.0"), ("1.9+build.5", "1.10")):
            with self.subTest(maximum=maximum, loader=loader):
                self.assertEqual(self.decision(maximum, loader)["badge"], "older")

    def test_prerelease_maximum_is_an_explicit_cap_even_when_short(self):
        for maximum, loader in (
            ("1.9-rc.1", "1.9-rc.2"),
            ("1.9-rc.1", "1.9"),
            ("1-rc.1", "1"),
            ("1.9-rc.1+build.5", "1.9.1-alpha"),
        ):
            with self.subTest(maximum=maximum, loader=loader):
                self.assertEqual(self.decision(maximum, loader)["badge"], "older")

    def test_prerelease_maximum_remains_inclusive(self):
        for maximum, loader in (
            ("1.9-rc.1", "1.9.0-rc.1"),
            ("1.9-rc.1+build.5", "1.9.0-rc.1+other.8"),
            ("1.9-rc.1", "1.9-alpha"),
        ):
            with self.subTest(maximum=maximum, loader=loader):
                self.assertEqual(self.decision(maximum, loader)["state"], "ok")

    def test_minimum_above_short_prerelease_maximum_is_invalid(self):
        for minimum in ("1.9-rc.2", "1.9", "1.9.1"):
            with self.subTest(minimum=minimum):
                manifest = {"id": "demo", "framey": {"min": minimum, "max": "1.9-rc.1"}}
                self.assertFalse(pk.normalize_manifest(manifest)["ok"])
                self.assertEqual(pk.decide(manifest, "1.9", "linux-aarch64")["state"], "incompatible")


class ArchitectureAliasTests(unittest.TestCase):
    def spec(self, platform="linux-arm64", **extra):
        value = {"name": "tool", "version": "1.0.0", "platform": platform,
                 "asset": "tool.zip", "sha256": "a" * 64, "size": 10, "exec": ["tool"]}
        value.update(extra)
        return value

    def test_only_equivalent_architecture_names_match(self):
        for declared, actual in (
            ("linux-arm64", "linux-aarch64"), ("linux-aarch64", "linux-arm64"),
            ("linux-amd64", "linux-x86_64"), ("linux-x86_64", "linux-amd64"),
        ):
            with self.subTest(declared=declared, actual=actual):
                self.assertEqual(pk.decide({"platform": [declared]}, "1.0.2", actual)["state"], "ok")

    def test_other_architectures_remain_incompatible(self):
        for declared, actual in (
            ("linux-arm64", "linux-amd64"), ("linux-amd64", "linux-aarch64"),
            ("linux-aarch64", "linux-arm"), ("linux-arm64", "linux-riscv64"),
        ):
            with self.subTest(declared=declared, actual=actual):
                self.assertEqual(pk.decide({"platform": [declared]}, "1.0.2", actual)["badge"], "platform")

    def test_manifest_normalizes_aliases_and_collapses_duplicates(self):
        result = pk.normalize_manifest({"id": "demo", "platform": ["linux-arm64", "linux-aarch64", "linux-amd64"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["value"]["platform"], ["linux-aarch64", "linux-x86_64"])

    def test_companion_filter_and_normalized_manifest_use_the_same_aliases(self):
        manifest = {"id": "demo", "companions": [self.spec(), self.spec("linux-amd64", name="other")]}
        result = pk.normalize_manifest(manifest)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["value"]["companions"][0]["platform"], "linux-aarch64")
        self.assertEqual([x["name"] for x in pk.companions(manifest, "linux-aarch64")], ["tool"])
        self.assertEqual([x["name"] for x in pk.companions(manifest, "linux-arm64")], ["tool"])
        self.assertEqual([x["name"] for x in pk.companions(manifest, "linux-x86_64")], ["other"])

    def test_companion_aliases_cannot_bypass_duplicate_detection(self):
        result = pk.normalize_manifest({"id": "demo", "companions": [self.spec(), self.spec("linux-aarch64")]})
        self.assertFalse(result["ok"])
        self.assertTrue(any("declared twice" in e for e in result["errors"]))

    def test_companion_install_accepts_only_the_canonical_equivalent(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("tool", "fixture")
        data = payload.getvalue()
        spec = self.spec(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        with tempfile.TemporaryDirectory() as root:
            result = pk.install_companion(data, spec, root, platform_id="linux-aarch64")
            self.assertEqual(result["files"], ["tool"])
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(pk.PkgError) as caught:
                pk.install_companion(data, spec, root, platform_id="linux-amd64")
            self.assertEqual(caught.exception.code, "platform")
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
