"""
WhatsApp Quota Service

Implements the TWO-WINDOW quota validation as per ticket specification.

Core Rule (Invariant):
    A tenant can message at most N unique contacts in any rolling 24-hour window.

Key Insight (Why Two Windows):
    When scheduling a broadcast at time T, the only rolling 24-hour windows
    that can be violated are:
    - Backward window: [T − 24h, T)
    - Forward window: [T, T + 24h)

    Every rolling window that includes T fully lies in one of these two ranges.
    Therefore, both must be validated independently.
"""

from datetime import timedelta

from django.utils import timezone

from broadcast.models import BroadcastStatusChoices
from tenants.models import WABAInfo


class QuotaService:
    """
    Manages WhatsApp conversation initiation quotas using TWO-WINDOW validation.

    Validation Logic (per ticket):
    1. Define windows: window_before=[T-24h, T), window_after=[T, T+24h)
    2. Get unique contacts from broadcasts in each window
    3. Calculate remaining: remaining = tier_limit - contacts_count
    4. Effective remaining = min(remaining_before, remaining_after)
    """

    # Statuses that count toward quota (active/pending broadcasts)
    # Uses BroadcastStatusChoices enum for type safety
    QUOTA_RELEVANT_STATUSES = [
        BroadcastStatusChoices.QUEUED,
        BroadcastStatusChoices.SCHEDULED,
        BroadcastStatusChoices.SENDING,
        BroadcastStatusChoices.SENT,
        BroadcastStatusChoices.PARTIALLY_SENT,
    ]

    def __init__(self, wa_app):
        """
        Initialize QuotaService for a specific WA app.

        Args:
            wa_app: TenantWAApp instance
        """
        self.wa_app = wa_app

    @property
    def tier_limit(self) -> int:
        """Get quota limit from WABAInfo tier using centralized MessagingLimit.get_limit()."""
        try:
            if hasattr(self.wa_app, "waba_info") and self.wa_app.waba_info:
                tier = self.wa_app.waba_info.messaging_limit
                return WABAInfo.MessagingLimit.get_limit(tier)
        except Exception:
            pass
        return WABAInfo.MessagingLimit.get_limit(None)  # Returns conservative default (50)

    @property
    def tier_name(self) -> str:
        """Get the tier name from WABAInfo."""
        try:
            if hasattr(self.wa_app, "waba_info") and self.wa_app.waba_info:
                return self.wa_app.waba_info.messaging_limit or WABAInfo.MessagingLimit.TIER_NOT_SET
        except Exception:
            pass
        return WABAInfo.MessagingLimit.TIER_NOT_SET

    @property
    def is_unlimited(self) -> bool:
        """Check if tier is unlimited using centralized enum."""
        return self.tier_name == WABAInfo.MessagingLimit.TIER_UNLIMITED

    # =========================================================================
    # CORE TWO-WINDOW QUOTA METHODS
    # =========================================================================

    def get_unique_contacts_in_window(self, window_start, window_end, exclude_broadcast_id=None) -> set:
        """
        Get unique recipient phone numbers from broadcasts in a time window.

        This is the CORE method for quota calculation per ticket specification.
        It queries broadcasts by scheduled_time and gets their recipients.

        Args:
            window_start: Start of time window (inclusive)
            window_end: End of time window (exclusive)
            exclude_broadcast_id: Broadcast ID to exclude (for re-validation)

        Returns:
            set: Unique phone numbers from broadcasts in the window
        """
        from contacts.models import TenantContact
        from wa.models import WABroadcast

        # Query broadcasts in window with quota-relevant statuses
        # Filter by tenant (via wa_app.tenant) since Broadcast has tenant FK
        qs = WABroadcast.objects.filter(
            tenant=self.wa_app.tenant,
            scheduled_time__gte=window_start,
            scheduled_time__lt=window_end,
            status__in=self.QUOTA_RELEVANT_STATUSES,
        )

        if exclude_broadcast_id:
            qs = qs.exclude(id=exclude_broadcast_id)

        # Single DB query: get distinct phone numbers via the M2M relationship
        # instead of loading all broadcast + recipient objects into Python memory.
        phones = set(TenantContact.objects.filter(broadcasts__in=qs).values_list("phone", flat=True).distinct())

        # Normalize phone numbers (strip non-digit/+ chars)
        return {"".join(c for c in str(phone) if c.isdigit() or c == "+") for phone in phones if phone}

    def compute_quota(self, scheduled_time, exclude_broadcast_id=None) -> dict:
        """
        Compute quota status for a given scheduled time using two-window approach.

        Per ticket specification:
        1. Define windows: window_before=[T-24h, T), window_after=[T, T+24h)
        2. Get unique contacts from broadcasts in each window
        3. Calculate remaining for each window
        4. Effective remaining = min(remaining_before, remaining_after)

        Args:
            scheduled_time: The time T to evaluate
            exclude_broadcast_id: Broadcast ID to exclude (for re-validation)

        Returns:
            dict: Quota computation with contacts and remaining for each window
        """
        T = scheduled_time

        # Define windows
        window_before_start = T - timedelta(hours=24)
        window_before_end = T
        window_after_start = T
        window_after_end = T + timedelta(hours=24)

        # Get unique contacts in each window
        contacts_before = self.get_unique_contacts_in_window(
            window_before_start, window_before_end, exclude_broadcast_id
        )
        contacts_after = self.get_unique_contacts_in_window(window_after_start, window_after_end, exclude_broadcast_id)

        # Calculate remaining capacity per window
        remaining_before = max(0, self.tier_limit - len(contacts_before))
        remaining_after = max(0, self.tier_limit - len(contacts_after))

        # Effective remaining quota
        effective_remaining = min(remaining_before, remaining_after)

        return {
            "scheduled_time": T,
            "tier_limit": self.tier_limit,
            # Before window
            "window_before": {
                "start": window_before_start,
                "end": window_before_end,
                "contacts": contacts_before,
                "count": len(contacts_before),
                "remaining": remaining_before,
            },
            # After window
            "window_after": {
                "start": window_after_start,
                "end": window_after_end,
                "contacts": contacts_after,
                "count": len(contacts_after),
                "remaining": remaining_after,
            },
            # Effective
            "effective_remaining": effective_remaining,
        }

    def validate_broadcast(
        self, recipient_phones: list, scheduled_time=None, exclude_broadcast_id=None, open_session_phones: set = None
    ) -> dict:
        """
        Validate if broadcast can proceed within quota limits.

        TWO-WINDOW VALIDATION (per ticket):
        1. Get contacts_before = unique recipients in [T-24h, T)
        2. Get contacts_after = unique recipients in [T, T+24h)
        3. Determine new unique contacts: new_unique = recipients - (contacts_before ∪ contacts_after)
        4. Optionally exclude open-session (CSW) contacts from new_unique count
        5. Validate: if |new_unique| > effective_remaining: reject

        Args:
            recipient_phones: List of recipient phone numbers
            scheduled_time: When broadcast will execute (defaults to now)
            exclude_broadcast_id: Broadcast ID to exclude (for re-validation)
            open_session_phones: Set of phone strings with active CSW (24h).
                If provided, these contacts are excluded from quota consumption
                since they don't open a new conversation.

        Returns:
            dict: Validation result with is_valid, details, and error message
        """
        T = scheduled_time or timezone.now()

        # Handle unlimited tier
        if self.is_unlimited:
            return {
                "is_valid": True,
                "tier_limit": None,
                "is_unlimited": True,
                "total_recipients": len(recipient_phones),
                "new_unique_contacts": len(recipient_phones),
                "open_session_excluded": len(open_session_phones) if open_session_phones else 0,
                "error": None,
            }

        # Normalize recipient phones
        broadcast_contacts = set()
        for phone in recipient_phones:
            normalized = "".join(c for c in str(phone) if c.isdigit() or c == "+")
            broadcast_contacts.add(normalized)

        # Compute quota for scheduled time
        quota = self.compute_quota(T, exclude_broadcast_id)

        contacts_before = quota["window_before"]["contacts"]
        contacts_after = quota["window_after"]["contacts"]
        effective_remaining = quota["effective_remaining"]

        # Per ticket: new_unique_contacts = broadcast.recipients − (contacts_before ∪ contacts_after)
        # These are contacts NOT in either window - truly new contacts
        all_existing_contacts = contacts_before | contacts_after
        new_unique_contacts = broadcast_contacts - all_existing_contacts

        # Exclude open-session contacts from quota count (Issue #190)
        # Contacts with an active CSW don't open a new conversation, so they
        # don't consume daily quota.
        open_session_excluded = 0
        if open_session_phones:
            normalized_csw = set()
            for phone in open_session_phones:
                normalized_csw.add("".join(c for c in str(phone) if c.isdigit() or c == "+"))
            quota_relevant = new_unique_contacts - normalized_csw
            open_session_excluded = len(new_unique_contacts) - len(quota_relevant)
            new_unique_contacts = quota_relevant

        # Validate: if |new_unique_contacts| > effective_remaining: reject
        is_valid = len(new_unique_contacts) <= effective_remaining

        # Build error message if validation fails
        error = None
        overflow_by = 0
        if not is_valid:
            overflow_by = len(new_unique_contacts) - effective_remaining
            error = (
                f"Quota exceeded: Need {len(new_unique_contacts)} new unique contacts, "
                f"but only {effective_remaining} available (Tier limit: {self.tier_limit}, "
                f"overflow by {overflow_by})"
            )

        return {
            "is_valid": is_valid,
            "tier_limit": self.tier_limit,
            "is_unlimited": False,
            # Recipient breakdown
            "total_recipients": len(recipient_phones),
            "unique_new_contacts": len(new_unique_contacts),
            "open_session_excluded": open_session_excluded,
            # Window details
            "contacts_before": len(contacts_before),
            "contacts_after": len(contacts_after),
            "contacts_union": len(all_existing_contacts),
            # Effective
            "effective_remaining": effective_remaining,
            "max_allowed_new_contacts": effective_remaining,
            # Error
            "error": error,
            "overflow_by": overflow_by,
        }

    def get_quota_status(self, at_time=None) -> dict:
        """
        Get quota status for API response.

        Returns format matching FE expectations:
        {
            "tier": 1000,           # tier_limit as number
            "used_quota": 250,      # tier_limit - effective_remaining
            "remaining_quota": 750  # effective_remaining
        }

        Args:
            at_time: The reference time (defaults to now)

        Returns:
            dict: Quota status with tier, used, and remaining
        """
        at_time = at_time or timezone.now()

        # Handle unlimited tier
        if self.is_unlimited:
            return {
                "tier": None,
                "used_quota": 0,
                "remaining_quota": None,
                "is_unlimited": True,
            }

        quota = self.compute_quota(at_time)
        effective_remaining = quota["effective_remaining"]
        used_quota = self.tier_limit - effective_remaining

        # Response format matching FE mock
        return {
            "tier": self.tier_limit,  # FE expects tier_limit as number
            "used_quota": used_quota,
            "remaining_quota": effective_remaining,
        }

    # =========================================================================
    # HELPER METHODS (kept for potential future use / backward compatibility)
    # =========================================================================

    def get_contacts_with_active_csw(self, phone_list: list, at_time=None) -> set:
        """
        Get contacts that have an active Customer Service Window (CSW).

        CSW opens when the CONTACT messages US. While CSW is active (24h from
        contact's last message), our outbound messages are FREE and don't count
        against the messaging limit tier.

        Note: Currently not used in validation (conservative approach),
        but kept for future optimization.

        Args:
            phone_list: List of phone numbers to check
            at_time: The reference time (defaults to now)

        Returns:
            set: Phone numbers that have active CSW (contact messaged within 24h)
        """
        from contacts.models import TenantContact
        from team_inbox.models import MessageDirectionChoices, MessagePlatformChoices, Messages

        if not phone_list:
            return set()

        at_time = at_time or timezone.now()
        window_start = at_time - timedelta(hours=24)

        # Normalize phone numbers for comparison
        normalized_phones = []
        for phone in phone_list:
            normalized = "".join(c for c in str(phone) if c.isdigit() or c == "+")
            normalized_phones.append(normalized)
            if normalized.startswith("+"):
                normalized_phones.append(normalized[1:])
            else:
                normalized_phones.append("+" + normalized)

        # Find contacts with these phone numbers for this tenant
        tenant = self.wa_app.tenant
        contact_ids = TenantContact.objects.filter(tenant=tenant, phone__in=normalized_phones).values_list(
            "id", flat=True
        )

        if not contact_ids:
            return set()

        # Find contacts who sent INCOMING WhatsApp messages within 24h
        contacts_with_csw = (
            Messages.objects.filter(
                tenant=tenant,
                contact_id__in=contact_ids,
                direction=MessageDirectionChoices.INCOMING,
                platform=MessagePlatformChoices.WHATSAPP,
                timestamp__gte=window_start,
                timestamp__lte=at_time,
            )
            .select_related("contact")
            .values_list("contact__phone", flat=True)
            .distinct()
        )

        # Return normalized phone numbers
        result = set()
        for phone in contacts_with_csw:
            normalized = "".join(c for c in str(phone) if c.isdigit() or c == "+")
            result.add(normalized)

        return result

    def get_contacts_not_needing_quota(self, phone_list: list, at_time=None) -> set:
        """
        Get contacts that don't need quota for outbound messages.

        A contact doesn't need quota if they have an active CSW
        (they messaged us within 24h - FREE messages).

        Note: Currently not used in validation (conservative approach),
        but kept for future optimization.

        Args:
            phone_list: List of phone numbers to check
            at_time: The reference time (defaults to now)

        Returns:
            set: Phone numbers that don't consume quota
        """
        if not phone_list:
            return set()

        at_time = at_time or timezone.now()
        return self.get_contacts_with_active_csw(phone_list, at_time)
