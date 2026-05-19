"""Webhook-signature verification tests (#198 + #201 review).

Reviewer's High concern: Salesforce v1 returned ``True`` when the
webhook secret was empty, "trusting the IP allowlist." That's an
unsafe default — anyone could spoof a SF webhook. The fix is fail-closed.
"""

from __future__ import annotations

import hashlib
import hmac


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict | None = None):
        self.body = body
        self.META = headers or {}


class TestHubSpotSignature:
    def test_valid_signature_accepted(self, db, hubspot_connection):
        from crm.adapters import get_connector

        body = b'{"events": []}'
        secret = hubspot_connection.webhook_secret.encode("utf-8")
        sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
        req = _FakeRequest(body, {"HTTP_X_HUBSPOT_SIGNATURE_V3": sig})

        connector = get_connector(hubspot_connection)
        assert connector.verify_inbound_signature(req) is True

    def test_invalid_signature_rejected(self, db, hubspot_connection):
        from crm.adapters import get_connector

        req = _FakeRequest(b'{"events": []}', {"HTTP_X_HUBSPOT_SIGNATURE_V3": "deadbeef"})
        connector = get_connector(hubspot_connection)
        assert connector.verify_inbound_signature(req) is False

    def test_missing_signature_rejected(self, db, hubspot_connection):
        from crm.adapters import get_connector

        req = _FakeRequest(b'{"events": []}')
        connector = get_connector(hubspot_connection)
        assert connector.verify_inbound_signature(req) is False


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
