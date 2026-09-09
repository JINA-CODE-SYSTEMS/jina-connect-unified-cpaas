"""
Tests for BrandingSettings product name and singleton behaviour.

Run with: python manage.py test tenants.tests.test_branding_settings
"""

from django.core.exceptions import ValidationError
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

    def test_remains_a_singleton_after_setting_product_name(self):
        first = BrandingSettings.get_instance()
        first.product_name = "One"
        first.save()

        second = BrandingSettings(product_name="Two")
        second.save()

        self.assertEqual(BrandingSettings.objects.count(), 1)
        self.assertEqual(BrandingSettings.objects.first().product_name, "Two")


class BrandingSettingsPrimaryColorTestCase(TestCase):
    """primary_color falls back to the deployment default and rejects junk."""

    @override_settings(DEFAULT_BRAND_COLOR="#465fff")
    def test_blank_colour_falls_back_to_setting(self):
        branding = BrandingSettings.get_instance()
        self.assertEqual(branding.primary_color, "")
        self.assertEqual(branding.effective_primary_color, "#465fff")

    @override_settings(DEFAULT_BRAND_COLOR="#465fff")
    def test_set_colour_overrides_setting(self):
        branding = BrandingSettings.get_instance()
        branding.primary_color = "#ff6b00"
        branding.save()

        self.assertEqual(BrandingSettings.get_instance().effective_primary_color, "#ff6b00")

    def test_shorthand_hex_is_accepted(self):
        branding = BrandingSettings.get_instance()
        branding.primary_color = "#f60"
        branding.full_clean()

    def test_non_hex_values_are_rejected(self):
        branding = BrandingSettings.get_instance()

        for bad in ["red", "465fff", "#12345", "#gggggg", "rgb(1,2,3)"]:
            with self.subTest(value=bad):
                branding.primary_color = bad
                with self.assertRaises(ValidationError):
                    branding.full_clean()
