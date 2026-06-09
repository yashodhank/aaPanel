#!/bin/bash
# =============================================================================
# aaPanel Pro License Hardener — surgical installer
# =============================================================================
# Turns a free/trial aaPanel into permanent-Pro on a self-hosted box.
#
# Design (vs the old installer):
#   * SURGICAL — never `cp -r` whole source trees over the live panel (that
#     downgrades unrelated modules on a version-mismatched target). All source
#     edits are applied in place by aaPanel_harden.py via content anchors.
#   * SINGLE SOURCE OF TRUTH — every Python edit lives in aaPanel_harden.py.
#     This script orchestrates; it does not re-implement patches.
#   * FAIL LOUD, FAIL EARLY — a pre-flight --check aborts BEFORE any write if a
#     patch anchor is missing (e.g. a future aaPanel moved it).
#   * REVERSIBLE — every touched file is backed up under a timestamped dir with a
#     manifest; custom_uninstall.sh restores from it.
#   * ROBUST — no `set -e` landmines, all helpers defined, optional steps cannot
#     abort the run, and the panel always gets restarted + a status banner.
#
# Usage:  sudo bash custom_install.sh [/www/server/panel]
# =============================================================================

set -uo pipefail

REPO_PATH="$(cd "$(dirname "$0")" && pwd)"
PANEL_PATH="${1:-/www/server/panel}"
TS="$(date +%s)"
BACKUP_DIR="$PANEL_PATH/backup_pro_patch_$TS"
HARDEN="$PANEL_PATH/aaPanel_harden.py"
WATCHDOG="$PANEL_PATH/watchdog.py"
PIDFILE="$PANEL_PATH/data/.aap_watchdog.pid"
SERVICE="aapanel-guard"

# Panel's own interpreter (has Flask + panel deps); fall back to system python3.
PANEL_PYTHON="$PANEL_PATH/pyenv/bin/python3"
[ -x "$PANEL_PYTHON" ] || PANEL_PYTHON="$(command -v python3 || true)"

log()  { echo "[ $(date +%H:%M:%S) ] $*"; }
warn() { echo "[ WARN ] $*" >&2; }
die()  { echo "[ FAIL ] $*" >&2; exit 1; }

backup_file() {  # back up a single file into BACKUP_DIR + append to manifest
    local f="$1" rel
    [ -f "$f" ] || return 0
    rel="${f#"$PANEL_PATH"/}"
    mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
    cp -a "$f" "$BACKUP_DIR/$rel" 2>/dev/null || return 0
    echo "$rel" >> "$BACKUP_DIR/manifest.txt"
}

echo "=== aaPanel Pro License Hardener — installer ==="

# --- Pre-conditions ----------------------------------------------------------
[ "$(id -u)" -eq 0 ]    || die "must run as root"
[ -d "$PANEL_PATH" ]    || die "aaPanel not found at $PANEL_PATH"
[ -n "$PANEL_PYTHON" ]  || die "no python3 interpreter found"
[ -f "$REPO_PATH/aaPanel_harden.py" ] || die "aaPanel_harden.py missing next to this script"

mkdir -p "$BACKUP_DIR" || die "cannot create backup dir"
: > "$BACKUP_DIR/manifest.txt"
echo "$PANEL_PYTHON" > "$BACKUP_DIR/.panel_python"
log "backup dir: $BACKUP_DIR"

# --- Step 1: stage tooling into the panel ------------------------------------
# So the watchdog and uninstaller can find them at a stable path.
log "Step 1: staging tooling into panel..."
cp -f "$REPO_PATH/aaPanel_harden.py" "$HARDEN"   || die "failed to stage aaPanel_harden.py"
[ -f "$REPO_PATH/watchdog.py" ] && cp -f "$REPO_PATH/watchdog.py" "$WATCHDOG"
[ -f "$REPO_PATH/aaPanel_provision.py" ] && cp -f "$REPO_PATH/aaPanel_provision.py" "$PANEL_PATH/aaPanel_provision.py"
if [ -f "$REPO_PATH/data/soft_catalog.json" ]; then
    backup_file "$PANEL_PATH/data/soft_catalog.json"
    cp -f "$REPO_PATH/data/soft_catalog.json" "$PANEL_PATH/data/soft_catalog.json"
    log "  offline plugin catalog deployed"
fi
if [ -f "$REPO_PATH/data/email_domain_blocklist.json" ]; then
    backup_file "$PANEL_PATH/data/email_domain_blocklist.json"
    cp -f "$REPO_PATH/data/email_domain_blocklist.json" "$PANEL_PATH/data/email_domain_blocklist.json"
    log "  email domain blocklist deployed"
fi

# --- Step 2: pre-flight anchor check (fail loud BEFORE any write) ------------
log "Step 2: pre-flight anchor check ($PANEL_PATH)..."
if ! "$PANEL_PYTHON" "$HARDEN" "$PANEL_PATH" --check; then
    die "anchor check failed — refusing to edit source (possible version drift).
        Review the [FAIL] lines above; add a VERSION_OVERRIDES entry in
        aaPanel_harden.py for this aaPanel version, then re-run."
