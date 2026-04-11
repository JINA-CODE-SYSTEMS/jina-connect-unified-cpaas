"""
Data migration: backfill UserChatFlowSession.tenant from flow.tenant
for all existing rows where tenant is NULL.
"""

from django.db import migrations


def backfill_tenant(apps, schema_editor):
    """Set session.tenant = session.flow.tenant for all rows with NULL tenant."""
    # Raw SQL is the most efficient way to update potentially many rows
    schema_editor.execute(
        """
        UPDATE chat_flow_userchatflowsession s
        SET tenant_id = f.tenant_id
        FROM chat_flow_chatflow f
        WHERE s.flow_id = f.id
          AND s.tenant_id IS NULL
          AND f.tenant_id IS NOT NULL
        """
    )


def reverse_backfill(apps, schema_editor):
    """Reverse: set tenant back to NULL (not typically needed)."""
    schema_editor.execute("UPDATE chat_flow_userchatflowsession SET tenant_id = NULL")


class Migration(migrations.Migration):
    dependencies = [
        ("chat_flow", "0003_add_tenant_to_userchatflowsession"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE chat_flow_userchatflowsession s
                SET tenant_id = f.tenant_id
                FROM chat_flow_chatflow f
                WHERE s.flow_id = f.id
                  AND s.tenant_id IS NULL
                  AND f.tenant_id IS NOT NULL;
            """,
            reverse_sql="UPDATE chat_flow_userchatflowsession SET tenant_id = NULL;",
        ),
    ]
