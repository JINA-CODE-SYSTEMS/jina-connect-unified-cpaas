"""META template lifecycle: pagination, rejection reasons, edit and delete (#272).

Nothing crossed the META template path end to end before this file, which is
how eight defects accumulated along it. Each test below is the one that would
have caught one of them:

  * a sync that imported META's first 25 templates and reported success;
  * a rejection reason written to the field the UI does not show as one;
  * a category change that knocked an APPROVED template out of every flow;
  * an edit that re-POSTed a name META already held;
  * a synced template that could never be submitted again;
  * a delete that dropped our row and left the template live on the WABA;
  * lifecycle states META reports and the model had no answer for.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_meta_template_lifecycle.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from wa.adapters.base import AdapterResult
from wa.adapters.meta_direct import MetaDirectAdapter

WABA = "waba-tpl-272"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _wa_app(bsp="META", **overrides):
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"TplTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": f"a-{uuid.uuid4().hex[:6]}",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": WABA,
        "phone_number_id": "pn-1",
        "bsp": bsp,
        "bsp_credentials": {"access_token": "tok-123"},
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _template(wa_app, **overrides):
    from wa.models import WATemplate

    fields = {
        "wa_app": wa_app,
        "name": f"T {uuid.uuid4().hex[:6]}",
        "element_name": f"t_{uuid.uuid4().hex[:8]}",
        "language_code": "en",
        "category": "MARKETING",
        "template_type": "TEXT",
        "content": "Hi",
        "status": "APPROVED",
        "meta_template_id": f"mt-{uuid.uuid4().hex[:8]}",
    }
    fields.update(overrides)
    return WATemplate.objects.create(**fields)


def _event(wa_app, field, value):
    """A TEMPLATE webhook event, already marked processed so the post_save
    signal does not run it before the test does."""
    from wa.models import WAWebhookEvent

    return WAWebhookEvent.objects.create(
        wa_app=wa_app,
        bsp="META",
        event_type="TEMPLATE",
        is_processed=True,
        payload={
            "object": "whatsapp_business_account",
            "entry": [{"id": WABA, "changes": [{"field": field, "value": value}]}],
        },
    )


def _graph_response(payload: dict):
    """A ``requests`` response double that only has to answer ``.json()``."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = 200
    return resp


def _meta_template(name: str, **overrides) -> dict:
    tpl = {
        "id": f"id-{name}",
        "name": name,
        "language": "en",
        "status": "APPROVED",
        "category": "MARKETING",
        "components": [{"type": "BODY", "text": "Hello"}],
    }
    tpl.update(overrides)
    return tpl


# ─────────────────────────────────────────────────────────────────────────────
# 1. list_templates paginates — a short list must never pass for a whole one
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_every_page_of_templates_is_read():
    """META returns 25 per page by default; the sync used to keep page one."""
    page_1 = {
        "data": [_meta_template(f"tpl_{i}") for i in range(25)],
        "paging": {"next": "https://graph.facebook.com/v24.0/next-cursor"},
    }
    page_2 = {"data": [_meta_template(f"tpl_{i}") for i in range(25, 31)], "paging": {}}

    adapter = MetaDirectAdapter(_wa_app())
    with patch("requests.get", side_effect=[_graph_response(page_1), _graph_response(page_2)]) as mock_get:
        result = adapter.list_templates()

    assert result.success
    assert len(result.data["templates"]) == 31
    assert mock_get.call_count == 2
    # Page two is fetched from paging.next verbatim — that URL already carries
    # the cursor, the limit and the fields.
    assert mock_get.call_args_list[1].args[0] == "https://graph.facebook.com/v24.0/next-cursor"


