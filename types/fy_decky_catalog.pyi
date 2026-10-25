from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, TypedDict, Union

from fy_catalog import CatalogError as CatalogError

FEED_MAX: int
ROW_MAX: int
VERSION_MAX: int
DETAIL_VERSIONS: int
ARCHIVE_MAX: int
CACHE_MAX: int
TTL: int
RETRY: int
ASSESS_TIMEOUT: int

class _CatalogError(TypedDict):
    error: str
    code: str

class _DeckySource(TypedDict):
    id: Literal["decky_stable", "decky_testing"]
    label: str
    url: str
    format: Literal["decky"]

class _NativeBinary(TypedDict):
    path: str
    format: Literal["ELF", "PE", "Mach-O"]
    architecture: Literal["aarch64", "x86_64", "x86", "arm", "unknown"]

class _ArchiveAssessment(TypedDict):
    state: Literal["port_required"]
    status: Literal["incompatible"]
    label: str
    architecture: Literal["unknown", "mixed", "aarch64", "x86_64", "other"]
    architecture_status: Literal["unverified", "incompatible", "evidence_only"]
    root_required: bool
    native_binaries: List[_NativeBinary]
    native_binary_count: int
    native_binaries_truncated: bool
    target_platform: str
    runtime_verified: Literal[False]
    basis: Literal["static_package_inspection"]
    has_frontend: bool
    has_backend: bool
    flags: List[str]
    dependencies: List[str]
    hardware_assumptions: List[str]
    reasons: List[str]
    warnings: List[str]
    verified_sha256: Literal[True]
    bytes: int
    files: int
    not_executed: Literal[True]

class _AssessmentReply(TypedDict):
    id: str
    format: Literal["decky"]
    read_only: Literal[True]
    version: str
    hash: str
    source: _DeckySource
    artifact: str
    identity: str
    version_identity: str
    checked_at: Union[int, float]
    downloaded_at: Union[int, float]
    assessment: _ArchiveAssessment

class _AssessmentSource(TypedDict, total=False):
    source: Literal["decky_stable", "decky_testing"]
    refresh: bool

class _AssessmentRequest(_AssessmentSource):
    id: str
    identity: str
    version_identity: str
    confirm: bool

_Metadata = Union[bytes, str]
_MetadataFetcher = Callable[[str, int], Union[_Metadata, Awaitable[_Metadata]]]
_ArchiveFetcher = Callable[[str, int], Awaitable[bytes]]

def parse_decky(data: _Metadata) -> Dict[str, Any]: ...
def inspect_archive(data: bytes, expected_hash: str, platform: str = ...) -> _ArchiveAssessment: ...

class DeckyCatalog:
    host: Any
    fetcher: Optional[_MetadataFetcher]
    archive_fetcher: Optional[_ArchiveFetcher]
    path: Path
    cache: Optional[Dict[str, Any]]
    def __init__(self, host: Any, fetcher: Optional[_MetadataFetcher] = ..., archive_fetcher: Optional[_ArchiveFetcher] = ...) -> None: ...
    def now(self) -> float: ...
    def source(self, arg: Dict[str, Any]) -> _DeckySource: ...
    async def list(self, arg: Optional[Dict[str, Any]] = ...) -> Dict[str, Any]: ...
    async def details(self, arg: Optional[Dict[str, Any]] = ...) -> Dict[str, Any]: ...
    async def assess(self, arg: Optional[_AssessmentRequest] = ...) -> Union[_AssessmentReply, _CatalogError]: ...
