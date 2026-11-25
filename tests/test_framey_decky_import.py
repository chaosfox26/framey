import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'framey_decky.py'


def load_tool():
    if not TOOL.exists():
        return None
    spec = importlib.util.spec_from_file_location('framey_decky_import_test', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeckyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fy-decky-import-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = load_tool()
        self.assertIsNotNone(self.tool, 'The independent Decky importer is not implemented')

    def frontend_fixture(self, destination):
        """Bind the actual optional provider to the existing owned browser API.

        This exercises client/stub behavior; core admission/verification has its
        own tests. The generated plugin remains a thin wrapper on disk.
        """
        from decky_runtime.runtime import frontend_bundle

        prefix = r"""(() => {
const provider = (__BUNDLE__)();
const register = Framey.register._ownedOriginal || Framey.register;
const owned = view => {
 const api = register(view), modules = api.compat?.modules;
 let active = true;
 const request = (method, arg) => {
  const name = ({decky_code_issue:'issue',decky_code_touch:'touch',decky_code_revoke:'revoke'})[method];
  if (!modules || !name || typeof modules[name] !== 'function')
   return Promise.reject(new Error('No code capability in this owned fixture'));
  return Promise.resolve(name === 'issue' ? modules.issue() : modules[name](arg.token));
 };
 const client = provider.attach({api,alive:()=>active,current:()=>active,request,
  cleanupRequest:request,lost:code=>window.dispatchEvent(new CustomEvent('owned:lost',{detail:code}))});
 api.compat = Object.freeze({modules:client.modules,async mount(...args) {
  const handle = await client.mount(...args); window.ownedMounted = handle; return handle;
 }});
 api.cleanup(() => { active = false; client.dispose(); });
 return api;
};
owned._ownedOriginal = register;
Framey.register = owned;
})();
""".replace('__BUNDLE__', frontend_bundle())
        fixture = self.root / (destination.name + '-owned-client.js')
        fixture.write_text(prefix + (destination / 'main.js').read_text())
        return fixture

    def archive(self, changes=None, extra=None):
        files = {
            'plugin.json': json.dumps({'name': 'Example', 'author': 'Example Author', 'api_version': 2, 'flags': []}).encode(),
            'package.json': b'{"version":"1.2.3","type":"module"}',
            'dist/index.js': b'export default () => ({content: SP_REACT.createElement("div",null,"Original")});',
            'main.py': b'raise RuntimeError("Inspection must never execute this backend")\n',
            'LICENSE': b'Original example license\n',
        }
        files.update(changes or {})
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr('example/' + name, data)
            for name, data in (extra or {}).items():
                z.writestr(name, data)
        value = raw.getvalue()
        return value, hashlib.sha256(value).hexdigest(), files

    def test_import_preserves_original_bytes_and_records_experimental_provenance(self):
        raw, sha, files = self.archive()
        dest = self.root / 'adapted'
        result = self.tool.import_plugin(raw, sha, dest, 'decky-example')
        self.assertEqual(result['id'], 'decky-example')
        self.assertEqual(result['state'], 'experimental')
        manifest = json.loads((dest / 'plugin.json').read_text())
        self.assertEqual(manifest['version'], '1.2.3')
        self.assertEqual(manifest['api'], 1)
        self.assertEqual(manifest['decky_compat']['sha256'], sha)
        self.assertFalse(manifest.get('privileged'))
        for name, value in files.items():
            self.assertEqual((dest / 'vendor' / 'decky' / name).read_bytes(), value)
        record = json.loads((dest / 'decky-provenance.json').read_text())
        self.assertEqual(record['sha256'], sha)
        self.assertFalse(record['runtime_verified'])
        self.assertIn('Steam page integration', record['unverified'])
        self.assertIn('.compat.mount(', (dest / 'main.js').read_text())
        self.assertIn('fy.compat.backend(', (dest / 'backend.py').read_text())
        self.assertNotIn('class DeckyBackend', (dest / 'backend.py').read_text())

    def test_inspection_identifies_modern_and_legacy_without_executing_plugin(self):
        raw, sha, _ = self.archive()
        modern = self.tool.inspect_package(raw, sha)
        self.assertEqual(modern['frontend_format'], 'esm')
        self.assertEqual(modern['backend_api'], 2)
        self.assertFalse(modern['runtime_verified'])
        raw, sha, _ = self.archive({'package.json': b'{"version":"0.1.0"}', 'plugin.json': b'{"name":"Legacy","flags":[]}'})
        legacy = self.tool.inspect_package(raw, sha)
        self.assertEqual(legacy['frontend_format'], 'iife')
        self.assertEqual(legacy['backend_api'], 0)

    def test_wrapped_frontend_is_byte_exact_even_with_template_like_tokens(self):
        source = b'const text = "__NAME__ __API__ __FORMAT__"; export default () => ({content:text});'
        raw, sha, _ = self.archive({'dist/index.js': source})
        dest = self.root / 'tokens'
        self.tool.import_plugin(raw, sha, dest, 'decky-example')
        main = (dest / 'main.js').read_text()
        literal = main.split('.compat.mount(container, ', 1)[1]
        try:
            decoded = json.JSONDecoder().raw_decode(literal)[0]['source']
        except ValueError:
            self.fail("Original frontend was corrupted by wrapper substitutions")
        self.assertEqual(decoded, source.decode())

    def test_modern_module_graph_records_all_original_module_hashes_without_rewriting(self):
        raw, sha, files = self.archive({
            'dist/index.js': b'import {value} from "./chunks/page.js"; export default () => ({content:value});',
            'dist/chunks/page.js': b'export const value="untouched";',
            'dist/chunks/page.js.map': b'{"version":3}',
        })
        report = self.tool.inspect_package(raw, sha)
        self.assertIn('module_graph', report)
        graph = report['module_graph']
        self.assertTrue(report['eligible'])
        self.assertTrue(graph['required'])
        self.assertEqual(graph['state'], 'experimental')
        self.assertEqual(graph['entry'], 'dist/index.js')
        self.assertEqual(graph['files'], {
            path: hashlib.sha256(value).hexdigest() for path, value in files.items()
            if path.endswith('.js') and path.startswith('dist/')
        })
        destination = self.root / 'graph'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        provenance = json.loads((destination / 'decky-provenance.json').read_text())
        self.assertEqual(provenance['module_graph'], graph)
        self.assertEqual(provenance['original_files'], {
            path: hashlib.sha256(value).hexdigest() for path, value in files.items()
        })
        for path, value in files.items():
            self.assertEqual((destination / 'vendor/decky' / path).read_bytes(), value)

    def test_import_stub_does_not_read_or_embed_an_optional_helper_copy(self):
        raw, sha, _ = self.archive({'dist/chunk.js': b'export const value=1;'})
        with mock.patch.object(self.tool._impl, 'ROOT', self.root / 'no-payload'):
            destination = self.root / 'thin-graph'
            self.tool.import_plugin(raw, sha, destination, 'decky-example')
        descriptor = json.JSONDecoder().raw_decode((destination / 'main.js').read_text().split('.compat.mount(container, ', 1)[1])[0]
        self.assertTrue(descriptor['moduleGraph']['required'])
        self.assertNotIn('function createFrameyDeckyModules', (destination / 'main.js').read_text())

    def test_unsupported_modern_graph_is_refused_without_allocating_destination(self):
        for name, value in (('dist/unsupported.mjs', b'export const value=1;'),
                            ('dist/chunk.js', b' ' * (2 * 1024 * 1024 + 1))):
            with self.subTest(name=name):
                raw, sha, _ = self.archive({name: value})
                report = self.tool.inspect_package(raw, sha)
                self.assertFalse(report['eligible'])
                self.assertEqual(report['module_graph']['state'], 'unsupported')
                with self.assertRaises(self.tool.ImportError) as raised:
                    self.tool.import_plugin(raw, sha, self.root / 'refused-graph', 'decky-example')
                self.assertEqual(raised.exception.code, 'unsupported_profile')
                self.assertFalse((self.root / 'refused-graph').exists())

    def test_generated_wrapper_forwards_reviewed_graph_descriptor_without_rewriting(self):
        for modern, extra in ((True, True), (True, False), (False, True)):
            with self.subTest(modern=modern, extra=extra):
                changes = {} if modern else {'package.json': b'{"version":"1.2.3"}'}
                if extra:
                    changes['dist/chunk.js'] = b'export const untouched=1;'
                raw, sha, files = self.archive(changes)
                report = self.tool.inspect_package(raw, sha)
                wrapper = self.tool._impl._main_source(files, report, {})
                actual = json.JSONDecoder().raw_decode(wrapper.split('.compat.mount(container, ', 1)[1])[0]
                self.assertEqual(actual['moduleGraph'], report['module_graph'])
                self.assertEqual(actual['moduleGraph']['required'], modern and extra)
                self.assertEqual(actual['source'], files['dist/index.js'].decode())
                self.assertEqual(actual['format'], 'esm' if modern else 'iife')

    def test_generated_graph_wrapper_fails_closed_without_native_module_sdk(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is unavailable')
        available = subprocess.run([node, '-e', "require.resolve('@playwright/test')"],
                                   cwd=ROOT, capture_output=True, text=True)
        if available.returncode:
            self.skipTest('Playwright is unavailable')
        raw, sha, _ = self.archive({
            'dist/index.js': b'console.log("graph-entry-must-not-evaluate"); export default () => ({content:"unexpected"});',
            'dist/chunk.js': b'export const original=1;',
        })
        destination = self.root / 'no-module-sdk'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        script = r'''
const fs=require('fs');const {chromium}=require('@playwright/test');
(async()=>{
 let browser;
 try{browser=await chromium.launch({headless:true});}
 catch(error){if(/Executable doesn't exist|browser executable/i.test(error.message)){
  console.log(JSON.stringify({skip:'Chromium is unavailable'}));return;}throw error;}
 try{
  const page=await browser.newPage(),consoleMessages=[],pageErrors=[],externalRequests=[];
  page.on('console',message=>consoleMessages.push(message.text()));
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.route('**/*',route=>route.request().url()==='https://framey-import.test/'
   ?route.fulfill({contentType:'text/html',body:'<div id="panel" style="height:600px"></div>'})
   :(externalRequests.push(route.request().url()),route.abort()));
  await page.goto('https://framey-import.test/');
  await page.addScriptTag({content:`window.ownedCleanup=[];window.Framey={register(view){
   window.importedView=view;return{cleanup(fn){window.ownedCleanup.push(fn)},
   call(){return Promise.reject(new Error('Backend unavailable'))},
   events:{on(){return()=>{}}},notify(){return{dismiss(){}}}};}};`});
  await page.addScriptTag({content:fs.readFileSync(process.argv[1],'utf8')});
  const notices=[];
  for(let render=0;render<2;render++){
   await page.evaluate(()=>window.importedView.render(document.getElementById('panel')).catch(error=>{window.ownedRenderError=error.message}));
   await page.waitForFunction(()=>document.getElementById('panel').textContent.includes('Panel could not be loaded'),null,{timeout:3000});
   notices.push(await page.locator('#panel').innerText());
  }
  await page.evaluate(()=>window.ownedCleanup.forEach(cleanup=>cleanup()));
  console.log(JSON.stringify({notices,entryEvaluated:consoleMessages.includes('graph-entry-must-not-evaluate'),
   pageErrors,externalRequests,iframesAfterCleanup:await page.locator('iframe').count()}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(self.frontend_fixture(destination))], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        if 'skip' in actual:
            self.skipTest(actual['skip'])
        self.assertEqual(len(actual['notices']), 2)
        self.assertTrue(all('Panel could not be loaded' in notice for notice in actual['notices']))
        self.assertFalse(actual['entryEvaluated'])
        self.assertEqual(actual['pageErrors'], [])
        self.assertEqual(actual['externalRequests'], [])
        self.assertEqual(actual['iframesAfterCleanup'], 0)

    def test_background_backend_survives_framey_idle_after_frontend_call(self):
        # A timer or event source must not be killed by Framey's ordinary lazy-worker idle policy.
        raw, sha, _ = self.archive({'main.py': b'class Plugin:\n    async def ping(self): return True\n'})
        destination = self.root / 'background'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        with mock.patch.object(sys, 'path', [str(ROOT), str(ROOT / 'tests/fixtures/a'), *sys.path]):
            import fy_supervisor
            import harness
        worker_root = self.root / 'worker'
        worker_root.mkdir()
        for name in ('fy_worker.py', 'fy_proto.py', 'fy_sdk.py', 'fy_store.py', 'fy_decky_runtime.py'):
            shutil.copy(ROOT / name, worker_root / name)

        async def check():
            host = harness.FakeHost(worker_root)
            import fy_decky_runtime
            from optional_backend_fixture import install_runtime
            package = ROOT / 'decky_runtime'
            files = {path.relative_to(package).as_posix(): path.read_bytes()
                     for path in package.rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.name != 'runtime-version.json'}
            host.CONF = host.HOME / ".config/framey/settings.json"
            install_runtime(host.HOME, files)
            enabled = await fy_decky_runtime.resume(host)
            self.assertTrue(enabled['enabled'], enabled)
            host.backend_runtime = lambda _pid: fy_decky_runtime.worker_descriptor(host)
            host.backend_admitted = lambda _pid: fy_decky_runtime.allowed(host, json.loads((destination / 'plugin.json').read_text()))
            supervisor = fy_supervisor.Supervisor(host)
            try:
                with mock.patch.dict(fy_supervisor.LIMITS, {'idle': (0.05, 0.01, 0.1)}):
                    supervisor.register('decky-example', destination,
                        json.loads((destination / 'plugin.json').read_text()))
                with mock.patch.dict(os.environ, {'HOME': str(host.HOME)}):
                    result = await supervisor.call('decky-example', 'ping', {'args': [], 'legacy': False})
                self.assertEqual(result, {'__framey_decky_rpc_result__': 1, 'value': True})
                before = supervisor.status()['decky-example']['os_pid']
                await asyncio.sleep(0.2)
                after = supervisor.status()['decky-example']
                self.assertEqual(after['state'], 'ready')
                self.assertEqual(after['os_pid'], before)
            finally:
                await supervisor.shutdown()
                await fy_decky_runtime.shutdown(host)
        asyncio.run(check())

    def test_digest_mismatch_does_not_create_destination(self):
        raw, _, _ = self.archive()
        dest = self.root / 'not-created'
        with self.assertRaises(Exception):
            self.tool.import_plugin(raw, '0' * 64, dest, 'decky-example')
        self.assertFalse(dest.exists())

    def test_root_native_and_remote_binary_profiles_are_refused(self):
        variants = [
            ({'plugin.json': b'{"name":"Root","flags":["root"]}'}, {}),
            ({}, {'example/bin/helper': b'\x7fELF\x02\x01' + b'\x00' * 12 + b'\xb7\x00' + b'\x00' * 44}),
            ({'package.json': b'{"version":"1.0.0","remote_binary":{"name":"helper"}}'}, {}),
        ]
        for changes, extra in variants:
            with self.subTest(changes=changes):
                raw, sha, _ = self.archive(changes, extra)
                report = self.tool.inspect_package(raw, sha)
                self.assertFalse(report['eligible'])
                with self.assertRaises(self.tool.ImportError):
                    self.tool.import_plugin(raw, sha, self.root / 'refused', 'decky-example')
                self.assertFalse((self.root / 'refused').exists())

    def test_unsafe_archive_path_and_link_are_refused(self):
        raw, sha, _ = self.archive(extra={'../escape': b'never'})
        with self.assertRaises(Exception):
            self.tool.import_plugin(raw, sha, self.root / 'refused', 'decky-example')
        self.assertFalse((self.root / 'refused').exists())
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            link = zipfile.ZipInfo('example/linked')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(link, '/tmp')
        raw = out.getvalue()
        with self.assertRaises(Exception):
            self.tool.inspect_package(raw, hashlib.sha256(raw).hexdigest())

    def test_existing_output_and_symlink_parent_are_never_replaced(self):
        raw, sha, _ = self.archive()
        dest = self.root / 'existing'
        dest.mkdir()
        (dest / 'keep').write_text('user data')
        with self.assertRaises(self.tool.ImportError):
            self.tool.import_plugin(raw, sha, dest, 'decky-example')
        self.assertEqual((dest / 'keep').read_text(), 'user data')
        link = self.root / 'link'
        link.symlink_to(dest, target_is_directory=True)
        with self.assertRaises(self.tool.ImportError):
            self.tool.import_plugin(raw, sha, link / 'new', 'decky-example')
        self.assertFalse((dest / 'new').exists())

    def test_invalid_or_reserved_id_and_unknown_backend_version_are_refused(self):
        raw, sha, _ = self.archive()
        for pid in ('../escape', 'settings', '_core', 'decky-store', ''):
            with self.subTest(pid=pid), self.assertRaises(self.tool.ImportError):
                self.tool.import_plugin(raw, sha, self.root / 'refused', pid)
        raw, sha, _ = self.archive({'plugin.json': b'{"name":"New","api_version":99,"flags":[]}'})
        self.assertFalse(self.tool.inspect_package(raw, sha)['eligible'])

    def test_png_assets_do_not_change_originals_or_plugin_eligibility(self):
        included = png_fixture(285, 276)
        oversized = png_fixture(size=262145)
        raw, sha, files = self.archive({
            'dist/assets/album.png': included,
            'assets/large.png': oversized,
            'assets/icon.svg': b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        })
        dest = self.root / 'asset-package'
        result = self.tool.import_plugin(raw, sha, dest, 'decky-example')
        self.assertTrue(result['eligible'])
        for name, value in files.items():
            self.assertEqual((dest / 'vendor/decky' / name).read_bytes(), value)
        provenance = json.loads((dest / 'decky-provenance.json').read_text())
        self.assertIn('asset_profile', provenance, 'Asset delivery limits must be recorded truthfully')
        profile = provenance['asset_profile']
        self.assertEqual(profile['included'], [
            {'path': 'dist/assets/album.png', 'bytes': len(included), 'width': 285, 'height': 276},
        ])
        self.assertEqual({value['path']: value['reason'] for value in profile['omitted']}, {
            'assets/icon.svg': 'unsupported_format', 'assets/large.png': 'image_bytes_limit',
        })

    def test_unsupported_asset_binding_omits_pngs_without_rejecting_original_package(self):
        image = png_fixture()
        raw, sha, files = self.archive({
            'plugin.json': b'{"name":"Example/Unsafe","api_version":2,"flags":[]}',
            'dist/assets/album.png': image,
        })
        destination = self.root / 'unsupported-asset-binding'
        result = self.tool.import_plugin(raw, sha, destination, 'decky-example')
        self.assertTrue(result['eligible'])
        self.assertEqual(result['name'], 'Example/Unsafe')
        provenance = json.loads((destination / 'decky-provenance.json').read_text())
        self.assertEqual(provenance['asset_profile']['included'], [])
        self.assertEqual(provenance['asset_profile']['omitted'], [
            {'path': 'dist/assets/album.png', 'reason': 'unsupported_package_name'},
        ])
        for name, value in files.items():
            self.assertEqual((destination / 'vendor/decky' / name).read_bytes(), value)

    def test_empty_asset_descriptor_does_not_restrict_an_original_plugin_name(self):
        raw, sha, _ = self.archive({'plugin.json': b'{"name":"Example/Unsafe","api_version":2,"flags":[]}'})
        report = self.tool.inspect_package(raw, sha)
        self.assertTrue(report['eligible'])
        _, files, _, _ = self.tool._contents(raw, sha)
        wrapper = self.tool._impl._main_source(files, report, {})
        actual = json.JSONDecoder().raw_decode(wrapper.split('.compat.mount(container, ', 1)[1])[0]
        self.assertEqual(actual['assets'], {})
        self.assertEqual(actual['name'], 'Example/Unsafe')
        self.assertNotIn('createFrameyDeckyAssets', wrapper)

    def test_generated_wrapper_decodes_original_pngs_and_recreates_resolver_on_render(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is unavailable')
        available = subprocess.run([node, '-e', "require.resolve('@playwright/test')"],
                                   cwd=ROOT, capture_output=True, text=True)
        if available.returncode:
            self.skipTest('Playwright is unavailable')
        source = b'''const h = SP_REACT.createElement;
export default () => ({content:h('section',null,
    h('img',{id:'direct',src:'http://127.0.0.1:1337/plugins/Example/assets/__NAME__.png'}),
    h('img',{id:'fallback',src:'data:image/png;base64,broken',onError:event=>{
        event.currentTarget.src='/plugins/Example/dist/assets/__NAME__.png';
    }}),
    h('img',{id:'foreign',src:'http://127.0.0.1:1337/plugins/Other/assets/__NAME__.png'})
)});'''
        image = png_fixture(3, 2)
        raw, sha, _ = self.archive({'dist/index.js': source, 'dist/assets/__NAME__.png': image})
        destination = self.root / 'generated-assets'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        script = r'''
const fs=require('fs'),path=require('path');
const {chromium}=require('@playwright/test');
(async()=>{
 let browser;
 try { browser=await chromium.launch({headless:true}); }
 catch(error) { if(/Executable doesn't exist|browser executable/i.test(error.message)) {
   console.log(JSON.stringify({skip:'Chromium is unavailable'}));return;
 } throw error; }
 try {
  const page=await browser.newPage();
  await page.route('**/*',route=>route.request().url()==='https://framey-import.test/'
   ?route.fulfill({contentType:'text/html',body:'<div id="panel" style="height:600px"></div>'})
   :route.abort());
  await page.goto('https://framey-import.test/');
  await page.addScriptTag({content:`window.ownedCleanup=[];window.Framey={register(view){
   window.importedView=view;return{cleanup(fn){window.ownedCleanup.push(fn)},
   call(){return Promise.reject(new Error('No backend in wrapper test'))},
   events:{on(){return()=>{}}},notify(){return{dismiss(){}}}};}};`});
  await page.addScriptTag({content:fs.readFileSync(process.argv[1],'utf8')});
  const original=fs.readFileSync(process.argv[2]);
  const expected='data:image/png;base64,'+original.toString('base64');
  const results=[];
  for(let render=0;render<2;render++){
   await page.evaluate(()=>window.importedView.render(document.getElementById('panel')));
   await page.waitForFunction(()=>{
    const document=window.document.querySelector('iframe')?.contentDocument;
    return ['direct','fallback'].every(id=>document?.getElementById(id)?.naturalWidth===3);
   },null,{timeout:3000});
   results.push(await page.evaluate(expected=>{
    const document=window.document.querySelector('iframe').contentDocument;
    return {direct:[document.getElementById('direct').naturalWidth,document.getElementById('direct').naturalHeight],
     fallback:[document.getElementById('fallback').naturalWidth,document.getElementById('fallback').naturalHeight],
     exactBytes:['direct','fallback'].every(id=>document.getElementById(id).src===expected),
     foreignUnchanged:document.getElementById('foreign').src==='http://127.0.0.1:1337/plugins/Other/assets/__NAME__.png'};
   },expected));
  }
  await page.evaluate(()=>window.ownedCleanup.forEach(cleanup=>cleanup()));
  console.log(JSON.stringify({results,iframesAfterCleanup:await page.locator('iframe').count()}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(self.frontend_fixture(destination)), str(destination / 'vendor/decky/dist/assets/__NAME__.png')], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        if 'skip' in rendered:
            self.skipTest(rendered['skip'])
        self.assertEqual(rendered['results'], [
            {'direct': [3, 2], 'fallback': [3, 2], 'exactBytes': True, 'foreignUnchanged': True},
            {'direct': [3, 2], 'fallback': [3, 2], 'exactBytes': True, 'foreignUnchanged': True},
        ])
        self.assertEqual(rendered['iframesAfterCleanup'], 0)

    def test_generated_wrapper_sidebar_preserves_local_page_state_and_rejects_routing_props(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is unavailable')
        available = subprocess.run([node, '-e', "require.resolve('@playwright/test')"],
                                   cwd=ROOT, capture_output=True, text=True)
        if available.returncode:
            self.skipTest('Playwright is unavailable')
        source = b'''const R=SP_REACT,h=R.createElement;window.cleaned=0;
function Settings(){const[count,setCount]=R.useState(0),input=R.useRef(null);
 window.inputRef=input;R.useEffect(()=>()=>{window.cleaned++;},[]);
 return h('section',null,h('output',{id:'count'},String(count)),h('input',{id:'entry',ref:input,defaultValue:''}),
  h('button',{id:'increment',onClick:()=>setCount(value=>value+1)},'Increment local counter'));}
export default()=>({content:h(DFL.SidebarNavigation,{title:'Synthetic local settings',showTitle:true,pages:[
 {identifier:'settings',title:'Settings',content:h(Settings)},
 {identifier:'info',title:'Info',content:h('p',null,'Synthetic local information')}
]})});'''
        raw, sha, _ = self.archive({'dist/index.js': source})
        destination = self.root / 'generated-sidebar'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        unsupported_source = b'''export default()=>({content:SP_REACT.createElement(DFL.SidebarNavigation,{
 title:'Unsupported route fixture',route:'/steam-rejected-target',pages:[{title:'Settings',content:'Local text'}]
})});'''
        raw, sha, _ = self.archive({'dist/index.js': unsupported_source})
        unsupported_destination = self.root / 'unsupported-sidebar'
        self.tool.import_plugin(raw, sha, unsupported_destination, 'decky-example')
        script = r'''
const fs=require('fs');const{chromium}=require('@playwright/test');
(async()=>{
 let browser;try{browser=await chromium.launch({headless:true});}
 catch(error){if(/Executable doesn't exist|browser executable/i.test(error.message)){
  console.log(JSON.stringify({skip:'Chromium is unavailable'}));return;}throw error;}
 try{
  const context=await browser.newContext();
  await context.route('**/*',route=>route.request().url()==='https://framey-import.test/'
   ?route.fulfill({contentType:'text/html',body:'<div id="panel" style="height:600px"></div>'}):route.abort());
  async function prepare(file){
   const page=await context.newPage();await page.goto('https://framey-import.test/');
   await page.addScriptTag({content:`window.ownedCleanup=[];window.Framey={register(view){window.importedView=view;
    return{cleanup(fn){window.ownedCleanup.push(fn)},events:{on(){return()=>{}}},
     call(){return Promise.reject(new Error('No backend in wrapper test'))},notify(){return{dismiss(){}}}};}};`});
   await page.addScriptTag({content:fs.readFileSync(file,'utf8')+'\nwindow.readWrapperStatus=()=>window.ownedMounted.status();'});
   await page.evaluate(()=>window.importedView.render(document.getElementById('panel')).catch(error=>{window.ownedRenderError=error.message}));return page;
  }
  const page=await prepare(process.argv[1]),panel=page.frameLocator('iframe');
  await panel.getByRole('tab',{name:'Settings',exact:true}).waitFor({state:'visible',timeout:3000});
  await panel.locator('#increment').click();await panel.locator('#entry').fill('Retained local input');
  await page.evaluate(()=>{const realm=document.querySelector('iframe').contentWindow;
   window.oldRealm=realm;window.savedNode=realm.document.getElementById('entry');window.savedRef=realm.inputRef;});
  await panel.getByRole('tab',{name:'Info',exact:true}).click();
  const inactiveHidden=await panel.locator('#entry').isHidden();
  await panel.getByRole('tab',{name:'Info',exact:true}).press('Home');
  const retained=await page.evaluate(()=>{const realm=document.querySelector('iframe').contentWindow;
   return{count:realm.document.getElementById('count').textContent,
    value:realm.document.getElementById('entry').value,sameNode:realm.document.getElementById('entry')===window.savedNode,
    sameRef:realm.inputRef===window.savedRef,focus:realm.document.activeElement.textContent,cleaned:realm.cleaned};});
  const scope=await page.evaluate(()=>window.readWrapperStatus().sidebar);
  await page.evaluate(()=>{window.oldIncrement=window.oldRealm.document.getElementById('increment');
   window.importedView.render(document.getElementById('panel'));});
  await panel.getByRole('tab',{name:'Settings',exact:true}).waitFor({state:'visible',timeout:3000});
  const rerendered=await page.evaluate(()=>{window.oldIncrement.click();const doc=document.querySelector('iframe').contentDocument;
   return{count:doc.getElementById('count').textContent,value:doc.getElementById('entry').value,
    previousCleaned:window.oldRealm.cleaned,previousCount:window.savedNode.ownerDocument.getElementById('count')?.textContent||null};});
  await page.evaluate(()=>{window.finalRealm=document.querySelector('iframe').contentWindow;
   window.ownedCleanup.forEach(cleanup=>cleanup());});
  const disposed=await page.evaluate(()=>({cleaned:window.finalRealm.cleaned,frames:document.querySelectorAll('iframe').length}));
  const rejected=await prepare(process.argv[2]);
  await rejected.waitForFunction(()=>window.ownedRenderError?.includes('SidebarNavigation.props.route'),null,{timeout:3000});
  const failure=await rejected.evaluate(()=>({unsupported:window.ownedRenderError,
   plainFailure:document.querySelector('#panel').textContent.includes('Panel could not be loaded')}));
  await rejected.evaluate(()=>window.ownedCleanup.forEach(cleanup=>cleanup()));
  console.log(JSON.stringify({inactiveHidden,retained,scope,rerendered,disposed,failure}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(self.frontend_fixture(destination)),
                                 str(self.frontend_fixture(unsupported_destination))], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        if 'skip' in rendered:
            self.skipTest(rendered['skip'])
        self.assertTrue(rendered['inactiveHidden'])
        self.assertEqual(rendered['retained'], {
            'count': '1', 'value': 'Retained local input', 'sameNode': True, 'sameRef': True,
            'focus': 'Settings', 'cleaned': 0,
        })
        self.assertEqual(rendered['scope']['scope'], 'framey-local-pages-only')
        self.assertFalse(rendered['scope']['routeReporting'])
        self.assertEqual(rendered['scope']['pageLifetime'], 'lazy-retained-until-removal')
        self.assertEqual(rendered['rerendered'], {'count': '0', 'value': '', 'previousCleaned': 1,
                                                 'previousCount': None})
        self.assertEqual(rendered['disposed'], {'cleaned': 1, 'frames': 0})
        self.assertIn('SidebarNavigation.props.route', rendered['failure']['unsupported'])
        self.assertTrue(rendered['failure']['plainFailure'])

    def test_generated_wrapper_runs_owned_menu_actions_and_disposes_stale_menu_handlers(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is unavailable')
        available = subprocess.run([node, '-e', "require.resolve('@playwright/test')"],
                                   cwd=ROOT, capture_output=True, text=True)
        if available.returncode:
            self.skipTest('Playwright is unavailable')
        source = b'''const R=SP_REACT,h=R.createElement;
window.actions=0;window.cancels=0;
function Panel(){const[count,setCount]=R.useState(0);
 return h('section',null,h('output',{id:'count'},String(count)),
  h('button',{id:'open-menu',onClick:event=>{
   window.menuHandle=DFL.showContextMenu(h(DFL.Menu,{label:'Synthetic players',onCancel:()=>window.cancels++},
    h(DFL.MenuItem,{onSelected:()=>{window.actions++;setCount(value=>value+1);}},'Synthetic player A'),
    h(DFL.MenuItem,{disabled:true,onSelected:()=>{window.actions++;}},'Disabled synthetic player')
   ),event.currentTarget);
  }},'Choose synthetic player'));
}
export default()=>({content:h(Panel)});'''
        raw, sha, _ = self.archive({'dist/index.js': source})
        destination = self.root / 'generated-menus'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        script = r'''
const fs=require('fs');const {chromium}=require('@playwright/test');
(async()=>{
 let browser;try{browser=await chromium.launch({headless:true});}
 catch(error){if(/Executable doesn't exist|browser executable/i.test(error.message)){
  console.log(JSON.stringify({skip:'Chromium is unavailable'}));return;}throw error;}
 try{
  const context=await browser.newContext();
  await context.route('**/*',route=>route.request().url()==='https://framey-import.test/'
   ?route.fulfill({contentType:'text/html',body:'<div id="panel" style="height:600px"></div>'}):route.abort());
  const page=await context.newPage();await page.goto('https://framey-import.test/');
  await page.addScriptTag({content:`window.ownedCleanup=[];window.Framey={register(view){window.importedView=view;
   return{cleanup(fn){window.ownedCleanup.push(fn)},events:{on(){return()=>{}}},
   call(){return Promise.reject(new Error('No backend in wrapper test'))},notify(){return{dismiss(){}}}};}};`});
  await page.addScriptTag({content:fs.readFileSync(process.argv[1],'utf8')});
  await page.evaluate(()=>window.importedView.render(document.getElementById('panel')));
  const panel=page.frameLocator('iframe');
  await panel.locator('#open-menu').click();
  const menu=panel.getByRole('menu',{name:'Synthetic players'});
  await menu.waitFor({state:'visible',timeout:3000});
  const disabled=await menu.getByRole('menuitem',{name:'Disabled synthetic player'}).isDisabled();
  await menu.getByRole('menuitem',{name:'Synthetic player A',exact:true}).click();
  await panel.locator('#count').filter({hasText:'1'}).waitFor({timeout:3000});
  const selected=await page.evaluate(()=>{const doc=document.querySelector('iframe').contentDocument;
   return{count:doc.getElementById('count').textContent,menus:doc.querySelectorAll('[role=menu]').length,
    focused:doc.activeElement.id};});
  await panel.locator('#open-menu').click();
  await menu.getByRole('menuitem',{name:'Cancel',exact:true}).click();
  const canceled=await page.evaluate(()=>{const realm=document.querySelector('iframe').contentWindow;
   return{actions:realm.actions,cancels:realm.cancels,menus:realm.document.querySelectorAll('[role=menu]').length};});
  await panel.locator('#open-menu').click();
  await menu.waitFor({state:'visible',timeout:3000});
  await page.evaluate(()=>{window.oldRealm=document.querySelector('iframe').contentWindow;
   window.oldHandle=window.oldRealm.menuHandle;
   window.oldItem=window.oldRealm.document.querySelector('[role=menuitem]');
   window.importedView.render(document.getElementById('panel'));});
  const stale=await page.evaluate(()=>{window.oldItem.click();let showThrew=false;
   try{window.oldHandle.Show();}catch{showThrew=true;}
   return{actions:window.oldRealm.actions,showThrew};});
  await panel.locator('#open-menu').click();
  await menu.getByRole('menuitem',{name:'Synthetic player A',exact:true}).click();
  await panel.locator('#count').filter({hasText:'1'}).waitFor({timeout:3000});
  await panel.locator('#open-menu').click();await menu.waitFor({state:'visible',timeout:3000});
  await page.evaluate(()=>window.ownedCleanup.forEach(cleanup=>cleanup()));
  console.log(JSON.stringify({disabled,selected,canceled,stale,
   iframesAfterCleanup:await page.locator('iframe').count()}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(self.frontend_fixture(destination))], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        if 'skip' in rendered:
            self.skipTest(rendered['skip'])
        self.assertTrue(rendered['disabled'])
        self.assertEqual(rendered['selected'], {'count': '1', 'menus': 0, 'focused': 'open-menu'})
        self.assertEqual(rendered['canceled'], {'actions': 1, 'cancels': 1, 'menus': 0})
        self.assertEqual(rendered['stale'], {'actions': 1, 'showThrew': True})
        self.assertEqual(rendered['iframesAfterCleanup'], 0)

    def test_generated_wrapper_requests_native_navigation_and_revokes_stale_handlers(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is unavailable')
        available = subprocess.run([node, '-e', "require.resolve('@playwright/test')"],
                                   cwd=ROOT, capture_output=True, text=True)
        if available.returncode:
            self.skipTest('Playwright is unavailable')
        source = b'''const h=SP_REACT.createElement;
export default()=>({content:h('section',null,
 h('button',{id:'accepted',onClick:()=>DFL.Navigation.NavigateToExternalWeb('https://example.test/accepted')},'Request link'),
 h('button',{id:'rejected',onClick:()=>window.open('https://example.test/rejected?private=value','_blank','noopener')},'Rejected link'),
 h('button',{id:'invalid',onClick:()=>DFL.Navigation.NavigateToExternalWeb('http://example.test/invalid')},'Invalid link')
)});'''
        raw, sha, _ = self.archive({'dist/index.js': source})
        destination = self.root / 'generated-navigation'
        self.tool.import_plugin(raw, sha, destination, 'decky-example')
        script = r'''
const fs=require('fs');const {chromium}=require('@playwright/test');
(async()=>{
 let browser;try{browser=await chromium.launch({headless:true});}
 catch(error){if(/Executable doesn't exist|browser executable/i.test(error.message)){
  console.log(JSON.stringify({skip:'Chromium is unavailable'}));return;}throw error;}
 try{
  const context=await browser.newContext();
  await context.route('**/*',route=>route.request().url()==='https://framey-import.test/'
   ?route.fulfill({contentType:'text/html',body:'<div id="panel" style="height:600px"></div>'}):route.abort());
  const page=await context.newPage();await page.goto('https://framey-import.test/');
  await page.addScriptTag({content:`window.calls=[];window.notices=[];window.ownedCleanup=[];
   window.Framey={register(view){window.importedView=view;return{
    cleanup(fn){window.ownedCleanup.push(fn)},events:{on(){return()=>{}}},
    call(){return Promise.reject(new Error('No backend in wrapper test'))},
    notify(message){window.notices.push(message);return{dismiss(){}}},
    openURL(url){window.calls.push(url);return url.includes('/rejected')
     ?Promise.reject(new Error('Private native failure')):Promise.resolve({requested:true});}
   };}};`});
  await page.addScriptTag({content:fs.readFileSync(process.argv[1],'utf8')});
  await page.evaluate(()=>window.importedView.render(document.getElementById('panel')));
  const panel=page.frameLocator('iframe');
  await panel.locator('#accepted').click();
  await page.waitForFunction(()=>window.calls.length===1,null,{timeout:3000});
  await panel.locator('#rejected').click();
  await page.waitForFunction(()=>window.notices.length===1,null,{timeout:3000});
  await panel.locator('#invalid').click();
  await page.waitForFunction(()=>window.notices.length===2,null,{timeout:3000});
  const before=await page.evaluate(()=>{
   window.staleOpen=window.document.querySelector('iframe').contentWindow.open;
   return{calls:[...window.calls],notices:[...window.notices]};});
  await page.evaluate(()=>window.importedView.render(document.getElementById('panel')));
  await panel.locator('#accepted').click();
  await page.waitForFunction(()=>window.calls.length===3,null,{timeout:3000});
  await page.evaluate(()=>{window.ownedCleanup.forEach(cleanup=>cleanup());
   window.staleResult=window.staleOpen('https://example.test/stale');});
  await page.waitForTimeout(25);
  console.log(JSON.stringify({before,callsAfterDispose:await page.evaluate(()=>window.calls),
   staleReturnsNull:await page.evaluate(()=>window.staleResult===null),
   iframesAfterCleanup:await page.locator('iframe').count()}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(self.frontend_fixture(destination))], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        if 'skip' in rendered:
            self.skipTest(rendered['skip'])
        accepted, rejected = 'https://example.test/accepted', 'https://example.test/rejected?private=value'
        self.assertEqual(rendered['before']['calls'], [accepted, rejected])
        self.assertEqual(rendered['before']['notices'], [
            {'title': 'Link unavailable', 'body': 'Link could not be opened. This action is unavailable here.'},
            {'title': 'Link unavailable', 'body': 'Link could not be opened. This action is unavailable here.'},
        ])
        self.assertEqual(rendered['callsAfterDispose'], [accepted, rejected, accepted])
        self.assertTrue(rendered['staleReturnsNull'])
        self.assertEqual(rendered['iframesAfterCleanup'], 0)


def png_fixture(width=1, height=1, size=None):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))

    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    pixels = (b'\x00' + b'\x44\x88\xcc\xff' * width) * height
    result = b'\x89PNG\r\n\x1a\n' + ihdr + chunk(b'IDAT', zlib.compress(pixels))
    end = chunk(b'IEND', b'')
    if size is not None:
        padding = size - len(result) - len(end) - 12
        result += chunk(b'tEXt', b'k\x00' + b'x' * (padding - 2))
    return result + end


class DeckyModuleGraphTests(unittest.TestCase):
    def setUp(self):
        self.implementation = load_tool()._impl

    def profile(self, files, frontend_format='esm'):
        self.assertTrue(hasattr(self.implementation, 'build_module_graph_profile'),
                        'The importer needs an explicit bounded native module graph profile')
        return self.implementation.build_module_graph_profile(files, frontend_format)

    def test_single_entry_and_legacy_keep_blob_limits(self):
        large = b' ' * 5_000_000
        for files, format in (({'dist/index.js': large}, 'esm'),
                              ({'dist/index.js': large, 'dist/old.js': large}, 'iife')):
            with self.subTest(format=format):
                graph = self.profile(files, format)
                self.assertFalse(graph['required'])
                self.assertEqual(graph['state'], 'not_required')
                self.assertEqual(graph['reasons'], [])

    def test_graph_count_per_file_and_total_limits_are_inclusive(self):
        small = {'dist/index.js': b'0', **{f'dist/chunk-{i}.js': b'0' for i in range(63)}}
        self.assertEqual(self.profile(small)['state'], 'experimental')
        too_many = self.profile({**small, 'dist/extra.js': b'0'})
        self.assertEqual(too_many['state'], 'unsupported')
        self.assertIn('module_count_limit', too_many['reasons'])
        full = {f'dist/{name}.js': b' ' * (2 * 1024 * 1024) for name in ('index', 'a', 'b', 'c')}
        self.assertEqual(self.profile(full)['state'], 'experimental')
        too_large = self.profile({**full, 'dist/a.js': full['dist/a.js'] + b'0'})
        self.assertIn('module_bytes_limit', too_large['reasons'])
        self.assertIn('total_bytes_limit', too_large['reasons'])
        self.assertEqual(too_large['state'], 'unsupported')

    def test_unknown_names_noncanonical_paths_and_missing_entry_are_explicit(self):
        for name in ('dist/a.mjs', 'dist/a.JS', 'dist/a b.js', 'dist/.hidden.js',
                     'dist/a%20b.js', 'dist/a/../b.js', 'dist/a.js?query'):
            with self.subTest(name=name):
                graph = self.profile({'dist/index.js': b'0', name: b'1'})
                self.assertTrue(graph['required'])
                self.assertEqual(graph['state'], 'unsupported')
                self.assertIn('unsupported_module_path', graph['reasons'])
        graph = self.profile({'dist/a.js': b'0', 'dist/b.js': b'1'})
        self.assertEqual(graph['state'], 'unsupported')
        self.assertIn('missing_entry', graph['reasons'])


class DeckyPngAssetTests(unittest.TestCase):
    def setUp(self):
        self.implementation = load_tool()._impl

    def build(self, files):
        self.assertTrue(hasattr(self.implementation, 'build_png_asset_map'),
                        'The importer needs a bounded package-local PNG map')
        return self.implementation.build_png_asset_map(files)

    def test_supported_roots_map_exact_original_bytes_as_png_only(self):
        album = png_fixture(3, 2)
        icon = png_fixture(1, 1)
        assets, profile = self.build({'dist/assets/album.png': album, 'assets/icon.PNG': icon})
        self.assertEqual(set(assets), {'dist/assets/album.png', 'assets/icon.PNG'})
        for path, expected in (('dist/assets/album.png', album), ('assets/icon.PNG', icon)):
            prefix, encoded = assets[path].split(',', 1)
            self.assertEqual(prefix, 'data:image/png;base64')
            self.assertEqual(base64.b64decode(encoded, validate=True), expected)
        self.assertEqual(profile['total_bytes'], len(album) + len(icon))
        self.assertEqual(profile['omitted'], [])

    def test_unsafe_or_outside_paths_and_non_png_media_are_omitted(self):
        files = {path: png_fixture() for path in (
            '../assets/a.png', '/assets/a.png', 'assets/../a.png', 'assets//a.png',
            'assets\\a.png', 'assets/%2e%2e/a.png', 'assets/a.png?x', 'images/a.png',
        )}
        files.update({path: b'unsupported' for path in (
            'assets/a.svg', 'assets/a.css', 'assets/a.js', 'assets/a.woff', 'assets/a.mp3',
        )})
        files['dist/index.js'] = b'not a media candidate'
        assets, profile = self.build(files)
        self.assertEqual(assets, {})
        omitted = {value['path']: value['reason'] for value in profile['omitted']}
        self.assertEqual(omitted['../assets/a.png'], 'invalid_path')
        self.assertEqual(omitted['assets/%2e%2e/a.png'], 'invalid_path')
        self.assertEqual(omitted['images/a.png'], 'unsupported_path')
        self.assertEqual(omitted['assets/a.svg'], 'unsupported_format')
        self.assertNotIn('dist/index.js', omitted)
        self.assertEqual(len(omitted), 13)

    def test_signature_ihdr_and_header_crc_are_validated(self):
        valid = png_fixture()
        wrong_length = valid[:8] + struct.pack('>I', 14) + valid[12:]
        wrong_crc = valid[:29] + bytes([valid[29] ^ 1]) + valid[30:]
        assets, profile = self.build({
            'assets/script.png': b'<svg onload="doSomething()"/>',
            'assets/truncated.png': valid[:24],
            'assets/wrong-length.png': wrong_length,
            'assets/wrong-crc.png': wrong_crc,
        })
        self.assertEqual(assets, {})
        self.assertEqual({entry['reason'] for entry in profile['omitted']}, {'invalid_png_header'})

    def test_dimensions_and_pixels_have_independent_bounds(self):
        files = {'assets/edge.png': png_fixture(4096, 1),
                 'assets/pixels.png': png_fixture(2000, 2000)}
        for index, dimensions in enumerate(((0, 1), (1, 0), (4097, 1), (1, 4097), (2001, 2000))):
            files['assets/bad-' + str(index) + '.png'] = png_fixture(*dimensions)
        assets, profile = self.build(files)
        self.assertEqual(set(assets), {'assets/edge.png', 'assets/pixels.png'})
        self.assertEqual(len(profile['omitted']), 5)
        self.assertEqual({entry['reason'] for entry in profile['omitted']}, {'dimensions_limit'})

    def test_per_image_and_total_bytes_limits_are_exact(self):
        image = png_fixture(size=262144)
        assets, profile = self.build({'assets/a.png': image, 'assets/b.png': image,
                                      'assets/c.png': png_fixture(),
                                      'assets/d.png': png_fixture(size=262145)})
        self.assertEqual(set(assets), {'assets/a.png', 'assets/b.png'})
        self.assertEqual(profile['total_bytes'], 524288)
        self.assertEqual({entry['path']: entry['reason'] for entry in profile['omitted']}, {
            'assets/c.png': 'total_bytes_limit', 'assets/d.png': 'image_bytes_limit',
        })

    def test_image_count_limit_uses_a_deterministic_path_order(self):
        files = {'assets/' + str(index).zfill(2) + '.png': png_fixture()
                 for index in reversed(range(65))}
        assets, profile = self.build(files)
        self.assertEqual(len(assets), 64)
        self.assertNotIn('assets/64.png', assets)
        self.assertEqual(profile['omitted'], [{'path': 'assets/64.png', 'reason': 'image_count_limit'}])


if __name__ == '__main__':
    unittest.main()
