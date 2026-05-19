"""Fuzz / shape tests for ``HubSpotConnector.parse_inbound_status``
(#201 second review High #2, Tests gap).

Connector contract: ``parse_inbound_status`` MUST NOT raise. v1 of
this method crashed with ``AttributeError`` if HubSpot delivered the
``jina_external_event_id`` property as a flat string (Workflow /
older webhook shape). v2 guards with ``isinstance(..., dict)`` and an
outer try/except.
"""

from __future__ import annotations

import pytest


@pytest.mark.django_db
class TestHubSpotParseInboundStatus:
    def _connector(self, hubspot_connection):
        from crm.adapters import get_connector

        return get_connector(hubspot_connection)

    def test_subscription_shape_nested_props_dict(self, hubspot_connection):
        """Canonical Subscription webhook: properties are
        ``{name: {value: ...}}``."""
        connector = self._connector(hubspot_connection)
        payload = {
            "events": [
                {
                    "subscriptionType": "contact.propertyChange",
                    "objectId": "hs-1",
                    "properties": {
                        "jina_external_event_id": {"value": "evt-abc"},
                        "lifecyclestage": {"value": "marketingqualifiedlead"},
                    },
                }
            ]
        }
        out = connector.parse_inbound_status(payload)
        assert out is not None
        assert out.external_event_id == "evt-abc"
        assert out.crm_external_id == "hs-1"
        assert out.new_status == "marketingqualifiedlead"

    def test_flat_shape_string_props(self, hubspot_connection):
        """Workflow / per-event delivery: properties are flat strings.
        v1 crashed with AttributeError on ``.get('value')``; v2 reads
        the string directly. (#201 second review High #2)"""
        connector = self._connector(hubspot_connection)
        payload = {
            "subscriptionType": "contact.propertyChange",
            "objectId": "hs-2",
            "properties": {
                "jina_external_event_id": "evt-flat-1",
                "lifecyclestage": "marketingqualifiedlead",
            },
        }
        out = connector.parse_inbound_status(payload)
        assert out is not None
        assert out.external_event_id == "evt-flat-1"
        assert out.crm_external_id == "hs-2"
        assert out.new_status == "marketingqualifiedlead"

    def test_garbage_payload_returns_none_not_raise(self, hubspot_connection):
        """Contract: never raise."""
        connector = self._connector(hubspot_connection)
        for garbage in [None, "string", 42, [], {"random": "shape"}]:
            assert connector.parse_inbound_status(garbage) is None

    def test_props_is_not_dict_returns_none(self, hubspot_connection):
        """``properties: null`` or a list — skip the event."""
        connector = self._connector(hubspot_connection)
        payload = {
            "events": [
                {
                    "subscriptionType": "contact.propertyChange",
                    "objectId": "hs-3",
                    "properties": None,
                }
            ]
        }
        assert connector.parse_inbound_status(payload) is None

    def test_missing_required_props_returns_none(self, hubspot_connection):
        connector = self._connector(hubspot_connection)
        payload = {
            "events": [
                {
                    "subscriptionType": "contact.propertyChange",
                    "objectId": "hs-4",
                    "properties": {"lifecyclestage": "qualified"},
                    # no jina_external_event_id, no objectId would also fail —
                    # but we test the property-missing path here.
                }
            ]
        }
        # external_event_id is empty but the v2 helper still extracts
        # an empty string for it; the loop only continues if BOTH
        # crm_external_id and new_status are truthy. Empty event_id
        # is allowed (the inbound handler will treat it as "not our
        # echo" and process as a real CRM-side change).
        out = connector.parse_inbound_status(payload)
        assert out is not None
        assert out.external_event_id == ""
        assert out.crm_external_id == "hs-4"
        assert out.new_status == "qualified"

    def test_non_propertychange_subscription_skipped(self, hubspot_connection):
        connector = self._connector(hubspot_connection)
        payload = {
            "events": [
                {
                    "subscriptionType": "contact.creation",
                    "objectId": "hs-5",
                    "properties": {
                        "jina_external_event_id": {"value": "evt-x"},
                        "lifecyclestage": {"value": "qualified"},
                    },
                }
            ]
        }
        assert connector.parse_inbound_status(payload) is None
