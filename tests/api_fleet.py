#!/usr/bin/env python3
"""An API-billed fleet with no target list (`[claude] billing = "api"`, `fleet.targets = "none"`), offline:
  * every worker is pinned to Claude with the worker's API flags, and none carries a pace, OAuth
    refresh, a login chain or a target list;
  * the key file is judged by its metadata: missing, open to others, or fine;
  * the periodic rounds lose the curator and the subscription settings, and pass the API flags;
  * `schedule.active = "always"` is the whole week, and the tick restarts a fleet whose manager has
    gone unless `down` stopped it;
  * a subscription fleet's workers are unchanged.
Run: python3 tests/api_fleet.py. Exit 0 = all hold."""

import argparse
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="api-fleet-"))
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def load(name: str, toml: str):
    home = TMP / name
    home.mkdir()
    (home / "fleet.toml").write_text(toml)
    os.environ["TAUCETI_FLEET_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "bin" / "tauceti-aim"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod, home


def shape(**kw):
    base = dict(claude=3, codex=2, fixers=3, reviewers=2, sandbox="bubble", pace="0:100,100:100", pilot=False,
                mutations_per_hour=30, reads_per_hour=2000, claude_model="claude-opus-5-5", claude_effort="high",
                codex_model="", codex_effort="")
    return argparse.Namespace(**{**base, **kw})


key = TMP / "anthropic.key"
api, home = load("api", f'[fleet]\nname = "tsa"\ngithub_login = "acct"\ntargets = "none"\n'
                        f'[claude]\nbilling = "api"\napi_key_file = "{key}"\n[schedule]\nactive = "always"\n')

check("no key file is a problem", "no API key file" in api.api_key_problem())
key.write_text("sk-ant-api03-" + "x" * 40 + "\n")
key.chmod(0o644)
check("a key others can read is a problem", "chmod 600" in api.api_key_problem(), api.api_key_problem())
key.chmod(0o600)
check("a private key file is fine", api.api_key_problem() == "", api.api_key_problem())

specs = api.lease_logins(api.fleet_specs(shape()), persist=False)
ids = [s["id"] for s in specs]
check("no Codex authors under API billing", not any("-x" in i for i in ids), str(ids))
check("every worker is pinned to Claude", {s["agent"] for s in specs} == {"claude"}, str({s["agent"] for s in specs}))
check("…with the API billing fields", all(s.get("claude_billing") == "api" and s.get("budget") is True
                                         and s.get("anthropic_api_key_file") == str(key) for s in specs))
check("…and no pace, OAuth refresh, login chain or target list",
      not any("pace" in s or "auto_refresh" in s or "roadmap_targets" in s or "CLAUDE_CONFIG_DIR" in s["env"]
              or any(k.startswith("TAUCETI_PACE") for k in s["env"]) for s in specs))
api.MAX_OPEN_PRS_SETTING = 100
api.worker_knows = lambda field: field == "max_open_prs"
check("a worker that takes max_open_prs gets the setting as that field",
      all(s.get("max_open_prs") == 100 for s in api.fleet_specs(shape())))
api.worker_knows = lambda field: False
check("…and an older one only the environment", not any("max_open_prs" in s for s in api.fleet_specs(shape())))
api.MAX_OPEN_PRS_SETTING = None
check("an integer field is written as one", "\nmax_open_prs = 100\n" in api.render_block({"id": "x", "max_open_prs": 100}))
toml = api.render_toml(specs)
check("the config says budget = true, unquoted", "\nbudget = true\n" in toml and 'claude_billing = "api"' in toml)
api._gh_status = lambda: ("gh: acct", "green")  # `gh auth status` would ask GitHub
check("the credentials row names the key file, not a login", "API key file" in api.credential_lines()[0][0])

check("no curator without a list", not api.periodic_enabled("curate") and api.periodic_enabled("decide"))
env = api.periodic_env()
check("periodic rounds carry no list, refresh or pace",
      not ({"TAUCETI_ROADMAP_TARGETS", "TAUCETI_AUTO_REFRESH", "TAUCETI_PACE"} & set(env)), str(sorted(env)))
check("…and pass the API flags", api.api_flags() == ["--claude-billing", "api", "--budget", "--anthropic-api-key-file", str(key)])
check("the target-list row says there is none", api.target_list_summary().startswith("none"))

slots = api.parse_schedule("always")
check("always is active at any hour", all(api.schedule_active(slots, datetime(2026, 10, d, h)) for d in (12, 15, 18) for h in (0, 13, 23)))
(home / "workers.toml").write_text("version = 1\n")
api.manager_alive = lambda: False
check("an always-on fleet whose manager is gone is revived", api.revive_always_on("always", "active", "active"))
api.STOPPED.write_text("x\n")
check("…unless `down` stopped it", not api.revive_always_on("always", "active", "active"))
api.STOPPED.unlink()
check("a weekly slot is not revived between its edges", not api.revive_always_on("Mon 23:00 -> Thu 07:00", "active", "active"))

sub, _ = load("sub", '[fleet]\nname = "tss"\ngithub_login = "acct"\ntargets = "/nonexistent/list.md"\n')
specs = sub.fleet_specs(shape())
check("a subscription fleet keeps its `auto` workers", {"auto", "claude"} == {s["agent"] for s in specs})
check("…its pace, OAuth refresh and target list",
      all(s.get("pace") and s.get("auto_refresh") and s.get("roadmap_targets") == "/nonexistent/list.md" for s in specs))
check("…and no API fields", not any("claude_billing" in s for s in specs) and sub.api_flags() == [])
check("…and tends every PR of its login", not any("TAUCETI_OWN_BRANCHES" in s["env"] for s in specs))

shared, _ = load("shared", '[fleet]\nname = "tsh"\ngithub_login = "acct"\ntargets = "none"\nown_branches_only = true\n')
check("own_branches_only hands every worker its fleet's branch pattern",
      {s["env"].get("TAUCETI_OWN_BRANCHES") for s in shared.fleet_specs(shape())} == {"-tsh-"})

print("\nALL OK" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
