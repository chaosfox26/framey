def startup(ctx):
    raise RuntimeError("startup boom")


def call(method, arg):
    return "unreachable"
