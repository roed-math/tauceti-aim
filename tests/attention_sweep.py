#!/usr/bin/env python3
"""sweep_attention and the view of all fleets, offline:
  * an item whose PR merged or closed, whose spent budget's head moved or went ready to merge, whose
    lookahead branch reads or was pushed since, or whose budget warning outlived the slot is archived
    with a cause; anything else stays, and so does everything when the PR read is refused;
  * one read per PR however many items name it, against the item's own repo;
  * the view of all fleets shows one row per PR, naming each fleet that filed it.
Run: python3 tests/attention_sweep.py. Exit 0 = all hold."""

import importlib.machinery
import io
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="attention-sweep-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)
tf.INCIDENTS = TMP / "incidents"
tf.ACKED = tf.INCIDENTS / "acked"
tf.LOOKAHEAD_DIR = TMP / "lookahead"
tf.periodic_enabled = lambda stage: True

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


NOW = time.time()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def item(name, **rec):
    tf.INCIDENTS.mkdir(parents=True, exist_ok=True)
    rec.setdefault("last_at", iso(NOW - 3 * 3600))
    (tf.INCIDENTS / f"{name}.json").write_text(json.dumps(rec))


HEAD, NEW = "a" * 40, "b" * 40
PRS = {
    ("TauCetiProject/TauCeti", 1): {"state": "MERGED", "headRefOid": HEAD, "labels": []},
    ("TauCetiProject/TauCeti", 2): {"state": "OPEN", "headRefOid": NEW, "labels": []},
    ("TauCetiProject/TauCeti", 3): {"state": "OPEN", "headRefOid": HEAD, "labels": [{"name": "ready-to-merge"}]},
    ("TauCetiProject/TauCeti", 4): {"state": "OPEN", "headRefOid": HEAD, "labels": [{"name": "awaiting-author"}]},
    ("TauCetiProject/TauCeti", 5): {"state": "CLOSED", "headRefOid": HEAD, "labels": []},
    ("TauCetiProject/TauCetiRoadmap", 6): {"state": "CLOSED", "headRefOid": HEAD, "labels": []},
}
reads = []


def fake_view(n, fields, repo=tf.TAUCETI):
    reads.append((repo, n))
    return None if n == 7 else PRS.get((repo, n))


tf._gated_pr_view = fake_view
active = {"now": False}
tf.schedule_info = lambda: {"active_now": active["now"]}

item("budget-spent-rebase-1", kind="budget-spent", stage="rebase", pr=1, head=HEAD)
item("budget-spent-rebase-1b", kind="budget-spent", stage="fix", pr=1, head=HEAD)
item("budget-spent-rebase-2", kind="budget-spent", stage="rebase", pr=2, head=HEAD)
item("budget-spent-fix-ci-3", kind="budget-spent", stage="fix-ci", pr=3, head=HEAD)
item("budget-spent-rebase-4", kind="budget-spent", stage="rebase", pr=4, head=HEAD)
item("declined-rebase-5", kind="declined", stage="rebase", pr=5, head=HEAD, decision="escalate")
item("progress-stuck-X-6", kind="progress-stuck", pr=6, repo="TauCetiProject/TauCetiRoadmap", head=HEAD)
item("budget-spent-rebase-7", kind="budget-spent", stage="rebase", pr=7, head=HEAD)
item("declined-fix-8", kind="declined", stage="fix", pr=1, head=HEAD, last_at=iso(NOW - 60))  # awaiting decide
item("lookahead-unreadable-A-read", kind="lookahead", what="unreadable", target="A/read", branch="lookahead/A/read")
item("lookahead-unreadable-A-still", kind="lookahead", what="unreadable", target="A/still", branch="lookahead/A/still")
item("lookahead-failed-A-pushed", kind="lookahead", what="failed", target="A/pushed", branch="lookahead/A/pushed")
item("lookahead-failed-A-idle", kind="lookahead", what="failed", target="A/idle", branch="lookahead/A/idle")
item("lookahead-failed-A-gone", kind="lookahead", what="failed", target="A/gone", branch="lookahead/A/gone")
item("budget-short-claude", kind="budget-short", key="claude")
tf.LOOKAHEAD_DIR.mkdir()
(tf.LOOKAHEAD_DIR / "branches.json").write_text(json.dumps({"at": NOW - 60, "fork": "f/f", "branches": [
    {"area": "A", "slug": "read", "sha": "1" * 40, "status": "partial", "built_at": NOW - 4 * 3600},
    {"area": "A", "slug": "still", "sha": "2" * 40, "status": "unknown", "built_at": None},
    {"area": "A", "slug": "pushed", "sha": "3" * 40, "status": "partial", "built_at": NOW - 600},
    {"area": "A", "slug": "idle", "sha": "4" * 40, "status": "partial", "built_at": NOW - 5 * 3600},
]}))

