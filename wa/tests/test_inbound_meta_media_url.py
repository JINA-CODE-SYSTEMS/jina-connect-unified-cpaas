"""Inbound media from a META app must not be handed to the browser as META's URL.

A customer sent a photo and the inbox drew a broken attachment. The message was
stored, typed correctly as an image, and carried a URL — but the URL was
``https://lookaside.fbsbx.com/...``, META's own media host. Those links require
an ``Authorization: Bearer`` header and expire minutes after the webhook, so a
browser asking for one as an ``<img src>`` is simply refused. Every inbound
image, video, voice note and document from a META app was affected.

The cause was one line of optimism in ``_resolve_media``:

    # If the payload already contains a URL (e.g. Gupshup-proxied), use it.
    if media_obj.get("url"):
        return media_obj["url"]

True for Gupshup, which proxies media through a public CDN. Not true for META,
whose inbound webhook carries **both** an ``id`` and a ``url`` — verified
against a real stored payload, whose image object held
``['id', 'mime_type', 'sha256', 'url']``. So the short-circuit fired, and the
download path right below it — which exists precisely to make a durable copy —
was never reached.

**These tests are about which URL comes out**, not about how. A test asserting
that ``_download_and_save_meta_media`` was called would pass against a fix that
called it and then returned the provider URL anyway.

HOW TO RUN:
    DB_NAME=... python -m pytest wa/tests/test_inbound_meta_media_url.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wa.tasks import _is_provider_authenticated_media_url

# A stand-in for META's media host. Obviously synthetic path, real host shape.
META_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=synthetic&ext=1&hash=synthetic"
GUPSHUP_URL = "https://filemanager.gupshup.io/fm/wamedia/synthetic/synthetic-image.jpg"
SAVED_URL = "https://connect.example.com/media/incoming_media/1/synthetic.jpg"


# ─────────────────────────────────────────────────────────────────────────────
# 1. The host predicate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        META_URL,
        "https://lookaside.facebook.com/whatsapp_business/attachments/?mid=x",
        "https://scontent.xx.fbcdn.net/v/t62/synthetic.jpg",
    ],
)
def test_meta_hosted_media_is_recognised_as_unfetchable(url):
    assert _is_provider_authenticated_media_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        GUPSHUP_URL,
        SAVED_URL,
        "https://storage.googleapis.com/bucket/tenant_media/synthetic.png",
        "",
    ],
)
def test_a_browser_fetchable_url_is_left_alone(url):
    assert _is_provider_authenticated_media_url(url) is False


def test_a_lookalike_host_elsewhere_in_the_url_does_not_count():
    """Matched on the parsed host, not by substring. A URL that merely mentions
    the host — in a path or a query parameter — is somebody else's URL, and
    treating it as META's would discard a perfectly good link."""
    assert _is_provider_authenticated_media_url("https://cdn.example.com/proxy?src=lookaside.fbsbx.com/x") is False
    # ...and the reverse: a subdomain of the real host still counts.
    assert _is_provider_authenticated_media_url("https://media.lookaside.fbsbx.com/x") is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. What actually comes out, which is the point
# ─────────────────────────────────────────────────────────────────────────────


def _envelope(message: dict) -> dict:
    """One inbound message wrapped in the webhook envelope META actually sends."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1555", "phone_number_id": "1"},
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def _extract(message: dict, wa_app=object()):
    """Run the real parser over one webhook message."""
    from wa.tasks import _parse_meta_message_payload

    return _parse_meta_message_payload(_envelope(message), wa_app=wa_app)


def _image_message(image: dict) -> dict:
    return {
        "from": "919999999999",
        "id": "wamid.synthetic",
        "timestamp": "1757800000",
        "type": "image",
        "image": image,
    }


@pytest.mark.django_db
def test_a_meta_url_is_replaced_by_a_downloaded_copy():
    """The defect itself. The stored URL must be ours, never META's."""
    with patch("wa.tasks._download_and_save_meta_media", return_value=SAVED_URL) as download:
        data = _extract(_image_message({"id": "media-1", "mime_type": "image/jpeg", "url": META_URL}))

    assert data["image_link"] == SAVED_URL
    assert data["image_link"] != META_URL
    assert download.called, "the download path must be reached, not short-circuited"


@pytest.mark.django_db
def test_a_gupshup_url_is_used_as_is_and_costs_no_download():
    """The behaviour the short-circuit was written for, and which must survive:
    a publicly fetchable URL is already durable, so re-fetching it would be a
    round trip for nothing."""
    with patch("wa.tasks._download_and_save_meta_media") as download:
        data = _extract(_image_message({"id": "media-2", "url": GUPSHUP_URL}))

    assert data["image_link"] == GUPSHUP_URL
    assert not download.called


@pytest.mark.django_db
def test_a_failed_download_yields_empty_not_metas_url():
    """When the download fails there is nothing renderable, and "" is what
    ``_build_team_inbox_content`` turns into an explicit failed attachment.
    Falling back to META's URL would restore the broken image this fixes."""
    with patch("wa.tasks._download_and_save_meta_media", return_value=""):
        data = _extract(_image_message({"id": "media-3", "url": META_URL}))

    assert data["image_link"] == ""


@pytest.mark.django_db
def test_with_no_app_to_authenticate_as_the_meta_url_is_still_refused():
    """A bare id or a credentialed URL both draw a broken bubble. #274 settled
    that "" — an explicit failure — beats an un-renderable string."""
    data = _extract(_image_message({"id": "media-4", "url": META_URL}), wa_app=None)

    assert data["image_link"] == ""


@pytest.mark.django_db
def test_the_same_rule_applies_to_a_voice_note():
    """Audio travels the same path, which is why inbound voice notes were
    broken for the same reason and are fixed by the same change."""
    message = {
        "from": "919999999999",
        "id": "wamid.synthetic-audio",
        "timestamp": "1757800000",
        "type": "audio",
        "audio": {"id": "media-5", "mime_type": "audio/ogg", "url": META_URL},
    }
    with patch("wa.tasks._download_and_save_meta_media", return_value=SAVED_URL):
        data = _extract(message)

    assert data.get("audio_link") == SAVED_URL
