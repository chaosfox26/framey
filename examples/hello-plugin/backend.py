import asyncio
import json
import os
import pathlib

CONF = pathlib.Path.home() / ".config/hello/settings.json"


def load():
    try:
        data = json.loads(CONF.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def call(method, arg):
    if method == "greet":
        return {"text": "Hello, %s" % str(arg)[:40]}
    if method == "set":
        level = int(arg["level"])
        if not 0 <= level <= 100:
            return {"error": "level must be 0 to 100"}
        CONF.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONF.with_name(CONF.name + ".tmp")
        tmp.write_text(json.dumps({"level": level}))
        os.replace(tmp, CONF)
        return {"ok": True}
    if method == "slow":
        await asyncio.sleep(min(float(arg), 30))
        return {"text": "finished"}
    return load()


def unload():
    pass
