import asyncio
import ipaddress
import re
import subprocess
import sys
import unicodedata
import urllib.parse

REPOSITORY = "https://github.com/chaosfox26/framey"
TITLE_MAX = 160
BODY_MAX = 10000
DIAGNOSTICS_MAX = 1200
URL_MAX = 7500
INPUT_MAX = 40000
DIAGNOSTIC_CODES = frozenset((
    "core.callback_failed", "core.cleanup_failed", "core.extension_failed",
    "core.storage_failed", "core.binding_failed", "core.transport_failed",
    "core.timer_failed", "core.event_failed", "core.visibility_failed",
    "core.panel_failed", "api.resource_failed", "api.locale_failed",
    "api.notification_failed", "page.callback_failed", "page.render_failed",
    "page.cleanup_failed", "page.navigation_failed", "page.focus_failed",
    "ui.callback_failed", "ui.pointer_capture_failed", "ui.pointer_release_failed",
    "picker.cleanup_failed", "picker.request_failed", "picker.focus_failed",
))
SOURCES = {
    "decky_stable": "Decky Store", "decky_testing": "Decky Store Testing",
    "decky": "Decky Store", "Decky Store": "Decky Store",
    "Decky Store Testing": "Decky Store Testing", "native": "Framey Store",
    "framey": "Framey Store", "Framey Store": "Framey Store",
    "stable": "Framey Store (Stable)", "testing": "Framey Store (Testing)",
    "custom": "Custom Framey catalog (address omitted)",
    "local": "Local Framey plugin", "Local Framey plugin": "Local Framey plugin",
}
SECRET = re.compile(r"(?im)\b[\w.-]{0,80}(?:token|secret|password|passwd|authorization|cookie|session(?:[_ -]?id)?|api[_ -]?key|serial(?:[_ -]?number)?|user(?:[_ -]?name)?|host(?:[_ -]?name)?|machine[_ -]?id|device[_ -]?id)[\w.-]{0,40}[\"']?\s*[:=]\s*[^\n]*")
QUERY = re.compile(r"(?i)([?&])(?:session(?:_id)?|state|code|auth|authorization|key|sig|signature|credential)=[^&#\s]*")
PRIVATE_KEY = re.compile(r"-----BEGIN[^\r\n]*PRIVATE KEY-----.*?(?:-----END[^\r\n]*PRIVATE KEY-----|\Z)", re.DOTALL)
AUTH = re.compile(r"(?i)\b(?:bearer|basic)\s+\S+")
TOKEN = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?|\b[A-Za-z0-9_-]{40,}\b")
URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{0,31}://[^\s<>\"']+")
WWW = re.compile(r"(?i)\bwww\.[^\s<>\"']+")
MARKDOWN_LINK = re.compile(r"!?\[([^\]\n]{0,300})\]\([^\)\n]*\)")
MAC = re.compile(r"(?i)(?<![\w])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![\w])")
EMAIL = re.compile(r"(?i)(?:[\w.!#$%&'*+/=?^`{|}~-]+)@[\w.-]+")
PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|(?<![\w])~/|(?<![\w])/)[^\s<>\"'`]*")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6 = re.compile(r"(?<![\w:])[0-9a-fA-F:]*:[0-9a-fA-F:]+(?:%[\w.-]+)?(?![\w:])")
VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]+)?\Z")
_opening = None
_launching = False


def clean(value, limit=300, multiline=False):
    if not isinstance(value, str):
        return ""
    def normalized(text):
        return "".join(c for c in unicodedata.normalize("NFKC", text) if c in "\n\t" or unicodedata.category(c)[0] != "C")

    text = normalized(value[:INPUT_MAX])
    for _ in range(12):
        decoded = normalized(urllib.parse.unquote(text))
        if decoded == text:
            break
        text = decoded
    else:
        return "[encoded text removed]"
    text = re.sub(r"(?:%[0-9a-fA-F]{2})+", "[encoded text removed]", text)
    text = PRIVATE_KEY.sub("[private key removed]", text)
    text = QUERY.sub(lambda match: match[1] + "[private parameter removed]", text)
    text = SECRET.sub("[private field removed]", text)
    text = AUTH.sub("[authentication removed]", text)
    text = TOKEN.sub("[token removed]", text)
    text = MARKDOWN_LINK.sub(lambda match: match[1] + " [link removed]", text)
    text = URL.sub("[link removed]", text)
    text = WWW.sub("[link removed]", text)
    text = EMAIL.sub("[email removed]", text)
    text = PATH.sub("[path removed]", text)
    text = IPV4.sub("[address removed]", text)
    text = MAC.sub("[address removed]", text)

    def address(match):
        try:
            ipaddress.IPv6Address(match.group(0))
        except ValueError:
            return match.group(0)
        return "[address removed]"

    text = IPV6.sub(address, text)
    text = text.replace("<", "(").replace(">", ")")
    return ("\n".join(line.strip() for line in text.splitlines()) if multiline else " ".join(text.split()))[:limit].strip()


def repository(host):
    value = getattr(host, "LOADER", "")
    return REPOSITORY if isinstance(value, str) and value.rstrip("/") == REPOSITORY else ""


