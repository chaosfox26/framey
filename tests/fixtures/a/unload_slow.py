import asyncio


async def unload(reason):
    await asyncio.sleep(60)


def call(method, arg):
    return "ok"
