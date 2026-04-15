"""Tests for broadcast RCS handler and pricing integration."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from broadcast.tasks import handle_rcs_message
from rcs.models import RCSApp
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="RCS Broadcast Tenant")


@pytest.fixture
def rcs_app(tenant):
    return RCSApp.objects.create(
        tenant=tenant,
        provider="GOOGLE_RBM",
        agent_id="broadcast-agent@rbm.goog",
        price_per_message=Decimal("0.005"),
    )


def _make_message(tenant, text="hello rcs broadcast", phone="+14155559000", media_url=None, media_type="image"):
    return SimpleNamespace(
        pk=42,
        contact=SimpleNamespace(phone=phone, pk=1),
        broadcast=SimpleNamespace(
            tenant=tenant,
            placeholder_data={
                "text": text,
                "media_url": media_url,
                "media_type": media_type,
            },
        ),
        rendered_content=text,
    )


@pytest.mark.django_db
class TestBroadcastRCSHandler:
    def test_returns_error_when_no_rcs_app(self):
        tenant = Tenant.objects.create(name="No RCS App Tenant")
        message = _make_message(tenant)

        result = handle_rcs_message(message)

        assert result["success"] is False
        assert "No active RCS app" in result["error"]

    def test_returns_error_when_contact_has_no_phone(self, rcs_app, tenant):
        message = SimpleNamespace(
            pk=99,
            contact=SimpleNamespace(phone=None, pk=2),
            broadcast=SimpleNamespace(
                tenant=tenant,
                placeholder_data={"text": "hi"},
            ),
            rendered_content="hi",
        )

        result = handle_rcs_message(message)

        assert result["success"] is False
        assert "no phone" in result["error"]

    def test_routes_to_send_text_when_no_media(self, rcs_app, tenant, monkeypatch):
        called = {}

        class _FakeSender:
            def __init__(self, app):
                pass

            def send_text(self, chat_id, text, **kw):
                called["method"] = "send_text"
                called["text"] = text
                return {"success": True, "message_id": "rcs-bc-1"}

            def send_media(self, **kw):
                called["method"] = "send_media"
                return {"success": True, "message_id": "rcs-bc-m1"}

        monkeypatch.setattr("rcs.services.message_sender.RCSMessageSender", _FakeSender)

        message = _make_message(tenant)
        result = handle_rcs_message(message)

        assert result["success"] is True
        assert called["method"] == "send_text"
        assert called["text"] == "hello rcs broadcast"

    def test_routes_to_send_media_when_media_url_present(self, rcs_app, tenant, monkeypatch):
        called = {}

        class _FakeSender:
            def __init__(self, app):
                pass

            def send_text(self, **kw):
                return {"success": True, "message_id": "t1"}

            def send_media(self, chat_id, media_type, media_url, caption=None, **kw):
                called["method"] = "send_media"
                called["media_url"] = media_url
                return {"success": True, "message_id": "rcs-bc-m1"}

        monkeypatch.setattr("rcs.services.message_sender.RCSMessageSender", _FakeSender)

        message = _make_message(tenant, media_url="https://example.com/img.jpg")
        result = handle_rcs_message(message)

        assert result["success"] is True
        assert called["method"] == "send_media"
        assert called["media_url"] == "https://example.com/img.jpg"

    def test_returns_error_when_no_content(self, rcs_app, tenant, monkeypatch):
        class _FakeSender:
            def __init__(self, app):
                pass

        monkeypatch.setattr("rcs.services.message_sender.RCSMessageSender", _FakeSender)

        message = SimpleNamespace(
            pk=55,
            contact=SimpleNamespace(phone="+14155559001", pk=3),
            broadcast=SimpleNamespace(tenant=tenant, placeholder_data={}),
            rendered_content="",
        )
        result = handle_rcs_message(message)
        assert result["success"] is False
        assert "no text or media" in result["error"].lower()

    def test_rcs_registered_in_platform_handlers(self):
        from broadcast.tasks import _PLATFORM_HANDLERS

        assert "RCS" in _PLATFORM_HANDLERS


@pytest.mark.django_db
class TestRCSBroadcastPricing:
    def test_get_message_price_returns_rcs_app_price(self, rcs_app, tenant):
        from broadcast.models import Broadcast, BroadcastPlatformChoices

        instance = Broadcast(tenant=tenant, platform=BroadcastPlatformChoices.RCS)
        price = instance._get_rcs_message_price()

        assert price == Decimal("0.005")

    def test_get_message_price_returns_zero_when_no_rcs_app(self, tenant):
        from broadcast.models import Broadcast, BroadcastPlatformChoices

        instance = Broadcast(tenant=tenant, platform=BroadcastPlatformChoices.RCS)
        price = instance._get_rcs_message_price()

        assert price == Decimal("0")
