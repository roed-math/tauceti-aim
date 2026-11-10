#!/usr/bin/env python3
"""`authoring.lookahead` reaches the workers as TAUCETI_LOOKAHEAD, offline:
  * true → 1 (sessions and ports), "ports" → ports (no session), false → 0 (passive);
  * absent → no variable at all;
  * the live view's lookahead row names the mode.
Run: python3 tests/lookahead_mode.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lookahead-mode-"))
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def load(name: str, authoring: str):
    home = TMP / name
    home.mkdir()
    (home / "fleet.toml").write_text(f'[fleet]\nname = "tst"\ngithub_login = "acct"\ntargets = "none"\n'
                                     f'[authoring]\n{authoring}')
    os.environ["TAUCETI_FLEET_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "bin" / "tauceti-aim"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


for name, line, env, row in (("on", "lookahead = true\n", {"TAUCETI_LOOKAHEAD": "1"}, "on"),
                             ("ports", 'lookahead = "ports"\n', {"TAUCETI_LOOKAHEAD": "ports"}, "ports only"),
                             ("passive", "lookahead = false\n", {"TAUCETI_LOOKAHEAD": "0"}, "passive")):
    mod = load(name, line)
    check(f"{name}: the workers get {env}", mod.LOOKAHEAD_ENV == env, mod.LOOKAHEAD_ENV)
    shown = mod.lookahead_line() or ""
    check(f"{name}: the live view says {row!r}", shown.startswith(row + " ·"), shown)
mod = load("absent", "")
check("absent: no variable at all", mod.LOOKAHEAD_ENV == {}, mod.LOOKAHEAD_ENV)
check("absent and nothing recorded: no row", mod.lookahead_line() is None)

sys.exit(1 if fails else 0)
