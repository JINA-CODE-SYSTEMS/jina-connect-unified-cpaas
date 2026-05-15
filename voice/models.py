"""Voice channel models.

Six models cover the voice channel:

  * ``VoiceProviderConfig`` — per-tenant provider connection (SIP trunk or
    HTTP voice API). Stores encrypted credentials and concurrency limits.
  * ``VoiceCall`` — one row per call leg. Transfer = second row with
    ``parent_call`` set.
  * ``VoiceCallEvent`` — append-only audit log of provider events.
  * ``VoiceTemplate`` — TTS / pre-recorded / IVR-menu templates.
  * ``VoiceRecording`` — recorded audio with retention metadata.
  * ``VoiceRateCard`` — destination-prefix rate card for SIP and any
    provider without a cost callback.

Voice transaction types already live in ``abstract.models``
(``VOICE_OUTBOUND``, ``VOICE_INBOUND``, ``VOICE_NUMBER_RENT``,
``VOICE_AI_AGENT``, ``VOICE_RECORDING``). The team-inbox ``Messages.platform``
enum already includes ``VOICE``. Contact call-tracking fields
(``last_called_at``, ``total_calls``, ``dnc``) already exist on ``Contact``.
So no schema change is required outside this app plus a
``TenantVoiceApp`` row in ``tenants/``.
"""

from __future__ import annotations

import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedTextField

from abstract.models import BaseTenantModelForFilterUser
from contacts.models import TenantContact
from tenants.models import Tenant
from voice.constants import (
    AudioFormat,
    CallDirection,
    CallEventType,
    CallStatus,
    CostSource,
    HangupCause,
    TemplateKind,
    VoiceProvider,
)

# ─────────────────────────────────────────────────────────────────────────────
# Provider configuration
# ─────────────────────────────────────────────────────────────────────────────


