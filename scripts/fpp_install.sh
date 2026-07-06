#!/bin/bash
# Runs once when FPP first installs this plugin (not re-run on upgrade —
# see fpp's install_plugin script). Sets up an isolated Python venv so this
# plugin's dependencies (requests, Pillow) never conflict with whatever
# Python packages fppd/other plugins expect from the system interpreter.
#
# IMPORTANT: FPP runs plugin lifecycle scripts (this one included) as root,
# but the FPP web UI's PHP runs as the "fpp" user (see php-fpm pool config).
# Anything this script creates under $PLUGIN_DIR — config/, venv/ — ends up
# root-owned unless explicitly chown'd back, which then silently blocks
# content.php's writes to config/settings.json (file_put_contents() just
# fails, no error surfaced) even though everything looks fine in the UI.
# Learned the hard way: this exact bug made the environment toggle's "Save"
# appear to work but never actually persist, so it always re-rendered
# defaults (environment=prod) on the next page load. Always chown back to
# fpp:fpp at the end of this script.

set -e
PLUGIN_DIR=/home/fpp/media/plugins/fpp-plugin-naughtynice
SCRIPTS_DIR=/home/fpp/media/scripts
cd "$PLUGIN_DIR"

mkdir -p config

VENV_OK=0
if command -v python3 >/dev/null 2>&1; then
    if python3 -m venv venv 2>/tmp/nnl_venv_err.log; then
        ./venv/bin/pip install --quiet --upgrade pip
        ./venv/bin/pip install --quiet -r daemon/requirements.txt
        echo "NaughtyNice Cloud: venv created and dependencies installed."
        VENV_OK=1
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

# Deploy the PhotoZone display script into FPP's own scripts directory so
# playlist "script" entries can reference it by name. IMPORTANT: FPP's
# eventScript wrapper always runs .py scripts under the bare system
# python3 (ignoring this file's shebang/executable bit entirely — see the
# script's own docstring), so it's deliberately stdlib-only + ffmpeg
# rather than depending on this plugin's venv/Pillow.
mkdir -p "$SCRIPTS_DIR"
cp "$PLUGIN_DIR/scripts/nnl_display_image.py" "$SCRIPTS_DIR/nnl_display_image.py"
chmod +x "$SCRIPTS_DIR/nnl_display_image.py"
chown fpp:fpp "$SCRIPTS_DIR/nnl_display_image.py" 2>/dev/null || true

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "NaughtyNice Cloud: WARNING — ffmpeg not found on this system. The photo/silhouette" \
         "overlay (nnl_display_image.py) needs it to decode current_display.png; ticker text" \
         "will still work without it, but photos won't display until ffmpeg is installed" \
         "(e.g. 'sudo apt-get install -y ffmpeg')." >&2
fi

# This script runs as root; hand ownership of everything it touched back to
# the fpp user so both php-fpm (content.php's settings save) and the daemon
# (launched as fpp unless FPP's own hooks override that) can read/write it.
chown -R fpp:fpp "$PLUGIN_DIR/config" "$PLUGIN_DIR/venv" 2>/dev/null || true

# Auto-provision PhotoZone/TickerZone Pixel Overlay Models + the breaking-
# news playlist so the plugin is ready to run immediately, without the
# customer hand-building anything in FPP first. Never fails the install —
# fppd may not be fully up yet on a very first boot, or the customer may not
# have a channel output configured yet; either way this can be re-run later
# from the plugin's setup page ("Re-run zone setup").
if [ "$VENV_OK" = "1" ]; then
    echo "NaughtyNice Cloud: auto-provisioning zones + playlist..."
    (cd "$PLUGIN_DIR/daemon" && "$PLUGIN_DIR/venv/bin/python3" fpp_provision.py) || \
        echo "NaughtyNice Cloud: zone auto-setup did not complete — use 'Re-run zone setup' on the plugin's setup page once FPP is fully up."
    chown -R fpp:fpp "$PLUGIN_DIR/config" 2>/dev/null || true
else
    echo "NaughtyNice Cloud: skipping zone auto-setup (no venv) — run it manually from the plugin's setup page once dependencies are installed."
fi

echo "fpp-plugin-naughtynice installed"

#fpp_install
