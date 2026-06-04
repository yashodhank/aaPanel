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

echo ""
if [ "$VERIFY_FAIL" -eq 0 ]; then
    echo "=== All patches applied successfully! ==="
    echo "You may need to restart aaPanel: systemctl restart bt"
else
    echo "=== Some patches FAILED verification — check logs above ==="
    exit 1
fi
