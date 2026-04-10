"""
Charge Breakdown Service for WhatsApp Broadcasts (Issue #190).

Provides a detailed, country-wise cost breakdown for broadcast creation,
including CSW (Customer Service Window) detection to distinguish new
initiations from open-session contacts.

This is INFORMATIONAL ONLY — no wallet deduction occurs here.
Final billing happens at actual send time.
"""
import logging
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import phonenumbers
from contacts.models import TenantContact
from django.db.models import QuerySet
from django.utils import timezone

logger = logging.getLogger(__name__)

# Threshold for async processing
ASYNC_CONTACT_THRESHOLD = 1000


class ChargeBreakdownService:
    """
    Computes a country-wise charge breakdown for a set of contacts.

    Steps:
        1. Resolve contacts → phone numbers
        2. Batch CSW detection (which contacts have 24h open session?)
        3. Group contacts by country code
        4. Per-country rate lookup via RateCardService
        5. Apply open_session_rate_multiplier for CSW contacts
        6. Return structured breakdown

    Usage:
        svc = ChargeBreakdownService(wa_app=wa_app)
        result = svc.compute(contact_ids=[1,2,3], template_id=uuid)
    """

    def __init__(self, wa_app):
        """
        Args:
            wa_app: TenantWAApp instance
        """
        self.wa_app = wa_app
        self.tenant = wa_app.tenant

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def compute(
        self,
        contact_ids: list = None,
        broadcast_id: int = None,
        template_id=None,
        at_time=None,
    ) -> dict:
        """
        Compute charge breakdown for a set of contacts.

        Accepts EITHER:
            - contact_ids + template_id (pre-creation estimation)
            - broadcast_id (existing broadcast with contacts attached)

        Args:
            contact_ids: List of TenantContact IDs
            broadcast_id: Existing Broadcast ID
            template_id: WATemplate ID or TemplateNumber ID for category lookup
            at_time: Reference time for CSW detection (defaults to now)

        Returns:
            dict with contact_summary, country_breakdown, summary, quota_impact, notes
        """
        at_time = at_time or timezone.now()

        # Resolve contacts and template
        contacts_qs, message_type = self._resolve_inputs(
            contact_ids=contact_ids,
            broadcast_id=broadcast_id,
            template_id=template_id,
        )

        if not contacts_qs.exists():
            return self._empty_response()

        # Get all phone numbers
        phone_map = {}  # phone_str -> contact_id
        for cid, phone in contacts_qs.values_list("id", "phone"):
            if phone:
                phone_str = str(phone).strip()
                phone_map[phone_str] = cid

        all_phones = list(phone_map.keys())
        total_contacts = len(all_phones)

        # Step 1: Batch CSW detection
        csw_phones = self._detect_csw_contacts(all_phones, at_time)
        new_initiation_phones = set(all_phones) - csw_phones
        open_session_phones = csw_phones

        # Step 2: Group by country
        country_data = self._group_by_country(
            new_initiation_phones, open_session_phones
        )

        # Step 3: Per-country rate lookup + cost computation
        from wa.services.rate_card_service import RateCardService
        rate_svc = RateCardService(self.tenant)
        open_session_multiplier = Decimal(str(
            self.wa_app.open_session_rate_multiplier or 0
        ))

        country_breakdown = []
        total_new_cost = Decimal("0")
        total_open_cost = Decimal("0")

        for country, counts in sorted(country_data.items(), key=lambda x: x[0]):
            new_count = counts["new"]
            open_count = counts["open"]

            # Get rate for this country
            rate = rate_svc.get_send_time_rate(country, message_type)
            if rate is None:
                # Fallback: flat rate from wa_app
                rate = self._get_flat_rate(message_type)

            # New initiation cost
            new_cost = rate * new_count
            total_new_cost += new_cost

            # Open session cost (rate × multiplier)
            open_rate = rate * open_session_multiplier
            open_cost = open_rate * open_count
            total_open_cost += open_cost

            country_name = self._get_country_name(country)
            country_breakdown.append({
                "country": country,
                "country_name": country_name,
                "new_initiations": {
                    "count": new_count,
                    "rate": str(rate.quantize(Decimal("0.000001"))),
                    "cost": str(new_cost.quantize(Decimal("0.000001"))),
                },
                "open_session": {
                    "count": open_count,
                    "rate": str(open_rate.quantize(Decimal("0.000001"))) if open_count > 0 else None,
                    "cost": str(open_cost.quantize(Decimal("0.000001"))),
                },
                "total_cost": str(
                    (new_cost + open_cost).quantize(Decimal("0.000001"))
                ),
            })

        estimated_total = total_new_cost + total_open_cost
        wallet_currency = str(self.tenant.balance.currency)

        # Step 4: Quota impact
        quota_impact = self._compute_quota_impact(
            new_initiation_count=len(new_initiation_phones),
            at_time=at_time,
        )

        # Build notes
        notes = self._build_notes(open_session_multiplier)

        return {
            "contact_summary": {
                "total_contacts": total_contacts,
                "new_initiations": len(new_initiation_phones),
                "open_sessions": len(open_session_phones),
            },
            "country_breakdown": country_breakdown,
            "summary": {
                "total_countries": len(country_breakdown),
                "total_new_initiations": len(new_initiation_phones),
                "total_open_sessions": len(open_session_phones),
                "estimated_total_cost": str(
                    estimated_total.quantize(Decimal("0.000001"))
                ),
                "wallet_currency": wallet_currency,
            },
            "quota_impact": quota_impact,
            "notes": notes,
        }

    # =========================================================================
    # INPUT RESOLUTION
    # =========================================================================

    def _resolve_inputs(
        self,
        contact_ids: list = None,
        broadcast_id: int = None,
        template_id=None,
    ) -> tuple:
        """
        Resolve inputs to (contacts_queryset, message_type).

        Returns:
            (QuerySet[TenantContact], str) — contacts and template category
        """
        message_type = "MARKETING"  # default

        if broadcast_id:
            from broadcast.models import Broadcast
            try:
                broadcast = Broadcast.objects.get(
                    id=broadcast_id, tenant=self.tenant
                )
                contacts_qs = broadcast.recipients.all()

                # Get message type from template
                if (
                    broadcast.template_number
                    and hasattr(broadcast.template_number, "gupshup_template")
                    and broadcast.template_number.gupshup_template
                ):
                    message_type = (
                        broadcast.template_number.gupshup_template.category
                        or "MARKETING"
                    )
                return contacts_qs, message_type

            except Broadcast.DoesNotExist:
                return TenantContact.objects.none(), message_type

        elif contact_ids:
            contacts_qs = TenantContact.objects.filter(
                id__in=contact_ids, tenant=self.tenant
            )

            # Resolve template category
            if template_id:
                message_type = self._resolve_message_type(template_id)

            return contacts_qs, message_type

        return TenantContact.objects.none(), message_type

    def _resolve_message_type(self, template_id) -> str:
        """Resolve message type from template_id (WATemplate UUID or TemplateNumber PK)."""
        # Try WATemplate first
        from wa.models import WATemplate
        try:
            tpl = WATemplate.objects.get(id=template_id)
            return tpl.category or "MARKETING"
        except (WATemplate.DoesNotExist, ValueError):
            pass

        # Try TemplateNumber → gupshup_template
        from message_templates.models import TemplateNumber
        try:
            tn = TemplateNumber.objects.select_related("gupshup_template").get(
                pk=template_id
            )
            if tn.gupshup_template:
                return tn.gupshup_template.category or "MARKETING"
        except (TemplateNumber.DoesNotExist, ValueError):
            pass

        return "MARKETING"

    # =========================================================================
    # CSW DETECTION (batch)
    # =========================================================================

    def _detect_csw_contacts(self, phone_list: list, at_time) -> set:
        """
        Batch-detect contacts with active Customer Service Window (24h).

        Uses the team_inbox Messages model to find contacts who sent us
        an incoming WhatsApp message within the last 24 hours.

        Args:
            phone_list: List of phone strings
            at_time: Reference timestamp

        Returns:
            set of phone strings that have an active CSW
        """
        from datetime import timedelta

        from team_inbox.models import (MessageDirectionChoices,
                                       MessagePlatformChoices, Messages)

        if not phone_list:
            return set()

        window_start = at_time - timedelta(hours=24)

        # Normalize phones for lookup — include both +{digits} and {digits}
        # Also build a reverse map: normalized form → original phone_list entry
        normalized = set()
        norm_to_original = {}
        for phone in phone_list:
            clean = "".join(c for c in str(phone) if c.isdigit() or c == "+")
            normalized.add(clean)
            norm_to_original[clean] = phone
            if clean.startswith("+"):
                normalized.add(clean[1:])
                norm_to_original[clean[1:]] = phone
            else:
                normalized.add("+" + clean)
                norm_to_original["+" + clean] = phone

        # Find contacts for this tenant with matching phones
        contact_phone_map = dict(
            TenantContact.objects.filter(
                tenant=self.tenant,
                phone__in=normalized,
            ).values_list("id", "phone")
        )

        if not contact_phone_map:
            return set()

        contact_ids = list(contact_phone_map.keys())

        # Batch query: find contacts who sent INCOMING WA messages in last 24h
        csw_contact_ids = set(
            Messages.objects.filter(
                tenant=self.tenant,
                contact_id__in=contact_ids,
                direction=MessageDirectionChoices.INCOMING,
                platform=MessagePlatformChoices.WHATSAPP,
                timestamp__gte=window_start,
                timestamp__lte=at_time,
            )
            .values_list("contact_id", flat=True)
            .distinct()
        )

        # Map back to original phone_list format via normalization
        csw_phones = set()
        for cid, phone in contact_phone_map.items():
            if cid in csw_contact_ids:
                clean = "".join(c for c in str(phone) if c.isdigit() or c == "+")
                original = norm_to_original.get(clean)
                if original:
                    csw_phones.add(original)

        return csw_phones

    # =========================================================================
    # COUNTRY GROUPING
    # =========================================================================

    def _group_by_country(
        self, new_phones: set, open_phones: set
    ) -> dict:
        """
        Group phone numbers by destination country.

        Returns:
            dict: {country_code: {"new": int, "open": int}}
        """
        country_data = defaultdict(lambda: {"new": 0, "open": 0})

        for phone in new_phones:
            country = self._phone_to_country(phone)
            country_data[country]["new"] += 1

        for phone in open_phones:
            country = self._phone_to_country(phone)
            country_data[country]["open"] += 1

        return dict(country_data)

    @staticmethod
    def _phone_to_country(phone_str: str) -> str:
        """Parse phone → ISO 3166-1 alpha-2 country code."""
        try:
            parsed = phonenumbers.parse(str(phone_str))
            region = phonenumbers.region_code_for_number(parsed)
            return region if region else "ZZ"
        except phonenumbers.NumberParseException:
            return "ZZ"

    # =========================================================================
    # RATE HELPERS
    # =========================================================================

    def _get_flat_rate(self, message_type: str) -> Decimal:
        """Fallback flat rate from TenantWAApp."""
        try:
            type_map = {
                "MARKETING": self.wa_app.marketing_message_price,
                "UTILITY": self.wa_app.utility_message_price,
                "AUTHENTICATION": self.wa_app.authentication_message_price,
            }
            price = type_map.get(message_type, self.wa_app.marketing_message_price)
            return Decimal(str(price.amount))
        except Exception:
            return Decimal("0.10")

    @staticmethod
    def _get_country_name(country_code: str) -> str:
        """Get full country name from ISO alpha-2 code."""
        try:
            import pycountry
            c = pycountry.countries.get(alpha_2=country_code)
            return c.name if c else country_code
        except (ImportError, AttributeError):
            # Fallback — minimal map for common countries
            NAMES = {
                "IN": "India", "US": "United States", "GB": "United Kingdom",
                "AE": "United Arab Emirates", "AU": "Australia",
                "SG": "Singapore", "BR": "Brazil", "DE": "Germany",
                "FR": "France", "CA": "Canada", "ZZ": "Unknown",
            }
            return NAMES.get(country_code, country_code)

    # =========================================================================
    # QUOTA IMPACT
    # =========================================================================

    def _compute_quota_impact(self, new_initiation_count: int, at_time) -> dict:
        """
        Compute quota impact — only NEW initiations count against limit.

        Returns:
            dict with daily_limit, used, remaining, etc.
        """
        from wa.services.quota_service import QuotaService

        quota_svc = QuotaService(self.wa_app)

        if quota_svc.is_unlimited:
            return {
                "daily_limit": None,
                "already_used": 0,
                "remaining_before": None,
                "new_initiations_needed": new_initiation_count,
                "remaining_after": None,
                "within_limit": True,
                "is_unlimited": True,
            }

        quota = quota_svc.compute_quota(at_time)
        effective_remaining = quota["effective_remaining"]
        already_used = quota_svc.tier_limit - effective_remaining

        remaining_after = effective_remaining - new_initiation_count

        return {
            "daily_limit": quota_svc.tier_limit,
            "already_used": already_used,
            "remaining_before": effective_remaining,
            "new_initiations_needed": new_initiation_count,
            "remaining_after": max(0, remaining_after),
            "within_limit": remaining_after >= 0,
            "is_unlimited": False,
        }

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _empty_response(self) -> dict:
        """Return empty breakdown response."""
        wallet_currency = str(self.tenant.balance.currency)
        return {
            "contact_summary": {
                "total_contacts": 0,
                "new_initiations": 0,
                "open_sessions": 0,
            },
            "country_breakdown": [],
            "summary": {
                "total_countries": 0,
                "total_new_initiations": 0,
                "total_open_sessions": 0,
                "estimated_total_cost": "0.000000",
                "wallet_currency": wallet_currency,
            },
            "quota_impact": {
                "daily_limit": None,
                "already_used": 0,
                "remaining_before": None,
                "new_initiations_needed": 0,
                "remaining_after": None,
                "within_limit": True,
                "is_unlimited": True,
            },
            "notes": [],
        }

    @staticmethod
    def _build_notes(open_session_multiplier: Decimal) -> list:
        """Build informational notes for the breakdown."""
        notes = [
            "This breakdown is informational only — no wallet deduction occurs at this stage.",
            "Final cost is calculated at actual send time using live rates.",
            "New initiations are charged at standard WhatsApp country rates.",
        ]
        if open_session_multiplier == Decimal("0"):
            notes.append(
                "Open-session (CSW) messages are free — the contact messaged you within 24 hours."
            )
        elif open_session_multiplier < Decimal("1"):
            notes.append(
                f"Open-session messages are charged at {open_session_multiplier * 100:.0f}% of the standard rate."
            )
        else:
            notes.append(
                "Open-session messages are charged at the full standard rate for this BSP."
            )
        notes.append("Only new initiations count against your daily WhatsApp limit.")
        return notes
