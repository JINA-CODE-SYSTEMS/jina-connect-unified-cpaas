from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    BroadcastStatusChoices,
    MessageStatusChoices,
)
from broadcast.url_tracker.models import TrackedURLClick
from tenants.permission_classes import TenantRolePermission


PERIOD_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}

# Broadcast statuses that mean "actually sent" (not draft/scheduled/cancelled)
_SENT_STATUSES = [
    BroadcastStatusChoices.SENDING,
    BroadcastStatusChoices.SENT,
    BroadcastStatusChoices.PARTIALLY_SENT,
]


class BroadcastDashboardViewSet(ViewSet):
    """
    GET /broadcast/dashboard/stats/
    Aggregated broadcast performance metrics for the current tenant.
    """

    permission_classes = [IsAuthenticated, TenantRolePermission]
    required_permissions = {"stats": "broadcast.view"}

    # ── helpers ────────────────────────────────────────────────────────

    def _get_tenant(self, request):
        return getattr(request.user, "tenant", None)

    def _parse_params(self, request):
        period_key = request.query_params.get("period", "7d")
        if period_key not in PERIOD_DELTAS:
            period_key = "7d"

        channel = request.query_params.get("channel", "all").upper()
        if channel not in ("ALL", "WHATSAPP", "SMS", "TELEGRAM"):
            channel = "ALL"

        return period_key, channel

    def _period_range(self, period_key):
        """Return (current_start, prev_start, now) for the given period."""
        now = timezone.now()
        delta = PERIOD_DELTAS[period_key]
        current_start = now - delta
        prev_start = current_start - delta
        return current_start, prev_start, now

    def _pct_change(self, current, previous):
        """Percentage change, rounded to 1 decimal. Returns 0 when no baseline."""
        if previous == 0:
            return round(float(current) * 100, 1) if current else 0.0
        return round(((current - previous) / previous) * 100, 1)

    # ── main action ───────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response(
                {"detail": "No active tenant found."}, status=400
            )

        period_key, channel = self._parse_params(request)
        current_start, prev_start, now = self._period_range(period_key)

        # ── base querysets (tenant-scoped) ─────────────────────────────
        broadcasts_qs = Broadcast.objects.filter(tenant=tenant)
        if channel != "ALL":
            broadcasts_qs = broadcasts_qs.filter(platform=channel)

        # Only broadcasts that actually went out
        sent_filter = Q(status__in=_SENT_STATUSES)

        # Current period broadcasts
        curr_broadcasts = broadcasts_qs.filter(
            sent_filter, created_at__gte=current_start, created_at__lte=now
        )
        prev_broadcasts = broadcasts_qs.filter(
            sent_filter, created_at__gte=prev_start, created_at__lt=current_start
        )

        # ── 1. Total broadcasts ────────────────────────────────────────
        total_broadcasts_curr = curr_broadcasts.count()
        total_broadcasts_prev = prev_broadcasts.count()

        # ── 2-4. Message-level metrics ─────────────────────────────────
        msg_base = BroadcastMessage.objects.filter(
            broadcast__tenant=tenant,
        )
        if channel != "ALL":
            msg_base = msg_base.filter(broadcast__platform=channel)

        curr_msgs = msg_base.filter(
            broadcast__created_at__gte=current_start,
            broadcast__created_at__lte=now,
            broadcast__status__in=_SENT_STATUSES,
        )
        prev_msgs = msg_base.filter(
            broadcast__created_at__gte=prev_start,
            broadcast__created_at__lt=current_start,
            broadcast__status__in=_SENT_STATUSES,
        )

        curr_agg = curr_msgs.aggregate(
            delivered=Count(
                "id",
                filter=Q(
                    status__in=[
                        MessageStatusChoices.DELIVERED,
                        MessageStatusChoices.READ,
                    ]
                ),
            ),
            read=Count("id", filter=Q(status=MessageStatusChoices.READ)),
            total=Count("id"),
        )
        prev_agg = prev_msgs.aggregate(
            delivered=Count(
                "id",
                filter=Q(
                    status__in=[
                        MessageStatusChoices.DELIVERED,
                        MessageStatusChoices.READ,
                    ]
                ),
            ),
            read=Count("id", filter=Q(status=MessageStatusChoices.READ)),
            total=Count("id"),
        )

        # Total reach = delivered + read
        total_reach_curr = curr_agg["delivered"]
        total_reach_prev = prev_agg["delivered"]

        # ── 3. Total clicks ────────────────────────────────────────────
        click_base = TrackedURLClick.objects.filter(
            tracked_url__tenant=tenant,
        )
        if channel != "ALL":
            click_base = click_base.filter(tracked_url__broadcast__platform=channel)

        total_clicks_curr = click_base.filter(
            clicked_at__gte=current_start, clicked_at__lte=now
        ).count()
        total_clicks_prev = click_base.filter(
            clicked_at__gte=prev_start, clicked_at__lt=current_start
        ).count()

        # ── 4. Engagement rate = (read + replied) / delivered × 100 ────
        # We don't have "replied" status — use read / delivered
        engagement_curr = (
            round((curr_agg["read"] / total_reach_curr) * 100, 1)
            if total_reach_curr > 0
            else 0.0
        )
        engagement_prev = (
            round((prev_agg["read"] / total_reach_prev) * 100, 1)
            if total_reach_prev > 0
            else 0.0
        )

        return Response(
            {
                "total_broadcasts": total_broadcasts_curr,
                "total_reach": total_reach_curr,
                "total_clicks": total_clicks_curr,
                "engagement_rate": engagement_curr,
                "period": period_key,
                "channel": channel.lower(),
                "comparison": {
                    "broadcasts_change_percent": self._pct_change(
                        total_broadcasts_curr, total_broadcasts_prev
                    ),
                    "reach_change_percent": self._pct_change(
                        total_reach_curr, total_reach_prev
                    ),
                    "clicks_change_percent": self._pct_change(
                        total_clicks_curr, total_clicks_prev
                    ),
                    "engagement_change_percent": self._pct_change(
                        engagement_curr, engagement_prev
                    ),
                },
            }
        )
