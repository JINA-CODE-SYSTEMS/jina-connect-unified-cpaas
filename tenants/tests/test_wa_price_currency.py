"""WhatsApp prices carry the deployment's currency (#263).

Three separate places discarded or mislabelled a currency, all rooted in the
same constraint: a `MoneyField`'s `default_currency` is fixed at
class-definition time and frozen into the migration, so it cannot follow
`PLATFORM_DEFAULT_CURRENCY`.

The sharpest of the three was not a wrong label but a silent reinterpretation:
`.amount` was taken off the Money and the resulting total relabelled with the
wallet's currency, so a price configured as `$0.10` was charged as `0.10` of
whatever the wallet held. No FX step, no error.

HOW TO RUN:
    .venv/bin/python -m pytest tenants/tests/test_wa_price_currency.py -v
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from djmoney.money import Money

from tenants.models import Tenant, TenantWAApp


def _app(tenant, **overrides):
    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": f"a-{uuid.uuid4().hex[:6]}",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
    }
    fields.update(overrides)
    return TenantWAApp.objects.create(**fields)


# ─────────────────────────────────────────────────────────────────────────────
# A new app is priced in the deployment's currency
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_prices_take_the_platform_currency(settings):
    settings.PLATFORM_DEFAULT_CURRENCY = "ZAR"
    app = _app(Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}"))

    assert str(app.authentication_message_price.currency) == "ZAR"
    assert str(app.marketing_message_price.currency) == "ZAR"
    assert str(app.utility_message_price.currency) == "ZAR"


@pytest.mark.django_db
def test_the_default_amounts_are_untouched(settings):
    """Stamping carries the amount across; it is not a conversion."""
    settings.PLATFORM_DEFAULT_CURRENCY = "ZAR"
    app = _app(Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}"))

    assert app.marketing_message_price.amount == Decimal("0.1")


@pytest.mark.django_db
def test_an_explicit_currency_wins(settings):
    """The caller's intent beats the setting, as with wallets."""
    settings.PLATFORM_DEFAULT_CURRENCY = "ZAR"
    app = _app(
        Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}"),
        marketing_message_price=Money(Decimal("0.25"), "GBP"),
    )

    assert str(app.marketing_message_price.currency) == "GBP"
    assert str(app.authentication_message_price.currency) == "GBP", "the others must follow, not split"


@pytest.mark.django_db
def test_an_existing_app_is_not_restamped(settings):
    """Only creation stamps. A later setting change must not silently relabel."""
    settings.PLATFORM_DEFAULT_CURRENCY = "ZAR"
    app = _app(Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}"))

    settings.PLATFORM_DEFAULT_CURRENCY = "GBP"
    app.save()
    app.refresh_from_db()

    assert str(app.marketing_message_price.currency) == "ZAR"


@pytest.mark.django_db
def test_a_usd_deployment_is_unchanged(settings):
    settings.PLATFORM_DEFAULT_CURRENCY = "USD"
    app = _app(Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}"))

    assert str(app.marketing_message_price.currency) == "USD"


# ─────────────────────────────────────────────────────────────────────────────
# A mismatch is refused rather than reinterpreted
# ─────────────────────────────────────────────────────────────────────────────


def _priced_broadcast(currency_of_prices: str, wallet_currency: str):
    """A broadcast whose template's app prices may disagree with the wallet."""
    from django.utils import timezone

    from broadcast.models import Broadcast, BroadcastPlatformChoices
    from message_templates.models import TemplateNumber
    from wa.models import WATemplate

    tenant = Tenant.objects.create(name=f"T-{uuid.uuid4().hex[:6]}")
    tenant.balance = Money(Decimal("100"), wallet_currency)
    tenant.credit_line = Money(0, wallet_currency)
    tenant.threshold_alert = Money(0, wallet_currency)
    tenant.save()

    app = _app(
        tenant,
        marketing_message_price=Money(Decimal("0.10"), currency_of_prices),
        authentication_message_price=Money(Decimal("0.10"), currency_of_prices),
        utility_message_price=Money(Decimal("0.10"), currency_of_prices),
    )
    # WATemplate.number is the forward FK; ``gupshup_template`` is its reverse
    # accessor, kept under that name for the broadcast models that read it.
    number = TemplateNumber.objects.create()
    WATemplate.objects.create(
        wa_app=app,
        number=number,
        name="T",
        element_name=f"t_{uuid.uuid4().hex[:8]}",
        language_code="en",
        category="MARKETING",
        template_type="TEXT",
        content="Hi",
        status="APPROVED",
    )
    return Broadcast.objects.create(
        tenant=tenant,
        name="B",
        platform=BroadcastPlatformChoices.WHATSAPP,
        scheduled_time=timezone.now(),
        template_number=number,
    )


@pytest.mark.django_db
def test_matching_currencies_price_normally():
    broadcast = _priced_broadcast("ZAR", "ZAR")
    assert broadcast._get_whatsapp_message_price() == Decimal("0.10")


@pytest.mark.django_db
def test_a_mismatch_raises_instead_of_repricing():
    """The bug in one line: 0.10 USD was charged as 0.10 ZAR, silently."""
    broadcast = _priced_broadcast("USD", "ZAR")

    with pytest.raises(ValueError, match="would silently reprice"):
        broadcast._get_whatsapp_message_price()


@pytest.mark.django_db
def test_the_error_names_both_currencies():
    """So the operator knows which end to change."""
    broadcast = _priced_broadcast("USD", "ZAR")

    with pytest.raises(ValueError) as exc:
        broadcast._get_whatsapp_message_price()

    assert "USD" in str(exc.value)
    assert "ZAR" in str(exc.value)
