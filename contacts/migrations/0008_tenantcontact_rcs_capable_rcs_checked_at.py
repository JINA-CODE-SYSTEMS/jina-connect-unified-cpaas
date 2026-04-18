# Generated migration for #110 — RCS capability fields on TenantContact

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contacts", "0007_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantcontact",
            name="rcs_capable",
            field=models.BooleanField(null=True, help_text="Whether this contact supports RCS messaging"),
        ),
        migrations.AddField(
            model_name="tenantcontact",
            name="rcs_checked_at",
            field=models.DateTimeField(null=True, blank=True, help_text="When RCS capability was last checked"),
        ),
    ]
