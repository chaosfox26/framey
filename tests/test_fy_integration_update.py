import asyncio
import base64
import hashlib
import io
import json
import pathlib
import re
import sys
import unittest
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H

OLD, NEW, OLDER = "1" * 40, "2" * 40, "0" * 40
STUB = "VERSION = '1.0.1'\nasync def main():\n    pass\n"
HOOKED = """
import framey_api
LOG = %r


def note(text):
    with open(LOG, "a") as f:
        f.write(text + "\\n")


def startup(ctx):
    note("startup")


def migrate(ctx):
    note("migrate:" + str(ctx.from_version) + ">" + str(ctx.to_version))


def unload(reason):
    note("unload:" + reason)


async def call(method, arg):
    return {"version": %r}
"""


def meta(version, **extra):
    return json.dumps(dict({"id": "a", "name": "A", "version": version, "short": "s", "api": 1, "source": "https://github.com/o/a"}, **extra))


def package(version, sha, main="NEW", **extra):
    return H.mkzip({"plugin.json": meta(version, **extra), "main.js": main, "backend.py": HOOKED % ("x.log", version), "data.txt": "d"}, top="a-" + sha)


def release(tag, pre=False, body="", commit=""):
    return {"tag_name": tag, "prerelease": pre, "published_at": "2026-10-01T00:00:00Z", "body": body, "html_url": "https://github.com/o/a/releases/tag/" + tag, "target_commitish": commit, "assets": []}


class Base(unittest.IsolatedAsyncioTestCase):
    async def start(self, version="1.0.0", sha=OLD, prefs=None, **manifest):
        loader = H.Loader(prefs=prefs)
        self.addAsyncCleanup(loader.close)
        self.log = loader.base / "a.log"
        d = loader.plugin("a", dict({"api": 1, "version": version, "source": "https://github.com/o/a"}, **manifest), main="OLD", backend=HOOKED % (str(self.log), version), extra={"data.txt": "old", ".version": sha, ".source": "https://github.com/o/a"})
        loader.dir = d
        loader.fy.load_backends()
        return loader

    def serve(self, loader, version, sha, ref="HEAD", tag=None, **extra):
        loader.net.commit("o", "a", tag or ref, sha)
        data = H.mkzip({"plugin.json": meta(version, **extra), "main.js": "NEW-" + version, "backend.py": HOOKED % (str(self.log), version), "data.txt": "d"}, top="a-" + sha)
        loader.net.archive("o", "a", sha, data)
        content = base64.b64encode(meta(version, **extra).encode()).decode()
        loader.net.json("o/a/contents/plugin.json?ref=" + sha, {"content": content})
        return data

    def lines(self):
        try:
            return self.log.read_text().split()
        except OSError:
            return []

    def leftovers(self, loader):
        return sorted(p.name for p in loader.plugins.iterdir() if p.name.startswith("."))


