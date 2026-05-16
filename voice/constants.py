"""Voice channel constants — canonical states, hangup causes, and provider ids.

These live separately from ``models.py`` so adapters, dialects, and tests
can import them without pulling in the full ORM. Each ``TextChoices`` is
the source of truth for the corresponding ``CharField(choices=...)``.
"""

from __future__ import annotations

from django.db import models

# ─────────────────────────────────────────────────────────────────────────────
# Provider / protocol family identifier
# ─────────────────────────────────────────────────────────────────────────────


class VoiceProvider(models.TextChoices):
    """Protocol/API family — *not* the vendor name.

    Every SIP trunk vendor (Dialogic, Telnyx Elastic SIP, Twilio Elastic SIP,
    Plivo SIP, Vonage SIP, Exotel SIP, Knowlarity, Servetel, MyOperator,
    Tata Tele, Airtel, ...) maps to ``SIP``. HTTP voice APIs each get their
    own entry.
    """

    SIP = "sip", "SIP (any trunk)"
    TWILIO = "twilio", "Twilio Voice"
    PLIVO = "plivo", "Plivo Voice"
    VONAGE = "vonage", "Vonage Voice"
    TELNYX = "telnyx", "Telnyx Call Control"
    EXOTEL = "exotel", "Exotel Voice"


# ─────────────────────────────────────────────────────────────────────────────
# Call lifecycle state machine
# ─────────────────────────────────────────────────────────────────────────────


class CallStatus(models.TextChoices):
    """Canonical call state. Terminal states (``COMPLETED``, ``FAILED``,
    ``CANCELED``) freeze the row — only ``VoiceCallEvent`` rows append after."""

    QUEUED = "QUEUED", "Queued"
    INITIATING = "INITIATING", "Initiating"
    RINGING = "RINGING", "Ringing"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELED = "CANCELED", "Canceled"


TERMINAL_STATUSES = frozenset({CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELED})


# ─────────────────────────────────────────────────────────────────────────────
# Hangup causes — adapters normalise provider-native causes onto these
# ─────────────────────────────────────────────────────────────────────────────


class HangupCause(models.TextChoices):
    """Q.850-aligned canonical causes. Each adapter maps its provider-native
    cause (SIP response, Twilio status, Q.850 code, vendor string) onto one
    of these in ``parse_webhook``."""

    NORMAL_CLEARING = "NORMAL_CLEARING", "Normal clearing"
    USER_BUSY = "USER_BUSY", "User busy"
    NO_ANSWER = "NO_ANSWER", "No answer"
    NO_USER_RESPONSE = "NO_USER_RESPONSE", "No user response"
    CALL_REJECTED = "CALL_REJECTED", "Call rejected"
    NUMBER_UNALLOCATED = "NUMBER_UNALLOCATED", "Number unallocated"
    NETWORK_OUT_OF_ORDER = "NETWORK_OUT_OF_ORDER", "Network out of order"
    NORMAL_TEMPORARY_FAILURE = "NORMAL_TEMPORARY_FAILURE", "Temporary failure"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE", "Resource unavailable"
    FACILITY_REJECTED = "FACILITY_REJECTED", "Facility rejected"
    DESTINATION_OUT_OF_ORDER = "DESTINATION_OUT_OF_ORDER", "Destination out of order"
    INVALID_NUMBER_FORMAT = "INVALID_NUMBER_FORMAT", "Invalid number format"
    INTERWORKING = "INTERWORKING", "Interworking error"
    UNKNOWN = "UNKNOWN", "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Other enums
# ─────────────────────────────────────────────────────────────────────────────


class CallDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class CostSource(models.TextChoices):
    """Where the cost amount came from — for audit."""

    PROVIDER = "provider", "Provider cost callback"
    LOCAL_RATECARD = "local_ratecard", "Local rate-card lookup"


class CallEventType(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    RINGING = "ringing", "Ringing"
    ANSWERED = "answered", "Answered"
    DTMF = "dtmf", "DTMF received"
    SPEECH = "speech", "Speech received"
    RECORDING_STARTED = "recording_started", "Recording started"
    RECORDING_COMPLETED = "recording_completed", "Recording completed"
    TRANSFERRED = "transferred", "Transferred"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class TemplateKind(models.TextChoices):
    TTS_SCRIPT = "tts_script", "TTS script"
    AUDIO_URL = "audio_url", "Pre-recorded audio URL"
    IVR_MENU = "ivr_menu", "IVR menu"


class AudioFormat(models.TextChoices):
    MP3 = "mp3", "MP3"
    WAV = "wav", "WAV"
