import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_decky_catalog as decky
import fy_worker


def inspection_package(marker, flags=None, native=True):
    manifest = {
        "name": "Counterexample", "author": "Fixture", "flags": flags or [],
        "platform": ["linux-aarch64"], "permissions": ["root", "filesystem", "subprocess"],
        "security_sandbox": True, "no_new_privs": True,
    }
    package = {
        "name": "counterexample", "version": "1.0.0", "dependencies": {},
        "scripts": {"preinstall": "touch " + str(marker)},
    }
    backend = "from pathlib import Path\nPath(%r).write_text('backend executed')\nraise RuntimeError('backend was imported')\n" % str(marker)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("package.json", json.dumps(package))
        archive.writestr("main.py", backend)
        archive.writestr("dist/index.js", "throw new Error('frontend must not execute during inspection');")
        archive.writestr("setup.sh", "#!/bin/sh\ntouch " + str(marker) + "\n")
        if native:
            elf = bytearray(64)
            elf[:7] = b"\x7fELF\x02\x01\x01"
            elf[16:18] = (2).to_bytes(2, "little")
            elf[18:20] = (183).to_bytes(2, "little")
            elf[20:24] = (1).to_bytes(4, "little")
            elf[52:54] = (64).to_bytes(2, "little")
            archive.writestr("bin/helper", elf)
    return data.getvalue()


@contextlib.contextmanager
def no_runtime_execution():
    failure = AssertionError("inspection attempted a runtime operation")
    with contextlib.ExitStack() as stack:
        for target in (
            "fy_worker.Worker", "fy_worker.privileges", "subprocess.Popen",
            "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
            "importlib.util.spec_from_file_location", "os.system", "os.execve",
            "zipfile.ZipFile.extract", "zipfile.ZipFile.extractall",
        ):
            stack.enter_context(mock.patch(target, side_effect=failure))
        yield


class StaticInspectionRuntimeIsolation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-isolation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.marker = self.root / "executed"

    def inspect(self, data):
        with no_runtime_execution():
            result = decky.inspect_archive(data, hashlib.sha256(data).hexdigest())
        self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue(result["not_executed"])
        self.assertTrue(result["verified_sha256"])
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertNotIn("permissions", result)
        self.assertNotIn("security_sandbox", result)
        self.assertNotIn("no_new_privs", result)
        return result

    def test_arm64_header_and_permission_claims_do_not_launch_backend(self):
        result = self.inspect(inspection_package(self.marker, ["root", "debug", "no_new_privs"]))
        self.assertTrue(result["has_backend"])
        self.assertTrue(result["root_required"])
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertEqual(result["flags"], ["root", "debug", "no_new_privs"])
        self.assertTrue(any("not verified" in reason or "does not establish" in reason for reason in result["reasons"]))

    def test_no_native_header_and_no_root_flag_do_not_prove_safety_or_compatibility(self):
        result = self.inspect(inspection_package(self.marker, native=False))
        self.assertFalse(result["root_required"])
        self.assertEqual(result["native_binaries"], [])
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["architecture_status"], "unverified")
        self.assertTrue(any("static inspection" in warning for warning in result["warnings"]))

    def test_fresh_inspector_process_does_not_even_import_runtime_modules(self):
        data = inspection_package(self.marker, ["root", "no_new_privs"])
        script = """
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
blocked = {'fy_worker', 'fy_supervisor', 'fy_sdk', 'framey'}
class RuntimeImportBlocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError('inspection imported runtime module: ' + fullname)
sys.meta_path.insert(0, RuntimeImportBlocker())
import fy_decky_catalog
data = sys.stdin.buffer.read()
result = fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest())
assert not blocked.intersection(sys.modules)
print(json.dumps({'state': result['state'], 'not_executed': result['not_executed']}))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(Path(decky.__file__).parent)],
            input=data, capture_output=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout), {"state": "port_required", "not_executed": True})
        self.assertFalse(self.marker.exists())


class CatalogAssessmentRuntimeIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_assessment_uses_metadata_not_worker_or_installer(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-assess-isolation-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        marker = root / "executed"
        plugins = root / "plugins"
        plugins.mkdir()
        artifact = inspection_package(marker, ["root"])
        digest = hashlib.sha256(artifact).hexdigest()
        host = type("Host", (), {
            "HOME": root, "CONF": root / "config" / "settings.json", "PLUGINS": plugins,
            "PLATFORM": "linux-aarch64", "now": lambda self: 1_790_000_000,
            "settings": lambda self: {}, "manifests": lambda self: [],
            "install_zip": mock.Mock(side_effect=AssertionError("inspection tried installation")),
            "worker": mock.Mock(side_effect=AssertionError("inspection requested worker")),
        })()
        requests = []

        async def metadata(url, max_bytes):
            requests.append(("metadata", url, max_bytes))
            return json.dumps([{
                "id": 1, "name": "Counterexample", "author": "Fixture", "tags": [],
                "versions": [{"name": "1.0.0", "hash": digest}],
            }]).encode()

        async def download(url, max_bytes):
            requests.append(("archive", url, max_bytes))
            return artifact

        catalog = decky.DeckyCatalog(host, fetcher=metadata, archive_fetcher=download)
        with no_runtime_execution():
            listing = await catalog.list()
            details = await catalog.details({"id": listing["rows"][0]["id"]})
            entry = details["entry"]
            self.assertEqual([request[0] for request in requests], ["metadata"])
            result = await asyncio.wait_for(catalog.assess({
                "source": "decky_stable", "id": entry["id"], "identity": entry["identity"],
                "version_identity": entry["versions"][0]["identity"], "confirm": True,
            }), 2)
        self.assertNotIn("error", result)
        self.assertTrue(result["read_only"])
        self.assertTrue(result["assessment"]["not_executed"])
        self.assertEqual(result["assessment"]["state"], "port_required")
        self.assertTrue(result["assessment"]["root_required"])
        self.assertEqual([request[0] for request in requests], ["metadata", "archive"])
        self.assertFalse(marker.exists())
        self.assertEqual(list(plugins.iterdir()), [])
        host.install_zip.assert_not_called()
        host.worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