class UpdateTests(Base):
    async def test_update_replaces_the_plugin_records_identity_and_restarts_its_backend(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual(await fy.backend("a", "x", 1), {"version": "1.0.0"})
        self.serve(loader, "1.1.0", NEW)
        r = await loader.core("update", "a")
        self.assertEqual(r, {"ok": "a", "version": "1.1.0"})
        d = loader.dir
        self.assertEqual((d / "main.js").read_text(), "NEW-1.1.0")
        self.assertEqual((d / ".version").read_text(), NEW)
        self.assertEqual((d / ".source").read_text(), "https://github.com/o/a")
        ident = json.loads((d / ".identity.json").read_text())
        self.assertEqual(ident, {"commit": NEW, "kind": "commit", "source": "o/a", "v": 1})
        self.assertEqual(fy.use("fy_pkg").read_identity(d), ident)
        self.assertEqual(self.leftovers(loader), [".old-a"])
        self.assertEqual(self.lines(), ["startup", "unload:reload", "migrate:1.0.0>1.1.0", "startup"])
        self.assertEqual(await fy.backend("a", "x", 1), {"version": "1.1.0"})
        self.assertEqual(self.lines()[-2:], ["migrate:1.0.0>1.1.0", "startup"])
        self.assertEqual(fy.settings()["updates"]["plugins"]["a"], {"latest": NEW[:7], "state": "current"})
        self.assertEqual(fy.module_state("a")["ver"], "1.1.0")
        self.assertEqual(fy.dirs("a").config.parent.name, "plugins")
        self.assertFalse((fy.dirs("a").config / "settings.json.premigrate").exists())

    async def test_update_keeps_the_disabled_flag_the_other_settings_and_reports_current(self):
        loader = await self.start()
        fy = loader.fy
        await loader.core("disable", "a")
        s = fy.settings()
        s["keep"] = {"x": 1}
        fy.save(s)
        self.serve(loader, "1.1.0", NEW)
        r = await loader.core("update", "a")
        self.assertEqual(r["ok"], "a")
        s = fy.settings()
        self.assertEqual((s["disabled"], s["keep"]), (["a"], {"x": 1}))
        again = await loader.core("update", "a")
        self.assertEqual(again, {"current": True})

    async def test_update_refuses_what_the_package_module_does_not_allow_before_swapping(self):
        loader = await self.start()
        before = (loader.dir / "main.js").read_text()
        self.serve(loader, "2.0.0", NEW, framey={"min": "9.0.0"})
        self.assertEqual((await loader.core("update", "a"))["error"], "Needs Framey 9.0.0 or newer; you have 1.0.2")
        self.serve(loader, "2.0.0", NEW, platform=["linux-sparc"])
        self.assertIn("this headset is", (await loader.core("update", "a"))["error"])
        self.serve(loader, "2.0.0", NEW, api=2)
        self.assertEqual((await loader.core("update", "a"))["error"], "Needs API generation 2; this Framey provides 1")
        self.serve(loader, "2.0.0", NEW, framey="soon")
        self.assertIn("compatibility fields are not valid", (await loader.core("update", "a"))["error"])
        self.assertEqual((loader.dir / "main.js").read_text(), before)
        self.assertEqual(self.leftovers(loader), [])

    async def test_update_rejects_a_package_for_a_different_plugin(self):
        loader = await self.start()
        loader.net.commit("o", "a", "HEAD", NEW)
        loader.net.archive("o", "a", NEW, H.mkzip({"plugin.json": json.dumps({"id": "other", "name": "O"}), "main.js": "x"}))
        r = await loader.core("update", "a")
        self.assertEqual(r["error"], "rejected: the package is plugin other, not a")
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")

    async def test_a_failed_download_leaves_everything_untouched(self):
        loader = await self.start()
        loader.net.commit("o", "a", "HEAD", NEW)
        r = await loader.core("update", "a")
        self.assertEqual(r["error"], "not found on GitHub (no release yet, private, or renamed)")
        self.assertEqual(self.leftovers(loader), [])
        loader.net.routes["https://github.com/o/a/archive/%s.zip" % NEW] = H.http_error("u", 403, X_RateLimit_Remaining="0")
        self.assertEqual((await loader.core("update", "a"))["error"], "GitHub rate limit reached, try again later")

    async def test_installing_over_a_plugin_runs_the_migration_hook_once(self):
        loader = await self.start(data_version=1)
        fy = loader.fy
        await fy.backend("a", "x", 1)
        self.serve(loader, "1.0.0", NEW, data_version=2)
        self.assertEqual((await loader.core("update", "a"))["ok"], "a")
        await fy.backend("a", "x", 1)
        self.assertEqual(self.lines().count("migrate:1.0.0>1.0.0"), 1)
        await fy.backend("a", "x", 1)
        await loader.core("reload")
        await fy.backend("a", "x", 1)
        self.assertEqual(self.lines().count("migrate:1.0.0>1.0.0"), 1)

    async def test_update_of_a_plugin_whose_backend_never_ran_starts_only_the_new_one_and_calls_no_unload(self):
        loader = await self.start()
        fy = loader.fy
        self.serve(loader, "1.1.0", NEW)
        self.assertEqual((await loader.core("update", "a"))["ok"], "a")
        self.assertIsNotNone(fy.sup().status()["a"]["os_pid"])
        self.assertEqual(self.lines(), ["startup"])

    async def test_failed_startup_after_migration_restores_settings_with_the_old_code(self):
        loader = await self.start(data_version=1)
        fy = loader.fy
        await fy.backend("a", "x", None)
        config = fy.dirs("a").config / "settings.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        original = '{"schema": 1, "keep": "saved"}'
        config.write_text(original)
        self.serve(loader, "1.1.0", NEW, data_version=2)
        failing = """
def migrate(ctx):
    ctx.settings.set("schema", 2)


def startup(ctx):
    raise RuntimeError("startup fixture failed after migration")


async def call(method, arg):
    return {"version": "1.1.0"}
"""
        loader.net.archive("o", "a", NEW, H.mkzip({
            "plugin.json": meta("1.1.0", data_version=2),
            "main.js": "NEW-1.1.0", "backend.py": failing,
        }, top="a-" + NEW))
        result = await loader.core("update", "a")
        self.assertIn("the previous version was restored", result["error"])
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        self.assertEqual((loader.dir / ".version").read_text(), OLD)
        self.assertEqual(config.read_text(), original)
        self.assertEqual(await fy.backend("a", "x", None), {"version": "1.0.0"})

    async def test_a_corrupt_package_fails_in_the_swap_and_the_old_copy_is_back(self):
        loader = await self.start()
        before = {p.name: p.read_bytes() for p in loader.dir.iterdir() if p.is_file()}
        loader.net.commit("o", "a", "HEAD", NEW)
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_STORED) as z:
            z.writestr("a-%s/plugin.json" % NEW, meta("1.1.0"))
            z.writestr("a-%s/main.js" % NEW, "NEW-1.1.0-NEW-1.1.0")
        data = bytearray(raw.getvalue())
        data[data.index(b"NEW-1.1.0-NEW")] ^= 0xFF
        loader.net.archive("o", "a", NEW, bytes(data))
        r = await loader.core("update", "a")
        self.assertIn("CRC", r["error"])
        self.assertEqual({p.name: p.read_bytes() for p in loader.dir.iterdir() if p.is_file()}, before)
        self.assertEqual(self.leftovers(loader), [])

    async def test_a_reply_that_is_not_a_commit_hash_is_rejected(self):
        loader = await self.start()
        loader.net.routes["https://api.github.com/repos/o/a/commits/HEAD"] = b"<html>nope</html>"
        r = await loader.core("update", "a")
        self.assertEqual(r["error"], "unexpected reply from GitHub")
        c = (await loader.core("check"))["checked"]["a"]
        self.assertEqual(c["state"], "error")

    async def test_sources_without_a_usable_github_link_say_why(self):
        loader = await self.start()
        (loader.dir / ".source").unlink()
        (loader.dir / "plugin.json").write_text(meta("1.0.0", source=""))
        self.assertEqual((await loader.core("update", "a"))["error"], "Automatic checking unavailable: no update source recorded")
        (loader.dir / ".source").write_text("https://example.com/o/a")
        self.assertEqual((await loader.core("update", "a"))["error"], "Automatic checking unavailable: the recorded source is not a GitHub repository link")
        (loader.dir / ".source").unlink()
        (loader.dir / "plugin.json").write_text(meta("1.0.0"))
        self.serve(loader, "1.1.0", NEW)
        self.assertEqual((await loader.core("update", "a"))["ok"], "a")

    async def test_install_by_link_and_from_the_store_obey_the_compatibility_rule(self):
        loader = await self.start()
        fy = loader.fy
        bad = H.mkzip({"plugin.json": json.dumps({"id": "z", "name": "Z", "framey": {"min": "3.0.0"}}), "main.js": "1"})
        self.assertEqual(await fy.install_zip(bad), {"error": "Needs Framey 3.0.0 or newer; you have 1.0.2"})
        self.assertFalse((loader.plugins / "z").exists())
        good = H.mkzip({"plugin.json": json.dumps({"id": "z", "name": "Z", "api": 1}), "main.js": "1"})
        self.assertEqual(await fy.install_zip(good), {"ok": "z"})
        (loader.root / "store" / "s").mkdir()
        (loader.root / "store" / "s" / "plugin.json").write_text(json.dumps({"name": "S", "platform": ["linux-sparc"]}))
        r = await loader.core("add", "s")
        self.assertIn("this headset is", r["error"])
        self.assertFalse((loader.plugins / "s").exists())
        (loader.root / "store" / "s" / "plugin.json").write_text(json.dumps({"name": "S"}))
        self.assertEqual(await loader.core("add", "s"), {"ok": "s"})
        loader.net.routes["https://github.com/o/a/archive/%s.zip" % NEW] = bad

