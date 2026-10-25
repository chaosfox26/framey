import hashlib
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import test_fy_decky_catalog_flow as flow
import test_fy_decky_catalog_validation as fixtures
import test_fy_maint as maintenance

import fy_catalog
import fy_decky_catalog as decky
import fy_maint
import fy_pkg


class SelectedArtifactSemantics(unittest.TestCase):
    def inspect(self, extra_files=(), **manifest):
        data = fixtures.archive(fixtures.package_files(**manifest) + list(extra_files))
        digest = hashlib.sha256(data).hexdigest()
        return digest, decky.inspect_archive(data, digest)

    def test_catalog_checksum_proves_selected_bytes_not_revision_or_execution(self):
        digest, result = self.inspect()
        self.assertTrue(result["verified_sha256"])
        self.assertTrue(result["not_executed"])
        self.assertIsNone(fy_maint.SHA.fullmatch(digest))
        self.assertFalse({"repo", "branch", "commit_at", "release_at", "checked_at"} & result.keys())
        self.assertTrue(any("not a publisher signature" in item for item in result["warnings"]))
        self.assertEqual(result["status"], "incompatible")

    def test_manifest_support_claims_cannot_promote_decky_package_to_works(self):
        _, result = self.inspect(platform=["linux-aarch64"], api=1, framey={"min": "1.0.0"},
                                 maintenance={"status": "maintained", "by": "example-maintainer"})
        self.assertEqual(result["state"], "port_required")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(result["architecture"], "unknown")
        self.assertEqual(result["architecture_status"], "unverified")
        self.assertTrue(result["not_executed"])

    def test_arm64_header_evidence_does_not_prove_decky_api_support(self):
        _, result = self.inspect([("bin/helper", fixtures.elf(183))])
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["architecture_status"], "evidence_only")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(result["state"], "port_required")
        self.assertFalse(result["runtime_verified"])
        self.assertEqual(result["basis"], "static_package_inspection")
        self.assertIn(decky.PORT_REASON, result["reasons"])

    def test_x86_header_reports_architecture_block_without_a_runtime_test(self):
        _, result = self.inspect([("bin/helper", fixtures.elf(62))])
        self.assertEqual(result["architecture"], "x86_64")
        self.assertEqual(result["architecture_status"], "incompatible")
        self.assertEqual(result["status"], "incompatible")
        self.assertTrue(result["not_executed"])

    def test_checksum_mismatch_never_reuses_a_previous_assessment(self):
        first = fixtures.archive(fixtures.package_files())
        selected = hashlib.sha256(first).hexdigest()
        changed = fixtures.archive(fixtures.package_files() + [("bin/helper", fixtures.elf(183))])
        with self.assertRaises(decky.CatalogError) as error:
            decky.inspect_archive(changed, selected)
        self.assertEqual(error.exception.code, "digest_mismatch")


class DeclaredApiAndPlatformSemantics(unittest.TestCase):
    def test_missing_declarations_stay_unknown(self):
        result = fy_pkg.decide({"id": "sample"}, "1.1.0", "linux-aarch64", 1)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["badge"], "unknown")

    def test_matching_architecture_does_not_clear_api_generation_mismatch(self):
        result = fy_pkg.decide({"api": 2, "platform": ["linux-aarch64"]}, "1.1.0", "linux-aarch64", 1)
        self.assertEqual(result["state"], "incompatible")
        self.assertEqual(result["badge"], "api")
        self.assertTrue(any("API generation 2" in reason for reason in result["reasons"]))
        self.assertFalse(any("built for" in reason for reason in result["reasons"]))

    def test_matching_api_does_not_clear_architecture_mismatch(self):
        result = fy_pkg.decide({"api": 1, "platform": ["linux-x86_64"]}, "1.1.0", "linux-aarch64", 1)
        self.assertEqual(result["state"], "incompatible")
        self.assertEqual(result["badge"], "platform")
        self.assertTrue(any("linux-x86_64" in reason for reason in result["reasons"]))
        self.assertFalse(any("API generation" in reason for reason in result["reasons"]))


