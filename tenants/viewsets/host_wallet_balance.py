import requests
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Count
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from tenants.models import Tenant
from tenants.serializers import HostWalletSerializer
from rest_framework.permissions import IsAuthenticated
from tenants.permission_classes import TenantRolePermission

class HostWalletViewSet(viewsets.ViewSet):
    """
    Expose wallet-related APIs:
    - GET /host-wallet/balance/ → Fetch host Gupshup wallet balance + outstanding tenant balance
    - GET /host-wallet/alerts/ → Fetch wallet/billing related alerts
    - GET /host-wallet/dashboard/ → Unified Host Dashboard stats with % change
    """

    permission_classes = [IsAdminUser, TenantRolePermission]
    required_permissions = {
        "get_balance": "billing.view",
        "dashboard": "billing.view",
        "message_stats": "billing.view",
        "latest_recharges": "billing.view",
        "alerts": "billing.view",
        "default": "billing.view",
    }

    @action(detail=False, methods=["get"], url_path="balance")
    def get_balance(self, request):
        serializer = HostWalletSerializer()
        response = serializer.get_wallet_balance()
        return Response(response, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        Get unified Host Dashboard statistics with month-over-month % change.
        
        Returns all key metrics for the host dashboard:
        - Total Tenants (with % change from last month)
        - Total Messages across all platforms (with % change)
        - Platform breakdown (WhatsApp, SMS counts)
        - Total Revenue (placeholder - not implemented yet)
        
        No query parameters needed - calculates current month vs previous month automatically.
        
        Response:
        {
            "total_tenants": {
                "count": 125,
                "last_month_count": 112,
                "percentage_change": 11.6,
                "trend": "up"
            },
            "total_messages": {
                "count": 15847,
                "last_month_count": 13780,
                "percentage_change": 15.0,
                "trend": "up"
            },
            "platform_breakdown": {
                "WHATSAPP": 8500,
                "SMS": 7347
            },
            "total_revenue": {
                "amount": null,
                "currency": "USD",
                "percentage_change": null,
                "trend": null,
                "note": "Not implemented yet"
            }
        }
        """
        from team_inbox.models import Messages
        
        now = timezone.now()
        
        # Define current month and previous month date ranges
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_end = current_month_start - timedelta(seconds=1)
        previous_month_start = previous_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # ========== 1. TOTAL TENANTS ==========
        total_tenants = Tenant.objects.filter(is_active=True).count()
        
        # Tenants created before current month start (existed last month)
        tenants_last_month = Tenant.objects.filter(
            is_active=True,
            created_at__lt=current_month_start
        ).count()
        
        # Tenants that existed at end of previous month
        tenants_previous_month = Tenant.objects.filter(
            is_active=True,
            created_at__lt=previous_month_start
        ).count()
        
        tenants_pct_change, tenants_trend = self._calculate_percentage_change(
            tenants_last_month, tenants_previous_month
        )
        
        # ========== 2. TOTAL MESSAGES (All Platforms) ==========
        # Current month messages
        current_month_messages = Messages.objects.filter(
            timestamp__gte=current_month_start
        ).count()
        
        # Previous month messages
        previous_month_messages = Messages.objects.filter(
            timestamp__gte=previous_month_start,
            timestamp__lt=current_month_start
        ).count()
        
        # Total all-time messages
        total_messages = Messages.objects.count()
        
        messages_pct_change, messages_trend = self._calculate_percentage_change(
            current_month_messages, previous_month_messages
        )
        
        # ========== 3. PLATFORM BREAKDOWN ==========
        platform_stats = Messages.objects.values('platform').annotate(
            count=Count('id')
        )
        platform_breakdown = {stat['platform']: stat['count'] for stat in platform_stats}
        
        # Ensure both platforms are present (even if 0)
        platform_breakdown.setdefault('WHATSAPP', 0)
        platform_breakdown.setdefault('SMS', 0)
        
        # ========== 4. TOTAL REVENUE (Placeholder) ==========
        # TODO: Implement when revenue tracking is added
        
        return Response({
            'total_tenants': {
                'count': total_tenants,
                'last_month_count': tenants_previous_month,
                'percentage_change': tenants_pct_change,
                'trend': tenants_trend
            },
            'total_messages': {
                'count': total_messages,
                'current_month_count': current_month_messages,
                'last_month_count': previous_month_messages,
                'percentage_change': messages_pct_change,
                'trend': messages_trend
            },
            'platform_breakdown': platform_breakdown,
            'total_revenue': {
                'amount': None,
                'currency': 'USD',
                'percentage_change': None,
                'trend': None,
                'note': 'Not implemented yet'
            },
            'period': {
                'current_month_start': current_month_start.isoformat(),
                'previous_month_start': previous_month_start.isoformat(),
                'generated_at': now.isoformat()
            }
        })

    def _calculate_percentage_change(self, current: int, previous: int) -> tuple:
        """
        Calculate percentage change and trend direction.
        
        Returns:
            tuple: (percentage_change: float|None, trend: str|None)
        """
        if previous == 0:
            if current > 0:
                return (100.0, 'up')
            return (0.0, 'neutral')
        
        pct_change = round(((current - previous) / previous) * 100, 1)
        
        if pct_change > 0:
            trend = 'up'
        elif pct_change < 0:
            trend = 'down'
        else:
            trend = 'neutral'
        
        return (pct_change, trend)

    @action(detail=False, methods=["get"], url_path="message-stats")
    def message_stats(self, request):
        """
        Get message statistics filtered by date range and platform.
        
        Used for the "Message Statistics" section on Host Dashboard showing:
        - Total WhatsApp Sent
        - Total SMS Sent
        
        Data source: team_inbox.Messages with direction=OUTGOING
        This includes ALL outgoing messages (broadcast + session/conversation messages).
        
        Query Parameters:
            start_date (str): Start date in YYYY-MM-DD format (optional)
            end_date (str): End date in YYYY-MM-DD format (optional)
        
        If no dates provided, returns all-time totals.
        
        Response:
        {
            "whatsapp_sent": 8500,
            "sms_sent": 7347,
            "total_sent": 15847,
            "date_range": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-28"
            }
        }
        """
        from datetime import datetime
        from team_inbox.models import Messages, MessageDirectionChoices, MessagePlatformChoices
        
        # Parse optional date parameters
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        
        # Build base queryset - only OUTGOING messages
        queryset = Messages.objects.filter(direction=MessageDirectionChoices.OUTGOING)
        
        date_range = {
            'start_date': None,
            'end_date': None
        }
        
        # Apply date filters if provided
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=start_date)
                date_range['start_date'] = start_date_str
            except ValueError:
                return Response(
                    {'error': 'Invalid start_date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=end_date)
                date_range['end_date'] = end_date_str
            except ValueError:
                return Response(
                    {'error': 'Invalid end_date format. Use YYYY-MM-DD'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Count by platform
        whatsapp_sent = queryset.filter(platform=MessagePlatformChoices.WHATSAPP).count()
        sms_sent = queryset.filter(platform=MessagePlatformChoices.SMS).count()
        total_sent = whatsapp_sent + sms_sent
        
        return Response({
            'whatsapp_sent': whatsapp_sent,
            'sms_sent': sms_sent,
            'total_sent': total_sent,
            'date_range': date_range
        })

    @action(detail=False, methods=["get"], url_path="latest-recharges")
    def latest_recharges(self, request):
        """
        Get latest 10 recharge transactions for the Host Dashboard.
        
        Returns list of recent recharges showing:
        - Tenant name
        - Amount with currency
        - Platform (WA/SMS)
        - Status (success/pending/failed)
        - Date
        
        Response:
        {
            "recharges": [
                {
                    "id": 1,
                    "tenant_name": "TechCorp Solutions",
                    "amount": "500.00",
                    "currency": "USD",
                    "platform": "WA",
                    "status": "success",
                    "created_at": "2025-11-20T10:30:00Z"
                },
                ...
            ],
            "total_count": 10
        }
        """
        from abstract.models import TransactionTypeChoices
        from transaction.models import TenantTransaction
        
        # Map transaction_type to display status
        STATUS_MAP = {
            TransactionTypeChoices.SUCCESS_RECHARGE: 'success',
            TransactionTypeChoices.PENDING_RECHARGE: 'pending',
            TransactionTypeChoices.FAILED_RECHARGE: 'failed',
        }
        
        # Get recharge transactions only (success, pending, failed)
        recharge_types = [
            TransactionTypeChoices.SUCCESS_RECHARGE,
            TransactionTypeChoices.PENDING_RECHARGE,
            TransactionTypeChoices.FAILED_RECHARGE,
        ]
        
        transactions = TenantTransaction.objects.filter(
            transaction_type__in=recharge_types
        ).select_related('tenant').order_by('-created_at')[:10]
        
        recharges = []
        for txn in transactions:
            recharges.append({
                'id': txn.id,
                'tenant_name': txn.tenant.name,
                'amount': str(txn.amount.amount),
                'currency': str(txn.amount.currency),
                'platform': 'WA',  # Default to WA for now
                'status': STATUS_MAP.get(txn.transaction_type, 'unknown'),
                'created_at': txn.created_at.isoformat(),
            })
        
        return Response({
            'recharges': recharges,
            'total_count': len(recharges)
        })

    @action(detail=False, methods=["get"], url_path="alerts", permission_classes=[IsAuthenticated, TenantRolePermission])
    def alerts(self, request):
        """
        Real-time alert detection for the tenant (#485).

        Checks: zero/low balance, template rejections, high bounce rate,
        daily limit approaching.  Each check returns an alert dict or None.
        """
        from broadcast.models import BroadcastMessage, MessageStatusChoices
        from tenants.models import TenantWAApp
        from wa.models import TemplateStatus, WATemplate

        tenant = request.user.tenant
        now = timezone.now()
        alerts = []

        # ── 1. Zero balance ───────────────────────────────────────────
        if tenant.total_balance.amount <= 0:
            alerts.append({
                'type': 'zero_balance',
                'severity': 'critical',
                'title': 'Zero wallet balance',
                'message': (
                    f'Your balance is {tenant.total_balance}. '
                    'Recharge immediately to continue sending messages.'
                ),
                'action_url': '/billing/recharge',
                'created_at': now.isoformat(),
            })

        # ── 2. Low balance (but not zero — avoid duplicate) ───────────
        elif tenant.is_below_threshold:
            alerts.append({
                'type': 'low_balance',
                'severity': 'critical',
                'title': 'Low wallet balance',
                'message': (
                    f'Your balance is {tenant.total_balance}. '
                    f'Threshold is {tenant.threshold_alert}. Recharge to continue sending messages.'
                ),
                'action_url': '/billing/recharge',
                'created_at': now.isoformat(),
            })

        # ── 3. Template rejections (last 7 days) ─────────────────────
        seven_days_ago = now - timedelta(days=7)
        rejected_templates = WATemplate.objects.filter(
            wa_app__tenant=tenant,
            status=TemplateStatus.REJECTED,
            updated_at__gte=seven_days_ago,
        ).values_list('element_name', flat=True)[:10]

        for tpl_name in rejected_templates:
            alerts.append({
                'type': 'template_rejected',
                'severity': 'warning',
                'title': 'Template rejected',
                'message': (
                    f"Template '{tpl_name}' was rejected by Meta. "
                    'Review and resubmit.'
                ),
                'action_url': f'/wa/templates/{tpl_name}',
                'metadata': {'template_name': tpl_name},
                'created_at': now.isoformat(),
            })

        # ── 4. High bounce rate (>10%) on broadcasts in last 7 days ──
        from django.db.models import Q

        broadcast_stats = (
            BroadcastMessage.objects
            .filter(broadcast__tenant=tenant, broadcast__created_at__gte=seven_days_ago)
            .exclude(status__in=[
                MessageStatusChoices.PENDING,
                MessageStatusChoices.QUEUED,
            ])
            .values('broadcast_id', 'broadcast__name')
            .annotate(
                total=Count('id'),
                failed=Count('id', filter=Q(status=MessageStatusChoices.FAILED)),
            )
        )
        for row in broadcast_stats:
            if row['total'] > 0 and (row['failed'] / row['total']) > 0.10:
                rate = round((row['failed'] / row['total']) * 100, 1)
                b_name = row['broadcast__name'] or f"Broadcast #{row['broadcast_id']}"
                alerts.append({
                    'type': 'high_bounce_rate',
                    'severity': 'warning',
                    'title': 'High bounce rate',
                    'message': f"Broadcast '{b_name}' has a {rate}% failure rate.",
                    'action_url': f"/broadcasts/{row['broadcast_id']}",
                    'metadata': {
                        'broadcast_id': row['broadcast_id'],
                        'bounce_rate': rate,
                    },
                    'created_at': now.isoformat(),
                })

        # ── 5. Daily limit approaching (>80%) ────────────────────────
        for wa_app in tenant.wa_apps.only('app_name', 'daily_limit', 'messages_sent_today'):
            if wa_app.daily_limit > 0:
                usage_pct = wa_app.messages_sent_today / wa_app.daily_limit
                if usage_pct > 0.80:
                    alerts.append({
                        'type': 'daily_limit_approaching',
                        'severity': 'info',
                        'title': 'Daily message limit approaching',
                        'message': (
                            f'{wa_app.app_name}: {wa_app.messages_sent_today}/{wa_app.daily_limit} '
                            f'messages sent today ({round(usage_pct * 100)}%).'
                        ),
                        'action_url': '/settings/wa-apps',
                        'created_at': now.isoformat(),
                    })

        # ── Summary ───────────────────────────────────────────────────
        summary = {'critical': 0, 'warning': 0, 'info': 0}
        for a in alerts:
            summary[a['severity']] = summary.get(a['severity'], 0) + 1
        summary['total'] = len(alerts)

        return Response({
            'alerts': alerts,
            'summary': summary,
        })
