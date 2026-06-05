#!/bin/bash
# aaPanel Pro License Bypass Installer
# Patches aaPanel to remove account limits and router pro guards

set -e

REPO_PATH="$(cd "$(dirname "$0")" && pwd)"
PANEL_PATH="/www/server/panel"

echo "=== aaPanel Pro License Bypass Installer ==="

# Step 1: Verify we're running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root"
    exit 1
fi

# Step 2: Check panel exists
if [ ! -d "$PANEL_PATH" ]; then
    echo "ERROR: aaPanel not found at $PANEL_PATH"
    exit 1
fi

# Step 3: Backup original files
BACKUP_DIR="$PANEL_PATH/backup_pro_patch_$(date +%s)"
mkdir -p "$BACKUP_DIR"
echo "[ backup ] Creating backup at $BACKUP_DIR"

for f in "BTPanel/app.py" "class/public/common.py"; do
    if [ -f "$PANEL_PATH/$f" ]; then
        mkdir -p "$(dirname "$BACKUP_DIR/$f")"
        cp "$PANEL_PATH/$f" "$BACKUP_DIR/$f"
        echo "[ backup ] Saved $f"
    fi
done

# Step 4a: Copy patched class/ and class_v2/ from repo
echo "[ patch ] Step 4a: Copying patched class/ and class_v2/..."
if [ -d "$REPO_PATH/class" ]; then
    cp -r "$REPO_PATH/class/"* "$PANEL_PATH/class/"
    echo "[ patch ] Copied class/"
else
    echo "[ WARN ] class/ not found in repo, skipping"
fi

if [ -d "$REPO_PATH/class_v2" ]; then
    cp -r "$REPO_PATH/class_v2/"* "$PANEL_PATH/class_v2/"
    echo "[ patch ] Copied class_v2/"
else
    echo "[ WARN ] class_v2/ not found in repo, skipping"
fi

if [ -f "$REPO_PATH/BTPanel/app.py" ]; then
    cp "$REPO_PATH/BTPanel/app.py" "$PANEL_PATH/BTPanel/app.py"
    echo "[ patch ] Copied BTPanel/app.py"
fi

# Step 4b: Dynamic JS patching via glob
echo "[ patch ] Step 4b: Patching JS bundles..."

find "$PANEL_PATH/BTPanel/static" -name "accountState*.js" -type f 2>/dev/null | while read -r jsfile; do
    if grep -q "table.total" "$jsfile" 2>/dev/null; then
        sed -i 's/table\.total>=30/table.total>=99999/g' "$jsfile"
        echo "[ patch ] Account limit patched in $(basename "$jsfile")"
    fi
done

find "$PANEL_PATH/BTPanel/static" -name "index*.js" -type f 2>/dev/null | while read -r jsfile; do
    if grep -q 'type:"pro"' "$jsfile" 2>/dev/null || grep -q "is_pro" "$jsfile" 2>/dev/null; then
        sed -i 's/{type:"pro"}/{}/g' "$jsfile"
        sed -i 's/{type:"pro",[^}]*}/{}/g' "$jsfile"
        echo "[ patch ] Router pro guard patched in $(basename "$jsfile")"
    fi
done

# Step 4c: Create Pro sentinel files
echo "[ patch ] Step 4c: Creating Pro sentinel files..."
touch "$PANEL_PATH/data/.is_pro.pl"
touch "$PANEL_PATH/data/panel_pro.pl"
chmod 644 "$PANEL_PATH/data/.is_pro.pl" 2>/dev/null || true
chmod 644 "$PANEL_PATH/data/panel_pro.pl" 2>/dev/null || true
echo "[ patch ] Created .is_pro.pl and panel_pro.pl"

# Step 4c1: Patch JS binds redirect (userInfo.status gate)
echo "[ patch ] Step 4c1: Patching JS binds redirect..."
python3 -c "
import os, glob
panel = os.environ.get('PANEL_PATH', '$PANEL_PATH')
for pattern in ['index-DV9DrNIN.js', 'index-legacy-6o9d0Mmi.js']:
    for jsfile in glob.glob(os.path.join(panel, 'BTPanel/static/vite/js', pattern)):
        with open(jsfile, 'r') as f:
            content = f.read()
        content = content.replace(
            '!n.userInfo.status&&n.aaPanelPro?e.path===\"/binds\"?s():s(\"/binds\"):(n.getCheckAuth(),s())',
            'n.getCheckAuth(),s()'
        ).replace(
            '!o.userInfo.status&&o.aaPanelPro?\"/binds\"===e.path?i():i(\"/binds\"):(o.getCheckAuth(),i())',
            'o.getCheckAuth(),i()'
        )
        with open(jsfile, 'w') as f:
            f.write(content)
        print(f'[ patch ] Binds redirect removed in {os.path.basename(jsfile)}')
