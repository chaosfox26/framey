import asyncio
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))
sys.path.insert(0, str(HERE.parent))

import jharness as H

KEYGEN = shutil.which("ssh-keygen") or shutil.which("ssh-keygen", path=r"C:\Windows\System32\OpenSSH")
NS = "test-ns"
SHA = "3" * 40
DL = "https://github.com/o/relpkg/releases/download/v1.1.0/"
RELAPI = "o/relpkg/releases/latest"


def meta(version, priv, **extra):
    return json.dumps(dict({"id": "rel", "name": "Rel", "version": version, "short": "Rel", "source": "https://github.com/o/relpkg", "privileged": priv}, **extra))


@unittest.skipUnless(KEYGEN, "ssh-keygen is not installed on this machine")
class PrivilegedTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = pathlib.Path(tempfile.mkdtemp(prefix="fyj-keys-"))
        for name in ("good", "evil"):
            subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(cls.keys / name)], check=True, capture_output=True)
        cls.pub = " ".join((cls.keys / "good.pub").read_text().split()[:2])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.keys, ignore_errors=True)

    def sign(self, data, key="good", ns=NS):
        f = self.keys / "payload"
        f.write_bytes(data)
        sig = self.keys / "payload.sig"
        sig.unlink(missing_ok=True)
        subprocess.run([KEYGEN, "-Y", "sign", "-q", "-f", str(self.keys / key), "-n", ns, str(f)], check=True, capture_output=True)
        return sig.read_bytes()

    def setUp(self):
        if os.name == "nt":
            import fy_updates

            patcher = mock.patch.object(fy_updates, "posixpath", os.path)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def start(self, marker="1.0.0", files=None, tag="v1.1.0", key="good", ns=NS, digest=None, newmeta=None, prefs=None):
        loader = H.Loader(prefs=prefs)
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        etc = loader.base / "etc" / "relpkg"
        etc.mkdir(parents=True)
        helper = etc / "update-helper"
        helper.write_text("#!/bin/sh\n")
        mk = etc / ".version"
        mk.write_text(marker)
        (etc / "allowed_signers").write_text('framey-release namespaces="%s" %s\n' % (NS, self.pub))
        priv = {"installed": str(mk), "helper": str(helper)}
        d = loader.plugin("rel", {"version": "1.0.0", "source": "https://github.com/o/relpkg", "privileged": priv}, main="OLD", extra={"data.txt": "old data", ".version": "1" * 40})
        loader.dir, loader.priv, loader.marker = d, priv, mk
        fy.save(dict(fy.settings(), disabled=["rel"]))
        self.cfg = loader.base / "sudo.json"
        self.sudo_log = loader.base / "sudo.log"
        self.cfg.write_text(json.dumps({"log": str(self.sudo_log), "marker": str(mk), "update": {"write": "1.1.0"}, "rollback": {"write": "1.0.0"}}))
        fy.ETC = str(loader.base / "etc") + os.sep
        fy.root_ok = lambda s, k: k(s.st_mode)
        fy.KEYGEN = KEYGEN
        fy.SUDO = [sys.executable, str(HERE / "fixtures" / "j" / "fake_sudo.py")]
        fy.ENV = dict(os.environ, FAKE_SUDO_CFG=str(self.cfg))
        files = files or {"plugin.json": newmeta or meta("1.1.0", priv), "main.js": "NEW", "backend.py": "async def call(m, a):\n    return 1\n", "install.sh": "#!/bin/sh\n"}
        data = H.mktar(files) if isinstance(files, dict) else files
        asset = {"name": "relpkg.tar.gz", "browser_download_url": DL + "relpkg.tar.gz"}
        if digest:
            asset["digest"] = digest(data)
        loader.net.json(RELAPI, {"tag_name": tag, "body": "Release notes. " * 60, "assets": [asset, {"name": "relpkg.tar.gz.sig", "browser_download_url": DL + "relpkg.tar.gz.sig"}]})
        loader.net.commit("o", "relpkg", tag, SHA)
        loader.net.routes[DL + "relpkg.tar.gz"] = data
        loader.net.routes[DL + "relpkg.tar.gz.sig"] = self.sign(data, key, ns)
        return loader

    def sudo_calls(self):
        return [json.loads(x) for x in self.sudo_log.read_text().splitlines()] if self.sudo_log.exists() else []

    def tree(self, p):
        p = pathlib.Path(p)
        return {str(f.relative_to(p)).replace("\\", "/"): f.read_bytes() for f in sorted(p.rglob("*")) if f.is_file()}

    def untouched(self, loader, before):
        return self.tree(loader.dir) == before and not any(p.name.startswith((".old-", ".stage-")) for p in loader.plugins.iterdir())

    async def test_the_privileged_constants_are_exactly_as_built(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        fy = loader.fy
        self.assertEqual(fy.SUDO, ["sudo", "-n"])
        self.assertEqual(fy.ENV, {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        self.assertEqual((fy.KEYGEN, fy.PRINCIPAL, fy.ETC), ("/usr/bin/ssh-keygen", "framey-release", "/etc/"))
        self.assertEqual(fy.HOSTS, ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"))

    async def test_check_reads_the_release_and_runs_neither_a_download_nor_sudo(self):
        loader = await self.start()
        c = (await loader.core("check"))["checked"]["rel"]
        self.assertEqual((c["state"], c["latest"], c["known"]), ("update", "v1.1.0", True))
        self.assertEqual(len(c["notes"]), 600)
        self.assertFalse(any("relpkg.tar.gz" in u for u in loader.net.calls))
        self.assertEqual(self.sudo_calls(), [])

    async def test_a_signed_release_is_verified_then_the_helper_runs_through_sudo(self):
        loader = await self.start()
        fy = loader.fy
        r = await loader.core("update", "rel")
        self.assertEqual(r, {"ok": "rel", "version": "1.1.0"})
        t = self.tree(loader.dir)
        self.assertEqual((t["main.js"], t[".version"]), (b"NEW", b"v1.1.0"))
        self.assertNotIn("data.txt", t)
        self.assertIn("install.sh", t)
        calls = self.sudo_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"][1:], ["update"])
        self.assertEqual(loader.marker.read_text(), "1.1.0")
        self.assertEqual(sorted(p.name for p in loader.plugins.iterdir() if p.name.startswith(".")), [".old-rel"])
        self.assertEqual(json.loads((loader.plugins / ".old-rel" / "plugin.json").read_text())["version"], "1.0.0")
        ident = json.loads((loader.dir / ".identity.json").read_text())
        self.assertEqual(ident, {"commit": SHA, "kind": "release", "source": "o/relpkg", "tag": "v1.1.0", "v": 1})
        self.assertEqual(fy.settings()["disabled"], ["rel"])
        self.assertEqual(fy.settings()["updates"]["plugins"]["rel"], {"latest": "1.1.0", "state": "current"})

    async def test_a_signature_from_the_wrong_key_or_namespace_or_for_other_data_is_refused_before_anything_changes(self):
        for kw in ({"key": "evil"}, {"ns": "other-ns"}):
            loader = await self.start(**kw)
            before = self.tree(loader.dir)
            r = await loader.core("update", "rel")
            self.assertEqual(r, {"error": "Refused: the package is not signed by the Framey publisher"}, kw)
            self.assertTrue(self.untouched(loader, before))
            self.assertEqual(self.sudo_calls(), [])
        loader = await self.start()
        before = self.tree(loader.dir)
        other = self.sign(b"something else")
        loader.net.routes[DL + "relpkg.tar.gz.sig"] = other
        self.assertEqual(await loader.core("update", "rel"), {"error": "Refused: the package is not signed by the Framey publisher"})
        loader.net.routes[DL + "relpkg.tar.gz.sig"] = b""
        self.assertIn("no usable signature", (await loader.core("update", "rel"))["error"])
        self.assertTrue(self.untouched(loader, before))
        self.assertEqual(self.sudo_calls(), [])

    async def test_the_digest_github_published_must_match_when_there_is_one(self):
        loader = await self.start(digest=lambda data: "sha256:" + "0" * 64)
        before = self.tree(loader.dir)
        r = await loader.core("update", "rel")
        self.assertEqual(r, {"error": "Refused: the package does not match the digest GitHub published for it"})
        self.assertTrue(self.untouched(loader, before))
        self.assertEqual(self.sudo_calls(), [])
        loader = await self.start(digest=lambda data: "sha256:" + hashlib.sha256(data).hexdigest().upper())
        self.assertEqual((await loader.core("update", "rel"))["ok"], "rel")
        loader = await self.start(digest=lambda data: "sha512:abc")
        self.assertEqual((await loader.core("update", "rel"))["ok"], "rel")

    async def test_a_package_that_fails_the_checks_is_refused_before_the_swap(self):
        cases = {
            "different plugin": lambda p: meta("1.1.0", p, id="evil"),
            "not newer": lambda p: meta("1.0.0", p),
            "no privileged declaration": lambda p: json.dumps({"id": "rel", "name": "Rel", "version": "1.1.0", "short": "x"}),
            "framey.min too high": lambda p: meta("1.1.0", p, framey={"min": "9.0.0"}),
        }
        for label, make in cases.items():
            probe = await self.start()
            loader = await self.start(newmeta=make(probe.priv))
            before = self.tree(loader.dir)
            r = await loader.core("update", "rel")
            self.assertIn("error", r, label)
            self.assertTrue(r["error"].startswith("rejected") or "Needs Framey" in r["error"], (label, r))
            self.assertTrue(self.untouched(loader, before), label)
            self.assertEqual(self.sudo_calls(), [], label)

    async def test_release_not_newer_than_the_marker_is_current_and_a_hostile_tag_is_refused(self):
        loader = await self.start(tag="v1.0.0")
        self.assertEqual(await loader.core("update", "rel"), {"current": True})
        loader = await self.start(tag="vX")
        self.assertIn("unrecognised release tag", (await loader.core("update", "rel"))["error"])
        self.assertEqual(self.sudo_calls(), [])

    async def test_a_missing_helper_or_untrusted_signers_file_says_what_to_do(self):
        loader = await self.start()
        pathlib.Path(loader.priv["helper"]).unlink()
        self.assertEqual(await loader.core("update", "rel"), {"error": loader.fy.NOSETUP})
        loader = await self.start()
        (pathlib.Path(loader.priv["installed"]).parent / "allowed_signers").unlink()
        self.assertEqual(await loader.core("update", "rel"), {"error": "Release signing key not installed by root: run Install / Update in the Framey App once"})

    async def test_a_failing_helper_leaves_a_partial_update_that_can_be_retried_or_rolled_back(self):
        loader = await self.start()
        fy = loader.fy
        cfg = json.loads(self.cfg.read_text())
        cfg["update"] = {"code": 1, "out": "boom"}
        self.cfg.write_text(json.dumps(cfg))
        r = await loader.core("update", "rel")
        self.assertEqual(r, {"error": "Partial update: The protected updater failed (exit 1): boom"})
        row = (await loader.core("list"))[0]
        self.assertEqual(row["pending"], {"installed": loader.priv["installed"]})
        self.assertTrue((loader.plugins / ".old-rel").is_dir())
        self.assertEqual(await loader.core("update", "rel"), {"error": "finish or roll back the pending update first"})
        self.assertEqual(await loader.core("finish", "rel"), {"pending": {"installed": loader.priv["installed"]}})
        cfg["update"] = {"write": "1.1.0"}
        self.cfg.write_text(json.dumps(cfg))
        self.assertEqual(await loader.core("retry", "rel"), {"ok": "rel", "version": "1.1.0"})
        self.assertTrue((loader.plugins / ".old-rel").is_dir())

    async def test_rollback_runs_the_helper_rollback_then_restores_the_previous_copy(self):
        loader = await self.start()
        cfg = json.loads(self.cfg.read_text())
        cfg["update"] = {"write": "1.0.5", "code": 0}
        self.cfg.write_text(json.dumps(cfg))
        r = await loader.core("update", "rel")
        self.assertIn("Partial update", r["error"])
        self.assertEqual(await loader.core("rollback", "rel"), {"ok": "rel"})
        self.assertEqual([c["argv"][1:] for c in self.sudo_calls()], [["update"], ["rollback"]])
        self.assertEqual(loader.marker.read_text(), "1.0.0")
        self.assertEqual((loader.dir / "main.js").read_text(), "OLD")
        self.assertFalse((loader.plugins / ".old-rel").exists())

    async def test_sudo_is_never_run_through_a_shell_and_always_gets_the_fixed_environment(self):
        loader = await self.start()
        seen = []
        orig = subprocess.run

        def spy(*a, **kw):
            if "env" in kw:
                seen.append((a[0], kw))
            return orig(*a, **kw)

        subprocess.run = spy
        try:
            await loader.core("update", "rel")
        finally:
            subprocess.run = orig
        self.assertEqual(len(seen), 2)
        for argv, kw in seen:
            self.assertIsInstance(argv, list)
            self.assertIsNone(kw.get("shell"))
            self.assertIs(kw["env"], loader.fy.ENV)
        self.assertEqual(seen[0][0][1:3], ["-Y", "verify"])
        self.assertEqual(seen[1][0], loader.fy.SUDO + [loader.priv["helper"], "update"])

    async def test_batch_update_of_a_protected_plugin_goes_through_the_same_signed_flow(self):
        loader = await self.start()
        r = await loader.core("batch_apply", {"ids": ["rel"]})
        self.assertEqual([(x["id"], x["state"], x["reason"]) for x in r["results"]], [("rel", "skipped", "protected: updates from its own card")])
        await loader.core("enable", "rel")
        plan = await loader.core("batch_plan", {"ids": ["rel"]})
        row = plan["items"][0]
        self.assertEqual((row["id"], row["eligible"], row.get("reason")), ("rel", False, "protected: updates from its own card"), row)
        r = await loader.core("batch_apply", {"ids": ["rel"]})
        self.assertEqual([(x["id"], x["state"], x["reason"]) for x in r["results"]], [("rel", "skipped", "protected: updates from its own card")])
        self.assertEqual(self.sudo_calls(), [])
        self.assertEqual(loader.marker.read_text(), "1.0.0")

    async def test_the_same_protected_plugin_updates_from_its_own_card_through_the_signed_flow(self):
        loader = await self.start()
        await loader.core("enable", "rel")
        await loader.core("batch_apply", {"ids": ["rel"]})
        self.assertEqual(self.sudo_calls(), [])
        r = await loader.core("update", "rel")
        self.assertEqual(r, {"ok": "rel", "version": "1.1.0"})
        self.assertEqual([c["argv"][1:] for c in self.sudo_calls()], [["update"]])
        self.assertEqual(loader.marker.read_text(), "1.1.0")

    async def test_the_protected_plugin_cannot_be_repaired_or_moved_back_from_the_version_list(self):
        loader = await self.start()
        r = await loader.core("repair", {"id": "rel"})
        self.assertEqual(r["code"], "protected")
        r = await loader.core("ver_install", {"id": "rel", "tag": "v0.5.0"})
        self.assertEqual(r["code"], "protected")
        self.assertEqual(self.sudo_calls(), [])


if __name__ == "__main__":
    unittest.main()
