#!/usr/bin/env python3
"""
NaughtyNice Cloud plugin daemon.

Runs on the FPP box itself. Polls the cloud service outbound-only (never
accepts inbound connections), pulls new submissions, renders them onto the
local matrix via FPPClient, and acks/nacks each one. Designed to survive
network blips indefinitely without taking fppd down with it — every
exception inside the loop is caught, logged, and turned into a nack or a
backoff rather than a crash (each item is guarded on its own, and the
whole poll cycle has a catch-all guard too).

Logging: one file, <logdir>/plugin-fpp-plugin-naughtynice.log (FPP
guideline §1.1), written through a WatchedFileHandler so logging carries on
into the fresh file after FPP rotates plugin-*.log (§1.2). Visitor data
(children's names) is never logged, and nack reasons sent to the cloud are
fixed strings, never raw exception text.

Launched by scripts/postStart.sh, stopped by scripts/preStop.sh (SIGTERM).
"""

import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone

LOG_NAME = "plugin-fpp-plugin-naughtynice.log"


def _log_path() -> str:
    """postStart.sh passes the resolved path in NNL_LOGFILE; FPP also exports
    LOGDIR to processes it launches; last resort is the default install."""
    if os.environ.get("NNL_LOGFILE"):
        return os.environ["NNL_LOGFILE"]
    return os.path.join(os.environ.get("LOGDIR") or "/home/fpp/media/logs", LOG_NAME)


def _setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s [nnl_daemon] %(message)s")
    try:
        handler = logging.handlers.WatchedFileHandler(_log_path())
    except OSError:
        handler = logging.StreamHandler(sys.stdout)  # e.g. running by hand off-box
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)

    def _log_uncaught(exc_type, exc, tb):
        logging.getLogger("nnl_daemon").critical("Daemon crashed", exc_info=(exc_type, exc, tb))
    sys.excepthook = _log_uncaught


log = logging.getLogger("nnl_daemon")

# Logging first, so even a failed import below (e.g. a missing
# python3-requests) is recorded in the plugin log rather than lost.
_setup_logging()

from cloud_client import CloudApiError, CloudClient, InvalidToken, LicenseExpired  # noqa: E402
from config import load_settings, read_status, write_status  # noqa: E402
from fpp_client import DISPLAY_IMAGE_PATH, FPPClient  # noqa: E402
from fpp_provision import ensure_script_entry_blocking  # noqa: E402
from image_processor import prepare_display_image  # noqa: E402

PLUGIN_VERSION = "0.4.0"
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


def _discard_display_image() -> None:
    """Delivery failed, so the display script will never run and delete the
    composited photo itself -- remove it here so it isn't left on disk."""
    try:
        os.remove(DISPLAY_IMAGE_PATH)
    except OSError:
        pass


# Fixed nack reasons: what the cloud stores and shows must never carry raw
# exception text (which can include URLs or response bodies).
NACK_FPP_FAILED = "fpp_call_failed"
NACK_PHOTO_FAILED = "photo_download_failed"
NACK_BAD_ITEM = "malformed_item"
NACK_UNEXPECTED = "unexpected_error"


def _safe_nack(cloud: CloudClient, submission_id, reason: str) -> None:
    try:
        cloud.nack(submission_id, reason)
    except Exception:  # noqa: BLE001
        log.warning("Could not nack submission %s (%s)", submission_id, reason)


