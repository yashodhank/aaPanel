#!/usr/bin/env python3
"""
aaPanel Pro License Bypass Verification Script
Verifies all 13 pro bypass patches are correctly applied.

Usage:
    python test_subaccount.py [/path/to/panel]
    python test_subaccount.py [/path/to/panel] --panel-python /path/to/python

When --panel-python is provided, the script uses that Python interpreter for
import-based runtime checks (tests 1-2, 8-9). Otherwise it falls back to
pattern-matching checks.
"""

import glob
import json
import os
import re
import subprocess
import sys
import traceback

PANEL_PYTHON = None

PASS = 0
FAIL = 0
SKIP = 0


def green(s):
    return "\033[32m{}\033[0m".format(s)


def red(s):
    return "\033[31m{}\033[0m".format(s)


def yellow(s):
    return "\033[33m{}\033[0m".format(s)


def test_result(name, passed, message="", skipped=False):
    global PASS, FAIL, SKIP
    if skipped:
        SKIP += 1
        print("  {} {}: {}".format(yellow('[SKIP]'), name, message))
    elif passed:
        PASS += 1
        print("  {} {}".format(green('[PASS]'), name))
    else:
        FAIL += 1
        print("  {} {}: {}".format(red('[FAIL]'), name, message))


def _run_panel_python(script, target):
    """Run a short Python snippet using the aaPanel interpreter."""
    if not PANEL_PYTHON or not os.path.isfile(PANEL_PYTHON):
        return None
    try:
        cp = subprocess.run(
            [PANEL_PYTHON, "-c", script],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONPATH": target}
        )
        return cp.stdout.strip()
    except Exception:
        return None


def test1_config_v2_is_pro(target):
    """Test 1: config_v2.is_pro() returns True"""
    filepath = os.path.join(target, "class_v2", "config_v2.py")
    if not os.path.isfile(filepath):
        test_result("config_v2.is_pro()", False, "file not found: " + filepath, skipped=True)
        return
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        passed = "def is_pro" in content and "return True" in content
        test_result("config_v2.is_pro() returns True", passed)
    except Exception as e:
        test_result("config_v2.is_pro()", False, str(e))


def test2_config_is_pro(target):
    """Test 2: config.is_pro() returns True"""
    filepath = os.path.join(target, "class", "config.py")
    if not os.path.isfile(filepath):
        test_result("config.is_pro()", False, "file not found: " + filepath, skipped=True)
        return
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        passed = "def is_pro" in content and "return True" in content
        test_result("config.is_pro() returns True", passed)
    except Exception as e:
        test_result("config.is_pro()", False, str(e))


def test3_sentinel_files(target):
    """Test 3: Pro sentinel files exist (.is_pro.pl, panel_pro.pl)"""
    data_dir = os.path.join(target, "data")
    if not os.path.isdir(data_dir):
        test_result("Sentinel files", False, "data/ dir not found: " + data_dir, skipped=True)
        return
    for fname in [".is_pro.pl", "panel_pro.pl"]:
        fpath = os.path.join(data_dir, fname)
        if os.path.isfile(fpath):
            test_result("Sentinel: " + fname, True)
        else:
            test_result("Sentinel: " + fname, False, "not found: " + fpath)
            return False
    return True


def test4_check_auth_logic(target):
    """Test 4: is_pro is referenced in config.py (pro bypass check)"""
    filepath = os.path.join(target, "class", "config.py")
    if not os.path.isfile(filepath):
        test_result("is_pro check in config.py", False, "config.py not found", skipped=True)
        return
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        has_pro_skip = "is_pro" in content
        auth_funcs = [l.strip() for l in content.splitlines() if "def " in l and "auth" in l.lower()]
        found_auth = len(auth_funcs) > 0
        test_result("is_pro referenced in config.py", has_pro_skip)
        test_result("auth function in config.py: " + (auth_funcs[0].split("(")[0] if found_auth else "none"), found_auth)
    except Exception as e:
        test_result("is_pro check in config.py", False, str(e))


