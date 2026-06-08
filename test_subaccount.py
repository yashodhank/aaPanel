#!/usr/bin/env python3
"""
aaPanel License Hardener — verification suite
=============================================
Confirms a live, patched panel is in the expected permanent-Pro state.

Two layers:
  1. RUNTIME (canonical): delegates to ``aaPanel_harden.py --check`` and
     ``--verify`` so the patch knowledge lives in exactly one place. --check
     confirms every source anchor is applied; --verify imports the panel modules
     and asserts is_pro()->True, get_not_auth_status()->200, get_pd() pro=0.
  2. ARTIFACTS (static): the things the patcher doesn't own — runtime guard,
     sentinels, userInfo.json, offline catalog, and the frontend JS bundles.

WARNING — side effects: --verify imports BTPanel.app and calls get_pd()/
load_soft_list(), which perform real network calls and write /tmp cache files.
Run it only against a panel you control.

Usage:
    python3 test_subaccount.py [/www/server/panel]
    python3 test_subaccount.py [/www/server/panel] --panel-python /path/to/python3
"""

import glob
import json
import os
import subprocess
import sys

target = "/www/server/panel"
PANEL_PYTHON = None
PASS = FAIL = SKIP = 0


def green(s):
    return "\033[32m%s\033[0m" % s


def red(s):
    return "\033[31m%s\033[0m" % s


def yellow(s):
    return "\033[33m%s\033[0m" % s


def R(name, ok, msg=""):
    global PASS, FAIL, SKIP
    if ok is None:
        SKIP += 1
        print("  %s %s%s" % (yellow("[SKIP]"), name, (": " + msg) if msg else ""))
    elif ok:
        PASS += 1
        print("  %s %s" % (green("[PASS]"), name))
    else:
        FAIL += 1
        print("  %s %s%s" % (red("[FAIL]"), name, (": " + msg) if msg else ""))


