#!/bin/sh
# Obico App for Rinkhals - Main entry point

. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname $(realpath $0))
OBICO_DIR="$APP_ROOT/moonraker-obico"
OBICO_CFG="$RINKHALS_HOME/printer_data/config/moonraker-obico.cfg"
OBICO_LOG="$RINKHALS_HOME/printer_data/logs/moonraker-obico.log"
API_LOG="$RINKHALS_HOME/printer_data/logs/obico-api.log"

mkdir -p "$RINKHALS_HOME/printer_data/config"
mkdir -p "$RINKHALS_HOME/printer_data/logs"

sync_config() {
    [ ! -f "$OBICO_CFG" ] && return
    RELINK=$(get_app_property obico relink 2>/dev/null)
    if [ "$RELINK" = "Yes" ]; then
        log "Relink requested, clearing token..."
        set_app_property obico relink "No" 2>/dev/null
        sed -i '/auth_token/d' "$OBICO_CFG"
    fi
}

check_installation() {
    [ ! -d "$OBICO_DIR" ] && { log "ERROR: Obico not found"; return 1; }
    [ ! -f "$OBICO_CFG" ] && "$APP_ROOT/install.sh" --config-only
    return 0
}

wait_for_moonraker() {
    i=0; while [ $i -lt 20 ]; do
        curl -sf http://127.0.0.1:7125/printer/info >/dev/null 2>&1 && return 0
        sleep 3; i=$((i+1))
    done
    return 1
}

activate_venv() {
    cd "$APP_ROOT"
    python -m venv --without-pip .
    . bin/activate
    export PYTHONPATH="$APP_ROOT/lib/python3.11/site-packages:$OBICO_DIR:$PYTHONPATH"
}

version() {
    grep '"version"' "$APP_ROOT/app.json" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || echo "unknown"
}

status() {
    PIDS=$(get_by_name moonraker_obico)
    [ -z "$PIDS" ] && report_status $APP_STATUS_STOPPED || report_status $APP_STATUS_STARTED "$PIDS"
}

start() {
    check_installation || return 1
    stop
    sync_config

    log "Waiting for Moonraker..."
    wait_for_moonraker || log "WARNING: Moonraker not ready, starting anyway"

    log "Starting Obico..."
    activate_venv

    python -m moonraker_obico.app -c "$OBICO_CFG" >> "$OBICO_LOG" 2>&1 &
    python "$APP_ROOT/obico_api.py" >> "$API_LOG" 2>&1 &

    sleep 2
    PIDS=$(get_by_name moonraker_obico)
    [ -n "$PIDS" ] && log "Obico started (PID: $PIDS)" || log "WARNING: Check $OBICO_LOG"
}

stop() {
    kill_by_name moonraker_obico
    kill_by_name obico_api
    pkill -f "obico_api.py" 2>/dev/null || true
    pkill -f "moonraker_obico.app" 2>/dev/null || true
    sleep 1
}

debug() {
    check_installation || return 1
    stop
    activate_venv
    python -m moonraker_obico.app -c "$OBICO_CFG" "$@"
}

case "$1" in
    version) version ;;
    status)  status ;;
    start)   start ;;
    stop)    stop ;;
    debug)   shift; debug "$@" ;;
    *)       echo "Usage: $0 {version|status|start|stop|debug}" >&2; exit 1 ;;
esac
