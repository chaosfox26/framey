import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / "fixtures" / "j"))

import jharness as H


class PanelShape(unittest.TestCase):
    def test_the_core_is_one_body_so_inject_handles_are_in_scope_for_the_built_ins(self):
        L = H.Loader()
        try:
            expr, pid = L.fy.main_code()[0]
            self.assertEqual(pid, "#core")
            self.assertTrue(expr.startswith("(function(__fyt){"))
            self.assertEqual(expr.count("const __FY = "), 1)
            self.assertNotIn("(function(){", expr)
            self.assertIn(L.fy.CORE_TOK, expr)
            self.assertLess(expr.index("const __FY = "), expr.index("const framey = __FY;"))
        finally:
            asyncio.run(L.close())

    def test_loader_events_are_called_on_the_core_object_and_carry_no_token(self):
        L = H.Loader()
        try:
            sent = []

            async def fake(method, **kw):
                sent.append((method, kw))

            fake.core = "core-object"
            L.fy.LIVE["main"] = fake
            asyncio.run(L.fy.notify_page("_core", "progress", {"n": 1}))
            self.assertEqual(sent, [("Runtime.callFunctionOn", {"objectId": "core-object", "functionDeclaration": L.fy.EVENT, "arguments": [{"value": "_core"}, {"value": "progress"}, {"value": {"n": 1}}, {"value": 0}], "returnByValue": True})])
            self.assertNotIn(L.fy.CORE_TOK, repr(sent))
        finally:
            asyncio.run(L.close())

    def test_the_plugin_run_step_is_a_bare_call_and_no_global_bind_exists(self):
        L = H.Loader()
        try:
            self.assertEqual(L.fy.RUN, "function(p){return this(p)}")
            self.assertNotIn("__bindFramey", "".join(e for e, _ in L.fy.main_code() if isinstance(e, str)))
        finally:
            asyncio.run(L.close())


class PanelInJsdom(unittest.TestCase):
    def test_the_real_generated_expressions_build_the_panel_and_run_in_jsdom(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        env = dict(os.environ, FRAMEY_PYTHON=sys.executable)
        out = subprocess.run([node, str(HERE / "js" / "e2e_loader_expr.test.js")], capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn("FAIL ", out.stdout)


if __name__ == "__main__":
    unittest.main()
