"""Contact dashboard engagement figures read real inbound traffic (#294).

``GET /contacts/dashboard/`` computed its two engagement figures — the
top-segment engagement rate and the funnel's "engaged" count — from
``WAMessage`` rows with ``direction=INBOUND``. No such row is ever written:
every ``WAMessage`` creation site is an outbound send, and inbound arrives as a
``team_inbox.Messages`` row. Both figures were therefore a structural zero, not
an empty one, on every deployment at any volume.

The adjacent ``messaged`` count filters OUTBOUND and worked, so the funnel
rendered as a plausible *N messaged, 0 engaged* — a reader concludes their
campaigns get no replies, and no amount of waiting for data fixes it. That is
why this shipped: nothing asserted that an inbound message moves the number.

So these tests do exactly that, and pin the two properties most likely to be
regressed by a future edit to the same query:

  * the figures move when a contact actually replies, and
  * they stay inside the requesting tenant — a cross-tenant leak in per-tenant
    analytics is worse than the zero being fixed here.

HOW TO RUN:
    DB_NAME=jc6_metrics .venv/bin/python -m pytest contacts/tests/test_engagement_metrics.py -v
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from contacts.models import TenantContact

DASHBOARD_URL = "/contacts/dashboard/"

# The top-segment query only considers tags with at least five contacts, so a
# segment fixture has to clear that floor to be visible at all.
SEGMENT_SIZE = 5
SEGMENT_TAG = "vip"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _phone() -> str:
    return f"+1415{uuid.uuid4().int % 10**7:07d}"


def _make_tenant(name_prefix: str):
    """A tenant plus an authenticated owner. The owner role and its
    ``analytics.view`` grant come from the post_save signal on Tenant."""
    from django.contrib.auth import get_user_model

    from tenants.models import Tenant, TenantRole, TenantUser

    tenant = Tenant.objects.create(name=f"{name_prefix}-{uuid.uuid4().hex[:6]}")
    user = get_user_model().objects.create_user(
        username=f"{name_prefix.lower()}_{uuid.uuid4().hex[:6]}",
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        mobile=_phone(),
        password="testpass123",
    )
    TenantUser.objects.create(
        tenant=tenant,
        user=user,
        role=TenantRole.objects.get(tenant=tenant, slug="owner"),
        is_active=True,
    )
    return tenant, user


def _make_wa_app(tenant):
    from wa.models import WAApp

    return WAApp.objects.create(
        tenant=tenant,
        app_name="Metrics App",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret="secret",
        wa_number=_phone(),
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id="PHONE_NUMBER_ID",
        bsp="META",
        bsp_credentials={"access_token": "EAAtest"},
        is_verified=True,
        is_active=True,
    )


def _make_segment(tenant, size: int = SEGMENT_SIZE, tag: str = SEGMENT_TAG):
    return [
        TenantContact.objects.create(tenant=tenant, first_name=f"C{i}", phone=_phone(), tag=tag) for i in range(size)
    ]


@pytest.fixture()
def tenant_a(db):
    return _make_tenant("MetricsA")


@pytest.fixture()
def tenant_b(db):
    return _make_tenant("MetricsB")


@pytest.fixture()
def segment_a(tenant_a):
    return _make_segment(tenant_a[0])


@pytest.fixture()
def client_a(tenant_a):
    client = APIClient()
    client.force_authenticate(user=tenant_a[1])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — the two ways a message can exist for a contact
# ─────────────────────────────────────────────────────────────────────────────


def _reply(tenant, contact, *, platform: str = "WHATSAPP", direction: str = "INCOMING", ago: timedelta | None = None):
    """Write the inbox row a real inbound produces, via the shared factory that
    every channel's ingest uses."""
    from team_inbox.models import Messages
    from team_inbox.utils.inbox_message_factory import create_inbox_message

    message = create_inbox_message(
        tenant=tenant,
        contact=contact,
        platform=platform,
        direction=direction,
        author="CONTACT" if direction == "INCOMING" else "USER",
        content={"type": "text", "body": {"text": "yes please"}},
    )
    if ago is not None:
        # ``timestamp`` is auto_now_add, so it can only be backdated by an
        # UPDATE — which is exactly how ingest stamps the BSP's reported time.
        Messages.objects.filter(pk=message.pk).update(timestamp=timezone.now() - ago)
    return message


def _outbound_send(wa_app, contact):
    """An outbound WAMessage — the ``messaged`` side of the funnel."""
    from wa.models import MessageDirection, WAMessage

    return WAMessage.objects.create(
        wa_app=wa_app,
        contact=contact,
        direction=MessageDirection.OUTBOUND,
        text="hello",
    )


def _dashboard(client, **params):
    resp = client.get(DASHBOARD_URL, params)
    assert resp.status_code == 200, resp.data
    return resp.data


