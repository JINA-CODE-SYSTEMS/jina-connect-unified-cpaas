"""
Inline keyboard helpers for Telegram Bot API.

Builds InlineKeyboardMarkup objects and parses callback_data strings.
"""

from __future__ import annotations

import re
from typing import Optional

from telegram.constants import CALLBACK_DATA_MAX_LENGTH, CALLBACK_DATA_VERSION

_CALLBACK_RE = re.compile(r"^(?P<version>v\d+):(?P<action>\w+):(?P<id>[^:]+):(?P<nonce>[^:]+)$")


def _truncate_callback_data(data: str) -> str:
    """Trim callback_data to Telegram's byte limit without splitting UTF-8 codepoints."""
    encoded = data.encode("utf-8")
    if len(encoded) <= CALLBACK_DATA_MAX_LENGTH:
        return data

    ellipsis = "..."
    max_prefix_bytes = CALLBACK_DATA_MAX_LENGTH - len(ellipsis.encode("utf-8"))
    truncated = encoded[:max_prefix_bytes].decode("utf-8", errors="ignore")
    return f"{truncated}{ellipsis}"


def build_template_button_keyboard(buttons: list[dict]) -> Optional[dict]:
    """Convert flat channel-template button specs into Telegram inline keyboard markup."""
    rows = []
    for button in buttons:
        text = (button.get("text") or "").strip()
        if not text:
            continue

        button_type = (button.get("type") or "").upper()
        normalized = {"text": text}

        if button_type == "URL" and button.get("url"):
            normalized["url"] = button["url"]
        elif button_type == "PHONE_NUMBER" and button.get("phone_number"):
            normalized["url"] = f"tel:{button['phone_number']}"
        elif button_type in ("QUICK_REPLY", "COPY_CODE", "OTP"):
            normalized["callback_data"] = _truncate_callback_data(f"{button_type.lower()}:{text}")
        else:
            normalized["callback_data"] = _truncate_callback_data(f"action:{text}")

        rows.append([normalized])

    if not rows:
        return None

    return build_inline_keyboard(rows)


def build_inline_keyboard(buttons: list[list[dict]]) -> dict:
    """
    Build a Telegram InlineKeyboardMarkup from a nested button spec.

    Args:
        buttons: Rows of button dicts. Each button must have ``text`` and at
                 least one of ``callback_data`` or ``url``.

    Returns:
        ``{"inline_keyboard": [[{"text": "...", "callback_data": "..."}]]}``

    Raises:
        ValueError: If any callback_data exceeds 64 bytes.
    """
    rows = []
    for row in buttons:
        row_buttons = []
        for btn in row:
            tg_btn = {"text": btn["text"]}
            if "callback_data" in btn:
                cb = btn["callback_data"]
                if len(cb.encode("utf-8")) > CALLBACK_DATA_MAX_LENGTH:
                    raise ValueError(f"callback_data exceeds {CALLBACK_DATA_MAX_LENGTH} bytes: {cb!r}")
                tg_btn["callback_data"] = cb
            elif "url" in btn:
                tg_btn["url"] = btn["url"]
            row_buttons.append(tg_btn)
        rows.append(row_buttons)
    return {"inline_keyboard": rows}


def build_callback_data(action: str, node_id: str, nonce: str) -> str:
    """
    Build a versioned callback_data string.

    Format: ``v1:<action>:<id>:<nonce>``
    """
    data = f"{CALLBACK_DATA_VERSION}:{action}:{node_id}:{nonce}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_MAX_LENGTH:
        raise ValueError(f"callback_data exceeds {CALLBACK_DATA_MAX_LENGTH} bytes: {data!r}")
    return data


def parse_callback_data(data: str) -> Optional[dict]:
    """
    Parse a versioned callback_data string.

    Returns:
        Dict with keys ``version``, ``action``, ``id``, ``nonce`` — or None if
        the data does not match the expected format.
    """
    match = _CALLBACK_RE.match(data)
    if not match:
        return None
    return {
        "version": match.group("version"),
        "action": match.group("action"),
        "id": match.group("id"),
        "nonce": match.group("nonce"),
    }
