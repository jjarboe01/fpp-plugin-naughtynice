#!/usr/bin/env python3
"""
Auto-provisioning for the NaughtyNice Cloud plugin — makes the plugin
"ready to run" immediately after install instead of requiring the
customer to hand-build FPP Pixel Overlay Models and a playlist.

Run automatically once by scripts/fpp_install.sh (via the plugin's own
venv). Can also be re-run any time — from content.php's "Re-run zone
setup" button, or manually over SSH — and is safe to call repeatedly:

- If PhotoZone/TickerZone models already exist, their CURRENT on-FPP
  dimensions are treated as the source of truth and synced into
  settings.json. This is what makes "click Re-run zone setup after you
  resize a model in FPP's own editor" work — we never fight a manual
  edit.
- Pass --force-recreate to instead delete and rebuild both models from
  freshly auto-detected matrix geometry (e.g. after swapping to a
  different-size matrix). This is destructive to any manual zone sizing
  and is only done when explicitly requested.
- The "NaughtyNice Breaking News" playlist (single script entry, no
  pause needed — the script itself sleeps for display_duration_seconds
  before returning, exactly like phase 1's proven breaking_news
  playlist) is only ever CREATED if missing, never overwritten, so any
  manual customization of it survives a re-run.

Why not use FPP's native "image" playlist entry type? Tested live
against the real Pi5 (P5Large) on two different FPP 9.5.3 builds — it
hangs indefinitely (isPlaying stays 1, isFinished never flips) even with
a known-good image file. A plain "script" playlist entry pointing at
show_display_image.py-style logic was confirmed to run to completion
correctly on the same hardware. See project memory 2026-07-05 for the
full test log. This module intentionally never creates an "image" entry.
"""

import argparse
import json
import logging
import sys

import requests

from config import Settings, load_settings, save_settings, write_status

log = logging.getLogger("fpp_provision")

# Phase 1's proven split on a 192px-tall matrix: 140 rows photo, 52 rows
# ticker (~72.9% / ~27.1%). Used as the default ratio for any matrix
# height so a differently-sized customer matrix gets a sensible split
# without asking them anything.
_PHOTO_RATIO = 140 / 192

TIMEOUT = 10


class ProvisionError(Exception):
    pass


