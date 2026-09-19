"""An inbound message opens the 24-hour window before anything answers it.

Seen on the live box. A contact tapped a quick-reply button on a flow; the
flow's next session message came back **Failed**, with:

    SERVICE_WINDOW_CLOSED — The 24-hour customer service window closed at
    2026-09-19T13:16:36+00:00

That window had been reopened by the very tap being processed, 1.4 milliseconds
earlier. The conversation row proves it: ``last_inbound_at`` 14:19:44.900269,
the send refused at 14:19:44.901712.

``_ingest_inbound_message`` reacts to the message before it records what the
message means:

    _handle_chatflow_routing(contact, instance, content)   # answers
    ...
    conversation = resolve_conversation(...)               # opens the window

The window update sits inside a block commented "CTWA wiring", as though the
24-hour rule were a click-to-WhatsApp concern. It is not: it is the rule for
every inbound message, and everything that answers one depends on it.

The cost is precisely the case chat flows exist for — a contact who replies
the next day. Their reply reopens the window, and the flow's answer is refused
against the window their reply just replaced.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_the_window_opens_before_we_answer.py -v
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from contacts.models import TenantContact
from wa.models import WaConversation
from wa.services.conversations import outbound_window_error
from wa.tasks import _ingest_inbound_message


@pytest.fixture()
def inbound(db):
    from tenants.models import BSPChoices, Tenant, TenantWAApp
    from wa.models import WAWebhookEvent, WebhookEventType

    tenant = Tenant.objects.create(name=f"WindowTenant-{uuid.uuid4().hex[:6]}")
    wa_app = TenantWAApp.objects.create(
        tenant=tenant,
        app_name="t-wa",
        app_id=f"app-{uuid.uuid4().hex[:8]}",
        app_secret="s",
        wa_number=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )
    event = WAWebhookEvent.objects.create(
        wa_app=wa_app,
        event_type=WebhookEventType.MESSAGE,
        bsp=BSPChoices.META,
        payload={},
    )
    return event, f"+1415666{uuid.uuid4().int % 10000:04d}"


def _ingest(event, phone, text="Tell me more"):
    _ingest_inbound_message(
        event,
        {"contact_phone": phone, "contact_name": "Ada", "text": text},
        str(event.pk),
    )
    return TenantContact.objects.get(tenant=event.wa_app.tenant, phone=phone)


def _expired_conversation(wa_app, contact):
    """The state a contact is in when they reply a day later."""
    yesterday = timezone.now() - timedelta(hours=25)
    return WaConversation.objects.create(
        wa_app=wa_app,
        contact=contact,
        first_message_at=yesterday,
        last_inbound_at=yesterday,
        service_window_expires_at=yesterday + timedelta(hours=24),
    )


def _window_seen_by_the_responder(event, phone):
    """Ingest a message, recording the window state as the flow router sees it."""
    seen = {}

    def _spy(contact, webhook_instance, message_content):
        seen["error"] = outbound_window_error(wa_app=webhook_instance.wa_app, contact=contact)
        seen["conversation_exists"] = WaConversation.objects.filter(
            wa_app=webhook_instance.wa_app, contact=contact, closed_at__isnull=True
        ).exists()

    with patch("wa.tasks._handle_chatflow_routing", side_effect=_spy):
        contact = _ingest(event, phone)
    return contact, seen


@pytest.mark.django_db
def test_the_window_is_open_by_the_time_the_flow_answers(inbound):
    event, phone = inbound
    contact = _ingest(event, phone, "first message")
    WaConversation.objects.filter(contact=contact).delete()
    _expired_conversation(event.wa_app, contact)

    _, seen = _window_seen_by_the_responder(event, phone)

    assert seen["error"] is None


@pytest.mark.django_db
def test_a_first_ever_message_has_its_conversation_before_the_flow_answers(inbound):
    event, phone = inbound

    _, seen = _window_seen_by_the_responder(event, phone)

    assert seen["conversation_exists"] is True


@pytest.mark.django_db
def test_the_inbound_still_extends_the_window(inbound):
    """Preserved: the window runs 24h from this message, not from the old one."""
    event, phone = inbound
    contact = _ingest(event, phone, "first message")

    before = WaConversation.objects.get(contact=contact, closed_at__isnull=True).service_window_expires_at
    _ingest(event, phone, "second message")
    after = WaConversation.objects.get(contact=contact, closed_at__isnull=True).service_window_expires_at

    assert after > before


@pytest.mark.django_db
def test_an_expired_window_is_replaced_rather_than_extended(inbound):
    """Preserved: the lapsed conversation is closed, a fresh one opened."""
    event, phone = inbound
    contact = _ingest(event, phone, "first message")
    WaConversation.objects.filter(contact=contact).delete()
    lapsed = _expired_conversation(event.wa_app, contact)

    _ingest(event, phone, "next day")

    lapsed.refresh_from_db()
    assert lapsed.closed_at is not None
    assert WaConversation.objects.filter(contact=contact, closed_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_a_broken_window_update_does_not_cost_us_the_message(inbound):
    """It runs earlier now, so it must still be unable to break ingestion."""
    from team_inbox.models import Messages

    event, phone = inbound

    with patch("wa.services.conversations.resolve_or_create", side_effect=RuntimeError("db is sulking")):
        contact = _ingest(event, phone, "hello")

    assert Messages.objects.filter(contact=contact).exists()
