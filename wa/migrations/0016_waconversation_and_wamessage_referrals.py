# #189: WaConversation model + WAMessage FK to it.
# #192: CTWA referral fields on WAMessage (populated by parse_referral
# at inbound-webhook processing time).
#
# Note on cross-app FK: WaConversation.ctwa_lead -> ctwa.CtwaLead lands
# in a follow-up migration (``0017_waconversation_ctwa_lead_fk``) once
# the ctwa app's initial migration creates the ``CtwaLead`` model.
# Splitting avoids a circular-dependency knot during makemigrations.
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wa", "0015_backfill_watemplate_tenant"),
        ("contacts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WaConversation",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("first_message_at", models.DateTimeField()),
                ("last_inbound_at", models.DateTimeField()),
                (
                    "service_window_expires_at",
                    models.DateTimeField(db_index=True),
                ),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "wa_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversations",
                        to="wa.waapp",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="wa_conversations",
                        to="contacts.tenantcontact",
                    ),
                ),
            ],
            options={
                "verbose_name": "WA conversation",
                "verbose_name_plural": "WA conversations",
            },
        ),
        migrations.AddIndex(
            model_name="waconversation",
            index=models.Index(
                fields=["wa_app", "contact", "-last_inbound_at"],
                name="wa_conv_app_contact_last_idx",
            ),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="messages",
                to="wa.waconversation",
            ),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_source_type",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_source_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_source_url",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_headline",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_body",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_media_type",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_media_url",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="wamessage",
            name="referral_ctwa_clid",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
