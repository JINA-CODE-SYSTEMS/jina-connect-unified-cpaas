"""Per-provider credential schema validation.

``VoiceProviderConfig.credentials`` is an encrypted JSON-serialised dict.
The shape depends on the ``provider``:

  * sip — SIP trunk auth (username/password/realm/proxy/codecs/vendor profile)
  * twilio — account_sid + auth_token + region + edge
  * plivo — auth_id + auth_token
  * vonage — JWT app (api_key/secret + application_id + private_key_pem)
  * telnyx — api_key + connection_id + outbound_voice_profile_id
  * exotel — sid + api_key + api_token + subdomain

``validate_credentials(provider, payload)`` parses the payload through the
right schema, raising ``VoiceCredentialError`` on mismatch. It's called
from ``VoiceProviderConfig.clean()`` so admin / API saves fail loudly
on bad config.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from voice.constants import VoiceProvider
from voice.exceptions import VoiceCredentialError

# ─────────────────────────────────────────────────────────────────────────────
# Per-provider schemas
# ─────────────────────────────────────────────────────────────────────────────


class SipCredentials(BaseModel):
    sip_username: str
    sip_password: str
    sip_realm: str
    sip_proxy: str
    sip_transport: str = "udp"
    registration_required: bool = True
    codecs: list[str] = Field(default_factory=list)
    dtmf_mode: str = "rfc2833"
    vendor_profile: str = "generic"


class TwilioCredentials(BaseModel):
    account_sid: str
    auth_token: str
    region: str | None = None
    edge: str | None = None


class PlivoCredentials(BaseModel):
    auth_id: str
    auth_token: str


class VonageCredentials(BaseModel):
    api_key: str
    api_secret: str
    application_id: str
    private_key_pem: str


class TelnyxCredentials(BaseModel):
    api_key: str
    connection_id: str
    outbound_voice_profile_id: str | None = None


class ExotelCredentials(BaseModel):
    sid: str
    api_key: str
    api_token: str
    subdomain: str = "api.exotel.com"


_SCHEMA_BY_PROVIDER: dict[str, type[BaseModel]] = {
    VoiceProvider.SIP.value: SipCredentials,
    VoiceProvider.TWILIO.value: TwilioCredentials,
    VoiceProvider.PLIVO.value: PlivoCredentials,
    VoiceProvider.VONAGE.value: VonageCredentials,
    VoiceProvider.TELNYX.value: TelnyxCredentials,
    VoiceProvider.EXOTEL.value: ExotelCredentials,
}


def validate_credentials(provider: str, payload: str | dict[str, Any] | None) -> dict[str, Any]:
    """Validate ``payload`` against the schema for ``provider``.

    ``payload`` may be a dict, a JSON string (as stored in the encrypted
    field), or ``None`` (treated as empty — useful before the user has
    filled credentials in).

    Returns the validated dict (with Pydantic defaults applied) so the
    caller can re-serialise it on save. Raises ``VoiceCredentialError``
    on any validation failure.
    """
    if payload is None:
        payload = {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise VoiceCredentialError(f"{provider} credentials are not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise VoiceCredentialError(f"{provider} credentials must be a dict, got {type(payload).__name__}.")

    schema = _SCHEMA_BY_PROVIDER.get(provider)
    if schema is None:
        raise VoiceCredentialError(f"Unknown voice provider {provider!r}.")

    try:
        return schema(**payload).model_dump()
    except ValidationError as e:
        raise VoiceCredentialError(f"Invalid {provider} credentials: {e}") from e
