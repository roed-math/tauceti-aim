#!/usr/bin/env python3
"""Token mode (`[claude] token_file`), offline:
  * the setup-token is staged as logins/token/.credentials.json with no refresh token, rewritten only
    when it changes, and refused when others can read the token file;
  * nothing is leased, every worker seeds from the token and reads usage through logins/usage, and
    neither directory counts as a pool chain;
  * the attention list asks for a sign-in when the usage login is missing or stops renewing within
    USAGE_WARN_S, and for a new token within TOKEN_WARN_S of its year; the pool chains' old items go.
Run: python3 tests/token_mode.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="token-mode-"))
(TMP / "fleet").mkdir()
os.environ["TAUCETI_FLEET_HOME"] = str(TMP / "fleet")
loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
spec = importlib.util.spec_from_loader("tf", loader)
tf = importlib.util.module_from_spec(spec)
loader.exec_module(tf)
tf.IS_MAC, tf.API = False, False
tf.LOGINS = TMP / "logins"
tf.LEASES = tf.LOGINS / "leases.json"
tf.TOKEN_DIR, tf.USAGE_LOGIN = tf.LOGINS / "token", tf.LOGINS / "usage"
tf.INCIDENTS = TMP / "incidents"
tf.ACKED = tf.INCIDENTS / "acked"
tf.TOKEN_FILE = TMP / "claude-setup-token"

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


NOW, DAY = time.time(), 86400
tf.TOKEN_FILE.write_text("sk-ant-oat01-test\n")
tf.TOKEN_FILE.chmod(0o600)

staged = tf.TOKEN_DIR / ".credentials.json"
check("a readable token stages", tf.stage_setup_token() is None)
blk = json.loads(staged.read_text())["claudeAiOauth"] if staged.is_file() else {}
check("...as an access token with no refresh token", blk.get("accessToken") == "sk-ant-oat01-test"
      and "refreshToken" not in blk and blk.get("scopes") == ["user:inference"], str(blk))
check("...mode 600", staged.is_file() and staged.stat().st_mode & 0o777 == 0o600)
check("...expiring a year after the token was saved",
      abs(blk.get("expiresAt", 0) / 1000 - tf.TOKEN_FILE.stat().st_mtime - 365 * DAY) < 2, str(blk.get("expiresAt")))
os.utime(staged, (NOW - 100, NOW - 100))
tf.stage_setup_token()
check("an unchanged token is not rewritten", abs(staged.stat().st_mtime - (NOW - 100)) < 1)

tf.TOKEN_FILE.chmod(0o644)
check("a token others can read is refused", "chmod 600" in (tf.stage_setup_token() or ""))
tf.TOKEN_FILE.chmod(0o600)


def login(name, refresh_ends=None):
    d = tf.LOGINS / name
    d.mkdir(parents=True, exist_ok=True)
    blk = {"accessToken": "a", "refreshToken": "r", "expiresAt": int((NOW + 5 * 3600) * 1000)}
    if refresh_ends is not None:
        blk["refreshTokenExpiresAt"] = int(refresh_ends * 1000)
    (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": blk}))
    return d


login("claude-1")
check("the token and usage directories are not pool chains",
      [d.name for d in tf.login_slots()] == ["claude-1"], str(tf.login_slots()))
specs = [{"id": "gq2-c1", "agent": "claude", "enabled": True, "env": {"CLAUDE_CONFIG_DIR": "/old"}},
         {"id": "gq2-x1", "agent": "auto", "enabled": True, "env": {}}]
leases = tf.assign_leases(specs)
check("nothing is leased", leases == {}, str(leases))
env = tf.with_logins(specs, leases)[0]["env"]
check("every worker seeds from the token and reads usage through the usage login",
      env == {"CLAUDE_CONFIG_DIR": str(tf.TOKEN_DIR),
              "TAUCETI_CLAUDE_USAGE_CREDENTIALS": str(tf.USAGE_LOGIN / ".credentials.json")}, str(env))

tf.INCIDENTS.mkdir(parents=True)
(tf.INCIDENTS / "login-claude-1.json").write_text("{}")
tf.login_incidents()
rec = json.loads((tf.INCIDENTS / "login-usage.json").read_text()) if (tf.INCIDENTS / "login-usage.json").is_file() else {}
check("a missing usage login files an item with the sign-in command",
      f"CLAUDE_CONFIG_DIR={tf.USAGE_LOGIN} claude auth login" in rec.get("detail", ""), str(rec))
check("the pool chains' old items are dropped", not (tf.INCIDENTS / "login-claude-1.json").exists())

login("usage", refresh_ends=NOW + 20 * DAY)
tf.login_incidents()
check("a usage login renewing for weeks needs nothing", not (tf.INCIDENTS / "login-usage.json").exists())

login("usage", refresh_ends=NOW + 2 * DAY)
tf.login_incidents()
rec = json.loads((tf.INCIDENTS / "login-usage.json").read_text()) if (tf.INCIDENTS / "login-usage.json").is_file() else {}
check("a usage login that stops renewing in two days is flagged now", "stops renewing" in rec.get("detail", ""), str(rec))
item = tf.INCIDENTS / "login-usage.json"
tf.ACKED.mkdir(parents=True, exist_ok=True)
item.rename(tf.ACKED / item.name)
tf.login_incidents()
check("an acknowledged warning stays off until the login changes", not item.exists())

old = NOW - 340 * DAY
os.utime(tf.TOKEN_FILE, (old, old))
tf.login_incidents()
rec = json.loads((tf.INCIDENTS / "login-token.json").read_text()) if (tf.INCIDENTS / "login-token.json").is_file() else {}
check("a token within a month of its year asks for a new one", "claude setup-token" in rec.get("detail", ""), str(rec))
text, style = tf._token_line()
check("the view line names what needs a sign-in", style == "red" and "token" in text, text)

sys.exit(1 if fails else 0)
