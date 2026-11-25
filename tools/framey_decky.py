#!/usr/bin/env python3
"""Offline, opt-in importer for Framey's experimental Decky compatibility profile."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _implementation():
    from decky_runtime import importer

    return importer


if __name__ == "__main__":
    # Use normal module discovery from this source distribution; no process-wide
    # path insertion or optional runtime download is performed by the CLI shim.
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "decky_runtime.importer", *sys.argv[1:]], cwd=ROOT)
    )

_impl = _implementation()
ImportError = _impl.ImportError
inspect_package = _impl.inspect_package
import_plugin = _impl.import_plugin
_contents = _impl._contents
main = _impl.main
