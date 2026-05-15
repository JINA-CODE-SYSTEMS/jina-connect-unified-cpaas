"""Render PJSIP endpoint/auth/aor/registration config from a tenant's
``VoiceProviderConfig`` and push it into Asterisk.

For dev / first-touch deployments the rendered config is dumped to a
filesystem path (``ASTERISK_PJSIP_DROP_DIR``) so a separate process can
``pjsip reload`` it. Production deployments should override
``WRITE_BACKEND`` to use Asterisk's dynamic config endpoint (e.g. ARI's
``asterisk/config/dynamic`` or a custom config DB) — the interface is
intentionally narrow so a follow-up PR can swap implementations
without touching adapter code.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from voice.exceptions import VoiceCredentialError
from voice.sip_config.templates import ProfileSpec, load_profile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderedPjsipConfig:
    """Bundle of PJSIP config sections + the section ids that name them.

    A consumer (the ``WRITE_BACKEND``) is responsible for actually
    applying these to Asterisk — writing to a file, hitting ARI, etc.
    Returned so unit tests can assert on the rendered text without
    needing Asterisk.
    """

    endpoint_id: str
    auth_id: str
    aor_id: str
    reg_id: str
    profile: ProfileSpec
    endpoint: str
    auth: str
    aor: str
    registration: str  # empty for IP-auth profiles

    def to_pjsip_text(self) -> str:
        """Concatenate the rendered sections into a single PJSIP file block."""
        chunks = [self.endpoint, self.auth, self.aor]
        if self.registration:
            chunks.append(self.registration)
        return "\n".join(c.strip() + "\n" for c in chunks if c)


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────


def _section_ids(provider_config) -> tuple[str, str, str, str]:
    """Derive deterministic section ids from the config UUID.

    Same UUID → same ids → re-rendering replaces the existing block
    cleanly, which is what ``ensure_endpoint`` relies on.
    """
    base = f"voice-{provider_config.id}"
    return (
        f"{base}-endpoint",
        f"{base}-auth",
        f"{base}-aor",
        f"{base}-reg",
    )


def render_config(provider_config) -> RenderedPjsipConfig:
    """Render the PJSIP config for a ``VoiceProviderConfig``.

    Reads ``provider_config.credentials`` (decoded JSON) for the
    vendor profile name + per-tenant SIP details, picks the right
    YAML profile, fills in the templates, and returns a
    ``RenderedPjsipConfig``.

    Raises ``VoiceCredentialError`` on a missing vendor profile or
    malformed credentials.
    """
    raw_creds = provider_config.credentials or "{}"
    try:
        creds = json.loads(raw_creds)
    except json.JSONDecodeError as e:
        raise VoiceCredentialError(f"VoiceProviderConfig {provider_config.id} has invalid JSON credentials") from e

    vendor = creds.get("vendor_profile") or "generic"
    profile = load_profile(vendor)
    endpoint_id, auth_id, aor_id, reg_id = _section_ids(provider_config)

    # All template placeholders we know about; the templates use a subset.
    fields = {
        "endpoint_id": endpoint_id,
        "auth_id": auth_id,
        "aor_id": aor_id,
        "reg_id": reg_id,
        "sip_username": creds.get("sip_username", ""),
        "sip_password": creds.get("sip_password", ""),
        "sip_realm": creds.get("sip_realm", ""),
        "sip_proxy": creds.get("sip_proxy", ""),
        "sip_transport": creds.get("sip_transport", "udp"),
    }

    try:
        endpoint = profile.endpoint_template.format(**fields)
        auth = profile.auth_template.format(**fields)
        aor = profile.aor_template.format(**fields)
        registration = (
            profile.registration_template.format(**fields)
            if profile.requires_registration and creds.get("registration_required", True)
            else ""
        )
    except KeyError as e:
        raise VoiceCredentialError(
            f"SIP profile {profile.name!r} references placeholder {e} that is not in the per-tenant credentials"
        ) from e

    return RenderedPjsipConfig(
        endpoint_id=endpoint_id,
        auth_id=auth_id,
        aor_id=aor_id,
        reg_id=reg_id,
        profile=profile,
        endpoint=endpoint,
        auth=auth,
        aor=aor,
        registration=registration,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Write backends
# ─────────────────────────────────────────────────────────────────────────────


def _drop_dir() -> Path:
    """Filesystem destination for rendered PJSIP fragments.

    Set via ``ASTERISK_PJSIP_DROP_DIR`` env var or falls back to a tmp
    path under the project root. The Asterisk container can then mount
    this directory and ``pjsip reload`` to pick up changes.
    """
    raw = getattr(settings, "ASTERISK_PJSIP_DROP_DIR", "") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "asterisk-config",
        "pjsip.d",
    )
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_to_filesystem(rendered: RenderedPjsipConfig) -> None:
    """Default ``WRITE_BACKEND``: drop a ``.conf`` file per provider config.

    Named ``{endpoint_id}.conf`` so re-rendering overwrites cleanly.
    """
    target = _drop_dir() / f"{rendered.endpoint_id}.conf"
    target.write_text(rendered.to_pjsip_text(), encoding="utf-8")
    logger.info("[voice.sip_config.pjsip_writer] wrote %s", target)


WRITE_BACKEND = _write_to_filesystem


def ensure_endpoint(provider_config) -> RenderedPjsipConfig:
    """Render + write the PJSIP config for a SIP provider config.

    Called from a ``post_save`` signal on ``VoiceProviderConfig`` (added
    in a follow-up wiring step). Idempotent: re-rendering with the
    same id overwrites the same target.
    """
    rendered = render_config(provider_config)
    WRITE_BACKEND(rendered)
    return rendered


def remove_endpoint(provider_config) -> None:
    """Delete the PJSIP config fragment for a provider on config removal."""
    endpoint_id, _, _, _ = _section_ids(provider_config)
    path = _drop_dir() / f"{endpoint_id}.conf"
    if path.exists():
        path.unlink()
        logger.info("[voice.sip_config.pjsip_writer] removed %s", path)
