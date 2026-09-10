from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from abstract.models import TransactionTypeChoices
from abstract.viewsets.base import BaseModelViewSet
from tenants.permission_classes import TenantRolePermission
from transaction.filters import TenantTransactionFilter
from transaction.models import TenantTransaction
from transaction.serializers import TenantTransactionSerializer


class TenantTransactionViewSet(BaseModelViewSet):
    """
    ViewSet for Tenant Transactions with historical broadcast filtering.

    Filters broadcast fields based on historical state at transaction time, not current state.

    Example queries:
    - /transactions/?broadcast_status=SCHEDULED  (transactions when broadcast was SCHEDULED)
    - /transactions/?broadcast_status__in=QUEUED,SCHEDULED  (multiple statuses)
    - /transactions/?broadcast_scheduled_time__gte=2025-11-20  (scheduled time at transaction)
    - /transactions/?broadcast_platform=WHATSAPP  (platform at transaction time)
    """

    # Read-only over the API. Every legitimate TenantTransaction is written
    # server-side — the Razorpay order signal (transaction/signals.py), the
    # voice rater and billing tasks, and broadcast credit_manager. No client
    # has ever created one: the web app calls this endpoint with GET only.
    #
    # Leaving POST and PATCH exposed is what made the wallet reachable at all,
    # since a SUCCESS RECHARGE row is applied to the balance by the
    # update_tenant_balance receiver. Operator-driven crediting gets its own
    # audited, host-only path in #233 rather than a raw row endpoint.
    http_method_names = ["get"]

    queryset = TenantTransaction.objects.all()
    serializer_class = TenantTransactionSerializer
    filterset_class = TenantTransactionFilter
    permission_classes = [IsAuthenticated, TenantRolePermission]
    required_permissions = {
        # A SUCCESS RECHARGE row credits the wallet — never a view-level permission.
        "create": "billing.manage",
        "partial_update": "billing.manage",
        "list": "billing.view",
        "retrieve": "billing.view",
        "transaction_status": "billing.view",
        "default": "billing.view",
    }

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset

        tenant_id = getattr(user, "tenant_id", None)
        return self.queryset.filter(tenant_id=tenant_id)

    @action(detail=False, methods=["get"], url_path="transaction-status-options")
    def transaction_status(self, request):
        """
        An endpoint to get the transaction status summary for the tenant.
        """
        choices = TransactionTypeChoices.choices
        list_status = [choice[0] for choice in choices]
        return Response(list_status, status=status.HTTP_200_OK)
