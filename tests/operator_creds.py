#!/usr/bin/env python3
"""The credentials row reads the operator's login, also from a worker's round-done hook, offline:
  * in a worker's environment ($TAUCETI_DATA_HOME set) $CLAUDE_CONFIG_DIR and $CODEX_HOME point at
    the worker's own copies, whose refresh token is blank; the row reads ~/.claude and ~/.codex;
  * in the operator's shell those two variables are honoured.
Run: python3 tests/operator_creds.py. Exit 0 = all hold."""

import base64
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="operator-creds-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "TAUCETI_DATA_HOME"):
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


def creds(home: Path, refresh: str, codex_exp: float) -> None:
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "a", "refreshToken": refresh, "expiresAt": int((time.time() + 5 * 3600) * 1000)}}))
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(codex_exp)}).encode()).decode().rstrip("=")
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": f"h.{payload}.s"}}))


op, worker = TMP / "op", TMP / "worker"
creds(op, "r", time.time() + 5 * 86400)
creds(worker, "", time.time() - 3600)  # the worker's copies: no refresh token, a stale Codex token
tf.HOME, tf.IS_MAC = op, False

os.environ.update(CLAUDE_CONFIG_DIR=str(worker / ".claude"), CODEX_HOME=str(worker / ".codex"),
                  TAUCETI_DATA_HOME=str(worker))
claude, codex = tf._claude_operator_expiry(), tf._codex_operator_expiry()
check("in a worker's environment the Claude row reads ~/.claude", claude[0].startswith("claude: token ok"), str(claude))
check("in a worker's environment the Codex row reads ~/.codex", codex[0].startswith("codex: token ok"), str(codex))

del os.environ["TAUCETI_DATA_HOME"]
claude, codex = tf._claude_operator_expiry(), tf._codex_operator_expiry()
check("in the operator's shell $CLAUDE_CONFIG_DIR is honoured", "no refresh token" in claude[0], str(claude))
check("in the operator's shell $CODEX_HOME is honoured", "expired" in codex[0], str(codex))

print("\nALL OK" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
