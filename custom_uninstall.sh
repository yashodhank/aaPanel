#!/bin/bash
# =============================================================================
# aaPanel Pro License Hardener — uninstaller / full revert
# =============================================================================
# Reverses custom_install.sh: stops the watchdog, removes the runtime guard,
# restores every patched file from the backup manifest, removes files the
# installer created, releases any immutable /tmp/bmac_* left by an OLD install,
# clears caches, and restarts the panel.
#
# Idempotent and safe to run twice.
#
# Usage:  sudo bash custom_uninstall.sh [/www/server/panel] [BACKUP_DIR]
#   BACKUP_DIR optional; if omitted, the newest backup_pro_patch_* is used.
# =============================================================================

set -uo pipefail

PANEL_PATH="${1:-/www/server/panel}"
SERVICE="aapanel-guard"
PIDFILE="$PANEL_PATH/data/.aap_watchdog.pid"
HARDEN="$PANEL_PATH/aaPanel_harden.py"

log()  { echo "[ $(date +%H:%M:%S) ] $*"; }
warn() { echo "[ WARN ] $*" >&2; }
die()  { echo "[ FAIL ] $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root"
[ -d "$PANEL_PATH" ] || die "aaPanel not found at $PANEL_PATH"

# Resolve backup dir: explicit arg, else newest backup_pro_patch_*.
BACKUP_DIR="${2:-}"
if [ -z "$BACKUP_DIR" ]; then
    BACKUP_DIR="$(ls -1dt "$PANEL_PATH"/backup_pro_patch_* 2>/dev/null | head -n1 || true)"
fi
PANEL_PYTHON="$PANEL_PATH/pyenv/bin/python3"
[ -x "$PANEL_PYTHON" ] || PANEL_PYTHON="$(command -v python3 || true)"

echo "=== aaPanel Pro License Hardener — uninstall ==="
log "panel:  $PANEL_PATH"
log "backup: ${BACKUP_DIR:-<none found>}"

# --- Step 1: stop + remove the watchdog --------------------------------------
log "Step 1: stopping watchdog..."
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}.service"; then
    systemctl disable --now "${SERVICE}.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE}.service"
    systemctl daemon-reload 2>/dev/null || true
    log "  systemd unit removed"
fi
if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
fi
# belt-and-suspenders: kill any stray watchdog.py
pkill -f "$PANEL_PATH/watchdog.py" 2>/dev/null || true
log "  watchdog stopped"

# --- Step 2: remove the runtime guard ----------------------------------------
log "Step 2: removing runtime guard..."
if [ -f "$HARDEN" ] && [ -n "$PANEL_PYTHON" ]; then
    "$PANEL_PYTHON" "$HARDEN" "$PANEL_PATH" --remove-guard || warn "guard removal reported issues"
else
    warn "aaPanel_harden.py not found — skipping guard removal"
fi

# --- Step 3: release immutable bmac tokens (from old chattr-based installs) ---
log "Step 3: releasing /tmp/bmac_* ..."
for f in /tmp/bmac_*; do
    [ -e "$f" ] || continue
    chattr -i "$f" 2>/dev/null || true   # no-op if not immutable
    rm -f "$f" 2>/dev/null || true       # panel regenerates on next request
done

# --- Step 4: restore patched files from the backup manifest -------------------
if [ -n "$BACKUP_DIR" ] && [ -f "$BACKUP_DIR/manifest.txt" ]; then
    log "Step 4: restoring files from manifest..."
    while IFS= read -r rel; do
        [ -z "$rel" ] && continue
        if [ -f "$BACKUP_DIR/$rel" ]; then
            mkdir -p "$PANEL_PATH/$(dirname "$rel")"
            cp -a "$BACKUP_DIR/$rel" "$PANEL_PATH/$rel" && log "  restored $rel"
        else
            warn "backup missing for $rel — left as-is"
        fi
    done < "$BACKUP_DIR/manifest.txt"
else
    warn "Step 4: no manifest found — falling back to anchor-based unpatch is not"
    warn "        available; if the panel source is still patched, restore from a"
    warn "        backup_pro_patch_* dir manually."
fi

# --- Step 5: remove files the installer CREATED (not pre-existing) ------------
if [ -n "$BACKUP_DIR" ] && [ -f "$BACKUP_DIR/created.txt" ]; then
    log "Step 5: removing installer-created files..."
    while IFS= read -r line; do
        rel="${line#new:}"
        [ -z "$rel" ] && continue
        rm -f "$PANEL_PATH/$rel" && log "  removed $rel"
    done < "$BACKUP_DIR/created.txt"
fi
# soft_catalog.json is installer-managed; drop it so the panel uses live cloud data.
rm -f "$PANEL_PATH/data/soft_catalog.json" 2>/dev/null && log "  removed data/soft_catalog.json" || true
rm -f "$PANEL_PATH/data/email_domain_blocklist.json" 2>/dev/null && log "  removed data/email_domain_blocklist.json" || true
rm -f "$PANEL_PATH/aaPanel_provision.py" 2>/dev/null || true
rm -f "$PANEL_PATH/data/provision_log.jsonl" 2>/dev/null || true

# --- Step 6: clear caches + staged tooling -----------------------------------
log "Step 6: clearing caches..."
find "$PANEL_PATH/class" "$PANEL_PATH/class_v2" "$PANEL_PATH/BTPanel" \
     -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -f "$HARDEN" "$PANEL_PATH/watchdog.py" 2>/dev/null || true

# --- Step 7: restart panel ---------------------------------------------------
log "Step 7: restarting panel..."
systemctl daemon-reload 2>/dev/null || true
if systemctl restart bt 2>/dev/null; then
    log "  panel restart issued (systemctl)"
elif [ -x /etc/init.d/bt ]; then
    /etc/init.d/bt restart >/dev/null 2>&1 && log "  panel restart issued (init.d)" \
        || warn "restart manually: /etc/init.d/bt restart"
else
    warn "restart the panel manually"
fi

cat <<DONE

===============================================================
  aaPanel Pro License Hardener — UNINSTALLED
===============================================================
  Watchdog + guard removed, patched files restored from backup,
  installer-created files cleaned up, panel restarted.

  The backup dir is kept for safety:
    ${BACKUP_DIR:-<none>}
  Remove it once you've confirmed the panel is healthy.
===============================================================
DONE
exit 0
