#!/usr/bin/env python3
"""`progress.skip` reaches the progress worker as TAUCETI_PROGRESS_SKIP, offline:
  * a list is joined with commas, a string passes through;
  * with no `skip`, the variable is absent rather than empty.
Run: python3 tests/progress_skip.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="progress-skip-"))
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def load(name: str, progress: str):
    home = TMP / name
    home.mkdir()
    (home / "fleet.toml").write_text(f'[fleet]\nname = "tst"\ngithub_login = "acct"\ntargets = "none"\n'
                                     f'[progress]\nenabled = true\nstrategy = "threshold"\n{progress}')
    os.environ["TAUCETI_FLEET_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "bin" / "tauceti-aim"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


env = load("listed", 'skip = ["NewtonPolygons", "RestrictedPowerSeries"]\n').progress_env()
check("a list is joined with commas", env.get("TAUCETI_PROGRESS_SKIP") == "NewtonPolygons,RestrictedPowerSeries", env)
env = load("string", 'skip = "NewtonPolygons"\n').progress_env()
check("a string passes through", env.get("TAUCETI_PROGRESS_SKIP") == "NewtonPolygons", env)
env = load("none", "").progress_env()
check("no skip, no variable", "TAUCETI_PROGRESS_SKIP" not in env, env)

sys.exit(1 if fails else 0)
