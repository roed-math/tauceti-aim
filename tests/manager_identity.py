#!/usr/bin/env python3
"""Which manager is this fleet's, offline:
  * the manager is alive only when it answers a ping with this fleet's config: a socket file a dead
    manager left behind is not alive (so an always-on fleet is revived), and neither is another fleet's;
  * `down` refuses to stop a manager that answers with another fleet's config;
  * a fleet in a home other than the default gets its own runtime directory and schedule unit; the
    default home keeps the ones that predate homes;
  * no periodic stage starts while the fleet is stopped or its manager is gone.
Run: python3 tests/manager_identity.py. Exit 0 = all hold."""

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="mgr-id-", dir="/tmp"))  # a Unix socket path must stay short


def load(home: Path | None, user_home: Path):
    env = {k: v for k, v in os.environ.items() if k not in ("TAUCETI_FLEET_HOME", "TAUCETI_RUNTIME_DIR")}
    env["HOME"] = str(user_home)
    if home is not None:
        env["TAUCETI_FLEET_HOME"] = str(home)
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        loader = importlib.machinery.SourceFileLoader("tf", str(HERE / "bin" / "tauceti-aim"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("tf", loader))
        loader.exec_module(mod)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return mod


fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


(TMP / "u").mkdir()
default = load(None, TMP / "u")
a = load(TMP / "a", TMP / "u")
b = load(TMP / "b", TMP / "u")
check("the default home keeps the runtime directory and unit that predate homes",
      default.RUNTIME_DIR is None and default.SCHEDULE_UNIT == "tauceti-fleet-schedule")
check("another home gets its own", a.RUNTIME_DIR is not None and a.RUNTIME_DIR != b.RUNTIME_DIR
      and a.SCHEDULE_UNIT != b.SCHEDULE_UNIT != "tauceti-fleet-schedule")
check("...and hands it to the worker", a.worker_env().get("TAUCETI_RUNTIME_DIR") == str(a.RUNTIME_DIR))


def serve(path: Path, config: str):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(4)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.recv(4096)
            conn.sendall(json.dumps({"ok": True, "pid": 1, "config": config}).encode() + b"\n")
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return srv


a.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
sock = a.RUNTIME_DIR / "manager.sock"
srv = serve(sock, str(a.CONFIG))
check("a manager answering with this fleet's config is alive", a.manager_alive())
srv.close()
check("a socket file a dead manager left behind is not", sock.exists() and not a.manager_alive())
sock.unlink()
srv = serve(sock, str(b.CONFIG))
check("another fleet's manager is not this fleet's", not a.manager_alive())
try:
    a.cmd_down(type("A", (), {"drain": False, "leave_workers": False})())
    refused = False
except SystemExit:
    refused = True
check("down refuses to stop another fleet's manager", refused and not a.STOPPED.exists())
srv.close()
sock.unlink()

started = []
a.run_periodic = lambda stage, quiet=True, force=False: started.append(stage) or None
a.manager_alive = lambda: True
a.STOPPED.parent.mkdir(parents=True, exist_ok=True)
a.STOPPED.write_text("x")
a.ensure_periodic(quiet=True)
check("no periodic stage starts after down", started == [])
a.STOPPED.unlink()
a.manager_alive = lambda: False
a.ensure_periodic(quiet=True)
check("...nor while the manager is gone", started == [])
a.manager_alive = lambda: True
a.ensure_periodic(quiet=True)
check("...and they start again while the fleet runs", bool(started))

sys.exit(1 if fails else 0)
