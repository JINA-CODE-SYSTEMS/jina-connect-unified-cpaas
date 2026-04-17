# Generated migration for #115 — Conversation threading + Messages.conversation FK

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("team_inbox", "0005_messages_edited_at"),
        ("tenants", "__latest__"),
        ("contacts", "0008_tenantcontact_rcs_capable_rcs_checked_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="Conversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("platform", models.CharField(
                    choices=[("WHATSAPP", "WhatsApp"), ("TELEGRAM", "Telegram"), ("SMS", "SMS"), ("RCS", "RCS"), ("VOICE", "Voice")],
                    max_length=10,
                )),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("contact", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversations", to="contacts.tenantcontact")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversations", to="tenants.tenant")),
            ],
            options={
                "verbose_name": "Conversation",
                "ordering": ["-started_at"],
                "indexes": [
                    models.Index(fields=["tenant", "contact", "-started_at"], name="team_inbox_conv_tenant_contact"),
                    models.Index(fields=["tenant", "is_active"], name="team_inbox_conv_tenant_active"),
                ],
            },
        ),
        migrations.AddField(
            model_name="messages",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="messages",
                to="team_inbox.conversation",
                help_text="Conversation thread this message belongs to (#115)",
            ),
        ),
    ]
