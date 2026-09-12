"""A STOP that arrives over WhatsApp actually unsubscribes the contact (#276).

The keyword matcher has its own unit tests in ``contacts/tests``. What is
tested here is the join: that inbound ingestion calls it at all, with the text
the customer really typed, pulled out of the content shape WhatsApp delivers.
A matcher nobody calls is exactly the failure mode #270 shipped — every
component passing its own tests while the place they meet did nothing.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_opt_out_keyword_inbound.py -v
"""

from __future__ import annotations

import uuid

import pytest

from contacts.models import MarketingOptOutSource, TenantContact
from wa.tasks import _ingest_inbound_message


@pytest.fixture()
def inbound(db):
    """A webhook event and the phone a customer will message us from."""
    from tenants.models import BSPChoices, Tenant, TenantWAApp
    from wa.models import WAWebhookEvent, WebhookEventType

    tenant = Tenant.objects.create(name=f"OptOutInboundTenant-{uuid.uuid4().hex[:6]}")
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


def _ingest(event, phone, text):
    _ingest_inbound_message(
        event,
        {"contact_phone": phone, "contact_name": "Ada", "text": text},
        str(event.pk),
    )
    return TenantContact.objects.get(tenant=event.wa_app.tenant, phone=phone)


@pytest.mark.django_db
def test_an_inbound_stop_opts_the_contact_out(inbound):
    event, phone = inbound

    contact = _ingest(event, phone, "STOP")

    assert contact.marketing_opt_out is True
    assert contact.marketing_opt_out_at is not None
    assert contact.marketing_opt_out_source == MarketingOptOutSource.KEYWORD


@pytest.mark.django_db
def test_the_stop_itself_is_still_written_to_the_inbox(inbound):
    """The contact's own request is the evidence the opt-out happened, so it
    has to survive on the record rather than being swallowed by the handler."""
    from team_inbox.models import Messages

    event, phone = inbound
    contact = _ingest(event, phone, "STOP")

    message = Messages.objects.filter(contact=contact).first()
    assert message is not None
    assert message.content["body"]["text"] == "STOP"


@pytest.mark.django_db
def test_an_inbound_start_puts_the_contact_back(inbound):
    event, phone = inbound
    # Opt out through the model rather than through a STOP, so this asserts
    # that START is wired up rather than inheriting the previous test's answer.
    contact = _ingest(event, phone, "hello")
    contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.AGENT)

    contact = _ingest(event, phone, "START")

    assert contact.marketing_opt_out is False


@pytest.mark.django_db
def test_ordinary_inbound_leaves_the_flag_alone(inbound):
    event, phone = inbound

    contact = _ingest(event, phone, "hi, is the shop open on Sunday?")

    assert contact.marketing_opt_out is False
    assert contact.marketing_opt_out_at is None


@pytest.mark.django_db
def test_a_caption_on_a_photo_counts_too(inbound):
    """Media inbounds carry the words in a caption, not a body — the same trap
    that made keyword triggers inert on every media message (#270)."""
    event, phone = inbound

    _ingest_inbound_message(
        event,
        {
            "contact_phone": phone,
            "contact_name": "Ada",
            "image_link": "https://example.invalid/i.jpg",
            "text": "stop",
        },
        str(event.pk),
    )

    contact = TenantContact.objects.get(tenant=event.wa_app.tenant, phone=phone)
    assert contact.marketing_opt_out is True
