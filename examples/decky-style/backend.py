"""A Framey backend for an independently authored Decky-pattern source port."""

import framey_api as fy

GOALS = ("Practice", "Read", "Plan")
MAX_COUNT = 1_000_000


def read_settings():
    goal = fy.settings.get("goal", GOALS[0])
    count = fy.settings.get("count", 0)
    return {
        "goal": goal if goal in GOALS else GOALS[0],
        "count": count if type(count) is int and 0 <= count <= MAX_COUNT else 0,
    }


async def call(method, args):
    # The source adapter sends a JSON array containing the positional arguments.
    if not isinstance(args, list):
        return {"error": "expected positional argument array"}
    if method == "read_settings" and not args:
        return read_settings()
    if method == "save_goal" and len(args) == 1:
        goal = args[0]
        if not isinstance(goal, str) or goal not in GOALS:
            return {"error": "goal must be Practice, Read or Plan"}
        fy.settings.set("goal", goal)
        return read_settings()
    if method == "check_in" and not args:
        snapshot = read_settings()
        if snapshot["count"] >= MAX_COUNT:
            return {"error": "check-in limit reached; reset the count to continue"}
        fy.settings.set("count", snapshot["count"] + 1)
        snapshot = read_settings()
        # Arrays are spread into positional handler arguments by the adapter.
        # Save first: the next read recovers even if this event is dropped.
        fy.events.emit("check_in", [snapshot])
        return snapshot
    if method == "reset_count" and not args:
        fy.settings.set("count", 0)
        return read_settings()
    return {"error": "unknown method or wrong number of arguments"}


def startup(ctx):
    ctx.log.info("session check-ins started")


def unload(reason):
    # This backend owns no tasks, threads, child processes or external resources.
    fy.log.info("session check-ins unloaded (%s)", reason)
