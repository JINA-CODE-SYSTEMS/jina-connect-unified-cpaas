"""Team inbox, Meta path: a batched delivery becomes inbox rows (#277, #268).

The boundary this crosses is the whole inbound chain, in one call:

    signed POST /wa/v2/webhooks/meta/
        → MetaWebhookView (signature, classify, route by phone_number_id)
            → WAWebhookEvent post_save → process_webhook_event_task
                → _split_meta_message_payloads → _parse_meta_message_payload
                    → Graph media resolution (two HTTP calls)
                        → TenantContact + team_inbox.Messages rows

Nothing in between is stubbed except the Graph HTTP calls themselves, and
those are asserted rather than waved through: the two-step media download is
the part of the Meta inbox path the audit found *correct*, and a test that
let it be skipped would not notice it breaking.

Why not the existing tests. ``wa/tests/test_batched_inbound.py`` covers the
splitter and calls ``process_message_webhook`` on a hand-built
``WAWebhookEvent``; ``wa/tests/test_meta_webhook_routing.py`` posts to the
view but patches ``wa.signals._dispatch``, so it stops at the event row. The
stretch between the two — the signal, the task dispatch and the
``bsp == META`` branch that chooses the splitter — is exactly where #268 and
#269 lived, and no test crossed it.

HOW TO RUN:
    python -m pytest team_inbox/tests/test_meta_path_inbound.py -v
"""

from __future__ import annotations

import pytest

from wa.tests.meta_path import (
    APP_SECRET,
    FakeGraph,
    FakeResponse,
    assert_meta_call,
    image_message,
    inbound_envelope,
    messages_value,
    meta_wa_app,
    multi_entry_envelope,
    run_webhooks_in_process,
    sign_meta_webhook,
    tenant,
    text_message,
)

pytestmark = pytest.mark.django_db

TOKEN = "inbox-tenant-token"
MEDIA_ID = "media-9912"
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=media-9912"
JPEG = b"\xff\xd8\xff\xe0thisisajpeg"


@pytest.fixture()
def app():
    return meta_wa_app(tenant("Inbox"), access_token=TOKEN)


@pytest.fixture(autouse=True)
def _meta_env(settings, tmp_path):
    settings.META_APP_SECRET = APP_SECRET
    # Without this the deployment-wide token would satisfy the clients and the
    # per-app credential assertions below could not tell the two apart.
    settings.META_PERM_TOKEN = ""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    run_webhooks_in_process(settings)


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


def _inbox_bodies(app) -> list[str]:
    from team_inbox.models import Messages

    return sorted(
        (m.content.get("body") or {}).get("text", "")
        for m in Messages.objects.filter(tenant=app.tenant)
        if m.content.get("type") == "text"
    )


def _messages(app):
    from team_inbox.models import Messages

    return Messages.objects.filter(tenant=app.tenant)


# ─────────────────────────────────────────────────────────────────────────────
# The headline: every message in a batched delivery reaches the inbox
# ─────────────────────────────────────────────────────────────────────────────


def test_a_batch_plural_at_all_three_levels_lands_as_one_row_each(client, app, graph):
    """Two entries × two changes × two messages — eight customer messages.

    The pre-fix parser read ``entry[0] / changes[0] / messages[0]``, so this
    delivery produced one inbox row and was marked processed. Plural at every
    level on purpose: a payload plural at only one level cannot tell a fix at
    that level from a fix at all three.
    """
    payload = multi_entry_envelope(
        app,
        [
            messages_value(app, text_message("wamid.1", "one"), text_message("wamid.2", "two")),
            messages_value(app, text_message("wamid.3", "three"), text_message("wamid.4", "four")),
        ],
        [
            messages_value(app, text_message("wamid.5", "five"), text_message("wamid.6", "six")),
            messages_value(app, text_message("wamid.7", "seven"), text_message("wamid.8", "eight")),
        ],
    )

    response = sign_meta_webhook(client, payload)

    assert response.status_code == 200
    assert response.json()["event_type"] == "MESSAGE"
    assert _inbox_bodies(app) == [
        "eight",
        "five",
        "four",
        "one",
        "seven",
        "six",
        "three",
        "two",
    ]


def test_the_delivery_is_attributed_to_the_meta_app_and_marked_processed(client, app, graph):
    """The event row the view wrote, processed by the Meta branch.

    ``bsp`` on the row is what ``process_message_webhook`` reads to choose the
    splitter at all; a row stored as Gupshup would take the single-message
    path and silently drop the rest of the batch even with #268 fixed.
    """
    from tenants.models import BSPChoices
    from wa.models import WAWebhookEvent

    payload = inbound_envelope(
        app, messages_value(app, text_message("wamid.a", "hello"), text_message("wamid.b", "hi"))
    )

    sign_meta_webhook(client, payload)

    event = WAWebhookEvent.objects.get(wa_app=app)
    assert event.bsp == BSPChoices.META
    assert event.is_processed is True
    assert not event.error_message
    assert _messages(app).count() == 2


def test_every_message_keeps_its_own_wamid(client, app, graph):
    """Each row traces back to the message Meta sent, not to the batch.

    ``content["_meta"]["wa_message_id"]`` is what correlates a status webhook
    and a delivery receipt to an inbox row. One slice aliasing another's
    message would give several rows the same id and silently cross-wire them.
    """
    payload = inbound_envelope(
        app,
        messages_value(
            app,
            text_message("wamid.AAA", "first"),
            text_message("wamid.BBB", "second"),
            text_message("wamid.CCC", "third"),
        ),
    )

    sign_meta_webhook(client, payload)

    by_body = {m.content["body"]["text"]: m.content["_meta"]["wa_message_id"] for m in _messages(app)}
    assert by_body == {"first": "wamid.AAA", "second": "wamid.BBB", "third": "wamid.CCC"}


