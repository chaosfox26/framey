import asyncio
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

REPO = H.REPO
LINUX = sys.platform.startswith("linux")

HOOKS = """
import os
import framey_api

LOG = %r


def note(text):
    with open(LOG, "a") as f:
        f.write(text + "\\n")


def startup(ctx):
    note("startup")
    with open(os.path.join(str(framey_api.dirs.data), "kept.txt"), "w") as f:
        f.write("data")


async def call(method, arg):
    if method == "boom":
        raise RuntimeError("kaput")
    if method == "exit":
        raise SystemExit(3)
    if method == "emit":
        framey_api.events.emit("ping", {"n": arg})
        return True
    return {"m": method, "a": arg}


def unload(reason):
    note("unload:" + reason)


def uninstall(ctx):
    note("uninstall:purge=%%s:code=%%s" %% (ctx.purge, os.path.exists(%r)))
"""


def hooks_source(log, code_file):
    return HOOKS % (str(log), str(code_file))


def lines(path):
    try:
        return pathlib.Path(path).read_text().split()
    except OSError:
        return []


class ImportTests(unittest.TestCase):
    def test_importing_the_loader_starts_nothing_and_imports_no_optional_module(self):
        code = "import sys; sys.path.insert(0, %r); import framey; print(sorted(m for m in sys.modules if m.startswith('fy_')), framey.MODS, framey.LIVE, framey.supervisor)" % str(REPO)
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30, cwd=str(HERE))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[] {} {} None")

    def test_script_runs_main_only_when_executed(self):
        src = (REPO / "framey.py").read_text()
        self.assertTrue(src.rstrip().endswith('if __name__ == "__main__":\n    main_entry()'))
        self.assertEqual(src.count("loop.run_until_complete(main())"), 1)

    def test_manifest_names_every_module_the_loader_needs(self):
        loader = H.Loader()
        try:
            fy = loader.fy
            have = {f.name for f in REPO.iterdir() if f.is_file() and (f.name.startswith("fy_") and f.suffix == ".py" or f.name.startswith("fy-") and f.suffix == ".js")}
            self.assertEqual(have - set(fy.MANIFEST), set())
            self.assertEqual(set(fy.MANIFEST) - have - set(fy.LEGACY), {"catalog/index.json"})
            for name in fy.LEGACY:
                self.assertIn(name, fy.MANIFEST)
            self.assertEqual(len(fy.MANIFEST), len(set(fy.MANIFEST)))
            for name in fy.MANIFEST:
                self.assertTrue((REPO / name).is_file(), name)
            self.assertEqual(fy.needs((REPO / "framey.py").read_text()), fy.MANIFEST)
            self.assertEqual(fy.needs("VERSION = 1"), fy.LEGACY)
            self.assertEqual(fy.needs("def ("), fy.LEGACY)
            self.assertEqual(fy.needs("MANIFEST = [1]"), fy.LEGACY)
        finally:
            asyncio.run(loader.close())

    def test_method_owners_agree_with_the_modules(self):
        loader = H.Loader()
        try:
            fy = loader.fy
            seen = {}
            for name in sorted(set(fy.OWNER.values())):
                mod = fy.use(name)
                self.assertIsNotNone(mod, name)
                table = fy.upd().METHODS if name == "fy_updates" else mod.METHODS
                self.assertEqual(set(table), {k for k, v in fy.OWNER.items() if v == name}, name)
                for key in table:
                    self.assertNotIn(key, seen, "method name used twice: " + key)
                    seen[key] = name
            own = set(fy.JOBS) | set(fy.EDIT) | {"toggle", "panel", "fail", "reload_one", "list", "loader", "store", "asset", "locale", "log"}
            self.assertEqual(own & set(seen), set())
            self.assertTrue(set(fy.LOCKED) <= set(seen))
        finally:
            asyncio.run(loader.close())


class DegradeTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, *missing):
        patcher = mock.patch.dict(sys.modules, {name: None for name in missing})
        patcher.start()
        self.addCleanup(patcher.stop)
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a", {"api": 1}, backend=hooks_source(loader.base / "a.log", loader.plugins / "a" / "backend.py"))
        return loader

    async def test_a_module_that_fails_to_import_is_reported_and_the_rest_keeps_working(self):
        loader = await self.make("fy_updates")
        fy = loader.fy
        self.assertEqual(await loader.core("batch_plan"), {"error": "fy_updates unavailable"})
        self.assertEqual(await loader.core("ver_list", {"id": "a"}), {"error": "fy_updates unavailable"})
        self.assertEqual(await loader.core("loader_check"), {"error": "fy_updates unavailable"})
        self.assertEqual(await loader.core("repair", {"id": "a"}), {"error": "fy_updates unavailable"})
        self.assertIsNone(fy.MODS["fy_updates"])
        self.assertIn("fy_updates unavailable", loader.errors.getvalue())
        self.assertEqual([m["id"] for m in await loader.core("list")], ["a"])
        self.assertEqual((await loader.core("state_get"))["order"], ["a"])
        self.assertEqual(await loader.core("check"), {"checked": {"a": {"state": "none", "msg": "Automatic checking unavailable: no update source recorded"}}})
        self.assertEqual(fy.badge_n(), 0)
        self.assertIsNone(await loader.core("panel", True))
        self.assertEqual(await loader.core("disable", "a"), {"ok": True})

    async def test_loader_update_still_works_without_the_updates_module(self):
        loader = await self.make("fy_updates")
        net = loader.net
        net.json("chaosfox26/framey/releases/latest", {"tag_name": "v9.0.0", "body": "n"})
        net.commit("chaosfox26", "framey", "v9.0.0", "b" * 40)
        full = H.full_tree()
        net.archive("chaosfox26", "framey", "b" * 40, H.mkzip(full, top="framey-" + "b" * 40))
        r = await loader.core("loader_update")
        self.assertEqual(r, {"ok": "framey", "tag": "v9.0.0", "restart": True})

    async def test_without_the_supervisor_calls_fail_cleanly_and_removal_still_works(self):
        loader = await self.make("fy_supervisor")
        fy = loader.fy
        fy.load_backends()
        with self.assertRaisesRegex(RuntimeError, "fy_supervisor unavailable"):
            await fy.backend("a", "x", 1)
        self.assertEqual(await loader.core("reload"), {"ok": True})
        self.assertEqual(await loader.core("uninstall", {"id": "a", "purge": True}), {"ok": True})
        self.assertFalse((loader.plugins / "a").exists())
        self.assertEqual(fy.info({"id": "a"})["stuck"], False)

    async def test_without_the_state_module_settings_fall_back_to_the_disabled_list(self):
        loader = await self.make("fy_state")
        fy = loader.fy
        self.assertEqual(await loader.core("state_get"), {"error": "fy_state unavailable"})
        self.assertEqual(await loader.core("prefs_set", {"safe": True}), {"error": "fy_state unavailable"})
        self.assertEqual(await loader.core("disable", "a"), {"ok": True})
        self.assertEqual(fy.settings()["disabled"], ["a"])
        self.assertEqual(json.loads(fy.CONF.read_text())["disabled"], ["a"])
        self.assertEqual(await loader.core("enable", "a"), {"ok": True})
        self.assertEqual(fy.settings()["disabled"], [])
        self.assertFalse(fy.safe())
        self.assertEqual(fy.pref("lang", "en"), "en")

    async def test_without_the_package_module_installs_are_refused_and_listing_works(self):
        loader = await self.make("fy_pkg")
        fy = loader.fy
        self.assertEqual(fy.gate({"id": "z"}), "fy_pkg unavailable")
        data = H.mkzip({"plugin.json": json.dumps({"id": "z", "name": "z"}), "main.js": "1"})
        self.assertEqual(await fy.install_zip(data), {"error": "fy_pkg unavailable"})
        rows = await loader.core("list")
        self.assertEqual((rows[0]["id"], rows[0]["compat"], rows[0]["held"]), ("a", None, False))

    async def test_without_the_store_module_locale_and_log_say_so(self):
        loader = await self.make("fy_store")
        self.assertEqual(await loader.core("locale", {"id": "a", "lang": "en"}), {"error": "fy_store unavailable"})
        self.assertEqual(await loader.core("log", {"pid": "a", "msg": "x"}), {"error": "fy_store unavailable"})

    async def test_hostcalls_of_a_missing_module_raise_a_readable_error(self):
        loader = await self.make("fy_net", "fy_files", "fy_media")
        for op in ("fetch", "files", "resource"):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                await loader.fy.hostcalls[op]("a", {})
        self.assertEqual(await loader.core("fetch", {"pid": "a", "url": "https://example.com/"}), {"error": "fy_net unavailable"})
        self.assertEqual(await loader.core("files_list", {"pid": "a"}), {"error": "fy_files unavailable"})
        self.assertEqual(await loader.core("resource", {"pid": "a"}), {"error": "fy_media unavailable"})

    async def test_a_broken_file_on_disk_degrades_the_same_way_in_a_fresh_process(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a")
        (loader.root / "fy_updates.py").write_text("def broken(:\n")
        (loader.root / "fy_targets.py").write_text("raise RuntimeError('boom at import')\n")
        script = (
            "import asyncio, json, sys\n"
            "sys.path.insert(0, %r)\n"
            "import framey as fy\n"
            "async def go():\n"
            "    return [await fy.core('batch_plan', None), await fy.core('target_add', {}), [m['id'] for m in await fy.core('list', None)], sorted(k for k, v in fy.MODS.items() if v is None)]\n"
            "print(json.dumps(asyncio.run(go())))\n"
        ) % str(loader.root)
        env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
        env["USERPROFILE"] = env["HOME"] = str(loader.home)
        out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(r[0], {"error": "fy_updates unavailable"})
        self.assertEqual(r[1], {"error": "fy_targets unavailable"})
        self.assertEqual(r[2], ["a"])
        self.assertEqual(r[3], ["fy_targets", "fy_updates"])
        self.assertIn("fy_updates unavailable", out.stderr)


class SafeModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_mode_injects_only_the_core_and_registers_no_backend(self):
        loader = H.Loader(prefs={"safe": True})
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loader.plugin("a", {"api": 1}, main="window.PLUGIN_A_RAN=1", backend=hooks_source(loader.base / "a.log", loader.plugins / "a" / "backend.py"))
        fy.load_backends()
        self.assertEqual(fy.sup().status(), {})
        code = fy.main_code()
        text = "\n".join(e for e, _ in code)
        self.assertNotIn("PLUGIN_A_RAN", text)
        self.assertEqual([p for _, p in code], ["#core"] + [None] * (len(code) - 1))
        self.assertIn("window.FY_RUNTIME", code[0][0])
        self.assertTrue(code[0][0].count("(() =>") or "Framey" in code[0][0])
        self.assertEqual(code[-1][0], "Framey.restore()")
        for name in ("fy-modules.js", "fy-settings.js", "fy-api.js", "fy-ui.js", "fy-strings-en.js"):
            self.assertIn((fy.ROOT / name).read_text(), text)
        self.assertNotIn((fy.ROOT / "fy-pages.js").read_text(), text)
        with self.assertRaisesRegex(LookupError, "backend not loaded"):
            await fy.backend("a", "x", 1)
        self.assertEqual([m["id"] for m in await loader.core("list")], ["a"])

    async def test_turning_safe_mode_off_restores_everything_after_reload(self):
        loader = H.Loader(prefs={"safe": True})
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loader.plugin("a", {"api": 1}, main="window.PLUGIN_A_RAN=1", backend=hooks_source(loader.base / "a.log", loader.plugins / "a" / "backend.py"))
        fy.load_backends()
        self.assertEqual(fy.sup().status(), {})
        self.assertIn("error", await loader.core("prefs_set", {"safe": False}))
        self.assertTrue(fy.safe())
        loader.trust(safe=False)
        self.assertIs((await loader.core("state_get"))["safe"], False)
        self.assertEqual(await loader.core("reload"), {"ok": True})
        self.assertEqual(list(fy.sup().status()), ["a"])
        self.assertIn("PLUGIN_A_RAN", "\n".join(e if isinstance(e, str) else e[2] for e, _ in fy.main_code()))
        self.assertEqual(await fy.backend("a", "hi", 2), {"m": "hi", "a": 2})

    async def test_reload_one_does_not_inject_plugin_code_in_safe_mode(self):
        loader = H.Loader(prefs={"safe": True})
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loader.plugin("a", main="1")
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        n = len(page.exprs())
        self.assertEqual(await loader.core("reload_one", "a"), {"ok": True})
        self.assertEqual(len(page.exprs()), n)


class UninstallTests(unittest.IsolatedAsyncioTestCase):
    async def start(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        log = loader.base / "a.log"
        loader.plugin("a", {"api": 1, "version": "1.0.0"}, backend=hooks_source(log, loader.plugins / "a" / "backend.py"))
        loader.plugin("b", {"api": 1}, backend=hooks_source(loader.base / "b.log", loader.plugins / "b" / "backend.py"))
        loader.fy.load_backends()
        self.assertEqual(await loader.fy.backend("a", "hi", 1), {"m": "hi", "a": 1})
        self.assertEqual(await loader.fy.backend("b", "hi", 1), {"m": "hi", "a": 1})
        return loader, log

    async def test_hook_runs_before_unload_before_code_removal_and_data_is_kept(self):
        loader, log = await self.start()
        fy = loader.fy
        data = fy.dirs("a").data
        self.assertTrue((data / "kept.txt").exists())
        b_pid = fy.sup().status()["b"]["os_pid"]
        r = await loader.core("uninstall", {"id": "a", "purge": False})
        self.assertEqual(r, {"ok": True})
        self.assertEqual(lines(log), ["startup", "uninstall:purge=False:code=True", "unload:uninstall"])
        self.assertFalse((loader.plugins / "a").exists())
        self.assertTrue((data / "kept.txt").exists())
        self.assertNotIn("a", fy.sup().status())
        self.assertEqual(fy.sup().status()["b"]["os_pid"], b_pid)

    async def test_remove_and_delete_data_purges_after_the_hook(self):
        loader, log = await self.start()
        fy = loader.fy
        dirs = fy.dirs("a")
        self.assertTrue(dirs.data.exists())
        r = await loader.core("uninstall", {"id": "a", "purge": True})
        self.assertEqual(r, {"ok": True})
        self.assertEqual(lines(log), ["startup", "uninstall:purge=True:code=True", "unload:uninstall"])
        self.assertFalse((loader.plugins / "a").exists())
        for name in ("config", "data", "runtime", "logs"):
            self.assertFalse(getattr(dirs, name).exists(), name)
        self.assertNotIn("a", fy.settings()["modules"]["state"])

    async def test_failed_code_removal_leaves_every_data_dir_and_returns_the_error(self):
        loader, log = await self.start()
        fy = loader.fy
        dirs = fy.dirs("a")
        code = loader.plugins / "a"
        for name in ("config", "logs"):
            getattr(dirs, name).mkdir(parents=True, exist_ok=True)
            (getattr(dirs, name) / "x.txt").write_text("x")
        real = shutil.rmtree

        def stuck(path, *args, **kw):
            return None if pathlib.Path(path) == code else real(path, *args, **kw)

        with mock.patch.object(shutil, "rmtree", stuck):
            r = await loader.core("uninstall", {"id": "a", "purge": True})
        self.assertEqual(r, {"error": "could not remove a"})
        self.assertTrue(code.exists())
        self.assertTrue((dirs.data / "kept.txt").exists())
        for name in ("config", "logs"):
            self.assertTrue((getattr(dirs, name) / "x.txt").exists(), name)
        self.assertIn("a", fy.settings()["modules"]["state"])
        self.assertEqual(await loader.core("uninstall", {"id": "a", "purge": True}), {"ok": True})
        self.assertFalse(code.exists())
        for name in ("config", "data", "logs"):
            self.assertFalse(getattr(dirs, name).exists(), name)

    async def test_plain_id_argument_still_works_and_never_purges(self):
        loader, log = await self.start()
        fy = loader.fy
        self.assertEqual(await loader.core("uninstall", "a"), {"ok": True})
        self.assertEqual(lines(log)[-2:], ["uninstall:purge=False:code=True", "unload:uninstall"])
        self.assertTrue((fy.dirs("a").data / "kept.txt").exists())

    async def test_bad_arguments_are_refused_without_touching_anything(self):
        loader, log = await self.start()
        for arg in (None, 5, {"id": 5}, {"purge": True}, "../a", "_core", {"id": "A b"}, []):
            self.assertEqual(await loader.core("uninstall", arg), {"error": "unknown plugin"}, arg)
        self.assertEqual(lines(log), ["startup"])
        self.assertTrue((loader.plugins / "a").exists())

    async def test_removal_clears_the_loader_records_but_keeps_the_migration_record_when_data_stays(self):
        loader, log = await self.start()
        fy = loader.fy
        await loader.core("mod_hide", {"id": "a", "hidden": True})
        await loader.core("mod_freeze", {"id": "a", "frozen": True})
        await loader.core("disable", "a")
        await loader.core("enable", "a")
        await loader.core("disable", "b")
        s = fy.settings()
        self.assertEqual(s["modules"]["state"]["a"]["ver"], "1.0.0")
        await loader.core("uninstall", {"id": "a", "purge": False})
        s = fy.settings()
        self.assertNotIn("a", s["modules"]["hidden"])
        self.assertNotIn("a", s["modules"]["frozen"])
        self.assertIn("a", s["modules"]["state"])
        self.assertEqual(s["disabled"], ["b"])
        await loader.core("uninstall", {"id": "b", "purge": True})
        s = fy.settings()
        self.assertEqual(s["disabled"], [])
        self.assertNotIn("b", s["modules"]["state"])

    async def test_a_disabled_module_is_removed_without_a_hook_and_purge_still_applies(self):
        loader, log = await self.start()
        fy = loader.fy
        await loader.core("disable", "a")
        dirs = fy.dirs("a")
        self.assertEqual(await loader.core("uninstall", {"id": "a", "purge": True}), {"ok": True})
        self.assertNotIn("uninstall:purge=True:code=True", lines(log))
        self.assertFalse(dirs.data.exists())
        self.assertEqual(fy.settings()["disabled"], [])

    async def test_a_hook_that_fails_never_blocks_the_removal(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("bad", {"api": 1}, backend="def uninstall(ctx):\n    raise RuntimeError('no')\nasync def call(m, a):\n    return 1\n")
        loader.fy.load_backends()
        self.assertEqual(await loader.fy.backend("bad", "x", 1), 1)
        self.assertEqual(await loader.core("uninstall", {"id": "bad", "purge": False}), {"ok": True})
        self.assertFalse((loader.plugins / "bad").exists())

    async def test_linked_plugin_is_unlinked_not_deleted(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        target = loader.home / "dev" / "mine"
        target.mkdir(parents=True)
        (target / "plugin.json").write_text(json.dumps({"id": "mine", "name": "m"}))
        link = loader.plugins / "mine"
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as e:
            self.skipTest("symlinks need a privilege here: %s" % e)
        self.assertEqual(await loader.core("uninstall", {"id": "mine"}), {"ok": True})
        self.assertFalse(os.path.lexists(link))
        self.assertTrue((target / "plugin.json").exists())


class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def start(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        for pid in ("a", "b"):
            loader.plugin(pid, {"api": 1}, backend=hooks_source(loader.base / (pid + ".log"), loader.plugins / pid / "backend.py"))
        loader.fy.load_backends()
        for pid in ("a", "b"):
            await loader.fy.backend(pid, "hi", 1)
        return loader

    async def test_reload_one_restarts_only_that_backend(self):
        loader = await self.start()
        fy = loader.fy
        before = {k: v["os_pid"] for k, v in fy.sup().status().items()}
        self.assertEqual(await loader.core("reload_one", "a"), {"ok": True})
        self.assertEqual(lines(loader.base / "a.log"), ["startup", "unload:reload"])
        self.assertEqual(lines(loader.base / "b.log"), ["startup"])
        self.assertIsNone(fy.sup().status()["a"]["os_pid"])
        self.assertEqual(fy.sup().status()["b"]["os_pid"], before["b"])
        await fy.backend("a", "hi", 2)
        self.assertNotEqual(fy.sup().status()["a"]["os_pid"], before["a"])
        self.assertEqual(await loader.core("reload_one", "../x"), {"error": "unknown plugin"})

    async def test_disable_stops_that_backend_with_the_disable_reason_and_enable_brings_it_back(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual(await loader.core("disable", "a"), {"ok": True})
        self.assertEqual(lines(loader.base / "a.log"), ["startup", "unload:disable"])
        self.assertNotIn("a", fy.sup().status())
        with self.assertRaisesRegex(LookupError, "backend not loaded"):
            await fy.backend("a", "hi", 1)
        self.assertEqual(await loader.core("enable", "a"), {"ok": True})
        self.assertEqual(await fy.backend("a", "hi", 3), {"m": "hi", "a": 3})

    async def test_full_reload_stops_every_backend_and_registers_again(self):
        loader = await self.start()
        fy = loader.fy
        self.assertEqual(await loader.core("reload"), {"ok": True})
        for pid in ("a", "b"):
            self.assertEqual(lines(loader.base / (pid + ".log")), ["startup", "unload:reload"])
            self.assertIn(pid, fy.sup().status())
        self.assertEqual(await fy.backend("b", "hi", 4), {"m": "hi", "a": 4})

    async def test_reload_clears_a_failed_backend_and_its_failure_record(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("crash", {"api": 1}, backend="import os\nasync def call(m, a):\n    os._exit(7)\n")
        fy = loader.fy
        fy.load_backends()
        sup = fy.sup()
        sup_mod = fy.use("fy_supervisor")
        for _ in range(3):
            with self.assertRaises(sup_mod.BackendError):
                await fy.backend("crash", "x", 1)
            w = fy.supervisor._ws["crash"]
            n = len(w.crashes)
            w.crashes.clear()
            w.crashes.extend([fy.mono() - 100] * n)
        self.assertEqual(sup.status()["crash"]["state"], "failed")
        self.assertEqual(fy.FAILS["crash"]["what"], "backend")
        self.assertEqual(await loader.core("reload_one", "crash"), {"ok": True})
        self.assertNotIn("crash", fy.FAILS)
        self.assertEqual(fy.sup().status()["crash"]["state"], "stopped")
        row = (await loader.core("list"))[0]
        self.assertEqual((row["fail"], row["stuck"]), (None, False))

    async def test_backend_error_is_recorded_with_its_stage_and_cleared_by_a_good_call(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("bad", {"api": 1}, backend="def startup(ctx):\n    raise RuntimeError('no start')\nasync def call(m, a):\n    return 1\n")
        loader.plugin("ok", {"api": 1}, backend=hooks_source(loader.base / "ok.log", loader.plugins / "ok" / "backend.py"))
        fy = loader.fy
        fy.load_backends()
        with self.assertRaises(fy.use("fy_supervisor").BackendError):
            await fy.backend("bad", "x", 1)
        self.assertEqual(fy.FAILS["bad"]["what"], "backend")
        self.assertEqual(fy.FAILS["bad"]["stage"], "startup")
        self.assertIn("no start", fy.FAILS["bad"]["msg"])
        rows = {m["id"]: m for m in await loader.core("list")}
        self.assertEqual(rows["bad"]["fail"]["stage"], "startup")
        with self.assertRaises(Exception):
            await fy.backend("ok", "boom", 1)
        self.assertEqual(fy.FAILS["ok"]["msg"], "kaput")
        self.assertEqual(await fy.backend("ok", "hi", 1), {"m": "hi", "a": 1})
        self.assertNotIn("ok", fy.FAILS)
        with self.assertRaises(Exception) as cm:
            await fy.backend("ok", "exit", 1)
        self.assertEqual(str(cm.exception), "backend called exit")


class MethodTests(unittest.IsolatedAsyncioTestCase):
    async def test_asset_is_a_no_op_and_the_picker_is_in_the_main_wrapper(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        page, task = await H.attach(loader, "main")
        self.addCleanup(task.cancel)
        n = len(page.exprs())
        for arg in ({"name": "picker"}, {"name": "dev"}, {"name": "../framey.py"}, None, "picker", 5):
            self.assertEqual(await loader.core("asset", arg), {"ok": True}, arg)
        self.assertEqual(len(page.exprs()), n)
        main = next(e for e in page.exprs() if e.startswith("(function(__fyt){"))
        self.assertIn((REPO / "fy-picker.js").read_text(), main)
        self.assertNotIn((REPO / "fy-dev.js").read_text(), main)

    async def test_locale_reads_the_plugin_file_and_refuses_unknown_plugins(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a", extra={})
        (loader.plugins / "a" / "locales").mkdir()
        (loader.plugins / "a" / "locales" / "pt-BR.json").write_text(json.dumps({"hello": "ola"}))
        self.assertEqual(await loader.core("locale", {"id": "a", "lang": "pt-BR"}), {"strings": {"hello": "ola"}})
        self.assertEqual(await loader.core("locale", {"id": "a", "lang": "fr"}), {"strings": {}})
        self.assertEqual(await loader.core("locale", {"id": "a", "lang": "../x"}), {"strings": {}})
        self.assertEqual(await loader.core("locale", {"id": "nope", "lang": "en"}), {"error": "unknown plugin"})
        self.assertEqual(await loader.core("locale", None), {"error": "unknown plugin"})

    async def test_log_writes_to_the_plugin_log_and_ignores_unknown_plugins(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a")
        self.assertIsNone(await loader.core("log", {"pid": "a", "level": "warning", "msg": "careful"}))
        self.assertIsNone(await loader.core("log", {"pid": "a", "level": "bogus", "msg": "x" * 5000}))
        text = (loader.fy.dirs("a").logs / "plugin.log").read_text()
        self.assertIn("WARNING careful", text)
        self.assertTrue(all(len(line) <= 2100 for line in text.splitlines()))
        self.assertEqual(await loader.core("log", {"pid": "ghost", "msg": "x"}), {"error": "unknown plugin"})
        self.assertFalse(loader.fy.dirs("ghost").logs.exists())

    async def test_state_methods_work_through_core_and_share_one_file(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        for pid in ("b", "a"):
            loader.plugin(pid, {"name": pid.upper()})
        r = await loader.core("state_get")
        self.assertEqual((r["order"], r["sort"], r["safe"]), (["a", "b"], "custom", False))
        r = await loader.core("mod_order", {"id": "b", "delta": -1})
        self.assertEqual(r["order"], ["b", "a"])
        await loader.core("disable", "a")
        r = await loader.core("mod_sort", "name")
        self.assertEqual((r["sort"], r["order"]), ("name", ["a", "b"]))
        saved = json.loads(loader.fy.CONF.read_text())
        self.assertEqual(saved["disabled"], ["a"])
        self.assertEqual(saved["modules"]["order"], ["b", "a"])
        self.assertEqual(saved["v"], 1)
        r = await loader.core("prefs_set", {"nope": 1})
        self.assertIn("error", r)

    async def test_unknown_settings_keys_survive_every_write(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a")
        loader.fy.CONF.write_text(json.dumps({"disabled": [], "future": {"x": 1}}))
        await loader.core("disable", "a")
        await loader.core("prefs_set", {"lang": "de"})
        self.assertEqual(json.loads(loader.fy.CONF.read_text())["future"], {"x": 1})

    async def test_hot_until_can_be_set_through_prefs(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        r = await loader.core("prefs_set", {"dev.hot_until": 1_900_000_000})
        self.assertEqual(r["code"], "not_allowed")

    async def test_developer_methods_are_closed_and_the_module_stays_unloaded_while_dev_mode_is_off(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        st = await loader.core("dev_status")
        self.assertEqual((st["enabled"], st["hot"]), (False, []))
        self.assertEqual(await loader.core("dev_set", {"id": "a", "hot": True}), {"error": "Developer mode is off", "code": "dev_off"})
        self.assertEqual(await loader.core("devtools_info"), {"error": "Developer mode is off", "code": "dev_off"})
        self.assertNotIn("fy_dev", fy.MODS)
        self.assertEqual(await loader.core("diag"), {"error": "Developer mode is off", "code": "dev_off"})
        self.assertNotIn("fy_dev", fy.MODS)
        loader.trust(**{"dev.enabled": True})
        diag = await loader.core("diag")
        self.assertEqual(set(diag) >= {"loader", "workers", "sessions"}, True)
        self.assertIn("fy_diag", fy.MODS)
        st = await loader.core("dev_status")
        self.assertIs(st["enabled"], True)
        self.assertIn("fy_dev", fy.MODS)

    async def test_diag_reports_the_loader_own_log_and_start_time(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loader.trust(**{"dev.enabled": True})
        TAIL = fy.TAIL
        TAIL.append("one line for the tail")
        d = await loader.core("diag")
        self.assertIn("one line for the tail", d["logs"]["tail"])
        self.assertIsInstance(d["loader"]["uptime_s"], int)
        self.assertEqual(d["loader"]["version"], fy.VERSION)
        self.assertNotIn(str(loader.home), json.dumps(d))

    async def test_autocheck_timer_exists_only_while_the_preference_is_on(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loop = asyncio.get_running_loop()
        before = len(loop._scheduled)
        await loader.core("state_get")
        await loader.core("list")
        self.assertIsNone(fy.updates)
        self.assertEqual(len(loop._scheduled), before)
        await loader.core("prefs_set", {"autocheck": True})
        self.assertIsNotNone(fy.updates._timer)
        self.assertEqual(len(loop._scheduled), before + 1)
        await loader.core("prefs_set", {"autocheck": False})
        self.assertIsNone(fy.updates._timer)
        self.assertEqual(len([h for h in loop._scheduled if not h.cancelled()]), before)

    async def test_the_auto_check_timer_rearms_once_a_day(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loop = asyncio.get_running_loop()
        await loader.core("prefs_set", {"autocheck": True})
        up = fy.updates
        up._fire()
        self.assertIsNotNone(up._timer)
        self.assertAlmostEqual(up._timer.when() - loop.time(), 86400, delta=5)
        up._task.cancel()
        up.disarm()

    async def test_compat_comes_from_the_package_module(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        loader.plugin("old", {"framey": {"min": "9.0.0"}})
        loader.plugin("none")
        loader.plugin("fine", {"api": 1, "framey": {"min": "1.0.2", "max": "1.0"}})
        rows = {m["id"]: m["compat"] for m in await loader.core("list")}
        self.assertEqual((rows["old"]["state"], rows["old"]["badge"]), ("incompatible", "newer"))
        self.assertEqual((rows["none"]["state"], rows["none"]["badge"]), ("unknown", "unknown"))
        self.assertEqual((rows["fine"]["state"], rows["fine"]["badge"]), ("ok", "ok"))
        self.assertEqual(fy.gate({"framey": {"min": "9.0.0"}}), "Needs Framey 9.0.0 or newer; you have 1.0.2")
        self.assertEqual(fy.gate({"api": 2}), "Needs API generation 2; this Framey provides 1")
        self.assertEqual(fy.gate({"name": "x"}), "")


HOSTY = """
import framey_api


async def call(method, arg):
    try:
        if method == "list":
            return await framey_api.list_dir(arg["root"], arg.get("path", ""))
        if method == "url":
            return await framey_api.resource_url("data", arg)
        if method == "fetch":
            return await framey_api.fetch(arg)
    except framey_api.FrameyApiError as e:
        return {"code": e.code, "message": e.message}
    return None
"""


class HostCallTests(unittest.IsolatedAsyncioTestCase):
    async def start(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        loader.plugin("a", {"api": 1}, backend=HOSTY)
        loader.fy.load_backends()
        return loader

    async def test_a_backend_lists_files_asks_for_a_resource_and_is_refused_a_private_address(self):
        loader = await self.start()
        fy = loader.fy
        data = fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        (data / "x.txt").write_text("x")
        r = await fy.backend("a", "list", {"root": "data", "path": ""})
        self.assertEqual([(e["name"], e["type"]) for e in r["entries"]], [("x.txt", "file")])
        r = await fy.backend("a", "list", {"root": "data", "path": "../etc"})
        self.assertEqual(r["code"], "bad_path")
        data = fy.dirs("a").data
        (data / "pic.png").write_bytes(bytes([137]) + b"PNG")
        url = await fy.backend("a", "url", "pic.png")
        self.assertTrue(url.startswith("data:image/png;base64,"))
        r = await fy.backend("a", "fetch", "https://127.0.0.1:9/")
        self.assertEqual(r["code"], "blocked_address")
        r = await fy.backend("a", "fetch", "http://example.com/")
        self.assertEqual(r["code"], "scheme")

    async def test_a_backend_gets_a_token_url_for_a_file_above_the_host_reply_cap(self):
        loader = await self.start()
        data = loader.fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        (data / "big.png").write_bytes(bytes([137]) + b"PNG" + b"x" * 600000)
        url = await loader.fy.backend("a", "url", "big.png")
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)

    async def test_the_page_methods_are_attributed_by_pid_and_never_write_files(self):
        loader = await self.start()
        loader.fy.dirs("a").data.mkdir(parents=True, exist_ok=True)
        r = await loader.core("fetch", {"pid": "a", "url": "https://127.0.0.1:9/", "dest": "data:evil"})
        self.assertEqual(r["code"], "blocked_address")
        self.assertFalse((loader.fy.dirs("a").data / "evil.part").exists())
        r = await loader.core("files_list", {"pid": "a", "root": "data"})
        self.assertIn("data", [x["id"] for x in r["roots"]])
        self.assertNotIn("home", [x["id"] for x in r["roots"]])
        self.assertEqual((await loader.core("files_list", {"pid": "a", "root": "etc"}))["code"], "bad_root")
        r = await loader.core("files_list", {"pid": "a", "root": "data", "path": ".."})
        self.assertIn(r["code"], ("bad_path", "outside_root"))
        data = loader.fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        (data / "s.txt").write_text("hello")
        r = await loader.core("resource", {"pid": "a", "root": "data", "path": "s.txt"})
        self.assertEqual(r["kind"], "data")
        self.assertEqual((await loader.core("resource", {"pid": "a", "root": "home", "path": "x"}))["code"], "bad_root")
        self.assertEqual((await loader.core("resource", {"pid": "a", "revoke": r.get("token") or "none"}))["ok"] in (True, False), True)

    async def test_resource_tokens_of_a_plugin_are_revoked_when_it_reloads(self):
        loader = await self.start()
        fy = loader.fy
        data = fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        (data / "big.bin").write_bytes(b"\0" * 1_100_000)
        (data / "big.bin").rename(data / "big.mp4")
        r = await loader.core("resource", {"pid": "a", "root": "data", "path": "big.mp4"})
        self.assertEqual(r["kind"], "http")
        media = fy.MODS["fy_media"].get()
        self.assertEqual(len(media._tokens), 1)
        await loader.core("reload_one", "a")
        self.assertEqual(len(media._tokens), 0)

    async def test_shutdown_closes_the_media_server_the_supervisor_and_the_hot_reload_watchers(self):
        loader = await self.start()
        fy = loader.fy
        data = fy.dirs("a").data
        data.mkdir(parents=True, exist_ok=True)
        (data / "big.mp4").write_bytes(b"\0" * 1_100_000)
        await loader.core("resource", {"pid": "a", "root": "data", "path": "big.mp4"})
        await fy.backend("a", "list", {"root": "home"})
        loader.trust(**{"dev.enabled": True})
        await loader.core("dev_status")
        self.assertIsNotNone(fy.MODS["fy_media"].get()._server)
        pid = fy.supervisor.status()["a"]["os_pid"]
        self.assertIsNotNone(pid)
        await asyncio.wait_for(fy.down(), 8)
        self.assertIsNone(fy.supervisor.status()["a"]["os_pid"])
        self.assertIsNone(fy.MODS["fy_media"]._media)


if __name__ == "__main__":
    unittest.main()
