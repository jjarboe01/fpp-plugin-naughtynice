#!/bin/bash
# Runs once when FPP first installs this plugin (not re-run on upgrade —
# see fpp's install_plugin script). Sets up an isolated Python venv so this
# plugin's dependencies (requests, Pillow) never conflict with whatever
# Python packages fppd/other plugins expect from the system interpreter.

set -e
PLUGIN_DIR=/home/fpp/media/plugins/fpp-plugin-naughtynice
cd "$PLUGIN_DIR"

mkdir -p config

if command -v python3 >/dev/null 2>&1; then
    if python3 -m venv venv 2>/tmp/nnl_venv_err.log; then
        ./venv/bin/pip install --quiet --upgrade pip
        ./venv/bin/pip install --quiet -r daemon/requirements.txt
        echo "NaughtyNice Cloud: venv created and dependencies installed."
    else
        echo "NaughtyNice Cloud: venv creation failed (see /tmp/nnl_venv_err.log)," \
             "falling back to --user pip install against the system python3."
        cat /tmp/nnl_venv_err.log 2>/dev/null || true
        python3 -m pip install --quiet --user -r daemon/requirements.txt || \
            echo "NaughtyNice Cloud: pip install failed — install requests+Pillow manually before enabling the plugin."
    fi
else
    echo "NaughtyNice Cloud: python3 not found on this system — the daemon cannot run." >&2
fi

echo "fpp-plugin-naughtynice installed"

#fpp_install
