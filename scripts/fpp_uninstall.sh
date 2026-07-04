#!/bin/bash
# FPP's plugin manager removes the plugin directory itself after this
# script exits; we only need to make sure the daemon isn't left running
# against a directory that's about to disappear.

. "$(dirname "$0")/_lib.sh" 2>/dev/null || true

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null)
    [ -n "$PID" ] && kill -TERM "$PID" 2>/dev/null || true
    sleep 1
    [ -n "$PID" ] && kill -KILL "$PID" 2>/dev/null || true
    rm -f "$PIDFILE"
else
    pkill -f nnl_daemon.py 2>/dev/null || true
fi

echo "fpp-plugin-naughtynice uninstalled"

#fpp_uninstall
