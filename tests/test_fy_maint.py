import ast
import asyncio
import base64
import email.message
import inspect
import io
import json
import os
import sys
import tempfile
import threading
import time
import tokenize
import types
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fy_maint as fm

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maint"
NOW = fm.parse_stamp("2026-10-09T00:00:00Z")
DAY = 86400
A, B, C = "a" * 40, "b" * 40, "c" * 40
SRC = "https://github.com/acme/widget"


def http_error(code, **headers):
    h = email.message.Message()
    body = headers.pop("body", "")
    for k, v in headers.items():
        h[k.replace("_", "-")] = str(v)
    return urllib.error.HTTPError("https://api.github.com/x", code, "x", h, io.BytesIO(body.encode()))


def iso(days_ago, now=NOW):
    return fm.fmt(now - int(days_ago * DAY))


def commit(sha, when, author=None):
    return {"sha": sha, "commit": {"committer": {"date": when}, "author": {"date": author or when}}}


def api_for(full="acme/widget", archived=False, fork=False, parent=None, commit_at="2026-09-01T00:00:00Z", release_at=None, plugin_text=None, sha=A):
    d = {"repos/" + full: {"full_name": full, "archived": archived, "fork": fork, "default_branch": "main", **({"parent": {"full_name": parent}} if parent else {})}}
    d["repos/%s/commits" % full] = [commit(sha, commit_at)] if commit_at else []
    d["repos/%s/releases/latest" % full] = {"tag_name": "v1.0.0", "published_at": release_at, "draft": False} if release_at else 404
    d["repos/%s/contents/plugin.json" % full] = {"text": plugin_text} if plugin_text is not None else 404
    return d


def decl_text(**m):
    return json.dumps({"id": "demo", "maintenance": m})


class FakeHost:
    def __init__(self, tmp, api=None, now=NOW):
        self.root = Path(tmp)
        self.CONF = self.root / "cfg" / "settings.json"
        self.api = dict(api or {})
        self.t, self.m, self.tick, self.delay = now, 1000.0, 0.0, 0.0
        self.calls, self.logs, self.plugins = [], [], {}
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.headers = []
        self.rnd = 0.5

    def now(self):
        return self.t

    def mono(self):
        return self.m

    def rand(self):
        return self.rnd

    def log(self, *a):
        self.logs.append(a)

    def plugin_dir(self, pid):
        return self.root / "plugins" / pid

    def manifest(self, d):
        return self.plugins.get(d.name)

    def manifests(self):
        return list(self.plugins.values())

    def add(self, pid, source=None, dot_source=None, **extra):
        m = {"id": pid, "name": pid.title(), "version": "1.0.0", **extra}
        if source is not None:
            m["source"] = source
        self.plugins[pid] = m
        d = self.plugin_dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        if dot_source is not None:
            (d / ".source").write_text(dot_source)
        return m

    def fetch(self, url, headers=None, hosts=None, limit=5_000_000):
        with self.lock:
            self.calls.append(url)
            self.headers.append((headers, hosts, limit))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            self.m += self.tick
            return self.answer(url)
        finally:
            with self.lock:
                self.active -= 1

    def answer(self, url):
        rest = url[len("https://api.github.com/"):]
        path, _, query = rest.partition("?")
        params = urllib.parse.parse_qs(query)
        key = path + ("?path=" + params["path"][0] if path.endswith("/commits") and "path" in params else "")
        v = self.api.get(key, 404)
        if callable(v):
            v = v(url)
        if isinstance(v, BaseException):
            raise v
        if isinstance(v, int):
            raise http_error(v)
        if isinstance(v, dict) and "raw" in v:
            return v["raw"].encode()
        if isinstance(v, dict) and set(v) == {"text"}:
            b = base64.b64encode(v["text"].encode()).decode()
            b = "\n".join(b[i:i + 60] for i in range(0, len(b), 60)) + "\n"
            v = {"type": "file", "encoding": "base64", "content": b, "path": "plugin.json", "sha": "f" * 40}
        return json.dumps(v).encode()

    def calls_with(self, part):
        return [c for c in self.calls if part in c]

    def stored(self):
        return json.loads((self.CONF.parent / "maint.json").read_text())


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fm.BUSY["on"] = False
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = d.name
        self.h = FakeHost(d.name)

    def host(self, api=None, **kw):
        self.h.api.update(api or {})
        return self.h


