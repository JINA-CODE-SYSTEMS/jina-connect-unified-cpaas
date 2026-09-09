"""
Tests for BrandingSettings product name and singleton behaviour.

Run with: python manage.py test tenants.tests.test_branding_settings
"""

from django.test import TestCase, override_settings

from tenants.models import BrandingSettings


class BrandingSettingsProductNameTestCase(TestCase):
    """product_name falls back to the deployment default when blank."""

    @override_settings(DEFAULT_PRODUCT_NAME="Jina Connect")
    def test_blank_product_name_falls_back_to_setting(self):
        branding = BrandingSettings.get_instance()
        self.assertEqual(branding.product_name, "")
        self.assertEqual(branding.effective_product_name, "Jina Connect")

    @override_settings(DEFAULT_PRODUCT_NAME="Jina Connect")
    def test_set_product_name_overrides_setting(self):
        branding = BrandingSettings.get_instance()
        branding.product_name = "Fabtary Connect"
        branding.save()

        self.assertEqual(BrandingSettings.get_instance().effective_product_name, "Fabtary Connect")

    @override_settings(DEFAULT_PRODUCT_NAME="White Label CPaaS")
    def test_fallback_tracks_the_deployment_setting(self):
        branding = BrandingSettings.get_instance()
        self.assertEqual(branding.effective_product_name, "White Label CPaaS")

    def test_saving_a_second_instance_preserves_created_at(self):
        """The singleton save adopts an existing pk, making this an UPDATE.

        auto_now_add only populates created_at on INSERT, so without carrying
        the original value across the UPDATE writes NULL into a NOT NULL column.
        """
        first = BrandingSettings.get_instance()
        original_created_at = first.created_at

        BrandingSettings(product_name="Second").save()

        stored = BrandingSettings.objects.get(pk=first.pk)
        self.assertEqual(stored.created_at, original_created_at)
        self.assertEqual(stored.product_name, "Second")

    def test_remains_a_singleton_after_setting_product_name(self):
        first = BrandingSettings.get_instance()
        first.product_name = "One"
        first.save()

        second = BrandingSettings(product_name="Two")
        second.save()

        self.assertEqual(BrandingSettings.objects.count(), 1)
        self.assertEqual(BrandingSettings.objects.first().product_name, "Two")
