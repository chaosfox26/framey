import sys


def call(method, arg):
    if method == "asyncexit":
        return exiter()
    sys.exit(2)


async def exiter():
    raise SystemExit(3)
