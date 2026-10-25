import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fy_catalog
import fy_decky_catalog
import fy_net
from test_fy_catalog_flow import FakeHost, catalog_data, plugin


class NativeCacheRestart(unittest.IsolatedAsyncioTestCase):
    async def test_cjk_catalog_survives_restart_offline_without_expanding_feed_limit(self):
        data = catalog_data([plugin("entry-" + str(i), "Example", "界" * 600, "tools", [], "") for i in range(200)])
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(raw), fy_catalog.CATALOG_MAX)
        self.assertGreater(len(fy_catalog._json(fy_catalog.parse_catalog(raw))), fy_catalog.CATALOG_MAX)
        calls = []
        async def fetch(url, max_bytes):
            calls.append(url)
            if len(calls) > 1:
                raise OSError("offline")
            self.assertEqual(max_bytes, fy_catalog.CATALOG_MAX)
            return raw
        with tempfile.TemporaryDirectory() as temporary:
            host = FakeHost(Path(temporary))
            service = fy_catalog.Catalog(host, fetcher=fetch)
            first = await service.list()
            self.assertEqual(first["total"], 200)
            self.assertLess(service.path.stat().st_size, fy_catalog.CACHE_MAX)
            restarted = fy_catalog.Catalog(host, fetcher=fetch)
            cached = await restarted.list()
            self.assertEqual(cached["total"], 200)
            self.assertEqual(len(calls), 1)
            host.clock += fy_catalog.TTL + 1
            stale = await restarted.list()
            self.assertEqual(stale["total"], 200)
            self.assertTrue(stale["stale"])
            self.assertEqual(len(calls), 2)
        with self.assertRaises(fy_catalog.CatalogError):
            fy_catalog.parse_catalog(b" " * (fy_catalog.CATALOG_MAX + 1))


class MachOWidthEvidence(unittest.TestCase):
    def test_cpu_abi_width_must_match_mach_o_header_width(self):
        for endian, magic32, magic64 in (("<", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"), (">", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf")):
            for cpu, architecture in ((0x100000C, "aarch64"), (0x1000007, "x86_64")):
                with self.subTest(endian=endian, cpu=cpu):
                    header32 = magic32 + struct.pack(endian + "I", cpu) + bytes(20)
                    header64 = magic64 + struct.pack(endian + "I", cpu) + bytes(24)
                    self.assertEqual(fy_decky_catalog._native_header(header32), ("Mach-O", "unknown"))
                    self.assertEqual(fy_decky_catalog._native_header(header64), ("Mach-O", architecture))


class InspectionLimitReason(unittest.IsolatedAsyncioTestCase):
    async def test_overlimit_download_explains_limit_and_releases_retry(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("plugin.json", '{"name":"Example","flags":[]}')
        payload = payload.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        feed = [{"id": 1, "name": "Example", "versions": [{"name": "1.0", "hash": digest}]}]
        attempts = []
        async def fetch(url, max_bytes):
            return json.dumps(feed).encode()
        async def archive(url, max_bytes):
            attempts.append(url)
            self.assertEqual(max_bytes, 5_000_000)
            if len(attempts) == 1:
                raise fy_net.NetError("too_large", "private internal details")
            return payload
        with tempfile.TemporaryDirectory() as temporary:
            service = fy_decky_catalog.DeckyCatalog(FakeHost(Path(temporary)), fetcher=fetch, archive_fetcher=archive)
            details = await service.details({"source": "decky_stable", "id": "decky-1"})
            entry = details["entry"]
            request = {"source": "decky_stable", "id": entry["id"], "identity": entry["identity"],
                       "version_identity": entry["versions"][0]["identity"], "confirm": True}
            result = await service.assess(request)
            self.assertEqual(result, {"code": "too_large", "error": "This package exceeds the 5 MB inspection limit; compatibility remains unverified."})
            self.assertIsNone(service.inspection)
            retried = await service.assess(request)
            self.assertEqual(retried["assessment"]["status"], "incompatible")
            self.assertTrue(retried["assessment"]["not_executed"])
            self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
