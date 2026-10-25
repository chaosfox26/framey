import json
import math
import re

LINE_MAX = 1048576
PAYLOAD_MAX = 786432
EVENT_MAX = 16384
EVENT_NAME = re.compile(r"[a-z0-9_.-]{1,40}\Z")


class ProtocolError(Exception):
    pass


def _constant(name):
    raise ValueError(name)


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(value)
    return result


def loads(data):
    return json.loads(data, parse_constant=_constant, parse_float=_float)


def dumps(obj):
    try:
        return json.dumps(obj, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as e:
        raise ProtocolError("not serialisable: %s" % type(e).__name__)


def encode(obj):
    data = dumps(obj).encode("ascii") + b"\n"
    if len(data) > LINE_MAX:
        raise ProtocolError("line too large")
    return data


def decode(line):
    data = line.rstrip(b"\r\n")
    if len(data) > LINE_MAX:
        raise ProtocolError("line too large")
    try:
        obj = loads(data)
    except (ValueError, RecursionError):
        raise ProtocolError("bad json")
    if not isinstance(obj, dict):
        raise ProtocolError("not an object")
    return obj


def error(code, message, **extra):
    out = {"code": code, "message": str(message)[:300]}
    for key, value in extra.items():
        out[key] = value[:600] if isinstance(value, str) else value
    return out


def event_ok(name, payload):
    if not isinstance(name, str) or not EVENT_NAME.match(name):
        return False
    try:
        return len(dumps(payload)) <= EVENT_MAX
    except ProtocolError:
        return False