def test_the_sender_becomes_one_contact_not_one_per_message(client, app, graph):
    """Eight messages from one number is one contact with eight messages."""
    from contacts.models import TenantContact

    payload = inbound_envelope(
        app,
        messages_value(app, *[text_message(f"wamid.{i}", f"msg {i}") for i in range(8)]),
    )

    sign_meta_webhook(client, payload)

    contacts = TenantContact.objects.filter(tenant=app.tenant)
    assert contacts.count() == 1
    assert contacts.first().phone == "+27821234567"
    assert _messages(app).count() == 8


# ─────────────────────────────────────────────────────────────────────────────
# Media: the two-step Graph resolution, asserted as Meta requests
# ─────────────────────────────────────────────────────────────────────────────


def test_an_image_in_a_batch_is_resolved_through_the_graph_and_stored(client, app, graph):
    """A media message alongside text messages, end to end.

    Two Graph calls, in order: resolve the media id to a five-minute URL,
    then fetch the bytes from that URL with the bearer token. Both are
    asserted by shape — the media edge is the one place the Meta inbox path
    talks to Graph on inbound, so if it is not in the recorded calls, it did
    not happen.
    """
    graph.get(f"/{MEDIA_ID}", {"url": MEDIA_URL, "mime_type": "image/jpeg", "id": MEDIA_ID})
    graph.get("lookaside.fbsbx.com", FakeResponse(content=JPEG, headers={"Content-Type": "image/jpeg"}))

    payload = inbound_envelope(
        app,
        messages_value(
            app,
            text_message("wamid.t1", "before"),
            image_message("wamid.i1", MEDIA_ID, caption="my broken screen"),
            text_message("wamid.t2", "after"),
        ),
    )

    sign_meta_webhook(client, payload)

    resolve = graph.only("GET", f"/{MEDIA_ID}")
    assert_meta_call(resolve, path=MEDIA_ID, token=TOKEN)
    assert resolve.params["phone_number_id"] == app.phone_number_id, (
        "the media read must be scoped to this app's number"
    )

    download = graph.only("GET", "lookaside.fbsbx.com")
    assert download.headers["Authorization"] == f"Bearer {TOKEN}"

    from team_inbox.models import Messages

    image_row = Messages.objects.get(tenant=app.tenant, content__type="image")
    assert image_row.content["image"]["caption"] == "my broken screen"
    assert image_row.content["image"]["mime_type"] == "image/jpeg"
    assert image_row.content["image"]["url"].endswith(".jpg")
    assert "error" not in image_row.content["image"]
    assert _inbox_bodies(app) == ["after", "before"]


def test_a_failed_media_download_does_not_cost_the_other_messages(client, app, graph):
    """Graph refuses the media; the batch still lands, the image says so.

    The image row is kept typed as an image with ``download_failed`` rather
    than falling through to an empty text bubble (#274), and its siblings are
    unaffected — one message must not drop the batch.
    """
    graph.get(f"/{MEDIA_ID}", FakeResponse({"error": {"message": "gone"}}, status_code=404))

    payload = inbound_envelope(
        app,
        messages_value(
            app,
            text_message("wamid.ok1", "still here"),
            image_message("wamid.bad", MEDIA_ID, caption="look at this"),
        ),
    )

    sign_meta_webhook(client, payload)

    from team_inbox.models import Messages

    image_row = Messages.objects.get(tenant=app.tenant, content__type="image")
    assert image_row.content["image"]["url"] == ""
    assert image_row.content["image"]["error"] == "download_failed"
    assert image_row.content["image"]["caption"] == "look at this"
    assert _inbox_bodies(app) == ["still here"]


# ─────────────────────────────────────────────────────────────────────────────
# The delivery is answered, and answered once
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING, not covered by any ticket in #277: inbound ingestion has no "
        "idempotency key. Meta redelivers a webhook whenever it does not see a "
        "timely 200 — and sometimes anyway — and `_ingest_inbound_message` "
        "creates a `Messages` row unconditionally, so the customer's message "
        "appears twice in the inbox. Nothing anywhere keys on the `wamid`, "
        "which the row already stores in `content._meta.wa_message_id`. The "
        "knock-on is in the sibling chat_flow test: the trigger dispatcher's "
        "replay guard keys on the *new* row's pk, so a replay defeats it too "
        "and the flow spawns a second time. Flip to a plain assertion once a "
        "fix lands; strict xfail so it cannot pass unnoticed."
    ),
)
def test_a_redelivered_batch_does_not_double_the_inbox(client, app, graph):
    """Meta retries on anything but a 200, so the same batch arrives twice.

    One ``WAWebhookEvent`` per delivery is expected — they are separate
    deliveries — but the inbox must not gain a second copy of every message.
    """
    payload = inbound_envelope(app, messages_value(app, text_message("wamid.dup", "only once")))

    sign_meta_webhook(client, payload)
    sign_meta_webhook(client, payload)

    assert _messages(app).count() == 1, "the same wamid was ingested twice"


def test_an_unsigned_batch_is_dropped_before_any_row_is_written(client, app, graph):
    """No signature, no inbox rows — and still a 200, because a non-200
    throttles every other tenant's delivery too (#306)."""
    payload = inbound_envelope(app, messages_value(app, text_message("wamid.x", "forged")))

    response = client.post(
        "/wa/v2/webhooks/meta/",
        data=payload,
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "malformed_signature_header"
    assert _messages(app).count() == 0
