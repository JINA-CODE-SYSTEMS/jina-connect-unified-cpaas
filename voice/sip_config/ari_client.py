"""Asterisk ARI REST client (#163).

Thin wrapper around ``requests`` for the REST half of ARI. The
event-stream (WebSocket) half lives in ``ari_consumer.py`` so the
process model — one long-running consumer per Asterisk instance — is
clearly separated from per-call REST commands.

ARI docs: https://docs.asterisk.org/Asterisk_18_Documentation/Asterisk_18_REST_API/
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15


class AriError(Exception):
    """Raised on a non-2xx ARI response.

    Carries the underlying ``status_code`` + ``body`` so callers can
    decide whether to retry or surface the error to the user.
    """

    def __init__(self, status_code: int, body: str):
        super().__init__(f"ARI {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


class AriClient:
    """REST half of the Asterisk ARI interface."""

    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        app_name: str | None = None,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "ASTERISK_ARI_URL", "")).rstrip("/")
        self.username = username or getattr(settings, "ASTERISK_ARI_USER", "")
        self.password = password or getattr(settings, "ASTERISK_ARI_PASSWORD", "")
        self.app_name = app_name or getattr(settings, "ASTERISK_ARI_APP_NAME", "jina-voice")
        self._session = requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
    ) -> requests.Response:
        if not self.base_url:
            raise AriError(503, "ASTERISK_ARI_URL is not configured")
        url = self._url(path)
        resp = self._session.request(
            method,
            url,
            params=params,
            json=json_body,
            auth=(self.username, self.password),
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        if not resp.ok:
            raise AriError(resp.status_code, resp.text)
        return resp

    # ── Channels (calls) ────────────────────────────────────────────────

    def originate(
        self,
        *,
        endpoint: str,
        callerid: str,
        extension: str = "s",
        context: str = "jina-voice-outbound",
        timeout: int = 30,
        variables: dict | None = None,
    ) -> dict:
        """Place an outbound call via ARI ``POST /channels``.

        ``endpoint`` is a PJSIP endpoint id (``voice-<uuid>-endpoint``)
        plus the dialed number, e.g. ``PJSIP/+14155550100@voice-...``.
        Returns the ARI ``Channel`` resource.
        """
        body: dict[str, Any] = {
            "endpoint": endpoint,
            "extension": extension,
            "context": context,
            "callerId": callerid,
            "timeout": timeout,
            "app": self.app_name,
        }
        if variables:
            body["variables"] = variables
        resp = self._request("POST", "ari/channels", json_body=body)
        return resp.json()

    def hangup_channel(self, channel_id: str, reason: str = "normal") -> None:
        self._request(
            "DELETE",
            f"ari/channels/{channel_id}",
            params={"reason": reason},
        )

    def play_media(self, channel_id: str, *, media: str) -> dict:
        """Play media on a channel via ``POST /channels/{id}/play``.

        ``media`` is an Asterisk media URI — ``sound:hello-world`` for
        bundled prompts, ``http://example.com/audio.wav`` for remote URLs,
        ``recording:<name>`` for a local recording.
        """
        resp = self._request(
            "POST",
            f"ari/channels/{channel_id}/play",
            params={"media": media},
        )
        return resp.json()

    def record_channel(
        self,
        channel_id: str,
        *,
        name: str,
        max_duration_seconds: int,
        beep: bool = True,
        format: str = "wav",
    ) -> dict:
        """Start a MixMonitor-style recording via ``POST /channels/{id}/record``."""
        params = {
            "name": name,
            "format": format,
            "maxDurationSeconds": max_duration_seconds,
            "beep": "true" if beep else "false",
            "ifExists": "overwrite",
        }
        resp = self._request(
            "POST",
            f"ari/channels/{channel_id}/record",
            params=params,
        )
        return resp.json()

    def stop_recording(self, recording_name: str) -> None:
        self._request("POST", f"ari/recordings/live/{recording_name}/stop")

    def refer_channel(self, channel_id: str, *, to_uri: str) -> None:
        """SIP REFER the channel to ``to_uri`` (blind transfer)."""
        self._request(
            "POST",
            f"ari/channels/{channel_id}/redirect",
            params={"endpoint": to_uri},
        )

    def get_recording_file(self, recording_name: str, fmt: str = "wav") -> bytes:
        """Fetch the recorded audio bytes from Asterisk's recording store."""
        # Asterisk serves recordings under a static URL — outside ARI
        # proper. We hit ARI's recording GET to learn the file path,
        # then fetch via a regular HTTP GET. For simplicity here we
        # use the ``stored`` endpoint which returns the audio directly
        # when supported; fallback callers can read from disk.
        resp = self._request("GET", f"ari/recordings/stored/{recording_name}/file")
        return resp.content
