import asyncio
import json
import pathlib
import sys
import types
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import fy_report as report


def host(**kwargs):
    return types.SimpleNamespace(**dict({"LOADER": report.REPOSITORY, "VERSION": "1.0.2",
                                        "API_GENERATION": 1, "PLATFORM": "linux-aarch64"}, **kwargs))


class DraftTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_is_local_bounded_and_relevant(self):
        context = {"plugin": {"id": "decky-3", "name": "Example", "version": "0.9.1", "source": "decky_testing"},
                   "error": "Inspection timed out", "diagnostics": [{"code": "core.callback_failed", "count": 3}]}
        with mock.patch.object(report.asyncio, "create_subprocess_exec", side_effect=AssertionError("no browser in preview")):
            result = await report.report_draft(host(), context)
        self.assertEqual(result["repository"], report.REPOSITORY)
        for expected in ("Example", "0.9.1", "Decky Store Testing", "1.0.2", "linux-aarch64",
                         "Framey Python runtime:", "Steps to reproduce", "Expected behavior", "Actual behavior",
                         "original package was not installed or executed"):
            self.assertIn(expected, result["body"])
        self.assertNotIn("decky-3", result["body"])
        self.assertIn("Session-wide", result["diagnostics"])
        self.assertNotIn("url", result)

    async def test_no_unneeded_diagnostics_are_collected(self):
        class MinimalHost:
            LOADER = report.REPOSITORY
            VERSION = "1.0.2"
            API_GENERATION = 1
            PLATFORM = "linux-aarch64"

            def __getattr__(self, name):
                raise AssertionError("unexpected collection: " + name)

        result = await report.report_draft(MinimalHost(), {})
        self.assertEqual(result["diagnostics"], "")
        self.assertNotIn("error", result)

    async def test_missing_repository_keeps_copyable_draft_and_never_uses_plugin_tracker(self):
        context = {"plugin": {"name": "Example", "source": "https://example.invalid/issues"}}
        result = await report.report_draft(host(LOADER=""), context)
        self.assertEqual(result["repository"], "")
        self.assertIn("unavailable", result["warning"])
        self.assertIn("Source: Unknown", result["body"])
        linked = await report.report_link(host(LOADER="https://github.com/SteamDeckHomebrew/decky-loader"), result)
        self.assertIsNone(linked["url"])
        self.assertEqual(linked["code"], "repository_missing")
        self.assertTrue(linked["body"])

    async def test_unmeasured_versions_and_platform_are_unknown(self):
        result = await report.report_draft(host(VERSION="not a version", PLATFORM="private-hostname", API_GENERATION=True), {})
        self.assertIn("Framey version: Unknown", result["body"])
        self.assertIn("Framey API: Unknown", result["body"])
        self.assertIn("Platform: Unknown", result["body"])
        self.assertNotIn("private-hostname", json.dumps(result))

    async def test_malformed_values_are_not_stringified(self):
        class Explosive:
            def __str__(self):
                raise AssertionError("must not stringify")

        result = await report.report_draft(host(), {"plugin": {"name": Explosive(), "version": ["private"], "source": []}, "error": Explosive()})
        self.assertNotIn("error", result)
        self.assertIn("Plugin version: Unknown", result["body"])
        linked = await report.report_link(host(), {"title": Explosive(), "body": {}, "diagnostics": []})
        self.assertEqual(linked["title"], "Framey problem")
        self.assertEqual(linked["body"], "")

    async def test_capture_failure_never_reflects_private_exception(self):
        class BrokenHost:
            def __getattr__(self, key):
                raise RuntimeError("private fixture data")

        result = await report.report_draft(BrokenHost(), {})
        self.assertEqual(result["code"], "preview_unavailable")
        self.assertNotIn("private fixture data", json.dumps(result))