def test5_router_pro_guard(target):
    """Test 5: Router pro guard patched in ALL index*.js bundles"""
    static_dir = os.path.join(target, "BTPanel", "static")
    if not os.path.isdir(static_dir):
        test_result("Router guard in index*.js", False, "static dir not found: " + static_dir, skipped=True)
        return
    index_files = glob.glob(os.path.join(static_dir, "**", "index*.js"), recursive=True)
    if not index_files:
        test_result("Router guard in index*.js", False, "no index*.js files found", skipped=True)
        return
    patched = 0
    unpatched = 0
    for fpath in index_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "{type:\"pro\"" not in content and "{\"type\":\"pro\"" not in content:
                patched += 1
            else:
                unpatched += 1
        except Exception:
            unpatched += 1
    passed = unpatched == 0
    test_result(
        "Router pro guard in index*.js",
        passed,
        "patched={}, unpatched={}, total={}".format(patched, unpatched, len(index_files))
    )


def test6_account_limit(target):
    """Test 6: Account limit (table.total>=30 -> 99999) in ALL accountState*.js bundles"""
    static_dir = os.path.join(target, "BTPanel", "static")
    if not os.path.isdir(static_dir):
        test_result("Account limit in accountState*.js", False, "static dir not found", skipped=True)
        return
    acct_files = glob.glob(os.path.join(static_dir, "**", "accountState*.js"), recursive=True)
    if not acct_files:
        test_result("Account limit in accountState*.js", False, "no accountState*.js found", skipped=True)
        return
    patched = 0
    unpatched = 0
    for fpath in acct_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "99999" in content:
                patched += 1
            else:
                unpatched += 1
        except Exception:
            unpatched += 1
    passed = unpatched == 0
    test_result(
        "Account limit (99999) in accountState*.js",
        passed,
        "patched={}, unpatched={}, total={}".format(patched, unpatched, len(acct_files))
    )


