"""Client for the NaughtyNice Cloud plugin API (naughtynice-cloud repo,
/api/v1/*). Outbound-only: every call here is the plugin reaching out to the
cloud service, never the reverse."""

import logging
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)


class CloudApiError(Exception):
    """Raised for network failures or unexpected (non-4xx-handled) responses."""


class LicenseExpired(Exception):
    """Raised when the cloud reports 402 — show's license has lapsed."""


class InvalidToken(Exception):
    """Raised when the cloud reports 401 — token is missing/wrong/revoked."""


# Photos are small JPEGs (the cloud caps uploads at 5 MB and re-encodes);
# anything bigger than this isn't one of ours.
MAX_PHOTO_BYTES = 10 * 1024 * 1024


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

    def _json(self, method: str, path: str) -> dict:
        r = self._request(method, path)
        try:
            data = r.json()
        except ValueError as exc:
            raise CloudApiError(f"{method} {path} returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise CloudApiError(f"{method} {path} returned unexpected JSON")
        return data

    def ping(self) -> dict:
        return self._json("GET", "/api/v1/ping")

    def get_queue(self) -> dict:
        return self._json("GET", "/api/v1/queue")

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
        """Download a submission photo. The cloud's photo endpoint needs the
        plugin token, so the request carries it -- which is exactly why it
        must only ever go to the configured cloud host:

        - the URL's host must be the cloud_base_url host, otherwise refused
          (the request is rebuilt on cloud_base_url, so it also keeps its
          https scheme even if the cloud sees itself as plain http behind
          its proxy);
        - redirects are not followed, so the token can't be bounced to
          another host;
        - the body is capped at MAX_PHOTO_BYTES.
        """
        base = urlsplit(self.base_url)
        url = urlsplit(photo_url)
        if not url.hostname or url.hostname.lower() != (base.hostname or "").lower():
            raise CloudApiError("photo URL is not on the configured cloud host -- refused")
        target = self.base_url + (url.path or "/") + (f"?{url.query}" if url.query else "")

        try:
            with self._session.get(target, timeout=self.timeout, allow_redirects=False, stream=True) as r:
                if r.status_code != 200:
                    raise CloudApiError(f"photo download -> HTTP {r.status_code}")
                declared = r.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > MAX_PHOTO_BYTES:
                    raise CloudApiError("photo larger than the size limit -- refused")
                chunks, total = [], 0
                for chunk in r.iter_content(64 * 1024):
                    total += len(chunk)
                    if total > MAX_PHOTO_BYTES:
                        raise CloudApiError("photo larger than the size limit -- refused")
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException as exc:
            raise CloudApiError(f"photo download failed: {type(exc).__name__}") from exc
