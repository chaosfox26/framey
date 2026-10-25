import asyncio


async def call(method, arg):
    await asyncio.sleep(float(arg))
    return {"slept": arg}
