import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent.parent
DECLARATIONS = ROOT / "types" / "framey.d.ts"
sys.path.insert(0, str(ROOT))


def interface_body(source, name):
    match = re.search(
        rf"\binterface {re.escape(name)}(?: extends [^{{]+)? \{{\n(.*?)\n\}}",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"Missing interface {name}")
    return match.group(1)


def interface_members(source, name):
    return set(re.findall(
        r"^  (?:readonly )?([A-Za-z_]\w*)\??(?:<[^\n]+?>)?(?=\s*[:(])",
        interface_body(source, name),
        re.MULTILINE,
    ))


class FrontendTypesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DECLARATIONS.read_text()

    def test_plugin_binding_and_loader_have_distinct_entry_points(self):
        self.assertRegex(self.source, r"declare const Framey: FrameyBinding;")
        self.assertRegex(interface_body(self.source, "Window"), r"readonly Framey: FrameyGlobal;")
        self.assertIn("register", interface_members(self.source, "FrameyBinding"))
        self.assertNotIn("register", interface_members(self.source, "FrameyGlobal"))
        self.assertIn("diag", interface_members(self.source, "FrameyGlobal"))
        self.assertNotIn("diag", interface_members(self.source, "FrameyBinding"))

    def test_unknown_payloads_are_explicit_and_page_fetch_cannot_save(self):
        without_comments = re.sub(r"/\*.*?\*/|//[^\n]*", "", self.source, flags=re.DOTALL)
        self.assertNotRegex(without_comments, r"\bany\b")
        self.assertRegex(self.source, r"type FrameyResult<T> = T \| FrameyErrorResult;")
        self.assertRegex(interface_body(self.source, "FrameyApi"), r"call<T = unknown>")
        self.assertRegex(interface_body(self.source, "FrameyFetchResult"), r"path: null;")
        self.assertNotIn("dest", interface_members(self.source, "FrameyFetchOptions"))

    def test_catalog_inspection_is_a_loader_ui_operation(self):
        catalog_ui = (ROOT / "fy-catalog.js").read_text()
        self.assertRegex(catalog_ui, r'call\("catalog_assess", request\)')
        for interface in ("FrameyApi", "FrameyBinding", "FrameyGlobal"):
            members = interface_members(self.source, interface)
            self.assertFalse({"catalog", "catalog_assess", "catalogAssess"} & members,
                             f"{interface} must not promise a plugin catalog API")

    def test_decky_assessment_types_match_offline_inspection_evidence(self):
        import fy_decky_catalog as decky

        def literals(alias):
            match = re.search(rf"type {alias} = ([^;]+);", self.source)
            self.assertIsNotNone(match, alias)
            return set(re.findall(r'"([^"]+)"', match.group(1)))

        def elf(machine):
            header = bytearray(64)
            header[:7] = b"\x7fELF\x02\x01\x01"
            struct.pack_into("<HHI", header, 16, 2, machine, 1)
            struct.pack_into("<H", header, 52, 64)
            return bytes(header)

        def archive(machines):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as bundle:
                bundle.writestr("plugin.json", '{"name":"Sample","flags":[]}')
                bundle.writestr("dist/index.js", "export const value = 1;")
                for index, machine in enumerate(machines):
                    bundle.writestr(f"bin/program-{index}", elf(machine))
            return output.getvalue()

        cases = (([], "unknown", "unverified"), ([183], "aarch64", "evidence_only"),
                 ([62], "x86_64", "incompatible"), ([183, 62], "mixed", "evidence_only"))
        for machines, architecture, status in cases:
            with self.subTest(machines=machines):
                data = archive(machines)
                result = decky.inspect_archive(data, hashlib.sha256(data).hexdigest())
                self.assertEqual(set(result), interface_members(self.source, "FrameyDeckyArchiveAssessment"))
                self.assertEqual((result["state"], result["status"]), ("port_required", "incompatible"))
                self.assertEqual(result["architecture"], architecture)
                self.assertEqual(result["architecture_status"], status)
                self.assertIn(result["architecture"], literals("FrameyDeckyArchiveArchitecture"))
                self.assertIn(result["architecture_status"], literals("FrameyDeckyArchitectureStatus"))
                self.assertIs(result["not_executed"], True)
                self.assertIs(result["verified_sha256"], True)
                self.assertIs(result["runtime_verified"], False)
                self.assertEqual(result["basis"], "static_package_inspection")
                self.assertEqual(result["native_binary_count"], len(machines))
                self.assertIs(result["native_binaries_truncated"], False)
                self.assertIsInstance(result["root_required"], bool)
                self.assertTrue(result["warnings"], "Bounded evidence must retain its limitations")
                for binary in result["native_binaries"]:
                    self.assertEqual(set(binary), interface_members(self.source, "FrameyDeckyNativeBinary"))
                    self.assertIn(binary["format"], literals("FrameyDeckyBinaryFormat"))
                    self.assertIn(binary["architecture"], literals("FrameyDeckyBinaryArchitecture"))

    def test_decky_assessment_reply_matches_current_core_producer(self):
        tree = ast.parse((ROOT / "fy_decky_catalog.py").read_text())
        assess = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == "_assess")
        replies = [node for node in ast.walk(assess) if isinstance(node, ast.Dict)]
        reply = next(value for value in replies
                     if any(isinstance(key, ast.Constant) and key.value == "assessment"
                            for key in value.keys))
        fields = {key.value for key in reply.keys if isinstance(key, ast.Constant)}
        self.assertEqual(fields, interface_members(self.source, "FrameyDeckyAssessmentReply"))
        import fy_decky_catalog as decky
        for source in decky.SOURCES.values():
            self.assertEqual(set(source), interface_members(self.source, "FrameyDeckyCatalogSource"))
            self.assertIn(source["id"], ("decky_stable", "decky_testing"))
            self.assertEqual(source["format"], "decky")
        catalog_ui = (ROOT / "fy-catalog.js").read_text()
        for status in ("compatible", "incompatible", "unverified"):
            self.assertIn(f'"{status}"', catalog_ui)
        self.assertIn('"Not checked"', catalog_ui)

    def test_control_and_binding_members_match_live_runtime(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = r'''
const { makeDom } = require('./tests/js/harness');
const env = makeDom({ loadFiles: ['fy-api.js', 'fy-ui.js', 'fy-pages.js', 'fy-picker.js'] });
try {
  const binding = env.window.__bindFramey('typed', 'typed-token-0000', {});
  const api = binding.register({ render() {} });
  const button = api.ui.button({ label: 'Save' });
  const toggle = api.ui.toggle({ label: 'Enabled' });
  const slider = api.ui.slider({ label: 'Level', min: 0, max: 10 });
  const single = api.ui.list({ items: [{ id: 'a', label: 'A' }], selected: 'a' });
  const multi = api.ui.list({ multi: true, items: [{ id: 1, label: 'One' }], selected: [1] });
  button.disabled = true;
  toggle.disabled = true;
  toggle.value = true;
  slider.value = 7;
  single.setItems([{ id: 'a', label: 'Updated A' }]);
  const describe = (element, names) => Object.fromEntries(names.map(name => {
    const descriptor = Object.getOwnPropertyDescriptor(element, name);
    return [name, { type: typeof element[name], writable: !!descriptor &&
      (typeof descriptor.set === 'function' || descriptor.writable === true) }];
  }));
  console.log(JSON.stringify({
    binding: Object.getOwnPropertyNames(binding),
    controls: {
      FrameyUiControl: describe(button, ['disabled']),
      FrameyUiToggle: describe(toggle, ['value']),
      FrameyUiSlider: describe(slider, ['value']),
      FrameyUiList: describe(single, ['selected', 'setItems']),
    },
    values: { disabled: button.disabled, toggle: toggle.value, slider: slider.value,
      single: single.selected, multi: multi.selected },
    loaderHasRegister: 'register' in env.window.Framey,
    pickers: [typeof api.pickFile, typeof api.pickFolder],
  }));
} finally { env.window.close(); }
'''
        result = subprocess.run(
            [node, "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=30,
        )
        if result.returncode and "jsdom not found" in result.stderr:
            self.skipTest("jsdom is not available; use the browser test dependency environment")
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(set(observed["binding"]), interface_members(self.source, "FrameyBinding"))
        self.assertFalse(observed["loaderHasRegister"])
        for name, fields in observed["controls"].items():
            body = interface_body(self.source, name)
            self.assertEqual(set(fields), interface_members(self.source, name))
            for field, descriptor in fields.items():
                declaration = next(line.strip() for line in body.splitlines()
                                   if re.match(rf"(?:readonly )?{field}\b", line.strip()))
                self.assertEqual(descriptor["writable"], not declaration.startswith("readonly "),
                                 f"{name}.{field}")
                if "(" in declaration.split(":", 1)[0]:
                    self.assertEqual(descriptor["type"], "function")
                elif ": boolean;" in declaration:
                    self.assertEqual(descriptor["type"], "boolean")
                elif ": number;" in declaration:
                    self.assertEqual(descriptor["type"], "number")
        self.assertEqual(observed["values"], {
            "disabled": True, "toggle": True, "slider": 7, "single": "a", "multi": [1],
        })
        self.assertEqual(observed["pickers"], ["function", "function"])

    def test_typescript_plugin_contract_when_compiler_is_installed(self):
        compiler = os.environ.get("FRAMEY_TSC") or shutil.which("tsc")
        if not compiler:
            self.skipTest("TypeScript compiler is not installed")
        fixture = r'''
const api = Framey.register({
  async render(box, scoped) { box.append(scoped.ui.button({ label: 'Save' })); },
});
const loader: FrameyGlobal = window.Framey;
declare const inspection: FrameyDeckyAssessmentResult;
if ('error' in inspection) inspection.error.toUpperCase();
else {
  const status: 'incompatible' = inspection.assessment.status;
  const executed: true = inspection.assessment.not_executed;
  const source: 'decky_stable' | 'decky_testing' = inspection.source.id;
  const downloadedAt: number = inspection.downloaded_at;
  inspection.identity.toUpperCase();
  inspection.artifact.toUpperCase();
  inspection.assessment.native_binaries.forEach(binary => binary.path.toUpperCase());
}
Framey.open();
// @ts-expect-error The plugin binding does not expose loader diagnostics.
Framey.diag();
// @ts-expect-error The loader global cannot register a plugin.
window.Framey.register({ render() {} });
const enabled = api.ui.toggle({ label: 'Enabled' });
enabled.disabled = true;
enabled.value = false;
// @ts-expect-error A toggle value is boolean.
enabled.value = 1;
const level = api.ui.slider({ label: 'Level' });
level.value = 7;
// @ts-expect-error A slider value is numeric.
level.value = '7';
const single = api.ui.list({ items: [{ id: 'a', label: 'A' }],
  onselect(value) { const selected: string | number | null = value; } });
const multi = api.ui.list({ multi: true, selected: [1],
  onselect(value) { value.map(id => String(id)); } });
const selected: (string | number)[] = multi.selected;
single.setItems([{ label: 'B' }]);
// @ts-expect-error List selection is exposed as a getter.
single.selected = 'a';
api.ui.focus(null);
api.ui.button({ label: 'Async', onclick: async () => 1 });
api.on(document.body, 'keydown', event => { const key: string = event.key; });
api.events.on<{ step: number }>('progress', payload => payload.step.toFixed());
api.events.on('unchecked', payload => {
  // @ts-expect-error Undeclared event data must be narrowed or explicitly typed.
  payload.step;
});
api.call<{ level: number }>('get', undefined, 30000).then(result => {
  if ('error' in result) result.error.toUpperCase();
  else result.level.toFixed();
});
api.call('unchecked').then(result => {
  // @ts-expect-error Unknown backend results must be narrowed or explicitly typed.
  result.level;
});
api.fetch('https://example.org').then(result => { const path: null = result.path; });
// @ts-expect-error Browser fetch cannot write a destination file.
api.fetch('https://example.org', { dest: 'data:file.txt' });
api.resource('data', 'cover.png');
// @ts-expect-error Resources are limited to plugin data/runtime roots.
api.resource('other', 'cover.png');
api.pickFile?.().then(result => { if (result) result.name.toUpperCase(); });
api.pickFolder?.().then(result => { if (result) result.path.toUpperCase(); });
api.pages.register({ id: 'details', async render(box, ctx) { ctx.close(); } });
api.global.mount('badge', { async render(box) { box.textContent = 'Ready'; } });
// Callback results are ignored, so older concise callbacks remain valid.
api.pages.register({ id: 'concise', render: box => box.appendChild(document.createElement('div')) });
api.global.mount('concise', { render: box => box.appendChild(document.createElement('div')) });
api.ui.button({ label: 'Notice', onclick: () => api.notify({ title: 'Saved' }) });
api.target('main').remove().then(result => {
  if ('error' in result) result.error.toUpperCase();
  else { const ok: true = result.ok; }
});
// @ts-expect-error The loader plugin registry is read-only.
loader.plugins.push({ render() {} });
'''
        with tempfile.TemporaryDirectory(prefix="framey-types-") as temp:
            source = Path(temp) / "plugin.ts"
            source.write_text(fixture)
            result = subprocess.run(
                [compiler, "--strict", "--noEmit", "--lib", "es2020,dom",
                 "--target", "es2020", str(DECLARATIONS), str(source)],
                cwd=ROOT, text=True, capture_output=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
