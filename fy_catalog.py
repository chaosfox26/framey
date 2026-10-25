import asyncio
import base64
import hashlib
import inspect
import ipaddress
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.parse
import weakref
from pathlib import Path

import fy_net
import fy_pkg
import fy_store

CATALOG_MAX = 524288
PLUGIN_MAX = 200
CACHE_MAX = 1572864
CACHE_SOURCES = 2
TTL = 86400
RETRY = 900
DEFAULT_URL = "https://raw.githubusercontent.com/chaosfox26/framey/experimental/catalog/index.json"
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
REPO = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
ENTRY_KEYS = frozenset(("id", "name", "version", "description", "category", "tags", "license", "homepage", "channel", "updated", "kind", "platform", "api", "framey", "source", "artifact"))
SOURCES = {"stable": "Stable", "testing": "Testing", "custom": "Custom"}
_instances = weakref.WeakKeyDictionary()


class CatalogError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, str(message)[:300]
        super().__init__(self.message)


def _fail(code, message):
    raise CatalogError(code, message)


def _text(value, limit, field, empty=True):
    if not isinstance(value, str) or len(value) > limit or any(not c.isprintable() for c in value) or (not empty and not value.strip()):
        _fail("bad_catalog", "%s is not valid" % field)
    return value.strip()


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _fail("bad_catalog", "Catalog has duplicate JSON fields")
        out[key] = value
    return out


def _constant(_):
    _fail("bad_catalog", "Catalog contains a non-finite number")


def _float(value):
    out = float(value)
    if not math.isfinite(out):
        _constant(value)
    return out


def _decode(data, limit=CATALOG_MAX):
    if not isinstance(data, (bytes, str)):
        _fail("bad_catalog", "Catalog must be UTF-8 JSON")
    try:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        if len(raw) > limit:
            _fail("too_large", "Catalog is too large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
    except (UnicodeError, ValueError, RecursionError) as e:
        if isinstance(e, CatalogError):
            raise
        _fail("bad_catalog", "Catalog is not valid UTF-8 JSON")
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 32:
            _fail("bad_catalog", "Catalog is nested too deeply")
        if isinstance(item, dict):
            stack.extend((x, depth + 1) for x in item.values())
        elif isinstance(item, list):
            stack.extend((x, depth + 1) for x in item)
    return value


def validate_source_url(url):
    try:
        scheme, host, port, _ = fy_net._target(url, False)
        parsed = urllib.parse.urlsplit(url)
        if scheme != "https" or port != 443 or parsed.query or parsed.fragment:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not fy_net.is_public(address):
            raise ValueError
        if host.casefold() == "localhost" or host.casefold().endswith((".localhost", ".local")):
            raise ValueError
        authority = "[" + host.lower() + "]" if ":" in host else host.lower()
        return urllib.parse.urlunsplit(("https", authority, parsed.path or "/", "", ""))
    except (ValueError, fy_net.NetError):
        _fail("bad_source", "Use a public HTTPS catalog URL without credentials, query or fragment")


def _repository(value):
    value = _text(value, 240, "source.repository", False)
    match = REPO.fullmatch(value)
    if not match or match[1] in (".", "..") or match[2] in (".", "..") or match[2].endswith(".git"):
        _fail("bad_catalog", "Package source must be a canonical GitHub repository URL")
    return value


def _path(value):
    value = _text(value, 200, "source.path")
    if not value:
        return ""
    pieces = value.split("/")
    if len(pieces) > 6 or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}", x) or x in (".", "..") for x in pieces):
        _fail("bad_catalog", "Package subdirectory is not a safe relative path")
    return value


