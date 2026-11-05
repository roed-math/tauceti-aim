#!/usr/bin/env python3
"""`round` runs on the fleet's credentials, offline (the quick start's `init → logins → round`):
  * in a pool with no leases yet, the round takes a free chain, persists the lease, and renews it;
  * a worker that already holds a lease keeps it; a worker the manager is running is refused;
  * in token mode the token is staged and usage is read through the usage login;
  * a Codex round with no model or effort configured passes neither flag (the worker's parser refuses
    a flag without its value).
Run: python3 tests/foreground_round.py. Exit 0 = all hold."""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="foreground-round-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)
tf.IS_MAC, tf.API, tf.TOKEN_FILE, tf.TARGETS = False, False, None, None
tf.LOGINS = TMP / "logins"
tf.LEASES = tf.LOGINS / "leases.json"
tf.TOKEN_DIR, tf.USAGE_LOGIN = tf.LOGINS / "token", tf.LOGINS / "usage"
tf.GATE_DIR = TMP / "gate"
tf.WORKTREE = TMP / "worker"
tf.worker_status = lambda: status
status: dict = {}

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def login(name):
    d = tf.LOGINS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "r",
                                                                        "expiresAt": int((time.time() + 5 * 3600) * 1000)}}))


runs = []


class Done:
    returncode = 0


real_run = tf.subprocess.run


def fake_run(argv, **kw):
    if "tauceti_worker" not in argv:  # the token-staging child runs for real
        return real_run(argv, **kw)
    runs.append((argv, kw.get("env") or {}))
    return Done()


tf.subprocess.run = fake_run


def round_(**over):
    runs.clear()
    a = argparse.Namespace(**{"only": "roadmap", "agent": "claude", "worker_id": None, "sandbox": "none",
                              "mutations_per_hour": 30, "reads_per_hour": 600, "claude_model": "opus",
                              "claude_effort": "high", "codex_model": "", "codex_effort": "", **over})
    try:
        rc = tf.cmd_round(a)
    except SystemExit as e:
        return e.code, None, None
    argv, env = next((r for r in runs if "work" in r[0]), (None, None))
    return rc, argv, env


login("claude-1")
login("claude-2")
rc, argv, env = round_(worker_id="demo-c1")
check("a first round takes a pool chain", env and env.get("CLAUDE_CONFIG_DIR") == str(tf.LOGINS / "claude-1"), str(env and env.get("CLAUDE_CONFIG_DIR")))
check("...persists the lease, so the fleet and the refresher leave it alone", tf.read_leases() == {"demo-c1": "claude-1"})
check("...and renews it itself", env.get("TAUCETI_AUTO_REFRESH") == "1")
tf.LEASES.write_text(json.dumps({"demo-x1": "claude-1", "demo-c1": "claude-2"}))
_, _, env = round_(worker_id="demo-c1")
check("a worker with a lease keeps it", env.get("CLAUDE_CONFIG_DIR") == str(tf.LOGINS / "claude-2"))
status = {"demo-c1": {"state": "running", "heartbeat_at": time.time()}}
rc, argv, _ = round_(worker_id="demo-c1")
check("a worker the manager is running is refused", argv is None and rc not in (0, None))
status = {}

tf.TOKEN_FILE = TMP / "token"
tf.TOKEN_FILE.write_text("sk-ant-oat01-test\n")
tf.TOKEN_FILE.chmod(0o600)
login("usage")
_, _, env = round_(worker_id="demo-c2")
check("in token mode the token is staged", (tf.TOKEN_DIR / ".credentials.json").is_file()
      and env.get("CLAUDE_CONFIG_DIR") == str(tf.TOKEN_DIR))
check("...and usage is read through the usage login",
      env.get("TAUCETI_CLAUDE_USAGE_CREDENTIALS") == str(tf.USAGE_LOGIN / ".credentials.json"))

_, argv, _ = round_(worker_id="demo-c3", agent="codex")
check("a Codex round without a model passes neither flag", "--author-model" not in argv and "--author-effort" not in argv, str(argv))
tf.TOKEN_FILE = None

sys.exit(1 if fails else 0)
