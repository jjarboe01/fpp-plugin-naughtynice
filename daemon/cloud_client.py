"""Client for the NaughtyNice Cloud plugin API (naughtynice-cloud repo,
/api/v1/*). Outbound-only: every call here is the plugin reaching out to the
cloud service, never the reverse."""

import logging

import requests

log = logging.getLogger(__name__)


class CloudApiError(Exception):
    """Raised for network failures or unexpected (non-4xx-handled) responses."""


class LicenseExpired(Exception):
    """Raised when the cloud reports 402 — show's license has lapsed."""


class InvalidToken(Exception):
    """Raised when the cloud reports 401 — token is missing/wrong/revoked."""


class CloudClient:
    def __init__(self, base_url: str, token: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            r = self._session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise CloudApiError(f"{method} {path} failed: {exc}") from exc

        if r.status_code == 401:
            raise InvalidToken("Cloud rejected the plugin token (401)")
        if r.status_code == 402:
            raise LicenseExpired("Show license has expired (402)")
        if r.status_code >= 400:
            raise CloudApiError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:200]}")
        return r

    def ping(self) -> dict:
        return self._request("GET", "/api/v1/ping").json()

    def get_queue(self) -> dict:
        return self._request("GET", "/api/v1/queue").json()

    def ack(self, submission_id: int) -> None:
        self._request("POST", f"/api/v1/queue/{submission_id}/ack")

    def nack(self, submission_id: int, reason: str) -> None:
        self._request("POST", f"/api/v1/queue/{submission_id}/nack", json={"reason": reason})

    def telemetry(self, plugin_version: str = None, fpp_version: str = None) -> None:
        body = {}
        if plugin_version is not None:
            body["plugin_version"] = plugin_version
        if fpp_version is not None:
            body["fpp_version"] = fpp_version
        if not body:
            return
        try:
            self._request("POST", "/api/v1/telemetry", json=body)
        except (CloudApiError, LicenseExpired, InvalidToken) as exc:
            # Telemetry is best-effort — never let it break the main loop.
            log.debug("telemetry report failed (non-fatal): %s", exc)

    def fetch_photo(self, photo_url: str) -> bytes:
        try:
            r = self._session.get(photo_url, timeout=self.timeout)
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            raise CloudApiError(f"fetch_photo {photo_url} failed: {exc}") from exc
