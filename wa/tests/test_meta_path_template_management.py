"""Template management, Meta path: sync from Graph, and media that works (#277).

Two of #277's findings meet in this area and neither had a test crossing the
boundary it lived on.

**``sync_templates_from_bsp`` on a Meta app** — named in the epic as one of
the five missing tests. A blank ``bsp`` got the Meta adapter from the factory
and the *Gupshup* mapper from this service, which reads ``elementName`` out of
a Meta payload, gets ``None``, and fails every row as "Template missing name"
(#265). Both halves read the same column; only a test that runs the real
service against a real Meta payload and then reads the database can see that
they agree.

**Media templates returned 501** — ``MetaDirectAdapter`` omitted
``"media_upload"`` from its capability frozenset while implementing
``upload_media()`` in full, so the viewset refused every media template (#266).
One word, in a declaration, contradicted by the code behind it.

Both start from an authenticated API call and end at rows in the database,
with ``requests`` as the only stub — so the Graph endpoints, the bearer token
and the Resumable Upload handshake are asserted rather than assumed.

HOW TO RUN:
    python -m pytest wa/tests/test_meta_path_template_management.py -v
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from wa.tests.meta_path import (
    FakeGraph,
    FakeResponse,
    assert_meta_call,
    meta_wa_app,
    tenant,
    wa_template,
)

pytestmark = pytest.mark.django_db

TOKEN = "template-tenant-token"
SYNC_URL = "/wa/v2/templates/sync-from-bsp/"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_global_token(settings, tmp_path):
    settings.META_PERM_TOKEN = ""
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture()
def owner():
    return tenant("Templates")


@pytest.fixture()
def app(owner):
    return meta_wa_app(owner, access_token=TOKEN)


@pytest.fixture()
def api(owner):
    """An owner-role client for *owner*'s tenant.

    The sync and upload endpoints are permission-gated and tenant-scoped, so
    going through them rather than calling the service directly also pins that
    a Meta app is reachable through the API a customer actually uses.
    """
    from django.contrib.auth import get_user_model

    from tenants.models import TenantRole, TenantUser

    user = get_user_model().objects.create_user(
        username=f"tpl_{uuid.uuid4().hex[:8]}",
        email=f"tpl_{uuid.uuid4().hex[:6]}@test.local",
        mobile=f"+91{9000000000 + uuid.uuid4().int % 999999999}",
        password="TestPass123!",
    )
    role = TenantRole.objects.get(tenant=owner, slug="owner")
    TenantUser.objects.create(tenant=owner, user=user, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


# ─────────────────────────────────────────────────────────────────────────────
# Graph template payloads
# ─────────────────────────────────────────────────────────────────────────────


def _meta_template(name, *, status="APPROVED", category="MARKETING", rejected_reason=None, components=None):
    """One row as ``GET /{waba_id}/message_templates`` returns it.

    Note ``name`` / ``language`` rather than Gupshup's ``elementName`` /
    ``languageCode``: this shape is what the Gupshup mapper cannot read, and
    reading it is the whole point of the Meta branch.
    """
    row = {
        "id": f"meta-{name}",
        "name": name,
        "language": "en_US",
        "status": status,
        "category": category,
        "components": components if components is not None else [{"type": "BODY", "text": f"Body of {name}"}],
    }
    if rejected_reason is not None:
        row["rejected_reason"] = rejected_reason
    return row


def _page(rows, next_url=None):
    body = {"data": rows}
    if next_url:
        body["paging"] = {"cursors": {"after": "cursor-1"}, "next": next_url}
    return body


def _route_template_reads(graph, app, *, pages, media_page=None):
    """Answer both Graph template reads the sync makes.

    ``list_templates`` passes its query as ``params``; the media pass in phase
    2 embeds the query in the URL. Routing on that difference keeps the two
    apart — they hit the same edge — so each is answered with the shape its
    caller expects, and neither silently serves the other.
    """
    remaining = list(pages)

    def _answer(call):
        if "?fields=" in call.url:
            # Phase 2: header-media URLs, queried with the fields inline.
            return media_page if media_page is not None else {"data": []}
        return remaining.pop(0) if remaining else {"data": []}

    graph.get(f"{app.waba_id}/message_templates", _answer)


def _sync(api, app, **extra):
    return api.post(SYNC_URL, {"wa_app_id": str(app.pk), **extra}, format="json")


# ─────────────────────────────────────────────────────────────────────────────
# sync_templates_from_bsp on a Meta app
# ─────────────────────────────────────────────────────────────────────────────


def test_a_meta_waba_syncs_into_local_templates(api, app, graph):
    """The headline: a Graph template list becomes ``WATemplate`` rows.

    With the Gupshup mapper on this payload every row fails as "Template
    missing name" and the endpoint reports ``failed: 3`` — which is what #265
    did for a blank-``bsp`` app. The assertion is therefore on the rows, not
    on the summary: a summary can be right while the mapper wrote nothing.
    """
    from wa.models import TemplateStatus, WATemplate

    _route_template_reads(
        graph,
        app,
        pages=[
            _page(
                [
                    _meta_template("order_shipped", category="UTILITY"),
                    _meta_template("flash_sale"),
                    _meta_template("otp_login", status="PENDING", category="AUTHENTICATION"),
                ]
            )
        ],
    )

    response = _sync(api, app)

    assert response.status_code == 200, response.data
    assert response.data["created"] == 3
    assert response.data["failed"] == 0
    assert response.data["errors"] == []

    rows = {t.element_name: t for t in WATemplate.objects.filter(wa_app=app)}
    assert set(rows) == {"order_shipped", "flash_sale", "otp_login"}
    assert rows["order_shipped"].language_code == "en_US"
    assert rows["order_shipped"].category == "UTILITY"
    assert rows["order_shipped"].status == TemplateStatus.APPROVED
    assert rows["order_shipped"].meta_template_id == "meta-order_shipped"
    assert rows["order_shipped"].content == "Body of order_shipped"
    assert rows["otp_login"].status == TemplateStatus.PENDING


def test_the_read_asks_graph_for_the_fields_it_parses(api, app, graph):
    """The request, as the Meta client made it.

    ``rejected_reason`` and ``quality_score`` are not in Graph's default field
    set for this edge, and a missing field is indistinguishable from "not
    rejected" — the response parses fine and the value is simply absent
    (#272). Asking for them by name is the fix, so the ``fields`` parameter is
    the assertion.
    """
    _route_template_reads(graph, app, pages=[_page([_meta_template("welcome")])])

    _sync(api, app)

    read = graph.all("GET", "message_templates")[0]
    assert_meta_call(read, path=f"{app.waba_id}/message_templates", token=TOKEN)
    fields = read.params["fields"].split(",")
    assert "rejected_reason" in fields
    assert "quality_score" in fields
    assert "components" in fields
    assert read.params["limit"] == 100


def test_every_page_is_followed(api, app, graph):
    """A WABA with more templates than one page holds.

    Importing Graph's first page and reporting success is worse than failing:
    the rest look like templates that do not exist, and the UI offers a
    partial list with no indication it is partial (#272).
    """
    from wa.models import WATemplate

    next_url = f"https://graph.facebook.com/v24.0/{app.waba_id}/message_templates?after=cursor-1"
    _route_template_reads(
        graph,
        app,
        pages=[
            _page([_meta_template("page1_a"), _meta_template("page1_b")], next_url=next_url),
            _page([_meta_template("page2_a")]),
        ],
    )

    response = _sync(api, app)

    assert response.data["total_from_bsp"] == 3
    assert WATemplate.objects.filter(wa_app=app).count() == 3
    assert len(graph.all("GET", "message_templates")) >= 2, "pagination was not followed"


def test_a_page_that_fails_fails_the_whole_sync(api, app, graph):
    """A short list must never pass for a complete one.

    Half a page of templates imported and reported as success is the state
    that makes a missing template look like a template the customer never
    created.
    """
    from wa.models import WATemplate

    next_url = f"https://graph.facebook.com/v24.0/{app.waba_id}/message_templates?after=cursor-1"
    pages = [
        _page([_meta_template("first_page")], next_url=next_url),
        {"error": {"message": "(#4) Application request limit reached", "code": 4}},
    ]
    _route_template_reads(graph, app, pages=pages)

    response = _sync(api, app)

    assert response.data["failed"] == 1
    assert response.data["created"] == 0
    assert "request limit" in response.data["errors"][0]
    assert WATemplate.objects.filter(wa_app=app).count() == 0, "a partial import was committed"


def test_a_rejection_reason_lands_in_the_field_the_ui_shows(api, app, graph):
    """``rejected_reason`` from Graph → ``rejection_reason`` on the row.

    Writing only ``error_message`` left ``rejection_reason`` null forever,
    because the poller that would have filled it in only polls PENDING rows
    and this one is REJECTED (#272). Two fields, one of which the UI renders
    next to a rejected template — a field written in one shape and read in
    another, which is the pattern #277 names.
    """
    from wa.models import TemplateStatus, WATemplate

    _route_template_reads(
        graph,
        app,
        pages=[
            _page(
                [
                    _meta_template(
                        "bad_promo",
                        status="REJECTED",
                        rejected_reason="ABUSIVE_CONTENT",
                    )
                ]
            )
        ],
    )

    _sync(api, app)

    row = WATemplate.objects.get(wa_app=app, element_name="bad_promo")
    assert row.status == TemplateStatus.REJECTED
    assert row.rejection_reason == "ABUSIVE_CONTENT"


def test_a_second_sync_updates_rather_than_duplicates(api, app, graph):
    """``(wa_app, element_name, language_code)`` is the matching key.

    A sync that re-created rows would break every broadcast and flow pointing
    at the old one, and a sync that matched on the wrong key would do it
    silently.
    """
    from wa.models import TemplateStatus, WATemplate

    _route_template_reads(graph, app, pages=[_page([_meta_template("promo", status="PENDING")])])
    _sync(api, app)
    first = WATemplate.objects.get(wa_app=app, element_name="promo")

    graph.reset_routes()
    _route_template_reads(graph, app, pages=[_page([_meta_template("promo", status="APPROVED")])])
    response = _sync(api, app)

    assert response.data["created"] == 0
    assert response.data["updated"] == 1
    assert WATemplate.objects.filter(wa_app=app, element_name="promo").count() == 1
    first.refresh_from_db()
    assert first.status == TemplateStatus.APPROVED
    assert first.last_synced_at is not None
    assert first.needs_sync is False


def test_a_dry_run_reads_graph_and_writes_nothing(api, app, graph):
    from wa.models import WATemplate

    _route_template_reads(graph, app, pages=[_page([_meta_template("preview_me")])])

    response = _sync(api, app, dry_run="true")

    assert response.data["dry_run"] is True
    assert [p["action"] for p in response.data["preview"]] == ["create"]
    assert WATemplate.objects.filter(wa_app=app).count() == 0
    assert graph.all("GET", "message_templates"), "a dry run must still read the BSP"


def test_a_synced_media_template_gets_a_cdn_url_and_a_fresh_handle(api, app, graph):
    """The full media sync: list → header URL → download → re-upload.

    Graph hands back a CDN URL when you read a template, never the handle that
    created it, so a synced media template had no handle to re-submit with and
    every re-submit failed (#272). Phase 3 makes a fresh one — which until the
    fix was a Gupshup-only branch that returned ``None`` for every Meta app.
    """
    from tenants.models import TenantMedia
    from wa.models import TemplateType, WATemplate

    cdn = "https://scontent.whatsapp.net/v/t61/header.png"
    header = {
        "type": "HEADER",
        "format": "IMAGE",
        "example": {"header_handle": [cdn]},
    }
    rows = [_meta_template("receipt_img", components=[header, {"type": "BODY", "text": "Your receipt"}])]
    _route_template_reads(graph, app, pages=[_page(rows)], media_page={"data": rows})

    graph.get(cdn, FakeResponse(content=PNG, headers={"Content-Type": "image/png"}))
    graph.post(f"{app.meta_app_id}/uploads", {"id": "upload:RESUMED1"})
    graph.post("upload:RESUMED1", {"h": "4:cmVjZWlwdA==:aW1hZ2UvcG5n"})

    response = _sync(api, app)

    assert response.data["created"] == 1
    assert response.data["media_urls_updated"] == 1
    assert response.data["media_patched"] == 1, response.data

    row = WATemplate.objects.get(wa_app=app, element_name="receipt_img")
    assert row.template_type == TemplateType.IMAGE
    assert row.example_media_url == cdn
    assert row.media_handle == "4:cmVjZWlwdA==:aW1hZ2UvcG5n", "no handle means the next submit fails"
    assert row.tenant_media is not None
    assert TenantMedia.objects.filter(pk=row.tenant_media_id).exists()

    # The re-upload is a Resumable Upload, not the message Media API: the
    # latter returns ids that are valid only for sending, never for a header.
    session = graph.only("POST", "/uploads")
    assert session.path.split("?")[0] == f"{app.meta_app_id}/uploads"
    assert session.params["file_type"] == "image/png"
    assert session.params["file_length"] == len(PNG)
    assert session.params["access_token"] == TOKEN


# This was written as a ``strict=True`` xfail: a finding no ticket covered.
# Phase 2 of ``sync_templates_from_bsp`` — the header-media URL fetch —
# authenticates through ``wa.services.meta_template_service.get_meta_access_token``,
# whose priority was (1) ``wa_app.meta_access_token``, (2)
# ``settings.META_PERM_TOKEN``, (3) ``wa_app.app_secret``. Priority 1 named a
# field no model had ever declared, so it could never fire, and nothing in the
# chain read the per-tenant token at all. On the configuration #275 asks for —
# token on the app, ``META_PERM_TOKEN`` unset — this sent the *Gupshup app
# secret* to Graph. Graph answers 401, the helper logs at WARNING and returns
# empty dicts, so every media template synced with no ``example_media_url`` and
# phase 3 had nothing to download or re-upload: media templates came back from a
# sync unusable, which is #266's symptom by another route.
#
# #289 fixed it, from the other end — it gave the token a real home as the
# encrypted ``bsp_access_token`` column and pointed priority 1 there. So the
# xfail is gone and this is a plain assertion, which is the whole point of
# having written it strict: the fix flipped it, visibly, rather than leaving a
# passing test that nobody re-read.
def test_the_media_url_fetch_uses_the_apps_own_token(api, app, graph):
    """Phase 2 must authenticate as the app, like every other Graph read here.

    Asserted on the recorded request because that is the only place the two
    token resolutions become distinguishable — both produce a syntactically
    valid bearer header, and only one of them is this customer's credential.

    The app fixture still writes the token as ``bsp_credentials["access_token"]``,
    which is what the v2 API accepts and what clients send; #289's
    ``_absorb_bsp_secrets`` moves it into the encrypted column on save. Left that
    way deliberately — it exercises the shape a client actually posts rather than
    the column the model happens to keep it in.
    """
    rows = [
        _meta_template(
            "hdr_token",
            components=[
                {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["https://scontent/x.png"]}},
                {"type": "BODY", "text": "b"},
            ],
        )
    ]
    _route_template_reads(graph, app, pages=[_page(rows)], media_page={"data": rows})
    graph.get("scontent", FakeResponse(content=PNG, headers={"Content-Type": "image/png"}))
    graph.post(f"{app.meta_app_id}/uploads", {"id": "upload:TOK"})
    graph.post("upload:TOK", {"h": "4:tok"})

    _sync(api, app)

    media_reads = [c for c in graph.all("GET", "message_templates") if "?fields=" in c.url]
    assert media_reads, "phase 2 never read the media fields"
    assert media_reads[0].authorization == f"Bearer {TOKEN}", (
        f"phase 2 authenticated with {media_reads[0].authorization!r} — the app_secret is {app.app_secret!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Media templates are possible at all (#266)
# ─────────────────────────────────────────────────────────────────────────────


def _upload(api, template, **extra):
    return api.post(
        f"/wa/v2/templates/{template.id}/upload-media/",
        {"file": SimpleUploadedFile("header.png", PNG, content_type="image/png"), **extra},
        format="multipart",
    )


def test_uploading_a_template_header_image_succeeds_on_meta(api, app, graph):
    """The endpoint #266 made unusable, exercised against real Graph calls.

    ``supports("media_upload")`` consults the adapter's capability frozenset
    and nothing else; with ``"media_upload"`` absent the viewset returned 501
    while ``upload_media()`` sat there fully implemented. A capability flag
    that disagrees with its implementation is invisible to every unit test of
    either half.
    """
    from wa.models import WATemplate

    template = wa_template(app, element_name="media_hdr", template_type="IMAGE", status="DRAFT")
    graph.post(f"{app.meta_app_id}/uploads", {"id": "upload:SESSION7"})
    graph.post("upload:SESSION7", {"h": "4:aGVhZGVy:aW1hZ2UvcG5n"})

    response = _upload(api, template)

    assert response.status_code == 200, response.data
    assert response.data["provider"] == "meta_direct", "a Gupshup adapter would answer with its own name"
    assert response.data["handle_id"] == "4:aGVhZGVy:aW1hZ2UvcG5n"
    assert response.data["stored_on"] == "template"

    stored = WATemplate.objects.get(pk=template.pk)
    assert stored.media_handle == "4:aGVhZGVy:aW1hZ2UvcG5n"
    assert stored.tenant_media is not None


def test_the_upload_is_the_resumable_api_in_two_steps(api, app, graph):
    """Session first, bytes second — and the bytes go to the session id.

    The regular media API (``/{phone_number_id}/media``) returns ids valid only
    for *sending*; a template header needs a Resumable Upload handle. Posting
    to the wrong one of the two returns a perfectly good id that then fails
    template creation, which is why the endpoints are asserted by name.
    """
    template = wa_template(app, element_name="resumable", template_type="IMAGE", status="DRAFT")
    graph.post(f"{app.meta_app_id}/uploads", {"id": "upload:ABC123"})
    graph.post("upload:ABC123", {"h": "4:handle"})

    _upload(api, template)

    assert [c.path.split("?")[0] for c in graph.calls] == [f"{app.meta_app_id}/uploads", "upload:ABC123"]

    session, bytes_call = graph.calls
    assert session.params["file_length"] == len(PNG)
    assert session.params["file_type"] == "image/png"
    assert session.params["access_token"] == TOKEN, "the session is authorised by query param, not header"

    assert bytes_call.data == PNG, "the file must be posted as a raw body"
    assert bytes_call.headers["Authorization"] == f"OAuth {TOKEN}", "the upload step uses OAuth, not Bearer"
    assert bytes_call.headers["file_offset"] == "0"

    assert not graph.all("POST", f"{app.phone_number_id}/media"), (
        "the message Media API was used; its ids are not valid for template headers"
    )


def test_the_handle_reaches_metas_template_create_as_a_header_example(api, app, graph):
    """Upload, then submit: the handle has to arrive where Meta expects it.

    This is the join the 501 hid. Even with the capability flag fixed, a handle
    that does not land in ``components[HEADER].example.header_handle`` means
    Meta rejects the template — and the upload endpoint would still have
    answered 200.
    """
    from wa.models import TemplateStatus, WATemplate

    template = wa_template(
        app,
        element_name="submit_with_media",
        template_type="IMAGE",
        status="DRAFT",
        content="Your receipt is attached",
    )
    graph.post(f"{app.meta_app_id}/uploads", {"id": "upload:SUB1"})
    graph.post("upload:SUB1", {"h": "4:submitted"})
    graph.post(f"{app.waba_id}/message_templates", {"id": "meta-new-1", "status": "PENDING", "category": "MARKETING"})

    assert _upload(api, template).status_code == 200

    from wa.adapters import get_bsp_adapter

    result = get_bsp_adapter(app).submit_template(WATemplate.objects.get(pk=template.pk))
    assert result.success, result.error_message

    create = graph.only("POST", f"{app.waba_id}/message_templates")
    assert_meta_call(create, path=f"{app.waba_id}/message_templates", token=TOKEN)
    # ``to_meta_payload`` emits lower-cased component types, which Graph
    # accepts — compared case-insensitively so this asserts the handle's
    # placement rather than the casing.
    headers = [c for c in create.json["components"] if str(c["type"]).upper() == "HEADER"]
    assert headers, f"no HEADER component was submitted: {create.json['components']}"
    assert str(headers[0]["format"]).upper() == "IMAGE"
    assert headers[0]["example"]["header_handle"] == ["4:submitted"]

    stored = WATemplate.objects.get(pk=template.pk)
    assert stored.status == TemplateStatus.PENDING
    assert stored.meta_template_id == "meta-new-1"


def test_a_graph_upload_failure_is_reported_as_a_gateway_error(api, app, graph):
    """A failed upload must not leave a handle behind.

    502 rather than 501: the provider *can* do this and refused, which is a
    different thing for the caller to act on than "this BSP cannot upload
    media" — the answer #266 gave for every Meta app.
    """
    from wa.models import WATemplate

    template = wa_template(app, element_name="upload_fails", template_type="IMAGE", status="DRAFT")
    graph.post(
        f"{app.meta_app_id}/uploads",
        FakeResponse({"error": {"message": "(#100) Invalid file_length", "code": 100}}, status_code=400),
    )

    response = _upload(api, template)

    assert response.status_code == 502
    assert "Invalid file_length" in response.data["error"]
    assert not WATemplate.objects.get(pk=template.pk).media_handle


def test_a_meta_app_without_a_meta_app_id_says_which_knob_is_unset(api, app, graph):
    """``meta_app_id`` is what the Resumable Upload endpoint is keyed on.

    It only ever lived in ``app_id`` — the Gupshup app id by definition —
    because there was no field for it (#275). The fallback still works, so the
    failure only appears when neither is set, and the message has to name the
    field rather than surface a 400 from Graph.
    """
    app.meta_app_id = ""
    app.app_id = ""
    app.save(update_fields=["meta_app_id", "app_id"])
    template = wa_template(app, element_name="no_app_id", template_type="IMAGE", status="DRAFT")

    response = _upload(api, template)

    assert response.status_code == 502
    assert "meta_app_id" in response.data["error"]
    assert graph.calls == [], "a request was made with no app id to address it to"
