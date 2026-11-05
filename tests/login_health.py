#!/usr/bin/env python3
"""A login chain whose renewal keeps failing is reported and routed around, offline:
  * chain_failing_since reads the refresh markers: a renewal tried after the token expired, with no
    success and no sign-in since, past the grace; a healthy chain, an idle expired one, a fresh sign-in
    and a failure inside the grace are not failing;
  * assign_leases moves a worker off a failing chain onto a healthy free one, keeps it there when
    none is free, and hands out healthy chains before failing ones;
  * login_incidents files a `login` item for a failing chain, drops it once the chain is signed in
    again, and an acknowledged item stays off until the credential changes.
Run: python3 tests/login_health.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="login-health-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)
tf.IS_MAC, tf.API = False, False
tf.LOGINS = TMP / "logins"
tf.LEASES = tf.LOGINS / "leases.json"
tf.INCIDENTS = TMP / "incidents"
tf.ACKED = tf.INCIDENTS / "acked"

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


NOW = time.time()


def chain(name, expires, attempt=None, success=None, written=None):
    d = tf.LOGINS / name
    d.mkdir(parents=True, exist_ok=True)
    cred = d / ".credentials.json"
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "r",
                                                  "expiresAt": int(expires * 1000)}}))
    for marker, at in (("last-attempt", attempt), ("last-success", success)):
        p = d / f".credentials.json.refresh.{marker}"
        if at is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(json.dumps({"at": at}))
    t = written if written is not None else min(x for x in (attempt, success, NOW - 7200) if x is not None) - 1
    os.utime(cred, (t, t))
    return d


H = 3600
healthy = chain("claude-1", NOW + 5 * H, attempt=NOW - 3 * H, success=NOW - 3 * H + 2)
idle = chain("claude-2", NOW - 4 * H, attempt=NOW - 13 * H, success=NOW - 13 * H + 2)
failing = chain("claude-3", NOW - 4 * H, attempt=NOW - 10 * 60, success=NOW - 13 * H)
signed_in = chain("claude-4", NOW - H, attempt=NOW - 2 * H, success=NOW - 20 * H, written=NOW - 1.5 * H)
fresh_fail = chain("claude-5", NOW - 10 * 60, attempt=NOW - 5 * 60, success=NOW - 9 * H)

check("a renewed chain is healthy", tf.chain_failing_since(healthy) is None)
check("a token that expired while nobody needed it is not failing", tf.chain_failing_since(idle) is None)
since = tf.chain_failing_since(failing)
check("a renewal tried after expiry, with no success since, is failing", since is not None and abs(since - (NOW - 4 * H)) < 2,
      str(since))
check("a sign-in after the failed renewal clears it", tf.chain_failing_since(signed_in) is None)
check("a failure inside the grace is not reported yet", tf.chain_failing_since(fresh_fail) is None)

specs = [{"id": "gq2-c1", "agent": "claude", "enabled": True}, {"id": "gq2-c2", "agent": "claude", "enabled": True},
         {"id": "gq2-x1", "agent": "auto", "enabled": True}]
tf.LEASES.write_text(json.dumps({"gq2-c1": "claude-1", "gq2-c2": "claude-3"}))
leases = tf.assign_leases(specs)
check("a worker on a failing chain moves to a healthy free one", leases["gq2-c2"] == "claude-2", str(leases))
check("a worker on a healthy chain keeps it", leases["gq2-c1"] == "claude-1", str(leases))
check("new leases take healthy chains before failing ones", leases["gq2-x1"] == "claude-4", str(leases))
check("the failing chain is left free", "claude-3" not in leases.values(), str(leases))

for name in ("claude-2", "claude-4", "claude-5"):
    for f in (tf.LOGINS / name).iterdir():
        f.unlink()
    (tf.LOGINS / name).rmdir()
leases = tf.assign_leases(specs)
check("with no healthy chain free, the worker keeps its failing one", leases["gq2-c2"] == "claude-3", str(leases))

tf.LEASES.write_text(json.dumps(leases))
tf.login_incidents()
item = tf.INCIDENTS / "login-claude-3.json"
rec = json.loads(item.read_text()) if item.is_file() else {}
check("a failing chain files a login item", rec.get("kind") == "login" and rec.get("key") == "claude-3"
      and "CLAUDE_CONFIG_DIR=" in rec.get("detail", "") and rec.get("worker") == "gq2-c2", str(rec))
check("a healthy chain files none", not (tf.INCIDENTS / "login-claude-1.json").exists())
check("the item reaches the attention list", any(d.get("key") == "claude-3" for d in tf.attention_items()))

tf.ACKED.mkdir(parents=True, exist_ok=True)
item.rename(tf.ACKED / item.name)
tf.login_incidents()
check("an acknowledged item stays off while the credential is unchanged", not item.exists())

chain("claude-3", NOW + 8 * H, attempt=NOW - 10 * 60, success=NOW - 13 * H, written=NOW - 60)
tf.login_incidents()
check("a sign-in clears the chain", tf.chain_failing_since(tf.LOGINS / "claude-3") is None and not item.exists())

chain("claude-3", NOW - H, attempt=NOW - 60, success=NOW - 13 * H, written=NOW - 2 * H + 5)
tf.login_incidents()
check("a new failure after the acknowledged one is filed again", item.is_file())

# A worker's credential pin follows its lease, whatever switched the lease.
tf.WORKTREE = TMP / "worker"
for name in ("claude-6", "claude-9"):
    chain(name, NOW + 5 * H, attempt=NOW - H, success=NOW - H + 2)
tf.LEASES.write_text(json.dumps({"gq2-rev2": "claude-9", "gq2-fix2": "claude-6", "gq2-rev1": "claude-9"}))


def pin(wid, source):
    iso = tf.WORKTREE / "state" / wid / "home" / ".claude"
    iso.mkdir(parents=True, exist_ok=True)
    (iso / ".credentials.json").write_text("{}")
    (iso / ".tauceti-creds-source").write_text(str(tf.LOGINS / source))
    return iso


stale, kept, busy = pin("gq2-rev2", "claude-6"), pin("gq2-fix2", "claude-6"), pin("gq2-rev1", "claude-6")
notes = tf.reset_stale_mirrors(["gq2-rev2", "gq2-fix2", "gq2-rev1"],
                               {"gq2-rev1": {"state": "running", "heartbeat_at": time.time()}})
check("a pin its lease no longer names is reset between rounds",
      not (stale / ".tauceti-creds-source").exists() and not (stale / ".credentials.json").exists(), str(notes))
check("a pin matching its lease is left alone", (kept / ".tauceti-creds-source").is_file())
check("a worker mid-round keeps its pin until the round ends", (busy / ".tauceti-creds-source").is_file())

sys.exit(1 if fails else 0)
