"""Add BrandingSettings.primary_color for white-label deployments.

The web app derives its whole brand ramp (--color-brand-25 through -950)
from this single hex value. Blank falls back to ``settings.DEFAULT_BRAND_COLOR``
via ``BrandingSettings.effective_primary_color``.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0020_brandingsettings_product_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="brandingsettings",
            name="primary_color",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Primary brand colour, e.g. #465fff. Blank uses the deployment default.",
                max_length=7,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a hex colour such as #465fff.",
                        regex="^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                    )
                ],
            ),
        ),
    ]
