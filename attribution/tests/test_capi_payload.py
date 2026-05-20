"""CAPI payload + PII hashing tests (#197 / #201 review)."""

from __future__ import annotations

import hashlib

import pytest
from django.utils import timezone

from attribution.tasks import (
    _build_capi_payload,
    _normalise_phone,
    _sha256_lower,
    _synthesise_fbc,
)


class TestPiiHashing:
    def test_sha256_lowercase(self):
        # Meta spec: email lowercased and SHA-256 hex.
        expected = hashlib.sha256(b"user@example.com").hexdigest()
        assert _sha256_lower("User@Example.com") == expected

    def test_sha256_strips_whitespace(self):
        expected = hashlib.sha256(b"user@example.com").hexdigest()
        assert _sha256_lower("  User@Example.COM  ") == expected

    def test_phone_normalisation(self):
        # Meta spec: digits-only, no leading +.
        assert _normalise_phone("+1 (415) 555-0100") == "14155550100"
        assert _normalise_phone("+91 7303 605 769") == "917303605769"

    def test_fbc_synthesis(self):
        # Format: fb.1.{unix_ms}.{ctwa_clid}
        when = timezone.now()
        out = _synthesise_fbc("clid-abc", when)
        assert out.startswith("fb.1.")
        assert out.endswith(".clid-abc")
        ts_part = out.split(".")[2]
        assert ts_part.isdigit()

    def test_fbc_empty_clid_returns_empty(self):
        assert _synthesise_fbc("") == ""
        assert _synthesise_fbc("", None) == ""


@pytest.mark.django_db
class TestCapiPayload:
    def test_payload_shape_lead(self, lead, db):
        # Force a Lead event to exist.
        from attribution.signals import _allocate_event

        ev = _allocate_event(lead_pk=lead.pk, event_name="Lead")
        payload = _build_capi_payload(ev)

        assert payload["event_name"] == "Lead"
        assert payload["event_id"] == ev.event_id
        assert payload["action_source"] == "business_messaging"
        assert isinstance(payload["event_time"], int)

        user_data = payload["user_data"]
        # ctwa_clid present → fbc must be synthesised.
        assert user_data["fbc"].startswith("fb.1.")
        assert user_data["fbc"].endswith(".clid-abc-123")
        # phone present on the contact → SHA-256 phone hashed.
        assert "ph" in user_data
        assert all(len(h) == 64 for h in user_data["ph"])

    def test_purchase_payload_carries_value_currency(self, lead, db):
        from attribution.signals import enqueue_purchase

        ev = enqueue_purchase(lead=lead, value_minor=12_345, currency="USD")
        payload = _build_capi_payload(ev)
        assert payload["event_name"] == "Purchase"
        assert payload["custom_data"]["value"] == 123.45
        assert payload["custom_data"]["currency"] == "USD"
