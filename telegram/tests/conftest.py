"""
Shared fixtures for Telegram test suite.
"""

import pytest

from contacts.models import TenantContact
from telegram.models import TelegramBotApp
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="TG Test Tenant")


@pytest.fixture
def bot_app(tenant):
    return TelegramBotApp.objects.create(
        tenant=tenant,
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        bot_username="test_bot",
        bot_user_id=123456,
    )


@pytest.fixture
def contact(tenant):
    return TenantContact.objects.create(
        tenant=tenant,
        phone="+919876543210",
        first_name="Test",
        last_name="User",
        telegram_chat_id=99887766,
        telegram_username="testuser",
        source="TELEGRAM",
    )
