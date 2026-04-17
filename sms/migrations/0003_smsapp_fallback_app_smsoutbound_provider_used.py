# Generated migration for #104 — SMS provider failover fields

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("sms", "0002_encrypt_credentials_add_dlr_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="smsapp",
            name="fallback_app",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="fallback_for",
                to="sms.smsapp",
                help_text="Fallback SMS app to use if sending via this app fails",
            ),
        ),
        migrations.AddField(
            model_name="smsoutboundmessage",
            name="provider_used",
            field=models.CharField(
                blank=True,
                max_length=20,
                help_text="Provider that actually sent this message (may differ from sms_app.provider after failover)",
            ),
        ),
    ]