class PinnedMaintenanceSemantics(maintenance.Base):
    async def test_explicit_revision_and_plugin_path_are_never_replaced_by_default_branch(self):
        h = self.host(maintenance.api_for(commit_at=maintenance.iso(1)))

        def commits(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            when = maintenance.iso(900) if query.get("sha") == [maintenance.A] else maintenance.iso(1)
            return [maintenance.commit(maintenance.A, when)]

        h.api["repos/acme/widget/commits?path=plugins/demo"] = commits
        h.add("demo", source=maintenance.SRC + "/tree/" + maintenance.A, source_path="plugins/demo",
              platform=["linux-aarch64"], api=1)
        result = await fy_maint.check_one(h, "demo")
        self.assertEqual((result["branch"], result["path"], result["label"]), (maintenance.A, "plugins/demo", "none"))
        self.assertIn("does not establish abandonment", result["reason"])
        self.assertTrue(all("sha=" + maintenance.A in url for url in h.calls_with("/commits?")))
        self.assertEqual(fy_pkg.decide(h.plugins["demo"], "1.1.0", "linux-aarch64", 1)["state"], "ok")

    async def test_stale_pinned_activity_retains_evidence_without_rechecking_other_sources(self):
        h = self.host(maintenance.api_for(commit_at=maintenance.iso(900)))
        h.add("demo", source=maintenance.SRC + "/tree/" + maintenance.A)
        first = await fy_maint.check_one(h, "demo")
        before = len(h.calls)
        stale = fy_maint.cached(h, "demo", maintenance.NOW + fy_maint.TTL + 1)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["checked_at"], first["checked_at"])
        self.assertEqual(stale["commit_at"], first["commit_at"])
        self.assertEqual(stale["branch"], maintenance.A)
        self.assertEqual(stale["label"], "none")
        self.assertEqual(len(h.calls), before)

    async def test_repository_release_is_separate_from_pinned_commit_activity(self):
        h = self.host(maintenance.api_for(commit_at=maintenance.iso(900), release_at=maintenance.iso(1)))
        h.add("whole", source=maintenance.SRC + "/tree/" + maintenance.A)
        h.add("scoped", source=maintenance.SRC + "/tree/" + maintenance.A, source_path="plugins/demo")
        h.api["repos/acme/widget/commits?path=plugins/demo"] = [maintenance.commit(maintenance.A, maintenance.iso(900))]
        results = await fy_maint.check_all(h, h.manifests())
        self.assertEqual(results["whole"]["label"], "recent")
        self.assertEqual(results["scoped"]["label"], "none")
        self.assertEqual(results["whole"]["commit_at"], results["scoped"]["commit_at"])
        self.assertEqual(results["whole"]["release_at"], results["scoped"]["release_at"])
        self.assertTrue(any("repository-wide, not used for this label" in item for item in results["scoped"]["evidence"]))

    async def test_decky_feed_url_never_implies_a_github_repository(self):
        h = self.host()
        result = await fy_maint.assess_url(h, flow.STABLE_URL)
        self.assertEqual(result["label"], "unknown")
        self.assertEqual(result["repo"], "")
        self.assertEqual(result["commit_at"], "")
        self.assertEqual(h.calls, [])


