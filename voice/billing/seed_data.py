"""Default voice rate-card seed data (#170).

Per-destination per-minute rates used by ``seed_voice_rate_cards`` to
populate a new ``VoiceProviderConfig`` so a tenant can start placing
calls without hand-entering a rate card.

Defaults are conservative wholesale-ish numbers in USD per minute,
billed at 60-second increments. Tenants should override with their
real contracted rates — this is "rather have a number than no number"
material so the local-billing path doesn't silently skip calls.
"""

from __future__ import annotations

from decimal import Decimal

# Each row: (destination_prefix, rate_per_minute_usd, billing_increment_seconds)
DEFAULT_RATE_CARDS: list[tuple[str, Decimal, int]] = [
    # India
    ("+91", Decimal("0.0150"), 60),
    # United States / Canada
    ("+1", Decimal("0.0100"), 60),
    # United Kingdom
    ("+44", Decimal("0.0180"), 60),
    # Catch-all (any +) — lowest-priority match because shorter prefix.
    ("+", Decimal("0.0500"), 60),
]
