import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_catalog
import fy_decky_catalog
import fy_proto


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files:
            bundle.writestr(name, data)
    return output.getvalue()


def inspect(files):
    data = archive(files)
    return fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest())


class DeckyRuntimeProtocolTests(unittest.TestCase):
    def test_shared_catalog_decoder_refuses_exponent_overflow(self):
        for token in ("1e309", "-1e309", "1e9999"):
            with self.subTest(token=token):
                with self.assertRaises(fy_catalog.CatalogError):
                    fy_catalog._decode(('{"unused":[{"number":' + token + '}]}').encode())

    def test_inspection_metadata_refuses_nonfinite_values(self):
        for name in ("plugin.json", "package.json"):
            with self.subTest(name=name):
                files = {"plugin.json": b'{"flags":[]}', name: b'{"unused":{"number":1e309}}'}
                with self.assertRaises(fy_catalog.CatalogError):
                    inspect(files.items())

    def test_finite_metadata_and_inspection_result_round_trip(self):
        result = inspect([
            ("plugin.json", b'{"flags":[],"unused":1e308}'),
            ("package.json", b'{"dependencies":{},"unused":1e-999}'),
        ])
        self.assertTrue(result["not_executed"])
        self.assertLessEqual(len(fy_proto.dumps(result)), fy_proto.PAYLOAD_MAX)
        self.assertEqual(fy_proto.decode(fy_proto.encode({"result": result}))["result"], result)

    def test_unicode_binary_evidence_cannot_exceed_rpc_payload(self):
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        header[16:18] = (3).to_bytes(2, "little")
        header[18:20] = (183).to_bytes(2, "little")
        header[20:24] = (1).to_bytes(4, "little")
        header[52:54] = (64).to_bytes(2, "little")
        files = [("plugin.json", b'{"flags":[]}')]
        for index in range(100):
            name = "/".join(["\U0001f600" * 200] * 3 + ["\U0001f600" * 140 + str(index) + ".so"])
            files.append((name, header))
        data = archive(files)
        self.assertLess(len(data), fy_decky_catalog.ARCHIVE_MAX)
        try:
            assessment = fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest())
        except fy_catalog.CatalogError as error:
            self.assertIn(error.code, ("too_large", "bad_archive", "bad_package"))
            return
        result = {"id": "decky-1", "format": "decky", "read_only": True, "version": "1.0.0", "hash": "a" * 64, "assessment": assessment}
        self.assertLessEqual(len(fy_proto.dumps(result)), fy_proto.PAYLOAD_MAX)
        self.assertEqual(fy_proto.decode(fy_proto.encode({"re": 1, "ok": True, "result": result}))["result"], result)

    def test_inspection_reads_backend_and_frontend_without_executing_them(self):
        with tempfile.TemporaryDirectory(prefix="fy-decky-protocol-") as temporary:
            sentinel = Path(temporary) / "executed"
            backend = "from pathlib import Path\nPath(%r).write_text('executed')\n" % str(sentinel)
            frontend = "throw new Error('frontend executed');"
            result = inspect([
                ("plugin.json", json.dumps({"flags": []}).encode()),
                ("main.py", backend.encode()),
                ("dist/index.js", frontend.encode()),
                ("setup.py", backend.encode()),
            ])
            self.assertTrue(result["not_executed"])
            self.assertTrue(result["has_backend"])
            self.assertTrue(result["has_frontend"])
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
