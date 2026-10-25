import framey_api


def call(method, arg):
    if method == "bad":
        return [framey_api.events.emit("Bad Name", 1), framey_api.events.emit("ok", "x" * 20000), framey_api.events.emit("fine", {"a": 1})]
    return sum(1 for i in range(int(arg)) if framey_api.events.emit("tick", {"i": i}))
