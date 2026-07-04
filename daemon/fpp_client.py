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
- Photo display: composite onto background, upload raw PNG bytes via
  POST /api/file/images/{filename} (NOT /jqUpload) -> Image playlist entry
- Ticker text: "Text" effect (not "Scrolling Text"); array arg order matters
"""

import io
import logging
import os
import time
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_DISPLAY_FILENAME = "current_display.png"

# Breaking news overlay dimensions (PhotoZone is 192x140 by default —
# configurable via settings.json for other matrix layouts)
_HEADER_H = 20
_LOWER_H = 22

_COLOR_RED = (204, 0, 0)
_COLOR_NAVY = (0, 20, 90)
_COLOR_NICE = (0, 160, 40)
_COLOR_NAUGHTY = (200, 0, 0)
_COLOR_WHITE = (255, 255, 255)


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
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

    def break_in_and_sync(self, name: Optional[str] = None, timeout: float = 5.0,
                           poll_interval: float = 0.25) -> bool:
        """Break into `name` (or self.playlist) and block until FPP confirms
        the playlist is actually the active one before returning.

        Why this exists: break_in_playlist() only confirms fppd *accepted*
        the start command over HTTP -- it does not mean the matrix is
        actually outputting that content yet. fppd needs a beat to stop
        whatever was running and load the new playlist/media, and that gap
        was measurably a couple of seconds on the real Pi 5. Meanwhile
        push_ticker_text() is an "Overlay Model Effect" command applied on
        a separate render path (TickerZone), which takes effect essentially
        immediately. Calling the two back-to-back without this wait meant
        the ticker text started scrolling visibly before the photo/
        background playlist appeared -- reported by Joe as "text scrolling
        started before the rest of the screen rendered by a few seconds."

        Polls GET /api/system/status (see _fetch_status) and watches its
        "playlist" field for the target name -- confirmed via a live status
        dump that this field is empty when no playlist is the active
        selection and becomes the playlist's name once fppd has actually
        switched to it. If FPP never reports the switch within `timeout`,
        logs a warning and returns True anyway (the break-in call itself
        succeeded, so we don't want a flaky status poll to nack an
        otherwise-working submission) -- worst case, sync degrades back to
        the old race instead of the item failing outright.
        """
        target = name or self.playlist
        if not self.break_in_playlist(target):
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._fetch_status()
            if status is not None and status.get("playlist") == target:
                log.info("Confirmed FPP switched to playlist %s before signaling ticker", target)
                return True
            time.sleep(poll_interval)

        log.warning(
            "break_in_and_sync: FPP never reported playlist=%s as active within %.1fs -- "
            "proceeding anyway, ticker text may start slightly ahead of the visual",
            target, timeout,
        )
        return True

    def push_photo_overlay(self, photo: Image.Image, child_name: str, status: str) -> bool:
        """Composite photo onto a matrix_width x matrix_width canvas with
        breaking-news overlays, upload as current_display.png."""
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

            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            buf.seek(0)
        except Exception as exc:
            log.error("push_photo_overlay image processing error: %s", exc)
            return False

        url = f"{self.base_url}/api/file/images/{_DISPLAY_FILENAME}"
        try:
            r = requests.post(url, data=buf.read(), headers={"Content-Type": "image/png"}, timeout=max(self.timeout, 15))
            if r.status_code in (200, 204):
                log.info("Uploaded %s to FPP", _DISPLAY_FILENAME)
                return True
            log.error("push_photo_overlay upload -> HTTP %s: %s", r.status_code, r.text[:200])
            return False
        except requests.RequestException as exc:
            log.error("push_photo_overlay upload exception: %s", exc)
            return False

    def push_ticker_text(self, child_name: str, status: str) -> bool:
        """FPP 9.x command API: args MUST be an array, not a dict."""
        label = status.upper()
        color = "#00FF00" if status == "nice" else "#FF0000"
        message = f"  BREAKING: {child_name} is on Santa's {label} LIST!  "

        payload = {
            "command": "Overlay Model Effect",
            "args": [
                self.ticker_model, "Enabled", "Text", color,
                "DejaVuSans", "16", "false", "Right to Left", "30", "0", message,
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