class VersionTests(Base):
    def releases(self, loader, rows):
        loader.net.json("o/a/releases?per_page=10&page=1", rows)

    async def test_version_list_marks_installed_older_and_prerelease_entries(self):
        loader = await self.start(version="1.0.0")
        self.releases(loader, [release("v1.1.0-rc.1", True), release("v1.0.0"), release("v0.9.0", body="old notes")])
        r = await loader.core("ver_list", {"id": "a"})
        tags = {v["tag"]: v for v in r["versions"]}
        self.assertEqual([v["tag"] for v in r["versions"]], ["v1.1.0-rc.1", "v1.0.0", "v0.9.0"])
        self.assertTrue(tags["v1.0.0"]["current"])
        self.assertFalse(tags["v1.1.0-rc.1"]["eligible"])
        self.assertTrue(tags["v0.9.0"]["downgrade"] and tags["v0.9.0"]["eligible"])
        self.assertEqual(await loader.core("notes", {"id": "a", "tag": "v0.9.0"}), {"notes": "old notes", "url": "https://github.com/o/a/releases/tag/v0.9.0"})

    async def test_downgrade_needs_confirmation_keeps_the_previous_copy_and_can_be_restored(self):
        loader = await self.start(version="1.0.0")
        fy = loader.fy
        await fy.backend("a", "x", 1)
        self.serve(loader, "0.9.0", OLDER, tag="v0.9.0")
        ask = await loader.core("ver_install", {"id": "a", "tag": "v0.9.0"})
        self.assertIn("Going back to 0.9.0", ask["confirm"])
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        r = await loader.core("ver_install", {"id": "a", "tag": "v0.9.0", "confirm": True})
        self.assertEqual(r, {"ok": "a", "version": "0.9.0", "kept_previous": True})
        self.assertEqual((loader.dir / "main.js").read_text(), "NEW-0.9.0")
        self.assertEqual(self.leftovers(loader), [".old-a"])
        row = (await loader.core("list"))[0]
        self.assertIs(row["held"], True)
        self.assertIsNone(row["pending"])
        self.assertEqual(ident_kind(loader.dir), "release")
        self.assertEqual(await loader.core("rollback", "a"), {"ok": "a"})
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        self.assertEqual(json.loads((loader.dir / "plugin.json").read_text())["version"], "1.0.0")
        self.assertEqual(self.leftovers(loader), [])
        self.assertIs((await loader.core("list"))[0]["held"], False)

    async def test_done_removes_the_kept_copy_and_rollback_without_one_is_refused(self):
        loader = await self.start(version="1.0.0")
        self.serve(loader, "0.9.0", OLDER, tag="v0.9.0")
        await loader.core("ver_install", {"id": "a", "tag": "v0.9.0", "confirm": True})
        self.assertEqual(await loader.core("finish", "a"), {"ok": "a"})
        self.assertEqual(self.leftovers(loader), [])
        self.assertEqual(await loader.core("rollback", "a"), {"error": "nothing to roll back"})
        self.assertEqual(await loader.core("finish", "a"), {"error": "no pending update"})

    async def test_an_upgrade_keeps_the_previous_copy_and_offers_restore_until_finish(self):
        loader = await self.start()
        self.serve(loader, "1.1.0", NEW)
        self.assertEqual(await loader.core("update", "a"), {"ok": "a", "version": "1.1.0"})
        self.assertEqual(self.leftovers(loader), [".old-a"])
        self.assertTrue((await loader.core("list"))[0]["held"])
        self.assertEqual(await loader.core("finish", "a"), {"ok": "a"})
        self.assertEqual(self.leftovers(loader), [])
        self.assertEqual(await loader.core("rollback", "a"), {"error": "nothing to roll back"})

    async def test_a_later_upgrade_replaces_the_kept_copy_with_the_newer_previous_version(self):
        loader = await self.start()
        self.serve(loader, "1.1.0", NEW)
        await loader.core("update", "a")
        self.serve(loader, "1.2.0", OLDER)
        self.assertEqual(await loader.core("update", "a"), {"ok": "a", "version": "1.2.0"})
        kept = json.loads((loader.plugins / ".old-a" / "plugin.json").read_text())
        self.assertEqual(kept["version"], "1.1.0")
        self.assertEqual(self.leftovers(loader), [".old-a"])
        self.assertEqual(await loader.core("rollback", "a"), {"ok": "a"})
        self.assertEqual((loader.dir / "main.js").read_text(), "NEW-1.1.0")
        self.assertEqual(self.leftovers(loader), [])

    async def test_removing_a_plugin_also_removes_its_kept_copy_so_recover_cannot_bring_it_back(self):
        loader = await self.start(version="1.0.0")
        self.serve(loader, "0.9.0", OLDER, tag="v0.9.0")
        await loader.core("ver_install", {"id": "a", "tag": "v0.9.0", "confirm": True})
        self.assertEqual(self.leftovers(loader), [".old-a"])
        self.assertEqual(await loader.core("uninstall", {"id": "a", "purge": False}), {"ok": True})
        self.assertEqual(self.leftovers(loader), [])
        loader.fy.recover()
        self.assertFalse((loader.plugins / "a").exists())

    async def test_a_kept_older_version_survives_recover_but_stale_leftovers_do_not(self):
        loader = await self.start(version="1.0.0")
        fy = loader.fy
        P = loader.plugins
        loader.plugin("b", {"version": "1.0.0"})
        loader.plugin("c", {"version": "2.0.0"})
        for pid, version in (("a", "2.0.0"), ("b", "0.5.0")):
            old = P / (".old-" + pid)
            old.mkdir()
            (old / "plugin.json").write_text(json.dumps({"id": pid, "name": pid, "version": version}))
        (P / ".stage-a").mkdir()
        gone = P / ".old-lost"
        gone.mkdir()
        (gone / "plugin.json").write_text(json.dumps({"id": "lost", "name": "lost", "version": "1.0.0"}))
        fy.recover()
        self.assertEqual(sorted(p.name for p in P.iterdir() if p.name.startswith(".")), [".old-a", ".old-b"])
        self.assertTrue((P / "lost" / "plugin.json").exists())

    async def test_install_of_a_chosen_version_refuses_a_frozen_module_and_a_prerelease_on_stable(self):
        loader = await self.start(version="1.0.0")
        self.serve(loader, "1.2.0", NEW, tag="v1.2.0")
        await loader.core("mod_freeze", {"id": "a", "frozen": True})
        r = await loader.core("ver_install", {"id": "a", "tag": "v1.2.0"})
        self.assertEqual(r["code"], "frozen")
        await loader.core("mod_freeze", {"id": "a", "frozen": False})
        self.serve(loader, "1.3.0-rc.1", NEW, tag="v1.3.0-rc.1")
        r = await loader.core("ver_install", {"id": "a", "tag": "v1.3.0-rc.1"})
        self.assertEqual(r["code"], "prerelease")
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")

    async def test_a_lock_is_held_while_a_version_install_runs(self):
        loader = await self.start(version="1.0.0")
        fy = loader.fy
        self.serve(loader, "1.2.0", NEW, tag="v1.2.0")
        first = asyncio.create_task(fy.core("ver_install", {"id": "a", "tag": "v1.2.0"}))
        await asyncio.sleep(0)
        busy = await fy.core("ver_install", {"id": "a", "tag": "v1.2.0"})
        self.assertEqual(busy, fy.BUSY)
        self.assertEqual(await fy.core("repair", {"id": "a"}), fy.BUSY)
        self.assertEqual(await fy.core("batch_apply", {"ids": ["a"]}), fy.BUSY)
        self.assertEqual(await fy.core("companion_fetch", {"id": "a", "name": "t"}), fy.BUSY)
        self.assertEqual(await fy.core("uninstall", "a"), fy.BUSY)
        self.assertEqual(await fy.core("loader_check", None), fy.BUSY)
        self.assertIsInstance(await fy.core("list", None), list)
        self.assertEqual((await first)["ok"], "a")
        self.assertNotEqual(await fy.core("update", "a"), fy.BUSY)

    async def test_repair_reinstalls_the_recorded_commit_and_keeps_the_settings(self):
        loader = await self.start(version="1.0.0")
        fy = loader.fy
        (loader.dir / ".identity.json").write_text(json.dumps({"v": 1, "kind": "commit", "source": "o/a", "commit": OLD}))
        loader.net.archive("o", "a", OLD, package("1.0.0", OLD, main="PRISTINE"))
        (loader.dir / "main.js").write_text("DAMAGED")
        cfg = fy.dirs("a").config
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text('{"keep": 1}')
        r = await loader.core("repair", {"id": "a"})
        self.assertEqual(r, {"ok": "a", "version": "1.0.0"})
        self.assertEqual((loader.dir / "main.js").read_text(), "PRISTINE")
        self.assertEqual((cfg / "settings.json").read_text(), '{"keep": 1}')
        ask = await loader.core("repair", {"id": "a", "reset": True})
        self.assertIn("deletes this module's saved settings", ask["confirm"])
        self.assertTrue((cfg / "settings.json").exists())
        r = await loader.core("repair", {"id": "a", "reset": True, "confirm": True})
        self.assertEqual(r["ok"], "a")
        self.assertFalse(cfg.exists())

    async def test_repair_without_an_immutable_source_says_so(self):
        loader = await self.start(version="1.0.0", sha="not a sha")
        r = await loader.core("repair", {"id": "a"})
        self.assertEqual(r["error"], "cannot repair: no immutable source recorded")
        (loader.dir / ".identity.json").write_text(json.dumps({"v": 1, "kind": "branch", "source": "o/a", "tag": "main"}))
        self.assertEqual((await loader.core("repair", {"id": "a"}))["error"], "cannot repair: no immutable source recorded")


