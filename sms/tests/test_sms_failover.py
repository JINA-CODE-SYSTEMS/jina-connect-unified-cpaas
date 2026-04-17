"""Tests for SMS provider failover logic (#104)."""

from __future__ import annotations

import pytest

from contacts.models import TenantContact
from sms.models import SMSApp, SMSOutboundMessage
from sms.providers.base import SMSSendResult
from sms.services.message_sender import SMSMessageSender
from tenants.models import Tenant


class _FakeProvider:
    """Configurable fake SMS provider."""

    def __init__(self, success=True, provider="primary"):
        self._success = success
        self._provider = provider

    def send_sms(self, to, body, sender_id=None, dlt_template_id=None, **kwargs):
        if self._success:
            return SMSSendResult(
                success=True, provider=self._provider, message_id=f"MSG-{self._provider}", segment_count=1
            )
        return SMSSendResult(success=False, provider=self._provider, error_message=f"{self._provider} failed")


@pytest.mark.django_db
class TestSMSFailover:
    def setup_method(self):
        self.tenant = Tenant.objects.create(name="Failover Tenant")
        self.contact = TenantContact.objects.create(tenant=self.tenant, phone="+919300000001", first_name="Test")

        self.fallback_app = SMSApp.objects.create(
            tenant=self.tenant,
            provider="MSG91",
            sender_id="+919300000900",
            provider_credentials={"key": "fb"},
            is_active=True,
        )
        self.primary_app = SMSApp.objects.create(
            tenant=self.tenant,
            provider="TWILIO",
            sender_id="+919300000800",
            provider_credentials={"key": "pr"},
            is_active=True,
            fallback_app=self.fallback_app,
        )

    def _patch(self, monkeypatch, primary_success, fallback_success=True):
        providers = {
            self.primary_app.pk: _FakeProvider(success=primary_success, provider="primary"),
            self.fallback_app.pk: _FakeProvider(success=fallback_success, provider="fallback"),
        }
        monkeypatch.setattr(
            "sms.services.message_sender.get_sms_provider",
            lambda app: providers[app.pk],
        )
        monkeypatch.setattr("sms.services.message_sender.check_rate_limit", lambda _: True)

    def test_primary_success_does_not_trigger_fallback(self, monkeypatch):
        """When primary succeeds, fallback is NOT used."""
        self._patch(monkeypatch, primary_success=True)

        sender = SMSMessageSender(self.primary_app)
        result = sender.send_text(
            chat_id=str(self.contact.phone), text="hello", contact=self.contact, create_inbox_entry=False
        )

        assert result["success"] is True
        assert result["message_id"] == "MSG-primary"

        outbound = SMSOutboundMessage.objects.get(provider_message_id="MSG-primary")
        assert outbound.provider_used == "TWILIO"

    def test_primary_fails_fallback_succeeds(self, monkeypatch):
        """When primary fails and fallback succeeds, message is sent via fallback (#104)."""
        self._patch(monkeypatch, primary_success=False, fallback_success=True)

        sender = SMSMessageSender(self.primary_app)
        result = sender.send_text(
            chat_id=str(self.contact.phone), text="hello", contact=self.contact, create_inbox_entry=False
        )

        assert result["success"] is True
        assert result["message_id"] == "MSG-fallback"

        outbound = SMSOutboundMessage.objects.get(provider_message_id="MSG-fallback")
        assert outbound.provider_used == "MSG91"

    def test_both_fail(self, monkeypatch):
        """When both primary and fallback fail, result is failure."""
        self._patch(monkeypatch, primary_success=False, fallback_success=False)

        sender = SMSMessageSender(self.primary_app)
        result = sender.send_text(
            chat_id=str(self.contact.phone), text="hello", contact=self.contact, create_inbox_entry=False
        )

        assert result["success"] is False
        assert "fallback failed" in result["error"]

    def test_no_fallback_configured(self, monkeypatch):
        """When no fallback_app, primary failure is the final result."""
        self.primary_app.fallback_app = None
        self.primary_app.save(update_fields=["fallback_app"])

        self._patch(monkeypatch, primary_success=False)

        sender = SMSMessageSender(self.primary_app)
        result = sender.send_text(
            chat_id=str(self.contact.phone), text="hello", contact=self.contact, create_inbox_entry=False
        )

        assert result["success"] is False
        assert "primary failed" in result["error"]

    def test_inactive_fallback_not_used(self, monkeypatch):
        """Inactive fallback_app is NOT used even when configured."""
        self.fallback_app.is_active = False
        self.fallback_app.save(update_fields=["is_active"])

        self._patch(monkeypatch, primary_success=False)

        sender = SMSMessageSender(self.primary_app)
        result = sender.send_text(
            chat_id=str(self.contact.phone), text="hello", contact=self.contact, create_inbox_entry=False
        )

        assert result["success"] is False
