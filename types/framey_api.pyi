from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

PLUGIN: str
API: int

class _Runtime:
    framey: str
    api: int
    platform: str
    python: str
    plugin: str
    version: str
    plugin_dir: Optional[Path]

class _Dirs:
    config: Path
    data: Path
    runtime: Path
    logs: Path

class _Settings:
    def get(self, key: str, default: Any = ...) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def update(self, mapping: Mapping[str, Any]) -> None: ...
    def delete(self, key: str) -> None: ...
    def all(self) -> Dict[str, Any]: ...
    def reset(self) -> None: ...

class _Log:
    dropped: int
    def debug(self, msg: str, *args: Any) -> None: ...
    def info(self, msg: str, *args: Any) -> None: ...
    def warning(self, msg: str, *args: Any) -> None: ...
    def error(self, msg: str, *args: Any) -> None: ...
    def close(self) -> None: ...

class _Events:
    def emit(self, name: str, payload: Any = ...) -> bool: ...

class _Migrate:
    def move(self, src: Any, dst: Any, overwrite: bool = ...) -> Dict[str, Any]: ...
    def keys(self, mapping: Mapping[str, str]) -> Dict[str, List[List[str]]]: ...
    def copy_once(self, src: Any, dst: Any, marker: str) -> bool: ...
    def snapshot(self) -> Optional[Path]: ...

runtime: _Runtime
dirs: _Dirs
settings: _Settings
log: _Log
events: _Events
migrate: _Migrate

class FrameyApiError(Exception):
    code: str
    message: str
    def __init__(self, code: str, message: str = ...) -> None: ...

async def fetch(
    url: str,
    *,
    method: str = ...,
    headers: Optional[Mapping[str, str]] = ...,
    body: Optional[str] = ...,
    timeout: float = ...,
    max_bytes: int = ...,
    dest: Optional[str] = ...,
) -> Dict[str, Any]: ...
async def resource_url(root: str, path: str, ttl: int = ...) -> str: ...
async def resource_revoke(url: str) -> bool: ...
async def list_dir(
    root: str,
    path: str = ...,
    *,
    hidden: bool = ...,
    dirs_only: bool = ...,
    exts: Optional[Sequence[str]] = ...,
) -> Dict[str, Any]: ...
def companion(name: str) -> Path: ...
