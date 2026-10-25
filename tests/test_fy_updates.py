import asyncio
import base64
import email.message
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_updates as fu

SHA_A, SHA_B, SHA_C, SHA_D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
LOADER_SHA, LOADER_NEW = "1" * 40, "2" * 40


def http_error(code, remaining=None):
    h = email.message.Message()
    if remaining is not None:
        h["X-RateLimit-Remaining"] = remaining
    return urllib.error.HTTPError("https://api.github.com/x", code, "x", h, None)


def zip_of(files, top="repo-sha"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, c in files.items():
            z.writestr(top + "/" + n if top else n, c)
    return b.getvalue()


def rel_json(tag, pre=False, draft=False, body="notes", assets=None, published="2026-01-01T00:00:00Z"):
    return {"tag_name": tag, "prerelease": pre, "draft": draft, "body": body, "published_at": published, "html_url": "https://github.com/o/r/releases/tag/" + tag, "target_commitish": "main", "assets": assets or []}


def plugin_zip(pid, version, **extra):
    meta = {"id": pid, "name": pid.title(), "version": version, "short": "x", **extra}
    return zip_of({"plugin.json": json.dumps(meta), "main.js": "// " + version})


class Timer:
    def __init__(self, delay, cb):
        self.delay, self.cb, self.cancelled = delay, cb, False

    def cancel(self):
        self.cancelled = True


class TimerLoop:
    def __init__(self):
        self.real = asyncio.get_running_loop()
        self.timers = []

    def call_later(self, delay, cb):
        t = Timer(delay, cb)
        self.timers.append(t)
        return t

    def __getattr__(self, name):
        return getattr(self.real, name)


class FakeHost:
    def __init__(self, tmp):
        self.ROOT = Path(tmp) / "loader"
        self.PLUGINS = Path(tmp) / "plugins"
        self.HOME = Path(tmp) / "home"
        for d in (self.ROOT, self.PLUGINS, self.HOME):
            d.mkdir(parents=True, exist_ok=True)
        (self.ROOT / ".version").write_text(LOADER_SHA)
        self.VERSION, self.PLATFORM, self.API_GENERATION = "1.0.2", "linux-aarch64", 1
        self.pkg = None
        self.state = {"disabled": [], "modules": {"frozen": {}}, "prefs": {}, "updates": {}}
        self.clock, self.mono_clock, self.random = 1_000_000.0, 0.0, 0.5
        self.events, self.notices, self.reloads, self.logs, self.swaps = [], [], [], [], []
        self.api, self.shas, self.archives, self.releases = {}, {}, {}, {}
        self.api_calls, self.latest_calls = [], []
        self.attached_main = True
        self.fail_reload = set()
        self.archive_progress = None
        self.timer_loop = None
        self.fail_archive = set()
        self.fail_unpack = False
        self.fail_swap = False

    def log(self, *a):
        self.logs.append(a)

    def now(self):
        return self.clock

    def mono(self):
        return self.mono_clock

    def rand(self):
        return self.random

    def loop(self):
        return self.timer_loop or asyncio.get_running_loop()

    def settings(self):
        return json.loads(json.dumps(self.state))

    def save(self, s):
        self.state = json.loads(json.dumps(s))

    def prefs(self, **kw):
        self.state.setdefault("prefs", {}).update(kw)

    def freeze(self, pid, version="1.0.0"):
        self.state["modules"]["frozen"][pid] = {"at": 1, "version": version}

    def plugin_dir(self, pid):
        return self.PLUGINS / pid

    def manifest(self, d):
        try:
            m = json.loads((Path(d) / "plugin.json").read_text())
        except Exception:
            return None
        m["id"] = Path(d).name
        return m

    def manifests(self):
        return [m for m in map(self.manifest, sorted(p for p in self.PLUGINS.iterdir() if not p.name.startswith("."))) if m]

    def real(self, pid):
        p = self.PLUGINS / pid
        return p if p.exists() else None

    def add(self, pid, version="1.0.0", sha=SHA_A, source="https://github.com/o/" + "r", **extra):
        d = self.PLUGINS / pid
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": pid.title(), "version": version, "short": "x", **extra}
        if source and "source" not in extra:
            (d / ".source").write_text(source)
        (d / "plugin.json").write_text(json.dumps(meta))
        (d / "main.js").write_text("// " + version)
        if sha:
            (d / ".version").write_text(sha)
        return d

    def source(self, m):
        raw = ""
        f = self.PLUGINS / m["id"] / ".source"
        if f.exists():
            raw = f.read_text().strip()
        raw = raw or m.get("source", "")
        if raw.startswith("https://github.com/"):
            o, r = raw.split("/")[3:5]
            return (o, r, "HEAD"), ""
        return None, "Automatic checking unavailable: no update source recorded"

    def gate(self, meta):
        return meta.get("_blocked", "")

    def fetch(self, url, headers=None, hosts=None, limit=5_000_000, progress=None):
        self.fetched = (url, headers, hosts, limit)
        data = self.assets[url]
        if progress and self.archive_progress is not False:
            progress(len(data) // 2, len(data))
            progress(len(data), len(data))
        return data

    def api_json(self, path):
        self.api_calls.append(path)
        if path not in self.api:
            raise http_error(404)
        v = self.api[path]
        if isinstance(v, Exception):
            raise v
        return json.loads(json.dumps(v))

    def release(self, owner, repo):
        v = self.releases.get((owner, repo))
        if isinstance(v, Exception):
            raise v
        return v

    def latest(self, owner, repo, ref):
        self.latest_calls.append((owner, repo, ref))
        v = self.shas.get((owner, repo, ref))
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise http_error(404)
        return v

    def archive(self, owner, repo, sha, progress=None):
        if sha in self.fail_archive:
            raise http_error(500)
        data = self.archives[sha]
        if progress and self.archive_progress is not False:
            progress(len(data) // 2, len(data))
            progress(len(data), len(data))
        return data

    def vet(self, data):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return {"error": "not a zip"}
        infos = {i.filename: i for i in z.infolist() if not i.filename.endswith("/")}
        base = ""
        names = list(infos)
        if "plugin.json" not in names:
            tops = {n.split("/")[0] for n in names}
            if len(tops) != 1 or next(iter(tops)) + "/plugin.json" not in names:
                return {"error": "no plugin.json"}
            base = next(iter(tops)) + "/"
        meta = json.loads(z.read(base + "plugin.json"))
        return z, infos, base, meta.get("id"), meta

    def unpack(self, z, infos, base):
        def fill(stage):
            if self.fail_unpack:
                raise ValueError("zip too large unpacked")
            for n, i in infos.items():
                if n.startswith(base):
                    f = stage / n[len(base):]
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(z.read(i))

        return fill

    def stage_swap(self, pid, fill, dest=None, keep=False):
        dest = dest or self.PLUGINS / pid
        stage = dest.with_name(".stage-" + dest.name)
        old = dest.with_name(".old-" + dest.name)
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        self.swaps.append(pid)
        try:
            fill(stage)
            if self.fail_swap:
                raise OSError("disk full")
            had = dest.exists()
            if had:
                dest.rename(old)
            try:
                stage.rename(dest)
            except OSError:
                if had:
                    old.rename(dest)
                raise
            if not keep:
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def restore(self, dest):
        old, bad = dest.with_name(".old-" + dest.name), dest.with_name(".stage-" + dest.name)
        if not old.is_dir():
            raise OSError("no previous version to restore")
        shutil.rmtree(bad, ignore_errors=True)
        dest.rename(bad)
        old.rename(dest)
        shutil.rmtree(bad, ignore_errors=True)

    async def reload(self, pid=None):
        self.reloads.append(pid)
        if pid in self.fail_reload:
            self.fail_reload.discard(pid)
            raise RuntimeError("backend would not start")

    def attached(self, target):
        return self.attached_main and target == "main"

    async def notify_page(self, plugin, name, payload):
        self.notices.append((plugin, name, payload))

    async def progress(self, job, ev):
        self.events.append(ev)

    def stages(self, job=None):
        out = []
        for e in self.events:
            pair = (e["stage"], e["state"])
            if (job is None or e["job"] == job) and (not out or out[-1] != pair):
                out.append(pair)
        return out


def releases_path(page=1, per=10):
    return "o/r/releases?per_page=%d&page=%d" % (per, page)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.host = FakeHost(self.tmp)
        self.up = fu.Updates(self.host)
        self.up.SPACING = 0

    async def asyncSetUp(self):
        self.host.timer_loop = None


class VersionTests(unittest.TestCase):
    def test_table(self):
        p = fu.parse_version
        self.assertEqual(p("v1.2.3"), p("1.2.3.0"))
        self.assertEqual(p("1.0"), p("1.0.0"))
        self.assertIsNone(p("release-1"))
        self.assertIsNone(p(None))
        self.assertIsNone(p("1.2.3.4.5"))
        self.assertTrue(p("1.0.0-rc.1") < p("1.0.0"))
        self.assertTrue(p("1.0.0-rc.1") < p("1.0.0-rc.2") < p("1.0.0-rc.10"))
        self.assertTrue(p("1.0.0-rc.2") < p("1.0.0-rc.2.1"))
        self.assertTrue(p("1.0.0-1") < p("1.0.0-alpha"))
        self.assertTrue(p("1.0.0") < p("1.0.1-rc.1"))
        self.assertTrue(p("1.9.0") < p("1.10.0"))
        self.assertTrue(fu.is_pre("v2.0.0-beta") and not fu.is_pre("2.0.0"))

    def test_digest(self):
        data = b"payload"
        good = "sha256:" + hashlib.sha256(data).hexdigest()
        self.assertEqual(fu.check_digest(data, good), "verified")
        self.assertEqual(fu.check_digest(data, good.upper().replace("SHA256", "sha256")), "verified")
        self.assertEqual(fu.check_digest(data, "sha256:" + "0" * 64), "mismatch")
        self.assertEqual(fu.check_digest(data, ""), "absent")
        self.assertEqual(fu.check_digest(data, None), "absent")
        self.assertEqual(fu.check_digest(data, "md5:abc"), "unsupported")
        self.assertEqual(fu.check_digest(data, "sha256:xyz"), "unsupported")

    def test_why_rate_limit(self):
        self.assertIn("rate limit", fu.why(http_error(429)))
        self.assertIn("rate limit", fu.why(http_error(403, "0")))
        self.assertIn("HTTP 403", fu.why(http_error(403, "5")))
        self.assertIn("not found", fu.why(http_error(404)))


class LoaderTests(Base):
    def setUp(self):
        super().setUp()
        h = self.host
        h.api["chaosfox26/framey/releases/latest"] = rel_json("v1.1.0")
        h.api["chaosfox26/framey/releases?per_page=10"] = [rel_json("v1.2.0-rc.1", pre=True, draft=True), rel_json("v1.2.0-rc.1", pre=True), rel_json("v1.1.0")]
        h.shas[("chaosfox26", "framey", "v1.1.0")] = LOADER_NEW
        h.shas[("chaosfox26", "framey", "v1.2.0-rc.1")] = SHA_C

    async def test_stable_uses_latest(self):
        c = await self.up.loader_candidate()
        self.assertEqual((c["tag"], c["commit"], c["prerelease"], c["channel"]), ("v1.1.0", LOADER_NEW, False, "stable"))
        self.assertEqual(c["published"], "2026-01-01T00:00:00Z")
        self.assertEqual(self.host.api_calls, ["chaosfox26/framey/releases/latest"])

    async def test_prerelease_takes_first_non_draft(self):
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        c = await self.up.loader_candidate()
        self.assertEqual((c["tag"], c["prerelease"], c["channel"]), ("v1.2.0-rc.1", True, "prerelease"))
        self.assertEqual(c["label"], "Testing build")

    async def test_testing_build_label_only_in_dev(self):
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        self.assertEqual((await self.up.loader_candidate())["label"], "Testing build")
        self.host.prefs(dev={"enabled": False})
        self.assertEqual((await self.up.loader_candidate())["tag"], "v1.1.0")
        self.assertEqual((await self.up.loader_candidate())["label"], "")

    async def test_bad_channel_falls_back_to_stable(self):
        self.host.prefs(channel="nightly")
        self.assertEqual((await self.up.loader_candidate())["channel"], "stable")

    async def test_explicit_channel_argument(self):
        self.host.prefs(dev={"enabled": True})
        c = await self.up.loader_candidate("prerelease")
        self.assertEqual(c["tag"], "v1.2.0-rc.1")

    async def test_error_reported(self):
        self.host.api["chaosfox26/framey/releases/latest"] = http_error(429)
        c = await self.up.loader_candidate()
        self.assertIn("rate limit", c["error"])

    async def test_check_shows_both_identities_and_never_installs(self):
        r = await self.up.loader_check()
        L = r["loader"]
        self.assertEqual(L["state"], "update")
        self.assertEqual(L["installed"], LOADER_SHA[:7])
        self.assertEqual(L["latest"], "v1.1.0")
        self.assertEqual(r["candidate"], {"tag": "v1.1.0", "commit": LOADER_NEW, "prerelease": False, "published": "2026-01-01T00:00:00Z", "label": "", "channel": "stable"})
        self.assertEqual(r["installed"], {"version": "1.0.2", "commit": LOADER_SHA[:7], "tag": ""})
        self.assertFalse(L["downgrade"])
        self.assertEqual(L["notes"], "notes")
        self.assertEqual((self.host.swaps, self.host.reloads), ([], []))
        self.assertEqual(self.host.state["updates"]["loader"], {"tag": "v1.1.0", "state": "update"})

    async def test_prerelease_channel_without_developer_mode_falls_back_and_says_so(self):
        self.host.prefs(channel="prerelease")
        r = await self.up.loader_check()
        self.assertEqual(r["candidate"]["channel"], "stable")
        self.assertEqual(r["candidate"]["tag"], "v1.1.0")
        self.assertTrue(r["loader"]["channel_fallback"])
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        r = await self.up.loader_check()
        self.assertEqual(r["candidate"]["channel"], "prerelease")
        self.assertFalse(r["loader"]["channel_fallback"])

    async def test_loader_check_pushes_badge_once(self):
        pushes = []

        async def after(badge):
            pushes.append(badge)

        self.up.after = after
        await self.up.loader_check()
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0], {"n": 1, "loader": True})

    async def test_check_current(self):
        self.host.shas[("chaosfox26", "framey", "v1.1.0")] = LOADER_SHA
        r = await self.up.loader_check()
        self.assertEqual(r["loader"]["state"], "current")
        self.assertEqual(r["loader"]["installed"], "v1.1.0")
        self.assertNotIn("notes", r["loader"])

    async def test_channel_switch_changes_offer_not_install(self):
        a = await self.up.loader_check()
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        b = await self.up.loader_check()
        self.assertEqual((a["candidate"]["tag"], b["candidate"]["tag"]), ("v1.1.0", "v1.2.0-rc.1"))
        self.assertEqual((self.host.swaps, self.host.reloads), ([], []))

    async def test_downgrade_flag_when_switching_to_lower_stable(self):
        self.host.VERSION = "1.2.0-rc.2"
        r = await self.up.loader_check()
        self.assertTrue(r["loader"]["downgrade"])
        self.assertEqual(self.host.state["updates"]["loader"]["state"], "downgrade")
        self.assertEqual(self.up.badge()["loader"], False)

    async def test_prerelease_suffix_compare_is_update(self):
        self.host.VERSION = "1.1.0-rc.1"
        r = await self.up.loader_check()
        self.assertFalse(r["loader"]["downgrade"])
        self.host.VERSION = "1.1.0"
        self.host.api["chaosfox26/framey/releases/latest"] = rel_json("v1.1.0-rc.2", pre=True)
        self.host.shas[("chaosfox26", "framey", "v1.1.0-rc.2")] = SHA_D
        r = await self.up.loader_check()
        self.assertTrue(r["loader"]["downgrade"])

    async def test_loader_allowed_rules(self):
        cand = {"tag": "v1.2.0-rc.1", "commit": SHA_C, "prerelease": True}
        self.assertEqual(self.up.loader_allowed(cand)["code"], "prerelease")
        self.host.prefs(channel="prerelease")
        self.assertEqual(self.up.loader_allowed(cand)["code"], "prerelease")
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        self.assertIsNone(self.up.loader_allowed(cand))
        low = {"tag": "v0.9.0", "commit": SHA_C, "prerelease": False}
        self.assertIn("confirm", self.up.loader_allowed(low))
        self.assertIsNone(self.up.loader_allowed(low, True))
        self.assertEqual(self.up.loader_allowed({})["code"], "no_candidate")

    async def test_loader_version_install_needs_hook_and_guards(self):
        r = await self.up.install_version("_loader", "v1.1.0", False)
        self.assertEqual(r["code"], "unavailable")
        calls = []

        async def install(tag, sha):
            calls.append((tag, sha))
            return {"ok": "framey"}

        self.host.loader_install = install
        self.assertEqual(await self.up.install_version("_loader", "v1.1.0", False), {"ok": "framey"})
        self.assertEqual(calls, [("v1.1.0", LOADER_NEW)])
        self.assertEqual((await self.up.install_version("_loader", "v1.2.0-rc.1", False))["code"], "prerelease")
        self.host.shas[("chaosfox26", "framey", "v0.9.0")] = SHA_D
        self.assertIn("confirm", await self.up.install_version("_loader", "v0.9.0", False))
        self.assertEqual(len(calls), 1)


class VersionListTests(Base):
    def setUp(self):
        super().setUp()
        self.host.add("demo", "1.2.0")
        self.host.api[releases_path()] = [rel_json("v1.3.0"), rel_json("v1.2.0"), rel_json("v1.1.0", body=""), rel_json("v1.4.0-rc.1", pre=True), rel_json("v9", draft=True), rel_json("odd name")]

    async def test_list_flags(self):
        r = await self.up.versions("demo")
        by = {v["tag"]: v for v in r["versions"]}
        self.assertEqual(set(by), {"v1.3.0", "v1.2.0", "v1.1.0", "v1.4.0-rc.1"})
        self.assertTrue(by["v1.2.0"]["current"] and not by["v1.2.0"]["eligible"])
        self.assertTrue(by["v1.1.0"]["downgrade"] and by["v1.1.0"]["eligible"])
        self.assertFalse(by["v1.3.0"]["downgrade"])
        self.assertTrue(by["v1.3.0"]["eligible"])
        self.assertFalse(by["v1.4.0-rc.1"]["eligible"])
        self.assertTrue(by["v1.4.0-rc.1"]["prerelease"])
        self.assertEqual(by["v1.3.0"]["version"], "1.3.0")
        self.assertEqual(by["v1.3.0"]["compat"]["badge"], "unknown")
        self.assertEqual(by["v1.2.0"]["compat"]["badge"], "ok")
        self.assertFalse(r["more"])

    async def test_prerelease_eligible_on_prerelease_channel(self):
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        by = {v["tag"]: v for v in (await self.up.versions("demo"))["versions"]}
        self.assertTrue(by["v1.4.0-rc.1"]["eligible"])

    async def test_paging_and_more(self):
        self.host.api[releases_path()] = [rel_json("v1.%d.0" % i) for i in range(10)]
        self.host.api[releases_path(2)] = [rel_json("v0.5.0")]
        r = await self.up.versions("demo", 1)
        self.assertTrue(r["more"])
        self.assertEqual(len(r["versions"]), 10)
        r2 = await self.up.versions("demo", 2)
        self.assertFalse(r2["more"])
        self.assertEqual(r2["versions"][0]["tag"], "v0.5.0")

    async def test_cache_five_minutes_in_memory(self):
        await self.up.versions("demo")
        await self.up.versions("demo")
        self.assertEqual(self.host.api_calls.count(releases_path()), 1)
        self.host.mono_clock += 299
        await self.up.versions("demo")
        self.assertEqual(self.host.api_calls.count(releases_path()), 1)
        self.host.mono_clock += 2
        await self.up.versions("demo")
        self.assertEqual(self.host.api_calls.count(releases_path()), 2)

    async def test_cache_is_bounded(self):
        for i in range(fu.CACHE_MAX + 5):
            self.up._cache[("o", "r", i + 50)] = (0, [], False)
        await self.up.versions("demo")
        self.assertLessEqual(len(self.up._cache), fu.CACHE_MAX)

    async def test_no_releases_falls_back_to_latest_commit(self):
        self.host.api[releases_path()] = []
        self.host.shas[("o", "r", "HEAD")] = SHA_B
        r = await self.up.versions("demo")
        self.assertEqual(len(r["versions"]), 1)
        self.assertEqual(r["versions"][0]["commit"], SHA_B)
        self.assertIn("unavailable", r["note"])
        self.assertFalse(r["more"])

    async def test_404_falls_back_too(self):
        del self.host.api[releases_path()]
        self.host.shas[("o", "r", "HEAD")] = SHA_A
        r = await self.up.versions("demo")
        self.assertTrue(r["versions"][0]["current"])

    async def test_other_http_error_is_reported(self):
        self.host.api[releases_path()] = http_error(429)
        r = await self.up.versions("demo")
        self.assertIn("rate limit", r["error"])

    async def test_privileged_entries_never_eligible(self):
        self.host.add("fan", "1.2.0", privileged={"installed": "/etc/x/.version", "helper": "/etc/x/h"})
        by = {v["tag"]: v for v in (await self.up.versions("fan"))["versions"]}
        self.assertFalse(by["v1.1.0"]["eligible"])
        self.assertEqual(by["v1.1.0"]["reason"], fu.FORWARD_ONLY)
        self.assertFalse(by["v1.3.0"]["eligible"])
        self.assertEqual(by["v1.3.0"]["reason"], fu.PROTECTED_UPDATE)

    async def test_no_source_and_unknown(self):
        self.host.add("lone", "1.0.0", source=None)
        self.assertEqual((await self.up.versions("lone"))["code"], "no_source")
        self.assertEqual((await self.up.versions("nope"))["code"], "unknown_plugin")
        self.assertEqual((await self.up.versions("../x"))["code"], "unknown_plugin")

    async def test_loader_history(self):
        self.host.api["chaosfox26/framey/releases?per_page=10&page=1"] = [rel_json("v1.1.0"), rel_json("v1.0.2"), rel_json("v1.0.0")]
        r = await self.up.versions("_loader")
        by = {v["tag"]: v for v in r["versions"]}
        self.assertTrue(by["v1.0.2"]["current"])
        self.assertTrue(by["v1.0.0"]["downgrade"])
        self.assertFalse(by["v1.1.0"]["downgrade"])

    async def test_testing_build_label(self):
        self.host.prefs(dev={"enabled": True}, channel="prerelease")
        by = {v["tag"]: v for v in (await self.up.versions("demo"))["versions"]}
        self.assertEqual(by["v1.4.0-rc.1"]["label"], "Testing build")
        self.assertEqual(by["v1.3.0"]["label"], "")


class NotesTests(Base):
    def setUp(self):
        super().setUp()
        self.host.add("demo", "1.2.0")

    async def test_notes_from_cache_after_list(self):
        self.host.api[releases_path()] = [rel_json("v1.3.0", body="Fixed things")]
        await self.up.versions("demo")
        n = len(self.host.api_calls)
        r = await self.up.notes("demo", "v1.3.0")
        self.assertEqual(r, {"notes": "Fixed things", "url": "https://github.com/o/r/releases/tag/v1.3.0"})
        self.assertEqual(len(self.host.api_calls), n)

    async def test_notes_fetched_by_tag(self):
        self.host.api["o/r/releases/tags/v1.3.0"] = rel_json("v1.3.0", body="Body")
        self.assertEqual((await self.up.notes("demo", "v1.3.0"))["notes"], "Body")

    async def test_empty_and_absent_notes(self):
        self.host.api["o/r/releases/tags/v1.3.0"] = rel_json("v1.3.0", body=None)
        self.assertEqual((await self.up.notes("demo", "v1.3.0"))["notes"], "")
        self.assertEqual(await self.up.notes("demo", "v7.7.7"), {"notes": "", "url": ""})

    async def test_oversized_and_control_characters(self):
        self.host.api["o/r/releases/tags/v1.3.0"] = rel_json("v1.3.0", body="x" * 20000)
        self.assertEqual(len((await self.up.notes("demo", "v1.3.0"))["notes"]), fu.NOTES_MAX)
        self.host.api["o/r/releases/tags/v1.4.0"] = rel_json("v1.4.0", body="a\x1b[31mb\r\nc\x00\td")
        self.assertEqual((await self.up.notes("demo", "v1.4.0"))["notes"], "a[31mb\nc\td")

    async def test_bad_tag_and_error(self):
        self.assertEqual((await self.up.notes("demo", "../x"))["code"], "bad_tag")
        self.assertEqual((await self.up.notes("demo", 5))["code"], "bad_tag")
        self.host.api["o/r/releases/tags/v2.0.0"] = http_error(500)
        self.assertIn("HTTP 500", (await self.up.notes("demo", "v2.0.0"))["error"])

    async def test_loader_notes(self):
        self.host.api["chaosfox26/framey/releases/tags/v1.1.0"] = rel_json("v1.1.0", body="Loader")
        self.assertEqual((await self.up.notes("_loader", "v1.1.0"))["notes"], "Loader")


class InstallBase(Base):
    def setUp(self):
        super().setUp()
        h = self.host
        h.add("demo", "1.2.0", sha=SHA_A)
        (h.HOME / ".config/framey/plugins/demo").mkdir(parents=True)
        (h.HOME / ".config/framey/plugins/demo/settings.json").write_text("{}")
        (h.HOME / ".local/share/framey/demo").mkdir(parents=True)
        h.archives[SHA_B] = plugin_zip("demo", "1.3.0")
        h.archives[SHA_C] = plugin_zip("demo", "1.1.0")
        h.archives[SHA_A] = plugin_zip("demo", "1.2.0")
        h.shas[("o", "r", "v1.3.0")] = SHA_B
        h.shas[("o", "r", "v1.1.0")] = SHA_C
        h.shas[("o", "r", "v1.2.0")] = SHA_A
        h.shas[("o", "r", "HEAD")] = SHA_B

    def plugin_version(self, pid="demo"):
        return json.loads((self.host.PLUGINS / pid / "plugin.json").read_text())["version"]

    def tree(self):
        return sorted(str(p.relative_to(self.host.PLUGINS)) for p in self.host.PLUGINS.rglob("*"))


class InstallVersionTests(InstallBase):
    async def test_upgrade_installs_and_keeps_one_previous(self):
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r, {"ok": "demo", "version": "1.3.0"})
        self.assertEqual(self.plugin_version(), "1.3.0")
        self.assertEqual((self.host.PLUGINS / "demo/.version").read_text(), SHA_B)
        self.assertEqual(json.loads((self.host.PLUGINS / ".old-demo/plugin.json").read_text())["version"], "1.2.0")
        self.assertEqual(self.host.reloads, ["demo"])
        ident = json.loads((self.host.PLUGINS / "demo/.identity.json").read_text())
        self.assertEqual(ident, {"kind": "release", "source": "o/r", "commit": SHA_B, "tag": "v1.3.0"})
        self.assertEqual((self.host.PLUGINS / "demo/.source").read_text(), "https://github.com/o/r")
        self.assertEqual(self.host.state["updates"]["plugins"]["demo"], {"latest": SHA_B[:7], "state": "current"})

    async def test_later_install_replaces_the_kept_previous(self):
        (self.host.PLUGINS / ".old-demo").mkdir()
        (self.host.PLUGINS / ".old-demo/plugin.json").write_text(json.dumps({"version": "1.0.0"}))
        await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(json.loads((self.host.PLUGINS / ".old-demo/plugin.json").read_text())["version"], "1.2.0")

    async def test_update_emits_the_batch_stages_and_keeps_previous(self):
        r = await self.up.update("demo")
        self.assertEqual(r, {"ok": "demo", "version": "1.3.0"})
        self.assertEqual(self.host.stages(1), [(s, st) for s in fu.STAGES for st in ("running", "ok")])
        self.assertEqual({e["id"] for e in self.host.events}, {"demo"})
        self.assertEqual(json.loads((self.host.PLUGINS / ".old-demo/plugin.json").read_text())["version"], "1.2.0")

    async def test_update_current_and_pending(self):
        self.host.shas[("o", "r", "HEAD")] = SHA_A
        self.assertEqual(await self.up.update("demo"), {"current": True})
        (self.host.PLUGINS / ".old-demo").mkdir()
        self.host.pending = lambda pid: True
        self.assertEqual((await self.up.update("demo"))["code"], "pending")

    async def test_downgrade_requires_confirm_and_changes_nothing(self):
        before = self.tree()
        r = await self.up.install_version("demo", "v1.1.0")
        self.assertTrue(r["downgrade"])
        self.assertIn("Going back to 1.1.0 removes nothing from your data", r["confirm"])
        self.assertIn("kept until you press Done", r["confirm"])
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.host.swaps, self.host.reloads), ([], []))
        self.assertEqual(self.plugin_version(), "1.2.0")

    async def test_confirmed_downgrade_keeps_previous_copy(self):
        r = await self.up.install_version("demo", "v1.1.0", True)
        self.assertEqual(r, {"ok": "demo", "version": "1.1.0", "kept_previous": True})
        self.assertEqual(self.plugin_version(), "1.1.0")
        old = self.host.PLUGINS / ".old-demo"
        self.assertEqual(json.loads((old / "plugin.json").read_text())["version"], "1.2.0")
        self.assertEqual(self.host.stages(1)[-2:], [("reload", "running"), ("reload", "ok")])

    async def test_confirm_must_be_true_bool(self):
        r = await self.host_call("ver_install", {"id": "demo", "tag": "v1.1.0", "confirm": "yes"})
        self.assertIn("confirm", r)

    async def host_call(self, name, arg):
        return await self.up.METHODS[name](self.host, arg)

    async def test_incompatible_rejected_before_any_change(self):
        self.host.archives[SHA_B] = plugin_zip("demo", "1.3.0", _blocked="needs Framey 9 or newer; you have 1.0.2")
        before = self.tree()
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["code"], "incompatible")
        self.assertIn("needs Framey 9", r["error"])
        self.assertEqual(r["stage"], "verify")
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.host.swaps, self.host.reloads), ([], []))

    async def test_incompatible_downgrade_is_not_even_offered_confirm(self):
        self.host.archives[SHA_C] = plugin_zip("demo", "1.1.0", _blocked="built for linux-x86_64")
        r = await self.up.install_version("demo", "v1.1.0")
        self.assertEqual(r["code"], "incompatible")
        self.assertNotIn("confirm", r)

    async def test_privileged_refused(self):
        self.host.add("fan", "1.2.0", privileged={"installed": "/etc/x/.version", "helper": "/etc/x/h"})
        self.host.shas[("o", "r", "v1.0.0")] = SHA_C
        r = await self.up.install_version("fan", "v1.0.0", True)
        self.assertEqual((r["code"], r["error"]), ("protected", fu.FORWARD_ONLY))
        r = await self.up.install_version("fan", "v1.5.0", True)
        self.assertEqual((r["code"], r["error"]), ("protected", fu.PROTECTED_UPDATE))
        self.assertEqual(self.host.swaps, [])
        self.assertEqual((self.host.PLUGINS / "fan/plugin.json").exists(), True)

    async def test_frozen_refused(self):
        self.host.freeze("demo", "1.2.0")
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["code"], "frozen")
        self.assertIn("unfreeze", r["error"])
        self.assertEqual(self.host.swaps, [])

    async def test_prerelease_needs_channel(self):
        self.host.shas[("o", "r", "v1.4.0-rc.1")] = SHA_D
        self.host.archives[SHA_D] = plugin_zip("demo", "1.4.0-rc.1")
        r = await self.up.install_version("demo", "v1.4.0-rc.1")
        self.assertEqual(r["code"], "prerelease")
        self.host.prefs(channel="prerelease")
        self.assertEqual((await self.up.install_version("demo", "v1.4.0-rc.1"))["code"], "prerelease")
        self.host.prefs(channel="prerelease", dev={"enabled": True})
        self.assertEqual((await self.up.install_version("demo", "v1.4.0-rc.1"))["version"], "1.4.0-rc.1")

    async def test_every_successful_path_pushes_badge_once(self):
        pushes = []

        async def after(badge):
            pushes.append(badge)

        self.up.after = after
        await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(len(pushes), 1)
        await self.up.repair("demo")
        self.assertEqual(len(pushes), 2)
        self.host.shas[("o", "r", "HEAD")] = SHA_C
        await self.up.update("demo")
        self.assertEqual(len(pushes), 3)

    async def test_same_commit_is_current(self):
        self.assertEqual(await self.up.install_version("demo", "v1.2.0"), {"current": True})
        self.assertEqual(self.host.swaps, [])

    async def test_commit_hash_tag_allowed(self):
        r = await self.up.install_version("demo", SHA_B)
        self.assertEqual(r["version"], "1.3.0")
        self.assertEqual(self.host.latest_calls, [])
        ident = json.loads((self.host.PLUGINS / "demo/.identity.json").read_text())
        self.assertEqual(ident["kind"], "commit")
        self.assertNotIn("tag", ident)

    async def test_bad_tags(self):
        for t in (None, 5, "", "../x", "a b", "x" * 65, "latest"):
            self.assertEqual((await self.up.install_version("demo", t))["code"], "bad_tag", t)
        self.assertEqual(self.host.swaps, [])

    async def test_wrong_plugin_package_rejected(self):
        self.host.archives[SHA_B] = plugin_zip("other", "1.3.0")
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["code"], "wrong_plugin")
        self.assertEqual(self.plugin_version(), "1.2.0")

    async def test_download_error_reports_check_or_download_stage(self):
        self.host.fail_archive.add(SHA_B)
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["stage"], "download")
        self.assertIn("HTTP 500", r["error"])
        self.assertEqual(self.host.stages(), [("check", "running"), ("check", "ok"), ("download", "running"), ("download", "fail")])

    async def test_tag_resolution_error_is_check_stage(self):
        self.host.shas[("o", "r", "v1.3.0")] = http_error(429)
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["stage"], "check")
        self.assertIn("rate limit", r["error"])
        self.assertEqual(self.host.stages(), [("check", "running"), ("check", "fail")])

    async def test_reload_failure_restores_previous_version(self):
        self.host.fail_reload.add("demo")
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["stage"], "reload")
        self.assertEqual(self.plugin_version(), "1.2.0")
        self.assertEqual(self.host.reloads, ["demo", "demo"])
        self.assertEqual(self.host.stages()[-1], ("reload", "fail"))

    async def test_swap_failure_leaves_current_untouched(self):
        self.host.fail_swap = True
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["stage"], "swap")
        self.assertEqual(self.plugin_version(), "1.2.0")
        self.assertEqual(self.host.reloads, [])
        self.assertEqual(self.host.stages()[-2:], [("swap", "running"), ("swap", "fail")])

    async def test_unpack_failure_stage(self):
        self.host.fail_unpack = True
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r["stage"], "unpack")
        self.assertEqual(self.host.stages()[-2:], [("unpack", "running"), ("unpack", "fail")])
        self.assertNotIn(("swap", "running"), self.host.stages())

    async def test_bad_zip_fails_verify_and_stops(self):
        self.host.archives[SHA_B] = b"not a zip"
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual((r["stage"], r["code"]), ("verify", "bad_package"))
        self.assertEqual([s for s, st in self.host.stages() if st == "running"], ["check", "download", "verify"])

    async def test_stage_order_and_indexes(self):
        await self.up.install_version("demo", "v1.3.0")
        order = self.host.stages()
        want = []
        for s in fu.STAGES:
            want += [(s, "running"), (s, "ok")]
        self.assertEqual(order, want)
        self.assertEqual([e["index"] for e in self.host.events if e["state"] == "ok"], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(e["total"] == 6 and e["id"] == "demo" and e["job"] == 1 for e in self.host.events))
        self.assertTrue(all(set(e) == {"job", "id", "stage", "index", "total", "label", "state", "measured", "error"} for e in self.host.events))
        self.assertEqual({e["label"] for e in self.host.events}, {"Checking", "Downloading", "Verifying", "Unpacking", "Installing", "Reloading"})

    async def test_measured_only_when_bytes_counted(self):
        self.host.archive_progress = False
        await self.up.install_version("demo", "v1.3.0")
        self.assertTrue(all(e["measured"] is None for e in self.host.events))

    async def test_measured_when_bytes_counted(self):
        await self.up.install_version("demo", "v1.3.0")
        dl = [e for e in self.host.events if e["stage"] == "download"]
        self.assertEqual(dl[0]["measured"], None)
        ok = dl[-1]
        self.assertEqual(ok["state"], "ok")
        size = len(self.host.archives[SHA_B])
        self.assertEqual(ok["measured"], {"done": size, "total": size})
        self.assertTrue(all(e["measured"] is None for e in self.host.events if e["stage"] != "download"))

    async def test_measured_none_without_known_total(self):
        def fetch(g, sha, job):
            job.progress(100, None)
            return self.host.archives[sha]

        self.up._archive = fetch
        await self.up.install_version("demo", "v1.3.0")
        self.assertTrue(all(e["measured"] is None for e in self.host.events))

    async def test_archive_without_progress_parameter(self):
        def archive(owner, repo, sha):
            return self.host.archives[sha]

        self.host.archive = archive
        self.assertEqual((await self.up.install_version("demo", "v1.3.0"))["version"], "1.3.0")
        self.assertTrue(all(e["measured"] is None for e in self.host.events))

    async def test_progress_callback_throttled(self):
        job = self.up._job("demo", fu.STAGES)
        for i in range(1, 101):
            await asyncio.to_thread(job.progress, i, 100)
        self.assertEqual(len(self.host.events), 1)
        self.host.mono_clock += 1
        await asyncio.to_thread(job.progress, 100, 100)
        self.assertEqual(len(self.host.events), 2)

    async def test_progress_failure_never_breaks_job(self):
        async def boom(job, ev):
            raise RuntimeError("page gone")

        self.host.progress = boom
        self.assertEqual((await self.up.install_version("demo", "v1.3.0"))["version"], "1.3.0")


