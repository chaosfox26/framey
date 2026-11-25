"""Owned TLS helper profile: fresh verified platform trust, distinct from cached certifi.

Only synthetic package fixtures and in-memory TLS engines execute here.
"""

import builtins
import importlib.util
import json
import logging
import os
import ssl
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BRIDGE = ROOT / "decky_runtime/compat/decky_backend.py"


def load_candidate():
    spec = importlib.util.spec_from_file_location("framey_owned_tls_candidate", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TLSProperties(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                k: v
                for k, v in os.environ.items()
                if k not in {"SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"}
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.module = load_candidate()
        helpers = self.module._helpers_module()
        self.assertTrue(
            hasattr(helpers, "get_ssl_context"), "Current helpers module lacks get_ssl_context"
        )
        self.factory = helpers.get_ssl_context

    def test_context_requires_server_certificate_and_hostname(self):
        context = self.factory()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.protocol, ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_mutating_one_context_does_not_weaken_the_next(self):
        first = self.factory()
        first.check_hostname = False
        first.verify_mode = ssl.CERT_NONE
        second = self.factory()
        self.assertIsNot(first, second)
        self.assertEqual(second.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(second.check_hostname)

    def test_platform_trust_environment_loads_only_owned_public_ca(self):
        with tempfile.TemporaryDirectory(prefix="framey-owned-tls-trust-") as directory:
            root = Path(directory)
            ca = root / "ca.pem"
            ca.write_bytes((HERE / "fixtures/owned-tls-ca.pem").read_bytes())
            empty = root / "empty-cadir"
            empty.mkdir()
            with patch.dict(os.environ, {"SSL_CERT_FILE": str(ca), "SSL_CERT_DIR": str(empty)}):
                context = self.factory()
            expected = ssl.PEM_cert_to_DER_cert(ca.read_text())
            self.assertEqual(context.get_ca_certs(binary_form=True), [expected])
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)

    def test_empty_platform_trust_does_not_disable_verification(self):
        with tempfile.TemporaryDirectory(prefix="framey-owned-empty-trust-") as directory:
            root = Path(directory)
            with patch.dict(
                os.environ, {"SSL_CERT_FILE": str(root / "absent.pem"), "SSL_CERT_DIR": str(root)}
            ):
                context = self.factory()
            self.assertEqual(context.get_ca_certs(binary_form=True), [])
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)

    def test_default_trust_loader_failure_propagates_without_unverified_fallback(self):
        with patch.object(
            ssl.SSLContext, "load_default_certs", side_effect=PermissionError("owned trust error")
        ):
            with self.assertRaises(PermissionError):
                self.factory()

    def test_context_does_not_replace_global_https_factory(self):
        original = ssl._create_default_https_context
        self.factory()
        self.assertIs(ssl._create_default_https_context, original)

    def test_hostname_is_required_for_in_memory_client_object(self):
        context = self.factory()
        with self.assertRaises(ValueError):
            context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_side=False)


