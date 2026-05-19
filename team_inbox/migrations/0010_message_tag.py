# #195: MessageTag join table linking team_inbox.Messages to
# tenants.TenantTags. CTWA #194 ingestion auto-applies a campaign tag
# on the first message of each new lead; agents can manually attach
# or detach via REST.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("team_inbox", "0009_messages_reactions"),
        ("tenants", "0017_tenantvoiceapp"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageTag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("attached_at", models.DateTimeField(auto_now_add=True)),
                ("auto", models.BooleanField(default=False)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tags",
                        to="team_inbox.messages",
                    ),
                ),
                (
                    "tag",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="message_tags",
                        to="tenants.tenanttags",
                    ),
                ),
                (
                    "attached_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="message_tags_attached",
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
                "verbose_name": "Message tag",
                "verbose_name_plural": "Message tags",
                "constraints": [
                    models.UniqueConstraint(fields=["message", "tag"], name="message_tag_unique"),
                ],
                "indexes": [
                    models.Index(fields=["tag", "-attached_at"], name="message_tag_tag_attached_idx"),
                ],
            },
        ),
    ]