@pytest.mark.django_db
def test_the_first_page_asks_for_a_hundred_and_for_the_fields_we_parse():
    """Whether rejected_reason is in Graph's default set is not settled, and a
    field that is missing is silent — so it is asked for by name."""
    adapter = MetaDirectAdapter(_wa_app())
    with patch("requests.get", return_value=_graph_response({"data": []})) as mock_get:
        adapter.list_templates()

    params = mock_get.call_args.kwargs["params"]
    assert params["limit"] == 100
    assert "rejected_reason" in params["fields"]
    assert "components" in params["fields"]


@pytest.mark.django_db
def test_a_failed_page_fails_the_whole_list():
    """Reporting success with half the templates is what made the truncation
    invisible: the missing ones looked like templates that do not exist."""
    page_1 = {"data": [_meta_template("tpl_0")], "paging": {"next": "https://graph.facebook.com/v24.0/next"}}
    page_2 = {"error": {"message": "Rate limit reached", "code": 4}}

    adapter = MetaDirectAdapter(_wa_app())
    with patch("requests.get", side_effect=[_graph_response(page_1), _graph_response(page_2)]):
        result = adapter.list_templates()

    assert result.success is False
    assert "Rate limit" in result.error_message


@pytest.mark.django_db
def test_a_cursor_that_never_advances_does_not_spin_for_ever():
    forever = {"data": [_meta_template("tpl")], "paging": {"next": "https://graph.facebook.com/v24.0/same"}}

    adapter = MetaDirectAdapter(_wa_app())
    with patch("requests.get", return_value=_graph_response(forever)) as mock_get:
        result = adapter.list_templates()

    assert result.success is False
    assert mock_get.call_count == MetaDirectAdapter._MAX_TEMPLATE_PAGES


@pytest.mark.django_db
def test_sync_from_bsp_imports_templates_past_the_first_page():
    """The end-to-end version: every page reaches the database."""
    from wa.models import WATemplate
    from wa.services.template_sync import sync_templates_from_bsp

    wa_app = _wa_app()
    page_1 = {
        "data": [_meta_template(f"tpl_{i}") for i in range(25)],
        "paging": {"next": "https://graph.facebook.com/v24.0/next"},
    }
    page_2 = {"data": [_meta_template("tpl_on_page_two")], "paging": {}}

    with (
        patch("requests.get", side_effect=[_graph_response(page_1), _graph_response(page_2)]),
        patch("wa.services.template_sync._fetch_meta_media_urls", return_value=({}, {})),
    ):
        summary = sync_templates_from_bsp(wa_app)

    assert summary["total_from_bsp"] == 26
    assert summary["created"] == 26
    assert WATemplate.objects.filter(wa_app=wa_app, element_name="tpl_on_page_two").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 2. The rejection reason lands in the field that means "rejection reason"
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_rejection_webhook_fills_rejection_reason():
    """The poller only ever sees PENDING rows, so if the webhook does not
    write this field on REJECTED nothing ever does."""
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app, status="PENDING")
    event = _event(
        wa_app,
        "message_template_status_update",
        {
            "event": "REJECTED",
            "message_template_id": template.meta_template_id,
            "message_template_name": template.element_name,
            "message_template_language": "en",
            "reason": "INVALID_FORMAT",
        },
    )

    _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.status == "REJECTED"
    assert template.rejection_reason == "INVALID_FORMAT"
    assert template.error_message == "INVALID_FORMAT"


@pytest.mark.django_db
def test_approval_clears_an_earlier_rejection_reason():
    """A verdict that has been overtaken is not a verdict."""
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app, status="PENDING", rejection_reason="INVALID_FORMAT")
    event = _event(
        wa_app,
        "message_template_status_update",
        {"event": "APPROVED", "message_template_id": template.meta_template_id},
    )

    _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.status == "APPROVED"
    assert template.rejection_reason is None


