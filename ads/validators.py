"""Pre-publish validators for CTWA campaign assets (#196).

Runs before any Marketing API publish call. Catches Meta-policy
violations on the prefilled message + creative spec at flow time so
tenants don't waste an API round-trip + an ad disapproval.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

# Prefilled-message length cap is the Meta-published WhatsApp deep-link
# limit. Keep in a constant so we can bump without code change.
PREFILLED_MESSAGE_MAX_CHARS = 1024

# Versioned denylist of phrases / industry-specific patterns Meta has
# historically rejected on review. Lives here (not in DB) so legal can
# update via PR. Add to this list rather than editing — review history.
DENYLIST_PATTERNS: list[tuple[str, str]] = [
    # (pattern, human-readable explanation)
    (r"\b(guaranteed|100%\s*guaranteed)\b", "Avoid absolute guarantee language."),
    (r"\b(miracle|cure|treatment for [A-Za-z]+)\b", "Medical-claim language is restricted."),
    (r"\b(get rich quick|easy money|no risk)\b", "Financial-claim language is restricted."),
]


def validate_prefilled_message(text: str) -> None:
    """Validate the prefilled message a tenant proposes to ship on a
    CTWA campaign. Raises ``ValidationError`` with per-rule context.
    """
    if not isinstance(text, str):
        raise ValidationError({"prefilled_message": "Must be a string."})

    stripped = text.strip()
    if not stripped:
        raise ValidationError({"prefilled_message": "Must not be empty."})
    if len(text) > PREFILLED_MESSAGE_MAX_CHARS:
        raise ValidationError(
            {"prefilled_message": f"Must be ≤ {PREFILLED_MESSAGE_MAX_CHARS} chars (got {len(text)})."}
        )

    violations: list[str] = []
    lowered = text.lower()
    for pattern, explanation in DENYLIST_PATTERNS:
        if re.search(pattern, lowered):
            violations.append(explanation)
    if violations:
        raise ValidationError({"prefilled_message": violations})


def validate_creative_dimensions(width: int, height: int, media_type: str) -> None:
    """Catch obviously-wrong aspect ratios before upload to Meta."""
    if not width or not height:
        return
    ratio = width / height if height else 0
    # Meta accepts a broad range; flag only egregiously off-spec ones.
    if media_type == "image" and not (0.5 <= ratio <= 2.0):
        raise ValidationError({"creative": f"Image aspect ratio {ratio:.2f} outside the [0.5, 2.0] accepted range."})


__all__ = [
    "DENYLIST_PATTERNS",
    "PREFILLED_MESSAGE_MAX_CHARS",
    "validate_creative_dimensions",
    "validate_prefilled_message",
]
