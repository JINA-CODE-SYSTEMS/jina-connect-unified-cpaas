"""
Add new permission keys introduced by RBAC-17 (#254):
  - wa_app.delete   (OWNER only)
  - webhook.view    (ADMIN/OWNER only)
  - webhook.manage  (ADMIN/OWNER only)

For every existing tenant, this creates the missing RolePermission rows
based on DEFAULT_ROLE_PERMISSIONS.
"""

from django.db import migrations

# New keys and which roles should have them (True = granted)
NEW_PERMISSION_GRANTS = {
    "wa_app.delete": {
        "owner": True,
    },
    "webhook.view": {
        "owner": True,
        "admin": True,
    },
    "webhook.manage": {
        "owner": True,
        "admin": True,
    },
}


def seed_new_permissions(apps, schema_editor):
    """Create RolePermission rows for the 3 new keys on all existing tenants."""
    TenantRole = apps.get_model("tenants", "TenantRole")
    RolePermission = apps.get_model("tenants", "RolePermission")

    for role in TenantRole.objects.all().iterator():
        for perm_key, grants in NEW_PERMISSION_GRANTS.items():
            allowed = grants.get(role.slug, False)
            RolePermission.objects.get_or_create(
                role=role,
                permission=perm_key,
                defaults={"allowed": allowed},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0008_rbac_make_role_non_nullable"),
    ]

    operations = [
        migrations.RunPython(seed_new_permissions, migrations.RunPython.noop),
    ]