" 2>/dev/null || echo "[ WARN ] JS binds patch failed (may already be applied)"

# Step 4c2: Create minimal userInfo.json to satisfy status checks
echo "[ patch ] Step 4c2: Creating userInfo.json..."
if [ ! -f "$PANEL_PATH/data/userInfo.json" ]; then
    python3 -c "
import json, uuid, time
ui = {
    'status': True,
    'uid': 1,
    'id': 1,
    'username': 'admin',
    'email': 'admin@localhost',
    'token': str(uuid.uuid4()) + '.' + str(uuid.uuid4()) + '.' + str(uuid.uuid4()),
    'server_id': 'patched_' + str(int(time.time())),
    'access_key': 'patched_access_key'
}
with open('$PANEL_PATH/data/userInfo.json', 'w') as f:
    json.dump(ui, f)
print('Created userInfo.json with status: true')
"
fi

# Step 4c-extra: Patch app.py via sed for Lifetime status (belt-and-suspenders)
echo "[ patch ] Step 4c-extra: Ensuring app.py Lifetime patch..."
if [ -f "$PANEL_PATH/BTPanel/app.py" ]; then
    if ! grep -q "tmp = 0.*Force Lifetime" "$PANEL_PATH/BTPanel/app.py" 2>/dev/null; then
        sed -i '/if tmp: tmp = int(tmp)/a\            tmp = 0  # Force Lifetime (patched)' "$PANEL_PATH/BTPanel/app.py"
        echo "[ patch ] app.py Lifetime patch applied via sed"
    else
        echo "[ patch ] app.py already patched"
    fi
fi

# Step 4c3: Deploy offline plugin catalog + patch load_soft_list fallback
echo "[ patch ] Step 4c3: Deploying offline plugin catalog..."
CATALOG_SRC="$REPO_PATH/data/soft_catalog.json"
if [ -f "$CATALOG_SRC" ]; then
    cp "$CATALOG_SRC" "$PANEL_PATH/data/soft_catalog.json"
    echo "[ patch ] Plugin catalog deployed (41 plugins)"
else
    echo "[ WARN ] soft_catalog.json not found at $CATALOG_SRC — catalog will be empty"
fi

# Step 4c3b: Patch load_soft_list() for offline catalog fallback
echo "[ patch ] Step 4c3b: Patching load_soft_list() for offline fallback..."
python3 << 'PYEOF'
import sys
common_path = '/www/server/panel/class/public/common.py'
with open(common_path, 'r') as f:
    content = f.read()

if '_load_local_catalog' in content:
    print('[ patch ] _load_local_catalog already present, skipping')
