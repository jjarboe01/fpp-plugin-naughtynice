"""Reads/writes this plugin's settings and status files.

Both files are plain JSON under <plugin_dir>/config/, shared between the
Python daemon (this module) and content.php (the FPP UI page). Keeping
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

# Two named environments so you can flip between prod and dev for plugin/
# server testing without re-typing tokens each time. Each keeps its own
# base URL + show token; "environment" picks which one the daemon uses.
#
# NOTE: this toggle is manual/human-operated by design. Nothing in this
# codebase should change SCALAR_DEFAULTS["environment"] or write a
# different "environment" value into settings.json on its own — Joe flips
# it deliberately via the plugin's setup page when he wants to test
# against dev vs prod. Don't "helpfully" switch it during unrelated work.
ENVIRONMENTS = ("prod", "dev")

ENVIRONMENT_DEFAULTS = {
    "prod": {"cloud_base_url": "https://naughtynicefpp.com", "token": ""},
    "dev": {"cloud_base_url": "https://dev.naughtynicefpp.com", "token": ""},
}

SCALAR_DEFAULTS = {
    "environment": "prod",
    "fpp_base_url": "http://localhost",
    "poll_interval_seconds": 10,
    "playlist": "breaking_news",
    "ticker_model": "TickerZone",
    "matrix_width": 192,
    "photo_zone_height": 140,
    "enabled": True,
}


def _default_environments() -> dict:
    return {name: dict(vals) for name, vals in ENVIRONMENT_DEFAULTS.items()}


@dataclass
class Settings:
    environment: str = "prod"
    environments: dict = field(default_factory=_default_environments)
    fpp_base_url: str = "http://localhost"
    poll_interval_seconds: int = 10
    playlist: str = "breaking_news"
    ticker_model: str = "TickerZone"
    matrix_width: int = 192
    photo_zone_height: int = 140
    enabled: bool = True

    @property
    def cloud_base_url(self) -> str:
        return self.environments.get(self.environment, {}).get("cloud_base_url", "")

    @property
    def token(self) -> str:
        return self.environments.get(self.environment, {}).get("token", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.cloud_base_url)


def load_settings() -> Settings:
    data = dict(SCALAR_DEFAULTS)
    data["environments"] = _default_environments()

    raw = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = {}  # fall back to defaults rather than crash the daemon

    if not isinstance(raw, dict):
        raw = {}

    # Migrate pre-environment-toggle settings.json (flat cloud_base_url/
    # token fields, no "environments" key) into the "prod" slot the first
    # time this runs against an old file. Whatever was configured before
    # this feature existed was, by definition, the production setup.
    if "environments" not in raw and ("cloud_base_url" in raw or "token" in raw):
        if raw.get("cloud_base_url"):
            data["environments"]["prod"]["cloud_base_url"] = raw["cloud_base_url"]
        data["environments"]["prod"]["token"] = raw.get("token", "")
        data["environment"] = "prod"

    for key in SCALAR_DEFAULTS:
        if key in raw:
            data[key] = raw[key]

    raw_envs = raw.get("environments")
    if isinstance(raw_envs, dict):
        for name in ENVIRONMENTS:
            env_raw = raw_envs.get(name)
            if isinstance(env_raw, dict):
                data["environments"][name].update(
                    {k: v for k, v in env_raw.items() if k in ("cloud_base_url", "token")}
                )

    if data["environment"] not in ENVIRONMENTS:
        data["environment"] = "prod"

    return Settings(**data)


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
    os.replace(tmp_path, STATUS_PATH)  # atomic, so content.php never reads a half-written file


def read_status() -> dict:
    if os.path.exists(STATUS_PATH):
        try:
            with open(STATUS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}
