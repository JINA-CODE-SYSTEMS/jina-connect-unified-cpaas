"""
Permission Registry for RBAC.

Defines all known permission keys, default role→permission mappings,
and helper functions for permission checks and role seeding.

Reference: docs/PRD_RBAC.md — Sections 3.2, 4.1.4
"""

# ---------------------------------------------------------------------------
# All known permission keys in the system, grouped by module.
# ---------------------------------------------------------------------------

ALL_PERMISSIONS = [
    # Tenant settings
    "tenant.view",
    "tenant.edit",
    "tenant.delete",
    "tenant.transfer",
    # User management
    "users.view",
    "users.invite",
    "users.change_role",
    "users.remove",
    # Billing & Wallet
    "billing.view",
    "billing.manage",
    # WA App configuration
    "wa_app.view",
    "wa_app.manage",
    "wa_app.delete",
    "rate_card.manage",
    # Webhook events (raw payloads)
    "webhook.view",
    "webhook.manage",
    # Templates
    "template.view",
    "template.create",
    "template.edit",
    "template.delete",
    "template.submit",
    # Broadcasts
    "broadcast.view",
    "broadcast.create",
    "broadcast.cancel",
    "broadcast.charge_breakdown",
    # Contacts
    "contact.view",
    "contact.create",
    "contact.edit",
    "contact.delete",
    "contact.import",
    "contact.export",
    # Team Inbox
    "inbox.view",
    "inbox.reply",
    "inbox.assign",
    "inbox.resolve",
    # ChatFlow
    "chatflow.view",
    "chatflow.create",
    "chatflow.edit",
    "chatflow.delete",
    "chatflow.toggle",
    # Products
    "product.view",
    "product.manage",
    # Analytics
    "analytics.view",
]

# ---------------------------------------------------------------------------
# Human-readable descriptions for each permission key.
# Used by the GET /tenants/roles/permissions-catalog/ endpoint.
# ---------------------------------------------------------------------------

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "tenant.view": "View tenant settings and details",
    "tenant.edit": "Edit tenant settings",
    "tenant.delete": "Delete the tenant organization",
    "tenant.transfer": "Transfer ownership to another user",
    "users.view": "View team members and their roles",
    "users.invite": "Invite new members to the tenant",
    "users.change_role": "Change members' roles and manage roles",
    "users.remove": "Remove members from the tenant",
    "billing.view": "View billing and wallet information",
    "billing.manage": "Manage billing settings and wallet",
    "wa_app.view": "View WhatsApp app configuration",
    "wa_app.manage": "Manage WhatsApp app settings",
    "wa_app.delete": "Delete WhatsApp app connections",
    "rate_card.manage": "Manage message rate cards",
    "webhook.view": "View raw webhook event payloads",
    "webhook.manage": "Retry and manage webhook events",
    "template.view": "View message templates",
    "template.create": "Create new message templates",
    "template.edit": "Edit existing message templates",
    "template.delete": "Delete message templates",
    "template.submit": "Submit templates for WhatsApp approval",
    "broadcast.view": "View broadcast campaigns",
    "broadcast.create": "Create and send broadcast campaigns",
    "broadcast.cancel": "Cancel scheduled broadcasts",
    "broadcast.charge_breakdown": "View broadcast charge breakdowns",
    "contact.view": "View contacts",
    "contact.create": "Create new contacts",
    "contact.edit": "Edit contact details",
    "contact.delete": "Delete contacts",
    "contact.import": "Import contacts from files",
    "contact.export": "Export contacts to files",
    "inbox.view": "View team inbox conversations",
    "inbox.reply": "Reply to inbox conversations",
    "inbox.assign": "Assign conversations to team members",
    "inbox.resolve": "Resolve inbox conversations",
    "chatflow.view": "View chatflow automations",
    "chatflow.create": "Create new chatflow automations",
    "chatflow.edit": "Edit existing chatflow automations",
    "chatflow.delete": "Delete chatflow automations",
    "chatflow.toggle": "Enable or disable chatflow automations",
    "product.view": "View product catalog",
    "product.manage": "Manage products and catalog",
    "analytics.view": "View analytics and reports",
}