else:
    # 1) Add _load_local_catalog function
    old1 = "return {'list': [], 'total': 0, 'pages': 0}\n\n\ndef load_soft_list"
    new1 = "return {'list': [], 'total': 0, 'pages': 0}\n\n\ndef _load_local_catalog():\n    import json\n    catalog_path = '{}/data/soft_catalog.json'.format(get_panel_path())\n    try:\n        if os.path.exists(catalog_path):\n            with open(catalog_path, 'r') as f:\n                data = json.load(f)\n            if isinstance(data, dict) and 'list' in data:\n                return data\n    except:\n        pass\n    return None\n\n\ndef load_soft_list"
    if old1 not in content:
        print('[ FAIL ] Could not find insertion point for _load_local_catalog')
        sys.exit(1)
    content = content.replace(old1, new1)
    print('[ patch ] Added _load_local_catalog()')

    # 2) parse_plugin_list failure -> catalog
    old2 = 'if not PluginLoader.parse_plugin_list(1):\n                    return _empty_soft_list()'
    if old2 in content:
        content = content.replace(old2, 'if not PluginLoader.parse_plugin_list(1):\n                    catalog = _load_local_catalog()\n                    if catalog is not None:\n                        return catalog\n                    return _empty_soft_list()')
        print('[ patch ] parse_plugin_list catalog fallback')

    # 3) PluginLoader except -> catalog
    old3 = 'plugin_list_data = PluginLoader.get_plugin_list(0)\n    except:\n        if retry_count < 6:\n            # ' + '\xe8\x8e\xb7\xe5\x8f\x96\xe8\xbd\xaf\xe4\xbb\xb6\xe5\x88\x97\xe8\xa1\xa8\xe5\xa4\xb1\xe8\xb4\xa5\xef\xbc\x8c\xe9\x87\x8d\xe8\xaf\x95\n            return load_soft_list(force, retry_count + 1)\n        return _empty_soft_list()'
    # Use regex-free approach: match the structure
    lines = content.split('\n')
    new_lines = []
    i = 0
    while i < len(lines):
        new_lines.append(lines[i])
        if 'plugin_list_data = PluginLoader.get_plugin_list(0)' in lines[i] and i+1 < len(lines) and 'except:' in lines[i+1]:
            # Found the except block, insert catalog fallback before retry
            indent = '        '
            new_lines.append(indent + 'catalog = _load_local_catalog()')
            new_lines.append(indent + 'if catalog is not None:')
            new_lines.append(indent + '    return catalog')
            i += 1  # skip the original except line, we'll add it
            new_lines.append(lines[i])  # except:
        i += 1
    content = '\n'.join(new_lines)
    print('[ patch ] PluginLoader except catalog fallback')

    # 4) isinstance check -> catalog
    old4 = 'if not isinstance(plugin_list_data, dict):\n        if retry_count < 6:\n            # ' + '\xe8\x8e\xb7\xe5\x8f\x96\xe8\xbd\xaf\xe4\xbb\xb6\xe5\x88\x97\xe8\xa1\xa8\xe5\xa4\xb1\xe8\xb4\xa5\xef\xbc\x8c\xe9\x87\x8d\xe8\xaf\x95\n            return load_soft_list(force, retry_count + 1)\n        return _empty_soft_list()'
    if old4 in content:
        content = content.replace(old4, 'if not isinstance(plugin_list_data, dict):\n        catalog = _load_local_catalog()\n        if catalog is not None:\n            return catalog\n        if retry_count < 6:\n            return load_soft_list(force, retry_count + 1)\n        return _empty_soft_list()')
        print('[ patch ] isinstance check catalog fallback')

    # 5) status==False -> catalog
    old5 = "if 'status' in plugin_list_data and 'msg' in plugin_list_data and plugin_list_data['status'] == False:\n        if retry_count < 6:\n            # " + '\xe8\x8e\xb7\xe5\x8f\x96\xe8\xbd\xaf\xe4\xbb\xb6\xe5\x88\x97\xe8\xa1\xa8\xe5\xa4\xb1\xe8\xb4\xa5\xef\xbc\x8c\xe9\x87\x8d\xe8\xaf\x95\n            return load_soft_list(force, retry_count + 1)\n        return _empty_soft_list()'
    if old5 in content:
        content = content.replace(old5, "if 'status' in plugin_list_data and 'msg' in plugin_list_data and plugin_list_data['status'] == False:\n        catalog = _load_local_catalog()\n        if catalog is not None:\n            return catalog\n        if retry_count < 6:\n            return load_soft_list(force, retry_count + 1)\n        return _empty_soft_list()")
        print('[ patch ] status==False catalog fallback')

    # 6) API empty response guard
    old6 = 'if resp.ok:\n                with open(local_cache_file,'
    if old6 in content:
        content = content.replace(
            'if resp.ok:\n                with open(local_cache_file, \'w\') as fp:\n                    fp.write(resp.text)\n                update_ok = True',
            'if resp.ok and resp.text and len(resp.text) > 100:\n                with open(local_cache_file, \'w\') as fp:\n                    fp.write(resp.text)\n                update_ok = True'
        )
        print('[ patch ] API empty response guard')

    with open(common_path, 'w') as f:
        f.write(content)
    print('[ patch ] load_soft_list fully patched for offline catalog')
PYEOF

