from broadcast.models import BroadcastPlatformChoices
from broadcast.viewsets.broadcast import BroadcastViewSet
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from wa.serializers import (ChargeBreakdownRequestSerializer,
                            ChargeBreakdownStatusSerializer,
                            DateTimeRequestSerializer, WABroadcastSerializer)
from wa.serializers.broadcast import WABroadcastLimitedSerializer


class WABroadcastViewSet(BroadcastViewSet):
    
    serializer_class = WABroadcastSerializer
    http_method_names = ["get","post","patch"]
    required_permissions = {
        "list": "broadcast.view",
        "retrieve": "broadcast.view",
        "create": "broadcast.create",
        "partial_update": "broadcast.create",
        "get_sending_quota": "broadcast.view",
        "preflight_check": "broadcast.create",
        "get_charge_breakdown": "broadcast.charge_breakdown",
        "charge_breakdown_status": "broadcast.charge_breakdown",
        "reserve_keyword_list": "broadcast.view",
        "min_scheduled_time": "broadcast.view",
        "default": "broadcast.view",
    }

    def get_serializer_class(self):
        """
        #251: MANAGER+ (priority >= 60) see cost fields.
        AGENT/VIEWER get WABroadcastLimitedSerializer.
        """
        tu = self._get_tenant_user()
        if tu and tu.role and tu.role.priority >= 60:
            return WABroadcastSerializer
        return WABroadcastLimitedSerializer

    def get_queryset(self):
        """
        Override to filter broadcasts specific to Gupshup platform
        """
        queryset = super().get_queryset()
        return queryset.filter(platform=BroadcastPlatformChoices.WHATSAPP)
    
    def _get_wa_app(self, request):
        """
        Get the TenantWAApp for the current user's tenant.
        
        Returns the first wa_app if tenant has only one,
        otherwise expects wa_app_id in request data.
        """
        from tenants.models import TenantWAApp
        
        user = request.user
        
        # Get all WA apps for the user's tenant
        wa_apps = TenantWAApp.objects.filter_by_user_tenant(user)
        
        # If wa_app_id is provided in request, use that
        wa_app_id = request.data.get('wa_app_id')
        if wa_app_id:
            try:
                return wa_apps.get(id=wa_app_id)
            except TenantWAApp.DoesNotExist:
                return None
        
        # Otherwise return the first one (most common case: tenant has 1 app)
        return wa_apps.first()

    @action(detail=False, methods=['post'], serializer_class=DateTimeRequestSerializer)    
    def get_sending_quota(self, request):
        """
        Get WhatsApp sending quota for the tenant at a specific datetime.
        
        Request body:
        {
            "datetime": "2026-01-19T10:00:00Z",
            "wa_app_id": 1  // Optional if tenant has only one app
        }
        
        Response:
        {
            "tier": "TIER_1K",
            "tier_limit": 1000,
            "used_quota": 250,
            "scheduled_reservations": 50,
            "remaining_quota": 700,
            "is_unlimited": false
        }
        """
        from wa.services.quota_service import QuotaService

        # Get WA app
        wa_app = self._get_wa_app(request)
        if not wa_app:
            return Response(
                {"error": "No WhatsApp app found. Please provide valid wa_app_id."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Parse datetime from request (optional — defaults to now)
        from django.utils import timezone
        from django.utils.dateparse import parse_datetime as _parse_dt

        raw_dt = request.data.get('datetime') or request.data.get('start_date')
        if raw_dt:
            at_time = _parse_dt(str(raw_dt))
            if at_time is None:
                return Response(
                    {"error": "Invalid datetime format. Use ISO 8601, e.g. 2026-01-19T10:00:00Z"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            at_time = timezone.now()
        
        # Get quota status
        quota_service = QuotaService(wa_app)
        quota_status = quota_service.get_quota_status(at_time=at_time)
        
        # Enrich with balance info and warnings
        balance_info = self._get_balance_info(request)
        quota_status['balance'] = balance_info
        
        return Response(quota_status)

    @action(detail=False, methods=['post'], url_path='preflight-check')
    def preflight_check(self, request):
        """
        Pre-flight check before broadcast creation.
        Returns combined quota + balance warnings/errors for the given broadcast params.
        
        Request body:
        {
            "recipient_count": 500,
            "datetime": "2026-01-19T10:00:00Z",  // optional, defaults to now
            "wa_app_id": 1  // optional if tenant has only one app
        }
        
        Response:
        {
            "can_proceed": true/false,
            "errors": [...],     // hard blocks
            "warnings": [...],   // soft warnings
            "quota": { ... },
            "balance": { ... }
        }
        """
        from django.utils import timezone
        from djmoney.money import Money
        from wa.services.quota_service import QuotaService
        
        wa_app = self._get_wa_app(request)
        if not wa_app:
            return Response(
                {"error": "No WhatsApp app found. Please provide valid wa_app_id."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        recipient_count = request.data.get('recipient_count', 0)
        at_time_str = request.data.get('datetime')
        
        if at_time_str:
            from django.utils.dateparse import parse_datetime as pd
            at_time = pd(at_time_str)
            if at_time and timezone.is_naive(at_time):
                at_time = timezone.make_aware(at_time)
        else:
            at_time = None
        
        errors = []
        warnings = []
        
        # --- Quota check ---
        quota_service = QuotaService(wa_app)
        quota_status = quota_service.get_quota_status(at_time=at_time)
        
        if not quota_status.get('is_unlimited') and recipient_count > 0:
            remaining = quota_status.get('remaining_quota', 0)
            if recipient_count > remaining:
                errors.append({
                    'type': 'QUOTA_EXCEEDED',
                    'message': (
                        f"Quota exceeded: Need capacity for {recipient_count} recipients, "
                        f"but only {remaining} remaining (Tier limit: {quota_status.get('tier')})."
                    ),
                    'details': {
                        'recipient_count': recipient_count,
                        'remaining_quota': remaining,
                        'tier_limit': quota_status.get('tier'),
                        'overflow_by': recipient_count - remaining,
                    }
                })
            elif remaining > 0 and recipient_count > remaining * 0.8:
                # Warn if using more than 80% of remaining quota
                warnings.append({
                    'type': 'QUOTA_APPROACHING_LIMIT',
                    'message': (
                        f"This broadcast will use {recipient_count} of your {remaining} "
                        f"remaining quota ({int(recipient_count / remaining * 100)}%)."
                    ),
                    'details': {
                        'recipient_count': recipient_count,
                        'remaining_quota': remaining,
                        'usage_percent': round(recipient_count / remaining * 100, 1),
                    }
                })
        
        # --- Balance check (ADMIN/OWNER only — #251) ---
        balance_info = self._get_balance_info(request)
        tu = self._get_tenant_user()
        _is_financial_role = tu and tu.role and tu.role.priority >= 80
        
        if _is_financial_role and recipient_count > 0:
            try:
                price_per_message = float(wa_app.marketing_message_price.amount)
            except Exception:
                price_per_message = 0
            
            if price_per_message > 0:
                tenant = request.user.tenant
                estimated_cost = recipient_count * price_per_message
                available = float(tenant.total_balance.amount)
                
                if available < estimated_cost:
                    errors.append({
                        'type': 'INSUFFICIENT_BALANCE',
                        'message': (
                            f"Insufficient balance. Estimated cost: {estimated_cost:.2f} {tenant.balance.currency}, "
                            f"Available: {available:.2f} {tenant.balance.currency}."
                        ),
                        'details': {
                            'estimated_cost': round(estimated_cost, 2),
                            'available_balance': round(available, 2),
                            'shortfall': round(estimated_cost - available, 2),
                            'currency': str(tenant.balance.currency),
                        }
                    })
                else:
                    balance_after = available - estimated_cost
                    threshold = float(tenant.threshold_alert.amount)
                    if balance_after < threshold or tenant.is_below_threshold:
                        warnings.append({
                            'type': 'LOW_BALANCE',
                            'message': (
                                f"Low balance warning: After this broadcast, your balance will be "
                                f"{balance_after:.2f} {tenant.balance.currency} "
                                f"(threshold: {threshold:.2f} {tenant.balance.currency})."
                            ),
                            'details': {
                                'available_balance': round(available, 2),
                                'estimated_cost': round(estimated_cost, 2),
                                'balance_after_broadcast': round(balance_after, 2),
                                'threshold': round(threshold, 2),
                                'currency': str(tenant.balance.currency),
                            }
                        })
        
        can_proceed = len(errors) == 0
        
        return Response({
            'can_proceed': can_proceed,
            'errors': errors,
            'warnings': warnings,
            'quota': quota_status,
            'balance': balance_info,
        })

    def _get_balance_info(self, request):
        """
        Get wallet balance info for the current user's tenant.
        Returns a dict with balance, credit_line, total_balance, threshold, and warnings.

        #251: AGENT/VIEWER (priority < 80) get an empty dict — no financial data.
        """
        tu = self._get_tenant_user()
        if not tu or not tu.role or tu.role.priority < 80:
            return {}

        tenant = request.user.tenant
        
        balance_info = {
            'balance': float(tenant.balance.amount),
            'credit_line': float(tenant.credit_line.amount),
            'total_balance': float(tenant.total_balance.amount),
            'threshold': float(tenant.threshold_alert.amount),
            'currency': str(tenant.balance.currency),
            'is_below_threshold': tenant.is_below_threshold,
            'is_prepaid': tenant.is_prepaid,
        }
        return balance_info

    def create(self, request, *args, **kwargs):
        """
        Override create to attach low-balance warnings to the response.
        """
        response = super().create(request, *args, **kwargs)
        
        # Check if serializer attached any warnings via context
        serializer = self.get_serializer()
        broadcast_warnings = serializer.context.get('_broadcast_warnings', [])
        if broadcast_warnings and response.status_code == 201:
            response.data['warnings'] = broadcast_warnings
        
        return response

    def update(self, request, *args, **kwargs):
        """
        Override update to attach low-balance warnings to the response.
        """
        response = super().update(request, *args, **kwargs)
        
        serializer = self.get_serializer()
        broadcast_warnings = serializer.context.get('_broadcast_warnings', [])
        if broadcast_warnings and response.status_code == 200:
            response.data['warnings'] = broadcast_warnings
        
        return response
        
    @action(detail=False, methods=['post'], serializer_class=ChargeBreakdownRequestSerializer)    
    def get_charge_breakdown(self, request):
        """
        Compute a country-wise charge breakdown for a set of contacts.

        Accepts EITHER:
          - ``contact_ids`` + ``template_id`` (pre-creation estimation)
          - ``broadcast_id`` (existing broadcast)

        For ≤ 1 000 contacts the result is returned synchronously.
        For > 1 000 contacts a Celery task is dispatched and a ``task_id``
        is returned; poll ``charge_breakdown_status`` for the result.
        """
        from broadcast.services.charge_breakdown import (
            ASYNC_CONTACT_THRESHOLD, ChargeBreakdownService)

        serializer = ChargeBreakdownRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        wa_app = self._get_wa_app(request)
        if not wa_app:
            return Response(
                {"error": "No WhatsApp app found. Please provide valid wa_app_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact_ids = data.get("contact_ids")
        broadcast_id = data.get("broadcast_id")
        template_id = data.get("template_id")

        # Determine contact count for sync/async decision
        contact_count = 0
        if contact_ids:
            contact_count = len(contact_ids)
        elif broadcast_id:
            from broadcast.models import Broadcast
            try:
                contact_count = Broadcast.objects.get(
                    id=broadcast_id, tenant=request.user.tenant
                ).recipients.count()
            except Broadcast.DoesNotExist:
                return Response(
                    {"error": f"Broadcast {broadcast_id} not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # --- ASYNC path for large sets ---
        if contact_count > ASYNC_CONTACT_THRESHOLD:
            from broadcast.tasks import compute_charge_breakdown_task

            task = compute_charge_breakdown_task.delay(
                wa_app_id=wa_app.id,
                contact_ids=contact_ids,
                broadcast_id=broadcast_id,
                template_id=template_id,
            )
            return Response(
                {
                    "status": "processing",
                    "task_id": task.id,
                    "message": (
                        f"Breakdown for {contact_count:,} contacts is being computed. "
                        "Poll charge_breakdown_status with the task_id."
                    ),
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # --- SYNC path ---
        svc = ChargeBreakdownService(wa_app=wa_app)
        result = svc.compute(
            contact_ids=contact_ids,
            broadcast_id=broadcast_id,
            template_id=template_id,
        )

        # Enrich with wallet balance
        balance_info = self._get_balance_info(request)
        result["balance"] = balance_info

        return Response(result)

    @action(
        detail=False,
        methods=["post"],
        url_path="charge-breakdown-status",
        serializer_class=ChargeBreakdownStatusSerializer,
    )
    def charge_breakdown_status(self, request):
        """
        Poll the result of an async charge-breakdown task.

        Request body:  ``{ "task_id": "<celery-task-id>" }``
        """
        import json

        from django.core.cache import cache

        serializer = ChargeBreakdownStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task_id = serializer.validated_data["task_id"]

        cache_key = f"charge_breakdown:{task_id}"
        cached = cache.get(cache_key)

        if cached:
            payload = json.loads(cached)
            if payload["status"] == "completed":
                result = payload["result"]
                result["balance"] = self._get_balance_info(request)
                return Response(result)
            elif payload["status"] == "failed":
                return Response(
                    {"status": "failed", "error": payload.get("error", "Unknown error")},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Still processing
        return Response(
            {"status": "processing", "task_id": task_id},
            status=status.HTTP_202_ACCEPTED,
        )