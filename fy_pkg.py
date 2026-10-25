import hashlib
import hmac
import io
import json
import math
import os
import platform as _platform
import re
import shutil
import stat
import tempfile
import unicodedata
import urllib.parse
import zlib
from pathlib import Path

def _constant(name):
    raise ValueError(name)


def _loads(data):
    return json.loads(data, parse_constant=_constant)


API_GENERATION = 1
ARCHIVE_MAX = 20_000_000
ENTRIES = 500
ENTRY_MAX = 5_000_000
TOTAL_MAX = 20_000_000
DEPTH = 8
IDENTITY_MAX = 4096
CHUNK = 65536
HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MEMORY_MAX = 64
MEMORY_DEFAULT = 32
MACHINE = {"amd64": "x86_64", "arm64": "aarch64"}
PLATFORM = "linux-" + MACHINE.get(_platform.machine().lower(), _platform.machine().lower())
ID = re.compile(r"[a-z0-9_-]{1,32}")
PLAT = re.compile(r"linux-[a-z0-9_]{1,24}")
LOCALE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")
VER = re.compile(r"v?([0-9]{1,9}(?:\.[0-9]{1,9}){0,3})(?:-([0-9A-Za-z-]{1,32}(?:\.[0-9A-Za-z-]{1,32}){0,7}))?(?:\+[0-9A-Za-z.-]{1,32})?")
COMMIT = re.compile(r"[0-9a-f]{7,64}")
HEX64 = re.compile(r"[0-9a-fA-F]{64}")
DIGEST = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})")
ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
BADCHAR = re.compile(r"[\x00-\x1f\x7f:*?\"<>|]")
KINDS = ("release", "commit", "branch", "url", "local")
NEEDS = {"release": ("source", "tag", "commit"), "commit": ("source", "commit"), "branch": ("source", "tag"), "url": ("url",), "local": ()}
FIELDS = ("source", "tag", "commit", "digest", "url")


def replace_guard(dest):
    d = Path(dest)
    if d.is_symlink() and (Path(os.path.realpath(d)) / ".git").exists():
        raise PkgError("this linked folder is a git checkout; it is never replaced or deleted. Remove the link first", "linked_tree")


class PkgError(Exception):
    def __init__(self, message, code="pkg_error"):
        super().__init__(message[:300])
        self.message = message[:300]
        self.code = code


class Version(tuple):
    def __new__(cls, text, release, pre):
        self = super().__new__(cls, (release, 0 if pre else 1, pre))
        self.text = text
        return self

    @property
    def prerelease(self):
        return self[1] == 0

    def __str__(self):
        return self.text


def parse_version(s):
    m = VER.fullmatch(s) if isinstance(s, str) and len(s) <= 100 else None
    if not m:
        return None
    release = tuple(int(x) for x in m[1].split("."))
    release += (0,) * (4 - len(release))
    pre = tuple((0, int(x)) if x.isdecimal() and x.isascii() else (1, x) for x in m[2].split(".")) if m[2] else ()
    return Version(s[1:] if s[0] == "v" else s, release, pre)


def _int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _clean(s, n):
    return "".join(c for c in s if c.isprintable())[:n]


def _constraints(m):
    errs = []
    out = {"api": None, "min": None, "max": None, "platform": None}
    api = m.get("api")
    if api is not None:
        if _int(api, 1, 1000):
            out["api"] = api
        else:
            errs.append("api must be an integer from 1 to 1000")
    fw = m.get("framey")
    if fw is not None:
        if not isinstance(fw, dict):
            errs.append("framey must be an object")
        else:
            for k in ("min", "max"):
                if fw.get(k) is not None:
                    v = parse_version(fw[k])
                    if v is None:
                        errs.append("framey.%s is not a valid version" % k)
                    else:
                        out[k] = v
            if out["min"] is not None and out["max"] is not None and _over(out["min"], out["max"]):
                errs.append("framey.min is above framey.max")
    pl = m.get("platform")
    if pl is not None:
        if not isinstance(pl, list) or len(pl) > 8 or not all(isinstance(x, str) and PLAT.fullmatch(x) for x in pl):
            errs.append("platform must be a list of up to 8 names like linux-aarch64")
        elif pl:
            out["platform"] = list(dict.fromkeys(pl))
    return out, errs


