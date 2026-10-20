#!/usr/bin/env python3
"""
Auto-provisioning for the NaughtyNice Cloud plugin — makes the plugin
"ready to run" immediately after install instead of requiring the
customer to hand-build FPP Pixel Overlay Models and a playlist.

Run automatically once by scripts/fpp_install.sh (via the system python3,
or a legacy plugin venv if one exists). Can also be re-run any time — from content.php's "Re-run zone
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
- The matrix is settings.matrix_model (picked on the setup page, or
  --matrix-model). Zones are Channel models on the matrix's own channels
  when its rows are contiguous channel runs (LED panels), otherwise FPP
  "Sub" models of it (traditional pixel matrices). --list-models prints
  the picker's candidates.
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


def get_live_models(base_url: str) -> dict:
    """GET /api/overlays/models -> {name: model} as fppd actually loaded them.
    Unlike /api/models (the raw config file), each entry carries fppd's own
    computed "width"/"height", which already account for Orientation and
    StrandsPerString. Read-only and best-effort: {} on any failure, callers
    fall back to model_geometry()'s own math."""
    try:
        r = requests.get(_api(base_url, "/api/overlays/models"), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return {}
    return {m.get("Name"): m for m in data if isinstance(m, dict)} if isinstance(data, list) else {}


def get_panel_outputs(base_url: str) -> list:
    """Every LED panel matrix configured under Input/Output Setup -> LED
    Panels, from GET /api/channel/output/channelOutputsJSON. Returns
    [{"name", "start_channel", "channel_count", "width", "height"}].
    Best-effort: [] on failure (e.g. a box with no panel outputs at all)."""
    try:
        r = requests.get(_api(base_url, "/api/channel/output/channelOutputsJSON"), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    outputs = data.get("channelOutputs", []) if isinstance(data, dict) else []
    panels = []
    for o in outputs:
        if not isinstance(o, dict) or o.get("type") != "LEDPanelMatrix" or not o.get("enabled", 1):
            continue
        pw, ph = int(o.get("panelWidth") or 0), int(o.get("panelHeight") or 0)
        cols, rows = int(o.get("LEDPanelCols") or 0), int(o.get("LEDPanelRows") or 0)
        panels.append({
            "name": o.get("LEDPanelMatrixName") or "LEDPanels",
            "start_channel": int(o.get("startChannel") or 1),
            "channel_count": int(o.get("channelCount") or 0),
            "width": pw * cols,
            "height": ph * rows,
        })
    return panels


def model_geometry(model: dict, live: dict = None) -> tuple:
    """(width, height) in pixels, the way fppd sees the model.

    Prefers fppd's own numbers from /api/overlays/models. Otherwise mirrors
    PixelOverlayModel's constructor: a Channel model is StringCount *
    StrandsPerString rows tall (columns, if Orientation is vertical); any
    other type (Sub, FB) uses its Width/Height fields. The original code
    assumed every matrix was horizontal with one strand per string, which
    is true of LED panels but not of most traditional pixel matrices
    (vertical zig-zag strings are the norm there)."""
    lm = (live or {}).get(model.get("Name")) or {}
    if lm.get("width") and lm.get("height"):
        return int(lm["width"]), int(lm["height"])
    if model.get("Type", "Channel") != "Channel":
        return int(model.get("Width", 0) or 0), int(model.get("Height", 0) or 0)
    cpn = int(model.get("ChannelCountPerNode", 3) or 3)
    lines = int(model.get("StringCount", 0) or 0) * max(int(model.get("StrandsPerString", 1) or 1), 1)
    if lines <= 0:
        return 0, 0
    per_line = int(model.get("ChannelCount", 0) or 0) // cpn // lines
    if str(model.get("Orientation", "horizontal")).lower().startswith("v"):
        return lines, per_line
    return per_line, lines


def rows_are_contiguous(model: dict) -> bool:
    """True if each pixel row of `model` is one contiguous channel run, top
    row first -- i.e. a horizontal, top-left-start, single-strand Channel
    model (every LED panel matrix FPP auto-creates). Only then can a zone be
    a plain Channel model covering "the top N rows". Anything else (vertical
    strings, zig-zag, bottom/right start) gets Sub-model zones instead."""
    return (
        model.get("Type", "Channel") == "Channel"
        and str(model.get("Orientation", "horizontal")).lower().startswith("h")
        and str(model.get("StartCorner", "TL")).upper() == "TL"
        and int(model.get("StrandsPerString", 1) or 1) <= 1
    )


def list_matrix_candidates(base_url: str, exclude_names: set) -> dict:
    """Everything the setup page's matrix picker can offer, tagged so the
    page can filter client-side:

    - "led_panel":  covers an LED panel matrix output (channel range match).
                    Per panel output, only the real (non auto-created) models
                    plus FPP's own auto model of the same name get this tag;
                    stray duplicates FPP sometimes auto-creates (P5-1, P5-2)
                    stay show-all-only.
    - "name_match": "matrix" appears in the model name (case-insensitive).
    Untagged models only appear with "Show all models" checked.

    An LED panel output with no model at all (autoCreate turned off) is
    listed with "missing_model": true; provisioning creates one for it.
    """
    try:
        models = get_models(base_url)
    except requests.RequestException as exc:
        return {"ok": False, "message": f"Could not reach FPP's API at {base_url}: {exc}", "candidates": []}
    live = get_live_models(base_url)
    panels = get_panel_outputs(base_url)

    candidates = []
    covered_panels = set()
    for m in models:
        name = m.get("Name")
        if not name or name in exclude_names:
            continue
        w, h = model_geometry(m, live)
        tags = []
        start, count = m.get("StartChannel"), m.get("ChannelCount")
        for p in panels:
            if m.get("Type", "Channel") == "Channel" and start == p["start_channel"] and count == p["channel_count"]:
                if not m.get("autoCreated") or name == p["name"]:
                    tags.append("led_panel")
                    covered_panels.add(p["name"])
                break
        if "matrix" in name.lower():
            tags.append("name_match")
        candidates.append({
            "name": name, "width": w, "height": h, "tags": tags,
            "type": m.get("Type", "Channel"),
            "auto_created": bool(m.get("autoCreated")),
            "orientation": m.get("Orientation", ""),
            "start_corner": m.get("StartCorner", ""),
            "strands_per_string": m.get("StrandsPerString", 1),
            "zone_type": "Channel" if rows_are_contiguous(m) else "Sub",
            "missing_model": False,
        })
    for p in panels:
        if p["name"] not in covered_panels and not any(c["name"] == p["name"] for c in candidates):
            candidates.append({
                "name": p["name"], "width": p["width"], "height": p["height"], "tags": ["led_panel"],
                "type": "Channel", "auto_created": False, "orientation": "horizontal", "start_corner": "TL",
                "strands_per_string": 1, "zone_type": "Channel", "missing_model": True,
            })
    return {"ok": True, "message": "", "candidates": candidates}


def _panel_model_payload(panel: dict) -> dict:
    """The same model FPP auto-creates for an LED panel output, for boxes
    where overlay autoCreate is off and the panel has no model to attach to."""
    return {
        "Name": panel["name"], "Type": "Channel",
        "StartChannel": panel["start_channel"], "ChannelCount": panel["channel_count"],
        "ChannelCountPerNode": 3, "StringCount": panel["height"], "StrandsPerString": 1,
        "Orientation": "horizontal", "StartCorner": "TL", "xLights": False,
    }


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


def _sub_model_payload(name: str, parent: str, y_offset: int, width: int, height: int) -> dict:
    """A zone as an FPP "Sub" model (FPP 9.0+): a width x height window at
    (0, y_offset) of `parent`, in the parent's own pixel coordinates. fppd
    maps it through the parent's wiring, so it works on any matrix --
    vertical, zig-zag, any start corner. Orientation/StartCorner/StringCount
    are filler fppd overwrites for non-Channel types, but FPP 9.0 indexes
    StartCorner unconditionally, so they must be present."""
    return {
        "Name": name,
        "Type": "Sub",
        "Parent": parent,
        "XOffset": 0,
        "YOffset": y_offset,
        "Width": width,
        "Height": height,
        "StartChannel": 1,
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
            # blocking: FPP's script entry defaults to fire-and-forget --
            # without this the entry "finishes" the instant the script is
            # launched, the one-entry playlist ends a few ms later, and the
            # daemon (and any interrupted playlist waiting to resume) never
            # sees it run. See ensure_script_entry_blocking().
            {"type": "script", "enabled": 1, "playOnce": 0, "blocking": True,
             "scriptName": script_name, "scriptArgs": ""}
        ],
        "leadOut": [],
    }
    r = requests.post(_api(base_url, f"/api/playlist/{name}"), json=payload, timeout=TIMEOUT)
    r.raise_for_status()


def ensure_script_entry_blocking(base_url: str, name: str, script_name: str = "nnl_display_image.py",
                                 dry_run: bool = False) -> str:
    """Migration for playlists created before "blocking" was set (every
    install up to and including PR #6): flip our display-script entry to
    blocking in place, leaving every other entry and setting exactly as the
    user has it. Returns a short note for the caller to log/show, or "" if
    nothing needed changing.

    Why it matters (found 2026-10-20 from a user's FPP 10.2 log):
    PlaylistEntryScript::StartPlaying() calls FinishPlay() right after
    launching a non-blocking script, so breaking_news started and ended
    within one fppd tick. Photo submissions still showed (the script runs
    on regardless) but text-only ones were nacked -- nothing to observe --
    and the show it interrupted was left stopped."""
    try:
        r = requests.get(_api(base_url, f"/api/playlist/{name}"), timeout=TIMEOUT)
        r.raise_for_status()
        pl = r.json()
    except (requests.RequestException, ValueError) as exc:
        return f"Could not read playlist '{name}' to check its script entry: {exc}"
    if not isinstance(pl, dict):
        return ""

    changed = 0
    for section in ("leadIn", "mainPlaylist", "leadOut"):
        for entry in pl.get(section) or []:
            if (isinstance(entry, dict) and entry.get("type") == "script"
                    and entry.get("scriptName") == script_name and not entry.get("blocking")):
                entry["blocking"] = True
                changed += 1
    if not changed:
        return ""
    if dry_run:
        return f"[DRY RUN] Would set 'Blocking' on the {script_name} entry of playlist '{name}'."
    try:
        w = requests.post(_api(base_url, f"/api/playlist/{name}"), json=pl, timeout=TIMEOUT)
        w.raise_for_status()
    except requests.RequestException as exc:
        return f"Failed to set 'Blocking' on the {script_name} entry of playlist '{name}': {exc}"
    return f"Set 'Blocking' on the {script_name} entry of playlist '{name}'."


def _find_main_matrix_model(models: list, exclude_names: set, preferred: str = "") -> dict:
    """If `preferred` (settings.matrix_model, chosen in the setup page's
    matrix picker) names an existing model, that's the matrix. Otherwise,
    heuristic: the main display is the largest-channel-count 'Channel'
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
    if preferred:
        for m in models:
            if m.get("Name") == preferred and preferred not in exclude_names:
                return m
    candidates = [
        m for m in models
        if m.get("Type") == "Channel" and m.get("Name") not in exclude_names
        and m.get("ChannelCountPerNode", 3) > 0 and m.get("StringCount", 0) > 0
    ]
    if not candidates:
        return {}
    return max(candidates, key=lambda m: (not m.get("autoCreated", False), m.get("ChannelCount", 0)))


def _zone_starts(main: dict, width: int, photo_h: int, cpn: int = 3) -> tuple:
    """PhotoZone/TickerZone are OVERLAYS on the main matrix: they must share
    its channels, PhotoZone on the top `photo_h` rows starting at the
    matrix's own StartChannel and TickerZone on the rows right after it.

    (The original version placed each zone after the end of every existing
    model -- i.e. on channels no output ever sends -- so FPP rendered the
    photo and ticker into thin air and the matrix stayed blank. Found
    2026-10-19 on the FPP 10.2 Pi: matrix 62031-172622, zones at 172624+.)"""
    photo_start = main.get("StartChannel", 1)
    ticker_start = photo_start + width * photo_h * cpn
    return photo_start, ticker_start


def _zones_misplaced(main: dict, photo: dict, ticker: dict) -> bool:
    """True if either zone isn't drawing onto the main matrix correctly:
    - Sub zones must have the main matrix as their Parent.
    - Channel zones must sit inside the matrix's channel range AND the
      matrix's rows must be contiguous channel runs -- a Channel zone on a
      vertical / zig-zag matrix lands on the right channels but in the
      wrong pixel order (scrambled photo)."""
    if not main:
        return False
    lo = main.get("StartChannel", 0)
    hi = lo + main.get("ChannelCount", 0)  # exclusive
    for z in (photo, ticker):
        if z.get("Type") == "Sub":
            if z.get("Parent") != main.get("Name"):
                return True
            continue
        if not rows_are_contiguous(main):
            return True
        start = z.get("StartChannel", 0)
        if start < lo or start + z.get("ChannelCount", 0) > hi:
            return True
    return False


def provision(settings: Settings, force_recreate: bool = False, dry_run: bool = False,
              matrix_model: str = None) -> dict:
    """matrix_model: the matrix chosen in the setup page's picker (None =
    keep settings.matrix_model). An explicit pick that the existing zones
    aren't drawing onto rebuilds them automatically -- choosing a matrix
    and clicking "Re-run zone setup" is all a user should have to do."""
    base_url = settings.fpp_base_url
    photo_name = settings.photo_model
    ticker_name = settings.ticker_model
    playlist_name = settings.playlist
    script_name = "nnl_display_image.py"
    zone_names = {photo_name, ticker_name}

    explicit_pick = matrix_model is not None
    if explicit_pick:
        settings.matrix_model = matrix_model.strip()

    result = {
        "ok": False,
        "dry_run": dry_run,
        "created_models": [],
        "synced_from_existing": False,
        "playlist_created": False,
        "matrix_model": settings.matrix_model,
        "zone_type": None,
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
    live = get_live_models(base_url)

    by_name = {m.get("Name"): m for m in models}
    have_photo = photo_name in by_name
    have_ticker = ticker_name in by_name

    # Everything currently on FPP that ISN'T one of our zones — this is the
    # baseline that must always be preserved in any POST /api/models call,
    # since that endpoint replaces the whole file rather than appending to
    # it (see module docstring). This includes the customer's own real
    # matrix model and anything else they've configured.
    other_models = [m for m in models if m.get("Name") not in zone_names]

    # An LED panel output picked in the picker that has no model yet (FPP's
    # overlay autoCreate is off): add the same model FPP would auto-create.
    added_models = []
    if settings.matrix_model and settings.matrix_model not in by_name:
        panel = next((p for p in get_panel_outputs(base_url) if p["name"] == settings.matrix_model), None)
        if panel:
            pm = _panel_model_payload(panel)
            other_models.append(pm)
            added_models.append(pm["Name"])
        elif explicit_pick:
            result["message"] = (f"Matrix model '{settings.matrix_model}' was not found on FPP — "
                                 f"pick another one from the list.")
            return result

    main = _find_main_matrix_model(other_models, exclude_names=zone_names, preferred=settings.matrix_model)
    if settings.matrix_model and main and main.get("Name") != settings.matrix_model:
        result["message"] = (f"Matrix model '{settings.matrix_model}' was not found on FPP — "
                             f"pick another one from the list.")
        return result

    if have_photo and have_ticker and not force_recreate and explicit_pick and \
            _zones_misplaced(main, by_name[photo_name], by_name[ticker_name]):
        # The user picked a different matrix than the zones are on.
        force_recreate = True
        result["rebuilt_for_new_matrix"] = True

    if force_recreate:
        have_photo = have_ticker = False

    if have_photo and have_ticker and not added_models:
        # Existing models on FPP are the source of truth — sync settings.json
        # to match rather than touching them. This is what makes a manual
        # resize in FPP's own Pixel Overlay Models editor + "Re-run zone
        # setup" actually pick up the change. No /api/models write needed.
        photo = by_name[photo_name]
        ticker = by_name[ticker_name]
        pw, photo_h = model_geometry(photo, live)
        _tw, ticker_h = model_geometry(ticker, live)
        photo_h = photo_h or settings.photo_zone_height
        ticker_h = ticker_h or settings.ticker_zone_height

        settings.matrix_width = pw or settings.matrix_width
        settings.photo_zone_height = photo_h
        settings.ticker_zone_height = ticker_h
        settings.matrix_height = photo_h + ticker_h
        if main and not settings.matrix_model:
            settings.matrix_model = main.get("Name", "")
        misplaced = _zones_misplaced(main, photo, ticker)
        result.update({
            "ok": not misplaced,
            "zones_misplaced": misplaced,
            "synced_from_existing": True,
            "matrix_model": settings.matrix_model,
            "zone_type": photo.get("Type", "Channel"),
            "matrix_width": settings.matrix_width,
            "matrix_height": settings.matrix_height,
            "photo_zone_height": settings.photo_zone_height,
            "ticker_zone_height": settings.ticker_zone_height,
            "message": f"{photo_name}/{ticker_name} already existed on FPP — synced settings to match "
                       f"their current size ({settings.matrix_width}x{settings.matrix_height}).",
        })
        if misplaced:
            result["message"] += (
                f" PROBLEM: {photo_name}/{ticker_name} don't line up with your matrix model "
                f"'{main.get('Name')}' (wrong channels, wrong parent, or Channel-type zones on a matrix "
                f"whose rows aren't wired as contiguous runs), so the picture won't land right. "
                f"Re-run zone setup with \"Full reset\" checked to fix."
            )
    else:
        if not main:
            result["message"] = (
                "No existing channel output/model found to size the zones from. "
                "Set up a channel output (Settings -> Channel Outputs) for your matrix first, "
                "then re-run zone setup."
            )
            return result

        total_width, total_height = model_geometry(main, live)
        if not total_height or not total_width:
            result["message"] = f"Could not determine matrix dimensions from model '{main.get('Name')}'."
            return result

        photo_h = max(1, round(total_height * _PHOTO_RATIO))
        ticker_h = max(1, total_height - photo_h)

        # LED panels (and any horizontal, top-left, single-strand matrix):
        # each row is a contiguous channel run, so zones are plain Channel
        # models on the matrix's own channels -- the layout proven on the
        # P5 since phase 1. Anything else (traditional pixel matrices are
        # usually vertical zig-zag) gets Sub models that FPP maps through
        # the matrix's wiring.
        use_sub = not rows_are_contiguous(main)
        zone_type = "Sub" if use_sub else "Channel"

        new_models = []
        final_models = list(other_models)
        cpn = main.get("ChannelCountPerNode", 3) or 3
        photo_start, ticker_start = _zone_starts(main, total_width, photo_h, cpn)

        if have_photo:
            final_models.append(by_name[photo_name])
        else:
            photo_entry = (_sub_model_payload(photo_name, main["Name"], 0, total_width, photo_h) if use_sub
                           else _model_payload(photo_name, photo_start, total_width, photo_h))
            new_models.append(photo_entry)
            final_models.append(photo_entry)

        if have_ticker:
            final_models.append(by_name[ticker_name])
        else:
            ticker_entry = (_sub_model_payload(ticker_name, main["Name"], photo_h, total_width, ticker_h) if use_sub
                            else _model_payload(ticker_name, ticker_start, total_width, ticker_h))
            new_models.append(ticker_entry)
            final_models.append(ticker_entry)

        created_names = added_models + [m["Name"] for m in new_models]
        if created_names:
            desc = (f"Detected matrix '{main.get('Name')}' as {total_width}x{total_height} "
                    f"({zone_type}-type zones)")
            if dry_run:
                result["would_post"] = {"models": final_models, "autoCreate": auto_create}
                result["created_models"] = created_names
                result["message"] = (f"[DRY RUN] {desc} — would create {', '.join(created_names)} alongside "
                                     f"{len(other_models) - len(added_models)} existing model(s). Nothing was written.")
            else:
                try:
                    save_models(base_url, final_models, auto_create=auto_create)
                    result["created_models"] = created_names
                except requests.RequestException as exc:
                    result["message"] = f"{desc} but failed creating models: {exc}"
                    return result
                result["message"] = (f"{desc} — created {', '.join(created_names)} "
                                     f"(preserved {len(other_models) - len(added_models)} existing model(s)).")
        else:
            result["message"] = "PhotoZone/TickerZone already present — nothing to create."

        settings.matrix_model = main.get("Name", "")
        settings.matrix_width = total_width
        settings.matrix_height = total_height
        settings.photo_zone_height = photo_h
        settings.ticker_zone_height = ticker_h
        result.update({
            "ok": True,
            "matrix_model": settings.matrix_model,
            "zone_type": zone_type,
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
    elif playlists is not None:
        note = ensure_script_entry_blocking(base_url, playlist_name, script_name, dry_run=dry_run)
        if note:
            result["message"] += " " + note

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
    parser.add_argument("--matrix-model", default=None,
                         help="Name of the FPP model that is your matrix (as chosen in the setup page's "
                              "picker). Zones are rebuilt onto it if they aren't on it already.")
    parser.add_argument("--list-models", action="store_true",
                         help="Print matrix picker candidates as JSON and exit. Read-only.")
    args = parser.parse_args()

    settings = load_settings()
    if args.list_models:
        out = list_matrix_candidates(settings.fpp_base_url, {settings.photo_model, settings.ticker_model})
        out["selected"] = settings.matrix_model
        print(json.dumps(out))
        return 0 if out["ok"] else 1
    result = provision(settings, force_recreate=args.force_recreate, dry_run=args.dry_run,
                       matrix_model=args.matrix_model)

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
