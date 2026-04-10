"""
Viewset for BrandingSettings - Admin only access.

Provides endpoints to manage branding assets:
- Favicon: PNG 583x583 px
- Primary Logo: SVG 854x262 px (aspect ratio 3.26:1)
- Secondary Logo: SVG 532x380 px (aspect ratio 1.4:1)
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from tenants.models import BrandingSettings
from tenants.serializers import BrandingSettingsSerializer
from tenants.permission_classes import TenantRolePermission
from typing_extensions import override


class BrandingSettingsViewSet(viewsets.ViewSet):
    """
    Admin-only viewset for managing branding settings.
    
    This is a singleton resource - there's only one BrandingSettings instance.
    
    Endpoints:
    - GET /branding/ - Get current branding settings
    - PUT /branding/ - Update branding settings (full update)
    - PATCH /branding/ - Partial update branding settings
    - DELETE /branding/favicon/ - Remove favicon
    - DELETE /branding/primary-logo/ - Remove primary logo
    - DELETE /branding/secondary-logo/ - Remove secondary logo
    """
    
    permission_classes = [IsAdminUser, TenantRolePermission]
    parser_classes = [MultiPartParser, FormParser]
    required_permissions = {
        "list": "tenant.view",
        "retrieve": "tenant.view",
        "create": "tenant.edit",
        "update": "tenant.edit",
        "partial_update": "tenant.edit",
        "delete_favicon": "tenant.edit",
        "delete_primary_logo": "tenant.edit",
        "delete_secondary_logo": "tenant.edit",
        "default": "tenant.view",
    }
    
    def get_instance(self):
        """Get or create the singleton BrandingSettings instance."""
        return BrandingSettings.get_instance()
    
    
    def list(self, request):
        """
        GET /branding/
        Get current branding settings.
        """
        instance = self.get_instance()
        serializer = BrandingSettingsSerializer(instance, context={'request': request})
        return Response(serializer.data)
    
    def create(self, request):
        """
        POST /branding/
        Create or update branding settings (same as PUT for singleton).
        """
        return self.update_settings(request, partial=False)
    
    def update(self, request, pk=None):
        """
        PUT /branding/{pk}/
        Full update of branding settings.
        """
        return self.update_settings(request, partial=False)
    
    def partial_update(self, request, pk=None):
        """
        PATCH /branding/{pk}/
        Partial update of branding settings.
        """
        return self.update_settings(request, partial=True)
    
    def update_settings(self, request, partial=False):
        """Helper method to update branding settings."""
        instance = self.get_instance()
        serializer = BrandingSettingsSerializer(
            instance, 
            data=request.data, 
            partial=partial,
            context={'request': request}
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['delete'], url_path='favicon')
    def delete_favicon(self, request):
        """
        DELETE /branding/favicon/
        Remove the favicon file and URL.
        """
        instance = self.get_instance()
        if instance.favicon:
            instance.favicon.delete(save=False)
        instance.favicon_url = None
        instance.save()
        
        serializer = BrandingSettingsSerializer(instance, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['delete'], url_path='primary-logo')
    def delete_primary_logo(self, request):
        """
        DELETE /branding/primary-logo/
        Remove the primary logo file and URL.
        """
        instance = self.get_instance()
        if instance.primary_logo:
            instance.primary_logo.delete(save=False)
        instance.primary_logo_url = None
        instance.save()
        
        serializer = BrandingSettingsSerializer(instance, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['delete'], url_path='secondary-logo')
    def delete_secondary_logo(self, request):
        """
        DELETE /branding/secondary-logo/
        Remove the secondary logo file and URL.
        """
        instance = self.get_instance()
        if instance.secondary_logo:
            instance.secondary_logo.delete(save=False)
        instance.secondary_logo_url = None
        instance.save()
        
        serializer = BrandingSettingsSerializer(instance, context={'request': request})
        return Response(serializer.data)

    @override
    def get_permissions(self):
        """Set permissions for the viewset."""
        if self.action in ['list', 'retrieve']:
            permission_classes = [AllowAny]
            return [permission() for permission in permission_classes]
        return super().get_permissions()