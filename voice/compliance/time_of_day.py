"""Time-of-day calling rules (#171).

Voice broadcasts can carry an ``allowed_hours_local`` window — a
TCPA-style "don't dial outside 9-9 local time" gate. The dispatcher
calls into ``is_within_allowed_hours`` before placing the call; if the
current local time is outside the window, the message is rescheduled
for ``next_allowed_time`` instead of being silently dropped.

The window shape is ``{"start": "HH:MM", "end": "HH:MM"}`` (24-hour
local time). Wrap-around windows (e.g. ``22:00`` → ``06:00``) are
supported and treated as "in window iff time >= start OR time < end".

Timezones come in two forms:

  * A ``zoneinfo`` key string (``"Asia/Kolkata"``).
  * A ``ZoneInfo`` instance, already resolved.

Bad / missing windows return ``True`` from
``is_within_allowed_hours`` — the absence of a gate means no gate.
``next_allowed_time`` returns ``now`` in that case.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _parse_window(allowed_hours: Any) -> tuple[time, time] | None:
    """Pull ``(start_time, end_time)`` out of the JSON window dict.

    Returns ``None`` for missing / malformed input — callers treat that
    as "no gate, dial freely".
    """
    if not allowed_hours:
        return None
    if not isinstance(allowed_hours, dict):
        return None
    start_raw = allowed_hours.get("start")
    end_raw = allowed_hours.get("end")
    if not start_raw or not end_raw:
        return None
    try:
        start = time.fromisoformat(str(start_raw))
        end = time.fromisoformat(str(end_raw))
    except (TypeError, ValueError):
        return None
    return start, end


def resolve_recipient_timezone(to_number: str) -> ZoneInfo:
    """Best-effort recipient timezone from an E.164 number.

    Uses ``phonenumbers.timezone.time_zones_for_number`` — when the
    library returns the catch-all ``Etc/Unknown`` (or anything we can't
    resolve) we fall back to UTC so dispatch still works.
    """
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_timezone
    except ImportError:
        return ZoneInfo("UTC")

    try:
        parsed = phonenumbers.parse(to_number, None)
        zones = pn_timezone.time_zones_for_number(parsed)
    except phonenumbers.NumberParseException:
        return ZoneInfo("UTC")

    for zone in zones or ():
        if zone and zone != "Etc/Unknown":
            try:
                return ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def _resolve_tz(tz: Any) -> ZoneInfo:
    """Coerce ``tz`` to a ``ZoneInfo``. Unknown zones fall back to UTC."""
    if isinstance(tz, ZoneInfo):
        return tz
    if tz in (None, "", b""):
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(str(tz))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_within_allowed_hours(
    allowed_hours: Any,
    recipient_timezone: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Return ``True`` if the recipient's local time is inside the window.

    Wrap-around windows (start > end) are treated as the union of
    ``[start, 24:00)`` and ``[00:00, end)`` — so a 22:00-06:00 window
    accepts 23:00 and 02:00 alike.
    """
    window = _parse_window(allowed_hours)
    if window is None:
        return True
    start, end = window

    tz = _resolve_tz(recipient_timezone)
    local_now = (now or datetime.now(tz=tz)).astimezone(tz)
    current = local_now.time()

    if start == end:
        # Zero-length window — no time matches.
        return False
    if start < end:
        return start <= current < end
    # Wrap-around: e.g. 22:00 - 06:00.
    return current >= start or current < end


def next_allowed_time(
    allowed_hours: Any,
    recipient_timezone: Any,
    *,
    now: datetime | None = None,
) -> datetime:
    """Return the next ``datetime`` (tz-aware) we can dial at.

    If ``now`` is already inside the window we return it unchanged.
    Otherwise we return today's ``start`` if it's still in the future,
    else tomorrow's ``start``. Wrap-around windows that include "now"
    are handled by short-circuiting via ``is_within_allowed_hours``.

    A missing / malformed window means "no gate" — we return ``now``.
    """
    tz = _resolve_tz(recipient_timezone)
    local_now = (now or datetime.now(tz=tz)).astimezone(tz)

    if is_within_allowed_hours(allowed_hours, tz, now=local_now):
        return local_now

    window = _parse_window(allowed_hours)
    if window is None:
        return local_now
    start, _ = window

    candidate = local_now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return candidate
