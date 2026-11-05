#!/usr/bin/env python3
"""`up.mathlib_store` and `up.persistent_build`, offline:
  * the workers get TAUCETI_MATHLIB_STORE and TAUCETI_PERSISTENT_BUILD only when the settings ask;
  * the reconcile's pool check counts a pinned revision missing from the store as missing, keeps a pinned
    one from pruning by touching its marker, and prunes one unpinned for longer than the grace period;
  * the background warm-pool it starts is told both.
The worker's mathlib_store is stood in for, so no worker checkout is needed.
Run: python3 tests/shared_builds.py. Exit 0 = all hold."""

import argparse
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="shared-builds-"))
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


store_mod = types.ModuleType("tauceti_worker.mathlib_store")
store_mod.READY = ".tauceti-store-ready"
store_mod.ready = lambda store, rev: (store / rev / store_mod.READY).is_file()
sys.modules.setdefault("tauceti_worker", types.ModuleType("tauceti_worker"))
sys.modules["tauceti_worker.mathlib_store"] = store_mod
sys.modules["tauceti_worker"].mathlib_store = store_mod


def load(name: str, up: str):
    home = TMP / name
    home.mkdir()
    (home / "fleet.toml").write_text(f'[fleet]\nname = "tsb"\ngithub_login = "acct"\ntargets = "none"\n[up]\n{up}\n')
    os.environ["TAUCETI_FLEET_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "bin" / "tauceti-aim"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


shape = argparse.Namespace(claude=1, codex=0, fixers=1, reviewers=1, sandbox="bubble", pace="", pilot=False,
                           mutations_per_hour=30, reads_per_hour=2000, claude_model="m", claude_effort="high",
                           codex_model="", codex_effort="")
off = load("off", "")
check("nothing shared unless asked", not any({"TAUCETI_MATHLIB_STORE", "TAUCETI_PERSISTENT_BUILD"} & set(s["env"])
                                             for s in off.fleet_specs(shape)))
on = load("on", "mathlib_store = true\npersistent_build = true")
envs = [s["env"] for s in on.fleet_specs(shape)]
check("every worker gets the store and the persistent build",
      all(e.get("TAUCETI_MATHLIB_STORE") == str(on.FLEET_HOME / "mathlib-store") and e.get("TAUCETI_PERSISTENT_BUILD") == "1"
          for e in envs))

store = on.MATHLIB_STORE
for rev, age in (("pinned-ready", 10 * 3600), ("unpinned-old", 10 * 3600), ("unpinned-new", 600)):
    (store / rev).mkdir(parents=True)
    marker = store / rev / store_mod.READY
    marker.write_text(rev)
    os.utime(marker, (time.time() - age, time.time() - age))
on.needed_revs = lambda: {"pinned-ready": ["main"], "pinned-missing": ["#1"]}
on.warmed_revs = lambda: {"pinned-ready", "pinned-missing"}
started = []
on.subprocess = types.SimpleNamespace(Popen=lambda argv, **kw: started.append(argv), run=on.subprocess.run, DEVNULL=-3)
on.ensure_pool_warm(quiet=True)
argv = started[0] if started else []
check("a pinned revision the store lacks is warmed", argv[argv.index("--revs") + 1:argv.index("--prune")] == ["pinned-missing"], str(argv))
check("one unpinned past the grace period is pruned, a recent one is not", argv[argv.index("--prune") + 1:] == ["unpinned-old"], str(argv))
check("a pinned one is touched, so it never ages out",
      time.time() - (store / "pinned-ready" / store_mod.READY).stat().st_mtime < 60)

print("\nALL OK" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
