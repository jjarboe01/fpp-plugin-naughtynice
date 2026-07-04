#!/bin/bash
# Launch the NaughtyNice Cloud poll daemon as a detached background process.
# A PID file is written so postStop.sh can shut it down cleanly.

. "$(dirname "$0")/_lib.sh"

if [ -f "$PIDFILE" ]; then
    OLDPID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -z "$OLDPID" ] || ! nnl_alive "$OLDPID"; then
        rm -f "$PIDFILE"
    fi
fi

# Don't start a second daemon if one is already running (e.g. preStart
# somehow didn't run, or this hook fires twice).
if [ -f "$PIDFILE" ]; then
    exit 0
fi

PYTHON_BIN="$(nnl_python)"
mkdir -p "$(dirname "$LOGFILE")"

cd "${PLUGIN_DIR}/daemon"
nohup "$PYTHON_BIN" nnl_daemon.py >> "$LOGFILE" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

#postStart
