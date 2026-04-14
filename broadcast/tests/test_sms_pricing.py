from decimal import Decimal

import pytest

from broadcast.models import Broadcast, BroadcastPlatformChoices
from contacts.models import TenantContact
from sms.models import SMSApp
from tenants.models import Tenant


@pytest.mark.django_db
class TestBroadcastSMSPricing:
    def test_get_sms_message_price_returns_zero_without_active_sms_app(self):
        tenant = Tenant.objects.create(name="Pricing tenant no app")
        broadcast = Broadcast.objects.create(
            tenant=tenant,
            name="SMS Pricing",
            platform=BroadcastPlatformChoices.SMS,
        )

        price = broadcast._get_sms_message_price()

        assert price == Decimal("0")

    def test_get_sms_message_price_uses_active_sms_app_price(self):
        tenant = Tenant.objects.create(name="Pricing tenant with app")
        SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
            price_per_sms=Decimal("0.35"),
            is_active=True,
        )
        # Inactive app should be ignored
        SMSApp.objects.create(
            tenant=tenant,
            provider="MSG91",
            sender_id="JINA",
            provider_credentials={"auth_key": "k1"},
            price_per_sms=Decimal("9.99"),
            is_active=False,
        )

        broadcast = Broadcast.objects.create(
            tenant=tenant,
            name="SMS Pricing",
            platform=BroadcastPlatformChoices.SMS,
        )

        price = broadcast._get_sms_message_price()

        assert price == Decimal("0.35")

    def test_get_message_price_routes_sms_platform(self):
        tenant = Tenant.objects.create(name="Pricing tenant routing")
        SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550001",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
            price_per_sms=Decimal("0.25"),
            is_active=True,
        )
        broadcast = Broadcast.objects.create(
            tenant=tenant,
            name="SMS Pricing",
            platform=BroadcastPlatformChoices.SMS,
        )

        assert broadcast.get_message_price() == Decimal("0.25")

    def test_calculate_initial_cost_for_sms_uses_recipients_count(self):
        tenant = Tenant.objects.create(name="Pricing tenant cost")
        SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550002",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
            price_per_sms=Decimal("0.15"),
            is_active=True,
        )

        c1 = TenantContact.objects.create(tenant=tenant, phone="+14155551111", first_name="A")
        c2 = TenantContact.objects.create(tenant=tenant, phone="+14155552222", first_name="B")
        c3 = TenantContact.objects.create(tenant=tenant, phone="+14155553333", first_name="C")

        broadcast = Broadcast.objects.create(
            tenant=tenant,
            name="SMS Pricing Cost",
            platform=BroadcastPlatformChoices.SMS,
        )
        broadcast.recipients.set([c1, c2, c3])

        total = broadcast.calculate_initial_cost()

        assert total == Decimal("0.45")
