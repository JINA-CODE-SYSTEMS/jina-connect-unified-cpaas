# #197: initial attribution app.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("ctwa", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AttributionEvent",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("event_name", models.CharField(max_length=40)),
                ("event_time", models.DateTimeField()),
                ("event_value_minor", models.BigIntegerField(blank=True, null=True)),
                ("currency", models.CharField(blank=True, default="", max_length=3)),
                ("sequence", models.BigIntegerField()),
                ("event_id", models.CharField(max_length=128, unique=True)),
                ("fbc", models.CharField(blank=True, default="", max_length=200)),
                ("capi_status", models.CharField(default="pending", max_length=20)),
                ("capi_response", models.JSONField(blank=True, null=True)),
                ("capi_attempts", models.SmallIntegerField(default=0)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("emq_score", models.SmallIntegerField(blank=True, null=True)),
                (
                    "lead",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attribution_events",
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
                "verbose_name": "Attribution event",
                "verbose_name_plural": "Attribution events",
                "constraints": [
                    models.UniqueConstraint(
                        fields=["lead", "event_name", "sequence"],
                        name="attribution_unique_event_per_lead",
                    )
                ],
                "indexes": [
                    models.Index(fields=["capi_status", "next_retry_at"], name="attr_evt_status_retry_idx"),
                    models.Index(fields=["lead", "event_name"], name="attr_evt_lead_name_idx"),
                ],
            },
        ),
    ]
