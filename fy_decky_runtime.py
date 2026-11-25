"""Fixed core broker for the independently installed Decky runtime.

No optional imports, downloads, executable package loads or state writes occur
on module import/status. Hash verification is integrity, not a signature claim.
"""

import asyncio
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import platform as _platform
import re
import shutil
import stat
import sys
import tempfile
import uuid
import weakref
import zipfile

METADATA_URL = "https://raw.githubusercontent.com/chaosfox26/framey/main/decky-runtime-release.json"
HOSTS = (
    "raw.githubusercontent.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)
ARCHIVE_MAX, FILE_MAX, TOTAL_MAX, COUNT_MAX = 20_000_000, 5_000_000, 64_000_000, 512
STATE_MAX, MANIFEST_MAX = 8192, 262144
HEX = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
PLATFORMS = frozenset(("linux-aarch64", "linux-x86_64"))
_STATES = weakref.WeakKeyDictionary()


class RuntimeError(ValueError):
    def __init__(self, code, message=None):
        self.code, self.message = code, str(message or code)[:300]
        super().__init__(self.message)


def _need(value, code):
    if not value:
        raise RuntimeError(code)


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names.split()), "closed_fields")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def bad(_):
        raise RuntimeError("nonfinite_json")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RuntimeError("bad_json", str(error)) from None


