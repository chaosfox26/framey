import asyncio
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H
import test_fy_integration_update as U

BACKEND = """
import framey_api


def startup(ctx):
    if %r:
        raise RuntimeError("cannot start")


async def call(method, arg):
    if method == "__ping__":
        raise ValueError("unknown method")
    return {"m": method, "a": arg}
"""


def token_of(fy, pid):
    return next(t for t, p in fy.TOKS.items() if p == pid)


class Base(unittest.IsolatedAsyncioTestCase):
    async def start(self, plugins=("a", "b"), prefs=None):
        loader = H.Loader(prefs=prefs)
        self.addAsyncCleanup(loader.close)
        for pid in plugins:
            loader.plugin(pid, {"api": 1}, main="window.RAN_%s=1" % pid, backend=BACKEND % False)
        loader.fy.load_backends()
        return loader

    async def attach(self, loader, name="main"):
        page, task = await H.attach(loader, name)
        self.addCleanup(task.cancel)
        return page, task

    async def call(self, page, **req):
        n = len(page.replies())
        page.bind(**req)
        self.assertTrue(await H.settle(lambda: len(page.replies()) > n))
        return page.replies()[-1][2]


HALF = """
import framey_api


def migrate(ctx):
    with open(%r, "w") as f:
        f.write('{"half": "migrated"}')
    raise RuntimeError("migration broke")


async def call(method, arg):
    return {}
"""


