#!/usr/bin/env python3
"""When the wrapper starts a decide round, offline: with no decline and no `wait` ruling, a prerequisite
the decide stage added that is still open or in flight on the target list keeps the 6-hourly cadence
running (the round retires it once its PRs have moved on); a done one, or an operator's own item, does
not. Run: python3 tests/decide_cadence.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="decide-cadence-"))
HOME = TMP / "fleet"
HOME.mkdir()
LIST = TMP / "targets.md"
(HOME / "fleet.toml").write_text(
    f'[fleet]\nname = "tst"\ngithub_login = "acct"\ntargets = "{LIST}"\n'
    f'[paths]\nworker = "{TMP / "no-worker"}"\nroadmap = "{TMP / "roadmap"}"\n'
)
os.environ["TAUCETI_FLEET_HOME"] = str(HOME)
os.environ.pop("TAUCETI_FLEET_TARGETS", None)
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


HEAD = "# t\n<!-- tauceti-targets:v1 -->\n\n## Area\n"
ADDED = "(unblocks: #5; needs: none; source: L0; added by the decide stage)"
last = tf.periodic_dir() / "decide.last"
last.touch()
os.utime(last, (time.time() - 7 * 3600, time.time() - 7 * 3600))

LIST.write_text(HEAD + "- [ ] `mine` — L0, prove it. (serves: B1; needs: none)\n")
check("with nothing to rule on, the cadence does not run decide", not tf.periodic_due("decide"))
LIST.write_text(HEAD + f"- [ ] `pre` — L0, prove it. {ADDED}\n")
check("an open added prerequisite keeps the cadence running", tf.periodic_due("decide"))
LIST.write_text(HEAD + f"- [~] `pre` — L0, prove it. {ADDED[:-1]}; in flight: #6)\n")
check("so does one in flight", tf.periodic_due("decide"))
os.utime(last, None)
check("but not before the cadence comes round", not tf.periodic_due("decide"))
os.utime(last, (time.time() - 7 * 3600, time.time() - 7 * 3600))
LIST.write_text(HEAD + f"- [x] `pre` — L0, prove it. {ADDED[:-1]}; landed: #7)\n")
check("a done one does not", not tf.periodic_due("decide"))
LIST.unlink()
check("a missing list does not", not tf.periodic_due("decide"))

sys.exit(1 if fails else 0)
