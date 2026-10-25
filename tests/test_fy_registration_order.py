import asyncio
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent / 'fixtures' / 'j'))
import jharness as H


class RegistrationOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def setup_page(self):
        loader = H.Loader()
        self.addAsyncCleanup(loader.close)
        for pid in ('a', 'b'):
            loader.plugin(pid, {'api': 1}, main='window.RAN_' + pid + '=1', backend='async def call(m,a): return {}')
        loader.fy.load_backends()
        page = H.PluginPage()
        page.hold_runs = True
        page, task = await H.attach(loader, page=page)
        self.addCleanup(task.cancel)
        self.assertTrue(await H.settle(lambda: len(page.runs) == 2))
        return loader, page

    async def test_restore_waits_for_all_plugin_registration_replies(self):
        loader, page = await self.setup_page()
        self.assertNotIn('Framey.restore()', page.exprs())
        page.reply(page.runs[1])
        await asyncio.sleep(0.05)
        self.assertNotIn('Framey.restore()', page.exprs())
        page.reply(page.runs[0])
        self.assertTrue(await H.settle(lambda: 'Framey.restore()' in page.exprs()))
        self.assertEqual(page.exprs().count('Framey.restore()'), 1)
        self.assertEqual(len(page.runs), 2)

    async def test_failed_plugin_does_not_leave_restoration_waiting(self):
        loader, page = await self.setup_page()
        page.reply(page.runs[0], failure=True)
        await asyncio.sleep(0.05)
        self.assertNotIn('Framey.restore()', page.exprs())
        page.reply(page.runs[1])
        self.assertTrue(await H.settle(lambda: 'Framey.restore()' in page.exprs()))
        self.assertTrue(loader.fy.FAILS)

    async def test_old_pending_registrations_do_not_block_a_new_generation(self):
        loader, page = await self.setup_page()
        old = page.runs[:]
        page.bind(id=999, gen='reload', plugin='_core', method='reload')
        self.assertTrue(await H.settle(lambda: len(page.runs) == 4))
        for request in page.runs[2:]:
            page.reply(request)
        self.assertTrue(await H.settle(lambda: 'Framey.restore()' in page.exprs()))
        self.assertEqual(page.exprs().count('Framey.restore()'), 1)
        start = len(page.sent)
        for request in old:
            page.reply(request)
        await asyncio.sleep(0.05)
        self.assertEqual(page.exprs().count('Framey.restore()'), 1)
        self.assertFalse(any('Framey.redraw' in expression for expression in [o['params'].get('expression', '') for o in page.sent[start:]]))

    async def test_dynamic_registration_can_leave_redrawing_to_its_caller(self):
        loader, page = await self.setup_page()
        for request in page.runs[:]:
            page.reply(request)
        self.assertTrue(await H.settle(lambda: 'Framey.restore()' in page.exprs()))
        start = len(page.sent)
        await loader.fy.page_reload('a', False)
        self.assertTrue(await H.settle(lambda: len(page.runs) == 3))
        page.reply(page.runs[-1])
        await asyncio.sleep(0.05)
        self.assertFalse(any('Framey.redraw' in o['params'].get('expression', '') for o in page.sent[start:]))

    async def test_dynamic_registration_redraws_only_after_running(self):
        loader, page = await self.setup_page()
        for request in page.runs[:]:
            page.reply(request)
        self.assertTrue(await H.settle(lambda: 'Framey.restore()' in page.exprs()))
        start = len(page.sent)
        await loader.fy.page_reload('a')
        self.assertTrue(await H.settle(lambda: len(page.runs) == 3))
        before = page.exprs().count('window.Framey&&Framey.redraw("a")')
        page.reply(page.runs[-1])
        self.assertTrue(await H.settle(lambda: page.exprs().count('window.Framey&&Framey.redraw("a")') == before + 1))
        self.assertTrue(any(o['method'] == 'Runtime.releaseObject' for o in page.sent[start:]))


if __name__ == '__main__':
    unittest.main()
