"""A failed broadcast message says why it failed.

The Error column on the broadcast page reads ``error_message``, which the
serializer never produced, so every failure showed "-". The reason was in
``response`` all along — for a failed status webhook, as a Python repr of
Meta's ``errors`` array, which the page could not parse either.

HOW TO RUN:
    .venv/bin/python -m pytest broadcast/tests/test_failure_reason_shown.py -v
"""

from __future__ import annotations

import json

import pytest
from django.utils import timezone

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    BroadcastStatusChoices,
    MessageStatusChoices,
)
from broadcast.serializers import BroadcastMessageSerializer
from broadcast.utils.send_errors import describe_send_error

MEDIA_ERRORS = [
    {
        "code": 131053,
        "title": "Media upload error",
        "message": "Media upload error",
        "error_data": {
            "details": "Downloading media from weblink failed with http code 400, status message Bad Request"
        },
    }
]
MEDIA_REASON = (
    "Media upload error (131053): Downloading media from weblink failed with http code 400, status message Bad Request"
)


class TestDescribeSendError:
    def test_webhook_errors_as_json(self):
        assert describe_send_error(json.dumps(MEDIA_ERRORS)) == MEDIA_REASON

    def test_webhook_errors_as_python_repr(self):
        # How the webhook stored them before; those rows are still there.
        assert describe_send_error(str(MEDIA_ERRORS)) == MEDIA_REASON

    def test_graph_api_error_envelope(self):
        raw = json.dumps({"error": {"message": "(#132001) Template name does not exist", "code": 132001}})
        assert describe_send_error(raw) == "(#132001) Template name does not exist (132001)"

    def test_plain_text_is_kept(self):
        assert describe_send_error("Max retries exceeded: timeout") == "Max retries exceeded: timeout"

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_nothing_stored(self, raw):
        assert describe_send_error(raw) is None


@pytest.fixture()
def broadcast(db):
    from contacts.models import TenantContact
    from tenants.models import Tenant

    tenant = Tenant.objects.create(name="Error Column Tenant")
    bc = Broadcast.objects.create(
        tenant=tenant,
        name="Media Campaign",
        status=BroadcastStatusChoices.SENDING,
        platform=BroadcastPlatformChoices.WHATSAPP,
        scheduled_time=timezone.now(),
    )
    bc._contacts = [
        TenantContact.objects.create(tenant=tenant, phone=f"+2782000{n:04d}", first_name=f"C{n}") for n in range(2)
    ]
    return bc


def test_failed_row_carries_the_reason(broadcast):
    msg = BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=broadcast._contacts[0],
        status=MessageStatusChoices.FAILED,
        response=str(MEDIA_ERRORS),
    )
    assert BroadcastMessageSerializer(msg).data["error_message"] == MEDIA_REASON


def test_sent_row_has_no_error(broadcast):
    # ``response`` holds the provider's success payload here; that is not an error.
    msg = BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=broadcast._contacts[1],
        status=MessageStatusChoices.SENT,
        response='{"messages": [{"id": "wamid.X"}]}',
    )
    assert BroadcastMessageSerializer(msg).data["error_message"] is None