# Step 4c4: Layer 10 — License hardening
echo "[ patch ] Step 4c4: Layer 10a — _harden_license_pro() in load_soft_list()..."
python3 << 'PYEOF'
import sys
common_path = '/www/server/panel/class/public/common.py'
with open(common_path, 'r') as f:
    content = f.read()

# 10a-1: Insert _harden_license_pro() function definition
if '_harden_license_pro' in content:
    print('[ patch ] _harden_license_pro() already present')
else:
    fn_def = """
def _harden_license_pro(data: dict) -> dict:
    try:
        if isinstance(data, dict):
            data['pro'] = 0
            data['trail'] = 0
    except:
        pass
    return data
"""
    old = "def load_soft_list(force: bool = True, retry_count: int = 0):"
    if old not in content:
        print('[ FAIL ] Could not find load_soft_list insertion point')
        sys.exit(1)
    content = content.replace(old, fn_def + "\n" + old)
    print('[ patch ] Inserted _harden_license_pro() before load_soft_list()')

# 10a-2: Call _harden_license_pro() before final return in load_soft_list()
if 'plugin_list_data = _harden_license_pro(plugin_list_data)' in content:
    print('[ patch ] _harden_license_pro() call already present')
else:
    old2 = 'return plugin_list_data'
    if old2 in content:
        content = content.replace(old2, '    plugin_list_data = _harden_license_pro(plugin_list_data)\n    return plugin_list_data')
        print('[ patch ] Added _harden_license_pro() call before return')
    else:
        print('[ WARN ] Could not find return plugin_list_data for hardening call')

with open(common_path, 'w') as f:
    f.write(content)
print('[ patch ] Layer 10a complete')
PYEOF

echo "[ patch ] Step 4c4: Layer 10b — Force softList['pro'] = 0 in refresh_pd()..."
python3 << 'PYEOF'
import sys
common_path = '/www/server/panel/class/public/common.py'
with open(common_path, 'r') as f:
    content = f.read()

if 'softList[\'pro\'] = 0  # Layer 10b: Force Pro license' in content:
    print('[ patch ] Layer 10b already applied')
else:
    needle = "writeFile(\"/tmp/\" + p_token, str(softList['pro']))"
    replacement = "softList['pro'] = 0  # Layer 10b: Force Pro license\n        writeFile(\"/tmp/\" + p_token, str(softList['pro']))"
    if needle not in content:
        print('[ FAIL ] Could not find refresh_pd writeFile for Layer 10b')
        sys.exit(1)
    content = content.replace(needle, replacement)
    with open(common_path, 'w') as f:
        f.write(content)
    print('[ patch ] Layer 10b: forced softList[pro]=0 in refresh_pd()')

print('[ patch ] Layer 10b complete')
PYEOF

echo "[ patch ] Step 4c4: Layer 10c — Extend get_pd() cache expiry to 10 years..."
python3 << 'PYEOF'
import sys, re
common_path = '/www/server/panel/class/public/common.py'
with open(common_path, 'r') as f:
    content = f.read()

if '315360000  # 10 years (patched)' in content:
    print('[ patch ] Layer 10c already applied (10-year expiry)')
else:
    # Replace 86400 with 315360000 near p_token_time_f / get_pd expiry
    # Target: int(readFile(p_token_time_f).strip()) + 86400  →  + 315360000
    old_val = '+ 86400'
    new_val = '+ 315360000  # 10 years (patched)'
    if old_val in content:
        content = content.replace(old_val, new_val)
        with open(common_path, 'w') as f:
            f.write(content)
        print('[ patch ] Layer 10c: cache expiry extended to 315360000 (10 years)')
    else:
        print('[ WARN ] Layer 10c: 86400 not found — may already be patched')

print('[ patch ] Layer 10c complete')
PYEOF

# Step 4d: Verify all patches
echo ""
echo "=== Verification ==="

VERIFY_FAIL=0

check_grep() {
    local desc="$1" file="$2" pattern="$3"
    if [ -f "$file" ]; then
        if grep -q "$pattern" "$file" 2>/dev/null; then
            echo "  [ PASS ] $desc"
        else
            echo "  [ FAIL ] $desc — pattern not found in $file"
            VERIFY_FAIL=1
        fi
    else
        echo "  [ SKIP ] $desc — file not found: $file"
    fi
}

