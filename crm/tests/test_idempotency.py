"""CRM push idempotency tests (#198 + #201 review).

Reviewer's High concern: ``push_lead_idempotent`` v1 stamped the dedup
key AFTER the HTTP push — if save() failed post-push, an inbound echo
within the next few seconds could bypass dedup. v2 stamps BEFORE
push, in its own commit, so the dedup key survives any post-push
crash.

These tests assert:
  * Dedup stamp is committed BEFORE the push call.
  * Push success → CRM external id stored.
  * Push failure → stamp persists; future inbound with the same
    event_id is dropped as our own echo.
  * Inbound dedup correctly identifies our-own-push echoes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from crm.adapters.base import handle_inbound_event, push_lead_idempotent
from crm.models import CrmSyncEvent


@pytest.mark.django_db
class TestPushIdempotent:
    def test_stamp_is_recorded_before_push(self, hubspot_connection, lead):
        """``last_crm_external_event_id`` MUST be saved BEFORE the
        push call. Use a fake adapter that asserts the lead row has
        the stamp at push time."""
        from crm.adapters.base import _REGISTRY
        from crm.adapters.hubspot import HubSpotConnector
        from ctwa.models import CtwaLead

        captured = {}

        class _SpyConnector(HubSpotConnector):
            def push_lead(self, lead_arg, external_event_id):
                # At this point the stamp MUST already be on the DB row.
                fresh = CtwaLead.objects.get(pk=lead_arg.pk)
                captured["stamp_at_push"] = fresh.last_crm_external_event_id
                captured["push_event_id"] = external_event_id
                return "hs-stub-id"

        with patch.dict(_REGISTRY, {"hubspot": _SpyConnector}):
            crm_id = push_lead_idempotent(connection=hubspot_connection, lead=lead)

        assert crm_id == "hs-stub-id"
        # The stamp visible to the push call equals the event_id passed.
        assert captured["stamp_at_push"] == captured["push_event_id"]
        # And it persists post-push.
        lead.refresh_from_db()
        assert lead.last_crm_external_event_id == captured["push_event_id"]
        assert lead.crm_external_id == "hs-stub-id"

    def test_push_failure_keeps_dedup_stamp(self, hubspot_connection, lead):
        """If push raises after the stamp is recorded, the stamp
        persists — so any inbound echo carrying that event_id is
        recognised. (#201 review)"""
        from crm.adapters.base import _REGISTRY
        from crm.adapters.hubspot import HubSpotConnector

        class _BoomConnector(HubSpotConnector):
            def push_lead(self, lead_arg, external_event_id):
                raise RuntimeError("HubSpot 502")

        with patch.dict(_REGISTRY, {"hubspot": _BoomConnector}):
            with pytest.raises(RuntimeError):
                push_lead_idempotent(connection=hubspot_connection, lead=lead)

        lead.refresh_from_db()
        # Stamp persists despite the push failure.
        assert lead.last_crm_external_event_id != ""
        # And an audit row exists.
        audit = CrmSyncEvent.objects.filter(
            connection=hubspot_connection,
            external_event_id=lead.last_crm_external_event_id,
            direction="outbound",
        ).first()
        assert audit is not None
        assert audit.processed is False


@pytest.mark.django_db
class TestInboundEchoDedup:
    def test_own_push_echo_is_dropped(self, hubspot_connection, lead):
        """Inbound webhook carrying our own ``external_event_id`` is
        skipped — the audit log records it with ``skip_reason='own_push_echo'``."""
        from crm.adapters.base import _REGISTRY
        from crm.adapters.hubspot import HubSpotConnector

        class _StubConnector(HubSpotConnector):
            def push_lead(self, lead_arg, external_event_id):
                return "hs-stub-1"

            def parse_inbound_status(self, payload):
                from crm.adapters.base import CrmStatusEvent

                return CrmStatusEvent(
                    external_event_id=payload["x_event_id"],
                    crm_external_id=payload["crm_id"],
                    new_status="qualified",
                    raw_payload=payload,
                )

        with patch.dict(_REGISTRY, {"hubspot": _StubConnector}):
            push_lead_idempotent(connection=hubspot_connection, lead=lead)
            lead.refresh_from_db()

            # Simulate the CRM echoing back our push.
            echo_payload = {
                "x_event_id": lead.last_crm_external_event_id,
                "crm_id": lead.crm_external_id,
            }
            processed = handle_inbound_event(connection=hubspot_connection, payload=echo_payload)

        assert processed is False
        audit = CrmSyncEvent.objects.filter(
            external_event_id=lead.last_crm_external_event_id,
            direction="inbound",
        ).first()
        assert audit is not None
        assert audit.skip_reason == "own_push_echo"

    def test_real_crm_change_is_processed(self, hubspot_connection, lead):
        """A webhook with a different event_id (genuine CRM-side change)
        is processed and updates the lead."""
        from crm.adapters.base import _REGISTRY
        from crm.adapters.hubspot import HubSpotConnector
        from crm.models import CrmEntityMapping

        # Set up a CRM mapping: HubSpot's "marketingqualifiedlead" → Jina's "qualified".
        CrmEntityMapping.objects.create(
            connection=hubspot_connection,
            name="qualified-map",
            jina_value="qualified",
            crm_value="marketingqualifiedlead",
        )

        # Stamp lead with a known crm_external_id but a different event_id.
        lead.crm_external_id = "hs-known-id"
        lead.last_crm_external_event_id = "our-old-event"
        lead.save()

        class _StubConnector(HubSpotConnector):
            def parse_inbound_status(self, payload):
                from crm.adapters.base import CrmStatusEvent

                return CrmStatusEvent(
                    external_event_id=payload["x_event_id"],
                    crm_external_id=payload["crm_id"],
                    new_status="marketingqualifiedlead",
                    raw_payload=payload,
                )

        with patch.dict(_REGISTRY, {"hubspot": _StubConnector}):
            processed = handle_inbound_event(
                connection=hubspot_connection,
                payload={"x_event_id": "DIFFERENT-event-id", "crm_id": "hs-known-id"},
            )

        assert processed is True
        lead.refresh_from_db()
        assert lead.qualification_status == "qualified"
