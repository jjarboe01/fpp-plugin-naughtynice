#!/usr/bin/env python3
"""
Writes the just-uploaded current_display.png into the PhotoZone Pixel
Overlay Model's shared-memory buffer, holds it for display_duration_seconds,
then disables the zone so the normal show resumes.

Deployed by fpp_install.sh to /home/fpp/media/scripts/nnl_display_image.py
and referenced by the auto-created playlist's single "script" entry (see
daemon/fpp_provision.py). This is a direct port of phase 1's proven
show_display_image.py (LightShow-NaughtyNice/pi-config/scripts/).

IMPORTANT — do not "upgrade" this to use Pillow/PIL. FPP's playlist
"script" entry does not exec this file directly (and does not honor a venv
shebang): it dispatches through $FPPDIR/scripts/eventScript, which for any
.py file hardcodes `exec /usr/bin/python3 "$@"` regardless of this file's
own shebang or executable bit. This script therefore always runs under the
bare system python3, which does NOT have this plugin's venv (or Pillow)
available — an earlier version of this file imported PIL and would raise
ModuleNotFoundError every time it actually ran under FPP, silently
breaking the photo/silhouette half of "breaking news" on every fresh
install. Shelling out to ffmpeg (already relied on by FPP itself, and by
phase 1's original script on this exact hardware) needs nothing beyond the
Python standard library, so it works no matter which interpreter FPP
decides to invoke this with.

Why a "script" playlist entry and not FPP's native "image" entry type?
Tested live against the real Pi5 this plugin was developed against — the
native "image" entry hangs indefinitely (never reports finished) on the
installed FPP 9.5.3 build, even with a known-good file. A "script" entry
was confirmed to run to completion reliably on the same hardware. See
project memory dated 2026-07-05 for the full test log before changing this.

Reads geometry from this plugin's own settings.json (via daemon/config.py,
stdlib-only so it's safe to import under bare system python3 too) rather
than hardcoding numbers, so resizing PhotoZone in FPP's own editor and
clicking "Re-run zone setup" (which syncs settings.json to match) is
enough to change what this script does — no code edit needed.
"""
import os
import struct
import subprocess
import sys
import time
import json

PLUGIN_DIR = "/home/fpp/media/plugins/fpp-plugin-naughtynice"
sys.path.insert(0, os.path.join(PLUGIN_DIR, "daemon"))

from config import load_settings  # noqa: E402

PIXEL_OFFSET = 12  # byte offset where RGB data begins in the overlay buffer
FLAGS_OFFSET = 8   # byte offset of the dirty-flag uint32_t
IMAGE_PATH = "/home/fpp/media/images/current_display.png"
UPLOAD_SETTLE_SECONDS = 3  # give the just-finished upload a moment to land on disk


def fpp_cmd(base_url: str, command: str, args: list) -> None:
    """FPP 9.x command API requires args as an ARRAY, not a dict — see
    daemon/fpp_client.py's docstring for the same quirk."""
    subprocess.run(
        ["curl", "-s", "-X", "POST", f"{base_url}/api/command",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"command": command, "args": args})],
        capture_output=True,
    )


def fpp_put(base_url: str, path: str) -> None:
    subprocess.run(["curl", "-s", "-X", "PUT", f"{base_url}{path}"], capture_output=True)


def decode_and_crop(image_path: str, width: int, height: int) -> bytes:
    """Decode the composited PNG and crop to the top `height` rows via
    ffmpeg (no Python imaging library needed — see module docstring for
    why that matters here). The daemon composites onto a
    matrix_width x matrix_width square canvas (see daemon/fpp_client.py's
    push_photo_overlay), but only the top photo_zone_height rows are ever
    meant to reach PhotoZone; the header/status badge are baked into those
    top rows already."""
    proc = subprocess.run(
        ["ffmpeg", "-i", image_path,
         "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-vf", f"crop={width}:{height}:0:0",
         "-vframes", "1", "pipe:1", "-loglevel", "quiet"],
        capture_output=True,
    )
    return proc.stdout


def main() -> int:
    settings = load_settings()
    model = settings.photo_model
    width = settings.matrix_width
    height = settings.photo_zone_height
    duration = settings.display_duration_seconds
    base_url = settings.fpp_base_url.rstrip("/")
    buffer_path = f"/dev/shm/FPP-Model-Overlay-Buffer-{model}"

    # 0. Give the daemon's just-completed upload a moment to finish landing
    #    on disk before we read it.
    time.sleep(UPLOAD_SETTLE_SECONDS)

    # 1. Force-create the shared memory overlay buffer WITHOUT enabling the
    #    zone yet. The buffer persists between runs and still holds the
    #    previous image — we must write the new one in before enabling, so
    #    the overlay never goes live with stale data.
    fpp_put(base_url, f"/api/overlays/model/{model}/mmap")
    time.sleep(0.25)

    # 2. Decode + crop via ffmpeg.
    pixel_data = decode_and_crop(IMAGE_PATH, width, height)
    expected = width * height * 3

    if len(pixel_data) != expected:
        print(f"ERROR: ffmpeg produced {len(pixel_data)} bytes, expected {expected} "
              f"({width}x{height}x3) — either ffmpeg failed to decode {IMAGE_PATH}, or "
              f"settings.json geometry doesn't match the actual PhotoZone model size "
              f"(re-run zone setup).", file=sys.stderr)
        return 1


    # 3. Write the new image into the buffer while the zone is still
    #    disabled, then clear the dirty flag so FPP doesn't flush stale data
    #    the moment we enable it.
    try:
        with open(buffer_path, "r+b") as f:
            f.seek(PIXEL_OFFSET)
            f.write(pixel_data)
            f.seek(FLAGS_OFFSET)
            f.write(struct.pack("<I", 0))
    except OSError as exc:
        print(f"ERROR: overlay buffer write failed ({buffer_path}): {exc}", file=sys.stderr)
        return 1

    # 4. Enable the zone (buffer already holds the new image), then set the
    #    dirty bit to trigger FPP's output loop to actually flush it.
    fpp_cmd(base_url, "Overlay Model State", [model, "Enabled"])
    try:
        with open(buffer_path, "r+b") as f:
            f.seek(FLAGS_OFFSET)
            f.write(struct.pack("<I", 0x1))
    except OSError as exc:
        print(f"ERROR: could not set dirty flag ({buffer_path}): {exc}", file=sys.stderr)
        return 1

    # 5. Hold for the configured duration. TickerZone's text effect runs
    #    independently via its own overlay buffer/command, triggered
    #    separately by the daemon — nothing to do here for it.
    time.sleep(duration)

    # 6. Disable the zone so the normal show resumes cleanly once this
    #    script (and the playlist entry it's attached to) finishes.
    fpp_cmd(base_url, "Overlay Model State", [model, "Disabled"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
