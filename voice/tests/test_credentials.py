"""Tests for the per-provider credential schemas (#159)."""

from __future__ import annotations

import json

import pytest
from django.test import TestCase

from voice.adapters.credentials import (
    ExotelCredentials,
    PlivoCredentials,
    SipCredentials,
    TelnyxCredentials,
    TwilioCredentials,
    VonageCredentials,
    validate_credentials,
)
from voice.constants import VoiceProvider
from voice.exceptions import VoiceCredentialError


class PerProviderSchemaTests(TestCase):
    def test_twilio_valid(self):
        c = TwilioCredentials(account_sid="AC123", auth_token="tok", region="us1")
        self.assertEqual(c.account_sid, "AC123")

    def test_plivo_requires_auth_id(self):
        with pytest.raises(Exception):
            PlivoCredentials(auth_token="t")  # missing auth_id

    def test_vonage_requires_pem(self):
        with pytest.raises(Exception):
            VonageCredentials(api_key="k", api_secret="s", application_id="uuid")

    def test_telnyx_minimum(self):
        c = TelnyxCredentials(api_key="KEYxxx", connection_id="conn")
        self.assertIsNone(c.outbound_voice_profile_id)

    def test_exotel_subdomain_default(self):
        c = ExotelCredentials(sid="s", api_key="k", api_token="t")
        self.assertEqual(c.subdomain, "api.exotel.com")

    def test_sip_defaults(self):
        c = SipCredentials(
            sip_username="u",
            sip_password="p",
            sip_realm="r",
            sip_proxy="proxy:5060",
        )
        self.assertEqual(c.dtmf_mode, "rfc2833")
        self.assertEqual(c.vendor_profile, "generic")
        self.assertTrue(c.registration_required)


class ValidateCredentialsTests(TestCase):
    def test_validate_dict_input(self):
        out = validate_credentials(
            VoiceProvider.TWILIO,
            {"account_sid": "AC1", "auth_token": "t"},
        )
        self.assertEqual(out["account_sid"], "AC1")

    def test_validate_json_string_input(self):
        out = validate_credentials(
            VoiceProvider.PLIVO,
            json.dumps({"auth_id": "MA1", "auth_token": "t"}),
        )
        self.assertEqual(out["auth_id"], "MA1")

    def test_validate_none_treated_as_empty(self):
        """None credentials → schemas with required fields raise."""
        with pytest.raises(VoiceCredentialError):
            validate_credentials(VoiceProvider.TWILIO, None)

    def test_invalid_json_raises_credential_error(self):
        with pytest.raises(VoiceCredentialError) as ctx:
            validate_credentials(VoiceProvider.TWILIO, "{not json")
        self.assertIn("not valid JSON", str(ctx.value))

    def test_unknown_provider_raises(self):
        with pytest.raises(VoiceCredentialError):
            validate_credentials("made_up_provider", {})

    def test_missing_required_field_raises(self):
        with pytest.raises(VoiceCredentialError):
            validate_credentials(VoiceProvider.TWILIO, {"auth_token": "t"})

    def test_returns_dict_with_defaults_applied(self):
        out = validate_credentials(
            VoiceProvider.SIP,
            {
                "sip_username": "u",
                "sip_password": "p",
                "sip_realm": "r",
                "sip_proxy": "p:5060",
            },
        )
        self.assertEqual(out["dtmf_mode"], "rfc2833")
        self.assertEqual(out["sip_transport"], "udp")


class VoiceProviderConfigCleanTests(TestCase):
    """``VoiceProviderConfig.clean()`` calls validate_credentials, so
    saving with bad creds must raise Django's ValidationError."""

    @classmethod
    def setUpTestData(cls):
        from tenants.models import Tenant

        cls.tenant = Tenant.objects.create(name="VPCClean Tenant")

    def test_save_with_valid_credentials_succeeds(self):
        from voice.models import VoiceProviderConfig

        cfg = VoiceProviderConfig(
            tenant=self.tenant,
            name="Valid creds",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        )
        cfg.full_clean()  # should not raise

    def test_save_with_invalid_credentials_raises(self):
        from django.core.exceptions import ValidationError

        from voice.models import VoiceProviderConfig

        cfg = VoiceProviderConfig(
            tenant=self.tenant,
            name="Invalid creds",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"only_auth_token": "t"}),  # missing account_sid
        )
        with pytest.raises(ValidationError) as ctx:
            cfg.full_clean()
        self.assertIn("credentials", ctx.value.message_dict)

    def test_empty_credentials_does_not_raise(self):
        from voice.models import VoiceProviderConfig

        cfg = VoiceProviderConfig(
            tenant=self.tenant,
            provider=VoiceProvider.TWILIO,
            credentials="",
        )
        # full_clean would still complain about the required ``credentials``
        # field if it weren't nullable. Call clean() directly to exercise
        # only our custom validation.
        cfg.clean()