def ident_kind(path):
    return json.loads((pathlib.Path(path) / ".identity.json").read_text())["kind"]


class BatchTests(Base):
    async def test_batch_plan_and_apply_report_one_row_per_module_and_keep_going_on_failure(self):
        loader = await self.start()
        fy = loader.fy
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        for pid in ("b", "c", "d"):
            loader.plugin(pid, {"version": "1.0.0", "source": "https://github.com/o/" + pid}, extra={".version": OLD})
        await loader.core("mod_freeze", {"id": "c", "frozen": True})
        await loader.core("disable", "d")
        self.serve(loader, "1.1.0", NEW)
        loader.net.commit("o", "b", "HEAD", NEW)
        plan = await loader.core("batch_plan", {})
        rows = {r["id"]: r for r in plan["items"]}
        self.assertTrue(rows["a"]["eligible"])
        self.assertEqual(rows["a"]["to"], "1.1.0")
        self.assertEqual((rows["c"]["eligible"], rows["c"]["reason"]), (False, "frozen"))
        self.assertEqual((rows["d"]["eligible"], rows["d"]["reason"]), (False, "disabled"))
        self.assertFalse(rows["b"]["eligible"] and rows["b"]["to"] == "")
        r = await loader.core("batch_apply", {"ids": ["a", "b", "c"]})
        states = {x["id"]: x["state"] for x in r["results"]}
        self.assertEqual(states, {"a": "ok", "b": "failed", "c": "skipped"})
        self.assertEqual((r["ok"], r["failed"]), (1, 1))
        self.assertEqual((loader.dir / "main.js").read_text(), "NEW-1.1.0")
        stages = []
        for e in page.events():
            if e[:2] == ["_core", "progress"]:
                stages.append(e[2])
        mine = [s for s in stages if s["id"] == "a"]
        self.assertEqual([(s["stage"], s["state"]) for s in mine if s["state"] != "running" or s["stage"] != "download"][:12], [("check", "running"), ("check", "ok"), ("download", "ok"), ("verify", "running"), ("verify", "ok"), ("unpack", "running"), ("unpack", "ok"), ("swap", "running"), ("swap", "ok"), ("reload", "running"), ("reload", "ok")])
        self.assertTrue(all(s["total"] == 6 for s in mine))
        done = [s for s in mine if s["stage"] == "download" and s["state"] == "ok"][0]
        self.assertEqual(done["measured"], {"done": len(loader.net.routes["https://github.com/o/a/archive/%s.zip" % NEW]), "total": len(loader.net.routes["https://github.com/o/a/archive/%s.zip" % NEW])})

    async def test_a_progress_percentage_is_never_invented(self):
        loader = await self.start()
        fy = loader.fy
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        self.serve(loader, "1.1.0", NEW, tag="v1.1.0")
        loader.net.report = False
        r = await loader.core("ver_install", {"id": "a", "tag": "v1.1.0"})
        self.assertEqual(r["ok"], "a")
        for e in page.events():
            if e[1] == "progress":
                self.assertIsNone(e[2]["measured"])