def _entry(raw):
    if not isinstance(raw, dict) or set(raw) - ENTRY_KEYS:
        _fail("bad_catalog", "Catalog entry has unknown or invalid fields")
    pid = raw.get("id")
    if not isinstance(pid, str) or not fy_pkg.ID.fullmatch(pid) or pid in ("_core", "_loader"):
        _fail("bad_catalog", "Catalog plugin ID is not valid")
    kind = raw.get("kind", "plugin")
    if kind not in ("plugin", "setup"):
        _fail("bad_catalog", "Catalog entry kind is not valid")
    source = raw.get("source")
    if not isinstance(source, dict) or set(source) - {"repository", "commit", "path"}:
        _fail("bad_catalog", "Package source is not valid")
    src = {"repository": _repository(source.get("repository")), "commit": "", "path": ""}
    if kind == "plugin":
        sha = source.get("commit")
        if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
            _fail("bad_catalog", "Package source requires an immutable full commit")
        src.update(commit=sha, path=_path(source.get("path", "")))
    elif source.get("commit") or source.get("path"):
        _fail("bad_catalog", "Setup listings cannot declare installable package sources")
    version = _text(raw.get("version", ""), 40, "version")
    if kind == "plugin" and fy_pkg.parse_version(version) is None:
        _fail("bad_catalog", "Package version is not valid")
    category = raw.get("category", "other")
    if not isinstance(category, str) or not SLUG.fullmatch(category):
        _fail("bad_catalog", "Catalog category is not valid")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 8 or not all(isinstance(x, str) and SLUG.fullmatch(x) for x in tags) or len(set(tags)) != len(tags):
        _fail("bad_catalog", "Catalog tags are not valid")
    channel = raw.get("channel", "stable")
    if channel not in ("stable", "testing"):
        _fail("bad_catalog", "Catalog channel is not valid")
    manifest = {k: raw[k] for k in ("id", "name", "version", "platform", "api", "framey") if k in raw}
    if isinstance(raw.get("framey"), dict) and set(raw["framey"]) - {"min", "max"}:
        _fail("bad_catalog", "Framey compatibility has unknown fields")
    checked = fy_pkg.normalize_manifest(manifest)
    if not checked["ok"]:
        _fail("bad_catalog", "Catalog compatibility fields are not valid")
    out = {"id": pid, "name": _text(raw.get("name"), 80, "name", False), "version": version,
           "description": _text(raw.get("description", ""), 600, "description"), "category": category,
           "tags": list(tags), "license": _text(raw.get("license", ""), 80, "license"),
           "homepage": _text(raw.get("homepage", src["repository"]), 400, "homepage"),
           "channel": channel, "updated": _text(raw.get("updated", ""), 40, "updated"),
           "kind": kind, "source": src}
    if out["homepage"]:
        out["homepage"] = validate_source_url(out["homepage"])
    for key in ("platform", "api", "framey"):
        if key in raw:
            out[key] = raw[key]
    artifact = raw.get("artifact")
    if artifact is not None:
        if kind != "plugin" or not isinstance(artifact, dict) or set(artifact) != {"sha256", "size"}:
            _fail("bad_catalog", "Package artifact metadata is not valid")
        sha, size = artifact["sha256"], artifact["size"]
        if not isinstance(sha, str) or not HEX.fullmatch(sha) or type(size) is not int or not 1 <= size <= fy_pkg.ARCHIVE_MAX:
            _fail("bad_catalog", "Package artifact hash or size is not valid")
        out["artifact"] = {"sha256": sha, "size": size}
    return out


def parse_catalog(data):
    raw = _decode(data)
    if not isinstance(raw, dict) or set(raw) - {"schema", "name", "plugins"} or type(raw.get("schema")) is not int or raw["schema"] != 1:
        _fail("bad_catalog", "Catalog schema is not supported")
    plugins = raw.get("plugins")
    if not isinstance(plugins, list) or len(plugins) > PLUGIN_MAX:
        _fail("bad_catalog", "Catalog must contain at most %d entries" % PLUGIN_MAX)
    out, seen = [], set()
    for row in plugins:
        item = _entry(row)
        if item["id"] in seen:
            _fail("bad_catalog", "Catalog contains duplicate plugin IDs")
        seen.add(item["id"])
        out.append(item)
    return {"schema": 1, "name": _text(raw.get("name", "Plugin catalog"), 80, "catalog name", False), "plugins": out}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _fingerprint(source, entry):
    return hashlib.sha256(_json({"source": source, "entry": entry}).encode()).hexdigest()


def _safe(fn):
    async def invoke(self, arg=None):
        try:
            return await fn(self, arg)
        except CatalogError as error:
            return {"error": error.message, "code": error.code}
    return invoke


def _receipt(path):
    marker = Path(path) / ".catalog.json"
    try:
        status = marker.lstat()
        if not stat.S_ISREG(status.st_mode) or status.st_size > 8192:
            return None
    except OSError:
        return None
    raw = fy_store.read_json(marker, None, 8192)
    if not isinstance(raw, dict) or raw.get("v") != 1 or not isinstance(raw.get("source"), dict):
        return None
    try:
        src = raw["source"]
        if not isinstance(raw.get("id"), str) or not fy_pkg.ID.fullmatch(raw["id"]) or not isinstance(src.get("commit"), str) or not COMMIT.fullmatch(src["commit"]):
            return None
        return {"id": raw["id"], "source": {"repository": _repository(src["repository"]), "commit": src["commit"], "path": _path(src.get("path", ""))}}
    except (CatalogError, KeyError, TypeError):
        return None


