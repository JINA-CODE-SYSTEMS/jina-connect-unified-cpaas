"""CDR importers (#170).

Some SIP / wholesale carriers publish a daily CDR (call-detail record)
file instead of (or in addition to) a real-time webhook. This module
parses those files and reconciles each row against the local
``VoiceCall`` row — filling in cost from the carrier when we'd otherwise
fall back on the local rate card.

A new vendor = a new parser class + an entry in ``_IMPORTERS_BY_VENDOR``.
For the common case (a simple CSV with predictable columns) callers
can use ``GenericCSVCDRImporter`` and configure the column-name map.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CDRRow:
    """One reconciled row out of a CDR file.

    ``provider_call_id`` lines up with ``VoiceCall.provider_call_id``;
    everything else is what the carrier billed for that call.
    """

    provider_call_id: str
    duration_seconds: int
    cost: Decimal
    currency: str
    raw: dict


class CDRImportError(Exception):
    """Raised on unparseable CDR rows / unknown vendor profiles."""


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────


class CDRImporter:
    """Base class — subclasses implement ``parse(file_path)``."""

    vendor: str = ""

    def parse(self, file_path: str) -> Iterator[CDRRow]:
        raise NotImplementedError


class GenericCSVCDRImporter(CDRImporter):
    """CSV importer with a configurable column map.

    Subclass it (or instantiate with a different ``columns`` dict) for
    vendors whose headers differ from our defaults. Defaults match the
    generic SIP profile shipped in #163.
    """

    vendor = "generic"

    columns = {
        "provider_call_id": "call_id",
        "duration_seconds": "duration_seconds",
        "cost": "cost",
        "currency": "currency",
    }

    def __init__(self, columns: dict[str, str] | None = None) -> None:
        if columns is not None:
            self.columns = {**self.columns, **columns}

    def parse(self, file_path: str) -> Iterator[CDRRow]:
        with open(file_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                yield self._row_from_dict(row)

    def _row_from_dict(self, row: dict[str, str]) -> CDRRow:
        try:
            call_id = (row[self.columns["provider_call_id"]] or "").strip()
            duration = int(row[self.columns["duration_seconds"]] or 0)
            cost = Decimal(row[self.columns["cost"]] or "0")
            currency = (row.get(self.columns["currency"]) or "USD").strip()
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise CDRImportError(f"unparseable CDR row {row!r}: {exc}") from exc
        return CDRRow(
            provider_call_id=call_id,
            duration_seconds=duration,
            cost=cost,
            currency=currency,
            raw=row,
        )


class KnowlarityCDRImporter(GenericCSVCDRImporter):
    """Knowlarity (Indian SIP carrier) publishes CDRs with different headers."""

    vendor = "knowlarity"

    columns = {
        "provider_call_id": "CallSid",
        "duration_seconds": "CallDuration",
        "cost": "Charge",
        "currency": "Currency",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


_IMPORTERS_BY_VENDOR: dict[str, type[CDRImporter]] = {
    "generic": GenericCSVCDRImporter,
    "knowlarity": KnowlarityCDRImporter,
}


def register_cdr_importer(vendor: str, cls: type[CDRImporter]) -> None:
    """Register a parser class under ``vendor``. Re-registering the
    same class is a no-op; a different class raises."""
    existing = _IMPORTERS_BY_VENDOR.get(vendor)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(
            f"CDR importer for {vendor!r} already registered as {existing.__name__}; "
            f"refusing to overwrite with {cls.__name__}"
        )
    _IMPORTERS_BY_VENDOR[vendor] = cls


def get_cdr_importer(vendor: str) -> CDRImporter:
    cls = _IMPORTERS_BY_VENDOR.get(vendor)
    if cls is None:
        raise CDRImportError(f"Unknown CDR vendor {vendor!r}. Registered: {sorted(_IMPORTERS_BY_VENDOR)}")
    return cls()


def _reset_cdr_importer_registry() -> None:
    """Test hook — clear all registrations except the shipped defaults."""
    _IMPORTERS_BY_VENDOR.clear()
    _IMPORTERS_BY_VENDOR["generic"] = GenericCSVCDRImporter
    _IMPORTERS_BY_VENDOR["knowlarity"] = KnowlarityCDRImporter


# ─────────────────────────────────────────────────────────────────────────────
# Import driver
# ─────────────────────────────────────────────────────────────────────────────


# When local-computed cost diverges from carrier cost by more than this
# ratio we log a discrepancy line for manual review. Tunable on the
# tenant side via settings later — keeping it conservative for now.
DEFAULT_DISCREPANCY_RATIO = Decimal("0.10")


def import_cdr_file(
    file_path: str,
    provider_config_id,
    *,
    vendor: str = "generic",
    discrepancy_ratio: Decimal = DEFAULT_DISCREPANCY_RATIO,
) -> dict[str, int]:
    """Parse ``file_path`` with the ``vendor`` parser and reconcile rows.

    For each row that matches a ``VoiceCall`` by ``provider_call_id``:
      * Update ``cost_amount`` / ``cost_currency`` / ``cost_source`` to
        ``PROVIDER`` (carrier-authoritative).
      * If the call already carried a ``LOCAL_RATECARD`` estimate, log
        when the carrier number diverges by more than
        ``discrepancy_ratio``.

    Returns a count summary: ``{"matched": N, "updated": N, "skipped":
    N, "discrepancies": N}``.
    """
    importer = get_cdr_importer(vendor)
    rows = importer.parse(file_path)
    return _apply_rows(rows, provider_config_id, discrepancy_ratio)


def _apply_rows(
    rows: Iterable[CDRRow],
    provider_config_id,
    discrepancy_ratio: Decimal,
) -> dict[str, int]:
    """Reconcile parsed CDR rows against ``VoiceCall`` rows."""
    from voice.constants import CostSource
    from voice.models import VoiceCall

    matched = updated = skipped = discrepancies = 0

    for row in rows:
        try:
            call = VoiceCall.objects.get(
                provider_config_id=provider_config_id,
                provider_call_id=row.provider_call_id,
            )
        except VoiceCall.DoesNotExist:
            logger.info(
                "[voice.billing.cdr] no VoiceCall for provider_call_id=%s; skipping",
                row.provider_call_id,
            )
            skipped += 1
            continue

        matched += 1

        prior_cost = call.cost_amount
        prior_source = call.cost_source

        call.cost_amount = row.cost
        call.cost_currency = row.currency
        call.cost_source = CostSource.PROVIDER
        if not call.duration_seconds and row.duration_seconds:
            call.duration_seconds = row.duration_seconds

        call.save(
            update_fields=[
                "cost_amount",
                "cost_currency",
                "cost_source",
                "duration_seconds",
                "updated_at",
            ]
        )
        updated += 1

        # If we already had a local-ratecard estimate, flag big swings
        # so a human can sanity check.
        if (
            prior_cost is not None
            and prior_source == CostSource.LOCAL_RATECARD
            and prior_cost > 0
            and abs(row.cost - prior_cost) / prior_cost > discrepancy_ratio
        ):
            logger.warning(
                "[voice.billing.cdr] cost discrepancy for call %s: local=%s carrier=%s",
                call.id,
                prior_cost,
                row.cost,
            )
            discrepancies += 1

    return {
        "matched": matched,
        "updated": updated,
        "skipped": skipped,
        "discrepancies": discrepancies,
    }
