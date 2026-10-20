"""
FPP REST API client — FPP 9.x compatible.

Ported from LightShow-NaughtyNice's app/fpp_client.py (phase 1). Phase 1
called this from a Flask app on a different host over an inter-VLAN route;
here it runs as part of this plugin, directly on the FPP box, so base_url
defaults to http://localhost and there's no firewall to cross.

FPP API docs: http://<fpp-host>/api/help

Notable FPP 9.x quirks (learned the hard way in phase 1 — see git history of
LightShow-NaughtyNice for the FPP 6.x-assumption bugs these fixed):
- Playlist start: GET /api/playlist/{name}/start  (singular; /api/playlists/ is list-only)
- Command API: args must be an ARRAY, not a dict
- Photo display: composite onto background, write the PNG into this
  plugin's plugindata dir; scripts/nnl_display_image.py (a playlist "script"
  entry) pushes it into the PhotoZone overlay buffer and then deletes it
- Ticker text: "Text" effect (not "Scrolling Text"); array arg order matters
"""

import logging
import os
import time
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# Composited image lives in this plugin's own plugindata dir (FPP listing
# guideline §5), not /home/fpp/media/images. scripts/nnl_display_image.py
# reads it from the same path and deletes it once it's on the matrix.
DATA_DIR = "/home/fpp/media/plugindata/fpp-plugin-naughtynice"
DISPLAY_IMAGE_PATH = os.path.join(DATA_DIR, "current_display.png")

# Breaking news overlay dimensions (PhotoZone is 192x140 by default —
# configurable via settings.json for other matrix layouts)
_HEADER_H = 20
_LOWER_H = 22

_COLOR_RED = (204, 0, 0)
_COLOR_NAVY = (0, 20, 90)
_COLOR_NICE = (0, 160, 40)
_COLOR_NAUGHTY = (200, 0, 0)
_COLOR_WHITE = (255, 255, 255)


# Font names FPP's "Text" overlay effect accepts are the bare filename stems
# of .ttf/.pfb files fppd found at startup under /usr/share/fonts/truetype/,
# /usr/share/fonts/X11/Type1/ and /usr/local/share/fonts/ (see
# PixelOverlayManager::loadFonts in FPP's PixelOverlay.cpp) -- e.g.
# DejaVuSans.ttf -> "DejaVuSans". Which fonts exist varies by FPP image /
# platform, so hard-coding "DejaVuSans" broke the ticker for customers whose
# box doesn't ship fonts-dejavu-core ("Could not find font 'DejaVuSans'").
# We now ask FPP (GET /api/overlays/fonts) and pick the first match below.
_PREFERRED_TICKER_FONTS = (
    "DejaVuSans",
    "DejaVuSans-Bold",
    "LiberationSans-Regular",
    "LiberationSans-Bold",
    "FreeSans",
    "FreeSansBold",
    "Arial",
    "NotoSans-Regular",
    "Roboto-Regular",
)
_TICKER_FONT_CACHE: dict = {}  # base_url -> resolved font name

_PIL_FONT_DIRS = (
    "/usr/share/fonts/truetype",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/local/share/fonts",
    "/usr/share/fonts",
)


