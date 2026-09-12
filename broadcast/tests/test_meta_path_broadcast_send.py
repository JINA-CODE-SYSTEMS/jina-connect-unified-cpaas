"""Broadcast, Meta path: a queued message becomes a Cloud API send (#277).

Two boundaries, both named in #277 and neither previously crossed.

**The send.** ``handle_whatsapp_message`` is what #277 calls
``_get_wa_api_for_broadcast``'s META branch: it used to pick an API client
from ``wa_app.bsp`` with ``else: Gupshup`` as the fallback, so a blank column
reached it and failed with "Gupshup credentials missing" while the adapter
factory, reading the same column, returned Meta Direct (#265). It now routes
through ``get_bsp_adapter``. The existing broadcast tests — correctly, for
what they are about — replace that factory with a ``MagicMock``, which means
none of them can tell a Meta send from a Gupshup one, or from no send at all.
Here the factory, the adapter and the Cloud API client are all real and the
only stub is ``requests``; the Graph URL, the bearer token and the JSON body
are the assertions, and the row the task persisted is the result.

**The 50-recipient cap.** ``messaging_limit`` was written only by a
Gupshup-shaped parser, so on Meta Direct it stayed NULL and ``tier_limit``
fell back to 50 — enforced as a hard ``ValidationError`` at broadcast create
(#267). The fix added a Meta-shaped read. The seam is between that read and
the quota that consumes it, so the test runs the real hourly sync against a
mocked Graph and then asks ``QuotaService`` — which is what the broadcast
serializer asks — whether 200 recipients are allowed.

HOW TO RUN:
    python -m pytest broadcast/tests/test_meta_path_broadcast_send.py -v
"""

from __future__ import annotations

import itertools

import pytest
from django.core.cache import cache
from django.utils import timezone

from wa.tests.meta_path import (
    FakeGraph,
    FakeResponse,
    assert_meta_call,
    meta_wa_app,
    tenant,
    wa_template,
)

pytestmark = pytest.mark.django_db

TOKEN = "broadcast-tenant-token"
WAMID = "wamid.HBgLMjc4MjEyMzQ1NjcVAgARGBI5"

_phone = itertools.count(1)


@pytest.fixture(autouse=True)
def _clean_cache():
    """The pacing window and cooldowns live in the cache, which outlives a test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def app():
    return meta_wa_app(tenant("Broadcast"), access_token=TOKEN)


@pytest.fixture(autouse=True)
def _no_global_token(settings):
    """A Meta app with no per-app token would otherwise fall back to the
    deployment-wide ``META_PERM_TOKEN``, and the token assertions below — the
    only thing in the request that proves *whose* credentials were used —
    would pass for the wrong reason (#275)."""
    settings.META_PERM_TOKEN = ""


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


def _template(app, **overrides):
    return wa_template(app, **overrides)


def _broadcast(app, template):
    """A SENDING broadcast on *template*.

    ``is_marketing_broadcast`` is derived from the template's category, not
    settable — which is itself worth knowing here, because the opt-out
    suppression the batch loop applies keys on it.
    """
    from broadcast.models import Broadcast, BroadcastPlatformChoices, BroadcastStatusChoices

    return Broadcast.objects.create(
        tenant=app.tenant,
        name="Meta path campaign",
        platform=BroadcastPlatformChoices.WHATSAPP,
        status=BroadcastStatusChoices.SENDING,
        template_number=template.number,
        scheduled_time=timezone.now(),
    )


def _message(broadcast):
    from broadcast.models import BroadcastMessage, MessageStatusChoices
    from contacts.models import TenantContact

    n = next(_phone)
    contact = TenantContact.objects.create(
        tenant=broadcast.tenant,
        phone=f"+2782{n:07d}",
        first_name=f"Recipient{n}",
    )
    return BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=contact,
        status=MessageStatusChoices.PENDING,
    )


def _accepted(wamid: str = WAMID) -> dict:
    """What the Cloud API returns for an accepted template send."""
    return {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "27821234567", "wa_id": "27821234567"}],
        "messages": [{"id": wamid, "message_status": "accepted"}],
    }


def _run(message_ids):
    from broadcast import tasks

    return tasks.process_broadcast_messages_batch(list(message_ids))


# ─────────────────────────────────────────────────────────────────────────────
# The send really goes to Meta, as this tenant
# ─────────────────────────────────────────────────────────────────────────────