class CatalogEvidenceIsolation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fy-decky-evidence-")
        self.addCleanup(temporary.cleanup)
        self.host = flow.FakeHost(Path(temporary.name))
        self.fetcher = flow.ControlledFetcher()
        self.archives = flow.NoArchiveFetcher()
        self.service = fy_catalog.Catalog(self.host, fetcher=self.fetcher, archive_fetcher=self.archives)
        blocker = mock.patch("socket.create_connection", side_effect=AssertionError("controlled test reached a network socket"))
        blocker.start()
        self.addCleanup(blocker.stop)

    async def test_recent_store_metadata_does_not_claim_github_maintenance_or_platform_support(self):
        page = await self.service.list({"source": "decky_stable"})
        for row in page["rows"]:
            self.assertFalse(row["platform_declared"])
            self.assertEqual(row["architecture"], "unknown")
            self.assertEqual(row["source"]["repository"], "")
            self.assertFalse(row["compatibility"]["runtime_verified"])
            self.assertEqual(row["compatibility"]["basis"], "decky_format")
            self.assertFalse({"maintenance", "commit_at", "release_at"} & row.keys())
        self.assertEqual(self.archives.calls, [])

    async def test_stale_decky_feed_blocks_inspection_and_keeps_native_selection(self):
        native = await self.service.list({"source": "stable"})
        details = await self.service.details({"source": "decky_stable", "id": "decky-1"})
        entry = details["entry"]
        self.host.clock += decky.TTL + 1
        self.fetcher.failure = OSError("controlled offline state")
        stale = await self.service.details({"source": "decky_stable", "id": "decky-1"})
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["downloaded_at"], details["downloaded_at"])
        self.assertGreater(stale["checked_at"], stale["downloaded_at"])
        denied = await self.service.assess({"source": "decky_stable", "id": entry["id"],
                                            "identity": entry["identity"], "version_identity": entry["versions"][0]["identity"], "confirm": True})
        self.assertEqual(denied["code"], "stale_catalog")
        self.assertEqual(self.archives.calls, [])
        self.fetcher.failure = None
        restored = await self.service.list()
        self.assertEqual(restored["source"]["id"], native["source"]["id"])
        self.assertEqual([row["id"] for row in restored["rows"]], [row["id"] for row in native["rows"]])

    async def test_selected_older_archive_is_inspected_without_substituting_latest(self):
        newest = fixtures.archive(fixtures.package_files() + [("bin/helper", fixtures.elf(62))])
        selected = fixtures.archive(fixtures.package_files() + [("bin/helper", fixtures.elf(183))])
        newest_hash, selected_hash = (hashlib.sha256(data).hexdigest() for data in (newest, selected))
        row = fixtures.raw_plugin(versions=[
            {"name": "2.0.0", "hash": newest_hash, "created": "2026-09-01T00:00:00Z"},
            {"name": "1.0.0", "hash": selected_hash, "created": "2026-01-01T00:00:00Z"},
        ])
        self.fetcher.routes[flow.STABLE_URL] = [row]
        calls = []

        async def archive_fetcher(url, limit):
            calls.append((url, limit))
            self.assertEqual(url, decky.CDN + selected_hash + ".zip")
            return selected

        service = fy_catalog.Catalog(self.host, fetcher=self.fetcher, archive_fetcher=archive_fetcher)
        details = await service.details({"source": "decky_stable", "id": "decky-1"})
        entry = details["entry"]
        version = entry["versions"][1]
        result = await service.assess({"source": "decky_stable", "id": entry["id"],
                                       "identity": entry["identity"], "version_identity": version["identity"], "confirm": True})
        self.assertEqual((result["version"], result["hash"]), ("1.0.0", selected_hash))
        self.assertEqual(result["assessment"]["architecture"], "aarch64")
        self.assertEqual(result["assessment"]["state"], "port_required")
        self.assertTrue(result["assessment"]["not_executed"])
        self.assertFalse({"commit_at", "release_at", "maintenance"} & result.keys())
        self.assertEqual(len(calls), 1)
        self.assertEqual((await service.sources())["source"]["id"], "stable")

    async def test_failed_refresh_check_time_survives_restart_during_backoff(self):
        await self.service.list({"source": "decky_stable"})
        self.host.clock += decky.TTL + 1
        self.fetcher.failure = OSError("controlled offline state")
        failed = await self.service.list({"source": "decky_stable"})
        self.assertGreater(failed["checked_at"], failed["downloaded_at"])
        count = len(self.fetcher.calls)
        restarted = fy_catalog.Catalog(self.host, fetcher=self.fetcher, archive_fetcher=self.archives)
        cached = await restarted.list({"source": "decky_stable"})
        self.assertTrue(cached["stale"])
        self.assertEqual(cached["downloaded_at"], failed["downloaded_at"])
        self.assertEqual(cached["checked_at"], failed["checked_at"])
        self.assertEqual(len(self.fetcher.calls), count)


if __name__ == "__main__":
    unittest.main()
