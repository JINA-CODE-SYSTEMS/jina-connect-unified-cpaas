from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from tenants.models import TenantAccessKey


class TenantAccessKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        access_key = request.headers.get("X-ACCESS-KEY") or request.headers.get("x-access-key")
        if not access_key:
            # Access key is optional - return None to allow other authenticators
            return (None, None)

        try:
            tenant_key = TenantAccessKey.objects.get(key=access_key)
        except TenantAccessKey.DoesNotExist:
            raise AuthenticationFailed("Invalid access key")

        # (user, auth) → here user=None, auth=tenant
        return (None, tenant_key.tenant)