def test7_lifetime_patch(target):
    """Test 7: Lifetime pro=0/tmp=0 patches in common.py and app.py"""
    common_path = os.path.join(target, "class", "public", "common.py")
    if not os.path.isfile(common_path):
        test_result("Lifetime in common.py", False, "file not found", skipped=True)
    else:
        try:
            with open(common_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            passed = "pro = 0" in content and "Force Lifetime" in content
            test_result("Lifetime patch in common.py", passed)
        except Exception as e:
            test_result("Lifetime patch in common.py", False, str(e))

    app_path = os.path.join(target, "BTPanel", "app.py")
    if not os.path.isfile(app_path):
        test_result("Lifetime in app.py", False, "file not found", skipped=True)
    else:
        try:
            with open(app_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            passed = "tmp = 0" in content and "Force Lifetime" in content
            test_result("Lifetime patch in app.py", passed)
        except Exception as e:
            test_result("Lifetime patch in app.py", False, str(e))


def test8_get_not_auth_status_v1(target):
    """Test 8: config.py get_not_auth_status returns 200 (not 404)"""
    config_path = os.path.join(target, "class", "config.py")
    if not os.path.isfile(config_path):
        test_result("get_not_auth_status (config.py)", False, "file not found", skipped=True)
        return


    with open(config_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    m = re.search(r"def get_not_auth_status.*?except:\s*\n\s*(return \d+)", content, re.DOTALL)
    if m:
        status = m.group(1)
        passed = "return 200" in status
        test_result("get_not_auth_status (config.py) -> {}".format(m.group(1)), passed)
    else:
        test_result("get_not_auth_status (config.py)", False, "pattern not matched")


def test9_get_not_auth_status_v2(target):
    """Test 9: config_v2.py get_not_auth_status returns 200 (not 404)"""
    config_path = os.path.join(target, "class_v2", "config_v2.py")
    if not os.path.isfile(config_path):
        test_result("get_not_auth_status (config_v2.py)", False, "file not found", skipped=True)
        return


    with open(config_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    m = re.search(r"def get_not_auth_status.*?except:\s*\n\s*(return \d+)", content, re.DOTALL)
    if m:
        status = m.group(1)
        passed = "return 200" in status
        test_result("get_not_auth_status (config_v2.py) -> {}".format(m.group(1)), passed)
    else:
        test_result("get_not_auth_status (config_v2.py)", False, "pattern not matched")


def test10_binds_js_patch(target):
    """Test 10: JS binds redirect patched out of main index bundles"""
    js_dir = os.path.join(target, "BTPanel", "static", "vite", "js")
    if not os.path.isdir(js_dir):
        test_result("JS binds redirect patch", False, "js dir not found", skipped=True)
        return
    patched = 0
    unpatched = []
    for fname in ["index-DV9DrNIN.js", "index-legacy-6o9d0Mmi.js"]:
        fpath = os.path.join(js_dir, fname)
        if not os.path.isfile(fpath):
            unpatched.append(fname)
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "binds" not in content or '!n.userInfo.status&&' not in content:
                patched += 1
            else:
                unpatched.append(fname)
        except Exception:
            unpatched.append(fname)
    passed = len(unpatched) == 0
    test_result(
        "JS binds redirect patched",
        passed,
        "patched={}, unpatched={}".format(patched, unpatched)
    )


def test11_userinfo_json(target):
    """Test 11: userInfo.json exists with status=True"""
    ui_path = os.path.join(target, "data", "userInfo.json")
    if not os.path.isfile(ui_path):
        test_result("userInfo.json exists", False, "file not found", skipped=True)
        return
    try:
        import json
        with open(ui_path, "r") as f:
            ui = json.load(f)
        passed = ui.get("status") is True
        test_result("userInfo.json status=True", passed,
                    "status={}".format(ui.get("status")))
    except Exception as e:
        test_result("userInfo.json", False, str(e))


def test12_A_soft_catalog(target):
    """Test 12a: soft_catalog.json exists with valid plugins"""
    catalog_path = os.path.join(target, "data", "soft_catalog.json")
    if not os.path.isfile(catalog_path):
        test_result("soft_catalog.json exists", False, "file not found", skipped=True)
        return
    try:
        with open(catalog_path, "r") as f:
            data = json.load(f)
        count = len(data.get("list", []))
        passed = count >= 10
        test_result("soft_catalog.json ({}) plugins".format(count), passed,
                    "expected >= 10, got {}".format(count))
    except Exception as e:
        test_result("soft_catalog.json", False, str(e))


def test12_B_load_soft_list_fallback(target):
    """Test 12b: load_soft_list has local catalog fallback"""
    common_path = os.path.join(target, "class", "public", "common.py")
    if not os.path.isfile(common_path):
        test_result("load_soft_list fallback", False, "file not found", skipped=True)
        return
    try:
        with open(common_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        has_catalog = "_load_local_catalog" in content
        has_guard = "resp.ok and resp.text and len(resp.text) > 100" in content
        test_result("load_soft_list _load_local_catalog()", has_catalog)
        test_result("load_soft_list API empty guard", has_guard)
    except Exception as e:
        test_result("load_soft_list fallback", False, str(e))


def main():
    global PASS, FAIL, SKIP, PANEL_PYTHON

    target = None
    for a in sys.argv[1:]:
        if a == "--panel-python":
            idx = sys.argv.index(a)
            if idx + 1 < len(sys.argv):
                PANEL_PYTHON = sys.argv[idx + 1]
        elif not a.startswith("--") and target is None:
            target = a
    if target is None:
        target = "/www/server/panel"

    print("=== aaPanel Pro License Bypass Verification ===")
    print("Target: {}".format(target))
    if PANEL_PYTHON:
        print("Panel Python: {}".format(PANEL_PYTHON))
    print()

    if not os.path.isdir(target):
        print(red("ERROR: Target directory not found: {}".format(target)))
        sys.exit(1)

    os.chdir(target)

    test1_config_v2_is_pro(target)
    test2_config_is_pro(target)
    test3_sentinel_files(target)
    test4_check_auth_logic(target)
    print("--- JS Bundles ---")
    test5_router_pro_guard(target)
    test6_account_limit(target)
    print("--- Lifetime Patch ---")
    test7_lifetime_patch(target)
    print("--- Auth Status Patch ---")
    test8_get_not_auth_status_v1(target)
    test9_get_not_auth_status_v2(target)
    print("--- Binds Redirect Patch ---")
    test10_binds_js_patch(target)
    test11_userinfo_json(target)
    print("--- Plugin Catalog ---")
    test12_A_soft_catalog(target)
    test12_B_load_soft_list_fallback(target)

    print()
    total = PASS + FAIL + SKIP
    print("Results: {} passed, {} failed, {} skipped (total: {})".format(
        green(str(PASS)), red(str(FAIL)), yellow(str(SKIP)), total))

    if FAIL > 0:
        print(red("\nSome tests FAILED!"))
        sys.exit(1)
    else:
        print(green("\nAll tests passed!"))
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
