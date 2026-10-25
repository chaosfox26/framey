import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import fy_updates
from test_fy_updates import FakeHost, SHA_A, SHA_B, plugin_zip, rel_json


class UpdatePlanningParityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeHost(self.tmp.name)
        self.host.add("demo", "2.0.0", sha=SHA_A)
        self.host.shas[("o", "r", "HEAD")] = SHA_B
        self.updates = fy_updates.Updates(self.host)

    async def plan_version(self, version, plugin_id="demo"):
        manifest = {"id": plugin_id, "name": "Demo", "version": version}
        self.host.api["o/r/contents/plugin.json?ref=" + SHA_B] = {
            "encoding": "base64",
            "content": base64.b64encode(json.dumps(manifest).encode()).decode(),
        }
        result = await self.updates.batch_plan(["demo"])
        return result["items"][0]

    async def test_batch_preview_excludes_new_commit_containing_older_version(self):
        row = await self.plan_version("1.9.0")
        self.assertFalse(row["eligible"])
        self.assertIn("older version", row["reason"].lower())
        self.assertEqual((row["from"], row["to"]), ("2.0.0", "1.9.0"))

    async def test_batch_preview_excludes_version_format_transition_needing_confirmation(self):
        row = await self.plan_version("development")
        self.assertFalse(row["eligible"])
        self.assertTrue(row["reason"])

    async def test_new_commit_with_same_version_remains_eligible(self):
        row = await self.plan_version("2.0.0")
        self.assertTrue(row["eligible"])
        self.assertEqual(row["reason"], "")

    async def test_newer_version_remains_eligible(self):
        row = await self.plan_version("2.1.0")
        self.assertTrue(row["eligible"])
        self.assertEqual(row["to"], "2.1.0")

    async def test_batch_preview_rejects_mismatched_explicit_plugin_id(self):
        row = await self.plan_version("2.1.0", plugin_id="different")
        self.assertFalse(row["eligible"])
        self.assertTrue(row["reason"])

    async def test_stable_preview_excludes_prerelease_head(self):
        row = await self.plan_version("2.1.0-rc.1")
        self.assertFalse(row["eligible"])
        self.assertIn("prerelease", row["reason"].lower())

    async def test_prerelease_preference_without_developer_mode_stays_stable(self):
        self.host.prefs(channel="prerelease", dev={"enabled": False})
        row = await self.plan_version("2.1.0-rc.1")
        self.assertFalse(row["eligible"])
        self.assertIn("prerelease", row["reason"].lower())

    async def test_testing_preview_allows_prerelease_head(self):
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        row = await self.plan_version("2.1.0-rc.1")
        self.assertTrue(row["eligible"])
        self.assertEqual(row["reason"], "")

    async def test_stable_batch_apply_skips_prerelease_head_before_swap(self):
        self.host.archives[SHA_B] = plugin_zip("demo", "2.1.0-rc.1")
        row = (await self.updates.batch_apply(["demo"]))["results"][0]
        self.assertEqual(row["state"], "skipped")
        self.assertIn("prerelease", row["reason"].lower())
        self.assertEqual(self.host.swaps, [])
        self.assertEqual(self.host.manifest(self.host.plugin_dir("demo"))["version"], "2.0.0")

    async def test_testing_batch_apply_installs_prerelease_head(self):
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        self.host.archives[SHA_B] = plugin_zip("demo", "2.1.0-rc.1")
        row = (await self.updates.batch_apply(["demo"]))["results"][0]
        self.assertEqual(row["state"], "ok")
        self.assertEqual(row["to"], "2.1.0-rc.1")
        self.assertEqual(self.host.manifest(self.host.plugin_dir("demo"))["version"], "2.1.0-rc.1")

    async def test_frozen_module_version_list_explains_install_refusal(self):
        self.host.freeze("demo", "2.0.0")
        self.host.api["o/r/releases?per_page=10&page=1"] = [rel_json("v2.1.0")]
        row = (await self.updates.versions("demo"))["versions"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("frozen", row["reason"].lower())


if __name__ == "__main__":
    unittest.main()
