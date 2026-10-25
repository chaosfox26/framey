from os import PathLike
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, TypedDict, Union

_PathInput = Union[str, PathLike[str]]

class _FetchResult(TypedDict):
    status: int
    headers: Dict[str, str]
    text: Optional[str]
    b64: Optional[str]
    bytes: int
    truncated: bool
    path: Optional[str]

class _FileEntry(TypedDict):
    name: str
    type: Literal["link", "dir", "file", "other"]
    size: Optional[int]
    mtime: Optional[int]

class _FileRoot(TypedDict):
    id: str
    label: str

class _DirectoryRoots(TypedDict, total=False):
    roots: List[_FileRoot]

class _DirectoryResult(_DirectoryRoots):
    root: str
    path: str
    parent: Optional[str]
    entries: List[_FileEntry]
    truncated: bool

class _MoveResult(TypedDict):
    moved: bool
    reason: str

class _KeysResult(TypedDict):
    renamed: List[List[str]]

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
    def debug(self, msg: object, *args: Any) -> None: ...
    def info(self, msg: object, *args: Any) -> None: ...
    def warning(self, msg: object, *args: Any) -> None: ...
    def error(self, msg: object, *args: Any) -> None: ...
    def close(self) -> None: ...

class _Events:
    def emit(self, name: str, payload: Any = ...) -> bool: ...

class _Migrate:
    def move(self, src: _PathInput, dst: _PathInput, overwrite: bool = ...) -> _MoveResult: ...
    def keys(self, mapping: Dict[str, str]) -> _KeysResult: ...
    def copy_once(self, src: _PathInput, dst: _PathInput, marker: str) -> bool: ...
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
    def __init__(self, code: object, message: object = ...) -> None: ...

async def fetch(
    url: str,
    *,
    method: str = ...,
    headers: Optional[Mapping[str, str]] = ...,
    body: Optional[str] = ...,
    timeout: float = ...,
    max_bytes: int = ...,
    dest: Optional[str] = ...,
) -> _FetchResult: ...
async def resource_url(root: str, path: str, ttl: float = ...) -> str: ...
async def resource_revoke(url: str) -> bool: ...
async def list_dir(
    root: str,
    path: str = ...,
    *,
    hidden: Optional[bool] = ...,
    dirs_only: Optional[bool] = ...,
    exts: Optional[Sequence[str]] = ...,
) -> _DirectoryResult: ...
def companion(name: str) -> Path: ...
