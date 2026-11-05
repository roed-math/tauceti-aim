#!/usr/bin/env python3
"""Budget pacing of the outside-list cap (`authoring.fallback_max_open = "auto"`), offline:
  * the controller step: a first step and a warm-up hold, no surplus stops outside work, spending under
    plan raises the cap (from the open-PR count when it was closed), over plan lowers it, bounds hold;
  * the horizon: the slot's end, the next slot's end for a fleet running ahead of it, or the reset;
  * the spend shares come from the workers' rounds.jsonl, Claude rounds only;
  * an end-to-end step writes the cap file the workers read, and the attention list gets a
    `budget-short` item only while the work that is not outside the list would outrun the budget.
Run: python3 tests/budget_pace.py [path to a TauCetiWorker checkout]. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
WORKER = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAUCETI_FLEET_WORKER", "~/claude/TauCetiWorker-v2")).expanduser()
TMP = Path(tempfile.mkdtemp(prefix="budget-pace-"))
HOME = TMP / "fleet"
WORKTREE = TMP / "worker"
(WORKTREE / "state").mkdir(parents=True)
(WORKTREE / "tauceti_worker").symlink_to(WORKER / "tauceti_worker")
HOME.mkdir()
(TMP / "targets.md").write_text("# t\n<!-- tauceti-targets:v1 -->\n\n## A\n- [ ] `x` — text (needs: none)\n")
(HOME / "fleet.toml").write_text(f"""[fleet]
name = "tst"
targets = "{TMP / 'targets.md'}"
github_login = "acct"
[paths]
worker = "{WORKTREE}"
[authoring]
fallback_max_open = "auto"
fallback_auto_max = 40
[schedule]
active = "Mon 23:00 -> Thu 07:00"
""")
os.environ["TAUCETI_FLEET_HOME"] = str(HOME)
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


now = time.time()
check("auto mode is read from fleet.toml", tf.FALLBACK_AUTO and tf.FALLBACK_MAX_OPEN_SETTING is None
      and tf.FALLBACK_AUTO_MIN == 0 and tf.FALLBACK_AUTO_MAX == 40)

# ---- the step ------------------------------------------------------------------------------------------
st = tf.pace_step({}, now=now, B=0.9, H=50, R=None, f_out=0.0, n_open=30)
check("a first step holds at the open-PR count", st["cap"] == 30 and st["reason"].startswith("warm-up"), str(st))
st = tf.pace_step({"cap": 25}, now=now, B=0.9, H=50, R=None, f_out=0.0, n_open=30)
check("a warm-up holds the last cap", st["cap"] == 25)
# D*H*(1.25)+0.05 = 0.02*(1-0.5)*40*1.25+0.05 = 0.55 > B = 0.5: no surplus
st = tf.pace_step({"cap": 33}, now=now, B=0.5, H=40, R=0.02, f_out=0.5, n_open=30)
check("no surplus over the reserve: outside work stops", st["cap"] == 0 and st["surplus"] < 0, str(st))
# reserve = 0.01*0.9*20*1.25 + 0.05 = 0.275; surplus 0.625 -> A = 0.03125/h; O = 0.001 < 0.8 A
st = tf.pace_step({"cap": 10}, now=now, B=0.9, H=20, R=0.01, f_out=0.1, n_open=30)
check("spending under plan raises the cap from the open count when it was closed", st["cap"] == 33, str(st))
# reserve = 0.04*0.5*10*1.25+0.05 = 0.30; surplus 0.30 -> A = 0.03/h; O = 0.02 within [0.024, 0.036]? no: 0.02 < 0.024 -> up
st = tf.pace_step({"cap": 35}, now=now, B=0.6, H=10, R=0.04, f_out=0.5, n_open=30)
check("still under plan: up a step", st["cap"] == 39, str(st))
# reserve = 0.05*0.2*10*1.25+0.05 = 0.175; surplus 0.225 -> A = 0.0225; O = 0.04 > 1.2 A
st = tf.pace_step({"cap": 35}, now=now, B=0.4, H=10, R=0.05, f_out=0.8, n_open=30)
check("spending over plan lowers the cap", st["cap"] == 31, str(st))
# reserve = 0.04*0.5*10*1.25 + 0.05 = 0.30; surplus = 0.24 -> A = 0.024; O = 0.02 in [0.0192, 0.0288]
st = tf.pace_step({"cap": 35}, now=now, B=0.54, H=10, R=0.04, f_out=0.5, n_open=30)
check("on plan: the cap holds", st["cap"] == 35 and st["reason"] == "on plan", str(st))
st = tf.pace_step({"cap": 39}, now=now, B=0.95, H=40, R=0.001, f_out=0.0, n_open=39)
check("the ceiling holds", st["cap"] == 40)

# ---- the horizon ---------------------------------------------------------------------------------------
def at(day: int, hh: int, mm: int = 0) -> float:  # 2026-10-05 is a Monday
    return datetime(2026, 10, 5 + day, hh, mm).timestamp()


far = at(10, 0)
check("inside its slot: the slot's end", tf.pace_horizon_end(at(1, 12), far) == at(3, 7))
check("ahead of its slot (an early start): the end of the coming slot", tf.pace_horizon_end(at(0, 5), far) == at(3, 7))
check("a reset before the slot ends is the horizon", tf.pace_horizon_end(at(1, 12), at(2, 4)) == at(2, 4))

# ---- spend shares --------------------------------------------------------------------------------------
w1 = WORKTREE / "state" / "tst-c1"
w1.mkdir(parents=True)
rows = [{"ended_at": now - 600, "provider": "claude", "kind": "target", "cost_usd": 6.0},
        {"ended_at": now - 500, "provider": "claude", "kind": "outside", "cost_usd": 2.0},
        {"ended_at": now - 400, "provider": "codex", "kind": "outside", "cost_usd": None, "tokens": {"input_tokens": 9}},
        {"ended_at": now - 300, "provider": "claude", "kind": "shared", "cost_usd": 2.0},
        {"ended_at": now - 5 * 3600, "provider": "claude", "kind": "outside", "cost_usd": 50.0}]
(w1 / "rounds.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
check("spend shares: Claude rounds in the window only", tf.round_costs(now - 3 * 3600) == {"target": 6.0, "outside": 2.0, "shared": 2.0})

# ---- an end-to-end step ----------------------------------------------------------------------------------
tf.manager_alive = lambda: True  # the real runtime dir may hold a live manager's socket: decide it here
# These steps run on the real clock, so give them a slot around it: 48 h left, before the 72 h reset. Under the
# fixed Mon-Thu slot the horizon was 72 h from Thursday 07:00 to Monday afternoon, and the reset check failed.
wd = lambda t: datetime.fromtimestamp(t).strftime("%a %H:%M")  # noqa: E731
tf.SETTINGS.setdefault("schedule", {})["active"] = f"{wd(now - 3600)} -> {wd(now + 48 * 3600)}"
resets = now + 3 * 86400
(w1 / "cache").mkdir()
(w1 / "cache" / "quota-claude.json").write_text(json.dumps({"fetched_at": now - 60, "payload": {
    "five_hour": {"utilization": 20.0, "resets_at": datetime.fromtimestamp(now + 3600).astimezone().isoformat()},
    "seven_day": {"utilization": 40.0, "resets_at": datetime.fromtimestamp(resets).astimezone().isoformat()}}}))
tf.USAGE_HISTORY.write_text(json.dumps({"at": now - 2 * 3600, "used": 36.0, "resets": resets}) + "\n")
(tf.GATE_DIR / "cache").mkdir(parents=True)
(tf.GATE_DIR / "cache" / "open_prs-x.json").write_text(json.dumps({"fetched_at": now, "prs": [
    {"number": n, "author": {"login": "acct"}, "isDraft": False, "labels": []} for n in range(12)]}))
out = tf.budget_pace(force=True)
cap_file = json.loads(tf.FALLBACK_CAP.read_text())
check("a step writes the cap the workers read", out is not None and cap_file["cap"] == out["cap"] and cap_file["mode"] == "auto"
      and abs(cap_file["B"] - 0.6) < 1e-9 and abs(cap_file["R"] - 0.02) < 1e-3, str(cap_file))
check("its reading was recorded in the usage history", len(tf.USAGE_HISTORY.read_text().splitlines()) == 2)
check("the view's budget row says what it did", "claude weekly 60% left" in (tf.budget_line() or "")
      and "outside cap" in tf.budget_line(), tf.budget_line())
check("a step is taken at most every PACE_EVERY seconds", tf.budget_pace()["at"] == cap_file["at"])
# 40% used in 2 h with only 20% outside: what remains cannot cover what is not outside work until the slot ends
check("budget-short only when the reserve outruns the budget",
      (tf.INCIDENTS / "budget-short-claude.json").exists() == (cap_file["D"] * cap_file["H"] > cap_file["B"]))
tf.budget_short_incident({"D": 0.001, "H": 10, "B": 0.5})
check("…and it clears once that stops being true", not (tf.INCIDENTS / "budget-short-claude.json").exists())
tf.budget_short_incident({"D": 0.1, "H": 10, "B": 0.5})
item = json.loads((tf.INCIDENTS / "budget-short-claude.json").read_text())
check("budget-short says when the budget runs out", item["kind"] == "budget-short" and "runs out in 5 h" in item["detail"], item["detail"])
# ---- the one-time reset the owner claims by hand -----------------------------------------------------------
tf.BUDGET_EXTRA, tf.BUDGET_EXTRA_UNTIL = 1.0, datetime.fromtimestamp(now + 86400).isoformat()
check("an unclaimed reset counts until its date", tf.budget_extra(now) == 1.0 and tf.budget_extra(now + 2 * 86400) == 0.0)


def reading(used, at):
    (w1 / "cache" / "quota-claude.json").write_text(json.dumps({"fetched_at": at, "payload": {
        "seven_day": {"utilization": used, "resets_at": datetime.fromtimestamp(resets).astimezone().isoformat()}}}))


t = time.time()
tf.USAGE_HISTORY.write_text("".join(json.dumps({"at": t - 7200 + 1800 * i, "used": u, "resets": resets}) + "\n"
                                    for i, u in enumerate((92.0, 93.0, 94.0))))
reading(95.0, t - 60)
out = tf.budget_pace(force=True)
check("the step spends the reset as budget", abs(out["B"] - 1.05) < 1e-9 and out["extra"] == 1.0 and out["surplus"] > 0, str(out))
check("the view says what is left and what is still to claim", "5% left + 100% reset to claim" in (tf.budget_line() or ""),
      tf.budget_line())
reset_item = tf.INCIDENTS / "budget-reset-claude.json"
item = json.loads(reset_item.read_text()) if reset_item.exists() else {}
check("nearly spent with the reset unclaimed: the owner is told to claim it",
      item.get("kind") == "budget-reset" and "claim" in item.get("detail", ""), str(item))
tf.note_claimed_reset({"used": 95.0, "resets": t - 100}, t, 1.0)
check("a drop at the window's own reset time is not a claim", not tf.EXTRA_CLAIMED.exists())
# the owner claims: weekly use falls before the window's reset time
reading(1.0, t - 45)
tf.record_usage_history()
check("a drop before the window's reset time is the claim", tf.EXTRA_CLAIMED.exists() and tf.budget_extra(t) == 0.0)
# move the claim back an hour, so the readings after it span the minimum the burn needs
tf.USAGE_HISTORY.write_text("".join(json.dumps({"at": t - a, "used": u, "resets": resets}) + "\n"
                                    for a, u in ((7000, 90.0), (5000, 95.0), (3700, 1.0))))
reading(3.0, t - 30)
out = tf.budget_pace(force=True)
check("after the claim the burn is measured from the claim on", out["R"] is not None and abs(out["R"] - 0.02) < 2e-3, str(out.get("R")))
check("…the reset is no longer counted, and the reminder clears",
      out["extra"] == 0 and abs(out["B"] - 0.97) < 1e-9 and not reset_item.exists(), str(out))

tf.manager_alive = lambda: False
tf.FALLBACK_CAP.write_text(json.dumps({"at": 0}))
check("a stopped fleet takes no step", tf.budget_pace(force=True).get("cap") is None)

print("\nALL OK" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
