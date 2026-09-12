from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from tenants.models import TenantAccessKey


def tenant_from_access_key(request):
    """Resolve ``X-ACCESS-KEY`` to a tenant, or None when the header is absent.

    Shared with the token endpoint, which needs the tenant before DRF has run
    an authenticator it could read ``request.auth`` from.
    """
    access_key = request.headers.get("X-ACCESS-KEY") or request.headers.get("x-access-key")
    if not access_key:
        # Access key is optional here - the caller decides what an absent one means
        return None

    tenant_key = TenantAccessKey.resolve(access_key)
    if tenant_key is None:
        # One message whether the key never existed, was revoked, or was
        # rotated away. Telling them apart would confirm to whoever holds a
        # stolen key that it was once real (#301).
        raise AuthenticationFailed("Invalid access key")

    return tenant_key.tenant


class TenantAccessKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        tenant = tenant_from_access_key(request)
        if tenant is None:
            # DRF reads None as "this authenticator has nothing to say", so the
            # next one gets a turn and request.user falls back to AnonymousUser.
            # The old (None, None) was a tuple, so DRF accepted it as a result
            # and set request.user to None outright — which left views reaching
            # for user attributes on None rather than on an anonymous user
            # (#301).
            return None

        # (user, auth) → here user=None, auth=tenant
        return (None, tenant)

    def authenticate_header(self, request):
        """Name the scheme, so a missing or bad key is a 401 and not a 403.

        DRF downgrades an authentication failure to 403 unless the first
        authenticator offers a WWW-Authenticate value — which made "no access
        key" indistinguishable from "not allowed" to any client trying to
        work out what it had done wrong.
        """
        return "X-ACCESS-KEY"
