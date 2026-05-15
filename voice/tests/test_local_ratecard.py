"""Local rate-card billing + CDR importer tests (#170)."""

from __future__ import annotations

import csv
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tenants.models import Tenant
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.adapters.registry import (
    _ADAPTER_BY_PROVIDER,
    _reset_voice_adapter_registry,
    register_voice_adapter,
)
from voice.adapters.sip import SIPVoiceAdapter
from voice.billing import importers
from voice.billing.importers import (
    CDRImportError,
    GenericCSVCDRImporter,
    KnowlarityCDRImporter,
    _reset_cdr_importer_registry,
    get_cdr_importer,
    import_cdr_file,
    register_cdr_importer,
)
from voice.billing.rater import compute_local_cost, rate_call_and_record
from voice.constants import CallDirection, CallStatus, CostSource, VoiceProvider
from voice.models import VoiceCall, VoiceProviderConfig, VoiceRateCard
from voice.signals import call_completed


def _make_sip_call(tenant, *, to_number="+919999911111", duration=65, provider_call_id="SIP_call_1"):
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="SIP RC",
        provider=VoiceProvider.SIP,
        credentials=json.dumps(
            {
                "sip_username": "u",
                "sip_password": "p",
                "sip_realm": "example.com",
                "sip_proxy": "sip.example.com",
            }
        ),
        currency="USD",
    )
    return VoiceCall.objects.create(
        tenant=tenant,
        name="rc-call",
        provider_config=cfg,
        provider_call_id=provider_call_id,
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number=to_number,
        status=CallStatus.COMPLETED,
        duration_seconds=duration,
        ended_at=timezone.now(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# compute_local_cost — prefix matching + increment rounding
# ─────────────────────────────────────────────────────────────────────────────


class ComputeLocalCostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="RC Tenant")

    def _add_rate(self, cfg, prefix, rate, *, increment=60, currency="USD", valid_from=None, valid_to=None):
        return VoiceRateCard.objects.create(
            name=f"RC {prefix}",
            provider_config=cfg,
            destination_prefix=prefix,
            rate_per_minute=Decimal(rate),
            currency=currency,
            billing_increment_seconds=increment,
            valid_from=valid_from or (timezone.now() - timedelta(days=1)),
            valid_to=valid_to,
        )

    def test_longest_prefix_wins(self):
        call = _make_sip_call(self.tenant, to_number="+918050001234")
        # Generic catch-all + India national + India Bangalore prefix.
        self._add_rate(call.provider_config, "+", "0.05")
        self._add_rate(call.provider_config, "+91", "0.015")
        self._add_rate(call.provider_config, "+9180", "0.005")

        cost, currency, card = compute_local_cost(call)
        self.assertEqual(card.destination_prefix, "+9180")
        # 65s with 60s increment → 120s billable → 2 minutes * 0.005.
        self.assertEqual(cost, Decimal("0.010000"))
        self.assertEqual(currency, "USD")

    def test_increment_rounds_up(self):
        call = _make_sip_call(self.tenant, duration=10)
        self._add_rate(call.provider_config, "+91", "0.06", increment=30)
        cost, _, _ = compute_local_cost(call)
        # 10s with 30s increment → 30s billable → 0.5 min * 0.06 = 0.03.
        self.assertEqual(cost, Decimal("0.030000"))

    def test_per_second_billing(self):
        call = _make_sip_call(self.tenant, duration=37)
        self._add_rate(call.provider_config, "+91", "0.06", increment=1)
        cost, _, _ = compute_local_cost(call)
        # 37s billable @ 0.06/min = 37/60 * 0.06 = 0.037.
        self.assertEqual(cost, Decimal("0.037000"))

    def test_no_matching_card_returns_none(self):
        call = _make_sip_call(self.tenant, to_number="+447400000000")
        self._add_rate(call.provider_config, "+91", "0.015")
        cost, currency, card = compute_local_cost(call)
        self.assertIsNone(cost)
        self.assertEqual(currency, "")
        self.assertIsNone(card)

    def test_validity_window_excludes_expired_card(self):
        call = _make_sip_call(self.tenant)
        # Expired card.
        self._add_rate(
            call.provider_config,
            "+91",
            "0.015",
            valid_from=timezone.now() - timedelta(days=30),
            valid_to=timezone.now() - timedelta(days=1),
        )
        cost, _, card = compute_local_cost(call)
        self.assertIsNone(cost)
        self.assertIsNone(card)

    def test_zero_duration_returns_zero(self):
        call = _make_sip_call(self.tenant, duration=0)
        cost, currency, _ = compute_local_cost(call)
        self.assertEqual(cost, Decimal("0"))
        self.assertEqual(currency, "USD")


# ─────────────────────────────────────────────────────────────────────────────
# rate_call_and_record — persistence + transaction write
# ─────────────────────────────────────────────────────────────────────────────


class RateCallAndRecordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Rec Tenant")

    def test_writes_call_and_transaction(self):
        call = _make_sip_call(self.tenant)
        VoiceRateCard.objects.create(
            name="RC India",
            provider_config=call.provider_config,
            destination_prefix="+91",
            rate_per_minute=Decimal("0.015"),
            currency="USD",
            billing_increment_seconds=60,
            valid_from=timezone.now() - timedelta(days=1),
        )

        from transaction.models import TenantTransaction

        wrote = rate_call_and_record(call)
        self.assertTrue(wrote)

        call.refresh_from_db()
        # 65s → 2 minutes → 2 * 0.015 = 0.030.
        self.assertEqual(call.cost_amount, Decimal("0.030000"))
        self.assertEqual(call.cost_source, CostSource.LOCAL_RATECARD)
        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 1)

    def test_idempotent_when_already_billed(self):
        call = _make_sip_call(self.tenant)
        call.cost_amount = Decimal("1.0")
        call.save(update_fields=["cost_amount", "updated_at"])

        wrote = rate_call_and_record(call)
        self.assertFalse(wrote)

    def test_returns_false_when_no_card_matches(self):
        call = _make_sip_call(self.tenant, to_number="+447400000000", duration=10)
        wrote = rate_call_and_record(call)
        self.assertFalse(wrote)


