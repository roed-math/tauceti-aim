#!/usr/bin/env python3
"""What a slot start depends on, offline:
  * `fleet.shared_cache` reaches every worker as $TAUCETI_SHARED_READ_CACHE, and `up` creates its
    `review_state/` group-writable and setgid, refusing one the other fleets could not write into;
  * a scheduled `up` that fails puts a `schedule-start` item on the attention list with the reason
    `up` gave, keeps the first failure across the tick's retries, shows in the all-fleets strip, and
    is archived by `attention --ack schedule`;
  * `up` fast-forwards a clean `main` roadmap checkout, at most once per 10 minutes, and leaves a
    checkout with local changes or on another branch alone;
  * a target area archived under `Completed/` with nothing open is accepted, and refused if it has open items.
Run: python3 tests/slot_start.py. Exit 0 = all hold."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="slot-start-"))
HOME = TMP / "fleet"
HOME.mkdir()
SHARED = TMP / "shared" / "cache"
(TMP / "shared").mkdir()
now = datetime.now()
day = lambda t: t.strftime("%a %H:%M")  # noqa: E731
(HOME / "fleet.toml").write_text(
    f'[fleet]\nname = "tst"\ngithub_login = "acct"\nshared_cache = "{SHARED}"\n'
    f'[paths]\nworker = "{TMP / "no-worker"}"\nroadmap = "{TMP / "roadmap"}"\n'
    f'[schedule]\nactive = "{day(now - timedelta(hours=1))} -> {day(now + timedelta(hours=1))}"\n')
os.environ["TAUCETI_FLEET_HOME"] = str(HOME)
for var in ("INVOCATION_ID", "TAUCETI_FLEET_SHARED_CACHE"):
    os.environ.pop(var, None)
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def run(*argv):
    sys.argv = ["tauceti-aim", *argv]
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = tf.main()
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue()


# ---- the shared read cache ----------------------------------------------------------------------------------------
check("workers and periodic rounds get $TAUCETI_SHARED_READ_CACHE",
      tf.worker_env().get("TAUCETI_SHARED_READ_CACHE") == str(SHARED)
      and tf.periodic_env().get("TAUCETI_SHARED_READ_CACHE") == str(SHARED))
check("a usable shared cache is no problem", tf.shared_cache_problem() == "", tf.shared_cache_problem())
mode = (SHARED / "review_state").stat().st_mode
check("…and `up` created review_state/ group-writable and setgid",
      bool(mode & stat.S_IWGRP and mode & stat.S_ISGID), oct(mode))
os.chmod(SHARED / "review_state", 0o755)
check("a review_state/ without group write is refused, with the chmod that fixes it",
      "chmod 2775" in tf.shared_cache_problem())
os.chmod(SHARED / "review_state", 0o3775)
check("a sticky review_state/ is refused", "sticky" in tf.shared_cache_problem())
os.chmod(SHARED / "review_state", 0o2775)

# ---- a failed scheduled start --------------------------------------------------------------------------------------
rc, out = run("schedule", "--tick")
inc = tf.read_json(tf.START_FAILED) if tf.START_FAILED.is_file() else {}
check("a slot whose `up` fails ticks with a nonzero status", rc not in (0, None), out)
check("…and puts a schedule-start item on the attention list",
      inc.get("kind") == "schedule-start" and any(d.get("kind") == "schedule-start" for d in tf.attention_items()))
check("…naming the reason `up` gave", "cannot import TauCetiWorker" in inc.get("detail", ""), inc.get("detail"))
check("…and the slot stays unstarted, so the next tick retries", "active" not in tf.SCHEDULE_STATE.read_text()
      if tf.SCHEDULE_STATE.is_file() else True)
first = inc.get("first_at")
run("schedule", "--tick")
inc = tf.read_json(tf.START_FAILED)
check("a retry keeps the first failure and counts the tries", inc.get("first_at") == first and inc.get("tries") == 2,
      json.dumps(inc))
sch = tf.schedule_info()
state, style = tf._fleet_state({"schedule": sch, "manager_up": False})
check("the all-fleets strip says the start failed, in red", "START FAILED" in state and "red" in style, state)
state, _ = tf._fleet_state({"schedule": {**sch, "active_now": False}, "manager_up": False})
check("…and still says so once the slot is over", "never started" in state, state)
state, _ = tf._fleet_state({"schedule": sch, "manager_up": True})
check("…but not over a fleet that is running", "RUNNING" in state, state)
rc, out = run("attention", "--ack", "schedule")
check("`attention --ack schedule` archives it", not tf.START_FAILED.is_file() and "acknowledged 1" in out, out)


# ---- attention links ------------------------------------------------------------------------------------------------
tf.INCIDENTS.mkdir(parents=True, exist_ok=True)
(tf.INCIDENTS / "progress-stuck-X-1369.json").write_text(json.dumps({
    "kind": "progress-stuck", "key": "X-1369", "pr": 1369, "repo": "TauCetiProject/TauCetiRoadmap",
    "detail": "report", "last_at": "2026-10-22T06:45:17Z"}))
rc, out = run("attention")
check("an attention item links the repository its PR is in",
      "https://github.com/TauCetiProject/TauCetiRoadmap/pull/1369" in out, out)
(tf.INCIDENTS / "progress-stuck-X-1369.json").unlink()

# ---- the roadmap checkout ------------------------------------------------------------------------------------------
def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


origin = TMP / "origin.git"
git("init", "-q", "--bare", "-b", "main", str(origin))
work = TMP / "work"
git("clone", "-q", str(origin), str(work))
for c in ("user.email", "user.name"):
    git("config", c, "t@example.com", cwd=work)
(work / "README.md").write_text("one\n")
git("add", ".", cwd=work)
git("commit", "-q", "-m", "one", cwd=work)
git("push", "-q", "origin", "HEAD:main", cwd=work)
co = TMP / "roadmap"
git("clone", "-q", "-b", "main", str(origin), str(co))
(work / "NewRoadmap").mkdir()
(work / "NewRoadmap" / "README.md").write_text("new\n")
git("add", ".", cwd=work)
git("commit", "-q", "-m", "two", cwd=work)
git("push", "-q", "origin", "HEAD:main", cwd=work)
note = tf.refresh_roadmap_checkout()
check("a clean main checkout is fast-forwarded to a roadmap merged since", (co / "NewRoadmap" / "README.md").is_file(),
      str(note))
check("…and says so", note is not None and "→" in note, str(note))
check("a second `up` within 10 minutes does not fetch again", tf.refresh_roadmap_checkout() is None)
(co / ".git" / "FETCH_HEAD").unlink()
(co / "README.md").write_text("edited\n")
check("a checkout with local changes is left alone", "local changes" in (tf.refresh_roadmap_checkout() or ""))
git("checkout", "-q", "--", "README.md", cwd=co)
git("checkout", "-q", "-b", "draft", cwd=co)
check("a checkout on another branch is left alone", "not main" in (tf.refresh_roadmap_checkout() or ""))

# ---- target areas against the checkout ------------------------------------------------------------------------------
# Archiving moves a roadmap to Completed/<area>/ (TauCetiRoadmap#726 did that to NumberFieldArithmetic on 2026-10-21,
# and every scheduled `up` of a fleet whose list still named it then refused to start).
rm = TMP / "rm"
for d in ("TauCetiRoadmap/Live", "Completed/Done", "Completed/Unfinished"):
    (rm / d).mkdir(parents=True)
    (rm / d / "README.md").write_text("x\n")
open_counts = {"Live": 3, "Done": 0, "Unfinished": 2, "Nowhere": 1}
tf.open_items = lambda t, area: [None] * open_counts[area]
lst = type("T", (), {"areas": ["Live", "Done"]})()
problem, archived = tf.target_area_problems(lst, rm)
check("an archived area with nothing open is accepted", problem == "", problem)
check("…and reported as archived", archived == ["Done"], archived)
lst.areas = ["Live", "Unfinished"]
problem, _ = tf.target_area_problems(lst, rm)
check("an archived area with open items is refused, naming the count",
      "Unfinished (2 open)" in problem and "Completed" in problem, problem)
lst.areas = ["Live", "Nowhere"]
problem, _ = tf.target_area_problems(lst, rm)
check("an area in neither place is still refused", "'Nowhere'" in problem and "not roadmap directories" in problem, problem)
lst.areas = ["Bad Name"]
check("a malformed area name is refused", "not roadmap directories" in tf.target_area_problems(lst, rm)[0])

print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
