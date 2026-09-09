"""Add BrandingSettings.product_name for white-label deployments.

The UI hardcoded "Jina Connect" in page titles and transactional copy.
This field lets a deployment set its own product name; blank falls back to
``settings.DEFAULT_PRODUCT_NAME`` via ``BrandingSettings.effective_product_name``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0019_rbac_add_voice_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="brandingsettings",
            name="product_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Product name shown in the UI (e.g. page titles). Blank uses the deployment default.",
                max_length=100,
            ),
        ),
    ]
