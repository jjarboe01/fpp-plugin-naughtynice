"""Reads/writes this plugin's settings and status files.

Both files are plain JSON under <plugin_dir>/config/, shared between the
Python daemon (this module) and plugin_setup.php (the FPP UI page). Keeping
the format dead simple (no dependency on FPP's PHP settings helpers) means
either side can read/write it without the other.
"""

import json
import os
from dataclasses import asdict, dataclass, field

PLUGIN_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_DIR = os.path.join(PLUGIN_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
STATUS_PATH = os.path.join(CONFIG_DIR, "status.json")

DEFAULTS = {
    "token": "",
    "cloud_base_url": "",
    "fpp_base_url": "http://localhost",
    "poll_interval_seconds": 10,
    "playlist": "breaking_news",
    "ticker_model": "TickerZone",
    "matrix_width": 192,
    "photo_zone_height": 140,
    "enabled": True,
}


@dataclass
class Settings:
    token: str = ""
    cloud_base_url: str = ""
    fpp_base_url: str = "http://localhost"
    poll_interval_seconds: int = 10
    playlist: str = "breaking_news"
    ticker_model: str = "TickerZone"
    matrix_width: int = 192
    photo_zone_height: int = 140
    enabled: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.cloud_base_url)


def load_settings() -> Settings:
    data = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                data.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass  # fall back to defaults rather than crash the daemon
    known = {k: v for k, v in data.items() if k in DEFAULTS}
    return Settings(**known)


def save_settings(settings: Settings) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(asdict(settings), f, indent=2)


def write_status(**kwargs) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    status = read_status()
    status.update(kwargs)
    tmp_path = STATUS_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(status, f, indent=2)
    os.replace(tmp_path, STATUS_PATH)  # atomic, so plugin_setup.php never reads a half-written file


def read_status() -> dict:
    if os.path.exists(STATUS_PATH):
        try:
            with open(STATUS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}