def _over(lv, hi):
    n = len(hi.text.partition("-")[0].split("."))
    return lv[0][:n] > hi[0][:n] if n < 3 else lv > hi


def decide(m, loader_version, platform, api=API_GENERATION):
    if not isinstance(m, dict):
        return {"state": "incompatible", "badge": "unknown", "msg": "plugin.json is not valid", "reasons": ["plugin.json is not valid"]}
    c, errs = _constraints(m)
    if errs:
        return {"state": "incompatible", "badge": "unknown", "msg": "plugin.json compatibility fields are not valid", "reasons": errs}
    if not any(c.values()):
        return {"state": "unknown", "badge": "unknown", "msg": "Compatibility not declared", "reasons": []}
    lv = parse_version(loader_version)
    found = []
    if (c["min"] or c["max"]) and lv is None:
        found.append(("unknown", "the Framey version could not be read"))
    if c["min"] and lv is not None and lv < c["min"]:
        found.append(("newer", "needs Framey %s or newer; you have %s" % (c["min"], loader_version)))
    if c["max"] and lv is not None and _over(lv, c["max"]):
        found.append(("older", "supports Framey up to %s; you have %s" % (c["max"], loader_version)))
    if c["api"] and c["api"] > api:
        found.append(("api", "needs API generation %d; this Framey provides %d" % (c["api"], api)))
    if c["platform"] and platform not in c["platform"]:
        found.append(("platform", "built for %s; this headset is %s" % (" or ".join(c["platform"]), platform)))
    if not found:
        return {"state": "ok", "badge": "ok", "msg": "", "reasons": []}
    reasons = [r for _, r in found]
    msg = ". ".join(r[0].upper() + r[1:] for r in reasons)
    return {"state": "incompatible", "badge": found[0][0], "msg": msg, "reasons": reasons}


def _companion(raw):
    if not isinstance(raw, dict):
        return None, "companion must be an object"
    name, ver, plat, asset = raw.get("name"), parse_version(raw.get("version")), raw.get("platform"), raw.get("asset")
    sha, size, run = raw.get("sha256"), raw.get("size"), raw.get("exec", [])
    if not (isinstance(name, str) and ID.fullmatch(name)):
        return None, "companion name must match [a-z0-9_-]{1,32}"
    if ver is None or len(ver.text) > 40:
        return None, "companion %s has no valid version" % name
    if not (isinstance(plat, str) and PLAT.fullmatch(plat)):
        return None, "companion %s has no valid platform" % name
    if not (isinstance(asset, str) and ASSET.fullmatch(asset) and asset.endswith((".tar.gz", ".tgz", ".zip"))):
        return None, "companion %s asset must be a .tar.gz, .tgz or .zip file name" % name
    if not (isinstance(sha, str) and HEX64.fullmatch(sha)):
        return None, "companion %s sha256 must be 64 hex digits" % name
    if not _int(size, 1, ARCHIVE_MAX):
        return None, "companion %s size must be 1 to %d bytes" % (name, ARCHIVE_MAX)
    if not isinstance(run, list) or len(run) > 8:
        return None, "companion %s exec must be a list of up to 8 paths" % name
    names = []
    for e in run:
        try:
            parts = _parts(e)
        except PkgError:
            return None, "companion %s has an unsafe exec path" % name
        if not parts:
            return None, "companion %s has an empty exec path" % name
        rel = "/".join(parts)
        if rel not in names:
            names.append(rel)
    return {"name": name, "version": ver.text, "platform": plat, "asset": asset, "sha256": sha.lower(), "size": size, "exec": names}, ""


def companions(m, platform_id=None):
    raw = m.get("companions") if isinstance(m, dict) else None
    out, seen = [], set()
    for x in raw[:16] if isinstance(raw, list) else []:
        d, _ = _companion(x)
        if d and d["platform"] == (platform_id or PLATFORM) and d["name"] not in seen:
            seen.add(d["name"])
            out.append(d)
    return out


