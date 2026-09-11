"""
Creating a tenant must produce a working owner login (#221).

Run with: python manage.py test tenants.tests.test_onboarding

Both previous paths — POST /tenants/ and the auto-registered admin — made a
tenant nobody could log into: roles were seeded and the wallet defaulted, but
no User and no OWNER TenantUser. On a freshly rebuilt database that left no
way to onboard anyone at all.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from tenants.models import Tenant, TenantUser
from tenants.services.onboarding import create_tenant_with_owner

User = get_user_model()


class CreateTenantWithOwnerTestCase(TestCase):
    def _create(self, **over):
        kwargs = {
            "name": "Acme Ltd",
            "owner_email": "owner@acme.test",
            "owner_mobile": "+919000040001",
            "temporary_password": "TempPass!2026",
        }
        kwargs.update(over)
        return create_tenant_with_owner(**kwargs)

    # ── the thing that was broken ─────────────────────────────────────────
    def test_the_owner_can_actually_be_authenticated(self):
        result = self._create()

        self.assertTrue(result.owner.check_password("TempPass!2026"))

    def test_the_owner_holds_the_owner_role(self):
        result = self._create()

        link = TenantUser.objects.get(tenant=result.tenant, user=result.owner)
        self.assertEqual(link.role.slug, "owner")

    def test_a_new_owner_must_replace_the_temporary_password(self):
        result = self._create()

        self.assertTrue(result.owner.must_change_password)
        self.assertTrue(result.created_owner)

    # ── linking an existing account ───────────────────────────────────────
    def test_an_existing_email_is_linked_rather_than_duplicated(self):
        existing = User.objects.create_user(
            username="already", email="owner@acme.test", password="TheirOwnPw!1", mobile="+919000040099"
        )

        result = self._create()

        self.assertEqual(result.owner.pk, existing.pk)
        self.assertFalse(result.created_owner)
        self.assertEqual(User.objects.filter(email__iexact="owner@acme.test").count(), 1)

    def test_a_linked_account_keeps_its_password_and_is_not_flagged(self):
        """It is an established login elsewhere; interfering with it would be wrong."""
        User.objects.create_user(
            username="already", email="owner@acme.test", password="TheirOwnPw!1", mobile="+919000040099"
        )

        result = self._create()

        self.assertTrue(result.owner.check_password("TheirOwnPw!1"))
        self.assertFalse(result.owner.must_change_password)

    def test_one_user_can_own_several_tenants(self):
        self._create(name="First Co")
        self._create(name="Second Co")

        owner = User.objects.get(email="owner@acme.test")
        self.assertEqual(TenantUser.objects.filter(user=owner, role__slug="owner").count(), 2)

    # ── rejections ────────────────────────────────────────────────────────
    def test_a_duplicate_tenant_name_is_rejected(self):
        self._create()

        with self.assertRaises(ValidationError):
            self._create(owner_email="other@acme.test", owner_mobile="+919000040002")

    def test_a_duplicate_name_is_case_insensitive(self):
        self._create(name="Acme Ltd")

        with self.assertRaises(ValidationError):
            self._create(name="acme ltd", owner_email="other@acme.test", owner_mobile="+919000040003")

    def test_a_mobile_belonging_to_someone_else_is_rejected(self):
        """Emails link accounts; mobiles are unique and cannot."""
        User.objects.create_user(username="someone", email="someone@else.test", password="x", mobile="+919000040001")

        with self.assertRaises(ValidationError) as ctx:
            self._create()

        self.assertIn("owner_mobile", ctx.exception.message_dict)

    def test_a_new_owner_without_a_password_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._create(temporary_password="")

    def test_a_blank_name_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._create(name="   ")

    # ── atomicity ─────────────────────────────────────────────────────────
    def test_a_failure_leaves_no_orphan_tenant_or_user(self):
        User.objects.create_user(username="someone", email="someone@else.test", password="x", mobile="+919000040001")

        with self.assertRaises(ValidationError):
            self._create(name="Orphan Co")

        self.assertFalse(Tenant.objects.filter(name="Orphan Co").exists())
        self.assertFalse(User.objects.filter(email="owner@acme.test").exists())
