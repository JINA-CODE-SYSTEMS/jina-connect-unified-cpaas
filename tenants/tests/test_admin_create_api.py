"""
Operator-driven tenant onboarding over the API (#220).

Run with: python manage.py test tenants.tests.test_admin_create_api

POST /tenants/ creates only the Tenant row, and /tenants/register is
AllowAny self-service that asks for a password an operator does not have.
This is the operator path.

The rules themselves are tested against the service in test_onboarding —
these cover what only the HTTP layer decides: who may call it, that the
response says whether the owner was created or linked, and that an
operator-fixable mistake reads as a 400 rather than a 500.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantUser

User = get_user_model()

URL = "/tenants/admin-create/"


def _payload(**over):
    body = {
        "name": "Wired Co",
        "owner_email": "wired-owner@acme.test",
        "owner_mobile": "+919000070001",
        "temporary_password": "TempPass!2026",
    }
    body.update(over)
    return body


class AdminCreateEndpointTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser(
            username="hostadmin", email="hostadmin@test.invalid", password="x", mobile="+919000070900"
        )
        cls.nobody = User.objects.create_user(
            username="nobody", email="nobody@test.invalid", password="x", mobile="+919000070901"
        )

    def _client(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user=user)
        return c

    def test_staff_can_onboard_a_tenant_with_a_working_owner(self):
        resp = self._client(self.staff).post(URL, _payload(), format="json")

        self.assertEqual(resp.status_code, 201)
        tenant = Tenant.objects.get(name="Wired Co")
        owner = User.objects.get(email="wired-owner@acme.test")
        self.assertEqual(TenantUser.objects.get(tenant=tenant, user=owner).role.slug, "owner")
        self.assertTrue(owner.check_password("TempPass!2026"))

    def test_the_response_says_the_owner_must_change_the_password(self):
        """The caller has to know to hand the temporary password over."""
        resp = self._client(self.staff).post(URL, _payload(), format="json")

        self.assertTrue(resp.data["owner"]["created"])
        self.assertTrue(resp.data["owner"]["must_change_password"])

    def test_the_response_distinguishes_a_linked_owner(self):
        """A linked account keeps its own login, so the operator must not be told to hand one over."""
        User.objects.create_user(
            username="established",
            email="wired-owner@acme.test",
            password="TheirOwnPw!1",
            mobile="+919000070002",
        )

        resp = self._client(self.staff).post(URL, _payload(), format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.data["owner"]["created"])
        self.assertFalse(resp.data["owner"]["must_change_password"])

    def test_a_non_staff_user_cannot_onboard(self):
        resp = self._client(self.nobody).post(URL, _payload(), format="json")

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Tenant.objects.filter(name="Wired Co").exists())

    def test_anonymous_cannot_onboard(self):
        resp = self._client().post(URL, _payload(), format="json")

        self.assertIn(resp.status_code, (401, 403))
        self.assertFalse(Tenant.objects.filter(name="Wired Co").exists())

    def test_a_duplicate_name_is_a_400(self):
        client = self._client(self.staff)
        client.post(URL, _payload(), format="json")

        resp = client.post(URL, _payload(owner_email="second@acme.test", owner_mobile="+919000070003"), format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.data)
        self.assertEqual(Tenant.objects.filter(name="Wired Co").count(), 1)

    def test_a_mobile_belonging_to_someone_else_is_a_400(self):
        User.objects.create_user(username="other", email="other@acme.test", password="x", mobile="+919000070001")

        resp = self._client(self.staff).post(URL, _payload(), format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("owner_mobile", resp.data)
        self.assertFalse(Tenant.objects.filter(name="Wired Co").exists())

    def test_a_new_owner_without_a_password_is_a_400(self):
        resp = self._client(self.staff).post(URL, _payload(temporary_password=""), format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Tenant.objects.filter(name="Wired Co").exists())

    def test_a_failure_leaves_no_orphan_user(self):
        User.objects.create_user(username="other", email="other@acme.test", password="x", mobile="+919000070001")

        self._client(self.staff).post(URL, _payload(), format="json")

        self.assertFalse(User.objects.filter(email="wired-owner@acme.test").exists())
