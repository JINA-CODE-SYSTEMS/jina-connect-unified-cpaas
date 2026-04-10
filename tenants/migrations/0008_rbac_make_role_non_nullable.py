"""
Make TenantUser.role non-nullable.

Migration 0006 already back-filled every existing TenantUser with the OWNER
role, so no NULL rows should exist.  The RunPython guard assigns the tenant's
default AGENT role to any that slipped through (e.g. via the access-key login
path before the fix in #253).
"""

from django.db import migrations, models
import django.db.models.deletion


def backfill_null_roles(apps, schema_editor):
    """Assign the tenant's 'agent' role to any TenantUser still without a role.
    Falls back to the lowest-priority role if 'agent' is missing, and raises
    if the tenant has no roles at all (corrupted data).
    """
    TenantUser = apps.get_model("tenants", "TenantUser")
    TenantRole = apps.get_model("tenants", "TenantRole")

    orphans = TenantUser.objects.filter(role__isnull=True).select_related("tenant")
    for tu in orphans.iterator():
        # Prefer the "agent" role; fall back to lowest-priority role
        fallback_role = (
            TenantRole.objects.filter(tenant=tu.tenant, slug="agent").first()
            or TenantRole.objects.filter(tenant=tu.tenant)
            .order_by("priority")
            .first()
        )
        if fallback_role is None:
            raise Exception(
                f"Tenant '{tu.tenant}' (pk={tu.tenant_id}) has no roles. "
                f"Cannot backfill TenantUser pk={tu.pk}. "
                f"Run seed_default_roles() for this tenant first."
            )
        tu.role = fallback_role
        tu.save(update_fields=["role"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0007_add_history_to_role_permission_tenantuser"),
    ]

    operations = [
        # 1. Back-fill any remaining NULL roles
        migrations.RunPython(backfill_null_roles, migrations.RunPython.noop),
        # 2. Make the column non-nullable with PROTECT
        migrations.AlterField(
            model_name="tenantuser",
            name="role",
            field=models.ForeignKey(
                help_text="RBAC role for this user within the tenant",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="members",
                to="tenants.tenantrole",
            ),
        ),
        # 3. Also update the historical model to match
        migrations.AlterField(
            model_name="historicaltenantuser",
            name="role",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text="RBAC role for this user within the tenant",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="tenants.tenantrole",
            ),
        ),
    ]
