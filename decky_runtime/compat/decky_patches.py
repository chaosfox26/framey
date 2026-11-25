"""Version-pinned, independently authored Framey repairs; original vendor bytes stay unchanged."""

import asyncio
import hashlib
import inspect
import os
import re
import stat
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

SOURCE_SHA256 = "6fd4d012568edae22ba421495455b3c02899155e4c7484f5aa5be6ce9b14dd5f"
ENTRY_ID = "framey.storage-cleaner.delete-cache-await.v1"
REGISTRY_VERSION = "framey-owned-decky-patches-v1"
PATH_PROFILE = "primary-Steam-cache-paths-v2"
MAX_ENTRIES = 200000
MAX_DEPTH = 32
REGISTRY = (
    {
        "entry_id": ENTRY_ID,
        "source_sha256": SOURCE_SHA256,
        "class": "Plugin",
        "method": "delete_cache",
        "descriptor": "classmethod",
        "signature": ["cls", "dirName"],
        "implementation_owner": "Framey",
        "path_profile": PATH_PROFILE,
    },
)
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class UnsupportedCachePath(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Unsupported Steam cache path: " + code)


@dataclass(frozen=True)
class CacheProfile:
    home: Path

    def __post_init__(self):
        home = Path(self.home).absolute()
        if os.getuid() == 0 or os.getuid() != os.geteuid():
            raise ValueError("normal unprivileged worker required")
        if home.resolve() != home or home.is_symlink() or not home.is_dir():
            raise ValueError("home must be a real normal-user directory")
        if home.stat().st_uid != os.getuid():
            raise ValueError("home ownership mismatch")
        object.__setattr__(self, "home", home)


def parse_argument(argument):
    if type(argument) is not str:
        raise TypeError("cache argument must be a plain string")
    match = re.fullmatch(
        r"(shadercache|compatdata)(?:/([1-9][0-9]{0,9}))?", argument, flags=re.ASCII
    )
    if match is None:
        raise UnsupportedCachePath("argument_shape")
    cache, appid = match.groups()
    if appid is not None and int(appid) > 4294967295:
        raise UnsupportedCachePath("appid_range")
    return cache, appid


def _directory(fd):
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("normal-user-owned directory required")
    return metadata


def _mount_id(fd):
    try:
        with open("/proc/self/fdinfo/" + str(fd), "r", encoding="ascii") as stream:
            text = stream.read(4097)
        if len(text) > 4096:
            raise ValueError("fdinfo limit")
        values = [
            line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("mnt_id:")
        ]
        if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
            raise ValueError("mount identity absent")
        return int(values[0])
    except (OSError, ValueError) as error:
        raise NotImplementedError("Linux descriptor mount identity is required") from error


def _open_absolute_directory(path):
    fd = os.open("/", DIR_FLAGS)
    try:
        for name in path.parts[1:]:
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        _directory(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _same_anchor(fd, anchor):
    metadata = os.fstat(fd)
    if metadata.st_dev != anchor[0] or _mount_id(fd) != anchor[1]:
        raise UnsupportedCachePath("mount_boundary")
    return metadata


def _entry_identity(parent, name):
    metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _steamapps(profile, stack):
    home = _open_absolute_directory(profile.home)
    stack.callback(os.close, home)
    dot = os.open(".steam", DIR_FLAGS, dir_fd=home)
    stack.callback(os.close, dot)
    _directory(dot)
    initial = _entry_identity(dot, "steam")
    if stat.S_ISLNK(initial[2]):
        link_metadata = os.stat("steam", dir_fd=dot, follow_symlinks=False)
        if link_metadata.st_uid != os.getuid():
            raise ValueError("Steam prefix link ownership mismatch")
        link = os.readlink("steam", dir_fd=dot)
        # Resolve only the Steam prefix once. No cache/app/data leaf is resolved.
        root = (profile.home / ".steam/steam").resolve(strict=True)
        steam = _open_absolute_directory(root)
        stack.callback(os.close, steam)
        if _entry_identity(dot, "steam") != initial or os.readlink("steam", dir_fd=dot) != link:
            raise ValueError("Steam prefix changed while resolving")
        if (profile.home / ".steam").stat().st_ino != os.fstat(dot).st_ino:
            raise ValueError("Steam prefix directory changed")
    elif stat.S_ISDIR(initial[2]):
        steam = os.open("steam", DIR_FLAGS, dir_fd=dot)
        stack.callback(os.close, steam)
        _directory(steam)
        if (os.fstat(steam).st_dev, os.fstat(steam).st_ino, os.fstat(steam).st_mode) != initial:
            raise ValueError("Steam root changed")
    else:
        raise ValueError("Steam prefix must be a directory or ordinary prefix alias")
    apps = os.open("steamapps", DIR_FLAGS, dir_fd=steam)
    stack.callback(os.close, apps)
    meta = _directory(apps)
    return apps, (meta.st_dev, _mount_id(apps))


def _inspect_tree(fd, anchor, budget, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("cache traversal depth unsupported")
    if not hasattr(os, "O_PATH"):
        raise NotImplementedError("Linux no-follow metadata handles required")
    with os.scandir(fd) as entries:
        for entry in entries:
            budget[0] += 1
            if budget[0] > MAX_ENTRIES:
                raise ValueError("cache traversal entry budget exceeded")
            before = entry.stat(follow_symlinks=False)
            mode = before.st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise UnsupportedCachePath("special_entry")
            flags = DIR_FLAGS if stat.S_ISDIR(mode) else os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
            child = os.open(entry.name, flags, dir_fd=fd)
            try:
                actual = _same_anchor(child, anchor)
                if (actual.st_ino, actual.st_dev, actual.st_mode) != (
                    before.st_ino,
                    before.st_dev,
                    before.st_mode,
                ):
                    raise ValueError("cache entry changed while checking")
                if stat.S_ISDIR(mode):
                    _directory(child)
                    _inspect_tree(child, anchor, budget, depth + 1)
                # Regular hardlinks and descendant symlinks are intentionally allowed.
                # Descriptor-relative unlink below removes their cache names, never their targets.
            finally:
                os.close(child)


def _verify_entry(parent, name, opened, anchor):
    current = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        actual = _same_anchor(current, anchor)
        wanted = os.fstat(opened)
        if (actual.st_dev, actual.st_ino, actual.st_mode) != (
            wanted.st_dev,
            wanted.st_ino,
            wanted.st_mode,
        ):
            raise ValueError("cache entry changed before unlink")
    finally:
        os.close(current)


def _delete_contents(fd, anchor, budget, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("cache deletion depth unsupported")
    with os.scandir(fd) as entries:
        for entry in entries:
            budget[0] += 1
            if budget[0] > MAX_ENTRIES:
                raise ValueError("cache deletion entry budget exceeded")
            before = entry.stat(follow_symlinks=False)
            if not (
                stat.S_ISDIR(before.st_mode)
                or stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
            ):
                raise UnsupportedCachePath("special_entry")
            flags = (
                DIR_FLAGS
                if stat.S_ISDIR(before.st_mode)
                else os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            child = os.open(entry.name, flags, dir_fd=fd)
            try:
                actual = _same_anchor(child, anchor)
                if (actual.st_dev, actual.st_ino, actual.st_mode) != (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                ):
                    raise ValueError("cache entry changed during deletion")
                if stat.S_ISDIR(actual.st_mode):
                    _directory(child)
                    _delete_contents(child, anchor, budget, depth + 1)
                    _verify_entry(fd, entry.name, child, anchor)
                    os.rmdir(entry.name, dir_fd=fd)
                else:
                    _verify_entry(fd, entry.name, child, anchor)
                    os.unlink(entry.name, dir_fd=fd)
            finally:
                os.close(child)


def _perform_delete(profile, argument):
    cache, appid = parse_argument(argument)
    with ExitStack() as stack:
        apps, anchor = _steamapps(profile, stack)
        cachefd = os.open(cache, DIR_FLAGS, dir_fd=apps)
        stack.callback(os.close, cachefd)
        _directory(cachefd)
        _same_anchor(cachefd, anchor)
        parent, name, target = apps, cache, cachefd
        if appid is not None:
            target = os.open(appid, DIR_FLAGS, dir_fd=cachefd)
            stack.callback(os.close, target)
            _directory(target)
            _same_anchor(target, anchor)
            parent, name = cachefd, appid
        expected = _entry_identity(parent, name)
        actual = os.fstat(target)
        if expected != (actual.st_dev, actual.st_ino, actual.st_mode):
            raise ValueError("cache target changed")
        _inspect_tree(target, anchor, [0])
        if _entry_identity(parent, name) != expected:
            raise ValueError("cache target changed after preflight")
        _verify_entry(parent, name, target, anchor)
        _delete_contents(target, anchor, [0])
        _verify_entry(parent, name, target, anchor)
        os.rmdir(name, dir_fd=parent)


def make_replacement(profile):
    if not isinstance(profile, CacheProfile):
        raise TypeError("Framey-owned cache profile required")

    lock = asyncio.Lock()

    async def delete_cache(cls, dirName):
        parse_argument(dirName)
        async with lock:
            return await accepted_delete(dirName)

    async def accepted_delete(dirName):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="framey-storage-cache")
        try:
            future = asyncio.get_running_loop().run_in_executor(
                executor, _perform_delete, profile, dirName
            )
            try:
                # wait() never forwards caller cancellation to the owned job and
                # leaves result/error retrieval here, including during drain.
                await asyncio.wait({future})
                return future.result()
            except asyncio.CancelledError as cancelled:
                while not future.done():
                    try:
                        await asyncio.wait({future})
                    except asyncio.CancelledError:
                        continue
                failure = future.exception()
                if failure is not None:
                    raise cancelled from failure
                raise
        finally:
            executor.shutdown(wait=True, cancel_futures=False)

    delete_cache.__framey_patch_id__ = ENTRY_ID
    return delete_cache


def _expected_methods(source, filename):
    compiled = compile(source, filename, "exec", dont_inherit=True)
    plugin = next(
        value
        for value in compiled.co_consts
        if isinstance(value, types.CodeType) and value.co_name == "Plugin"
    )
    return {value.co_name: value for value in plugin.co_consts if isinstance(value, types.CodeType)}


class PatchContractError(ValueError):
    """Known original source loaded with an unsupported runtime contract."""


def apply_known_patches(module, source, home):
    """Apply only the fixed source/runtime match; wrapper IDs have no authority."""
    if not isinstance(source, bytes) or hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        return []

    def require(value, reason):
        if not value:
            raise PatchContractError("Recognized Storage backend contract mismatch: " + reason)

    try:
        profile = CacheProfile(home)
    except (OSError, ValueError, TypeError) as error:
        raise PatchContractError(
            "Recognized Storage backend requires a normal owned home"
        ) from error
    require(getattr(module, "homeDir", None) == str(profile.home), "owned_home_binding")
    filename = getattr(module, "__file__", None)
    require(isinstance(filename, str), "module_file")
    path = Path(filename)
    require(
        path.is_absolute() and not path.is_symlink() and path.resolve() == path, "module_file_alias"
    )
    try:
        with path.open("rb") as stream:
            actual_source = stream.read(65537)
    except OSError as error:
        raise PatchContractError("Recognized Storage loaded file is unavailable") from error
    require(actual_source == source, "loaded_file_fingerprint")
    plugin = getattr(module, "Plugin", None)
    require(
        isinstance(plugin, type)
        and plugin.__name__ == "Plugin"
        and plugin.__module__ == module.__name__,
        "class_contract",
    )
    expected = _expected_methods(source, filename)
    methods = {
        name: value for name, value in plugin.__dict__.items() if isinstance(value, classmethod)
    }
    require(set(methods) == set(expected), "class_method_contract")
    for name, descriptor in methods.items():
        function = descriptor.__func__
        require(
            isinstance(function, types.FunctionType)
            and function.__code__ == expected[name]
            and function.__globals__ is module.__dict__,
            "runtime_method_fingerprint",
        )
    original = methods["delete_cache"].__func__
    require(
        inspect.iscoroutinefunction(original)
        and list(inspect.signature(original).parameters) == ["cls", "dirName"],
        "delete_signature",
    )
    implementation = make_replacement(profile)

    async def replacement(cls, dirName):
        if getattr(module, "homeDir", None) != str(profile.home):
            raise PatchContractError("Recognized Storage home binding changed after startup")
        return await implementation(cls, dirName)

    replacement.__framey_patch_id__ = ENTRY_ID
    replacement.__name__ = "delete_cache"
    replacement.__qualname__ = "Plugin.delete_cache"
    plugin.delete_cache = classmethod(replacement)
    return [
        {
            "entry_id": ENTRY_ID,
            "source_sha256": SOURCE_SHA256,
            "implementation_owner": "Framey",
            "path_profile": PATH_PROFILE,
        }
    ]
