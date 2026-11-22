#!/bin/bash
# FPP's plugin manager removes the plugin directory itself after this script
# exits. Here we stop the daemon and undo everything the plugin created
# outside that directory (FPP guideline §2.1). Safe to run more than once.
#
# Reinstall (FPP 10.4+) runs this with FPP_PLUGIN_REINSTALL=1, then installs
# again: in that case the show token (plugindata/) and the overlay zones +
# breaking-news playlist are kept, so the show keeps working without being
# set up again (§2.12). Unset (FPP 10.3 and earlier) or anything else means
# a real uninstall, which removes them.

. "$(dirname "$0")/_lib.sh" 2>/dev/null || true
: "${PLUGIN_DIR:=/home/fpp/media/plugins/fpp-plugin-naughtynice}"
: "${DATA_DIR:=/home/fpp/media/plugindata/fpp-plugin-naughtynice}"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null)
    [ -n "$PID" ] && kill -TERM "$PID" 2>/dev/null || true
    sleep 1
    [ -n "$PID" ] && kill -KILL "$PID" 2>/dev/null || true
    rm -f "$PIDFILE"
else
    pkill -f nnl_daemon.py 2>/dev/null || true
fi

# The display script FPP's playlist runs from its scripts directory is
# rewritten by postStart.sh on every start, so it goes either way.
rm -f /home/fpp/media/scripts/nnl_display_image.py

if [ "${FPP_PLUGIN_REINSTALL:-0}" != "1" ]; then
    # Remove the PhotoZone/TickerZone models and the plugin-created
    # playlist through FPP's API. Needs the settings (model/playlist names,
    # FPP URL), so this runs before plugindata/ is deleted.
    PYTHON_BIN="$(nnl_python 2>/dev/null || command -v python3)"
    if [ -n "$PYTHON_BIN" ] && [ -f "${PLUGIN_DIR}/daemon/fpp_provision.py" ]; then
        ( cd "${PLUGIN_DIR}/daemon" && "$PYTHON_BIN" fpp_provision.py --uninstall ) \
            >> "${LOGFILE:-/dev/null}" 2>&1 \
            || echo "fpp-plugin-naughtynice: could not remove every zone/playlist (see the plugin log); remove PhotoZone, TickerZone and breaking_news by hand if they remain."
    fi
    rm -rf "$DATA_DIR"
fi

echo "fpp-plugin-naughtynice uninstalled"
