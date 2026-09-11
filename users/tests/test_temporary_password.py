"""
A temporary password cannot become the permanent one (#221).

Run with: python manage.py test users.tests.test_temporary_password

An operator sets the first password when onboarding a tenant, which means
the operator knows the customer's credentials. Blocking token issuance until
the holder replaces it is the half that makes "temporary" true; without it
the flag would be decoration.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


def _link_to_a_tenant(user, name):
    """The token view refuses a user with no tenant before the serializer runs."""
    tenant = Tenant.objects.create(name=name)
    TenantUser.objects.create(tenant=tenant, user=user, role=TenantRole.objects.get(tenant=tenant, slug="owner"))
    return tenant


TOKEN_URL = "/token/"
SET_URL = "/users/set-initial-password/"


class TokenIssuanceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pending@acme.test",
            email="pending@acme.test",
            password="TempPass!2026",
            mobile="+919000050001",
        )
        _link_to_a_tenant(self.user, "Token Co")
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])

    def test_a_flagged_account_cannot_obtain_a_token(self):
        resp = APIClient().post(
            TOKEN_URL, {"username": "pending@acme.test", "password": "TempPass!2026"}, format="json"
        )

        self.assertEqual(resp.status_code, 401)
        self.assertNotIn("access", resp.data)

    def test_the_refusal_says_what_to_do(self):
        resp = APIClient().post(
            TOKEN_URL, {"username": "pending@acme.test", "password": "TempPass!2026"}, format="json"
        )

        self.assertIn("set-initial-password", str(resp.data))

    def test_clearing_the_flag_restores_normal_login(self):
        self.user.must_change_password = False
        self.user.save(update_fields=["must_change_password"])

        resp = APIClient().post(
            TOKEN_URL, {"username": "pending@acme.test", "password": "TempPass!2026"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)


class SetInitialPasswordTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pending@acme.test",
            email="pending@acme.test",
            password="TempPass!2026",
            mobile="+919000050002",
        )
        _link_to_a_tenant(self.user, "SetPw Co")
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        self.client_api = APIClient()

    def _post(self, **over):
        body = {
            "username": "pending@acme.test",
            "temporary_password": "TempPass!2026",
            "new_password": "ChosenByThem!77",
        }
        body.update(over)
        return self.client_api.post(SET_URL, body, format="json")

    def test_the_holder_can_set_their_own_password_without_a_token(self):
        resp = self._post()

        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ChosenByThem!77"))
        self.assertFalse(self.user.must_change_password)

    def test_login_works_immediately_afterwards(self):
        self._post()

        resp = APIClient().post(
            TOKEN_URL, {"username": "pending@acme.test", "password": "ChosenByThem!77"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)

    def test_the_wrong_temporary_password_changes_nothing(self):
        resp = self._post(temporary_password="not-it")

        self.assertEqual(resp.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("TempPass!2026"))
        self.assertTrue(self.user.must_change_password)

    def test_an_account_not_awaiting_a_password_is_refused(self):
        """Otherwise this is a password reset for anyone who knows one password."""
        self.user.must_change_password = False
        self.user.save(update_fields=["must_change_password"])

        resp = self._post()

        self.assertEqual(resp.status_code, 400)

    def test_an_unknown_account_gets_the_same_answer_as_a_wrong_password(self):
        """The response must not reveal which accounts exist or are pending."""
        unknown = self._post(username="nobody@acme.test")
        wrong = self._post(temporary_password="not-it")

        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.data, wrong.data)

    def test_reusing_the_temporary_password_is_refused(self):
        resp = self._post(new_password="TempPass!2026")

        self.assertEqual(resp.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)

    def test_a_weak_password_is_refused_by_django_validators(self):
        resp = self._post(new_password="1234")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("new_password", resp.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
