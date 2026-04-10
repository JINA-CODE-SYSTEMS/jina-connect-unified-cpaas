from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets.branding_settings import BrandingSettingsViewSet
from .viewsets.host_wallet_balance import HostWalletViewSet
from .viewsets.member_management import MemberManagementViewSet
from .viewsets.onboarding_options import OnboardingOptionsViewSet
from .viewsets.role_management import RoleManagementViewSet
from .viewsets.tenant_gupshup import TenantGupshupAppsViewSet
from .viewsets.tenant_media import TenantMediaViewSet
from .viewsets.tenant_users import TenantUserViewSet
from .viewsets.tenants import TenantViewSet
from .viewsets.tenants_tags import TenantTagsViewSet
from .viewsets.waba_info import WABAInfoViewSet

router = DefaultRouter()

router.register(r'onboarding-options', OnboardingOptionsViewSet, basename='onboarding-options')
router.register(r'tenant-gupshup', TenantGupshupAppsViewSet, basename='tenantgupshup')
router.register(r'host-wallet', HostWalletViewSet, basename='hostwallet')
router.register(r'tenant-media', TenantMediaViewSet, basename='tenantmedia')
router.register(r'tenant-tags', TenantTagsViewSet, basename='tenanttags')
router.register(r'waba-info', WABAInfoViewSet, basename='wabainfo')
router.register(r'users', TenantUserViewSet, basename='tenantuser')
router.register(r'branding', BrandingSettingsViewSet, basename='branding')
router.register(r'members', MemberManagementViewSet, basename='member')
router.register(r'roles', RoleManagementViewSet, basename='role')
router.register(r'', TenantViewSet, basename='tenant')


urlpatterns = [
    path("", include(router.urls)),
]