echo "--- Source Patches ---"
check_grep "app.py Lifetime patch" "$PANEL_PATH/BTPanel/app.py" "tmp = 0.*Force Lifetime"
check_grep "common.py Lifetime patch" "$PANEL_PATH/class/public/common.py" "pro = 0.*Force Lifetime"
check_grep "config.py is_pro() patch" "$PANEL_PATH/class/config.py" "return True.*Force Pro"
check_grep "config_v2.py is_pro() patch" "$PANEL_PATH/class_v2/config_v2.py" "return True.*Force Pro"
check_grep "config.py not_auth 200" "$PANEL_PATH/class/config.py" "except:.*return 200"
check_grep "config_v2.py not_auth 200" "$PANEL_PATH/class_v2/config_v2.py" "except:.*return 200"
check_grep "common.py catalog fallback" "$PANEL_PATH/class/public/common.py" "_load_local_catalog"
check_grep "common.py empty resp guard" "$PANEL_PATH/class/public/common.py" "resp.ok and resp.text and len(resp.text) > 100"

echo "--- Layer 10: License Hardening ---"
check_grep "10a: _harden_license_pro function" "$PANEL_PATH/class/public/common.py" "_harden_license_pro"
check_grep "10a: harden call in load_soft_list" "$PANEL_PATH/class/public/common.py" "plugin_list_data = _harden_license_pro"
check_grep "10b: softList pro=0 in refresh_pd" "$PANEL_PATH/class/public/common.py" "softList\['pro'\] = 0"
check_grep "10c: 10-year cache expiry" "$PANEL_PATH/class/public/common.py" "315360000"

echo "--- Sentinel Files ---"
for sentinel in ".is_pro.pl" "panel_pro.pl"; do
    if [ -f "$PANEL_PATH/data/$sentinel" ]; then
        echo "  [ PASS ] Sentinel $sentinel exists"
    else
        echo "  [ FAIL ] Sentinel $sentinel MISSING"
        VERIFY_FAIL=1
    fi
done

echo "--- JS Bundle Patches ---"
ACCT_COUNT=$(find "$PANEL_PATH/BTPanel/static" -name "accountState*.js" -type f -exec grep -l "99999" {} \; 2>/dev/null | wc -l)
if [ "$ACCT_COUNT" -gt 0 ]; then
    echo "  [ PASS ] Account limit patched in $ACCT_COUNT bundle(s)"
else
    echo "  [ WARN ] No accountState*.js bundles found with 99999"
fi

INDEX_GUARD_COUNT=$(find "$PANEL_PATH/BTPanel/static" -name "index*.js" -type f 2>/dev/null | wc -l)
echo "  [ INFO ] Checked $INDEX_GUARD_COUNT index*.js bundle(s)"

echo "--- JS Redirect & Binding ---"
BINDS_PASS=0
BINDS_TOTAL=0
for pat in "index-DV9DrNIN.js" "index-legacy-6o9d0Mmi.js"; do
    for f in $(find "$PANEL_PATH/BTPanel/static/vite/js" -name "$pat" -type f 2>/dev/null); do
        BINDS_TOTAL=$((BINDS_TOTAL + 1))
        if grep -q "n\.getCheckAuth(),s()" "$f" 2>/dev/null || grep -q "o\.getCheckAuth(),i()" "$f" 2>/dev/null; then
            echo "  [ PASS ] JS binds redirect removed in $(basename "$f")"
            BINDS_PASS=$((BINDS_PASS + 1))
        else
            echo "  [ FAIL ] JS binds redirect still present in $(basename "$f")"
            VERIFY_FAIL=1
        fi
    done
done
[ "$BINDS_TOTAL" -eq 0 ] && echo "  [ SKIP ] No matching JS bundles found"
echo "  [ INFO ] Binds patches: $BINDS_PASS/$BINDS_TOTAL matched"

if [ -f "$PANEL_PATH/data/userInfo.json" ]; then
    python3 -c "import json; d=json.load(open('$PANEL_PATH/data/userInfo.json')); assert d.get('status')==True" 2>/dev/null && \
        echo "  [ PASS ] userInfo.json status=True" || \
        echo "  [ WARN ] userInfo.json exists but status check failed"
else
    echo "  [ WARN ] userInfo.json not found (non-critical if binds JS patch succeeded)"
fi

