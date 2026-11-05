#!/usr/bin/env python3
"""Flexible workers (`up.flex = true`), offline:
  * every author and fixer slot runs fix, fix-ci, rebase and roadmap, keeping its id and agent;
    reviewers and a fleet without the setting are unchanged;
  * the reconcile runs every slot whatever the backlog or the open-PR cap, and reviewers at their most;
  * once every enabled slot's last round found nothing to do (at the cap, "backpressure, not
    authoring" included), only the first stays on, and a slot's productive round turns them all back on;
  * without the setting a stale backpressure reason does not make the authors idle.
Run: python3 tests/flex_workers.py. Exit 0 = all hold."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="flex-workers-"))
fails = 0
FLEX = ["fix", "fix-ci", "rebase", "roadmap"]
IDLE = "roadmap: nothing to author — the target list offers nothing eligible"
CAP = "roadmap: 100 open PRs in selected scope (>= 100) — backpressure, not authoring"


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def load(name: str, up: str):
    home = TMP / name
    home.mkdir()
    (home / "fleet.toml").write_text(f'[fleet]\nname = "tst"\ngithub_login = "acct"\ntargets = "none"\n'
                                     f'[authoring]\nmax_open_prs = 100\n[up]\nmax_fixers = 3\nmax_reviewers = 2\n{up}')
    os.environ["TAUCETI_FLEET_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "bin" / "tauceti-aim"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def shape(**kw):
    base = dict(claude=2, codex=2, fixers=1, reviewers=1, max_fixers=3, max_reviewers=2, sandbox="bubble",
                pace="", pilot=False, mutations_per_hour=30, reads_per_hour=2500, claude_model="claude-opus-5-5",
                claude_effort="high", codex_model="", codex_effort="", dry_run=True)
    return argparse.Namespace(**{**base, **kw})


def stages(spec):
    return spec["only"].split(",")


def reconcile(mod, mine_open, backlog, statuses):
    """One dry reconcile with the counts and statuses given; returns (specs written, its line)."""
    got = {}

    def write_config(specs, *_a, **_k):
        got["specs"] = specs
        return True, [], []

    mod.fleet_backlog = lambda *_a, **_k: (mine_open, backlog, "test")
    mod.worker_status = lambda: statuses
    mod.lease_logins = lambda specs, **_k: specs
    mod.write_config = write_config
    for fn in ("ensure_pool_warm", "ensure_cache_proxy_alive", "ensure_auth_proxy_alive", "honor_restart_request"):
        setattr(mod, fn, lambda *_a, **_k: None)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        mod.reconcile(shape(), apply=False, quiet=False)
    return {s["id"]: s for s in got["specs"]}, out.getvalue()


def write_enabled(mod, specs):
    """The config the reconcile would have written, so the next pass sees which slots are off."""
    mod.CONFIG.write_text(mod.render_toml(list(specs.values())))


flex = load("flex", "flex = true\n")
specs = {s["id"]: s for s in flex.fleet_specs(shape(fixers=3, reviewers=2))}
slots = ["tst-c1", "tst-c2", "tst-x1", "tst-x2", "tst-fix1", "tst-fix2", "tst-fix3"]
check("every author and fixer slot fixes and authors", all(stages(specs[w]) == FLEX for w in slots),
      str({w: specs[w]["only"] for w in slots}))
check("…keeping its agent", [specs[w]["agent"] for w in slots] == ["claude", "claude", "auto", "auto", "claude", "auto", "claude"])
check("reviewers only review", all(stages(specs[w]) == ["review"] for w in ("tst-rev1", "tst-rev2")))
check("the open-PR cap turns no flexible slot off",
      all(s["enabled"] for s in flex.fleet_specs(shape(fixers=3, authors_enabled=False))))

busy = {w: {"state": "running"} for w in slots}
specs, line = reconcile(flex, 83, 75, busy)
check("a busy flexible fleet runs every slot", [w for w in slots if specs.get(w, {}).get("enabled")] == slots, line)
check("…and reviewers at their most", specs.get("tst-rev2", {}).get("enabled") is True, line)
check("…and says so", "flex 7 on (fix first, else author)" in line, line)
specs, line = reconcile(flex, 100, 0, {**busy, "tst-c1": {"state": "backoff", "failure_reason": CAP}})
check("at the open-PR cap the slots stay on to fix", all(specs[w]["enabled"] for w in slots), line)

idle = {w: {"state": "backoff", "failure_reason": IDLE} for w in slots}
idle["tst-fix2"] = {"state": "backoff", "failure_reason": CAP}
specs, line = reconcile(flex, 100, 2, idle)
check("all slots idle: only the first stays on as the probe",
      [w for w in slots if specs[w]["enabled"]] == ["tst-c1"], line)
check("…and the line says so", "flex idle: 1 probe" in line, line)
check("…and reviewers stay at their most", specs["tst-rev2"]["enabled"], line)
write_enabled(flex, specs)
specs, line = reconcile(flex, 100, 2, idle)
check("the turned-off slots' old reasons keep it idle", [w for w in slots if specs[w]["enabled"]] == ["tst-c1"], line)
os.environ.update(TAUCETI_ROUND_WID="tst-c1", TAUCETI_ROUND_RC="0")
specs, line = reconcile(flex, 100, 3, idle)
check("the probe's productive round turns every slot back on", all(specs[w]["enabled"] for w in slots), line)
os.environ.update(TAUCETI_ROUND_RC="75")
write_enabled(flex, specs)
specs, line = reconcile(flex, 100, 3, {**idle, "tst-fix3": {"state": "running"}})
check("a slot mid-round keeps them all on", all(specs[w]["enabled"] for w in slots), line)
for var in ("TAUCETI_ROUND_WID", "TAUCETI_ROUND_RC"):
    os.environ.pop(var)

fixed = load("fixed", "")
specs = {s["id"]: s for s in fixed.fleet_specs(shape(fixers=2, reviewers=2))}
check("without up.flex authors only author", all(stages(specs[w]) == ["roadmap"] for w in ("tst-c1", "tst-x2")))
check("…and fixers only fix", all(stages(specs[w]) == ["fix", "fix-ci", "rebase"] for w in ("tst-fix1", "tst-fix2")))
stale = {w: {"state": "backoff", "failure_reason": CAP} for w in ("tst-c1", "tst-c2", "tst-x1", "tst-x2")}
check("…and an author's stale backpressure reason is not idleness", not fixed.authors_idle(stale))
specs, line = reconcile(fixed, 83, 75, {w: {"state": "running"} for w in slots})
check("…and the reconcile line keeps its form", "authors on, fixers 3, reviewers 2" in line, line)

print("\nALL OK" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
