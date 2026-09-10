"""
Writes must be mapped explicitly; the "default" key never authorises them.

Run with: python manage.py test tenants.tests.test_rbac_write_actions

Background: `TenantTransactionViewSet` mapped only read actions and set
`"default": "billing.view"`. `create` fell through to that default, and
`billing.view` is granted to every seeded role including `viewer`. Since
`BaseModelViewSet` enables POST and the serializer is `fields = "__all__"`,
any authenticated user could create a `SUCCESS RECHARGE` row — which the
`update_tenant_balance` receiver applies to the wallet — for any tenant.

These tests pin both halves: the specific hole, and the general rule that
closed it.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser
from users.models import User

TXN_URL = "/transaction/tenant-transactions/"


def _user(username, mobile):
    return User.objects.create_user(username=username, email=f"{username}@test.invalid", password="x", mobile=mobile)


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _recharge_payload(tenant_pk, amount="1000000.00"):
    return {
        "tenant": tenant_pk,
        "amount": amount,
        "amount_currency": "USD",
        "transaction_type": "SUCCESS RECHARGE",
    }


class WalletCreditPermissionTestCase(TestCase):
    """A read-only role must not be able to move money."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Acme")
        cls.other = Tenant.objects.create(name="Other Co")

        cls.viewer = _user("rbac_viewer", "+919000010001")
        TenantUser.objects.create(
            tenant=cls.tenant, user=cls.viewer, role=TenantRole.objects.get(tenant=cls.tenant, slug="viewer")
        )

        cls.owner = _user("rbac_owner", "+919000010002")
        TenantUser.objects.create(
            tenant=cls.tenant, user=cls.owner, role=TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        )

    def test_viewer_cannot_credit_its_own_wallet(self):
        before = Tenant.objects.get(pk=self.tenant.pk).balance

        resp = _client(self.viewer).post(TXN_URL, _recharge_payload(self.tenant.pk), format="json")

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).balance, before)

    def test_viewer_cannot_credit_another_tenants_wallet(self):
        before = Tenant.objects.get(pk=self.other.pk).balance

        resp = _client(self.viewer).post(TXN_URL, _recharge_payload(self.other.pk), format="json")

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Tenant.objects.get(pk=self.other.pk).balance, before)

    def test_viewer_can_still_read_transactions(self):
        """The fix must not take away the read access viewers legitimately have."""
        self.assertEqual(_client(self.viewer).get(TXN_URL).status_code, 200)

    def test_billing_manage_role_can_still_create(self):
        """Closing the hole must not block the role that is supposed to do this."""
        resp = _client(self.owner).post(TXN_URL, _recharge_payload(self.tenant.pk, "10.00"), format="json")

        self.assertEqual(resp.status_code, 201)


class WriteFallbackRuleTestCase(TestCase):
    """The general rule, tested directly against the permission class."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Rule Co")
        cls.viewer = _user("rule_viewer", "+919000010003")
        TenantUser.objects.create(
            tenant=cls.tenant, user=cls.viewer, role=TenantRole.objects.get(tenant=cls.tenant, slug="viewer")
        )

    def _check(self, method, action, required_permissions):
        from unittest.mock import Mock

        from tenants.permission_classes import TenantRolePermission

        request = Mock()
        request.user = self.viewer
        request.method = method
        view = Mock()
        view.action = action
        view.required_permissions = required_permissions
        return TenantRolePermission().has_permission(request, view)

    def test_unmapped_write_is_denied_even_with_a_permissive_default(self):
        self.assertFalse(self._check("POST", "create", {"list": "billing.view", "default": "billing.view"}))

    def test_unmapped_read_still_falls_back_to_default(self):
        self.assertTrue(self._check("GET", "list", {"default": "billing.view"}))

    def test_explicitly_mapped_write_is_allowed_when_the_role_has_it(self):
        self.assertTrue(self._check("PATCH", "partial_update", {"partial_update": "billing.view"}))

    def test_explicitly_mapped_write_is_denied_when_the_role_lacks_it(self):
        self.assertFalse(self._check("POST", "create", {"create": "billing.manage"}))

    def test_viewset_with_no_required_permissions_is_untouched(self):
        self.assertTrue(self._check("POST", "create", {}))
