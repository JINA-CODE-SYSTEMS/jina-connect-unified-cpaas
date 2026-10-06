"""Link media templates to the header file they were created with.

Templates created from the web app kept only the header file's handle and its
seven-day signed URL, never the ``TenantMedia`` itself. Once the URL expired,
every broadcast of the template failed at Meta with 131053. Going forward
``WATemplate.link_header_media`` makes the link on create; this does it for
the templates already stuck. Same match: the tenant's non-card upload whose
handle equals the template's ``media_handle``.
"""

from django.db import migrations
from django.db.models import Q


def link_header_media(apps, schema_editor):
    WATemplate = apps.get_model("wa", "WATemplate")
    TenantMedia = apps.get_model("tenants", "TenantMedia")

    orphans = (
        WATemplate.objects.filter(tenant_media__isnull=True, wa_app__isnull=False)
        .exclude(media_handle__isnull=True)
        .exclude(media_handle="")
        .select_related("wa_app")
    )
    for template in orphans.iterator():
        tm = (
            TenantMedia.objects.filter(tenant_id=template.wa_app.tenant_id, card_index__isnull=True)
            .filter(Q(wa_handle_id__handleId=template.media_handle) | Q(wa_handle_id=template.media_handle))
            .order_by("-created_at")
            .first()
        )
        if tm:
            template.tenant_media_id = tm.pk
            template.save(update_fields=["tenant_media"])


class Migration(migrations.Migration):
    dependencies = [
        ("wa", "0020_template_quality_rating"),
        ("tenants", "0032_wa_credential_reveal"),
    ]

    operations = [
        migrations.RunPython(link_header_media, migrations.RunPython.noop),
    ]
