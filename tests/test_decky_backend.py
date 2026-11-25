"""Contract tests using unchanged, importable plugin files in temporary roots."""

import asyncio
import importlib
import json
import logging
import os
import pwd
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DeckyBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="framey-decky-backend-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.instances = []
        try:
            self.Backend = importlib.import_module("decky_runtime.compat.decky_backend").DeckyBackend
        except (ImportError, AttributeError):
            self.fail("The independently implemented DeckyBackend bridge is missing")

    async def asyncTearDown(self):
        for backend in reversed(self.instances):
            try:
                await backend.stop()
            except BaseException:
                pass

    def plugin(self, source, *, api_version=2, flags=None, extra=None, name="fixture"):
        root = self.base / name / "original"
        root.mkdir(parents=True)
        metadata = {"name": name, "flags": [] if flags is None else flags}
        if api_version is not None:
            metadata["api_version"] = api_version
        (root / "plugin.json").write_text(json.dumps(metadata))
        (root / "package.json").write_text('{"version":"1.2.3"}')
        (root / "main.py").write_text(textwrap.dedent(source))
        for relative, content in (extra or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def backend(self, root, **kwargs):
        backend = self.Backend(
            root,
            self.base / root.parent.name / "config",
            self.base / root.parent.name / "data",
            self.base / root.parent.name / "logs",
            **kwargs,
        )
        self.instances.append(backend)
        return backend

    async def test_modern_settings_persist_with_local_imports_and_explicit_start(self):
        # Dropping root imports, constructing before start, or losing instance self breaks persistence.
        root = self.plugin(
            """
            import decky_plugin
            from settings import Store
            class Plugin:
                async def _main(self):
                    self.store = Store(decky_plugin.DECKY_PLUGIN_SETTINGS_DIR)
                async def set_setting(self, key, value):
                    self.store.set(key, value)
                async def get_setting(self, key):
                    return self.store.get(key)
        """,
            extra={
                "settings.py": """
            import json
            from pathlib import Path
            class Store:
                def __init__(self, directory):
                    self.path = Path(directory) / 'settings.json'
                def set(self, key, value):
                    self.path.write_text(json.dumps({key: value}))
                def get(self, key):
                    return json.loads(self.path.read_text())[key]
        """
            },
        )
        original = (root / "main.py").read_bytes()
        original_paths = set(root.rglob("*"))
        bytecode_before = sys.dont_write_bytecode
        backend = self.backend(root)
        self.assertNotIn("decky_plugin", sys.modules)
        self.assertFalse((root.parent / "config").exists())
        metadata = await backend.start()
        self.assertEqual(metadata["api_version"], 2)
        self.assertEqual(metadata["version"], "1.2.3")
        await backend.call("set_setting", ["enabled", True])
        self.assertIs(await backend.call("get_setting", ["enabled"]), True)
        self.assertEqual(
            json.loads((root.parent / "config/settings.json").read_text()), {"enabled": True}
        )
        await backend.stop()
        second = self.backend(root)
        await second.start()
        self.assertIs(await second.call("get_setting", ["enabled"]), True)
        self.assertEqual((root / "main.py").read_bytes(), original)
        await second.stop()
        self.assertEqual(set(root.rglob("*")), original_paths)
        self.assertEqual(sys.dont_write_bytecode, bytecode_before)

    async def test_legacy_calls_use_class_self_and_keyword_arguments(self):
        # Instantiating legacy Plugin or dispatching kwargs positionally breaks this fixture.
        root = self.plugin(
            """
            class Plugin:
                def __init__(self):
                    raise AssertionError('legacy must not instantiate')
                async def _migration(self):
                    self.migrated = True
                async def _main(self):
                    self.started = self.migrated
                async def read(self, *, value):
                    return [self.started, value]
        """,
            api_version=None,
        )
        backend = self.backend(root)
        await backend.start()
        self.assertEqual(await backend.call("read", {"value": 7}, legacy=True), [True, 7])

    async def test_private_rpc_and_mixed_protocols_fail_before_invocation(self):
        # Removing name/protocol guards would execute observable writes.
        root = self.plugin("""
            from pathlib import Path
            import decky
            class Plugin:
                async def _private(self):
                    Path(decky.DECKY_PLUGIN_SETTINGS_DIR, 'private').touch()
                async def public(self, *args, **kwargs):
                    Path(decky.DECKY_PLUGIN_SETTINGS_DIR, 'public').touch()
        """)
        backend = self.backend(root)
        await backend.start()
        for method, args, legacy in [
            ("_private", [], False),
            ("__class__", [], False),
            ("public", {}, False),
            ("public", {}, True),
            ("public", [], 1),
            ("x.y", [], False),
        ]:
            with self.subTest(method=method, args=args, legacy=legacy):
                with self.assertRaises((ValueError, TypeError)):
                    await backend.call(method, args, legacy=legacy)
        self.assertEqual(list((root.parent / "config").iterdir()), [])

    async def test_legacy_rejects_modern_protocol(self):
        root = self.plugin("class Plugin: pass", api_version=0)
        backend = self.backend(root)
        await backend.start()
        with self.assertRaises(ValueError):
            await backend.call("whatever", [], legacy=False)
        with self.assertRaises(TypeError):
            await backend.call("whatever", [], legacy=True)

    async def test_constants_logger_emit_and_environment_are_plugin_owned(self):
        # Wrong paths or an un-awaited callback changes actual I/O/event results.
        events = []
        logger = logging.getLogger("framey-backend-test")

        async def emit(event, payload):
            await asyncio.sleep(0)
            events.append((event, payload))

        before = dict(os.environ)
        path_before = list(sys.path)
        root = self.plugin("""
            import os
            import decky
            import decky_plugin
            class Plugin:
                async def inspect(self):
                    await decky.emit('updated', 4, {'ok': True})
                    return [decky is decky_plugin, decky.DECKY_PLUGIN_DIR,
                        decky.DECKY_PLUGIN_SETTINGS_DIR, decky.DECKY_PLUGIN_RUNTIME_DIR,
                        decky.DECKY_PLUGIN_LOG_DIR, decky.DECKY_PLUGIN_VERSION,
                        decky.logger.name, os.environ['DECKY_PLUGIN_SETTINGS_DIR'],
                        decky.DECKY_VERSION, os.environ.get('HOME'), os.environ.get('USER')]
        """)
        backend = self.backend(root, logger=logger, emit=emit)
        await backend.start()
        result = await backend.call("inspect", [])
        self.assertEqual(
            result[:8],
            [
                True,
                str(root),
                str(root.parent / "config"),
                str(root.parent / "data"),
                str(root.parent / "logs"),
                "1.2.3",
                "framey-backend-test",
                str(root.parent / "config"),
            ],
        )
        self.assertIn("Framey", result[8])
        self.assertEqual(result[9:], [before.get("HOME"), before.get("USER")])
        self.assertEqual(events, [("updated", [4, {"ok": True}])])
        await backend.stop()
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(sys.path, path_before)
        self.assertNotIn("decky_plugin", sys.modules)
        self.assertNotIn("decky", sys.modules)

    async def test_real_framey_sdk_preserves_zero_array_and_multiple_event_arguments(self):
        # Without the outer argument list, a single list payload is accidentally spread by the host.
        import fy_sdk

        events = []
        root = self.plugin("""
            import decky
            class Plugin:
                async def publish(self, args):
                    await decky.emit('updated', *args)
        """)
        dirs = {key: root.parent / key for key in ("config", "data", "runtime", "logs")}
        rpc = types.SimpleNamespace(
            emit=lambda name, payload: events.append((name, payload)) or True
        )
        sdk = fy_sdk.build("fixture", {"version": "1.2.3"}, dirs, {}, rpc)
        backend = self.backend(root, emit=sdk.events.emit)
        await backend.start()
        for args in ([], [[1, 2]], [4, {"ok": True}]):
            with self.subTest(args=args):
                events.clear()
                try:
                    await backend.call("publish", [args])
                except TypeError:
                    self.fail("The compatibility bridge did not encode one Framey event payload")
                self.assertEqual(events, [("updated", args)])

    async def test_stop_unloads_then_cancels_and_awaits_background_main(self):
        # Failing to await cancellation leaves the final write missing at stop return.
        root = self.plugin("""
            import asyncio
            from pathlib import Path
            import decky
            class Plugin:
                async def _main(self):
                    try:
                        await asyncio.Event().wait()
                    finally:
                        await asyncio.sleep(0)
                        Path(decky.DECKY_PLUGIN_SETTINGS_DIR, 'cancelled').write_text(str(self.unloaded))
                async def _unload(self):
                    self.unloaded = True
        """)
        backend = self.backend(root)
        await backend.start()
        await backend.stop()
        self.assertEqual((root.parent / "config/cancelled").read_text(), "True")
        with self.assertRaises(RuntimeError):
            await backend.call("read", [])
        await backend.stop()

    async def test_py_modules_do_not_leak_into_next_plugin_or_overwrite_host_modules(self):
        # Reusing cached helper modules makes plugin B return plugin A's value.
        original = types.ModuleType("framey_fixture_helper")
        original.VALUE = "host"
        sys.modules["framey_fixture_helper"] = original
        self.addCleanup(sys.modules.pop, "framey_fixture_helper", None)
        before_path = list(sys.path)
        for name, value in [("first", "one"), ("second", "two")]:
            root = self.plugin(
                """
                from framey_fixture_helper import VALUE
                class Plugin:
                    async def read(self): return VALUE
            """,
                name=name,
                extra={"py_modules/framey_fixture_helper.py": f"VALUE = {value!r}"},
            )
            backend = self.backend(root)
            await backend.start()
            self.assertEqual(await backend.call("read", []), value)
            await backend.stop()
            self.assertIs(sys.modules["framey_fixture_helper"], original)
            self.assertEqual(sys.path, before_path)

    async def test_only_one_backend_can_be_active_and_cleanup_releases_it(self):
        root = self.plugin("class Plugin: pass")
        first, second = self.backend(root), self.backend(root)
        await first.start()
        with self.assertRaises(RuntimeError):
            await second.start()
        await first.stop()
        await second.start()

    async def test_import_migration_constructor_and_main_failures_restore_runtime(self):
        # Swallowing any initialization failure hides a broken backend and leaks globals.
        for name, source in {
            "import": "import decky\nraise RuntimeError('broken import')",
            "constructor": "class Plugin:\n def __init__(self): raise RuntimeError('broken constructor')",
            "migration": "class Plugin:\n async def _migration(self): raise RuntimeError('broken migration')",
            "main": "class Plugin:\n async def _main(self): raise RuntimeError('broken main')",
        }.items():
            with self.subTest(name=name):
                root = self.plugin(source, name=name)
                backend = self.backend(root)
                before_path, before_env = list(sys.path), dict(os.environ)
                with self.assertRaisesRegex(RuntimeError, f"broken {name}"):
                    await backend.start()
                self.assertEqual(sys.path, before_path)
                self.assertEqual(dict(os.environ), before_env)
                self.assertNotIn("decky_plugin", sys.modules)

    async def test_later_main_failure_is_propagated_by_calls_and_stop(self):
        root = self.plugin("""
            import asyncio
            class Plugin:
                async def _main(self):
                    await asyncio.sleep(0)
                    raise RuntimeError('background failure')
                async def read(self): return 1
        """)
        backend = self.backend(root)
        await backend.start()
        await asyncio.sleep(0)
        with self.assertRaisesRegex(RuntimeError, "background failure"):
            await backend.call("read", [])
        with self.assertRaisesRegex(RuntimeError, "background failure"):
            await backend.stop()
        self.assertNotIn("decky", sys.modules)

    async def test_unload_failure_still_cleans_up_and_cancels_main(self):
        root = self.plugin("""
            import asyncio
            from pathlib import Path
            import decky
            class Plugin:
                async def _main(self):
                    try: await asyncio.Event().wait()
                    finally: Path(decky.DECKY_PLUGIN_SETTINGS_DIR, 'cancelled').touch()
                async def _unload(self): raise RuntimeError('unload failure')
        """)
        backend = self.backend(root)
        await backend.start()
        with self.assertRaisesRegex(RuntimeError, "unload failure"):
            await backend.stop()
        self.assertTrue((root.parent / "config/cancelled").exists())
        self.assertNotIn("decky_plugin", sys.modules)

    async def test_migration_ignores_only_absent_source_and_preserves_existing_data(self):
        root = self.plugin("""
            import decky
            class Plugin:
                async def migrate(self, source): return decky.migrate_settings(source)
        """)
        backend = self.backend(root)
        await backend.start()
        missing = self.base / "missing-legacy"
        self.assertEqual(await backend.call("migrate", [str(missing)]), {str(missing): ""})
        existing = self.base / "external-legacy"
        existing.mkdir()
        (existing / "important").write_text("keep")
        with self.assertRaises(NotImplementedError):
            await backend.call("migrate", [str(existing)])
        self.assertEqual((existing / "important").read_text(), "keep")
        self.assertEqual(list((root.parent / "config").iterdir()), [])

    async def test_migration_checks_later_existing_sources_before_starting_main(self):
        # Checking just the first argument silently hides existing second-source state.
        existing_file = self.base / "legacy-file"
        existing_file.write_text("original legacy bytes")
        existing_dir = self.base / "legacy-dir"
        existing_dir.mkdir()
        (existing_dir / "important").write_text("directory legacy bytes")
        dangling = self.base / "legacy-link"
        dangling.symlink_to(self.base / "absent-link-target")
        for index, existing in enumerate([existing_file, existing_dir, dangling]):
            for kind in ["migrate_settings", "migrate_runtime", "migrate_logs"]:
                with self.subTest(source_type=index, kind=kind):
                    missing = self.base / "absent-first-source"
                    root = self.plugin(
                        f"""
                        from pathlib import Path
                        import decky
                        class Plugin:
                            async def _migration(self):
                                decky.{kind}({str(missing)!r}, {str(existing)!r})
                            async def _main(self):
                                Path(decky.DECKY_PLUGIN_SETTINGS_DIR, 'main-ran').touch()
                    """,
                        name=f"migration-{index}-{kind}",
                    )
                    backend = self.backend(root)
                    try:
                        with self.assertRaises(NotImplementedError):
                            await backend.start()
                    finally:
                        await backend.stop()
                    self.assertFalse((root.parent / "config/main-ran").exists())
        self.assertEqual(existing_file.read_text(), "original legacy bytes")
        self.assertEqual((existing_dir / "important").read_text(), "directory legacy bytes")
        self.assertTrue(dangling.is_symlink())
        self.assertFalse((self.base / "absent-first-source").exists())

    async def test_variadic_missing_migrations_return_a_truthful_map_without_writes(self):
        # Returning None or accepting only one source breaks the public migration return contract.
        root = self.plugin("""
            import decky
            class Plugin:
                async def migrate(self, kind, sources):
                    return getattr(decky, kind)(*sources)
        """)
        backend = self.backend(root)
        await backend.start()
        sources = [str(self.base / "missing-one"), str(self.base / "missing-two")]
        for kind in ["migrate_settings", "migrate_runtime", "migrate_logs"]:
            with self.subTest(kind=kind):
                self.assertEqual(
                    await backend.call("migrate", [kind, sources]), {sources[0]: "", sources[1]: ""}
                )
                self.assertEqual(await backend.call("migrate", [kind, []]), {})
        self.assertFalse(any(Path(source).exists() for source in sources))
        for directory in ["config", "data", "logs"]:
            self.assertEqual(list((root.parent / directory).iterdir()), [])

    async def test_migration_validates_each_source_and_does_not_hide_path_errors(self):
        # Ignored later arguments or lexists swallowing errors falsely mark inaccessible state absent.
        root = self.plugin("""
            import decky
            class Plugin:
                async def migrate(self, kind, sources):
                    return getattr(decky, kind)(*sources)
                async def keyword(self):
                    return decky.migrate_settings('absent', ignored='not-supported')
        """)
        backend = self.backend(root)
        await backend.start()
        missing = str(self.base / "missing")
        for kind in ["migrate_settings", "migrate_runtime", "migrate_logs"]:
            for invalid, error in [
                (None, TypeError),
                (3, TypeError),
                ({}, TypeError),
                ([], TypeError),
                ("", ValueError),
                ("bad\0path", ValueError),
            ]:
                for sources in [[invalid, missing], [missing, invalid]]:
                    with self.subTest(kind=kind, invalid=invalid, position=sources.index(invalid)):
                        with self.assertRaises(error):
                            await backend.call("migrate", [kind, sources])
        with self.assertRaises(TypeError):
            await backend.call("keyword", [])
        file = self.base / "not-a-directory"
        file.write_text("keep")
        with self.assertRaises(NotADirectoryError):
            await backend.call("migrate", ["migrate_settings", [missing, str(file / "child")]])
        # Isolate just the OS metadata lookup: inability to inspect is not absence.
        with patch("decky_runtime.compat.decky_backend.os.lstat", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                await backend.call("migrate", ["migrate_settings", [missing]])
        self.assertEqual(file.read_text(), "keep")

    async def test_bad_metadata_and_root_flags_are_rejected_before_execution(self):
        # Running before validating metadata would create the sentinel.
        for index, (version, flags) in enumerate(
            [(2, ["root"]), (True, []), (-1, []), ("2", []), (2, "root"), (2, [1])]
        ):
            with self.subTest(version=version, flags=flags):
                root = self.plugin(
                    'from pathlib import Path\nPath(__file__).with_name("executed").touch()\nclass Plugin: pass',
                    name=f"bad-{index}",
                    api_version=version,
                    flags=flags,
                )
                with self.assertRaises((ValueError, TypeError)):
                    await self.backend(root).start()
                self.assertFalse((root / "executed").exists())
        with self.assertRaises(ValueError):
            await self.backend(self.base / "does-not-exist").start()

    async def start_settings(self, backend):
        try:
            return await backend.start()
        except ModuleNotFoundError as error:
            self.fail(f"Required shared settings helper is unavailable: {error.name}")

    async def test_shared_settings_default_persistence_staging_and_read(self):
        # A missing helper, wrong default path or non-persisting set/commit loses actual state.
        root = self.plugin("""
            from settings import SettingsManager
            class Plugin:
                async def _main(self): self.store = SettingsManager('options')
                async def get(self, key, default=None): return self.store.getSetting(key, default)
                async def put(self, key, value): self.store.setSetting(key, value)
                async def stage(self, key, value): self.store.settings[key] = value
                async def commit(self): self.store.commit()
                async def read(self): self.store.read()
        """)
        backend = self.backend(root)
        await self.start_settings(backend)
        file = root.parent / "config/options.json"
        self.assertEqual(json.loads(file.read_text()), {})
        self.assertEqual(await backend.call("get", ["absent", 41]), 41)
        await backend.call("put", ["enabled", True])
        self.assertEqual(json.loads(file.read_text()), {"enabled": True})
        await backend.call("stage", ["label", "日本語"])
        self.assertEqual(json.loads(file.read_text()), {"enabled": True})
        await backend.call("commit", [])
        self.assertEqual(json.loads(file.read_text()), {"enabled": True, "label": "日本語"})
        file.write_text('{"external":7}')
        await backend.call("read", [])
        self.assertEqual(await backend.call("get", ["external"]), 7)
        self.assertIsNone(await backend.call("get", ["enabled"]))
        await backend.stop()
        again = self.backend(root)
        await self.start_settings(again)
        self.assertEqual(await again.call("get", ["external"]), 7)

    async def test_shared_settings_rejects_corruption_without_overwriting_it(self):
        # Swallowing invalid JSON or a non-object value would silently discard existing state.
        for index, content in enumerate(['{"broken":', "[1,2]", "null", '{"bad":NaN}']):
            with self.subTest(content=content):
                root = self.plugin(
                    """
                    from settings import SettingsManager
                    class Plugin:
                        async def _main(self): self.store = SettingsManager('options')
                """,
                    name=f"corrupt-{index}",
                )
                directory = root.parent / "config"
                directory.mkdir()
                file = directory / "options.json"
                file.write_text(content)
                with self.assertRaises(ValueError):
                    await self.start_settings(self.backend(root))
                self.assertEqual(file.read_text(), content)

    async def test_shared_settings_failed_commit_keeps_original_json_and_no_temp_files(self):
        # Writing directly to the destination would truncate the valid file on encoding failure.
        root = self.plugin("""
            from settings import SettingsManager
            class Plugin:
                async def _main(self): self.store = SettingsManager('options')
                async def put(self): self.store.setSetting('good', 9)
                async def invalid_commit(self):
                    self.store.settings['invalid'] = {1, 2}
                    self.store.commit()
                async def nonfinite_commit(self):
                    self.store.settings = {'invalid': float('inf')}
                    self.store.commit()
        """)
        backend = self.backend(root)
        await self.start_settings(backend)
        await backend.call("put", [])
        directory = root.parent / "config"
        file = directory / "options.json"
        original = file.read_bytes()
        for method, error in [("invalid_commit", TypeError), ("nonfinite_commit", ValueError)]:
            with self.subTest(method=method):
                with self.assertRaises(error):
                    await backend.call(method, [])
                self.assertEqual(file.read_bytes(), original)
                self.assertEqual([p.name for p in directory.iterdir()], ["options.json"])

    async def test_shared_settings_constrains_names_directories_and_symlinks(self):
        # Unchecked names or symlinks would read/write outside the owned settings directory.
        root = self.plugin("""
            from settings import SettingsManager
            class Plugin:
                async def configure(self, name, directory=None):
                    self.store = SettingsManager(name, settings_directory=directory)
                async def put(self): self.store.setSetting('key', 3)
                async def read(self): self.store.read()
        """)
        backend = self.backend(root)
        await self.start_settings(backend)
        for name in ["../escape", "x/y", "x\\y", "", ".", "..", "bad\0name"]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    await backend.call("configure", [name])
        outside = self.base / "outside-settings"
        outside.mkdir()
        for directory in [str(outside), str(root)]:
            with self.subTest(directory=directory):
                with self.assertRaises(ValueError):
                    await backend.call("configure", ["options", directory])
        config = root.parent / "config"
        (config / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            await backend.call("configure", ["options", str(config / "linked")])
        external = outside / "external.json"
        external.write_text('{"keep":true}')
        (config / "options.json").symlink_to(external)
        with self.assertRaises(ValueError):
            await backend.call("configure", ["options"])
        (config / "options.json").unlink()
        await backend.call("configure", ["options", str(config / "nested")])
        await backend.call("put", [])
        self.assertEqual(json.loads((config / "nested/options.json").read_text()), {"key": 3})
        (config / "nested/options.json").unlink()
        (config / "nested/options.json").symlink_to(external)
        for method in ["read", "put"]:
            with self.assertRaises(ValueError):
                await backend.call(method, [])
        self.assertEqual(external.read_text(), '{"keep":true}')

    async def test_shared_settings_preserves_host_module_and_prefers_bundled_module(self):
        # A global helper cache would shadow the plugin's own original settings implementation.
        host = types.ModuleType("settings")
        host.VALUE = "host"
        previous = sys.modules.get("settings")
        sys.modules["settings"] = host

        def restore():
            if previous is None:
                sys.modules.pop("settings", None)
            else:
                sys.modules["settings"] = previous

        self.addCleanup(restore)
        for name, extra, expected in [
            ("fallback", {}, "Framey"),
            ("bundled-root", {"settings.py": "ORIGIN = 'plugin-root'"}, "plugin-root"),
            (
                "bundled-modules",
                {"py_modules/settings.py": "ORIGIN = 'plugin-modules'"},
                "plugin-modules",
            ),
        ]:
            root = self.plugin(
                """
                import settings
                class Plugin:
                    async def read(self): return getattr(settings, 'ORIGIN', settings.__doc__)
            """,
                name=name,
                extra=extra,
            )
            backend = self.backend(root)
            await self.start_settings(backend)
            origin = await backend.call("read", [])
            self.assertIsInstance(origin, str, "The shared helper must identify its Framey origin")
            self.assertIn(expected, origin)
            await backend.stop()
            self.assertIs(sys.modules["settings"], host)

    async def test_shared_settings_accepts_only_this_plugins_other_owned_state_directories(self):
        # Rejecting runtime/log paths would prevent legitimate per-plugin counters from loading.
        root = self.plugin("""
            import decky
            from settings import SettingsManager
            class Plugin:
                async def configure(self, kind):
                    directory = (decky.DECKY_PLUGIN_RUNTIME_DIR if kind == 'runtime'
                        else decky.DECKY_PLUGIN_LOG_DIR)
                    self.store = SettingsManager('counter', settings_directory=directory)
                    self.store.setSetting('count', 4)
        """)
        backend = self.backend(root)
        await self.start_settings(backend)
        for kind, directory in [("runtime", "data"), ("logs", "logs")]:
            with self.subTest(kind=kind):
                try:
                    await backend.call("configure", [kind])
                except ValueError as error:
                    self.fail(f"This plugin's owned {kind} state path was rejected: {error}")
                self.assertEqual(
                    json.loads((root.parent / directory / "counter.json").read_text()), {"count": 4}
                )
        self.assertEqual(list((root.parent / "config").iterdir()), [])

    async def test_shared_identity_helpers_use_actual_normal_worker_account(self):
        # Using USER/LOGNAME or a Decky-specific account would misidentify the worker.
        if os.getuid() == 0:
            self.skipTest("The successful identity contract requires a normal worker UID")
        root = self.plugin("""
            from helpers import get_user, get_user_id
            class Plugin:
                async def read(self): return [get_user(), get_user_id()]
        """)
        with patch.dict(os.environ, {"USER": "root", "LOGNAME": "root"}):
            backend = self.backend(root)
            try:
                await backend.start()
            except ImportError as error:
                self.fail(f"The requested worker identity helper is unavailable: {error}")
            self.assertEqual(
                await backend.call("read", []), [pwd.getpwuid(os.getuid()).pw_name, os.getuid()]
            )
            await backend.stop()
            self.assertEqual(os.environ["USER"], "root")

    async def test_shared_identity_helpers_reject_root_and_mixed_privilege_workers(self):
        # Mapping privileged process IDs to an invented normal user would hide a bad worker launch.
        root = self.plugin("""
            from helpers import get_user, get_user_id
            class Plugin:
                async def username(self): return get_user()
                async def uid(self): return get_user_id()
        """)
        backend = self.backend(root)
        try:
            await backend.start()
        except ImportError as error:
            self.fail(f"The requested worker identity helper is unavailable: {error}")
        for real, effective in [(0, 0), (1000, 0), (1000, 1001)]:
            for method in ["username", "uid"]:
                with self.subTest(real=real, effective=effective, method=method):
                    with (
                        patch("os.getuid", return_value=real),
                        patch("os.geteuid", return_value=effective),
                    ):
                        with self.assertRaises(RuntimeError):
                            await backend.call(method, [])

    async def test_shared_identity_helper_preserves_host_module_and_prefers_bundled_modules(self):
        # A global compatibility alias must not shadow unchanged plugin-local helper modules.
        host = types.ModuleType("helpers")
        host.ORIGIN = "host"
        previous = sys.modules.get("helpers")
        sys.modules["helpers"] = host

        def restore():
            if previous is None:
                sys.modules.pop("helpers", None)
            else:
                sys.modules["helpers"] = previous

        self.addCleanup(restore)
        for name, extra, expected in [
            ("identity-fallback", {}, "Framey"),
            ("identity-root", {"helpers.py": "ORIGIN = 'plugin-root'"}, "plugin-root"),
            (
                "identity-modules",
                {"py_modules/helpers.py": "ORIGIN = 'plugin-modules'"},
                "plugin-modules",
            ),
        ]:
            root = self.plugin(
                """
                import helpers
                class Plugin:
                    async def read(self): return getattr(helpers, 'ORIGIN', helpers.__doc__)
            """,
                name=name,
                extra=extra,
            )
            backend = self.backend(root)
            await backend.start()
            origin = await backend.call("read", [])
            self.assertIsInstance(origin, str)
            self.assertIn(expected, origin)
            await backend.stop()
            self.assertIs(sys.modules["helpers"], host)


if __name__ == "__main__":
    unittest.main()
