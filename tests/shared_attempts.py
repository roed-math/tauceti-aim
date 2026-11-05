#!/usr/bin/env python3
"""Attempt budgets shared by the fleets on one `fleet.shared_cache`, offline:
  * the workers get TAUCETI_SHARED_ATTEMPTS_DIR = <shared_cache>/attempts, and a fleet without the
    setting gets nothing;
  * each fleet carries its own counts in once, keeping the larger, so a PR this fleet spent stays spent;
    a rebase checkpoint already there is kept; the second reconcile carries nothing (a refund made in
    the shared store since must survive), and the files are group read/write.
Run: python3 tests/shared_attempts.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import os
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="shared-attempts-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


tf.SHARED_CACHE = None
check("a fleet without fleet.shared_cache keeps its own budgets", "TAUCETI_SHARED_ATTEMPTS_DIR" not in tf.worker_env()
      and tf.merge_attempts_into_shared() == 0)
tf.SHARED_CACHE = TMP / "shared"
check("its workers charge the shared store", tf.worker_env().get("TAUCETI_SHARED_ATTEMPTS_DIR") == str(TMP / "shared" / "attempts"))
shared = TMP / "shared" / "attempts"


def fleet(name: str, counts: dict[str, str]) -> None:
    tf.GATE_DIR = TMP / name / "gate"
    own = tf.GATE_DIR / "attempts"
    own.mkdir(parents=True)
    for k, v in counts.items():
        (own / k).write_text(v)


fleet("gq2", {"rebase-pr-22428": "3", "fix-27295-2d47b932faad": "1", "rebase-progress-9.json": '{"a": 2}', ".lock": ""})
check("the first fleet carries its counts in", tf.merge_attempts_into_shared() == 3
      and (shared / "rebase-pr-22428").read_text() == "3")
check("...group read/write", stat.S_IMODE((shared / "rebase-pr-22428").stat().st_mode) & 0o060 == 0o060)
(shared / "fix-27295-2d47b932faad").write_text("0")  # a refund since
fleet("gqm", {"rebase-pr-22428": "1", "fix-27295-2d47b932faad": "2", "ci-3-abc": "2", "rebase-progress-9.json": '{"b": 1}'})
tf.merge_attempts_into_shared()
read = lambda k: (shared / k).read_text()
check("the larger count wins", read("rebase-pr-22428") == "3" and read("fix-27295-2d47b932faad") == "2" and read("ci-3-abc") == "2")
check("a rebase checkpoint already there is kept", read("rebase-progress-9.json") == '{"a": 2}')
(shared / "fix-27295-2d47b932faad").write_text("1")  # another refund
check("a fleet carries its counts in only once", tf.merge_attempts_into_shared() == 0 and read("fix-27295-2d47b932faad") == "1")

sys.exit(1 if fails else 0)
