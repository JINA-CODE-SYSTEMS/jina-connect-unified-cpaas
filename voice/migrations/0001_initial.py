# Voice channel initial migration (#158).
#
# Hand-written because manage.py makemigrations hangs on this dev box.
# Mirrors the auto-generator output: BaseTenantModelForFilterUser fields
# (description, name, created_at, updated_at, is_active) + created_by /
# updated_by from BaseModelWithOwner are listed explicitly per model. The
# concrete model overrides ``id`` to UUIDField, so the migration uses
# UUIDField (not BigAutoField).
#
# All six voice models are created in this single migration.
# ``tenants.TenantVoiceApp`` (which FKs ``voice.VoiceProviderConfig``)
# lives in a separate migration in ``tenants/`` that depends on this one.

import uuid

import django.db.models.deletion
import encrypted_model_fields.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0016_initial"),
        ("contacts", "0010_add_import_to_contact_source"),
        ("chat_flow", "0007_chatflow_platform"),
        ("broadcast", "0005_initial"),
        ("team_inbox", "0009_messages_reactions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ──────────────────────────────────────────────────────────────
        # VoiceProviderConfig
        # ──────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="VoiceProviderConfig",
            fields=[
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("sip", "SIP (any trunk)"),
                            ("twilio", "Twilio Voice"),
                            ("plivo", "Plivo Voice"),
                            ("vonage", "Vonage Voice"),
                            ("telnyx", "Telnyx Call Control"),
                            ("exotel", "Exotel Voice"),
                        ],
                        help_text="Protocol/API family (sip, twilio, plivo, vonage, telnyx, exotel).",
                        max_length=20,
                    ),
                ),
                (
                    "vendor_label",
                    models.CharField(
                        blank=True,
                        help_text='Display-only label, e.g. "Dialogic India" or "Plivo Mumbai".',
                        max_length=120,
                    ),
                ),
                ("is_default_outbound", models.BooleanField(default=False)),
                ("is_default_inbound", models.BooleanField(default=False)),
                (
                    "priority",
                    models.IntegerField(
                        default=0,
                        help_text="Higher priority configs are picked first when multiple match.",
                    ),
                ),
                (
                    "credentials",
                    encrypted_model_fields.fields.EncryptedTextField(
                        blank=True,
                        help_text="JSON-serialised provider credentials, encrypted at rest.",
                        null=True,
                    ),
                ),
                (
                    "from_numbers",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="List of E.164 DIDs the tenant can dial from on this config.",
                    ),
                ),
                (
                    "inbound_webhook_token",
                    models.CharField(
                        blank=True,
                        help_text="Shared secret used for inbound webhook URL signing.",
                        max_length=64,
                    ),
                ),
                (
                    "max_concurrent_calls",
                    models.IntegerField(
                        default=10,
                        help_text="Concurrency cap enforced by a Redis semaphore at dispatch.",
                    ),
                ),
                ("currency", models.CharField(default="USD", max_length=3)),
                ("recording_enabled", models.BooleanField(default=False)),
                ("enabled", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="voice_provider_configs",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Voice provider config",
                "verbose_name_plural": "Voice provider configs",
            },
        ),
        migrations.AddIndex(
            model_name="voiceproviderconfig",
            index=models.Index(
                fields=["tenant", "provider"],
                name="voice_voice_tenant__1d2c14_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="voiceproviderconfig",
            index=models.Index(
                fields=["tenant", "enabled", "priority"],
                name="voice_voice_tenant__a3b5c4_idx",
            ),
        ),
        # ──────────────────────────────────────────────────────────────
        # VoiceCall
        # ──────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="VoiceCall",
            fields=[
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "provider_call_id",
                    models.CharField(
                        db_index=True,
                        help_text="Twilio CallSid / Plivo CallUUID / SIP Call-ID / …",
                        max_length=128,
                    ),
                ),
                (
                    "direction",
                    models.CharField(
                        choices=[("inbound", "Inbound"), ("outbound", "Outbound")],
                        max_length=10,
                    ),
                ),
                ("from_number", models.CharField(help_text="E.164", max_length=20)),
                ("to_number", models.CharField(help_text="E.164", max_length=20)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("QUEUED", "Queued"),
                            ("INITIATING", "Initiating"),
                            ("RINGING", "Ringing"),
                            ("IN_PROGRESS", "In progress"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                            ("CANCELED", "Canceled"),
                        ],
                        default="QUEUED",
                        max_length=15,
                    ),
                ),
                (
                    "started_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the call was answered (not when it was placed).",
                        null=True,
                    ),
                ),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("duration_seconds", models.IntegerField(blank=True, null=True)),
                (
                    "hangup_cause",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("NORMAL_CLEARING", "Normal clearing"),
                            ("USER_BUSY", "User busy"),
                            ("NO_ANSWER", "No answer"),
                            ("NO_USER_RESPONSE", "No user response"),
                            ("CALL_REJECTED", "Call rejected"),
                            ("NUMBER_UNALLOCATED", "Number unallocated"),
                            ("NETWORK_OUT_OF_ORDER", "Network out of order"),
                            ("NORMAL_TEMPORARY_FAILURE", "Temporary failure"),
                            ("RESOURCE_UNAVAILABLE", "Resource unavailable"),
                            ("FACILITY_REJECTED", "Facility rejected"),
                            ("DESTINATION_OUT_OF_ORDER", "Destination out of order"),
                            ("INVALID_NUMBER_FORMAT", "Invalid number format"),
                            ("INTERWORKING", "Interworking error"),
                            ("UNKNOWN", "Unknown"),
                        ],
                        help_text="Canonical hangup cause; raw provider cause in metadata.",
                        max_length=30,
                    ),
                ),
                ("recording_url", models.CharField(blank=True, max_length=512)),
                ("recording_duration_seconds", models.IntegerField(blank=True, null=True)),
                (
                    "cost_amount",
                    models.DecimalField(blank=True, decimal_places=6, max_digits=14, null=True),
                ),
                ("cost_currency", models.CharField(blank=True, max_length=3)),
                (
                    "cost_source",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("provider", "Provider cost callback"),
                            ("local_ratecard", "Local rate-card lookup"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Raw provider payload + any per-call notes.",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="voice_calls",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "provider_config",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="calls",
                        to="voice.voiceproviderconfig",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="voice_calls",
                        to="contacts.tenantcontact",
                    ),
                ),
                (
                    "parent_call",
                    models.ForeignKey(
                        blank=True,
                        help_text="Set on transfer legs; points at the original call.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transfer_legs",
                        to="voice.voicecall",
                    ),
                ),
                (
                    "flow_session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="voice_calls",
                        to="chat_flow.userchatflowsession",
                    ),
                ),
                (
                    "broadcast",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="voice_calls",
                        to="broadcast.broadcast",
                    ),
                ),
                (
                    "team_inbox_message",
                    models.ForeignKey(
                        blank=True,
                        help_text="Inbox conversation row for this call (set by signal).",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="voice_calls",
                        to="team_inbox.messages",
                    ),
                ),
            ],
            options={
                "verbose_name": "Voice call",
                "verbose_name_plural": "Voice calls",
            },
        ),
        migrations.AddIndex(
            model_name="voicecall",
            index=models.Index(fields=["tenant", "status"], name="voice_voice_tenant__b9d4e2_idx"),
        ),
        migrations.AddIndex(
            model_name="voicecall",
            index=models.Index(fields=["tenant", "started_at"], name="voice_voice_tenant__e7f1a3_idx"),
        ),
        migrations.AddConstraint(
            model_name="voicecall",
            constraint=models.UniqueConstraint(
                fields=("provider_config", "provider_call_id"),
                name="voicecall_unique_provider_call_id",
            ),
        ),
        # ──────────────────────────────────────────────────────────────
        # VoiceCallEvent
        # ──────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="VoiceCallEvent",
            fields=[
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("initiated", "Initiated"),
                            ("ringing", "Ringing"),
                            ("answered", "Answered"),
                            ("dtmf", "DTMF received"),
                            ("speech", "Speech received"),
                            ("recording_started", "Recording started"),
                            ("recording_completed", "Recording completed"),
                            ("transferred", "Transferred"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        max_length=30,
                    ),
                ),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField()),
                (
                    "sequence",
                    models.BigIntegerField(help_text="Monotonic per-call sequence — used to reconstruct order."),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "call",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="voice.voicecall",
                    ),
                ),
            ],
            options={
                "verbose_name": "Voice call event",
                "verbose_name_plural": "Voice call events",
                "ordering": ["call", "sequence"],
            },
        ),
        migrations.AddIndex(
            model_name="voicecallevent",
            index=models.Index(fields=["call", "sequence"], name="voice_voice_call_id_c0a8d1_idx"),
        ),
        # ──────────────────────────────────────────────────────────────
        # VoiceTemplate
        # ──────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="VoiceTemplate",
            fields=[
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "template_kind",
                    models.CharField(
                        choices=[
                            ("tts_script", "TTS script"),
                            ("audio_url", "Pre-recorded audio URL"),
                            ("ivr_menu", "IVR menu"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "tts_text",
                    models.TextField(
                        blank=True,
                        help_text='Jinja-style "{{var}}" placeholders allowed.',
                    ),
                ),
                ("tts_voice", models.CharField(blank=True, max_length=64)),
                (
                    "tts_language",
                    models.CharField(
                        blank=True,
                        help_text="BCP-47 (e.g. en-IN, hi-IN).",
                        max_length=10,
                    ),
                ),
                ("audio_url", models.CharField(blank=True, max_length=512)),
                (
                    "audio_format",
                    models.CharField(
                        blank=True,
                        choices=[("mp3", "MP3"), ("wav", "WAV")],
                        max_length=10,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="voice_templates",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Voice template",
                "verbose_name_plural": "Voice templates",
            },
        ),
        migrations.AddIndex(
            model_name="voicetemplate",
            index=models.Index(fields=["tenant", "name"], name="voice_voice_tenant__d1e2f3_idx"),
        ),
        # ──────────────────────────────────────────────────────────────
        # VoiceRecording
        # ──────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="VoiceRecording",
            fields=[
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("provider_recording_id", models.CharField(max_length=128)),
                (
                    "storage_url",
                    models.CharField(
                        help_text="Path inside our S3 bucket (not a signed URL).",
                        max_length=512,
                    ),
                ),
                ("duration_seconds", models.IntegerField()),
                ("size_bytes", models.BigIntegerField()),
                (
                    "format",
                    models.CharField(choices=[("mp3", "MP3"), ("wav", "WAV")], max_length=10),
                ),
                ("transcription", models.TextField(blank=True)),
                ("transcription_provider", models.CharField(blank=True, max_length=40)),
                ("transcription_confidence", models.FloatField(blank=True, null=True)),
                ("retention_expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "call",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recordings",
                        to="voice.voicecall",
                    ),
                ),
            ],
            options={
                "verbose_name": "Voice recording",
                "verbose_name_plural": "Voice recordings",
            },
        ),
        migrations.AddIndex(
            model_name="voicerecording",
            index=models.Index(fields=["call"], name="voice_voice_call_id_a1b2c3_idx"),
        ),
        migrations.AddIndex(
            model_name="voicerecording",
            index=models.Index(fields=["retention_expires_at"], name="voice_voice_retent_d4e5f6_idx"),
        ),
        # ──────────────────────────────────────────────────────────────
        # VoiceRateCard
        # ──────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="VoiceRateCard",
            fields=[
                ("description", models.TextField(blank=True, null=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "destination_prefix",
                    models.CharField(
                        help_text='E.164 prefix, e.g. "+91", "+1", "+9180".',
                        max_length=10,
                    ),
                ),
                ("rate_per_minute", models.DecimalField(decimal_places=6, max_digits=14)),
                ("currency", models.CharField(default="USD", max_length=3)),
                (
                    "billing_increment_seconds",
                    models.IntegerField(
                        default=60,
                        help_text="Typically 60 (per-minute) or 1 (per-second).",
                    ),
                ),
                ("valid_from", models.DateTimeField()),
                ("valid_to", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "provider_config",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rate_cards",
                        to="voice.voiceproviderconfig",
                    ),
                ),
            ],
            options={
                "verbose_name": "Voice rate card",
                "verbose_name_plural": "Voice rate cards",
            },
        ),
        migrations.AddIndex(
            model_name="voiceratecard",
            index=models.Index(
                fields=["provider_config", "destination_prefix"],
                name="voice_voice_provide_aa11bb_idx",
            ),
        ),
    ]