def _norm_font(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _pick_font(available, preferred=_PREFERRED_TICKER_FONTS) -> Optional[str]:
    """Pick the best font from FPP's list: exact preferred match, then a
    normalized (case/dash-insensitive) preferred match, then any non-mono
    sans, then anything at all. Returns None only if the list is empty."""
    fonts = [f for f in (available or []) if isinstance(f, str) and f]
    if not fonts:
        return None
    for want in preferred:
        if want in fonts:
            return want
    by_norm = {_norm_font(f): f for f in fonts}
    for want in preferred:
        hit = by_norm.get(_norm_font(want))
        if hit:
            return hit
    sans = sorted(f for f in fonts if "sans" in f.lower() and "mono" not in f.lower())
    if sans:
        return sans[0]
    return sorted(fonts)[0]


def _find_font_file(filenames) -> Optional[str]:
    for base in _PIL_FONT_DIRS:
        for name in filenames:
            path = os.path.join(base, name)
            if os.path.exists(path):
                return path
    # Last resort: any sans .ttf anywhere under the font dirs.
    for base in _PIL_FONT_DIRS:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in sorted(files):
                fl = f.lower()
                if fl.endswith(".ttf") and "sans" in fl and "mono" not in fl:
                    return os.path.join(root, f)
    return None


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if bold:
        names = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf")
    else:
        names = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf")
    path = _find_font_file(names)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception as exc:
            log.warning("Could not load font %s: %s", path, exc)
    log.warning("No TrueType font found for overlay image; using Pillow's built-in font "
                "(install fonts-dejavu-core for proper rendering)")
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _draw_text_centered(draw, text, font, x0, y0, x1, y1, color) -> None:
    bb = font.getbbox(text)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = x0 + ((x1 - x0) - tw) // 2
    y = y0 + ((y1 - y0) - th) // 2
    draw.text((x, y), text, fill=color, font=font)


class FPPClient:
    def __init__(self, base_url: str, ticker_model: str, playlist: str,
                 matrix_width: int = 192, photo_zone_height: int = 140,
                 timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.ticker_model = ticker_model
        self.playlist = playlist
        self.matrix_width = matrix_width
        self.photo_zone_height = photo_zone_height
        self.timeout = timeout

    def is_alive(self) -> bool:
        return self._fetch_status() is not None

    def get_fpp_version(self) -> Optional[str]:
        """GET /api/fppd/version -> {"version": "9.5.3-5-g87310ad7a", ...}.
        Used purely for telemetry (so Joe can see which FPP build a show is
        running when troubleshooting with a customer) -- never fatal if it
        fails, just omitted from that poll's telemetry report."""
        try:
            r = requests.get(f"{self.base_url}/api/fppd/version", timeout=self.timeout)
            if r.status_code == 200:
                version = r.json().get("version")
                return version or None
            log.warning("FPP version check -> HTTP %s", r.status_code)
            return None
        except (requests.RequestException, ValueError) as exc:
            log.warning("FPP version check failed: %s", exc)
            return None

    def _fetch_status(self) -> Optional[dict]:
        """GET /api/system/status — NOT /api/status, which 404s on FPP 9.x
        (confirmed against a live 9.5.3 box; that older path is a Limonade
        route that no longer exists). Shared by is_alive() and
        break_in_and_sync()'s polling loop."""
        try:
            r = requests.get(f"{self.base_url}/api/system/status", timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            log.warning("FPP status check -> HTTP %s", r.status_code)
            return None
        except (requests.RequestException, ValueError) as exc:
            log.warning("FPP status check failed: %s", exc)
            return None

    def break_in_playlist(self, name: Optional[str] = None) -> bool:
        """GET /api/playlist/{name}/start — SINGULAR. Breaks in from whatever
        is currently playing; FPP resumes normal sync when it ends."""
        target = name or self.playlist
        url = f"{self.base_url}/api/playlist/{target}/start"
        try:
            r = requests.get(url, timeout=self.timeout)
            if r.status_code == 200:
                log.info("Started FPP playlist: %s", target)
                return True
            log.error("break_in_playlist %s -> HTTP %s: %s", target, r.status_code, r.text[:200])
            return False
        except requests.RequestException as exc:
            log.error("break_in_playlist exception: %s", exc)
            return False

    def break_in_and_sync(self, name: Optional[str] = None, expect_photo: bool = True,
                          timeout: float = 12.0, poll_interval: float = 0.25) -> bool:
        """Break into `name` (or self.playlist) and block until there is proof
        the playlist actually ran, so the ticker starts with the visual and a
        submission is only acked when it was really shown.

        Why a 200 from break_in_playlist() isn't enough: fppd answers the
        start request even when it then drops it -- 2026-10-19 on FPP 10.2,
        a start sent ~0.3s before fppd's main loop came up was never run, yet
        the submission was acked as delivered.

        Proof, in order of preference:
        - Photo submissions: the display script (the playlist's "script"
          entry) deletes DISPLAY_IMAGE_PATH right after decoding it, just
          before it enables PhotoZone. The file disappearing is direct proof
          our playlist ran, in every FPP version and mode.
        - Otherwise: GET /api/system/status "playlist" == target (FPP 9, and
          player mode). FPP 10 in remote mode leaves that field '' even while
          playing, so for text-only submissions status_name == "playing" is
          accepted as the best signal available there.

        No proof within `timeout` -> False, so the caller nacks the item (the
        cloud retries it) instead of acking something nobody saw.
        """
        target = name or self.playlist
        if not self.break_in_playlist(target):
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if expect_photo and not os.path.exists(DISPLAY_IMAGE_PATH):
                log.info("Display script picked up the photo -- playlist %s is running", target)
                return True
            status = self._fetch_status()
            if status is not None:
                if status.get("playlist") == target:
                    log.info("FPP reports playlist %s active", target)
                    return True
                if not expect_photo and status.get("status_name") == "playing":
                    log.info("FPP reports playing (playlist name not exposed in this mode)")
                    return True
            time.sleep(poll_interval)

        log.error("break_in_and_sync: no sign playlist %s ran within %.0fs -- will nack", target, timeout)
        return False

    def wait_until_ready(self, settle_seconds: float = 10.0, max_wait: float = 120.0) -> bool:
        """Block until fppd answers its status API, then a further
        `settle_seconds`. The daemon is launched by postStart.sh at the same
        moment fppd starts, and fppd's HTTP API answers before its main loop
        is running -- commands sent in that gap are silently dropped."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if self._fetch_status() is not None:
                time.sleep(settle_seconds)
                return True
            time.sleep(2)
        return False

    def push_photo_overlay(self, photo: Image.Image, child_name: str, status: str) -> bool:
        """Composite photo onto a matrix_width x matrix_width canvas with
        breaking-news overlays, write it to DISPLAY_IMAGE_PATH."""
        w = self.matrix_width
        lower_y = self.photo_zone_height - 22 if self.photo_zone_height >= 22 else 0
        badge_x = int(w * 0.635)

        try:
            canvas = Image.new("RGBA", (w, w), (0, 0, 0, 255))
            canvas.alpha_composite(photo.convert("RGBA"), (0, 0))
            canvas = canvas.convert("RGB")
            draw = ImageDraw.Draw(canvas)

            draw.rectangle([(0, 0), (w - 1, _HEADER_H - 1)], fill=_COLOR_RED)
            font_header = _get_font(11, bold=True)
            _draw_text_centered(draw, "BREAKING NEWS", font_header, 6, 0, w - 1, _HEADER_H, _COLOR_WHITE)

            draw.rectangle([(0, lower_y), (w - 1, lower_y + _LOWER_H - 1)], fill=_COLOR_NAVY)

            badge_color = _COLOR_NICE if status == "nice" else _COLOR_NAUGHTY
            draw.rectangle([(badge_x, lower_y), (w - 1, lower_y + _LOWER_H - 1)], fill=badge_color)

            font_name = _get_font(10)
            font_status = _get_font(10, bold=True)

            name_display = child_name.upper()
            while font_name.getbbox(name_display)[2] > (badge_x - 8) and len(name_display) > 1:
                name_display = name_display[:-1]
            draw.text((4, lower_y + 5), name_display, fill=_COLOR_WHITE, font=font_name)

            status_text = "NICE" if status == "nice" else "NAUGHTY"
            _draw_text_centered(draw, status_text, font_status, badge_x, lower_y, w - 1, lower_y + _LOWER_H, _COLOR_WHITE)

        except Exception as exc:
            log.error("push_photo_overlay image processing error: %s", exc)
            return False

        # Write atomically (tmp + rename) so the display script never reads
        # a half-written PNG.
        tmp_path = DISPLAY_IMAGE_PATH + ".tmp"
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            canvas.save(tmp_path, format="PNG")
            os.replace(tmp_path, DISPLAY_IMAGE_PATH)
            log.info("Wrote display image to %s", DISPLAY_IMAGE_PATH)
            return True
        except OSError as exc:
            log.error("push_photo_overlay write failed (%s): %s", DISPLAY_IMAGE_PATH, exc)
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return False

    def get_ticker_font(self) -> str:
        """Resolve a font name this FPP box actually has, via
        GET /api/overlays/fonts (a JSON array of names, or null if fppd found
        none). Cached per base_url once resolved, since FPPClient is rebuilt
        every poll loop and fppd only scans fonts at startup."""
        cached = _TICKER_FONT_CACHE.get(self.base_url)
        if cached:
            return cached
        fallback = _PREFERRED_TICKER_FONTS[0]
        try:
            r = requests.get(f"{self.base_url}/api/overlays/fonts", timeout=self.timeout)
            if r.status_code != 200:
                log.warning("FPP font list -> HTTP %s; falling back to %s", r.status_code, fallback)
                return fallback
            fonts = r.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("FPP font list failed (%s); falling back to %s", exc, fallback)
            return fallback

        chosen = _pick_font(fonts if isinstance(fonts, list) else [])
        if not chosen:
            log.error("FPP reports no fonts installed, so the ticker Text effect cannot render. "
                      "Fix on the FPP box: 'apt-get install -y fonts-dejavu-core' (as root) then a full "
                      "FPPD restart (plugin page -> Restart FPPD (full)).")
            return fallback  # don't cache; re-check next time in case fonts get installed
        if chosen != fallback:
            log.info("Ticker font %s not on this FPP; using %s", fallback, chosen)
        _TICKER_FONT_CACHE[self.base_url] = chosen
        return chosen

    def push_ticker_text(self, child_name: str, status: str) -> bool:
        """FPP 9.x command API: args MUST be an array, not a dict."""
        label = status.upper()
        color = "#00FF00" if status == "nice" else "#FF0000"
        message = f"  BREAKING: {child_name} is on Santa's {label} LIST!  "

        payload = {
            "command": "Overlay Model Effect",
            "args": [
                self.ticker_model, "Enabled", "Text", color,
                self.get_ticker_font(), "16", "false", "Right to Left", "30", "0", message,
            ],
        }
        try:
            r = requests.post(f"{self.base_url}/api/command", json=payload, timeout=self.timeout)
            if r.status_code in (200, 204):
                log.info("Ticker text pushed: %s", message.strip())
                return True
            log.error("push_ticker_text -> HTTP %s: %s", r.status_code, r.text[:200])
            return False
        except requests.RequestException as exc:
            log.error("push_ticker_text exception: %s", exc)
            return False
