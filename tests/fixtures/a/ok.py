import os
import sys


def note(text):
    with open("hooks.log", "a") as f:
        f.write(text + "\n")


def startup(ctx):
    note("startup %s %s" % (ctx.plugin, sorted(vars(ctx))))


def migrate(ctx):
    note("migrate %s %s %s %s" % (ctx.from_version, ctx.to_version, ctx.data_from, ctx.data_to))


def uninstall(ctx):
    note("uninstall purge=%s" % ctx.purge)


def unload(reason):
    note("unload %s" % reason)


def status_field(name):
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(name + ":"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return None


def call(method, arg):
    if method == "echo":
        return {"echo": arg}
    if method == "pid":
        return os.getpid()
    if method == "env":
        return dict(os.environ)
    if method == "cwd":
        return os.getcwd()
    if method == "nnp":
        return status_field("NoNewPrivs")
    if method == "limits":
        import resource
        return {n: resource.getrlimit(getattr(resource, n)) for n in ("RLIMIT_CORE", "RLIMIT_NOFILE", "RLIMIT_FSIZE", "RLIMIT_DATA")}
    if method == "nice":
        return os.nice(0)
    if method == "hog":
        return len(bytearray(int(arg)))
    if method == "raise":
        raise ValueError("bad thing")
    if method == "stdin":
        return sys.stdin.read()
    if method == "sysmods":
        return sorted(m for m in ("framey", "framey_api", "fy_proto") if m in sys.modules)
    return {"method": method}
