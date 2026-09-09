"""Add archived_at to Tenant (and its historical model).

The Active Customer Account report (Fabtary agreement Cl. 4.2) pro-rates each
account by the days it existed in the month, so it needs the date an account
was archived. archived_at records that as an explicit business state rather
than inferring it from the simple_history audit trail, which is a log subject
to pruning and cannot distinguish "archived, data purged" from "row deleted".

Existing rows get NULL, i.e. never archived, which is correct.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0021_brandingsettings_primary_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicaltenant",
            name="archived_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When this account was archived. Null means the account is live.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="archived_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When this account was archived. Null means the account is live.",
                null=True,
            ),
        ),
    ]
