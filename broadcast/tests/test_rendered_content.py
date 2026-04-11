from datetime import datetime
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from djmoney.money import Money

from broadcast.models import Broadcast, BroadcastMessage, BroadcastPlatformChoices
from contacts.models import TenantContact
from message_templates.models import TemplateNumber
from tenants.models import Tenant, TenantWAApp
from wa.models import WATemplate

User = get_user_model()


class BroadcastRenderedContentTestCase(TestCase):
    """Test cases for BroadcastMessage.rendered_content property"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", mobile="+919876543210", password="testpass123"
        )

        self.tenant = Tenant.objects.create(
            name="Test Company Ltd",
            created_by=self.user,
            updated_by=self.user,
            balance=Money(1000.0, "USD"),
            credit_line=Money(0.0, "USD"),
        )

        self.contact = TenantContact.objects.create(
            first_name="John",
            last_name="Smith",
            phone="+918765432109",
            tenant=self.tenant,
            created_by=self.user,
            updated_by=self.user,
        )

        # Create WA app for tenant
        self.wa_app = TenantWAApp.objects.create(
            tenant=self.tenant,
            app_name="Test WhatsApp App",
            app_id="test-app-123",
            created_by=self.user,
            updated_by=self.user,
        )

        # Create TemplateNumber
        self.template_number = TemplateNumber.objects.create()

        # Create WA template with content
        self.wa_template = WATemplate.objects.create(
            wa_app=self.wa_app,
            number=self.template_number,
            element_name="welcome_template",
            language_code="en",
            category="UTILITY",
            template_type="TEXT",
            content=(
                "Hi {{ first_name }}, welcome to {{ company_name }}! "
                "Your booking {{ booking_id }} is confirmed for {{ destination }}. "
                "Today's date: {{ today_date }}"
            ),
            vertical="General",
            created_by=self.user,
            updated_by=self.user,
        )

        self.broadcast = Broadcast.objects.create(
            name="Welcome Campaign",
            tenant=self.tenant,
            platform=BroadcastPlatformChoices.WHATSAPP,
            template_number=self.template_number,
            placeholder_data={
                "booking_id": "TRV123456",
                "destination": "Dubai, UAE",
                "today_date": datetime.now().strftime("%Y-%m-%d"),
                "company_name": "Test Company Ltd",
            },
            created_by=self.user,
            updated_by=self.user,
        )

        self.message = BroadcastMessage.objects.create(broadcast=self.broadcast, contact=self.contact)

    def test_render_content_with_all_placeholders_available(self):
        """Test rendering when all placeholders are provided in broadcast data"""
        # Update template to only use provided placeholders
        self.wa_template.content = "Your booking {{ booking_id }} to {{ destination }} is confirmed!"
        self.wa_template.save()

        rendered = self.message.rendered_content
        expected = "Your booking TRV123456 to Dubai, UAE is confirmed!"

        self.assertEqual(rendered, expected)
        print(f"✅ All placeholders available: {rendered}")

    def test_render_content_with_missing_placeholders_filled_by_reserved_vars(self):
        """Test rendering when some placeholders are missing and filled by reserved variables"""
        rendered = self.message.rendered_content

        # Should contain:
        # - first_name: "John" (from contact name)
        # - company_name: "Test Company Ltd" (from tenant name)
        # - booking_id: "TRV123456" (from placeholder_data)
        # - destination: "Dubai, UAE" (from placeholder_data)
        # - today_date: formatted current date (from reserved vars)

        self.assertIn("John", rendered)  # first_name from contact
        self.assertIn("Test Company Ltd", rendered)  # company_name from tenant
        self.assertIn("TRV123456", rendered)  # booking_id from placeholder_data
        self.assertIn("Dubai, UAE", rendered)  # destination from placeholder_data
        self.assertIn(str(datetime.now().year), rendered)  # today_date should contain current year

        print(f"✅ Mixed placeholders: {rendered}")

    def test_render_content_with_contact_name_variations(self):
        """Test rendering with different contact name formats"""
        # Test with full name
        self.contact.first_name = "Alice"
        self.contact.last_name = "Johnson"
        self.contact.save()

        self.wa_template.content = "Hello {{ first_name }} {{ last_name }}, welcome to {{ company_name }}!"
        self.wa_template.save()

        rendered = self.message.rendered_content

        self.assertIn("Alice", rendered)  # first_name
        self.assertIn("Johnson", rendered)  # last_name
        self.assertIn("Test Company Ltd", rendered)  # company_name

        print(f"✅ Full name rendering: {rendered}")

    def test_render_content_with_single_name(self):
        """Test rendering when contact has only first name"""
        self.contact.first_name = "Bob"
        self.contact.last_name = ""
        self.contact.save()

        self.wa_template.content = "Hi {{ first_name }} {{ last_name }}, your contact is {{ phone }}!"
        self.wa_template.save()

        rendered = self.message.rendered_content

        self.assertIn("Bob", rendered)  # first_name
        self.assertIn("+918765432109", rendered)  # phone from contact
        # last_name should be empty string for single names

        print(f"✅ Single name rendering: {rendered}")

    def test_render_content_with_empty_contact_name(self):
        """Test rendering when contact name is empty"""
        self.contact.first_name = ""
        self.contact.last_name = ""
        self.contact.save()

        self.wa_template.content = "Dear {{ contact_name }}, welcome!"
        self.wa_template.save()

        rendered = self.message.rendered_content

        # Should fallback to "Customer" when name is empty
        self.assertIn("Customer", rendered)

        print(f"✅ Empty name fallback: {rendered}")

    @patch("django.utils.timezone.localtime")
    def test_render_content_with_date_time_variables(self, mock_localtime):
        """Test rendering with date and time reserved variables"""
        # Mock timezone.localtime() to return predictable values
        mock_now = Mock()
        mock_now.strftime.side_effect = lambda fmt: {
            "%B %d, %Y": "November 05, 2025",
            "%I:%M %p": "02:30 PM",
        }.get(fmt, "2025")
        mock_now.year = 2025
        mock_localtime.return_value = mock_now

        self.wa_template.content = (
            "Today is {{ today_date }} and current time is {{ current_time }}. Year: {{ current_year }}"
        )
        self.wa_template.save()

        rendered = self.message.rendered_content

        self.assertIn("November 05, 2025", rendered)
        self.assertIn("02:30 PM", rendered)
        self.assertIn("2025", rendered)

        print(f"✅ Date/time rendering: {rendered}")

    def test_render_content_with_broadcast_name(self):
        """Test rendering with broadcast name in template"""
        self.wa_template.content = "You received this message from {{ broadcast_name }} campaign."
        self.wa_template.save()

        rendered = self.message.rendered_content

        self.assertIn("Welcome Campaign", rendered)

        print(f"✅ Broadcast name rendering: {rendered}")

    def test_render_content_with_mixed_existing_and_reserved_vars(self):
        """Test complex scenario with both provided and reserved variables"""
        self.broadcast.placeholder_data = {
            "order_number": "ORD789",
            "product_name": "Premium Package",
            "custom_message": "Thank you for choosing us!",
        }
        self.broadcast.save()

        self.wa_template.content = (
            "Hi {{ first_name }},\n\n"
            "{{ custom_message }}\n\n"
            "Order Details:\n"
            "- Order: {{ order_number }}\n"
            "- Product: {{ product_name }}\n"
            "- Company: {{ company_name }}\n"
            "- Date: {{ today_date }}\n\n"
            "Contact us at {{ company_name }} for support."
        )
        self.wa_template.save()

        rendered = self.message.rendered_content

        # Check provided placeholders
        self.assertIn("ORD789", rendered)
        self.assertIn("Premium Package", rendered)
        self.assertIn("Thank you for choosing us!", rendered)

        # Check reserved variables
        self.assertIn("John", rendered)  # first_name
        self.assertIn("Test Company Ltd", rendered)  # company_name (appears twice)

        print(f"✅ Complex mixed rendering: {rendered}")

    def test_render_content_placeholder_not_found(self):
        """Test rendering when placeholder is not in data or reserved vars"""
        self.wa_template.content = "Hello {{ first_name }}, your {{ unknown_variable }} is ready!"
        self.wa_template.save()

        rendered = self.message.rendered_content

        # Should contain first_name but unknown_variable should remain as is
        self.assertIn("John", rendered)
        self.assertIn("{{ unknown_variable }}", rendered)  # Should remain unreplaced

        print(f"✅ Unknown variable handling: {rendered}")

    def test_render_content_no_placeholders(self):
        """Test rendering with no placeholders in template"""
        self.wa_template.content = "This is a simple message with no placeholders."
        self.wa_template.save()

        rendered = self.message.rendered_content

        self.assertEqual(rendered, "This is a simple message with no placeholders.")

        print(f"✅ No placeholders: {rendered}")

    def test_render_content_edge_case_whitespace_in_placeholders(self):
        """Test rendering with whitespace in placeholder syntax"""
        self.wa_template.content = "Hello {{  first_name  }}, welcome to {{company_name}}!"
        self.wa_template.save()

        rendered = self.message.rendered_content

        self.assertIn("John", rendered)
        self.assertIn("Test Company Ltd", rendered)

        print(f"✅ Whitespace in placeholders: {rendered}")


def run_broadcast_tests():
    """Helper function to run broadcast rendering tests"""
    import unittest

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(BroadcastRenderedContentTestCase))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n{'=' * 60}")
    print("BROADCAST RENDERING TEST SUMMARY")
    print(f"{'=' * 60}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(
        f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%"
    )

    return result


if __name__ == "__main__":
    run_broadcast_tests()