class ClassifyTests(unittest.TestCase):
    def facts(self, **kw):
        f = {"repo": "acme/widget", "branch": "main", "path": "", "archived": False, "fork_of": "", "commit_at": iso(10), "author_at": "", "release_at": "", "release_tag": "", "decl": None, "decl_note": ""}
        f.update(kw)
        return f

    def label(self, window=365, **kw):
        return fm.classify(self.facts(**kw), NOW, window)["label"]

    def test_precedence_archived_declared_recent(self):
        d = {"by": "example-maintainer", "note": "", "date": "", "file": "plugin.json", "commit": A}
        self.assertEqual(self.label(archived=True, decl=d, commit_at=iso(1)), "archived")
        self.assertEqual(self.label(decl=d, commit_at=iso(1)), "declared")
        self.assertEqual(self.label(commit_at=iso(1)), "recent")
        self.assertEqual(self.label(commit_at=iso(900)), "none")
        self.assertEqual(fm.classify({"unknown": "network"}, NOW, 365)["label"], "unknown")

    def test_exactly_five_labels(self):
        self.assertEqual(set(fm.TITLES), {"archived", "declared", "recent", "none", "unknown"})
        self.assertEqual(fm.TITLES["declared"], "Maintainer-declared unmaintained")
        self.assertEqual(fm.TITLES["recent"], "Recent activity")
        self.assertEqual(fm.TITLES["none"], "No recent activity")

    def test_window_edge(self):
        self.assertEqual(self.label(commit_at=fm.fmt(NOW - 365 * DAY)), "recent")
        self.assertEqual(self.label(commit_at=fm.fmt(NOW - 365 * DAY - 1)), "none")

    def test_window_bounds(self):
        self.assertEqual(fm.window(1), 30)
        self.assertEqual(fm.window(29), 30)
        self.assertEqual(fm.window(30), 30)
        self.assertEqual(fm.window(1825), 1825)
        self.assertEqual(fm.window(10**9), 1825)
        for bad in ("x", None, True, 365.5, [], {}):
            self.assertEqual(fm.window(bad), 365)
        self.assertEqual(self.label(window=1, commit_at=iso(20)), "recent")
        self.assertEqual(self.label(window=10**6, commit_at=iso(1800)), "recent")
        self.assertEqual(self.label(window=10**6, commit_at=iso(1900)), "none")
        self.assertEqual(self.label(window="x", commit_at=iso(300)), "recent")

    def test_none_says_not_abandonment(self):
        r = fm.classify(self.facts(commit_at=iso(900)), NOW, 365)
        self.assertIn("does not establish abandonment", r["reason"])
        self.assertIn("stable software may need few changes", r["reason"])

    def test_future_dates_are_ignored(self):
        r = fm.classify(self.facts(commit_at=fm.fmt(NOW + 30 * DAY)), NOW, 365)
        self.assertEqual(r["label"], "none")
        self.assertTrue(any("future" in e for e in r["evidence"]))
        self.assertEqual(self.label(commit_at=iso(800), release_at=fm.fmt(NOW + 30 * DAY)), "none")

    def test_release_counts_for_whole_repository_only(self):
        self.assertEqual(self.label(commit_at=iso(900), release_at=iso(10)), "recent")
        r = fm.classify(self.facts(path="plugins/x", commit_at=iso(900), release_at=iso(10), release_tag="v2"), NOW, 365)
        self.assertEqual(r["label"], "none")
        self.assertTrue(any("repository-wide" in e for e in r["evidence"]))
        self.assertTrue(any("under plugins/x" in e for e in r["evidence"]))

    def test_whole_repository_evidence(self):
        r = fm.classify(self.facts(), NOW, 365)
        self.assertTrue(any("whole repository" in e for e in r["evidence"]))

    def test_unknown_wording(self):
        zip_ = fm.classify({"unknown": "no_source"}, NOW, 365)["reason"]
        self.assertIn("ZIP with no upstream source", zip_)
        for code in ("unavailable", "rate_limited", "network"):
            self.assertTrue(fm.classify({"unknown": code}, NOW, 365)["reason"].startswith("Could not check"), code)
        self.assertIn("not proof", fm.classify({"unknown": "unavailable"}, NOW, 365)["reason"])
        self.assertEqual(fm.classify(None, NOW, 365)["label"], "unknown")
        self.assertEqual(fm.classify({"unknown": "bogus"}, NOW, 365)["reason"], fm.UNKNOWN["unchecked"])

    def test_author_date_shown_only_when_different(self):
        r = fm.classify(self.facts(commit_at=iso(10), author_at=iso(40)), NOW, 365)
        self.assertTrue(any("authored" in e for e in r["evidence"]))
        r = fm.classify(self.facts(commit_at=iso(10)), NOW, 365)
        self.assertFalse(any("authored" in e for e in r["evidence"]))

    def test_stamp_parsing(self):
        self.assertEqual(fm.parse_stamp("2026-10-09T00:00:00Z"), NOW)
        self.assertEqual(fm.parse_stamp("2026-10-09T02:00:00+02:00"), NOW)
        self.assertEqual(fm.parse_stamp("2026-10-09T00:00:00.123Z"), NOW)
        for bad in ("2026-13-09T00:00:00Z", "2026-10-09", "", None, 5, "2026-10-09T25:00:00Z", "0001-01-01T00:00:00Z", "9999-01-01T00:00:00Z"):
            self.assertIsNone(fm.parse_stamp(bad), bad)


class FixtureTests(Base):
    async def test_every_fixture(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(files), 12)
        for f in files:
            with self.subTest(fixture=f.name):
                fx = json.loads(f.read_text())
                self.setUp()
                h = self.h
                h.api, h.t = fx["api"], fm.parse_stamp(fx["now"])
                p = dict(fx["plugin"])
                h.add(p.pop("id"), **p)
                r = await fm.check_one(h, "demo")
                e = fx["expect"]
                self.assertEqual(r["label"], e["label"], fx["note"])
                for k in ("scope", "fork_of", "repo", "path", "release_at", "commit_at"):
                    if k in e:
                        self.assertEqual(r[k], e[k], k)
                if "declaration" in e:
                    if e["declaration"] is None:
                        self.assertIsNone(r["declaration"])
                    else:
                        for k, v in e["declaration"].items():
                            self.assertEqual(r["declaration"][k], v, k)
                for part in e.get("reason_has", []):
                    self.assertIn(part, r["reason"])
                for part in e.get("evidence_has", []):
                    self.assertTrue(any(part in x for x in r["evidence"]), part)
                if "note_has" in e:
                    self.assertIn(e["note_has"], r["declaration_note"])
                    self.assertTrue(any(e["note_has"] in x for x in r["evidence"]))
                for part in e.get("no_calls", []):
                    self.assertEqual(h.calls_with(part), [], part)
                for part in e.get("calls_have", []):
                    self.assertTrue(h.calls_with(part), part)
                if "all_calls_have" in e:
                    self.assertTrue(all(e["all_calls_have"] in c for c in h.calls))
                self.assertFalse(r["stale"])
                self.assertIsNone(r["failure"])
                self.assertEqual(r["checked_at"], h.t)

    async def test_archived_stops_before_declaration_and_still_reads_commit_and_release(self):
        h = self.host(api_for(archived=True, plugin_text=decl_text(status="unmaintained", by="x")))
        h.add("demo", source=SRC)
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "archived")
        self.assertEqual(len(h.calls), 3)
        self.assertEqual(h.calls_with("contents"), [])
        self.assertTrue(r["archived"])


