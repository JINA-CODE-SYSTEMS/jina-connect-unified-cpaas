"""Marketing opt-out keywords and the contact flag they set (#276).

There was no opt-out path at all: no keyword handler, no per-contact flag, no
suppression before dispatch. Meta does not answer that with an API error — it
answers it with a falling quality rating and a lower messaging tier, which
reaches every tenant sharing the number.

Two properties are load-bearing and easy to regress, so they are pinned here:

  * the keyword list comes from configuration, not from English built into the
    code — this platform is white-labelled and deployments do not all speak it;
  * the whole message must be the keyword, because a wrongly suppressed
    contact goes quiet and nobody finds out until they ask why.

HOW TO RUN:
    .venv/bin/python -m pytest contacts/tests/test_marketing_opt_out.py -v
"""

from __future__ import annotations

import uuid

import pytest
from django.test import override_settings

from contacts.models import MarketingOptOutSource, TenantContact
from contacts.opt_out import OPT_IN, OPT_OUT, apply_inbound_keyword, classify_inbound_keyword


@pytest.fixture()
def contact(db):
    from tenants.models import Tenant

    tenant = Tenant.objects.create(name=f"OptOutTenant-{uuid.uuid4().hex[:6]}")
    return TenantContact.objects.create(
        tenant=tenant,
        first_name="Ada",
        phone=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Recognising the keyword
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["STOP", "stop", " Stop ", "STOP.", "stop!", "STOP 🛑", "UNSUBSCRIBE", "opt out", "OPT  OUT", "optout"],
)
def test_a_stop_message_is_an_opt_out(text):
    assert classify_inbound_keyword(text) == OPT_OUT


@pytest.mark.parametrize("text", ["START", "start", "unstop", "SUBSCRIBE", "opt in", "optin"])
def test_a_start_message_is_an_opt_in(text):
    assert classify_inbound_keyword(text) == OPT_IN


@pytest.mark.parametrize(
    "text",
    [
        "please don't stop sending me these",
        "stop by the shop tomorrow",
        "I want to unsubscribe from the other one, not this",
        "where do I start?",
        "hi",
        "",
        None,
    ],
)
def test_ordinary_conversation_is_left_alone(text):
    """The whole message must be the keyword. Substring matching would suppress
    a contact who said the opposite of STOP, and silence is not a visible bug."""
    assert classify_inbound_keyword(text) is None


# ─────────────────────────────────────────────────────────────────────────────
# Configurable per locale — the property that keeps this out of English-only
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(MARKETING_OPT_OUT_KEYWORDS={"default": [], "pt": ["parar", "sair"]})
def test_a_locale_keyword_opts_out_and_the_built_in_one_can_be_removed():
    """A deployment that serves another language configures it and it works —
    and can drop the English words entirely, proving nothing is hardcoded."""
    assert classify_inbound_keyword("PARAR") == OPT_OUT
    assert classify_inbound_keyword("sair") == OPT_OUT
    assert classify_inbound_keyword("stop") is None


@override_settings(MARKETING_OPT_OUT_KEYWORDS={"default": ["stop"], "el": ["σταματα"], "ru": ["стоп"]})
def test_non_latin_keywords_survive_normalisation():
    """Normalising by stripping everything outside [a-z0-9] — the obvious
    shortcut — erases a non-Latin keyword completely and the handler silently
    stops working for that locale."""
    assert classify_inbound_keyword("ΣΤΑΜΑΤΑ") == OPT_OUT
    assert classify_inbound_keyword("Стоп!") == OPT_OUT
    assert classify_inbound_keyword("stop") == OPT_OUT


@override_settings(
    MARKETING_OPT_OUT_KEYWORDS={"default": ["stop"]},
    MARKETING_OPT_IN_KEYWORDS={"default": ["stop", "start"]},
)
def test_a_word_in_both_lists_suppresses_rather_than_resubscribes():
    """A misconfiguration should fail towards the quieter outcome."""
    assert classify_inbound_keyword("stop") == OPT_OUT
    assert classify_inbound_keyword("start") == OPT_IN


@override_settings(MARKETING_OPT_OUT_KEYWORDS={})
def test_an_empty_configuration_matches_nothing_rather_than_exploding():
    assert classify_inbound_keyword("stop") is None


# ─────────────────────────────────────────────────────────────────────────────
# What it records on the contact
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_opt_out_is_visible_on_the_contact_record(contact):
    assert apply_inbound_keyword(contact, "STOP") == OPT_OUT

    contact.refresh_from_db()
    assert contact.marketing_opt_out is True
    assert contact.marketing_opt_out_at is not None
    assert contact.marketing_opt_out_source == MarketingOptOutSource.KEYWORD


@pytest.mark.django_db
def test_a_repeated_stop_keeps_the_date_the_contact_first_asked(contact):
    """Customers send STOP more than once. Restamping would lose the only date
    that matters if the opt-out is ever questioned."""
    apply_inbound_keyword(contact, "STOP")
    contact.refresh_from_db()
    first_asked = contact.marketing_opt_out_at

    apply_inbound_keyword(contact, "stop")
    contact.refresh_from_db()

    assert contact.marketing_opt_out_at == first_asked


@pytest.mark.django_db
def test_start_puts_the_contact_back_on_the_list(contact):
    apply_inbound_keyword(contact, "STOP")

    assert apply_inbound_keyword(contact, "START") == OPT_IN

    contact.refresh_from_db()
    assert contact.marketing_opt_out is False
    assert contact.marketing_opt_out_source == MarketingOptOutSource.KEYWORD


@pytest.mark.django_db
def test_ordinary_traffic_does_not_touch_the_flag(contact):
    contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.AGENT)

    assert apply_inbound_keyword(contact, "when does the sale start on Friday?") is None

    contact.refresh_from_db()
    assert contact.marketing_opt_out is True
    assert contact.marketing_opt_out_source == MarketingOptOutSource.AGENT


@pytest.mark.django_db
def test_an_agent_setting_the_flag_through_the_api_is_stamped_as_such(contact):
    """The API cannot post its own timestamp or source, so an opt-out made in
    the UI must still say when it happened and that a person made it."""
    from contacts.serializers import TenantContactSerializer

    serializer = TenantContactSerializer(
        contact,
        data={"marketing_opt_out": True, "marketing_opt_out_source": "KEYWORD"},
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    contact.refresh_from_db()
    assert contact.marketing_opt_out is True
    assert contact.marketing_opt_out_at is not None
    assert contact.marketing_opt_out_source == MarketingOptOutSource.AGENT


@pytest.mark.django_db
def test_the_source_records_who_set_it(contact):
    """A keyword opt-out is the contact's own word; an agent's is not. The
    record has to say which."""
    assert contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.AGENT) is True
    assert contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.KEYWORD) is False

    contact.refresh_from_db()
    assert contact.marketing_opt_out_source == MarketingOptOutSource.AGENT
