import asyncio
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import jharness as H

FAN = pathlib.Path(os.environ.get("FRAMEY_FAN_MAIN", "D:/Test/frame-fan/main.js"))

BETA = """
window.__betaApi = Framey.register({ render(box) { box.textContent = "beta"; }, dispose() { window.__betaGone = (window.__betaGone || 0) + 1; } });
window.__betaApi.events.on("ping", p => { window.__ping = p; });
Framey.call("fan", "get", {});
"""

EVIL = "}); window.__leak = this; (function(){"

SNOOP = "window.__snoop = Array.prototype.slice.call(arguments.callee.caller.arguments);"

L = H.Loader()
L.plugin("fan", {"name": "Fan", "short": "Fan"}, main=FAN.read_text(encoding="utf8"))
L.plugin("beta", {"name": "Beta", "short": "Beta"}, main=BETA)
L.plugin("evil", {"name": "Evil", "short": "Evil"}, main=EVIL)
L.plugin("snoop", {"name": "Snoop", "short": "Snoop"}, main=SNOOP)
fy = L.fy
main = []
for expr, pid in fy.main_code():
    if isinstance(expr, tuple):
        m, tok, src = expr
        main.append({"id": pid, "tok": tok, "meta": m, "src": src})
    else:
        main.append({"id": pid, "expr": expr})
bar = [{"id": pid, "expr": expr} for expr, pid in fy.bar_code()]
calls = []


async def fake(method, **kw):
    calls.append({"method": method, **kw})


fake.core = "placeholder"
fy.LIVE["main"] = fake
asyncio.run(fy.notify_page("_core", "update_result", {"ok": True, "name": "Fan"}))
asyncio.run(fy.notify_page("beta", "ping", {"n": 7}))
out = {
    "core": fy.CORE_TOK,
    "tokens": {pid: tok for tok, pid in fy.TOKS.items()},
    "consts": {"BIND": fy.BIND, "RUN": fy.RUN, "EVENT": fy.EVENT, "REPLY": fy.REPLY, "DISPOSE": fy.DISPOSE, "WIPE": fy.WIPE},
    "main": main,
    "bar": bar,
    "events": calls,
}
sys.stdout.write(json.dumps(out))
