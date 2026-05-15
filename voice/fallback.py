"""Cross-channel SMS fallback (#172).

When a voice call ends in a failure state (NO_ANSWER, USER_BUSY,
CALL_REJECTED, …) and the provider config has ``fallback_sms_enabled``
set, the call-completion signal routes here. We render the configured
template against the call context and dispatch via the SMS app the
tenant has wired up.

``Broadcast.fallback_sms_enabled`` (when not ``None``) overrides the
config-level default so a single campaign can opt out (or in)
independently of the provider config.

Idempotency: ``VoiceCall.metadata["sms_fallback_sent"]`` is set on
first dispatch; re-fired signals see it and no-op. SMS dispatch
failures are logged but never raise — voice processing must complete
even if the SMS provider is down.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Default hangup causes that trigger the fallback when the config
# leaves ``fallback_on_causes`` empty. Mirrors the "user didn't pick up
# or the network refused" set; busy/no-answer/rejected are the cases
# where the recipient is reachable but didn't engage.
DEFAULT_FALLBACK_CAUSES = ("NO_ANSWER", "USER_BUSY", "CALL_REJECTED")


# ``{{ var }}`` and ``{{var}}`` — tolerant whitespace.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_template(template: str, context: dict[str, Any]) -> str:
    """Substitute ``{{var}}`` placeholders.

    Missing variables resolve to an empty string rather than blowing
    up — the fallback path must be lossy-but-deliverable, not fragile.
    """
    if not template:
        return ""

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        value = context.get(key, "")
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_sub, template)


def _build_context(call) -> dict[str, Any]:
    """Pull the substitution context out of a ``VoiceCall``."""
    contact = getattr(call, "contact", None)
    first_name = getattr(contact, "first_name", "") if contact else ""
    last_name = getattr(contact, "last_name", "") if contact else ""
    return {
        "first_name": first_name,
        "last_name": last_name,
        "from_number": call.from_number or "",
        "to_number": call.to_number or "",
        "hangup_cause": call.hangup_cause or "",
        "call_id": str(call.id),
        "tenant_id": str(call.tenant_id),
    }


def _resolve_enabled(call) -> bool:
    """Per-broadcast override beats the per-config default.

    Returns the effective ``enabled`` flag for ``call``. A
    ``broadcast.fallback_sms_enabled`` of ``True`` or ``False`` wins;
    ``None`` (the default) falls through to the provider config.
    """
    config_enabled = bool(getattr(call.provider_config, "fallback_sms_enabled", False))
    broadcast = getattr(call, "broadcast", None)
    if broadcast is not None:
        broadcast_override = getattr(broadcast, "fallback_sms_enabled", None)
        if broadcast_override is not None:
            return bool(broadcast_override)
    return config_enabled


def _is_failure(call) -> bool:
    """A call counts as a failure if it terminated without connecting.

    COMPLETED with duration > 0 → success (no fallback).
    COMPLETED with duration 0 → recipient never engaged → fallback.
    FAILED / CANCELED / any non-COMPLETED → failure.
    """
    if call.status == "COMPLETED":
        return (call.duration_seconds or 0) == 0
    return True


def _cause_matches(call, causes: list[str]) -> bool:
    """An empty cause list disables the filter (fall back on any failure)."""
    if not causes:
        return True
    if not call.hangup_cause:
        # No cause reported — only count it as matching when the filter
        # is permissive enough to include the special "" sentinel that
        # operators sometimes use.
        return "" in causes
    return call.hangup_cause in causes


def maybe_send_sms_fallback(call) -> dict[str, Any]:
    """Dispatch an SMS if this call qualifies for fallback.

    Returns a small status dict for the signal layer to log /
    inspect — primarily for tests. The dict is also stamped onto
    ``call.metadata["sms_fallback"]`` so admin / audit can see what
    happened.

    All exceptions are logged and swallowed: the post-call signal
    must not crash on an SMS provider hiccup.
    """
    result: dict[str, Any] = {"attempted": False, "skipped_reason": None}

    if (call.metadata or {}).get("sms_fallback_sent"):
        result["skipped_reason"] = "already_sent"
        return result

    if not _is_failure(call):
        result["skipped_reason"] = "call_succeeded"
        return result

    if not _resolve_enabled(call):
        result["skipped_reason"] = "fallback_disabled"
        return result

    config = call.provider_config
    sms_app = getattr(config, "fallback_sms_config", None)
    if sms_app is None:
        result["skipped_reason"] = "no_sms_app"
        return result

    causes = config.fallback_on_causes or list(DEFAULT_FALLBACK_CAUSES)
    if not _cause_matches(call, causes):
        result["skipped_reason"] = "cause_filtered"
        return result

    template = config.fallback_sms_template or ""
    body = render_template(template, _build_context(call))
    if not body.strip():
        result["skipped_reason"] = "empty_body"
        return result

    result["attempted"] = True

    # Late imports — the SMS sender pulls in provider credentials, so
    # we want to be sure the call qualifies before touching it.
    try:
        from sms.services.message_sender import SMSMessageSender

        sender = SMSMessageSender(sms_app)
        send_result = sender.send_text(call.to_number, body)
    except Exception as exc:  # noqa: BLE001 — never crash voice processing
        logger.exception(
            "[voice.fallback] SMS dispatch raised for call %s; swallowing",
            call.id,
        )
        send_result = {"success": False, "error": str(exc), "message_id": ""}

    result["success"] = bool(send_result.get("success"))
    result["sms_message_id"] = send_result.get("message_id", "")
    result["error"] = send_result.get("error", "")

    # Stamp the outcome on the call so this signal is idempotent and
    # admin views can see the fallback record.
    new_metadata = {
        **(call.metadata or {}),
        "sms_fallback_sent": True,
        "sms_fallback": result,
    }
    call.metadata = new_metadata
    call.save(update_fields=["metadata", "updated_at"])

    return result
