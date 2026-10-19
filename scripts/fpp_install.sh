#!/bin/bash
# Runs once when FPP first installs this plugin (not re-run on upgrade —
# see fpp's install_plugin script). Makes sure the Debian packages the
# daemon needs (python3-requests, python3-pil, ffmpeg, fonts-dejavu-core)
# are present, deploys the display script, and auto-provisions the zones.
#
# IMPORTANT: FPP runs plugin lifecycle scripts (this one included) as root,
# but the FPP web UI's PHP runs as the "fpp" user (see php-fpm pool config).
# Anything this script creates under $PLUGIN_DIR (config/) ends up
# root-owned unless explicitly chown'd back, which then silently blocks
# content.php's writes to config/settings.json (file_put_contents() just
# fails, no error surfaced) even though everything looks fine in the UI.
# Learned the hard way: this exact bug made the environment toggle's "Save"
# appear to work but never actually persist, so it always re-rendered
# defaults (environment=prod) on the next page load. Always chown back to
# fpp:fpp at the end of this script.

set -e
. "$(dirname "$0")/_lib.sh"   # PLUGIN_DIR, DATA_DIR, LOGFILE
SCRIPTS_DIR=/home/fpp/media/scripts
cd "$PLUGIN_DIR"

mkdir -p config "$DATA_DIR" "$(dirname "$LOGFILE")"

# Python + system dependencies come from Debian packages (FPP plugin
# guideline §6), not pip. On FPP 10+ the plugin manager has already
# installed these from pluginInfo.json's "dependencies" block before this
# script runs; FPP 9 ignores that block, so install whatever is still
# missing here. Already-present packages are skipped, so this is a no-op
# on FPP 10+.
NNL_APT_PACKAGES="python3-requests python3-pil ffmpeg fonts-dejavu-core"
MISSING=""
for pkg in $NNL_APT_PACKAGES; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
done
if [ -n "$MISSING" ] && command -v apt-get >/dev/null 2>&1; then
    echo "NaughtyNice Cloud: installing$MISSING ..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q $MISSING >>"$LOGFILE" 2>&1 || \
        echo "NaughtyNice Cloud: could not install$MISSING (offline?) — details in $LOGFILE."
fi

DEPS_OK=0
PYTHON_BIN="$(nnl_python || true)"
if [ -n "$PYTHON_BIN" ] && "$PYTHON_BIN" -c "import requests, PIL" >/dev/null 2>&1; then
    DEPS_OK=1
else
    echo "NaughtyNice Cloud: python3 with requests + Pillow not available — the daemon cannot run" \
         "until python3-requests and python3-pil are installed." >&2
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


# fonts-dejavu-core (above) is for the ticker's FPP "Text" overlay effect
# and the Pillow-rendered overlay; fppd only scans fonts at startup, so a
# full FPPD restart picks it up.

# This script runs as root; hand ownership of everything it touched back to
# the fpp user so both php-fpm (content.php's settings save) and the daemon
# (launched as fpp unless FPP's own hooks override that) can read/write it.
chown -R fpp:fpp "$PLUGIN_DIR/config" "$DATA_DIR" 2>/dev/null || true

# Auto-provision PhotoZone/TickerZone Pixel Overlay Models + the breaking-
# news playlist so the plugin is ready to run immediately, without the
# customer hand-building anything in FPP first. Never fails the install —
# fppd may not be fully up yet on a very first boot, or the customer may not
# have a channel output configured yet; either way this can be re-run later
# from the plugin's setup page ("Re-run zone setup").
if [ "$DEPS_OK" = "1" ]; then
    echo "NaughtyNice Cloud: auto-provisioning zones + playlist..."
    (cd "$PLUGIN_DIR/daemon" && "$PYTHON_BIN" fpp_provision.py) || \
        echo "NaughtyNice Cloud: zone auto-setup did not complete — use 'Re-run zone setup' on the plugin's setup page once FPP is fully up."
    chown -R fpp:fpp "$PLUGIN_DIR/config" 2>/dev/null || true
else
    echo "NaughtyNice Cloud: skipping zone auto-setup (Python dependencies missing) — run it manually from the plugin's setup page once dependencies are installed."
fi

echo "fpp-plugin-naughtynice installed"

#fpp_install
