"""Marketing opt-out keyword handling, shared by every inbound channel (#276).

The platform had no opt-out path at all: no keyword handler, no per-contact
flag, no suppression before dispatch. That is a WhatsApp Business Policy
breach, but the way it bites is deliverability — a recipient who cannot
unsubscribe blocks and reports instead, and Meta answers a falling quality
rating by lowering the number's messaging tier.

Two decisions here are worth knowing about:

*Whole-message matching.* The normalised message must **be** the keyword.
Substring matching would read "please don't stop sending me these" as a STOP,
and a wrongly suppressed contact is silent — nobody finds out until they ask
why the messages stopped.

*Every configured locale is matched, not just one.* One WhatsApp number serves
whichever language the recipient writes in, and nothing in an inbound payload
says which that is. Resolving a single locale first would mean a recipient who
replies in the wrong one is not unsubscribed, and the two errors are not
priced the same: a missed opt-out costs quality rating and eventually the
number, an over-eager one costs a contact who can re-subscribe with START.
"""

from __future__ import annotations

import logging
import unicodedata

from django.conf import settings

from contacts.models import MarketingOptOutSource

logger = logging.getLogger(__name__)

OPT_OUT = "OPT_OUT"
OPT_IN = "OPT_IN"


def _normalise(text: str | None) -> str:
    """Fold a message (or a configured keyword) to its comparable form.

    NFKC first, so a word typed with full-width or decomposed characters
    matches the plain one, then ``casefold`` rather than ``lower`` because
    casefold is the mapping defined for scripts outside Latin.

    Punctuation and symbols become spaces so "STOP." and "STOP 🛑" still
    count. They are found by Unicode category rather than by stripping
    everything outside ``[a-z0-9]`` — that shortcut erases a non-Latin keyword
    completely, which is the English-only assumption this module exists to
    avoid.
    """
    if not text:
        return ""

    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    stripped = "".join(" " if unicodedata.category(ch).startswith(("P", "S")) else ch for ch in folded)
    return " ".join(stripped.split())


def _keyword_set(configured: dict | None) -> frozenset[str]:
    """Flatten a ``{locale: [keyword, ...]}`` setting into what we compare against.

    Recomputed per call rather than cached at import: it is a handful of short
    strings, and caching would make ``override_settings`` in a test — or a
    deployment reloading configuration — silently ineffective.
    """
    words = set()
    for keywords in (configured or {}).values():
        for keyword in keywords or ():
            normalised = _normalise(keyword)
            if normalised:
                words.add(normalised)
    return frozenset(words)


def classify_inbound_keyword(text: str | None) -> str | None:
    """``OPT_OUT``, ``OPT_IN``, or ``None`` when the message is ordinary traffic."""
    normalised = _normalise(text)
    if not normalised:
        return None

    # Opt-out is checked first so a word configured into both lists suppresses
    # rather than resubscribes. Fail towards the quieter outcome.
    if normalised in _keyword_set(getattr(settings, "MARKETING_OPT_OUT_KEYWORDS", None)):
        return OPT_OUT
    if normalised in _keyword_set(getattr(settings, "MARKETING_OPT_IN_KEYWORDS", None)):
        return OPT_IN
    return None


def apply_inbound_keyword(contact, text: str | None) -> str | None:
    """Apply an inbound opt-out/opt-in keyword to *contact*, if the message is one.

    Returns the verdict so the caller can log or branch; ``None`` means nothing
    matched and the message is ordinary inbound traffic.
    """
    verdict = classify_inbound_keyword(text)
    if verdict is None:
        return None

    changed = contact.set_marketing_opt_out(
        opted_out=verdict == OPT_OUT,
        source=MarketingOptOutSource.KEYWORD,
    )
    if changed:
        logger.info(
            "[opt_out] Contact %s (tenant %s) %s marketing by keyword",
            contact.pk,
            contact.tenant_id,
            "opted out of" if verdict == OPT_OUT else "opted back into",
        )
    return verdict
