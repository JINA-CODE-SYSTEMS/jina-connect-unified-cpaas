"""
The Django admin can onboard a tenant that someone can log into (#221).

Run with: python manage.py test tenants.tests.test_tenant_admin

Tenant previously came from the generic auto-registration loop at the bottom
of tenants/admin.py — every field listed, no validation, and creating one
produced a tenant with no owner. This exercises the real admin through HTTP
rather than calling the service again, because the point is the page.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tenants.models import Tenant, TenantUser

User = get_user_model()


class TenantAdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser(
            username="root", email="root@test.invalid", password="RootPass!2026", mobile="+919000060001"
        )

    def setUp(self):
        self.client.force_login(self.staff)
        self.add_url = reverse("admin:tenants_tenant_add")

    def _post(self, **over):
        body = {
            "name": "Admin Co",
            "description": "",
            "owner_email": "admin-owner@acme.test",
            "owner_mobile": "+919000060002",
            "temporary_password": "TempPass!2026",
            "owner_first_name": "",
            "owner_last_name": "",
        }
        body.update(over)
        return self.client.post(self.add_url, body, follow=True)

    def test_the_add_page_asks_for_an_owner(self):
        resp = self.client.get(self.add_url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "owner_email")
        self.assertContains(resp, "temporary_password")

    def test_creating_through_the_admin_yields_a_working_owner(self):
        self._post()

        tenant = Tenant.objects.get(name="Admin Co")
        owner = User.objects.get(email="admin-owner@acme.test")
        link = TenantUser.objects.get(tenant=tenant, user=owner)

        self.assertEqual(link.role.slug, "owner")
        self.assertTrue(owner.check_password("TempPass!2026"))
        self.assertTrue(owner.must_change_password)

    def test_a_duplicate_name_is_a_form_error_not_a_500(self):
        self._post()

        resp = self._post(owner_email="second@acme.test", owner_mobile="+919000060003")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertEqual(Tenant.objects.filter(name="Admin Co").count(), 1)

    def test_a_mobile_belonging_to_someone_else_is_a_form_error(self):
        User.objects.create_user(username="other", email="other@acme.test", password="x", mobile="+919000060002")

        resp = self._post()

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already belongs to another user")
        self.assertFalse(Tenant.objects.filter(name="Admin Co").exists())

    def test_an_existing_email_is_linked_and_keeps_its_password(self):
        User.objects.create_user(
            username="established",
            email="admin-owner@acme.test",
            password="TheirOwnPw!1",
            mobile="+919000060004",
        )

        self._post()

        owner = User.objects.get(email="admin-owner@acme.test")
        self.assertTrue(owner.check_password("TheirOwnPw!1"))
        self.assertFalse(owner.must_change_password)
        self.assertEqual(User.objects.filter(email="admin-owner@acme.test").count(), 1)

    def test_the_changelist_still_works(self):
        self._post()

        resp = self.client.get(reverse("admin:tenants_tenant_changelist"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Admin Co")
