# #191: initial meta app.
import uuid

import django.db.models.deletion
import encrypted_model_fields.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0017_tenantvoiceapp"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MetaBusinessConnection",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("meta_business_id", models.CharField(max_length=64)),
                ("meta_ad_account_id", models.CharField(max_length=64)),
                ("page_id", models.CharField(blank=True, default="", max_length=64)),
                ("instagram_actor_id", models.CharField(blank=True, default="", max_length=64)),
                ("system_user_token", encrypted_model_fields.fields.EncryptedTextField()),
                ("scopes", models.JSONField(blank=True, default=list)),
                ("connected_at", models.DateTimeField(auto_now_add=True)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("needs_reauth", models.BooleanField(default=False)),
                ("linked_waba_ids", models.JSONField(blank=True, default=list)),
                ("linked_wabas_refreshed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="meta_connections",
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
                "verbose_name": "Meta business connection",
                "verbose_name_plural": "Meta business connections",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["tenant", "meta_ad_account_id"],
                        name="meta_unique_ad_account_per_tenant",
                    )
                ],
                "indexes": [
                    models.Index(fields=["tenant", "needs_reauth"], name="meta_conn_t_reauth_idx"),
                ],
            },
        ),
    ]