# ---------------------------------------------------------------------------
# Default permission grants per system role.
# True  = granted
# False = explicitly denied
# Absent key = denied (same as False)
#
# Matches PRD Section 3.2 permission matrix.
# ---------------------------------------------------------------------------

DEFAULT_ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    # ── OWNER: full control ──────────────────────────────────────────────
    "owner": {p: True for p in ALL_PERMISSIONS},
    # ── ADMIN: everything except ownership ops ───────────────────────────
    "admin": {
        **{p: True for p in ALL_PERMISSIONS},
        "tenant.delete": False,
        "tenant.transfer": False,
        "wa_app.delete": False,
    },
    # ── MANAGER: operational control ─────────────────────────────────────
    "manager": {
        "tenant.view": True,
        "users.view": True,
        "billing.view": True,
        "wa_app.view": True,
        "template.view": True,
        "template.create": True,
        "template.edit": True,
        "template.delete": True,
        "template.submit": True,
        "broadcast.view": True,
        "broadcast.create": True,
        "broadcast.cancel": True,
        "broadcast.charge_breakdown": True,
        "contact.view": True,
        "contact.create": True,
        "contact.edit": True,
        "contact.delete": True,
        "contact.import": True,
        "contact.export": True,
        "inbox.view": True,
        "inbox.reply": True,
        "inbox.assign": True,
        "inbox.resolve": True,
        "chatflow.view": True,
        "chatflow.create": True,
        "chatflow.edit": True,
        "chatflow.delete": True,
        "chatflow.toggle": True,
        "product.view": True,
        "product.manage": True,
        "analytics.view": True,
    },
    "agent": {
        "tenant.view": True,
        "users.view": True,
        "wa_app.view": True,
        "template.view": True,
        "broadcast.view": True,
        "contact.view": True,
        "inbox.view": True,
        "inbox.reply": True,
        "chatflow.view": True,
        "product.view": True,
        "analytics.view": True,
    },
    # ── VIEWER: read-only ────────────────────────────────────────────────
    "viewer": {
        "tenant.view": True,
        "users.view": True,
        "billing.view": True,
        "wa_app.view": True,
        "template.view": True,
        "broadcast.view": True,
        "contact.view": True,
        "inbox.view": True,
        "chatflow.view": True,
        "product.view": True,
        "analytics.view": True,
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def has_permission(tenant_role, permission: str) -> bool:
    """
    Check whether *tenant_role* grants *permission*.

    Performs a DB lookup against the RolePermission table.  Returns False
    when no explicit row exists (deny-by-default).
    """
    from tenants.models import RolePermission

    perm = RolePermission.objects.filter(role=tenant_role, permission=permission).first()
    if perm is not None:
        return perm.allowed
    # No explicit record → deny
    return False


def seed_default_roles(tenant):
    """
    Create the 5 default TenantRole records and their RolePermission rows
    for *tenant*.

    Idempotent — safe to call multiple times (uses ``get_or_create``).
    Typically called from a ``post_save`` signal on Tenant or via a
    management command for back-filling existing tenants.
    """
    from tenants.models import DefaultRoleSlugs, RolePermission, TenantRole

    PRIORITY_MAP = {
        "owner": 100,
        "admin": 80,
        "manager": 60,
        "agent": 40,
        "viewer": 20,
    }
    DISPLAY_NAMES = {
        "owner": "Owner",
        "admin": "Admin",
        "manager": "Manager",
        "agent": "Agent",
        "viewer": "Viewer",
    }

    for slug in DefaultRoleSlugs.values:
        role, _ = TenantRole.objects.get_or_create(
            tenant=tenant,
            slug=slug,
            defaults={
                "name": DISPLAY_NAMES[slug],
                "priority": PRIORITY_MAP[slug],
                "is_system": True,
                "is_editable": slug != "owner",  # OWNER perms are locked
            },
        )
        default_perms = DEFAULT_ROLE_PERMISSIONS.get(slug, {})
        for perm_key in ALL_PERMISSIONS:
            RolePermission.objects.get_or_create(
                role=role,
                permission=perm_key,
                defaults={"allowed": default_perms.get(perm_key, False)},
            )
