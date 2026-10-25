import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import stat
import time
import zipfile
import zlib
from pathlib import Path

import fy_net
import fy_pkg
import fy_store
from fy_catalog import CatalogError, _decode, _json, _safe, validate_source_url

FEED_MAX = 524288
REPLY_MAX = 600000
ROW_MAX = 512
VERSION_MAX = 100
DETAIL_VERSIONS = 50
ARCHIVE_MAX = 5_000_000
CACHE_MAX = 1_572_864
TTL, RETRY, ASSESS_TIMEOUT = 86400, 900, 50
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"decky-([1-9][0-9]{0,15})\Z")
CDN = "https://cdn.tzatzikiweeb.moe/file/steam-deck-homebrew/versions/"
SOURCES = {
    "decky_stable": {"id": "decky_stable", "label": "Decky Store", "url": "https://plugins.deckbrew.xyz/plugins", "format": "decky"},
    "decky_testing": {"id": "decky_testing", "label": "Decky Store Testing", "url": "https://testing.deckbrew.xyz/plugins", "format": "decky"},
}
PORT_REASON = "Built for Decky’s interface; it needs a Framey version before it can run here."
HEADERS = {"Accept": "application/json", "User-Agent": "Framey/1.0 catalog"}


def _fail(code, message):
    raise CatalogError(code, message)


def _text(value, limit, required=False):
    if not isinstance(value, str) or any(not c.isprintable() for c in value) or (required and not value.strip()):
        _fail("bad_catalog", "Decky catalog contains invalid display metadata")
    return value.strip()[:limit]


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**15 else 0


def _tag(value):
    if not isinstance(value, str):
        _fail("bad_catalog", "Decky catalog tags are not valid")
    value = _text(value, 80).casefold()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:32]


def _version(raw):
    if not isinstance(raw, dict):
        _fail("bad_catalog", "Decky catalog version is not valid")
    digest = raw.get("hash")
    if not isinstance(digest, str) or not HEX.fullmatch(digest):
        _fail("bad_catalog", "Decky catalog version has no valid SHA-256 hash")
    if not isinstance(raw.get("name"), str) or len(raw["name"]) > 80:
        _fail("bad_catalog", "Decky version label exceeds its identity limit")
    name = _text(raw.get("name"), 80, True)
    artifact = raw.get("artifact")
    url = CDN + digest + ".zip" if artifact is None else artifact
    try:
        url = validate_source_url(url)
    except CatalogError:
        url = ""
    return {"name": name, "hash": digest, "created": _text(raw.get("created", ""), 40),
            "downloads": _count(raw.get("downloads")), "updates": _count(raw.get("updates")), "artifact": url}


def parse_decky(data):
    raw = _decode(data, FEED_MAX)
    if not isinstance(raw, list) or len(raw) > ROW_MAX:
        _fail("bad_catalog", "Decky catalog must be a bounded plugin list")
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or type(item.get("id")) is not int or not 1 <= item["id"] <= 9_007_199_254_740_991:
            _fail("bad_catalog", "Decky catalog has an invalid plugin ID")
        numeric_id = item["id"]
        if numeric_id in seen:
            _fail("bad_catalog", "Decky catalog contains duplicate plugin IDs")
        seen.add(numeric_id)
        if "visible" in item and type(item["visible"]) is not bool:
            _fail("bad_catalog", "Decky catalog visibility is not valid")
        if item.get("visible") is False:
            continue
        versions = item.get("versions")
        if not isinstance(versions, list) or len(versions) > VERSION_MAX:
            _fail("bad_catalog", "Decky catalog version list is not valid or is too large")
        clean_versions = [_version(x) for x in versions]
        version_ids = {(x["name"], x["hash"]) for x in clean_versions}
        if len(version_ids) != len(clean_versions):
            _fail("bad_catalog", "Decky catalog contains duplicate versions")
        raw_tags = item.get("tags", [])
        if not isinstance(raw_tags, list) or len(raw_tags) > 32:
            _fail("bad_catalog", "Decky catalog tags are not valid")
        tags = list(dict.fromkeys(filter(None, (_tag(x) for x in raw_tags))))[:16]
        rows.append({"id": "decky-" + str(numeric_id), "catalog_id": numeric_id,
                     "name": _text(item.get("name"), 100, True), "author": _text(item.get("author", ""), 120),
                     "description": _text(item.get("description", ""), 1000), "tags": tags,
                     "category": tags[0] if tags else "other", "versions": clean_versions,
                     "updated": _text(item.get("updated", ""), 40), "format": "decky"})
    return {"plugins": rows}


