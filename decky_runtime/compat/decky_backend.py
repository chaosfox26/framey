"""Framey's independently implemented, experimental Decky Python API profile.

Runs trusted original plugin code in its owning Framey worker. This is an API
adapter, not an isolation boundary. Only one backend may own Python's import
and environment context at a time; callers must await stop before replacing it.
"""

import asyncio
import importlib.util
import inspect
import json
import logging
import math
import os
import pwd
import re
import sys
import tempfile
import types
from pathlib import Path

try:
    import ssl
except ImportError:
    ssl = None


_active_backend = None

# Only the audited generated Terminal event family receives an envelope.
# Keep SDK/protocol wire admission, payload/rate/drop accounting and owner routing intact.
_COMPAT_EVENT_WIRE = "decky.compat.event"
_TERMINAL_OUTPUT = re.compile(
    r"terminal_output#[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


def _settings_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Framey settings must contain finite JSON numbers")
    return number


def _settings_constant(value):
    raise ValueError("Framey settings do not support non-finite JSON constants")


class _SettingsStore:
    """Independently authored JSON settings storage confined to owned Framey state."""

    def __init__(self, config, state_roots, name, settings_directory):
        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or any(character in name for character in ("/", "\\", "\0"))
        ):
            raise ValueError("Framey settings name must be a non-empty filename stem")
        self._state_roots = state_roots
        directory = config if settings_directory is None else Path(settings_directory)
        self.path = str(directory.absolute() / (name + ".json"))
        self.settings = {}
        file = self._file()
        file.parent.mkdir(parents=True, exist_ok=True)
        if file.exists():
            self.read()
        else:
            self.commit()

    def _file(self):
        candidate = Path(self.path)
        resolved = candidate.resolve()
        if candidate.is_symlink() or not any(
            root in resolved.parents for root in self._state_roots
        ):
            raise ValueError(
                "Settings must stay within this plugin's owned Framey state directories"
            )
        return candidate

    def read(self):
        loaded = json.loads(
            self._file().read_text(encoding="utf-8"),
            parse_float=_settings_number,
            parse_constant=_settings_constant,
        )
        if not isinstance(loaded, dict):
            raise ValueError("Framey settings must contain a JSON object")
        self.settings = loaded

    def commit(self):
        if not isinstance(self.settings, dict):
            raise ValueError("Framey settings must contain a JSON object")
        # Encode before opening anything, then replace only a complete flushed file.
        encoded = json.dumps(self.settings, ensure_ascii=False, allow_nan=False, indent=2)
        destination = self._file()
        descriptor, temporary = tempfile.mkstemp(prefix=".framey-settings-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._file())
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def getSetting(self, key, default=None):
        return self.settings.get(key, default)

    def setSetting(self, key, value):
        self.settings[key] = value
        self.commit()


def _settings_module(config, state_roots):
    module = types.ModuleType("settings", "Framey's independently implemented settings helper")

    class SettingsManager(_SettingsStore):
        """Framey implementation of the selected public SettingsManager methods."""

        def __init__(self, name, settings_directory=None):
            super().__init__(config, state_roots, name, settings_directory)

    module.SettingsManager = SettingsManager
    return module


def _worker_user_id():
    uid = os.getuid()
    if uid == 0 or uid != os.geteuid():
        raise RuntimeError("Framey identity helpers require an unprivileged normal worker")
    return uid


def _worker_user():
    return pwd.getpwuid(_worker_user_id()).pw_name


def _worker_ssl_context():
    """Return a fresh verified TLS client context using platform trust defaults."""
    if ssl is None:
        raise ImportError("TLS support is unavailable in this Python runtime")
    return ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)


def _helpers_module():
    module = types.ModuleType("helpers", "Framey compatibility helpers for normal worker identity")
    module.get_user = _worker_user
    module.get_user_id = _worker_user_id
    module.get_ssl_context = _worker_ssl_context
    return module


def _standalone_patch_registry():
    """Load the trusted sibling when this bridge is used directly by Framey."""
    path = Path(__file__).with_name("decky_patches.py")
    module_name = "_framey_owned_decky_patches"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    return module


