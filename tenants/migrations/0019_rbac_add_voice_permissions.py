"""B2 (#182): seed voice.* RBAC permission keys for existing tenants.

Adds the voice configuration / call / template / consent / rate-card
keys to every existing TenantRole's RolePermission table. New tenants
pick them up automatically via ``seed_default_roles``.

Reverse migration deletes the rows so the down-path is clean.
"""

from django.db import migrations

# Per role slug → permission key → granted?
VOICE_PERMISSION_GRANTS: dict[str, dict[str, bool]] = {
    # Configuration / provider
    "voice.provider.view": {"owner": True, "admin": True, "manager": True},
    "voice.provider.create": {"owner": True, "admin": True},
    "voice.provider.edit": {"owner": True, "admin": True},
    "voice.provider.delete": {"owner": True, "admin": True},
    "voice.config.view": {"owner": True, "admin": True, "manager": True},
    "voice.config.edit": {"owner": True, "admin": True},
    # Calls
    "voice.call.view": {
        "owner": True,
        "admin": True,
        "manager": True,
        "agent": True,
        "viewer": True,
    },
    "voice.call.initiate": {
        "owner": True,
        "admin": True,
        "manager": True,
        "agent": True,
    },
    "voice.call.recording.play": {
        "owner": True,
        "admin": True,
        "manager": True,
        "agent": True,
        "viewer": True,
    },
    "voice.call.recording.download": {
        "owner": True,
        "admin": True,
        "manager": True,
    },
    # Templates (reserved — UI ships later)
    "voice.template.view": {"owner": True, "admin": True},
    "voice.template.create": {"owner": True, "admin": True},
    "voice.template.edit": {"owner": True, "admin": True},
    "voice.template.delete": {"owner": True, "admin": True},
    # Recording-consent records
    "voice.consent.view": {"owner": True, "admin": True},
    "voice.consent.edit": {"owner": True, "admin": True},
    # Rate cards
    "voice.rate_card.view": {"owner": True, "admin": True},
    "voice.rate_card.edit": {"owner": True, "admin": True},
}


def seed_voice_permissions(apps, schema_editor):
    TenantRole = apps.get_model("tenants", "TenantRole")
    RolePermission = apps.get_model("tenants", "RolePermission")

    for role in TenantRole.objects.all().iterator():
        for perm_key, grants in VOICE_PERMISSION_GRANTS.items():
            allowed = grants.get(role.slug, False)
            RolePermission.objects.get_or_create(
                role=role,
                permission=perm_key,
                defaults={"allowed": allowed},
            )


def remove_voice_permissions(apps, schema_editor):
    RolePermission = apps.get_model("tenants", "RolePermission")
    RolePermission.objects.filter(permission__in=list(VOICE_PERMISSION_GRANTS)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0018_tenantvoiceapp_recording_requires_consent"),
    ]

    operations = [
        migrations.RunPython(seed_voice_permissions, remove_voice_permissions),
    ]