class VoiceProviderConfig(BaseTenantModelForFilterUser):
    """Per-tenant voice provider connection.

    A tenant may have several configs (e.g. a SIP trunk for outbound +
    Twilio for inbound, or one Twilio account per region). The
    ``priority`` field plus ``is_default_outbound`` / ``is_default_inbound``
    flags drive selection.

    ``credentials`` is an encrypted JSON-serialised dict whose shape depends
    on ``provider`` — see ``voice/adapters/credentials.py`` (added by #159)
    for the per-provider schema validators.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="voice_provider_configs")

    provider = models.CharField(
        max_length=20,
        choices=VoiceProvider.choices,
        help_text="Protocol/API family (sip, twilio, plivo, vonage, telnyx, exotel).",
    )
    vendor_label = models.CharField(
        max_length=120,
        blank=True,
        help_text='Display-only label, e.g. "Dialogic India" or "Plivo Mumbai".',
    )

    is_default_outbound = models.BooleanField(default=False)
    is_default_inbound = models.BooleanField(default=False)
    priority = models.IntegerField(
        default=0,
        help_text="Higher priority configs are picked first when multiple match.",
    )

    credentials = EncryptedTextField(
        blank=True,
        null=True,
        help_text="JSON-serialised provider credentials, encrypted at rest.",
    )
    from_numbers = models.JSONField(
        default=list,
        blank=True,
        help_text="List of E.164 DIDs the tenant can dial from on this config.",
    )
    inbound_webhook_token = models.CharField(
        max_length=64,
        blank=True,
        help_text="Shared secret used for inbound webhook URL signing.",
    )
    max_concurrent_calls = models.IntegerField(
        default=10,
        help_text="Concurrency cap enforced by a Redis semaphore at dispatch.",
    )
    currency = models.CharField(max_length=3, default="USD")
    recording_enabled = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)

    # Cross-channel SMS fallback (#172). When the voice call ends in
    # one of ``fallback_on_causes`` (default NO_ANSWER / USER_BUSY /
    # CALL_REJECTED) and ``fallback_sms_enabled`` is True, the post-call
    # signal dispatches an SMS via ``fallback_sms_config`` rendering
    # ``fallback_sms_template`` against the call context.
    fallback_sms_enabled = models.BooleanField(default=False)
    fallback_sms_config = models.ForeignKey(
        "sms.SMSApp",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_fallback_configs",
        help_text="SMS app used to send the fallback message.",
    )
    fallback_sms_template = models.TextField(
        blank=True,
        help_text='Jinja-style "{{var}}" placeholders allowed (first_name, from_number, to_number).',
    )
    fallback_on_causes = models.JSONField(
        default=list,
        blank=True,
        help_text=("List of HangupCause values that trigger the SMS fallback. Empty list disables the cause filter."),
    )

    class Meta:
        verbose_name = "Voice provider config"
        verbose_name_plural = "Voice provider configs"
        indexes = [
            models.Index(fields=["tenant", "provider"]),
            models.Index(fields=["tenant", "enabled", "priority"]),
        ]

    def __str__(self) -> str:
        label = self.vendor_label or self.get_provider_display()
        return f"{self.tenant_id}:{label}"

    def clean(self):
        """Validate ``credentials`` against the per-provider schema."""
        super().clean()
        # Lazy import — keeps the model importable before the adapter
        # package is fully loaded (matters at Django startup).
        from django.core.exceptions import ValidationError as DjValidationError

        from voice.adapters.credentials import validate_credentials
        from voice.exceptions import VoiceCredentialError

        if self.credentials in (None, "", "{}"):
            return
        try:
            validate_credentials(self.provider, self.credentials)
        except VoiceCredentialError as e:
            raise DjValidationError({"credentials": str(e)}) from e


# ─────────────────────────────────────────────────────────────────────────────
# Calls
# ─────────────────────────────────────────────────────────────────────────────


class VoiceCall(BaseTenantModelForFilterUser):
    """One row per call leg.

    Transfers create a second row with ``parent_call`` pointing at the
    original. ``provider_call_id`` (Twilio ``CallSid``, Plivo ``CallUUID``,
    SIP ``Call-ID``, …) is unique within a ``provider_config``.

    The row is mutated through the state machine until it hits a terminal
    state (``COMPLETED`` / ``FAILED`` / ``CANCELED``); after that, only
    ``VoiceCallEvent`` rows append.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="voice_calls")
    provider_config = models.ForeignKey(
        VoiceProviderConfig,
        on_delete=models.PROTECT,
        related_name="calls",
    )
    provider_call_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Twilio CallSid / Plivo CallUUID / SIP Call-ID / …",
    )

    direction = models.CharField(max_length=10, choices=CallDirection.choices)
    from_number = models.CharField(max_length=20, help_text="E.164")
    to_number = models.CharField(max_length=20, help_text="E.164")

    contact = models.ForeignKey(
        TenantContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_calls",
    )
    parent_call = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_legs",
        help_text="Set on transfer legs; points at the original call.",
    )
    flow_session = models.ForeignKey(
        "chat_flow.UserChatFlowSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_calls",
    )
    broadcast = models.ForeignKey(
        "broadcast.Broadcast",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_calls",
    )
    team_inbox_message = models.ForeignKey(
        "team_inbox.Messages",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voice_calls",
        help_text="Inbox conversation row for this call (set by signal).",
    )

    status = models.CharField(
        max_length=15,
        choices=CallStatus.choices,
        default=CallStatus.QUEUED,
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the call was answered (not when it was placed).",
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    hangup_cause = models.CharField(
        max_length=30,
        choices=HangupCause.choices,
        blank=True,
        help_text="Canonical hangup cause; raw provider cause in metadata.",
    )

    recording_url = models.CharField(max_length=512, blank=True)
    recording_duration_seconds = models.IntegerField(null=True, blank=True)

    cost_amount = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    cost_currency = models.CharField(max_length=3, blank=True)
    cost_source = models.CharField(
        max_length=20,
        choices=CostSource.choices,
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw provider payload + any per-call notes.",
    )

    class Meta:
        verbose_name = "Voice call"
        verbose_name_plural = "Voice calls"
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "started_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["provider_config", "provider_call_id"],
                name="voicecall_unique_provider_call_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.from_number}->{self.to_number} ({self.status})"


class VoiceCallEvent(BaseTenantModelForFilterUser):
    """Append-only event log for a ``VoiceCall``.

    Every provider event (initiated, ringing, answered, dtmf, recording_*,
    transferred, completed, failed) gets a row. ``sequence`` is monotonic
    per call so the timeline can be reconstructed deterministically even
    when webhooks arrive out of order.
    """

    filter_by_user_tenant_fk = "call__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=30, choices=CallEventType.choices)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    sequence = models.BigIntegerField(help_text="Monotonic per-call sequence — used to reconstruct order.")

    class Meta:
        verbose_name = "Voice call event"
        verbose_name_plural = "Voice call events"
        indexes = [
            models.Index(fields=["call", "sequence"]),
        ]
        ordering = ["call", "sequence"]

    def __str__(self) -> str:
        return f"{self.call_id}:{self.sequence}:{self.event_type}"


# ─────────────────────────────────────────────────────────────────────────────
# Templates, recordings, rate cards
# ─────────────────────────────────────────────────────────────────────────────


class VoiceTemplate(BaseTenantModelForFilterUser):
    """Voice-specific message template (TTS / audio URL / IVR menu).

    Stands alone for now; ``WATemplate`` and ``VoiceTemplate`` will fold
    under a shared template base in a future refactor.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="voice_templates")

    template_kind = models.CharField(max_length=20, choices=TemplateKind.choices)
    tts_text = models.TextField(
        blank=True,
        help_text='Jinja-style "{{var}}" placeholders allowed.',
    )
    tts_voice = models.CharField(max_length=64, blank=True)
    tts_language = models.CharField(max_length=10, blank=True, help_text="BCP-47 (e.g. en-IN, hi-IN).")
    audio_url = models.CharField(max_length=512, blank=True)
    audio_format = models.CharField(max_length=10, choices=AudioFormat.choices, blank=True)

    class Meta:
        verbose_name = "Voice template"
        verbose_name_plural = "Voice templates"
        indexes = [models.Index(fields=["tenant", "name"])]

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.name}"


class VoiceRecording(BaseTenantModelForFilterUser):
    """Recorded audio + retention metadata.

    Uploaded to S3-compatible storage by ``voice.recordings.tasks`` (PR #161).
    ``retention_expires_at`` is filled when the row is created based on
    ``TenantVoiceApp.recording_retention_days``.
    """

    filter_by_user_tenant_fk = "call__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="recordings")
    provider_recording_id = models.CharField(max_length=128)
    storage_url = models.CharField(
        max_length=512,
        help_text="Path inside our S3 bucket (not a signed URL).",
    )

    duration_seconds = models.IntegerField()
    size_bytes = models.BigIntegerField()
    format = models.CharField(max_length=10, choices=AudioFormat.choices)

    transcription = models.TextField(blank=True)
    transcription_provider = models.CharField(max_length=40, blank=True)
    transcription_confidence = models.FloatField(null=True, blank=True)

    retention_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Voice recording"
        verbose_name_plural = "Voice recordings"
        indexes = [
            models.Index(fields=["call"]),
            models.Index(fields=["retention_expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.call_id}:{self.provider_recording_id}"


class RecordingConsent(BaseTenantModelForFilterUser):
    """Per-contact recording-consent record (#171).

    When a tenant has ``TenantVoiceApp.recording_requires_consent``
    enabled, adapters refuse to turn on call recording for a contact
    that doesn't have a ``RecordingConsent`` row with
    ``consent_given=True``. The row tracks how consent was captured
    (verbal IVR, web form, API, implied) so audit / legal reviews can
    reconstruct the chain of custody.

    A pointer back to the verbal-IVR recording (``recording_url``) lets
    legal teams play back the consent capture itself, which most
    jurisdictions need for two-party-consent regions.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="recording_consents")
    contact = models.ForeignKey(
        TenantContact,
        on_delete=models.CASCADE,
        related_name="recording_consents",
    )
    consent_given = models.BooleanField(default=False)
    consent_timestamp = models.DateTimeField(null=True, blank=True)
    consent_method = models.CharField(
        max_length=20,
        choices=[
            ("verbal_ivr", "Verbal IVR"),
            ("web_form", "Web form"),
            ("api", "API"),
            ("implied", "Implied (legal opt-in)"),
        ],
        blank=True,
    )
    recording_url = models.CharField(
        max_length=512,
        blank=True,
        help_text="Storage key for the verbal-IVR consent recording, if applicable.",
    )

    class Meta:
        verbose_name = "Recording consent"
        verbose_name_plural = "Recording consents"
        indexes = [
            models.Index(fields=["tenant", "contact"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "contact"],
                name="recordingconsent_unique_tenant_contact",
            ),
        ]

    def __str__(self) -> str:
        state = "given" if self.consent_given else "not given"
        return f"{self.tenant_id}:{self.contact_id}:{state}"


class VoiceRateCard(BaseTenantModelForFilterUser):
    """Per-prefix per-config rate card.

    Lookup: longest matching ``destination_prefix`` within ``valid_from`` /
    ``valid_to``. Used by ``voice.billing.rater`` when the provider does
    not publish a cost callback (e.g. SIP).
    """

    filter_by_user_tenant_fk = "provider_config__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider_config = models.ForeignKey(
        VoiceProviderConfig,
        on_delete=models.CASCADE,
        related_name="rate_cards",
    )
    destination_prefix = models.CharField(
        max_length=10,
        help_text='E.164 prefix, e.g. "+91", "+1", "+9180".',
    )
    rate_per_minute = models.DecimalField(max_digits=14, decimal_places=6)
    currency = models.CharField(max_length=3, default="USD")
    billing_increment_seconds = models.IntegerField(
        default=60,
        help_text="Typically 60 (per-minute) or 1 (per-second).",
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Voice rate card"
        verbose_name_plural = "Voice rate cards"
        indexes = [
            models.Index(fields=["provider_config", "destination_prefix"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider_config_id}:{self.destination_prefix}@{self.rate_per_minute}"