def _cache_bytes(catalog):
    rows = []
    for row in catalog["plugins"]:
        versions = []
        for value in row["versions"]:
            version = {"name": value["name"], "hash": value["hash"]}
            for key in ("created", "downloads", "updates"):
                if value[key]:
                    version[key] = value[key]
            if value["artifact"] != CDN + value["hash"] + ".zip":
                version["artifact"] = value["artifact"]
            versions.append(version)
        item = {"id": row["catalog_id"], "name": row["name"], "versions": versions}
        for key in ("author", "description", "tags", "updated"):
            if row[key]:
                item[key] = row[key]
        rows.append(item)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _identity(source, value):
    return hashlib.sha256(_json({"source": source, "value": value}).encode()).hexdigest()


def _native_header(data):
    if data.startswith(b"\x7fELF"):
        if len(data) < 20 or data[4] not in (1, 2) or data[5] not in (1, 2) or data[6] != 1:
            return "ELF", "unknown"
        order = "little" if data[5] == 1 else "big"
        size, position = (64, 52) if data[4] == 2 else (52, 40)
        if len(data) < size or int.from_bytes(data[20:24], order) != 1 or int.from_bytes(data[position:position + 2], order) != size or int.from_bytes(data[16:18], order) not in (1, 2, 3, 4):
            return "ELF", "unknown"
        machine = int.from_bytes(data[18:20], order)
        if machine in (183, 62) and data[4] != 2 or machine in (3, 40) and data[4] != 1:
            return "ELF", "unknown"
        return "ELF", {183: "aarch64", 62: "x86_64", 3: "x86", 40: "arm"}.get(machine, "unknown")
    if data.startswith(b"MZ"):
        return "PE", "unknown"
    if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        if data[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            return "Mach-O", "unknown"
        size = 32 if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else 28
        if len(data) < size:
            return "Mach-O", "unknown"
        order = "little" if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe") else "big"
        cpu = int.from_bytes(data[4:8], order) if len(data) >= 8 else 0
        if bool(cpu & 0x1000000) != (size == 32):
            return "Mach-O", "unknown"
        return "Mach-O", {0x100000C: "aarch64", 0x1000007: "x86_64"}.get(cpu, "unknown")
    return None


def _metadata(archive, info, limit):
    if info.file_size > limit:
        _fail("bad_package", "Plugin metadata exceeds the inspection limit")
    with archive.open(info) as stream:
        raw = stream.read(limit + 1)
    value = _decode(raw, limit)
    if not isinstance(value, dict):
        _fail("bad_package", "Plugin metadata is not an object")
    return value


def inspect_archive(data, expected_hash, platform="linux-aarch64"):
    if not isinstance(expected_hash, str) or not HEX.fullmatch(expected_hash):
        _fail("bad_hash", "Choose a version with a valid catalog checksum")
    if not isinstance(data, bytes) or not data or len(data) > ARCHIVE_MAX:
        _fail("bad_archive", "The plugin archive exceeds the read-only inspection limit")
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_hash):
        _fail("digest_mismatch", "The downloaded plugin does not match the selected catalog checksum")
    try:
        fy_pkg._zip_end_ok(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            budget, items = fy_pkg._Budget(), []
            for info in archive.infolist():
                budget.add(info.file_size)
                kind = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
                if info.orig_filename != info.filename or info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    _fail("bad_archive", "The archive has encrypted, unsupported or ambiguous entries")
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not info.is_dir()) or (kind == stat.S_IFREG and info.is_dir()):
                    _fail("bad_archive", "The archive contains links or special files")
                items.append((fy_pkg._parts(info.filename), info.is_dir(), info.file_size, info))
            layout = fy_pkg._layout(items, "auto", None)
            files = {"/".join(parts): info for parts, is_dir, _, info in layout if not is_dir}
            if "plugin.json" not in files:
                _fail("bad_package", "The archive has no plugin manifest at its package root")
            manifest = _metadata(archive, files["plugin.json"], 65536)
            package = _metadata(archive, files["package.json"], 262144) if "package.json" in files else {}
            flags = manifest.get("flags", [])
            if not isinstance(flags, list) or len(flags) > 32 or not all(isinstance(x, str) and len(x) <= 80 for x in flags):
                _fail("bad_package", "The plugin declares invalid execution flags")
            flags = [_text(x, 80) for x in flags]
            binaries, assumptions, architectures, binary_count = [], [], set(), 0
            text_candidates = {"main.py", "dist/index.js"}
            for name, info in files.items():
                with archive.open(info) as stream:
                    header = stream.read(64)
                native = _native_header(header)
                if native:
                    binary_count += 1
                    architectures.add(native[1])
                    if len(binaries) < 64:
                        label = "".join(c if c.isprintable() else "?" for c in name)
                        binaries.append({"path": label[:256], "format": native[0], "architecture": native[1]})
                if name in text_candidates:
                    with archive.open(info) as stream:
                        prefix = stream.read(65536).decode("utf-8", "replace")
                    for marker in ("/sys/devices/platform/steamdeck", "steamdeck_hwmon", "/home/deck/", "amdgpu"):
                        if marker in prefix and marker not in assumptions:
                            assumptions.append(marker)
            dependencies = package.get("dependencies", {})
            if not isinstance(dependencies, dict) or len(dependencies) > 128:
                _fail("bad_package", "The plugin dependency list exceeds the inspection limit")
            dependency_names = [_text(k, 100, True) for k in dependencies][:64]
            archs = architectures
            if not binary_count or archs == {"unknown"}:
                architecture = "unknown"
            elif len(archs) > 1:
                architecture = "mixed"
            elif next(iter(archs)) in ("aarch64", "x86_64"):
                architecture = next(iter(archs))
            else:
                architecture = "other"
            reasons = [PORT_REASON]
            architecture_status = "unverified"
            if platform == "linux-aarch64" and binary_count and archs <= {"x86_64", "x86"}:
                architecture_status = "incompatible"
                reasons.append("Includes native code for Intel/AMD processors; your headset needs an ARM64 build of any required programs.")
            elif "aarch64" in archs:
                architecture_status = "evidence_only"
                reasons.append("An ARM64 header was found; operating-system requirements and runtime behavior were not verified.")
            else:
                reasons.append("Support for the headset's architecture has not been established by this package inspection.")
            if architecture == "mixed":
                reasons.append("This archive contains different processor builds; which programs it uses still needs checking.")
            if "root" in flags:
                reasons.append("Requests administrator access through Decky; it needs a Steam Frame helper before it can be used here.")
            if "amdgpu" in assumptions:
                reasons.append("References the AMD graphics driver; support for Steam Frame’s graphics hardware has not been verified.")
            if any(marker != "amdgpu" for marker in assumptions):
                reasons.append("Refers to Steam Deck hardware or paths; those assumptions need checking for Steam Frame.")
            if package.get("remote_binary"):
                reasons.append("Downloads additional programs; their ARM64 support has not been checked.")
            return {"state": "port_required", "status": "incompatible", "label": "Needs a Framey port", "architecture": architecture,
                    "architecture_status": architecture_status, "root_required": "root" in flags,
                    "native_binaries": binaries, "native_binary_count": binary_count, "native_binaries_truncated": binary_count > len(binaries),
                    "target_platform": platform, "runtime_verified": False, "basis": "static_package_inspection",
                    "has_frontend": "dist/index.js" in files,
                    "has_backend": "main.py" in files, "flags": flags, "dependencies": dependency_names,
                    "hardware_assumptions": assumptions, "reasons": reasons,
                    "warnings": ["This is a static inspection. No plugin code was executed, and VR behavior was not tested.",
                                 "The SHA-256 matches the catalog; it is not a publisher signature.",
                                 "Only selected metadata and file headers were read; complete entry contents and CRCs were not checked."],
                    "verified_sha256": True, "bytes": len(data), "files": len(files), "not_executed": True}
    except CatalogError:
        raise
    except (fy_pkg.PkgError, OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError, NotImplementedError, zlib.error):
        _fail("bad_archive", "The archive could not be inspected safely within its limits")


