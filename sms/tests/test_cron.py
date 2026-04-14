import pytest

from sms.cron import reset_daily_sms_counters
from sms.models import SMSApp
from tenants.models import Tenant


@pytest.mark.django_db
def test_reset_daily_sms_counters_updates_only_active_apps():
    tenant = Tenant.objects.create(name="SMS Cron Tenant")
    active = SMSApp.objects.create(
        tenant=tenant,
        provider="TWILIO",
        sender_id="+14155550101",
        provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        is_active=True,
        messages_sent_today=17,
    )
    inactive = SMSApp.objects.create(
        tenant=tenant,
        provider="MSG91",
        sender_id="JINA01",
        provider_credentials={"auth_key": "k1"},
        is_active=False,
        messages_sent_today=23,
    )

    reset_daily_sms_counters()

    active.refresh_from_db()
    inactive.refresh_from_db()
    assert active.messages_sent_today == 0
    assert inactive.messages_sent_today == 23
