import asyncio

import framey_api as fy


def read_level():
    level = fy.settings.get("level", 50)
    return level if type(level) is int and 0 <= level <= 100 else 50


async def call(method, arg):
    if method == "get":
        return {"level": read_level()}
    if method == "greet":
        if not isinstance(arg, str):
            return {"error": "name must be text"}
        return {"text": "Hello, " + arg[:40]}
    if method == "set":
        level = arg.get("level") if isinstance(arg, dict) else None
        if type(level) is not int or not 0 <= level <= 100:
            return {"error": "level must be a whole number from 0 to 100"}
        fy.settings.set("level", level)
        return {"ok": True}
    if method == "slow":
        if type(arg) not in (int, float) or not 0 <= arg <= 30:
            return {"error": "delay must be a number from 0 to 30"}
        await asyncio.sleep(arg)
        return {"text": "finished"}
    return {"error": "unknown method"}