async def _await(value):
    return await value if inspect.isawaitable(value) else value


class Catalog:
    def __init__(self, host, fetcher=None, archive_fetcher=None):
        self.host, self.fetcher = host, fetcher
        self.archive_fetcher = archive_fetcher
        self.decky = None
        self.path = Path(getattr(host, "CONF", Path(host.HOME) / ".config/framey/settings.json")).parent / "catalog.json"
        self.state = None
        self.refreshes = {}
        self.install_lock = asyncio.Lock()
        self.load_lock = asyncio.Lock()
        self.save_lock = asyncio.Lock()

    def now(self):
        try:
            value = self.host.now()
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1e12 else time.time()
        except (AttributeError, TypeError, ValueError):
            return time.time()

    def developer(self):
        try:
            return self.host.settings().get("prefs", {}).get("dev", {}).get("enabled") is True
        except (AttributeError, TypeError):
            return False

    async def _load(self):
        if self.state is not None:
            return
        async with self.load_lock:
            if self.state is None:
                await self._load_state()

    async def _load_state(self):
        raw = await asyncio.to_thread(fy_store.read_json, self.path, {}, CACHE_MAX)
        self.state = {"v": 1, "source": "stable", "custom_url": "", "cache": {}}
        if not isinstance(raw, dict) or raw.get("v") != 1:
            return
        if raw.get("source") in SOURCES:
            self.state["source"] = raw["source"]
        try:
            if raw.get("custom_url"):
                self.state["custom_url"] = validate_source_url(raw["custom_url"])
        except CatalogError:
            pass
        now = self.now()
        for key, value in list(raw.get("cache", {}).items())[:CACHE_SOURCES] if isinstance(raw.get("cache"), dict) else []:
            if not isinstance(key, str) or not HEX.fullmatch(key) or not isinstance(value, dict):
                continue
            try:
                catalog = parse_catalog(_json(value["catalog"]))
                downloaded = value["downloaded_at"]
                if not isinstance(downloaded, (int, float)) or isinstance(downloaded, bool) or not 0 <= downloaded <= now + 60:
                    continue
                checked = value.get("checked_at", downloaded)
                checked = checked if type(checked) in (int, float) and 0 <= checked <= now + 60 else downloaded
                retry = value.get("retry_after", 0)
                retry = retry if type(retry) in (int, float) and 0 <= retry <= now + RETRY else 0
                self.state["cache"][key] = {"catalog": catalog, "downloaded_at": downloaded, "checked_at": checked,
                                            "error": "Previous catalog refresh failed" if value.get("error") else "", "retry_after": retry}
            except (KeyError, CatalogError, TypeError, ValueError):
                continue

    async def _save(self):
        async with self.save_lock:
            try:
                snapshot = json.loads(_json(self.state))
                await asyncio.to_thread(fy_store.write_json_atomic, self.path, snapshot, CACHE_MAX)
                return ""
            except (OSError, ValueError):
                return "Catalog settings could not be saved"

    def _selection(self, arg):
        source = arg.get("source", self.state["source"])
        if not isinstance(source, str) or source not in SOURCES:
            _fail("bad_source", "Unknown catalog source")
        if source == "testing" and not self.developer():
            _fail("dev_off", "Testing catalog requires developer mode")
        url = arg.get("url", self.state["custom_url"]) if source == "custom" else DEFAULT_URL
        if source == "custom" and not url:
            _fail("bad_source", "Enter a custom catalog URL")
        return {"id": source, "label": SOURCES[source], "url": validate_source_url(url)}

    @_safe
    async def sources(self, arg=None):
        await self._load()
        arg = arg if isinstance(arg, dict) else {}
        if arg:
            selection = self._selection(arg)
            old_source, old_url = self.state["source"], self.state["custom_url"]
            self.state["source"] = selection["id"]
            if selection["id"] == "custom":
                self.state["custom_url"] = selection["url"]
            error = await self._save()
            if error:
                self.state.update(source=old_source, custom_url=old_url)
                _fail("write_failed", error)
        effective = dict(arg)
        if not effective and self.state["source"] == "testing" and not self.developer():
            effective["source"] = "stable"
        selection = self._selection(effective)
        from fy_decky_catalog import SOURCES as decky_sources
        return {"source": selection, "sources": [{"id": k, "label": label, "url": self.state["custom_url"] if k == "custom" else DEFAULT_URL} for k, label in SOURCES.items()], "custom_url": self.state["custom_url"], "decky_sources": [dict(x) for x in decky_sources.values()]}

    def _decky(self):
        if self.decky is None:
            from fy_decky_catalog import DeckyCatalog
            self.decky = DeckyCatalog(self.host, self.fetcher, self.archive_fetcher)
        return self.decky

    async def _download(self, url):
        if self.fetcher is not None:
            return await _await(self.fetcher(url, CATALOG_MAX))
        reply = await fy_net.fetch_plugin("_core", url, headers={"Accept": "application/json", "User-Agent": "Framey"}, timeout=15, max_bytes=CATALOG_MAX, allow_http=False)
        if not isinstance(reply, dict) or reply.get("status") != 200 or reply.get("truncated"):
            _fail("download_failed", "The catalog server did not return a complete catalog")
        if isinstance(reply.get("text"), str):
            return reply["text"]
        try:
            return base64.b64decode(reply["b64"], validate=True)
        except (KeyError, TypeError, ValueError):
            _fail("download_failed", "The catalog server returned unreadable content")

    async def _refresh(self, source, key):
        now = self.now()
        old = self.state["cache"].get(key)
        try:
            catalog = parse_catalog(await self._download(source["url"]))
            record = {"catalog": catalog, "downloaded_at": now, "checked_at": now, "error": "", "retry_after": 0}
        except Exception as error:
            record = dict(old or {})
            record.update(checked_at=now, error=getattr(error, "message", None) or "Catalog could not be refreshed", retry_after=now + RETRY)
        self.state["cache"][key] = record
        while len(self.state["cache"]) > CACHE_SOURCES:
            victim = min((k for k in self.state["cache"] if k != key), key=lambda k: self.state["cache"][k].get("checked_at", 0))
            del self.state["cache"][victim]
        save_error = await self._save()
        if save_error and not record.get("error"):
            record["save_warning"] = save_error
        return record

    async def _catalog(self, arg):
        await self._load()
        if "source" not in arg and self.state["source"] == "testing" and not self.developer():
            arg = {**arg, "source": "stable"}
        source = self._selection(arg)
        key = hashlib.sha256(_json(source).encode()).hexdigest()
        record, now = self.state["cache"].get(key), self.now()
        refresh = arg.get("refresh") is True or not record or now - record.get("downloaded_at", 0) >= TTL or bool(record.get("error"))
        if refresh and (arg.get("refresh") is True or not record or now >= record.get("retry_after", 0)):
            task = self.refreshes.get(key)
            if task is None:
                task = self.refreshes[key] = asyncio.create_task(self._refresh(source, key))
                task.add_done_callback(lambda done, k=key: self.refreshes.pop(k, None) if self.refreshes.get(k) is done else None)
            try:
                record = await asyncio.shield(task)
            finally:
                if task.done() and self.refreshes.get(key) is task:
                    del self.refreshes[key]
        error = record.get("error", "") if record else ""
        catalog = record.get("catalog") if record else None
        if catalog is None:
            try:
                data = await asyncio.to_thread((Path(self.host.ROOT) / "catalog/index.json").read_bytes)
                catalog = parse_catalog(data)
            except (OSError, CatalogError):
                catalog = {"schema": 1, "name": "Plugin catalog", "plugins": []}
            origin, downloaded = "bundled", 0
        else:
            origin = "downloaded" if refresh and not error else "cache"
            downloaded = record.get("downloaded_at", 0)
        stale = origin == "bundled" or bool(error) or now - downloaded >= TTL
        warning = "Bundled catalog; remote source unavailable" if origin == "bundled" else "Saved catalog; refresh failed" if error else "Saved catalog is older than 24 hours" if stale else ""
        if record and record.get("save_warning"):
            warning = record["save_warning"]
        return catalog, {"source": source, "origin": origin, "stale": stale, "checked_at": record.get("checked_at", 0) if record else 0, "downloaded_at": downloaded, "error": None, "fetch_error": error or None, "warning": warning}

    def _row(self, entry, source):
        row = dict(entry)
        pid = entry["id"]
        dest = Path(self.host.PLUGINS) / pid
        installed, linked = os.path.lexists(dest), dest.is_symlink() or getattr(dest, "is_junction", lambda: False)()
        receipt = _receipt(dest) if installed and not linked else None
        managed = bool(receipt and receipt["id"] == pid and all(receipt["source"][k] == entry["source"][k] for k in ("repository", "path")))
        newer = bool(managed and receipt["source"].get("commit") != entry["source"].get("commit"))
        compatibility = fy_pkg.decide(entry, self.host.VERSION, self.host.PLATFORM, getattr(self.host, "API_GENERATION", 1))
        existing = fy_store.read_json(dest / "plugin.json", {}, 65536) if installed and not linked else {}
        current = fy_pkg.parse_version(existing.get("version")) if isinstance(existing, dict) else None
        candidate = fy_pkg.parse_version(entry.get("version"))
        state = self.host.settings() if hasattr(self.host, "settings") else {}
        modules = state.get("modules", {}) if isinstance(state, dict) else {}
        frozen = modules.get("frozen", {}) if isinstance(modules, dict) else {}
        reason = ""
        if entry["kind"] == "setup" or pid == "fan" or entry["source"]["repository"].casefold() == "https://github.com/chaosfox26/frame-fan":
            reason = "Requires Framey App signed privileged setup"
        elif compatibility["state"] == "incompatible":
            reason = compatibility["msg"]
        elif linked:
            reason = "This module is linked; manage it from Modules"
        elif installed and (not isinstance(existing, dict) or "privileged" in existing):
            reason = "Use the module's protected update action"
        elif installed and isinstance(frozen, dict) and pid in frozen:
            reason = "Unfreeze this module from Modules before updating"
        elif installed and current is not None and candidate is not None and candidate < current:
            reason = "A newer version is installed"
        elif installed and (os.path.lexists(dest.with_name(".old-" + pid)) or getattr(self.host, "pending", lambda _: None)(pid)):
            reason = "Keep or restore the previous version from Modules first"
        elif installed and not newer:
            reason = "Already installed; manage it from Modules"
        row.update(identity=_fingerprint(source, entry), compat=compatibility, installed=installed, linked=linked,
                   eligible=not reason, reason=reason, catalog_managed=managed, update_available=newer,
                   platform_declared=bool(entry.get("platform")))
        known_platform = bool(entry.get("platform"))
        status = "incompatible" if compatibility["state"] == "incompatible" else "compatible" if compatibility["state"] == "ok" and known_platform else "unverified"
        explanations = []
        for message in compatibility["reasons"]:
            if message.startswith("built for "):
                explanations.append("Built for a different processor; this headset needs an ARM64 build." if self.host.PLATFORM == "linux-aarch64" else "Built for a different processor than this device uses.")
            elif message.startswith("needs API generation "):
                explanations.append("Needs a newer Framey plugin interface than this version provides.")
            elif message.startswith("needs Framey "):
                explanations.append("Needs a newer version of Framey.")
            elif message.startswith("supports Framey up to "):
                explanations.append("Supports an older version of Framey.")
            else:
                explanations.append("The plugin's compatibility requirements could not be verified.")
        plain = " ".join(explanations) if status == "incompatible" else "Declared requirements match this Framey version and headset architecture. VR behavior has not been verified." if status == "compatible" else "The plugin does not declare enough requirements to verify support for this headset."
        row["compatibility"] = {"status": status, "label": "Requirements match" if status == "compatible" else "Not compatible" if status == "incompatible" else "Unverified",
                                "reason": plain, "reasons": explanations or [plain], "basis": "declared_requirements", "runtime_verified": False}
        return row

    @_safe
    async def list(self, arg=None):
        arg = arg if isinstance(arg, dict) else {}
        if arg.get("source") in ("decky_stable", "decky_testing"):
            return await self._decky().list(arg)
        query = _text(arg.get("query", ""), 120, "search query").casefold()
        category = arg.get("category", "all")
        sort = arg.get("sort", "name")
        if not isinstance(category, str) or (category != "all" and not SLUG.fullmatch(category)) or sort not in ("name", "updated", "relevance"):
            _fail("bad_arg", "Catalog filter or sorting is not valid")
        offset, limit = arg.get("offset", 0), arg.get("limit", 50)
        if type(offset) is not int or not 0 <= offset <= PLUGIN_MAX or type(limit) is not int or not 1 <= limit <= 100:
            _fail("bad_arg", "Catalog page is not valid")
        catalog, status = await self._catalog(arg)
        entries = [x for x in catalog["plugins"] if status["source"]["id"] == "testing" or (x["channel"] != "testing" and not (fy_pkg.parse_version(x["version"]) and fy_pkg.parse_version(x["version"]).prerelease))]
        categories = sorted({x["category"] for x in entries})
        entries = [x for x in entries if (category == "all" or x["category"] == category) and all(word in " ".join((x["name"], x["description"], x["id"], " ".join(x["tags"]))).casefold() for word in query.split())]
        if sort == "updated":
            entries.sort(key=lambda x: (x["updated"], x["name"].casefold()), reverse=True)
        elif sort == "relevance" and query:
            entries.sort(key=lambda x: (0 if query in x["name"].casefold() else 1, x["name"].casefold(), x["id"]))
        else:
            entries.sort(key=lambda x: (x["name"].casefold(), x["id"]))
        return {**status, "rows": [self._row(x, status["source"]) for x in entries[offset:offset + limit]], "total": len(entries), "offset": offset, "limit": limit, "categories": categories}

    @_safe
    async def details(self, arg):
        arg = arg if isinstance(arg, dict) else {}
        if arg.get("source") in ("decky_stable", "decky_testing"):
            return await self._decky().details(arg)
        pid = arg.get("id")
        if not isinstance(pid, str) or not fy_pkg.ID.fullmatch(pid):
            _fail("bad_arg", "Choose a catalog module")
        result = await self.list({k: v for k, v in arg.items() if k in ("source", "url", "refresh")} | {"limit": 100})
        if "rows" not in result:
            return result
        entry = next((x for x in result["rows"] if x["id"] == pid), None)
        if entry is None and result["total"] > 100:
            second = await self.list({k: v for k, v in arg.items() if k in ("source", "url")} | {"offset": 100, "limit": 100})
            entry = next((x for x in second["rows"] if x["id"] == pid), None)
        if entry is None:
            _fail("not_found", "Module is not in this catalog")
        return {k: result[k] for k in ("source", "origin", "stale", "checked_at", "downloaded_at", "error", "fetch_error", "warning")} | {"entry": entry}

    @_safe
    async def install(self, arg):
        arg = arg if isinstance(arg, dict) else {}
        if arg.get("source") in ("decky_stable", "decky_testing") or arg.get("format") == "decky":
            _fail("decky_port_required", "Built for Decky’s interface; it needs a Framey version before it can run here.")
        if arg.get("confirm") is not True or not isinstance(arg.get("identity"), str) or not HEX.fullmatch(arg["identity"]):
            _fail("confirmation_required", "Review the catalog module before installing it")
        async with self.install_lock:
            result = await self.details(arg)
            if "entry" not in result:
                return result
            row = result["entry"]
            if not __import__("hmac").compare_digest(arg["identity"], row["identity"]):
                _fail("stale_catalog", "Catalog entry changed; review it again before installing")
            if not row["eligible"]:
                _fail("not_eligible", row["reason"])
            if result["stale"] or result["origin"] == "bundled":
                _fail("stale_catalog", "Refresh the catalog before installing this module")
            from fy_updates import _finish_transaction
            return await _finish_transaction(self._install(row))

    @_safe
    async def assess(self, arg=None):
        arg = arg if isinstance(arg, dict) else {}
        if arg.get("source") not in ("decky_stable", "decky_testing"):
            _fail("bad_source", "Choose a plugin in Decky Store to inspect")
        return await self._decky().assess(arg)

    async def _install(self, row):
        source, pid = row["source"], row["id"]
        owner, repo = REPO.fullmatch(source["repository"]).groups()
        data = await asyncio.to_thread(self.host.archive, owner, repo, source["commit"])
        if not isinstance(data, bytes) or len(data) > fy_pkg.ARCHIVE_MAX:
            _fail("bad_archive", "Package archive is not valid or is too large")
        artifact = row.get("artifact")
        if artifact and (len(data) != artifact["size"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]):
            _fail("digest_mismatch", "Package does not match the catalog hash and size")
        root = Path(self.host.ROOT)
        temp_parent = root.parent if root.parent.is_dir() else None
        with tempfile.TemporaryDirectory(prefix=".framey-catalog-", dir=temp_parent) as tmp:
            extracted = Path(tmp) / "tree"
            await asyncio.to_thread(fy_pkg.safe_extract_zip, data, extracted, preserve_exec=True)
            package = extracted.joinpath(*source["path"].split("/")) if source["path"] else extracted
            if not package.is_dir() or package.is_symlink():
                _fail("bad_package", "The selected package directory is missing")
            try:
                raw = await asyncio.to_thread((package / "plugin.json").read_bytes)
                meta = _decode(raw, 65536)
            except OSError:
                _fail("bad_package", "The selected package has no plugin manifest")
            if not isinstance(meta, dict) or meta.get("id") != pid or meta.get("version") != row["version"]:
                _fail("identity_mismatch", "Package identity or version does not match the reviewed catalog entry")
            normalized = fy_pkg.normalize_manifest(meta)
            if not normalized["ok"]:
                _fail("bad_package", "The package manifest is not valid")
            if "privileged" in meta or pid == "fan":
                _fail("protected", "Use Framey App for protected components")
            compatibility = fy_pkg.decide(meta, self.host.VERSION, self.host.PLATFORM, getattr(self.host, "API_GENERATION", 1))
            if compatibility["state"] == "incompatible":
                _fail("incompatible", compatibility["msg"])
            if not (package / "main.js").is_file() and not (package / "backend.py").is_file():
                _fail("bad_package", "The package has no frontend or backend")
            if source["path"]:
                for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE", "NOTICE.md", "THIRD_PARTY_NOTICES"):
                    notice = extracted / name
                    if notice.is_file() and not (package / name).exists():
                        shutil.copy2(notice, package / name)
            dest = Path(self.host.PLUGINS) / pid
            before = self._row({k: v for k, v in row.items() if k in ENTRY_KEYS}, {"id": "stable", "label": "Stable", "url": DEFAULT_URL})
            if not before["eligible"]:
                _fail("not_eligible", before["reason"])
            old = dest.with_name(".old-" + pid)
            if os.path.lexists(old) or (getattr(self.host, "pending", lambda _: None)(pid)):
                _fail("pending", "Keep or restore the previous version before installing another")
            if dest.exists():
                existing = fy_store.read_json(dest / "plugin.json", {}, 65536)
                if not isinstance(existing, dict) or "privileged" in existing:
                    _fail("protected", "Use the module's protected update action")
            was_installed = dest.exists()
            for name in (".source", ".version", ".identity.json", ".catalog.json"):
                (package / name).unlink(missing_ok=True)
            canonical = source["repository"] + "/tree/" + source["commit"]
            meta["source"] = canonical
            if source["path"]:
                meta["source_path"] = source["path"]
            else:
                meta.pop("source_path", None)
            (package / "plugin.json").write_text(_json(meta) + "\n")
            (package / ".source").write_text(canonical + "\n")
            (package / ".version").write_text(source["commit"] + "\n")
            fy_pkg.write_identity(package, fy_pkg.identity("commit", source=source["repository"], commit=source["commit"], digest=artifact["sha256"] if artifact else None))
            fy_store.write_json_atomic(package / ".catalog.json", {"v": 1, "id": pid, "source": source, "identity": row["identity"]}, 8192)
            await asyncio.to_thread(self.host.stage_swap, pid, lambda stage: shutil.copytree(package, stage), keep=True)
            try:
                await _await(self.host.reload(pid))
                check = getattr(self.host, "backend_check", None)
                failure = await _await(check(pid)) if check else ""
                if failure:
                    raise CatalogError("load_failed", failure)
            except Exception:
                if was_installed:
                    await asyncio.to_thread(self.host.restore, dest)
                else:
                    await asyncio.to_thread(shutil.rmtree, dest)
                await _await(self.host.reload(pid))
                raise
        return {"ok": pid, "version": row["version"], "commit": source["commit"], "updated": was_installed}


def _service(host):
    service = _instances.get(host)
    if service is None:
        service = _instances[host] = Catalog(host)
    return service


def _method(name):
    async def invoke(host, arg):
        try:
            return await getattr(_service(host), name)(arg)
        except CatalogError as error:
            return {"error": error.message, "code": error.code}
        except Exception:
            return {"error": "Catalog operation could not be completed", "code": "catalog_failed"}
    return invoke


METHODS = {"catalog_list": _method("list"), "catalog_details": _method("details"), "catalog_install": _method("install"), "catalog_sources": _method("sources"), "catalog_assess": _method("assess")}
