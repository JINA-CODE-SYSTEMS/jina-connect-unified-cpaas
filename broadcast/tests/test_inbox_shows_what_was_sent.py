"""The inbox bubble must show what the customer actually received (#389).

A template body carrying a *positional* placeholder — ``{{1}}`` — sent fine and
then displayed wrong. The agent opening the conversation saw a literal
``Hi {{1}},`` on a message the customer had received with their name filled in,
so a working send looked broken and there was no way to tell a real rendering
failure from this one.

The two sides had grown their own answer to the same question:

* the send (``BroadcastMessage._build_template_components``) resolves ``{{1}}``
  with no usable mapping to the contact's name, and puts that in the parameter
  it hands the provider;
* the inbox (``render_placeholders``) matched the placeholder against
  ``placeholder_data`` + reserved vars, found nothing named ``1``, and left the
  braces where they were.

Both are defensible rules. Having two of them is the defect — this codebase has
undone that same shape three times already (#265, #333, #365). So the inbox no
longer re-derives anything: it renders from the values the send resolved, which
are by definition the ones the customer saw.

These tests run the real batch task against a stubbed Graph, so the send, the
parameter building and the inbox row are all production code, and they assert
the *agreement* between the two — the expected body is reconstructed from the
recorded outbound request rather than hardcoded, so a future change that moves
one side without the other fails here rather than in someone's inbox.

HOW TO RUN:
    python -m pytest broadcast/tests/test_inbox_shows_what_was_sent.py -v
"""

from __future__ import annotations

import itertools
import re

import pytest
from django.core.cache import cache
from django.utils import timezone

from wa.tests.meta_path import FakeGraph, meta_wa_app, tenant, wa_template

pytestmark = pytest.mark.django_db

_phone = itertools.count(1)