def test_a_queued_broadcast_message_is_posted_to_the_cloud_api(app, graph):
    """The headline. One message in, one Graph POST out, one SENT row.

    The path is: batch task → ``route_to_platform_handler`` →
    ``handle_whatsapp_message`` → ``_wa_app_for_broadcast`` →
    ``get_bsp_adapter`` → ``MetaDirectAdapter.send_template`` → ``TemplateAPI``
    → ``requests.post``. Nothing in it is stubbed above ``requests``, so the
    endpoint and credentials in the recorded call are the ones a real Meta
    deployment would have used.
    """
    from broadcast.models import MessageStatusChoices

    graph.post(f"/{app.phone_number_id}/messages", _accepted())
    template = _template(app)
    message = _message(_broadcast(app, template))

    result = _run([message.id])

    call = graph.only("POST", "/messages")
    assert_meta_call(call, path=f"{app.phone_number_id}/messages", token=TOKEN)

    assert result["successful"] == 1
    message.refresh_from_db()
    assert message.status == MessageStatusChoices.SENT
    assert message.message_id == WAMID, "the wamid Meta returned is what the status webhook will quote"
    assert message.sent_at is not None


def test_the_request_body_is_cloud_api_shaped(app, graph):
    """The body, as JSON, on the wire.

    The audit's encouraging half is that payloads are built once in Cloud API
    shape and need no translation — but nothing checked that, and the Pydantic
    models live in a package called ``gupshup``, so "it happens to be the same
    shape" was an assumption. ``json=`` rather than ``data=`` matters too: the
    Cloud API rejects a form-encoded template send.
    """
    graph.post(f"/{app.phone_number_id}/messages", _accepted())
    template = _template(app, element_name="meta_path_welcome", language_code="en_US")
    message = _message(_broadcast(app, template))

    _run([message.id])

    call = graph.only("POST", "/messages")
    assert call.data is None, "the Cloud API takes a JSON body, not form-encoded fields"
    body = call.json
    assert body["messaging_product"] == "whatsapp"
    assert body["recipient_type"] == "individual"
    assert body["to"] == str(message.contact.phone)
    assert body["type"] == "template"
    assert body["template"] == {"name": "meta_path_welcome", "language": {"code": "en_US"}}
    assert call.headers["Content-Type"] == "application/json"


def test_the_send_uses_the_apps_own_token_not_the_deployment_wide_one(app, graph, settings):
    """Per-tenant credentials, which is what #275 is about.

    With a global token configured as well, only an assertion on the bearer
    token distinguishes "this app's credentials were used" from "the fallback
    happened to work" — and on a multi-customer deployment the fallback means
    posting one customer's message from another's number.
    """
    settings.META_PERM_TOKEN = "deployment-wide-token"
    graph.post(f"/{app.phone_number_id}/messages", _accepted())
    message = _message(_broadcast(app, _template(app)))

    _run([message.id])

    assert graph.only("POST", "/messages").authorization == f"Bearer {TOKEN}"


def test_each_recipient_is_one_post_to_its_own_number(app, graph):
    """A batch is one request per recipient, each naming its own ``to``.

    The Cloud API has no multi-recipient send; a loop that reused one payload
    would deliver the whole broadcast to whichever contact the queryset
    returned first, and every row would still be marked SENT.
    """
    graph.post(f"/{app.phone_number_id}/messages", lambda call: _accepted(f"wamid.{call.json['to']}"))
    broadcast = _broadcast(app, _template(app))
    messages = [_message(broadcast) for _ in range(3)]

    _run([m.id for m in messages])

    posted = {c.json["to"] for c in graph.all("POST", "/messages")}
    assert posted == {str(m.contact.phone) for m in messages}

    for message in messages:
        message.refresh_from_db()
        assert message.message_id == f"wamid.{message.contact.phone}"


# ─────────────────────────────────────────────────────────────────────────────
# Failure shapes that Meta actually returns
# ─────────────────────────────────────────────────────────────────────────────


def test_a_graph_error_leaves_the_row_unsent_with_metas_own_message(app, graph):
    """A 400 from Graph is not a send, and the operator needs Meta's wording.

    ``requests`` is what raises here — the client raises on any non-2xx — so
    this also pins that the adapter turns the raised client error into an
    ``AdapterResult`` rather than letting it escape into the batch loop.
    """
    from broadcast.models import MessageStatusChoices

    graph.post(
        f"/{app.phone_number_id}/messages",
        FakeResponse(
            {"error": {"message": "(#132001) Template name does not exist in the translation", "code": 132001}},
            status_code=400,
        ),
    )
    message = _message(_broadcast(app, _template(app)))

    result = _run([message.id])

    assert result["successful"] == 0
    message.refresh_from_db()
    assert message.status != MessageStatusChoices.SENT
    assert not message.message_id
    assert "132001" in (message.response or ""), "Meta's diagnostic did not survive to the row"


