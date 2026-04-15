"""Tests for RCS models: RCSApp, RCSWebhookEvent, RCSOutboundMessage."""

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from rcs.models import RCSApp, RCSOutboundMessage, RCSWebhookEvent
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="RCS Model Tenant")


@pytest.fixture
def rcs_app(tenant):
    return RCSApp.objects.create(
        tenant=tenant,
        provider="GOOGLE_RBM",
        agent_id="test-agent@rbm.goog",
        agent_name="Test Agent",
        daily_limit=100,
    )


@pytest.mark.django_db
class TestRCSApp:
    def test_creates_webhook_client_token_on_save(self, tenant):
        app = RCSApp.objects.create(
            tenant=tenant,
            provider="GOOGLE_RBM",
            agent_id="token-agent@rbm.goog",
        )
        assert len(app.webhook_client_token) > 0

    def test_str_uses_agent_name(self, rcs_app):
        assert "Test Agent" in str(rcs_app)

    def test_str_falls_back_to_agent_id(self, tenant):
        app = RCSApp.objects.create(
            tenant=tenant,
            provider="GOOGLE_RBM",
            agent_id="fallback@rbm.goog",
        )
        assert "fallback@rbm.goog" in str(app)

    def test_unique_together_agent_id_per_tenant_provider(self, tenant):
        RCSApp.objects.create(
            tenant=tenant,
            provider="GOOGLE_RBM",
            agent_id="dup-agent@rbm.goog",
        )
        with pytest.raises(IntegrityError):
            RCSApp.objects.create(
                tenant=tenant,
                provider="GOOGLE_RBM",
                agent_id="dup-agent@rbm.goog",
            )

    def test_increment_daily_counter_returns_true_when_under_limit(self, rcs_app):
        rcs_app.messages_sent_today = 0
        rcs_app.save()
        result = rcs_app.increment_daily_counter()
        assert result is True
        rcs_app.refresh_from_db()
        assert rcs_app.messages_sent_today == 1

    def test_increment_daily_counter_returns_false_at_limit(self, rcs_app):
        rcs_app.messages_sent_today = rcs_app.daily_limit
        rcs_app.save()
        result = rcs_app.increment_daily_counter()
        assert result is False
        rcs_app.refresh_from_db()
        assert rcs_app.messages_sent_today == rcs_app.daily_limit

    def test_default_provider_is_google_rbm(self, tenant):
        app = RCSApp.objects.create(
            tenant=tenant,
            agent_id="default-provider@rbm.goog",
        )
        assert app.provider == "GOOGLE_RBM"

    def test_rcs_apps_related_name_on_tenant(self, tenant, rcs_app):
        assert tenant.rcs_apps.filter(pk=rcs_app.pk).exists()


@pytest.mark.django_db
class TestRCSWebhookEvent:
    def test_unique_together_prevents_duplicate_events(self, rcs_app, tenant):
        RCSWebhookEvent.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            provider="GOOGLE_RBM",
            event_type="MESSAGE",
            provider_message_id="msg-001",
            payload={},
        )
        with pytest.raises(IntegrityError):
            RCSWebhookEvent.objects.create(
                tenant=tenant,
                rcs_app=rcs_app,
                provider="GOOGLE_RBM",
                event_type="MESSAGE",
                provider_message_id="msg-001",
                payload={},
            )

    def test_duplicate_with_different_event_type_is_allowed(self, rcs_app, tenant):
        RCSWebhookEvent.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            provider="GOOGLE_RBM",
            event_type="MESSAGE",
            provider_message_id="same-id",
            payload={},
        )
        ev2 = RCSWebhookEvent.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            provider="GOOGLE_RBM",
            event_type="DELIVERED",
            provider_message_id="same-id",
            payload={},
        )
        assert ev2.pk is not None

    def test_default_is_processed_false(self, rcs_app, tenant):
        ev = RCSWebhookEvent.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            provider="GOOGLE_RBM",
            event_type="MESSAGE",
            provider_message_id="msg-002",
            payload={},
        )
        assert ev.is_processed is False


@pytest.mark.django_db
class TestRCSOutboundMessage:
    def test_creates_with_default_status_pending(self, rcs_app, tenant):
        msg = RCSOutboundMessage.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            to_phone="+14155550001",
        )
        assert msg.status == "PENDING"

    def test_default_message_type_text(self, rcs_app, tenant):
        msg = RCSOutboundMessage.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            to_phone="+14155550002",
        )
        assert msg.message_type == "TEXT"

    def test_ordering_newest_first(self, rcs_app, tenant):
        m1 = RCSOutboundMessage.objects.create(tenant=tenant, rcs_app=rcs_app, to_phone="+111")
        m2 = RCSOutboundMessage.objects.create(tenant=tenant, rcs_app=rcs_app, to_phone="+222")
        RCSOutboundMessage.objects.filter(pk=m1.pk).update(created_at=timezone.now() - timedelta(seconds=1))
        qs = list(RCSOutboundMessage.objects.filter(rcs_app=rcs_app))
        assert qs[0].pk == m2.pk
        assert qs[1].pk == m1.pk

    def test_str_contains_phone_and_status(self, rcs_app, tenant):
        msg = RCSOutboundMessage.objects.create(
            tenant=tenant,
            rcs_app=rcs_app,
            to_phone="+14155553333",
        )
        text = str(msg)
        assert "+14155553333" in text
        assert "PENDING" in text
