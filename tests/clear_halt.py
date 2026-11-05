#!/usr/bin/env python3
"""`clear-halt` clears through the worker, for this fleet only, offline:
  * each halted worker of this fleet goes through `work --clear-halt`, which also returns the fleet gate
    to RUNNING; a halted worker of another fleet sharing the checkout is left alone;
  * with no worker halted, one call still clears a halt the gate holds by itself.
Run: python3 tests/clear_halt.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="clear-halt-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
tf = importlib.util.module_from_spec(importlib.util.spec_from_loader("tf", loader))
loader.exec_module(tf)
tf.WORKTREE = TMP / "worker"
tf.CONFIG = TMP / "fleet" / "workers.toml"
tf.PERIODIC_WID = "demo-periodic"
tf.CONFIG.write_text('[[workers]]\nid = "demo-c1"\n\n[[workers]]\nid = "demo-c2"\n')

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


calls = []
tf.tauceti = lambda *args, capture=False: calls.append(args) or type("P", (), {"returncode": 0})()
for w in ("demo-c2", "other-c1"):
    (tf.WORKTREE / "state" / w).mkdir(parents=True)
    (tf.WORKTREE / "state" / w / "halt.json").write_text('{"reason": "wrong login"}')
tf.cmd_clear_halt(None)
check("this fleet's halted worker is cleared through the worker", calls == [("work", "--clear-halt", "--worker-id", "demo-c2")], str(calls))
check("another fleet's halt is left alone", (tf.WORKTREE / "state" / "other-c1" / "halt.json").exists())
(tf.WORKTREE / "state" / "demo-c2" / "halt.json").unlink()
calls.clear()
tf.cmd_clear_halt(None)
check("with none halted, one call still clears the gate", calls == [("work", "--clear-halt", "--worker-id", "demo-c1")], str(calls))

sys.exit(1 if fails else 0)