def _process_item(cloud: CloudClient, fpp: FPPClient, item: dict, matrix_width: int, photo_zone_height: int, plan: str = "full") -> None:
    submission_id = item.get("id") if isinstance(item, dict) else None
    if submission_id is None:
        log.warning("Skipping a queue item with no id")
        return
    if not item.get("child_name") or item.get("verdict") not in ("nice", "naughty"):
        log.warning("Submission %s is missing its name or verdict -- nacking", submission_id)
        _safe_nack(cloud, submission_id, NACK_BAD_ITEM)
        return

    try:
        if plan == "text_only":
            # No photo zone to render into — just break in and scroll the
            # ticker text. Skips photo download/compositing/upload entirely
            # so text-only shows have no dependency on a PhotoZone model.
            # break_in_and_sync waits for proof the playlist ran before the
            # ticker fires, and returns False (-> nack) if there is none.
            ok = fpp.break_in_and_sync(expect_photo=False)
            ok = ok and fpp.push_ticker_text(item["child_name"], item["verdict"])
        else:
            photo_bytes = None
            if item.get("photo_url"):
                try:
                    photo_bytes = cloud.fetch_photo(item["photo_url"])
                except CloudApiError as exc:
                    log.warning("Photo download for submission %s failed: %s", submission_id, exc)
                    _safe_nack(cloud, submission_id, NACK_PHOTO_FAILED)
                    return

            display_img = prepare_display_image(photo_bytes, item.get("gender"), matrix_width, photo_zone_height)

            ok = fpp.push_photo_overlay(display_img, item["child_name"], item["verdict"])
            ok = ok and fpp.break_in_and_sync(expect_photo=True)
            ok = ok and fpp.push_ticker_text(item["child_name"], item["verdict"])

        if ok:
            cloud.ack(submission_id)
            log.info("Delivered submission %s (%s)", submission_id, item["verdict"])
        else:
            _discard_display_image()
            _safe_nack(cloud, submission_id, NACK_FPP_FAILED)
            log.warning("Nacked submission %s after FPP call failure", submission_id)
    except Exception as exc:  # noqa: BLE001 — one bad item must never kill the loop
        # Exception type only: the message could carry visitor data.
        log.error("Unhandled %s processing submission %s", type(exc).__name__, submission_id)
        _discard_display_image()
        _safe_nack(cloud, submission_id, NACK_UNEXPECTED)


def run() -> None:
    log.info("Starting NaughtyNice Cloud plugin daemon v%s", PLUGIN_VERSION)

    items_processed_total = read_status().get("items_processed_total", 0)
    backoff_seconds = 1

    # postStart.sh launches us the moment fppd starts; give fppd time to get
    # its main loop running before sending it anything (see
    # FPPClient.wait_until_ready).
    startup = load_settings()
    if not FPPClient(startup.fpp_base_url, startup.ticker_model, startup.playlist).wait_until_ready():
        log.warning("FPP status API not answering after startup wait -- continuing anyway")

    # Plugin updates don't re-run fpp_install.sh, so existing installs get the
    # "blocking" fix for their breaking_news playlist here, once per start.
    try:
        note = ensure_script_entry_blocking(startup.fpp_base_url, startup.playlist)
        if note:
            log.info(note)
    except Exception as exc:  # noqa: BLE001 — a startup nicety, never fatal
        log.warning("Could not check the %s playlist at startup (%s)", startup.playlist, type(exc).__name__)

    while not _stop:
        try:
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
                ticker_zone_height=settings.ticker_zone_height,
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

            fpp_version = fpp.get_fpp_version()
            cloud.telemetry(PLUGIN_VERSION, fpp_version=fpp_version)

            write_status(
                license="active",
                license_expires=license_expires,
                plan=plan,
                last_poll_at=_now_iso(),
                last_error=None,
                items_processed_total=items_processed_total,
                plugin_version=PLUGIN_VERSION,
                fpp_version=fpp_version,
            )

            _interruptible_sleep(max(settings.poll_interval_seconds, 5))
        except Exception as exc:  # noqa: BLE001 — last-resort guard; never let the loop die
            log.exception("Unexpected %s in poll cycle -- backing off", type(exc).__name__)
            try:
                write_status(last_error="Unexpected error — see the plugin log", last_poll_at=_now_iso())
            except Exception:  # noqa: BLE001
                pass
            backoff_seconds = min(backoff_seconds * 2, MAX_BACKOFF_SECONDS)
            _interruptible_sleep(backoff_seconds)

    log.info("Daemon stopped cleanly")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    run()
