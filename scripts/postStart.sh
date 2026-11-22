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

# fpp_install.sh only runs on first install, so refresh the display script
# FPP's playlist runs from its scripts directory whenever the plugin's copy
# has changed (i.e. after a plugin update).
SRC_SCRIPT="${PLUGIN_DIR}/scripts/nnl_display_image.py"
DST_SCRIPT=/home/fpp/media/scripts/nnl_display_image.py
if [ -f "$SRC_SCRIPT" ] && ! cmp -s "$SRC_SCRIPT" "$DST_SCRIPT"; then
    cp "$SRC_SCRIPT" "$DST_SCRIPT" && chmod 755 "$DST_SCRIPT"
    chown fpp:fpp "$DST_SCRIPT" 2>/dev/null || true
fi

# Settings (incl. show tokens) live in plugindata/, 0600, owned by fpp
# (FPP guidelines §14.4/§14.11). Move a pre-0.4.0 copy out of config/, and
# re-assert ownership/mode on every start.
SETTINGS="${DATA_DIR}/settings.json"
LEGACY_SETTINGS="${PLUGIN_DIR}/config/settings.json"
mkdir -p "$DATA_DIR"
chown fpp:fpp "$DATA_DIR" 2>/dev/null || true
if [ -f "$LEGACY_SETTINGS" ] && [ ! -f "$SETTINGS" ]; then
    mv -f "$LEGACY_SETTINGS" "$SETTINGS"
elif [ -f "$LEGACY_SETTINGS" ]; then
    rm -f "$LEGACY_SETTINGS"   # newer copy already in plugindata/
fi
if [ -f "$SETTINGS" ]; then
    chown fpp:fpp "$SETTINGS" 2>/dev/null || true
    chmod 600 "$SETTINGS"
fi

PYTHON_BIN="$(nnl_python)"
mkdir -p "$(dirname "$LOGFILE")"

# The daemon writes its own log through a WatchedFileHandler (so it follows
# FPP's rotation of plugin-*.log, §1.2) -- don't hold the file open here.
cd "${PLUGIN_DIR}/daemon"
NNL_LOGFILE="$LOGFILE" nohup "$PYTHON_BIN" nnl_daemon.py > /dev/null 2>&1 < /dev/null &
echo $! > "$PIDFILE"

#postStart