def test_a_200_with_no_wamid_is_not_counted_as_sent(app, graph):
    """A send we cannot track is not a send.

    Marking the row SENT with a blank ``message_id`` is what silently disabled
    the duplicate-send guard in ``_already_sent`` (#271): a batch retry then
    re-sent every message that had no id.
    """
    from broadcast.models import MessageStatusChoices

    graph.post(f"/{app.phone_number_id}/messages", {"messaging_product": "whatsapp", "messages": []})
    message = _message(_broadcast(app, _template(app)))

    result = _run([message.id])

    assert result["successful"] == 0
    message.refresh_from_db()
    assert message.status != MessageStatusChoices.SENT


def test_a_429_is_read_off_the_graph_response_headers(app, graph, monkeypatch, settings):
    """``Retry-After`` survives the raise that carries it.

    The header exists only on the response object the client raises with, and
    the interval it names is what keeps the retry out of Meta's own window
    (#271). Asserted through a real 429 from the HTTP layer rather than a
    hand-built ``AdapterResult``, so the header's whole journey is the thing
    under test: Graph response → raised client error → ``response_headers`` →
    ``retry_after_seconds`` → cooldown → re-queue countdown.

    Eager mode is pinned off because ``_requeue_deferred`` deliberately skips
    the countdown when there is no broker, and the countdown is the assertion.
    """
    from broadcast import tasks
    from broadcast.models import MessageStatusChoices

    settings.CELERY_TASK_ALWAYS_EAGER = False
    requeued: list[dict] = []
    monkeypatch.setattr(
        tasks.process_broadcast_messages_batch,
        "apply_async",
        lambda *a, **kw: requeued.append(kw),
    )

    graph.post(
        f"/{app.phone_number_id}/messages",
        FakeResponse(
            {"error": {"message": "rate limit hit", "code": 130429}},
            status_code=429,
            headers={"Retry-After": "90"},
        ),
    )
    message = _message(_broadcast(app, _template(app)))

    result = _run([message.id])

    assert result["deferred_countdown"] == 90, f"the provider's 90s window was not honoured: {result}"
    assert requeued == [{"args": [[message.id]], "countdown": 90}]
    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING, "a 429 must not be terminal"


def test_the_429_cooldown_keeps_the_rest_of_the_batch_off_the_wire(app, graph, monkeypatch, settings):
    """One 429 takes the number out of action for the interval Meta named.

    Asserted on the recorded HTTP calls: exactly one POST reached Graph, so the
    remaining recipients were not offered to a provider that had just said no.
    This is the half that a stubbed adapter can assert too — the half it cannot
    is that the interval came off a real response header.
    """
    from broadcast import tasks

    settings.CELERY_TASK_ALWAYS_EAGER = False
    monkeypatch.setattr(tasks.process_broadcast_messages_batch, "apply_async", lambda *a, **kw: None)

    graph.post(
        f"/{app.phone_number_id}/messages",
        FakeResponse({"error": {"message": "rate limit hit"}}, status_code=429, headers={"Retry-After": "300"}),
    )
    broadcast = _broadcast(app, _template(app))
    messages = [_message(broadcast) for _ in range(3)]

    result = _run([m.id for m in messages])

    assert len(graph.all("POST", "/messages")) == 1, "Meta was asked again inside its own window"
    assert result["deferred"] == 3


def test_a_meta_app_with_no_phone_number_id_fails_before_the_request(app, graph):
    """Sending needs the number id, and the failure must name it.

    Template CRUD only needs the WABA, so an app can be configured well enough
    to submit templates and not well enough to send — which on Meta Direct is
    the difference between a working account and a silent one. Nothing is
    posted, so there is no charge and nothing to reconcile.
    """
    app.phone_number_id = ""
    app.save(update_fields=["phone_number_id"])
    message = _message(_broadcast(app, _template(app)))

    result = _run([message.id])

    assert graph.calls == [], "a request was made without a phone_number_id"
    assert result["successful"] == 0
    message.refresh_from_db()
    assert "phone_number_id" in (message.response or "")


# ─────────────────────────────────────────────────────────────────────────────
# The 50-recipient cap (#267): Graph read → WABAInfo → quota
# ─────────────────────────────────────────────────────────────────────────────


