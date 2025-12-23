#!/bin/sh
# Obico Link/Relink Script
# Usage: ./link.sh [--relink|-r]

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
RINKHALS_HOME="${RINKHALS_HOME:-/useremain/home/rinkhals}"
OBICO_CFG="$RINKHALS_HOME/printer_data/config/moonraker-obico.cfg"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

# Parse arguments
RELINK=0
for arg in "$@"; do
    case "$arg" in
        --relink|-r) RELINK=1 ;;
    esac
done

# Validate
[ ! -d "$OBICO_DIR" ] && { log "ERROR: Obico not found"; exit 1; }
[ ! -f "$OBICO_CFG" ] && { log "ERROR: Config not found, run install.sh first"; exit 1; }

# Handle relink
if [ "$RELINK" -eq 1 ]; then
    log "Re-linking (removing existing token)..."
    grep -q "auth_token" "$OBICO_CFG" && sed -i '/auth_token/d' "$OBICO_CFG"
else
    log "Linking printer to Obico..."
fi

# Activate venv and set paths
cd "$APP_ROOT"
python -m venv --without-pip .
. bin/activate
export PYTHONPATH="$APP_ROOT/lib/python3.11/site-packages:$OBICO_DIR:$PYTHONPATH"

cd "$OBICO_DIR"
python -m moonraker_obico.link -c "$OBICO_CFG"
