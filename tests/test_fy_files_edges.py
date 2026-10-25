import asyncio
import threading
import types
from unittest import mock

import fy_files
from test_fy_files import Tree


class RootNames(Tree):
    def test_extra_ids_are_independent_of_plugin_and_page_roots(self):
        extra = []
        for name in ("data", "config", "home", "media"):
            path = self.out / name
            path.mkdir()
            extra.append(str(path))
        dirs = types.SimpleNamespace(data=self.home / "plugin-data", config=self.home / "plugin-config")
        without_plugin = fy_files.roots(self.home, None, extra)
        with_plugin = fy_files.roots(self.home, dirs, extra)
        ids = ["data-2", "config-2", "home-2", "media-2"]
        self.assertEqual(list(without_plugin), ids)
        for key, path in zip(ids, extra):
            self.assertEqual(str(with_plugin[key]), path)
        self.assertEqual([r["id"] for r in fy_files.root_list(without_plugin)], ids)


class HostCalls(Tree):
    def host(self):
        return types.SimpleNamespace(HOME=self.home, dirs=lambda pid: None, settings=lambda: {})

    def test_page_cannot_explicitly_browse_the_builtin_media_root(self):
        with mock.patch.object(fy_files, "roots", side_effect=lambda *args: {"media": self.out}):
            result = asyncio.run(fy_files.files_list(self.host(), {"pid": "sample", "root": "media"}))
            self.assertEqual(result.get("code"), "bad_root")
            result = asyncio.run(fy_files.hostcall(self.host(), "sample", {"root": "media"}))
            self.assertEqual([e["name"] for e in result["entries"]], ["secret.txt"])

    def test_slow_root_discovery_keeps_the_event_loop_responsive(self):
        released = threading.Event()
        observations = []

        def slow_roots(*args):
            observations.append(released.wait(1))
            return {"data": self.out}

        async def run():
            timer = asyncio.get_running_loop().call_later(0.02, released.set)
            try:
                return await fy_files.hostcall(self.host(), "sample", {"root": "data"})
            finally:
                timer.cancel()
                released.set()

        with mock.patch.object(fy_files, "roots", side_effect=slow_roots):
            result = asyncio.run(run())
        self.assertEqual(observations, [True], "root discovery prevented the loop from releasing it")
        self.assertEqual([e["name"] for e in result["entries"]], ["secret.txt"])


class StatErrors(Tree):
    def test_permission_failure_retains_the_denied_error(self):
        with mock.patch.object(fy_files.os, "stat", side_effect=PermissionError):
            with self.assertRaises(fy_files.FileError) as caught:
                fy_files.stat_file(self.roots, "home", "a.png")
        self.assertEqual(caught.exception.code, "denied")