def version(value):
    return value if isinstance(value, str) and len(value) <= 64 and VERSION.fullmatch(value) else "Unknown"


def diagnostic_excerpt(records):
    counts = {}
    for record in records[:32] if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        code, count = record.get("code"), record.get("count")
        if isinstance(code, str) and code in DIAGNOSTIC_CODES and type(count) is int and count > 0:
            counts[code] = min(9999, counts.get(code, 0) + count)
    if not counts:
        return ""
    lines = ["Session-wide callback counters; these are not attributed to this plugin."]
    lines.extend("%s: %d" % (code, counts[code]) for code in sorted(counts))
    return "\n".join(lines)[:DIAGNOSTICS_MAX]


def draft(host, arg):
    arg = arg if isinstance(arg, dict) else {}
    plugin = arg.get("plugin") if isinstance(arg.get("plugin"), dict) else {}
    name = clean(plugin.get("name"), 100) or ("Unknown plugin" if plugin else "Framey")
    plugin_version = clean(plugin.get("version"), 80) or "Unknown"
    source = plugin.get("source")
    source = SOURCES.get(source, "Unknown") if isinstance(source, str) else "Unknown"
    platform = getattr(host, "PLATFORM", "")
    platform = platform if platform in ("linux-aarch64", "linux-x86_64", "linux-armv7l", "linux-x86") else "Unknown"
    api = getattr(host, "API_GENERATION", None)
    api = str(api) if type(api) is int and 0 <= api <= 9999 else "Unknown"
    error = clean(arg.get("error"), 300) or "Describe the problem below."
    code = clean(arg.get("code"), 80)
    body = "\n".join((
        "## Context", "Plugin: " + name, "Plugin version: " + plugin_version,
        "Source: " + source, "Framey version: " + version(getattr(host, "VERSION", "")),
        "Framey API: " + api, "Platform: " + platform,
        "Framey Python runtime: %d.%d.%d" % sys.version_info[:3], "",
        "## Problem", error + (" (" + code + ")" if code else ""), "",
        "## Steps to reproduce", "1. ", "2. ", "",
        "## Expected behavior", "Describe what you expected.", "",
        "## Actual behavior", "Describe what happened.",
    ))
    if source.startswith("Decky"):
        body += "\n\nDecky store browsing/static inspection only; the original package was not installed or executed by Framey."
    repo = repository(host)
    return {"repository": repo, "title": clean("Framey: " + name + " problem", TITLE_MAX),
            "body": body, "diagnostics": diagnostic_excerpt(arg.get("diagnostics")),
            "notice": "Review and remove private details before sharing. No full logs are collected. Submit on GitHub only when you are ready.",
            "warning": "" if repo else "Framey's issue repository is unavailable. You can still copy your report."}


def link(host, arg):
    arg = arg if isinstance(arg, dict) else {}
    title = clean(arg.get("title"), TITLE_MAX) or "Framey problem"
    body = clean(arg.get("body"), BODY_MAX, multiline=True)
    diagnostics = clean(arg.get("diagnostics"), DIAGNOSTICS_MAX, multiline=True)
    if diagnostics:
        body += "\n\n## Optional diagnostics\n" + diagnostics
    repo = repository(host)
    url = repo + "/issues/new?" + urllib.parse.urlencode({"title": title, "body": body}) if repo else ""
    code = "repository_missing" if not repo else "too_long" if len(url.encode("ascii")) > URL_MAX else ""
    return {"repository": repo, "title": title, "body": body, "url": None if code else url,
            "code": code, "warning": "Framey's issue repository is unavailable. Copy the report to share later." if not repo else
            "The report is too long for a browser link. Copy it and paste it into a new Framey issue." if code else ""}


async def report_draft(host, arg=None):
    try:
        return draft(host, arg)
    except Exception:
        return {"error": "The report preview could not be prepared. No report was sent.", "code": "preview_unavailable"}


async def report_link(host, arg=None):
    try:
        return link(host, arg)
    except Exception:
        return {"error": "The report could not be prepared. No report was sent.", "code": "preview_unavailable"}


async def report_open(host, arg=None):
    global _opening, _launching
    result = await report_link(host, arg)
    if not result.get("url"):
        return dict(result, error=result.get("warning") or result.get("error") or "Copy the report to share later.")
    if _launching or _opening is not None and not _opening.done():
        return dict(result, error="A browser request is still pending. Use the existing draft or copy this report.", code="opening")
    _launching = True
    try:
        process = await asyncio.create_subprocess_exec("/usr/bin/xdg-open", result["url"],
                                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _opening = asyncio.create_task(process.wait())
        done, _ = await asyncio.wait((_opening,), timeout=3)
        if done and _opening.result() != 0:
            return dict(result, error="The browser could not be opened. Copy the report and open Framey's issue page yourself.", code="browser_unavailable")
        return dict(result, requested=True, notice="Browser request sent. Framey cannot confirm the page opened. Review and submit on GitHub; Framey has not created an issue.")
    except Exception:
        return dict(result, error="The browser could not be opened. Copy the report and open Framey's issue page yourself.", code="browser_unavailable")
    finally:
        _launching = False


METHODS = {"report_draft": report_draft, "report_link": report_link, "report_open": report_open}