def _phone_numbers_response(app, *, tier: str, quality: str = "GREEN", throughput: str = "STANDARD") -> dict:
    """``GET /{waba_id}/phone_numbers`` as Graph returns it."""
    return {
        "data": [
            {
                "id": app.phone_number_id,
                "display_phone_number": str(app.wa_number).lstrip("+"),
                "verified_name": "Meta Path Co",
                "quality_rating": quality,
                "messaging_limit_tier": tier,
                "throughput": {"level": throughput},
            }
        ]
    }


def _sync(app, graph, *, tier: str):
    """Run the real hourly sync for *app* against a mocked Graph."""
    from wa.cron import sync_waba_info

    graph.get(f"{app.waba_id}/phone_numbers", _phone_numbers_response(app, tier=tier))
    graph.get(f"{app.waba_id}?", {"id": app.waba_id, "account_review_status": "APPROVED"})
    graph.get(f"/{app.waba_id}", {"id": app.waba_id, "account_review_status": "APPROVED"})
    return sync_waba_info()


def test_the_synced_tier_lifts_the_fifty_recipient_cap(app, graph):
    """The seam #267 is about, from the Graph read to the quota that uses it.

    Before the fix ``messaging_limit`` stayed NULL on every Meta app because
    only a Gupshup-shaped parser ever wrote it, and ``tier_limit`` answers NULL
    with the conservative 50 — which the broadcast serializer enforces as a
    hard ``ValidationError``. So a TIER_100K Meta number could not send to 51
    people.

    ``QuotaService`` is the consumer rather than the serializer because the
    serializer's own ``ValidationError`` wrapping is already covered; what was
    never covered is whether a Meta sync puts anything in the field it reads.
    """
    from tenants.models import WABAInfo
    from wa.services.quota_service import QuotaService

    before = QuotaService(app).tier_limit
    assert before == 50, "precondition: an unsynced Meta app is capped at the conservative default"

    summary = _sync(app, graph, tier="TIER_100K")
    assert summary == {"synced": 1, "failed": 0}

    read = graph.only("GET", "phone_numbers")
    assert_meta_call(read, path=f"{app.waba_id}/phone_numbers", token=TOKEN)
    assert "messaging_limit_tier" in read.params["fields"], (
        "the tier is not in Graph's default field set — asking for it is the fix"
    )

    info = WABAInfo.objects.get(wa_app=app)
    assert info.messaging_limit == WABAInfo.MessagingLimit.TIER_100K
    assert info.phone_quality == "GREEN"
    assert info.throughput == "STANDARD"
    assert info.last_sync_error is None

    app.refresh_from_db()
    assert QuotaService(app).tier_limit == 100_000

    validation = QuotaService(app).validate_broadcast(recipient_phones=[f"+2783{i:07d}" for i in range(200)])
    assert validation["is_valid"] is True, validation.get("error")
    assert validation["tier_limit"] == 100_000


def test_an_unknown_tier_is_refused_rather_than_stored(app, graph):
    """A tier string the model does not know must not be written.

    Storing it would read as a real tier in the admin while ``get_limit()``
    silently scores it 50 — the same invisible cap #267 is about, arrived at
    from the opposite direction.
    """
    from tenants.models import WABAInfo

    _sync(app, graph, tier="TIER_5M")

    info = WABAInfo.objects.get(wa_app=app)
    assert not info.messaging_limit
    assert info.phone_quality == "GREEN", "the fields Graph did report must still be stored"


def test_the_tier_is_read_for_this_number_not_whichever_comes_first(app, graph):
    """On a shared WABA the row is selected by ``phone_number_id``.

    Taking the first row would attribute another customer's tier and quality
    rating to this app, and a broadcast would then be sized against a number
    it is not sending from.
    """
    from tenants.models import WABAInfo
    from wa.cron import sync_waba_info

    graph.get(
        f"{app.waba_id}/phone_numbers",
        {
            "data": [
                {
                    "id": "someone-elses-number",
                    "quality_rating": "RED",
                    "messaging_limit_tier": "TIER_50",
                    "throughput": {"level": "NOT_APPLICABLE"},
                },
                {
                    "id": app.phone_number_id,
                    "quality_rating": "GREEN",
                    "messaging_limit_tier": "TIER_10K",
                    "throughput": {"level": "STANDARD"},
                },
            ]
        },
    )
    graph.get(f"/{app.waba_id}", {"id": app.waba_id, "account_review_status": "APPROVED"})

    sync_waba_info()

    info = WABAInfo.objects.get(wa_app=app)
    assert info.messaging_limit == WABAInfo.MessagingLimit.TIER_10K
    assert info.phone_quality == "GREEN"
