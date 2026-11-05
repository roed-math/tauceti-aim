#!/usr/bin/env python3
"""Login-chain leases stay exclusive while the fleet changes shape, offline:
  * a reshape that disables a worker mid-round and adds another leaves the busy worker on its chain
    (write_config keeps its old block) and gives the new worker a different one;
  * once that worker is between rounds the chain is released as before;
  * the free-chain refresher never counts a busy worker's chain as free.
Run: python3 tests/lease_exclusive.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lease-exclusive-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)
tf.IS_MAC, tf.API, tf.TOKEN_FILE = False, False, None
tf.LOGINS = TMP / "logins"
tf.LEASES = tf.LOGINS / "leases.json"
tf.CONFIG = TMP / "fleet" / "workers.toml"

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


for n in (1, 2, 3):
    d = tf.LOGINS / f"claude-{n}"
    d.mkdir(parents=True)
    (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "r",
                                                                        "expiresAt": int((time.time() + 5 * 3600) * 1000)}}))
tf.LEASES.write_text(json.dumps({"demo-c1": "claude-1"}))


def specs(c1_enabled: bool, fix2: bool):
    out = [{"id": "demo-c1", "agent": "claude", "enabled": c1_enabled, "env": {}}]
    if fix2:
        out.append({"id": "demo-fix2", "agent": "claude", "enabled": True, "env": {}})
    return out


def chains_in_use(text: str) -> dict[str, str]:
    """Enabled worker → its CLAUDE_CONFIG_DIR, from the config the manager would run."""
    out = {}
    for wid, blk in tf.parse_blocks(text).items():
        if "enabled = true" in blk and (m := re.search(r'CLAUDE_CONFIG_DIR = "([^"]+)"', blk)):
            out[wid] = Path(m.group(1)).name
    return out


tf.write_config(tf.lease_logins(specs(True, False)), {})
running = {"demo-c1": {"state": "running", "heartbeat_at": time.time()}}
busy = tf.busy_workers(running)
check("the reshape sees the author as busy", busy == {"demo-c1"})
tf.write_config(tf.lease_logins(specs(False, True), busy=busy), running)
use = chains_in_use(tf.CONFIG.read_text())
check("a busy worker disabled by the reshape keeps running on its chain", use.get("demo-c1") == "claude-1", str(use))
check("...and the new worker gets another one", use.get("demo-fix2") not in (None, "claude-1"), str(use))
check("no chain is held by two running workers", len(set(use.values())) == len(use), str(use))
check("the busy worker's chain is not free for the refresher", "claude-1" in tf.read_leases().values())

idle = {"demo-c1": {"state": "idle"}}
tf.write_config(tf.lease_logins(specs(False, True), busy=tf.busy_workers(idle)), idle)
check("between rounds the disabled worker's chain is released",
      "demo-c1" not in tf.read_leases() and list(chains_in_use(tf.CONFIG.read_text())) == ["demo-fix2"])

sys.exit(1 if fails else 0)
