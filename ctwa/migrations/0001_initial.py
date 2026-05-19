# #194: initial ctwa app — CtwaCampaign + CtwaLead.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0017_tenantvoiceapp"),
        ("contacts", "0001_initial"),
        ("wa", "0016_waconversation_and_wamessage_referrals"),
        # meta/ and ads/ apps' initial migrations are required because
        # CtwaCampaign FK-references them (nullable until those land
        # OAuth + creative-upload flows in this same PR).
        ("meta", "0001_initial"),
        ("ads", "0001_initial"),
        ("chat_flow", "0008_chatflow_triggers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CtwaCampaign",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("meta_campaign_id", models.CharField(blank=True, default="", max_length=64)),
                ("meta_adset_id", models.CharField(blank=True, default="", max_length=64)),
                (
                    "meta_ad_id",
                    models.CharField(blank=True, db_index=True, default="", max_length=64),
                ),
                ("prefilled_message", models.TextField()),
                ("status", models.CharField(default="draft", max_length=20)),
                ("budget_type", models.CharField(default="daily", max_length=20)),
                ("budget_amount_minor", models.BigIntegerField(default=0)),
                ("currency", models.CharField(default="USD", max_length=3)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("qualification_signal", models.CharField(default="flow_node", max_length=20)),
                ("agent_team_key", models.CharField(blank=True, default="", max_length=64)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ctwa_campaigns",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "tenant_wa_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ctwa_campaigns",
                        to="tenants.tenantwaapp",
                    ),
                ),
                (
                    "meta_connection",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaigns",
                        to="meta.metabusinessconnection",
                    ),
                ),
                (
                    "creative",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaigns",
                        to="ads.adcreative",
                    ),
                ),
                (
                    "flow",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ctwa_campaigns",
                        to="chat_flow.chatflow",
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
                "verbose_name": "CTWA campaign",
                "verbose_name_plural": "CTWA campaigns",
                "indexes": [
                    models.Index(fields=["tenant", "status"], name="ctwa_campaign_tenant_status"),
                    models.Index(fields=["meta_ad_id"], name="ctwa_campaign_meta_ad_id_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CtwaLead",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("flagged_orphan_campaign", models.BooleanField(default=False)),
                ("meta_ad_id", models.CharField(db_index=True, max_length=64)),
                ("meta_adset_id", models.CharField(blank=True, default="", max_length=64)),
                ("meta_campaign_id", models.CharField(blank=True, default="", max_length=64)),
                (
                    "ctwa_clid",
                    models.CharField(blank=True, db_index=True, default="", max_length=128),
                ),
                ("source_url", models.TextField(blank=True, default="")),
                ("source_type", models.CharField(blank=True, default="", max_length=20)),
                ("headline", models.TextField(blank=True, default="")),
                ("body", models.TextField(blank=True, default="")),
                ("media_type", models.CharField(blank=True, default="", max_length=20)),
                ("media_url", models.TextField(blank=True, default="")),
                ("thumbnail_url", models.TextField(blank=True, default="")),
                ("first_message_at", models.DateTimeField()),
                ("qualification_status", models.CharField(default="new", max_length=20)),
                ("crm_external_id", models.CharField(blank=True, default="", max_length=128)),
                (
                    "last_crm_external_event_id",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ctwa_leads",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ctwa_leads",
                        to="contacts.tenantcontact",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ctwa_leads",
                        to="wa.waconversation",
                    ),
                ),
                (
                    "campaign",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="leads",
                        to="ctwa.ctwacampaign",
                    ),
                ),
                (
                    "agent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="claimed_ctwa_leads",
                        to=settings.AUTH_USER_MODEL,
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
                "verbose_name": "CTWA lead",
                "verbose_name_plural": "CTWA leads",
                "indexes": [
                    models.Index(
                        fields=["tenant", "campaign", "qualification_status"],
                        name="ctwa_lead_t_camp_status_idx",
                    ),
                    models.Index(fields=["meta_ad_id"], name="ctwa_lead_meta_ad_id_idx"),
                    models.Index(fields=["ctwa_clid"], name="ctwa_lead_clid_idx"),
                    models.Index(
                        fields=["flagged_orphan_campaign", "tenant"],
                        name="ctwa_lead_orphan_idx",
                    ),
                ],
            },
        ),
    ]
