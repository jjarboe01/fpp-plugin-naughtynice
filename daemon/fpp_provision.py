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
- Pass --force-recreate to instead rebuild both models from freshly
  auto-detected matrix geometry (e.g. after swapping to a
  different-size matrix). This is destructive to any manual zone sizing
  and is only done when explicitly requested.
- The playlist (single script entry, no pause needed — the script
  itself sleeps for display_duration_seconds before returning) is only
  ever CREATED if missing, never overwritten, so any manual
  customization of it survives a re-run.
- Pass --dry-run to print exactly what would be sent to FPP (the full
  desired /api/models payload, and whether a playlist would be created)
  without writing anything. Added 2026-07-06 after the incident
  described below wiped a real customer's matrix model in production —
  always dry-run first against a box you can't afford to break.

CRITICAL — POST /api/models REPLACES the entire model-overlays.json
file, it does not append. Confirmed directly from FPP's own source
(PixelOverlayManager::render_POST in src/overlays/PixelOverlay.cpp):
the handler for `POST /api/models` opens config/model-overlays.json
with O_TRUNC and writes the raw request body to it verbatim, then sets
FPP's restart flag. There is no merge/append/create-single-model
semantic anywhere in that path — "POST /api/models" is really "replace
the whole model list with what I send you." This module's model
mutations therefore ALWAYS send FPP's *complete* desired model list,
wrapped as {"models": [...]}, in a single call. The original (buggy)
version of this file POSTed one bare `{"Name": "PhotoZone", ...}`
object per model with no wrapper at all — that silently wiped every
model FPP didn't already know about (including the customer's own real
matrix model) on the very first call, and didn't even load back
correctly itself, since model-overlays.json ended up with no top-level
"models" key for FPP's loadModelMap() to read.

Real-hardware incident, 2026-07-06: a fresh install against the
customer's real Pi5 ran the pre-fix version of this script. The box's
real channel-output model (originally named "P5Large") was wiped by
the very first POST call (each call overwrote the whole file), and
neither PhotoZone nor TickerZone actually loaded after the next fppd
restart, since neither payload was wrapped in {"models": [...]}. FPP's
own channel-output subsystem auto-recreated a bare replacement model
("P5", autoCreated:true) to keep the show outputting *something* after
restart, which is why the box didn't go completely dark — but the
original model's name and any custom tweaks were lost. Fixed by
rewriting every model-list mutation as: GET the current full list,
modify it in memory, POST the complete list back in exactly one call.
See project memory for the full incident writeup.

Why not use FPP's native "image" playlist entry type? Tested live
against the real Pi5 on two different FPP 9.5.3 builds — it hangs
indefinitely (isPlaying stays 1, isFinished never flips) even with a
known-good image file. A plain "script" playlist entry pointing at
show_display_image.py-style logic was confirmed to run to completion
correctly on the same hardware. This module intentionally never
creates an "image" entry.
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


def get_overlay_settings(base_url: str) -> dict:
    """GET /api/overlays/settings -> {"autoCreate": bool}. Fetched so
    save_models()'s full-file rewrite can carry this flag forward
    unchanged instead of silently dropping it (see module docstring —
    POST /api/models replaces the whole file, so anything we don't
    explicitly include gets lost)."""
    try:
        r = requests.get(_api(base_url, "/api/overlays/settings"), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}
    except requests.RequestException:
        return {}


def save_models(base_url: str, models: list, auto_create: bool = None) -> None:
    """The ONLY safe way to mutate FPP's Pixel Overlay Models over the API.

    POST /api/models replaces config/model-overlays.json wholesale (see
    module docstring) — always pass the COMPLETE desired list of model
    dicts, never a single new/changed model on its own, or every model
    FPP doesn't already know about gets silently deleted.
    """
    payload = {"models": models}
    if auto_create is not None:
        payload["autoCreate"] = auto_create
    r = requests.post(_api(base_url, "/api/models"), json=payload, timeout=TIMEOUT)
    r.raise_for_status()