def _normalize(m):
    errs = []
    v = {}
    pid = m.get("id")
    if isinstance(pid, str) and ID.fullmatch(pid) and pid != "_core":
        v["id"] = pid
    else:
        errs.append("id must match [a-z0-9_-]{1,32}")
        v["id"] = ""
    for key, cap in (("name", 80), ("version", 40), ("short", 200), ("source", 300)):
        x = m.get(key)
        if x is None:
            v[key] = v["id"] if key == "name" else ""
        elif isinstance(x, str):
            v[key] = _clean(x, cap)
        else:
            errs.append("%s must be a string" % key)
            v[key] = v["id"] if key == "name" else ""
    c, cerrs = _constraints(m)
    errs += cerrs
    v["api"] = c["api"]
    v["framey"] = {"min": c["min"].text if c["min"] else None, "max": c["max"].text if c["max"] else None}
    v["platform"] = c["platform"]
    b = m.get("backend")
    if b is not None and not isinstance(b, dict):
        errs.append("backend must be an object")
    b = b if isinstance(b, dict) else {}

    def num(src, key, label, lo, hi, default, integer):
        x = src.get(key)
        if x is None:
            return default
        if isinstance(x, bool) or not isinstance(x, int if integer else (int, float)) or (isinstance(x, float) and not math.isfinite(x)):
            errs.append("%s must be a %s" % (label, "whole number" if integer else "number"))
            return default
        return min(max(x, lo), hi)

    src, label = (b, "backend.timeout") if "timeout" in b else (m, "timeout")
    timeout = num(src, "timeout", label, 1, 60, 10, False)
    mem = b.get("memory_mb")
    if isinstance(mem, (int, float)) and not isinstance(mem, bool) and mem > MEMORY_MAX:
        errs.append("backend.memory_mb must be %d or less" % MEMORY_MAX)
    memory = num(b, "memory_mb", "backend.memory_mb", 16, MEMORY_MAX, MEMORY_DEFAULT, True)
    keep = b.get("keepalive", False)
    if not isinstance(keep, bool):
        errs.append("backend.keepalive must be true or false")
        keep = False
    v["backend"] = {
        "timeout": timeout,
        "idle": num(b, "idle", "backend.idle", 10, 3600, 120, False),
        "keepalive": keep,
        "concurrency": num(b, "concurrency", "backend.concurrency", 1, 8, 1, True),
        "memory_mb": memory,
        "startup_timeout": num(b, "startup_timeout", "backend.startup_timeout", 1, 60, 10, False),
    }
    v["timeout"] = timeout
    dv = 0 if m.get("data_version") is None else m["data_version"]
    if _int(dv, 0, 1_000_000):
        v["data_version"] = dv
    else:
        errs.append("data_version must be a whole number from 0 to 1000000")
        v["data_version"] = 0
    i18n, lang = m.get("i18n"), "en"
    if i18n is not None:
        d = i18n.get("default", "en") if isinstance(i18n, dict) else None
        if isinstance(d, str) and LOCALE.fullmatch(d):
            lang = d
        else:
            errs.append("i18n.default must be a locale such as en")
    v["i18n"] = {"default": lang}
    raw = m.get("companions")
    v["companions"] = []
    if raw is not None and (not isinstance(raw, list) or len(raw) > 16):
        errs.append("companions must be a list of up to 16 entries")
    elif raw:
        seen = set()
        for x in raw:
            d, e = _companion(x)
            if d is None:
                errs.append(e)
            elif (d["name"], d["platform"]) in seen:
                errs.append("companion %s is declared twice for %s" % (d["name"], d["platform"]))
            else:
                seen.add((d["name"], d["platform"]))
                v["companions"].append(d)
    pv = m.get("privileged")
    v["privileged"] = None
    if pv is not None:
        ok = isinstance(pv, dict) and all(isinstance(x, str) and len(x) <= 300 and x.startswith("/") and x.isprintable() for x in pv.values())
        if ok:
            v["privileged"] = {k: x for k, x in pv.items() if k in ("installed", "helper")}
        else:
            errs.append("privileged must hold absolute paths")
    return {"ok": not errs, "errors": [e[:300] for e in errs[:20]], "value": v}


