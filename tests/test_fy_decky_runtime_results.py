import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog


class DeckyRuntimeClassificationTests(unittest.TestCase):
    def test_manifest_runtime_claims_and_unknown_backend_apis_never_prove_a_port(self):
        manifest = {
            "name": "Runtime claims fixture",
            "flags": [],
            "api": 1,
            "platform": ["linux-aarch64"],
            "platform_declared": True,
            "eligible": True,
            "read_only": False,
            "compatibility": {
                "status": "compatible",
                "basis": "declared_requirements",
                "runtime_verified": True,
            },
        }
        backend = b"class UnknownLoaderPlugin:\n    async def _main(self):\n        raise AssertionError('not a Framey backend API')\n"
        for backend_path in ("main.py", "backend.py"):
            with self.subTest(backend_path=backend_path):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("plugin.json", json.dumps(manifest))
                    archive.writestr("package.json", json.dumps({
                        "name": "runtime-claims-fixture",
                        "dependencies": {"@decky/api": "1.0.0", "@decky/ui": "1.0.0"},
                    }))
                    archive.writestr("dist/index.js", 'import { callable } from "@decky/api";\nexport const read = callable("read");\n')
                    archive.writestr(backend_path, backend)
                data = stream.getvalue()
                result = fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest())
                self.assertEqual(result["state"], "port_required")
                self.assertEqual(result["status"], "incompatible")
                self.assertEqual(result["architecture"], "unknown")
                self.assertIs(result["not_executed"], True)
                self.assertIs(result["verified_sha256"], True)
                self.assertIs(result["has_frontend"], True)
                self.assertIn("@decky/api", result["dependencies"])
                self.assertFalse(result.get("eligible", False))
                self.assertFalse(result.get("runtime_verified", False))


if __name__ == "__main__":
    unittest.main()