# ─────────────────────────────────────────────────────────────────────────────
# Signal routing — SIP routes to local rater, Twilio to provider fetch
# ─────────────────────────────────────────────────────────────────────────────


class BillingSignalRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Routing Tenant")

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)
        register_voice_adapter(VoiceProvider.SIP.value, SIPVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @patch("voice.signals.write_to_team_inbox")
    @patch("voice.signals.release_concurrency_semaphore")
    @patch("voice.billing.tasks.rate_call_locally.delay")
    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    def test_sip_routes_to_local_rater(self, mock_provider, mock_local, _rel, _inbox):
        call = _make_sip_call(self.tenant)
        call_completed.send(sender=VoiceCall, call=call)
        mock_local.assert_called_once_with(str(call.id))
        mock_provider.assert_not_called()

    @patch("voice.signals.write_to_team_inbox")
    @patch("voice.signals.release_concurrency_semaphore")
    @patch("voice.billing.tasks.rate_call_locally.delay")
    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    def test_twilio_routes_to_provider_fetch(self, mock_provider, mock_local, _rel, _inbox):
        cfg = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            name="Twilio routing",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        )
        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name="routing-twilio",
            provider_config=cfg,
            provider_call_id="CA_route_t",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550199",
            status=CallStatus.COMPLETED,
            duration_seconds=60,
            ended_at=timezone.now(),
        )
        call_completed.send(sender=VoiceCall, call=call)
        mock_provider.assert_called_once()
        self.assertEqual(mock_provider.call_args.kwargs.get("args"), [str(call.id)])
        mock_local.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# CDR importer — parsers + reconciliation
# ─────────────────────────────────────────────────────────────────────────────


class CDRParserTests(TestCase):
    def test_generic_csv_parser_reads_expected_columns(self):
        csv_text = "call_id,duration_seconds,cost,currency\nC_1,30,0.01,USD\nC_2,120,0.05,USD\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write(csv_text)
            path = fh.name

        rows = list(GenericCSVCDRImporter().parse(path))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].provider_call_id, "C_1")
        self.assertEqual(rows[0].duration_seconds, 30)
        self.assertEqual(rows[0].cost, Decimal("0.01"))
        self.assertEqual(rows[1].currency, "USD")

    def test_knowlarity_parser_uses_vendor_columns(self):
        csv_text = "CallSid,CallDuration,Charge,Currency\nKN_1,45,0.02,INR\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write(csv_text)
            path = fh.name

        rows = list(KnowlarityCDRImporter().parse(path))
        self.assertEqual(rows[0].provider_call_id, "KN_1")
        self.assertEqual(rows[0].duration_seconds, 45)
        self.assertEqual(rows[0].currency, "INR")

    def test_unparseable_row_raises(self):
        csv_text = "call_id,duration_seconds,cost,currency\nX,notanumber,0.01,USD\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write(csv_text)
            path = fh.name

        with self.assertRaises(CDRImportError):
            list(GenericCSVCDRImporter().parse(path))


class CDRRegistryTests(TestCase):
    def setUp(self):
        self._snapshot = dict(importers._IMPORTERS_BY_VENDOR)

    def tearDown(self):
        importers._IMPORTERS_BY_VENDOR.clear()
        importers._IMPORTERS_BY_VENDOR.update(self._snapshot)

    def test_get_unknown_raises(self):
        _reset_cdr_importer_registry()
        with self.assertRaises(CDRImportError):
            get_cdr_importer("nope")

    def test_register_is_idempotent(self):
        _reset_cdr_importer_registry()
        register_cdr_importer("generic", GenericCSVCDRImporter)
        # No-op when same class re-registered.
        register_cdr_importer("generic", GenericCSVCDRImporter)

    def test_register_rejects_conflict(self):
        class _Other(GenericCSVCDRImporter):
            pass

        _reset_cdr_importer_registry()
        with self.assertRaises(ValueError):
            register_cdr_importer("generic", _Other)


class CDRReconcileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="CDR Tenant")

    def _csv_path(self, rows):
        buf = StringIO()
        writer = csv.DictWriter(buf, fieldnames=["call_id", "duration_seconds", "cost", "currency"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        path = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        path.write(buf.getvalue())
        path.close()
        return path.name

    def test_updates_cost_on_matched_call(self):
        call = _make_sip_call(self.tenant, provider_call_id="CDR_1")
        path = self._csv_path([{"call_id": "CDR_1", "duration_seconds": "60", "cost": "0.025", "currency": "USD"}])

        summary = import_cdr_file(path, call.provider_config_id)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["skipped"], 0)

        call.refresh_from_db()
        self.assertEqual(call.cost_amount, Decimal("0.025"))
        self.assertEqual(call.cost_source, CostSource.PROVIDER)

    def test_skips_unmatched_call_id(self):
        call = _make_sip_call(self.tenant, provider_call_id="CDR_MATCH")
        path = self._csv_path(
            [
                {"call_id": "CDR_NOPE", "duration_seconds": "30", "cost": "0.01", "currency": "USD"},
                {"call_id": "CDR_MATCH", "duration_seconds": "30", "cost": "0.01", "currency": "USD"},
            ]
        )

        summary = import_cdr_file(path, call.provider_config_id)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["skipped"], 1)

    def test_logs_discrepancy_when_estimate_diverges(self):
        call = _make_sip_call(self.tenant, provider_call_id="CDR_DIFF")
        # Pre-existing local estimate.
        call.cost_amount = Decimal("0.010000")
        call.cost_source = CostSource.LOCAL_RATECARD
        call.save(update_fields=["cost_amount", "cost_source", "updated_at"])

        # Carrier billed 50% more — well above the 10% threshold.
        path = self._csv_path([{"call_id": "CDR_DIFF", "duration_seconds": "65", "cost": "0.015", "currency": "USD"}])
        summary = import_cdr_file(path, call.provider_config_id)
        self.assertEqual(summary["discrepancies"], 1)

    def test_no_discrepancy_when_within_ratio(self):
        call = _make_sip_call(self.tenant, provider_call_id="CDR_OK")
        call.cost_amount = Decimal("0.010000")
        call.cost_source = CostSource.LOCAL_RATECARD
        call.save(update_fields=["cost_amount", "cost_source", "updated_at"])

        # Carrier billed 5% more — under the 10% threshold.
        path = self._csv_path([{"call_id": "CDR_OK", "duration_seconds": "65", "cost": "0.0105", "currency": "USD"}])
        summary = import_cdr_file(path, call.provider_config_id)
        self.assertEqual(summary["discrepancies"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# Management commands
# ─────────────────────────────────────────────────────────────────────────────


class SeedVoiceRateCardsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Seed Tenant")

    def test_seeds_default_rate_cards(self):
        cfg = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            name="Seed cfg",
            provider=VoiceProvider.SIP,
        )
        out = StringIO()
        call_command("seed_voice_rate_cards", config_id=str(cfg.id), stdout=out)
        cards = VoiceRateCard.objects.filter(provider_config=cfg)
        # 4 defaults: +91, +1, +44, + (catch-all)
        self.assertEqual(cards.count(), 4)
        self.assertIn("Seeded", out.getvalue())

    def test_refuses_to_double_seed_without_force(self):
        cfg = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            name="Seed cfg dup",
            provider=VoiceProvider.SIP,
        )
        call_command("seed_voice_rate_cards", config_id=str(cfg.id), stdout=StringIO())
        out = StringIO()
        call_command("seed_voice_rate_cards", config_id=str(cfg.id), stdout=out)
        self.assertIn("already has", out.getvalue())
        # Still 4 cards, not 8.
        self.assertEqual(VoiceRateCard.objects.filter(provider_config=cfg).count(), 4)


class ImportVoiceCDRCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Import Tenant")

    def test_command_runs_importer_and_prints_summary(self):
        call = _make_sip_call(self.tenant, provider_call_id="CMD_1")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write("call_id,duration_seconds,cost,currency\nCMD_1,30,0.05,USD\n")
            path = fh.name

        out = StringIO()
        call_command(
            "import_voice_cdr",
            path,
            config_id=str(call.provider_config_id),
            stdout=out,
        )
        self.assertIn("matched=1", out.getvalue())
        call.refresh_from_db()
        self.assertEqual(call.cost_amount, Decimal("0.05"))
