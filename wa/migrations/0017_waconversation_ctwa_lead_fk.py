# #189 + #194: add WaConversation.ctwa_lead FK now that ctwa.CtwaLead
# exists. Split from 0016 to avoid a circular dep between wa.0016 and
# ctwa.0001_initial — ctwa.CtwaLead has an FK to wa.WaConversation
# (defined in 0016), and this migration completes the loop.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wa", "0016_waconversation_and_wamessage_referrals"),
        ("ctwa", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="waconversation",
            name="ctwa_lead",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="wa_conversations",
                to="ctwa.ctwalead",
            ),
        ),
    ]