@pytest.mark.django_db
def test_the_status_poll_asks_graph_for_the_rejection_fields():
    """Same unsettled default-field question as the list edge, same answer."""
    from wa.utility.apis.meta.template_api import TemplateAPI

    api = TemplateAPI(token="tok-123")
    api.waba_id = WABA
    with patch("requests.get", return_value=_graph_response({"id": "1", "status": "APPROVED"})) as mock_get:
        api.get_template_status("1")

    fields = mock_get.call_args.kwargs["params"]["fields"]
    assert "rejected_reason" in fields
    assert "quality_score" in fields


@pytest.mark.django_db
def test_a_polled_rejection_survives_a_null_quality_score():
    """``quality_score`` comes back null often enough that reading ``reasons``
    off it before checking was a live AttributeError."""
    wa_app = _wa_app()
    template = _template(wa_app, status="PENDING")

    adapter = MetaDirectAdapter(wa_app)
    response = {"id": template.meta_template_id, "status": "REJECTED", "rejected_reason": "SCAM", "quality_score": None}
    with patch.object(type(adapter), "_get_template_api") as mock_api:
        mock_api.return_value.get_template_status.return_value = response
        result = adapter.get_template_status(template)

    assert result.success
    template.refresh_from_db()
    assert template.status == "REJECTED"
    assert template.rejection_reason == "SCAM"


@pytest.mark.django_db
def test_meta_declining_to_give_a_reason_is_not_a_reason_reading_none():
    wa_app = _wa_app()
    template = _template(wa_app, status="PENDING")

    adapter = MetaDirectAdapter(wa_app)
    with patch.object(type(adapter), "_get_template_api") as mock_api:
        mock_api.return_value.get_template_status.return_value = {"status": "REJECTED", "rejected_reason": "NONE"}
        adapter.get_template_status(template)

    template.refresh_from_db()
    assert template.rejection_reason is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. A category change is not a new review
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_category_change_leaves_an_approved_template_approved():
    """Chat flows gate on APPROVED, so knocking it to PENDING took the
    template out of every live flow until the cron put it back."""
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app, status="APPROVED", category="MARKETING")
    event = _event(
        wa_app,
        "template_category_update",
        {
            "message_template_id": template.meta_template_id,
            "message_template_name": template.element_name,
            "previous_category": "MARKETING",
            "new_category": "UTILITY",
        },
    )

    with patch("wa.services.template_notifications.TemplateNotificationService.send_category_change_notification"):
        _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.category == "UTILITY"
    assert template.status == "APPROVED"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Edit instead of re-create
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_template_meta_already_knows_is_edited_not_re_created():
    """Re-POSTing create with a name META holds is how a rejected template
    became unfixable."""
    wa_app = _wa_app()
    template = _template(wa_app, status="REJECTED", meta_template_id="mt-999", content="Fixed copy")

    adapter = MetaDirectAdapter(wa_app)
    with patch.object(type(adapter), "_get_template_api") as mock_api:
        api = mock_api.return_value
        api.edit_template.return_value = {"success": True}
        result = adapter.submit_template(template)

    assert result.success
    api.edit_template.assert_called_once()
    api.apply_for_template.assert_not_called()
    assert api.edit_template.call_args.args[0] == "mt-999"

    template.refresh_from_db()
    # An edit re-opens review, and the id we already hold is the id it keeps —
    # META answers an edit with {"success": true} and no id at all.
    assert template.status == "PENDING"
    assert template.meta_template_id == "mt-999"
    assert template.needs_sync is False


@pytest.mark.django_db
def test_a_template_meta_has_never_seen_is_created():
    wa_app = _wa_app()
    template = _template(wa_app, status="DRAFT", meta_template_id=None)

    adapter = MetaDirectAdapter(wa_app)
    with patch.object(type(adapter), "_get_template_api") as mock_api:
        api = mock_api.return_value
        api.apply_for_template.return_value = {"id": "mt-new", "status": "PENDING"}
        result = adapter.submit_template(template)

    assert result.success
    api.apply_for_template.assert_called_once()
    api.edit_template.assert_not_called()
    template.refresh_from_db()
    assert template.meta_template_id == "mt-new"


