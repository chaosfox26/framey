"""Native-only distribution layout verification; loader source is parsed as data."""

import ast
import re

HEADER = "framey-native-core-v1"
HEAVY = frozenset(
    (
        "fy_decky_catalog.py",
        "fy_decky_import.py",
        "fy_decky_code.py",
        "fy_code_http.py",
        "tools/framey_decky.py",
        "compat/framey-view.js",
        "compat/decky-assets.js",
        "compat/decky-navigation.js",
        "compat/decky-menus.js",
        "compat/decky-sidebar.js",
        "compat/decky-modules.js",
        "compat/decky-host.js",
        "compat/decky_backend.py",
        "compat/decky_patches.py",
    )
)
MINIMUM = frozenset(
    ("framey.py", "core-files.txt", "fy_core_layout.py", "fy_decky_runtime.py", "fy-optional.js")
)


def safe_file(name):
    return (
        type(name) is str
        and re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", name) is not None
        and len(name.split("/")) <= 8
        and all(part not in ("", ".", "..") for part in name.split("/"))
        and name.lower() != ".version"
        and name.lower().split("/")[0] != "decky_runtime"
        and name.lower() not in HEAVY
    )


def _assignment(tree, name):
    for node in tree.body:
        if (
            isinstance(node, (ast.AugAssign, ast.AnnAssign))
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            raise ValueError("Core distribution disallows rebinding " + name)
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    if len(matches) != 1 or len(matches[0].targets) != 1:
        raise ValueError("Core distribution requires one literal " + name)
    try:
        return ast.literal_eval(matches[0].value)
    except (ValueError, TypeError) as error:
        raise ValueError("Core distribution requires literal " + name) from error


def generate_manifest(source):
    if len(source) > 2_000_000:
        raise ValueError("Loader source is too large")
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeError) as error:
        raise ValueError("Invalid loader source") from error
    layout = _assignment(tree, "NATIVE_CORE_LAYOUT")
    if type(layout) is not int or layout != 1:
        raise ValueError("A native-core layout1 loader is required")
    selected = _assignment(tree, "MANIFEST")
    if type(selected) is not tuple or not 1 <= len(selected) <= 498:
        raise ValueError("Invalid native core MANIFEST")
    if any(not safe_file(name) for name in selected):
        raise ValueError("Core MANIFEST selects unsafe or optional files")
    if len({name.lower() for name in selected}) != len(selected):
        raise ValueError("Duplicate core MANIFEST entry")
    if not MINIMUM.issubset(selected):
        raise ValueError("Core MANIFEST lacks its fixed manager/facade/layout files")
    files = sorted(set(selected) | {"LICENSE"})
    if len({name.lower() for name in files}) != len(files):
        raise ValueError("Core metadata filename collision")
    wire = (HEADER + "\n" + "\n".join(files) + "\n").encode("ascii")
    if len(wire) > 64_000:
        raise ValueError("Core metadata is too large")
    return wire


def core_files(source, wire):
    if len(wire) > 64_000 or wire != generate_manifest(source):
        raise ValueError("core-files.txt does not match literal core MANIFEST")
    return tuple(wire.decode("ascii").splitlines()[1:])