#: Matches a placeholder the way every renderer in this repo does.
PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_cache():
    """Send pacing and provider cooldowns live in the cache and outlive a test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_global_token(settings):
    """No deployment-wide token, so a send that resolves the wrong app fails
    loudly rather than quietly borrowing credentials."""
    settings.META_PERM_TOKEN = ""


@pytest.fixture()
def app():
    return meta_wa_app(tenant("InboxRender"), access_token="inbox-render-token")


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _contact(app, first_name="Customer", last_name="One"):
    from contacts.models import TenantContact

    n = next(_phone)
    return TenantContact.objects.create(
        tenant=app.tenant,
        phone=f"+2782{n:07d}",
        first_name=first_name,
        last_name=last_name,
    )


def _send(app, graph, template, contact, placeholder_data=None):
    """Run one real broadcast send and return the ``BroadcastMessage``."""
    from broadcast import tasks
    from broadcast.models import (
        Broadcast,
        BroadcastMessage,
        BroadcastPlatformChoices,
        BroadcastStatusChoices,
        MessageStatusChoices,
    )

    graph.post(
        f"/{app.phone_number_id}/messages",
        {
            "messaging_product": "whatsapp",
            "contacts": [{"input": str(contact.phone), "wa_id": str(contact.phone).lstrip("+")}],
            "messages": [{"id": f"wamid.{next(_phone)}", "message_status": "accepted"}],
        },
    )

    broadcast = Broadcast.objects.create(
        tenant=app.tenant,
        name="Campaign",
        platform=BroadcastPlatformChoices.WHATSAPP,
        status=BroadcastStatusChoices.SENDING,
        template_number=template.number,
        scheduled_time=timezone.now(),
        placeholder_data=placeholder_data or {},
    )
    message = BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=contact,
        status=MessageStatusChoices.PENDING,
    )
    tasks.process_broadcast_messages_batch([message.id])
    message.refresh_from_db()
    assert message.status == MessageStatusChoices.SENT, f"the send itself failed: {message.response}"
    return message


def _sent_parameters(graph, app, component_type="body"):
    """The parameter texts the provider was actually given for *component_type*.

    This is the ground truth the whole fix rests on: whatever is in here is
    what the customer's phone rendered.
    """
    call = graph.only("POST", f"/{app.phone_number_id}/messages")
    components = (call.json.get("template") or {}).get("components") or []
    for component in components:
        if (component.get("type") or "").lower() == component_type:
            return [p.get("text") for p in component.get("parameters") or []]
    return []


def _inbox_body(contact) -> str:
    from team_inbox.models import Messages

    rows = list(Messages.objects.filter(contact=contact))
    assert len(rows) == 1, f"expected exactly one inbox row, got {len(rows)}"
    return ((rows[0].content or {}).get("body") or {}).get("text") or ""


def _inbox_header(contact) -> str:
    from team_inbox.models import Messages

    row = Messages.objects.filter(contact=contact).first()
    return ((row.content or {}).get("header") or {}).get("text") or ""


def _fill(text: str, values: list[str]) -> str:
    """Substitute *text*'s placeholders, in order of appearance, from *values*.

    Used to build the expectation out of the recorded outbound request, so the
    assertion is "the two agree" rather than "the inbox says this string".
    """
    remaining = list(values)
    return PLACEHOLDER.sub(lambda m: remaining.pop(0) if remaining else m.group(0), text)


# ─────────────────────────────────────────────────────────────────────────────
# The defect
# ─────────────────────────────────────────────────────────────────────────────


def test_a_positional_placeholder_is_not_left_literal_in_the_inbox(app, graph):
    """The headline, end to end.

    ``{{1}}`` with nothing named ``1`` anywhere in the data is the exact shape
    that shipped broken: the customer got their name, the agent got braces.
    """
    template = wa_template(app, content="Hi {{1}}, your order is ready.")
    contact = _contact(app)

    _send(app, graph, template, contact)

    # The premise: the send was never wrong, so a test that only checked the
    # outbound request would have passed straight through this bug.
    assert _sent_parameters(graph, app) == ["Customer One"]

    body = _inbox_body(contact)
    assert "{{" not in body, f"the agent is still being shown a raw placeholder: {body!r}"
    assert body == "Hi Customer One, your order is ready."


def test_the_inbox_body_is_the_template_filled_with_what_was_sent(app, graph):
    """The property that keeps the two sides from drifting again.

    Nothing here hardcodes the rendered text. The expectation is assembled from
    the parameters recorded on the outbound Graph call, so this fails whenever
    the inbox and the send stop agreeing — whatever either of them decides to
    say next.
    """
    body_text = "Hi {{1}}, your order {{2}} ships today."
    template = wa_template(app, content=body_text)
    contact = _contact(app)

    # "2" is supplied, "1" is not — the mixed case, where one placeholder has
    # real data behind it and the other only has the send's fallback.
    _send(app, graph, template, contact, placeholder_data={"2": "A-1000"})

    sent = _sent_parameters(graph, app)
    assert len(sent) == 2, f"expected both body parameters on the wire, got {sent}"
    assert _inbox_body(contact) == _fill(body_text, sent)


def test_a_positional_placeholder_in_the_header_also_matches_what_was_sent(app, graph):
    """Headers resolve through the same mapping and had the same split.

    Worth its own test because the header travels a different function on the
    inbox side (``_render_template_field``), so fixing the body alone would
    leave the bug visible one line higher up the bubble.
    """
    header_text = "Order {{1}}"
    template = wa_template(app, content="Your order is ready.", header=header_text)
    contact = _contact(app)

    _send(app, graph, template, contact)

    sent = _sent_parameters(graph, app, component_type="header")
    assert sent, "no header parameters reached the provider; the premise is wrong"
    assert _inbox_header(contact) == _fill(header_text, sent)
    assert "{{" not in _inbox_header(contact)


# ─────────────────────────────────────────────────────────────────────────────
# What must not change
# ─────────────────────────────────────────────────────────────────────────────


def test_a_named_placeholder_with_data_renders_exactly_as_before(app, graph):
    """The regression guard.

    ``{{first_name}}`` already worked, and the point of rendering from the sent
    values is that it keeps working — same string, by the same agreement. This
    test passes before the fix as well as after; it is here so that a later
    change cannot buy positional support at the named case's expense.
    """
    body_text = "Hi {{first_name}}, welcome."
    template = wa_template(app, content=body_text)
    contact = _contact(app, first_name="Customer", last_name="One")

    _send(app, graph, template, contact)

    assert _sent_parameters(graph, app) == ["Customer"]
    assert _inbox_body(contact) == "Hi Customer, welcome."


def test_a_template_with_no_placeholders_is_untouched(app, graph):
    """No mapping, no parameters, nothing to reconcile — the body is the
    template. Cheap, but it is the case that proves the new path does not
    need parameters to exist before it will render anything at all."""
    template = wa_template(app, content="Your order is ready.")
    contact = _contact(app)

    _send(app, graph, template, contact)

    assert _inbox_body(contact) == "Your order is ready."
