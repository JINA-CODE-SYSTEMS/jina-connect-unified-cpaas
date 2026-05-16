"""S3-compatible recording storage (#161).

Voice recordings get their own bucket (``VOICE_RECORDING_STORAGE_BUCKET``)
so retention policies / lifecycle rules can be applied independently of
the main media bucket. Uses ``boto3`` directly with AWS credentials
already configured for Django's media storage (``AWS_ACCESS_KEY_ID`` /
``AWS_SECRET_ACCESS_KEY`` / ``AWS_S3_REGION_NAME``).

Path layout::

    {tenant_id}/{call_id}/{provider_recording_id}.{format}

This namespaces recordings per tenant for easy listing + retention
sweeps, and per call for easy lookup when reviewing a single inbox row.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)


# Default URL TTL used by ``signed_url`` when callers don't pass one.
DEFAULT_SIGNED_URL_EXPIRES_SECONDS = 3600


def _client():
    """Lazily build a boto3 S3 client.

    Building lazily means the rest of the module imports cleanly even
    when AWS credentials are not configured (e.g. in unit tests).
    """
    import boto3

    return boto3.client(
        "s3",
        aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", None) or None,
        aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", None) or None,
        region_name=getattr(settings, "AWS_S3_REGION_NAME", None) or None,
    )


def _bucket() -> str:
    """Return the configured recording bucket name.

    Falls back to ``AWS_STORAGE_BUCKET_NAME`` if no dedicated voice
    bucket is set — useful for dev where teams don't want to provision
    two buckets, but production should always set the dedicated var so
    retention lifecycle rules don't collide with the media bucket.

    Logs a loud warning whenever the fallback kicks in so accidental
    production drift surfaces in dashboards instead of silently
    putting the retention sweep on the shared media bucket. Tenants
    that want to make the fallback a hard failure set
    ``VOICE_REQUIRE_DEDICATED_RECORDING_BUCKET=True``. (#179 review)
    """
    voice_bucket = getattr(settings, "VOICE_RECORDING_STORAGE_BUCKET", "") or ""
    if voice_bucket:
        return voice_bucket

    media_bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or ""
    if media_bucket:
        if getattr(settings, "VOICE_REQUIRE_DEDICATED_RECORDING_BUCKET", False):
            raise ValueError(
                "VOICE_RECORDING_STORAGE_BUCKET is required but unset. "
                "Refusing to fall back to the shared media bucket "
                "(AWS_STORAGE_BUCKET_NAME) because retention rules "
                "would conflict."
            )
        logger.warning(
            "[voice.recordings.storage] VOICE_RECORDING_STORAGE_BUCKET unset; "
            "falling back to AWS_STORAGE_BUCKET_NAME=%r. Production should "
            "provision a dedicated voice-recordings bucket.",
            media_bucket,
        )
        return media_bucket

    raise ValueError(
        "Voice recording storage is not configured. Set "
        "VOICE_RECORDING_STORAGE_BUCKET (or AWS_STORAGE_BUCKET_NAME) "
        "in the environment."
    )


def make_storage_key(
    tenant_id: "UUID | str",
    call_id: "UUID | str",
    provider_recording_id: str,
    fmt: str,
) -> str:
    """Construct the S3 object key for a recording.

    Kept as a small public function so tests + callers don't reimplement
    the path layout.
    """
    return f"{tenant_id}/{call_id}/{provider_recording_id}.{fmt}"


def upload(storage_key: str, audio_bytes: bytes, content_type: str) -> str:
    """Upload ``audio_bytes`` to the recording bucket and return the key.

    Returns the same ``storage_key`` so callers can persist it on
    ``VoiceRecording.storage_url`` without rebuilding the path.
    """
    client = _client()
    client.put_object(
        Bucket=_bucket(),
        Key=storage_key,
        Body=audio_bytes,
        ContentType=content_type,
    )
    logger.info("[voice.recordings.storage] uploaded %s", storage_key)
    return storage_key


def fetch(storage_key: str) -> bytes:
    """Download a recording from the bucket and return its bytes.

    Used by the transcription task layer (#169). Lets transcription
    backends stay storage-agnostic — they only see audio bytes.
    """
    client = _client()
    obj = client.get_object(Bucket=_bucket(), Key=storage_key)
    body = obj.get("Body")
    if body is None:
        return b""
    return body.read()


def signed_url(storage_key: str, expires_seconds: int | None = None) -> str:
    """Return a presigned GET URL for an existing recording.

    The URL is valid for ``expires_seconds`` (default 1 hour). Callers
    surface this URL via MCP / REST / inbox — never the raw S3 path,
    so private buckets stay private.
    """
    expires = expires_seconds or DEFAULT_SIGNED_URL_EXPIRES_SECONDS
    client = _client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": storage_key},
        ExpiresIn=expires,
    )


def delete(storage_key: str) -> None:
    """Delete a recording from the bucket.

    Used by the retention task. Missing keys are ignored — the goal is
    "after this call, the object no longer exists", and an already-gone
    object satisfies that.
    """
    client = _client()
    try:
        client.delete_object(Bucket=_bucket(), Key=storage_key)
        logger.info("[voice.recordings.storage] deleted %s", storage_key)
    except Exception:  # noqa: BLE001 — log + swallow so retention sweep continues
        logger.exception("[voice.recordings.storage] failed to delete %s", storage_key)
