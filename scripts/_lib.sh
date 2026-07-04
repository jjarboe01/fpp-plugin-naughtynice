# Shared helpers for this plugin's lifecycle scripts. Not directly executed
# by FPP — sourced by preStart.sh/postStart.sh/postStop.sh.
#
# Cross-user note (same story as Remote Falcon's plugin, which this pattern
# is borrowed from): FPP's command system runs these scripts as root, but a
# developer testing manually over SSH may run as the fpp user. Try a direct
# signal first, fall back to passwordless sudo, so it works either way.

PLUGIN_DIR=/home/fpp/media/plugins/fpp-plugin-naughtynice
PIDFILE="${PLUGIN_DIR}/nnl_daemon.pid"
LOGFILE=/home/fpp/media/logs/fpp-plugin-naughtynice-daemon.log

nnl_signal() {
    local sig="$1" pid="$2"
    kill -"$sig" "$pid" 2>/dev/null || sudo -n kill -"$sig" "$pid" 2>/dev/null || true
}

nnl_alive() {
    kill -0 "$1" 2>/dev/null || sudo -n kill -0 "$1" 2>/dev/null
}

nnl_stop_if_running() {
    if [ -f "$PIDFILE" ]; then
        local pid
        pid=$(cat "$PIDFILE" 2>/dev/null)
        if [ -n "$pid" ] && nnl_alive "$pid"; then
            nnl_signal TERM "$pid"
            for i in 1 2 3 4 5; do
                nnl_alive "$pid" || break
                sleep 1
            done
            nnl_alive "$pid" && nnl_signal KILL "$pid"
        fi
        rm -f "$PIDFILE"
    else
        pkill -f nnl_daemon.py 2>/dev/null || sudo -n pkill -f nnl_daemon.py 2>/dev/null || true
    fi
}

# Prefer the venv Python fpp_install.sh created; fall back to system python3
# if the venv wasn't created (e.g. python3-venv missing on this FPP image).
nnl_python() {
    if [ -x "${PLUGIN_DIR}/venv/bin/python3" ]; then
        echo "${PLUGIN_DIR}/venv/bin/python3"
    else
        command -v python3
    fi
}
