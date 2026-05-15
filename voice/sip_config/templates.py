"""PJSIP vendor profile loader (#163).

Profiles live in ``voice/sip_config/profiles/*.yaml``. Each file defines
a vendor-specific PJSIP endpoint/auth/aor/registration template with
``{placeholders}`` that get filled in from the per-tenant
``VoiceProviderConfig.credentials`` at provisioning time.

Adding a new vendor = drop in a YAML file. No Python required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from voice.exceptions import VoiceCredentialError

_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

# Cache so repeated lookups don't re-read disk. ``reload_profile`` clears
# a single entry; tests use ``_PROFILE_CACHE.clear()`` directly.
_PROFILE_CACHE: dict[str, "ProfileSpec"] = {}


@dataclass(frozen=True)
class ProfileSpec:
    """One vendor profile loaded from YAML.

    Each ``*_template`` is a string with ``{placeholder}`` slots that the
    PJSIP writer fills in (``sip_username`` / ``sip_password`` /
    ``sip_realm`` / ``sip_proxy`` plus the writer-supplied ``endpoint_id``
    / ``auth_id`` / ``aor_id`` / ``reg_id``). An empty
    ``registration_template`` means the vendor uses IP-auth and we
    should not emit a registration block.
    """

    name: str
    display_name: str
    notes: str
    endpoint_template: str
    auth_template: str
    aor_template: str
    registration_template: str

    @property
    def requires_registration(self) -> bool:
        return bool(self.registration_template and self.registration_template.strip())


_REQUIRED_KEYS = (
    "name",
    "display_name",
    "endpoint_template",
    "auth_template",
    "aor_template",
)


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _validate(name: str, payload: dict) -> None:
    missing = [k for k in _REQUIRED_KEYS if k not in payload]
    if missing:
        raise VoiceCredentialError(f"SIP profile {name!r} is missing required keys: {missing}")


def load_profile(name: str) -> ProfileSpec:
    """Return the parsed ProfileSpec for ``name``.

    Raises ``VoiceCredentialError`` if the YAML is missing or malformed.
    Cached after the first call.
    """
    cached = _PROFILE_CACHE.get(name)
    if cached is not None:
        return cached

    path = _PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise VoiceCredentialError(
            f"Unknown SIP vendor profile {name!r}. Profiles available: {sorted(list_profiles())}"
        )

    payload = _load_yaml(path)
    _validate(name, payload)

    spec = ProfileSpec(
        name=payload["name"],
        display_name=payload["display_name"],
        notes=payload.get("notes", ""),
        endpoint_template=payload["endpoint_template"],
        auth_template=payload["auth_template"],
        aor_template=payload["aor_template"],
        registration_template=payload.get("registration_template", "") or "",
    )
    _PROFILE_CACHE[name] = spec
    return spec


def list_profiles() -> list[str]:
    """Return the names of every shipped profile (sorted)."""
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))


def reload_profile(name: str) -> ProfileSpec:
    """Drop the cached entry for ``name`` and reload from disk.

    Useful in tests + after editing a profile in a long-running process.
    """
    _PROFILE_CACHE.pop(name, None)
    return load_profile(name)
