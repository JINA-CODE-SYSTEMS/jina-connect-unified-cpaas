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

    Args:
        text: Template string with {{ placeholder }} markers.
        data: Merged dict of placeholder_data + reserved_vars.

    Returns:
        Rendered string. Unmatched placeholders are left as-is.
    """
    if not text:
        return text or ""

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        value = data.get(key, match.group(0))
        return str(value) if value else ""

    return _PLACEHOLDER_PATTERN.sub(_replace, text)