def normalize_manifest(m):
    if not isinstance(m, dict):
        return {"ok": False, "errors": ["manifest must be an object"], "value": {}}
    try:
        return _normalize(m)
    except Exception:
        return {"ok": False, "errors": ["manifest could not be read"], "value": {}}


def _text_field(x, n, pattern=None):
    if not (isinstance(x, str) and 0 < len(x) <= n and x.isprintable() and not any(c.isspace() for c in x)):
        return None
    return x if pattern is None or pattern.fullmatch(x) else None


def identity(kind, source=None, tag=None, commit=None, digest=None, url=None):
    if kind not in KINDS or not isinstance(kind, str):
        raise PkgError("unknown identity kind", "bad_identity")
    given = {"source": source, "tag": tag, "commit": commit, "digest": digest, "url": url}
    out = {"v": 1, "kind": kind}
    for key in FIELDS:
        x = given[key]
        if x is None or x == "":
            continue
        if key == "commit":
            x = _text_field(x, 64, COMMIT)
        elif key == "digest":
            m = DIGEST.fullmatch(x) if isinstance(x, str) else None
            x = "sha256:" + m[1].lower() if m else None
        elif key == "url":
            x = _text_field(x, 500)
            x = x if x and x.startswith("https://") else None
        else:
            x = _text_field(x, 300 if key == "source" else 100)
        if x is None:
            raise PkgError("identity field %s is not valid" % key, "bad_identity")
        out[key] = x
    missing = [k for k in NEEDS[kind] if k not in out]
    if missing:
        raise PkgError("identity of kind %s needs %s" % (kind, ", ".join(missing)), "bad_identity")
    return out


def read_identity(plugin_dir):
    p = Path(plugin_dir) / ".identity.json"
    try:
        if p.is_symlink() or not p.is_file():
            return None
        with open(p, "rb") as f:
            data = f.read(IDENTITY_MAX + 1)
        raw = _loads(data) if len(data) <= IDENTITY_MAX else None
        if not isinstance(raw, dict) or raw.get("v") != 1:
            return None
        return identity(raw.get("kind"), **{k: raw.get(k) for k in FIELDS})
    except (OSError, ValueError, PkgError, RecursionError):
        return None


def write_identity(stage_dir, ident):
    if not isinstance(ident, dict):
        raise PkgError("identity must be an object", "bad_identity")
    clean = identity(ident.get("kind"), **{k: ident.get(k) for k in FIELDS})
    data = json.dumps(clean, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(data) > IDENTITY_MAX:
        raise PkgError("identity too large", "bad_identity")
    stage = Path(stage_dir)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=str(stage), prefix=".identity.", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, stage / ".identity.json")
    except OSError as e:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise PkgError("could not write identity: " + (e.strerror or "i/o error"), "write_failed") from None


def _parts(name, tar=False):
    if not isinstance(name, str) or not name or len(name) > 1024 or "\\" in name or name.startswith("/"):
        raise PkgError("archive has an unsafe path", "unsafe_path")
    parts = name.split("/")
    while tar and parts and parts[0] == ".":
        parts.pop(0)
    if parts and parts[-1] == "":
        parts.pop()
    if len(parts) > DEPTH or any(p in ("", ".", "..") or len(p) > 255 or BADCHAR.search(p) or p[-1] in ". " for p in parts):
        raise PkgError("archive has an unsafe or too deep path", "unsafe_path")
    return parts


class _Budget:
    def __init__(self):
        self.count = self.total = 0

    def add(self, size):
        self.count += 1
        self.total += size
        if self.count > ENTRIES:
            raise PkgError("archive has more than %d entries" % ENTRIES, "too_many")
        if size > ENTRY_MAX or self.total > TOTAL_MAX:
            raise PkgError("archive is too large when unpacked", "too_large")


