import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_framey():
    spec = importlib.util.spec_from_file_location("framey_open_url_auth", ROOT / "framey.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenUrlAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fy = load_framey()
        self.token = self.fy.new_tok("owner")

    def test_plugin_token_binds_both_owner_fields_and_preserves_input(self):
        forged = {
            "pid": "foreign",
            "id": "foreign",
            "url": "https://example.org/",
            "source": "decky_stable",
            "token": self.fy.CORE_TOK,
        }
        allowed, args = self.fy.authorize(
            {
                "plugin": "_core",
                "method": "open_url",
                "t": self.token,
                "arg": forged,
            }
        )
        self.assertTrue(allowed)
        self.assertEqual(args["pid"], "owner")
        self.assertEqual(args["id"], "owner")
        self.assertEqual(args["url"], "https://example.org/")
        self.assertEqual(forged["pid"], "foreign")
        self.assertEqual(forged["id"], "foreign")

    def test_core_unknown_missing_and_revoked_tokens_are_denied(self):
        self.fy.drop_tok("owner")
        for token in (self.fy.CORE_TOK, "unknown-token", self.token, None, 42):
            with self.subTest(token=token):
                allowed, _ = self.fy.authorize(
                    {
                        "plugin": "_core",
                        "method": "open_url",
                        "t": token,
                        "arg": {"pid": "owner", "id": "owner", "url": "https://example.org/"},
                    }
                )
                self.assertFalse(allowed)

    def test_replacement_token_revokes_the_previous_handle(self):
        replacement = self.fy.new_tok("owner")
        self.assertFalse(self.fy.authorize({"method": "open_url", "t": self.token})[0])
        allowed, args = self.fy.authorize({"method": "open_url", "t": replacement})
        self.assertTrue(allowed)
        self.assertEqual(args, {"pid": "owner", "id": "owner"})

    def test_real_host_enabled_requires_installed_and_not_disabled_identity(self):
        enabled = getattr(self.fy.host, "enabled", None)
        self.assertTrue(
            callable(enabled), "Host must expose the native browser authorization callback"
        )
        with (
            mock.patch.object(
                self.fy, "manifests", return_value=[{"id": "owner"}, {"id": "disabled"}]
            ),
            mock.patch.object(self.fy, "settings", return_value={"disabled": ["disabled"]}),
        ):
            self.assertIs(enabled("owner"), True)
            for pid in ("disabled", "missing", "", None, 42, True):
                with self.subTest(pid=pid):
                    self.assertIs(enabled(pid), False)

    async def test_core_dispatches_only_the_authenticated_owner_to_native_module(self):
        received = []

        async def native_open(host, args):
            received.append((host, args))
            return {"requested": True}

        native = SimpleNamespace(METHODS={"open_url": native_open})
        allowed, args = self.fy.authorize(
            {
                "plugin": "_core",
                "method": "open_url",
                "t": self.token,
                "arg": {"pid": "foreign", "id": "foreign", "url": "https://example.org/"},
            }
        )
        self.assertTrue(allowed)
        with mock.patch.object(
            self.fy, "use", side_effect=lambda name: native if name == "fy_browser" else None
        ):
            result = await self.fy.core("open_url", args)
        self.assertEqual(result, {"requested": True})
        self.assertEqual(
            received,
            [
                (
                    self.fy.host,
                    {
                        "pid": "owner",
                        "id": "owner",
                        "url": "https://example.org/",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