class DeclarationTests(Base):
    async def run_decl(self, text, api_text=None):
        h = self.host(api_for(commit_at=iso(900), plugin_text=text))
        h.add("demo", source=SRC)
        return await fm.check_one(h, "demo")

    async def test_valid_with_source_file_and_commit(self):
        r = await self.run_decl(decl_text(status="unmaintained", by="example-maintainer", note="No more fixes.", date="2026-08-01"))
        self.assertEqual(r["label"], "declared")
        self.assertEqual(r["declaration"], {"by": "example-maintainer", "note": "No more fixes.", "date": "2026-08-01", "file": "plugin.json", "commit": A})
        self.assertIn("example-maintainer", r["reason"])
        self.assertTrue(any("plugin.json@aaaaaaa" in e for e in r["evidence"]))

    async def test_only_exact_status_and_named_by_are_accepted(self):
        cases = [
            decl_text(status="unmaintained"),
            decl_text(status="unmaintained", by=""),
            decl_text(status="unmaintained", by="  \t "),
            decl_text(status="unmaintained", by=7),
            decl_text(status="unmaintained", by=None),
            decl_text(status="Unmaintained", by="x"),
            decl_text(status="maintained", by="x"),
            decl_text(status=True, by="x"),
            decl_text(by="x"),
            json.dumps({"maintenance": "unmaintained"}),
            json.dumps({"maintenance": ["unmaintained"]}),
            json.dumps({"maintenance": None}),
        ]
        for c in cases:
            with self.subTest(case=c):
                self.setUp()
                r = await self.run_decl(c)
                self.assertEqual(r["label"], "none")
                self.assertIsNone(r["declaration"])
                self.assertIn("ignored", r["declaration_note"])

    async def test_malformed_and_wrong_shapes_are_ignored_with_note(self):
        for c in ("{broken", "", "[1,2]", "null", "\x00\xff", "[" * 5000):
            with self.subTest(case=c[:10]):
                self.setUp()
                r = await self.run_decl(c)
                self.assertEqual(r["label"], "none")
                self.assertIsNone(r["declaration"])
                self.assertIn("ignored", r["declaration_note"])

    async def test_no_maintenance_field_has_no_note(self):
        r = await self.run_decl(json.dumps({"id": "demo"}))
        self.assertEqual(r["declaration_note"], "")
        self.assertEqual(r["label"], "none")

    async def test_text_is_sanitised_and_capped(self):
        r = await self.run_decl(decl_text(status="unmaintained", by="name\x00‮" + "x" * 500, note="a\nb\x07" + "y" * 900, date="2026-02-30"))
        d = r["declaration"]
        self.assertLessEqual(len(d["by"]), 80)
        self.assertLessEqual(len(d["note"]), 300)
        self.assertTrue(d["by"].isprintable() and d["note"].isprintable())
        self.assertNotIn("\n", d["note"])
        self.assertEqual(d["date"], "")

    async def test_raw_response_is_accepted(self):
        h = self.host(api_for(commit_at=iso(900)))
        h.api["repos/acme/widget/contents/plugin.json"] = {"raw": decl_text(status="unmaintained", by="raw-author")}
        h.add("demo", source=SRC)
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "declared")
        self.assertEqual(r["declaration"]["by"], "raw-author")

    async def test_declaration_is_read_at_the_commit(self):
        h = self.host(api_for(commit_at=iso(900), plugin_text=decl_text(status="unmaintained", by="x")))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertTrue(h.calls_with("contents/plugin.json?ref=" + A))

    async def test_missing_file_is_not_an_error(self):
        r = await self.run_decl(None)
        self.assertEqual(r["label"], "none")
        self.assertIsNone(r["failure"])

    async def test_declaration_fetch_error_fails_the_check(self):
        h = self.host(api_for(commit_at=iso(10)))
        h.api["repos/acme/widget/contents/plugin.json"] = http_error(500)
        h.add("demo", source=SRC)
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "unknown")
        self.assertTrue(r["failure"]["reason"].startswith("Could not check"))


class SourceTests(Base):
    async def test_zip_install_has_no_source(self):
        h = self.host()
        h.add("demo")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "unknown")
        self.assertIn("ZIP with no upstream source", r["reason"])
        self.assertEqual(h.calls, [])
        self.assertFalse(r["stale"])
        self.assertEqual(r["repo"], "")

    async def test_non_github_sources_are_unsupported(self):
        for src in ("https://gitlab.com/acme/widget", "http://github.com/acme/widget", "https://github.com/acme", "https://github.com/acme/widget/blob/main/x", "ftp://github.com/acme/widget", "git@github.com:acme/widget.git", "https://example.com/github.com/acme/widget", "https://github.com:8080/acme/widget", "https://user@github.com/acme/widget", "not a url", "https://github.com/../widget", "https://github.com/acme/..", "https://github.com/acme/widget/tree/../x"):
            with self.subTest(src=src):
                self.setUp()
                h = self.host()
                h.add("demo", source=src)
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["label"], "unknown")
                self.assertIn("not a GitHub repository", r["reason"])
                self.assertEqual(h.calls, [])
                self.assertEqual(r["source"], fm.text(src, 200))

    async def test_dot_source_file_is_read(self):
        h = self.host(api_for())
        h.add("demo", dot_source=SRC + "\n")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["repo"], "acme/widget")

    async def test_first_usable_source_wins(self):
        h = self.host(api_for())
        h.add("demo", source=SRC, dot_source="https://gitlab.com/x/y")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["repo"], "acme/widget")

    async def test_git_suffix_and_case(self):
        h = self.host(api_for())
        h.add("demo", source="https://github.com/Acme/Widget.git")
        await fm.check_one(h, "demo")
        self.assertTrue(h.calls[0].endswith("/repos/Acme/Widget"))

    async def test_source_path_from_manifest(self):
        h = self.host()
        h.api.update({"repos/acme/suite": {"full_name": "acme/suite", "archived": False, "default_branch": "main"}, "repos/acme/suite/commits?path=tools/demo": [commit(B, iso(5))]})
        h.add("demo", source="https://github.com/acme/suite", source_path="tools/demo")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["scope"], "plugin path")
        self.assertEqual(r["path"], "tools/demo")
        self.assertTrue(h.calls_with("contents/tools/demo/plugin.json"))

    async def test_hostile_source_path_is_ignored(self):
        for p in ("../x", "a/../b", 5, "a b", "x" * 300, "a\\b", "a?b=c"):
            with self.subTest(p=p):
                self.setUp()
                h = self.host(api_for())
                h.add("demo", source=SRC, source_path=p)
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["scope"], "whole repository")
                self.assertTrue(all("path=" not in c for c in h.calls))

    async def test_branch_from_tree_link(self):
        h = self.host(api_for())
        h.api["repos/acme/widget/commits"] = [commit(A, iso(3))]
        h.add("demo", source=SRC + "/tree/dev")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["branch"], "dev")
        self.assertTrue(h.calls_with("sha=dev"))

    async def test_default_branch_is_recorded(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["branch"], "main")
        self.assertTrue(h.calls_with("sha=main"))

    async def test_monorepo_and_whole_repository_scopes(self):
        h = self.host(api_for("acme/suite"))
        h.api["repos/acme/suite/commits?path=plugins/a"] = [commit(B, iso(2000))]
        h.add("a", source="https://github.com/acme/suite/tree/main/plugins/a")
        h.add("b", source="https://github.com/acme/suite")
        out = await fm.check_all(h, h.manifests())
        self.assertEqual(out["a"]["scope"], "plugin path")
        self.assertEqual(out["a"]["label"], "none")
        self.assertEqual(out["b"]["scope"], "whole repository")
        self.assertEqual(out["b"]["label"], "recent")
        self.assertTrue(any("whole repository" in e for e in out["b"]["evidence"]))
        self.assertTrue(any("under plugins/a" in e for e in out["a"]["evidence"]))

    async def test_unknown_plugin_id_helpers(self):
        h = self.host()
        self.assertEqual(fm.describe(h, 5)["unknown"], "no_source")
        self.assertEqual(fm.describe(h, "_core")["unknown"], "unsupported")


