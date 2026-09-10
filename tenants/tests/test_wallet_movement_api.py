"""
The offline-settlement endpoint is host-only (#233).

Run with: python manage.py test tenants.tests.test_wallet_movement_api

The service is tested directly in test_wallet_service. These cover the parts
only the HTTP layer decides: who may call it, and that a rejected movement
comes back as a 400 with a reason rather than a 500.
"""

from decimal import Decimal

from django.test import TestCase
from djmoney.money import Money
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser
from users.models import User


def _url(tenant):
    return f"/tenants/{tenant.pk}/wallet-movement/"


def _payload(**over):
    body = {"amount": "250.00", "currency": "USD", "reference": "INV-API-1", "note": "EFT"}
    body.update(over)
    return body


class WalletMovementEndpointTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="API Co")
        Tenant.objects.filter(pk=cls.tenant.pk).update(balance=Decimal("100.00"), balance_currency="USD")

        cls.staff = User.objects.create_user(
            username="host_ops", email="host@test.invalid", password="x", mobile="+919000030001", is_staff=True
        )
        cls.owner = User.objects.create_user(
            username="cust_owner", email="own@test.invalid", password="x", mobile="+919000030002"
        )
        TenantUser.objects.create(
            tenant=cls.tenant, user=cls.owner, role=TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        )

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _balance(self):
        return Tenant.objects.get(pk=self.tenant.pk).balance

    def test_staff_can_credit(self):
        resp = self._client(self.staff).post(_url(self.tenant), _payload(), format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._balance(), Money(Decimal("350.00"), "USD"))
        self.assertEqual(resp.data["reference"], "INV-API-1")

    def test_the_tenants_own_owner_cannot_credit_it(self):
        """The whole point: a tenant must never be able to credit itself."""
        resp = self._client(self.owner).post(_url(self.tenant), _payload(reference="INV-SELF"), format="json")

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._balance(), Money(Decimal("100.00"), "USD"))

    def test_anonymous_is_rejected(self):
        resp = APIClient().post(_url(self.tenant), _payload(reference="INV-ANON"), format="json")

        self.assertIn(resp.status_code, (401, 403))
        self.assertEqual(self._balance(), Money(Decimal("100.00"), "USD"))

    def test_a_duplicate_reference_is_a_400_not_a_500(self):
        client = self._client(self.staff)
        client.post(_url(self.tenant), _payload(reference="INV-TWICE"), format="json")

        resp = client.post(_url(self.tenant), _payload(reference="INV-TWICE"), format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("already been applied", resp.data["detail"])
        self.assertEqual(self._balance(), Money(Decimal("350.00"), "USD"))

    def test_a_currency_mismatch_is_a_400_not_a_500(self):
        resp = self._client(self.staff).post(
            _url(self.tenant), _payload(currency="INR", reference="INV-FX"), format="json"
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._balance(), Money(Decimal("100.00"), "USD"))

    def test_a_debit_reverses(self):
        client = self._client(self.staff)
        client.post(_url(self.tenant), _payload(reference="INV-R1"), format="json")

        resp = client.post(_url(self.tenant), _payload(reference="INV-R1-REV", direction="debit"), format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._balance(), Money(Decimal("100.00"), "USD"))

    def test_a_zero_amount_is_rejected_by_validation(self):
        resp = self._client(self.staff).post(
            _url(self.tenant), _payload(amount="0.00", reference="INV-Z"), format="json"
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._balance(), Money(Decimal("100.00"), "USD"))
