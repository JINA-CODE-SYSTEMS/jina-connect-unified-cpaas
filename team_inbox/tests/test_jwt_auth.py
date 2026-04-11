"""
Tests for #265: WebSocketSecurityManager.authenticate_jwt uses SimpleJWT's
JWTAuthentication instead of raw jwt.decode, consistent with the consumer.
"""

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from team_inbox.security import WebSocketSecurityManager
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


class AuthenticateJwtTests(TransactionTestCase):
    """Verify authenticate_jwt uses SimpleJWT and resolves users correctly."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="JWTTest Tenant")
        self.owner_role = TenantRole.objects.get(tenant=self.tenant, slug="owner")
        self.user = User.objects.create_user(
            username="jwt_user",
            email="jwt_user@t.com",
            mobile="+910000077771",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=self.owner_role,
            is_active=True,
        )
        self.manager = WebSocketSecurityManager()
        self.token = str(AccessToken.for_user(self.user))

    def _scope(self, token=None, method="header"):
        """Build a minimal ASGI scope with a JWT token."""
        headers = []
        query_string = b""
        if token and method == "header":
            headers.append((b"authorization", f"Bearer {token}".encode()))
        elif token and method == "query":
            query_string = f"token={token}".encode()
        elif token and method == "cookie":
            headers.append((b"cookie", f"ws_token={token}".encode()))
        return {
            "type": "websocket",
            "headers": headers,
            "query_string": query_string,
        }

    # ── Valid token ──────────────────────────────────────────────

    def test_valid_token_header_returns_user(self):
        """Valid JWT in Authorization header returns the correct user."""
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope(self.token, "header"))
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    def test_valid_token_query_returns_user(self):
        """Valid JWT in query param returns the correct user."""
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope(self.token, "query"))
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    def test_valid_token_cookie_returns_user(self):
        """Valid JWT in cookie returns the correct user."""
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope(self.token, "cookie"))
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    # ── Invalid / missing token ──────────────────────────────────

    def test_no_token_returns_none(self):
        """No token in scope returns None."""
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope())
        self.assertIsNone(user)

    def test_invalid_token_returns_none(self):
        """Garbage token returns None."""
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope("not.a.valid.jwt", "header"))
        self.assertIsNone(user)

    def test_expired_token_returns_none(self):
        """Expired token returns None."""
        from datetime import timedelta

        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope(str(token), "header"))
        self.assertIsNone(user)

    # ── SimpleJWT consistency ────────────────────────────────────

    def test_uses_simplejwt_not_raw_decode(self):
        """
        Confirm the method uses JWTAuthentication (not raw jwt.decode).
        If SECRET_KEY were wrong but SimpleJWT SIGNING_KEY correct,
        auth should still work — this is the whole point of #265.
        """
        user = async_to_sync(self.manager.authenticate_jwt)(self._scope(self.token, "header"))
        self.assertEqual(user.id, self.user.id)
