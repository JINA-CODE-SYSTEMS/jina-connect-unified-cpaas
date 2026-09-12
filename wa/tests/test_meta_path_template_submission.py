"""Template submission, Meta path: submit → poll → webhook (#277, #272).

#277 names "no test for the Meta template webhook" as one of the five gaps,
and rates this area's lifecycle fidelity as thin. The three legs of the
lifecycle are each other's context, so they are exercised in sequence here
rather than in isolation:

    POST /wa/v2/templates/            → Graph POST /{waba}/message_templates
      (the row is DRAFT, then PENDING with Meta's id)
    adapter.get_template_status()     → Graph GET /{template_id}
      (the two-minute poller's read)
    signed POST /wa/v2/webhooks/meta/ → WAWebhookEvent → process_template_webhook
      → _process_meta_template_webhook   ← the site with no test at all

Every assertion is on the ``WATemplate`` row after the fact, because the
defects in this area were all "a field written in one shape and read in
another": a rejection reason in ``error_message`` and not in
``rejection_reason``, a category update knocking an APPROVED template back to
PENDING, a lifecycle state with no mapping falling through to "keep the
current status". None of those change the shape of the call — only the row.

HOW TO RUN:
    python -m pytest wa/tests/test_meta_path_template_submission.py -v
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from wa.tests.meta_path import (
    APP_SECRET,
    FakeGraph,
    FakeResponse,
    assert_meta_call,
    meta_wa_app,
    run_webhooks_in_process,
    sign_meta_webhook,
    template_status_envelope,
    tenant,
    wa_template,
)

pytestmark = pytest.mark.django_db

TOKEN = "submission-tenant-token"
META_ID = "1234567890"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _meta_env(settings, tmp_path):
    settings.META_APP_SECRET = APP_SECRET
    settings.META_PERM_TOKEN = ""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    run_webhooks_in_process(settings)


@pytest.fixture()
def owner():
    return tenant("Submission")


@pytest.fixture()
def app(owner):
    return meta_wa_app(owner, access_token=TOKEN)


@pytest.fixture()
def api(owner):
    from django.contrib.auth import get_user_model

    from tenants.models import TenantRole, TenantUser

    user = get_user_model().objects.create_user(
        username=f"sub_{uuid.uuid4().hex[:8]}",
        email=f"sub_{uuid.uuid4().hex[:6]}@test.local",
        mobile=f"+91{9000000000 + uuid.uuid4().int % 999999999}",
        password="TestPass123!",
    )
    TenantUser.objects.create(
        tenant=owner,
        user=user,
        role=TenantRole.objects.get(tenant=owner, slug="owner"),
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


def _create_payload(app, **overrides):
    payload = {
        "wa_app": str(app.id),
        "name": "Order Update",
        "element_name": f"order_update_{uuid.uuid4().hex[:6]}",
        "language_code": "en_US",
        "category": "UTILITY",
        "template_type": "TEXT",
        "content": "Hi {{name}}, your order {{order_id}} has shipped.",
        "footer": "Reply STOP to opt out",
        "example_body": ["Thandi", "A-4471"],
    }
    payload.update(overrides)
    return payload


def _submitted(api, app, graph, **overrides):
    """Create a template through the API with Graph accepting the submission."""
    from wa.models import WATemplate

    graph.post(f"{app.waba_id}/message_templates", {"id": META_ID, "status": "PENDING", "category": "UTILITY"})
    response = api.post("/wa/v2/templates/", _create_payload(app, **overrides), format="json")
    assert response.status_code == 201, response.data
    return WATemplate.objects.get(pk=response.data["id"])


def _webhook(client, app, *, field, value):
    return sign_meta_webhook(client, template_status_envelope(app, field=field, value=value))


def _status_value(template, event, **extra):
    value = {
        "event": event,
        "message_template_id": int(template.meta_template_id),
        "message_template_name": template.element_name,
        "message_template_language": template.language_code,
    }
    value.update(extra)
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Leg 1 — submission
# ─────────────────────────────────────────────────────────────────────────────


def test_creating_a_template_submits_it_to_graph_and_stores_metas_id(api, app, graph):
    """The headline for this leg: one API call, one Graph create, a PENDING row.

    ``meta_template_id`` is the load-bearing field — without it the poller has
    nothing to read, the webhook has nothing to match on, and a re-submit
    re-POSTs a name Meta already holds and fails as a duplicate (#272).
    """
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)

    create = graph.only("POST", "message_templates")
    assert_meta_call(create, path=f"{app.waba_id}/message_templates", token=TOKEN)
    assert create.json["name"] == template.element_name
    assert create.json["language"] == "en_US"
    assert create.json["category"] == "UTILITY"
    assert create.headers["Content-Type"] == "application/json"

    assert template.status == TemplateStatus.PENDING
    assert template.meta_template_id == META_ID
    assert template.needs_sync is False
    assert not template.error_message


def test_the_submitted_body_carries_the_example_meta_requires(api, app, graph):
    """Placeholders without examples are refused by Meta at review time.

    "Example is required when body text contains parameters" is the error, and
    it arrives asynchronously — so a submission missing it looks successful and
    is rejected hours later (#272).
    """
    template = _submitted(api, app, graph)

    body = [c for c in graph.only("POST", "message_templates").json["components"] if str(c["type"]).upper() == "BODY"]
    assert body, "no BODY component was submitted"
    example = body[0].get("example") or {}
    assert example, f"the body has placeholders and no example: {body[0]}"
    flattened = str(example)
    assert "Thandi" in flattened and "A-4471" in flattened
    assert template.element_name  # row survived


def test_a_graph_rejection_leaves_the_row_as_a_draft_with_metas_reason(api, app, graph):
    """Meta refused the create: the template stays editable and says why.

    The row is deliberately kept — the customer's work is not thrown away
    because the provider said no — but it must not claim to be under review.
    """
    from wa.models import TemplateStatus, WATemplate

    graph.post(
        f"{app.waba_id}/message_templates",
        {"error": {"message": "(#100) Invalid parameter: body text is too long", "code": 100}},
    )

    response = api.post("/wa/v2/templates/", _create_payload(app), format="json")

    assert response.status_code == 201, "the local row is kept even when the BSP refuses"
    template = WATemplate.objects.get(pk=response.data["id"])
    assert template.status == TemplateStatus.DRAFT
    assert not template.meta_template_id
    assert "body text is too long" in template.error_message


def test_a_duplicate_name_tells_the_operator_to_adopt_rather_than_rename(api, app, graph):
    """Meta holds the name and we hold no id — the dashboard-created case.

    Gupshup's answer was to bump the name to ``_v2``, which on Meta would leave
    two live templates on the WABA when one only needs adopting. The message
    has to name the remedy because nothing else will.
    """
    from wa.models import WATemplate

    graph.post(
        f"{app.waba_id}/message_templates",
        {"error": {"message": "A template with this name already exists", "code": 2388023}},
    )

    response = api.post("/wa/v2/templates/", _create_payload(app), format="json")

    template = WATemplate.objects.get(pk=response.data["id"])
    assert "sync-from-bsp" in template.error_message
    assert not template.meta_template_id


def test_resubmitting_edits_in_place_instead_of_re_creating(api, app, graph):
    """A template Meta already holds is amended at ``POST /{template_id}``.

    Meta has no "resubmit": re-POSTing the create endpoint with a name it holds
    fails as a duplicate, which is what made a rejected template unfixable
    (#272). ``name`` and ``language`` are not editable and must not be sent.
    """
    from wa.adapters import get_bsp_adapter
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)
    template.status = TemplateStatus.REJECTED
    template.content = "Hi {{name}}, your order {{order_id}} is on its way."
    template.save(update_fields=["status", "content"])

    graph.post(f"/{META_ID}", {"success": True})
    result = get_bsp_adapter(app).submit_template(template)

    assert result.success, result.error_message
    assert len(graph.all("POST", "message_templates")) == 1, "create was called again for a template Meta holds"

    edit = graph.only("POST", f"/{META_ID}")
    assert edit.path == META_ID
    assert "name" not in edit.json, "name is not editable and Graph rejects the request if it is sent"
    assert "language" not in edit.json
    assert edit.json["components"]

    template.refresh_from_db()
    assert template.status == TemplateStatus.PENDING, "an edit re-opens review"
    assert template.meta_template_id == META_ID, "the id must survive an edit response that carries none"


# ─────────────────────────────────────────────────────────────────────────────
# Leg 2 — the poller's read
# ─────────────────────────────────────────────────────────────────────────────


def test_polling_reads_the_status_back_from_graph(api, app, graph):
    """``GET /{template_id}`` with the fields the parser actually reads."""
    from wa.adapters import get_bsp_adapter
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)
    graph.get(f"/{META_ID}", {"id": META_ID, "name": template.element_name, "status": "APPROVED"})

    result = get_bsp_adapter(app).get_template_status(template)

    assert result.success
    read = graph.only("GET", f"/{META_ID}")
    assert_meta_call(read, path=META_ID, token=TOKEN)
    assert "rejected_reason" in read.params["fields"]

    template.refresh_from_db()
    assert template.status == TemplateStatus.APPROVED
    assert template.last_synced_at is not None


@pytest.mark.parametrize(
    ("meta_status", "expected"),
    [
        ("IN_APPEAL", "PENDING"),
        ("PENDING_DELETION", "DISABLED"),
        ("DELETED", "DISABLED"),
        ("ARCHIVED", "DISABLED"),
        ("LIMIT_EXCEEDED", "FAILED"),
        ("PAUSED", "PAUSED"),
    ],
)
def test_every_lifecycle_state_meta_reports_has_an_answer(api, app, graph, meta_status, expected):
    """Meta's lifecycle is wider than the six states the model was built for.

    The unmapped ones fell through to "keep the current status", so a DELETED
    template stayed APPROVED and kept being offered to chat flows, and an
    IN_APPEAL one stayed PENDING and was re-polled every two minutes for ever
    (#272). Parametrised over the states rather than asserting the dict, so a
    state removed from the map fails here too.
    """
    from wa.adapters import get_bsp_adapter

    template = _submitted(api, app, graph)
    graph.get(f"/{META_ID}", {"id": META_ID, "status": meta_status})

    get_bsp_adapter(app).get_template_status(template)

    template.refresh_from_db()
    assert template.status == expected, f"Meta said {meta_status}, the row says {template.status}"


def test_a_rejection_read_by_the_poller_has_a_reason_the_ui_can_show(api, app, graph):
    """``rejected_reason`` over ``quality_score.reasons``, and "NONE" is no reason.

    ``quality_score`` can come back null and the old lookup asked it for
    ``reasons`` before checking; Meta also says "NONE" when it declines to give
    a reason, which is not a reason reading "NONE".
    """
    from wa.adapters import get_bsp_adapter
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)
    graph.get(
        f"/{META_ID}",
        {"id": META_ID, "status": "REJECTED", "rejected_reason": "INVALID_FORMAT", "quality_score": None},
    )

    get_bsp_adapter(app).get_template_status(template)

    template.refresh_from_db()
    assert template.status == TemplateStatus.REJECTED
    assert template.rejection_reason == "INVALID_FORMAT"


def test_a_rejection_with_no_reason_is_not_recorded_as_the_word_none(api, app, graph):
    from wa.adapters import get_bsp_adapter

    template = _submitted(api, app, graph)
    graph.get(f"/{META_ID}", {"id": META_ID, "status": "REJECTED", "rejected_reason": "NONE"})

    get_bsp_adapter(app).get_template_status(template)

    template.refresh_from_db()
    assert template.rejection_reason is None


# ─────────────────────────────────────────────────────────────────────────────
# Leg 3 — the Meta template webhook, which had no test at all
# ─────────────────────────────────────────────────────────────────────────────


def test_an_approval_webhook_moves_the_template_to_approved(client, api, app, graph):
    """The gap #277 names, end to end from a signed delivery.

    The view classifies ``message_template_status_update`` as TEMPLATE, stores
    the event against the Meta app, and the signal runs
    ``process_template_webhook``, which branches on ``bsp == META``. Each of
    those three steps is a place the delivery could be dropped without anything
    saying so; the row is the only proof it was not.
    """
    from wa.models import TemplateStatus, WAWebhookEvent

    template = _submitted(api, app, graph)

    response = _webhook(client, app, field="message_template_status_update", value=_status_value(template, "APPROVED"))

    assert response.status_code == 200
    assert response.json()["event_type"] == "TEMPLATE"

    event = WAWebhookEvent.objects.get(event_type="TEMPLATE")
    assert event.wa_app_id == app.pk
    assert event.is_processed is True

    template.refresh_from_db()
    assert template.status == TemplateStatus.APPROVED


def test_a_rejection_webhook_writes_the_reason_where_the_ui_reads_it(client, api, app, graph):
    """Both fields, because they are read by different things.

    Writing only ``error_message`` left ``rejection_reason`` null for ever: the
    cron that would have filled it in polls PENDING rows, and this one is now
    REJECTED (#272). The UI shows ``rejection_reason``, so the customer saw a
    rejected template with no stated reason.
    """
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)

    _webhook(
        client,
        app,
        field="message_template_status_update",
        value=_status_value(template, "REJECTED", reason="INCORRECT_CATEGORY"),
    )

    template.refresh_from_db()
    assert template.status == TemplateStatus.REJECTED
    assert template.rejection_reason == "INCORRECT_CATEGORY"
    assert template.error_message == "INCORRECT_CATEGORY"


def test_an_approval_clears_a_reason_an_earlier_rejection_left(client, api, app, graph):
    """A stale verdict next to an APPROVED template is worse than none."""
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)
    _webhook(
        client,
        app,
        field="message_template_status_update",
        value=_status_value(template, "REJECTED", reason="INCORRECT_CATEGORY"),
    )

    _webhook(client, app, field="message_template_status_update", value=_status_value(template, "APPROVED"))

    template.refresh_from_db()
    assert template.status == TemplateStatus.APPROVED
    assert template.rejection_reason is None


def test_a_category_update_does_not_knock_the_template_out_of_every_flow(client, api, app, graph):
    """Meta re-categorises an approved template without re-opening review.

    Moving it to PENDING is Gupshup's semantics. On Meta it took the template
    out of every chat flow — they gate on APPROVED — until the two-minute cron
    polled it back (#272). So the category moves and the status must not.
    """
    from wa.models import TemplateCategory, TemplateStatus

    template = _submitted(api, app, graph)
    template.status = TemplateStatus.APPROVED
    template.save(update_fields=["status"])

    _webhook(
        client,
        app,
        field="template_category_update",
        value={
            "message_template_id": int(META_ID),
            "message_template_name": template.element_name,
            "message_template_language": template.language_code,
            "previous_category": "UTILITY",
            "new_category": "MARKETING",
        },
    )

    template.refresh_from_db()
    assert template.category == TemplateCategory.MARKETING
    assert template.status == TemplateStatus.APPROVED, "a re-categorisation is not a new review"


def test_a_quality_webhook_records_the_score_without_changing_the_status(client, api, app, graph):
    """How a template dies: the score falls to RED and Meta then pauses it.

    The event was unclassified and discarded, so the pause was the first
    visible sign (#267). The status is deliberately untouched — a quality drop
    is a warning, and PAUSED arrives on its own event.
    """
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)
    template.status = TemplateStatus.APPROVED
    template.save(update_fields=["status"])

    _webhook(
        client,
        app,
        field="message_template_quality_update",
        value={
            "message_template_id": int(META_ID),
            "message_template_name": template.element_name,
            "message_template_language": template.language_code,
            "previous_quality_score": "GREEN",
            "new_quality_score": "RED",
        },
    )

    template.refresh_from_db()
    assert template.quality_rating == "RED"
    assert template.quality_rating_updated_at is not None
    assert template.status == TemplateStatus.APPROVED


def test_the_webhook_matches_on_metas_id_not_only_on_the_name(client, api, app, graph):
    """Two templates, same name, different languages — one row must move.

    ``(element_name, language_code)`` is unique per app, so a webhook matched
    on the name alone would update whichever row came back first and silently
    approve the wrong language.
    """
    from wa.models import TemplateStatus

    en = _submitted(api, app, graph, element_name="shared_name", language_code="en_US")
    graph.reset_routes()
    graph.post(f"{app.waba_id}/message_templates", {"id": "9999", "status": "PENDING"})
    es = _submitted(api, app, graph, element_name="shared_name", language_code="es_ES")
    assert es.meta_template_id == "9999"

    _webhook(
        client,
        app,
        field="message_template_status_update",
        value={
            "event": "APPROVED",
            "message_template_id": 9999,
            "message_template_name": "shared_name",
            "message_template_language": "es_ES",
        },
    )

    en.refresh_from_db()
    es.refresh_from_db()
    assert es.status == TemplateStatus.APPROVED
    assert en.status == TemplateStatus.PENDING, "the wrong language version was approved"


def test_a_webhook_for_a_template_we_do_not_hold_creates_a_stub(client, app, graph):
    """A template created on Meta's dashboard still has to be visible here.

    Dropping the event would leave the template live on the WABA and invisible
    to the platform — which is also how a locally deleted template's webhooks
    arrive.
    """
    from wa.models import TemplateStatus, WATemplate

    _webhook(
        client,
        app,
        field="message_template_status_update",
        value={
            "event": "APPROVED",
            "message_template_id": 555000,
            "message_template_name": "made_on_the_dashboard",
            "message_template_language": "en_US",
            "message_template_category": "UTILITY",
        },
    )

    stub = WATemplate.objects.get(wa_app=app, element_name="made_on_the_dashboard")
    assert stub.status == TemplateStatus.APPROVED
    assert stub.meta_template_id == "555000"
    assert stub.needs_sync is True, "a stub has no content yet — it must be picked up by a sync"


def test_a_template_webhook_for_an_unknown_waba_is_not_applied_anywhere(client, app, graph):
    """A delivery we cannot attribute must change nothing.

    The receiver answers 200 regardless — a non-200 throttles delivery for
    every tenant — so ``reason`` in the body and the absence of any row change
    are the only observable outcomes.
    """
    from wa.models import WATemplate, WAWebhookEvent

    payload = template_status_envelope(app, field="message_template_status_update", value={})
    payload["entry"][0]["id"] = "waba-that-is-not-ours"

    response = sign_meta_webhook(client, payload)

    assert response.status_code == 200
    assert response.json()["reason"] == "unknown_app"
    assert WAWebhookEvent.objects.count() == 0
    assert WATemplate.objects.filter(wa_app=app).count() == 0


def test_an_unsigned_template_webhook_cannot_approve_anything(client, api, app, graph):
    """The endpoint is public, so a forged approval would be a way to make any
    template sendable. Verification is what stops it (#306)."""
    from wa.models import TemplateStatus

    template = _submitted(api, app, graph)

    response = client.post(
        "/wa/v2/webhooks/meta/",
        data=template_status_envelope(
            app, field="message_template_status_update", value=_status_value(template, "APPROVED")
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    template.refresh_from_db()
    assert template.status == TemplateStatus.PENDING


# ─────────────────────────────────────────────────────────────────────────────
# Retiring a template — the row and the WABA must agree
# ─────────────────────────────────────────────────────────────────────────────


def test_deleting_a_template_deletes_it_at_meta_first(api, app, graph):
    """Dropping our row and leaving the template live on the WABA is worse
    than refusing: the name stays taken, so it can never be re-created (#272)."""
    from wa.models import WATemplate

    template = wa_template(app, element_name="retire_me", meta_template_id=META_ID)
    graph.on("DELETE", f"{app.waba_id}/message_templates", {"success": True})

    response = api.delete(f"/wa/v2/templates/{template.id}/")

    assert response.status_code in (200, 204), getattr(response, "data", response)
    deleted = graph.only("DELETE", "message_templates")
    assert deleted.params.get("name") == "retire_me"
    assert deleted.params.get("hsm_id") == META_ID, "without the id Meta deletes every language version of the name"
    assert not WATemplate.objects.filter(pk=template.pk).exists()


def test_a_meta_refusal_keeps_the_local_row(api, app, graph):
    from wa.models import WATemplate

    template = wa_template(app, element_name="cannot_retire", meta_template_id=META_ID)
    graph.on(
        "DELETE",
        f"{app.waba_id}/message_templates",
        FakeResponse({"error": {"message": "(#33) Unsupported get request", "code": 33}}, status_code=400),
    )

    api.delete(f"/wa/v2/templates/{template.id}/")

    assert WATemplate.objects.filter(pk=template.pk).exists(), "the row was dropped while the template is still live"
