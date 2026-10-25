import sys, os, json
cfg = json.load(open(os.environ["FAKE_SUDO_CFG"]))
arg = sys.argv[2] if len(sys.argv) > 2 else ""
with open(cfg["log"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ)}) + "\n")
b = cfg.get(arg, {})
if b.get("write") is not None:
    open(cfg["marker"], "w").write(b["write"])
sys.stdout.write(b.get("out", ""))
sys.exit(b.get("code", 0))