class FreshInterpreterTests(unittest.TestCase):
    def test_urllib_submodules_import_alone(self):
        import subprocess

        repo = str(Path(__file__).resolve().parent.parent)
        code = (
            "import sys, types, pathlib, tempfile\n"
            "sys.path.insert(0, %r)\n"
            "import fy_updates as f\n"
            "d = pathlib.Path(tempfile.mkdtemp())\n"
            "h = types.SimpleNamespace(plugin_dir=lambda p: d, api_json=lambda path: {'tag_name': 'v1', 'assets': [{'name': 'a.bin', 'browser_download_url': 'https://github.com/o/r/releases/download/v1/a.bin'}]})\n"
            "print(f.Updates(h)._find_asset(('o', 'r'), {'id': 'x', 'version': '1'}, {'asset': 'a.bin'})[0])\n"
        ) % repo
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "https://github.com/o/r/releases/download/v1/a.bin")


class RepairTests(InstallBase):
    def config(self):
        return self.host.HOME / ".config/framey/plugins/demo/settings.json"

    def corrupt(self):
        (self.host.PLUGINS / "demo/main.js").write_text("broken")

    async def test_repair_from_version_file_keeps_dirs(self):
        self.corrupt()
        r = await self.up.repair("demo")
        self.assertEqual(r, {"ok": "demo", "version": "1.2.0"})
        self.assertEqual((self.host.PLUGINS / "demo/main.js").read_text(), "// 1.2.0")
        self.assertTrue(self.config().exists())
        self.assertTrue((self.host.HOME / ".local/share/framey/demo").is_dir())
        self.assertEqual(json.loads((self.host.PLUGINS / ".old-demo/plugin.json").read_text())["version"], "1.2.0")
        self.assertEqual(self.host.reloads, ["demo"])
        self.assertEqual(self.host.latest_calls, [])

    async def test_repair_of_same_version_keeps_older_rollback_copy(self):
        old = self.host.PLUGINS / ".old-demo"
        old.mkdir()
        (old / "plugin.json").write_text(json.dumps({"version": "1.1.0"}))
        self.corrupt()
        r = await self.up.repair("demo")
        self.assertEqual(r["ok"], "demo")
        self.assertEqual(json.loads((old / "plugin.json").read_text())["version"], "1.1.0")
        self.assertFalse((self.host.PLUGINS / ".hold-demo").exists())

    async def test_failed_repair_keeps_older_rollback_copy(self):
        old = self.host.PLUGINS / ".old-demo"
        old.mkdir()
        (old / "plugin.json").write_text(json.dumps({"version": "1.1.0"}))
        self.corrupt()
        self.host.fail_reload.add("demo")
        r = await self.up.repair("demo")
        self.assertEqual(r["stage"], "reload")
        self.assertEqual(json.loads((old / "plugin.json").read_text())["version"], "1.1.0")
        self.assertFalse((self.host.PLUGINS / ".hold-demo").exists())

    async def test_repair_uses_identity_commit(self):
        ident = {"kind": "release", "source": "o/r", "commit": SHA_C, "tag": "v1.1.0"}
        (self.host.PLUGINS / "demo/.identity.json").write_text(json.dumps(ident))
        r = await self.up.repair("demo")
        self.assertEqual(r["version"], "1.1.0")
        self.assertTrue(r["kept_previous"])
        self.assertEqual(json.loads((self.host.PLUGINS / "demo/.identity.json").read_text()), ident)

    async def test_repair_refuses_without_immutable_source(self):
        (self.host.PLUGINS / "demo/.version").unlink()
        r = await self.up.repair("demo")
        self.assertEqual(r["code"], "no_identity")
        self.assertIn("no immutable source", r["error"])
        self.assertEqual(self.host.swaps, [])

    async def test_repair_refuses_branch_url_local_identities(self):
        for kind in ("branch", "url", "local", "weird"):
            (self.host.PLUGINS / "demo/.identity.json").write_text(json.dumps({"kind": kind, "commit": SHA_A}))
            self.assertEqual((await self.up.repair("demo"))["code"], "no_identity", kind)
        (self.host.PLUGINS / "demo/.identity.json").write_text("{not json")
        self.assertEqual((await self.up.repair("demo"))["ok"], "demo")
        (self.host.PLUGINS / "demo/.identity.json").write_text(json.dumps({"kind": "commit", "commit": "zz"}))
        self.assertEqual((await self.up.repair("demo"))["code"], "no_identity")

    async def test_repair_refuses_privileged(self):
        self.host.add("fan", "1.0.0", privileged={"installed": "/etc/x/.version", "helper": "/etc/x/h"})
        r = await self.up.repair("fan")
        self.assertEqual(r["code"], "protected")
        self.assertEqual(self.host.swaps, [])

    async def test_repair_allowed_when_frozen(self):
        self.host.freeze("demo", "1.2.0")
        self.assertEqual((await self.up.repair("demo"))["ok"], "demo")

    async def test_failed_verify_rolls_nothing_forward(self):
        self.host.archives[SHA_A] = b"junk"
        self.corrupt()
        r = await self.up.repair("demo")
        self.assertEqual(r["stage"], "verify")
        self.assertEqual((self.host.PLUGINS / "demo/main.js").read_text(), "broken")
        self.assertEqual((self.host.swaps, self.host.reloads), ([], []))
        self.assertTrue(self.config().exists())

    async def test_failed_swap_keeps_installed_copy(self):
        self.host.fail_swap = True
        r = await self.up.repair("demo")
        self.assertEqual(r["stage"], "swap")
        self.assertEqual(self.plugin_version(), "1.2.0")
        self.assertFalse((self.host.PLUGINS / ".stage-demo").exists())

    async def test_failed_reload_restores_previous_copy(self):
        self.corrupt()
        self.host.fail_reload.add("demo")
        r = await self.up.repair("demo")
        self.assertEqual(r["stage"], "reload")
        self.assertEqual((self.host.PLUGINS / "demo/main.js").read_text(), "broken")
        self.assertTrue(self.config().exists())

    async def test_reset_needs_second_confirmation(self):
        r = await self.up.repair("demo", True)
        self.assertIn("confirm", r)
        self.assertTrue(self.config().exists())
        self.assertEqual(self.host.swaps, [])

    async def test_confirmed_reset_purges_only_config(self):
        r = await self.up.repair("demo", True, True)
        self.assertEqual(r["ok"], "demo")
        self.assertFalse(self.config().parent.exists())
        self.assertTrue((self.host.HOME / ".local/share/framey/demo").is_dir())

    async def test_reset_not_applied_when_repair_fails(self):
        self.host.archives[SHA_A] = b"junk"
        await self.up.repair("demo", True, True)
        self.assertTrue(self.config().exists())

    async def test_reset_refuses_symlinked_config_folder(self):
        target = Path(self.tmp) / "elsewhere"
        target.mkdir()
        (target / "keep").write_text("1")
        shutil.rmtree(self.config().parent)
        try:
            os.symlink(target, self.config().parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        r = await self.up.repair("demo", True, True)
        self.assertEqual(r["code"], "bad_link")
        self.assertTrue((target / "keep").exists())

    async def test_repair_through_methods_table(self):
        r = await self.up.METHODS["repair"](self.host, {"id": "demo", "reset": True})
        self.assertIn("confirm", r)
        r = await self.up.METHODS["repair"](self.host, {"id": "demo", "reset": True, "confirm": True})
        self.assertEqual(r["ok"], "demo")


class LinkedFolderTests(InstallBase):
    def setUp(self):
        super().setUp()
        h = self.host
        h.add("tool", "1.2.0", sha=SHA_A)
        self.work = Path(self.tmp) / "work"
        self.work.mkdir()
        shutil.move(str(h.PLUGINS / "tool"), str(self.work / "tool"))
        try:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(h.PLUGINS / "tool"), str(self.work / "tool")], check=True, capture_output=True)
            else:
                os.symlink(self.work / "tool", h.PLUGINS / "tool", target_is_directory=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("links unavailable here")

    def snapshot(self):
        return sorted((p.relative_to(self.work).as_posix(), p.read_bytes() if p.is_file() else None) for p in self.work.rglob("*"))

    async def test_version_install_refused_on_linked_folder(self):
        before = self.snapshot()
        r = await self.up.install_version("tool", "v1.3.0")
        self.assertEqual((r["code"], r["error"]), ("bad_link", fu.LINKED))
        self.assertEqual(self.host.swaps, [])
        self.assertEqual(self.snapshot(), before)

    async def test_repair_refused_on_linked_folder(self):
        (self.work / "tool/main.js").write_text("broken")
        before = self.snapshot()
        r = await self.up.repair("tool")
        self.assertEqual((r["code"], r["error"]), ("bad_link", fu.LINKED))
        self.assertEqual(self.host.swaps, [])
        self.assertEqual(self.snapshot(), before)

    async def test_single_update_refused_on_linked_folder(self):
        before = self.snapshot()
        r = await self.up.update("tool")
        self.assertEqual((r["code"], r["error"]), ("bad_link", fu.LINKED))
        self.assertEqual(self.snapshot(), before)

    async def test_companion_refused_on_linked_folder(self):
        r = await self.up.companion_fetch("tool", "helper")
        self.assertEqual((r["code"], r["error"]), ("bad_link", fu.LINKED))
        self.assertEqual(self.host.swaps, [])

    async def test_batch_skips_linked_folder(self):
        plan = await self.up.batch_plan(["tool"])
        self.assertEqual(plan["items"][0]["reason"], "linked folder")

    async def test_normal_plugin_still_installs_beside_a_linked_one(self):
        r = await self.up.install_version("demo", "v1.3.0")
        self.assertEqual(r, {"ok": "demo", "version": "1.3.0"})
        self.assertEqual(self.plugin_version("demo"), "1.3.0")


class BatchTests(InstallBase):
    def setUp(self):
        super().setUp()
        h = self.host
        h.add("fresh", "1.0.0", sha=SHA_A, source="https://github.com/o/fresh")
        h.add("same", "1.0.0", sha=SHA_B, source="https://github.com/o/same")
        h.add("lone", "1.0.0", sha=SHA_A, source=None)
        h.add("off", "1.0.0", sha=SHA_A, source="https://github.com/o/off")
        h.add("ice", "1.0.0", sha=SHA_A, source="https://github.com/o/ice")
        h.add("old", "1.0.0", sha=SHA_A, source="https://github.com/o/old", privileged={"installed": "/etc/x/.version", "helper": "/etc/x/h"})
        (h.PLUGINS / ".old-old").mkdir()
        h.add("bad", "1.0.0", sha=SHA_A, source="https://github.com/o/bad")
        h.state["disabled"] = ["off"]
        h.freeze("ice")
        remote = {"id": "fresh", "name": "Fresh", "version": "2.0.0", "short": "x"}
        h.shas[("o", "fresh", "HEAD")] = SHA_C
        h.shas[("o", "same", "HEAD")] = SHA_B
        h.shas[("o", "off", "HEAD")] = SHA_C
        h.shas[("o", "ice", "HEAD")] = SHA_C
        h.shas[("o", "old", "HEAD")] = SHA_C
        h.shas[("o", "bad", "HEAD")] = SHA_D
        h.api["o/fresh/contents/plugin.json?ref=" + SHA_C] = {"content": base64.b64encode(json.dumps(remote).encode()).decode(), "encoding": "base64"}
        self.rows = {}

    async def plan(self, ids=None):
        r = await self.up.batch_plan(ids)
        self.rows = {x["id"]: x for x in r["items"]}
        return r["items"]

    async def test_matrix_order_first_match_wins(self):
        await self.plan()
        self.assertEqual(self.rows["lone"]["reason"], "no source")
        self.assertEqual(self.rows["off"]["reason"], "disabled")
        self.assertEqual(self.rows["ice"]["reason"], "frozen")
        self.assertEqual(self.rows["old"]["reason"], "protected: updates from its own card")
        self.assertEqual(self.rows["same"]["reason"], "current")
        self.assertFalse(self.rows["same"]["eligible"])
        self.assertTrue(self.rows["fresh"]["eligible"])
        self.assertEqual(self.rows["fresh"]["reason"], "")
        self.assertEqual((self.rows["fresh"]["name"], self.rows["fresh"]["from"], self.rows["fresh"]["to"]), ("Fresh", "1.0.0", "2.0.0"))

    async def test_frozen_and_disabled_make_no_network_calls(self):
        await self.plan(["off", "ice", "lone", "old"])
        self.assertEqual(self.host.latest_calls, [])
        self.assertEqual(self.host.api_calls, [])

    async def test_incompatible_reported_from_target_manifest(self):
        remote = {"id": "fresh", "version": "2.0.0", "short": "x", "name": "Fresh", "_blocked": "needs Framey 1.5.0 or newer; you have 1.0.2"}
        self.host.api["o/fresh/contents/plugin.json?ref=" + SHA_C] = {"content": base64.b64encode(json.dumps(remote).encode()).decode()}
        await self.plan(["fresh"])
        r = self.rows["fresh"]
        self.assertFalse(r["eligible"])
        self.assertEqual(r["reason"], "incompatible: needs Framey 1.5.0 or newer; you have 1.0.2")

    async def test_target_without_manifest_falls_back_to_commit(self):
        del self.host.api["o/fresh/contents/plugin.json?ref=" + SHA_C]
        await self.plan(["fresh"])
        self.assertEqual(self.rows["fresh"]["to"], SHA_C[:7])
        self.assertTrue(self.rows["fresh"]["eligible"])

    async def test_lookup_error_marks_row_only(self):
        self.host.shas[("o", "fresh", "HEAD")] = http_error(429)
        items = await self.plan(["fresh", "same"])
        self.assertIn("rate limit", self.rows["fresh"]["reason"])
        self.assertEqual(self.rows["same"]["reason"], "current")
        self.assertEqual(len(items), 2)

    async def test_ids_validation_and_unknown(self):
        self.assertEqual((await self.up.batch_plan(["x"] * 33))["code"], "bad_batch")
        self.assertEqual((await self.up.batch_plan(["../x"]))["code"], "bad_batch")
        self.assertEqual((await self.up.batch_plan("fresh"))["code"], "bad_batch")
        await self.plan(["ghost", "fresh", "fresh"])
        self.assertEqual(self.rows["ghost"]["reason"], "not installed")

    async def test_protected_is_never_eligible_or_applied(self):
        self.host.state["disabled"] = []
        (self.host.PLUGINS / ".old-old").rmdir()
        self.host.releases[("o", "old")] = {"tag": "v2.0.0", "assets": {}, "notes": ""}
        await self.plan(["old"])
        self.assertFalse(self.rows["old"]["eligible"])
        self.assertEqual(self.rows["old"]["reason"], "protected: updates from its own card")
        res = await self.up.batch_apply(["old"])
        self.assertEqual((res["results"][0]["state"], res["results"][0]["reason"]), ("skipped", "protected: updates from its own card"))
        self.assertEqual(self.host.swaps, [])

    async def test_lookup_budget_is_bounded(self):
        for i in range(40):
            self.host.add("bulk%02d" % i, "1.0.0", sha=SHA_A, source="https://github.com/o/bulk%02d" % i)
            self.host.shas[("o", "bulk%02d" % i, "HEAD")] = SHA_B
        items = await self.plan()
        checked = [x for x in items if x["reason"] != "not checked: limit of 32 reached" and x["id"].startswith(("bulk", "fresh", "same", "bad"))]
        self.assertLessEqual(len(self.host.latest_calls), fu.BATCH_MAX)
        self.assertTrue(any(x["reason"] == "not checked: limit of 32 reached" for x in items))
        self.assertLessEqual(sum(x["eligible"] for x in items), fu.BATCH_MAX)
        self.assertTrue(checked)

    async def test_apply_mixed_results_do_not_hide_successes(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        self.host.archives[SHA_D] = b"junk"
        res = await self.up.batch_apply(["fresh", "same", "bad", "ice", "off", "lone", "old", "ghost"])
        rows = {r["id"]: r for r in res["results"]}
        self.assertEqual([r["id"] for r in res["results"]], ["fresh", "same", "bad", "ice", "off", "lone", "old", "ghost"])
        self.assertEqual(rows["fresh"]["state"], "ok")
        self.assertEqual((rows["fresh"]["from"], rows["fresh"]["to"], rows["fresh"]["name"]), ("1.0.0", "2.0.0", "Fresh"))
        self.assertEqual(rows["same"]["state"], "current")
        self.assertEqual(rows["bad"]["state"], "failed")
        self.assertEqual(rows["bad"]["error"], "not a zip")
        self.assertEqual([rows[x]["state"] for x in ("ice", "off", "lone", "old", "ghost")], ["skipped"] * 5)
        self.assertEqual([rows[x]["reason"] for x in ("ice", "off", "lone", "old", "ghost")], ["frozen", "disabled", "no source", "protected: updates from its own card", "not installed"])
        self.assertEqual((res["ok"], res["failed"]), (1, 1))
        self.assertEqual(json.loads((self.host.PLUGINS / "fresh/plugin.json").read_text())["version"], "2.0.0")
        self.assertEqual((self.host.PLUGINS / "bad/plugin.json").exists(), True)
        self.assertEqual(self.host.reloads, ["fresh"])

    async def test_failure_first_does_not_stop_the_rest(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        self.host.archives[SHA_D] = b"junk"
        res = await self.up.batch_apply(["bad", "fresh"])
        self.assertEqual([r["state"] for r in res["results"]], ["failed", "ok"])

    async def test_one_stage_stream_per_module(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        self.host.archives[SHA_D] = plugin_zip("bad", "1.1.0")
        await self.up.batch_apply(["fresh", "bad"])
        self.assertEqual({e["job"]: e["id"] for e in self.host.events}, {1: "fresh", 2: "bad"})
        self.assertEqual(self.host.stages(1), self.host.stages(2))

    async def test_exception_in_one_module_is_contained(self):
        self.host.shas[("o", "fresh", "HEAD")] = RuntimeError("boom")
        res = await self.up.batch_apply(["fresh", "same"])
        self.assertEqual([r["state"] for r in res["results"]], ["failed", "current"])
        self.assertEqual(res["results"][0]["error"], "boom")

    async def test_incompatible_at_apply_is_skipped_not_failed(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0", _blocked="built for linux-x86_64")
        res = await self.up.batch_apply(["fresh"])
        self.assertEqual(res["results"][0]["state"], "skipped")
        self.assertEqual((res["ok"], res["failed"]), (0, 0))
        self.assertEqual(self.host.swaps, [])

    async def test_apply_never_downgrades(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "0.9.0")
        res = await self.up.batch_apply(["fresh"])
        self.assertEqual(res["results"][0]["state"], "skipped")
        self.assertEqual(json.loads((self.host.PLUGINS / "fresh/plugin.json").read_text())["version"], "1.0.0")

    async def test_apply_validation(self):
        for bad in (None, [], "fresh", ["x"] * 33, ["../x"], [1]):
            self.assertEqual((await self.up.batch_apply(bad))["code"], "bad_batch", bad)

    async def test_apply_dedupes_ids(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        res = await self.up.batch_apply(["fresh", "fresh"])
        self.assertEqual(len(res["results"]), 1)

    async def test_batch_keeps_previous_copy_of_each_update(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        res = await self.up.batch_apply(["fresh"])
        self.assertEqual(res["results"][0]["state"], "ok")
        self.assertEqual(json.loads((self.host.PLUGINS / ".old-fresh/plugin.json").read_text())["version"], "1.0.0")

    async def test_batch_pushes_badge_once(self):
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        pushes = []

        async def after(badge):
            pushes.append(badge)

        self.up.after = after
        await self.up.batch_apply(["fresh", "same"])
        self.assertEqual(len(pushes), 1)



    async def test_pending_uses_host_hook_when_present(self):
        self.host.pending = lambda pid: pid == "same"
        await self.plan(["same", "old"])
        self.assertEqual(self.rows["same"]["reason"], "pending update")
        self.assertNotEqual(self.rows["old"]["reason"], "pending update")

    async def test_batch_updates_badge_state(self):
        self.host.state["updates"] = {"plugins": {"fresh": {"latest": SHA_C[:7], "state": "update"}}}
        self.host.archives[SHA_C] = plugin_zip("fresh", "2.0.0")
        self.assertEqual(self.up.badge()["n"], 1)
        await self.up.batch_apply(["fresh"])
        self.assertEqual(self.up.badge()["n"], 0)


class BadgeTests(Base):
    async def test_counts(self):
        h = self.host
        for p in ("a", "b", "c", "d", "e"):
            h.add(p, "1.0.0")
        h.state["updates"] = {"plugins": {"a": {"state": "update", "latest": "x"}, "b": {"state": "update", "latest": "x"}, "c": {"state": "update", "latest": "x"}, "d": {"state": "current", "latest": "x"}, "gone": {"state": "update", "latest": "x"}}, "loader": {"tag": "v2", "state": "update"}}
        h.state["disabled"] = ["b"]
        h.freeze("c")
        self.assertEqual(self.up.badge(), {"n": 2, "loader": True})
        h.state["updates"]["loader"]["state"] = "current"
        self.assertEqual(self.up.badge(), {"n": 1, "loader": False})
        self.assertEqual(self.up.badge(h.settings()), {"n": 1, "loader": False})
        self.assertEqual(self.up.badge({}), {"n": 0, "loader": False})
        self.assertEqual(await self.up.METHODS["badge"](h, None), {"n": 1, "loader": False})

    async def test_garbage_state(self):
        self.host.state["updates"] = "junk"
        self.assertEqual(self.up.badge(), {"n": 0, "loader": False})
        self.host.state["updates"] = {"plugins": [1], "loader": 5, "seen": []}
        self.assertEqual(self.up.badge(), {"n": 0, "loader": False})


class AutoCheckTests(Base):
    def setUp(self):
        super().setUp()
        h = self.host
        h.api["chaosfox26/framey/releases/latest"] = rel_json("v1.1.0")
        h.shas[("chaosfox26", "framey", "v1.1.0")] = LOADER_NEW
        h.add("one", "1.0.0", sha=SHA_A, source="https://github.com/o/one")
        h.add("two", "1.0.0", sha=SHA_A, source="https://github.com/o/two")
        h.shas[("o", "one", "HEAD")] = SHA_B
        h.shas[("o", "two", "HEAD")] = SHA_A

    def on(self, **kw):
        self.host.prefs(autocheck=True, **kw)

    async def test_off_means_no_timer_and_no_checks(self):
        self.host.timer_loop = TimerLoop()
        self.up.arm_autocheck()
        self.assertEqual(self.host.timer_loop.timers, [])
        self.assertIsNone(self.up._timer)
        await self.up.on_panel_open()
        await self.up._cycle(fu.FLOOR)
        self.assertEqual((self.host.api_calls, self.host.latest_calls), ([], []))
        self.assertFalse(self.up.due(self.host.clock + 10 ** 9))
        self.assertEqual(self.host.state["updates"], {})

    async def test_arm_schedules_single_jittered_one_shot(self):
        self.on()
        self.host.timer_loop = TimerLoop()
        for r, want in ((0.0, 90), (0.5, 240), (0.999999, 90 + 0.999999 * 300)):
            self.up.disarm()
            self.host.timer_loop.timers.clear()
            self.host.random = r
            self.up.arm_autocheck()
            self.up.arm_autocheck()
            self.assertEqual(len(self.host.timer_loop.timers), 1)
            self.assertAlmostEqual(self.host.timer_loop.timers[0].delay, want)
            self.assertTrue(90 <= self.host.timer_loop.timers[0].delay < 390)

    async def test_disarm_cancels(self):
        self.on()
        self.host.timer_loop = TimerLoop()
        self.up.arm_autocheck()
        t = self.host.timer_loop.timers[0]
        self.up.disarm()
        self.assertTrue(t.cancelled)
        self.assertIsNone(self.up._timer)
        self.up.disarm()

    async def test_timer_fires_one_check_and_never_installs(self):
        self.on()
        self.host.timer_loop = TimerLoop()
        self.up.arm_autocheck()
        self.host.timer_loop.timers[0].cb()
        await self.up._task
        u = self.host.state["updates"]
        self.assertEqual(u["last"], self.host.clock)
        self.assertEqual(u["plugins"]["one"], {"latest": SHA_B[:7], "state": "update"})
        self.assertEqual(u["plugins"]["two"]["state"], "current")
        self.assertEqual(u["loader"], {"tag": "v1.1.0", "state": "update"})
        self.assertEqual((u["fails"], u["next_ok"]), (0, 0))
        self.assertEqual((self.host.swaps, self.host.reloads), ([], []))
        self.assertEqual(len(self.host.timer_loop.timers), 2)
        self.assertEqual(self.host.timer_loop.timers[1].delay, fu.DAY)
        self.assertIs(self.up._timer, self.host.timer_loop.timers[1])
        self.up.disarm()

    async def test_due_with_fake_clock(self):
        self.on()
        now = 5_000_000.0
        self.host.state["updates"] = {"last": now - fu.DAY + 1}
        self.assertFalse(self.up.due(now))
        self.assertTrue(self.up.due(now + 1))
        self.assertTrue(self.up.due(now, fu.FLOOR))
        self.host.state["updates"] = {"last": now - fu.FLOOR + 1}
        self.assertFalse(self.up.due(now, fu.FLOOR))
        self.assertTrue(self.up.due(now + 1, fu.FLOOR))
        self.assertFalse(self.up.due(now + 1))
        self.host.state["updates"] = {"last": 0}
        self.assertTrue(self.up.due(now))
        self.host.state["updates"] = {"last": 0, "next_ok": now + 5}
        self.assertFalse(self.up.due(now))
        self.assertTrue(self.up.due(now + 5))

    async def test_six_hour_floor_on_restart_arm(self):
        self.on()
        self.host.state["updates"] = {"last": self.host.clock - fu.FLOOR + 60}
        self.host.timer_loop = TimerLoop()
        self.up.arm_autocheck()
        self.host.timer_loop.timers[0].cb()
        await self.up._task
        self.assertEqual((self.host.api_calls, self.host.latest_calls), ([], []))
        self.host.clock += 61
        self.up.arm_autocheck()
        self.host.timer_loop.timers[-1].cb()
        await self.up._task
        self.assertTrue(self.host.latest_calls)

    async def test_panel_open_uses_24h(self):
        self.on()
        self.host.state["updates"] = {"last": self.host.clock - fu.DAY + 60}
        await self.up.on_panel_open()
        self.assertEqual(self.host.latest_calls, [])
        self.host.clock += 61
        await self.up.on_panel_open()
        self.assertTrue(self.host.latest_calls)
        n = len(self.host.latest_calls)
        await self.up.on_panel_open()
        self.assertEqual(len(self.host.latest_calls), n)

    async def test_failure_backoff_doubles_to_six_hours(self):
        self.on()
        self.host.api["chaosfox26/framey/releases/latest"] = http_error(500)
        self.host.shas[("o", "one", "HEAD")] = http_error(500)
        self.host.shas[("o", "two", "HEAD")] = http_error(500)
        gaps = []
        for i in range(7):
            u0 = self.host.state["updates"].get("fails", 0)
            now = self.host.clock
            await self.up._cycle(fu.FLOOR)
            u = self.host.state["updates"]
            self.assertEqual(u["fails"], u0 + 1)
            self.assertEqual(u["last"] if "last" in u else 0, 0)
            gaps.append(u["next_ok"] - now)
            self.assertFalse(self.up.due(self.host.clock))
            self.assertFalse(self.up.due(self.host.clock, fu.FLOOR))
            self.host.clock = u["next_ok"]
        self.assertEqual(gaps, [900, 1800, 3600, 7200, 14400, 21600, 21600])

    async def test_success_resets_backoff(self):
        self.on()
        self.host.state["updates"] = {"fails": 3, "next_ok": 0, "last": 0}
        await self.up._cycle(fu.FLOOR)
        u = self.host.state["updates"]
        self.assertEqual((u["fails"], u["next_ok"], u["last"]), (0, 0, self.host.clock))

    async def test_rate_limit_backs_off_six_hours_and_stops(self):
        self.on()
        self.host.shas[("o", "one", "HEAD")] = http_error(429)
        await self.up._cycle(fu.FLOOR)
        u = self.host.state["updates"]
        self.assertEqual(u["next_ok"] - self.host.clock, fu.FLOOR)
        self.assertEqual(u["fails"], 1)
        self.assertEqual(u["last"], self.host.clock)
        self.assertNotIn(("o", "two", "HEAD"), self.host.latest_calls)

    async def test_rate_limit_on_loader_stops_everything(self):
        self.on()
        self.host.api["chaosfox26/framey/releases/latest"] = http_error(403, "0")
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(self.host.latest_calls, [])
        u = self.host.state["updates"]
        self.assertEqual((u["fails"], u["next_ok"] - self.host.clock), (1, fu.FLOOR))
        self.assertEqual(u.get("last", 0), 0)

    async def test_partial_failure_keeps_old_record(self):
        self.on()
        self.host.state["updates"] = {"plugins": {"one": {"latest": "old1234", "state": "update"}}}
        self.host.shas[("o", "one", "HEAD")] = http_error(500)
        await self.up._cycle(fu.FLOOR)
        u = self.host.state["updates"]
        self.assertEqual(u["plugins"]["one"], {"latest": "old1234", "state": "update"})
        self.assertEqual(u["plugins"]["two"]["state"], "current")
        self.assertEqual(u["fails"], 0)

    async def test_frozen_and_disabled_skipped(self):
        self.on()
        self.host.state["disabled"] = ["one"]
        self.host.freeze("two")
        await self.up._cycle(fu.FLOOR)
        self.assertEqual([c for c in self.host.latest_calls if c[0] == "o"], [])

    async def test_at_most_twenty_plugins_per_cycle_unseen_first(self):
        self.on()
        for i in range(25):
            self.host.add("m%02d" % i, "1.0.0", sha=SHA_A, source="https://github.com/o/m%02d" % i)
            self.host.shas[("o", "m%02d" % i, "HEAD")] = SHA_A
        self.host.state["updates"] = {"plugins": {"one": {"latest": "x", "state": "current"}, "two": {"latest": "x", "state": "current"}}}
        await self.up._cycle(fu.FLOOR)
        calls = [c for c in self.host.latest_calls if c[0] == "o"]
        self.assertEqual(len(calls), fu.CYCLE_MAX)
        self.assertEqual(calls[0][1], "m00")
        self.assertNotIn(("o", "one", "HEAD"), calls)
        await self.up._cycle(fu.DAY)

    async def test_spacing_between_requests(self):
        self.on()
        self.up.SPACING = 2.0
        waits = []
        real = fu.asyncio.sleep

        async def sleep(d):
            waits.append(d)

        fu.asyncio.sleep = sleep
        try:
            await self.up._cycle(fu.FLOOR)
        finally:
            fu.asyncio.sleep = real
        self.assertEqual(waits, [2.0])

    async def test_no_overlapping_cycles(self):
        self.on()
        gate = asyncio.Event()
        orig = self.up._check_all

        async def slow(now):
            await gate.wait()

        self.up._check_all = slow
        t = asyncio.ensure_future(self.up._cycle(fu.FLOOR))
        await asyncio.sleep(0)
        self.assertTrue(self.up._running)
        await self.up._cycle(fu.FLOOR)
        gate.set()
        await t
        self.assertFalse(self.up._running)

    async def test_status(self):
        self.on()
        self.host.state["updates"] = {"last": 12, "next_ok": 34, "fails": 2}
        self.assertEqual(self.up.autocheck_status(), {"enabled": True, "last": 12, "next_ok": 34, "fails": 2})
        self.host.prefs(autocheck=False)
        self.assertFalse((await self.up.METHODS["autocheck_status"](self.host, {}))["enabled"])

    async def test_pruned_removed_plugins_and_badge_push(self):
        self.on()
        pushed = []

        async def after(b):
            pushed.append(b)

        self.up.after = after
        self.host.state["updates"] = {"plugins": {"ghost": {"latest": "x", "state": "update"}}}
        await self.up._cycle(fu.FLOOR)
        self.assertNotIn("ghost", self.host.state["updates"]["plugins"])
        self.assertEqual(pushed, [{"n": 2, "loader": True}])

    async def test_after_hook_error_is_contained(self):
        self.on()

        async def after(b):
            raise RuntimeError("bar gone")

        self.up.after = after
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(self.host.state["updates"]["last"], self.host.clock)

    async def test_other_settings_keys_survive(self):
        self.on()
        self.host.state["extra"] = {"keep": 1}
        self.host.state["modules"]["order"] = ["two", "one"]
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(self.host.state["extra"], {"keep": 1})
        self.assertEqual(self.host.state["modules"]["order"], ["two", "one"])


class NotificationTests(Base):
    def setUp(self):
        super().setUp()
        h = self.host
        h.api["chaosfox26/framey/releases/latest"] = rel_json("v1.1.0")
        h.shas[("chaosfox26", "framey", "v1.1.0")] = LOADER_NEW
        h.add("one", "1.0.0", sha=SHA_A, source="https://github.com/o/one")
        h.add("two", "1.0.0", sha=SHA_A, source="https://github.com/o/two")
        h.shas[("o", "one", "HEAD")] = SHA_B
        h.shas[("o", "two", "HEAD")] = SHA_C
        h.prefs(autocheck=True)

    async def test_loader_notice_once_per_candidate(self):
        self.host.prefs(notify_loader=True)
        await self.up.loader_check()
        self.assertEqual(len(self.host.notices), 1)
        plugin, name, p = self.host.notices[0]
        self.assertEqual((plugin, name), ("_core", "notice"))
        self.assertEqual(p["actions"], [{"id": "open:settings", "label": "Open Settings"}])
        self.assertIn("v1.1.0", p["body"])
        self.assertLessEqual(len(p["title"]), 80)
        self.assertLessEqual(len(p["body"]), 400)
        await self.up.loader_check()
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(len(self.host.notices), 1)
        self.host.api["chaosfox26/framey/releases/latest"] = rel_json("v1.2.0")
        self.host.shas[("chaosfox26", "framey", "v1.2.0")] = SHA_D
        await self.up.loader_check()
        self.assertEqual(len(self.host.notices), 2)
        self.assertEqual(self.host.state["updates"]["seen"]["loader"], "v1.2.0")

    async def test_pref_off_no_notice_and_not_consumed(self):
        await self.up.loader_check()
        self.assertEqual(self.host.notices, [])
        self.assertEqual(self.host.state["updates"]["seen"]["loader"], "")
        self.host.prefs(notify_loader=True)
        await self.up.loader_check()
        self.assertEqual(len(self.host.notices), 1)

    async def test_current_loader_never_notifies(self):
        self.host.prefs(notify_loader=True)
        self.host.shas[("chaosfox26", "framey", "v1.1.0")] = LOADER_SHA
        await self.up.loader_check()
        self.assertEqual(self.host.notices, [])

    async def test_plugin_notice_once_per_candidate(self):
        self.host.prefs(notify_plugins=True)
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(len(self.host.notices), 1)
        p = self.host.notices[0][2]
        self.assertEqual(p["actions"], [{"id": "open:modules", "label": "Open Modules"}])
        self.assertIn("One", p["body"])
        self.assertIn("Two", p["body"])
        self.assertIn("(2)", p["title"])
        self.host.clock += fu.DAY + 1
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(len(self.host.notices), 1)
        self.host.shas[("o", "one", "HEAD")] = SHA_D
        self.host.clock += fu.DAY + 1
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(len(self.host.notices), 2)
        self.assertIn("One", self.host.notices[1][2]["body"])
        self.assertNotIn("Two", self.host.notices[1][2]["body"])

    async def test_loader_and_plugin_prefs_are_independent(self):
        self.host.prefs(notify_loader=True)
        await self.up._cycle(fu.FLOOR)
        self.assertEqual([n[2]["actions"][0]["id"] for n in self.host.notices], ["open:settings"])

    async def test_frozen_disabled_never_notify(self):
        self.host.prefs(notify_plugins=True)
        await self.up._cycle(fu.FLOOR)
        self.host.notices.clear()
        self.host.state["updates"]["seen"] = {"loader": "", "plugins": {}}
        self.host.freeze("one")
        self.host.state["disabled"] = ["two"]
        await self.up.record_checked({"one": {"state": "update", "latest": "zzz1111"}, "two": {"state": "update", "latest": "zzz2222"}})
        self.assertEqual(self.host.notices, [])

    async def test_detached_page_drops_notice_without_consuming_it(self):
        self.host.prefs(notify_plugins=True, notify_loader=True)
        self.host.attached_main = False
        await self.up._cycle(fu.FLOOR)
        self.assertEqual(self.host.notices, [])
        self.assertEqual(self.up.badge()["n"], 3)
        self.host.attached_main = True
        await self.up.record_checked({})
        self.assertEqual(len(self.host.notices), 2)

    async def test_notice_delivery_failure_does_not_consume(self):
        self.host.prefs(notify_loader=True)

        async def boom(*a):
            raise RuntimeError("no page")

        real = self.host.notify_page
        self.host.notify_page = boom
        await self.up.loader_check()
        self.assertEqual(self.host.state["updates"]["seen"]["loader"], "")
        self.host.notify_page = real
        await self.up.loader_check()
        self.assertEqual(len(self.host.notices), 1)

    async def test_many_names_are_summarised(self):
        self.host.prefs(notify_plugins=True)
        for i in range(8):
            self.host.add("p%d" % i, "1.0.0")
        recs = {"p%d" % i: {"state": "update", "latest": "abcdef%d" % i} for i in range(8)}
        await self.up.record_checked(recs)
        body = self.host.notices[0][2]["body"]
        self.assertIn("and more", body)
        self.assertLessEqual(len(body), 400)

    async def test_manual_record_checked_ignores_garbage(self):
        await self.up.record_checked({"one": {"state": "boom"}, "../x": {"state": "update", "latest": "a"}, "two": "no", "ok": None})
        self.assertEqual(self.host.state["updates"]["plugins"], {})


class CompanionTests(Base):
    def setUp(self):
        super().setUp()
        h = self.host
        self.data = b"archive-bytes"
        self.digest = "sha256:" + hashlib.sha256(self.data).hexdigest()
        self.spec = {"name": "tool", "version": "1.2.0", "platform": "linux-aarch64", "asset": "tool.tar.gz", "sha256": hashlib.sha256(self.data).hexdigest(), "size": len(self.data), "exec": ["tool"]}
        h.add("demo", "1.2.0", companions=[self.spec])
        self.url = "https://github.com/o/r/releases/download/v1.2.0/tool.tar.gz"
        h.assets = {self.url: self.data}
        h.api["o/r/releases/tags/v1.2.0"] = rel_json("v1.2.0", assets=[{"name": "tool.tar.gz", "browser_download_url": self.url, "digest": self.digest}])
        self.installed = []

        def companions(m):
            return [self.spec] if m["id"] == "demo" else []

        def install(data, spec, dest):
            self.installed.append((data, spec, dest))
            return {"files": ["tool"], "exec": ["tool"]}

        h.pkg = types.SimpleNamespace(companions=companions, install_companion=install)

    async def test_verified_download_and_install(self):
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual(r["ok"], "tool")
        self.assertEqual(r["digest"], "verified")
        self.assertIn("not a signature", r["note"])
        dest = self.host.HOME / ".local/share/framey/demo/bin"
        self.assertEqual(Path(r["path"]), dest / "tool-1.2.0" / "tool")
        self.assertEqual(self.installed[0][2], dest)
        self.assertEqual(self.installed[0][0], self.data)
        self.assertEqual(self.host.fetched[2], fu.HOSTS)
        self.assertEqual(self.host.fetched[3], fu.COMPANION_MAX)
        self.assertEqual(self.host.stages(), [(s, st) for s in fu.COMPANION_STAGES for st in ("running", "ok")])
        self.assertEqual({e["total"] for e in self.host.events}, {4})

    async def test_measured_bytes_on_download(self):
        await self.up.companion_fetch("demo", "tool")
        dl = [e for e in self.host.events if e["stage"] == "download" and e["state"] == "ok"][0]
        self.assertEqual(dl["measured"], {"done": len(self.data), "total": len(self.data)})

    async def test_digest_mismatch_stops_before_extraction(self):
        self.host.assets[self.url] = b"tampered"
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual((r["code"], r["stage"]), ("digest_mismatch", "verify"))
        self.assertEqual(self.installed, [])
        self.assertNotIn(("unpack", "running"), self.host.stages())

    async def test_absent_digest_is_reported(self):
        self.host.api["o/r/releases/tags/v1.2.0"] = rel_json("v1.2.0", assets=[{"name": "tool.tar.gz", "browser_download_url": self.url}])
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual(r["digest"], "no digest published")
        self.assertEqual(len(self.installed), 1)

    async def test_published_digest_must_agree_with_manifest(self):
        other = b"other"
        self.host.api["o/r/releases/tags/v1.2.0"] = rel_json("v1.2.0", assets=[{"name": "tool.tar.gz", "browser_download_url": self.url, "digest": "sha256:" + hashlib.sha256(self.data).hexdigest()}])
        self.spec["sha256"] = hashlib.sha256(other).hexdigest()
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual(r["code"], "digest_mismatch")
        self.assertEqual(self.installed, [])

    async def test_asset_outside_own_repository_refused(self):
        for bad in ("https://github.com/evil/r/releases/download/v1.2.0/tool.tar.gz", "http://github.com/o/r/releases/download/v1.2.0/tool.tar.gz", "https://evil.example/o/r/releases/download/v1.2.0/tool.tar.gz", "https://github.com/o/r/archive/x.zip"):
            self.host.api["o/r/releases/tags/v1.2.0"] = rel_json("v1.2.0", assets=[{"name": "tool.tar.gz", "browser_download_url": bad}])
            r = await self.up.companion_fetch("demo", "tool")
            self.assertEqual(r["code"], "bad_asset", bad)
        self.assertEqual(self.installed, [])

    async def test_missing_release_or_asset(self):
        del self.host.api["o/r/releases/tags/v1.2.0"]
        self.assertEqual((await self.up.companion_fetch("demo", "tool"))["code"], "no_asset")
        self.host.api["o/r/releases/tags/v1.2.0"] = rel_json("v1.2.0", assets=[])
        self.assertEqual((await self.up.companion_fetch("demo", "tool"))["code"], "no_asset")

    async def test_tag_prefix_fallback(self):
        self.host.api["o/r/releases/tags/1.2.0"] = self.host.api.pop("o/r/releases/tags/v1.2.0")
        self.assertEqual((await self.up.companion_fetch("demo", "tool"))["ok"], "tool")

    async def test_unknown_companion_and_module(self):
        self.assertEqual((await self.up.companion_fetch("demo", "nope"))["code"], "unknown_companion")
        self.assertEqual((await self.up.companion_fetch("demo", None))["code"], "unknown_companion")
        self.assertEqual((await self.up.companion_fetch("ghost", "tool"))["code"], "unknown_plugin")

    async def test_oversized_download_refused(self):
        self.host.assets[self.url] = b"x" * (fu.COMPANION_MAX + 1)
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual(r["code"], "too_large")
        self.assertEqual(self.installed, [])

    async def test_install_error_reported_at_unpack(self):
        def install(data, spec, dest):
            raise ValueError("archive has unsafe entries")

        self.host.pkg.install_companion = install
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual((r["stage"], r["error"]), ("unpack", "archive has unsafe entries"))

    async def test_package_unavailable(self):
        self.host.pkg = None
        self.assertEqual((await self.up.companion_fetch("demo", "tool"))["code"], "unavailable")

    async def test_dir_path_when_no_exec(self):
        self.host.pkg.install_companion = lambda d, s, p: {"files": ["data.bin"], "exec": []}
        r = await self.up.companion_fetch("demo", "tool")
        self.assertEqual(Path(r["path"]), self.host.HOME / ".local/share/framey/demo/bin/tool-1.2.0")

    async def test_via_methods_table(self):
        r = await self.up.METHODS["companion_fetch"](self.host, {"id": "demo", "name": "tool"})
        self.assertEqual(r["ok"], "tool")


class MethodTableTests(Base):
    async def test_names_and_shapes(self):
        want = {"ver_list", "ver_install", "repair", "notes", "batch_plan", "batch_apply", "loader_check", "badge", "autocheck_status", "companion_fetch"}
        self.assertEqual(set(self.up.METHODS), want)
        for fn in self.up.METHODS.values():
            self.assertTrue(asyncio.iscoroutinefunction(fn))

    async def test_garbage_arguments_never_raise(self):
        for name, fn in self.up.METHODS.items():
            for arg in (None, 5, "x", [], {}, {"id": 5}, {"id": "../x", "tag": ["a"], "ids": "no"}):
                r = await fn(self.host, arg)
                self.assertIsInstance(r, dict, name)

    async def test_error_shape(self):
        r = await self.up.METHODS["ver_list"](self.host, {"id": "nope"})
        self.assertEqual(set(r), {"error", "code"})
        self.assertLessEqual(len(r["error"]), 300)

    async def test_ver_list_and_notes_through_table(self):
        self.host.add("demo", "1.2.0")
        self.host.api[releases_path()] = [rel_json("v1.3.0")]
        r = await self.up.METHODS["ver_list"](self.host, {"id": "demo", "page": 1})
        self.assertEqual(r["versions"][0]["tag"], "v1.3.0")
        r = await self.up.METHODS["notes"](self.host, {"id": "demo", "tag": "v1.3.0"})
        self.assertEqual(r["notes"], "notes")

    async def test_no_comments_or_docstrings_in_module(self):
        import ast
        import tokenize

        path = Path(fu.__file__)
        with tokenize.open(path) as f:
            self.assertFalse([t for t in tokenize.generate_tokens(f.readline) if t.type == tokenize.COMMENT])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(ast.get_docstring(node))

    async def test_module_has_no_import_time_side_effects_or_foreign_imports(self):
        import ast

        tree = ast.parse(Path(fu.__file__).read_text(encoding="utf-8"))
        top = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)}
        self.assertFalse(top & {"framey", "fy_pkg", "fy_state", "fy_net", "requests", "aiohttp"})

    async def test_no_polling_loops(self):
        text = Path(fu.__file__).read_text(encoding="utf-8")
        self.assertNotIn("while True", text)
        self.assertNotIn("call_repeating", text)


if __name__ == "__main__":
    unittest.main()
