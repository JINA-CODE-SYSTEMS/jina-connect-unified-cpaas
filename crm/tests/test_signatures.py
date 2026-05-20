"""Webhook-signature verification tests (#198 + #201 review)."""

from __future__ import annotations

import hashlib
import hmac

import pytest


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict | None = None):
        self.body = body
        self.META = headers or {}


class TestHubSpotSignatureNotImplemented:
    """v1 of ``HubSpotConnector.verify_inbound_signature`` did
    ``HMAC-SHA256(body)`` which doesn't match HubSpot's V3 spec
    (``HMAC-SHA256(method + uri + body + timestamp)``). Rather than
    ship a half-implementation that silently looks correct, the
    method now raises ``NotImplementedError`` so production cannot
    enable the connector until the full V3 algorithm + replay-window
    check lands. (#201 second review Medium #6)"""

    def test_raises_not_implemented(self, db, hubspot_connection):
        from crm.adapters import get_connector

        req = _FakeRequest(b'{"events": []}', {"HTTP_X_HUBSPOT_SIGNATURE_V3": "anything"})
        connector = get_connector(hubspot_connection)
        with pytest.raises(NotImplementedError, match="V3"):
            connector.verify_inbound_signature(req)


class TestSalesforceSignatureFailClosed:
    def test_missing_secret_rejects_all_inbound(self, db, salesforce_connection):
        """v1 returned True here; v2 (post-#201 review) returns False
        to prevent spoofing on tenants without a configured secret."""
        from crm.adapters import get_connector

        # Connection has no webhook_secret set.
        req = _FakeRequest(b'{"sobject": {}}', {"HTTP_X_SF_SIGNATURE": "any-sig-value"})
        connector = get_connector(salesforce_connection)
        assert connector.verify_inbound_signature(req) is False

    def test_with_secret_correctly_validates(self, db, salesforce_connection):
        salesforce_connection.webhook_secret = "shared-sf-secret"
        salesforce_connection.save()

        from crm.adapters import get_connector

        body = b'{"sobject": {"Id": "abc"}}'
        sig = hmac.new(b"shared-sf-secret", body, hashlib.sha256).hexdigest()
        req = _FakeRequest(body, {"HTTP_X_SF_SIGNATURE": sig})

        connector = get_connector(salesforce_connection)
        assert connector.verify_inbound_signature(req) is True

    def test_with_secret_rejects_bad_signature(self, db, salesforce_connection):
        salesforce_connection.webhook_secret = "shared-sf-secret"
        salesforce_connection.save()

        from crm.adapters import get_connector

        req = _FakeRequest(b'{"sobject": {}}', {"HTTP_X_SF_SIGNATURE": "bad-sig"})
        connector = get_connector(salesforce_connection)
        assert connector.verify_inbound_signature(req) is False
