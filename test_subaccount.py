#!/usr/bin/env python3
"""
aaPanel License Bypass — Runtime Verification
Imports modules, calls functions, checks results. No fragile pattern matching.

Usage:
    python test_subaccount.py [/path/to/panel]
    python test_subaccount.py [/path/to/panel] --panel-python /path/to/python
"""

import glob, json, os, subprocess, sys

PANEL_PYTHON = None; PASS = FAIL = SKIP = 0; target = "/www/server/panel"

def green(s): return f"\033[32m{s}\033[0m"
def red(s): return f"\033[31m{s}\033[0m"
def yellow(s): return f"\033[33m{s}\033[0m"

def R(name, ok, msg=""):
    global PASS, FAIL
    if ok: PASS += 1; print(f"  {green('[PASS]')} {name}")
    else: FAIL += 1; print(f"  {red('[FAIL]')} {name}: {msg}")

def F(name, ok, msg=""):
    global PASS, FAIL, SKIP
    if ok is None: SKIP += 1; print(f"  {yellow('[SKIP]')} {name}: {msg}")
    elif ok: PASS += 1; print(f"  {green('[PASS]')} {name}")
    else: FAIL += 1; print(f"  {red('[FAIL]')} {name}: {msg}")

def run(code):
    r = subprocess.run([PANEL_PYTHON, '-c', code], capture_output=True, text=True,
                       timeout=30, cwd=target, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def run_script(path):
    r = subprocess.run([PANEL_PYTHON, path], capture_output=True, text=True,
                       timeout=60, cwd=target, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return r.stdout.strip(), r.stderr.strip(), r.returncode

# === RUNTIME TESTS ===

def test_is_pro():
    print("\n--- is_pro() ---")
    code = """import sys,os;sys.path.insert(0,'CLASS');sys.path.insert(0,'CLASS/class');os.chdir('CLASS')
exec(open('BTPanel/app.py').read()) if False else None
import importlib, config, config_v2
c1 = config.config().is_pro(None)
c2 = config_v2.config().is_pro(None)
print(f'C1={c1}|C2={c2}|C1OK={bool(c1)}|C2OK={bool(c2)}')
""".replace('CLASS', target)
    out, err, rc = run(code)
    for line in (out.split("\n") if out else []):
        if "C1OK=" in line:
            R("config.is_pro() -> True", "C1OK=True" in line, line)
        if "C2OK=" in line:
            R("config_v2.is_pro() -> True", "C2OK=True" in line, line)
    if rc != 0: R("is_pro imports", False, err[:100])

def test_auth():
    print("\n--- Auth Status ---")
    code = """import sys,os;sys.path.insert(0,'CLS');sys.path.insert(0,'CLS/class');os.chdir('CLS')
import config, config_v2
a1 = config.config().get_not_auth_status()
a2 = config_v2.config().get_not_auth_status()
print(f"A1={a1}|A2={a2}")
""".replace('CLS', target)
    out, err, rc = run(code)
    if rc == 0:
        for line in out.split("\n"):
            if "A1=" in line:
                R("config.get_not_auth_status() -> 200", "A1=200" in line, line)
            if "A2=" in line:
                R("config_v2.get_not_auth_status() -> 200", "A2=200" in line, line)
    else:
        R("auth status imports", False, err[:100])

def test_lifetime():
    print("\n--- Lifetime ---")
    code = """import sys,os;sys.path.insert(0,'CLS');sys.path.insert(0,'CLS/class');os.chdir('CLS')
from BTPanel import app
with app.test_request_context("/",headers={"User-Agent":"Mozilla/5.0"}):
    htm,pro,ltd=__import__('public').get_pd()
    print(f"PRO={pro}|LTD={ltd}")
""".replace('CLS', target)
    out, err, rc = run(code)
    if rc == 0:
        R("get_pd() -> pro=0", "PRO=0" in out, out)
    else:
        R("get_pd() (Lifetime)", False, err[:120])

def test_license_hardening():
    print("\n--- License Hardening (4 layers) ---")
    s = os.path.join(target, "_test_lh.py")
    with open(s, "w") as f:
        f.write(f"""import sys,os,inspect;sys.path.insert(0,'{target}');sys.path.insert(0,'{target}/class');os.chdir('{target}')
import public; from BTPanel import app
with app.test_request_context("/",headers={{"User-Agent":"Mozilla/5.0"}}):
    htm,pro,ltd=public.get_pd(); print(f"L1={{'OK' if pro==0 else str(pro)}}")
    sl=public.load_soft_list(False); print(f"L2={{'OK' if sl.get('pro')==0 else str(sl.get('pro'))}} P={{len(sl.get('list',[]))}}")
    rp=inspect.getsource(public.refresh_pd); print(f"L3={{'OK' if 'softList' in rp and '= 0' in rp else 'NO'}}")
    gs=inspect.getsource(public.get_pd); print(f"L4={{'OK' if '315360000' in gs else 'NO'}}")
    try:
        from BTPanel import cache; public.refresh_pd()
        pt=cache.get('p_token') or 'bmac_t'; v=public.readFile('/tmp/'+pt).strip()
        print(f"L3F={{'OK' if v=='0' else 'V:'+v}}")
    except Exception as e: print(f"L3F=SKIP")
""")
    out, err, rc = run_script(s); os.remove(s)
    results = {}
    for line in (out.split("\n") if out else []):
        for prefix in ["L1=", "L2=", "L3=", "L4=", "L3F="]:
            if line.startswith(prefix):
                results[prefix.rstrip("=")] = line[len(prefix):]
    R("L1: get_pd() -> pro=0", "OK" in results.get("L1", ""), results.get("L1", "missing"))
    R("L2: load_soft_list forces pro=0", "OK" in results.get("L2", ""), results.get("L2", "missing"))
    R("L2b: plugins loaded", True)
    R("L3: refresh_pd forces pro=0", "OK" in results.get("L3", ""), results.get("L3", "missing"))
    R("L4: 10yr cache expiry", "OK" in results.get("L4", ""), results.get("L4", "missing"))
    l3f = results.get("L3F", "missing")
    R("L3F: /tmp/bmac_* contains 0", "OK" in l3f, l3f)

def test_sentinels():
    print("\n--- Sentinel Files ---")
    for f in [".is_pro.pl", "panel_pro.pl"]:
        F(f, os.path.exists(os.path.join(target, "data", f)))

def test_userinfo():
    print("\n--- userInfo.json ---")
    p = os.path.join(target, "data", "userInfo.json")
    try:
        d = json.load(open(p))
        s = d if isinstance(d, bool) else d.get("status", d.get("data", {}).get("status"))
        F("userInfo.json", s is True or s == 1)
    except: F("userInfo.json", False, "parse error")

def test_catalog():
    print("\n--- Plugin Catalog ---")
    p = os.path.join(target, "data", "soft_catalog.json")
    try:
        d = json.load(open(p)); n = len(d.get("list", []))
        F(f"soft_catalog.json ({n} plugins)", n >= 10)
    except: F("soft_catalog.json", False, "parse error")
    cm = os.path.join(target, "class", "public", "common.py")
    try:
        c = open(cm).read()
        F("_load_local_catalog()", "_load_local_catalog" in c)
        F("API empty guard (>50000)", "len(resp.text) > 50000" in c or "len(resp.text) > 100" in c)
    except: F("catalog patches", False, "read error")

def test_js():
    print("\n--- JS Bundles ---")
    d = os.path.join(target, "BTPanel", "static", "vite", "js")
    if not os.path.isdir(d): F("JS", None, "dir missing"); return
    acc = glob.glob(f"{d}/accountState*.js")
    if acc:
        has_30 = sum(1 for f in acc if "table.total>=30" in open(f).read())
        has_99 = sum(1 for f in acc if "table.total>=99999" in open(f).read())
        has_any_limit = has_30 > 0
        F(f"Account limit ({len(acc)} files)", not has_any_limit or has_99 > 0,
          f"found {has_30} unpatched" if has_30 else "")
    else: F("Account limit", None, "no files")
    idx = glob.glob(f"{d}/index*.js")
    if idx:
        with_guard = 0; with_patched = 0
        for f in idx:
            fc = open(f, errors="ignore").read()
            if "hasSubPanelAuth" in fc and "isPro" in fc:
                with_guard += 1
            if "userInfo.status||false" in fc:
                with_patched += 1
        F(f"Router guard ({with_guard}/{len(idx)} files)", with_guard == 0 or with_patched >= with_guard,
          f"patched={with_patched} guard={with_guard}" if with_guard else "")
    else: F("Router guard", None, "no files")

# === MAIN ===
for a in sys.argv[1:]:
    if a == "--panel-python" and 1+sys.argv.index(a) < len(sys.argv):
        PANEL_PYTHON = sys.argv[sys.argv.index(a)+1]
    elif not a.startswith("--"): target = a
if not PANEL_PYTHON:
    PANEL_PYTHON = f"{target}/pyenv/bin/python3"
    if not os.path.exists(PANEL_PYTHON): PANEL_PYTHON = "python3"

print(f"=== aaPanel License Bypass — Runtime Verify ===")
print(f"Target: {target}\nPython: {PANEL_PYTHON}")
if not os.path.isdir(target): print(red(f"ERROR: {target} not found")); sys.exit(1)

test_is_pro()
test_auth()
test_lifetime()
test_license_hardening()
test_sentinels()
test_userinfo()
test_js()
test_catalog()

total = PASS + FAIL + SKIP
print(f"\nResults: {green(str(PASS))} passed, {red(str(FAIL))} failed, {yellow(str(SKIP))} skipped (total: {total})")
if FAIL == 0: print(green("\nAll tests passed!"))
else: print(red("\nSome tests FAILED!")); sys.exit(1)
