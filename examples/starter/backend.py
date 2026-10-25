import asyncio

import framey_api as fy

MODES = ("eco", "normal", "boost")
STEPS = 5
URL = "https://example.com/"
state = {"job": None}


def read():
    return {
        "enabled": fy.settings.get("enabled", True),
        "level": fy.settings.get("level", 50),
        "mode": fy.settings.get("mode", "normal"),
    }


async def work():
    try:
        for step in range(1, STEPS + 1):
            await asyncio.sleep(0.5)
            fy.events.emit("progress", {"step": step, "total": STEPS})
        fy.events.emit("done", {"steps": STEPS})
        fy.log.info("job finished")
    finally:
        state["job"] = None


def save(arg):
    level, mode, enabled = arg.get("level"), arg.get("mode"), arg.get("enabled")
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        return {"error": "level must be a whole number from 0 to 100"}
    if mode not in MODES:
        return {"error": "mode must be one of: " + ", ".join(MODES)}
    if not isinstance(enabled, bool):
        return {"error": "enabled must be true or false"}
    fy.settings.update({"level": level, "mode": mode, "enabled": enabled})
    fy.log.info("saved level=%s mode=%s enabled=%s", level, mode, enabled)
    return {"ok": True}


async def call(method, arg):
    arg = arg if isinstance(arg, dict) else {}
    if method == "get":
        return read()
    if method == "set":
        return save(arg)
    if method == "start":
        if state["job"] is not None:
            return {"error": "a job is already running"}
        state["job"] = asyncio.get_running_loop().create_task(work())
        return {"ok": True}
    if method == "fetch_demo":
        try:
            r = await fy.fetch(URL, max_bytes=65536)
        except fy.FrameyApiError as e:
            return {"error": e.message, "code": e.code}
        return {"status": r["status"], "bytes": r["bytes"]}
    return {"error": "unknown method: " + str(method)[:40]}


def startup(ctx):
    ctx.log.info("started %s %s on api %s", fy.PLUGIN, fy.runtime.version, fy.API)


def migrate(ctx):
    done = fy.migrate.keys({"lvl": "level"})
    ctx.log.info("migrated data %s -> %s, renamed %s", ctx.data_from, ctx.data_to, done["renamed"])


async def unload(reason):
    job, state["job"] = state["job"], None
    if job is not None:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    fy.log.info("unloaded (%s)", reason)


def uninstall(ctx):
    fy.log.info("uninstall, purge=%s", ctx.purge)