class CompanionTests(Base):
    async def test_companion_download_verifies_the_digest_and_installs_under_the_data_dir(self):
        archive = H.mktar({"tool": "#!/bin/sh\necho hi\n", "readme": "r"}, top="")
        sha = hashlib.sha256(archive).hexdigest()
        loader = await self.start(version="1.0.0", companions=[{"name": "tool", "version": "1.2.0", "platform": loader_platform(), "asset": "tool.tar.gz", "sha256": sha, "size": len(archive), "exec": ["tool"]}])
        url = "https://github.com/o/a/releases/download/v1.0.0/tool.tar.gz"
        asset = {"name": "tool.tar.gz", "browser_download_url": url, "digest": "sha256:" + sha}
        loader.net.json("o/a/releases/tags/v1.0.0", dict(release("v1.0.0"), assets=[asset]))
        loader.net.routes[url] = archive
        r = await loader.core("companion_fetch", {"id": "a", "name": "tool"})
        self.assertEqual(r["ok"], "tool")
        self.assertEqual(r["digest"], "verified")
        self.assertEqual(r["note"], "integrity check, not a signature")
        base = loader.fy.dirs("a").data / "bin" / "tool-1.2.0"
        self.assertEqual(pathlib.Path(r["path"]), base / "tool")
        self.assertTrue((base / "tool").is_file())
        loader.net.routes[url] = archive + b"x"
        bad = await loader.core("companion_fetch", {"id": "a", "name": "tool"})
        self.assertIn("error", bad)
        self.assertEqual((await loader.core("companion_fetch", {"id": "a", "name": "nope"}))["code"], "unknown_companion")


