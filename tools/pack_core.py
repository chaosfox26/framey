"""Select/build a native-only Framey release without importing loader source."""

import argparse
import gzip
import importlib.util
import io
import pathlib
import re
import tarfile

_layout_spec = importlib.util.spec_from_file_location(
    "_framey_core_layout_build", pathlib.Path(__file__).resolve().parents[1] / "fy_core_layout.py"
)
_layout = importlib.util.module_from_spec(_layout_spec)
_layout_spec.loader.exec_module(_layout)
HEADER = _layout.HEADER
HEAVY = _layout.HEAVY
MINIMUM = _layout.MINIMUM
safe_file = _layout.safe_file
_assignment = _layout._assignment
generate_manifest = _layout.generate_manifest
core_files = _layout.core_files


def _regular(root, name):
    path = root
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Core input contains a link: " + name)
    if not path.is_file():
        raise ValueError("Core input is missing: " + name)
    if path.stat().st_size > 5_000_000:
        raise ValueError("Core input is too large: " + name)
    return path


def write_core_archive(root, output, revision):
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Core revision must be a pinned commit")
    root = pathlib.Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Core source must be a regular directory")
    files = core_files(
        _regular(root, "framey.py").read_bytes(), _regular(root, "core-files.txt").read_bytes()
    )
    payload = {name: _regular(root, name).read_bytes() for name in files}
    if sum(map(len, payload.values())) > 20_000_000:
        raise ValueError("Core payload is too large")
    payload[".version"] = revision.encode("ascii")
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo("framey/" + name)
                info.size = len(data)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
    pathlib.Path(output).write_bytes(stream.getvalue())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--revision")
    args = parser.parse_args()
    if args.write_manifest:
        (args.root / "core-files.txt").write_bytes(
            generate_manifest((args.root / "framey.py").read_bytes())
        )
    if args.output is not None:
        if not args.revision:
            parser.error("--output requires --revision")
        write_core_archive(args.root, args.output, args.revision)
    elif not args.write_manifest:
        core_files(
            (args.root / "framey.py").read_bytes(), (args.root / "core-files.txt").read_bytes()
        )


if __name__ == "__main__":
    main()
