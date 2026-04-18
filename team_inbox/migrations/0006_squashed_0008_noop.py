"""
Squashed replacement for 0006 → 0008 (#127).

Those three migrations created and then immediately removed the
Conversation model and its Messages FK.  The net effect is a no-op,
so this squash contains zero operations.

On databases that already applied 0006-0008, Django marks this squash
as applied automatically via ``replaces``.  On fresh databases it does
nothing — which is the correct outcome.
"""

from django.db import migrations


class Migration(migrations.Migration):
    replaces = [
        ("team_inbox", "0006_conversation_messages_conversation"),
        (
            "team_inbox",
            "0007_rename_team_inbox_conv_tenant_contact_team_inbox__tenant__ef00a5_idx_and_more",
        ),
        ("team_inbox", "0008_remove_conversation_model"),
    ]

    dependencies = [
        ("team_inbox", "0005_messages_edited_at"),
        ("tenants", "0016_initial"),
        ("contacts", "0008_tenantcontact_rcs_capable_rcs_checked_at"),
    ]

    operations = []