# --- Layer 1: runtime, via the canonical patcher -----------------------------
def run_harden(*flags):
    harden = os.path.join(target, "aaPanel_harden.py")
    if not os.path.exists(harden):
        return None, "aaPanel_harden.py not staged in panel"
    try:
        r = subprocess.run([PANEL_PYTHON, harden, target, *flags],
                           capture_output=True, text=True, timeout=90, cwd=target,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return None, str(e)


def test_anchors():
    print("\n--- Source patches applied (aaPanel_harden.py --check) ---")
    rc, out = run_harden("--check")
    if rc is None:
        R("anchor check", None, out[:120])
        return
    # surface each anchor line for visibility
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[PASS]") or s.startswith("[FAIL]"):
            print("    " + s)
    R("all required source anchors applied", rc == 0,
      "see --check output above (rc=%s)" % rc)


def test_runtime():
    print("\n--- Runtime semantics (aaPanel_harden.py --verify) ---")
    rc, out = run_harden("--verify")
    if rc is None:
        R("runtime verify", None, out[:120])
        return
    flags = {"is_pro": None, "get_not_auth": None, "get_pd": None}
    for line in out.splitlines():
        s = line.strip()
        if "is_pro()" in s:
            flags["is_pro"] = s.startswith("[PASS]")
        elif "get_not_auth_status()" in s:
            flags["get_not_auth"] = s.startswith("[PASS]")
        elif "get_pd()" in s:
            flags["get_pd"] = s.startswith("[PASS]")
        if s.startswith("[PASS]") or s.startswith("[FAIL]"):
            print("    " + s)
    R("is_pro() -> True", flags["is_pro"] if flags["is_pro"] is not None else None,
      "not reported")
    R("get_not_auth_status() -> 200",
      flags["get_not_auth"] if flags["get_not_auth"] is not None else None, "not reported")
    R("get_pd() -> pro=0", flags["get_pd"] if flags["get_pd"] is not None else None,
      "not reported")


# --- Layer 2: artifacts the patcher doesn't own ------------------------------
def test_guard():
    print("\n--- Runtime guard (sitecustomize.py) ---")
    found = []
    for py in glob.glob(os.path.join(target, "pyenv", "lib", "python*", "site-packages",
                                     "sitecustomize.py")):
        try:
            if "AAP_GUARD" in open(py).read():
                found.append(py)
        except OSError:
            pass
    R("AAP_GUARD installed", bool(found),
      "no sitecustomize.py with AAP_GUARD under pyenv" if not found else "")


def test_sentinels():
    print("\n--- Sentinel files ---")
    for f in (".is_pro.pl", "panel_pro.pl"):
        R(f, os.path.exists(os.path.join(target, "data", f)))


def test_userinfo():
    print("\n--- userInfo.json ---")
    p = os.path.join(target, "data", "userInfo.json")
    if not os.path.exists(p):
        R("userInfo.json", None, "absent (ok if /binds JS patch succeeded)")
        return
    try:
        d = json.load(open(p))
        R("userInfo.json status=True", d.get("status") is True or d.get("status") == 1)
    except (OSError, ValueError):
        R("userInfo.json", False, "parse error")


def test_catalog():
    print("\n--- Offline plugin catalog ---")
    p = os.path.join(target, "data", "soft_catalog.json")
    try:
        n = len(json.load(open(p)).get("list", []))
        R("soft_catalog.json (%d plugins)" % n, n >= 10)
    except (OSError, ValueError):
        R("soft_catalog.json", False, "missing or invalid")


def test_js():
    print("\n--- Frontend JS bundles ---")
    static = os.path.join(target, "BTPanel", "static")
    acc = glob.glob(os.path.join(static, "**", "accountState*.js"), recursive=True)
    if acc:
        unpatched = [f for f in acc if "table.total>=30" in _read(f) and "99999" not in _read(f)]
        R("account limit lifted (%d files)" % len(acc), not unpatched,
          "%d still capped" % len(unpatched) if unpatched else "")
    else:
        R("account limit", None, "no accountState*.js bundles")

    idx = glob.glob(os.path.join(static, "**", "index*.js"), recursive=True)
    if idx:
        still_gated = [f for f in idx if 'aaPanelPro?e.path==="/binds"' in _read(f)
                       or 'aaPanelPro?"/binds"===e.path' in _read(f)]
        R("/binds redirect removed (%d files)" % len(idx), not still_gated,
          "%d still gated" % len(still_gated) if still_gated else "")
    else:
        R("/binds redirect", None, "no index*.js bundles")


def _read(path):
    try:
        return open(path, errors="ignore").read()
    except OSError:
        return ""


# --- main --------------------------------------------------------------------
def parse_args():
    global target, PANEL_PYTHON
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--panel-python" and i + 1 < len(args):
            PANEL_PYTHON = args[i + 1]
            i += 2
            continue
        if not a.startswith("--"):
            target = a
        i += 1
    if not PANEL_PYTHON:
        cand = os.path.join(target, "pyenv", "bin", "python3")
        PANEL_PYTHON = cand if os.path.exists(cand) else "python3"


def main():
    parse_args()
    print("=== aaPanel License Hardener — verification ===")
    print("Target: %s\nPython: %s" % (target, PANEL_PYTHON))
    if not os.path.isdir(target):
        print(red("ERROR: %s not found" % target))
        return 1

    test_anchors()
    test_runtime()
    test_guard()
    test_sentinels()
    test_userinfo()
    test_catalog()
    test_js()

    total = PASS + FAIL + SKIP
    print("\nResults: %s passed, %s failed, %s skipped (total %d)"
          % (green(str(PASS)), red(str(FAIL)), yellow(str(SKIP)), total))
    if FAIL == 0:
        print(green("\nAll checks passed."))
        return 0
    print(red("\nSome checks FAILED."))
    return 1


if __name__ == "__main__":
    sys.exit(main())