def _model_payload(name: str, start_channel: int, width: int, height: int) -> dict:
    return {
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
    reliably the one real matrix output.

    Prefers a real (non-auto-created) model over FPP's own auto-generated
    channel-output stand-in when both exist and cover the same range —
    this happens after the 2026-07-06 incident's recovery: the customer
    re-pushed their real named model ("P5Large") from xLights, but FPP's
    own auto-recreated bookkeeping model ("P5", autoCreated:true) is still
    sitting there too, with an identical ChannelCount. Without this tie-
    break, plain `max()` by ChannelCount would silently depend on
    dict/list ordering to decide which one "wins" — fragile. autoCreated
    models are always deprioritized so zones get sized off the customer's
    actual model.
    """
    candidates = [
        m for m in models
        if m.get("Type") == "Channel" and m.get("Name") not in exclude_names
        and m.get("ChannelCountPerNode", 3) > 0 and m.get("StringCount", 0) > 0
    ]
    if not candidates:
        return {}
    return max(candidates, key=lambda m: (not m.get("autoCreated", False), m.get("ChannelCount", 0)))


def _next_start_channel(models: list) -> int:
    if not models:
        return 1
    ends = [m.get("StartChannel", 0) + m.get("ChannelCount", 0) for m in models]
    return max(ends) + 1 if ends else 1


def provision(settings: Settings, force_recreate: bool = False, dry_run: bool = False) -> dict:
    base_url = settings.fpp_base_url
    photo_name = settings.photo_model
    ticker_name = settings.ticker_model
    playlist_name = settings.playlist
    script_name = "nnl_display_image.py"

    result = {
        "ok": False,
        "dry_run": dry_run,
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

    auto_create = get_overlay_settings(base_url).get("autoCreate")

    by_name = {m.get("Name"): m for m in models}
    have_photo = photo_name in by_name
    have_ticker = ticker_name in by_name

    # Everything currently on FPP that ISN'T one of our zones — this is the
    # baseline that must always be preserved in any POST /api/models call,
    # since that endpoint replaces the whole file rather than appending to
    # it (see module docstring). This includes the customer's own real
    # matrix model and anything else they've configured.
    other_models = [m for m in models if m.get("Name") not in (photo_name, ticker_name)]

    if force_recreate:
        have_photo = have_ticker = False

    if have_photo and have_ticker:
        # Existing models on FPP are the source of truth — sync settings.json
        # to match rather than touching them. This is what makes a manual
        # resize in FPP's own Pixel Overlay Models editor + "Re-run zone
        # setup" actually pick up the change. No /api/models write needed.
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
        main = _find_main_matrix_model(other_models, exclude_names={photo_name, ticker_name})
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

        new_models = []
        # Build the final desired list incrementally, always computing the
        # next free start channel against what's ALREADY in the list (so
        # existing entries — including a partially-provisioned prior run —
        # are never overlapped, and force-recreate reuses freed address
        # space instead of drifting upward forever).
        final_models = list(other_models)

        if have_photo:
            final_models.append(by_name[photo_name])
        else:
            photo_start = _next_start_channel(final_models)
            photo_entry = _model_payload(photo_name, photo_start, total_width, photo_h)
            new_models.append(photo_entry)
            final_models.append(photo_entry)

        if have_ticker:
            final_models.append(by_name[ticker_name])
        else:
            ticker_start = _next_start_channel(final_models)
            ticker_entry = _model_payload(ticker_name, ticker_start, total_width, ticker_h)
            new_models.append(ticker_entry)
            final_models.append(ticker_entry)

        if new_models:
            created_names = [m["Name"] for m in new_models]
            if dry_run:
                result["would_post"] = {"models": final_models, "autoCreate": auto_create}
                result["created_models"] = created_names
                result["message"] = (
                    f"[DRY RUN] Detected matrix as {total_width}x{total_height} from model "
                    f"'{main.get('Name')}' — would create {', '.join(created_names)} alongside "
                    f"{len(other_models)} existing model(s). Nothing was written."
                )
            else:
                try:
                    save_models(base_url, final_models, auto_create=auto_create)
                    result["created_models"] = created_names
                except requests.RequestException as exc:
                    result["message"] = (
                        f"Detected matrix as {total_width}x{total_height} but failed creating models: {exc}"
                    )
                    return result
                result["message"] = (
                    f"Detected matrix as {total_width}x{total_height} from model "
                    f"'{main.get('Name')}' — created {', '.join(created_names)} "
                    f"(preserved {len(other_models)} existing model(s))."
                )
        else:
            result["message"] = "PhotoZone/TickerZone already present — nothing to create."

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
        })

    try:
        playlists = get_playlists(base_url)
    except requests.RequestException as exc:
        result["message"] += f" (Warning: could not list playlists to check for '{playlist_name}': {exc})"
        playlists = None

    if playlists is not None and playlist_name not in playlists:
        if dry_run:
            result["message"] += f" [DRY RUN] Would create playlist '{playlist_name}' (script: {script_name})."
        else:
            try:
                save_playlist(base_url, playlist_name, script_name)
                result["playlist_created"] = True
                result["message"] += f" Created playlist '{playlist_name}' (script: {script_name})."
            except requests.RequestException as exc:
                result["message"] += f" Warning: failed to create playlist '{playlist_name}': {exc}"

    if not dry_run and result.get("created_models"):
        result["message"] += (" A restart of fppd is required for these model changes to take "
                               "effect (Status/Control -> Restart FPPD, or reboot).")

    if not dry_run:
        save_settings(settings)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [fpp_provision] %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-recreate", action="store_true",
                         help="Rebuild PhotoZone/TickerZone from freshly detected geometry, "
                              "instead of syncing from whatever already exists.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be sent to FPP without writing anything. "
                              "Recommended before running against any box you can't afford to "
                              "break — see the module docstring for why POST /api/models is "
                              "destructive if not done carefully.")
    args = parser.parse_args()

    settings = load_settings()
    result = provision(settings, force_recreate=args.force_recreate, dry_run=args.dry_run)

    if not args.dry_run:
        write_status(last_provision=result)
    print(json.dumps(result, indent=2))

    if not result["ok"]:
        log.error("Provisioning did not complete: %s", result["message"])
        return 1
    log.info("Provisioning OK: %s", result["message"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
