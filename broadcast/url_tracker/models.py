"""
URL Tracking Models for Broadcast Messages.

When a tenant user adds a URL button to a WhatsApp template, we replace it with
a tracked URL (e.g. https://localhost:8000/r/AbC12x/) that
redirects through our server. Each click is logged, giving tenants visibility
into URL button engagement — something WhatsApp does NOT provide via webhooks.

Architecture:
    TrackedURL   – one per (original_url, broadcast_message, button_index) tuple
    TrackedURLClick – one per click event (stores IP, UA, timestamp, etc.)
"""

import secrets
import string

from django.db import models
from django.utils import timezone

# ── Short code generator ──────────────────────────────────────────────────
_CODE_ALPHABET = string.ascii_letters + string.digits  # a-zA-Z0-9 → 62 chars
_CODE_LENGTH = 8  # 62^8 ≈ 218 trillion unique codes


def _generate_short_code() -> str:
    """Generate a cryptographically random short code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


# ── TrackedURL ────────────────────────────────────────────────────────────


class TrackedURL(models.Model):
    """
    Maps a short code to an original URL for click tracking.

    One TrackedURL is created for every URL button in every BroadcastMessage.
    This is a per-recipient link (not per-broadcast) so we can attribute
    clicks to individual contacts.
    """

    # Unique short code used in the redirect URL: /r/<code>/
    code = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        default=_generate_short_code,
        editable=False,
    )

    # The destination the user is redirected to
    original_url = models.URLField(max_length=2048)

    # ── Relations ─────────────────────────────────────────────────────────
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="tracked_urls",
    )
    broadcast = models.ForeignKey(
        "broadcast.Broadcast",
        on_delete=models.CASCADE,
        related_name="tracked_urls",
        null=True,
        blank=True,
        help_text="Broadcast this link belongs to",
    )
    broadcast_message = models.ForeignKey(
        "broadcast.BroadcastMessage",
        on_delete=models.CASCADE,
        related_name="tracked_urls",
        null=True,
        blank=True,
        help_text="Per-recipient broadcast message this link belongs to",
    )
    contact = models.ForeignKey(
        "contacts.TenantContact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracked_urls",
        help_text="Contact who received this tracked link",
    )

    # Which button position in the template (0-indexed)
    button_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Index of the URL button in the template",
    )
    button_text = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Display text of the button (e.g. 'Shop Now')",
    )

    # ── Denormalized counters (updated atomically on each click) ──────────
    click_count = models.PositiveIntegerField(default=0)
    first_clicked_at = models.DateTimeField(null=True, blank=True)
    last_clicked_at = models.DateTimeField(null=True, blank=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "broadcast_tracked_url"
        indexes = [
            models.Index(fields=["tenant", "broadcast"]),
            models.Index(fields=["contact"]),
            models.Index(fields=["broadcast_message"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"[{self.code}] → {self.original_url[:80]}"

    @property
    def tracked_url(self) -> str:
        """Full tracked URL (e.g. https://localhost:8000/r/AbC12x/)."""
        from django.conf import settings

        base = getattr(settings, "BASE_URL", "http://localhost:8000").rstrip("/")
        return f"{base}/r/{self.code}/"

    def record_click(self, ip_address: str = "", user_agent: str = "", referer: str = ""):
        """
        Record a click event and update denormalized counters.
        Uses F() expressions for atomic counter increment (no race conditions).
        """
        now = timezone.now()

        # Create the click record
        TrackedURLClick.objects.create(
            tracked_url=self,
            ip_address=ip_address,
            user_agent=user_agent[:512] if user_agent else "",
            referer=referer[:2048] if referer else "",
            clicked_at=now,
        )

        # Atomically update counters
        from django.db.models import F

        updates = {
            "click_count": F("click_count") + 1,
            "last_clicked_at": now,
        }
        if not self.first_clicked_at:
            updates["first_clicked_at"] = now

        TrackedURL.objects.filter(pk=self.pk).update(**updates)


# ── TrackedURLClick ───────────────────────────────────────────────────────


class TrackedURLClick(models.Model):
    """
    Individual click event on a tracked URL.
    Stores metadata useful for analytics (IP, user-agent, timestamp).
    """

    tracked_url = models.ForeignKey(
        TrackedURL,
        on_delete=models.CASCADE,
        related_name="clicks",
    )

    clicked_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    referer = models.URLField(max_length=2048, blank=True, default="")

    class Meta:
        db_table = "broadcast_tracked_url_click"
        ordering = ["-clicked_at"]
        indexes = [
            models.Index(fields=["tracked_url", "clicked_at"]),
        ]

    def __str__(self):
        return f"Click on {self.tracked_url.code} at {self.clicked_at}"