class DeckyBackend:
    """Adapt a package's original Plugin class without loading Decky Loader."""

    def __init__(self, root, config, data, logs, logger=None, emit=None, patch_registry=None):
        # Constructors never import or execute the package.
        self.root = Path(root).resolve()
        self.config = Path(config).resolve()
        self.data = Path(data).resolve()
        self.logs = Path(logs).resolve()
        self.logger = logger
        self.emit = emit
        # Framey binds the fixed owned module before any vendor import context.
        self._patch_registry = (
            patch_registry if patch_registry is not None else _standalone_patch_registry()
        )
        if not isinstance(self._patch_registry, types.ModuleType):
            raise TypeError("A Framey-owned patch registry module is required")
        self._patch_home = Path.home()
        self._plugin = None
        self._main_task = None
        self._saved_modules = {}
        self._local_names = set()
        self._saved_path = None
        self._saved_env = None
        self._saved_bytecode = None
        self._state = "new"
        self._api_version = 0

    def _metadata(self):
        if not self.root.is_dir():
            raise ValueError("Decky plugin root must be an existing directory")
        for name in ("plugin.json", "main.py"):
            path = self.root / name
            if not path.is_file() or path.resolve().parent != self.root:
                raise ValueError(f"Decky plugin requires an original {name} in its root")
        try:
            manifest = json.loads((self.root / "plugin.json").read_text(encoding="utf-8"))
            package_path = self.root / "package.json"
            package = (
                json.loads(package_path.read_text(encoding="utf-8"))
                if package_path.is_file()
                else {}
            )
        except (OSError, ValueError) as error:
            raise ValueError("Invalid Decky package metadata") from error
        if not isinstance(manifest, dict) or not isinstance(package, dict):
            raise ValueError("Decky package metadata must be objects")
        flags = manifest.get("flags", [])
        if not isinstance(flags, list) or flags:
            raise ValueError("This Framey profile supports only plugins with no privilege flags")
        api_version = manifest.get("api_version", 0)
        if type(api_version) is not int or api_version < 0:
            raise ValueError("Decky api_version must be a non-negative integer")
        name = manifest.get("name")
        version = package.get("version", "unknown")
        if not isinstance(name, str) or not name.strip() or not isinstance(version, str):
            raise ValueError("Decky plugin name and version must be strings")
        for directory in (self.config, self.data, self.logs):
            if directory == self.root or self.root in directory.parents:
                raise ValueError(
                    "Framey state directories must be outside the original plugin root"
                )
        return {
            "name": name,
            "version": version,
            "api_version": api_version,
            "compatibility": "framey",
        }

    @staticmethod
    def _migrate(*sources):
        # Deliberately never touch arbitrary external settings or pretend to move them.
        for source in sources:
            if not isinstance(source, str):
                raise TypeError("Legacy migration sources must be path strings")
            if not source or "\0" in source:
                raise ValueError("Legacy migration sources must be non-empty valid paths")
        existing = False
        for source in sources:
            try:
                os.lstat(source)
            except FileNotFoundError:
                continue
            existing = True
        if existing:
            raise NotImplementedError(
                "Migration of existing legacy Decky state is unsupported by Framey"
            )
        return {source: "" for source in sources}

    async def _emit(self, event, *args):
        if self.emit is None:
            raise NotImplementedError("No Framey event transport is configured")
        payload = list(args)
        if isinstance(event, str) and _TERMINAL_OUTPUT.fullmatch(event):
            payload = {"v": 1, "name": event, "args": payload}
            event = _COMPAT_EVENT_WIRE
        result = self.emit(event, payload)
        if inspect.isawaitable(result):
            return await result
        return result

    def _install_context(self, metadata):
        self._saved_path = list(sys.path)
        self._saved_env = dict(os.environ)
        self._saved_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        search = [self.root, self.root / "py_modules"]
        for directory in search:
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.suffix == ".py":
                        self._local_names.add(path.stem)
                    elif path.is_dir():
                        self._local_names.add(path.name)
        bundled_settings = "settings" in self._local_names
        bundled_helpers = "helpers" in self._local_names
        self._local_names.update(
            ("decky", "decky_plugin", "settings", "helpers", "_framey_original_decky_backend")
        )
        for name in list(sys.modules):
            if name.split(".", 1)[0] in self._local_names:
                self._saved_modules[name] = sys.modules.pop(name)
        sys.path[:0] = [str(path) for path in search if path.is_dir()]
        if not bundled_settings:
            sys.modules["settings"] = _settings_module(
                self.config, (self.config, self.data, self.logs)
            )
        if not bundled_helpers:
            sys.modules["helpers"] = _helpers_module()
        module = types.ModuleType(
            "decky_plugin", "Framey compatibility API; Decky Loader is not installed"
        )
        constants = {
            "DECKY_PLUGIN_DIR": str(self.root),
            "DECKY_PLUGIN_SETTINGS_DIR": str(self.config),
            "DECKY_PLUGIN_RUNTIME_DIR": str(self.data),
            "DECKY_PLUGIN_LOG_DIR": str(self.logs),
            "DECKY_PLUGIN_NAME": metadata["name"],
            "DECKY_PLUGIN_VERSION": metadata["version"],
            "DECKY_HOME": str(self.config.parent / "legacy-decky"),
            "DECKY_USER_HOME": str(Path.home()),
            "DECKY_USER": os.environ.get("USER", ""),
            "DECKY_VERSION": "Framey compatibility (Decky Loader is not installed)",
        }
        for name, value in constants.items():
            setattr(module, name, value)
            os.environ[name] = value
        module.logger = self.logger or logging.getLogger("framey.decky." + metadata["name"])
        module.emit = self._emit
        module.migrate_settings = self._migrate
        module.migrate_runtime = self._migrate
        module.migrate_logs = self._migrate
        sys.modules["decky"] = sys.modules["decky_plugin"] = module
        importlib.invalidate_caches()

    def _restore_context(self):
        global _active_backend
        if self._saved_path is None:
            return
        for name, module in list(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            local = False
            if isinstance(filename, str):
                try:
                    local = self.root in Path(filename).resolve().parents
                except (OSError, ValueError):
                    pass
            if name.split(".", 1)[0] in self._local_names or local:
                sys.modules.pop(name, None)
        sys.modules.update(self._saved_modules)
        sys.path[:] = self._saved_path
        sys.dont_write_bytecode = self._saved_bytecode
        os.environ.clear()
        os.environ.update(self._saved_env)
        self._saved_path = None
        self._saved_env = None
        self._saved_modules.clear()
        importlib.invalidate_caches()
        if _active_backend is self:
            _active_backend = None

    async def _invoke(self, method, *args, **kwargs):
        function = getattr(self._plugin, method)
        if not callable(function):
            raise TypeError("Decky RPC target is not callable")
        if self._api_version == 0:
            result = function(self._plugin, *args, **kwargs)
        else:
            result = function(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _check_main(self):
        if self._main_task is not None and self._main_task.done():
            if self._main_task.cancelled():
                raise RuntimeError("Decky plugin main task was cancelled")
            error = self._main_task.exception()
            if error is not None:
                raise error

    async def start(self):
        """Validate, import, migrate and schedule the original backend explicitly."""
        global _active_backend
        if self._state != "new":
            raise RuntimeError("Decky backend has already been started or stopped")
        if _active_backend is not None:
            raise RuntimeError("Only one Decky backend may be active in a worker process")
        metadata = self._metadata()
        self._api_version = metadata["api_version"]
        _active_backend = self
        try:
            for directory in (self.config, self.data, self.logs):
                directory.mkdir(parents=True, exist_ok=True)
            self._install_context(metadata)
            spec = importlib.util.spec_from_file_location(
                "_framey_original_decky_backend", self.root / "main.py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            source = (self.root / "main.py").read_bytes()
            spec.loader.exec_module(module)
            applied = self._patch_registry.apply_known_patches(module, source, self._patch_home)
            metadata["framey_patches"] = {
                "registry_version": self._patch_registry.REGISTRY_VERSION,
                "applied": applied,
            }
            plugin_class = module.Plugin
            if not isinstance(plugin_class, type):
                raise TypeError("Decky main.py must export a Plugin class")
            self._plugin = plugin_class() if self._api_version > 0 else plugin_class
            if hasattr(self._plugin, "_migration"):
                await self._invoke("_migration")
            self._state = "running"
            if hasattr(self._plugin, "_main"):
                self._main_task = asyncio.create_task(self._invoke("_main"))
                # Let immediate initialization complete without awaiting a long-running loop.
                await asyncio.sleep(0)
                self._check_main()
            return metadata
        except BaseException:
            try:
                await self.stop()
            except BaseException:
                pass
            # A directory failure can happen before an import context was installed.
            if _active_backend is self:
                _active_backend = None
            self._state = "stopped"
            raise

    async def call(self, method, args, legacy=False, *, result_envelope_version=None):
        """Dispatch public RPC with the package's original argument convention."""
        if result_envelope_version is not None and (
            type(result_envelope_version) is not int or result_envelope_version != 1
        ):
            raise ValueError("Unsupported Framey RPC result envelope version")
        if not isinstance(method, str) or not method.isidentifier() or method.startswith("_"):
            raise ValueError("Decky RPC requires a public method name")
        if type(legacy) is not bool:
            raise TypeError("legacy must be a boolean")
        if not isinstance(args, dict if legacy else list):
            raise TypeError(
                "Legacy RPC requires a keyword object; modern RPC requires an argument list"
            )
        if legacy != (self._api_version == 0):
            raise ValueError("RPC argument protocol does not match this plugin's api_version")
        if self._state != "running":
            raise RuntimeError("Decky backend is not running")
        self._check_main()
        result = await self._invoke(method, **args) if legacy else await self._invoke(method, *args)
        if result_envelope_version == 1:
            return {"__framey_decky_rpc_result__": 1, "value": result}
        return result

    async def stop(self):
        """Unload, cancel and await main, and restore the worker's original context."""
        if self._state == "stopped":
            return
        error = None
        try:
            if self._plugin is not None and hasattr(self._plugin, "_unload"):
                await self._invoke("_unload")
        except BaseException as failure:
            error = failure
        try:
            task = self._main_task
            if task is not None:
                cancelling = not task.done()
                if cancelling:
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError as failure:
                    if not cancelling and error is None:
                        error = failure
                except BaseException as failure:
                    if error is None:
                        error = failure
        finally:
            self._restore_context()
            self._plugin = None
            self._main_task = None
            self._state = "stopped"
        if error is not None:
            raise error
