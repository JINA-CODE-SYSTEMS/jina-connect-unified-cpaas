"""``mobile`` becomes nullable, and existing empty values become NULL.

``mobile`` was ``unique=True`` and NOT NULL, so "no number known" was the empty
string — and the empty string fits exactly once. The second account ever created
without a number died on ``users_user_mobile_key``, globally across every
tenant. In practice that meant the second person invited to any organisation who
did not already have an account.

Two steps, in this order and for a reason:

1. ``AlterField`` first, so the column will accept NULL at all.
2. ``RunPython`` second, moving any existing ``''`` to NULL — otherwise the one
   row already holding the empty string keeps occupying the single slot, and the
   next invitation fails exactly as before. The schema change alone does not fix
   the bug for a deployment that has already used its one empty string.

The backfill is written both ways so the migration reverses cleanly. Reversing
can only restore a single empty string, which is all the old constraint ever
permitted; if more than one NULL exists by then, the reverse fails loudly on the
unique index rather than silently discarding numbers. That is the correct
outcome — the old schema genuinely cannot hold that data.
"""

import phonenumber_field.modelfields
from django.db import migrations


def empty_mobiles_to_null(apps, schema_editor):
    """Free the single slot the empty string was occupying."""
    User = apps.get_model("users", "User")
    User.objects.filter(mobile="").update(mobile=None)


def null_mobiles_to_empty(apps, schema_editor):
    """Reverse. Fails on the unique index if more than one row is NULL, which is
    honest: the pre-migration schema cannot represent two unknown numbers."""
    User = apps.get_model("users", "User")
    User.objects.filter(mobile__isnull=True).update(mobile="")


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_impersonationsession"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="mobile",
            field=phonenumber_field.modelfields.PhoneNumberField(
                blank=True, max_length=128, null=True, region=None, unique=True
            ),
        ),
        migrations.RunPython(empty_mobiles_to_null, null_mobiles_to_empty),
    ]
