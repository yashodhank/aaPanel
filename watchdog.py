#!/usr/bin/env python3
"""
aaPanel license watchdog — auto-repair on revert.

Runs as a singleton service (systemd unit aapanel-guard, or nohup with a PID
file). Every cycle it:
  * keeps /tmp/bmac_* license tokens pinned to "0" (it OWNS these files — it does
    NOT make them immutable, which would deadlock its own writes),
  * recreates the Pro sentinel files if something deletes them,
  * re-applies the source patches via aaPanel_harden.py IF a panel self-upgrade
    reverted them — delegating to the one canonical patcher instead of
    re-implementing edits here (so logic can't drift),
  * bounces the panel only when it actually had to re-patch,
  * caps its own log so it can't fill the disk.

All edits are idempotent and fail loud: if a required anchor goes missing (a new
aaPanel version moved it), that is logged prominently rather than silently
leaving the panel reverted.

Usage:  python3 watchdog.py [/www/server/panel]
"""

import contextlib
import importlib.util
import io
import os
import sys
import time

PANEL = sys.argv[1] if len(sys.argv) > 1 else "/www/server/panel"
HARDEN = os.path.join(PANEL, "aaPanel_harden.py")
PIDFILE = os.path.join(PANEL, "data", ".aap_watchdog.pid")
LOGFILE = os.path.join(PANEL, "watchdog.log")
LOG_CAP = 2 * 1024 * 1024  # 2 MiB
INTERVAL = 60


def log(msg):
    print("[watchdog %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def already_running():
    """True if another live watchdog holds the PID file."""
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def write_pidfile():
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        log("could not write pidfile: %s" % e)


def load_harden():
    try:
        spec = importlib.util.spec_from_file_location("aap_harden", HARDEN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        log("cannot load aaPanel_harden.py: %s" % e)
        return None


def fix_bmac():
    """Pin every /tmp/bmac_* token to 0. No chattr — we must stay able to write."""
    try:
        entries = os.listdir("/tmp")
    except OSError:
        return
    for name in entries:
        if not name.startswith("bmac_") or name.endswith(".time"):
            continue
        fp = os.path.join("/tmp", name)
        try:
            with open(fp) as f:
                val = f.read().strip()
        except OSError:
            continue
        if val != "0":
            try:
                os.chmod(fp, 0o644)
                with open(fp, "w") as f:
                    f.write("0")
                log("bmac %s: %s -> 0" % (name, val))
            except OSError as e:
                log("bmac %s write failed: %s" % (name, e))


def fix_sentinels():
    for name, content in ((".is_pro.pl", ""), ("panel_pro.pl", "true")):
        p = os.path.join(PANEL, "data", name)
        if not os.path.exists(p):
            try:
                with open(p, "w") as f:
                    f.write(content)
                log("recreated sentinel %s" % name)
            except OSError as e:
                log("sentinel %s: %s" % (name, e))


def repatch(harden):
    """Re-apply source patches if reverted. Returns True if anything changed."""
    if harden is None:
        return False
    try:
        hd = harden.Hardener(PANEL)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = hd.apply()
        out = buf.getvalue()
        if not ok:
            log("RE-PATCH FAILED — a REQUIRED anchor is missing (new aaPanel "
                "version?). Manual review needed:\n" + out)
            return False
        applied = [ln.strip() for ln in out.splitlines()
                   if "-> " in ln and "already" not in ln and "no change" not in ln]
        if applied:
            log("re-patched %d site(s) after revert: %s"
                % (len(applied), ", ".join(a.split()[1] for a in applied if len(a.split()) > 1)))
            return True
        return False
    except Exception as e:
        log("re-patch error: %s" % e)
        return False


def restart_panel():
    for cmd in ("systemctl restart bt", "/etc/init.d/bt restart"):
        if os.system(cmd + " >/dev/null 2>&1") == 0:
            log("panel restarted (%s)" % cmd.split()[0])
            return
    log("could not restart panel automatically")


def cap_log():
    try:
        if os.path.exists(LOGFILE) and os.path.getsize(LOGFILE) > LOG_CAP:
            with open(LOGFILE, "rb") as f:
                f.seek(-LOG_CAP // 2, os.SEEK_END)
                tail = f.read()
            with open(LOGFILE, "wb") as f:
                f.write(b"[watchdog] log truncated\n" + tail)
    except OSError:
        pass


def main():
    if already_running():
        log("another watchdog is already running; exiting")
        return
    write_pidfile()
    harden = load_harden()
    log("watchdog started (panel=%s pid=%d)" % (PANEL, os.getpid()))
    while True:
        try:
            fix_sentinels()
            fix_bmac()
            if repatch(harden):
                restart_panel()
            cap_log()
        except Exception as e:
            log("loop error: %s" % e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
