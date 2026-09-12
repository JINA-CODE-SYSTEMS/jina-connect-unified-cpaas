"""Make a blank ``bsp`` mean META, in the column as well as in code (#265).

Two changes that have to travel together.

The column default moves from GUPSHUP to META, so a row created without an
explicit ``bsp`` — by admin, a fixture, a data migration, or the legacy
``tenant-gupshup`` endpoint — lands on the provider the adapter factory would
have given it anyway.

And existing rows with a blank column are stamped META. Those are the rows
that behaved inconsistently: the factory returned a META adapter, the sync
mapper returned the Gupshup one, and the webhook receiver rejected them
outright. Writing the value down ends the disagreement in the data, rather
than relying on every reader remembering to resolve it.

Rows that already say GUPSHUP are left alone. Some are only GUPSHUP because
the old default put it there, but nothing in the data tells those apart from
a deliberate choice, and guessing wrong would move a live app to another
provider.
"""

from django.db import migrations, models


def blank_bsp_becomes_meta(apps, schema_editor):
    TenantWAApp = apps.get_model("tenants", "TenantWAApp")
    TenantWAApp.objects.filter(bsp="").update(bsp="META")
    TenantWAApp.objects.filter(bsp__isnull=True).update(bsp="META")


def noop_reverse(apps, schema_editor):
    """Deliberately does nothing.

    Reversing would mean blanking every META row, and nothing records which
    ones were blank before. Leaving the value set is harmless going back: the
    old code read an explicit META correctly — it was only the blank it read
    three different ways.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0025_meta_app_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenantwaapp",
            name="bsp",
            field=models.CharField(
                choices=[
                    ("GUPSHUP", "Gupshup"),
                    ("META", "Meta"),
                    ("TWILIO", "Twilio"),
                    ("MESSAGEBIRD", "MessageBird"),
                    ("WATI", "WATI"),
                    ("AISENSY", "Aisensy"),
                    ("INTERAKT", "Interakt"),
                    ("YELLOW_AI", "Yellow.ai"),
                    ("GOOGLE_RBM", "Google RBM"),
                    ("META_RCS", "Meta RCS"),
                ],
                default="META",
                max_length=20,
            ),
        ),
        migrations.RunPython(blank_bsp_becomes_meta, noop_reverse),
    ]