class CurrentBridgeHelperImports(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="framey-owned-helper-import-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = load_candidate()
        self.instance = None
        self.environment = patch.dict(
            os.environ,
            {
                k: v
                for k, v in os.environ.items()
                if k not in {"SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"}
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def asyncTearDown(self):
        if self.instance is not None:
            await self.instance.stop()

    def backend(self, source, extra=None):
        original = self.root / "owned-synthetic-package"
        original.mkdir()
        (original / "plugin.json").write_text(
            json.dumps({"name": "owned TLS fixture", "api_version": 2, "flags": []})
        )
        (original / "package.json").write_text('{"version":"1.0.0"}')
        (original / "main.py").write_text(source)
        for name, text in (extra or {}).items():
            path = original / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.instance = self.module.DeckyBackend(
            original,
            self.root / "config",
            self.root / "data",
            self.root / "logs",
            logger=logging.getLogger("owned TLS import"),
        )
        return self.instance

    async def test_current_bridge_import_and_call_resolve_helpers_tls_factory(self):
        backend = self.backend(
            "from helpers import get_ssl_context\nclass Plugin:\n    async def inspect(self):\n        context = get_ssl_context()\n        return [int(context.verify_mode), context.check_hostname, int(context.protocol)]\n"
        )
        metadata = await backend.start()
        self.assertEqual(metadata["framey_patches"]["applied"], [])
        self.assertEqual(
            await backend.call("inspect", []),
            [int(ssl.CERT_REQUIRED), True, int(ssl.PROTOCOL_TLS_CLIENT)],
        )

    async def test_captured_owned_ssl_module_ignores_synthetic_dependency_shadow(self):
        backend = self.backend(
            "from helpers import get_ssl_context\nclass Plugin:\n    async def inspect(self):\n        return get_ssl_context().check_hostname\n",
            {"py_modules/ssl.py": 'raise AssertionError("OWNED SHADOW MUST NOT RUN")\n'},
        )
        await backend.start()
        self.assertIs(await backend.call("inspect", []), True)

    async def test_optional_ssl_unavailable_does_not_block_unrelated_identity_startup(self):
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name == "ssl":
                raise ImportError("owned missing ssl support")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=unavailable):
            self.module = load_candidate()
            self.assertEqual(self.module._helpers_module().get_user_id(), os.getuid())
            backend = self.backend(
                "from helpers import get_user_id\nclass Plugin:\n    async def inspect(self):\n        return get_user_id()\n"
            )
            await backend.start()
            self.assertEqual(await backend.call("inspect", []), os.getuid())

    async def test_unavailable_ssl_fails_only_on_helper_use_without_shadow_fallback(self):
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name == "ssl":
                raise ImportError("owned missing ssl support")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=unavailable):
            self.module = load_candidate()
        backend = self.backend(
            "from helpers import get_ssl_context\nclass Plugin:\n    async def inspect(self):\n        return get_ssl_context()\n",
            {"py_modules/ssl.py": 'raise AssertionError("UNTRUSTED SSL FALLBACK EXECUTED")\n'},
        )
        await backend.start()
        with self.assertRaisesRegex(ImportError, "TLS support is unavailable"):
            await backend.call("inspect", [])
        await backend.stop()
        self.assertIs(sys.modules.get("ssl"), ssl)

    async def test_bundled_helpers_preference_and_host_module_restoration_remain(self):
        host = types.ModuleType("helpers")
        host.marker = "HOST_HELPERS_UNCHANGED"
        with patch.dict(sys.modules, {"helpers": host}):
            backend = self.backend(
                "from helpers import get_ssl_context\nclass Plugin:\n    async def inspect(self):\n        return get_ssl_context()\n",
                {"helpers.py": 'def get_ssl_context():\n    return "OWNED_BUNDLED_HELPER"\n'},
            )
            await backend.start()
            self.assertEqual(await backend.call("inspect", []), "OWNED_BUNDLED_HELPER")
            await backend.stop()
            self.assertIs(sys.modules["helpers"], host)


PRIME = """import sys,types
from helpers import get_ssl_context
primed_ssl=types.ModuleType("ssl")
def forbidden_factory(*args,**kwargs):
    raise AssertionError("PRIMED_SSL_FACTORY_MUST_NOT_RUN")
primed_ssl.create_default_context=forbidden_factory
primed_ssl._create_default_https_context=forbidden_factory
primed_native=types.ModuleType("_ssl")
primed_native.SSLContext=forbidden_factory
sys.modules["ssl"]=primed_ssl
sys.modules["_ssl"]=primed_native
"""


class ExtraTrustTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owned-extra-trust-", dir=None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance = None
        self.host_modules = {name: sys.modules.get(name) for name in ["ssl", "_ssl"]}
        self.host_path = list(sys.path)
        self.https_factory = ssl._create_default_https_context
        self.default_factory = ssl.create_default_context
        self.environment = patch.dict(
            os.environ,
            {
                k: v
                for k, v in os.environ.items()
                if k not in {"SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"}
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def asyncTearDown(self):
        if self.instance is not None:
            await self.instance.stop()
        self.assertEqual(sys.path, self.host_path)
        for name, value in self.host_modules.items():
            self.assertIs(sys.modules.get(name), value)
        self.assertIs(ssl._create_default_https_context, self.https_factory)
        self.assertIs(ssl.create_default_context, self.default_factory)

    def backend(self, module, body):
        original = self.root / "owned-original"
        original.mkdir()
        (original / "plugin.json").write_text(
            json.dumps({"name": "owned primed SSL fixture", "api_version": 2, "flags": []})
        )
        (original / "package.json").write_text('{"version":"1.0.0"}')
        (original / "main.py").write_text(body)
        modules = original / "py_modules"
        modules.mkdir()
        for name in ["ssl.py", "_ssl.py"]:
            (modules / name).write_text(
                'raise AssertionError("OWNED_BUNDLED_SSL_SHADOW_MUST_NOT_RUN")\n'
            )
        self.instance = module.DeckyBackend(
            original,
            self.root / "config",
            self.root / "data",
            self.root / "logs",
            logger=logging.getLogger("owned extra trust"),
        )
        return self.instance

    async def test_available_capture_ignores_both_primed_module_names_and_bundled_shadows(self):
        module = load_candidate()
        backend = self.backend(
            module,
            PRIME
            + """class Plugin:
    async def inspect(self):
        first=get_ssl_context()
        second=get_ssl_context()
        return [first.check_hostname,int(first.verify_mode),int(first.protocol),first is not second]
""",
        )
        await backend.start()
        self.assertEqual(
            await backend.call("inspect", []),
            [True, int(ssl.CERT_REQUIRED), int(ssl.PROTOCOL_TLS_CLIENT), True],
        )
        self.assertIs(module.ssl, self.host_modules["ssl"])

    async def test_missing_ssl_capture_ignores_primed_names_and_fails_only_on_tls_use(self):
        imported = builtins.__import__

        def missing(name, *args, **kwargs):
            if name == "ssl":
                raise ImportError("owned unavailable ssl")
            return imported(name, *args, **kwargs)

        with patch.object(builtins, "__import__", missing):
            module = load_candidate()
        backend = self.backend(
            module,
            PRIME
            + """import helpers
class Plugin:
    async def identity(self):
        return helpers.get_user_id()
    async def inspect(self):
        return get_ssl_context()
""",
        )
        await backend.start()
        self.assertEqual(await backend.call("identity", []), os.getuid())
        with self.assertRaisesRegex(ImportError, "TLS support is unavailable"):
            await backend.call("inspect", [])
        self.assertIsNone(module.ssl)


class TLSAcquisitionTests(unittest.TestCase):
    def test_bridge_import_and_helper_acquisition_do_not_construct_context(self):
        with patch.object(
            ssl, "create_default_context", side_effect=AssertionError("context created before use")
        ):
            module = load_candidate()
            self.assertTrue(
                callable(getattr(module._helpers_module(), "get_ssl_context", None)),
                "TLS helper export missing",
            )

    def test_missing_native_ssl_preserves_identity_and_fails_only_on_tls_use(self):
        imported = builtins.__import__

        def missing_native(name, *args, **kwargs):
            if name == "_ssl":
                raise ImportError("owned optional native ssl missing")
            return imported(name, *args, **kwargs)

        with patch.dict(sys.modules):
            sys.modules.pop("ssl", None)
            with patch.object(builtins, "__import__", missing_native):
                module = load_candidate()
            helpers = module._helpers_module()
            self.assertTrue(
                callable(getattr(helpers, "get_ssl_context", None)), "TLS helper export missing"
            )
            self.assertEqual(helpers.get_user_id(), os.getuid())
            with self.assertRaisesRegex(ImportError, "TLS support is unavailable"):
                helpers.get_ssl_context()

    def test_factory_rejects_unsupported_ca_or_verify_arguments(self):
        helpers = load_candidate()._helpers_module()
        self.assertTrue(
            callable(getattr(helpers, "get_ssl_context", None)), "TLS helper export missing"
        )
        with self.assertRaises(TypeError):
            helpers.get_ssl_context("custom-ca")
        with self.assertRaises(TypeError):
            helpers.get_ssl_context(verify=False)

    def test_factory_builds_offline_client_engine_without_network(self):
        helpers = load_candidate()._helpers_module()
        self.assertTrue(
            callable(getattr(helpers, "get_ssl_context", None)), "TLS helper export missing"
        )
        with patch.dict(
            os.environ,
            {
                k: v
                for k, v in os.environ.items()
                if k not in {"SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"}
            },
            clear=True,
        ):
            context = helpers.get_ssl_context()
        outgoing = ssl.MemoryBIO()
        client = context.wrap_bio(
            ssl.MemoryBIO(), outgoing, server_side=False, server_hostname="owned-fixture.invalid"
        )
        with self.assertRaises(ssl.SSLWantReadError):
            client.do_handshake()
        self.assertGreater(outgoing.pending, 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


if __name__ == "__main__":
    unittest.main()
