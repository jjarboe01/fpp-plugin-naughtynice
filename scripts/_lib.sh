#!/bin/bash
# Shared helpers for this plugin's lifecycle scripts. Not directly executed
# by FPP — sourced by fpp_install.sh/preStart.sh/postStart.sh/postStop.sh/
# fpp_uninstall.sh. FPP runs all of those as root, so no sudo is needed.

PLUGIN_DIR=/home/fpp/media/plugins/fpp-plugin-naughtynice
PIDFILE="${PLUGIN_DIR}/nnl_daemon.pid"
DATA_DIR=/home/fpp/media/plugindata/fpp-plugin-naughtynice

# Single plugin log in FPP's log directory (rotated + included in the
# Support Zip). getSetting comes from FPP's scripts/common; the subshell and
# set +u keep sourcing it from aborting a set -e/-u caller.
LOGDIR=$( ( set +u; . "${FPPDIR:-/opt/fpp}/scripts/common" >/dev/null 2>&1 && getSetting logDirectory ) 2>/dev/null || true )
[ -n "$LOGDIR" ] || LOGDIR=/home/fpp/media/logs
# FPP guideline §1.1: exactly one log, named plugin-<repoName>.log -- the
# plugin- prefix is what FPP's logrotate and log viewer key on.
LOGFILE="${LOGDIR}/plugin-fpp-plugin-naughtynice.log"

nnl_signal() {
    local sig="$1" pid="$2"
    kill -"$sig" "$pid" 2>/dev/null || true
}

nnl_alive() {
    kill -0 "$1" 2>/dev/null
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
        pkill -f nnl_daemon.py 2>/dev/null || true
    fi
}

# Installs made before the switch to Debian packages have a plugin venv
# (fpp_install.sh isn't re-run on update) -- keep using it if present.
# New installs use the system python3 with python3-requests/python3-pil.
nnl_python() {
    if [ -x "${PLUGIN_DIR}/venv/bin/python3" ]; then
        echo "${PLUGIN_DIR}/venv/bin/python3"
    else
        command -v python3
    fi
}
