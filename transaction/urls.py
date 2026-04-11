from django.urls import include, path
from rest_framework.routers import DefaultRouter

from transaction.viewsets.tenant_transaction import TenantTransactionViewSet

router = DefaultRouter()
router.register(r"tenant-transactions", TenantTransactionViewSet, basename="tenant-transaction")

urlpatterns = [
    path("", include(router.urls)),
]
