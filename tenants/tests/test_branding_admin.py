"""
Branding settings are editable from a real admin page, and stay a singleton (#226).

Run with: python manage.py test tenants.tests.test_branding_admin

BrandingSettings used to come from the generic auto-registration loop at the
bottom of tenants/admin.py: every field in list_display, no grouping, and an
"Add" button for a model whose save() silently folds a second row into the
first. This exercises the real admin through HTTP, because the point is the
page rather than the model.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from tenants.models import BrandingSettings

User = get_user_model()


class BrandingSettingsAdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser(
            username="branding-root",
            email="branding-root@test.invalid",
            password="RootPass!2026",
            mobile="+919000070001",
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_it_is_registered_explicitly_not_by_the_generic_loop(self):
        registered = admin.site._registry[BrandingSettings]
        self.assertEqual(type(registered).__name__, "BrandingSettingsAdmin")

    def test_add_is_offered_while_no_row_exists(self):
        self.assertFalse(BrandingSettings.objects.exists())
        self.assertEqual(self.client.get(reverse("admin:tenants_brandingsettings_add")).status_code, 200)

    def test_add_is_refused_once_a_row_exists(self):
        """save() would fold a second row into the first, so never offer it."""
        BrandingSettings.get_instance()

        response = self.client.get(reverse("admin:tenants_brandingsettings_add"))

        self.assertEqual(response.status_code, 403)

    def test_the_changelist_does_not_link_to_add_once_a_row_exists(self):
        BrandingSettings.get_instance()

        response = self.client.get(reverse("admin:tenants_brandingsettings_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("admin:tenants_brandingsettings_add"))

    @override_settings(DEFAULT_PRODUCT_NAME="Deployment Default")
    def test_the_change_page_shows_what_blank_resolves_to(self):
        instance = BrandingSettings.get_instance()

        response = self.client.get(reverse("admin:tenants_brandingsettings_change", args=[instance.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deployment Default")

    def test_the_product_name_saves_from_the_admin_form(self):
        instance = BrandingSettings.get_instance()

        response = self.client.post(
            reverse("admin:tenants_brandingsettings_change", args=[instance.pk]),
            {
                "product_name": "Renamed Product",
                "primary_color": "",
                "favicon_url": "",
                "primary_logo_url": "",
                "secondary_logo_url": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        instance.refresh_from_db()
        self.assertEqual(instance.product_name, "Renamed Product")
        self.assertEqual(BrandingSettings.objects.count(), 1)

    @override_settings(DEFAULT_PRODUCT_NAME="Deployment Default")
    def test_clearing_the_product_name_restores_the_deployment_default(self):
        """Blank must save as blank, not as the literal default string."""
        instance = BrandingSettings.get_instance()
        instance.product_name = "Something Else"
        instance.save()

        self.client.post(
            reverse("admin:tenants_brandingsettings_change", args=[instance.pk]),
            {
                "product_name": "",
                "primary_color": "",
                "favicon_url": "",
                "primary_logo_url": "",
                "secondary_logo_url": "",
            },
            follow=True,
        )

        instance.refresh_from_db()
        self.assertEqual(instance.product_name, "")
        self.assertEqual(instance.effective_product_name, "Deployment Default")

    def test_every_editable_field_appears_in_some_fieldset(self):
        """A field added to the model must not silently vanish from the form."""
        model_admin = admin.site._registry[BrandingSettings]
        in_fieldsets = {name for _, opts in model_admin.fieldsets for name in opts["fields"]}
        editable = {
            field.name
            for field in BrandingSettings._meta.fields
            if field.editable and not field.auto_created and field.name != "id"
        }

        self.assertEqual(editable - in_fieldsets, set())
