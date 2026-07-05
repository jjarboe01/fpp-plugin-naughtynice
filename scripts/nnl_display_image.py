#!/home/fpp/media/plugins/fpp-plugin-naughtynice/venv/bin/python3
"""
Writes the just-uploaded current_display.png into the PhotoZone Pixel
Overlay Model's shared-memory buffer, holds it for display_duration_seconds,
then disables the zone so the normal show resumes.

Deployed by fpp_install.sh to /home/fpp/media/scripts/nnl_display_image.py
and referenced by the auto-created playlist's single "script" entry (see
daemon/fpp_provision.py). This is a direct port of phase 1's proven
show_display_image.py (LightShow-NaughtyNice/pi-config/scripts/), with one
change: image decoding uses Pillow (already a daemon dependency, installed
into this plugin's own venv by fpp_install.sh) instead of shelling out to
ffmpeg, since the venv gives us a real interpreter with Pillow available via
this file's own shebang.

Why a "script" playlist entry and not FPP's native "image" entry type?
Tested live against the real Pi5 this plugin was developed against — the
native "image" entry hangs indefinitely (never reports finished) on the
installed FPP 9.5.3 build, even with a known-good file. A "script" entry
was confirmed to run to completion reliably on the same hardware. See
project memory dated 2026-07-05 for the full test log before changing this.

Reads geometry from this plugin's own settings.json (via daemon/config.py)
rather than hardcoding numbers, so resizing PhotoZone in FPP's own editor
and clicking "Re-run zone setup" (which syncs settings.json to match) is
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

from PIL import Image  # noqa: E402

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

    # 2. Decode the composited PNG and crop to the top `height` rows (the
    #    daemon composites onto a matrix_width x matrix_width square canvas
    #    — see daemon/fpp_client.py's push_photo_overlay — but only the top
    #    photo_zone_height rows are ever meant to reach PhotoZone; the
    #    header/status badge are baked into those top rows already).
    try:
        img = Image.open(IMAGE_PATH).convert("RGB")
        img = img.crop((0, 0, width, height))
        pixel_data = img.tobytes()
    except Exception as exc:
        print(f"ERROR: could not decode/crop {IMAGE_PATH}: {exc}", file=sys.stderr)
        return 1

    expected = width * height * 3
    if len(pixel_data) != expected:
        print(f"ERROR: got {len(pixel_data)} bytes, expected {expected} "
              f"({width}x{height}x3) — settings.json geometry may not match "
              f"the actual PhotoZone model size; re-run zone setup.", file=sys.stderr)
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