class DeckyCatalog:
    def __init__(self, host, fetcher=None, archive_fetcher=None):
        self.host, self.fetcher, self.archive_fetcher = host, fetcher, archive_fetcher
        self.path = Path(getattr(host, "CONF", Path(host.HOME) / ".config/framey/settings.json")).parent / "decky-catalog.json"
        self.cache = None
        self.load_lock, self.save_lock = asyncio.Lock(), asyncio.Lock()
        self.refreshes = {}
        self.invalid_cache = False
        self.inspection = None

    def now(self):
        try:
            value = self.host.now()
            if type(value) in (int, float) and 0 <= value < 10**12:
                return value
        except (AttributeError, TypeError, ValueError):
            pass
        return time.time()

    def source(self, arg):
        key = arg.get("source", "decky_stable")
        if not isinstance(key, str) or key not in SOURCES:
            _fail("bad_source", "Choose an official Decky catalog")
        if key == "decky_testing":
            try:
                enabled = self.host.settings().get("prefs", {}).get("dev", {}).get("enabled") is True
            except (AttributeError, TypeError):
                enabled = False
            if not enabled:
                _fail("dev_off", "Decky testing catalog requires developer mode")
        return dict(SOURCES[key])

    async def _load(self):
        if self.cache is not None:
            return
        async with self.load_lock:
            if self.cache is not None:
                return
            raw = await asyncio.to_thread(fy_store.read_json, self.path, None, CACHE_MAX)
            self.cache = {}
            if not isinstance(raw, dict) or raw.get("v") != 1 or not isinstance(raw.get("cache"), dict):
                self.invalid_cache = self.path.exists()
                return
            now = self.now()
            for key in SOURCES:
                value = raw["cache"].get(key)
                if not isinstance(value, dict):
                    continue
                try:
                    if isinstance(value.get("raw"), list):
                        encoded = json.dumps(value["raw"], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    elif isinstance(value.get("catalog"), dict):
                        encoded = _cache_bytes(value["catalog"])
                    else:
                        continue
                    catalog = parse_decky(encoded)
                    downloaded = value["downloaded_at"]
                    if type(downloaded) not in (int, float) or not 0 <= downloaded <= now + 60:
                        continue
                    retry = value.get("retry_after", 0)
                    retry = retry if type(retry) in (int, float) and 0 <= retry <= now + RETRY else 0
                    checked = value.get("checked_at", downloaded)
                    checked = checked if type(checked) in (int, float) and downloaded <= checked <= now + 60 else downloaded
                    self.cache[key] = {"catalog": catalog, "downloaded_at": downloaded,
                                       "checked_at": checked, "error": "Previous Decky catalog refresh failed" if value.get("error") else "", "retry_after": retry}
                except (CatalogError, KeyError, TypeError, ValueError):
                    continue

    async def _save(self):
        async with self.save_lock:
            cache = {}
            for key, value in self.cache.items():
                cached = {k: v for k, v in value.items() if k != "catalog"}
                if value.get("catalog"):
                    cached["raw"] = json.loads(_cache_bytes(value["catalog"]))
                cache[key] = cached
            snapshot = {"v": 1, "cache": cache}
            try:
                await asyncio.to_thread(fy_store.write_json_atomic, self.path, snapshot, CACHE_MAX)
                return ""
            except (OSError, ValueError):
                return "The Decky catalog cache could not be saved"

    async def _download(self, url):
        if self.fetcher is not None:
            return await self.fetcher(url, FEED_MAX)
        reply = await fy_net.fetch_plugin("_core", url, headers=HEADERS, timeout=15, max_bytes=FEED_MAX, allow_http=False)
        if reply.get("status") != 200 or reply.get("truncated"):
            _fail("download_failed", "The Decky server did not return a complete catalog")
        if isinstance(reply.get("text"), str):
            return reply["text"]
        import base64
        try:
            return base64.b64decode(reply.get("b64", ""), validate=True)
        except (ValueError, TypeError):
            _fail("download_failed", "The Decky catalog response was unreadable")

    async def _refresh(self, source):
        key, now = source["id"], self.now()
        try:
            catalog = parse_decky(await self._download(source["url"]))
            record = {"catalog": catalog, "downloaded_at": now, "checked_at": now, "error": "", "retry_after": 0}
        except Exception as error:
            record = dict(self.cache.get(key, {}))
            record.update(error=getattr(error, "message", None) or "Decky catalog could not be refreshed", checked_at=now, retry_after=now + RETRY)
        self.cache[key] = record
        warning = "" if record.get("error") and self.invalid_cache else await self._save()
        if warning:
            record["save_warning"] = warning
        return record

    async def _catalog(self, arg):
        source = self.source(arg)
        await self._load()
        key, now = source["id"], self.now()
        record = self.cache.get(key)
        refresh = arg.get("refresh") is True or not record or now - record.get("downloaded_at", 0) >= TTL or bool(record.get("error"))
        fetched = False
        if refresh and (arg.get("refresh") is True or not record or now >= record.get("retry_after", 0)):
            task = self.refreshes.get(key)
            if task is None:
                task = self.refreshes[key] = asyncio.create_task(self._refresh(source))
                task.add_done_callback(lambda done, k=key: self.refreshes.pop(k, None) if self.refreshes.get(k) is done else None)
            record = await asyncio.shield(task)
            fetched = not record.get("error")
        error = record.get("error", "") if record else ""
        catalog = record.get("catalog") if record else None
        origin = "downloaded" if fetched else "cache" if catalog is not None else "unavailable"
        downloaded = record.get("downloaded_at", 0) if record else 0
        stale = not downloaded or bool(error) or now - downloaded >= TTL
        warning = "The Decky catalog is unavailable; no saved catalog exists" if catalog is None else "Saved Decky catalog; refresh failed" if error else "Saved Decky catalog is older than 24 hours" if stale else ""
        if record and record.get("save_warning"):
            warning = record["save_warning"]
        return catalog or {"plugins": []}, {"source": source, "origin": origin, "stale": stale,
                                            "checked_at": record.get("checked_at", 0) if record else 0,
                                            "downloaded_at": downloaded, "error": None, "fetch_error": error or None, "warning": warning}

    def _row(self, record, source, details=False):
        version = record["versions"][0]["name"] if record["versions"] else ""
        identity = _identity(source, record)
        compatibility = {"status": "incompatible", "label": "Needs a Framey port", "reason": PORT_REASON,
                         "reasons": [PORT_REASON], "basis": "decky_format", "runtime_verified": False}
        row = {k: record[k] for k in ("id", "name", "author", "description", "tags", "category", "updated", "format")}
        row.update(version=version, version_count=len(record["versions"]), kind="decky", read_only=True,
                   eligible=False, reason=PORT_REASON, identity=identity, installed=False, linked=False,
                   catalog_managed=False, update_available=False, platform_declared=False, architecture="unknown",
                   compatibility=compatibility, source={"repository": "", "url": source["url"]},
                   compat={"state": "incompatible", "badge": "port", "msg": PORT_REASON, "reasons": [PORT_REASON]})
        if details:
            row["versions"] = [{k: value[k] for k in ("name", "hash", "created", "downloads", "updates")} |
                               {"identity": _identity(source, {"id": record["id"], "version": value}), "inspectable": bool(value["artifact"])}
                               for value in record["versions"][:DETAIL_VERSIONS]]
            row["versions_truncated"] = len(record["versions"]) > DETAIL_VERSIONS
        return row

    @_safe
    async def list(self, arg=None):
        arg = arg if isinstance(arg, dict) else {}
        query = _text(arg.get("query", ""), 120).casefold()
        category, sort = arg.get("category", "all"), arg.get("sort", "name")
        offset, limit = arg.get("offset", 0), arg.get("limit", 50)
        if not isinstance(category, str) or len(category) > 32 or sort not in ("name", "updated", "relevance"):
            _fail("bad_arg", "The Decky catalog filter is not valid")
        if type(offset) is not int or not 0 <= offset <= ROW_MAX or type(limit) is not int or not 1 <= limit <= 100:
            _fail("bad_arg", "The Decky catalog page is not valid")
        catalog, status = await self._catalog(arg)
        categories = sorted({x["category"] for x in catalog["plugins"]})
        rows = [x for x in catalog["plugins"] if (category == "all" or x["category"] == category) and all(word in " ".join((x["name"], x["description"], x["author"], " ".join(x["tags"]))).casefold() for word in query.split())]
        if sort == "updated":
            rows.sort(key=lambda x: (x["updated"], x["name"].casefold()), reverse=True)
        elif sort == "relevance" and query:
            rows.sort(key=lambda x: (0 if query in x["name"].casefold() else 1, x["name"].casefold(), x["id"]))
        else:
            rows.sort(key=lambda x: (x["name"].casefold(), x["id"]))
        result = {**status, "rows": [], "total": len(rows), "offset": offset, "limit": limit, "categories": categories, "truncated": False}
        for value in rows[offset:offset + limit]:
            projected = self._row(value, status["source"])
            if len(_json(result)) + len(_json(projected)) > REPLY_MAX:
                result.update(truncated=True, limit=len(result["rows"]))
                break
            result["rows"].append(projected)
        return result

    @_safe
    async def details(self, arg=None):
        arg = arg if isinstance(arg, dict) else {}
        pid = arg.get("id")
        if not isinstance(pid, str) or not ID.fullmatch(pid):
            _fail("bad_arg", "Choose a Decky catalog plugin")
        catalog, status = await self._catalog(arg)
        record = next((x for x in catalog["plugins"] if x["id"] == pid), None)
        if record is None:
            _fail("not_found", "This plugin is not in the selected Decky catalog")
        return {**status, "entry": self._row(record, status["source"], True)}

    async def _archive(self, url):
        if self.archive_fetcher is not None:
            return await self.archive_fetcher(url, ARCHIVE_MAX)
        return await fy_net.fetch_bytes("_core", url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30, max_bytes=ARCHIVE_MAX)

    async def _assess(self, arg):
        source = self.source(arg)
        catalog, status = await self._catalog(arg)
        if status["stale"] or status["origin"] == "unavailable":
            _fail("stale_catalog", "Refresh the Decky catalog before inspecting a plugin")
        record = next((x for x in catalog["plugins"] if x["id"] == arg.get("id")), None)
        if record is None:
            _fail("not_found", "Choose a plugin from the selected Decky catalog")
        if not hmac.compare_digest(arg["identity"], _identity(source, record)):
            _fail("stale_catalog", "The Decky listing changed; review it again")
        chosen = next((x for x in record["versions"] if hmac.compare_digest(arg["version_identity"], _identity(source, {"id": record["id"], "version": x}))), None)
        if chosen is None:
            _fail("stale_catalog", "The selected Decky version changed; choose it again")
        if not chosen["artifact"]:
            _fail("unverified", "The selected version has no supported public package link")
        data = await self._archive(chosen["artifact"])
        from fy_updates import _finish_transaction
        assessment = await _finish_transaction(asyncio.to_thread(inspect_archive, data, chosen["hash"], getattr(self.host, "PLATFORM", "linux-aarch64")))
        result = {"id": record["id"], "format": "decky", "read_only": True, "version": chosen["name"], "hash": chosen["hash"],
                  "source": source, "artifact": chosen["artifact"], "identity": arg["identity"], "version_identity": arg["version_identity"],
                  "checked_at": status["checked_at"], "downloaded_at": status["downloaded_at"], "assessment": assessment}
        if len(_json(result)) > REPLY_MAX:
            _fail("too_large", "The inspection result exceeds the display limit")
        return result

    @_safe
    async def assess(self, arg=None):
        arg = arg if isinstance(arg, dict) else {}
        if arg.get("confirm") is not True or not isinstance(arg.get("identity"), str) or not HEX.fullmatch(arg["identity"]) or not isinstance(arg.get("version_identity"), str) or not HEX.fullmatch(arg["version_identity"]):
            _fail("confirmation_required", "Choose and review a Decky plugin version before inspecting it")
        if self.inspection is not None and not self.inspection.done():
            _fail("busy", "A plugin compatibility check is already running")
        task = self.inspection = asyncio.create_task(self._assess(arg))
        def finished(done):
            if self.inspection is done:
                self.inspection = None
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), ASSESS_TIMEOUT)
        except asyncio.TimeoutError:
            task.cancel()
            _fail("timeout", "The compatibility check took too long; its result is unverified")
        except asyncio.CancelledError:
            task.cancel()
            raise
        except (OSError, ValueError, fy_net.NetError) as error:
            if isinstance(error, CatalogError):
                raise
            if isinstance(error, fy_net.NetError) and error.code == "too_large":
                _fail("too_large", "This package exceeds the 5 MB inspection limit; compatibility remains unverified.")
            _fail("unverified", "The package could not be inspected; compatibility remains unverified")