@pytest.mark.django_db
def test_the_edit_payload_drops_the_fields_meta_will_not_let_us_change():
    from wa.utility.apis.meta.template_api import TemplateAPI

    api = TemplateAPI(token="tok-123")
    api.waba_id = WABA
    payload = {"name": "order_update", "language": "en", "category": "UTILITY", "components": [{"type": "body"}]}

    with patch("requests.post", return_value=_graph_response({"success": True})) as mock_post:
        api.edit_template("mt-1", payload)

    sent = mock_post.call_args.kwargs["json"]
    assert "name" not in sent
    assert "language" not in sent
    assert sent["components"] == [{"type": "body"}]
    assert mock_post.call_args.args[0].endswith("/mt-1")


@pytest.mark.django_db
def test_a_name_meta_already_holds_is_reported_as_something_an_operator_can_act_on():
    wa_app = _wa_app()
    template = _template(wa_app, status="DRAFT", meta_template_id=None, element_name="order_update")

    adapter = MetaDirectAdapter(wa_app)
    with patch.object(type(adapter), "_get_template_api") as mock_api:
        mock_api.return_value.apply_for_template.return_value = {
            "error": {"message": "Template name already exists (order_update)", "code": 100}
        }
        result = adapter.submit_template(template)

    assert result.success is False
    assert "sync-from-bsp" in result.error_message
    template.refresh_from_db()
    assert "sync-from-bsp" in template.error_message


# ─────────────────────────────────────────────────────────────────────────────
# 5. A synced template can be submitted again
# ─────────────────────────────────────────────────────────────────────────────


def test_named_examples_survive_the_trip_back_from_meta():
    """``to_meta_payload`` sends body_text_named_params; the mapper read only
    body_text, so example_body came back null and the next submit failed
    validation with "Example is required when body text contains parameters"."""
    from wa.services.template_sync import _map_meta_template

    mapped = _map_meta_template(
        {
            "id": "mt-1",
            "name": "order_update",
            "language": "en",
            "status": "APPROVED",
            "category": "UTILITY",
            "components": [
                {
                    "type": "BODY",
                    "text": "Hi {{customer_name}}, order {{order_id}} shipped",
                    "example": {
                        "body_text_named_params": [
                            # Deliberately out of body order — example_body is
                            # positional, so the mapper has to reorder.
                            {"param_name": "order_id", "example": "A-1"},
                            {"param_name": "customer_name", "example": "Alex"},
                        ]
                    },
                }
            ],
        }
    )

    assert mapped["example_body"] == ["Alex", "A-1"]


@pytest.mark.django_db
def test_a_synced_named_template_still_validates_on_the_way_back_out():
    """The round trip that matters: submit → sync → submit again."""
    from wa.services.template_sync import _map_meta_template
    from wa.utility.data_model.meta_direct import BodyComponent

    wa_app = _wa_app()
    original = _template(
        wa_app,
        content="Hi {{customer_name}}, order {{order_id}} shipped",
        example_body=["Alex", "A-1"],
    )

    # What META echoes back for that template, in META's own shape.
    sent = original.to_meta_payload()
    echoed = {
        "id": "mt-1",
        "name": original.element_name,
        "language": "en",
        "status": "APPROVED",
        "category": "UTILITY",
        "components": [
            {"type": "BODY", "text": c["text"], "example": c.get("example", {})}
            for c in sent["components"]
            if c["type"] == "body"
        ],
    }

    mapped = _map_meta_template(echoed)
    synced = _template(wa_app, content=mapped["content"], example_body=mapped["example_body"])

    body = [c for c in synced.to_meta_payload()["components"] if c["type"] == "body"][0]
    # Raises "Example is required when body text contains parameters" if the
    # examples were lost in the round trip.
    BodyComponent(**body)


