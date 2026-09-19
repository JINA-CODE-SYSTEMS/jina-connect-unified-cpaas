"""
Shared placeholder rendering — single source of truth.

Replaces 3 duplicate implementations in broadcast/tasks.py:
- _render_template_field()
- _convert_template_buttons_to_inbox_format() (inline lambda)
- _convert_template_cards_to_inbox_format() (inline lambda)
"""

from __future__ import annotations

import re
from typing import Dict

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_placeholders(text: str, data: Dict[str, str]) -> str:
    """
    Replace {{ key }} placeholders in text with values from data dict.

    Substitution only — this deliberately knows nothing about what a
    placeholder *ought* to resolve to. It used to be asked that question for
    broadcast template bubbles, answered it differently from the send, and left
    a positional ``{{1}}`` showing in the inbox on a message the customer had
    received with their name in it (#389). Callers on that path now pass in the
    values the send resolved (``BroadcastMessage.sent_placeholder_values``)
    rather than expecting this function to work them out.

    Args:
        text: Template string with {{ placeholder }} markers.
        data: What each placeholder resolves to.

    Returns:
        Rendered string. A placeholder with no entry in *data* is left as-is,
        which is the honest answer here: nothing told this function what it
        should say.
    """
    if not text:
        return text or ""

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        value = data.get(key, match.group(0))
        return str(value) if value else ""

    return _PLACEHOLDER_PATTERN.sub(_replace, text)
