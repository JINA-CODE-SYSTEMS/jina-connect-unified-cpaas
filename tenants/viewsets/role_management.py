"""
Role Management ViewSet (RBAC-10).

Provides CRUD endpoints for tenant roles, a permissions catalog,
and a reset-to-defaults action at /tenants/roles/.
"""

from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import RolePermission, TenantRole
from tenants.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
)
from tenants.rbac_validators import check_target_priority, get_requester_tenant_user
from tenants.serializers import (
    CreateRoleSerializer,
    RoleDetailSerializer,
    UpdateRoleSerializer,
)


class RoleManagementViewSet(BaseTenantModelViewSet):
    """
    Manage tenant roles: list, create, detail, edit, delete.

    Endpoints:
        GET    /tenants/roles/                        → list all roles
        POST   /tenants/roles/                        → create custom role
        GET    /tenants/roles/{id}/                    → role detail + permissions
        PATCH  /tenants/roles/{id}/                    → edit role / permissions
        DELETE /tenants/roles/{id}/                    → delete custom role
        GET    /tenants/roles/permissions-catalog/     → available permission keys
        POST   /tenants/roles/{id}/reset/              → reset default role perms
    """

    queryset = TenantRole.objects.all()
    serializer_class = RoleDetailSerializer
    http_method_names = ["get", "post", "patch", "delete"]
    search_fields = ["name", "slug"]
    required_permissions = {
        "list": "users.view",
        "retrieve": "users.view",
        "create": "users.change_role",
        "partial_update": "users.change_role",
        "destroy": "users.change_role",
        "permissions_catalog": "users.view",
        "reset_permissions": "users.change_role",
        "default": "users.view",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        return (
            qs.select_related("tenant")
            .prefetch_related("permissions")
            .annotate(member_count=Count("members", filter=Q(members__is_active=True)))
        )

    def get_serializer_class(self):
        if self.action == "create":
            return CreateRoleSerializer
        elif self.action == "partial_update":
            return UpdateRoleSerializer
        return RoleDetailSerializer

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        """Create a new custom role with optional permission grants."""
        requester_tu = get_requester_tenant_user(request)
        if not requester_tu:
            return Response(
                {"detail": "You are not an active member of any tenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CreateRoleSerializer(
            data=request.data,
            context={"request": request, "tenant": requester_tu.tenant},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        tenant = requester_tu.tenant
        role = TenantRole.objects.create(
            tenant=tenant,
            name=data["name"],
            slug=data["slug"],
            description=data.get("description", ""),
            priority=data["priority"],
            is_system=False,
            is_editable=True,
            created_by=request.user,
        )

        # Create permission rows for all known keys
        permissions = data.get("permissions", {})
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    role=role,
                    permission=perm_key,
                    allowed=permissions.get(perm_key, False),
                )
                for perm_key in ALL_PERMISSIONS
            ]
        )

        # Re-fetch with annotations for response
        role = self.get_queryset().get(pk=role.pk)
        return Response(
            RoleDetailSerializer(role).data,
            status=status.HTTP_201_CREATED,
        )

    # ------------------------------------------------------------------
    # Partial Update
    # ------------------------------------------------------------------
    def partial_update(self, request, *args, **kwargs):
        """Edit a role's name, priority, or permissions."""
        role = self.get_object()

        if not role.is_editable:
            return Response(
                {"detail": "This role cannot be edited."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requester_tu = get_requester_tenant_user(request)
        if not requester_tu:
            return Response(
                {"detail": "You are not an active member of any tenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Cannot edit a role with priority >= your own
        err = check_target_priority(requester_tu, role, action_verb="edit")
        if err:
            return Response({"detail": err}, status=status.HTTP_403_FORBIDDEN)

        serializer = UpdateRoleSerializer(
            data=request.data,
            context={"request": request, "tenant": requester_tu.tenant},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Update role fields
        update_fields = ["updated_by", "updated_at"]
        if "name" in data:
            role.name = data["name"]
            update_fields.append("name")
        if "description" in data:
            role.description = data["description"]
            update_fields.append("description")
        if "priority" in data:
            role.priority = data["priority"]
            update_fields.append("priority")
        role.updated_by = request.user
        role.save(update_fields=update_fields)

        # Update permissions (only specified keys)
        if "permissions" in data:
            for perm_key, allowed in data["permissions"].items():
                RolePermission.objects.update_or_create(
                    role=role,
                    permission=perm_key,
                    defaults={"allowed": allowed},
                )

        # Re-fetch with annotations
        role = self.get_queryset().get(pk=role.pk)
        return Response(RoleDetailSerializer(role).data)

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------
    def destroy(self, request, *args, **kwargs):
        """Delete a custom role. System roles cannot be deleted."""
        role = self.get_object()

        if role.is_system:
            return Response(
                {"detail": "System default roles cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot delete a role with priority >= your own
        requester_tu = get_requester_tenant_user(request)
        if requester_tu and requester_tu.role:
            err = check_target_priority(requester_tu, role, action_verb="delete")
            if err:
                return Response({"detail": err}, status=status.HTTP_403_FORBIDDEN)

        # PROTECT prevents deletion if ANY members reference this role,
        # so we must check total count (not just active).
        total_members = role.members.count()
        if total_members > 0:
            active = role.members.filter(is_active=True).count()
            inactive = total_members - active
            parts = []
            if active:
                parts.append(f"{active} active")
            if inactive:
                parts.append(f"{inactive} inactive")
            detail = f"Cannot delete role with {' and '.join(parts)} member(s). Reassign them first."
            return Response(
                {
                    "detail": detail,
                    "total_member_count": total_members,
                    "active_member_count": active,
                },
                status=status.HTTP_409_CONFLICT,
            )

        role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Permissions Catalog
    # ------------------------------------------------------------------
    @action(
        detail=False,
        methods=["get"],
        url_path="permissions-catalog",
        url_name="permissions-catalog",
    )
    def permissions_catalog(self, request):
        """Return all available permission keys with descriptions, grouped by module."""
        from collections import defaultdict

        grouped = defaultdict(list)
        for perm in ALL_PERMISSIONS:
            group = perm.split(".")[0]
            grouped[group].append(
                {
                    "key": perm,
                    "label": PERMISSION_DESCRIPTIONS.get(perm, ""),
                }
            )
        return Response(dict(grouped))

    # ------------------------------------------------------------------
    # Reset Defaults
    # ------------------------------------------------------------------
    @action(
        detail=True,
        methods=["post"],
        url_path="reset",
        url_name="reset-permissions",
    )
    def reset_permissions(self, request, pk=None):
        """Reset a system role's permissions to the built-in defaults."""
        role = self.get_object()

        if not role.is_system:
            return Response(
                {"detail": "Only system default roles can be reset to defaults."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # OWNER role reset is a no-op — permissions are locked
        if role.slug == "owner":
            role = self.get_queryset().get(pk=role.pk)
            return Response(RoleDetailSerializer(role).data)

        # Delete all existing permission rows, then re-seed from defaults
        role.permissions.all().delete()
        default_perms = DEFAULT_ROLE_PERMISSIONS.get(role.slug, {})
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    role=role,
                    permission=perm_key,
                    allowed=default_perms.get(perm_key, False),
                )
                for perm_key in ALL_PERMISSIONS
            ]
        )

        # Re-fetch with annotations
        role = self.get_queryset().get(pk=role.pk)
        return Response(RoleDetailSerializer(role).data)
