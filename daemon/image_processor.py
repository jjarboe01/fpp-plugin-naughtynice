"""
Image processing for the photo/silhouette overlay zone. Ported from
LightShow-NaughtyNice's app/image_processor.py (phase 1) — same fit/fallback
logic, but the "upload" here is bytes downloaded from the cloud queue rather
than a local Flask upload path.
"""

import logging
import os
from typing import Optional

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

SILHOUETTE_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "silhouettes")


def prepare_display_image(photo_bytes: Optional[bytes], gender: str, target_w: int, target_h: int) -> Image.Image:
    """Return a PIL Image (RGBA, target_w x target_h) for the photo zone.

    If photo_bytes decodes to a valid image, use it; otherwise fall back to
    the boy/girl silhouette."""
    img: Optional[Image.Image] = None

    if photo_bytes:
        try:
            import io
            img = Image.open(io.BytesIO(photo_bytes))
            img.load()
        except Exception as exc:
            log.warning("Could not decode downloaded photo (%s), falling back to silhouette", exc)
            img = None

    if img is None:
        img = _load_silhouette(gender, target_w, target_h)

    return _fit_to_zone(img, target_w, target_h)


def _load_silhouette(gender: str, fallback_w: int, fallback_h: int) -> Image.Image:
    filename = "boy.png" if gender == "boy" else "girl.png"
    path = os.path.join(SILHOUETTE_DIR, filename)

    if os.path.exists(path):
        try:
            return Image.open(path)
        except Exception as exc:
            log.error("Failed to load silhouette %s: %s", path, exc)

    log.warning("Silhouette file not found: %s — using placeholder", path)
    color = (30, 60, 200, 255) if gender == "boy" else (200, 30, 120, 255)
    return Image.new("RGBA", (fallback_w, fallback_h), color)


def _fit_to_zone(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    img = img.convert("RGBA")
    img = ImageOps.fit(img, (target_w, target_h), method=Image.LANCZOS)
    return img
