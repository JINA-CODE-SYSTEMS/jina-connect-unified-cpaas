"""Tests for RCS message sender: send_text, send_media, send_keyboard, SMS fallback."""

from types import SimpleNamespace

import pytest

from contacts.models import TenantContact
from rcs.models import RCSApp, RCSOutboundMessage
from rcs.providers.base import RCSSendResult
from rcs.services.message_sender import RCSMessageSender
from sms.models import SMSApp
from tenants.models import Tenant

# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="Sender Tenant")


@pytest.fixture
def rcs_app(tenant):
    return RCSApp.objects.create(
        tenant=tenant,
        provider="GOOGLE_RBM",
        agent_id="sender-agent@rbm.goog",
        daily_limit=1000,
        sms_fallback_enabled=False,
    )


@pytest.fixture
def contact(tenant):
    return TenantContact.objects.create(tenant=tenant, phone="+14155550100")


def _make_capability(is_rcs=True, os="android"):
    return SimpleNamespace(is_rcs_enabled=is_rcs, device_os=os, features=[])


def _make_sender(
    rcs_app,
    monkeypatch,
    *,
    capability=None,
    send_success=True,
    send_message_id="rcs-msg-1",
    send_is_rcs_capable=True,
):
    """Return a patched RCSMessageSender."""
    cap = capability if capability is not None else _make_capability()

    monkeypatch.setattr(
        "rcs.services.capability_checker.RCSCapabilityChecker.get_capability",
        lambda self, phone: cap,
    )
    monkeypatch.setattr(
        "rcs.services.message_sender.check_rate_limit",
        lambda key: True,
    )
    fake_provider = SimpleNamespace(
        send_message=lambda **kw: RCSSendResult(
            success=send_success,
            provider="GOOGLE_RBM",
            message_id=send_message_id if send_success else "",
            is_rcs_capable=send_is_rcs_capable,
            raw_response={},
            error_message="" if send_success else "provider error",
        )
    )
    monkeypatch.setattr(
        "rcs.services.message_sender.get_rcs_provider",
        lambda app: fake_provider,
    )
    return RCSMessageSender(rcs_app)


# ── send_text ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRCSSendText:
    def test_success_creates_outbound_message(self, rcs_app, contact, monkeypatch):
        sender = _make_sender(rcs_app, monkeypatch)
        result = sender.send_text(
            chat_id=str(contact.phone),
            text="Hello RCS",
            contact=contact,
            create_inbox_entry=False,
            check_capability=False,
        )
        assert result["success"] is True
        assert result["channel"] == "RCS"
        outbound = RCSOutboundMessage.objects.get(provider_message_id="rcs-msg-1")
        assert outbound.status == "SENT"
        assert outbound.message_type == "TEXT"

    def test_failure_creates_failed_outbound(self, rcs_app, contact, monkeypatch):
        sender = _make_sender(
            rcs_app,
            monkeypatch,
            send_success=False,
            send_message_id="",
            send_is_rcs_capable=True,
        )
        result = sender.send_text(
            chat_id=str(contact.phone),
            text="fail test",
            contact=contact,
            create_inbox_entry=False,
            check_capability=False,
        )
        assert result["success"] is False
        outbound = RCSOutboundMessage.objects.filter(to_phone=str(contact.phone)).latest("created_at")
        assert outbound.status == "FAILED"

    def test_truncates_text_to_3072_chars(self, rcs_app, contact, monkeypatch):
        sender = _make_sender(rcs_app, monkeypatch)
        long_text = "A" * 5000
        result = sender.send_text(
            chat_id=str(contact.phone),
            text=long_text,
            contact=contact,
            create_inbox_entry=False,
            check_capability=False,
        )
        assert result["success"] is True
        outbound = RCSOutboundMessage.objects.get(provider_message_id="rcs-msg-1")
        assert len(outbound.message_content["text"]) == 3072

    def test_daily_limit_gate_returns_error(self, rcs_app, contact, monkeypatch):
        rcs_app.messages_sent_today = rcs_app.daily_limit
        rcs_app.save()

        cap = _make_capability()
        monkeypatch.setattr(
            "rcs.services.capability_checker.RCSCapabilityChecker.get_capability",
            lambda self, phone: cap,
        )
        monkeypatch.setattr("rcs.services.message_sender.check_rate_limit", lambda key: True)
        monkeypatch.setattr(
            "rcs.services.message_sender.get_rcs_provider",
            lambda app: SimpleNamespace(
                send_message=lambda **kw: RCSSendResult(success=True, provider="GOOGLE_RBM", message_id="ok")
            ),
        )

        sender = RCSMessageSender(rcs_app)
        result = sender.send_text(
            chat_id=str(contact.phone),
            text="blocked",
            check_capability=False,
        )
        assert result["success"] is False
        assert "Daily limit" in result["error"]

    def test_rate_limit_returns_error(self, rcs_app, contact, monkeypatch):
        cap = _make_capability()
        monkeypatch.setattr(
            "rcs.services.capability_checker.RCSCapabilityChecker.get_capability",
            lambda self, phone: cap,
        )
        monkeypatch.setattr("rcs.services.message_sender.check_rate_limit", lambda key: False)
        monkeypatch.setattr(
            "rcs.services.message_sender.get_rcs_provider",
            lambda app: SimpleNamespace(
                send_message=lambda **kw: RCSSendResult(success=True, provider="GOOGLE_RBM", message_id="ok")
            ),
        )

        sender = RCSMessageSender(rcs_app)
        result = sender.send_text(chat_id=str(contact.phone), text="rate-blocked", check_capability=False)
        assert result["success"] is False
        assert "Rate limited" in result["error"]


