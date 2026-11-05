#!/usr/bin/env python3
"""The target audit and the dashboard complete an item only as the curator would, offline:
  * a merged PR whose marker says `"partial": true` reopens its item (`partial: #N`) instead of marking
    it done, so the items that need it stay ineligible;
  * a merged PR with the item's complete marker marks it done; one without the item's marker is left
    for the curator;
  * the dashboard counts neither a partial marker nor a PR the item records as partial as done.
Run: python3 tests/targets_partial.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(os.environ.get("TAUCETI_TEST_WORKER", "~/claude/TauCetiWorker-v2")).expanduser()))
TMP = Path(tempfile.mkdtemp(prefix="targets-partial-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
tf = importlib.util.module_from_spec(importlib.util.spec_from_loader("tf", loader))
loader.exec_module(tf)
tf.INCIDENTS = TMP / "incidents"
tf.ACKED = tf.INCIDENTS / "acked"
tf.TARGETS = TMP / "targets.md"
tf.targets_lock = lambda: __import__("contextlib").nullcontext()

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def marker(slug, partial=False):
    d = {"focus": "Area", "id": slug, **({"partial": True} if partial else {})}
    return f"<!--tauceti-target:v1 {json.dumps(d)}-->"


tf.TARGETS.write_text("""# targets
<!-- tauceti-targets:v1 -->

## Area

- [~] `parent` — the parent (in flight: #5)
- [ ] `child` — the child (needs: `parent`)
- [~] `whole` — done in one (in flight: #6)
- [~] `unmarked` — no marker (in flight: #7)
""")
PRS = {5: {"state": "MERGED", "body": marker("parent", partial=True)},
       6: {"state": "MERGED", "body": marker("whole")},
       7: {"state": "MERGED", "body": "no marker here"}}
tf._gated_pr_view = lambda n, fields, repo=tf.TAUCETI: PRS.get(n)
tf.cmd_targets(types.SimpleNamespace(apply=True))
t = tf.load_targets(tf.TARGETS)
st = {it.slug: it.status for it in t.areas["Area"]}
check("a partial merge reopens its item", st["parent"] == "open" and "partial: #5" in t.find("parent").line, str(st))
check("...so the item needing it stays ineligible", "child" not in [it.slug for it in tf.eligible_items(t, "Area")])
check("a complete marker marks its item done", st["whole"] == "done", str(st))
check("a merge without the item's marker is left for the curator", st["unmarked"] == "inflight", str(st))

tf.TARGETS.write_text("""# targets
<!-- tauceti-targets:v1 -->

## Area

- [ ] `parent` — the parent (partial: #5)
- [ ] `child` — the child (needs: `parent`)
- [ ] `whole` — done in one
""")
lists = {"open": [], "closed": [{"number": 5, "body": marker("parent"), "state": "MERGED", "mergedAt": "x", "author": {}},
                                {"number": 8, "body": marker("parent", partial=True), "state": "MERGED", "mergedAt": "x", "author": {}},
                                {"number": 6, "body": marker("whole"), "state": "MERGED", "mergedAt": "x", "author": {}}]}


def fake_gated(op, argv, kind="api_read", target=tf.TAUCETI, timeout=60):
    out = lists["open"] if "open" in argv else lists["closed"] if "closed" in argv else []
    return types.SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")


tf.gated_run = fake_gated
d = tf.Progress().refresh()
area = d.get("per_area", {}).get("Area", {})
check("the dashboard counts only the complete merge as done", area.get("done") == 1 and area.get("eligible") == 1, str(d))

sys.exit(1 if fails else 0)