@pytest.mark.django_db
def test_a_sync_does_not_wipe_the_media_handle_it_cannot_see():
    """META answers a read with a CDN URL, never the upload handle. Writing
    the absent value back meant every sync erased the one field that makes a
    media template re-submittable."""
    from wa.services.template_sync import sync_templates_from_bsp

    wa_app = _wa_app()
    template = _template(
        wa_app,
        element_name="promo_banner",
        template_type="IMAGE",
        media_handle="4::handle-we-uploaded",
        content="Hello",
    )
    listed = {
        "data": [
            _meta_template(
                "promo_banner",
                components=[
                    {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["https://cdn/img.jpg"]}},
                    {"type": "BODY", "text": "Hello"},
                ],
            )
        ],
        "paging": {},
    }

    with (
        patch("requests.get", return_value=_graph_response(listed)),
        patch("wa.services.template_sync._fetch_meta_media_urls", return_value=({}, {})),
    ):
        sync_templates_from_bsp(wa_app)

    template.refresh_from_db()
    assert template.media_handle == "4::handle-we-uploaded"


@pytest.mark.django_db
def test_media_synced_from_meta_gets_a_fresh_handle_to_submit_with():
    """The recovery upload ran behind a Gupshup-only guard, so META apps came
    out of a sync with a CDN URL and no handle."""
    from wa.services.template_sync import _patch_template_media

    wa_app = _wa_app()
    template = _template(
        wa_app,
        template_type="IMAGE",
        example_media_url="https://scontent.whatsapp.net/example.jpg",
    )

    download = MagicMock(content=b"jpeg-bytes", headers={"Content-Type": "image/jpeg"})
    download.raise_for_status.return_value = None
    adapter = MagicMock()
    adapter.supports.return_value = True
    adapter.upload_media.return_value = AdapterResult(
        success=True, provider="meta_direct", data={"handle_id": "4::fresh-handle"}
    )

    with (
        patch("wa.services.template_sync.http_requests.get", return_value=download),
        patch("wa.services.template_sync.get_bsp_adapter", return_value=adapter),
    ):
        _patch_template_media(template)

    template.refresh_from_db()
    assert template.media_handle == "4::fresh-handle"
    assert template.tenant_media is not None
    assert template.tenant_media.handle_id == "4::fresh-handle"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Deleting a template reaches META
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_delete_names_the_language_it_means():
    """``name`` on its own deletes every language version of the template."""
    wa_app = _wa_app()
    template = _template(wa_app, element_name="order_update", meta_template_id="mt-7")

    adapter = MetaDirectAdapter(wa_app)
    with patch("requests.delete", return_value=_graph_response({"success": True})) as mock_delete:
        result = adapter.delete_template(template)

    assert result.success
    assert mock_delete.call_args.kwargs["params"] == {"name": "order_update", "hsm_id": "mt-7"}