# ── send_keyboard ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRCSSendKeyboard:
    def test_attaches_suggestions_to_content(self, rcs_app, contact, monkeypatch):
        sender = _make_sender(rcs_app, monkeypatch)
        keyboard = [{"type": "reply", "text": "Yes", "postbackData": "yes"}]
        result = sender.send_keyboard(
            chat_id=str(contact.phone),
            text="Choose",
            keyboard=keyboard,
            contact=contact,
            create_inbox_entry=False,
            check_capability=False,
        )
        assert result["success"] is True
        outbound = RCSOutboundMessage.objects.get(provider_message_id="rcs-msg-1")
        assert outbound.message_content.get("suggestions") is not None


# ── SMS fallback ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRCSSMSFallback:
    def test_fallback_not_configured_returns_error(self, rcs_app, contact, monkeypatch):
        rcs_app.sms_fallback_enabled = False
        rcs_app.save()

        cap = _make_capability(is_rcs=False)
        monkeypatch.setattr(
            "rcs.services.capability_checker.RCSCapabilityChecker.get_capability",
            lambda self, phone: cap,
        )
        monkeypatch.setattr("rcs.services.message_sender.check_rate_limit", lambda key: True)
        monkeypatch.setattr(
            "rcs.services.message_sender.get_rcs_provider",
            lambda app: SimpleNamespace(
                send_message=lambda **kw: RCSSendResult(success=True, provider="GOOGLE_RBM", message_id="ok")
            ),
        )

        sender = RCSMessageSender(rcs_app)
        result = sender.send_text(chat_id=str(contact.phone), text="fallback test")
        assert result["success"] is False
        assert "SMS fallback" in result["error"]

    def test_fallback_fires_when_not_rcs_capable(self, rcs_app, contact, monkeypatch):
        cap = _make_capability(is_rcs=False)
        monkeypatch.setattr(
            "rcs.services.capability_checker.RCSCapabilityChecker.get_capability",
            lambda self, phone: cap,
        )
        monkeypatch.setattr("rcs.services.message_sender.check_rate_limit", lambda key: True)
        monkeypatch.setattr(
            "rcs.services.message_sender.get_rcs_provider",
            lambda app: SimpleNamespace(
                send_message=lambda **kw: RCSSendResult(success=True, provider="GOOGLE_RBM", message_id="ok")
            ),
        )

        called = {}

        def _fake_get_channel_adapter(channel, tenant):
            class FakeSMS:
                def send_text(self, phone, text, **kw):
                    called["fired"] = True
                    return {"success": True, "message_id": "sms-fallback-1"}

            return FakeSMS()

        monkeypatch.setattr(
            "jina_connect.channel_registry.get_channel_adapter",
            _fake_get_channel_adapter,
        )
        sms_app = SMSApp.objects.create(
            tenant=rcs_app.tenant,
            provider="TWILIO",
            sender_id="TEST",
        )
        rcs_app.sms_fallback_enabled = True
        rcs_app.sms_fallback_app = sms_app
        rcs_app.save()

        sender = RCSMessageSender(rcs_app)
        result = sender.send_text(chat_id=str(contact.phone), text="test")
        assert called.get("fired") is True
        assert result.get("channel") == "SMS_FALLBACK"


# ── iOS adjustment ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRCSIosAdjustment:
    def test_adjust_for_ios_trims_suggestion_text(self, rcs_app, monkeypatch):
        sender = RCSMessageSender.__new__(RCSMessageSender)
        content = {
            "text": "Pick one",
            "suggestions": [
                {"reply": {"text": "A" * 30, "postbackData": "a"}},
            ],
        }
        adjusted = sender._adjust_for_ios(content)
        assert len(adjusted["suggestions"][0]["reply"]["text"]) <= 20

    def test_adjust_for_ios_limits_media_height_to_medium(self, rcs_app, monkeypatch):
        sender = RCSMessageSender.__new__(RCSMessageSender)
        content = {
            "richCard": {
                "standaloneCard": {
                    "cardContent": {
                        "media": {"height": "TALL"},
                    }
                }
            }
        }
        adjusted = sender._adjust_for_ios(content)
        height = (
            adjusted.get("richCard", {}).get("standaloneCard", {}).get("cardContent", {}).get("media", {}).get("height")
        )
        assert height == "MEDIUM"


# ── _extract_text_from_content ────────────────────────────────────────────────


class TestExtractTextFromContent:
    def test_returns_text_field_directly(self):
        content = {"text": "hello world"}
        assert RCSMessageSender._extract_text_from_content(content) == "hello world"

    def test_extracts_title_from_standalone_card(self):
        content = {
            "richCard": {
                "standaloneCard": {
                    "cardContent": {
                        "title": "Card Title",
                        "description": "Card Desc",
                    }
                }
            }
        }
        text = RCSMessageSender._extract_text_from_content(content)
        assert "Card Title" in text
        assert "Card Desc" in text

    def test_extracts_titles_from_carousel(self):
        content = {
            "richCard": {
                "carouselCard": {
                    "cardContents": [
                        {"title": "Card 1", "description": ""},
                        {"title": "Card 2", "description": ""},
                    ]
                }
            }
        }
        text = RCSMessageSender._extract_text_from_content(content)
        assert "Card 1" in text
        assert "Card 2" in text

    def test_returns_default_when_empty(self):
        assert RCSMessageSender._extract_text_from_content({}) == "Message from RCS"