def _layout(items, strip, top):
    rows = []
    for parts, is_dir, size, ref in items:
        if not parts and is_dir:
            continue
        if top is not None:
            if not parts or parts[0] != top:
                raise PkgError("archive entry is outside the expected folder", "unsafe_path")
            parts = parts[1:]
        rows.append((parts, is_dir, size, ref))
    if strip == "auto":
        firsts = {p[0] for p, _, _, _ in rows if p}
        strip = 1 if len(firsts) == 1 and all(len(p) > 1 for p, d, _, _ in rows if not d) else 0
    elif not _int(strip, 0, 4):
        raise PkgError("strip must be auto or 0 to 4", "bad_value")
    files, dirs, out = {}, {}, []
    for parts, is_dir, size, ref in rows:
        parts = parts[strip:]
        if not parts:
            if is_dir:
                continue
            raise PkgError("archive file is above the stripped folder", "unsafe_path")
        key = tuple(unicodedata.normalize("NFC", p).casefold() for p in parts)
        prefixes = [(key[:i], tuple(parts[:i])) for i in range(1, len(key))]
        if any(k in files for k, _ in prefixes) or (is_dir and key in files):
            raise PkgError("archive uses a file as a folder", "duplicate")
        for k, raw in prefixes + ([(key, tuple(parts))] if is_dir else []):
            if dirs.setdefault(k, raw) != raw:
                raise PkgError("archive names collide when case is ignored", "case_collision")
        if not is_dir:
            if key in files:
                if files[key] == tuple(parts):
                    raise PkgError("archive has a duplicate name", "duplicate")
                raise PkgError("archive names collide when case is ignored", "case_collision")
            if key in dirs:
                raise PkgError("archive uses a folder as a file", "duplicate")
            files[key] = tuple(parts)
        out.append((parts, is_dir, size, ref))
    return out


def _save(dest, parts, src, declared, run, exec_names):
    root = os.path.realpath(dest)
    target = Path(root).joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    real = os.path.realpath(target.parent)
    if real != root and not real.startswith(root + os.sep):
        raise PkgError("archive path leaves the destination", "unsafe_path")
    size = 0
    with open(target, "xb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            run[0] += len(chunk)
            if size > ENTRY_MAX or run[0] > TOTAL_MAX:
                raise PkgError("archive is too large when unpacked", "too_large")
            out.write(chunk)
    if size != declared:
        raise PkgError("archive entry is damaged", "bad_archive")
    if "/".join(parts) in exec_names:
        os.chmod(target, 0o755)


class _Cap:
    def __init__(self, f, limit):
        self.f, self.left = f, limit

    def read(self, n=-1):
        b = self.f.read(CHUNK if n is None or n < 0 else min(n, self.left + 1))
        self.left -= len(b)
        if self.left < 0:
            raise PkgError("archive is too large when unpacked", "too_large")
        return b


def _open_tar(data):
    import gzip
    import tarfile
    return tarfile.open(fileobj=_Cap(gzip.GzipFile(fileobj=io.BytesIO(data)), TOTAL_MAX + 2_000_000), mode="r|")


def _need_bytes(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) > ARCHIVE_MAX:
        raise PkgError("archive is missing or larger than %d bytes" % ARCHIVE_MAX, "too_large")


def safe_extract_tar(data, dest, *, top=None, strip=0, exec_names=()):
    import tarfile
    _need_bytes(data)
    run_names = {x for x in exec_names if isinstance(x, str)}
    try:
        budget, items = _Budget(), []
        with _open_tar(bytes(data)) as tf:
            for i, m in enumerate(tf):
                budget.add(m.size)
                if not (m.isdir() or (m.isreg() and not m.issparse())):
                    raise PkgError("archive has a link, device or special entry", "unsafe_type")
                items.append((_parts(m.name, True), m.isdir(), m.size, i))
        plan = {ref: (parts, size) for parts, is_dir, size, ref in _layout(items, strip, top) if not is_dir}
        Path(dest).mkdir(parents=True, exist_ok=True)
        run, written = [0], []
        with _open_tar(bytes(data)) as tf:
            for i, m in enumerate(tf):
                if i in plan:
                    parts, size = plan[i]
                    _save(dest, parts, tf.extractfile(m), size, run, run_names)
                    written.append("/".join(parts))
    except (tarfile.TarError, EOFError, OSError, zlib.error, ValueError):
        raise PkgError("archive is damaged or could not be unpacked", "bad_archive") from None
    return sorted(written)


def _zip_end_ok(data):
    i = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if i < 0 or len(data) < i + 22:
        raise PkgError("not a zip archive", "bad_archive")
    if int.from_bytes(data[i + 10:i + 12], "little") > ENTRIES or int.from_bytes(data[i + 12:i + 16], "little") > 1_500_000:
        raise PkgError("archive has more than %d entries" % ENTRIES, "too_many")


def safe_extract_zip(data, dest, *, strip="auto", exec_names=()):
    import zipfile
    _need_bytes(data)
    _zip_end_ok(bytes(data))
    run_names = {x for x in exec_names if isinstance(x, str)}
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
            budget, items = _Budget(), []
            for info in z.infolist():
                budget.add(info.file_size)
                kind = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
                if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise PkgError("archive has an encrypted or unsupported entry", "unsafe_type")
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not info.is_dir()) or (kind == stat.S_IFREG and info.is_dir()):
                    raise PkgError("archive has a link, device or special entry", "unsafe_type")
                items.append((_parts(info.filename), info.is_dir(), info.file_size, info))
            Path(dest).mkdir(parents=True, exist_ok=True)
            run, written = [0], []
            for parts, is_dir, size, info in _layout(items, strip, None):
                if not is_dir:
                    with z.open(info) as src:
                        _save(dest, parts, src, size, run, run_names)
                    written.append("/".join(parts))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, zlib.error, ValueError, NotImplementedError):
        raise PkgError("archive is damaged or could not be unpacked", "bad_archive") from None
    return sorted(written)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path, sha256, size=None):
    m = DIGEST.fullmatch(sha256) if isinstance(sha256, str) else None
    try:
        if not m or (size is not None and os.path.getsize(path) != size):
            return False
        return hmac.compare_digest(sha256_file(path), m[1].lower())
    except OSError:
        return False