class FailureTests(Base):
    async def test_not_found_private_and_forbidden_are_could_not_check(self):
        cases = {"404": http_error(404), "403": http_error(403, body='{"message":"Resource not accessible"}'), "401": http_error(401), "451": http_error(451), "410": http_error(410)}
        for name, err in cases.items():
            with self.subTest(code=name):
                self.setUp()
                h = self.host({"repos/acme/widget": err})
                h.add("demo", source=SRC)
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["label"], "unknown")
                self.assertTrue(r["reason"].startswith("Could not check"))
                self.assertIn("not proof", r["reason"])
                self.assertTrue(r["failure"]["reason"].startswith("Could not check"))
                self.assertTrue(r["stale"])
                self.assertEqual(r["retry_after"], NOW + fm.COOLDOWN)
                self.assertNotIn("deleted", (r["reason"] + r["failure"]["reason"]).lower())

    async def test_network_errors(self):
        for err in (urllib.error.URLError("down"), TimeoutError("slow"), OSError("reset"), ValueError("redirected away from https"), http_error(502), http_error(500)):
            with self.subTest(err=repr(err)):
                self.setUp()
                h = self.host({"repos/acme/widget": err})
                h.add("demo", source=SRC)
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["label"], "unknown")
                self.assertTrue(r["reason"].startswith("Could not check"))

    async def test_bad_replies(self):
        for body in (b"not json", b"[]", b'{"archived": "no"}', b"null", b'{"full_name":"a/b"}'):
            with self.subTest(body=body):
                self.setUp()
                h = self.host({"repos/acme/widget": {"raw": body.decode()}})
                h.add("demo", source=SRC)
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["label"], "unknown")
                self.assertIsNotNone(r["failure"])

    async def test_bad_commit_and_release_shapes(self):
        for k, v in (("commits", {"raw": "{}"}), ("commits", [{"commit": {}}]), ("commits", [5]), ("commits", [{"commit": {"committer": {"date": "yesterday"}}}]), ("releases/latest", {"raw": "[]"}), ("releases/latest", {"tag_name": "v1"})):
            with self.subTest(k=k, v=v):
                self.setUp()
                h = self.host(api_for())
                h.api["repos/acme/widget/" + k] = v
                h.add("demo", source=SRC)
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["label"], "unknown")

    async def test_missing_recorded_branch_is_could_not_check(self):
        h = self.host(api_for())
        h.api["repos/acme/widget/commits"] = http_error(404)
        h.add("demo", source=SRC + "/tree/gone")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "unknown")
        self.assertIn("Could not check", r["failure"]["reason"])

    async def test_release_404_is_not_an_error(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "recent")
        self.assertEqual(r["release_at"], "")
        self.assertIsNone(r["failure"])

    async def test_never_touches_anything_but_its_cache(self):
        h = self.host(api_for(archived=True))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertEqual(sorted(p.name for p in h.CONF.parent.iterdir()), ["maint.json"])
        self.assertTrue(set(vars(h)) >= {"plugins"})

    async def test_no_credentials_and_https_only(self):
        h = self.host(api_for(plugin_text=decl_text(status="unmaintained", by="x")))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertTrue(h.calls)
        self.assertTrue(all(c.startswith("https://api.github.com/repos/") for c in h.calls))
        for headers, hosts, limit in h.headers:
            self.assertEqual({k.lower() for k in headers}, {"accept", "user-agent"})
            self.assertEqual(hosts, ("api.github.com",))
            self.assertLessEqual(limit, 500_000)


