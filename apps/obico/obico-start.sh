#!/bin/sh

[ -f /useremain/rinkhals/.current/tools.sh ] && . /useremain/rinkhals/.current/tools.sh
RINKHALS_HOME="${RINKHALS_HOME:-/useremain/home/rinkhals}"

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
OBICO_CFG="$RINKHALS_HOME/printer_data/config/moonraker-obico.cfg"
APP_LOG="$RINKHALS_HOME/printer_data/logs/obico-app.log"
OBICO_LOG="$RINKHALS_HOME/printer_data/logs/moonraker-obico.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

wait_for_moonraker() {
    log "Waiting for Moonraker..."
    command -v curl >/dev/null 2>&1 || { log "curl not available, skipping"; return 1; }
    for i in $(seq 1 30); do
        curl -sf http://127.0.0.1:7125/printer/info >/dev/null 2>&1 && { log "Moonraker ready"; return 0; }
        sleep 2
    done
    log "Moonraker timeout, continuing anyway"
    return 1
}

setup_venv() {
    cd "$APP_ROOT"
    [ ! -d "$APP_ROOT/bin" ] && { log "Creating venv..."; python -m venv --without-pip . || return 1; }
    [ ! -f "$APP_ROOT/bin/activate" ] && { log "ERROR: bin/activate not found"; return 1; }
    . bin/activate
    export PYTHONPATH="$APP_ROOT/lib/python3.11/site-packages:$OBICO_DIR:$PYTHONPATH"
    log "Python environment ready"
    return 0
}

check_relink() {
    [ ! -f "$OBICO_CFG" ] && return
    RELINK=$(get_app_property obico relink 2>/dev/null)
    [ "$RELINK" = "Yes" ] && { log "Relink requested"; set_app_property obico relink "No"; sed -i '/auth_token/d' "$OBICO_CFG"; }
}

# Main
log "=========================================="
log "Obico starting (APP_ROOT: $APP_ROOT)"

[ ! -d "$OBICO_DIR" ] && { log "ERROR: $OBICO_DIR not found"; exit 1; }
[ ! -f "$OBICO_CFG" ] && { log "Config missing, running install.sh"; "$APP_ROOT/install.sh" || exit 1; }

check_relink
wait_for_moonraker
setup_venv || exit 1

log "Starting moonraker_obico..."
python -m moonraker_obico.app -c "$OBICO_CFG" >> "$OBICO_LOG" 2>&1 &
OBICO_PID=$!

sleep 2
log "Starting obico_api..."
python "$APP_ROOT/obico_api.py" >> "$APP_LOG" 2>&1 &
API_PID=$!

log "Processes started (obico=$OBICO_PID, api=$API_PID), monitoring..."

while true; do
    sleep 30
    if ! kill -0 $OBICO_PID 2>/dev/null; then
        log "moonraker_obico died, restarting..."
        python -m moonraker_obico.app -c "$OBICO_CFG" >> "$OBICO_LOG" 2>&1 &
        OBICO_PID=$!
    fi
    if ! kill -0 $API_PID 2>/dev/null; then
        log "obico_api died, restarting..."
        python "$APP_ROOT/obico_api.py" >> "$APP_LOG" 2>&1 &
        API_PID=$!
    fi
done