def url_allowed(url):
    try:
        u = urllib.parse.urlsplit(url)
        return u.scheme == "https" and u.hostname in HOSTS and u.port in (None, 443) and not u.username and not u.password
    except (ValueError, AttributeError):
        return False


def digest_agrees(sha256, api_digest):
    if not api_digest:
        return True
    m = DIGEST.fullmatch(api_digest) if isinstance(api_digest, str) and api_digest.startswith("sha256:") else None
    return bool(m) and isinstance(sha256, str) and hmac.compare_digest(m[1].lower(), sha256.lower())


def install_companion(archive_bytes, spec, dest_dir, *, platform_id=None):
    d, why = _companion(spec)
    if d is None:
        raise PkgError(why, "bad_spec")
    if d["platform"] != (platform_id or PLATFORM):
        raise PkgError("companion %s is built for %s; this headset is %s" % (d["name"], d["platform"], platform_id or PLATFORM), "platform")
    _need_bytes(archive_bytes)
    if len(archive_bytes) != d["size"]:
        raise PkgError("companion %s has the wrong size" % d["name"], "size")
    if not hmac.compare_digest(hashlib.sha256(archive_bytes).hexdigest(), d["sha256"]):
        raise PkgError("companion %s failed the integrity check" % d["name"], "digest")
    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = root / ("%s-%s" % (d["name"], d["version"]))
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(root)))
    try:
        extract = safe_extract_zip if d["asset"].endswith(".zip") else safe_extract_tar
        files = extract(archive_bytes, stage, strip="auto", exec_names=d["exec"])
        if any(e not in files for e in d["exec"]):
            raise PkgError("companion %s does not contain a declared executable" % d["name"], "missing_exec")
        old = None
        if os.path.lexists(final):
            old = root / (".old-" + stage.name[len(".stage-"):])
            os.replace(final, old)
        try:
            os.replace(stage, final)
        except OSError:
            if old:
                os.replace(old, final)
            raise
        if old:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"files": files, "exec": list(d["exec"])}