def loader_platform():
    import platform
    m = platform.machine().lower()
    return "linux-" + {"amd64": "x86_64", "arm64": "aarch64"}.get(m, m)


class AutoCheckTests(Base):
    async def test_a_cycle_stores_results_notifies_once_and_never_installs(self):
        loader = await self.start(prefs={"autocheck": True, "notify_plugins": True})
        fy = loader.fy
        page, task = await H.attach(loader, "main")
        bar, btask = await H.attach(loader, "bar")
        self.addCleanup(task.cancel)
        self.addCleanup(btask.cancel)
        u = fy.upd()
        u.SPACING = 0
        loader.net.json("chaosfox26/framey/releases/latest", release("v1.0.2", commit="c" * 40))
        loader.net.commit("chaosfox26", "framey", "v1.0.2", (loader.root / ".version").read_text())
        loader.net.commit("o", "a", "HEAD", NEW)
        await u._cycle(21600)
        s = fy.settings()
        self.assertEqual(s["updates"]["plugins"]["a"], {"latest": NEW[:7], "state": "update"})
        self.assertGreater(s["updates"]["last"], 0)
        notices = [e for e in page.events() if e[1] == "notice"]
        self.assertEqual(len(notices), 1)
        self.assertIn("open:modules", json.dumps(notices[0]))
        self.assertEqual(bar.exprs()[-1], "window.FYBar&&FYBar.badge(1)")
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        fy.mono = lambda: 0
        s["updates"]["last"] = 0
        fy.save(s)
        await u._cycle(21600)
        self.assertEqual(len([e for e in page.events() if e[1] == "notice"]), 1)

    async def test_panel_open_checks_only_when_the_pref_is_on_and_a_day_has_passed(self):
        loader = await self.start()
        fy = loader.fy
        loader.net.json("chaosfox26/framey/releases/latest", release("v1.0.2"))
        loader.net.commit("chaosfox26", "framey", "v1.0.2", (loader.root / ".version").read_text())
        loader.net.commit("o", "a", "HEAD", OLD)
        await fy.panel(True)
        await asyncio.sleep(0.2)
        self.assertEqual(loader.net.calls, [])
        self.assertIsNone(fy.updates)
        await loader.core("prefs_set", {"autocheck": True})
        fy.updates.disarm()
        await fy.panel(True)
        self.assertTrue(await H.settle(lambda: fy.settings()["updates"]["last"] > 0))
        calls = len(loader.net.calls)
        await fy.panel(True)
        await asyncio.sleep(0.3)
        self.assertEqual(len(loader.net.calls), calls)


