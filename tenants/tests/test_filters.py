"""
Tests for Tenant filters.

Run with: python manage.py test tenants.tests.test_filters
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from moneyed import Money

from contacts.models import TenantContact
from tenants.models import Tenant, TenantWAApp

User = get_user_model()


class TenantFilterTestCase(TestCase):
    """Test cases for tenant filtering functionality"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        # Create tenants with different contact counts
        self.tenant_low = Tenant.objects.create(
            name="Low Contact Tenant",
            balance=Money(100, "INR"),
            credit_line=Money(0, "INR"),
            threshold_alert=Money(10, "INR"),
            created_by=self.user,
            updated_by=self.user,
        )

        self.tenant_medium = Tenant.objects.create(
            name="Medium Contact Tenant",
            balance=Money(200, "INR"),
            credit_line=Money(0, "INR"),
            threshold_alert=Money(10, "INR"),
            created_by=self.user,
            updated_by=self.user,
        )

        self.tenant_high = Tenant.objects.create(
            name="High Contact Tenant",
            balance=Money(300, "INR"),
            credit_line=Money(0, "INR"),
            threshold_alert=Money(10, "INR"),
            created_by=self.user,
            updated_by=self.user,
        )

        # Create contacts for tenants
        # Low tenant: 5 contacts
        for i in range(5):
            TenantContact.objects.create(
                tenant=self.tenant_low,
                phone=f"+1415555000{i}",
                first_name=f"Contact{i}",
                last_name="Low",
                created_by=self.user,
                updated_by=self.user,
            )

        # Medium tenant: 50 contacts
        for i in range(50):
            TenantContact.objects.create(
                tenant=self.tenant_medium,
                phone=f"+1415556{i:03d}",
                first_name=f"Contact{i}",
                last_name="Medium",
                created_by=self.user,
                updated_by=self.user,
            )

        # High tenant: 150 contacts
        for i in range(150):
            TenantContact.objects.create(
                tenant=self.tenant_high,
                phone=f"+1415557{i:03d}",
                first_name=f"Contact{i}",
                last_name="High",
                created_by=self.user,
                updated_by=self.user,
            )

        # Create WhatsApp app for medium and high tenants
        TenantWAApp.objects.create(
            tenant=self.tenant_medium,
            app_name="Medium WA App",
            app_id="medium_app_123",
            app_secret="secret_medium",
            wa_number="+14155550001",
            created_by=self.user,
            updated_by=self.user,
            is_active=True,
        )

        TenantWAApp.objects.create(
            tenant=self.tenant_high,
            app_name="High WA App",
            app_id="high_app_456",
            app_secret="secret_high",
            wa_number="+14155550002",
            created_by=self.user,
            updated_by=self.user,
            is_active=True,
        )

    def test_contacts_count_gte_filter(self):
        """Test filtering tenants with contacts_count >= value"""
        from django.db.models import Count

        from tenants.filters import TenantFilter

        queryset = Tenant.objects.annotate(contacts_count=Count("contacts"))

        # Filter for tenants with >= 50 contacts
        filterset = TenantFilter({"contacts_count__gte": 50}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 2)  # Medium (50) and High (150)
        self.assertIn(self.tenant_medium, results)
        self.assertIn(self.tenant_high, results)
        self.assertNotIn(self.tenant_low, results)

    def test_contacts_count_lte_filter(self):
        """Test filtering tenants with contacts_count <= value"""
        from django.db.models import Count

        from tenants.filters import TenantFilter

        queryset = Tenant.objects.annotate(contacts_count=Count("contacts"))

        # Filter for tenants with <= 50 contacts
        filterset = TenantFilter({"contacts_count__lte": 50}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 2)  # Low (5) and Medium (50)
        self.assertIn(self.tenant_low, results)
        self.assertIn(self.tenant_medium, results)
        self.assertNotIn(self.tenant_high, results)

    def test_contacts_count_range_filter(self):
        """Test filtering tenants with contacts_count in a range"""
        from django.db.models import Count

        from tenants.filters import TenantFilter

        queryset = Tenant.objects.annotate(contacts_count=Count("contacts"))

        # Filter for tenants with 10 <= contacts <= 100
        filterset = TenantFilter({"contacts_count__gte": 10, "contacts_count__lte": 100}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 1)  # Only Medium (50)
        self.assertIn(self.tenant_medium, results)
        self.assertNotIn(self.tenant_low, results)
        self.assertNotIn(self.tenant_high, results)

    def test_product_wa_filter(self):
        """Test filtering tenants by WhatsApp product"""
        from tenants.filters import TenantFilter

        queryset = Tenant.objects.all()

        # Filter for tenants with WhatsApp
        filterset = TenantFilter({"product": "wa"}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 2)  # Medium and High have WA
        self.assertIn(self.tenant_medium, results)
        self.assertIn(self.tenant_high, results)
        self.assertNotIn(self.tenant_low, results)

    def test_product_all_filter(self):
        """Test filtering tenants by all products"""
        from tenants.filters import TenantFilter

        queryset = Tenant.objects.all()

        # Filter for tenants with any product
        filterset = TenantFilter({"product": "all"}, queryset=queryset)
        results = list(filterset.qs)

        # Should return tenants with WhatsApp (currently only product available)
        self.assertEqual(len(results), 2)
        self.assertIn(self.tenant_medium, results)
        self.assertIn(self.tenant_high, results)

    def test_product_sms_filter(self):
        """Test filtering tenants by SMS product."""
        from sms.models import SMSApp
        from tenants.filters import TenantFilter

        SMSApp.objects.create(
            tenant=self.tenant_low,
            provider="TWILIO",
            sender_id="+14155559999",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
            is_active=True,
        )

        queryset = Tenant.objects.all()

        # Filter for tenants with SMS
        filterset = TenantFilter({"product": "sms"}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 1)
        self.assertIn(self.tenant_low, results)

    def test_combined_filters(self):
        """Test combining multiple filters"""
        from django.db.models import Count

        from tenants.filters import TenantFilter

        queryset = Tenant.objects.annotate(contacts_count=Count("contacts"))

        # Filter for tenants with WhatsApp AND >= 100 contacts
        filterset = TenantFilter({"product": "wa", "contacts_count__gte": 100}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 1)  # Only High tenant
        self.assertIn(self.tenant_high, results)
        self.assertNotIn(self.tenant_medium, results)
        self.assertNotIn(self.tenant_low, results)

    def test_name_filter(self):
        """Test name filtering"""
        from tenants.filters import TenantFilter

        queryset = Tenant.objects.all()

        # Filter by name contains
        filterset = TenantFilter({"name": "High"}, queryset=queryset)
        results = list(filterset.qs)

        self.assertEqual(len(results), 1)
        self.assertIn(self.tenant_high, results)
