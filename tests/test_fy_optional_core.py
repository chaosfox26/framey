import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OptionalRuntimeCoreTests(unittest.TestCase):
    def run_core(self, body, *, legacy=False):
        with tempfile.TemporaryDirectory(prefix="framey-native-only-") as temporary:
            base = Path(temporary)
            core, home, xdg = (base / name for name in ("core", "home", "xdg"))
            for path in (core, home, xdg):
                path.mkdir(mode=0o700)
            for pattern in ("*.py", "*.js", "icon.svg"):
                for source in ROOT.glob(pattern):
                    if source.name not in {
                        "fy_decky_catalog.py",
                        "fy_decky_import.py",
                        "fy_decky_code.py",
                        "fy_code_http.py",
                    }:
                        shutil.copyfile(source, core / source.name)
            for name in ("core-files.txt", "LICENSE", "catalog/index.json"):
                target = core / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            plugins = core / "plugins"
            plugins.mkdir()
            if legacy:
                plugin = plugins / "legacy_fixture"
                plugin.mkdir()
                (plugin / "plugin.json").write_text(
                    json.dumps(
                        {
                            "id": "legacy_fixture",
                            "name": "Owned legacy fixture",
                            "version": "1.0.0",
                            "api": 1,
                            "decky_compat": {
                                "profile": 1,
                                "sha256": "a" * 64,
                                "experimental": True,
                            },
                        }
                    )
                )
                (plugin / "backend.py").write_text(
                    "raise AssertionError('A disabled legacy backend must not run')\n"
                )
                (plugin / "main.js").write_text(
                    "throw new Error('A disabled legacy frontend must not run');\n"
                )
            script = (
                "import asyncio,json,sys\n"
                f"sys.path.insert(0, {str(core)!r})\n"
                "import framey\n" + body
            )
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", script],
                cwd=core,
                env={**os.environ, "HOME": str(home), "XDG_RUNTIME_DIR": str(xdg)},
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((core / "decky_runtime").exists())
            return json.loads(result.stdout.strip().splitlines()[-1])

    def test_native_store_sources_work_without_any_optional_package_or_checkout_import(self):
        result = self.run_core("""
import fy_catalog
async def run():
    catalog = fy_catalog.Catalog(framey.host)
    sources = await catalog.sources()
    print(json.dumps({'sources': [s['id'] for s in sources['sources']],
                      'decky_sources': sources.get('decky_sources', []),
                      'optional_imports': [n for n in sys.modules
                                           if n.startswith('decky_runtime')]}))
asyncio.run(run())
""")
        self.assertEqual(
            result,
            {
                "sources": ["stable", "testing", "custom"],
                "decky_sources": [],
                "optional_imports": [],
            },
        )

    def test_absent_runtime_keeps_retained_legacy_backend_unregistered(self):
        result = self.run_core(
            """
async def run():
    try:
        framey.load_backends()
        status = framey.supervisor.status() if framey.supervisor else {}
        print(json.dumps({'registered': list(status),
                          'requested_disabled': framey.settings()['disabled']}))
    finally:
        await framey.down()
asyncio.run(run())
""",
            legacy=True,
        )
        self.assertEqual(result, {"registered": [], "requested_disabled": []})

    def test_absent_runtime_does_not_prepare_retained_legacy_frontend(self):
        result = self.run_core(
            """
prepared = framey.main_code()
print(json.dumps({'prepared': [entry[1] for entry in prepared
                                if entry[1] == 'legacy_fixture'],
                  'tokens': list(framey.TOKS.values())}))
""",
            legacy=True,
        )
        self.assertEqual(result, {"prepared": [], "tokens": []})

    def test_native_worker_sdk_call_reload_and_shutdown_with_package_physically_absent(self):
        result = self.run_core("""
from pathlib import Path
plugin = framey.PLUGINS / 'owned_native'
plugin.mkdir()
(plugin / 'plugin.json').write_text(json.dumps({
    'id': 'owned_native', 'name': 'Owned native fixture', 'version': '1.0.0',
    'api': 1, 'backend': {'memory_mb': 64},
}))
(plugin / 'backend.py').write_text(
    'import framey_api as fy\\n'
    'async def call(method, arg):\\n'
    '    return {"echo": arg, "api": fy.API, "plugin": fy.PLUGIN}\\n'
)
async def run():
    try:
        framey.load_backends()
        first = await asyncio.wait_for(framey.backend('owned_native', 'echo', 42), 6)
        await framey.reload('owned_native')
        second = await asyncio.wait_for(framey.backend('owned_native', 'echo', 43), 6)
    finally:
        await framey.down()
    print(json.dumps({'first': first, 'second': second,
                      'optional_imports': [n for n in sys.modules
                                           if n.startswith('decky_runtime')]}))
asyncio.run(run())
""")
        self.assertEqual(
            result,
            {
                "first": {"echo": 42, "api": 1, "plugin": "owned_native"},
                "second": {"echo": 43, "api": 1, "plugin": "owned_native"},
                "optional_imports": [],
            },
        )

    def test_recovery_rejects_legacy_loader_that_would_bypass_optional_admission(self):
        result = self.run_core("""
from pathlib import Path
legacy = framey.HOME / 'legacy_backup'
legacy.mkdir()
(legacy / 'framey.py').write_text('def main(): pass\\nVERSION = "1.0.2"\\n')
print(json.dumps({'native_eligible': framey.loader_ok(framey.ROOT),
                  'legacy_eligible': framey.loader_ok(legacy)}))
""")
        self.assertEqual(result, {"native_eligible": True, "legacy_eligible": False})

    def test_core_update_filters_optional_payload_and_preserves_external_runtime(self):
        result = self.run_core("""
import io, zipfile
external = framey.HOME / '.local/share/framey/runtimes/decky/retained'
external.mkdir(parents=True)
(external / 'sentinel').write_text('keep')
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, 'w') as archive:
    for path in framey.ROOT.rglob('*'):
        if path.is_file():
            archive.writestr('source/' + path.relative_to(framey.ROOT).as_posix(), path.read_bytes())
    archive.writestr('source/decky_runtime/unwanted.py', 'raise AssertionError("not core")')
    archive.writestr('source/compat/decky-host.js', 'throw Error("not core")')
framey.archive = lambda *args: buffer.getvalue()
async def run():
    result = await framey.install_loader('v2.0.0', 'b' * 40)
    print(json.dumps({'updated': result.get('ok'),
                      'optional_directory': (framey.ROOT / 'decky_runtime').exists(),
                      'embedded_helper': (framey.ROOT / 'compat/decky-host.js').exists(),
                      'external_data': (external / 'sentinel').read_text()}))
asyncio.run(run())
""")
        self.assertEqual(
            result,
            {
                "updated": "framey",
                "optional_directory": False,
                "embedded_helper": False,
                "external_data": "keep",
            },
        )


if __name__ == "__main__":
    unittest.main()