def _encoded(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf8")


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        _need(
            stat.S_ISREG(meta.st_mode)
            and meta.st_nlink == 1
            and meta.st_uid == os.getuid()
            and meta.st_size <= limit,
            "bounded_regular_file",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        _need(len(raw) == meta.st_size and len(raw) <= limit, "file_changed")
        return raw
    finally:
        os.close(fd)


def _directory(path, create=False):
    path = Path(path)
    _need(path.is_absolute() and path == Path(os.path.normpath(path)), "absolute_owned_path")
    for parent in reversed((path, *path.parents)):
        if not parent.exists():
            _need(create, "missing_directory")
            parent.mkdir(mode=0o700)
        meta = parent.lstat()
        _need(stat.S_ISDIR(meta.st_mode), "directory_alias")
    _need(path.stat().st_uid == os.getuid(), "directory_owner")
    return path


def _relative(name):
    _need(
        type(name) is str and len(name) <= 240 and not name.startswith("/") and "\\" not in name,
        "relative_member",
    )
    parts = name.split("/")
    _need(
        all(p not in ("", ".", "..") and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", p) for p in parts),
        "relative_member",
    )
    return name


def _record(value):
    if value is None:
        return None
    _keys(value, "version archive_sha256 manifest_sha256 manager_api core_api compat_profile")
    _need(
        type(value["version"]) is str
        and len(value["version"]) <= 40
        and VERSION.fullmatch(value["version"]),
        "runtime_version",
    )
    _need(
        all(
            type(value[k]) is str and HEX.fullmatch(value[k])
            for k in ("archive_sha256", "manifest_sha256")
        ),
        "runtime_digest",
    )
    _need(
        all(
            type(value[k]) is int and value[k] == 1
            for k in ("manager_api", "core_api", "compat_profile")
        ),
        "runtime_abi",
    )
    return dict(value)


def _default():
    return dict(schema=1, enabled=False, generation=0, installed=None, previous=None)


def _state_file(path):
    if not os.path.lexists(path):
        return _default()
    value = _json(_read(path, STATE_MAX))
    _keys(value, "schema enabled generation installed previous")
    _need(
        type(value["schema"]) is int and value["schema"] == 1 and type(value["enabled"]) is bool,
        "state_schema",
    )
    _need(
        type(value["generation"]) is int and 0 <= value["generation"] < 2**53 - 1,
        "state_generation",
    )
    value["installed"], value["previous"] = _record(value["installed"]), _record(value["previous"])
    _need(not value["enabled"] or value["installed"] is not None, "state_missing_revision")
    return value


def _platform_id():
    return (
        sys.platform
        + "-"
        + {"arm64": "aarch64", "AMD64": "x86_64"}.get(_platform.machine(), _platform.machine())
    )


def _manifest(raw, platform, core_api):
    manifest = _json(raw)
    _keys(manifest, "schema version manager_api core_api compat_profile platform files")
    _need(type(manifest["schema"]) is int and manifest["schema"] == 1, "manifest_schema")
    _record(
        {k: manifest[k] for k in ("version", "manager_api", "core_api", "compat_profile")}
        | dict(archive_sha256="0" * 64, manifest_sha256="0" * 64)
    )
    _need(type(core_api) is int and manifest["core_api"] == core_api, "core_abi")
    targets = manifest["platform"]
    _need(
        type(targets) is list
        and targets
        and all(type(p) is str and p in PLATFORMS for p in targets)
        and len(set(targets)) == len(targets)
        and platform in targets,
        "runtime_platform",
    )
    files = manifest["files"]
    _need(
        type(files) is dict
        and 2 <= len(files) <= COUNT_MAX
        and {"runtime.py", "__init__.py"} <= set(files),
        "runtime_surface",
    )
    for name, digest in files.items():
        _relative(name)
        _need(
            name != "manifest.json" and type(digest) is str and HEX.fullmatch(digest),
            "member_digest",
        )
        _need(
            not name.endswith((".pyc", ".pth", ".so", ".pyd", ".dll", ".exe")),
            "runtime_source_only",
        )
    return manifest


def verify_revision(root, manifest_sha256, *, platform=None, core_api=1):
    """Verify the fixed source package, never import/execute it.

    Callers must obtain root from the core manager/parent descriptor, not plugin
    arguments. The complete directory closure is checked without following links.
    """
    root = _directory(root)
    _need(type(manifest_sha256) is str and HEX.fullmatch(manifest_sha256), "manifest_digest")
    raw = _read(root / "manifest.json", MANIFEST_MAX)
    _need(_sha(raw) == manifest_sha256, "manifest_digest")
    manifest = _manifest(raw, platform or _platform_id(), core_api)
    files, directories, total = {}, set(), 0
    for name in manifest["files"]:
        directories.update(str(p) for p in Path(name).parents if str(p) != ".")
    for folder, names, leaves in os.walk(root, followlinks=False):
        for name in names + leaves:
            path = Path(folder) / name
            relative = str(path.relative_to(root))
            meta = path.lstat()
            if stat.S_ISDIR(meta.st_mode):
                _need(relative in directories, "unexpected_directory")
                continue
            _need(stat.S_ISREG(meta.st_mode), "runtime_alias")
            _need(relative == "manifest.json" or relative in manifest["files"], "unexpected_file")
            value = _read(path, MANIFEST_MAX if relative == "manifest.json" else FILE_MAX)
            total += len(value)
            _need(total <= TOTAL_MAX, "expanded_size")
            if relative != "manifest.json":
                _need(_sha(value) == manifest["files"][relative], "member_digest")
                files[relative] = value
    _need(set(files) == set(manifest["files"]), "missing_file")
    return manifest


def _release(raw):
    release = _json(raw)
    _keys(release, "schema version archive manifest_sha256")
    _need(type(release["schema"]) is int and release["schema"] == 1, "release_schema")
    archive = release["archive"]
    _keys(archive, "url sha256 size")
    record = _record(
        dict(
            version=release["version"],
            archive_sha256=archive["sha256"],
            manifest_sha256=release["manifest_sha256"],
            manager_api=1,
            core_api=1,
            compat_profile=1,
        )
    )
    _need(type(archive["size"]) is int and 0 < archive["size"] <= ARCHIVE_MAX, "archive_size")
    prefix = r"https://github\.com/chaosfox26/framey/releases/download/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/decky-runtime-"
    _need(
        type(archive["url"]) is str
        and re.fullmatch(prefix + re.escape(record["version"]) + r"\.zip", archive["url"]),
        "release_origin",
    )
    return release, record


def _archive(raw, release, record, platform, core_api):
    archive = release["archive"]
    _need(
        type(raw) is bytes and len(raw) == archive["size"] and _sha(raw) == archive["sha256"],
        "archive_digest",
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        entries = package.infolist()
        _need(
            len(entries) <= COUNT_MAX + 1 and sum(p.file_size for p in entries) <= TOTAL_MAX,
            "archive_bounds",
        )
        files = {}
        for item in entries:
            name = item.filename
            _need(
                not item.flag_bits & 1
                and name == item.orig_filename
                and name.startswith("decky_runtime/"),
                "archive_layout",
            )
            _need(
                item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                "archive_compression",
            )
            relative = name[len("decky_runtime/") :]
            _relative(relative)
            _need(
                not item.is_dir()
                and relative not in files
                and stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG),
                "archive_member",
            )
            _need(
                item.file_size <= (MANIFEST_MAX if relative == "manifest.json" else FILE_MAX),
                "member_size",
            )
            files[relative] = package.read(item)
    _need(
        "manifest.json" in files and _sha(files["manifest.json"]) == record["manifest_sha256"],
        "manifest_digest",
    )
    manifest = _manifest(files["manifest.json"], platform, core_api)
    _need(
        manifest["version"] == record["version"]
        and set(files) == set(manifest["files"]) | {"manifest.json"},
        "archive_surface",
    )
    _need(all(_sha(files[n]) == digest for n, digest in manifest["files"].items()), "member_digest")
    return manifest, files


def verify_archive(data, metadata, *, platform=None, core_api=1):
    """Offline integrity/ABI/closure verification; no writes or executable imports."""
    release, record = _release(metadata if type(metadata) is bytes else _encoded(metadata))
    return _archive(data, release, record, platform or _platform_id(), core_api)[0]


async def _await(value):
    return await value if inspect.isawaitable(value) else value


class Handle:
    def __init__(self, manager, record, entry, namespace):
        self._owner = weakref.ref(manager)
        self._nonce = manager.nonce
        self.generation = manager.data["generation"]
        self.record, self.version, self.root = (
            dict(record),
            record["version"],
            manager.revision(record),
        )
        self.entry, self.namespace = entry, namespace


class _Manager:
    def __init__(self, host):
        self._host = weakref.ref(host)
        self.path = Path(host.CONF).parent / "decky-runtime.json"
        self.base = Path(host.HOME) / ".local/share/framey/runtimes/decky"
        self.nonce = uuid.uuid4().hex
        self.lock, self.handle = asyncio.Lock(), None
        self.calls = set()
        self.enabled, self.error, self.blocked = False, None, False
        try:
            _directory(Path(host.HOME))
            for parent in reversed((self.path.parent, *self.path.parent.parents)):
                if os.path.lexists(parent):
                    _need(stat.S_ISDIR(parent.lstat().st_mode), "state_parent_alias")
            self.data = _state_file(self.path)
        except (OSError, RuntimeError) as error:
            self.data, self.blocked = _default(), True
            self.error = self.detail(error)
        self.persisted_enabled = self.data["enabled"]
        self.phase = "error" if self.error else ("disabled" if self.present() else "absent")

    def host(self):
        host = self._host()
        _need(host is not None, "host_closed")
        return host

    @staticmethod
    def detail(error):
        return dict(code=getattr(error, "code", "runtime_error"), message=str(error)[:300])

    def revision(self, record):
        return self.base / "versions" / record["archive_sha256"]

    def present(self):
        record = self.data["installed"]
        return bool(
            record and self.revision(record).is_dir() and not self.revision(record).is_symlink()
        )

    def snapshot(self):
        return dict(
            installed=self.present(),
            enabled=self.enabled,
            requested_enabled=self.persisted_enabled,
            version=self.data["installed"]["version"] if self.data["installed"] else None,
            previous_version=self.data["previous"]["version"] if self.data["previous"] else None,
            generation=self.data["generation"],
            phase=self.phase,
            error=dict(self.error) if self.error else None,
        )

    def save(self):
        _need(not self.blocked, "state_unreadable")
        _directory(self.path.parent, create=True)
        if os.path.lexists(self.path):
            _read(self.path, STATE_MAX)  # Never replace an alias/nonregular state file.
        raw = _encoded(self.data)
        _need(len(raw) <= STATE_MAX, "state_size")
        fd, temporary = tempfile.mkstemp(prefix=".decky-runtime-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.persisted_enabled = self.data["enabled"]
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def fence(self, *, persist_off):
        self.enabled = False
        self.data["generation"] += 1
        _need(self.data["generation"] < 2**53 - 1, "generation_exhausted")
        if persist_off:
            self.data["enabled"] = False
        self.save()

    def check(self, generation):
        _need(generation == self.data["generation"], "stale_generation")

    async def fetch(self, url, limit):
        job = asyncio.create_task(
            asyncio.to_thread(self.host().fetch, url, {"User-Agent": "Framey"}, HOSTS, limit)
        )
        cancelled = False
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                cancelled = True
        raw = job.result()
        if cancelled:
            raise asyncio.CancelledError
        _need(type(raw) is bytes and len(raw) <= limit, "download_size")
        return raw

    def erase(self, path):
        _need(
            path.parent == self.base / "versions" or path.parent == self.base, "owned_removal_path"
        )
        if not os.path.lexists(path):
            return
        _need(not path.is_symlink() and path.is_dir(), "removal_alias")
        for folder, names, leaves in os.walk(path, followlinks=False):
            os.chmod(folder, 0o700)
            for name in names + leaves:
                child = Path(folder) / name
                _need(not child.is_symlink(), "removal_alias")
        shutil.rmtree(path)

    def install(self, raw, release, record):
        archive = release["archive"]
        _need(len(raw) == archive["size"] and _sha(raw) == archive["sha256"], "archive_digest")
        host = self.host()
        _, files = _archive(raw, release, record, host.PLATFORM, host.API_GENERATION)
        versions = _directory(self.base / "versions", create=True)
        destination = self.revision(record)
        if os.path.lexists(destination):
            verify_revision(
                destination,
                record["manifest_sha256"],
                platform=host.PLATFORM,
                core_api=host.API_GENERATION,
            )
            return
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.base))
        try:
            for name, value in files.items():
                target = stage / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
            verify_revision(
                stage,
                record["manifest_sha256"],
                platform=host.PLATFORM,
                core_api=host.API_GENERATION,
            )
            for folder, _, _ in os.walk(stage):
                if Path(folder) != stage:
                    os.chmod(folder, 0o500)
            os.rename(stage, destination)
            os.chmod(destination, 0o500)
            fd = os.open(versions, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if stage.exists():
                self.erase(stage)

    def load(self, record):
        root = self.revision(record)
        verify_revision(
            root,
            record["manifest_sha256"],
            platform=self.host().PLATFORM,
            core_api=self.host().API_GENERATION,
        )
        namespace = "_framey_decky_runtime_" + self.nonce + "_" + str(self.data["generation"])
        try:
            spec = importlib.util.spec_from_file_location(
                namespace, root / "__init__.py", submodule_search_locations=[str(root)]
            )
            package = importlib.util.module_from_spec(spec)
            sys.modules[namespace] = package
            spec.loader.exec_module(package)
            spec = importlib.util.spec_from_file_location(
                namespace + ".runtime", root / "runtime.py"
            )
            entry = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = entry
            spec.loader.exec_module(entry)
            _need(
                type(getattr(entry, "ABI", None)) is int
                and entry.ABI == 1
                and callable(getattr(entry, "frontend_bundle", None))
                and callable(getattr(entry, "shutdown", None)),
                "entry_abi",
            )
            return Handle(self, record, entry, namespace)
        except BaseException:
            self.unload(namespace)
            raise

    @staticmethod
    def unload(namespace):
        for name in list(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                sys.modules.pop(name, None)

    async def close_owner(self):
        handle = self.handle
        pending = tuple(task for task in self.calls if task is not asyncio.current_task())
        for task in pending:
            task.cancel()
        errors = []
        hook = getattr(self.host(), "decky_runtime_stop", None)
        if hook:
            try:
                await _await(hook())
            except Exception as error:
                errors.append(error)
        if handle:
            try:
                await _await(handle.entry.shutdown(self.host()))
            except Exception as error:
                errors.append(error)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if errors:
            raise errors[0]
        if handle:
            self.unload(handle.namespace)
            self.handle = None

    async def enable(self, *, download):
        ticket = self.data["generation"]
        generation = ticket
        async with self.lock:
            if self.enabled:
                return self.snapshot()
            try:
                _need(not self.blocked, "state_unreadable")
                self.check(ticket)
                generation = ticket + 1
                self.fence(persist_off=False)
                self.phase, self.error = "enabling", None
                record = self.data["installed"]
                if record is None or not self.present():
                    _need(download, "runtime_missing")
                    self.phase = "installing"
                    release, record = _release(await self.fetch(METADATA_URL, MANIFEST_MAX))
                    self.check(generation)
                    raw = await self.fetch(release["archive"]["url"], ARCHIVE_MAX)
                    self.check(generation)
                    self.install(raw, release, record)
                    self.data["previous"], self.data["installed"] = self.data["installed"], record
                    self.save()
                self.check(generation)
                self.handle = self.load(record)
                startup = getattr(self.handle.entry, "startup", None)
                if startup:
                    await _await(startup(self.host()))
                self.check(generation)
                self.data["enabled"] = True
                self.save()
                self.enabled, self.phase = True, "enabled"
                hook = getattr(self.host(), "decky_runtime_start", None)
                if hook:
                    await _await(hook())
                self.check(generation)
            except BaseException as error:
                # A newer disable/remove owns its intent/error; never roll it back.
                if self.handle:
                    self.enabled = False
                    try:
                        await self.close_owner()
                    except Exception as cleanup:
                        self.error = self.detail(cleanup)
                if self.data["generation"] == generation:
                    self.enabled, self.data["enabled"], self.phase = False, False, "error"
                    self.error = self.error or self.detail(error)
                    try:
                        self.save()
                    except Exception as persist:
                        self.error = self.detail(persist)
                if isinstance(error, asyncio.CancelledError):
                    raise
            return self.snapshot()

    def begin_disable(self, *, remove=False):
        if (
            not remove
            and not self.enabled
            and not self.handle
            and self.phase in ("absent", "disabled")
            and not self.data["enabled"]
        ):
            return True, None
        persistence_error = None
        try:
            self.fence(persist_off=True)
            self.phase, self.error = ("removing" if remove else "disabling"), None
        except Exception as error:
            self.enabled, self.phase, self.error = False, "error", self.detail(error)
            persistence_error = error
        return False, persistence_error

    async def disable(self, *, remove=False, preparation=None):
        done, persistence_error = (
            preparation if preparation is not None else self.begin_disable(remove=remove)
        )
        if done:
            return self.snapshot()
        async with self.lock:
            try:
                # Even failed durable intent must not leave active services running.
                await self.close_owner()
                if persistence_error is not None:
                    raise persistence_error
                if remove:
                    if self.base.exists():
                        _directory(self.base)
                        versions = self.base / "versions"
                        if versions.exists():
                            _directory(versions)
                            for path in versions.iterdir():
                                self.erase(path)
                        for path in self.base.iterdir():
                            if path.name.startswith(".stage-"):
                                self.erase(path)
                    self.data["installed"] = self.data["previous"] = None
                    self.save()
                self.phase = "disabled" if self.present() else "absent"
            except Exception as error:
                self.phase, self.error = "error", self.detail(error)
            return self.snapshot()

    async def shutdown(self):
        if not self.enabled and not self.handle:
            return
        try:
            self.fence(persist_off=False)
            async with self.lock:
                await self.close_owner()
            self.phase = "disabled" if self.present() else "absent"
        except Exception as error:
            self.enabled, self.phase, self.error = False, "error", self.detail(error)


def _get(host):
    state = _STATES.get(host)
    if state is None:
        state = _STATES[host] = _Manager(host)
    return state


def status(host):
    return _get(host).snapshot()


def allowed(host, manifest):
    managed = type(manifest) is dict and (
        "decky_compat" in manifest or manifest.get("_framey_decky_managed") is True
    )
    marker = manifest.get("decky_compat") if type(manifest) is dict else None
    paired = (
        type(marker) is dict
        and type(marker.get("runtime_abi")) is int
        and marker["runtime_abi"] == 1
        and type(marker.get("wrapper_version")) is int
        and marker["wrapper_version"] == 2
    )
    return not managed or paired and _get(host).enabled


def acquire(host):
    state = _get(host)
    handle = state.handle
    _need(state.enabled and handle is not None and current(host, handle), "runtime_disabled")
    verify_revision(
        handle.root,
        handle.record["manifest_sha256"],
        platform=host.PLATFORM,
        core_api=host.API_GENERATION,
    )
    return handle


def current(host, handle):
    state = _get(host)
    return (
        type(handle) is Handle
        and state.enabled
        and state.handle is handle
        and handle._owner() is state
        and handle._nonce == state.nonce
        and handle.generation == state.data["generation"]
    )


async def set_enabled(host, enabled):
    if type(enabled) is not bool:
        raise RuntimeError("bad_request")
    if enabled:
        return await _get(host).enable(download=True)
    return await _disable_settled(host, remove=False)


async def resume(host):
    state = _get(host)
    return await state.enable(download=False) if state.data["enabled"] else state.snapshot()


async def _settled(operation):
    job = asyncio.create_task(operation)
    cancelled = False
    while not job.done():
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            cancelled = True
    result = job.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _disable_settled(host, *, remove):
    state = _get(host)
    preparation = state.begin_disable(remove=remove)  # Fence before the first await.
    return await _settled(state.disable(remove=remove, preparation=preparation))


async def remove(host):
    return await _disable_settled(host, remove=True)


async def shutdown(host):
    await _settled(_get(host).shutdown())


def frontend(host):
    handle = acquire(host)
    source = handle.entry.frontend_bundle()
    _need(type(source) is str and len(source.encode("utf8")) <= FILE_MAX, "frontend_size")
    _need(current(host, handle), "stale_generation")
    return dict(abi=1, generation=handle.generation, version=handle.version, source=source)


def worker_descriptor(host):
    handle = acquire(host)
    return dict(
        schema=1,
        root=str(handle.root),
        manifest_sha256=handle.record["manifest_sha256"],
        generation=handle.generation,
        version=handle.version,
        state_path=str(_get(host).path),
    )


def validate_worker_descriptor(descriptor, *, home=None, platform=None, core_api=1):
    """Validate the parent-issued fixed runtime descriptor before worker imports.

    SDK callers never accept this object from plugin call arguments. The default
    HOME binding is the worker's owned environment; tests may supply owned HOME.
    """
    _keys(descriptor, "schema root manifest_sha256 generation version state_path")
    _need(type(descriptor["schema"]) is int and descriptor["schema"] == 1, "descriptor_schema")
    _need(
        type(descriptor["generation"]) is int and 0 <= descriptor["generation"] < 2**53 - 1,
        "descriptor_generation",
    )
    _need(
        all(
            type(descriptor[k]) is str for k in ("root", "manifest_sha256", "version", "state_path")
        ),
        "descriptor_fields",
    )
    home = _directory(Path.home() if home is None else Path(home))
    path = home / ".config/framey/decky-runtime.json"
    _need(descriptor["state_path"] == str(path), "descriptor_state_path")
    _directory(path.parent)
    state = _state_file(path)
    record = state["installed"]
    _need(
        state["enabled"] and record is not None and state["generation"] == descriptor["generation"],
        "descriptor_retired",
    )
    _need(
        record["version"] == descriptor["version"]
        and record["manifest_sha256"] == descriptor["manifest_sha256"],
        "descriptor_revision",
    )
    root = home / ".local/share/framey/runtimes/decky/versions" / record["archive_sha256"]
    _need(descriptor["root"] == str(root), "descriptor_root")
    manifest = verify_revision(
        root, record["manifest_sha256"], platform=platform, core_api=core_api
    )
    _need(manifest["version"] == record["version"], "descriptor_version")
    return manifest


def invalidate(host, pid=None):
    state = _STATES.get(host)
    if state is None or state.handle is None:
        return
    callback = getattr(state.handle.entry, "invalidate", None)
    if callback:
        try:
            callback(host, pid)
        except Exception as error:
            state.enabled, state.phase, state.error = False, "error", state.detail(error)


async def _status(host, arg=None):
    try:
        _keys({} if arg is None else arg, "")
        return status(host)
    except RuntimeError as error:
        return dict(error=error.message, code="bad_request")


async def _enable(host, arg=None):
    try:
        _keys(arg, "enabled")
        _need(type(arg["enabled"]) is bool, "bad_request")
        return await set_enabled(host, arg["enabled"])
    except RuntimeError as error:
        return dict(error=error.message, code="bad_request")


async def _remove(host, arg=None):
    try:
        _keys({} if arg is None else arg, "")
        return await remove(host)
    except RuntimeError as error:
        return dict(error=error.message, code="bad_request")


async def _frontend(host, arg=None):
    try:
        arg = {} if arg is None else arg
        _need(type(arg) is dict and not set(arg) - {"pid", "id"}, "bad_request")
        _need(
            all(
                type(value) is str and re.fullmatch("[a-z0-9_-]{1,32}", value)
                for value in arg.values()
            ),
            "bad_request",
        )
        _need(not ("pid" in arg and "id" in arg) or arg["pid"] == arg["id"], "bad_request")
        return frontend(host)
    except RuntimeError as error:
        return dict(error=error.message, code=error.code)


async def _code(host, arg, operation):
    state = _get(host)
    if operation == "revoke" and state.handle is None:
        return {"revoked": True}  # The retired owner has already been fenced/drained.
    try:
        handle = state.handle if operation == "revoke" else acquire(host)
    except RuntimeError as error:
        return dict(error=error.message, code=error.code)
    task = asyncio.current_task()
    state.calls.add(task)
    try:
        result = await handle.entry.code_dispatch(host, operation, arg)
        _need(current(host, handle) or operation == "revoke", "stale_generation")
        return result
    except asyncio.CancelledError:
        if not current(host, handle):
            return dict(error="Runtime generation retired", code="stale_generation")
        raise
    except RuntimeError as error:
        return dict(error=error.message, code=error.code)
    finally:
        state.calls.discard(task)


async def _issue(host, arg=None):
    return await _code(host, arg, "issue")


async def _touch(host, arg=None):
    return await _code(host, arg, "touch")


async def _revoke(host, arg=None):
    return await _code(host, arg, "revoke")


METHODS = dict(
    decky_runtime_status=_status,
    decky_runtime_enable=_enable,
    decky_runtime_remove=_remove,
    decky_runtime_frontend=_frontend,
    decky_code_issue=_issue,
    decky_code_touch=_touch,
    decky_code_revoke=_revoke,
)
