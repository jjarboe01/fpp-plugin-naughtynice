#!/usr/bin/env python3
"""
NaughtyNice Cloud plugin daemon.

Runs on the FPP box itself. Polls the cloud service outbound-only (never
accepts inbound connections), pulls new submissions, renders them onto the
local matrix via FPPClient, and acks/nacks each one. Designed to survive
network blips indefinitely without taking fppd down with it — every
exception inside the loop is caught, logged, and turned into a nack or a
backoff rather than a crash.

Launched by scripts/postStart.sh, stopped by scripts/preStop.sh (SIGTERM).
"""

import logging
import signal
import sys
import time
from datetime import datetime, timezone

from cloud_client import CloudApiError, CloudClient, InvalidToken, LicenseExpired
from config import load_settings, read_status, write_status
from fpp_client import FPPClient
from image_processor import prepare_display_image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [nnl_daemon] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nnl_daemon")

PLUGIN_VERSION = "0.2.0"
MAX_BACKOFF_SECONDS = 300

_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    log.info("Received signal %s, shutting down after current cycle", signum)
    _stop = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interruptible_sleep(seconds: float) -> None:
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def _process_item(cloud: CloudClient, fpp: FPPClient, item: dict, matrix_width: int, photo_zone_height: int, plan: str = "full") -> None:
    submission_id = item["id"]
    try:
        if plan == "text_only":
            # No photo zone to render into — just break in and scroll the
            # ticker text. Skips photo download/compositing/upload entirely
            # so text-only shows have no dependency on a PhotoZone model.
            # break_in_and_sync (not break_in_playlist) waits for FPP to
            # confirm the playlist actually switched before the ticker
            # fires — see its docstring for why (fppd's own start-up lag
            # vs. the ticker's near-instant overlay command was causing the
            # text to visibly start before the background playlist did).
            ok = fpp.break_in_and_sync()
            ok = ok and fpp.push_ticker_text(item["child_name"], item["verdict"])
        else:
            photo_bytes = None
            if item.get("photo_url"):
                photo_bytes = cloud.fetch_photo(item["photo_url"])

            display_img = prepare_display_image(photo_bytes, item.get("gender"), matrix_width, photo_zone_height)

            ok = fpp.push_photo_overlay(display_img, item["child_name"], item["verdict"])
            ok = ok and fpp.break_in_and_sync()
            ok = ok and fpp.push_ticker_text(item["child_name"], item["verdict"])

        if ok:
            cloud.ack(submission_id)
            log.info("Delivered submission %s (%s, %s)", submission_id, item["child_name"], item["verdict"])
        else:
            cloud.nack(submission_id, "one or more FPP calls failed — see plugin log")
            log.warning("Nacked submission %s after FPP call failure", submission_id)
    except Exception as exc:  # noqa: BLE001 — one bad item must never kill the loop
        log.exception("Unhandled error processing submission %s", submission_id)
        try:
            cloud.nack(submission_id, f"exception: {exc}")
        except Exception:
            log.exception("Also failed to nack submission %s after the original error", submission_id)


def run() -> None:
    log.info("Starting NaughtyNice Cloud plugin daemon v%s", PLUGIN_VERSION)

    items_processed_total = read_status().get("items_processed_total", 0)
    backoff_seconds = 1

    while not _stop:
        settings = load_settings()

        if not settings.enabled:
            write_status(license="disabled", last_poll_at=_now_iso())
            _interruptible_sleep(max(settings.poll_interval_seconds, 5))
            continue

        if not settings.is_configured:
            write_status(license="not_configured", last_poll_at=_now_iso(),
                          last_error="Token and/or cloud URL not set — visit the plugin's setup page")
            _interruptible_sleep(max(settings.poll_interval_seconds, 5))
            continue

        cloud = CloudClient(settings.cloud_base_url, settings.token)
        fpp = FPPClient(
            settings.fpp_base_url, settings.ticker_model, settings.playlist,
            matrix_width=settings.matrix_width, photo_zone_height=settings.photo_zone_height,
        )

        try:
            ping = cloud.ping()
        except InvalidToken:
            write_status(license="invalid_token", last_poll_at=_now_iso(), last_error="Cloud rejected the token — regenerate it in the dashboard")
            backoff_seconds = min(backoff_seconds * 2, MAX_BACKOFF_SECONDS)
            _interruptible_sleep(backoff_seconds)
            continue
        except CloudApiError as exc:
            log.warning("ping failed: %s (retrying in %ss)", exc, backoff_seconds)
            write_status(last_error=str(exc), last_poll_at=_now_iso())
            backoff_seconds = min(backoff_seconds * 2, MAX_BACKOFF_SECONDS)
            _interruptible_sleep(backoff_seconds)
            continue

        backoff_seconds = 1  # reset now that the cloud responded
        license_state = ping.get("license", "unknown")
        license_expires = ping.get("expires")

        if license_state != "active":
            write_status(license=license_state, license_expires=license_expires,
                         last_poll_at=_now_iso(), last_error=None)
            _interruptible_sleep(max(settings.poll_interval_seconds, 5))
            continue

        try:
            queue = cloud.get_queue()
        except LicenseExpired:
            write_status(license="expired", last_poll_at=_now_iso())
            _interruptible_sleep(max(settings.poll_interval_seconds, 5))
            continue
        except (CloudApiError, InvalidToken) as exc:
            log.warning("queue fetch failed: %s", exc)
            write_status(last_error=str(exc), last_poll_at=_now_iso())
            _interruptible_sleep(max(settings.poll_interval_seconds, 5))
            continue

        plan = queue.get("show", {}).get("plan", "full")

        for item in queue.get("items", []):
            _process_item(cloud, fpp, item, settings.matrix_width, settings.photo_zone_height, plan=plan)
            items_processed_total += 1

        cloud.telemetry(PLUGIN_VERSION, fpp_version=None)

        write_status(
            license="active",
            license_expires=license_expires,
            plan=plan,
            last_poll_at=_now_iso(),
            last_error=None,
            items_processed_total=items_processed_total,
            plugin_version=PLUGIN_VERSION,
        )

        _interruptible_sleep(max(settings.poll_interval_seconds, 5))

    log.info("Daemon stopped cleanly")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    run()