lines = tf.sweep_attention()
left = sorted(p.stem for p in tf.INCIDENTS.glob("*.json"))
cleared = {p.stem: json.loads(p.read_text()) for p in tf.ACKED.glob("*.json")}
want_left = ["budget-spent-rebase-4", "budget-spent-rebase-7", "declined-fix-8", "lookahead-failed-A-idle",
             "lookahead-unreadable-A-still"]
check("only the items nothing has overtaken stay", left == want_left, str(left))
check("a merged PR clears every item naming it", cleared.get("budget-spent-rebase-1", {}).get("resolved_cause") == "merged"
      and "budget-spent-rebase-1b" in cleared)
check("a spent budget clears on a new head", cleared.get("budget-spent-rebase-2", {}).get("resolved_cause") == "new head")
check("...and when the PR went ready to merge", cleared.get("budget-spent-fix-ci-3", {}).get("resolved_cause") == "ready to merge")
check("an escalation on a closed PR clears", cleared.get("declined-rebase-5", {}).get("resolved_cause") == "closed")
check("a progress report is read in its own repo", ("TauCetiProject/TauCetiRoadmap", 6) in reads
      and "progress-stuck-X-6" in cleared)
check("one read per PR", len(reads) == len(set(reads)), str(reads))
check("a decline the decide stage has yet to rule on is not read or touched",
      "declined-fix-8" in left and reads.count(("TauCetiProject/TauCeti", 1)) == 1)
check("an unreadable branch clears once its header reads", cleared.get("lookahead-unreadable-A-read", {}).get("resolved_cause") == "branch reads")
check("a failed branch clears once pushed since", cleared.get("lookahead-failed-A-pushed", {}).get("resolved_cause") == "branch pushed")
check("...or once the listing no longer has it", cleared.get("lookahead-failed-A-gone", {}).get("resolved_cause") == "branch gone")
check("a budget warning clears outside the slot", cleared.get("budget-short-claude", {}).get("resolved_cause") == "slot ended")
check("cleared items are reported as such", len(lines) == len(cleared) and len(tf.recent_clears()) == len(cleared))

item("budget-short-claude", kind="budget-short", key="claude")
active["now"] = True
tf.sweep_attention()
check("...but stays in the slot", (tf.INCIDENTS / "budget-short-claude.json").exists())

shown = lambda **d: tf.lookahead_needs_owner({"kind": "lookahead", **d})
check("a lookahead session's first failure is not shown, its second is",
      not shown(what="failed", count=1) and shown(what="failed", count=2))
check("...a port round that stood down never is, an unreadable header always is",
      not shown(what="mismatch", count=3) and shown(what="unreadable", count=1))

try:
    from rich.console import Console
except ImportError:
    Console = None
    print("skip the view of all fleets: no `rich` here (run under the worker's uv python)")
if Console is not None:
    snap = lambda fleet, ids: {"fleet": fleet, "user": fleet, "at": NOW, "manager_up": True, "healthy": 1, "enabled": 1,
                               "needs": [[f"{i} line", "yellow"] for i, _c in ids], "needs_ids": [list(x) for x in ids]}
    tf.render_view = lambda main, console=None, needs=True, above=(): above
    panels = tf.render_fleets([snap("gq2", [("T#9", "attempts spent"), ("T#8", "declined")]),
                               snap("gqm", [("T#9", "attempts spent")])], TMP)
    c = Console(width=200, record=True, file=io.StringIO())
    for p in panels:
        c.print(p)
    out = c.export_text()
    check("the view of all fleets shows a PR once, naming its fleets", out.count("T#9 line") == 1 and "gq2,gqm: T#9" in out, out)
    check("...and counts by cause", "(2: 1 attempts spent, 1 declined)" in out, out)

sys.exit(1 if fails else 0)
