"""Owned optional-runtime installation data for focused tests; never a downloader."""

import hashlib
import io
import json
from pathlib import Path
import zipfile


def install_runtime(home, files, *, version="0.1.0", generation=7):
    home = Path(home)
    files = dict(files)
    manifest = dict(
        schema=1,
        version=version,
        manager_api=1,
        core_api=1,
        compat_profile=1,
        platform=["linux-aarch64", "linux-x86_64"],
        files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
    )
    manifest_raw = json.dumps(manifest).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, raw in files.items():
            archive.writestr("decky_runtime/" + name, raw)
        archive.writestr("decky_runtime/manifest.json", manifest_raw)
    archive_sha = hashlib.sha256(stream.getvalue()).hexdigest()
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    root = home / ".local/share/framey/runtimes/decky/versions" / archive_sha
    root.mkdir(parents=True)
    for name, raw in files.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
    (root / "manifest.json").write_bytes(manifest_raw)
    record = dict(
        version=version,
        archive_sha256=archive_sha,
        manifest_sha256=manifest_sha,
        manager_api=1,
        core_api=1,
        compat_profile=1,
    )
    state_path = home / ".config/framey/decky-runtime.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            dict(schema=1, enabled=True, generation=generation, installed=record, previous=None)
        )
    )
    return dict(
        schema=1,
        root=str(root),
        manifest_sha256=manifest_sha,
        generation=generation,
        version=version,
        state_path=str(state_path),
    )
