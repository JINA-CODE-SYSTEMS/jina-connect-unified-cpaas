# Adds ``RecordingConsent`` for the voice consent gate (#171).
#
# Hand-written for the same reason ``voice/0001_initial.py`` is —
# ``manage.py makemigrations`` hangs on this dev box. Mirrors the
# auto-generator output: BaseTenantModelForFilterUser inherited fields
# come first, then the model-specific ones.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("voice", "0001_initial"),
        ("tenants", "0017_tenantvoiceapp"),
        ("contacts", "0010_add_import_to_contact_source"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecordingConsent",
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
                ("consent_given", models.BooleanField(default=False)),
                ("consent_timestamp", models.DateTimeField(blank=True, null=True)),
                (
                    "consent_method",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("verbal_ivr", "Verbal IVR"),
                            ("web_form", "Web form"),
                            ("api", "API"),
                            ("implied", "Implied (legal opt-in)"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "recording_url",
                    models.CharField(
                        blank=True,
                        help_text="Storage key for the verbal-IVR consent recording, if applicable.",
                        max_length=512,
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recording_consents",
                        to="contacts.tenantcontact",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recordingconsent_created_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recording_consents",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recordingconsent_updated_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Recording consent",
                "verbose_name_plural": "Recording consents",
                "indexes": [
                    models.Index(fields=["tenant", "contact"], name="voice_recor_tenant__idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "contact"),
                        name="recordingconsent_unique_tenant_contact",
                    ),
                ],
            },
        ),
    ]
