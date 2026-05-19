"""CTWA referral dataclass (#192).

Normalised cross-BSP shape returned by ``BaseBSPAdapter.parse_referral``.
Each adapter parses its provider's webhook payload into this dataclass;
the inbound-message persistence layer (``wa/tasks.py``) reads the
fields and stamps them onto the ``WAMessage`` row. ``ctwa/ingestion.py``
later picks them up to create the ``CtwaLead``.

Missing-field policy: every field except ``source_id`` is optional.
Adapters MUST NOT raise on missing data — return what's available, let
attribution downgrade the match quality on the downstream side.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CtwaReferral:
    """Canonical CTWA referral payload."""

    source_type: str           # "ad" | "post" | "unknown"
    source_id: str             # Meta ad ID — required
    source_url: str = ""
    headline: str = ""
    body: str = ""
    media_type: str = ""       # "image" | "video" | ""
    media_url: str = ""
    thumbnail_url: str = ""
    ctwa_clid: str = ""        # critical for CAPI match quality; may be ""
