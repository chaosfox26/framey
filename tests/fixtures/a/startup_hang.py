import asyncio


async def startup(ctx):
    await asyncio.sleep(60)


def call(method, arg):
    return "unreachable"
