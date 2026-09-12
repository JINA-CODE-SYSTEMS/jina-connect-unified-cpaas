"""A webhook payload with an emoji must survive the round trip.

Django's JSON encoder writes non-ASCII as ``\\uXXXX`` escapes. Postgres only
accepts those in a database whose encoding can represent them, so on a
SQL_ASCII database the write fails outright with *"unsupported Unicode escape
sequence"* rather than storing something wrong.

Nothing about that is hypothetical on this path: an inbound WhatsApp message
carrying a single emoji — a thumbs-up reaction, or a product name with an
accent — lands in ``WAWebhookEvent.payload`` verbatim. A test database that
cannot hold it fails tests that have nothing to do with encoding, which is how
this was found.
"""

import pytest

pytestmark = pytest.mark.django_db


def _wa_app():
    from wa.tests.test_template_api_v2 import create_test_tenant_and_user, create_test_wa_app

    tenant, _user, _token = create_test_tenant_and_user(username="unicode")
    return create_test_wa_app(tenant)


@pytest.mark.parametrize(
    "text",
    [
        "👍",
        "Thanks! 🙏🏽",
        "Café — naïve",
        "ऑप्ट आउट",
        "مرحبا",
    ],
    ids=["emoji", "emoji-with-modifier", "latin-accents", "devanagari", "arabic"],
)
def test_a_payload_with_non_ascii_text_round_trips(text):
    from wa.models import WAWebhookEvent

    event = WAWebhookEvent.objects.create(
        wa_app=_wa_app(),
        bsp="META",
        event_type="MESSAGE",
        payload={"entry": [{"changes": [{"value": {"messages": [{"text": {"body": text}}]}}]}]},
    )

    event.refresh_from_db()
    stored = event.payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]
    assert stored == text
