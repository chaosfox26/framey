import hashlib
import io
import inspect
import json
import runpy
import sys
import tempfile
import types
import typing
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fy_files
import fy_decky_catalog
import fy_net
import fy_store

STUB = runpy.run_path(str(ROOT / "types" / "framey_api.pyi"))
DECKY_STUB = runpy.run_path(str(ROOT / "types" / "fy_decky_catalog.pyi"))


class BackendResultTypesTest(unittest.TestCase):
    def assert_matches(self, value, annotation):
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if annotation is typing.Any:
            return
        if origin is typing.Union:
            for item in args:
                try:
                    self.assert_matches(value, item)
                    return
                except AssertionError:
                    pass
            self.fail("%r does not match %r" % (value, annotation))
        elif origin is typing.Literal:
            self.assertIn(value, args)
        elif hasattr(annotation, "__required_keys__"):
            self.assertIsInstance(value, dict)
            fields = typing.get_type_hints(annotation)
            self.assertTrue(annotation.__required_keys__ <= value.keys())
            self.assertTrue(value.keys() <= fields.keys())
            for key, item in value.items():
                self.assert_matches(item, fields[key])
        elif origin is list:
            self.assertIsInstance(value, list)
            for item in value:
                self.assert_matches(item, args[0])
        elif origin is dict:
            self.assertIsInstance(value, dict)
            for key, item in value.items():
                self.assert_matches(key, args[0])
                self.assert_matches(item, args[1])
        else:
            self.assertIsInstance(value, annotation)

    def test_fetch_annotation_matches_text_binary_and_saved_host_results(self):
        response = types.SimpleNamespace(status=200, getheader=lambda name: "text/plain" if name == "content-type" else None)
        annotation = typing.get_type_hints(STUB["fetch"])["return"]
        for body, cut, path in ((b"hello", False, None), (b"\xff", True, None), (b"", False, "data:download.bin")):
            with self.subTest(body=body, path=path):
                result = fy_net._shape(response, body, len(body), cut, path)
                self.assert_matches(result, annotation)

    def test_listing_annotation_handles_roots_subfolders_and_nullable_metadata(self):
        annotation = typing.get_type_hints(STUB["list_dir"])["return"]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "folder").mkdir()
            (base / "file.txt").write_text("hello", encoding="utf-8")
            root_result = fy_files.list_dir({"data": base}, "data")
            self.assert_matches(root_result, annotation)
            self.assertIn("roots", root_result)
            nested = fy_files.list_dir({"data": base}, "data", "folder")
            self.assert_matches(nested, annotation)
            self.assertNotIn("roots", nested)
        required = annotation.__required_keys__
        self.assertNotIn("roots", required)
        self.assertIn("entries", required)
        entry_type = typing.get_args(typing.get_type_hints(annotation)["entries"])[0]
        self.assert_matches({"name": "unavailable", "type": "other", "size": None, "mtime": None}, entry_type)

    def test_migration_annotations_match_success_and_missing_results(self):
        move_type = typing.get_type_hints(STUB["_Migrate"].move)["return"]
        keys_type = typing.get_type_hints(STUB["_Migrate"].keys)["return"]
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp) / "source", Path(tmp) / "dest"
            self.assert_matches(fy_store.migrate_move(source, dest), move_type)
            source.write_text("data", encoding="utf-8")
            self.assert_matches(fy_store.migrate_move(source, dest), move_type)
        self.assert_matches(fy_store.migrate_keys({"old": 1}, {"old": "new"}), keys_type)
        self.assert_matches(fy_store.migrate_keys({}, {"old": "new"}), keys_type)


class DeckyAssessmentTypesTest(unittest.IsolatedAsyncioTestCase):
    assert_matches = BackendResultTypesTest.assert_matches

    def test_internal_catalog_declarations_keep_runtime_call_signatures(self):
        def signature(function):
            return [(p.name, p.kind, p.default is not inspect.Parameter.empty)
                    for p in inspect.signature(function).parameters.values()]

        pairs = [(DECKY_STUB["inspect_archive"], fy_decky_catalog.inspect_archive),
                 (DECKY_STUB["parse_decky"], fy_decky_catalog.parse_decky)]
        for name in ("__init__", "now", "source", "list", "details", "assess"):
            pairs.append((getattr(DECKY_STUB["DeckyCatalog"], name), getattr(fy_decky_catalog.DeckyCatalog, name)))
        for declared, actual in pairs:
            with self.subTest(method=actual.__name__):
                self.assertEqual(signature(declared), signature(actual))
                self.assertEqual(inspect.iscoroutinefunction(declared), inspect.iscoroutinefunction(actual))

    def test_static_archive_assessment_matches_arm_x86_and_unknown_schema(self):
        annotation = typing.get_type_hints(DECKY_STUB["inspect_archive"])["return"]
        for machine in (None, 62, 183):
            with self.subTest(machine=machine):
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w") as archive:
                    archive.writestr("plugin.json", json.dumps({"name": "Example Plugin", "flags": []}))
                    archive.writestr("dist/index.js", "export default {};")
                    if machine is not None:
                        header = bytearray(64)
                        header[:6] = b"\x7fELF\x02\x01"
                        header[18:20] = machine.to_bytes(2, "little")
                        archive.writestr("bin/helper", header)
                data = output.getvalue()
                result = fy_decky_catalog.inspect_archive(data, hashlib.sha256(data).hexdigest())
                self.assert_matches(result, annotation)
                self.assertTrue(result["warnings"])
                self.assertTrue(result["reasons"])
                self.assertTrue(result["not_executed"])

    async def test_assessment_success_and_error_match_declared_reply_branches(self):
        annotation = typing.get_type_hints(DECKY_STUB["DeckyCatalog"].assess)["return"]
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as bundle:
            bundle.writestr("plugin.json", json.dumps({"name": "Example Plugin", "flags": []}))
        data = output.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        calls = []

        async def metadata(url, limit):
            calls.append("metadata")
            self.assertEqual(url, fy_decky_catalog.SOURCES["decky_stable"]["url"])
            return json.dumps([{"id": 1, "name": "Example Plugin", "versions": [{"name": "1.0.0", "hash": digest}]}]).encode()

        async def archive(url, limit):
            calls.append("archive")
            self.assertEqual(url, fy_decky_catalog.CDN + digest + ".zip")
            return data

        with tempfile.TemporaryDirectory() as tmp:
            host = types.SimpleNamespace(HOME=Path(tmp), PLATFORM="linux-aarch64", now=lambda: 1790000000)
            service = fy_decky_catalog.DeckyCatalog(host, fetcher=metadata, archive_fetcher=archive)
            reply = await service.assess()
            self.assert_matches(reply, annotation)
            self.assertEqual(reply["code"], "confirmation_required")
            self.assertEqual(calls, [])
            details = await service.details({"id": "decky-1"})
            entry = details["entry"]
            request = {"id": entry["id"], "identity": entry["identity"],
                       "version_identity": entry["versions"][0]["identity"], "confirm": True}
            reply = await service.assess(request)
            self.assert_matches(reply, annotation)
            self.assertEqual(reply["identity"], request["identity"])
            self.assertEqual(reply["version_identity"], request["version_identity"])
            self.assertEqual(reply["source"], details["source"])
            for field in ("hash", "identity", "version_identity"):
                self.assertRegex(reply[field], r"^[0-9a-f]{64}$")
            self.assertEqual(calls, ["metadata", "archive"])


if __name__ == "__main__":
    unittest.main()