fi

# --- Step 3: apply source patches + runtime guard (backed up) -----------------
log "Step 3: applying source patches + guard..."
"$PANEL_PYTHON" "$HARDEN" "$PANEL_PATH" --apply --backup-dir "$BACKUP_DIR" \
    || die "source patching failed — see [FAIL] lines above. Backup at $BACKUP_DIR"

# --- Step 4: sentinel + identity data files ----------------------------------
log "Step 4: sentinel + identity files..."
for s in ".is_pro.pl" "panel_pro.pl"; do
    f="$PANEL_PATH/data/$s"
    if [ ! -f "$f" ]; then
        [ "$s" = "panel_pro.pl" ] && echo -n "true" > "$f" || : > "$f"
        chmod 644 "$f" 2>/dev/null || true
        echo "new:$s" >> "$BACKUP_DIR/created.txt"   # uninstall removes these
        log "  created $s"
    fi
done

UI="$PANEL_PATH/data/userInfo.json"
if [ ! -f "$UI" ]; then
    "$PANEL_PYTHON" - "$UI" <<'PYEOF' && echo "new:data/userInfo.json" >> "$BACKUP_DIR/created.txt"
import json, sys, uuid, time
ui = {"status": True, "is_bind": True, "uid": 1, "id": 1,
      "username": "admin", "email": "admin@localhost",
      "token": "%s.%s.%s" % (uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
      "server_id": "patched_%d" % int(time.time()),
      "access_key": "patched_access_key"}
open(sys.argv[1], "w").write(json.dumps(ui))
print("  created userInfo.json (status=true)")
PYEOF
fi

# NOTE: frontend JS bundles (the /binds trial gate, sub-account limit, and router
# "pro" guards) are patched by aaPanel_harden.py in Step 3 above, using
# variable-agnostic structural regexes that survive per-build minification. They
# are backed up into the same manifest, so the uninstaller restores them too.

# --- Step 6: watchdog as a singleton service (reboot-persistent) -------------
log "Step 6: watchdog service..."
if [ -f "$WATCHDOG" ] && command -v systemctl >/dev/null 2>&1; then
    cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=aaPanel license guard watchdog
After=network.target

[Service]
Type=simple
ExecStart=$PANEL_PYTHON -u $WATCHDOG $PANEL_PATH
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now "${SERVICE}.service" 2>/dev/null \
        && log "  watchdog running as systemd unit ${SERVICE}.service" \
        || warn "could not enable ${SERVICE}.service"
elif [ -f "$WATCHDOG" ]; then
    # nohup fallback with PID-file singleton guard
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
        log "  watchdog already running (pid $(cat "$PIDFILE"))"
    else
        nohup "$PANEL_PYTHON" -u "$WATCHDOG" "$PANEL_PATH" >> "$PANEL_PATH/watchdog.log" 2>&1 &
        echo $! > "$PIDFILE"
        log "  watchdog started (pid $!, nohup)"
    fi
fi

# --- Step 7: restart panel ---------------------------------------------------
log "Step 7: restarting panel..."
systemctl daemon-reload 2>/dev/null || true
if systemctl restart bt 2>/dev/null; then
    log "  panel restart issued (systemctl)"
elif [ -x /etc/init.d/bt ]; then
    /etc/init.d/bt restart >/dev/null 2>&1 && log "  panel restart issued (init.d)" \
        || warn "could not restart panel — restart manually: /etc/init.d/bt restart"
else
    warn "could not restart panel — restart manually"
fi
sleep 2

# --- Step 8: runtime verification (advisory — patches are already applied) ----
log "Step 8: runtime verification..."
"$PANEL_PYTHON" "$HARDEN" "$PANEL_PATH" --verify || warn "runtime verify reported issues (see above)"

# --- Done --------------------------------------------------------------------
PANEL_PORT="$(cat "$PANEL_PATH/data/port.pl" 2>/dev/null || echo 8888)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<DONE

===============================================================
  aaPanel Pro License Hardener — INSTALLED
===============================================================
  Source patches   : aaPanel_harden.py (anchor-based, idempotent)
  Runtime guard    : sitecustomize.py (PluginLoader + catalog fallback)
  Watchdog         : ${SERVICE}.service (auto-repair on revert)
  Backup           : $BACKUP_DIR  (manifest.txt drives uninstall)

  Panel URL        : http://${IP}:${PANEL_PORT}
  Verify           : $PANEL_PYTHON $PANEL_PATH/aaPanel_harden.py $PANEL_PATH --verify
  Tests            : $PANEL_PYTHON $PANEL_PATH/test_subaccount.py $PANEL_PATH
  Provision        : $PANEL_PYTHON $PANEL_PATH/aaPanel_provision.py $PANEL_PATH
  Uninstall        : bash custom_uninstall.sh $PANEL_PATH $BACKUP_DIR
===============================================================
DONE
exit 0
