#!/usr/bin/env python3
"""`up.max_load`, offline:
  * every worker gets TAUCETI_MAX_LOAD and the slot directory only when the setting asks, with the fleet's
    own directory unless `up.load_gate_dir` names a shared one; `up.load_spacing` is passed when set;
  * the view says how many enabled workers wait, and says so when this user cannot write the slot directory.
Run: python3 tests/load_gate.py. Exit 0 = all hold."""

import argparse
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="load-gate-"))
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


store_mod = types.ModuleType("tauceti_worker.mathlib_store")
sys.modules.setdefault("tauceti_worker", types.ModuleType("tauceti_worker"))
sys.modules["tauceti_worker.mathlib_store"] = store_mod
sys.modules["tauceti_worker"].mathlib_store = store_mod


def load(name: str, up: str):
    home = TMP / name
    home.mkdir()
    (home / "fleet.toml").write_text(f'[fleet]\nname = "tlg"\ngithub_login = "acct"\ntargets = "none"\n[up]\n{up}\n')
    os.environ["TAUCETI_FLEET_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "bin" / "tauceti-aim"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


shape = argparse.Namespace(claude=1, codex=0, fixers=1, reviewers=1, sandbox="bubble", pace="", pilot=False,
                           claude_model="m", claude_effort="high", codex_model="", codex_effort="",
                           mutations_per_hour=10, reads_per_hour=100)


def envs(mod):
    return [s["env"] for s in mod.fleet_specs(shape)]


off = load("off", "")
check("no setting: no worker gets the gate", not any("TAUCETI_MAX_LOAD" in e or "TAUCETI_LOAD_GATE_DIR" in e for e in envs(off)))
check("...and the view says there is no limit", "no limit" in off.load_line({}, []))

local = load("local", "max_load = 64")
check("every worker gets the limit", {e.get("TAUCETI_MAX_LOAD") for e in envs(local)} == {"64"})
check("...and the fleet's own slot directory", {e.get("TAUCETI_LOAD_GATE_DIR") for e in envs(local)} == {str(local.FLEET_HOME / "load-gate")})
check("...and the worker's own spacing", not any("TAUCETI_LOAD_SPACING" in e for e in envs(local)))

shared_dir = TMP / "shared-slots"
shared = load("shared", f'max_load = 64\nload_gate_dir = "{shared_dir}"\nload_spacing = 90')
check("a shared directory is passed on", {e.get("TAUCETI_LOAD_GATE_DIR") for e in envs(shared)} == {str(shared_dir)})
check("...and the spacing", {e.get("TAUCETI_LOAD_SPACING") for e in envs(shared)} == {"90"})

statuses = {"tlg-c1": {"state": "waiting-load"}, "tlg-c2": {"state": "running"}, "tlg-c3": {"state": "waiting-load"}}
line = shared.load_line(statuses, ["tlg-c1", "tlg-c2"])
check("the view counts enabled workers waiting", "1 worker waiting" in line and "at 64" in line, line)
check("a worker on hold reads as waiting", shared.health({"state": "waiting-load", "child_pid": 1, "heartbeat_at": 0}) == ("waiting-load", "yellow"))
shared_dir.mkdir()
if os.geteuid():
    shared_dir.chmod(0o500)
    line = shared.load_line({}, [])
    check("a slot directory this user cannot write is named", "not spaced" in line and str(shared_dir) in line, line)
    shared_dir.chmod(0o700)
check("a writable one is not mentioned", "not spaced" not in shared.load_line({}, []))

sys.exit(1 if fails else 0)
