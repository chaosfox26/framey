import asyncio
import base64
import json
import threading
import unittest
from unittest import mock

import test_fy_maint as support

fm = support.fm


class TimestampTests(unittest.TestCase):
    def test_invalid_timezone_offsets_are_not_activity(self):
        for zone in ("+24:00", "-24:00", "+01:60", "-00:99", "+99:99"):
            with self.subTest(zone=zone):
                self.assertIsNone(fm.parse_stamp("2026-10-09T00:00:00" + zone))
        self.assertEqual(fm.parse_stamp("2026-10-09T23:59:00+23:59"), support.NOW)


class SourceAndReplyTests(support.Base):
    async def test_malformed_recorded_github_scope_does_not_fall_back_to_repository(self):
        for suffix in ("/tree", "/tree/main/plugins/../demo", "/tree/main//plugins/demo", "/tree//main/plugins/demo"):
            with self.subTest(suffix=suffix):
                h = self.host(support.api_for())
                h.calls.clear()
                source = support.SRC + suffix
                h.add("demo", source=support.SRC, dot_source=source)
                result = await fm.check_one(h, "demo")
                self.assertEqual(result["label"], "unknown")
                self.assertEqual(result["scope"], "")
                self.assertEqual(result["source"], source)
                self.assertIn("source path", result["reason"])
                self.assertEqual(h.calls, [])

    async def test_unsupported_non_github_recorded_source_still_uses_manifest(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC, dot_source="https://gitlab.com/example/project/tree/main/plugins/demo")
        result = await fm.check_one(h, "demo")
        self.assertEqual((result["repo"], result["source"], result["label"]), ("acme/widget", support.SRC, "recent"))
        self.assertTrue(h.calls)

    async def test_future_success_stays_unknown_across_clock_correction_and_restart(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC)
        await fm.check_one(h, "demo")
        h.t = support.NOW - 365 * fm.DAY
        future_bytes = (h.CONF.parent / "maint.json").read_bytes()
        restarted = support.FakeHost(self.tmp, now=h.t)
        restarted.add("demo", source=support.SRC)
        for host, now in ((h, h.t), (restarted, h.t + 2 * fm.DAY)):
            with self.subTest(now=now):
                call_count = len(host.calls)
                result = fm.cached(host, "demo", now)
                self.assertEqual(result["label"], "unknown")
                self.assertTrue(result["stale"])
                self.assertEqual(result["checked_at"], 0)
                self.assertTrue(fm.wanted(fm.load(host, now), "acme/widget@#", now, False))
                self.assertEqual(len(host.calls), call_count)
        self.assertEqual((h.CONF.parent / "maint.json").read_bytes(), future_bytes)

    async def test_normal_bounded_check_repairs_future_success_after_restart(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC)
        await fm.check_one(h, "demo")
        restarted = support.FakeHost(self.tmp, api=support.api_for(), now=support.NOW - 365 * fm.DAY)
        restarted.add("demo", source=support.SRC)
        result = (await fm.check_all(restarted, restarted.manifests()))["demo"]
        self.assertFalse(result["stale"])
        self.assertEqual(result["checked_at"], restarted.t)
        self.assertEqual(len(restarted.calls), 4)
        self.assertLessEqual(len(restarted.calls), fm.CAP)
        self.assertEqual(restarted.stored()["entries"]["acme/widget@#"]["ok_at"], restarted.t)

    async def test_incomplete_tree_sources_never_broaden_to_repository(self):
        for suffix in ("/tree", "/tree/", "/tree//main", "/tree/main//plugins/demo"):
            with self.subTest(suffix=suffix):
                h = self.host(support.api_for())
                h.calls.clear()
                h.add("demo", source=support.SRC + suffix)
                result = await fm.check_one(h, "demo")
                self.assertEqual(result["label"], "unknown")
                self.assertEqual(result["scope"], "")
                self.assertEqual(h.calls, [])

    async def test_valid_tree_path_takes_precedence_over_manifest_path(self):
        h = self.host(support.api_for())
        h.api["repos/acme/widget/commits?path=plugins/demo"] = [support.commit(support.A, support.iso(900))]
        h.add("demo", source=support.SRC + "/tree/main/plugins/demo", source_path="../wrong")
        result = await fm.check_one(h, "demo")
        self.assertEqual((result["path"], result["label"]), ("plugins/demo", "none"))

    async def test_release_creation_is_not_a_publication_date(self):
        h = self.host(support.api_for(commit_at=support.iso(900)))
        h.api["repos/acme/widget/releases/latest"] = {"created_at": support.iso(1), "published_at": None, "tag_name": "v1"}
        h.add("demo", source=support.SRC)
        result = await fm.check_one(h, "demo")
        self.assertEqual(result["label"], "unknown")
        self.assertIsNotNone(result["failure"])

    async def test_draft_and_prerelease_replies_are_not_latest_public_release(self):
        for field in ("draft", "prerelease"):
            with self.subTest(field=field):
                h = self.host(support.api_for(commit_at=support.iso(900), release_at=support.iso(1)))
                h.api["repos/acme/widget/releases/latest"][field] = True
                h.add("demo", source=support.SRC)
                result = await fm.check_one(h, "demo")
                self.assertEqual(result["label"], "unknown")
                self.assertIsNotNone(result["failure"])

    async def test_invalid_base64_cannot_produce_a_declaration(self):
        body = support.decl_text(status="unmaintained", by="example-maintainer")
        encoded = base64.b64encode(body.encode()).decode()
        h = self.host(support.api_for(commit_at=support.iso(900)))
        h.api["repos/acme/widget/contents/plugin.json"] = {"encoding": "base64", "content": "!" + encoded}
        h.add("demo", source=support.SRC)
        result = await fm.check_one(h, "demo")
        self.assertEqual(result["label"], "none")
        self.assertIsNone(result["declaration"])
        self.assertIn("could not be parsed", result["declaration_note"])

    async def test_cache_failure_times_and_backoff_are_bounded(self):
        h = self.host()
        s = fm.blank()
        s["entries"]["acme/widget@#"] = {
            "ok_at": 0, "facts": None,
            "fail": {"reason": "Could not check", "at": support.NOW + 10 * fm.WEEK, "code": "network"},
            "retry_after": support.NOW + 10 * fm.WEEK,
        }
        fm.save(h, s)
        result = fm.load(h)["entries"]["acme/widget@#"]
        self.assertEqual(result["fail"]["at"], support.NOW)
        self.assertEqual(result["retry_after"], support.NOW + fm.COOLDOWN)

    async def test_small_cache_entries_are_capped_on_load(self):
        h = self.host()
        s = fm.blank()
        for i in range(fm.ENTRIES_MAX + 20):
            s["entries"]["org/r%d@#" % i] = {
                "fail": {"reason": "Could not check", "at": support.NOW, "code": "network"},
            }
        h.CONF.parent.mkdir(parents=True)
        (h.CONF.parent / "maint.json").write_text(json.dumps(s))
        entries = fm.load(h)["entries"]
        self.assertEqual(len(entries), fm.ENTRIES_MAX)
        self.assertEqual(next(iter(entries)), "org/r20@#")

    async def test_concurrent_rate_limits_keep_the_longest_backoff(self):
        h = self.host(support.api_for("org/one"))
        h.api.update(support.api_for("org/two"))
        barrier = threading.Barrier(2)

        def limited(seconds):
            def reply(url):
                barrier.wait(2)
                return support.http_error(429, Retry_After=seconds)
            return reply

        h.api["repos/org/one"] = limited(3600)
        h.api["repos/org/two"] = limited(30)
        h.add("one", source="https://github.com/org/one")
        h.add("two", source="https://github.com/org/two")
        results = await fm.check_all(h, h.manifests())
        self.assertEqual(h.stored()["limit"]["until"], support.NOW + 3600)
        self.assertEqual({r["retry_after"] for r in results.values()}, {support.NOW + 3600})
        before = len(h.calls)
        await fm.check_all(h, h.manifests(), now=support.NOW + 31, force=True)
        self.assertEqual(len(h.calls), before)

    async def test_network_failure_does_not_publish_transport_details(self):
        h = self.host({"repos/acme/widget": OSError("fixture transport detail")})
        h.add("demo", source=support.SRC)
        result = await fm.check_one(h, "demo")
        self.assertEqual(result["label"], "unknown")
        self.assertNotIn("fixture transport detail", result["failure"]["reason"])
        self.assertNotIn("fixture transport detail", (h.CONF.parent / "maint.json").read_text())

    async def test_rate_limit_survives_a_sibling_request_budget_skip(self):
        h = self.host(support.api_for())
        h.api["repos/acme/widget/releases/latest"] = support.http_error(429, Retry_After=300)
        h.add("demo", source=support.SRC)

        async def skipped(run, base, branch, path):
            raise fm.Skip()

        with mock.patch.object(fm.Run, "commit", skipped):
            result = await fm.check_one(h, "demo")
        self.assertEqual(result["label"], "unknown")
        self.assertEqual(result["retry_after"], support.NOW + 300)
        self.assertEqual(h.stored()["limit"]["until"], support.NOW + 300)

    async def test_failed_cache_flush_preserves_previous_file(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC)
        await fm.check_one(h, "demo")
        path = h.CONF.parent / "maint.json"
        before = path.read_bytes()
        with mock.patch.object(fm.os, "fsync", side_effect=OSError("fixture disk failure")):
            self.assertFalse(fm.save(h, fm.blank()))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["maint.json"])


