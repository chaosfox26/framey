from __future__ import annotations

import json
import math
import re

LINE_MAX = 1048576
PAYLOAD_MAX = 786432
EVENT_MAX = 16384
EVENT_NAME = re.compile(r"[a-z0-9_.-]{1,40}\Z")


class ProtocolError(Exception):
    pass


def _reject_constant(name: str) -> None:
    raise ValueError(name)


def _parse_finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(value)
    return result


def loads(data: str | bytes | bytearray) -> object:
    value: object = json.loads(
        data, parse_constant=_reject_constant, parse_float=_parse_finite_float
    )
    return value


def dumps(obj: object) -> str:
    try:
        return json.dumps(obj, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exception:
        raise ProtocolError("not serialisable: %s" % type(exception).__name__)


def encode(obj: object) -> bytes:
    data = dumps(obj).encode("ascii") + b"\n"
    if len(data) > LINE_MAX:
        raise ProtocolError("line too large")
    return data


def decode(line: bytes) -> dict[str, object]:
    data = line.rstrip(b"\r\n")
    if len(data) > LINE_MAX:
        raise ProtocolError("line too large")
    try:
        message = loads(data)
    except (ValueError, RecursionError):
        raise ProtocolError("bad json")
    if not isinstance(message, dict):
        raise ProtocolError("not an object")
    return message


def error(code: str, message: object, **extra: object) -> dict[str, object]:
    result: dict[str, object] = {"code": code, "message": str(message)[:300]}
    for key, value in extra.items():
        result[key] = value[:600] if isinstance(value, str) else value
    return result


def event_ok(name: object, payload: object) -> bool:
    if not isinstance(name, str) or not EVENT_NAME.match(name):
        return False
    try:
        return len(dumps(payload)) <= EVENT_MAX
    except ProtocolError:
        return False