class CacheTests(Base):
    async def test_fresh_result_is_not_refetched_but_forced_is(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        await fm.check_all(h, h.manifests())
        n = len(h.calls)
        await fm.check_all(h, h.manifests(), now=NOW + DAY - 1)
        self.assertEqual(len(h.calls), n)
        await fm.check_all(h, h.manifests(), now=NOW + DAY - 1, force=True)
        self.assertGreater(len(h.calls), n)

    async def test_stale_after_ttl_and_refetched(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertFalse(fm.cached(h, "demo", NOW + DAY)["stale"])
        r = fm.cached(h, "demo", NOW + DAY + 1)
        self.assertTrue(r["stale"])
        self.assertEqual(r["label"], "recent")
        self.assertEqual(r["checked_at"], NOW)
        n = len(h.calls)
        await fm.check_all(h, h.manifests(), now=NOW + DAY + 1)
        self.assertGreater(len(h.calls), n)

    async def test_cached_makes_no_network_call(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        r = fm.cached(h, "demo", NOW)
        self.assertEqual(h.calls, [])
        self.assertEqual(r["label"], "unknown")
        self.assertEqual(r["reason"], fm.UNKNOWN["unchecked"])
        self.assertTrue(r["stale"])
        self.assertEqual(fm.cached(h, SRC, NOW)["source"], SRC)

    async def test_failed_refresh_keeps_last_success_marked_stale(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        first = await fm.check_one(h, "demo")
        self.assertEqual(first["label"], "recent")
        h.t = NOW + 2 * DAY
        h.api["repos/acme/widget"] = http_error(404)
        r = await fm.check_one(h, "demo", now=h.t)
        self.assertEqual(r["label"], "recent")
        self.assertTrue(r["stale"])
        self.assertEqual(r["checked_at"], NOW)
        self.assertEqual(r["commit_at"], first["commit_at"])
        self.assertTrue(r["failure"]["reason"].startswith("Could not check"))
        self.assertEqual(r["failure"]["at"], h.t)
        self.assertEqual(r["retry_after"], h.t + fm.COOLDOWN)

    async def test_cooldown_after_failure_blocks_background_but_not_force(self):
        h = self.host({"repos/acme/widget": urllib.error.URLError("down")})
        h.add("demo", source=SRC)
        await fm.check_all(h, h.manifests())
        n = len(h.calls)
        await fm.check_all(h, h.manifests(), now=NOW + 60)
        self.assertEqual(len(h.calls), n)
        await fm.check_all(h, h.manifests(), now=NOW + fm.COOLDOWN + 1)
        self.assertGreater(len(h.calls), n)
        n = len(h.calls)
        await fm.check_one(h, "demo", now=NOW + fm.COOLDOWN + 2)
        self.assertGreater(len(h.calls), n)

    async def test_success_clears_failure(self):
        h = self.host({"repos/acme/widget": http_error(404)})
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        h.api.update(api_for(commit_at=iso(5)))
        r = await fm.check_one(h, "demo")
        self.assertIsNone(r["failure"])
        self.assertFalse(r["stale"])
        self.assertEqual(r["retry_after"], 0)

    async def test_changing_window_relabels_without_network(self):
        h = self.host(api_for(commit_at=iso(100)))
        h.add("demo", source=SRC)
        self.assertEqual((await fm.check_one(h, "demo"))["label"], "recent")
        n = len(h.calls)
        self.assertEqual((await fm.METHODS["maint_prefs"](h, {"maint.window_days": 60}))["maint.window_days"], 60)
        r = fm.cached(h, "demo", NOW)
        self.assertEqual((r["label"], r["window_days"]), ("none", 60))
        self.assertEqual(len(h.calls), n)

    def entries(self, n, **kw):
        out = {}
        for i in range(n):
            out["o%03d/r@#" % i] = {"ok_at": NOW - i, "facts": {"repo": "o%03d/r" % i, "branch": "main", "path": "", "archived": False, "fork_of": "", "commit_at": iso(5), "author_at": "", "release_at": "", "release_tag": "", "decl": None, "decl_note": ""}, "fail": None, "retry_after": 0, **kw}
        return out

    def write_store(self, h, entries, **top):
        h.CONF.parent.mkdir(parents=True, exist_ok=True)
        (h.CONF.parent / "maint.json").write_text(json.dumps({"v": 1, "prefs": {"enabled_background": False, "window_days": 365}, "bg_next": 0, "limit": None, "entries": entries, **top}))

    async def test_entry_cap_and_lru_eviction(self):
        h = self.host(api_for("n/new"))
        self.write_store(h, self.entries(200))
        h.add("old", source="https://github.com/o000/r")
        h.api.update(api_for("o000/r"))
        await fm.check_one(h, "old")
        self.assertEqual(len(h.stored()["entries"]), 200)
        h.add("new", source="https://github.com/n/new")
        await fm.check_one(h, "new")
        keys = list(h.stored()["entries"])
        self.assertEqual(len(keys), 200)
        self.assertIn("o000/r@#", keys)
        self.assertNotIn("o001/r@#", keys)
        self.assertEqual(keys[-1], "n/new@#")
        self.assertEqual(keys[-2], "o000/r@#")

    async def test_entries_over_the_cap_in_the_file_are_cut_on_load(self):
        h = self.host()
        self.write_store(h, self.entries(250))
        self.assertLessEqual(len(fm.load(h)["entries"]), 200)
        s = fm.load(h)
        fm.save(h, s)
        self.assertLessEqual(len(h.stored()["entries"]), 200)

    async def test_size_cap_evicts_oldest_first(self):
        h = self.host()
        big = "x" * 300
        s = fm.blank()
        for i in range(200):
            d = {"by": "b" * 80, "note": big, "date": "", "file": "plugin.json", "commit": A}
            e = self.entries(1)["o000/r@#"]
            e["facts"]["decl"] = d
            e["facts"]["decl_note"] = "n" * 160
            e["fail"] = {"reason": "r" * 300, "at": NOW, "code": "network"}
            s["entries"]["o%03d/r@#" % i] = e
        fm.save(h, s)
        size = (h.CONF.parent / "maint.json").stat().st_size
        self.assertLessEqual(size, 65536)
        kept = list(h.stored()["entries"])
        self.assertTrue(0 < len(kept) < 200)
        self.assertEqual(kept[-1], "o199/r@#")
        self.assertEqual(kept, ["o%03d/r@#" % i for i in range(200 - len(kept), 200)])

    async def test_invalid_cache_file_is_repaired(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        h.CONF.parent.mkdir(parents=True)
        path = h.CONF.parent / "maint.json"
        for junk in (b"\xff\xfe\x00garbage", b"[1,2,3]", b"null", b'{"entries": 5}', b"{", b"x" * 70000, b'{"entries":' + b"[" * 100000, b""):
            with self.subTest(junk=junk[:12]):
                path.write_bytes(junk)
                s = fm.load(h)
                self.assertEqual(s, fm.blank())
                r = await fm.check_one(h, "demo")
                self.assertEqual(r["label"], "recent")
                self.assertEqual(len(h.stored()["entries"]), 1)

    async def test_bad_entries_are_dropped_alone(self):
        h = self.host()
        good = self.entries(2)
        ents = {
            "o000/r@#": good["o000/r@#"],
            "bad key": good["o001/r@#"],
            "o002/r@#": "string",
            "o003/r@#": {"ok_at": "x", "facts": 5, "fail": 5},
            "o004/r@#": {"ok_at": NOW, "facts": {"repo": "no-slash", "branch": "main", "path": ""}},
            "o005/r@#": {"ok_at": 0, "facts": good["o001/r@#"]["facts"]},
            "o006/r@#": {"ok_at": NOW, "facts": {**good["o001/r@#"]["facts"], "path": "../x"}},
            "o007/r@#": {"fail": {"reason": "Could not check: x", "at": NOW, "code": "network"}, "retry_after": NOW},
            "o008/r@#": {"fail": {"reason": "x", "at": NOW, "code": "evil"}},
            "o009/r@#": {"ok_at": NOW, "facts": {**good["o001/r@#"]["facts"], "commit_at": "garbage", "decl": {"by": "", "file": "plugin.json"}, "fork_of": "bad", "release_tag": "a b"}},
        }
        self.write_store(h, ents, prefs={"enabled_background": "yes", "window_days": "many"}, bg_next="soon", limit={"until": "x"})
        s = fm.load(h)
        self.assertEqual(list(s["entries"]), ["o000/r@#", "o007/r@#", "o009/r@#"])
        self.assertIsNone(s["entries"]["o007/r@#"]["facts"])
        f = s["entries"]["o009/r@#"]["facts"]
        self.assertEqual((f["commit_at"], f["decl"], f["fork_of"], f["release_tag"]), ("", None, "", ""))
        self.assertEqual(s["prefs"], {"enabled_background": False, "window_days": 365})
        self.assertEqual((s["bg_next"], s["limit"]), (0, None))

    async def test_unwritable_cache_does_not_break_the_check(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        h.CONF.parent.parent.mkdir(parents=True, exist_ok=True)
        h.CONF.parent.write_text("a file, not a directory")
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "unknown")
        self.assertTrue(h.logs)

    async def test_write_is_atomic_and_leaves_no_temp_file(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertEqual(sorted(p.name for p in h.CONF.parent.iterdir()), ["maint.json"])
        before = (h.CONF.parent / "maint.json").read_bytes()
        real = os.replace
        os.replace = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
        try:
            self.assertFalse(fm.save(h, fm.blank()))
        finally:
            os.replace = real
        self.assertEqual((h.CONF.parent / "maint.json").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in h.CONF.parent.iterdir()), ["maint.json"])


class RateLimitTests(Base):
    def setup_limited(self, err):
        h = self.host(api_for("a/one"))
        h.api.update(api_for("b/two"))
        h.api["repos/a/one"] = err
        h.add("one", source="https://github.com/a/one")
        h.add("two", source="https://github.com/b/two")
        return h

    async def test_429_with_retry_after(self):
        h = self.setup_limited(http_error(429, Retry_After=120))
        r = await fm.check_one(h, "one")
        self.assertEqual(r["label"], "unknown")
        self.assertEqual(r["retry_after"], NOW + 120)
        self.assertIn("rate limit", r["failure"]["reason"])
        n = len(h.calls)
        r = await fm.check_one(h, "one", now=NOW + 119)
        self.assertEqual(len(h.calls), n)
        self.assertEqual(r["retry_after"], NOW + 120)
        h.api.update(api_for("a/one"))
        r = await fm.check_one(h, "one", now=NOW + 120)
        self.assertGreater(len(h.calls), n)
        self.assertEqual(r["label"], "recent")
        self.assertEqual(r["retry_after"], 0)

    async def test_403_remaining_zero_with_reset(self):
        h = self.setup_limited(http_error(403, X_RateLimit_Remaining=0, X_RateLimit_Reset=NOW + 1800))
        r = await fm.check_one(h, "one")
        self.assertEqual(r["retry_after"], NOW + 1800)
        n = len(h.calls)
        await fm.check_one(h, "one", now=NOW + 1799, force=True)
        self.assertEqual(len(h.calls), n)

    async def test_secondary_limit_in_body_and_retry_after_on_403(self):
        h = self.setup_limited(http_error(403, body='{"message":"You have exceeded a secondary rate limit"}'))
        r = await fm.check_one(h, "one")
        self.assertEqual(r["retry_after"], NOW + fm.WAIT)
        self.setUp()
        h = self.setup_limited(http_error(403, Retry_After=45, X_RateLimit_Remaining=7))
        r = await fm.check_one(h, "one")
        self.assertEqual(r["retry_after"], NOW + 45)

    async def test_retry_after_wins_over_reset_and_values_are_bounded(self):
        cases = [
            (dict(Retry_After=30, X_RateLimit_Remaining=0, X_RateLimit_Reset=NOW + 3000), NOW + 30),
            (dict(Retry_After=10**9), NOW + fm.WAIT_MAX),
            (dict(X_RateLimit_Remaining=0, X_RateLimit_Reset=NOW - 500), NOW + 1),
            (dict(X_RateLimit_Remaining=0, X_RateLimit_Reset=NOW + 10**9), NOW + fm.WAIT_MAX),
            (dict(Retry_After="Wed, 21 Oct 2026 07:28:00 GMT", X_RateLimit_Remaining=0, X_RateLimit_Reset=NOW + 600), NOW + 600),
            (dict(Retry_After="9" * 5000), NOW + fm.WAIT),
            (dict(), NOW + fm.WAIT),
        ]
        for hdr, want in cases:
            with self.subTest(hdr=str(hdr)[:60]):
                self.setUp()
                h = self.setup_limited(http_error(429, **hdr))
                self.assertEqual((await fm.check_one(h, "one"))["retry_after"], want)

    async def test_plain_403_is_not_a_rate_limit(self):
        h = self.setup_limited(http_error(403, X_RateLimit_Remaining=40))
        r = await fm.check_one(h, "one")
        self.assertEqual(r["retry_after"], NOW + fm.COOLDOWN)
        self.assertIsNone(h.stored()["limit"])

    async def test_limit_stops_the_run_and_other_repos_are_not_requested(self):
        h = self.setup_limited(http_error(429, Retry_After=300))
        with mock.patch.object(fm, "PARALLEL", 1):
            out = await fm.check_all(h, h.manifests())
        self.assertEqual(out["one"]["label"], "unknown")
        self.assertEqual(h.calls_with("repos/b/two"), [])
        self.assertEqual(h.stored()["limit"]["until"], NOW + 300)
        n = len(h.calls)
        out = await fm.check_all(h, h.manifests(), now=NOW + 100, force=True)
        self.assertEqual(len(h.calls), n)
        self.assertEqual(out["two"]["label"], "unknown")
        self.assertEqual(out["two"]["retry_after"], NOW + 300)
        self.assertIn("rate limit", out["two"]["failure"]["reason"])
        self.assertEqual(out["two"]["reason"], fm.UNKNOWN["rate_limited"])
        h.api["repos/a/one"] = api_for("a/one")["repos/a/one"]
        await fm.check_all(h, h.manifests(), now=NOW + 300)
        self.assertTrue(h.calls_with("repos/b/two"))

    async def test_cached_result_returned_while_limited(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        h.api["repos/acme/widget"] = http_error(429, Retry_After=3600)
        r = await fm.check_one(h, "demo", now=NOW + 2 * DAY)
        self.assertEqual(r["label"], "recent")
        self.assertTrue(r["stale"])
        self.assertEqual(r["retry_after"], NOW + 2 * DAY + 3600)
        n = len(h.calls)
        r = await fm.check_one(h, "demo", now=NOW + 2 * DAY + 10)
        self.assertEqual(len(h.calls), n)
        self.assertEqual((r["label"], r["stale"]), ("recent", True))
        self.assertIn("rate limit", r["failure"]["reason"])

    async def test_mid_check_limit_discards_partial_data(self):
        h = self.host(api_for(commit_at=iso(5)))
        h.api["repos/acme/widget/releases/latest"] = http_error(429, Retry_After=100)
        h.add("demo", source=SRC)
        r = await fm.check_one(h, "demo")
        self.assertEqual(r["label"], "unknown")
        self.assertEqual(h.calls_with("contents"), [])
        self.assertEqual(r["retry_after"], NOW + 100)


class BudgetTests(Base):
    def many(self, n, archived=False):
        for i in range(n):
            full = "o%02d/r" % i
            self.h.api.update(api_for(full, archived=archived))
            self.h.add("p%02d" % i, source="https://github.com/" + full)

    async def test_dedupe_two_plugins_one_repository(self):
        h = self.host(api_for())
        h.add("one", source=SRC)
        h.add("two", dot_source=SRC + "/")
        out = await fm.check_all(h, h.manifests())
        self.assertEqual(len(h.calls), 4)
        for part in ("repos/acme/widget?", "/commits?"):
            self.assertLessEqual(len(h.calls_with(part)), 1)
        self.assertEqual(len(h.calls_with("releases/latest")), 1)
        self.assertEqual(len(h.calls_with("contents")), 1)
        self.assertEqual(out["one"]["label"], out["two"]["label"])
        self.assertEqual(len(h.stored()["entries"]), 1)

    async def test_dedupe_same_plugin_listed_twice_and_across_runs(self):
        h = self.host(api_for())
        h.add("one", source=SRC)
        await fm.check_all(h, [h.plugins["one"], "one", h.plugins["one"]])
        self.assertEqual(len(h.calls), 4)

    async def test_monorepo_shares_repo_and_release(self):
        h = self.host(api_for("acme/suite"))
        for p in ("a", "b", "c"):
            h.api["repos/acme/suite/commits?path=plugins/" + p] = [commit(B, iso(3))]
            h.add(p, source="https://github.com/acme/suite/tree/main/plugins/" + p)
        out = await fm.check_all(h, h.manifests())
        self.assertEqual(len(h.calls_with("releases/latest")), 1)
        self.assertEqual(len([c for c in h.calls if c.endswith("/repos/acme/suite")]), 1)
        self.assertEqual(len(h.calls_with("/commits?")), 3)
        self.assertEqual(len(h.calls_with("contents")), 3)
        self.assertEqual({r["label"] for r in out.values()}, {"recent"})

    async def test_requests_per_repository(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertEqual(len(h.calls), 4)
        self.setUp()
        h = self.host(api_for(archived=True))
        h.add("demo", source=SRC)
        await fm.check_one(h, "demo")
        self.assertEqual(len(h.calls), 3)

    async def test_concurrency_is_bounded_by_two(self):
        self.many(10)
        self.h.delay = 0.01
        out = await fm.check_all(self.h, self.h.manifests())
        self.assertEqual(len(out), 10)
        self.assertEqual({r["label"] for r in out.values()}, {"recent"})
        self.assertLessEqual(self.h.peak, 2)
        self.assertEqual(self.h.peak, 2)

    async def test_run_request_cap(self):
        self.many(30)
        out = await fm.check_all(self.h, self.h.manifests())
        self.assertEqual(len(self.h.calls), fm.CAP)
        done = [r for r in out.values() if r["label"] == "recent"]
        left = [r for r in out.values() if r["label"] == "unknown"]
        self.assertEqual(len(done), 10)
        self.assertEqual(len(done) + len(left), 30)
        self.assertTrue(all(r["reason"] == fm.UNKNOWN["unchecked"] and r["failure"] is None and r["stale"] for r in left))
        self.assertEqual(len(self.h.stored()["entries"]), 10)
        n = len(self.h.calls)
        out = await fm.check_all(self.h, self.h.manifests())
        self.assertGreater(len(self.h.calls), n)
        self.assertLessEqual(len(self.h.calls) - n, fm.CAP)
        self.assertEqual(sum(1 for r in out.values() if r["label"] == "recent"), 20)

    async def test_time_budget_stops_new_requests(self):
        self.many(10)
        self.h.tick = 10.0
        out = await fm.check_all(self.h, self.h.manifests())
        self.assertLess(len(self.h.calls), 10)
        self.assertGreater(len(self.h.calls), 0)
        self.assertLess(sum(1 for r in out.values() if r["label"] == "recent"), 10)

    async def test_least_recently_checked_goes_first(self):
        self.many(3)
        self.h.api.update(api_for("o00/r"))
        s = fm.blank()
        s["entries"]["o00/r@#"] = self.entry_at(NOW - 3 * DAY)
        s["entries"]["o01/r@#"] = self.entry_at(NOW - 5 * DAY)
        fm.save(self.h, s)
        self.h.delay = 0
        order = []
        real = self.h.answer

        def spy(url):
            if url.endswith("/r") and "/repos/o0" in url:
                order.append(url.split("/")[-2])
            return real(url)

        self.h.answer = spy
        await fm.check_all(self.h, self.h.manifests(), force=True)
        self.assertEqual(set(order[:2]), {"o02", "o01"})
        self.assertEqual(order[2], "o00")

    def entry_at(self, when):
        return {"ok_at": when, "facts": {"repo": "x/y", "branch": "main", "path": "", "archived": False, "fork_of": "", "commit_at": iso(5), "author_at": "", "release_at": "", "release_tag": "", "decl": None, "decl_note": ""}, "fail": None, "retry_after": 0}

    async def test_plugin_list_is_capped(self):
        self.h.add("only", source=SRC)
        self.h.api.update(api_for())
        out = await fm.check_all(self.h, ["only"] * 1000)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(self.h.calls), 4)

    async def test_concurrent_runs_do_not_overlap(self):
        self.many(2)
        self.h.delay = 0.02
        a, b = await asyncio.gather(fm.check_all(self.h, self.h.manifests()), fm.check_all(self.h, self.h.manifests()))
        self.assertEqual(len(a), 2)
        self.assertEqual(len(b), 2)
        self.assertEqual(len(self.h.calls), 8)
        self.assertFalse(fm.BUSY["on"])


class ForkTests(Base):
    async def test_fork_is_assessed_itself(self):
        h = self.host(api_for("forker/widget", fork=True, parent="upstream/widget", commit_at=iso(900)))
        h.api.update(api_for("upstream/widget", commit_at=iso(2)))
        h.add("demo", source="https://github.com/forker/widget")
        r = await fm.check_one(h, "demo")
        self.assertEqual((r["repo"], r["fork_of"], r["label"]), ("forker/widget", "upstream/widget", "none"))
        self.assertEqual(h.calls_with("upstream/widget"), [])

    async def test_fork_flag_without_parent_records_nothing(self):
        h = self.host(api_for(fork=True))
        h.add("demo", source=SRC)
        self.assertEqual((await fm.check_one(h, "demo"))["fork_of"], "")

    async def test_renamed_repository_uses_reported_name(self):
        h = self.host(api_for())
        h.api["repos/acme/widget"]["full_name"] = "acme/widget-new"
        h.add("demo", source=SRC)
        self.assertEqual((await fm.check_one(h, "demo"))["repo"], "acme/widget-new")


class BackgroundAndMethodsTests(Base):
    async def test_background_off_by_default_does_nothing(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        self.assertIsNone(await fm.background(h, h.manifests()))
        self.assertEqual(h.calls, [])
        self.assertEqual(await fm.METHODS["maint_prefs"](h, None), {"maint.enabled_background": False, "maint.window_days": 365})

    async def test_background_runs_at_most_weekly_with_jitter(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        await fm.METHODS["maint_prefs"](h, {"maint.enabled_background": True})
        h.rnd = 0.5
        out = await fm.background(h, h.manifests(), NOW)
        self.assertEqual(out["demo"]["label"], "recent")
        self.assertEqual(h.stored()["bg_next"], int(NOW + 7 * DAY + 0.5 * DAY))
        n = len(h.calls)
        self.assertIsNone(await fm.background(h, h.manifests(), NOW + 7 * DAY))
        self.assertIsNone(await fm.background(h, h.manifests(), NOW + 7 * DAY + DAY // 2 - 1))
        self.assertEqual(len(h.calls), n)
        self.assertIsNotNone(await fm.background(h, h.manifests(), NOW + 7 * DAY + DAY // 2))
        self.assertGreater(len(h.calls), n)
        for r in (0.0, 0.999999):
            h.rnd = r
            await fm.background(h, h.manifests(), NOW + 100 * DAY)
            nxt = h.stored()["bg_next"]
            self.assertTrue(NOW + 107 * DAY <= nxt < NOW + 108 * DAY)
            h.t = NOW
            s = fm.load(h)
            s["bg_next"] = 0
            fm.save(h, s)

    async def test_methods_table(self):
        self.assertEqual(set(fm.METHODS), {"maint_get", "maint_refresh", "maint_prefs"})
        for fn in fm.METHODS.values():
            self.assertTrue(inspect.iscoroutinefunction(fn))

    async def test_maint_get(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        h.add("zipped")
        r = await fm.METHODS["maint_get"](h, {"id": "demo"})
        self.assertEqual((r["id"], r["label"], r["stale"]), ("demo", "unknown", True))
        self.assertEqual(h.calls, [])
        r = await fm.METHODS["maint_get"](h, {"ids": ["demo", "zipped"]})
        self.assertEqual(set(r["records"]), {"demo", "zipped"})
        self.assertEqual(r["records"]["zipped"]["label"], "unknown")
        r = await fm.METHODS["maint_get"](h, {"source": SRC})
        self.assertEqual(r["source"], SRC)
        for bad in (None, {}, {"id": "nope"}, {"id": 5}, {"id": "_core"}, {"ids": "demo"}, {"ids": ["nope"]}, {"ids": ["demo"] * 65}, "demo", []):
            r = await fm.METHODS["maint_get"](h, bad)
            self.assertIn("error", r, bad)
            self.assertIn("code", r)
        self.assertEqual(h.calls, [])

    async def test_maint_refresh_one_and_all(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        h.add("zipped")
        r = await fm.METHODS["maint_refresh"](h, {"id": "demo"})
        self.assertEqual(r["label"], "recent")
        n = len(h.calls)
        r = await fm.METHODS["maint_refresh"](h, {"id": "all"})
        self.assertEqual(set(r["records"]), {"demo", "zipped"})
        self.assertGreater(len(h.calls), n)
        for bad in (None, {}, {"id": "nope"}, {"id": 5}, {"id": "ALL"}):
            self.assertIn("error", await fm.METHODS["maint_refresh"](h, bad))

    async def test_maint_refresh_while_busy(self):
        h = self.host(api_for())
        h.add("demo", source=SRC)
        fm.BUSY["on"] = True
        r = await fm.METHODS["maint_refresh"](h, {"id": "demo"})
        self.assertEqual(r["code"], "busy")
        self.assertEqual(h.calls, [])

    async def test_maint_prefs_validation_and_persistence(self):
        h = self.host()
        p = fm.METHODS["maint_prefs"]
        r = await p(h, {"maint.window_days": 90, "maint.enabled_background": True})
        self.assertEqual(r, {"maint.enabled_background": True, "maint.window_days": 90})
        self.assertEqual(await p(h, {}), r)
        self.assertEqual(h.stored()["prefs"], {"enabled_background": True, "window_days": 90})
        for bad in ({"maint.window_days": 29}, {"maint.window_days": 1826}, {"maint.window_days": "90"}, {"maint.window_days": True}, {"maint.window_days": 90.5}, {"maint.enabled_background": 1}, {"maint.enabled_background": "yes"}, {"window_days": 90}, {"maint.other": 1}, {"maint.window_days": 100, "x": 1}):
            r = await p(h, bad)
            self.assertIn("error", r, bad)
        self.assertEqual(h.stored()["prefs"], {"enabled_background": True, "window_days": 90})
        self.assertEqual((await p(h, {"maint.window_days": 30}))["maint.window_days"], 30)
        self.assertEqual((await p(h, {"maint.window_days": 1825}))["maint.window_days"], 1825)

    async def test_assess_url(self):
        h = self.host(api_for("acme/catalog", commit_at=iso(4)))
        r = await fm.assess_url(h, "https://github.com/acme/catalog")
        self.assertEqual((r["id"], r["label"], r["repo"]), (None, "recent", "acme/catalog"))
        n = len(h.calls)
        await fm.assess_url(h, "https://github.com/acme/catalog")
        self.assertEqual(len(h.calls), n)
        r = await fm.assess_url(h, "https://example.org/x")
        self.assertEqual(r["label"], "unknown")
        self.assertEqual(len(h.calls), n)

    async def test_methods_never_raise(self):
        h = self.host()
        h.manifest = lambda d: (_ for _ in ()).throw(RuntimeError("boom"))
        r = await fm.METHODS["maint_get"](h, {"id": "demo"})
        self.assertEqual(r["code"], "failed")


class SourceCodeTests(unittest.TestCase):
    path = Path(__file__).resolve().parent.parent / "fy_maint.py"

    def test_no_comments_and_no_docstrings(self):
        src = self.path.read_text()
        with open(self.path, "rb") as f:
            self.assertEqual([t for t in tokenize.tokenize(f.readline) if t.type == tokenize.COMMENT], [])
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(ast.get_docstring(node))

    def test_python39_grammar_and_stdlib_only(self):
        tree = ast.parse(self.path.read_text(), feature_version=(3, 9))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        self.assertTrue(mods <= set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else True, mods)
        self.assertFalse({m for m in mods if m.startswith("fy_") or m == "framey"})

    def test_no_pushed_at_no_timers_no_polling(self):
        src = self.path.read_text()
        for word in ("pushed_at", "updated_at", "call_later", "call_at", "while True", "Authorization", "token", "threading"):
            self.assertNotIn(word, src, word)
        self.assertNotIn("asyncio.sleep", src)

    def test_no_side_effects_at_import(self):
        self.assertEqual(fm.BUSY, {"on": False})


class ClockSkewTests(unittest.TestCase):
    def test_timestamps_from_the_future_are_clamped_to_now(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "config.json"
            far = 4_000_000_000
            (Path(d) / "maint.json").write_text(json.dumps({
                "bg_next": far,
                "limit": {"until": far, "at": far, "reason": "x"},
                "entries": {"a/b@main#": {"ok_at": far, "facts": None, "fail": None, "retry_after": far}},
            }))
            host = types.SimpleNamespace(CONF=conf, now=lambda: 1_000_000)
            s = fm.load(host)
            self.assertLessEqual(s["bg_next"], 1_000_000 + fm.WEEK + fm.DAY)
            self.assertLessEqual(s["limit"]["until"], 1_000_000 + fm.WAIT_MAX)
            self.assertLessEqual(s["limit"]["at"], 1_000_000)
            for e in s["entries"].values():
                self.assertLessEqual(e["ok_at"], 1_000_000)
                self.assertLessEqual(e["retry_after"], 1_000_000 + fm.WEEK)


if __name__ == "__main__":
    unittest.main()
