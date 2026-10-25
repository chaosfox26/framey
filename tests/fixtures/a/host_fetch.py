import asyncio

import framey_api


async def one(i):
    try:
        return await framey_api.rpc.host("fetch", {"i": i})
    except Exception as e:
        return "err:" + e.code


async def call(method, arg):
    if method == "many":
        return await asyncio.gather(*(one(i) for i in range(6)))
    if method == "raw":
        try:
            return await framey_api.rpc.host(arg, {})
        except Exception as e:
            return {"failed": e.code}
    try:
        return await framey_api.fetch(arg)
    except Exception as e:
        return {"failed": getattr(e, "code", "?"), "message": str(e)}