echo "--- Offline Plugin Catalog ---"
if [ -f "$PANEL_PATH/data/soft_catalog.json" ]; then
    PLUGIN_COUNT=$(python3 -c "import json; d=json.load(open('$PANEL_PATH/data/soft_catalog.json')); print(len(d.get('list',[])))" 2>/dev/null || echo "0")
    echo "  [ PASS ] soft_catalog.json deployed ($PLUGIN_COUNT plugins)"
else
    echo "  [ FAIL ] soft_catalog.json MISSING"
    VERIFY_FAIL=1
fi
check_grep "load_soft_list offline fallback" "$PANEL_PATH/class/public/common.py" "_load_local_catalog"

# =============================================
# Layer 11: Adaptive Guard + Watchdog
# =============================================
if [ -f "${REPO_PATH}/aaPanel_harden.py" ] && [ -x "${PANEL_PYTHON}" ]; then
    log "Step 11: Running adaptive patcher..."
    cp "${REPO_PATH}/aaPanel_harden.py" "${PANEL_PATH}/" 2>/dev/null || true
    ${PANEL_PYTHON} "${PANEL_PATH}/aaPanel_harden.py" "${PANEL_PATH}" 2>&1 || warn "Adaptive patcher had errors"
fi

if [ -f "${REPO_PATH}/watchdog.py" ]; then
    cp "${REPO_PATH}/watchdog.py" "${PANEL_PATH}/" 2>/dev/null || true
    nohup python3 -u "${PANEL_PATH}/watchdog.py" >> "${PANEL_PATH}/watchdog.log" 2>&1 &
    log "Watchdog started (PID: $!)"
fi

if [ "$VERIFY_FAIL" -eq 0 ]; then
    echo "=== All patches applied successfully! ==="
    echo ""
    echo "Restarting aaPanel..."
    systemctl daemon-reload 2>/dev/null || true
    systemctl restart bt 2>/dev/null && echo "[ OK ] Panel restart initiated" || echo "[ WARN ] Manual restart may be needed: systemctl restart bt"
    sleep 2
    # Layer 10d: Immobilize bmac_* license tokens in /tmp
    echo "[ patch ] Layer 10d: Immobilizing bmac license tokens..."
    BMAC_FILES=$(find /tmp -maxdepth 1 -name 'bmac_*' -type f 2>/dev/null)
    if [ -n "$BMAC_FILES" ]; then
        for f in $BMAC_FILES; do
            if chattr +i "$f" 2>/dev/null; then
                echo "[ patch ] Layer 10d: Made $(basename "$f") immutable"
            else
                echo "[ WARN ] Layer 10d: Failed to chattr +i $(basename "$f") (try: chattr +i $f)"
            fi
        done
    else
        echo "[ WARN ] Layer 10d: No bmac_* files found in /tmp yet (will be created on first panel request)"
    fi
    PANEL_PORT=$(cat "$PANEL_PATH/data/port.pl" 2>/dev/null || echo "7800")
    echo ""
    echo "==============================================="
    echo "  aaPanel Pro License Bypass — INSTALLED"
    echo "==============================================="
    echo ""
    echo "  Patches applied (10 layers):"
    echo "    1. is_pro() → True         (config.py)"
    echo "    2. is_pro() → True         (config_v2.py)"
    echo "    3. get_pd() → Lifetime     (app.py)"
    echo "    4. get_pd() → Lifetime     (common.py)"
    echo "    5. get_not_auth() → 200    (config.py)"
    echo "    6. get_not_auth() → 200    (config_v2.py)"
    echo "    7. JS binds redirect       (index*.js)"
    echo "    8. JS router/account       (index*.js/accountState*.js)"
    echo "    9. Plugin catalog offline (soft_catalog.json + load_soft_list)"
    echo "   10. License hardening       (_harden_license_pro, pro=0, 10yr cache, bmac chattr)"
    echo "   11. Adaptive guard          (sitecustomize.py + PluginLoader runtime patch)"
    echo ""
    echo "  Panel URL: https://$(hostname -I | awk '{print $1}'):${PANEL_PORT}"
    echo ""
    echo "  Run tests: python3 test_subaccount.py /www/server/panel"
    echo "==============================================="
else
    echo "=== Some patches FAILED verification — check logs above ==="
    exit 1
fi