def _api(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def get_models(base_url: str) -> list:
    r = requests.get(_api(base_url, "/api/models"), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def create_model(base_url: str, name: str, start_channel: int, width: int, height: int) -> None:
    payload = {
        "Name": name,
        "Type": "Channel",
        "StartChannel": start_channel,
        "ChannelCount": width * height * 3,
        "ChannelCountPerNode": 3,
        "StringCount": height,
        "StrandsPerString": 1,
        "Orientation": "horizontal",
        "StartCorner": "TL",
        "xLights": False,
    }
    r = requests.post(_api(base_url, "/api/models"), json=payload, timeout=TIMEOUT)
    r.raise_for_status()


def delete_model(base_url: str, name: str) -> None:
    try:
        requests.delete(_api(base_url, f"/api/models/{name}"), timeout=TIMEOUT)
    except requests.RequestException as exc:
        log.warning("delete_model(%s) failed (continuing anyway): %s", name, exc)


def get_playlists(base_url: str) -> list:
    r = requests.get(_api(base_url, "/api/playlists"), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def save_playlist(base_url: str, name: str, script_name: str) -> None:
    payload = {
        "name": name,
        "version": 4,
        "repeat": 0,
        "loopCount": 0,
        "desc": "Auto-created by the NaughtyNice Cloud plugin. Safe to customize — "
                "add lead-in/lead-out content or swap the background art; just don't "
                "remove the script entry, the daemon relies on it to render each submission.",
        "random": 0,
        "leadIn": [],
        "mainPlaylist": [
            {"type": "script", "enabled": 1, "playOnce": 0, "scriptName": script_name, "scriptArgs": ""}
        ],
        "leadOut": [],
    }
    r = requests.post(_api(base_url, f"/api/playlist/{name}"), json=payload, timeout=TIMEOUT)
    r.raise_for_status()


def _find_main_matrix_model(models: list, exclude_names: set) -> dict:
    """Heuristic: the main display is the largest-channel-count 'Channel'
    type model that isn't one of our own overlay zones. On a dedicated
    single-purpose FPP box (the common case for this plugin) that's
    reliably the one real matrix output."""
    candidates = [
        m for m in models
        if m.get("Type") == "Channel" and m.get("Name") not in exclude_names
        and m.get("ChannelCountPerNode", 3) > 0 and m.get("StringCount", 0) > 0
    ]
    if not candidates:
        return {}
    return max(candidates, key=lambda m: m.get("ChannelCount", 0))


def _next_start_channel(models: list) -> int:
    if not models:
        return 1
    ends = [m.get("StartChannel", 0) + m.get("ChannelCount", 0) for m in models]
    return max(ends) + 1 if ends else 1


def provision(settings: Settings, force_recreate: bool = False) -> dict:
    base_url = settings.fpp_base_url
    photo_name = settings.photo_model
    ticker_name = settings.ticker_model
    playlist_name = settings.playlist
    script_name = "nnl_display_image.py"

    result = {
        "ok": False,
        "created_models": [],
        "synced_from_existing": False,
        "playlist_created": False,
        "matrix_width": settings.matrix_width,
        "matrix_height": settings.matrix_height,
        "photo_zone_height": settings.photo_zone_height,
        "ticker_zone_height": settings.ticker_zone_height,
        "message": "",
    }

    try:
        models = get_models(base_url)
    except requests.RequestException as exc:
        result["message"] = f"Could not reach FPP's API at {base_url}: {exc}"
        return result

    by_name = {m.get("Name"): m for m in models}
    have_photo = photo_name in by_name
    have_ticker = ticker_name in by_name

    if force_recreate:
        if have_photo:
            delete_model(base_url, photo_name)
        if have_ticker:
            delete_model(base_url, ticker_name)
        have_photo = have_ticker = False
        models = [m for m in models if m.get("Name") not in (photo_name, ticker_name)]

    if have_photo and have_ticker:
        # Existing models on FPP are the source of truth — sync settings.json
        # to match rather than touching them. This is what makes a manual
        # resize in FPP's own Pixel Overlay Models editor + "Re-run zone
        # setup" actually pick up the change.
        photo = by_name[photo_name]
        ticker = by_name[ticker_name]
        photo_h = photo.get("StringCount", settings.photo_zone_height)
        ticker_h = ticker.get("StringCount", settings.ticker_zone_height)
        cpn = photo.get("ChannelCountPerNode", 3) or 3
        width = photo.get("ChannelCount", 0) // max(photo_h, 1) // cpn if photo_h else settings.matrix_width

        settings.matrix_width = width or settings.matrix_width
        settings.photo_zone_height = photo_h
        settings.ticker_zone_height = ticker_h
        settings.matrix_height = photo_h + ticker_h
        result.update({
            "ok": True,
            "synced_from_existing": True,
            "matrix_width": settings.matrix_width,
            "matrix_height": settings.matrix_height,
            "photo_zone_height": settings.photo_zone_height,
            "ticker_zone_height": settings.ticker_zone_height,
            "message": f"{photo_name}/{ticker_name} already existed on FPP — synced settings to match "
                       f"their current size ({settings.matrix_width}x{settings.matrix_height}).",
        })
    else:
        main = _find_main_matrix_model(models, exclude_names={photo_name, ticker_name})
        if not main:
            result["message"] = (
                "No existing channel output/model found to size the zones from. "
                "Set up a channel output (Settings -> Channel Outputs) for your matrix first, "
                "then re-run zone setup."
            )
            return result

        cpn = main.get("ChannelCountPerNode", 3) or 3
        total_height = main.get("StringCount", 0)
        total_width = main.get("ChannelCount", 0) // max(total_height, 1) // cpn

        if not total_height or not total_width:
            result["message"] = f"Could not determine matrix dimensions from model '{main.get('Name')}'."
            return result

        photo_h = max(1, round(total_height * _PHOTO_RATIO))
        ticker_h = max(1, total_height - photo_h)
        start_channel = _next_start_channel(models)

        try:
            if not have_photo:
                create_model(base_url, photo_name, start_channel, total_width, photo_h)
                result["created_models"].append(photo_name)
            if not have_ticker:
                ticker_start = start_channel + (total_width * photo_h * cpn)
                create_model(base_url, ticker_name, ticker_start, total_width, ticker_h)
                result["created_models"].append(ticker_name)
        except requests.RequestException as exc:
            result["message"] = f"Detected matrix as {total_width}x{total_height} but failed creating models: {exc}"
            return result

        settings.matrix_width = total_width
        settings.matrix_height = total_height
        settings.photo_zone_height = photo_h
        settings.ticker_zone_height = ticker_h
        result.update({
            "ok": True,
            "matrix_width": total_width,
            "matrix_height": total_height,
            "photo_zone_height": photo_h,
            "ticker_zone_height": ticker_h,
            "message": f"Detected matrix as {total_width}x{total_height} from model "
                       f"'{main.get('Name')}' — created {', '.join(result['created_models']) or 'no new models (already present)'}.",
        })

    try:
        playlists = get_playlists(base_url)
    except requests.RequestException as exc:
        result["message"] += f" (Warning: could not list playlists to check for '{playlist_name}': {exc})"
        playlists = None

    if playlists is not None and playlist_name not in playlists:
        try:
            save_playlist(base_url, playlist_name, script_name)
            result["playlist_created"] = True
            result["message"] += f" Created playlist '{playlist_name}' (script: {script_name})."
        except requests.RequestException as exc:
            result["message"] += f" Warning: failed to create playlist '{playlist_name}': {exc}"

    save_settings(settings)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [fpp_provision] %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-recreate", action="store_true",
                         help="Delete and rebuild PhotoZone/TickerZone from freshly detected geometry, "
                              "instead of syncing from whatever already exists.")
    args = parser.parse_args()

    settings = load_settings()
    result = provision(settings, force_recreate=args.force_recreate)

    write_status(last_provision=result)
    print(json.dumps(result, indent=2))

    if not result["ok"]:
        log.error("Provisioning did not complete: %s", result["message"])
        return 1
    log.info("Provisioning OK: %s", result["message"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
