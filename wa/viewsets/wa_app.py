"""
WAApp ViewSet - BSP Agnostic WhatsApp App Configuration

Provides CRUD operations for WhatsApp Business Account configurations.
Frontend uses this to manage connected WhatsApp accounts.
"""

from abstract.viewsets.base import BaseTenantModelViewSet
from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from wa.models import BSPChoices, WAApp
from wa.serializers import WAAppListSerializer, WAAppSafeSerializer, WAAppSerializer


class WAAppFilter(filters.FilterSet):
    """Filter for WAApp listing."""
    
    phone_number = filters.CharFilter(field_name='wa_number', lookup_expr='icontains')
    bsp = filters.ChoiceFilter(choices=BSPChoices.choices)
    is_active = filters.BooleanFilter()
    is_verified = filters.BooleanFilter()
    tenant = filters.NumberFilter(field_name='tenant__id')
    
    class Meta:
        model = WAApp
        fields = ['phone_number', 'bsp', 'is_active', 'is_verified', 'tenant']


class WAAppViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing WhatsApp Business App configurations.
    
    Provides endpoints to:
    - List all connected WhatsApp accounts for the tenant
    - Create new WhatsApp account connections
    - Retrieve, update, and manage individual accounts
    - Check quota and verification status
    
    All operations are tenant-scoped based on authenticated user.
    """
    
    queryset = WAApp.objects.all()
    serializer_class = WAAppSerializer
    filterset_class = WAAppFilter
    search_fields = ['app_name', 'wa_number', 'waba_id']
    ordering_fields = ['created_at', 'updated_at', 'name', 'phone_number']
    ordering = ['-created_at']
    http_method_names = ["get", "post", "patch", "delete"]
    required_permissions = {
        "list": "wa_app.view",
        "retrieve": "wa_app.view",
        "create": "wa_app.manage",
        "partial_update": "wa_app.manage",
        "destroy": "wa_app.delete",
        "quota": "wa_app.view",
        "reset_counter": "wa_app.manage",
        "capabilities": "wa_app.view",
        "default": "wa_app.view",
    }
    
    def get_serializer_class(self):
        """
        #251: ADMIN/OWNER (priority >= 80) get full BSP identifiers.
        MANAGER and below get WAAppSafeSerializer (no app_id, waba_id, phone_number_id).
        List action uses WAAppListSerializer for all roles (already minimal).
        """
        if self.action == 'list':
            return WAAppListSerializer
        tu = self._get_tenant_user()
        if tu and tu.role and tu.role.priority >= 80:
            return WAAppSerializer
        return WAAppSafeSerializer
    
    @swagger_auto_schema(
        operation_description="List all WhatsApp Business Apps for the current tenant",
        operation_summary="List WA Apps",
        operation_id="list_wa_apps",
        tags=["WhatsApp Apps (v2)"],
        manual_parameters=[
            openapi.Parameter(
                'phone_number',
                openapi.IN_QUERY,
                description="Filter by phone number (partial match)",
                type=openapi.TYPE_STRING,
                required=False
            ),
            openapi.Parameter(
                'bsp',
                openapi.IN_QUERY,
                description="Filter by Business Solution Provider",
                type=openapi.TYPE_STRING,
                enum=['META', 'GUPSHUP', 'TWILIO', 'MESSAGEBIRD'],
                required=False
            ),
            openapi.Parameter(
                'is_active',
                openapi.IN_QUERY,
                description="Filter by active status",
                type=openapi.TYPE_BOOLEAN,
                required=False
            ),
            openapi.Parameter(
                'is_verified',
                openapi.IN_QUERY,
                description="Filter by verification status",
                type=openapi.TYPE_BOOLEAN,
                required=False
            ),
            openapi.Parameter(
                'search',
                openapi.IN_QUERY,
                description="Search in name, phone_number, waba_id",
                type=openapi.TYPE_STRING,
                required=False
            ),
            openapi.Parameter(
                'ordering',
                openapi.IN_QUERY,
                description="Order results by field (prefix with - for descending)",
                type=openapi.TYPE_STRING,
                enum=['created_at', '-created_at', 'name', '-name', 'phone_number', '-phone_number'],
                required=False
            ),
        ],
        responses={
            200: openapi.Response(
                description="List of WhatsApp Apps",
                schema=WAAppListSerializer(many=True)
            ),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Permission denied")
        }
    )
    def list(self, request, *args, **kwargs):
        """List all WhatsApp Business Apps for the current tenant."""
        return super().list(request, *args, **kwargs)
    
    @swagger_auto_schema(
        operation_description="Create a new WhatsApp Business App connection",
        operation_summary="Create WA App",
        operation_id="create_wa_app",
        tags=["WhatsApp Apps (v2)"],
        request_body=WAAppSerializer,
        responses={
            201: openapi.Response(description="WA App created successfully", schema=WAAppSerializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
        }
    )
    def create(self, request, *args, **kwargs):
        """Create a new WhatsApp Business App connection."""
        return super().create(request, *args, **kwargs)
    
    @swagger_auto_schema(
        operation_description="Retrieve a specific WhatsApp Business App by ID",
        operation_summary="Get WA App",
        operation_id="retrieve_wa_app",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(description="WA App details", schema=WAAppSerializer()),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found")
        }
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific WhatsApp Business App by ID."""
        return super().retrieve(request, *args, **kwargs)
    
    @swagger_auto_schema(
        operation_description="Partially update a WhatsApp Business App",
        operation_summary="Update WA App",
        operation_id="partial_update_wa_app",
        tags=["WhatsApp Apps (v2)"],
        request_body=WAAppSerializer,
        responses={
            200: openapi.Response(description="WA App updated successfully", schema=WAAppSerializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found")
        }
    )
    def partial_update(self, request, *args, **kwargs):
        """Partially update a WhatsApp Business App."""
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description=(
            "Delete a WhatsApp Business App connection. "
            "This is a destructive operation restricted to OWNER only."
        ),
        operation_summary="Delete WA App",
        operation_id="delete_wa_app",
        tags=["WhatsApp Apps (v2)"],
        responses={
            204: openapi.Response(description="WA App deleted successfully"),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Only OWNER can delete WA apps"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a WhatsApp Business App (OWNER only via wa_app.delete)."""
        return super().destroy(request, *args, **kwargs)
    
    @swagger_auto_schema(
        operation_description="Get messaging quota information for a WA App",
        operation_summary="Get WA App Quota",
        operation_id="get_wa_app_quota",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(
                description="Quota information",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'daily_limit': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'messages_sent_today': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'remaining_quota': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'tier': openapi.Schema(type=openapi.TYPE_STRING),
                    }
                )
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found")
        }
    )
    @action(detail=True, methods=['get'], url_path='quota')
    def quota(self, request, pk=None):
        """Get messaging quota information for a WA App."""
        wa_app = self.get_object()
        return Response({
            'daily_limit': wa_app.daily_limit,
            'messages_sent_today': wa_app.messages_sent_today,
            'remaining_quota': max(0, wa_app.daily_limit - wa_app.messages_sent_today),
            'tier': wa_app.tier,
        })
    
    @swagger_auto_schema(
        operation_description="Reset daily message counter (admin only)",
        operation_summary="Reset Daily Counter",
        operation_id="reset_wa_app_counter",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(description="Counter reset successfully"),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Admin access required"),
            404: openapi.Response(description="WA App not found")
        }
    )
    @action(detail=True, methods=['post'], url_path='reset-counter')
    def reset_counter(self, request, pk=None):
        """Reset daily message counter."""
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        wa_app = self.get_object()
        wa_app.messages_sent_today = 0
        wa_app.save(update_fields=['messages_sent_today'])
        
        return Response({'message': 'Daily counter reset successfully'})

    @swagger_auto_schema(
        operation_description=(
            "Return the set of capabilities supported by this WA App's BSP adapter. "
            "Capabilities are strings like 'templates', 'subscriptions', 'media_upload'."
        ),
        operation_summary="Get BSP Capabilities",
        operation_id="get_wa_app_capabilities",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(
                description="BSP capabilities",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'provider': openapi.Schema(type=openapi.TYPE_STRING),
                        'capabilities': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_STRING),
                        ),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    @action(detail=True, methods=['get'], url_path='capabilities')
    def capabilities(self, request, pk=None):
        """Return the capability set for this app's BSP adapter."""
        from wa.adapters import get_bsp_adapter

        wa_app = self.get_object()
        adapter = get_bsp_adapter(wa_app)

        return Response({
            'provider': adapter.PROVIDER_NAME,
            'capabilities': sorted(adapter.CAPABILITIES),
        })