# ─────────────────────────────────────────────────────────────────────────────
# The figures move when a contact replies
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestInboundMovesTheEngagementFigures:
    def test_no_inbound_means_zero(self, client_a, segment_a):
        """Baseline, so the assertions below are about the reply and not the
        fixture: with no replies at all, zero is the right answer."""
        data = _dashboard(client_a)

        assert data["funnel"]["engaged"] == 0
        assert data["top_segment"]["engagement_rate"] == 0.0

    def test_funnel_engaged_counts_replying_contacts(self, tenant_a, client_a, segment_a):
        """Two of five contacts reply — the funnel must say two, not zero."""
        tenant, _ = tenant_a
        wa_app = _make_wa_app(tenant)
        for contact in segment_a:
            _outbound_send(wa_app, contact)
        _reply(tenant, segment_a[0])
        _reply(tenant, segment_a[1])

        data = _dashboard(client_a)

        assert data["funnel"]["messaged"] == SEGMENT_SIZE
        assert data["funnel"]["engaged"] == 2
        # ``converted`` is derived from the same figure today.
        assert data["funnel"]["converted"] == 2

    def test_a_contact_who_replies_twice_is_counted_once(self, tenant_a, client_a, segment_a):
        """The count is of contacts, not messages — the join must stay distinct."""
        tenant, _ = tenant_a
        _reply(tenant, segment_a[0])
        _reply(tenant, segment_a[0])
        _reply(tenant, segment_a[0])

        assert _dashboard(client_a)["funnel"]["engaged"] == 1

    def test_top_segment_engagement_rate_reflects_replies(self, tenant_a, client_a, segment_a):
        """Two replies out of a five-contact tag is 40%, not 0%."""
        tenant, _ = tenant_a
        _reply(tenant, segment_a[0])
        _reply(tenant, segment_a[1])

        top = _dashboard(client_a)["top_segment"]

        assert top["name"] == SEGMENT_TAG
        assert top["contact_count"] == SEGMENT_SIZE
        assert top["engagement_rate"] == 40.0

    def test_the_higher_rate_segment_wins(self, tenant_a, client_a, segment_a):
        """With every rate stuck at 0.0 the "best" segment was whichever tag the
        ordering happened to surface first. Once rates are real, the comparison
        has to pick the better one."""
        tenant, _ = tenant_a
        quiet = _make_segment(tenant, tag="newsletter")
        _reply(tenant, quiet[0])  # 1/5 = 20%
        for contact in segment_a[:3]:
            _reply(tenant, contact)  # 3/5 = 60%

        top = _dashboard(client_a)["top_segment"]

        assert top["name"] == SEGMENT_TAG
        assert top["engagement_rate"] == 60.0


# ─────────────────────────────────────────────────────────────────────────────
# What must NOT count
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEngagementScoping:
    def test_another_tenants_replies_do_not_count(self, tenant_a, tenant_b, client_a, segment_a):
        """Tenant B's contacts carry the same tag and all reply. Tenant A's
        dashboard must not move — these are per-tenant analytics and the source
        table is shared across tenants."""
        tenant_b_obj, _ = tenant_b
        for contact in _make_segment(tenant_b_obj):
            _reply(tenant_b_obj, contact)

        data = _dashboard(client_a)

        assert data["funnel"]["engaged"] == 0
        assert data["top_segment"]["engagement_rate"] == 0.0

    def test_replies_before_the_period_do_not_count(self, tenant_a, client_a, segment_a):
        """The window is the selected period, so a reply from before it is out."""
        tenant, _ = tenant_a
        _reply(tenant, segment_a[0], ago=timedelta(days=10))
        _reply(tenant, segment_a[1])

        assert _dashboard(client_a, period="7d")["funnel"]["engaged"] == 1
        # Same rows, wider window — the older reply comes back into scope.
        assert _dashboard(client_a, period="30d")["funnel"]["engaged"] == 2

    def test_our_own_outgoing_inbox_rows_are_not_engagement(self, tenant_a, client_a, segment_a):
        """team_inbox holds both directions. Dropping the direction filter would
        turn every send into an engagement and make the funnel 100%."""
        tenant, _ = tenant_a
        for contact in segment_a:
            _reply(tenant, contact, direction="OUTGOING")

        assert _dashboard(client_a)["funnel"]["engaged"] == 0

    def test_non_whatsapp_inbound_is_not_counted(self, tenant_a, client_a, segment_a):
        """``messaged`` counts outbound WhatsApp, so ``engaged`` stays on
        WhatsApp too — otherwise an inbound call or SMS could report more
        engaged than messaged and invert the funnel."""
        tenant, _ = tenant_a
        _reply(tenant, segment_a[0], platform="VOICE")
        _reply(tenant, segment_a[1], platform="SMS")

        assert _dashboard(client_a)["funnel"]["engaged"] == 0
