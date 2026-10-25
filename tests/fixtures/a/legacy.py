def note(text):
    with open("hooks.log", "a") as f:
        f.write(text + "\n")


def startup():
    note("startup noarg")


def unload():
    note("unload noarg")


async def call(method, arg):
    return {"legacy": method}
