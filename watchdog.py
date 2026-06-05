#!/usr/bin/env python3
import os, sys, time, json

PANEL = "/www/server/panel"
CM = PANEL + "/class/public/common.py"

def log(msg):
    t = time.strftime("%H:%M:%S")
    print("[watchdog " + t + "] " + msg, flush=True)

def fix_bmac():
    for f in os.listdir("/tmp"):
        fp = "/tmp/" + f
        if f.startswith("bmac_") and not f.endswith(".time"):
            val = open(fp).read().strip()
            if val != "0":
                try:
                    os.chmod(fp, 0o644)
                    open(fp, "w").write("0")
                    os.chmod(fp, 0o444)
                    log("Fixed " + f + ": " + val + " -> 0")
                except Exception as e:
                    log("ERROR bmac: " + str(e))

def fix_sentinels():
    for f in [PANEL + "/data/.is_pro.pl", PANEL + "/data/panel_pro.pl"]:
        if not os.path.exists(f):
            open(f, "w").write("true" if "panel_pro" in f else "")
            log("Created " + os.path.basename(f))

def fix_common():
    try:
        c = open(CM).read()
        changed = False
        if "pro = 0  # Force Lifetime" not in c:
            lines = c.split("\n")
            for i, line in enumerate(lines):
                if line.strip() == "pro = int(tmp)":
                    lines.insert(i+1, "        pro = 0  # Force Lifetime (patched)")
                    changed = True
                    break
            c = "\n".join(lines)
        if "315360000" not in c:
            c = c.replace("+ 86400", "+ 315360000  # 10 years (patched)")
            changed = True
        if changed:
            open(CM, "w").write(c)
            log("Re-patched common.py")
    except Exception as e:
        log("ERROR common: " + str(e))

def fix_pycache():
    for root, dirs, files in os.walk(os.path.join(PANEL, "class")):
        if "__pycache__" in dirs:
            pc = os.path.join(root, "__pycache__")
            for f in os.listdir(pc):
                fp = os.path.join(pc, f)
                if f.endswith(".pyc"):
                    py = os.path.join(root, f[:-1])
                    if os.path.exists(py) and os.path.getmtime(py) > os.path.getmtime(fp):
                        os.remove(fp)
                        log("Cleared stale pyc: " + fp)

log("Watchdog started")
while True:
    try:
        fix_sentinels()
        fix_bmac()
        fix_common()
        fix_pycache()
    except Exception as e:
        log("LOOP: " + str(e))
    time.sleep(60)