class LoaderUpdateTests(Base):
    def serve_loader(self, loader, files=None, tag="v2.0.0", sha="b" * 40, manifest=None):
        net = loader.net
        net.json("chaosfox26/framey/releases/latest", dict(release(tag, body="Loader notes"), assets=[]))
        net.commit("chaosfox26", "framey", tag, sha)
        full = files if files is not None else H.full_tree()
        net.archive("chaosfox26", "framey", sha, H.mkzip(full, top="framey-" + sha))
        return full

    async def test_a_complete_package_is_installed_beside_a_backup_and_the_user_data_moves_over(self):
        loader = await self.start()
        fy = loader.fy
        loader.plugin("keep", {"version": "1.0.0"})
        (loader.root / "store" / "x").mkdir()
        self.serve_loader(loader)
        restarted = []
        fy.restart = lambda: restarted.append(1)
        r = await loader.core("loader_update")
        self.assertEqual(r, {"ok": "framey", "tag": "v2.0.0", "restart": True})
        root = loader.root
        self.assertEqual((root / ".version").read_text(), "b" * 40)
        self.assertTrue((root / "plugins" / "keep" / "plugin.json").exists())
        self.assertTrue((root / "store" / "x").is_dir())
        bak = root.with_name(root.name + ".bak")
        self.assertTrue(bak.is_dir())
        self.assertEqual((bak / ".version").read_text(), "a" * 40)
        self.assertEqual(await loader.core("loader"), {"commit": "bbbbbbb", "kept": True})
        again = await loader.core("loader_update")
        self.assertEqual(again, {"error": "Finish the previous Framey update first (Done or Roll back)"})
        self.assertEqual(await loader.core("loader_roll"), {"ok": "framey", "restart": True})
        self.assertEqual((root / ".version").read_text(), "a" * 40)
        self.assertTrue((root / "plugins" / "keep").is_dir())
        self.assertFalse(bak.exists())
        self.assertEqual(await loader.core("loader_done"), {"error": "nothing to finish"})

    async def test_an_incomplete_package_is_refused_and_nothing_changes(self):
        loader = await self.start()
        root = loader.root
        before = sorted(p.name for p in root.iterdir())
        full = H.full_tree()
        for missing in ("fy_updates.py", "fy_supervisor.py", "fy-api.js", "fy_worker.py", "icon.svg", "fy-dev.js"):
            files = {k: v for k, v in full.items() if k != missing}
            self.serve_loader(loader, files)
            r = await loader.core("loader_update")
            self.assertEqual(r, {"error": "Framey update not applied: the package has no " + missing}, missing)
            self.assertEqual(sorted(p.name for p in root.iterdir()), before)
            self.assertFalse(root.with_name(root.name + ".bak").exists())
            self.assertFalse(root.with_name(root.name + ".new").exists())
        self.assertEqual((root / ".version").read_text(), "a" * 40)

    async def test_a_package_with_a_module_that_does_not_compile_is_refused(self):
        loader = await self.start()
        full = H.full_tree()
        full["fy_state.py"] = "def broken(:\n"
        self.serve_loader(loader, full)
        r = await loader.core("loader_update")
        self.assertIn("Framey update not applied", r["error"])
        self.assertIn("fy_state.py", r["error"])
        full = H.full_tree()
        full["framey.py"] = "def broken(:\n"
        self.serve_loader(loader, full)
        self.assertIn("Framey update not applied", (await loader.core("loader_update"))["error"])
        self.assertFalse(loader.root.with_name(loader.root.name + ".bak").exists())

    async def test_a_package_that_names_a_file_outside_itself_is_refused(self):
        loader = await self.start()
        full = H.full_tree()
        full["framey.py"], replacements = re.subn(
            r"(?m)^(MANIFEST\s*=\s*\()", r'\1"../escape.py",', full["framey.py"], count=1,
        )
        self.assertEqual(replacements, 1, "the fixture must change the package manifest")
        self.assertEqual(loader.fy.needs(full["framey.py"])[0], "../escape.py")
        self.serve_loader(loader, full)
        r = await loader.core("loader_update")
        self.assertEqual(r, {"error": "Framey update not applied: the package has no ../escape.py"})

    async def test_a_package_from_before_the_manifest_is_judged_by_the_four_old_files(self):
        loader = await self.start()
        self.serve_loader(loader, {"framey.py": STUB, "inject.js": "1", "bar.js": "1", "icon.svg": "<svg/>"})
        r = await loader.core("loader_update")
        self.assertEqual(r, {"ok": "framey", "tag": "v2.0.0", "restart": True})
        await loader.core("loader_done")
        self.serve_loader(loader, {"framey.py": STUB, "inject.js": "1", "bar.js": "1"}, sha="c" * 40)
        self.assertEqual(await loader.core("loader_update"), {"error": "Framey update not applied: the package has no icon.svg"})

    async def test_a_loader_update_reports_the_same_stage_steps_as_a_module_update(self):
        loader = await self.start()
        loader.fy.restart = lambda: None
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        self.serve_loader(loader)
        r = await loader.core("loader_update")
        self.assertEqual(r["ok"], "framey")
        mine = [e[2] for e in page.events() if e[:2] == ["_core", "progress"] and e[2]["id"] == "_loader"]
        self.assertEqual([(s["stage"], s["state"]) for s in mine if (s["stage"], s["state"]) != ("download", "running")], [("check", "ok"), ("download", "ok"), ("verify", "running"), ("verify", "ok"), ("unpack", "running"), ("unpack", "ok"), ("swap", "running"), ("swap", "ok"), ("reload", "ok")])
        self.assertTrue(all(s["total"] == 6 for s in mine))

    async def test_a_loader_update_that_fails_names_the_stage_that_failed(self):
        loader = await self.start()
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        self.serve_loader(loader, {"framey.py": STUB, "inject.js": "1", "bar.js": "1"})
        r = await loader.core("loader_update")
        self.assertEqual(r, {"error": "Framey update not applied: the package has no icon.svg"})
        mine = [e[2] for e in page.events() if e[:2] == ["_core", "progress"] and e[2]["id"] == "_loader"]
        self.assertEqual([(s["stage"], s["state"]) for s in mine][-2:], [("unpack", "running"), ("unpack", "fail")])
        self.assertEqual(mine[-1]["error"], "the package has no icon.svg")

    async def test_the_chosen_channel_and_the_downgrade_rule_apply_to_the_real_install(self):
        loader = await self.start()
        fy = loader.fy
        self.serve_loader(loader, tag="v1.0.1")
        r = await loader.core("loader_update")
        self.assertIn("Going back to v1.0.1", r["confirm"])
        self.assertFalse(loader.root.with_name(loader.root.name + ".bak").exists())
        r = await loader.core("loader_update", {"confirm": True})
        self.assertEqual(r["ok"], "framey")
        await loader.core("loader_done")
        loader.net.json("chaosfox26/framey/releases/latest", release("v3.0.0-rc.1", True))
        loader.net.commit("chaosfox26", "framey", "v3.0.0-rc.1", "c" * 40)
        pre = await loader.core("loader_update")
        self.assertEqual(pre["code"], "prerelease")
        self.assertEqual((loader.root / ".version").read_text(), "b" * 40)

    async def test_loader_check_reports_both_identities_and_never_installs(self):
        loader = await self.start()
        fy = loader.fy
        self.serve_loader(loader)
        r = await loader.core("loader_check")
        self.assertEqual(r["loader"]["state"], "update")
        self.assertEqual(r["candidate"]["tag"], "v2.0.0")
        self.assertEqual(r["installed"]["version"], fy.VERSION)
        self.assertEqual(r["installed"]["commit"], "aaaaaaa")
        self.assertEqual((loader.root / ".version").read_text(), "a" * 40)
        self.assertEqual(fy.settings()["updates"]["loader"], {"tag": "v2.0.0", "state": "update"})
        self.assertEqual(await loader.core("badge"), {"n": 1, "loader": True})

    async def test_choosing_a_loader_version_from_the_history_uses_the_same_install_path(self):
        loader = await self.start()
        fy = loader.fy
        self.serve_loader(loader, tag="v2.0.0")
        r = await loader.core("ver_install", {"id": "_loader", "tag": "v2.0.0"})
        self.assertEqual(r, {"ok": "framey", "tag": "v2.0.0", "restart": True})
        self.assertIs(fy.loader_install, fy.install_loader)


if __name__ == "__main__":
    unittest.main()