class CancellationTests(support.Base):
    async def test_setup_failure_does_not_leave_refresh_busy(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC)
        h.mono = lambda: (_ for _ in ()).throw(ValueError("fixture clock failure"))
        self.assertEqual((await fm.maint_refresh(h, {"id": "demo"}))["code"], "failed")
        self.assertFalse(fm.BUSY["on"])

    async def test_cancelled_refresh_drains_fetch_before_releasing_busy(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC)
        await fm.check_one(h, "demo")
        before = (h.CONF.parent / "maint.json").read_bytes()
        started = asyncio.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        loop = asyncio.get_running_loop()
        repository = h.api["repos/acme/widget"]

        def blocked(url):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(2):
                raise TimeoutError("controlled fetch timed out")
            return repository

        h.api["repos/acme/widget"] = blocked
        task = asyncio.create_task(fm.check_one(h, "demo"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertTrue(fm.BUSY["on"])
            self.assertEqual((await fm.maint_refresh(h, {"id": "demo"}))["code"], "busy")
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(fm.BUSY["on"])
        self.assertEqual(h.active, 0)
        self.assertEqual((h.CONF.parent / "maint.json").read_bytes(), before)
        self.assertEqual([p.name for p in h.CONF.parent.iterdir()], ["maint.json"])

    async def test_repeated_cancellation_keeps_busy_until_late_error_is_closed(self):
        h = self.host(support.api_for())
        h.add("demo", source=support.SRC)
        started = asyncio.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        loop = asyncio.get_running_loop()
        error = support.http_error(503)
        response = error.fp

        def blocked(url):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(2):
                raise TimeoutError("controlled fetch timed out")
            return error

        h.api["repos/acme/widget"] = blocked
        task = asyncio.create_task(fm.check_one(h, "demo"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(fm.BUSY["on"])
            self.assertEqual((await fm.maint_refresh(h, {"id": "demo"}))["code"], "busy")
        finally:
            release.set()

        async def drained():
            while fm.BUSY["on"]:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(drained(), 1)
        self.assertEqual(h.active, 0)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
