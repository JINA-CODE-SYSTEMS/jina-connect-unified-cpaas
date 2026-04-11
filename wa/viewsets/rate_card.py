"""
ViewSet for WhatsApp Rate Card API (Issue #188).

Endpoints:
    GET  /wa/rate-card/                   List tenant rate card (paginated, filterable)
    GET  /wa/rate-card/recent-changes/    Only rows where rate changed vs previous period
    GET  /wa/rate-card/summary/           Aggregated stats
"""

from django.db.models import Avg, Count, Max, Min
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from tenants.permission_classes import TenantRolePermission
from wa.models import MessageTypeChoices, TenantRateCard
from wa.serializers.rate_card import TenantRateCardSerializer
from wa.services.rate_card_service import RateCardService


class RateCardViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only rate card for the authenticated tenant.

    Rates are reference-only — final billing is calculated at send time.
    """

    serializer_class = TenantRateCardSerializer
    http_method_names = ["get"]
    permission_classes = [IsAuthenticated, TenantRolePermission]
    required_permissions = {
        "list": "rate_card.manage",
        "retrieve": "rate_card.manage",
        "recent_changes": "rate_card.manage",
        "summary": "rate_card.manage",
        "default": "rate_card.manage",
    }

    def get_queryset(self):
        tenant = self.request.user.tenant
        if not tenant:
            return TenantRateCard.objects.none()

        qs = TenantRateCard.objects.filter(tenant=tenant)

        # ---- Filters ----
        country = self.request.query_params.get("country")
        if country:
            qs = qs.filter(destination_country=country.upper())

        message_type = self.request.query_params.get("message_type")
        if message_type:
            qs = qs.filter(message_type=message_type.upper())

        currency = self.request.query_params.get("currency")
        if currency:
            qs = qs.filter(wallet_currency=currency.upper())

        effective_from = self.request.query_params.get("effective_from")
        if effective_from:
            qs = qs.filter(effective_from=effective_from)
        else:
            # Default: current period (1st of this month)
            today = timezone.now().date()
            current_period = today.replace(day=1)
            qs = qs.filter(effective_from=current_period)

        return qs.order_by("destination_country", "message_type")

    # -----------------------------------------------------------------
    # Recent Changes
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="recent-changes")
    def recent_changes(self, request):
        """
        Return only rate card entries where the rate changed vs the
        previous period. Includes new entries (previous_rate is NULL).

        Query params:
            - effective_from  (optional, defaults to current month)
            - country         (optional filter)
            - message_type    (optional filter)
            - currency        (optional filter, ISO 4217 e.g. INR, USD)
        """
        tenant = request.user.tenant
        if not tenant:
            return Response([], status=status.HTTP_200_OK)

        svc = RateCardService(tenant)

        effective_from = request.query_params.get("effective_from")
        if effective_from:
            from django.utils.dateparse import parse_date

            effective_from = parse_date(effective_from)
        else:
            today = timezone.now().date()
            effective_from = today.replace(day=1)

        qs = svc.get_recent_changes(effective_from=effective_from)

        # Additional filters
        country = request.query_params.get("country")
        if country:
            qs = qs.filter(destination_country=country.upper())

        message_type = request.query_params.get("message_type")
        if message_type:
            qs = qs.filter(message_type=message_type.upper())

        currency = request.query_params.get("currency")
        if currency:
            qs = qs.filter(wallet_currency=currency.upper())

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = TenantRateCardSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TenantRateCardSerializer(qs, many=True)
        return Response(serializer.data)

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        Aggregated rate card stats for the current period.

        Response:
        {
            "total_countries": 45,
            "total_entries": 135,
            "wallet_currency": "INR",
            "effective_from": "2026-02-01",
            "by_message_type": {
                "MARKETING":       {"avg": 0.85, "min": 0.12, "max": 2.10, "count": 45},
                "UTILITY":         {"avg": 0.45, "min": 0.08, "max": 1.50, "count": 45},
                "AUTHENTICATION":  {"avg": 0.35, "min": 0.05, "max": 1.20, "count": 45}
            },
            "recent_changes_count": 12
        }
        """
        tenant = request.user.tenant
        if not tenant:
            return Response(
                {"error": "No tenant found for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        today = timezone.now().date()
        current_period = today.replace(day=1)

        qs = TenantRateCard.objects.filter(
            tenant=tenant,
            effective_from=current_period,
        )

        if not qs.exists():
            return Response(
                {
                    "total_countries": 0,
                    "total_entries": 0,
                    "wallet_currency": str(tenant.balance.currency),
                    "effective_from": current_period.isoformat(),
                    "by_message_type": {},
                    "recent_changes_count": 0,
                },
                status=status.HTTP_200_OK,
            )

        by_type = {}
        for mt in MessageTypeChoices.values:
            agg = qs.filter(message_type=mt).aggregate(
                avg=Avg("reference_rate"),
                min=Min("reference_rate"),
                max=Max("reference_rate"),
                count=Count("id"),
            )
            if agg["count"] > 0:
                by_type[mt] = {
                    "avg": float(agg["avg"]) if agg["avg"] else 0,
                    "min": float(agg["min"]) if agg["min"] else 0,
                    "max": float(agg["max"]) if agg["max"] else 0,
                    "count": agg["count"],
                }

        svc = RateCardService(tenant)
        recent_changes_count = svc.get_recent_changes(effective_from=current_period).count()

        data = {
            "total_countries": qs.values("destination_country").distinct().count(),
            "total_entries": qs.count(),
            "wallet_currency": str(tenant.balance.currency),
            "effective_from": current_period.isoformat(),
            "by_message_type": by_type,
            "recent_changes_count": recent_changes_count,
        }
        return Response(data)
