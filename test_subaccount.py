#!/usr/bin/env python3
"""
aaPanel Pro License Bypass Verification Script
Verifies all 7 pro bypass patches are correctly applied.
Usage: python test_subaccount.py [/path/to/panel]
"""

import glob
import os
import sys
import traceback

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

    try:
        sys.path.insert(0, target)
        sys.path.insert(0, os.path.join(target, "class"))
        sys.path.insert(0, os.path.join(target, "class_v2"))
        from config import config
        c = config()
        result = c.get_not_auth_status()
        passed = result == 200
        test_result("get_not_auth_status (config.py) -> {}".format(result), passed,
                    "expected 200" if not passed else "")
    except Exception as e:
        test_result("get_not_auth_status (config.py)", False, str(e))


def test9_get_not_auth_status_v2(target):
    """Test 9: config_v2.py get_not_auth_status returns 200 (not 404)"""
    config_path = os.path.join(target, "class_v2", "config_v2.py")
    if not os.path.isfile(config_path):
        test_result("get_not_auth_status (config_v2.py)", False, "file not found", skipped=True)
        return

    try:
        sys.path.insert(0, target)
        sys.path.insert(0, os.path.join(target, "class"))
        sys.path.insert(0, os.path.join(target, "class_v2"))
        import config_v2
        c = config_v2.config()
        result = c.get_not_auth_status()
        passed = result == 200
        test_result("get_not_auth_status (config_v2.py) -> {}".format(result), passed,
                    "expected 200" if not passed else "")
    except Exception as e:
        test_result("get_not_auth_status (config_v2.py)", False, str(e))


def main():
    global PASS, FAIL, SKIP

    target = sys.argv[1] if len(sys.argv) > 1 else "/www/server/panel"

    print("=== aaPanel Pro License Bypass Verification ===")
    print("Target: {}".format(target))
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
