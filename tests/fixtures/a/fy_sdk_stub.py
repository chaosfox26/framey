import types


def build(pid, meta, dirs, runtime, rpc, max_events=None):
    mod = types.ModuleType("framey_api")
    mod.PLUGIN = pid
    mod.API = 1
    mod.runtime = runtime
    mod.dirs = dirs
    mod.meta = meta
    mod.settings = None
    mod.log = types.SimpleNamespace(close=lambda: None)
    mod.events = types.SimpleNamespace(emit=rpc.emit)

    async def fetch(url, **kw):
        return await rpc.host("fetch", dict(kw, url=url))

    mod.fetch = fetch
    mod.rpc = rpc
    return mod
