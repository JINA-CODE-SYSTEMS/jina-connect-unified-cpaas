"""Voice channel URL routes.

Webhook endpoints for each HTTP voice provider live here. The
top-level URL config mounts this module under ``/voice/v1/``.

For #160 only Twilio is wired up. Plivo / Vonage / Telnyx / Exotel
arrive with PRs #164–#167.
"""

from __future__ import annotations

from django.urls import path

from voice.webhooks.plivo import (
    PlivoAnswerHandler,
    PlivoCallStatusHandler,
    PlivoRecordingHandler,
)
from voice.webhooks.telnyx import TelnyxEventHandler
from voice.webhooks.twilio import (
    TwilioAnswerHandler,
    TwilioCallStatusHandler,
    TwilioGatherHandler,
    TwilioRecordingStatusHandler,
)
from voice.webhooks.vonage import (
    VonageAnswerHandler,
    VonageEventHandler,
)

app_name = "voice"

urlpatterns = [
    # ── Twilio ─────────────────────────────────────────────────────────
    path(
        "webhooks/twilio/<uuid:config_uuid>/call-status/",
        TwilioCallStatusHandler.as_view(),
        name="twilio-call-status",
    ),
    path(
        "webhooks/twilio/<uuid:config_uuid>/answer/",
        TwilioAnswerHandler.as_view(),
        name="twilio-answer",
    ),
    path(
        "webhooks/twilio/<uuid:config_uuid>/gather/",
        TwilioGatherHandler.as_view(),
        name="twilio-gather",
    ),
    path(
        "webhooks/twilio/<uuid:config_uuid>/recording-status/",
        TwilioRecordingStatusHandler.as_view(),
        name="twilio-recording-status",
    ),
    # ── Plivo ──────────────────────────────────────────────────────────
    path(
        "webhooks/plivo/<uuid:config_uuid>/call-status/",
        PlivoCallStatusHandler.as_view(),
        name="plivo-call-status",
    ),
    path(
        "webhooks/plivo/<uuid:config_uuid>/answer/",
        PlivoAnswerHandler.as_view(),
        name="plivo-answer",
    ),
    path(
        "webhooks/plivo/<uuid:config_uuid>/recording/",
        PlivoRecordingHandler.as_view(),
        name="plivo-recording",
    ),
    # ── Vonage ─────────────────────────────────────────────────────────
    path(
        "webhooks/vonage/<uuid:config_uuid>/event/",
        VonageEventHandler.as_view(),
        name="vonage-event",
    ),
    path(
        "webhooks/vonage/<uuid:config_uuid>/answer/",
        VonageAnswerHandler.as_view(),
        name="vonage-answer",
    ),
    # ── Telnyx ─────────────────────────────────────────────────────────
    path(
        "webhooks/telnyx/<uuid:config_uuid>/event/",
        TelnyxEventHandler.as_view(),
        name="telnyx-event",
    ),
]