class PrivacyTests(unittest.TestCase):
    def test_every_editable_field_is_resanitized(self):
        private = "fixture-person@example.test 192.0.2.44 2001:db8::7 /home/fixture-person/private-project.txt C:\\Users\\fixture-person\\private.txt"
        result = report.link(host(), {"title": private, "body": private, "diagnostics": private})
        rendered = json.dumps(result)
        for value in ("fixture-person", "example.test", "192.0.2.44", "2001:db8", "private-project", "private.txt"):
            self.assertNotIn(value, rendered)
        self.assertIn("removed", result["body"])

    def test_auth_secret_serial_query_and_encoded_url_are_removed(self):
        fixtures = ["password=FIXTURE_PRIVATE", "Authorization: Bearer FIXTURE_PRIVATE", "Bearer FIXTURE_PRIVATE",
                    "serial_number=FIXTURE_PRIVATE", "token=FIXTURE_PRIVATE", "username=FIXTURE_PRIVATE",
                    "https://user:FIXTURE_PRIVATE@example.test/path?token=FIXTURE_PRIVATE",
                    "%2574oken=FIXTURE_PRIVATE", urllib.parse.quote(urllib.parse.quote("https://example.test?token=FIXTURE_PRIVATE", safe=""), safe=""),
                    "github_pat_FIXTURE_PRIVATE_0123456789012345"]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertNotIn("FIXTURE_PRIVATE", report.clean(fixture, 1000, multiline=True))

    def test_controls_and_remote_markdown_resources_are_not_forwarded(self):
        text = report.clean("bad\u202e\u2066\u200b ![image](https://example.test/private) <iframe src='https://example.test'>", 1000)
        self.assertNotIn("example.test", text)
        self.assertNotIn("\u202e", text)
        self.assertNotIn("\u2066", text)
        self.assertNotIn("\u200b", text)
        self.assertNotIn("<iframe", text)

    def test_normalized_labels_obfuscated_secrets_and_nested_encodings_are_removed(self):
        fixtures = ["to\u200bken=FIXTURE_PRIVATE", "ｔｏｋｅｎ=FIXTURE_PRIVATE", "％７４ｏｋｅｎ=FIXTURE_PRIVATE", "%\u200b74oken=FIXTURE_PRIVATE", "session_id=FIXTURE_PRIVATE",
                    "User: FIXTURE_PRIVATE", "Host name: FIXTURE_PRIVATE", "Serial number: FIXTURE_PRIVATE",
                    "?session=FIXTURE_PRIVATE&state=FIXTURE_PRIVATE&code=FIXTURE_PRIVATE",
                    "-----BEGIN " + "PRIVATE KEY-----\nFIXTURE_PRIVATE\n-----END PRIVATE KEY-----"]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertNotIn("FIXTURE_PRIVATE", report.clean(fixture, 1000, multiline=True))
        encoded = "fixture-person@example.test"
        for _ in range(15):
            encoded = urllib.parse.quote(encoded, safe="")
        self.assertNotIn("example.test", report.clean(encoded, 1000))
        self.assertNotIn("%40", report.clean(encoded, 1000))
        for value in ("www.example.test/private", "[fixture](example.test/private)", "aa:bb:cc:dd:ee:ff", "aa-bb-cc-dd-ee-ff"):
            self.assertNotIn(value, report.clean(value, 1000))

    def test_report_diagnostics_accept_only_fixed_codes_and_bounded_counts(self):
        records = [{"code": "core.callback_failed", "count": 100000}, {"code": "core.event_failed", "count": True},
                   {"code": "private.fixture", "count": 3}, {"code": "page.render_failed", "count": 2, "message": "PRIVATE"}]
        value = report.diagnostic_excerpt(records)
        self.assertIn("core.callback_failed: 9999", value)
        self.assertIn("page.render_failed: 2", value)
        self.assertNotIn("private.fixture", value)
        self.assertNotIn("PRIVATE", value)
        self.assertNotIn("core.event_failed", value)

    def test_unicode_url_budget_and_copy_fallback_preserve_sanitized_body(self):
        original = "🙂" * 2000
        result = report.link(host(), {"title": "Unicode", "body": original})
        self.assertIsNone(result["url"])
        self.assertEqual(result["code"], "too_long")
        self.assertEqual(result["body"], original)
        self.assertIn("Copy", result["warning"])

    def test_link_is_fixed_repository_and_round_trips_sanitized_fields(self):
        result = report.link(host(), {"repository": "https://evil.invalid", "title": "A & B", "body": "Steps\n1. Open Framey", "diagnostics": ""})
        parsed = urllib.parse.urlsplit(result["url"])
        self.assertEqual(parsed.netloc, "github.com")
        self.assertEqual(parsed.path, "/chaosfox26/framey/issues/new")
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(params["title"], [result["title"]])
        self.assertEqual(params["body"], [result["body"]])
        self.assertLessEqual(len(result["url"].encode("ascii")), report.URL_MAX)

    def test_input_size_and_diagnostics_are_bounded(self):
        result = report.link(host(), {"title": "x" * 100000, "body": "b" * 100000, "diagnostics": "d " * 100000})
        self.assertLessEqual(len(result["title"]), report.TITLE_MAX)
        self.assertLessEqual(len(result["body"]), report.BODY_MAX + report.DIAGNOSTICS_MAX + 40)
        self.assertEqual(report.clean(None), "")


class OpenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        report._opening = None
        report._launching = False

    async def asyncTearDown(self):
        if report._opening is not None and not report._opening.done():
            report._opening.cancel()
            await asyncio.gather(report._opening, return_exceptions=True)
        report._opening = None
        report._launching = False

    async def test_only_explicit_open_requests_browser_and_never_claims_issue_created(self):
        process = types.SimpleNamespace(wait=mock.AsyncMock(return_value=0))
        with mock.patch.object(report.asyncio, "create_subprocess_exec", mock.AsyncMock(return_value=process)) as spawn:
            result = await report.report_open(host(), {"title": "Example", "body": "Steps"})
        self.assertIs(result["requested"], True)
        self.assertIn("has not created an issue", result["notice"])
        self.assertEqual(spawn.call_args.args[0], "/usr/bin/xdg-open")
        self.assertTrue(spawn.call_args.args[1].startswith(report.REPOSITORY + "/issues/new?"))
        self.assertEqual(len(spawn.call_args.args), 2)
        self.assertNotIn("shell", spawn.call_args.kwargs)

    async def test_missing_browser_failure_is_generic_and_copyable(self):
        with mock.patch.object(report.asyncio, "create_subprocess_exec", mock.AsyncMock(side_effect=OSError("/private/fixture/path"))):
            result = await report.report_open(host(), {"title": "Example", "body": "Steps"})
        self.assertEqual(result["code"], "browser_unavailable")
        self.assertNotIn("/private/fixture/path", json.dumps(result))
        self.assertEqual(result["body"], "Steps")
        self.assertNotIn("requested", result)

    async def test_nonzero_opener_and_missing_repo_do_not_claim_success(self):
        process = types.SimpleNamespace(wait=mock.AsyncMock(return_value=1))
        with mock.patch.object(report.asyncio, "create_subprocess_exec", mock.AsyncMock(return_value=process)) as spawn:
            failed = await report.report_open(host(), {"body": "Steps"})
            missing = await report.report_open(host(LOADER=""), {"body": "Steps"})
        self.assertEqual(spawn.await_count, 1)
        self.assertEqual(failed["code"], "browser_unavailable")
        self.assertEqual(missing["code"], "repository_missing")
        self.assertNotIn("requested", failed)
        self.assertNotIn("requested", missing)

    async def test_pending_opener_is_not_retried(self):
        report._opening = asyncio.create_task(asyncio.sleep(60))
        with mock.patch.object(report.asyncio, "create_subprocess_exec", mock.AsyncMock()) as spawn:
            result = await report.report_open(host(), {"title": "Example", "body": "Steps"})
        self.assertEqual(result["code"], "opening")
        spawn.assert_not_awaited()

    async def test_concurrent_open_calls_admit_one_browser_launch(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def launch(*args, **kwargs):
            entered.set()
            await release.wait()
            return types.SimpleNamespace(wait=mock.AsyncMock(return_value=0))

        with mock.patch.object(report.asyncio, "create_subprocess_exec", mock.AsyncMock(side_effect=launch)) as spawn:
            first = asyncio.create_task(report.report_open(host(), {"body": "Steps"}))
            await entered.wait()
            second = await report.report_open(host(), {"body": "Steps"})
            release.set()
            opened = await first
        self.assertEqual(spawn.await_count, 1)
        self.assertEqual(second["code"], "opening")
        self.assertTrue(opened["requested"])


if __name__ == "__main__":
    unittest.main()
