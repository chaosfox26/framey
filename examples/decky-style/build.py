from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def bundle():
    helper = (ROOT / "compat" / "decky-source.js").read_text(encoding="utf-8")
    source = (HERE / "source.js").read_text(encoding="utf-8")
    return helper.rstrip() + "\n\n" + source


if __name__ == "__main__":
    (HERE / "main.js").write_text(bundle(), encoding="utf-8")