class MigrationRestoreTests(Base):
    async def test_a_failed_migration_restores_the_settings_it_started_with(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        cfg = fy.dirs("m").config
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text('{"before": 1}')
        loader.plugin("m", {"api": 1, "version": "2.0.0"}, backend=HALF % str(cfg / "settings.json"))
        fy.set_module_state("m", {"ver": "1.0.0"})
        fy.load_backends()
        with self.assertRaises(fy.use("fy_supervisor").BackendError) as caught:
            await fy.backend("m", "x", 1)
        self.assertEqual(caught.exception.stage, "migrate")
        self.assertEqual(json.loads((cfg / "settings.json").read_text()), {"before": 1})
        self.assertTrue((cfg / "settings.json.premigrate").exists())
        self.assertEqual(fy.module_state("m")["ver"], "1.0.0")

    async def test_the_host_exposes_restore_snapshot_for_the_supervisor(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        cfg = fy.dirs("m").config
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text('{"a": 1}')
        fy.snapshot_settings("m")
        (cfg / "settings.json").write_text('{"a": 2}')
        self.assertIs(fy.host.restore_snapshot(("m")), True)
        self.assertEqual(json.loads((cfg / "settings.json").read_text()), {"a": 1})
        self.assertIs(fy.host.restore_snapshot("nothing"), False)


class AuthorizationTests(Base):
    async def test_a_page_script_without_the_token_cannot_change_anything(self):
        loader = await self.start()
        page, _ = await self.attach(loader)
        for i, (method, arg) in enumerate([("install", "https://example.com/x.zip"), ("uninstall", {"id": "a", "purge": True}), ("update", "a"), ("ver_install", {"id": "a", "tag": "v1.0.0", "confirm": True}), ("repair", {"id": "a", "reset": True, "confirm": True}), ("batch_apply", {"ids": ["a"]}), ("prefs_set", {"lang": "de"}), ("loader_update", {"confirm": True}), ("rollback", "a"), ("finish", "a"), ("enable", "a"), ("disable", "a"), ("reload", None), ("diag", None), ("list", None), ("state_get", None), ("asset", {"name": "picker"}), ("fetch", {"url": "https://example.com/", "pid": "a"}), ("resource", {"pid": "a", "root": "data", "path": "x"}), ("log", {"pid": "a", "msg": "x"})]):
            r = await self.call(page, id=i + 1, gen="g", plugin="_core", method=method, arg=arg, t=None)
            self.assertEqual(r, {"error": "not authorized"}, method)
        self.assertTrue((loader.plugins / "a" / "plugin.json").exists())
        self.assertEqual(loader.net.calls, [])
        self.assertFalse(any("Runtime.evaluate" in str(o) and "dispose" in str(o) for o in page.sent[len(page.sent) - 1:]))

    async def test_a_wrong_token_and_a_plugin_token_on_a_core_method_are_refused(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        tok = token_of(fy, "a")
        for i, t in enumerate(["x" * 32, "", tok, 5, None, "é" * 32]):
            r = await self.call(page, id=i + 1, gen="g", plugin="_core", method="uninstall", arg={"id": "a", "purge": True}, t=t)
            self.assertEqual(r, {"error": "not authorized"}, repr(t))
        self.assertTrue((loader.plugins / "a").exists())
        r = await self.call(page, id=50, gen="g", plugin="_core", method="list", t=fy.CORE_TOK)
        self.assertEqual(sorted(m["id"] for m in r), ["a", "b"])

    async def test_a_forged_pid_cannot_read_another_plugins_data(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        victim = fy.dirs("b").data
        victim.mkdir(parents=True, exist_ok=True)
        (victim / "secret.txt").write_text("SECRET-123")
        mine = fy.dirs("a").data
        mine.mkdir(parents=True, exist_ok=True)
        (mine / "own.txt").write_text("own")
        tok = token_of(fy, "a")
        r = await self.call(page, id=1, gen="g", plugin="_core", method="resource", arg={"pid": "b", "root": "data", "path": "secret.txt"}, t=tok)
        self.assertNotIn("SECRET-123", json.dumps(r))
        self.assertIn("error", r)
        self.assertNotEqual(r["error"], "not authorized")
        r = await self.call(page, id=2, gen="g", plugin="_core", method="resource", arg={"pid": "b", "root": "data", "path": "own.txt"}, t=tok)
        self.assertNotIn("error", r)
        r = await self.call(page, id=3, gen="g", plugin="_core", method="files_list", arg={"pid": "b", "root": "data"}, t=tok)
        self.assertEqual([e["name"] for e in r["entries"]], ["own.txt"])

    async def test_every_plugin_scoped_method_uses_the_pid_of_the_token_never_the_one_in_the_message(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        net, files, store, targets = (fy.use(m) for m in ("fy_net", "fy_files", "fy_store", "fy_targets"))
        seen = {}

        def spy(name, result=None):
            async def run(*a, **k):
                seen.setdefault(name, []).append(a[0] if name == "fetch" else a[1])
                return result

            return run

        real_dirs, real_locale, real_log = files.plugin_dirs, store.read_locale, store.Log
        files.plugin_dirs = lambda host, pid: (seen.setdefault("dirs", []).append(pid), real_dirs(host, pid))[1]
        store.read_locale = lambda path, lang: (seen.setdefault("locale", []).append(pathlib.Path(path).name), real_locale(path, lang))[1]
        self.addCleanup(setattr, files, "plugin_dirs", real_dirs)
        self.addCleanup(setattr, store, "read_locale", real_locale)
        tok = token_of(fy, "a")
        forged = {"pid": "b", "id": "b"}
        sends = [
            ("fetch", {"url": "https://example.com/"}, mock.patch.object(net, "fetch_plugin", spy("fetch", {"status": 200}))),
            ("files_list", {"root": "data"}, None),
            ("resource", {"root": "data", "path": "x"}, None),
            ("locale", {"lang": "en"}, None),
            ("log", {"level": "info", "msg": "hello"}, None),
            ("target_add", {"target": "store", "kind": "css", "key": "k", "code": "p{}"}, mock.patch.object(targets.Targets, "add", spy("target_add"))),
            ("target_remove", {"target": "store", "key": "k"}, mock.patch.object(targets.Targets, "remove", spy("target_remove"))),
        ]
        self.assertEqual(sorted(m for m, _, _ in sends), sorted(fy.SCOPED))
        for i, (method, arg, patch) in enumerate(sends):
            seen.clear()
            fy.LOGS.clear()
            if patch:
                patch.start()
                self.addCleanup(patch.stop)
            r = await self.call(page, id=i + 1, gen="g", plugin="_core", method=method, arg={**arg, **forged}, t=tok)
            self.assertNotEqual(r, {"error": "not authorized"}, method)
            used = {pid for k, v in seen.items() for pid in v} | set(fy.LOGS)
            self.assertEqual(used, {"a"}, (method, seen, r))

    async def test_a_core_token_cannot_be_used_for_plugin_scoped_methods(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        for i, method in enumerate(sorted(fy.SCOPED)):
            r = await self.call(page, id=i + 1, gen="g", plugin="_core", method=method, arg={"pid": "a", "id": "a", "root": "data", "lang": "en", "msg": "x"}, t=fy.CORE_TOK)
            self.assertEqual(r, {"error": "not authorized"}, method)
        self.assertEqual(fy.authorize({"method": "state_get", "t": fy.CORE_TOK, "arg": None}), (True, None))

    async def test_a_plugin_token_cannot_log_or_target_for_another_plugin(self):
        loader = await self.start()
        fy = loader.fy
        fy.plugin_args(fy.manifest(fy.PLUGINS / "a"))
        ok, arg = fy.authorize({"method": "log", "t": token_of(fy, "a"), "arg": {"pid": "b", "id": "b", "msg": "x"}})
        self.assertEqual((ok, arg["pid"], arg["id"]), (True, "a", "a"))
        self.assertEqual(fy.authorize({"method": "install", "t": token_of(fy, "a"), "arg": "u"})[0], False)

    async def test_the_bar_can_still_toggle_without_a_token(self):
        loader = await self.start()
        main, _ = await self.attach(loader)
        bar, _ = await self.attach(loader, "bar")
        bar.bind(id=0, plugin="_core", method="toggle", t=None)
        self.assertTrue(await H.settle(lambda: "Framey.toggle()" in main.exprs()))

    async def test_a_backend_call_needs_its_own_plugin_token_or_the_core_token(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        r = await self.call(page, id=1, gen="g", plugin="a", method="hi", arg=3, t=None)
        self.assertEqual(r, {"error": "not authorized"})
        r = await self.call(page, id=2, gen="g", plugin="a", method="uninstall", arg={"id": "a"}, t="0" * 32)
        self.assertEqual(r, {"error": "not authorized"})
        r = await self.call(page, id=3, gen="g", plugin="a", method="hi", arg=3, t="f" * 32)
        self.assertEqual(r, {"error": "not authorized"})
        self.assertTrue((loader.plugins / "a").exists())
        r = await self.call(page, id=4, gen="g", plugin="a", method="hi", arg=3, t=token_of(fy, "a"))
        self.assertEqual(r, {"m": "hi", "a": 3})
        r = await self.call(page, id=5, gen="g", plugin="a", method="hi", arg=3, t=fy.CORE_TOK)
        self.assertEqual(r, {"m": "hi", "a": 3})
        r = await self.call(page, id=6, gen="g", plugin="zzz", method="install", arg="x", t=fy.CORE_TOK)
        self.assertIn("error", r)

    async def test_the_token_is_kept_when_a_plugin_reloads_inside_a_live_page(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        first = token_of(fy, "a")
        self.assertEqual(len(fy.TOKS), 2)
        await loader.core("reload_one", "a")
        await loader.core("reload_one", "a")
        self.assertEqual(token_of(fy, "a"), first)
        self.assertEqual(len(fy.TOKS), 2)
        self.assertEqual(fy.authorize({"method": "log", "t": first, "arg": {}})[0], True)

    async def test_the_token_rotates_on_page_injection_and_on_loader_restart(self):
        loader = await self.start()
        fy = loader.fy
        await self.attach(loader)
        first = token_of(fy, "a")
        fy.main_code()
        second = token_of(fy, "a")
        self.assertNotEqual(first, second)
        self.assertNotIn(first, fy.TOKS)
        self.assertEqual(len(fy.TOKS), 2)
        self.assertEqual(fy.authorize({"method": "log", "t": first, "arg": {}})[0], False)
        other = await self.start()
        await self.attach(other)
        self.assertNotEqual(token_of(other.fy, "a"), second)

    async def test_disabling_a_plugin_drops_its_token_and_enabling_issues_a_new_one(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        first = token_of(fy, "a")
        await loader.core("disable", "a")
        self.assertNotIn("a", fy.TOKS.values())
        await loader.core("enable", "a")
        fy.main_code()
        self.assertNotEqual(token_of(fy, "a"), first)

    async def test_tokens_are_random_per_process_and_32_hex(self):
        loader = await self.start()
        other = H.Loader()
        self.addAsyncCleanup(other.close)
        self.assertRegex(loader.fy.CORE_TOK, r"^[0-9a-f]{32}$")
        self.assertNotEqual(loader.fy.CORE_TOK, other.fy.CORE_TOK)


class PrefsTests(Base):
    async def test_the_page_cannot_widen_privileges_through_prefs(self):
        loader = await self.start()
        fy = loader.fy
        for key, value in (("dev.enabled", True), ("dev.targets", {"x": "http"}), ("dev.hot", ["a"]), ("dev.hot_until", 1_900_000_000), ("file_roots", ["/home/x"]), ("safe", True), ("channel", "prerelease")):
            r = await loader.core("prefs_set", {key: value})
            self.assertEqual(r.get("code"), "not_allowed", key)
        self.assertFalse(fy.dev_on())
        self.assertFalse(fy.safe())
        self.assertEqual(fy.settings()["prefs"]["channel"], "stable")
        self.assertEqual(fy.settings()["prefs"]["file_roots"], [])
        self.assertEqual((await loader.core("target_add", {"pid": "a", "target": "x", "kind": "css", "key": "k", "code": "p{}"}))["code"], "unknown_target")
        self.assertEqual((await loader.core("prefs_set", {"lang": "de", "autocheck": False}))["prefs"]["lang"], "de")

    async def test_old_page_written_values_in_the_state_file_are_ignored(self):
        loader = await self.start()
        fy = loader.fy
        fy.CONF.write_text(json.dumps({"v": 1, "disabled": [], "prefs": {"safe": True, "channel": "prerelease", "file_roots": ["/home/x"], "dev": {"enabled": True, "targets": {"x": "http"}}}}))
        s = fy.settings()["prefs"]
        self.assertEqual((s["safe"], s["channel"], s["file_roots"], s["dev"]["enabled"], s["dev"]["targets"]), (False, "stable", [], False, {}))
        self.assertFalse(fy.safe() or fy.dev_on())
        self.assertEqual((await loader.core("dev_status"))["enabled"], False)

    async def test_the_root_of_trust_files_switch_modes_and_a_reload_picks_them_up(self):
        loader = await self.start()
        fy = loader.fy
        d = fy.CONF.parent
        (d / "developer").write_text("")
        self.assertTrue(fy.dev_on())
        self.assertIs((await loader.core("dev_status"))["enabled"], True)
        (d / "developer").write_text(json.dumps({"targets": {"store": "steamstore", "bad name": "x"}}))
        self.assertEqual(fy.settings()["prefs"]["dev"]["targets"], {})
        (d / "developer").write_text(json.dumps({"targets": {"store": "steamstore"}}))
        self.assertEqual(fy.settings()["prefs"]["dev"]["targets"], {"store": "steamstore"})
        (d / "safe").write_text("")
        self.assertTrue(fy.safe())
        self.assertIs((await loader.core("state_get"))["safe"], True)
        self.assertIs((await loader.core("state_get"))["prefs"]["safe"], True)
        (d / "safe").unlink()
        (d / "developer").unlink()
        self.assertFalse(fy.dev_on() or fy.safe())

    async def test_loader_json_is_shape_validated(self):
        loader = await self.start()
        fy = loader.fy
        cfg = fy.CONF.parent / "loader.json"
        cfg.write_text(json.dumps({"channel": "prerelease", "file_roots": ["/home/x/media", "/etc", "..", 5]}))
        p = fy.settings()["prefs"]
        self.assertEqual((p["channel"], p["file_roots"]), ("prerelease", []))
        cfg.write_text(json.dumps({"file_roots": ["/home/x/media", "/mnt/disk"]}))
        self.assertEqual(fy.settings()["prefs"]["file_roots"], ["/home/x/media", "/mnt/disk"])
        cfg.write_text(json.dumps({"channel": "prerelease", "file_roots": ["/home/x/media", "/etc", "..", 5]}))
        for bad in ('{"channel": "nightly", "file_roots": "x"}', "[1]", "nonsense", '{"channel": NaN}', "{" * 5000, json.dumps({"channel": "prerelease", "pad": "x" * 5000})):
            cfg.write_text(bad)
            p = fy.settings()["prefs"]
            self.assertEqual((p["channel"], p["file_roots"]), ("stable", []), bad[:20])
        cfg.unlink()

    async def test_the_extra_dev_watch_follows_the_file_after_a_reload(self):
        loader = await self.start()
        fy = loader.fy

        async def tabs():
            return []

        fy.tabs = tabs
        d = fy.CONF.parent
        (d / "developer").write_text(json.dumps({"targets": {"store": "steamstore"}}))
        await loader.core("target_add", {"pid": "a", "target": "store", "kind": "css", "key": "k", "code": "p{}"})
        self.assertEqual(list(fy.WATCH), ["store"])
        (d / "developer").unlink()
        await loader.core("reload")
        self.assertEqual(fy.WATCH, {})


class InputTests(Base):
    async def test_non_finite_numbers_and_deep_nesting_in_page_json_are_rejected(self):
        fy = (await self.start()).fy
        for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"arg": {"timeout": NaN}}', "[" * 200000 + "]" * 200000):
            self.assertEqual(fy.parse(text), {}, text[:20])
        self.assertEqual(fy.parse('{"a": 1.5}'), {"a": 1.5})

    async def test_a_nan_payload_through_the_binding_never_reaches_a_method(self):
        loader = await self.start()
        page, task = await self.attach(loader)
        page.q.put_nowait(json.dumps({"method": "Runtime.bindingCalled", "params": {"payload": '{"id": 1, "gen": "g", "plugin": "_core", "method": "fetch", "t": "x", "arg": {"timeout": NaN}}'}}))
        for i, bad in enumerate(("NaN", "Infinity", "-Infinity", "[1, NaN]")):
            page.q.put_nowait(json.dumps({"method": "Runtime.bindingCalled", "params": {"payload": '{"id": %d, "gen": "g", "plugin": "_core", "method": "state_get", "t": "%s", "arg": {"x": %s}}' % (i + 2, loader.fy.CORE_TOK, bad)}}))
        await asyncio.sleep(0.2)
        self.assertEqual(page.replies(), [])
        self.assertFalse(task.done())
        r = await self.call(page, id=9, gen="g", plugin="_core", method="state_get", arg=None, t=loader.fy.CORE_TOK)
        self.assertIn("order", r)


class InstallTests(Base):
    async def test_install_refuses_loopback_private_and_link_local_before_connecting(self):
        loader = await self.start()
        fy = loader.fy
        for url in ("https://127.0.0.1:8080/x.zip", "https://[::1]/x.zip", "https://10.0.0.5/x.zip", "https://192.168.1.2/x.zip", "https://169.254.169.254/x.zip", "https://[fec0::1]/x.zip", "https://localhost/x.zip"):
            r = await loader.core("install", url)
            self.assertIn("error", r, url)
        self.assertEqual(loader.net.calls, [])
        self.assertEqual(await loader.core("install", "http://example.com/x.zip"), {"error": "https links only"})

    async def test_install_goes_ahead_for_a_public_address(self):
        loader = await self.start()
        fy = loader.fy
        zipped = H.mkzip({"plugin.json": json.dumps({"id": "n", "name": "N", "version": "1.0.0", "short": "n"}), "main.js": "1"})
        loader.net.routes["https://example.com/x.zip"] = zipped
        with mock.patch("fy_net._gai", lambda h, p: ["93.184.216.34"]):
            self.assertEqual(await loader.core("install", "https://example.com/x.zip"), {"ok": "n"})

    async def test_a_redirect_to_a_private_address_is_refused(self):
        loader = await self.start()
        fy = loader.fy
        fy.use("fy_net")
        import urllib.request
        handler = None

        class Fake(urllib.request.HTTPRedirectHandler):
            pass

        with mock.patch("fy_net._gai", lambda h, p: ["10.0.0.1"]):
            with self.assertRaises(Exception) as ctx:
                fy.addr_check("https://redirect.example/x")
        self.assertEqual(getattr(ctx.exception, "code", ""), "blocked_address")

    async def test_zip_entries_with_empty_or_dot_components_are_refused(self):
        fy = (await self.start()).fy
        meta = json.dumps({"id": "n", "name": "N", "version": "1.0.0", "short": "n"})
        for name in ("repo-abc//fyr_probe/pwned.txt", "repo-abc/./x.txt", "repo-abc/../x.txt", "//abs/x.txt"):
            data = H.mkzip({"repo-abc/plugin.json": meta, name: "x"})
            self.assertEqual(fy.vet(data), {"error": "zip has unsafe paths or too many files"}, name)
        self.assertIsInstance(fy.vet(H.mkzip({"repo-abc/plugin.json": meta, "repo-abc/sub/x.txt": "x"})), tuple)

    async def test_unpack_never_writes_outside_the_stage(self):
        fy = (await self.start()).fy
        stage = fy.PLUGINS / ".stage-z"
        data = H.mkzip({"repo/plugin.json": "{}", "repo//escape/x.txt": "x"})
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = {i.filename: i for i in z.infolist()}
        with self.assertRaises(ValueError):
            fy.unpack(z, infos, "repo/")(stage)
        self.assertFalse(pathlib.Path("/escape").exists())


class LoaderUpdateTests(U.Base):
    def serve_pkg(self, loader, files, sha="b" * 40):
        loader.net.json("chaosfox26/framey/releases/latest", {"tag_name": "v2.0.0", "assets": []})
        loader.net.commit("chaosfox26", "framey", "v2.0.0", sha)
        loader.net.archive("chaosfox26", "framey", sha, H.mkzip(files, top="framey-" + sha))

    def full(self):
        return H.full_tree()

    async def test_an_empty_or_import_broken_framey_py_is_refused_and_nothing_is_swapped(self):
        loader = await self.start()
        for body in ("", "import not_a_real_module_zz\n", "VERSION = '9'\n", "VERSION = '9'\nasync def main():\n    raise SystemExit\nraise SystemExit(0)\n"):
            full = self.full()
            full["framey.py"] = body
            self.serve_pkg(loader, full)
            r = await loader.core("loader_update")
            self.assertIn("Framey update not applied", r["error"], body)
            self.assertFalse(loader.root.with_name(loader.root.name + ".bak").exists())
            self.assertFalse(loader.root.with_name(loader.root.name + ".new").exists())

    async def test_a_healthy_package_is_still_accepted(self):
        loader = await self.start()
        self.serve_pkg(loader, self.full())
        r = await loader.core("loader_update")
        self.assertEqual(r, {"ok": "framey", "tag": "v2.0.0", "restart": True})

    async def test_ver_install_of_the_loader_restarts_instead_of_refreshing(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach_page(loader)
        calls = []
        fy.restart = lambda: calls.append("restart")

        async def fake(method, arg):
            return {"ok": "framey", "tag": "v2.0.0", "restart": True}

        fy.core = fake
        n = len(page.exprs())
        page.bind(id=1, gen="g", plugin="_core", method="ver_install", arg={"id": "_loader", "tag": "v2.0.0"})
        self.assertTrue(await H.settle(lambda: calls))
        self.assertFalse(any("delete window.Framey" in e for e in page.exprs()[n:]))

    async def attach_page(self, loader):
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        return page, task

    async def test_the_new_tree_is_flushed_before_the_directories_swap(self):
        loader = await self.start()
        fy = loader.fy
        seen = []
        self.serve(loader, "1.1.0", U.NEW)
        with mock.patch("os.sync", lambda: seen.append(((loader.plugins / "a").exists(), (loader.plugins / ".old-a").exists())), create=True):
            self.assertEqual((await loader.core("update", "a"))["ok"], "a")
        self.assertEqual(seen, [(True, False)])
        seen.clear()
        self.serve_pkg(loader, self.full())

        async def flush():
            seen.append((loader.root.exists(), loader.root.with_name(loader.root.name + ".bak").exists()))

        fy.sync_disk = flush
        await loader.core("loader_update")
        self.assertEqual(seen, [(True, False)])


class BackendStartTests(U.Base):
    def bad_package(self, loader, version, sha):
        loader.net.commit("o", "a", "HEAD", sha)
        files = {"plugin.json": U.meta(version), "main.js": "NEW-" + version, "backend.py": (U.HOOKED % (str(self.log), version)).replace("def startup(ctx):", "def startup(ctx):\n    raise RuntimeError('boom')\n\n\ndef unused(ctx):"), "data.txt": "d"}
        loader.net.archive("o", "a", sha, H.mkzip(files, top="a-" + sha))
        import base64
        loader.net.json("o/a/contents/plugin.json?ref=" + sha, {"content": base64.b64encode(U.meta(version).encode()).decode()})

    async def test_a_new_backend_that_cannot_start_is_rolled_back_and_reported(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual(await fy.backend("a", "x", 1), {"version": "1.0.0"})
        self.bad_package(loader, "1.1.0", U.NEW)
        r = await loader.core("update", "a")
        self.assertIn("could not start", r["error"])
        self.assertIn("previous version was restored", r["error"])
        self.assertIn("boom", r["error"])
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        self.assertEqual((loader.dir / ".version").read_text(), U.OLD)
        self.assertEqual(self.leftovers(loader), [])
        self.assertEqual(await fy.backend("a", "x", 1), {"version": "1.0.0"})

    async def test_the_updates_module_rolls_back_too_and_names_the_stage(self):
        loader = await self.start()
        fy = loader.fy
        self.bad_package(loader, "1.1.0", U.NEW)
        loader.net.routes["https://api.github.com/repos/o/a/commits/v1.1.0"] = U.NEW.encode()
        r = await loader.core("ver_install", {"id": "a", "tag": "v1.1.0", "confirm": True})
        self.assertEqual(r.get("stage"), "reload", r)
        self.assertIn("previous version was restored", r["error"])
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        self.assertEqual(self.leftovers(loader), [])

    async def test_a_good_update_keeps_the_previous_copy_after_the_start(self):
        loader = await self.start()
        self.serve(loader, "1.1.0", U.NEW)
        self.assertEqual((await loader.core("update", "a"))["ok"], "a")
        self.assertEqual(self.leftovers(loader), [".old-a"])
        self.assertIn("startup", self.lines())


class DowngradeTests(U.Base):
    async def test_an_older_tag_needs_confirm_even_when_the_manifest_forgot_to_bump(self):
        loader = await self.start(version="2.0.0")
        self.serve(loader, "2.0.0", U.NEW, tag="v1.0.0")
        r = await loader.core("ver_install", {"id": "a", "tag": "v1.0.0"})
        self.assertIn("confirm", r)
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        r = await loader.core("ver_install", {"id": "a", "tag": "v1.0.0", "confirm": True})
        self.assertEqual(r["ok"], "a")

    async def test_a_manifest_that_cannot_be_compared_needs_confirm(self):
        loader = await self.start(version="2.0.0")
        self.serve(loader, "weird-build", U.NEW, tag="v3.0.0")
        r = await loader.core("ver_install", {"id": "a", "tag": "v3.0.0"})
        self.assertIn("confirm", r)

    async def test_a_newer_version_and_an_unbumped_commit_do_not_ask(self):
        loader = await self.start(version="1.0.0")
        self.serve(loader, "1.0.0", U.NEW)
        self.assertEqual((await loader.core("update", "a"))["ok"], "a")

    async def test_the_loader_cannot_be_pinned_to_a_raw_commit(self):
        loader = await self.start()
        r = await loader.core("ver_install", {"id": "_loader", "tag": "c" * 40})
        self.assertIn("error", r)
        self.assertEqual(r["code"], "bad_tag")


class LinkedFolderTests(U.Base):
    async def test_update_all_skips_a_linked_working_tree(self):
        loader = await self.start()
        fy = loader.fy
        work = loader.home / "work-a"
        shutil_copy = __import__("shutil").copytree
        shutil_copy(loader.dir, work)
        __import__("shutil").rmtree(loader.dir)
        try:
            os.symlink(work, loader.dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need a privilege on this account")
        self.serve(loader, "1.1.0", U.NEW)
        plan = await loader.core("batch_plan", {"ids": ["a"]}) if False else await loader.core("batch_plan")
        self.assertEqual(plan["items"][0]["reason"], "linked folder")
        self.assertFalse(plan["items"][0]["eligible"])
        r = await loader.core("batch_apply", {"ids": ["a"]})
        self.assertEqual(r["results"][0]["state"], "skipped")
        self.assertEqual(r["results"][0]["reason"], "linked folder")
        self.assertEqual((work / "main.js").read_text(), "OLD")
        self.assertTrue(loader.dir.is_symlink())


class LockTests(Base):
    async def test_edit_methods_hold_the_lock_while_they_run(self):
        loader = await self.start()
        fy = loader.fy
        gate, inside = asyncio.Event(), asyncio.Event()
        real = fy.reload

        async def slow(only=None, reason="reload"):
            inside.set()
            await gate.wait()
            await real(only, reason)

        fy.reload = slow
        fy.LOCK = None
        first = asyncio.create_task(fy.core("disable", "a"))
        await inside.wait()
        self.assertTrue(fy.LOCK.locked())
        self.assertEqual(await fy.core("disable", "b"), fy.BUSY)
        self.assertEqual(await fy.core("update", "a"), fy.BUSY)
        gate.set()
        self.assertEqual(await first, {"ok": True})
        self.assertFalse(fy.LOCK.locked())


class UpdateResultTests(U.Base):
    async def test_an_update_keeps_the_page_and_emits_one_update_result_event(self):
        loader = await self.start()
        fy = loader.fy
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        n = len(page.sent)
        self.serve(loader, "1.1.0", U.NEW)
        page.bind(id=1, gen="g", plugin="_core", method="update", arg="a")
        self.assertTrue(await H.settle(lambda: any(x[0] == "event" and x[1][1] == "update_result" for x in page.trace(n) if isinstance(x, tuple))))
        sent = page.trace(n)
        self.assertFalse(any(isinstance(x, str) and ("delete window.Framey" in x or x.startswith("typeof window.Framey")) for x in sent))
        ev = [x[1] for x in sent if isinstance(x, tuple) and x[0] == "event" and x[1][1] == "update_result"]
        self.assertEqual(len(ev), 1)
        data = ev[0][2]
        self.assertEqual((data["method"], data["id"], data["ok"], data["failed"]), ("update", "a", 1, 0))
        self.assertEqual(data["results"], [{"id": "a", "state": "ok", "to": "1.1.0"}])
        self.assertIn(("dispose", ["a"]), sent)
        self.assertTrue(any(isinstance(x, str) and x.startswith("(function(Framey){") for x in sent))
        self.assertFalse(any(x == "window.Framey&&Framey.redraw()" for x in sent))
        self.assertLess(next(i for i, x in enumerate(sent) if isinstance(x, tuple) and x[0] == "reply"), next(i for i, x in enumerate(sent) if isinstance(x, tuple) and x[0] == "event" and x[1][1] == "update_result"))

    async def test_a_failed_update_still_emits_the_event_with_the_error(self):
        loader = await self.start()
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        n = len(page.sent)
        page.bind(id=1, gen="g", plugin="_core", method="update", arg="a")
        self.assertTrue(await H.settle(lambda: any(isinstance(x, tuple) and x[0] == "event" and x[1][1] == "update_result" for x in page.trace(n))))
        ev = next(x[1] for x in page.trace(n) if isinstance(x, tuple) and x[0] == "event" and x[1][1] == "update_result")
        self.assertEqual(ev[2]["results"][0]["state"], "failed")

    async def test_batch_failures_travel_in_the_event(self):
        fy = (await self.start()).fy
        out = {"results": [{"id": "a", "state": "ok", "to": "2"}, {"id": "b", "state": "failed", "error": "x"}], "ok": 1, "failed": 1}
        data = fy.update_event("batch_apply", {"ids": ["a", "b"]}, out)
        self.assertEqual((data["ok"], data["failed"], data["id"]), (1, 1, None))
        self.assertEqual(data["results"], out["results"])


class TokenHygieneTests(Base):
    async def exprs(self, loader):
        fy = loader.fy
        page, _ = await self.attach(loader)
        await loader.core("reload_one", "a")
        loader.plugin("c", {"api": 1}, main="throw new Error('bad plugin')")
        await loader.core("reload_one", "c")
        return fy, page

    async def test_no_token_appears_outside_the_lexical_wrappers(self):
        loader = await self.start()
        fy, page = await self.exprs(loader)
        core, plug = fy.CORE_TOK, set(fy.TOKS)
        wrapped = 0
        for e in page.exprs():
            if core in e:
                wrapped += 1
                self.assertTrue(e.startswith("(function(__fyt){") and e.endswith('})("%s")' % core), e[:80])
                self.assertEqual(e.count(core), 1)
            for t in plug:
                if t in e:
                    self.assertRegex(e, r'\}\)\(__bindFramey\("[a-z0-9_-]+","%s"\)\)$' % t)
                    self.assertEqual(e.count(t), 1)
        self.assertGreaterEqual(wrapped, 1)
        for e in fy.bar_code() + fy.replay("bar"):
            self.assertNotIn(core, e[0] if isinstance(e, tuple) else e)

    async def test_a_plugin_that_throws_at_load_is_recorded_from_the_evaluate_result(self):
        loader = await self.start()
        fy, page = await self.exprs(loader)
        sent = [o for o in page.sent if o["method"] == "Runtime.evaluate" and "bad plugin" in o["params"]["expression"]]
        self.assertEqual(len(sent), 1)
        self.assertNotIn("var q", sent[0]["params"]["expression"])
        self.assertNotIn("fyCall", sent[0]["params"]["expression"])
        page.q.put_nowait(json.dumps({"id": sent[0]["id"], "result": {"exceptionDetails": {"text": "Uncaught", "exception": {"description": "Error: bad plugin\n    at x"}}}}))
        self.assertTrue(await H.settle(lambda: "c" in fy.FAILS))
        self.assertEqual((fy.FAILS["c"]["what"], fy.FAILS["c"]["msg"]), ("load", "Error: bad plugin"))
        self.assertTrue(await H.settle(lambda: any(e.startswith('Framey.fail("c"') for e in page.exprs())))

    async def test_the_generated_expressions_leave_no_token_in_any_global(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        loader = await self.start()
        fy, page = await self.exprs(loader)
        exprs = [e for e in page.exprs() if not e.startswith("typeof")]
        script = """
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const ctx = vm.createContext({});
vm.runInContext('var window = this; var Framey = {dispose: function () {}, redraw: function () {}, _event: function () {}, _reply: function () {}, restore: function () {}, fail: function () {}}; var __bindFramey = function (p, t) { return {register: function () {}}; }; var document = undefined;', ctx);
for (const e of input.exprs) { try { vm.runInContext(e, ctx); } catch (err) {} }
const seen = [];
for (const k of Object.getOwnPropertyNames(ctx)) {
  let v; try { v = JSON.stringify(ctx[k], (kk, x) => typeof x === 'function' ? String(x) : x) || ''; } catch (err) { v = ''; }
  seen.push(k + '=' + v);
}
const hit = input.tokens.filter(t => seen.some(s => s.includes(t)));
let q = 'none'; try { q = vm.runInContext('typeof q', ctx); } catch (err) {}
console.log(JSON.stringify({hit, q, keys: Object.getOwnPropertyNames(ctx)}));
"""
        tmp = loader.base / "node"
        tmp.mkdir()
        (tmp / "check.js").write_text(script)
        (tmp / "in.json").write_text(json.dumps({"exprs": exprs, "tokens": [fy.CORE_TOK] + list(fy.TOKS)}))
        out = subprocess.run([node, str(tmp / "check.js"), str(tmp / "in.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["hit"], [])
        self.assertEqual(got["q"], "undefined")
        self.assertNotIn("q", got["keys"])


class AssetTests(Base):
    async def test_the_asset_method_is_a_no_op_and_the_picker_ships_in_the_main_wrapper(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        n = len(page.sent)
        self.assertEqual(await self.call(page, id=1, gen="g", plugin="_core", method="asset", arg={"name": "picker"}, t=fy.CORE_TOK), {"ok": True})
        self.assertEqual([x for x in page.trace(n) if isinstance(x, str)], [])
        self.assertIn((fy.ROOT / "fy-picker.js").read_text(), next(e for e in page.exprs() if e.startswith("(function(__fyt){")))
        self.assertNotIn(fy.CORE_TOK, json.dumps(page.replies()))

    async def test_the_developer_file_adds_the_dev_module_and_plugins_cannot_ask(self):
        loader = await self.start()
        fy = loader.fy
        page, _ = await self.attach(loader)
        dev = (fy.ROOT / "fy-dev.js").read_text()
        self.assertNotIn(dev, fy.main_code()[0][0])
        (fy.CONF.parent / "developer").write_text("")
        self.assertIn(dev, fy.main_code()[0][0])
        fy.plugin_args(fy.manifest(fy.PLUGINS / "a"))
        self.assertEqual(await self.call(page, id=3, gen="g", plugin="_core", method="asset", arg={"name": "picker"}, t=token_of(fy, "a")), {"error": "not authorized"})


class ToggleAndInputTests(Base):
    async def test_toggle_is_limited_to_five_per_second(self):
        loader = await self.start()
        page, _ = await self.attach(loader)
        n = len(page.exprs())
        for _ in range(8):
            await loader.core("toggle")
        self.assertEqual(sum("Framey.toggle()" in e for e in page.exprs()[n:]), 5)

    async def test_the_panel_reports_untrusted_clicks_and_the_setting_reaches_the_page(self):
        loader = await self.start()
        fy = loader.fy
        loader.trust(trusted_input="require", **{"dev.enabled": True})
        self.assertEqual(json.loads(fy.prelude().split("window.FY_RUNTIME=", 1)[1].split(";", 1)[0])["trusted_input"], "require")
        page, _ = await self.attach(loader)
        self.assertEqual(await self.call(page, id=1, gen="g", plugin="_core", method="panel", arg={"open": True, "untrusted_clicks": 3}, t=fy.CORE_TOK), None)
        self.assertEqual((await self.call(page, id=2, gen="g", plugin="_core", method="diag", arg=None, t=fy.CORE_TOK)).get("untrusted_clicks"), 3)

    async def test_plugin_file_listing_is_limited_to_its_data_and_the_picked_roots(self):
        loader = await self.start()
        fy = loader.fy
        fy.plugin_args(fy.manifest(fy.PLUGINS / "a"))
        tok = token_of(fy, "a")
        self.assertEqual(fy.authorize({"method": "files_list", "t": tok, "arg": {"root": "home"}})[0], False)
        self.assertEqual(fy.authorize({"method": "files_list", "t": tok, "arg": {"root": "media"}})[0], False)
        self.assertEqual(fy.authorize({"method": "files_list", "t": tok, "arg": {}})[0], True)
        self.assertEqual(fy.authorize({"method": "files_list", "t": tok, "arg": {"root": "data"}})[0], True)


class ClockTests(Base):
    async def test_a_stored_time_in_the_future_does_not_disable_the_auto_check_for_ever(self):
        loader = await self.start(plugins=())
        fy = loader.fy
        up = fy.upd()
        state = {"prefs": {"autocheck": True}, "updates": {"last": 10**12, "next_ok": 10**12}}
        self.assertTrue(up.due(1_800_000_000, 86400, state))
        self.assertFalse(up.due(1_800_000_000, 86400, {"prefs": {"autocheck": True}, "updates": {"last": 1_800_000_000 - 60, "next_ok": 1_800_000_000 + 600}}))
        self.assertTrue(up.due(1_800_000_000 + 7 * 86400, 86400, {"prefs": {"autocheck": True}, "updates": {"last": 0, "next_ok": 0}}))


class SmallFixTests(Base):
    async def test_the_overlay_key_is_compared_as_a_query_value_not_as_a_substring(self):
        fy = (await self.start()).fy
        main, bar = "valve.steam.gamepadui.main", "valve.steam.gamepadui.bar"
        yes = ["https://steamloopback.host/routes/?vrOverlayKey=valve.steam.gamepadui.main&x=1", "https://steamloopback.host/?a=1&vrOverlayKey=valve.steam.gamepadui.main", "https://steamloopback.host/#/x?vrOverlayKey=valve.steam.gamepadui.main"]
        no = ["https://evil.example/vrOverlayKey=valve.steam.gamepadui.main", "https://evil.example/?x=vrOverlayKey=valve.steam.gamepadui.main", "https://evil.example/?vrOverlayKey=valve.steam.gamepadui.main.evil", "https://evil.example/?vrOverlayKey=xvalve.steam.gamepadui.main", "https://evil.example/?vrOverlayKey=valve.steam.gamepadui.bar", "https://evil.example/?vrOverlayKey=a&vrOverlayKey=valve.steam.gamepadui.main", "https://evil.example/?vrOverlayKey=", "about:blank", "", "https://evil.example/?q=1#vrOverlayKey=valve.steam.gamepadui.main"]
        for u in yes:
            self.assertTrue(fy.overlay_key(u, main), u)
            self.assertFalse(fy.overlay_key(u, bar), u)
        for u in no:
            self.assertFalse(fy.overlay_key(u, main), u)
        self.assertTrue(fy.overlay_key("https://steamloopback.host/?vrOverlayKey=valve.steam.gamepadui.bar", bar))

    async def test_watch_attaches_to_the_real_tab_and_ignores_a_decoy(self):
        fy = (await self.start()).fy
        chosen = []

        async def tabs():
            return [{"url": "https://evil.example/?x=vrOverlayKey=valve.steam.gamepadui.main"}, {"url": "https://steamloopback.host/?vrOverlayKey=valve.steam.gamepadui.main"}]

        async def session(name, tab, code, probe, marker=None):
            chosen.append(tab["url"])
            raise asyncio.CancelledError

        with mock.patch.object(fy, "tabs", tabs), mock.patch.object(fy, "session", session):
            with self.assertRaises(asyncio.CancelledError):
                await fy.watch("main", lambda u: fy.overlay_key(u, "valve.steam.gamepadui.main"), None, "x")
        self.assertEqual(chosen, ["https://steamloopback.host/?vrOverlayKey=valve.steam.gamepadui.main"])

    async def test_one_version_parser_serves_the_loader_and_the_updater(self):
        import fy_pkg
        fy = (await self.start()).fy
        up = fy.use("fy_updates")
        for text in ("1.2.3", "v1.2", "1.0.0-rc.1", "1.0.0+7", "1.0.0.0.0", "release-1", "", None, 5, "1" * 30):
            a, b = up.parse_version(text), fy_pkg.parse_version(text)
            self.assertEqual((a is None, a), (b is None, b), repr(text))
        self.assertIsNone(up.parse_version("1.0.0.0.0"))
        self.assertIsNotNone(up.parse_version("1.0.0+7"))
        self.assertEqual(fy.vkey("v1.2"), (1, 2, 0, 0))
        self.assertEqual(fy.vkey("1.2.3.4"), (1, 2, 3, 4))
        for text in ("1.0.0-rc.1", "x", "", None, 5, "1.2.3.4.5"):
            self.assertIsNone(fy.vkey(text), repr(text))
        self.assertTrue(fy.vkey("1.10.0") > fy.vkey("1.9.0") > fy.ZERO)

    async def test_without_the_package_module_versions_do_not_parse_and_nothing_crashes(self):
        loader = await self.start()
        fy = loader.fy
        with mock.patch.dict(sys.modules, {"fy_pkg": None}):
            fy.MODS.pop("fy_pkg", None)
            self.assertIsNone(fy.vkey("1.2.3"))
            self.assertIsNone(fy.use("fy_updates").parse_version("1.2.3"))
            self.assertFalse(fy.use("fy_updates").is_pre("1.0.0-rc.1"))

    async def test_an_unreadable_settings_file_injects_no_plugin_code(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual({p for _, p in fy.main_code() if p in ("a", "b")}, {"a", "b"})
        fy.CONF.unlink(missing_ok=True)
        fy.CONF.mkdir()
        self.assertEqual({p for _, p in fy.main_code() if p in ("a", "b")}, set())
        self.assertIn("#core", {p for _, p in fy.main_code()})

    async def test_token_comparison_is_exact_and_length_aware(self):
        fy = (await self.start()).fy
        self.assertTrue(fy.same("abc", "abc"))
        self.assertTrue(fy.same("", ""))
        for a, b in (("abc", "abd"), ("ab", "abc"), ("abc", "ab"), ("", "a")):
            self.assertFalse(fy.same(a, b), (a, b))

    async def test_windows_machine_is_read_from_the_environment(self):
        import builtins
        fy = (await self.start()).fy
        real = builtins.hasattr
        with mock.patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "ARM64"}), mock.patch.object(builtins, "hasattr", lambda o, n: False if o is os and n == "uname" else real(o, n)):
            self.assertEqual(fy.platform_name(), "linux-aarch64")

    async def test_a_missing_icon_still_gives_the_panel_and_bar_a_default_glyph(self):
        loader = await self.start()
        fy = loader.fy
        (fy.ROOT / "icon.svg").unlink()
        self.assertIn("#core", {p for _, p in fy.main_code()})
        self.assertTrue(fy.bar_code()[0][0].startswith("window.FY_ICON_URL=\"data:image/svg+xml,%3Csvg"))

    async def test_shutdown_fits_inside_the_stop_bound_and_is_cut_at_six_seconds(self):
        fy = (await self.start()).fy
        seen = []

        class Slow:
            async def shutdown(self, total=4.0):
                seen.append(total)
                await asyncio.sleep(60)

        loop = asyncio.get_running_loop()
        began = loop.time()
        with mock.patch.object(fy, "supervisor", Slow()):
            with self.assertRaises(asyncio.TimeoutError):
                await fy.down()
        self.assertEqual(seen, [1.5])
        self.assertLess(loop.time() - began, 7)


class UninstallTests(Base):
    async def test_the_hook_sees_the_real_purge_flag_and_data_survives_until_code_is_gone(self):
        loader = await self.start(plugins=())
        log = loader.base / "u.log"
        loader.plugin("a", {"api": 1}, main=None, backend="import os\nLOG = %r\n\n\ndef uninstall(ctx):\n    open(LOG, 'a').write('purge=%%s data=%%s\\n' %% (ctx.purge, os.path.isdir(%r)))\n\n\nasync def call(m, a):\n    return 1\n" % (str(log), str(loader.fy.dirs("a").data)))
        fy = loader.fy
        fy.load_backends()
        fy.dirs("a").data.mkdir(parents=True, exist_ok=True)
        self.assertEqual(await fy.backend("a", "x", 1), 1)
        self.assertEqual(await loader.core("uninstall", {"id": "a", "purge": True}), {"ok": True})
        self.assertEqual(log.read_text().strip(), "purge=True data=True")
        self.assertFalse(fy.dirs("a").data.exists())

    async def test_a_failed_purge_is_reported_in_the_reply(self):
        loader = await self.start(plugins=("a",))
        fy = loader.fy
        with mock.patch.object(fy, "purge", lambda pid: {"removed": [], "failed": [{"name": "data", "reason": "error"}]}):
            r = await loader.core("uninstall", {"id": "a", "purge": True})
        self.assertEqual(r, {"ok": True, "purge_failed": [{"name": "data", "reason": "error"}]})


if __name__ == "__main__":
    unittest.main()