class TestTemplateDestroyEndpoint:
    """DELETE /wa/v2/templates/{id}/ — the call site that did not exist."""

    @pytest.fixture(autouse=True)
    def _setup(self, db):
        from rest_framework.test import APIClient

        from wa.tests.test_template_api_v2 import create_test_tenant_and_user, create_test_wa_app

        self.tenant, self.user, self.token = create_test_tenant_and_user(username="destroy")
        self.wa_app = create_test_wa_app(self.tenant)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def _url(self, template):
        return f"/wa/v2/templates/{template.id}/"

    def test_deleting_a_submitted_template_deletes_it_at_meta_first(self):
        from wa.models import WATemplate

        template = _template(self.wa_app, meta_template_id="mt-42")
        adapter = MagicMock()
        adapter.delete_template.return_value = AdapterResult(success=True, provider="meta_direct", data={})

        with patch("wa.viewsets.wa_template_v2.get_bsp_adapter", return_value=adapter):
            resp = self.client.delete(self._url(template))

        assert resp.status_code == 204
        adapter.delete_template.assert_called_once()
        assert not WATemplate.objects.filter(pk=template.pk).exists()

    def test_a_failed_delete_at_meta_keeps_the_local_row(self):
        """Dropping our row anyway leaves a template live on the WABA that we
        can no longer see, let alone delete."""
        from wa.models import WATemplate

        template = _template(self.wa_app, meta_template_id="mt-42")
        adapter = MagicMock()
        adapter.delete_template.return_value = AdapterResult(
            success=False, provider="meta_direct", error_message="Template is in use"
        )

        with patch("wa.viewsets.wa_template_v2.get_bsp_adapter", return_value=adapter):
            resp = self.client.delete(self._url(template))

        assert resp.status_code == 502
        assert WATemplate.objects.filter(pk=template.pk).exists()

    def test_a_draft_that_was_never_submitted_needs_no_provider_call(self):
        from wa.models import WATemplate

        template = _template(self.wa_app, status="DRAFT", meta_template_id=None)
        adapter = MagicMock()

        with patch("wa.viewsets.wa_template_v2.get_bsp_adapter", return_value=adapter):
            resp = self.client.delete(self._url(template))

        assert resp.status_code == 204
        adapter.delete_template.assert_not_called()
        assert not WATemplate.objects.filter(pk=template.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# 7. Lifecycle states META reports and the model had no answer for
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("meta_status", "expected"),
    [
        ("IN_APPEAL", "PENDING"),
        ("PENDING_DELETION", "DISABLED"),
        ("DELETED", "DISABLED"),
        ("ARCHIVED", "DISABLED"),
        ("LIMIT_EXCEEDED", "FAILED"),
    ],
)
def test_the_poller_has_an_answer_for_every_state_meta_reports(meta_status, expected):
    wa_app = _wa_app()
    template = _template(wa_app, status="PENDING")

    adapter = MetaDirectAdapter(wa_app)
    with patch.object(type(adapter), "_get_template_api") as mock_api:
        mock_api.return_value.get_template_status.return_value = {"status": meta_status}
        adapter.get_template_status(template)

    template.refresh_from_db()
    assert template.status == expected


@pytest.mark.django_db
def test_a_state_we_still_do_not_know_keeps_the_status_and_says_so():
    """Keeping the status is the right answer; keeping it silently is not —
    that is how the state META had moved to stayed invisible."""
    wa_app = _wa_app()
    template = _template(wa_app, status="PENDING")

    adapter = MetaDirectAdapter(wa_app)
    with patch.object(type(adapter), "_get_template_api") as mock_api, patch.object(adapter, "_log") as mock_log:
        mock_api.return_value.get_template_status.return_value = {"status": "SOME_FUTURE_STATE"}
        adapter.get_template_status(template)

    template.refresh_from_db()
    assert template.status == "PENDING"
    warnings = [c.args[1] for c in mock_log.call_args_list if c.args[0] == "warning"]
    assert any("SOME_FUTURE_STATE" in w for w in warnings)


@pytest.mark.django_db
def test_a_deletion_webhook_takes_the_template_out_of_service():
    """It stayed APPROVED, so flows kept offering a template META had deleted."""
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app, status="APPROVED")
    event = _event(
        wa_app,
        "message_template_status_update",
        {"event": "DELETED", "message_template_id": template.meta_template_id},
    )

    _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.status == "DISABLED"


def test_the_sync_mapper_does_not_call_an_archived_template_pending():
    """Mapping an end state to PENDING hands it to the cron, which then polls
    it every two minutes for ever."""
    from wa.services.template_sync import _map_meta_template

    assert _map_meta_template(_meta_template("t", status="ARCHIVED"))["status"] == "DISABLED"
    assert _map_meta_template(_meta_template("t", status="IN_APPEAL"))["status"] == "PENDING"
    assert _map_meta_template(_meta_template("t", status="LIMIT_EXCEEDED"))["status"] == "FAILED"
