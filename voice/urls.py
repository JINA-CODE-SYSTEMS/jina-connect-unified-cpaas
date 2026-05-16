"""Voice channel URL routes.

Webhook endpoints for each HTTP voice provider live here. The
top-level URL config mounts this module under ``/voice/v1/``.

For #160 only Twilio is wired up. Plivo / Vonage / Telnyx / Exotel
arrive with PRs #164–#167.
"""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from voice.views import (
    RecordingConsentViewSet,
    TenantVoiceAppViewSet,
    VoiceCallEventViewSet,
    VoiceCallViewSet,
    VoiceProviderConfigViewSet,
    VoiceRateCardViewSet,
    VoiceRecordingViewSet,
    VoiceTemplateViewSet,
)
from voice.webhooks.exotel import (
    ExotelPassthruHandler,
    ExotelStatusHandler,
)
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

router = DefaultRouter()
router.register(r"provider-configs", VoiceProviderConfigViewSet, basename="provider-config")
router.register(r"calls", VoiceCallViewSet, basename="call")
router.register(r"call-events", VoiceCallEventViewSet, basename="call-event")
router.register(r"templates", VoiceTemplateViewSet, basename="template")
router.register(r"recordings", VoiceRecordingViewSet, basename="recording")
router.register(r"rate-cards", VoiceRateCardViewSet, basename="rate-card")
router.register(r"tenant-voice-app", TenantVoiceAppViewSet, basename="tenant-voice-app")
router.register(r"recording-consents", RecordingConsentViewSet, basename="recording-consent")


urlpatterns = [
    # REST API surface — mounted under ``/voice/v1/`` from the project
    # URL conf, so the effective prefix becomes ``/voice/v1/api/``.
    path("api/", include(router.urls)),
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
    # ── Exotel ─────────────────────────────────────────────────────────
    path(
        "webhooks/exotel/<uuid:config_uuid>/status/",
        ExotelStatusHandler.as_view(),
        name="exotel-status",
    ),
    path(
        "webhooks/exotel/<uuid:config_uuid>/passthru/",
        ExotelPassthruHandler.as_view(),
        name="exotel-passthru",
    ),
]
