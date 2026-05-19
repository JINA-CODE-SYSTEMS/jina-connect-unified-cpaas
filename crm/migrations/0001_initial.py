# #198: initial crm app.
import uuid

import django.db.models.deletion
import encrypted_model_fields.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0017_tenantvoiceapp"),
        ("ctwa", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CrmConnection",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("provider", models.CharField(max_length=20)),
                ("access_token", encrypted_model_fields.fields.EncryptedTextField(blank=True, default="")),
                ("refresh_token", encrypted_model_fields.fields.EncryptedTextField(blank=True, default="")),
                ("api_key", encrypted_model_fields.fields.EncryptedTextField(blank=True, default="")),
                ("webhook_secret", encrypted_model_fields.fields.EncryptedTextField(blank=True, default="")),
                ("webhook_url", models.URLField(blank=True, default="", max_length=512)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("enabled", models.BooleanField(default=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="crm_connections",
                        to="tenants.tenant",
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
            ],
            options={
                "verbose_name": "CRM connection",
                "verbose_name_plural": "CRM connections",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["tenant", "provider"],
                        name="crm_unique_provider_per_tenant",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="CrmEntityMapping",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("jina_value", models.CharField(max_length=64)),
                ("crm_value", models.CharField(max_length=128)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mappings",
                        to="crm.crmconnection",
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
            ],
            options={
                "verbose_name": "CRM entity mapping",
                "verbose_name_plural": "CRM entity mappings",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["connection", "jina_value"],
                        name="crm_mapping_unique_jina_value",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="CrmSyncEvent",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("direction", models.CharField(max_length=10)),
                ("external_event_id", models.CharField(db_index=True, max_length=128)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("processed", models.BooleanField(default=False)),
                ("skip_reason", models.CharField(blank=True, default="", max_length=128)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sync_events",
                        to="crm.crmconnection",
                    ),
                ),
                (
                    "lead",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="crm_sync_events",
                        to="ctwa.ctwalead",
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
            ],
            options={
                "verbose_name": "CRM sync event",
                "verbose_name_plural": "CRM sync events",
                "indexes": [
                    models.Index(fields=["connection", "external_event_id"], name="crm_sync_conn_extid_idx"),
                    models.Index(fields=["direction", "processed"], name="crm_sync_dir_processed_idx"),
                ],
            },
        ),
    ]
