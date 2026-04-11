"""
Enhanced WebSocket authentication with multiple security layers
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

User = get_user_model()
logger = logging.getLogger(__name__)


class WebSocketSecurityManager:
    """
    Enhanced security manager for WebSocket connections
    """

    def __init__(self):
        self.max_connections_per_user = getattr(settings, "WS_MAX_CONNECTIONS_PER_USER", 5)
        self.rate_limit_window = getattr(settings, "WS_RATE_LIMIT_WINDOW", 60)  # seconds
        self.max_requests_per_window = getattr(settings, "WS_MAX_REQUESTS_PER_WINDOW", 100)

    async def authenticate_websocket(self, scope, consumer_instance):
        """
        Multi-layer WebSocket authentication
        """
        # 1. Origin validation
        if not await self.validate_origin(scope):
            return None, "Invalid origin"

        # 2. JWT authentication
        user = await self.authenticate_jwt(scope)
        if not user:
            return None, "Invalid authentication"

        # 3. Connection limit check
        if not await self.check_connection_limits(user):
            return None, "Connection limit exceeded"

        # 4. Rate limiting
        if not await self.check_rate_limits(user, scope):
            return None, "Rate limit exceeded"

        # 5. Tenant access & role permission validation
        tenant_id = scope["url_route"]["kwargs"].get("tenant_id")
        if not await self.validate_tenant_access(user, tenant_id):
            return None, "Unauthorized tenant access — user lacks inbox.view permission"

        return user, None

    async def validate_origin(self, scope) -> bool:
        """
        Validate WebSocket origin (like CORS for WebSockets)
        """
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode()

        if not origin:
            # Allow connections without origin (mobile apps)
            return True

        allowed_origins = getattr(
            settings, "WS_ALLOWED_ORIGINS", ["http://localhost:3000", "http://localhost:8000", "https://yourdomain.com"]
        )

        return origin in allowed_origins

    async def authenticate_jwt(self, scope) -> Optional[User]:
        """
        Enhanced JWT authentication with multiple sources
        """
        try:
            token = None

            # Method 1: Authorization header (preferred for web)
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

            # Method 2: Query parameter (mobile fallback)
            if not token:
                query_string = scope.get("query_string", b"").decode()
                if "token=" in query_string:
                    for param in query_string.split("&"):
                        if param.startswith("token="):
                            token = param.split("=")[1]
                            break

            # Method 3: Cookie-based (web sessions)
            if not token:
                cookies = headers.get(b"cookie", b"").decode()
                for cookie in cookies.split(";"):
                    if "ws_token=" in cookie:
                        token = cookie.split("ws_token=")[1].split(";")[0]
                        break

            if not token:
                return None

            # Validate JWT token using SimpleJWT (consistent with consumer)
            from rest_framework_simplejwt.authentication import JWTAuthentication

            jwt_auth = JWTAuthentication()
            validated_token = await database_sync_to_async(jwt_auth.get_validated_token)(token)

            # Check token blacklist (for logout/revocation)
            if await self.is_token_blacklisted(token):
                return None

            user = await database_sync_to_async(jwt_auth.get_user)(validated_token)
            return user

        except (InvalidToken, TokenError, Exception) as e:
            logger.warning(f"WebSocket JWT authentication failed: {str(e)}")
            return None

    async def check_connection_limits(self, user) -> bool:
        """
        Prevent too many concurrent connections per user.
        Uses atomic cache operations to avoid race conditions.
        """
        cache_key = f"ws_connections:{user.id}"
        # Initialize key if it doesn't exist (atomic, won't overwrite)
        cache.add(cache_key, 0, timeout=3600)
        # Atomically increment and check
        try:
            new_count = cache.incr(cache_key)
        except ValueError:
            # Key expired between add and incr; re-initialize
            cache.set(cache_key, 1, timeout=3600)
            return True

        if new_count > self.max_connections_per_user:
            # Over limit — roll back the increment
            cache.decr(cache_key)
            logger.warning(f"User {user.id} exceeded connection limit")
            return False

        return True

    async def check_rate_limits(self, user, scope) -> bool:
        """
        Rate limiting for WebSocket connections
        """
        # Get client IP
        client_ip = self.get_client_ip(scope)

        # Check per-user rate limit
        user_key = f"ws_rate_limit:user:{user.id}"
        if not self.check_rate_limit(user_key):
            return False

        # Check per-IP rate limit
        ip_key = f"ws_rate_limit:ip:{client_ip}"
        if not self.check_rate_limit(ip_key):
            return False

        return True

    def check_rate_limit(self, cache_key) -> bool:
        """
        Generic rate limiting implementation
        """
        current_requests = cache.get(cache_key, 0)

        if current_requests >= self.max_requests_per_window:
            return False

        # Increment request count
        if current_requests == 0:
            cache.set(cache_key, 1, timeout=self.rate_limit_window)
        else:
            cache.incr(cache_key)

        return True

    def get_client_ip(self, scope) -> str:
        """
        Extract client IP address from WebSocket scope
        """
        headers = dict(scope.get("headers", []))

        # Check X-Forwarded-For header (proxy/load balancer)
        x_forwarded_for = headers.get(b"x-forwarded-for", b"").decode()
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        x_real_ip = headers.get(b"x-real-ip", b"").decode()
        if x_real_ip:
            return x_real_ip

        # Fall back to client address
        client_info = scope.get("client", ["unknown", 0])
        return client_info[0] if client_info else "unknown"

    async def is_token_blacklisted(self, token) -> bool:
        """
        Check if token is blacklisted (for logout/revocation)
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return cache.get(f"blacklisted_token:{token_hash}") is not None

    async def blacklist_token(self, token, expiry_time=None):
        """
        Add token to blacklist
        """
        if not expiry_time:
            expiry_time = datetime.now() + timedelta(days=1)

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        cache.set(f"blacklisted_token:{token_hash}", True, timeout=86400)

    async def decrement_connection_count(self, user):
        """
        Decrement connection count when user disconnects.
        Uses atomic cache operations to avoid race conditions.
        """
        cache_key = f"ws_connections:{user.id}"
        try:
            new_count = cache.decr(cache_key)
            if new_count <= 0:
                cache.delete(cache_key)
        except ValueError:
            # Key doesn't exist or already expired
            cache.delete(cache_key)

    @database_sync_to_async
    def get_user_by_id(self, user_id) -> Optional[User]:
        """
        Get user by ID with caching
        """
        cache_key = f"user:{user_id}"
        cached_user = cache.get(cache_key)

        if cached_user:
            return cached_user

        try:
            user = User.objects.get(id=user_id, is_active=True)
            cache.set(cache_key, user, timeout=300)  # 5 minute cache
            return user
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def validate_tenant_access(self, user, tenant_id) -> bool:
        """
        Validate user has access to tenant AND holds the inbox.view permission.

        Checks:
        1. Active TenantUser record exists for this user + tenant
        2. The user's role grants the "inbox.view" permission
        """
        if not tenant_id:
            return False

        cache_key = f"tenant_access:{user.id}:{tenant_id}"
        cached_result = cache.get(cache_key)

        if cached_result is not None:
            return cached_result

        from tenants.models import TenantUser
        from tenants.permissions import has_permission

        tenant_user = (
            TenantUser.objects.filter(
                user=user,
                tenant_id=tenant_id,
                is_active=True,
            )
            .select_related("role")
            .first()
        )

        if not tenant_user:
            cache.set(cache_key, False, timeout=600)
            return False

        has_access = has_permission(tenant_user.role, "inbox.view")

        cache.set(cache_key, has_access, timeout=600)  # 10 minute cache
        return has_access


# Global security manager instance
security_manager = WebSocketSecurityManager()


class SecureWebSocketMixin:
    """
    Mixin for enhanced WebSocket security
    """

    async def secure_connect(self):
        """
        Enhanced connection method with security validation
        """
        user, error = await security_manager.authenticate_websocket(self.scope, self)

        if not user:
            logger.warning(f"WebSocket connection rejected: {error}")
            await self.close(code=4001)  # Unauthorized
            return False

        self.user = user
        return True

    async def secure_disconnect(self, close_code):
        """
        Enhanced disconnect with cleanup
        """
        if hasattr(self, "user") and self.user:
            await security_manager.decrement_connection_count(self.user)


# Settings additions for security configuration
"""
Add these to your settings.py:

# WebSocket Security Settings
WS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://localhost:8000',
    'https://yourdomain.com'
]

WS_MAX_CONNECTIONS_PER_USER = 5
WS_RATE_LIMIT_WINDOW = 60  # seconds
WS_MAX_REQUESTS_PER_WINDOW = 100

# Token blacklist cache
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}
"""
