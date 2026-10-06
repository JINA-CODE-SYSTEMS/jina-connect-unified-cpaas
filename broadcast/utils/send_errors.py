"""
One readable line for why a broadcast message failed.

``BroadcastMessage.response`` holds the failure in whatever shape it arrived:

- the provider's error text from a rejected send (a plain string);
- the ``errors`` array from a ``failed`` status webhook, as JSON;
- that same array as a Python repr, which is how the webhook stored it
  until it was changed to write JSON. Those rows are still in the table.

The broadcast detail page has an Error column that reads ``error_message``,
and nothing produced it, so every failure showed "-" — including the ones
where Meta had said exactly what went wrong.
"""

import ast
import json


def _parse(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return raw


def _describe(error):
    if not isinstance(error, dict):
        return str(error)
    # Graph API error envelope: {"error": {"message": ..., "code": ...}}
    if isinstance(error.get("error"), dict):
        error = error["error"]

    title = error.get("title") or error.get("message") or "Delivery failed"
    code = error.get("code")
    error_data = error.get("error_data")
    details = (error_data.get("details") if isinstance(error_data, dict) else None) or error.get("error_user_msg")
    if not details and error.get("message") and error.get("message") != title:
        details = error["message"]

    text = f"{title} ({code})" if code else title
    return f"{text}: {details}" if details else text


def describe_send_error(raw):
    """Return a short human-readable error for a stored failure, or None."""
    if not raw or not str(raw).strip():
        return None
    data = _parse(raw)
    if isinstance(data, list):
        parts = [_describe(e) for e in data if e]
        return "; ".join(parts) or None
    if isinstance(data, dict):
        return _describe(data)
    return str(data).strip()
